# myetl

ETL pipeline for EU/EEA electricity data: renewables vs fossil fuels, by
country and year. It extracts data from three public sources, transforms and
merges them into one table, and serves the pipeline through an internal
FastAPI service that a portfolio Shiny app calls to run it and download the
result as CSV.

> **Status:** work in progress. Only the package skeleton exists so far.

## Data sources

| Source | Content | Access |
|---|---|---|
| Our World in Data, energy dataset | Electricity generation by source | CSV download |
| Eurostat | Official renewable-energy share, household electricity prices | REST API |
| World Bank | GDP per capita, population | REST API |

Exact datasets, indicators and licence terms are documented once the source
contracts are verified.

## Development

Requirements: [uv](https://docs.astral.sh/uv/) and GNU Make. uv installs the
pinned Python version (3.13) if it is missing.

```bash
make sync    # create .venv from uv.lock
make check   # format check, lint, type check, tests
make help    # list all recipes
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
