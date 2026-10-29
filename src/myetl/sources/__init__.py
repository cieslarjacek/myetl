"""Source contracts: one module per upstream data source.

Each module holds everything the project relies on about its source:
where to request data, which filters or columns it uses, and the
response format.

* :mod:`myetl.sources.owid`: Our World in Data energy dataset (CSV).
* :mod:`myetl.sources.eurostat`: Eurostat SDMX 3.0 API (SDMX-CSV).
* :mod:`myetl.sources.worldbank`: World Bank Indicators API v2 (JSON).

Each source checks a response before returning it, using the shared
:mod:`myetl.sources.response_validation`.

Raw data are requested broadly on purpose (whole files, all countries
and aggregates) - to reflect real live situations where input data
are not "perfect". The transform step narrows them down. The
human-readable contract is ``docs/data-contract.md``.
"""
