"""Scope of the output: which countries and years it covers.

The country table is the single place that says how each data source
identifies a country: OWID and the World Bank by ISO 3166-1 alpha-3
code, Eurostat by its English label (its responses carry no ISO code).
"""

__all__ = [
    "COUNTRIES",
    "EUROSTAT_NAMES",
    "FIRST_YEAR",
    "ISO3_CODES",
    "Country",
    "YearRange",
]

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class Country:
    """A country in scope and how each source identifies it.

    Attributes:
        iso3: ISO 3166-1 alpha-3 code; the join key for OWID
            (``iso_code``) and the World Bank (``countryiso3code``).
        name: English short name used in the output; also the
            Eurostat label unless ``eurostat_alias`` is set.
        eurostat_alias: Eurostat's label, only when it differs from
            ``name`` (``None`` for all countries today).
    """

    iso3: str
    name: str
    eurostat_alias: str | None = None

    @property
    def eurostat_name(self) -> str:
        """Eurostat's English label, the join key for Eurostat.

        ``eurostat_alias`` when set, otherwise ``name``.
        """
        return self.eurostat_alias or self.name


@dataclass(frozen=True, slots=True)
class YearRange:
    """An inclusive range of calendar years.

    Attributes:
        first: First year (inclusive).
        last: Last year (inclusive).

    Raises:
        ValueError: If ``first`` is after ``last``.
    """

    first: int
    last: int

    def __post_init__(self) -> None:
        """Reject a reversed range."""
        if self.first > self.last:
            raise ValueError(f"first {self.first} > last {self.last}")

    def __contains__(self, year: object) -> bool:
        """Return whether ``year`` is an ``int`` within the range."""
        return isinstance(year, int) and self.first <= year <= self.last


#: EU27 plus Norway and Iceland, ordered by ISO3 code.
COUNTRIES: Final[tuple[Country, ...]] = (
    Country("AUT", "Austria"),
    Country("BEL", "Belgium"),
    Country("BGR", "Bulgaria"),
    Country("CYP", "Cyprus"),
    Country("CZE", "Czechia"),
    Country("DEU", "Germany"),
    Country("DNK", "Denmark"),
    Country("ESP", "Spain"),
    Country("EST", "Estonia"),
    Country("FIN", "Finland"),
    Country("FRA", "France"),
    Country("GRC", "Greece"),
    Country("HRV", "Croatia"),
    Country("HUN", "Hungary"),
    Country("IRL", "Ireland"),
    Country("ISL", "Iceland"),
    Country("ITA", "Italy"),
    Country("LTU", "Lithuania"),
    Country("LUX", "Luxembourg"),
    Country("LVA", "Latvia"),
    Country("MLT", "Malta"),
    Country("NLD", "Netherlands"),
    Country("NOR", "Norway"),
    Country("POL", "Poland"),
    Country("PRT", "Portugal"),
    Country("ROU", "Romania"),
    Country("SVK", "Slovakia"),
    Country("SVN", "Slovenia"),
    Country("SWE", "Sweden"),
)

#: Join keys of the countries in scope: ISO3 codes for OWID and the
#: World Bank, labels for Eurostat.
ISO3_CODES: Final[frozenset[str]] = frozenset(
    country.iso3 for country in COUNTRIES
)
EUROSTAT_NAMES: Final[frozenset[str]] = frozenset(
    country.eurostat_name for country in COUNTRIES
)

#: First year in the output. The last year comes from the data.
FIRST_YEAR: Final[int] = 2000
