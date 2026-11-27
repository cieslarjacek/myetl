"""Calculation transformers.

Derive, aggregate and scale variables.
"""

__all__ = [
    "Aggregation",
    "Difference",
    "NewVariableFormula",
    "Ratio",
    "VariableDeriver",
    "VariableScaler",
    "YearAggregator",
]

import enum
import math
from dataclasses import dataclass
from typing import Protocol

import pandas as pd

from myetl.transform.observations import (
    ANNUAL_SUB_PERIOD,
    OBSERVATION_COLUMNS,
    DfFormatError,
    ObservationColumns,
)
from myetl.transform.steps._dataframes import order_columns, pivot_to_wide


class NewVariableFormula(Protocol):
    """Compute a new variable from two others."""

    @property
    def operands(self) -> tuple[str, str]:
        """The two input variables."""
        ...

    def compute(self, wide_df: pd.DataFrame) -> pd.Series:
        """Return the new values.

        Args:
            wide_df: Data in wide format - one column per input
                variable.

        Returns:
            One value per row; NaN where an input is missing.
        """
        ...


@dataclass(frozen=True, slots=True)
class Difference:
    """Calculate a difference between two variables.

    ``difference = minuend - subtrahend``, e.g. fossil = ``CF - CF_R``.

    Attributes:
        minuend: Variable name from which subtrahend is subtracted.
        subtrahend: Variable name being subtracted from minuend.
    """

    minuend: str
    subtrahend: str

    @property
    def operands(self) -> tuple[str, str]:
        """Minuend and subtrahend."""
        return (self.minuend, self.subtrahend)

    def compute(self, wide_df: pd.DataFrame) -> pd.Series:
        """Return the difference.

        Args:
            wide_df: Data in wide format - one column per input
                variable.

        Returns:
            Minuend minus subtrahend.
        """
        return wide_df[self.minuend] - wide_df[self.subtrahend]


@dataclass(frozen=True, slots=True)
class Ratio:
    """Calculate a ratio between two variables.

    ``ratio = numerator * factor / denominator``, e.g. GDP per capita.

    Attributes:
        numerator: Variable name being divided by denominator.
        denominator: Variable name by which numerator is divided.
        factor: Multiplier for unit conversion, e.g. 1 000 000 for TWh
            per person to MWh per person.
    """

    numerator: str
    denominator: str
    factor: float = 1.0

    @property
    def operands(self) -> tuple[str, str]:
        """Numerator and denominator."""
        return (self.numerator, self.denominator)

    def compute(self, wide_df: pd.DataFrame) -> pd.Series:
        """Return the ratio.

        Args:
            wide_df: Data in wide format - one column per input
                variable.

        Returns:
            Numerator times factor over denominator; infinite where the
            denominator is zero.
        """
        return (
            wide_df[self.numerator] * self.factor / wide_df[self.denominator]
        )


@dataclass(frozen=True, slots=True)
class VariableDeriver:
    """Transformer for adding a new variable computed from two others.

    A row gets a value only where both inputs have one and the
    result is finite.

    Attributes:
        target_col: Name of the new variable.
        formula: How it is computed.
        columns: Names of the observation columns.
    """

    target_col: str
    formula: NewVariableFormula
    columns: ObservationColumns = OBSERVATION_COLUMNS

    def apply(self, long_df: pd.DataFrame) -> pd.DataFrame:
        """Return the observations plus the new variable.

        Args:
            long_df: Observations.

        Returns:
            The observations, followed by the new variable's rows.

        Raises:
            DfFormatError: If the target variable already exists.
        """
        columns = self.columns
        if (long_df[columns.variable] == self.target_col).any():
            step_name = type(self).__name__
            problem = (
                f"{step_name}: variable {self.target_col!r} already exists"
            )
            raise DfFormatError(problem)

        operand_rows = long_df[
            long_df[columns.variable].isin(self.formula.operands)
        ]
        wide_df = pivot_to_wide(operand_rows, columns)
        if not set(self.formula.operands) <= set(wide_df.columns):
            return long_df

        derived_values = self.formula.compute(wide_df)
        is_finite = derived_values.map(math.isfinite)
        derived_rows = (
            derived_values[is_finite]
            .rename(columns.value)
            .reset_index()
            .assign(**{columns.variable: self.target_col})
        )

        return pd.concat(
            [long_df, order_columns(derived_rows, columns)], ignore_index=True
        )


