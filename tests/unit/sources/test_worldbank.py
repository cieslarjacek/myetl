"""Unit tests for ``myetl.sources.worldbank``."""

import dataclasses
from urllib.parse import parse_qsl, urlsplit

import pytest

from myetl.scope import YearRange
from myetl.sources import worldbank
from myetl.sources.worldbank import REQUEST_URL_SETTINGS, RequestUrlSettings

YEARS = YearRange(1990, 2025)


def _query(url: str) -> dict[str, str]:
    """Return the decoded query of ``url`` as a dict."""
    return dict(parse_qsl(urlsplit(url).query))


# --------------------------------------------------------------------
# Request URL
# --------------------------------------------------------------------


def test_world_bank_url() -> None:
    url = worldbank.POPULATION.create_request_url(
        YEARS, page=3, request_settings=RequestUrlSettings(page_size=200)
    )
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


def test_world_bank_url_defaults_to_first_page_and_pipeline_size() -> None:
    url = worldbank.POPULATION.create_request_url(YEARS)
    assert _query(url)["page"] == "1"
    assert _query(url)["per_page"] == str(worldbank.PAGE_SIZE)
    assert "date=1990:2025" in url


def test_world_bank_gdp_is_total_not_per_capita() -> None:
    assert worldbank.GDP_PPP.indicator_code == "NY.GDP.MKTP.PP.KD"
    assert worldbank.POPULATION.indicator_code == "SP.POP.TOTL"
    assert worldbank.INDICATORS == (worldbank.GDP_PPP, worldbank.POPULATION)


def test_indicators_have_distinct_target_columns() -> None:
    columns = [i.target_column for i in worldbank.INDICATORS]
    assert len(columns) == len(set(columns))


def test_world_bank_url_rejects_page_below_one() -> None:
    with pytest.raises(ValueError, match="page"):
        worldbank.POPULATION.create_request_url(YEARS, page=0)


# --------------------------------------------------------------------
# Request URL settings
# --------------------------------------------------------------------


def test_default_settings_come_from_module_constants() -> None:
    from_constants = RequestUrlSettings(
        worldbank.API_BASE_URL,
        worldbank.FORMAT_PARAMS,
        worldbank.PAGE_SIZE,
        worldbank.URL_SAFE_CHARACTERS,
    )

    assert from_constants == REQUEST_URL_SETTINGS


def test_settings_reject_page_size_below_one() -> None:
    with pytest.raises(ValueError, match="page_size"):
        RequestUrlSettings(page_size=0)


def test_injected_settings_change_base_url() -> None:
    settings = RequestUrlSettings(base_url="https://example.test/v2")

    url = worldbank.GDP_PPP.create_request_url(
        YEARS, request_settings=settings
    )

    assert url.startswith("https://example.test/v2/country/all/indicator/")


def test_request_url_settings_are_immutable() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        REQUEST_URL_SETTINGS.page_size = 1  # type: ignore[misc]
