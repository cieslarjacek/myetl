"""Source contracts: what is requested from each upstream data source.

Raw data are fetched broadly on purpose - to reflect real live
situations where input data are not "perfect". The transform step
narrows them down:

1. Our World in Data (OWID): the full energy CSV (all countries and
   regions, about 130 columns).
2. Eurostat SDMX 3.0 API: SDMX-CSV 2.0 for all geographic entities,
   including aggregates. Countries appear only as ``"CODE: Name"``
   labels and are matched by name.
3. World Bank Indicators API v2: all economies and regional aggregates
   from 1990, paginated. GDP is total GDP (PPP) and GDP per capita is
   derived in the transform step.

One Eurostat dataset is monthly and is aggregated to years downstream.
The human-readable version of this contract is
``docs/data-contract.md``.
"""

__all__ = [
    "COUNTRIES",
    "EUROSTAT_API_BASE",
    "FIRST_YEAR",
    "HOUSEHOLD_PRICE",
    "MONTHLY_GENERATION",
    "OWID_COLUMNS",
    "OWID_ENERGY_CSV_URL",
    "RES_SHARE",
    "WORLD_BANK_API_BASE",
    "WORLD_BANK_FIRST_YEAR",
    "WORLD_BANK_INDICATORS",
    "WORLD_BANK_PAGE_SIZE",
    "Country",
    "EurostatDataset",
    "world_bank_url",
]

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final
from urllib.parse import urlencode

# ----------------------------------------------------------------------
# Scope
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Country:
    """A country in scope and how each source identifies it.

    Attributes:
        iso3: ISO 3166-1 alpha-3 code. Join key for OWID
            (``iso_code``) and the World Bank (``countryiso3code``).
        name: English short name used in the output.
        eurostat_name: Eurostat's English label for the country. Join
            key for Eurostat, whose responses carry no ISO code.
    """

    iso3: str
    name: str
    eurostat_name: str


#: EU27 plus Norway and Iceland, ordered by ISO3 code.
COUNTRIES: Final[tuple[Country, ...]] = (
    Country("AUT", "Austria", "Austria"),
    Country("BEL", "Belgium", "Belgium"),
    Country("BGR", "Bulgaria", "Bulgaria"),
    Country("CYP", "Cyprus", "Cyprus"),
    Country("CZE", "Czechia", "Czechia"),
    Country("DEU", "Germany", "Germany"),
    Country("DNK", "Denmark", "Denmark"),
    Country("ESP", "Spain", "Spain"),
    Country("EST", "Estonia", "Estonia"),
    Country("FIN", "Finland", "Finland"),
    Country("FRA", "France", "France"),
    Country("GRC", "Greece", "Greece"),
    Country("HRV", "Croatia", "Croatia"),
    Country("HUN", "Hungary", "Hungary"),
    Country("IRL", "Ireland", "Ireland"),
    Country("ISL", "Iceland", "Iceland"),
    Country("ITA", "Italy", "Italy"),
    Country("LTU", "Lithuania", "Lithuania"),
    Country("LUX", "Luxembourg", "Luxembourg"),
    Country("LVA", "Latvia", "Latvia"),
    Country("MLT", "Malta", "Malta"),
    Country("NLD", "Netherlands", "Netherlands"),
    Country("NOR", "Norway", "Norway"),
    Country("POL", "Poland", "Poland"),
    Country("PRT", "Portugal", "Portugal"),
    Country("ROU", "Romania", "Romania"),
    Country("SVK", "Slovakia", "Slovakia"),
    Country("SVN", "Slovenia", "Slovenia"),
    Country("SWE", "Sweden", "Sweden"),
)

#: First year in the output. The last year comes from the data.
FIRST_YEAR: Final[int] = 2000


# ----------------------------------------------------------------------
# Our World in Data
# ----------------------------------------------------------------------

#: Full energy dataset: one row per country or region and year, from
#: 1900, about 9 MB.
OWID_ENERGY_CSV_URL: Final[str] = (
    "https://raw.githubusercontent.com/owid/energy-data/master/"
    "owid-energy-data.csv"
)

#: OWID column -> output column. Electricity values since 2000 are
#: Ember's yearly electricity data, republished by OWID.
OWID_COLUMNS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "electricity_generation": "electricity_generation_twh",
        "renewables_electricity": "renewables_electricity_twh",
        "fossil_electricity": "fossil_electricity_twh",
        "nuclear_electricity": "nuclear_electricity_twh",
        "wind_electricity": "wind_electricity_twh",
        "solar_electricity": "solar_electricity_twh",
        "hydro_electricity": "hydro_electricity_twh",
        "renewables_share_elec": "renewables_share_elec_pct",
        "fossil_share_elec": "fossil_share_elec_pct",
        "carbon_intensity_elec": "carbon_intensity_elec_gco2e_per_kwh",
    }
)


# ----------------------------------------------------------------------
# Eurostat
# ----------------------------------------------------------------------

#: SDMX 3.0 data endpoint for Eurostat dataflows.
EUROSTAT_API_BASE: Final[str] = (
    "https://ec.europa.eu/eurostat/api/dissemination/sdmx/3.0/data/"
    "dataflow/ESTAT"
)

#: Response format: SDMX-CSV 2.0, uncompressed, every coded value as
#: "CODE: Label" (e.g. "EL: Greece").
EUROSTAT_FORMAT_PARAMS: Final[tuple[tuple[str, str], ...]] = (
    ("format", "csvdata"),
    ("formatVersion", "2.0"),
    ("labels", "both"),
    ("compress", "false"),
)


