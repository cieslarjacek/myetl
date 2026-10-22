"""ETL pipeline for EU/EEA electricity data.

1. Extracts country-year data from three public sources (Our World in
Data, Eurostat, World Bank).
2. Transforms and merges it into a single data table.
3. Serves the pipeline through an internal FastAPI service.
"""

# PEP 8: module-level dunders go after the docstring, before imports.
__all__ = ["__version__"]

from importlib.metadata import version

# The version is defined once in pyproject.toml.
__version__: str = version("myetl")
