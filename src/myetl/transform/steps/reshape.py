"""Reshape transformers.

A source's data frame into observations.

Coded headers and values (Eurostat's ``"CODE: Label"``) are reduced to
their code or label, wide data frames are melted, and the observation
columns are taken with numbers as numbers and periods parsed.
"""

__all__ = [
    "CODE_SEPARATOR",
    "ColumnCodeRenamer",
    "ColumnMelter",
    "ObservationBuilder",
    "PeriodParser",
    "SourceColumns",
    "ValueCodeExtractor",
    "ValueLabelExtractor",
]

from dataclasses import dataclass
from typing import Final

import pandas as pd

from myetl.transform.observations import (
    OBSERVATION_COLUMNS,
    DfFormatError,
    ObservationColumns,
    Period,
    parse_period,
    require_columns,
)
from myetl.transform.steps._dataframes import order_columns

#: Separates the code from the label in a coded value or column name,
#: e.g. ``"AT: Austria"`` (Eurostat SDMX-CSV with ``labels=both``).
CODE_SEPARATOR: Final[str] = ": "


@dataclass(frozen=True, slots=True)
class ColumnCodeRenamer:
    """Transformer for renaming coded column names to their code.

    ``"geo: Geopolitical entity (reporting)"`` name becomes ``"geo"``.
    A name without a separator is kept as it is.

    Attributes:
        separator: Separates the code from the label.
    """

    separator: str = CODE_SEPARATOR

    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        """Return the data frame with code-only column names.

        Args:
            df: Data frame with coded column names.

        Returns:
            The same rows under the renamed columns.

        Raises:
            DfFormatError: If two columns end up with the same code.
        """
        col_name_codes = [
            str(column_name).partition(self.separator)[0]
            for column_name in df.columns
        ]
        repeated_codes = sorted(
            {code for code in col_name_codes if col_name_codes.count(code) > 1}
        )

        if repeated_codes:
            step_name = type(self).__name__
            problem = f"{step_name}: repeated codes {repeated_codes}"
            raise DfFormatError(problem)

        return df.set_axis(col_name_codes, axis="columns")


@dataclass(frozen=True, slots=True)
class ValueCodeExtractor:
    """Transformer for replacing coded values in columns by their code.

    For example ``"CF_R: Combustible fuels - renewable"`` value becomes
    ``"CF_R"``.

    Attributes:
        value_columns: Columns holding coded values.
        separator: Separates the code from the label.
    """

    value_columns: tuple[str, ...]
    separator: str = CODE_SEPARATOR

    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        """Return the data frame with the code part of coded values.

        Args:
            df: Data frame with coded values in :attr:`value_columns`.

        Returns:
            The data frame with those columns replaced.

        Raises:
            DfFormatError: If a column is missing.
        """
        require_columns(df, self.value_columns, type(self).__name__)
        col_value_code = {
            column: df[column].str.partition(self.separator)[0]
            for column in self.value_columns
        }
        return df.assign(**col_value_code)


@dataclass(frozen=True, slots=True)
class ValueLabelExtractor:
    """Transformer for replacing coded values by their label.

    For example ``"EL: Greece"`` value becomes ``"Greece"``.

    Attributes:
        value_columns: Columns holding coded values.
        separator: Separates the code from the label.
    """

    value_columns: tuple[str, ...]
    separator: str = CODE_SEPARATOR

    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        """Return the data frame with the label part of coded values.

        A value without a separator gets an empty label.

        Args:
            df: Data frame with coded values in :attr:`value_columns`.

        Returns:
            The data frame with those columns replaced.

        Raises:
            DfFormatError: If a column is missing.
        """
        require_columns(df, self.value_columns, type(self).__name__)
        col_value_label = {
            column: df[column].str.partition(self.separator)[2]
            for column in self.value_columns
        }
        return df.assign(**col_value_label)


@dataclass(frozen=True, slots=True)
class ColumnMelter:
    """Transformer for turning a wide data frame into a long one.

    The value column's name becomes the variable; for example OWID's
    ``wind_electricity`` column becomes rows of variable
    ``wind_electricity``.

    Attributes:
        key_columns: Columns kept on every row, e.g. country and year.
        value_columns: Columns turned into rows.
        columns: Names of the variable and value columns written.
    """

    key_columns: tuple[str, ...]
    value_columns: tuple[str, ...]
    columns: ObservationColumns = OBSERVATION_COLUMNS

    def apply(self, wide_df: pd.DataFrame) -> pd.DataFrame:
        """Return the key columns plus a series and a value column.

        Args:
            wide_df: Wide data frame.

        Returns:
            Long data frame - one row per original row and value column.

        Raises:
            DfFormatError: If a column is missing.
        """
        require_columns(
            wide_df,
            (*self.key_columns, *self.value_columns),
            type(self).__name__,
        )
        return wide_df.melt(
            id_vars=list(self.key_columns),
            value_vars=list(self.value_columns),
            var_name=self.columns.variable,
            value_name=self.columns.value,
        )


