"""Filter transformers.

Keep the countries, years and variables needed.
"""

__all__ = ["CountryFilter", "VariablesSelector", "YearFilter"]

from collections.abc import Mapping
from dataclasses import dataclass

import pandas as pd

from myetl.transform.observations import (
    OBSERVATION_COLUMNS,
    ObservationColumns,
)
from myetl.transform.steps._dataframes import require_unique


@dataclass(frozen=True, slots=True)
class CountryFilter:
    """Transformer for keeping the countries in scope, under ISO3 codes.

    ISO3 codes are ISO 3166-1 alpha-3. Rows whose country key is not
    in :attr:`country_keys`, such as regions and aggregates, are
    dropped. Repeated observations are only an error once in scope:
    regions may share an empty key.

    Attributes:
        country_keys: Source country key -> ISO3 code, e.g. ``"Greece"``
            -> ``"GRC"`` for Eurostat, or ISO3 -> ISO3.
        columns: Names of the observation columns.
    """

    country_keys: Mapping[str, str]
    columns: ObservationColumns = OBSERVATION_COLUMNS

    def apply(self, long_df: pd.DataFrame) -> pd.DataFrame:
        """Return the rows in scope with ISO3 codes as country keys.

        Args:
            long_df: Observations keyed by the source's country key.

        Returns:
            The rows in scope.

        Raises:
            DfFormatError: If two rows in scope share country, period
                and variable, e.g. two keys mapped to one country.
        """
        columns = self.columns
        iso3_codes = long_df[columns.country].map(dict(self.country_keys))
        in_scope_df = long_df.assign(**{columns.country: iso3_codes})
        in_scope_df = long_df[iso3_codes.notna()]

        require_unique(
            in_scope_df,
            columns.observation_key,
            type(self).__name__,
        )

        return in_scope_df.reset_index(drop=True)


@dataclass(frozen=True, slots=True)
class YearFilter:
    """Transformer for keeping the years from a first year on.

    Attributes:
        first_year: First year kept.
        columns: Names of the observation columns.
    """

    first_year: int
    columns: ObservationColumns = OBSERVATION_COLUMNS

    def apply(self, long_df: pd.DataFrame) -> pd.DataFrame:
        """Return the rows from :attr:`first_year` on.

        Args:
            long_df: Observations.

        Returns:
            The rows of the kept years.
        """
        is_kept = long_df[self.columns.year] >= self.first_year
        return long_df[is_kept].reset_index(drop=True)


@dataclass(frozen=True, slots=True)
class VariablesSelector:
    """Transformer for keeping chosen variables under new names.

    The new names are typically the output column names.

    Attributes:
        variable_names: Source variable -> new name. Other variables are
            dropped.
        columns: Names of the observation columns.
    """

    variable_names: Mapping[str, str]
    columns: ObservationColumns = OBSERVATION_COLUMNS

    def apply(self, long_df: pd.DataFrame) -> pd.DataFrame:
        """Return the chosen variables renamed.

        Args:
            long_df: Observations.

        Returns:
            The rows of the chosen variables.
        """
        variable_column = self.columns.variable
        new_names = long_df[variable_column].map(dict(self.variable_names))
        selected_df = long_df.assign(**{variable_column: new_names})

        return selected_df[new_names.notna()].reset_index(drop=True)
