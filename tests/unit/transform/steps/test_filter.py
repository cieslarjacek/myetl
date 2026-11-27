"""Unit tests for ``myetl.transform.steps.filter``."""

import pytest
from observation_tables import observation_rows, observation_table

from myetl.transform.observations import TableFormatError
from myetl.transform.steps import CountryFilter, SeriesSelector, YearFilter


def test_countries_are_mapped_to_iso3_and_others_dropped() -> None:
    table = observation_table(
        [
            ("Greece", 2008, 1, "REN", 1.0),
            ("Euro area", 2008, 1, "REN", 2.0),
        ]
    )

    in_scope_table = CountryFilter({"Greece": "GRC"}).apply(table)

    assert observation_rows(in_scope_table) == [("GRC", 2008, 1, "REN", 1.0)]


def test_repeated_keys_outside_scope_are_allowed() -> None:
    table = observation_table(
        [
            ("", 2008, 1, "wind", 1.0),
            ("", 2008, 1, "wind", 2.0),
            ("AUT", 2008, 1, "wind", 3.0),
        ]
    )

    in_scope_table = CountryFilter({"AUT": "AUT"}).apply(table)

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
        CountryFilter(country_keys).apply(table)


def test_years_from_first_year_are_kept() -> None:
    table = observation_table(
        [("AUT", 1999, 1, "X", 1.0), ("AUT", 2000, 1, "X", 2.0)]
    )

    kept_table = YearFilter(first_year=2000).apply(table)

    assert observation_rows(kept_table) == [("AUT", 2000, 1, "X", 2.0)]


def test_chosen_series_are_kept_under_new_names() -> None:
    table = observation_table(
        [("AUT", 2017, 1, "TOTAL", 1.0), ("AUT", 2017, 1, "CF", 2.0)]
    )

    selected_table = SeriesSelector(
        {"TOTAL": "net_generation_eurostat_twh"}
    ).apply(table)

    assert observation_rows(selected_table) == [
        ("AUT", 2017, 1, "net_generation_eurostat_twh", 1.0)
    ]
