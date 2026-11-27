"""Check transformers.

Implausible values and gaps in variables.

Both log what they find as warnings: an implausible value is left out,
a gap passes through and leaves its year empty after aggregation.
"""

__all__ = [
    "Gap",
    "GapReporter",
    "ImplausibleValueRemover",
    "NonNegativeRule",
    "PartOfWholeRule",
    "PlausibilityRule",
    "find_gaps",
]

import logging
from collections.abc import Hashable, Mapping
from dataclasses import dataclass
from typing import Protocol

import pandas as pd

from myetl.transform.observations import (
    FIRST_SUB_PERIOD,
    OBSERVATION_COLUMNS,
    ObservationColumns,
)
from myetl.transform.steps._dataframes import pivot_to_wide

_logger = logging.getLogger(__name__)


class PlausibilityRule(Protocol):
    """A condition every value of a variable must meet per period."""

    @property
    def checked_variable(self) -> str:
        """Variable whose value is omitted when the rule is violated."""
        ...

    @property
    def needed_variables(self) -> tuple[str, ...]:
        """Variables the rule compares."""
        ...

    def find_violations(self, wide_df: pd.DataFrame) -> pd.Series:
        """Return which periods violate the rule.

        Args:
            wide_df: One row per country and period, one column per
                variable in :attr:`needed_variables`.

        Returns:
            ``True`` where the rule is violated; a missing value never
            violates it.
        """
        ...


@dataclass(frozen=True, slots=True)
class NonNegativeRule:
    """A variable is never below zero, e.g. generation.

    Attributes:
        variable: The variable name.
    """

    target_variable: str

    @property
    def checked_variable(self) -> str:
        """The variable name itself."""
        return self.target_variable

    @property
    def needed_variables(self) -> tuple[str, ...]:
        """The variable names itself."""
        return (self.target_variable,)

    def find_violations(self, wide_df: pd.DataFrame) -> pd.Series:
        """Return where the variable is negative.

        Args:
            wide_df: One column per variable.

        Returns:
            ``True`` where the value is below zero.
        """
        return wide_df[self.target_variable] < 0

    def __str__(self) -> str:
        """Return the rule, e.g. ``"FOSSIL >= 0"``."""
        return f"{self.target_variable} >= 0"


@dataclass(frozen=True, slots=True)
class PartOfWholeRule:
    """A part is never above its whole, e.g. fossil within total.

    Attributes:
        part_variable: Variable name that is part of the whole; left out
            on violation.
        whole_variable: Variable name it is part of.
    """

    part_variable: str
    whole_variable: str

    @property
    def checked_variable(self) -> str:
        """The part."""
        return self.part_variable

    @property
    def needed_variables(self) -> tuple[str, ...]:
        """The part and the whole."""
        return (self.part_variable, self.whole_variable)

    def find_violations(self, wide_df: pd.DataFrame) -> pd.Series:
        """Return where the part exceeds the whole.

        Args:
            wide_df: One column per variable.

        Returns:
            ``True`` where the part is above the whole.
        """
        return wide_df[self.part_variable] > wide_df[self.whole_variable]

    def __str__(self) -> str:
        """Return the rule, e.g. ``"FOSSIL <= TOTAL"``."""
        return f"{self.part_variable} <= {self.whole_variable}"


@dataclass(frozen=True, slots=True)
class ImplausibleValueRemover:
    """Transformer for omitting values that break a plausibility rule.

    A left-out value is an absent observation, so a year aggregated
    from it stays empty.

    Attributes:
        rules: Rules to check.
        source_label: Names the source in warnings, e.g.
            ``"nrg_cb_pem"``.
        columns: Names of the observation columns.
    """

    rules: tuple[PlausibilityRule, ...]
    source_label: str
    columns: ObservationColumns = OBSERVATION_COLUMNS

    def apply(self, long_df: pd.DataFrame) -> pd.DataFrame:
        """Return the observations without implausible values.

        Args:
            long_df: Observations.

        Returns:
            The observations minus each violating rule's checked value.
        """
        columns = self.columns
        wide_df = pivot_to_wide(long_df, columns)
        invalid_keys: list[tuple[str, int, int, str]] = []

        for rule in self.rules:
            if not set(rule.needed_variables) <= set(wide_df.columns):
                continue

            is_violated = rule.find_violations(wide_df)
            violated_periods = wide_df[is_violated].reset_index()

            for violation_case in violated_periods.to_dict("records"):
                self._warn(rule, violation_case)
                invalid_keys.append(
                    (
                        violation_case[columns.country],
                        violation_case[columns.year],
                        violation_case[columns.sub_period],
                        rule.checked_variable,
                    )
                )

        return _drop_observations(long_df, invalid_keys, columns)

    def _warn(
        self, rule: PlausibilityRule, violation: Mapping[Hashable, object]
    ) -> None:
        """Log one violation.

        Args:
            rule: The violated rule.
            violation: Country, year, sub-period and the variable values
                of the violating period.
        """
        columns = self.columns
        compared_values = {
            variable_name: violation[variable_name]
            for variable_name in rule.needed_variables
        }
        _logger.warning(
            "%s: %s %s sub-period %s violates %s %s; %s left out",
            self.source_label,
            violation[columns.country],
            violation[columns.year],
            violation[columns.sub_period],
            rule,
            compared_values,
            rule.checked_variable,
        )


