"""Unit tests for ``myetl.transform.observations``."""

import pickle

import pandas as pd
import pytest

from myetl.transform.observations import (
    ANNUAL_SUB_PERIOD,
    OBSERVATION_COLUMNS,
    Period,
    TableFormatError,
    parse_period,
    require_columns,
)

# --------------------------------------------------------------------
# Periods
# --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("period_text", "expected_period"),
    [
        ("2008", Period(2008, 1)),
        ("2008-S1", Period(2008, 1)),
        ("2008-S2", Period(2008, 2)),
        ("2017-01", Period(2017, 1)),
        ("2017-07", Period(2017, 7)),
        ("2017-12", Period(2017, 12)),
    ],
)
def test_parse_period_reads_years_halves_and_months(
    period_text: str, expected_period: Period
) -> None:
    assert parse_period(period_text) == expected_period


@pytest.mark.parametrize(
    "period_text",
    ["", "08", "2008-S3", "2008-S0", "2008-00", "2008-13", "2008-Q1", "x"],
)
def test_parse_period_rejects_other_texts(period_text: str) -> None:
    with pytest.raises(TableFormatError, match="expected a year"):
        parse_period(period_text)


def test_annual_sub_period_is_one() -> None:
    assert ANNUAL_SUB_PERIOD == 1


def test_parse_period_uses_given_annual_sub_period() -> None:
    assert parse_period("2008", annual_sub_period=0) == Period(2008, 0)


# --------------------------------------------------------------------
# Columns
# --------------------------------------------------------------------


def test_observation_key_is_country_year_sub_period_series() -> None:
    assert OBSERVATION_COLUMNS.observation_key == (
        "country",
        "year",
        "sub_period",
        "series",
    )
    assert OBSERVATION_COLUMNS.period_key == (
        "country",
        "year",
        "sub_period",
    )


def test_require_columns_passes_when_all_present() -> None:
    table = pd.DataFrame(columns=["a", "b", "c"])

    require_columns(table, ("a", "c"), "Step")


def test_require_columns_names_missing_and_found() -> None:
    table = pd.DataFrame(columns=["a", "b"])

    with pytest.raises(TableFormatError) as raised:
        require_columns(table, ("c", "a", "d"), "Step")

    assert raised.value.problem == (
        "Step: missing columns ['c', 'd']; found ['a', 'b']"
    )


def test_table_format_error_survives_pickling() -> None:
    error = TableFormatError("Step: missing columns ['c']")

    # Safe: the bytes come from ``pickle.dumps`` one call earlier.
    restored_error = pickle.loads(pickle.dumps(error))  # noqa: S301

    assert restored_error.problem == error.problem
    assert str(restored_error) == str(error)
