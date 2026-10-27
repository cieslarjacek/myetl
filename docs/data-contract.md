# Data contract

What `myetl` fetches, from where, under which terms and what the output
CSV contains. The scope is implemented in
[`src/myetl/scope.py`](../src/myetl/scope.py) and the requests in
[`src/myetl/sources/`](../src/myetl/sources/), one module per source.
This document and that code change together.

Status: **draft for review** (step 2).

## 1. Scope

| Item | Value |
|---|---|
| Countries | EU27 + Norway + Iceland (29). Liechtenstein is excluded: its electricity and price data are too sparse. |
| Years | From 2000 to the latest year for which any source has data for any country in scope. |
| Grain | One row per country and year: the full grid of 29 countries × all years, also where a source has no data. |
| Join keys | OWID and World Bank: ISO 3166-1 alpha-3 code. Eurostat: country name (its responses carry no ISO code). |

### Countries and their keys

The output name and Eurostat's label are the same for all 29 countries
today; they are kept separate so a label change at Eurostat needs only a
table update. `make fixtures` fails if a label matches no Eurostat data.

| ISO3 | Name / Eurostat label | | ISO3 | Name / Eurostat label |
|---|---|---|---|---|
| AUT | Austria | | ISL | Iceland |
| BEL | Belgium | | ITA | Italy |
| BGR | Bulgaria | | LTU | Lithuania |
| CYP | Cyprus | | LUX | Luxembourg |
| CZE | Czechia | | LVA | Latvia |
| DEU | Germany | | MLT | Malta |
| DNK | Denmark | | NLD | Netherlands |
| ESP | Spain | | NOR | Norway |
| EST | Estonia | | POL | Poland |
| FIN | Finland | | PRT | Portugal |
| FRA | France | | ROU | Romania |
| GRC | Greece | | SVK | Slovakia |
| HRV | Croatia | | SVN | Slovenia |
| HUN | Hungary | | SWE | Sweden |
| IRL | Ireland | | | |

## 2. Sources

Raw data is fetched broadly on purpose, as it would be in practice: whole
files and all countries and aggregates. Selecting, reshaping, matching and
aggregating happen in the transform step.

### 2.1 Our World in Data: energy dataset

| Item | Value |
|---|---|
| Request | `GET https://raw.githubusercontent.com/owid/energy-data/master/owid-energy-data.csv` |
| Raw shape | About 23 000 rows × 130 columns (9 MB): every country and region from 1900. Regions have an empty `iso_code` (for example "Africa (Ember)"). |
| Used | Rows: `iso_code` in scope, `year` ≥ 2000. Columns: `iso_code`, `year` and the 10 columns in section 3.2. |
| Coverage (checked 2026-10-22) | All 29 countries, 2000–2025, except Iceland 2025. `wind_electricity` is empty for 53 early country-years. |
| Upstream | For 2000 onward, the electricity values are Ember's Yearly Electricity Data, republished by OWID |
| Licence | CC BY 4.0. Data that OWID republishes keeps its original licence; Ember's data is also CC BY 4.0 |
| Attribution | "Our World in Data, based on Ember – Yearly Electricity Data (2026)" |

### 2.2 Eurostat: SDMX 3.0 API

| Item | Value |
|---|---|
| Request | `GET https://ec.europa.eu/eurostat/api/dissemination/sdmx/3.0/data/dataflow/ESTAT/{DATASET}/1.0?format=csvdata&formatVersion=2.0&labels=both&compress=false&c[DIM]=v1,v2…` |
| Raw shape | SDMX-CSV 2.0, long format: one row per observation. Every coded value holds `CODE: Label` (for example `EL: Greece`), and so does every coded column name (for example `TIME_PERIOD: Time`), so columns are matched by code. Values are text; status flags sit in their own column. No `geo` filter is sent, so all countries and aggregates (EU27, euro area, candidate countries, …) are included. |
| Country matching | The label part of `geo` (`EL: Greece` → `Greece`) is matched against the Eurostat labels in section 1 |

| Dataset | Frequency | Fixed filters | Coverage | To annual |
|---|---|---|---|---|
| `nrg_ind_ren`: share of energy from renewable sources | annual | `FREQ=A`, `UNIT=PC`, `NRG_BAL=REN,REN_ELC` | 2004–2025 | as published |
| `nrg_pc_204`: household electricity prices | semi-annual | `FREQ=S`, `SIEC=E7000`, `UNIT=KWH`, `NRG_CONS=KWH2500-4999` (band DC), `TAX=I_TAX` (all taxes and levies), `CURRENCY=EUR,PPS` | 2007-S1 – 2026-S1 with the current bands | mean of S1 and S2 |
| `nrg_cb_pem`: net electricity generation by fuel | **monthly** | `FREQ=M`, `SIEC=TOTAL,RA000,CF,CF_R` (total, renewables and biofuels, combustible fuels, renewable combustible fuels), `UNIT=GWH` | `TOTAL` 2008-01 – 2026-08; `RA000`, `CF_R` 2017-01 – 2026-08 | sum of 12 months, GWh → TWh; fossil = `CF − CF_R` |

