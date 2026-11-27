"""Observation tables built by hand, for the transformer tests.

On the test path (``pythonpath`` and ``mypy_path`` in
``pyproject.toml``), like ``scripts/`` for the fixture recorder.
"""

__all__ = [
    "ALL_MONTHS",
    "COMPARED_DECIMALS",
    "OBSERVATION_HEADER",
    "ObservationRow",
    "monthly_rows",
    "observation_rows",
    "observation_table",
]

from collections.abc import Iterable

import pandas as pd

type ObservationRow = tuple[str, int, int, str, float]

OBSERVATION_HEADER = ["country", "year", "sub_period", "series", "value"]


#: Decimals kept when comparing values, so that float noise such as
#: 0.18000000000000002 compares equal to 0.18.
COMPARED_DECIMALS = 9


def observation_table(
    rows: Iterable[ObservationRow], header: list[str] = OBSERVATION_HEADER
) -> pd.DataFrame:
    """Build an observation table from observation rows."""
    return pd.DataFrame(list(rows), columns=header)


def observation_rows(
    df: pd.DataFrame,
    header: list[str] = OBSERVATION_HEADER,
    compared_decimals: int = COMPARED_DECIMALS,
) -> list[ObservationRow]:
    """Return the rows of an observation table, sorted."""
    return sorted(
        (
            str(country),
            int(year),
            int(sub_period),
            str(series),
            round(float(value), compared_decimals),
        )
        for country, year, sub_period, series, value in df.loc[
            :, header
        ].itertuples(index=False, name=None)
    )


def monthly_rows(
    country: str, series: str, year: int, months: Iterable[int]
) -> list[ObservationRow]:
    """Return one observation of 1.0 per month."""
    return [(country, year, month, series, 1.0) for month in months]


ALL_MONTHS = range(1, 13)
