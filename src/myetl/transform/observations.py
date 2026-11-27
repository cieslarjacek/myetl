"""Observations: the long data format every source is reshaped into.

One row per country, year, sub-period and variable, with one value. A
sub-period numbers the parts of a year: 1 for annual data, 1-2 for
half-years, 1-12 for months. Once a source is in this format, the same
transformers filter, check, aggregate and rename it, whatever the
source.

A missing value is an absent row, never a row with an empty value.
"""

__all__ = [
    "ANNUAL_SUB_PERIOD",
    "FIRST_SUB_PERIOD",
    "OBSERVATION_COLUMNS",
    "PERIOD_PATTERN",
    "DfFormatError",
    "ObservationColumns",
    "Period",
    "parse_period",
    "require_columns",
]

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

import pandas as pd


class DfFormatError(ValueError):
    """A data frame does not have the shape a transformer expects.

    Attributes:
        problem: What was expected and what was found.
    """

    def __init__(self, problem: str) -> None:
        """Store the problem.

        Args:
            problem: What was expected and what was found.
        """
        super().__init__(problem)
        self.problem = problem


@dataclass(frozen=True, slots=True)
class ObservationColumns:
    """Column names of the observation format.

    Attributes:
        country: The source's own country key, e.g. ``"Austria"``,
            until :class:`~myetl.transform.steps.CountryFilter` replaces
            it by the ISO 3166-1 alpha-3 code (``"AUT"``). The output
            data frame names the column ``country_iso3``.
        period: Period as the source writes it, e.g. ``"2017-07"``;
            replaced by ``year`` and ``sub_period`` once parsed.
        year: Calendar year.
        sub_period: Part of the year: 1 for annual data, 1-2 for
            half-years, 1-12 for months.
        variable: What is measured, e.g. ``"CF_R"``, later the output
            column name.
        value: The number.
    """

    country: str = "country"
    period: str = "period"
    year: str = "year"
    sub_period: str = "sub_period"
    variable: str = "variable"
    value: str = "value"

    @property
    def observation_key(self) -> tuple[str, str, str, str]:
        """Columns that identify one observation."""
        return (self.country, self.year, self.sub_period, self.variable)

    @property
    def period_key(self) -> tuple[str, str, str]:
        """Columns that identify one country and period."""
        return (self.country, self.year, self.sub_period)


#: Column names used by every transformer unless told otherwise.
OBSERVATION_COLUMNS: Final[ObservationColumns] = ObservationColumns()

#: Number of the first part of a year: sub-periods count from 1.
FIRST_SUB_PERIOD: Final[int] = 1

#: Sub-period of annual data and of a year aggregated from its parts.
ANNUAL_SUB_PERIOD: Final[int] = FIRST_SUB_PERIOD

#: Periods as SDMX and the other sources write them: a year (``2008``),
#: a half-year (``2008-S1``) or a month (``2008-01``).
#: Used with :meth:`~re.Pattern.fullmatch`, it returns a
#: :class:`~re.Match` object with ``year``, ``half``, and ``month``
#: named groups.
PERIOD_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?P<year>\d{4})(?:-S(?P<half>[12])|-(?P<month>0[1-9]|1[0-2]))?"
)


@dataclass(frozen=True, slots=True)
class Period:
    """A year and a part of it.

    Attributes:
        year: Calendar year.
        sub_period: 1 for a whole year, the half (1-2) or the month
            (1-12).
    """

    year: int
    sub_period: int


def parse_period(
    period_text: str,
    period_pattern: re.Pattern[str] = PERIOD_PATTERN,
    annual_sub_period: int = ANNUAL_SUB_PERIOD,
) -> Period:
    """Parse a period as written by a source.

    Args:
        period_text: E.g. ``"2008"``, ``"2008-S2"`` or ``"2008-07"``.
        period_pattern: Pattern with the groups ``year``, ``half`` and
            ``month``.
        annual_sub_period: Sub-period given to a plain year.

    Returns:
        The whole year: ``"2008"`` gives (2008, 1).
        The year's half: ``"2008-S2"`` gives (2008, 2).
        The year's month: ``"2008-07"`` gives (2008, 7).

    Raises:
        DfFormatError: If the text is not a year, half-year or month.
    """
    period_match = period_pattern.fullmatch(period_text)
    if period_match is None:
        problem = f"expected a year, half-year or month, found {period_text!r}"
        raise DfFormatError(problem)

    sub_period_text = period_match["half"] or period_match["month"]
    if sub_period_text is None:
        return Period(int(period_match["year"]), annual_sub_period)

    return Period(int(period_match["year"]), int(sub_period_text))


def require_columns(
    df: pd.DataFrame, required_columns: Iterable[str], step_name: str
) -> None:
    """Raise if a data frame lacks a column a transformer needs.

    Args:
        df: Data frame to check.
        required_columns: Columns the transformer reads.
        step_name: Transformer name, for the message.

    Raises:
        DfFormatError: If a column is missing.
    """
    present_columns = set(df.columns)
    missing_columns = [
        column for column in required_columns if column not in present_columns
    ]
    if missing_columns:
        problem = (
            f"{step_name}: missing columns {missing_columns}; "
            f"found {list(df.columns)}"
        )
        raise DfFormatError(problem)