def _drop_observations(
    long_df: pd.DataFrame,
    observation_keys: list[tuple[str, int, int, str]],
    columns: ObservationColumns,
) -> pd.DataFrame:
    """Return the observations without the given ones.

    Args:
        long_df: Observations.
        observation_keys: Country, year, sub-period and variable of each
            observation to drop.
        columns: Names of the observation columns.

    Returns:
        The remaining observations.
    """
    if not observation_keys:
        return long_df

    df_keys = pd.MultiIndex.from_frame(
        long_df.loc[:, list(columns.observation_key)]
    )
    dropped_keys = pd.MultiIndex.from_tuples(observation_keys)
    is_dropped = df_keys.isin(dropped_keys)

    return long_df[~is_dropped].reset_index(drop=True)


@dataclass(frozen=True, slots=True)
class Gap:
    """Periods missing inside a variable for one year.

    Attributes:
        country: ISO3 code.
        variable: The variable name.
        year: The year.
        missing_sub_periods: Sub-periods without a value.
    """

    country: str
    variable: str
    year: int
    missing_sub_periods: tuple[int, ...]


def find_gaps(
    long_df: pd.DataFrame,
    periods_per_year: int,
    columns: ObservationColumns = OBSERVATION_COLUMNS,
    first_sub_period: int = FIRST_SUB_PERIOD,
) -> list[Gap]:
    """Find years that miss periods inside a variable.

    A year counts from the first year a variable has any value for a
    country. Its last year counts only up to its last value, so a year
    still in progress is not a gap.

    Args:
        long_df: Observations.
        periods_per_year: Sub-periods in a complete year, e.g. 12.
        columns: Names of the observation columns.
        first_sub_period: Number of the first sub-period.

    Returns:
        One gap per country, variable and year with missing periods, in
        country, variable and year order.
    """
    gaps: list[Gap] = []
    variable_groups = long_df.groupby([columns.country, columns.variable])

    for (country_group, variable_group), variable_df in variable_groups:
        observed_periods = set(
            zip(
                variable_df[columns.year],
                variable_df[columns.sub_period],
                strict=True,
            )
        )

        for year, missing_sub_periods in _find_missing_periods(
            observed_periods, periods_per_year, first_sub_period
        ):
            gaps.append(
                Gap(
                    str(country_group),
                    str(variable_group),
                    year,
                    missing_sub_periods,
                )
            )

    return gaps


def _find_missing_periods(
    observed_periods: set[tuple[int, int]],
    periods_per_year: int,
    first_sub_period: int,
) -> list[tuple[int, tuple[int, ...]]]:
    """Return the missing sub-periods of each year of one variable.

    Args:
        observed_periods: (year, sub-period) of every value.
        periods_per_year: Sub-periods in a complete year.
        first_sub_period: Number of the first sub-period.

    Returns:
        (year, missing sub-periods) for each year with any missing.
    """
    first_year = min(observed_periods)[0]
    last_year, last_sub_period = max(observed_periods)
    missing_by_year = []

    for year in range(first_year, last_year + 1):
        year_end = last_sub_period if year == last_year else periods_per_year
        missing_sub_periods = tuple(
            sub_period
            for sub_period in range(first_sub_period, year_end + 1)
            if (year, sub_period) not in observed_periods
        )

        if missing_sub_periods:
            missing_by_year.append((year, missing_sub_periods))

    return missing_by_year


@dataclass(frozen=True, slots=True)
class GapReporter:
    """Transformer for logging every gap inside a variable as a warning.

    The data frame passes through unchanged; aggregation then leaves
    the gap years empty.

    Attributes:
        periods_per_year: Sub-periods in a complete year, e.g. 12.
        source_label: Names the source in the log, e.g.
            ``"nrg_cb_pem"``.
        columns: Names of the observation columns.
    """

    periods_per_year: int
    source_label: str
    columns: ObservationColumns = OBSERVATION_COLUMNS

    def apply(self, long_df: pd.DataFrame) -> pd.DataFrame:
        """Log the gaps of ``df`` and return it unchanged.

        Args:
            long_df: Observations.

        Returns:
            ``long_df`` itself.
        """
        gaps = find_gaps(long_df, self.periods_per_year, self.columns)

        for gap in gaps:
            _logger.warning(
                "%s: %s %s %d lacks sub-periods %s; year left empty",
                self.source_label,
                gap.country,
                gap.variable,
                gap.year,
                list(gap.missing_sub_periods),
            )
        _logger.info("%s: %d gaps", self.source_label, len(gaps))

        return long_df
