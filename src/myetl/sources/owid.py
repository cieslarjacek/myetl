"""Our World in Data (OWID): energy dataset.

The whole CSV is downloaded: one row per country or region and year,
from 1900, about 130 columns. Regions have an empty ``iso_code``.
"""

__all__ = ["COLUMNS", "ENERGY_CSV_URL", "KEY_COLUMNS", "REQUIRED_COLUMNS"]

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

#: Full energy dataset (about 9 MB), from its GitHub repository.
ENERGY_CSV_URL: Final[str] = (
    "https://raw.githubusercontent.com/owid/energy-data/master/"
    "owid-energy-data.csv"
)

#: Columns that identify a row.
KEY_COLUMNS: Final[tuple[str, ...]] = ("country", "year", "iso_code")

#: OWID column -> output column. Electricity values since 2000 are
#: Ember's yearly electricity data, republished by OWID.
COLUMNS: Final[Mapping[str, str]] = MappingProxyType(
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

#: Every column the project reads from the CSV.
REQUIRED_COLUMNS: Final[tuple[str, ...]] = (*KEY_COLUMNS, *COLUMNS)
