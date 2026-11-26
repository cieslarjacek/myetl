"""Generic transformers: configurable steps from a table to a table.

Each transformer is a frozen dataclass whose fields are its parameters
and whose :meth:`apply` returns a new table. None of them knows a
source: a source's transform is a sequence of transformers configured
for it. Most work on the observation format
(:mod:`myetl.transform.observations`); the first few reshape a source's
table into it:

1. Header and values: :class:`RenameColumnsToCodes`,
   :class:`KeepValueCodes`, :class:`KeepValueLabels`.
2. Into observations: :class:`MeltColumns` (wide sources),
   :class:`ToObservations`, :class:`ParsePeriods`.
3. Scope: :class:`KeepCountries`, :class:`KeepYears`.
4. Checks: :class:`InvalidateImplausible`, :class:`ReportGaps`.
5. Values: :class:`DeriveSeries`, :class:`AggregateToYears`,
   :class:`ScaleSeries`, :class:`SelectSeries`.
"""

__all__ = [
    "CODE_SEPARATOR",
    "AggregateToYears",
    "Aggregation",
    "DeriveSeries",
    "Difference",
    "Gap",
    "InvalidateImplausible",
    "KeepCountries",
    "KeepValueCodes",
    "KeepValueLabels",
    "KeepYears",
    "MeltColumns",
    "NonNegative",
    "NotAbove",
    "ParsePeriods",
    "PlausibilityRule",
    "Ratio",
    "RenameColumnsToCodes",
    "ReportGaps",
    "ScaleSeries",
    "SelectSeries",
    "SeriesFormula",
    "SourceColumns",
    "TableStep",
    "ToObservations",
    "find_gaps",
]

import enum
import logging
import math
from collections.abc import Hashable, Iterable, Mapping
from dataclasses import dataclass
from typing import Final, Protocol

import pandas as pd

from myetl.transform.observations import (
    ANNUAL_SUB_PERIOD,
    FIRST_SUB_PERIOD,
    OBSERVATION_COLUMNS,
    ObservationColumns,
    Period,
    TableFormatError,
    parse_period,
    require_columns,
)

#: Separates the code from the label in a coded value or column name,
#: e.g. ``"AT: Austria"`` (Eurostat SDMX-CSV with ``labels=both``).
CODE_SEPARATOR: Final[str] = ": "

_logger = logging.getLogger(__name__)


class TableStep(Protocol):
    """One configured transformer."""

    def apply(self, table: pd.DataFrame) -> pd.DataFrame:
        """Return the transformed table; the input is left unchanged.

        Args:
            table: Table to transform.

        Returns:
            A new table.
        """
        ...


# ----------------------------------------------------------------------
# Header and values
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RenameColumnsToCodes:
    """Rename coded column names to their code.

    ``"geo: Geopolitical entity (reporting)"`` becomes ``"geo"``; a name
    without a separator is kept.

    Attributes:
        separator: Separates the code from the label.
    """

    separator: str = CODE_SEPARATOR

    def apply(self, table: pd.DataFrame) -> pd.DataFrame:
        """Return the table with code-only column names.

        Args:
            table: Table with coded column names.

        Returns:
            The same rows under the renamed columns.

        Raises:
            TableFormatError: If two columns end up with the same code.
        """
        column_codes = [
            str(column_name).partition(self.separator)[0]
            for column_name in table.columns
        ]
        repeated_codes = sorted(
            {code for code in column_codes if column_codes.count(code) > 1}
        )
        if repeated_codes:
            problem = f"RenameColumnsToCodes: repeated codes {repeated_codes}"
            raise TableFormatError(problem)
        return table.set_axis(column_codes, axis="columns")


