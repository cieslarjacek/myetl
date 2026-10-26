"""Unit tests for the source contracts in ``myetl.sources``."""

from urllib.parse import parse_qsl, urlsplit

import pytest

from myetl import sources
from myetl.sources import (
    COUNTRIES,
    HOUSEHOLD_PRICE,
    MONTHLY_GENERATION,
    RES_SHARE,
    EurostatDataset,
    world_bank_url,
)

EU27 = {
    "AUT", "BEL", "BGR", "CYP", "CZE", "DEU", "DNK", "ESP", "EST", "FIN",
    "FRA", "GRC", "HRV", "HUN", "IRL", "ITA", "LTU", "LUX", "LVA", "MLT",
    "NLD", "POL", "PRT", "ROU", "SVK", "SVN", "SWE",
}  # fmt: skip


def _query(url: str) -> dict[str, str]:
    """Return the decoded query of ``url`` as a dict."""
    return dict(parse_qsl(urlsplit(url).query))


# --------------------------------------------------------------------
# Scope
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


def test_first_year() -> None:
    assert sources.FIRST_YEAR == 2000


# --------------------------------------------------------------------
# Eurostat
# --------------------------------------------------------------------


def test_eurostat_url_path() -> None:
    parts = urlsplit(RES_SHARE.url())
    assert parts.scheme == "https"
    assert parts.netloc == "ec.europa.eu"
    assert parts.path == (
        "/eurostat/api/dissemination/sdmx/3.0/data/dataflow/ESTAT/"
        "NRG_IND_REN/1.0"
    )


def test_eurostat_url_requests_labelled_sdmx_csv() -> None:
    query = _query(MONTHLY_GENERATION.url())
    assert query["format"] == "csvdata"
    assert query["formatVersion"] == "2.0"
    assert query["labels"] == "both"
    assert query["compress"] == "false"


def test_eurostat_url_has_no_geo_filter() -> None:
    for dataset in (RES_SHARE, HOUSEHOLD_PRICE, MONTHLY_GENERATION):
        assert not any(k.upper() == "C[GEO]" for k in _query(dataset.url()))


def test_eurostat_url_joins_multiple_values_with_comma() -> None:
    query = _query(HOUSEHOLD_PRICE.url())
    assert query["c[CURRENCY]"] == "EUR,PPS"
    assert query["c[NRG_CONS]"] == "KWH2500-4999"
    assert query["c[TAX]"] == "I_TAX"


def test_monthly_generation_filters() -> None:
    query = _query(MONTHLY_GENERATION.url())
    assert query["c[FREQ]"] == "M"
    assert query["c[SIEC]"] == "TOTAL,RA000,FE"
    assert query["c[UNIT]"] == "GWH"


def test_eurostat_url_full_time_range_by_default() -> None:
    assert "c[TIME_PERIOD]" not in _query(RES_SHARE.url())


@pytest.mark.parametrize(
    ("since", "until", "expected"),
    [
        ("2008-01", "2009-06", "ge:2008-01+le:2009-06"),
        ("2008", None, "ge:2008"),
        (None, "2009-S1", "le:2009-S1"),
    ],
)
def test_eurostat_url_time_range(
    since: str | None, until: str | None, expected: str
) -> None:
    url = MONTHLY_GENERATION.url(since=since, until=until)
    assert _query(url)["c[TIME_PERIOD]"] == expected
    # "+" must be percent-encoded, or the server reads it as a space.
    assert "%2B" in url or "+" not in expected


def test_eurostat_dataset_version_is_configurable() -> None:
    dataset = EurostatDataset("nrg_x", (), version="2.0")
    assert urlsplit(dataset.url()).path.endswith("/NRG_X/2.0")


# --------------------------------------------------------------------
# World Bank
# --------------------------------------------------------------------


def test_world_bank_url() -> None:
    url = world_bank_url("SP.POP.TOTL", 1990, 2025, page=3, per_page=200)
    parts = urlsplit(url)
    assert parts.scheme == "https"
    assert parts.netloc == "api.worldbank.org"
    assert parts.path == "/v2/country/all/indicator/SP.POP.TOTL"
    assert _query(url) == {
        "format": "json",
        "date": "1990:2025",
        "per_page": "200",
        "page": "3",
    }


def test_world_bank_url_defaults_to_pipeline_page_size() -> None:
    url = world_bank_url("SP.POP.TOTL", 1990, 2025)
    assert _query(url)["per_page"] == str(sources.WORLD_BANK_PAGE_SIZE)
    assert "date=1990:2025" in url


def test_world_bank_gdp_is_total_not_per_capita() -> None:
    assert "NY.GDP.MKTP.PP.KD" in sources.WORLD_BANK_INDICATORS
    assert "SP.POP.TOTL" in sources.WORLD_BANK_INDICATORS


@pytest.mark.parametrize(
    ("first_year", "last_year", "page", "per_page", "message"),
    [
        (2025, 1990, 1, 1000, "first_year"),
        (1990, 2025, 0, 1000, "page"),
        (1990, 2025, 1, 0, "per_page"),
    ],
)
def test_world_bank_url_rejects_invalid_arguments(
    first_year: int,
    last_year: int,
    page: int,
    per_page: int,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        world_bank_url("SP.POP.TOTL", first_year, last_year, page, per_page)
