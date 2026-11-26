"""Unit tests for ``myetl.transform.steps``.

Every test builds a small table by hand, so each rule is checked
without the recorded fixtures.
"""

import logging
from collections.abc import Iterable

import pandas as pd
import pytest

from myetl.transform.observations import TableFormatError
from myetl.transform.steps import (
    AggregateToYears,
    Aggregation,
    DeriveSeries,
    Difference,
    Gap,
    InvalidateImplausible,
    KeepCountries,
    KeepValueCodes,
    KeepValueLabels,
    KeepYears,
    MeltColumns,
    NonNegative,
    NotAbove,
    ParsePeriods,
    Ratio,
    RenameColumnsToCodes,
    ReportGaps,
    ScaleSeries,
    SelectSeries,
    SourceColumns,
    ToObservations,
    find_gaps,
)

type ObservationRow = tuple[str, int, int, str, float]

OBSERVATION_HEADER = ["country_iso3", "year", "sub_period", "series", "value"]


def observation_table(rows: Iterable[ObservationRow]) -> pd.DataFrame:
    """Build an observation table from observation rows."""
    return pd.DataFrame(list(rows), columns=OBSERVATION_HEADER)


def observation_rows(table: pd.DataFrame) -> list[ObservationRow]:
    """Return the rows of an observation table, sorted."""
    return sorted(
        (
            str(country),
            int(year),
            int(sub_period),
            str(series),
            round(float(value), 9),
        )
        for country, year, sub_period, series, value in table.loc[
            :, OBSERVATION_HEADER
        ].itertuples(index=False, name=None)
    )


def monthly_rows(
    country: str, series: str, year: int, months: Iterable[int]
) -> list[ObservationRow]:
    """Return one observation of 1.0 per month."""
    return [(country, year, month, series, 1.0) for month in months]


ALL_MONTHS = range(1, 13)

# --------------------------------------------------------------------
# Header and values
# --------------------------------------------------------------------


def test_coded_column_names_become_codes() -> None:
    table = pd.DataFrame(columns=["STRUCTURE", "geo: Geopolitical entity"])

    renamed_table = RenameColumnsToCodes().apply(table)

    assert list(renamed_table.columns) == ["STRUCTURE", "geo"]


def test_repeated_column_codes_fail() -> None:
    table = pd.DataFrame(columns=["geo: Country", "geo: Region"])

    with pytest.raises(TableFormatError, match=r"repeated codes \['geo'\]"):
        RenameColumnsToCodes().apply(table)


def test_value_codes_keep_part_before_separator() -> None:
    table = pd.DataFrame(
        {"siec": ["CF_R: Renewable", "TOTAL"], "x": ["a", "b"]}
    )

    coded_table = KeepValueCodes(("siec",)).apply(table)

    assert coded_table.to_dict("list") == {
        "siec": ["CF_R", "TOTAL"],
        "x": ["a", "b"],
    }


def test_value_labels_keep_part_after_separator() -> None:
    table = pd.DataFrame({"geo": ["EL: Greece", "XX"]})

    labelled_table = KeepValueLabels(("geo",)).apply(table)

    assert labelled_table["geo"].tolist() == ["Greece", ""]


def test_value_codes_use_given_separator() -> None:
    table = pd.DataFrame({"geo": ["AT|Austria"]})

    coded_table = KeepValueCodes(("geo",), separator="|").apply(table)

    assert coded_table["geo"].tolist() == ["AT"]


def test_value_codes_of_missing_column_fail() -> None:
    with pytest.raises(TableFormatError, match="KeepValueCodes"):
        KeepValueCodes(("siec",)).apply(pd.DataFrame(columns=["geo"]))


def test_input_table_is_left_unchanged() -> None:
    table = pd.DataFrame({"geo": ["EL: Greece"]})

    KeepValueLabels(("geo",)).apply(table)

    assert table["geo"].tolist() == ["EL: Greece"]


# --------------------------------------------------------------------
# Into observations
# --------------------------------------------------------------------


def test_melt_turns_value_columns_into_series_rows() -> None:
    table = pd.DataFrame(
        {"iso_code": ["AUT"], "year": ["2008"], "wind": ["2.0"], "solar": [""]}
    )

    long_table = MeltColumns(("iso_code", "year"), ("wind", "solar")).apply(
        table
    )

    assert long_table.to_dict("records") == [
        {"iso_code": "AUT", "year": "2008", "series": "wind", "value": "2.0"},
        {"iso_code": "AUT", "year": "2008", "series": "solar", "value": ""},
    ]


def test_melt_of_missing_column_fails() -> None:
    table = pd.DataFrame(columns=["iso_code", "year"])

    with pytest.raises(TableFormatError, match="MeltColumns"):
        MeltColumns(("iso_code", "year"), ("wind",)).apply(table)