@dataclass(frozen=True, slots=True)
class KeepValueCodes:
    """Replace coded values by their code, e.g. ``"CF_R: ..."``.

    Attributes:
        value_columns: Columns holding coded values.
        separator: Separates the code from the label.
    """

    value_columns: tuple[str, ...]
    separator: str = CODE_SEPARATOR

    def apply(self, table: pd.DataFrame) -> pd.DataFrame:
        """Return the table with the code part of each coded value.

        Args:
            table: Table with coded values in :attr:`value_columns`.

        Returns:
            The table with those columns replaced.

        Raises:
            TableFormatError: If a column is missing.
        """
        require_columns(table, self.value_columns, "KeepValueCodes")
        code_columns = {
            column: table[column].str.partition(self.separator)[0]
            for column in self.value_columns
        }
        return table.assign(**code_columns)


@dataclass(frozen=True, slots=True)
class KeepValueLabels:
    """Replace coded values by their label, e.g. ``"Greece"``.

    Attributes:
        value_columns: Columns holding coded values.
        separator: Separates the code from the label.
    """

    value_columns: tuple[str, ...]
    separator: str = CODE_SEPARATOR

    def apply(self, table: pd.DataFrame) -> pd.DataFrame:
        """Return the table with the label part of each coded value.

        A value without a separator gets an empty label.

        Args:
            table: Table with coded values in :attr:`value_columns`.

        Returns:
            The table with those columns replaced.

        Raises:
            TableFormatError: If a column is missing.
        """
        require_columns(table, self.value_columns, "KeepValueLabels")
        label_columns = {
            column: table[column].str.partition(self.separator)[2]
            for column in self.value_columns
        }
        return table.assign(**label_columns)


# ----------------------------------------------------------------------
# Into observations
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MeltColumns:
    """Turn a wide table into one row per key and value column.

    The value column's name becomes the series; for example OWID's
    ``wind_electricity`` column becomes rows of series
    ``wind_electricity``.

    Attributes:
        key_columns: Columns kept on every row, e.g. country and year.
        value_columns: Columns turned into rows.
        columns: Names of the series and value columns written.
    """

    key_columns: tuple[str, ...]
    value_columns: tuple[str, ...]
    columns: ObservationColumns = OBSERVATION_COLUMNS

    def apply(self, table: pd.DataFrame) -> pd.DataFrame:
        """Return the key columns plus a series and a value column.

        Args:
            table: Wide table.

        Returns:
            One row per original row and value column.

        Raises:
            TableFormatError: If a column is missing.
        """
        require_columns(
            table, (*self.key_columns, *self.value_columns), "MeltColumns"
        )
        return table.melt(
            id_vars=list(self.key_columns),
            value_vars=list(self.value_columns),
            var_name=self.columns.series,
            value_name=self.columns.value,
        )


@dataclass(frozen=True, slots=True)
class SourceColumns:
    """Which source columns hold the parts of an observation.

    Attributes:
        country: Country key, e.g. ``"geo"`` or ``"iso_code"``.
        period: Period text, e.g. ``"TIME_PERIOD"`` or ``"year"``.
        series: What is measured, e.g. ``"siec"``.
        value: The number, e.g. ``"OBS_VALUE"``.
    """

    country: str
    period: str
    series: str
    value: str


@dataclass(frozen=True, slots=True)
class ToObservations:
    """Keep the observation columns, with numbers as numbers.

    Empty values are dropped: in the observation format a missing value
    is an absent row.

    Attributes:
        source_columns: Where the parts of an observation are.
        columns: Names of the observation columns.
    """

    source_columns: SourceColumns
    columns: ObservationColumns = OBSERVATION_COLUMNS

    def apply(self, table: pd.DataFrame) -> pd.DataFrame:
        """Return country, period, series and value columns.

        Args:
            table: Source table.

        Returns:
            One row per non-empty value; the period is still text.

        Raises:
            TableFormatError: If a column is missing or a value is not
                a number.
        """
        source, target = self.source_columns, self.columns
        source_names = [source.country, source.period, source.series]
        require_columns(table, (*source_names, source.value), "ToObservations")
        text_columns = {
            target_name: table[source_name].astype(str)
            for source_name, target_name in zip(
                source_names,
                (target.country, target.period, target.series),
                strict=True,
            )
        }
        numbers = _parse_numbers(table[source.value])
        observations = pd.DataFrame(text_columns | {target.value: numbers})
        return observations[numbers.notna()].reset_index(drop=True)


