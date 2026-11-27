"""Unit tests for ``myetl.transform.steps.calculate``."""

import pytest
from observation_dfs import (
    ALL_MONTHS,
    monthly_rows,
    observation_df,
    observation_rows,
)

from myetl.transform.observations import DfFormatError
from myetl.transform.steps import (
    Aggregation,
    Difference,
    Ratio,
    VariableDeriver,
    VariableScaler,
    YearAggregator,
)


def test_difference_is_derived_per_period() -> None:
    df = observation_df(
        [
            ("AUT", 2017, 1, "CF", 10.0),
            ("AUT", 2017, 1, "CF_R", 4.0),
            ("AUT", 2017, 2, "CF", 8.0),
        ]
    )

    derived_df = VariableDeriver("FOSSIL", Difference("CF", "CF_R")).apply(df)

    assert observation_rows(derived_df) == sorted(
        [*observation_rows(df), ("AUT", 2017, 1, "FOSSIL", 6.0)]
    )


def test_ratio_applies_factor() -> None:
    df = observation_df(
        [
            ("AUT", 2008, 1, "generation_twh", 64.38),
            ("AUT", 2008, 1, "population", 8_321_496.0),
        ]
    )
    per_capita = Ratio("generation_twh", "population", factor=1_000_000)

    derived_df = VariableDeriver("per_capita_mwh", per_capita).apply(df)

    [derived_row] = [
        row
        for row in observation_rows(derived_df)
        if row[3] == "per_capita_mwh"
    ]
    assert derived_row[4] == pytest.approx(64.38e6 / 8_321_496)


def test_division_by_zero_gives_no_value() -> None:
    df = observation_df(
        [("AUT", 2008, 1, "a", 1.0), ("AUT", 2008, 1, "b", 0.0)]
    )

    derived_df = VariableDeriver("a_per_b", Ratio("a", "b")).apply(df)

    assert observation_rows(derived_df) == observation_rows(df)


def test_absent_operand_variable_derives_nothing() -> None:
    df = observation_df([("ITA", 2017, 1, "CF", 10.0)])

    derived_df = VariableDeriver("FOSSIL", Difference("CF", "CF_R")).apply(df)

    assert observation_rows(derived_df) == observation_rows(df)


def test_deriving_an_existing_variable_fails() -> None:
    df = observation_df([("AUT", 2017, 1, "FOSSIL", 1.0)])

    with pytest.raises(DfFormatError, match="'FOSSIL' already exists"):
        VariableDeriver("FOSSIL", Difference("CF", "CF_R")).apply(df)


def test_complete_months_are_summed_to_a_year() -> None:
    df = observation_df(
        monthly_rows("AUT", "TOTAL", 2017, ALL_MONTHS)
        + monthly_rows("AUT", "TOTAL", 2018, range(1, 7))
    )

    annual_df = YearAggregator(12, Aggregation.SUM).apply(df)

    assert observation_rows(annual_df) == [("AUT", 2017, 1, "TOTAL", 12.0)]


def test_complete_halves_are_averaged_to_a_year() -> None:
    df = observation_df(
        [
            ("AUT", 2008, 1, "EUR", 0.17),
            ("AUT", 2008, 2, "EUR", 0.19),
            ("AUT", 2009, 1, "EUR", 0.20),
        ]
    )

    annual_df = YearAggregator(2, Aggregation.MEAN).apply(df)

    assert observation_rows(annual_df) == [("AUT", 2008, 1, "EUR", 0.18)]


def test_annual_values_pass_through_aggregation() -> None:
    df = observation_df([("AUT", 2008, 1, "REN", 30.0)])

    annual_df = YearAggregator(1, Aggregation.SUM).apply(df)

    assert observation_rows(annual_df) == observation_rows(df)


def test_sub_period_beyond_year_fails() -> None:
    df = observation_df(monthly_rows("AUT", "TOTAL", 2017, [1, 3]))

    with pytest.raises(DfFormatError, match=r"above 2, found \[1, 3\]"):
        YearAggregator(2, Aggregation.MEAN).apply(df)


def test_only_chosen_variables_are_scaled() -> None:
    df = observation_df(
        [("AUT", 2017, 1, "TOTAL", 1500.0), ("AUT", 2017, 1, "CF", 700.0)]
    )

    scaled_df = VariableScaler(("TOTAL",), factor=0.001).apply(df)

    assert observation_rows(scaled_df) == [
        ("AUT", 2017, 1, "CF", 700.0),
        ("AUT", 2017, 1, "TOTAL", 1.5),
    ]
