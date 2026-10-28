"""Eurostat: SDMX 3.0 dissemination API.

Responses are SDMX-CSV 2.0 in long format, one row per observation.
With ``labels=both`` every coded value and every coded column name
reads ``"CODE: Label"`` (for example ``"EL: Greece"`` and
``"TIME_PERIOD: Time"``). No ``geo`` filter is sent, so responses
include every country and aggregate (EU27, euro area, ...).
"""

__all__ = [
    "API_BASE_URL",
    "DATAFLOW_VERSION",
    "FORMAT_PARAMS",
    "GEO_COLUMN",
    "HOUSEHOLD_PRICE",
    "LABEL_SEPARATOR",
    "MONTHLY_GENERATION",
    "REQUEST_URL_SETTINGS",
    "REQUIRED_COLUMNS",
    "RES_SHARE",
    "URL_SAFE_CHARACTERS",
    "Dataset",
    "DimensionFilter",
    "Period",
    "PeriodRange",
    "RequestUrlSettings",
    "get_value_code",
    "get_value_label",
]

from dataclasses import dataclass
from typing import Final
from urllib.parse import urlencode

from myetl._docstrings import share_docstring
from myetl.http_client import Fetcher
from myetl.sources.response_validation import (
    SourceResponse,
    read_csv_header,
    validate_columns,
)

