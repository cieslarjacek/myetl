"""Unit tests for ``myetl.sources.worldbank``."""

import dataclasses
import json
from urllib.parse import parse_qsl, urlsplit

import pytest

from myetl.scope import YearRange
from myetl.sources import worldbank
from myetl.sources.response_validation import (
    ResponseFormatError,
    SourceResponse,
)
from myetl.sources.worldbank import (
    REQUEST_URL_SETTINGS,
    Page,
    RequestUrlSettings,
)

YEARS = YearRange(1990, 2025)


def _query(url: str) -> dict[str, str]:
    """Return the decoded query of ``url`` as a dict."""
    return dict(parse_qsl(urlsplit(url).query))


# --------------------------------------------------------------------
# Request URL
# --------------------------------------------------------------------


def test_world_bank_url() -> None:
    url = worldbank.POPULATION.create_request_url(
        YEARS,
        page_number=3,
        request_settings=RequestUrlSettings(page_size=200),
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
    assert worldbank.GDP_PPP.code == "NY.GDP.MKTP.PP.KD"
    assert worldbank.POPULATION.code == "SP.POP.TOTL"
    assert worldbank.INDICATORS == (worldbank.GDP_PPP, worldbank.POPULATION)


def test_indicators_have_distinct_target_columns() -> None:
    columns = [i.target_column for i in worldbank.INDICATORS]
    assert len(columns) == len(set(columns))


def test_world_bank_url_rejects_page_below_one() -> None:
    with pytest.raises(ValueError, match="page"):
        worldbank.POPULATION.create_request_url(YEARS, page_number=0)


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


# --------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------

PAGE_URL = "https://example.test/page"

#: An API error, which the World Bank sends with HTTP 200.
API_ERROR = [{"message": [{"id": "120", "value": "Invalid value"}]}]


def page_response(page_document: object) -> SourceResponse:
    """Encode ``page_document`` as a response from :data:`PAGE_URL`."""
    return SourceResponse(PAGE_URL, json.dumps(page_document).encode())


def test_parse_page_returns_page_count_and_rows() -> None:
    page_rows = [{"countryiso3code": "AUT", "date": "2008", "value": 1.0}]

    parsed_page = worldbank.parse_page(
        page_response([{"pages": 2}, page_rows])
    )

    assert parsed_page == Page(total_pages=2, rows=tuple(page_rows))


def test_world_bank_page_count_as_text_is_accepted() -> None:
    parsed_page = worldbank.parse_page(page_response([{"pages": "3"}, []]))

    assert parsed_page.total_pages == 3


@pytest.mark.parametrize(
    "page_document",
    [
        API_ERROR,
        [{"page": 1, "pages": 0, "total": 0}, None],
        [{"pages": "three"}, []],
        [{"pages": 1}, ["AUT"]],
        {"pages": 1},
    ],
    ids=[
        "api-error",
        "no-data",
        "count-not-a-number",
        "row-not-object",
        "dict",
    ],
)
def test_parse_page_with_unexpected_shape_fails(page_document: object) -> None:
    with pytest.raises(ResponseFormatError, match="unexpected response"):
        worldbank.parse_page(page_response(page_document))


def test_parse_page_of_invalid_json_fails() -> None:
    html_response = SourceResponse(PAGE_URL, b"<html>Unavailable</html>")

    with pytest.raises(ResponseFormatError, match="expected JSON"):
        worldbank.parse_page(html_response)


class PagedApiFake:
    """Serves ``total_pages`` empty pages and records the URLs."""

    def __init__(self, total_pages: int) -> None:
        self.total_pages = total_pages
        self.requested_urls: list[str] = []

    def serve_page(self, url: str) -> bytes:
        self.requested_urls.append(url)
        page_metadata = {"pages": self.total_pages}
        return json.dumps([page_metadata, []]).encode()


def test_fetch_pages_follows_every_page_in_order() -> None:
    paged_api = PagedApiFake(total_pages=3)
    first_page = worldbank.FIRST_PAGE_NUMBER

    page_responses = worldbank.POPULATION.fetch_pages(
        paged_api.serve_page, YEARS
    )

    assert paged_api.requested_urls == [
        worldbank.POPULATION.create_request_url(YEARS, page_number)
        for page_number in range(first_page, first_page + 3)
    ]
    assert [
        page_response.url for page_response in page_responses
    ] == paged_api.requested_urls


def test_fetch_pages_of_single_page_requests_once() -> None:
    paged_api = PagedApiFake(total_pages=1)

    page_responses = worldbank.POPULATION.fetch_pages(
        paged_api.serve_page, YEARS
    )

    assert len(page_responses) == 1
    assert len(paged_api.requested_urls) == 1


def test_fetch_pages_uses_injected_settings() -> None:
    paged_api = PagedApiFake(total_pages=1)
    small_page_settings = RequestUrlSettings(page_size=200)

    worldbank.POPULATION.fetch_pages(
        paged_api.serve_page, YEARS, small_page_settings
    )

    assert _query(paged_api.requested_urls[0])["per_page"] == "200"


def test_fetch_pages_stops_at_unexpected_page() -> None:
    api_error_content = json.dumps(API_ERROR).encode()

    with pytest.raises(ResponseFormatError, match="unexpected response"):
        worldbank.POPULATION.fetch_pages(lambda url: api_error_content, YEARS)