def _parse_numbers(value_texts: pd.Series) -> pd.Series:
    """Convert values to floats; empty values become NaN.

    Args:
        value_texts: Values as read, text or numbers.

    Returns:
        Floats, NaN where the value was empty.

    Raises:
        TableFormatError: If a non-empty value is not a number.
    """
    numbers = pd.to_numeric(value_texts, errors="coerce")
    is_blank = value_texts.isna() | (value_texts.astype(str).str.strip() == "")
    is_malformed = numbers.isna() & ~is_blank
    if is_malformed.any():
        examples = sorted(set(value_texts[is_malformed].astype(str)))[:5]
        problem = f"ToObservations: expected numbers, found {examples}"
        raise TableFormatError(problem)
    return numbers.astype(float)


def _require_unique(
    table: pd.DataFrame, key_columns: Iterable[str], step_name: str
) -> None:
    """Raise if two rows share a key.

    Args:
        table: Table to check.
        key_columns: Columns that must identify a row.
        step_name: Transformer name, for the message.

    Raises:
        TableFormatError: If a key occurs twice.
    """
    is_repeated = table.duplicated(subset=list(key_columns), keep=False)
    if is_repeated.any():
        examples = table[is_repeated].head(3).to_dict("records")
        problem = f"{step_name}: repeated observations, e.g. {examples}"
        raise TableFormatError(problem)


@dataclass(frozen=True, slots=True)
class ParsePeriods:
    """Replace period text by year and sub-period.

    Attributes:
        columns: Names of the observation columns.
    """

    columns: ObservationColumns = OBSERVATION_COLUMNS

    def apply(self, table: pd.DataFrame) -> pd.DataFrame:
        """Return observations with integer year and sub-period columns.

        Each distinct period text is parsed once.

        Args:
            table: Observations with a period text column.

        Returns:
            The observations in observation column order.

        Raises:
            TableFormatError: If the period column is missing or a
                period is not a year, half-year or month.
        """
        columns = self.columns
        require_columns(table, (columns.period,), "ParsePeriods")
        periods: dict[str, Period] = {
            period_text: parse_period(period_text)
            for period_text in table[columns.period].unique()
        }
        period_texts = table[columns.period]
        parsed_table = table.drop(columns=columns.period).assign(
            **{
                columns.year: period_texts.map(
                    lambda text: periods[text].year
                ).astype(int),
                columns.sub_period: period_texts.map(
                    lambda text: periods[text].sub_period
                ).astype(int),
            }
        )
        return _order_columns(parsed_table, columns)


def _order_columns(
    table: pd.DataFrame, columns: ObservationColumns
) -> pd.DataFrame:
    """Return the observation columns in their standard order.

    Args:
        table: Observations, possibly in another column order.
        columns: Names of the observation columns.

    Returns:
        Country, year, sub-period, series and value columns.
    """
    return table.loc[:, [*columns.observation_key, columns.value]]


# ----------------------------------------------------------------------
# Scope
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class KeepCountries:
    """Keep countries in scope, keyed by ISO 3166-1 alpha-3 code.

    Rows whose country key is not in :attr:`country_keys`, such as
    regions and aggregates, are dropped. Repeated observations are only
    an error once in scope: regions may share an empty key.

    Attributes:
        country_keys: Source country key -> ISO3 code, e.g. ``"Greece"``
            -> ``"GRC"`` for Eurostat, or ISO3 -> ISO3.
        columns: Names of the observation columns.
    """

    country_keys: Mapping[str, str]
    columns: ObservationColumns = OBSERVATION_COLUMNS

    def apply(self, table: pd.DataFrame) -> pd.DataFrame:
        """Return the rows in scope with ISO3 codes as country keys.

        Args:
            table: Observations keyed by the source's country key.

        Returns:
            The rows in scope.

        Raises:
            TableFormatError: If two rows in scope share country,
                period and series, e.g. two keys mapped to one country.
        """
        columns = self.columns
        iso3_codes = table[columns.country].map(dict(self.country_keys))
        in_scope_table = table.assign(**{columns.country: iso3_codes})
        in_scope_table = in_scope_table[iso3_codes.notna()]
        _require_unique(
            in_scope_table, columns.observation_key, "KeepCountries"
        )
        return in_scope_table.reset_index(drop=True)