@dataclass(frozen=True, slots=True)
class EurostatDataset:
    """A Eurostat dataflow with this project's fixed dimension filters.

    No ``geo`` filter is applied: responses include every country and
    aggregate (EU27, euro area, ...) that Eurostat publishes.

    Attributes:
        code: Dataset code, e.g. ``nrg_ind_ren``.
        filters: Fixed dimension filters as ``(dimension, values)``
            pairs; several values are combined with OR.
        version: Dataflow version.
    """

    dataset_code: str
    filters: tuple[tuple[str, tuple[str, ...]], ...]
    version: str = "1.0"

    def url(self, since: str | None = None, until: str | None = None) -> str:
        """Build the request URL, optionally limited to a time range.

        The pipeline requests the full time range; the limits exist for
        recording small test fixtures.

        Args:
            since: First period (inclusive) in the dataset's frequency
                (``2008``, ``2008-S1`` or ``2008-01``).
            until: Last period (inclusive).

        Returns:
            Absolute URL for the SDMX-CSV 2.0 response.
        """
        format_params = list(EUROSTAT_FORMAT_PARAMS)
        format_params.extend(
            (f"c[{dimension}]", ",".join(values))
            for dimension, values in self.filters
        )
        bounds = []
        if since is not None:
            bounds.append(f"ge:{since}")
        if until is not None:
            bounds.append(f"le:{until}")
        if bounds:
            # "+" combines conditions with AND; urlencode sends it as
            # %2B, which the server decodes back to "+".
            format_params.append(("c[TIME_PERIOD]", "+".join(bounds)))
        query = urlencode(format_params, safe="[],:")
        return f"{EUROSTAT_API_BASE}/{self.dataset_code.upper()}/{
            self.version
        }?{query}"


#: Share of renewable energy (%), annual: overall (``REN``) and in
#: electricity (``REN_ELC``). Published from 2004.
RES_SHARE: Final[EurostatDataset] = EurostatDataset(
    dataset_code="nrg_ind_ren",
    filters=(
        ("FREQ", ("A",)),
        ("UNIT", ("PC",)),
        ("NRG_BAL", ("REN", "REN_ELC")),
    ),
)

#: Household electricity price per kWh, semi-annual: band DC
#: (2 500-4 999 kWh/year), all taxes and levies, in EUR and PPS.
#: Published from 2007-S1 with the current bands.
HOUSEHOLD_PRICE: Final[EurostatDataset] = EurostatDataset(
    dataset_code="nrg_pc_204",
    filters=(
        ("FREQ", ("S",)),
        ("SIEC", ("E7000",)),
        ("UNIT", ("KWH",)),
        ("NRG_CONS", ("KWH2500-4999",)),
        ("TAX", ("I_TAX",)),
        ("CURRENCY", ("EUR", "PPS")),
    ),
)

#: Net electricity generation (GWh), monthly: total, renewables and
#: biofuels (``RA000``) and fossil energy (``FE``). Published from
#: 2008-01; summed to years downstream.
MONTHLY_GENERATION: Final[EurostatDataset] = EurostatDataset(
    dataset_code="nrg_cb_pem",
    filters=(
        ("FREQ", ("M",)),
        ("SIEC", ("TOTAL", "RA000", "FE")),
        ("UNIT", ("GWH",)),
    ),
)


# ----------------------------------------------------------------------
# World Bank
# ----------------------------------------------------------------------

#: Indicators API v2.
WORLD_BANK_API_BASE: Final[str] = "https://api.worldbank.org/v2"

#: First year requested (PPP series start in 1990).
WORLD_BANK_FIRST_YEAR: Final[int] = 1990

#: World Development Indicators code -> intermediate column name.
WORLD_BANK_INDICATORS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "NY.GDP.MKTP.PP.KD": "gdp_ppp_const2021_intl_usd",
        "SP.POP.TOTL": "population",
    }
)

#: Rows per page requested by the pipeline.
WORLD_BANK_PAGE_SIZE: Final[int] = 1000


def world_bank_url(
    indicator: str,
    first_year: int,
    last_year: int,
    page: int = 1,
    per_page: int = WORLD_BANK_PAGE_SIZE,
) -> str:
    """Build the request URL for one indicator, all economies.

    Args:
        indicator: World Development Indicators code, e.g.
            ``SP.POP.TOTL``.
        first_year: First year (inclusive).
        last_year: Last year (inclusive).
        page: 1-based page number.
        per_page: Rows per page.

    Returns:
        Absolute URL for the JSON response.

    Raises:
        ValueError: If the year range is reversed, or ``page`` or
            ``per_page`` is less than 1.
    """
    if first_year > last_year:
        raise ValueError(f"first_year {first_year} > last_year {last_year}")
    if page < 1:
        raise ValueError(f"page must be >= 1, got {page}")
    if per_page < 1:
        raise ValueError(f"per_page must be >= 1, got {per_page}")
    params = urlencode(
        {
            "format": "json",
            "date": f"{first_year}:{last_year}",
            "per_page": per_page,
            "page": page,
        },
        # Keep the year range readable ("1990:2025", not "1990%3A2025").
        safe=":",
    )
    return f"{WORLD_BANK_API_BASE}/country/all/indicator/{indicator}?{params}"
