"""World Bank: Indicators API v2.

Requests cover all economies and aggregates (World, regions, income
groups) from 1990, paginated. A response is a JSON array
``[metadata, rows]``; API errors also arrive with HTTP 200, as a
one-element array holding ``message``.
"""

__all__ = [
    "API_BASE_URL",
    "FIRST_PAGE_NUMBER",
    "FIRST_YEAR",
    "FORMAT_PARAMS",
    "GDP_PPP",
    "INDICATORS",
    "PAGE_SIZE",
    "POPULATION",
    "REQUEST_URL_SETTINGS",
    "URL_SAFE_CHARACTERS",
    "Indicator",
    "JsonObject",
    "Page",
    "RequestUrlSettings",
    "parse_page",
]

import itertools
import json
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlencode

from myetl.http_client import Fetcher
from myetl.scope import YearRange
from myetl.sources.response_validation import (
    ResponseFormatError,
    SourceResponse,
)

#: Indicators API v2.
API_BASE_URL: Final[str] = "https://api.worldbank.org/v2"

#: First year requested; the PPP series start in 1990.
FIRST_YEAR: Final[int] = 1990

#: Response format: JSON (the API's default is XML).
FORMAT_PARAMS: Final[tuple[tuple[str, str], ...]] = (("format", "json"),)

#: Number of the first page: the API numbers pages from 1.
FIRST_PAGE_NUMBER: Final[int] = 1

#: Rows per page requested by the pipeline.
PAGE_SIZE: Final[int] = 1000

#: Kept unescaped so the year range stays readable ("1990:2025", not
#: "1990%3A2025").
URL_SAFE_CHARACTERS: Final[str] = ":"

#: Longest part of an unexpected response quoted in an error message.
_ERROR_EXCERPT_CHARACTERS: Final[int] = 200

#: One observation as decoded from the response.
type JsonObject = dict[str, object]


def _require_positive(name: str, value: int) -> None:
    """Raise if ``value`` is less than 1.

    Args:
        name: Parameter name, for the error message.
        value: Value to check.

    Raises:
        ValueError: If ``value`` is less than 1.
    """
    if value < 1:
        raise ValueError(f"{name} must be >= 1, got {value}")


@dataclass(frozen=True, slots=True)
class RequestUrlSettings:
    """How request URLs are built.

    The defaults are the module constants, so the pipeline uses
    :data:`REQUEST_URL_SETTINGS`; other values are for tests (for
    example a small page size, to get several pages).

    Attributes:
        base_url: API endpoint; the indicator path is appended to it.
        format_params: Query parameters that select the response
            format.
        page_size: Rows per page.
        safe_characters: Characters kept unescaped in the query.

    Raises:
        ValueError: If ``page_size`` is less than 1.
    """

    base_url: str = API_BASE_URL
    format_params: tuple[tuple[str, str], ...] = FORMAT_PARAMS
    page_size: int = PAGE_SIZE
    safe_characters: str = URL_SAFE_CHARACTERS

    def __post_init__(self) -> None:
        """Reject a page size below 1."""
        _require_positive("page_size", self.page_size)


#: Request URL settings used by the pipeline.
REQUEST_URL_SETTINGS: Final[RequestUrlSettings] = RequestUrlSettings()


@dataclass(frozen=True, slots=True)
class Page:
    """One page of an indicator response.

    Attributes:
        total_pages: Number of pages in the whole response.
        rows: Observations on this page.
    """

    total_pages: int
    rows: tuple[JsonObject, ...]


def parse_page(response: SourceResponse) -> Page:
    """Parse one response page, checking its ``[metadata, rows]`` shape.

    The page count is accepted as a number or as text: the API sends
    both.

    Args:
        response: One page as downloaded.

    Returns:
        The page count and the rows.

    Raises:
        ResponseFormatError: If the content is not JSON, or not
            ``[metadata, rows]`` with a page count and object rows (for
            example an API error message, which arrives with HTTP 200).
    """
    page_document = _decode_json(response)
    match page_document:
        case [{"pages": int() | str() as total_pages}, list(rows)] if (
            _has_page_types(total_pages, rows)
        ):
            return Page(int(total_pages), tuple(rows))
    document_excerpt = repr(page_document)[:_ERROR_EXCERPT_CHARACTERS]
    problem = (
        f"unexpected response, expected [metadata, rows]: {document_excerpt}"
    )
    raise ResponseFormatError(response.url, problem)


