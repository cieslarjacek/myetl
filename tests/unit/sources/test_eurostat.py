"""Unit tests for ``myetl.sources.eurostat``."""

import dataclasses
from urllib.parse import parse_qsl, urlsplit

import pytest

from myetl.sources import eurostat
from myetl.sources.eurostat import (
    HOUSEHOLD_PRICE,
    MONTHLY_GENERATION,
    REQUEST_URL_SETTINGS,
    RES_SHARE,
    PeriodRange,
    RequestUrlSettings,
)
from myetl.sources.response_validation import ResponseFormatError


def _query(url: str) -> dict[str, str]:
    """Return the decoded query of ``url`` as a dict."""
    return dict(parse_qsl(urlsplit(url).query))


# --------------------------------------------------------------------
# Request URL
# --------------------------------------------------------------------


def test_eurostat_url_path() -> None:
    parts = urlsplit(RES_SHARE.create_request_url())
    assert parts.scheme == "https"
    assert parts.netloc == "ec.europa.eu"
    assert parts.path == (
        "/eurostat/api/dissemination/sdmx/3.0/data/dataflow/ESTAT/"
        "NRG_IND_REN/1.0"
    )


def test_eurostat_url_requests_labelled_sdmx_csv() -> None:
    query = _query(MONTHLY_GENERATION.create_request_url())
    assert query["format"] == "csvdata"
    assert query["formatVersion"] == "2.0"
    assert query["labels"] == "both"
    assert query["compress"] == "false"


def test_eurostat_url_has_no_geo_filter() -> None:
    for dataset in (RES_SHARE, HOUSEHOLD_PRICE, MONTHLY_GENERATION):
        query = _query(dataset.create_request_url())
        assert not any(key.upper() == "C[GEO]" for key in query)


def test_eurostat_url_joins_multiple_values_with_comma() -> None:
    query = _query(HOUSEHOLD_PRICE.create_request_url())
    assert query["c[CURRENCY]"] == "EUR,PPS"
    assert query["c[NRG_CONS]"] == "KWH2500-4999"
    assert query["c[TAX]"] == "I_TAX"


def test_monthly_generation_filters() -> None:
    query = _query(MONTHLY_GENERATION.create_request_url())
    assert query["c[FREQ]"] == "M"
    assert query["c[SIEC]"] == "TOTAL,RA000,CF,CF_R"
    assert query["c[UNIT]"] == "GWH"


@pytest.mark.parametrize("period_range", [None, (None, None)])
def test_eurostat_url_full_time_range_without_bounds(
    period_range: PeriodRange | None,
) -> None:
    url = RES_SHARE.create_request_url(period_range)
    assert "c[TIME_PERIOD]" not in _query(url)


@pytest.mark.parametrize(
    ("period_range", "expected"),
    [
        (("2008-01", "2009-06"), "ge:2008-01+le:2009-06"),
        (("2008", None), "ge:2008"),
        ((None, "2009-S1"), "le:2009-S1"),
    ],
)
def test_eurostat_url_time_range(
    period_range: PeriodRange, expected: str
) -> None:
    url = MONTHLY_GENERATION.create_request_url(period_range)
    assert _query(url)["c[TIME_PERIOD]"] == expected
    # "+" must be percent-encoded, or the server reads it as a space.
    assert "%2B" in url or "+" not in expected


def test_eurostat_dataset_version_is_configurable() -> None:
    dataset = eurostat.Dataset("nrg_x", (), version="2.0")
    url = dataset.create_request_url()
    assert urlsplit(url).path.endswith("/NRG_X/2.0")


# --------------------------------------------------------------------
# Request URL settings
# --------------------------------------------------------------------


def test_default_settings_come_from_module_constants() -> None:
    from_constants = RequestUrlSettings(
        eurostat.API_BASE_URL,
        eurostat.FORMAT_PARAMS,
        eurostat.URL_SAFE_CHARACTERS,
    )

    assert from_constants == REQUEST_URL_SETTINGS


def test_injected_settings_change_base_url_and_format() -> None:
    settings = RequestUrlSettings(
        base_url="https://example.test/data",
        format_params=(("format", "TSV"),),
    )

    url = RES_SHARE.create_request_url(request_settings=settings)

    assert url.startswith("https://example.test/data/NRG_IND_REN/1.0?")
    assert _query(url)["format"] == "TSV"
    assert "labels" not in _query(url)


def test_request_url_settings_are_immutable() -> None:
    new_url = "https://example.test"
    with pytest.raises(dataclasses.FrozenInstanceError):
        REQUEST_URL_SETTINGS.base_url = new_url  # type: ignore[misc]


# --------------------------------------------------------------------
# Labels
# --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("coded_value", "label"),
    [
        ("EL: Greece", "Greece"),
        ("EU27_2020: European Union - 27 countries (from 2020)",
         "European Union - 27 countries (from 2020)"),
        ("EL", ""),
    ],
)  # fmt: skip
def test_get_value_label_returns_text_after_separator(
    coded_value: str, label: str
) -> None:
    assert eurostat.get_value_label(coded_value) == label


def test_get_value_label_accepts_another_separator() -> None:
    label = eurostat.get_value_label("EL | Greece", separator=" | ")
    assert label == "Greece"