@dataclass(frozen=True, slots=True)
class KeepYears:
    """Keep years from a first year on.

    Attributes:
        first_year: First year kept.
        columns: Names of the observation columns.
    """

    first_year: int
    columns: ObservationColumns = OBSERVATION_COLUMNS

    def apply(self, table: pd.DataFrame) -> pd.DataFrame:
        """Return the rows from :attr:`first_year` on.

        Args:
            table: Observations.

        Returns:
            The rows of the kept years.
        """
        is_kept = table[self.columns.year] >= self.first_year
        return table[is_kept].reset_index(drop=True)


# ----------------------------------------------------------------------
# Checks
# ----------------------------------------------------------------------


class PlausibilityRule(Protocol):
    """A condition every value of a series must meet in each period."""

    @property
    def checked_series(self) -> str:
        """Series whose value is left out when the rule is violated."""
        ...

    @property
    def needed_series(self) -> tuple[str, ...]:
        """Series the rule compares."""
        ...

    def find_violations(self, period_table: pd.DataFrame) -> pd.Series:
        """Return which periods violate the rule.

        Args:
            period_table: One row per country and period, one column per
                series in :attr:`needed_series`.

        Returns:
            ``True`` where the rule is violated; a missing value never
            violates it.
        """
        ...


@dataclass(frozen=True, slots=True)
class NonNegative:
    """A series is never below zero, e.g. generation.

    Attributes:
        series: The series.
    """

    series: str

    @property
    def checked_series(self) -> str:
        """The series itself."""
        return self.series

    @property
    def needed_series(self) -> tuple[str, ...]:
        """The series itself."""
        return (self.series,)

    def find_violations(self, period_table: pd.DataFrame) -> pd.Series:
        """Return where the series is negative.

        Args:
            period_table: One column per series.

        Returns:
            ``True`` where the value is below zero.
        """
        return period_table[self.series] < 0

    def __str__(self) -> str:
        """Return the rule, e.g. ``"FOSSIL >= 0"``."""
        return f"{self.series} >= 0"


@dataclass(frozen=True, slots=True)
class NotAbove:
    """A part is never above its whole, e.g. fossil within total.

    Attributes:
        part: Series that is part of the whole; left out on violation.
        whole: Series it is part of.
    """

    part: str
    whole: str

    @property
    def checked_series(self) -> str:
        """The part."""
        return self.part

    @property
    def needed_series(self) -> tuple[str, ...]:
        """The part and the whole."""
        return (self.part, self.whole)

    def find_violations(self, period_table: pd.DataFrame) -> pd.Series:
        """Return where the part exceeds the whole.

        Args:
            period_table: One column per series.

        Returns:
            ``True`` where the part is above the whole.
        """
        return period_table[self.part] > period_table[self.whole]

    def __str__(self) -> str:
        """Return the rule, e.g. ``"FOSSIL <= TOTAL"``."""
        return f"{self.part} <= {self.whole}"


