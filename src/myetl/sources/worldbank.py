"""World Bank: Indicators API v2.

Requests cover all economies and aggregates (World, regions, income
groups) from 1990, paginated. A response is a JSON array
``[metadata, rows]``; API errors also arrive with HTTP 200, as a
one-element array holding ``message``.
"""

__all__ = [
    "FIRST_YEAR",
    "GDP_PPP",
    "INDICATORS",
    "POPULATION",
    "Indicator",
]

from dataclasses import dataclass
from typing import Final, TypedDict
from urllib.parse import urlencode

from myetl.scope import YearRange

#: First year requested; the PPP series start in 1990.
FIRST_YEAR: Final[int] = 1990


class RequestSettingsDict(TypedDict):
    """Settings for building the data request URL."""

    #: Indicators API v2.
    base_uri: str
    #: Response format: json, rows per page, starting page.
    format_params: tuple[tuple[str, str | int], ...]
    #: To keep the year range readable: "1990:2025", not "%3A".
    safe_characters: str


#: Request URL settings.
REQUEST_URL_SETTINGS: Final[RequestSettingsDict] = {
    "base_uri": ("https://api.worldbank.org/v2"),
    "format_params": (
        ("format", "json"),
        ("per_page", 1000),
        ("page", 1),
    ),
    "safe_characters": ":",
}


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
        date_range: YearRange,
        request_settings: RequestSettingsDict = REQUEST_URL_SETTINGS,
    ) -> str:
        """Build the request URL for one page, all economies.

        Args:
            date_range: Range of years to request.
            page: 1-based page number.
            request_settings: A dictionary with the settings for
                the request URL. Must contain at least three elements
                - "base_uri", "format_params" and "safe_characters".

        Returns:
            Absolute URL for the JSON response.

        Raises:
            ValueError: If ``page`` or ``per_page`` is less than 1.
        """
        query_params = dict(
            [
                *request_settings["format_params"],
                ("date", f"{date_range.first}:{date_range.last}"),
            ]
        )
        _require_positive("page", int(query_params["page"]))
        _require_positive("per_page", int(query_params["per_page"]))
        query = urlencode(
            query_params, safe=request_settings["safe_characters"]
        )
        path = f"country/all/indicator/{self.indicator_code}"
        return f"{request_settings['base_uri']}/{path}?{query}"


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


#: GDP, PPP (constant 2021 international $), total. GDP per capita is
#: derived from it and population in the transform step.
GDP_PPP: Final[Indicator] = Indicator(
    "NY.GDP.MKTP.PP.KD", "gdp_ppp_const2021_intl_usd"
)

#: Population, total.
POPULATION: Final[Indicator] = Indicator("SP.POP.TOTL", "population")

#: Every indicator the pipeline requests.
INDICATORS: Final[tuple[Indicator, ...]] = (GDP_PPP, POPULATION)
