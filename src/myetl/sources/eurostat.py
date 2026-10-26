"""Eurostat: SDMX 3.0 dissemination API.

Responses are SDMX-CSV 2.0 in long format, one row per observation.
With ``labels=both`` every coded value reads ``"CODE: Label"`` (for
example ``"EL: Greece"``). No ``geo`` filter is sent, so responses
include every country and aggregate (EU27, euro area, ...).
"""

__all__ = [
    "DATAFLOW_VERSION",
    "HOUSEHOLD_PRICE",
    "LABEL_SEPARATOR",
    "MONTHLY_GENERATION",
    "REQUEST_URL_SETTINGS",
    "REQUIRED_COLUMNS",
    "RES_SHARE",
    "Dataset",
    "DimensionFilter",
    "extract_country_label",
]

from dataclasses import dataclass
from typing import Final, TypedDict
from urllib.parse import urlencode

#: SDMX 3.0 data endpoint for Eurostat dataflows.
API_BASE_URI: Final[str] = (
    "https://ec.europa.eu/eurostat/api/dissemination/sdmx/3.0/data/"
    "dataflow/ESTAT"
)

#: Dataflow version used by all datasets of this project.
DATAFLOW_VERSION: Final[str] = "1.0"

#: Response format: SDMX-CSV 2.0, uncompressed, coded values labelled.
FORMAT_PARAMS: Final[tuple[tuple[str, str], ...]] = (
    ("format", "csvdata"),
    ("formatVersion", "2.0"),
    ("labels", "both"),
    ("compress", "false"),
)

#: Separates the code from the label in a coded value.
LABEL_SEPARATOR: Final[str] = ": "

#: SDMX-CSV columns the project reads (besides the ``geo`` column).
REQUIRED_COLUMNS: Final[tuple[str, ...]] = ("TIME_PERIOD", "OBS_VALUE")


class RequestSettingsDict(TypedDict):
    """Settings for building the data request URL."""

    #: SDMX 3.0 data endpoint for Eurostat dataflows.
    base_uri: str
    #: Response format: SDMX-CSV 2.0, uncompressed, coded values
    #: labelled.
    format_params: tuple[tuple[str, str], ...]
    #: Characters kept unescaped in the query, for readable URLs.
    safe_characters: str


#: Request URL settings.
REQUEST_URL_SETTINGS: Final[RequestSettingsDict] = {
    "base_uri": (
        "https://ec.europa.eu/eurostat/api/dissemination/sdmx/3.0/data/"
        "dataflow/ESTAT"
    ),
    "format_params": (
        ("format", "csvdata"),
        ("formatVersion", "2.0"),
        ("labels", "both"),
        ("compress", "false"),
    ),
    "safe_characters": "[],:",
}

#: Time period types.
type DateValue = str | None
type DateRange = tuple[DateValue, DateValue] | None


def extract_country_label(
    coded_value: str, data_separator: str = LABEL_SEPARATOR
) -> str:
    """Return the label part of a coded value.

    Coded value is a character string that contains
    EU-modified ISO 3166-1 alpha-2 country code and country name
    separated by ": " (e.g. ``"EL: Greece"``).

    Args:
        coded_value: A value as returned with ``labels=both``.
        data_separator: Separates the code from the label in a coded
            value.

    Returns:
        The label (``"EL: Greece"`` gives ``"Greece"``) or an empty
        string when the value carries no label.
    """
    return coded_value.partition(data_separator)[2]


@dataclass(frozen=True, slots=True)
class DimensionFilter:
    """A data dimension filter.

    Several values are combined with OR.

    Attributes:
        dimension: Dimension code, e.g. ``FREQ``.
        values: Accepted codes, e.g. ``("A",)``.
    """

    dimension: str
    values: tuple[str, ...]

    def as_query_param(self) -> tuple[str, str]:
        """Return the filter as an SDMX ``c[...]`` query parameter."""
        return f"c[{self.dimension}]", ",".join(self.values)