@dataclass(frozen=True, slots=True)
class InvalidateImplausible:
    """Leave out values that break a plausibility rule, with a warning.

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

    def apply(self, table: pd.DataFrame) -> pd.DataFrame:
        """Return the observations without implausible values.

        Args:
            table: Observations.

        Returns:
            The observations minus each violating rule's checked value.
        """
        columns = self.columns
        period_table = _to_period_table(table, columns)
        invalid_keys: list[tuple[str, int, int, str]] = []
        for rule in self.rules:
            if not set(rule.needed_series) <= set(period_table.columns):
                continue
            is_violated = rule.find_violations(period_table)
            violated_periods = period_table[is_violated].reset_index()
            for violation in violated_periods.to_dict("records"):
                self._warn(rule, violation)
                invalid_keys.append(
                    (
                        violation[columns.country],
                        violation[columns.year],
                        violation[columns.sub_period],
                        rule.checked_series,
                    )
                )
        return _drop_observations(table, invalid_keys, columns)

    def _warn(
        self, rule: PlausibilityRule, violation: Mapping[Hashable, object]
    ) -> None:
        """Log one violation.

        Args:
            rule: The violated rule.
            violation: Country, year, sub-period and the series values
                of the violating period.
        """
        columns = self.columns
        compared_values = {
            series: violation[series] for series in rule.needed_series
        }
        _logger.warning(
            "%s: %s %s sub-period %s violates %s %s; %s left out",
            self.source_label,
            violation[columns.country],
            violation[columns.year],
            violation[columns.sub_period],
            rule,
            compared_values,
            rule.checked_series,
        )


def _to_period_table(
    table: pd.DataFrame, columns: ObservationColumns
) -> pd.DataFrame:
    """Return one row per country and period, one column per series.

    Args:
        table: Observations.
        columns: Names of the observation columns.

    Returns:
        Values indexed by country, year and sub-period.
    """
    return table.pivot(
        index=list(columns.period_key),
        columns=columns.series,
        values=columns.value,
    )


def _drop_observations(
    table: pd.DataFrame,
    observation_keys: list[tuple[str, int, int, str]],
    columns: ObservationColumns,
) -> pd.DataFrame:
    """Return the observations without the given ones.

    Args:
        table: Observations.
        observation_keys: Country, year, sub-period and series of each
            observation to drop.
        columns: Names of the observation columns.

    Returns:
        The remaining observations.
    """
    if not observation_keys:
        return table
    table_keys = pd.MultiIndex.from_frame(
        table.loc[:, list(columns.observation_key)]
    )
    dropped_keys = pd.MultiIndex.from_tuples(observation_keys)
    is_dropped = table_keys.isin(dropped_keys)
    return table[~is_dropped].reset_index(drop=True)


@dataclass(frozen=True, slots=True)
class Gap:
    """Periods missing inside a series for one year.

    Attributes:
        country: ISO3 code.
        series: The series.
        year: The year.
        missing_sub_periods: Sub-periods without a value.
    """

    country: str
    series: str
    year: int
    missing_sub_periods: tuple[int, ...]


def find_gaps(
    table: pd.DataFrame,
    periods_per_year: int,
    columns: ObservationColumns = OBSERVATION_COLUMNS,
    first_sub_period: int = FIRST_SUB_PERIOD,
) -> list[Gap]:
    """Find years that miss periods inside a series.

    A year counts from the first year a series has any value for a
    country. Its last year counts only up to its last value, so a year
    still in progress is not a gap.

    Args:
        table: Observations.
        periods_per_year: Sub-periods in a complete year, e.g. 12.
        columns: Names of the observation columns.
        first_sub_period: Number of the first sub-period.

    Returns:
        One gap per country, series and year with missing periods, in
        country, series and year order.
    """
    gaps: list[Gap] = []
    series_groups = table.groupby([columns.country, columns.series])
    for (country, series), series_table in series_groups:
        observed_periods = set(
            zip(
                series_table[columns.year],
                series_table[columns.sub_period],
                strict=True,
            )
        )
        for year, missing_sub_periods in _find_missing_periods(
            observed_periods, periods_per_year, first_sub_period
        ):
            gaps.append(
                Gap(str(country), str(series), year, missing_sub_periods)
            )
    return gaps


def _find_missing_periods(
    observed_periods: set[tuple[int, int]],
    periods_per_year: int,
    first_sub_period: int,
) -> list[tuple[int, tuple[int, ...]]]:
    """Return the missing sub-periods of each year of one series.

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
class ReportGaps:
    """Log every gap inside a series as a warning, then a count.

    The table passes through unchanged; aggregation then leaves the gap
    years empty.

    Attributes:
        periods_per_year: Sub-periods in a complete year, e.g. 12.
        source_label: Names the source in the log, e.g.
            ``"nrg_cb_pem"``.
        columns: Names of the observation columns.
    """

    periods_per_year: int
    source_label: str
    columns: ObservationColumns = OBSERVATION_COLUMNS

    def apply(self, table: pd.DataFrame) -> pd.DataFrame:
        """Log the gaps of ``table`` and return it unchanged.

        Args:
            table: Observations.

        Returns:
            ``table`` itself.
        """
        gaps = find_gaps(table, self.periods_per_year, self.columns)
        for gap in gaps:
            _logger.warning(
                "%s: %s %s %d lacks sub-periods %s; year left empty",
                self.source_label,
                gap.country,
                gap.series,
                gap.year,
                list(gap.missing_sub_periods),
            )
        _logger.info("%s: %d gaps", self.source_label, len(gaps))
        return table


