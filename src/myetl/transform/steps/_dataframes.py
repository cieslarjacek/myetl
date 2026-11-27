"""Data frame helpers shared by the transformer modules (private)."""

__all__ = ["order_columns", "pivot_to_wide", "require_unique"]

from collections.abc import Iterable

import pandas as pd

from myetl.transform.observations import (
    DfFormatError,
    ObservationColumns,
)


def order_columns(
    long_df: pd.DataFrame, columns: ObservationColumns
) -> pd.DataFrame:
    """Return the observation columns in their standard order.

    Args:
        long_df: Observations, possibly in another column order.
        columns: Names of the observation columns in the right order.

    Returns:
        Country, year, sub-period, variable and value columns.
    """
    return long_df.loc[:, [*columns.observation_key, columns.value]]


def pivot_to_wide(
    long_df: pd.DataFrame, columns: ObservationColumns
) -> pd.DataFrame:
    """Return one row per country and period, one column per variable.

    Args:
        long_df: Observations.
        columns: Names of the observation columns.

    Returns:
        Values indexed by country, year and sub-period.
    """
    return long_df.pivot(
        index=list(columns.period_key),
        columns=columns.variable,
        values=columns.value,
    )


def require_unique(
    df: pd.DataFrame, key_columns: Iterable[str], step_name: str
) -> None:
    """Raise if two rows share a key.

    Args:
        df: Data frame to check.
        key_columns: Columns that must identify a row.
        step_name: Transformer name, for the message.

    Raises:
        DfFormatError: If a key occurs twice.
    """
    is_repeated = df.duplicated(subset=list(key_columns), keep=False)

    if is_repeated.any():
        examples = df[is_repeated].head(3).to_dict("records")
        problem = f"{step_name}: repeated observations, e.g. {examples}"
        raise DfFormatError(problem)
