"""World Bank: Indicators API v2.

Requests cover all economies and aggregates (World, regions, income
groups) from 1990, paginated. A response is a JSON array
``[metadata, rows]``; API errors also arrive with HTTP 200, as a
one-element array holding ``message``.
"""

__all__ = [
    "API_BASE_URL",
    "FIRST_YEAR",
    "FORMAT_PARAMS",
    "GDP_PPP",
    "INDICATORS",
    "PAGE_SIZE",
    "POPULATION",
    "REQUEST_URL_SETTINGS",
    "URL_SAFE_CHARACTERS",
    "Indicator",
    "RequestUrlSettings",
]

from dataclasses import dataclass
from typing import Final
from urllib.parse import urlencode

from myetl.scope import YearRange

#: Indicators API v2.
API_BASE_URL: Final[str] = "https://api.worldbank.org/v2"

#: First year requested; the PPP series start in 1990.
FIRST_YEAR: Final[int] = 1990

#: Response format: JSON (the API's default is XML).
FORMAT_PARAMS: Final[tuple[tuple[str, str], ...]] = (("format", "json"),)

#: Rows per page requested by the pipeline.
PAGE_SIZE: Final[int] = 1000

#: Kept unescaped so the year range stays readable ("1990:2025", not
#: "1990%3A2025").
URL_SAFE_CHARACTERS: Final[str] = ":"


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
        page: int = 1,
        request_settings: RequestUrlSettings = REQUEST_URL_SETTINGS,
    ) -> str:
        """Build the request URL for one page, all economies.

        Args:
            year_range: Years to request.
            page: 1-based page number.
            request_settings: How the URL is built.

        Returns:
            Absolute URL for the JSON response.

        Raises:
            ValueError: If ``page`` is less than 1.
        """
        _require_positive("page", page)
        query_params = [
            *request_settings.format_params,
            ("date", f"{year_range.first}:{year_range.last}"),
            ("per_page", str(request_settings.page_size)),
            ("page", str(page)),
        ]
        query = urlencode(query_params, safe=request_settings.safe_characters)
        path = f"country/all/indicator/{self.indicator_code}"
        return f"{request_settings.base_url}/{path}?{query}"


#: GDP, PPP (constant 2021 international $), total. GDP per capita is
#: derived from it and population in the transform step.
GDP_PPP: Final[Indicator] = Indicator(
    "NY.GDP.MKTP.PP.KD", "gdp_ppp_const2021_intl_usd"
)

#: Population, total.
POPULATION: Final[Indicator] = Indicator("SP.POP.TOTL", "population")

#: Every indicator the pipeline requests.
INDICATORS: Final[tuple[Indicator, ...]] = (GDP_PPP, POPULATION)