# ----------------------------------------------------------------------
# Values
# ----------------------------------------------------------------------


class SeriesFormula(Protocol):
    """Computes a new series from two others, period by period."""

    @property
    def operands(self) -> tuple[str, str]:
        """The two input series."""
        ...

    def compute(self, period_table: pd.DataFrame) -> pd.Series:
        """Return the new values.

        Args:
            period_table: One column per input series.

        Returns:
            One value per row; NaN where an input is missing.
        """
        ...


@dataclass(frozen=True, slots=True)
class Difference:
    """``minuend - subtrahend``, e.g. fossil = ``CF - CF_R``.

    Attributes:
        minuend: Series subtracted from.
        subtrahend: Series subtracted.
    """

    minuend: str
    subtrahend: str

    @property
    def operands(self) -> tuple[str, str]:
        """Minuend and subtrahend."""
        return (self.minuend, self.subtrahend)

    def compute(self, period_table: pd.DataFrame) -> pd.Series:
        """Return the difference.

        Args:
            period_table: One column per input series.

        Returns:
            Minuend minus subtrahend.
        """
        return period_table[self.minuend] - period_table[self.subtrahend]


@dataclass(frozen=True, slots=True)
class Ratio:
    """``numerator * factor / denominator``, e.g. GDP per capita.

    Attributes:
        numerator: Series divided.
        denominator: Series divided by.
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

    def compute(self, period_table: pd.DataFrame) -> pd.Series:
        """Return the ratio.

        Args:
            period_table: One column per input series.

        Returns:
            Numerator times factor over denominator; infinite where the
            denominator is zero.
        """
        return (
            period_table[self.numerator]
            * self.factor
            / period_table[self.denominator]
        )


@dataclass(frozen=True, slots=True)
class DeriveSeries:
    """Add a series computed from two others in the same period.

    A period gets a value only where both inputs have one and the
    result is finite.

    Attributes:
        target: Name of the new series.
        formula: How it is computed.
        columns: Names of the observation columns.
    """

    target: str
    formula: SeriesFormula
    columns: ObservationColumns = OBSERVATION_COLUMNS

    def apply(self, table: pd.DataFrame) -> pd.DataFrame:
        """Return the observations plus the new series.

        Args:
            table: Observations.

        Returns:
            The observations, followed by the new series' rows.

        Raises:
            TableFormatError: If the target series already exists.
        """
        columns = self.columns
        if (table[columns.series] == self.target).any():
            problem = f"DeriveSeries: series {self.target!r} already exists"
            raise TableFormatError(problem)
        operand_rows = table[table[columns.series].isin(self.formula.operands)]
        period_table = _to_period_table(operand_rows, columns)
        if not set(self.formula.operands) <= set(period_table.columns):
            return table
        derived_values = self.formula.compute(period_table)
        is_finite = derived_values.map(math.isfinite)
        derived_rows = (
            derived_values[is_finite]
            .rename(columns.value)
            .reset_index()
            .assign(**{columns.series: self.target})
        )
        return pd.concat(
            [table, _order_columns(derived_rows, columns)], ignore_index=True
        )


class Aggregation(enum.Enum):
    """How the sub-periods of a year are combined."""

    #: Flows, e.g. monthly generation to annual generation.
    SUM = "sum"
    #: Levels, e.g. half-yearly prices to an annual price.
    MEAN = "mean"


@dataclass(frozen=True, slots=True)
class AggregateToYears:
    """Combine sub-periods to years; only complete years get a value.

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

    def apply(self, table: pd.DataFrame) -> pd.DataFrame:
        """Return one observation per country, year and series.

        Args:
            table: Observations with sub-periods.

        Returns:
            Annual observations of the complete years.

        Raises:
            TableFormatError: If a sub-period exceeds
                :attr:`periods_per_year`.
        """
        columns = self.columns
        _require_sub_periods_within(
            table[columns.sub_period], self.periods_per_year
        )
        year_groups = table.groupby(
            [columns.country, columns.year, columns.series]
        )
        period_counts = year_groups[columns.sub_period].nunique()
        annual_values = year_groups[columns.value].agg(self.aggregation.value)
        is_complete = period_counts == self.periods_per_year
        annual_rows = (
            annual_values[is_complete]
            .reset_index()
            .assign(**{columns.sub_period: self.annual_sub_period})
        )
        return _order_columns(annual_rows, columns)


