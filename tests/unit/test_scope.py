"""Unit tests for ``myetl.scope``."""

import pytest

from myetl import scope
from myetl.scope import COUNTRIES, Country, YearRange

EU27 = {
    "AUT", "BEL", "BGR", "CYP", "CZE", "DEU", "DNK", "ESP", "EST", "FIN",
    "FRA", "GRC", "HRV", "HUN", "IRL", "ITA", "LTU", "LUX", "LVA", "MLT",
    "NLD", "POL", "PRT", "ROU", "SVK", "SVN", "SWE",
}  # fmt: skip


# --------------------------------------------------------------------
# Countries
# --------------------------------------------------------------------


def test_countries_are_eu27_plus_norway_and_iceland() -> None:
    assert {c.iso3 for c in COUNTRIES} == EU27 | {"NOR", "ISL"}


def test_country_keys_are_unique() -> None:
    assert len({c.iso3 for c in COUNTRIES}) == len(COUNTRIES)
    assert len({c.eurostat_name for c in COUNTRIES}) == len(COUNTRIES)


def test_iso3_codes_have_expected_shape() -> None:
    for country in COUNTRIES:
        assert len(country.iso3) == 3
        assert country.iso3.isupper()


def test_countries_are_sorted_by_iso3() -> None:
    codes = [c.iso3 for c in COUNTRIES]
    assert codes == sorted(codes)


def test_eurostat_name_defaults_to_name() -> None:
    assert Country("CZE", "Czechia").eurostat_name == "Czechia"


def test_eurostat_alias_overrides_name() -> None:
    country = Country("CZE", "Czechia", eurostat_alias="Czech Republic")
    assert country.eurostat_name == "Czech Republic"


def test_key_sets_match_country_table() -> None:
    assert {c.iso3 for c in COUNTRIES} == scope.ISO3_CODES
    assert {c.eurostat_name for c in COUNTRIES} == scope.EUROSTAT_NAMES


def test_first_year() -> None:
    assert scope.FIRST_YEAR == 2000


# --------------------------------------------------------------------
# YearRange
# --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("year", "expected"),
    [(2007, False), (2008, True), (2009, True), (2010, False)],
)
def test_year_range_contains_both_bounds(year: int, expected: bool) -> None:
    assert (year in YearRange(2008, 2009)) is expected


def test_year_range_does_not_contain_year_as_text() -> None:
    assert "2008" not in YearRange(2008, 2009)


def test_year_range_allows_a_single_year() -> None:
    assert 2008 in YearRange(2008, 2008)


def test_year_range_rejects_reversed_bounds() -> None:
    with pytest.raises(ValueError, match="first"):
        YearRange(2025, 1990)