EUROSTAT_COLUMNS = SourceColumns("geo", "TIME_PERIOD", "siec", "OBS_VALUE")


def test_observations_take_the_named_columns_as_numbers() -> None:
    table = pd.DataFrame(
        {
            "geo": ["AT"],
            "TIME_PERIOD": ["2017-01"],
            "siec": ["CF"],
            "OBS_VALUE": ["9227.314"],
            "OBS_FLAG": ["e"],
        }
    )

    observations = ToObservations(EUROSTAT_COLUMNS).apply(table)

    assert observations.to_dict("records") == [
        {
            "country_iso3": "AT",
            "period": "2017-01",
            "series": "CF",
            "value": 9227.314,
        }
    ]


def test_empty_and_null_values_become_absent_rows() -> None:
    table = pd.DataFrame(
        {
            "geo": ["AT", "BE", "CY"],
            "TIME_PERIOD": ["2008", "2008", "2008"],
            "siec": ["CF", "CF", "CF"],
            "OBS_VALUE": ["", None, "1.5"],
        }
    )

    observations = ToObservations(EUROSTAT_COLUMNS).apply(table)

    assert observations["country_iso3"].tolist() == ["CY"]


def test_non_numeric_value_fails() -> None:
    table = pd.DataFrame(
        {
            "geo": ["AT"],
            "TIME_PERIOD": ["2008"],
            "siec": ["CF"],
            "OBS_VALUE": [":"],
        }
    )

    with pytest.raises(
        TableFormatError, match=r"expected numbers, found \[':'\]"
    ):
        ToObservations(EUROSTAT_COLUMNS).apply(table)


def test_observations_from_missing_column_fail() -> None:
    table = pd.DataFrame(columns=["geo", "TIME_PERIOD", "siec"])

    with pytest.raises(TableFormatError, match="OBS_VALUE"):
        ToObservations(EUROSTAT_COLUMNS).apply(table)


def test_periods_become_year_and_sub_period() -> None:
    table = pd.DataFrame(
        {
            "country_iso3": ["AUT", "AUT", "AUT"],
            "period": ["2008", "2008-S2", "2017-07"],
            "series": ["REN", "EUR", "CF"],
            "value": [1.0, 2.0, 3.0],
        }
    )

    parsed_table = ParsePeriods().apply(table)

    assert list(parsed_table.columns) == OBSERVATION_HEADER
    assert observation_rows(parsed_table) == [
        ("AUT", 2008, 1, "REN", 1.0),
        ("AUT", 2008, 2, "EUR", 2.0),
        ("AUT", 2017, 7, "CF", 3.0),
    ]


def test_unparseable_period_fails() -> None:
    table = pd.DataFrame(
        {
            "country_iso3": ["AUT"],
            "period": ["2008-Q1"],
            "series": ["X"],
            "value": [1.0],
        }
    )

    with pytest.raises(TableFormatError, match="2008-Q1"):
        ParsePeriods().apply(table)


# --------------------------------------------------------------------
# Scope
# --------------------------------------------------------------------


def test_countries_are_mapped_to_iso3_and_others_dropped() -> None:
    table = observation_table(
        [
            ("Greece", 2008, 1, "REN", 1.0),
            ("Euro area", 2008, 1, "REN", 2.0),
        ]
    )

    in_scope_table = KeepCountries({"Greece": "GRC"}).apply(table)

    assert observation_rows(in_scope_table) == [("GRC", 2008, 1, "REN", 1.0)]


def test_repeated_keys_outside_scope_are_allowed() -> None:
    table = observation_table(
        [
            ("", 2008, 1, "wind", 1.0),
            ("", 2008, 1, "wind", 2.0),
            ("AUT", 2008, 1, "wind", 3.0),
        ]
    )

    in_scope_table = KeepCountries({"AUT": "AUT"}).apply(table)

    assert observation_rows(in_scope_table) == [("AUT", 2008, 1, "wind", 3.0)]


def test_two_keys_mapped_to_one_country_fail() -> None:
    table = observation_table(
        [
            ("Germany", 2008, 1, "REN", 1.0),
            ("Germany (until 1990)", 2008, 1, "REN", 2.0),
        ]
    )
    country_keys = {"Germany": "DEU", "Germany (until 1990)": "DEU"}

    with pytest.raises(TableFormatError, match="repeated observations"):
        KeepCountries(country_keys).apply(table)


def test_years_from_first_year_are_kept() -> None:
    table = observation_table(
        [("AUT", 1999, 1, "X", 1.0), ("AUT", 2000, 1, "X", 2.0)]
    )

    kept_table = KeepYears(first_year=2000).apply(table)

    assert observation_rows(kept_table) == [("AUT", 2000, 1, "X", 2.0)]


