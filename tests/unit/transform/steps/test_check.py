"""Unit tests for ``myetl.transform.steps.check``."""

import logging

import pytest
from observation_dfs import (
    ALL_MONTHS,
    monthly_rows,
    observation_df,
    observation_rows,
)

from myetl.transform.steps import (
    Gap,
    GapReporter,
    ImplausibleValueRemover,
    NonNegativeRule,
    PartOfWholeRule,
    find_gaps,
)

FOSSIL_RULES = (
    NonNegativeRule("FOSSIL"),
    PartOfWholeRule("FOSSIL", "TOTAL"),
    PartOfWholeRule("RA000", "TOTAL"),
)


def test_rules_describe_themselves() -> None:
    assert [str(rule) for rule in FOSSIL_RULES] == [
        "FOSSIL >= 0",
        "FOSSIL <= TOTAL",
        "RA000 <= TOTAL",
    ]


def test_negative_value_is_left_out_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    df = observation_df(
        [
            ("FRA", 2017, 7, "FOSSIL", -3.5),
            ("FRA", 2017, 7, "TOTAL", 100.0),
            ("FRA", 2017, 8, "FOSSIL", 4.0),
        ]
    )

    with caplog.at_level(logging.WARNING, logger="myetl.transform.steps"):
        checked_df = ImplausibleValueRemover(FOSSIL_RULES, "nrg_cb_pem").apply(
            df
        )

    assert observation_rows(checked_df) == [
        ("FRA", 2017, 7, "TOTAL", 100.0),
        ("FRA", 2017, 8, "FOSSIL", 4.0),
    ]
    [warning_record] = caplog.records
    assert warning_record.getMessage() == (
        "nrg_cb_pem: FRA 2017 sub-period 7 violates FOSSIL >= 0 "
        "{'FOSSIL': -3.5}; FOSSIL left out"
    )


def test_part_above_whole_leaves_out_only_the_part() -> None:
    df = observation_df(
        [
            ("FRA", 2017, 7, "RA000", 120.0),
            ("FRA", 2017, 7, "TOTAL", 100.0),
        ]
    )

    checked_df = ImplausibleValueRemover(FOSSIL_RULES, "nrg_cb_pem").apply(df)

    assert observation_rows(checked_df) == [("FRA", 2017, 7, "TOTAL", 100.0)]


def test_missing_value_never_violates_a_rule() -> None:
    df = observation_df(
        [
            ("FRA", 2017, 7, "FOSSIL", 50.0),
            ("FRA", 2017, 8, "TOTAL", 10.0),
        ]
    )

    checked_df = ImplausibleValueRemover(FOSSIL_RULES, "nrg_cb_pem").apply(df)

    assert observation_rows(checked_df) == observation_rows(df)


def test_rule_on_absent_variable_is_skipped() -> None:
    df = observation_df([("FRA", 2017, 7, "TOTAL", 10.0)])

    checked_df = ImplausibleValueRemover(FOSSIL_RULES, "nrg_cb_pem").apply(df)

    assert observation_rows(checked_df) == observation_rows(df)


def test_complete_months_have_no_gaps() -> None:
    df = observation_df(monthly_rows("AUT", "CF", 2017, ALL_MONTHS))

    assert find_gaps(df, periods_per_year=12) == []


def test_month_missing_inside_variable_is_a_gap() -> None:
    months = [month for month in ALL_MONTHS if month != 7]
    df = observation_df(
        monthly_rows("NLD", "CF_R", 2017, months)
        + monthly_rows("NLD", "CF_R", 2018, ALL_MONTHS)
    )

    gaps = find_gaps(df, periods_per_year=12)

    assert gaps == [Gap("NLD", "CF_R", 2017, (7,))]


def test_months_before_variable_start_in_first_year_are_a_gap() -> None:
    df = observation_df(
        monthly_rows("ITA", "CF_R", 2019, range(3, 13))
        + monthly_rows("ITA", "CF_R", 2020, ALL_MONTHS)
    )

    gaps = find_gaps(df, periods_per_year=12)

    assert gaps == [Gap("ITA", "CF_R", 2019, (1, 2))]


def test_year_in_progress_is_not_a_gap() -> None:
    df = observation_df(
        monthly_rows("AUT", "CF", 2025, ALL_MONTHS)
        + monthly_rows("AUT", "CF", 2026, range(1, 9))
    )

    assert find_gaps(df, periods_per_year=12) == []


def test_whole_year_missing_inside_variable_is_a_gap() -> None:
    df = observation_df(
        [
            ("AUT", 2008, 1, "EUR", 1.0),
            ("AUT", 2008, 2, "EUR", 1.0),
            ("AUT", 2010, 1, "EUR", 1.0),
        ]
    )

    gaps = find_gaps(df, periods_per_year=2)

    assert gaps == [Gap("AUT", "EUR", 2009, (1, 2))]


def test_gaps_are_found_per_country_and_variable() -> None:
    df = observation_df(
        monthly_rows("AUT", "CF", 2017, range(1, 13, 2))
        + monthly_rows("AUT", "CF", 2018, [1])
        + monthly_rows("BEL", "CF", 2017, ALL_MONTHS)
    )

    gaps = find_gaps(df, periods_per_year=12)

    assert gaps == [Gap("AUT", "CF", 2017, (2, 4, 6, 8, 10, 12))]


def test_gaps_are_logged_and_df_passes_through(
    caplog: pytest.LogCaptureFixture,
) -> None:
    months = [month for month in ALL_MONTHS if month != 7]
    df = observation_df(
        monthly_rows("NLD", "CF_R", 2017, months)
        + monthly_rows("NLD", "CF_R", 2018, [1])
    )

    with caplog.at_level(logging.INFO, logger="myetl.transform.steps"):
        reported_df = GapReporter(12, "nrg_cb_pem").apply(df)

    assert reported_df is df
    assert [record.getMessage() for record in caplog.records] == [
        "nrg_cb_pem: NLD CF_R 2017 lacks sub-periods [7]; year left empty",
        "nrg_cb_pem: 1 gaps",
    ]
    assert caplog.records[0].levelno == logging.WARNING