def _require_sub_periods_within(
    sub_periods: pd.Series, periods_per_year: int
) -> None:
    """Raise if a sub-period does not fit the expected year length.

    Args:
        sub_periods: Sub-period of each observation.
        periods_per_year: Sub-periods in a complete year.

    Raises:
        TableFormatError: If a sub-period exceeds ``periods_per_year``.
    """
    if (sub_periods > periods_per_year).any():
        found_sub_periods = sorted(int(sub) for sub in sub_periods.unique())
        problem = (
            f"AggregateToYears: sub-periods above {periods_per_year}, "
            f"found {found_sub_periods}"
        )
        raise TableFormatError(problem)


@dataclass(frozen=True, slots=True)
class ScaleSeries:
    """Multiply chosen series by a factor, e.g. GWh to TWh.

    Attributes:
        series: Series to scale.
        factor: Multiplier, e.g. 0.001.
        columns: Names of the observation columns.
    """

    series: tuple[str, ...]
    factor: float
    columns: ObservationColumns = OBSERVATION_COLUMNS

    def apply(self, table: pd.DataFrame) -> pd.DataFrame:
        """Return the observations with the chosen series scaled.

        Args:
            table: Observations.

        Returns:
            The observations; other series unchanged.
        """
        columns = self.columns
        is_scaled = table[columns.series].isin(self.series)
        scaled_values = table[columns.value].where(
            ~is_scaled, table[columns.value] * self.factor
        )
        return table.assign(**{columns.value: scaled_values})


@dataclass(frozen=True, slots=True)
class SelectSeries:
    """Keep chosen series under new names, e.g. output column names.

    Attributes:
        series_names: Source series -> new name. Other series are
            dropped.
        columns: Names of the observation columns.
    """

    series_names: Mapping[str, str]
    columns: ObservationColumns = OBSERVATION_COLUMNS

    def apply(self, table: pd.DataFrame) -> pd.DataFrame:
        """Return the chosen series, renamed.

        Args:
            table: Observations.

        Returns:
            The rows of the chosen series.
        """
        series_column = self.columns.series
        new_names = table[series_column].map(dict(self.series_names))
        selected_table = table.assign(**{series_column: new_names})
        return selected_table[new_names.notna()].reset_index(drop=True)