# --------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------

FOSSIL_RULES = (
    NonNegative("FOSSIL"),
    NotAbove("FOSSIL", "TOTAL"),
    NotAbove("RA000", "TOTAL"),
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
    table = observation_table(
        [
            ("FRA", 2017, 7, "FOSSIL", -3.5),
            ("FRA", 2017, 7, "TOTAL", 100.0),
            ("FRA", 2017, 8, "FOSSIL", 4.0),
        ]
    )

    with caplog.at_level(logging.WARNING, logger="myetl.transform.steps"):
        checked_table = InvalidateImplausible(
            FOSSIL_RULES, "nrg_cb_pem"
        ).apply(table)

    assert observation_rows(checked_table) == [
        ("FRA", 2017, 7, "TOTAL", 100.0),
        ("FRA", 2017, 8, "FOSSIL", 4.0),
    ]
    [warning_record] = caplog.records
    assert warning_record.getMessage() == (
        "nrg_cb_pem: FRA 2017 sub-period 7 violates FOSSIL >= 0 "
        "{'FOSSIL': -3.5}; FOSSIL left out"
    )


def test_part_above_whole_leaves_out_only_the_part() -> None:
    table = observation_table(
        [
            ("FRA", 2017, 7, "RA000", 120.0),
            ("FRA", 2017, 7, "TOTAL", 100.0),
        ]
    )

    checked_table = InvalidateImplausible(FOSSIL_RULES, "nrg_cb_pem").apply(
        table
    )

    assert observation_rows(checked_table) == [
        ("FRA", 2017, 7, "TOTAL", 100.0)
    ]


def test_missing_value_never_violates_a_rule() -> None:
    table = observation_table(
        [
            ("FRA", 2017, 7, "FOSSIL", 50.0),
            ("FRA", 2017, 8, "TOTAL", 10.0),
        ]
    )

    checked_table = InvalidateImplausible(FOSSIL_RULES, "nrg_cb_pem").apply(
        table
    )

    assert observation_rows(checked_table) == observation_rows(table)


def test_rule_on_absent_series_is_skipped() -> None:
    table = observation_table([("FRA", 2017, 7, "TOTAL", 10.0)])

    checked_table = InvalidateImplausible(FOSSIL_RULES, "nrg_cb_pem").apply(
        table
    )

    assert observation_rows(checked_table) == observation_rows(table)


def test_complete_months_have_no_gaps() -> None:
    table = observation_table(monthly_rows("AUT", "CF", 2017, ALL_MONTHS))

    assert find_gaps(table, periods_per_year=12) == []


def test_month_missing_inside_series_is_a_gap() -> None:
    months = [month for month in ALL_MONTHS if month != 7]
    table = observation_table(
        monthly_rows("NLD", "CF_R", 2017, months)
        + monthly_rows("NLD", "CF_R", 2018, ALL_MONTHS)
    )

    gaps = find_gaps(table, periods_per_year=12)

    assert gaps == [Gap("NLD", "CF_R", 2017, (7,))]


def test_months_before_series_start_in_first_year_are_a_gap() -> None:
    table = observation_table(
        monthly_rows("ITA", "CF_R", 2019, range(3, 13))
        + monthly_rows("ITA", "CF_R", 2020, ALL_MONTHS)
    )

    gaps = find_gaps(table, periods_per_year=12)

    assert gaps == [Gap("ITA", "CF_R", 2019, (1, 2))]


def test_year_in_progress_is_not_a_gap() -> None:
    table = observation_table(
        monthly_rows("AUT", "CF", 2025, ALL_MONTHS)
        + monthly_rows("AUT", "CF", 2026, range(1, 9))
    )

    assert find_gaps(table, periods_per_year=12) == []


def test_whole_year_missing_inside_series_is_a_gap() -> None:
    table = observation_table(
        [
            ("AUT", 2008, 1, "EUR", 1.0),
            ("AUT", 2008, 2, "EUR", 1.0),
            ("AUT", 2010, 1, "EUR", 1.0),
        ]
    )

    gaps = find_gaps(table, periods_per_year=2)

    assert gaps == [Gap("AUT", "EUR", 2009, (1, 2))]


def test_gaps_are_found_per_country_and_series() -> None:
    table = observation_table(
        monthly_rows("AUT", "CF", 2017, range(1, 13, 2))
        + monthly_rows("AUT", "CF", 2018, [1])
        + monthly_rows("BEL", "CF", 2017, ALL_MONTHS)
    )

    gaps = find_gaps(table, periods_per_year=12)

    assert gaps == [Gap("AUT", "CF", 2017, (2, 4, 6, 8, 10, 12))]


def test_gaps_are_logged_and_table_passes_through(
    caplog: pytest.LogCaptureFixture,
) -> None:
    months = [month for month in ALL_MONTHS if month != 7]
    table = observation_table(
        monthly_rows("NLD", "CF_R", 2017, months)
        + monthly_rows("NLD", "CF_R", 2018, [1])
    )

    with caplog.at_level(logging.INFO, logger="myetl.transform.steps"):
        reported_table = ReportGaps(12, "nrg_cb_pem").apply(table)

    assert reported_table is table
    assert [record.getMessage() for record in caplog.records] == [
        "nrg_cb_pem: NLD CF_R 2017 lacks sub-periods [7]; year left empty",
        "nrg_cb_pem: 1 gaps",
    ]
    assert caplog.records[0].levelno == logging.WARNING


# --------------------------------------------------------------------
# Values
# --------------------------------------------------------------------


def test_difference_is_derived_per_period() -> None:
    table = observation_table(
        [
            ("AUT", 2017, 1, "CF", 10.0),
            ("AUT", 2017, 1, "CF_R", 4.0),
            ("AUT", 2017, 2, "CF", 8.0),
        ]
    )

    derived_table = DeriveSeries("FOSSIL", Difference("CF", "CF_R")).apply(
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

    derived_table = DeriveSeries("per_capita_mwh", per_capita).apply(table)

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

    derived_table = DeriveSeries("a_per_b", Ratio("a", "b")).apply(table)

    assert observation_rows(derived_table) == observation_rows(table)


def test_absent_operand_series_derives_nothing() -> None:
    table = observation_table([("ITA", 2017, 1, "CF", 10.0)])

    derived_table = DeriveSeries("FOSSIL", Difference("CF", "CF_R")).apply(
        table
    )

    assert observation_rows(derived_table) == observation_rows(table)


def test_deriving_an_existing_series_fails() -> None:
    table = observation_table([("AUT", 2017, 1, "FOSSIL", 1.0)])

    with pytest.raises(TableFormatError, match="'FOSSIL' already exists"):
        DeriveSeries("FOSSIL", Difference("CF", "CF_R")).apply(table)


def test_complete_months_are_summed_to_a_year() -> None:
    table = observation_table(
        monthly_rows("AUT", "TOTAL", 2017, ALL_MONTHS)
        + monthly_rows("AUT", "TOTAL", 2018, range(1, 7))
    )

    annual_table = AggregateToYears(12, Aggregation.SUM).apply(table)

    assert observation_rows(annual_table) == [("AUT", 2017, 1, "TOTAL", 12.0)]


def test_complete_halves_are_averaged_to_a_year() -> None:
    table = observation_table(
        [
            ("AUT", 2008, 1, "EUR", 0.17),
            ("AUT", 2008, 2, "EUR", 0.19),
            ("AUT", 2009, 1, "EUR", 0.20),
        ]
    )

    annual_table = AggregateToYears(2, Aggregation.MEAN).apply(table)

    assert observation_rows(annual_table) == [("AUT", 2008, 1, "EUR", 0.18)]


def test_annual_values_pass_through_aggregation() -> None:
    table = observation_table([("AUT", 2008, 1, "REN", 30.0)])

    annual_table = AggregateToYears(1, Aggregation.SUM).apply(table)

    assert observation_rows(annual_table) == observation_rows(table)


def test_sub_period_beyond_year_fails() -> None:
    table = observation_table(monthly_rows("AUT", "TOTAL", 2017, [1, 3]))

    with pytest.raises(TableFormatError, match=r"above 2, found \[1, 3\]"):
        AggregateToYears(2, Aggregation.MEAN).apply(table)


def test_only_chosen_series_are_scaled() -> None:
    table = observation_table(
        [("AUT", 2017, 1, "TOTAL", 1500.0), ("AUT", 2017, 1, "CF", 700.0)]
    )

    scaled_table = ScaleSeries(("TOTAL",), factor=0.001).apply(table)

    assert observation_rows(scaled_table) == [
        ("AUT", 2017, 1, "CF", 700.0),
        ("AUT", 2017, 1, "TOTAL", 1.5),
    ]


def test_chosen_series_are_kept_under_new_names() -> None:
    table = observation_table(
        [("AUT", 2017, 1, "TOTAL", 1.0), ("AUT", 2017, 1, "CF", 2.0)]
    )

    selected_table = SelectSeries(
        {"TOTAL": "net_generation_eurostat_twh"}
    ).apply(table)

    assert observation_rows(selected_table) == [
        ("AUT", 2017, 1, "net_generation_eurostat_twh", 1.0)
    ]