| Item | Value |
|---|---|
| Licence | Free reuse for commercial and non-commercial purposes with acknowledgement of the source (Commission Decision 2011/833/EU) |
| Attribution | "Source: Eurostat, datasets nrg_ind_ren, nrg_pc_204 and nrg_cb_pem (accessed YYYY-MM-DD)" |
| Modified data | Required by Eurostat: the output states that values were aggregated (semi-annual and monthly to annual) and that Eurostat is not responsible for the transformation |

#### Coverage of the annual values

Checked on 2026-10-27 against the full time range, for the 29 countries
in scope. A sub-annual series gives an annual value only for complete
years (section 4, rule 3), so the first annual value can be later than the first
period published.

| Columns | First annual value | Exceptions |
|---|---|---|
| 14–15 (RES shares) | 2004 | none |
| 16–17 (household prices) | 2008 | 2007 for Croatia, Czechia, Germany, Luxembourg, the Netherlands and Sweden, which start in 2007-S1; 2012 for Iceland (starts 2012-S1) |
| 18 (net generation) | 2008 | 2011 for Croatia |
| 19 (renewables) | 2017 | none |
| 20 (fossil, `CF − CF_R`) | 2017 | per-country exceptions to be confirmed by the next coverage run |
| 16–20 | | the current year stays empty until all its periods are published |

#### Fossil generation: `CF − CF_R`

1. **Definition.** Eurostat defines fossil fuels as non-renewable
   sources: coal, coal products, natural gas, derived gas, crude oil,
   petroleum products and non-renewable waste, with peat and oil shale
   in the same group. Combustible fuels (`CF`) minus renewable
   combustible fuels (`CF_R`) is that group.
2. **Check against `FE`.** Eurostat publishes fossil energy (`FE`)
   directly only from 2023-01 (France), 2023-02 (Sweden) and 2024-01
   (all others). In every month where both exist (2023-01 – 2026-08),
   `CF − CF_R` matches `FE` within 1 % for 28 of 29 countries.
3. **Rejected alternatives.** Coal + gas + oil (`C0000 + G3000 +
   O4000XBIO`) matches `FE` for only 9 of 27 countries: it misses peat,
   oil shale and non-renewable waste (Estonia, for example, generates
   from oil shale), and the IEA documents gaps in the monthly fuel
   breakdown for Estonia, Sweden and Poland. `CF − CF_R − CF_NR_OTH`
   matches for 8 of 29, because `CF_NR_OTH` (non-renewable waste) is
   fossil by Eurostat's definition.
4. **Known issue: France.** `CF − CF_R` differs from `FE` by up to
   101.9 % in some months (median 0.0 %). No published explanation was
   found. The transform step decides how implausible monthly values are
   handled.

### 2.3 World Bank: Indicators API v2

| Item | Value |
|---|---|
| Request | `GET https://api.worldbank.org/v2/country/all/indicator/{code}?format=json&date=1990:{last}&per_page=1000&page={n}`, one series of requests per indicator, following pages until `page == pages` |
| Raw shape | JSON array `[metadata, rows]`, 262 economies and aggregates (World, regions, income groups; checked 2026-10-27) × 36 years ≈ 9 400 rows per indicator, so about 10 pages. Missing values are `null`. API errors also arrive with HTTP 200, as a one-element array holding `message`, so the shape is checked, not only the status code. |
| Indicators | `NY.GDP.MKTP.PP.KD`: GDP, PPP (constant 2021 international $), **total**; `SP.POP.TOTL`: population, total |
| Used | Rows with `countryiso3code` in scope (`country.id` is alpha-2), `date` ≥ 2000. GDP per capita is computed: GDP ÷ population. |
| Licence | CC BY 4.0, unless an indicator's metadata says otherwise |
| Attribution | "The World Bank: World Development Indicators" |

## 3. Output: data dictionary

CSV: UTF-8, comma-separated, header row, `.` as the decimal separator,
empty field for a missing value, sorted by `country_iso3` then `year`.
24 columns.

### 3.1 Keys

| # | Column | Type | Description |
|---|---|---|---|
| 1 | `country_iso3` | string | ISO 3166-1 alpha-3 code |
| 2 | `country_name` | string | English short name (country table, not a source) |
| 3 | `year` | integer | Calendar year |

### 3.2 Electricity generation (OWID, based on Ember)

| # | Column | Type | Unit | OWID column |
|---|---|---|---|---|
| 4 | `electricity_generation_twh` | float | TWh | `electricity_generation` |
| 5 | `renewables_electricity_twh` | float | TWh | `renewables_electricity` |
| 6 | `fossil_electricity_twh` | float | TWh | `fossil_electricity` (coal + gas + oil) |
| 7 | `nuclear_electricity_twh` | float | TWh | `nuclear_electricity` |
| 8 | `wind_electricity_twh` | float | TWh | `wind_electricity` |
| 9 | `solar_electricity_twh` | float | TWh | `solar_electricity` |
| 10 | `hydro_electricity_twh` | float | TWh | `hydro_electricity` |
| 11 | `renewables_share_elec_pct` | float | % of generation | `renewables_share_elec` |
| 12 | `fossil_share_elec_pct` | float | % of generation | `fossil_share_elec` |
| 13 | `carbon_intensity_elec_gco2e_per_kwh` | float | gCO₂e/kWh | `carbon_intensity_elec` |

