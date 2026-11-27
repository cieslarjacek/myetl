"""Generic transformers: steps from a data frame to a data frame.

Each transformer is a frozen dataclass whose fields are its parameters
and whose :meth:`apply` returns a new data frame. None of them knows a
source: a source's transform is a sequence of transformers configured
for it. Most work on the observation format
(:mod:`myetl.transform.observations`); the first few reshape a source's
data frame into it. Import them from this package; the modules group
them:

1. :mod:`~myetl.transform.steps.reshape`: coded headers and values
   (:class:`ColumnCodeRenamer`, :class:`ValueCodeExtractor`,
   :class:`ValueLabelExtractor`) and the way into observations
   (:class:`ColumnMelter`, :class:`ObservationBuilder`,
   :class:`PeriodParser`).
2. :mod:`~myetl.transform.steps.filter`: :class:`CountryFilter`,
   :class:`YearFilter`, :class:`VariablesSelector`.
3. :mod:`~myetl.transform.steps.check`:
   :class:`ImplausibleValueRemover`, :class:`GapReporter`.
4. :mod:`~myetl.transform.steps.calculate`: :class:`VariableDeriver`,
   :class:`YearAggregator`, :class:`VariableScaler`.
"""

__all__ = [
    "CODE_SEPARATOR",
    "Aggregation",
    "ColumnCodeRenamer",
    "ColumnMelter",
    "CountryFilter",
    "DfStep",
    "Difference",
    "Gap",
    "GapReporter",
    "ImplausibleValueRemover",
    "NonNegativeRule",
    "ObservationBuilder",
    "PartOfWholeRule",
    "PeriodParser",
    "PlausibilityRule",
    "Ratio",
    "SeriesFormula",
    "SourceColumns",
    "ValueCodeExtractor",
    "ValueLabelExtractor",
    "VariableDeriver",
    "VariableScaler",
    "VariablesSelector",
    "YearAggregator",
    "YearFilter",
    "find_gaps",
]

from typing import Protocol

import pandas as pd

from myetl.transform.steps.calculate import (
    Aggregation,
    Difference,
    Ratio,
    SeriesFormula,
    VariableDeriver,
    VariableScaler,
    YearAggregator,
)
from myetl.transform.steps.check import (
    Gap,
    GapReporter,
    ImplausibleValueRemover,
    NonNegativeRule,
    PartOfWholeRule,
    PlausibilityRule,
    find_gaps,
)
from myetl.transform.steps.filter import (
    CountryFilter,
    VariablesSelector,
    YearFilter,
)
from myetl.transform.steps.reshape import (
    CODE_SEPARATOR,
    ColumnCodeRenamer,
    ColumnMelter,
    ObservationBuilder,
    PeriodParser,
    SourceColumns,
    ValueCodeExtractor,
    ValueLabelExtractor,
)


class DfStep(Protocol):
    """One configured transformer."""

    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        """Return the transformed data frame; input is left unchanged.

        Args:
            df: Data frame to transform.

        Returns:
            A new frame.
        """
        ...
