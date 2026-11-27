"""Unit tests for ``myetl.transform.steps.calculate``."""

import pytest
from observation_tables import (
    ALL_MONTHS,
    monthly_rows,
    observation_rows,
    observation_table,
)

from myetl.transform.observations import TableFormatError
from myetl.transform.steps import (
    Aggregation,
    Difference,
    Ratio,
    SeriesDeriver,
    SeriesScaler,
    YearAggregator,
)


def test_difference_is_derived_per_period() -> None:
    table = observation_table(
        [
            ("AUT", 2017, 1, "CF", 10.0),
            ("AUT", 2017, 1, "CF_R", 4.0),
            ("AUT", 2017, 2, "CF", 8.0),
        ]
    )

    derived_table = SeriesDeriver("FOSSIL", Difference("CF", "CF_R")).apply(
        table
    )

    assert observation_rows(derived_table) == sorted(
        [*observation_rows(table), ("AUT", 2017, 1, "FOSSIL", 6.0)]
    )


def test_ratio_applies_factor() -> None:
    table = observation_table(
        [
            ("AUT", 2008, 1, "generation_twh", 64.38),
            ("AUT", 2008, 1, "population", 8_321_496.0),
        ]
    )
    per_capita = Ratio("generation_twh", "population", factor=1_000_000)

    derived_table = SeriesDeriver("per_capita_mwh", per_capita).apply(table)

    [derived_row] = [
        row
        for row in observation_rows(derived_table)
        if row[3] == "per_capita_mwh"
    ]
    assert derived_row[4] == pytest.approx(64.38e6 / 8_321_496)


def test_division_by_zero_gives_no_value() -> None:
    table = observation_table(
        [("AUT", 2008, 1, "a", 1.0), ("AUT", 2008, 1, "b", 0.0)]
    )

    derived_table = SeriesDeriver("a_per_b", Ratio("a", "b")).apply(table)

    assert observation_rows(derived_table) == observation_rows(table)


def test_absent_operand_series_derives_nothing() -> None:
    table = observation_table([("ITA", 2017, 1, "CF", 10.0)])

    derived_table = SeriesDeriver("FOSSIL", Difference("CF", "CF_R")).apply(
        table
    )

    assert observation_rows(derived_table) == observation_rows(table)


def test_deriving_an_existing_series_fails() -> None:
    table = observation_table([("AUT", 2017, 1, "FOSSIL", 1.0)])

    with pytest.raises(TableFormatError, match="'FOSSIL' already exists"):
        SeriesDeriver("FOSSIL", Difference("CF", "CF_R")).apply(table)


def test_complete_months_are_summed_to_a_year() -> None:
    table = observation_table(
        monthly_rows("AUT", "TOTAL", 2017, ALL_MONTHS)
        + monthly_rows("AUT", "TOTAL", 2018, range(1, 7))
    )

    annual_table = YearAggregator(12, Aggregation.SUM).apply(table)

    assert observation_rows(annual_table) == [("AUT", 2017, 1, "TOTAL", 12.0)]


def test_complete_halves_are_averaged_to_a_year() -> None:
    table = observation_table(
        [
            ("AUT", 2008, 1, "EUR", 0.17),
            ("AUT", 2008, 2, "EUR", 0.19),
            ("AUT", 2009, 1, "EUR", 0.20),
        ]
    )

    annual_table = YearAggregator(2, Aggregation.MEAN).apply(table)

    assert observation_rows(annual_table) == [("AUT", 2008, 1, "EUR", 0.18)]


def test_annual_values_pass_through_aggregation() -> None:
    table = observation_table([("AUT", 2008, 1, "REN", 30.0)])

    annual_table = YearAggregator(1, Aggregation.SUM).apply(table)

    assert observation_rows(annual_table) == observation_rows(table)


def test_sub_period_beyond_year_fails() -> None:
    table = observation_table(monthly_rows("AUT", "TOTAL", 2017, [1, 3]))

    with pytest.raises(TableFormatError, match=r"above 2, found \[1, 3\]"):
        YearAggregator(2, Aggregation.MEAN).apply(table)


def test_only_chosen_series_are_scaled() -> None:
    table = observation_table(
        [("AUT", 2017, 1, "TOTAL", 1500.0), ("AUT", 2017, 1, "CF", 700.0)]
    )

    scaled_table = SeriesScaler(("TOTAL",), factor=0.001).apply(table)

    assert observation_rows(scaled_table) == [
        ("AUT", 2017, 1, "CF", 700.0),
        ("AUT", 2017, 1, "TOTAL", 1.5),
    ]