### 3.3 Eurostat

| # | Column | Type | Unit | Source |
|---|---|---|---|---|
| 14 | `res_share_overall_pct` | float | % of gross final energy consumption | `nrg_ind_ren`, `REN` |
| 15 | `res_share_electricity_pct` | float | % of gross electricity consumption | `nrg_ind_ren`, `REN_ELC` |
| 16 | `household_price_eur_per_kwh` | float | EUR/kWh | `nrg_pc_204`, `EUR`: mean of S1 and S2 |
| 17 | `household_price_pps_per_kwh` | float | PPS/kWh | `nrg_pc_204`, `PPS`: mean of S1 and S2 |
| 18 | `net_generation_eurostat_twh` | float | TWh | `nrg_cb_pem`, `TOTAL`: sum of 12 months ÷ 1000 |
| 19 | `renewables_generation_eurostat_twh` | float | TWh | `nrg_cb_pem`, `RA000`: sum of 12 months ÷ 1000 |
| 20 | `fossil_generation_eurostat_twh` | float | TWh | `nrg_cb_pem`, `CF − CF_R`: sum of 12 months ÷ 1000 |

### 3.4 Economy (World Bank)

| # | Column | Type | Unit | Source |
|---|---|---|---|---|
| 21 | `gdp_per_capita_ppp_const2021_intl_usd` | float | constant 2021 international $ | `NY.GDP.MKTP.PP.KD` ÷ `SP.POP.TOTL` |
| 22 | `population` | integer | people | `SP.POP.TOTL` |

### 3.5 Derived

| # | Column | Type | Unit | Formula |
|---|---|---|---|---|
| 23 | `electricity_generation_per_capita_mwh` | float | MWh per person | `electricity_generation_twh × 1 000 000 / population` |
| 24 | `renewables_minus_fossil_share_pp` | float | percentage points | `renewables_share_elec_pct − fossil_share_elec_pct` |

A computed value (columns 16–21, 23, 24) is empty when any of its inputs
is empty.

## 4. Rules

1. **Grid.** The output has a row for every country × year in scope.
   Every source is left-joined onto that grid, so a missing upstream value
   shows as an empty field and is never dropped or filled in.
2. **No interpolation, no splicing.** Prices before 2007 use different
   consumption bands and are not mixed in. Gaps stay empty.
3. **Sub-annual to annual.** A year gets a value only when all its
   periods are present: both halves for prices (mean), all 12 months for
   generation (sum). The current, incomplete year is therefore empty in
   columns 16–20.
4. **Eurostat status flags** (for example `p` = provisional) are not
   carried into the CSV. Provisional values are included as published.
5. **Two renewable shares on purpose.** `renewables_share_elec_pct`
   (Ember) is the share of actual generation in that year.
   `res_share_electricity_pct` (Eurostat) follows the EU Renewable Energy
   Directive: hydro and wind are normalised for weather, and the share is
   of gross electricity consumption. The two differ and are not
   interchangeable.
6. **Two generation series on purpose.** Columns 4–6 (Ember via OWID) and
   18–20 (Eurostat monthly) measure the same thing from different
   collections; they are expected to be close, not identical, and are a
   cross-check of each other. Eurostat notes that annual totals added up
   from monthly data may differ from its annual data. The fossil columns
   also differ by definition: Ember's (column 6) is coal, gas and oil,
   while Eurostat's (column 20) also includes peat, oil shale and
   non-renewable waste, so countries using those fuels (for example
   Estonia, Finland and Ireland) differ more.
7. **Population** comes only from the World Bank; OWID's population
   column is not used.

## 5. Test fixtures

`make fixtures` (script `scripts/record_fixtures.py`) records a time
slice of every source into `tests/fixtures/`, using the same request
builders as the pipeline and keeping the raw breadth (all countries,
aggregates and columns):

| Source | Slice | Files |
|---|---|---|
| OWID | all rows for 2008–2009, unchanged | `owid/owid-energy-data.csv` |
| Eurostat | `nrg_ind_ren` 2008–2009; `nrg_pc_204` 2008-S1–2009-S1 (2009 incomplete on purpose); `nrg_cb_pem` 2017-01–2018-06 (the first months with every monthly series; 2018 incomplete on purpose) | `eurostat/{dataset}.csv`, byte for byte |
| World Bank | all economies, 2008–2009, 200 rows per page (several pages on purpose) | `worldbank/{indicator}/page-{n}.json`, re-indented |

`manifest.json` lists the recording time (UTC), URL, size and SHA-256 of
every file. The script writes nothing unless every source succeeds, and
it fails if a country in scope cannot be matched in a source.