def _has_page_types(total_pages: object, rows: list[object]) -> bool:
    """Return whether decoded page parts have the expected types.

    Args:
        total_pages: The ``pages`` value of the metadata.
        rows: The second element of the document.

    Returns:
        ``True`` if the page count is a whole number (as a number or
        as text) and every row is an object.
    """
    is_whole_page_count = str(total_pages).isdecimal()
    return is_whole_page_count and all(isinstance(row, dict) for row in rows)


def _decode_json(response: SourceResponse) -> object:
    """Return the decoded JSON content of a response.

    Args:
        response: Response holding a JSON document.

    Returns:
        The decoded document.

    Raises:
        ResponseFormatError: If the content is not valid JSON.
    """
    try:
        document: object = json.loads(response.content)
    except ValueError as error:
        problem = f"expected JSON, found invalid content ({error})"
        raise ResponseFormatError(response.url, problem) from error
    return document


@dataclass(frozen=True, slots=True)
class Indicator:
    """A World Development Indicators series.

    Attributes:
        indicator_code: Indicator code, e.g. ``SP.POP.TOTL``.
        target_column: Name of the column the values go to.
    """

    indicator_code: str
    target_column: str

    def create_request_url(
        self,
        year_range: YearRange,
        page_number: int = FIRST_PAGE_NUMBER,
        request_settings: RequestUrlSettings = REQUEST_URL_SETTINGS,
    ) -> str:
        """Build the request URL for one page, all economies.

        Args:
            year_range: Years to request.
            page_number: Which page of the paginated response to
                request.
            request_settings: How the URL is built.

        Returns:
            Absolute URL for the JSON response.

        Raises:
            ValueError: If ``page_number`` is before the first page.
        """
        if page_number < FIRST_PAGE_NUMBER:
            raise ValueError(
                f"page_number must be >= {FIRST_PAGE_NUMBER}, "
                f"got {page_number}"
            )

        query_params = [
            *request_settings.format_params,
            ("date", f"{year_range.first}:{year_range.last}"),
            ("per_page", str(request_settings.page_size)),
            ("page", str(page_number)),
        ]
        query = urlencode(query_params, safe=request_settings.safe_characters)
        indicator_path = f"country/all/indicator/{self.indicator_code}"

        return f"{request_settings.base_url}/{indicator_path}?{query}"

    def fetch_pages(
        self,
        url_fetcher: Fetcher,
        year_range: YearRange,
        page_number: int = FIRST_PAGE_NUMBER,
        request_settings: RequestUrlSettings = REQUEST_URL_SETTINGS,
    ) -> tuple[SourceResponse, ...]:
        """Download every page, failing on an unexpected page.

        Pages are requested in order until the page count reported by
        the API is reached.

        Args:
            url_fetcher: A callable that downloads a URL.
            year_range: Years to request.
            page_number: Which page of the paginated response to
                request.
            request_settings: How the URLs are built.

        Returns:
            One response per page, byte for byte, in page order.

        Raises:
            ResponseFormatError: If a page is not ``[metadata, rows]``.
        """
        page_responses: list[SourceResponse] = []
        for current_page in itertools.count(page_number):
            url = self.create_request_url(
                year_range, current_page, request_settings
            )
            page_response = SourceResponse(url, url_fetcher(url))
            page_responses.append(page_response)
            if current_page >= parse_page(page_response).total_pages:
                break
        return tuple(page_responses)


#: GDP, PPP (constant 2021 international $), total. GDP per capita is
#: derived from it and population in the transform step.
GDP_PPP: Final[Indicator] = Indicator(
    "NY.GDP.MKTP.PP.KD", "gdp_ppp_const2021_intl_usd"
)

#: Population, total.
POPULATION: Final[Indicator] = Indicator("SP.POP.TOTL", "population")

#: Every indicator the pipeline requests.
INDICATORS: Final[tuple[Indicator, ...]] = (GDP_PPP, POPULATION)