#: SDMX 3.0 data endpoint for Eurostat dataflows.
API_BASE_URL: Final[str] = (
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

#: Characters kept unescaped in the query, for readable URLs.
URL_SAFE_CHARACTERS: Final[str] = "[],:"

#: Separates the code from the label in a coded value.
LABEL_SEPARATOR: Final[str] = ": "

#: Code of the column that holds the country or aggregate.
GEO_COLUMN: Final[str] = "geo"

#: Codes of the SDMX-CSV columns the project reads. Header names are
#: labelled too (``"TIME_PERIOD: Time"``), so match them by code.
REQUIRED_COLUMNS: Final[tuple[str, ...]] = (
    GEO_COLUMN,
    "TIME_PERIOD",
    "OBS_VALUE",
)

#: A period in a dataset's frequency (``"2008"``, ``"2008-S1"`` or
#: ``"2008-01"``). ``None`` leaves that end of a range open.
type Period = str | None

#: Inclusive ``(since, until)`` periods.
type PeriodRange = tuple[Period, Period]


@dataclass(frozen=True, slots=True)
class RequestUrlSettings:
    """How request URLs are built.

    The defaults are the module constants, so the pipeline uses
    :data:`REQUEST_URL_SETTINGS`. Other values are for tests.

    Attributes:
        base_url: Data endpoint. The dataset code and version are
            appended to it.
        format_params: Query parameters that select the response
            format.
        safe_characters: Characters kept unescaped in the query.
    """

    base_url: str = API_BASE_URL
    format_params: tuple[tuple[str, str], ...] = FORMAT_PARAMS
    safe_characters: str = URL_SAFE_CHARACTERS


#: Request URL settings used by the pipeline.
REQUEST_URL_SETTINGS: Final[RequestUrlSettings] = RequestUrlSettings()


def get_value_code(
    coded_value: str, *, separator: str = LABEL_SEPARATOR
) -> str:
    """Return the code or label part of a coded value or column name.

    ``get_value_code`` returns the part before the separator,
    ``get_value_label`` the part after it.

    Args:
        coded_value: A value or header name as returned with
            ``labels=both``. E.g. ``"AT: Austria"`` or
            ``"TIME_PERIOD: Time"``.
        separator: Separates the code from the label. Must match
            the ``labels`` setting of the request.

    Returns:
        The code (``"TIME_PERIOD: Time"`` gives ``"TIME_PERIOD"``) or
        the label (``"EL: Greece"`` gives ``"Greece"``). For a value
        without a separator the code is the whole value and the label
        is an empty string.
    """
    return coded_value.partition(separator)[0]


@share_docstring(get_value_code)
def get_value_label(  # noqa: D103 (docstring shared from get_value_code)
    coded_value: str, *, separator: str = LABEL_SEPARATOR
) -> str:
    return coded_value.partition(separator)[2]


@dataclass(frozen=True, slots=True)
class DimensionFilter:
    """A filter on one dimension of a dataset.

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
        code: Dataset code, e.g. ``nrg_ind_ren``.
        filters: Fixed dimension filters.
        version: Dataflow version.
    """

    code: str
    filters: tuple[DimensionFilter, ...]
    version: str = DATAFLOW_VERSION

    def create_request_url(
        self,
        period_range: PeriodRange | None = None,
        request_settings: RequestUrlSettings = REQUEST_URL_SETTINGS,
    ) -> str:
        """Build the request URL, optionally limited to a time range.

        The pipeline requests the full time range. The limits exist for
        recording small test fixtures.

        Args:
            period_range: First and last period (inclusive), or
                ``None`` for the full time range.
            request_settings: How the URL is built.

        Returns:
            Absolute URL for the SDMX-CSV 2.0 response.
        """
        filters = [*self.filters, *_time_filter(period_range)]
        query_params = [
            *request_settings.format_params,
            *(
                dimension_filter.as_query_param()
                for dimension_filter in filters
            ),
        ]
        query = urlencode(query_params, safe=request_settings.safe_characters)
        dataflow_path = f"{self.code.upper()}/{self.version}"

        return f"{request_settings.base_url}/{dataflow_path}?{query}"

    def fetch_csv(
        self, url_fetcher: Fetcher, period_range: PeriodRange | None = None
    ) -> SourceResponse:
        """Download the dataset, failing if it is not as expected.

        Args:
            url_fetcher: A callable that downloads a URL.
            period_range: First and last period (inclusive), or
                ``None`` for the full time range.

        Returns:
            The SDMX-CSV response, byte for byte.

        Raises:
            ResponseFormatError: If the CSV has no data rows or lacks a
                column in :data:`REQUIRED_COLUMNS`, matched by code.
        """
        url = self.create_request_url(period_range)
        response = SourceResponse(url, url_fetcher(url))
        csv_header = read_csv_header(response)
        column_codes = [
            get_value_code(column_name) for column_name in csv_header
        ]

        validate_columns(response, column_codes, REQUIRED_COLUMNS)

        return response


def _time_filter(
    period_range: PeriodRange | None,
) -> tuple[DimensionFilter, ...]:
    """Return the ``TIME_PERIOD`` filter for a range, if it has bounds.

    Args:
        period_range: First and last period (inclusive) or ``None``
            for no range.

    Returns:
        One filter or none when there is no bound at all.
    """
    since, until = period_range or (None, None)
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
    code="nrg_ind_ren",
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
    code="nrg_pc_204",
    filters=(
        DimensionFilter("FREQ", ("S",)),
        DimensionFilter("SIEC", ("E7000",)),
        DimensionFilter("UNIT", ("KWH",)),
        DimensionFilter("NRG_CONS", ("KWH2500-4999",)),
        DimensionFilter("TAX", ("I_TAX",)),
        DimensionFilter("CURRENCY", ("EUR", "PPS")),
    ),
)

#: Net electricity generation (GWh), monthly; summed to years
#: downstream.
#:
#: * ``TOTAL``: all fuels, from 2008-01 (Croatia from 2011-01).
#: * ``RA000``: renewables and biofuels, from 2017-01.
#: * ``CF`` and ``CF_R``: combustible fuels, all and renewable only.
#:   Fossil generation is derived as ``CF - CF_R``, from 2017-01. It
#:   matches Eurostat's fossil energy (``FE``), which is published only
#:   from 2023/2024, and includes peat, oil shale and non-renewable
#:   waste.
MONTHLY_GENERATION: Final[Dataset] = Dataset(
    code="nrg_cb_pem",
    filters=(
        DimensionFilter("FREQ", ("M",)),
        DimensionFilter("SIEC", ("TOTAL", "RA000", "CF", "CF_R")),
        DimensionFilter("UNIT", ("GWH",)),
    ),
)