class Aggregation(enum.Enum):
    """How the sub-periods of a year are combined."""

    #: Flows, e.g. monthly generation to annual generation.
    SUM = "sum"
    #: Levels, e.g. half-yearly prices to an annual price.
    MEAN = "mean"


@dataclass(frozen=True, slots=True)
class YearAggregator:
    """Transformer for combining sub-periods into complete years.

    Only a year with every sub-period gets a value.

    Attributes:
        periods_per_year: Sub-periods in a complete year: 1, 2 or 12.
        aggregation: How the sub-periods are combined.
        columns: Names of the observation columns.
        annual_sub_period: Sub-period given to the annual values.
    """

    periods_per_year: int
    aggregation: Aggregation
    columns: ObservationColumns = OBSERVATION_COLUMNS
    annual_sub_period: int = ANNUAL_SUB_PERIOD

    def apply(self, long_df: pd.DataFrame) -> pd.DataFrame:
        """Return one observation per country, year and variable.

        Args:
            long_df: Observations with sub-periods.

        Returns:
            Annual observations of the complete years.

        Raises:
            DfFormatError: If a sub-period exceeds
                :attr:`periods_per_year`.
        """
        columns = self.columns
        _require_sub_periods_within(
            long_df[columns.sub_period],
            self.periods_per_year,
            type(self).__name__,
        )

        year_groups = long_df.groupby(
            [columns.country, columns.year, columns.variable]
        )
        period_counts = year_groups[columns.sub_period].nunique()
        annual_values = year_groups[columns.value].agg(self.aggregation.value)
        is_complete = period_counts == self.periods_per_year
        annual_rows = (
            annual_values[is_complete]
            .reset_index()
            .assign(**{columns.sub_period: self.annual_sub_period})
        )

        return order_columns(annual_rows, columns)


def _require_sub_periods_within(
    sub_periods: pd.Series, periods_per_year: int, step_name: str
) -> None:
    """Raise if a sub-period does not fit the expected year length.

    Args:
        sub_periods: Sub-period of each observation.
        periods_per_year: Sub-periods in a complete year.
        step_name: Transformer name for the message.

    Raises:
        DfFormatError: If a sub-period exceeds ``periods_per_year``.
    """
    if (sub_periods > periods_per_year).any():
        found_sub_periods = sorted(int(sub) for sub in sub_periods.unique())
        problem = (
            f"{step_name}: sub-periods above {periods_per_year}, "
            f"found {found_sub_periods}"
        )
        raise DfFormatError(problem)


@dataclass(frozen=True, slots=True)
class VariableScaler:
    """Transformer for multiplying chosen variables by a factor.

    For example 0.001 converts GWh to TWh.

    Attributes:
        target_cols: Names of the variables to scale.
        factor: Multiplier, e.g. 0.001.
        columns: Names of the observation columns.
    """

    target_cols: tuple[str, ...]
    factor: float
    columns: ObservationColumns = OBSERVATION_COLUMNS

    def apply(self, long_df: pd.DataFrame) -> pd.DataFrame:
        """Return the observations with the chosen variables scaled.

        Args:
            long_df: Observations.

        Returns:
            The observations with the chosen variables scaled and other
            variables unchanged.
        """
        columns = self.columns
        is_scaled = long_df[columns.variable].isin(self.target_cols)
        scaled_values = long_df[columns.value].mask(
            is_scaled, long_df[columns.value] * self.factor
        )
        return long_df.assign(**{columns.value: scaled_values})