@pytest.mark.parametrize(
    ("coded_value", "code"),
    [
        ("TIME_PERIOD: Time", "TIME_PERIOD"),
        ("geo: Geopolitical entity (reporting)", "geo"),
        ("AT: Austria", "AT"),
        ("STRUCTURE", "STRUCTURE"),
    ],
)
def test_get_value_code_returns_text_before_separator(
    coded_value: str, code: str
) -> None:
    assert eurostat.get_value_code(coded_value) == code


def test_get_value_code_accepts_another_separator() -> None:
    assert eurostat.get_value_code("EL | Greece", separator=" | ") == "EL"


def test_required_columns_are_codes_including_geo() -> None:
    assert eurostat.GEO_COLUMN in eurostat.REQUIRED_COLUMNS
    assert all(": " not in code for code in eurostat.REQUIRED_COLUMNS)


def test_get_value_label_shares_the_docstring_of_get_value_code() -> None:
    assert eurostat.get_value_label.__doc__ == eurostat.get_value_code.__doc__
    assert "label" in (eurostat.get_value_label.__doc__ or "")


# --------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------

#: Header as Eurostat returns it with ``labels=both`` (2026-10-26).
LABELLED_HEADER = (
    "STRUCTURE",
    "STRUCTURE_ID",
    "freq: Time frequency",
    "geo: Geopolitical entity (reporting)",
    "TIME_PERIOD: Time",
    "OBS_VALUE: Observation value",
)


def sdmx_csv(header: tuple[str, ...] = LABELLED_HEADER) -> bytes:
    """Build a one-row SDMX-CSV response under ``header``."""
    full_row = "dataflow,ESTAT:X(1.0),A: Annual,AT: Austria,2008,1.0"
    row_fields = full_row.split(",")[: len(header)]
    return (",".join(header) + "\n" + ",".join(row_fields) + "\n").encode()


def test_fetch_requests_url_for_period_range() -> None:
    requested_urls: list[str] = []
    period_range: PeriodRange = ("2008-S1", "2009-S1")

    def fetch_and_record(url: str) -> bytes:
        requested_urls.append(url)
        return sdmx_csv()

    HOUSEHOLD_PRICE.fetch_csv(fetch_and_record, period_range)

    assert requested_urls == [HOUSEHOLD_PRICE.create_request_url(period_range)]


def test_fetch_without_period_range_requests_full_range() -> None:
    requested_urls: list[str] = []

    def fetch_and_record(url: str) -> bytes:
        requested_urls.append(url)
        return sdmx_csv()

    RES_SHARE.fetch_csv(fetch_and_record)

    assert requested_urls == [RES_SHARE.create_request_url()]


def test_fetch_returns_response_byte_for_byte() -> None:
    csv_content = sdmx_csv()

    response = RES_SHARE.fetch_csv(lambda url: csv_content)

    assert response.url == RES_SHARE.create_request_url()
    assert response.content == csv_content


def test_fetch_matches_columns_without_labels() -> None:
    plain_header = tuple(
        eurostat.get_value_code(column_name) for column_name in LABELLED_HEADER
    )

    response = RES_SHARE.fetch_csv(lambda url: sdmx_csv(plain_header))

    assert response.content.startswith(b"STRUCTURE,STRUCTURE_ID,freq,geo")


@pytest.mark.parametrize("code", eurostat.REQUIRED_COLUMNS)
def test_fetch_without_required_column_fails(code: str) -> None:
    header = tuple(
        column_name
        for column_name in LABELLED_HEADER
        if eurostat.get_value_code(column_name) != code
    )

    with pytest.raises(ResponseFormatError, match=f"'{code}'"):
        RES_SHARE.fetch_csv(lambda url: sdmx_csv(header))


def test_fetch_of_header_only_response_fails() -> None:
    header_only_csv = (",".join(LABELLED_HEADER) + "\n").encode()

    with pytest.raises(ResponseFormatError, match="no data rows"):
        RES_SHARE.fetch_csv(lambda url: header_only_csv)


def test_fetch_datasets_requests_each_dataset_full_range() -> None:
    requested_urls: list[str] = []

    def fetch_and_record(url: str) -> bytes:
        requested_urls.append(url)
        return sdmx_csv()

    dataset_responses = eurostat.fetch_datasets(
        fetch_and_record, eurostat.PIPELINE_DATASETS
    )

    assert requested_urls == [
        dataset.create_request_url() for dataset in eurostat.PIPELINE_DATASETS
    ]
    assert [response.url for response in dataset_responses] == requested_urls


def test_datasets_are_the_three_project_datasets() -> None:
    assert eurostat.PIPELINE_DATASETS == (
        RES_SHARE,
        HOUSEHOLD_PRICE,
        MONTHLY_GENERATION,
    )


def test_fetch_tags_response_with_dataset_code() -> None:
    response = HOUSEHOLD_PRICE.fetch_csv(lambda url: sdmx_csv())

    assert response.resource_code == "nrg_pc_204"


def test_fetch_datasets_tags_each_response_with_its_dataset() -> None:
    dataset_responses = eurostat.fetch_datasets(
        lambda url: sdmx_csv(), eurostat.PIPELINE_DATASETS
    )

    assert [response.resource_code for response in dataset_responses] == [
        "nrg_ind_ren",
        "nrg_pc_204",
        "nrg_cb_pem",
    ]