@dataclass(frozen=True, slots=True)
class Dataset:
    """A Eurostat dataflow with this project's fixed filters.

    Attributes:
        dataset_code: Dataset code, e.g. ``nrg_ind_ren``.
        filters: Fixed dimension filters.
        version: Dataflow version.
    """

    dataset_code: str
    filters: tuple[DimensionFilter, ...]
    version: str = DATAFLOW_VERSION

    def create_request_url(
        self,
        date_range: DateRange = None,
        request_settings: RequestSettingsDict = REQUEST_URL_SETTINGS,
    ) -> str:
        """Build the request URL, optionally limited to a time range.

        The pipeline requests the full time range. The limits exist for
        recording small test fixtures.

        Args:
            date_range: A tuple of ``(since, until)`` inclusive periods
                in the dataset's frequency (e.g. ``2008``, ``2008-S1``
                or ``2008-01``). Either value can be ``None``.
            request_settings: A dictionary with the settings for
                the request URL. Must contain at least three elements
                - "base_uri", "format_params" and "safe_characters".

        Returns:
            Absolute URL for the SDMX-CSV 2.0 response.
        """
        filters = [*self.filters, *_time_filter(date_range)]
        query_params = [
            *request_settings["format_params"],
            *(elem.as_query_param() for elem in filters),
        ]
        query = urlencode(
            query_params, safe=request_settings["safe_characters"]
        )
        dataset = self.dataset_code.upper()
        return (
            f"{request_settings['base_uri']}/{dataset}/{self.version}?{query}"
        )


def _time_filter(date_range: DateRange) -> tuple[DimensionFilter, ...]:
    """Return the ``TIME_PERIOD`` filter for a range, if it has bounds.

    Args:
        date_range: A ``(since, until)`` pair of inclusive periods or
            ``None`` for no range. Either bound can be ``None``.

    Returns:
        One filter or none when the range is ``None`` (or both bounds
        are ``None``).
    """
    since, until = date_range or (None, None)
    bounds = [
        f"{operator}:{period}"
        for operator, period in (("ge", since), ("le", until))
        if period is not None
    ]
    if not bounds:
        return ()
    # "+" combines conditions with AND. urlencode sends it as %2B, which
    # the server decodes back to "+" (a bare "+" would mean a space).
    return (DimensionFilter("TIME_PERIOD", ("+".join(bounds),)),)


#: Share of renewable energy (%), annual: overall (``REN``) and in
#: electricity (``REN_ELC``). Published from 2004.
RES_SHARE: Final[Dataset] = Dataset(
    dataset_code="nrg_ind_ren",
    filters=(
        DimensionFilter("FREQ", ("A",)),
        DimensionFilter("UNIT", ("PC",)),
        DimensionFilter("NRG_BAL", ("REN", "REN_ELC")),
    ),
)

#: Household electricity price per kWh, semi-annual: band DC
#: (2 500-4 999 kWh/year), all taxes and levies, in EUR and PPS.
#: Published from 2007-S1 with the current bands.
HOUSEHOLD_PRICE: Final[Dataset] = Dataset(
    dataset_code="nrg_pc_204",
    filters=(
        DimensionFilter("FREQ", ("S",)),
        DimensionFilter("SIEC", ("E7000",)),
        DimensionFilter("UNIT", ("KWH",)),
        DimensionFilter("NRG_CONS", ("KWH2500-4999",)),
        DimensionFilter("TAX", ("I_TAX",)),
        DimensionFilter("CURRENCY", ("EUR", "PPS")),
    ),
)

#: Net electricity generation (GWh), monthly: total, renewables and
#: biofuels (``RA000``) and fossil energy (``FE``). Published from
#: 2008-01. Summed to years downstream.
MONTHLY_GENERATION: Final[Dataset] = Dataset(
    dataset_code="nrg_cb_pem",
    filters=(
        DimensionFilter("FREQ", ("M",)),
        DimensionFilter("SIEC", ("TOTAL", "RA000", "FE")),
        DimensionFilter("UNIT", ("GWH",)),
    ),
)
