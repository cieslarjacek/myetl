# myetl

ETL pipeline for EU/EEA electricity data: renewables vs fossil fuels, by
country and year. It extracts data from three public sources, transforms and
merges them into one table, and serves the pipeline through an internal
FastAPI service that a portfolio Shiny app calls to run it and download the
result as CSV.

> **Status:** work in progress. Package skeleton and source contracts so
> far; the pipeline itself is not implemented yet.

## Data sources

| Source | Content | Access | Licence |
|---|---|---|---|
| Our World in Data, energy dataset (based on Ember) | Electricity generation by source, shares, carbon intensity | CSV download | CC BY 4.0 |
| Eurostat (`nrg_ind_ren`, `nrg_pc_204`, `nrg_cb_pem`) | Official renewable-energy share, semi-annual household electricity prices, monthly net generation | SDMX API (CSV) | Reuse with acknowledgement (Decision 2011/833/EU) |
| World Bank, World Development Indicators | GDP (PPP), population | REST API (paginated JSON) | CC BY 4.0 |

Raw data is fetched as broadly as it comes (whole files, all countries and
aggregates); the pipeline selects, matches countries by name or code,
aggregates monthly and semi-annual data to years and derives per-capita
values. Output scope: EU27 + Norway + Iceland, from 2000, one row per
country and year.
Requests, licences, attribution and the output columns are specified in
[docs/data-contract.md](docs/data-contract.md).

## Development

Requirements: [uv](https://docs.astral.sh/uv/) and GNU Make. uv installs the
pinned Python version (3.13) if it is missing.

```bash
make sync    # create .venv from uv.lock
make check   # format check, lint, type check, tests
make help    # list all recipes
make fixtures  # re-record test fixtures from the live sources
```

### Conventions

| PEP | Applied as |
|---|---|
| 8 | Code style: 79 characters for code, 72 for docstrings and comments; checked by Ruff and pycodestyle |
| 257 | Docstring basics (Ruff `D` rules); layout follows the Google style guide (`Args:`, `Returns:`, `Raises:`) instead of the `pep257` convention |
| 484, 526, 585, 604 | Type hints everywhere, modern syntax; mypy in strict mode |
| 440 | Version scheme: `X.X.X` releases, `X.X.X.XXXX` DEV builds |
| 517, 518, 621, 639 | `pyproject.toml` build system, metadata and licence expression |
| 561 | Typed package (`py.typed`) |
| 735 | Dev tools in a dependency group |

VS Code: run `make sync` first, then open the folder and install the
recommended extensions (`.vscode/extensions.json`). The workspace settings
point Ruff, mypy and pytest at `.venv`, so the editor uses the same tool
versions as `make check`.

## License

Code: [MIT](LICENSE). Data obtained from the sources above remains subject to
each source's own licence terms.