@dataclass(frozen=True, slots=True)
class SourceColumns:
    """Which source columns hold the parts of an observation.

    Attributes:
        country: Country key, e.g. ``"geo"`` or ``"iso_code"``.
        period: Period text, e.g. ``"TIME_PERIOD"`` or ``"year"``.
        variable: What is measured, e.g. ``"siec"``.
        value: The number, e.g. ``"OBS_VALUE"``.
    """

    country: str
    period: str
    variable: str
    value: str


@dataclass(frozen=True, slots=True)
class ObservationBuilder:
    """Transformer for selecting the columns from a source data frame.

    Values become numbers and empty values are dropped: in the
    observation format a missing value is an absent row.

    Attributes:
        source_columns: Where the parts of an observation are.
        columns: Names of the observation columns.
    """

    source_columns: SourceColumns
    columns: ObservationColumns = OBSERVATION_COLUMNS

    def apply(self, long_df: pd.DataFrame) -> pd.DataFrame:
        """Return country, period, variable and value columns.

        Args:
            long_df: Source data frame.

        Returns:
            Long data frame with one row per non-empty value. The period
            is still text.

        Raises:
            DfFormatError: If a column is missing or a value is not
                a number.
        """
        source, target = self.source_columns, self.columns
        source_names = [source.country, source.period, source.variable]
        step_name = type(self).__name__
        require_columns(long_df, (*source_names, source.value), step_name)

        text_columns = {
            target_name: long_df[source_name].astype(str)
            for source_name, target_name in zip(
                source_names,
                (target.country, target.period, target.variable),
                strict=True,
            )
        }

        parsed_numbers = _parse_numbers(long_df[source.value], step_name)
        observations = pd.DataFrame(
            text_columns | {target.value: parsed_numbers}
        )

        return observations[parsed_numbers.notna()].reset_index(drop=True)


def _parse_numbers(value_texts: pd.Series, step_name: str) -> pd.Series:
    """Convert values to floats; empty values become NaN.

    Args:
        value_texts: Values as read, text or numbers.
        step_name: Transformer name, for the message.

    Returns:
        Floats, NaN where the value was empty.

    Raises:
        DfFormatError: If a non-empty value is not a number.
    """
    numbers = pd.to_numeric(value_texts, errors="coerce")
    is_blank = value_texts.isna() | (value_texts.astype(str).str.strip() == "")
    is_malformed = numbers.isna() & ~is_blank

    if is_malformed.any():
        examples = sorted(set(value_texts[is_malformed].astype(str)))[:5]
        problem = f"{step_name}: expected numbers, found {examples}"
        raise DfFormatError(problem)

    return numbers.astype(float)


@dataclass(frozen=True, slots=True)
class PeriodParser:
    """Transformer for replacing period text by year and sub-period.

    Attributes:
        columns: Names of the observation columns.
    """

    columns: ObservationColumns = OBSERVATION_COLUMNS

    def apply(self, long_df: pd.DataFrame) -> pd.DataFrame:
        """Return observations with integer year and sub-period columns.

        Each distinct period text is parsed once.

        Args:
            long_df: Observations with a period text column.

        Returns:
            Long data frame with the observations in observation column
            order.

        Raises:
            DfFormatError: If the period column is missing or a
                period is not a year, half-year or month.
        """
        columns = self.columns
        require_columns(long_df, (columns.period,), type(self).__name__)

        periods: dict[str, Period] = {
            period_text: parse_period(period_text)
            for period_text in long_df[columns.period].unique()
        }

        period_texts = long_df[columns.period]
        parsed_df = long_df.drop(columns=columns.period).assign(
            **{
                columns.year: period_texts.map(
                    lambda text: periods[text].year
                ).astype(int),
                columns.sub_period: period_texts.map(
                    lambda text: periods[text].sub_period
                ).astype(int),
            }
        )

        return order_columns(parsed_df, columns)
