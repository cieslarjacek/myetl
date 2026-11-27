"""Unit tests for ``myetl.transform.steps.filter``."""

import pytest
from observation_dfs import observation_df, observation_rows

from myetl.transform.observations import DfFormatError
from myetl.transform.steps import CountryFilter, VariablesSelector, YearFilter


def test_countries_are_mapped_to_iso3_and_others_dropped() -> None:
    df = observation_df(
        [
            ("Greece", 2008, 1, "REN", 1.0),
            ("Euro area", 2008, 1, "REN", 2.0),
        ]
    )

    in_scope_df = CountryFilter({"Greece": "GRC"}).apply(df)

    assert observation_rows(in_scope_df) == [("GRC", 2008, 1, "REN", 1.0)]


def test_repeated_keys_outside_scope_are_allowed() -> None:
    df = observation_df(
        [
            ("", 2008, 1, "wind", 1.0),
            ("", 2008, 1, "wind", 2.0),
            ("AUT", 2008, 1, "wind", 3.0),
        ]
    )

    in_scope_df = CountryFilter({"AUT": "AUT"}).apply(df)

    assert observation_rows(in_scope_df) == [("AUT", 2008, 1, "wind", 3.0)]


def test_two_keys_mapped_to_one_country_fail() -> None:
    df = observation_df(
        [
            ("Germany", 2008, 1, "REN", 1.0),
            ("Germany (until 1990)", 2008, 1, "REN", 2.0),
        ]
    )
    country_keys = {"Germany": "DEU", "Germany (until 1990)": "DEU"}

    with pytest.raises(DfFormatError, match="repeated observations"):
        CountryFilter(country_keys).apply(df)


def test_years_from_first_year_are_kept() -> None:
    df = observation_df(
        [("AUT", 1999, 1, "X", 1.0), ("AUT", 2000, 1, "X", 2.0)]
    )

    kept_df = YearFilter(first_year=2000).apply(df)

    assert observation_rows(kept_df) == [("AUT", 2000, 1, "X", 2.0)]


def test_chosen_variables_are_kept_under_new_names() -> None:
    df = observation_df(
        [("AUT", 2017, 1, "TOTAL", 1.0), ("AUT", 2017, 1, "CF", 2.0)]
    )

    selected_df = VariablesSelector(
        {"TOTAL": "net_generation_eurostat_twh"}
    ).apply(df)

    assert observation_rows(selected_df) == [
        ("AUT", 2017, 1, "net_generation_eurostat_twh", 1.0)
    ]
