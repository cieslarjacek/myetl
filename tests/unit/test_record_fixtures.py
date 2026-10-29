"""Unit tests for ``scripts/record_fixtures.py``.

Every test injects a fake URL fetcher that serves canned responses by
URL: no network access and no patching of module globals.
"""

import hashlib
import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest

import record_fixtures as recorder
from myetl.http_client import Fetcher, FetchError
from myetl.scope import COUNTRIES
from myetl.sources import owid, worldbank
from myetl.sources.response_validation import ResponseFormatError

OWID_HEADER = ["country", "year", "iso_code", *owid.COLUMNS]
RECORDED_AT = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


# --------------------------------------------------------------------
# Canned responses
# --------------------------------------------------------------------


def owid_csv(
    rows: Iterable[tuple[str, int, str]], header: list[str] = OWID_HEADER
) -> bytes:
    """Build an OWID-like CSV from (name, year, iso3) rows."""
    measure_values = ["1.0"] * (len(header) - 3)
    csv_lines = [",".join(header)]
    csv_lines += [
        f"{name},{year},{iso3}," + ",".join(measure_values)
        for name, year, iso3 in rows
    ]
    return ("\n".join(csv_lines) + "\n").encode()


def owid_all_countries(years: Iterable[int]) -> bytes:
    """Build an OWID CSV with every country in scope and one region."""
    rows = [
        (country.name, year, country.iso3)
        for country in COUNTRIES
        for year in years
    ]
    rows += [("Africa (Ember)", year, "") for year in years]
    return owid_csv(rows)


#: Header as Eurostat returns it with ``labels=both`` (2026-10-26).
EUROSTAT_HEADER = (
    "STRUCTURE",
    "STRUCTURE_ID",
    "freq: Time frequency",
    "geo: Geopolitical entity (reporting)",
    "TIME_PERIOD: Time",
    "OBS_VALUE: Observation value",
    "OBS_FLAG: Observation status (Flag) V2 structure",
    "CONF_STATUS: Confidentiality status (flag)",
)


def eurostat_csv(
    labels: Iterable[str], header: Iterable[str] = EUROSTAT_HEADER
) -> bytes:
    """Build an SDMX-CSV 2.0 response with one row per geo label."""
    csv_lines = [",".join(header)]
    csv_lines += [
        f"dataflow,ESTAT:X(1.0),A: Annual,XX: {label},2008,1.0,,"
        for label in labels
    ]
    return ("\n".join(csv_lines) + "\n").encode()


def header_without(code: str) -> list[str]:
    """Return the Eurostat header without the column ``code``."""
    return [
        column_name
        for column_name in EUROSTAT_HEADER
        if not column_name.startswith(code)
    ]


ALL_EUROSTAT_LABELS = [country.eurostat_name for country in COUNTRIES] + [
    "European Union - 27 countries (from 2020)"
]


def world_bank_pages(
    indicator: worldbank.Indicator, total_pages: int = 2
) -> dict[str, bytes]:
    """Spread the countries in scope over ``total_pages`` pages."""
    canned_responses = {}
    for page_offset in range(total_pages):
        page_number = worldbank.FIRST_PAGE_NUMBER + page_offset
        page_rows = [
            {"countryiso3code": country.iso3, "date": "2008", "value": 1.0}
            for country in COUNTRIES[page_offset::total_pages]
        ]
        url = indicator.create_request_url(
            recorder.SLICE_YEARS,
            page_number,
            recorder.WORLD_BANK_URL_SETTINGS,
        )
        page_metadata = {"page": page_number, "pages": total_pages}
        canned_responses[url] = json.dumps([page_metadata, page_rows]).encode()
    return canned_responses


def eurostat_url(eurostat_slice: recorder.EurostatSlice) -> str:
    """Return the URL the recorder requests for a Eurostat slice."""
    dataset = eurostat_slice.dataset
    return dataset.create_request_url(eurostat_slice.period_range)


def all_responses() -> dict[str, bytes]:
    """Return valid responses for every URL the recorder requests."""
    canned_responses = {
        owid.ENERGY_CSV_URL: owid_all_countries([2007, 2008, 2009])
    }
    for eurostat_slice in recorder.EUROSTAT_SLICES:
        canned_responses[eurostat_url(eurostat_slice)] = eurostat_csv(
            ALL_EUROSTAT_LABELS
        )
    for indicator in worldbank.PIPELINE_INDICATORS:
        canned_responses |= world_bank_pages(indicator)
    return canned_responses


def create_url_fetcher(canned_responses: Mapping[str, bytes]) -> Fetcher:
    """Return a URL fetcher serving ``canned_responses``.

    Any other URL fails as a real fetcher would on HTTP 404.
    """

    def serve_canned_response(url: str) -> bytes:
        if url not in canned_responses:
            raise FetchError(url, "HTTP 404")
        return canned_responses[url]

    return serve_canned_response


# --------------------------------------------------------------------
# OWID
# --------------------------------------------------------------------


def test_owid_keeps_header_and_only_slice_years() -> None:
    url_fetcher = create_url_fetcher(all_responses())

    [fixture] = recorder.record_owid(url_fetcher)

    csv_header, *csv_rows = fixture.content.decode().splitlines()
    assert csv_header == ",".join(OWID_HEADER)
    assert {row.split(",")[1] for row in csv_rows} == {"2008", "2009"}
    assert any(row.startswith("Africa (Ember),") for row in csv_rows)
    assert fixture.path == recorder.OWID_FIXTURE


def test_owid_without_required_column_fails() -> None:
    header = [
        column_name
        for column_name in OWID_HEADER
        if column_name != "fossil_electricity"
    ]
    rows = [(country.name, 2008, country.iso3) for country in COUNTRIES]
    url_fetcher = create_url_fetcher(
        {owid.ENERGY_CSV_URL: owid_csv(rows, header)}
    )

    with pytest.raises(ResponseFormatError, match="fossil_electricity"):
        recorder.record_owid(url_fetcher)


def test_owid_without_country_in_scope_fails() -> None:
    rows = [
        (country.name, 2008, country.iso3)
        for country in COUNTRIES
        if country.iso3 != "MLT"
    ]
    url_fetcher = create_url_fetcher({owid.ENERGY_CSV_URL: owid_csv(rows)})

    with pytest.raises(recorder.RecordingError, match="MLT"):
        recorder.record_owid(url_fetcher)


# --------------------------------------------------------------------
# Eurostat
# --------------------------------------------------------------------


def test_eurostat_stores_each_dataset_byte_for_byte() -> None:
    canned_responses = all_responses()

    fixtures = recorder.record_eurostat(create_url_fetcher(canned_responses))

    assert [fixture.path.name for fixture in fixtures] == [
        "nrg_ind_ren.csv",
        "nrg_pc_204.csv",
        "nrg_cb_pem.csv",
    ]
    assert all(
        fixture.content == canned_responses[fixture.url]
        for fixture in fixtures
    )


def test_eurostat_country_absent_from_one_dataset_is_noted(
    capsys: pytest.CaptureFixture[str],
) -> None:
    canned_responses = all_responses()
    prices_url = eurostat_url(recorder.EUROSTAT_SLICES[1])
    labels = [label for label in ALL_EUROSTAT_LABELS if label != "Iceland"]
    canned_responses[prices_url] = eurostat_csv(labels)

    recorder.record_eurostat(create_url_fetcher(canned_responses))

    assert "nrg_pc_204 has no data for: ['Iceland']" in capsys.readouterr().out


def test_eurostat_label_matching_no_dataset_fails() -> None:
    labels = [label for label in ALL_EUROSTAT_LABELS if label != "Germany"]
    canned_responses = {
        eurostat_url(eurostat_slice): eurostat_csv(labels)
        for eurostat_slice in recorder.EUROSTAT_SLICES
    }

    with pytest.raises(recorder.RecordingError, match="Germany"):
        recorder.record_eurostat(create_url_fetcher(canned_responses))


@pytest.mark.parametrize("code", ["geo", "TIME_PERIOD", "OBS_VALUE"])
def test_eurostat_without_required_column_fails(code: str) -> None:
    csv_content = eurostat_csv(ALL_EUROSTAT_LABELS, header_without(code))
    canned_responses = {
        eurostat_url(eurostat_slice): csv_content
        for eurostat_slice in recorder.EUROSTAT_SLICES
    }

    with pytest.raises(ResponseFormatError, match=code):
        recorder.record_eurostat(create_url_fetcher(canned_responses))


def test_eurostat_columns_match_by_code_with_or_without_label() -> None:
    plain_header = [
        column_name.split(": ")[0] for column_name in EUROSTAT_HEADER
    ]

    labels_found = recorder.geo_labels(eurostat_csv(["Greece"], plain_header))

    assert labels_found == frozenset({"Greece"})


def test_eurostat_geo_labels_from_real_response_lines() -> None:
    # Header and first rows of the live nrg_ind_ren response.
    csv_content = (
        b"STRUCTURE,STRUCTURE_ID,freq: Time frequency,"
        b"nrg_bal: Energy balance,unit: Unit of measure,"
        b"geo: Geopolitical entity (reporting),TIME_PERIOD: Time,"
        b"OBS_VALUE: Observation value,"
        b"OBS_FLAG: Observation status (Flag) V2 structure,"
        b"CONF_STATUS: Confidentiality status (flag)\n"
        b"dataflow,ESTAT:NRG_IND_REN(1.0),A: Annual,"
        b"REN: Renewable energy - overall,PC: Percentage,AL: Albania,"
        b"2008,32.448,,\n"
        b"dataflow,ESTAT:NRG_IND_REN(1.0),A: Annual,"
        b"REN: Renewable energy - overall,PC: Percentage,AT: Austria,"
        b"2008,28.788,,\n"
    )

    labels_found = recorder.geo_labels(csv_content)

    assert labels_found == frozenset({"Albania", "Austria"})


def test_eurostat_geo_labels_include_aggregates() -> None:
    labels_found = recorder.geo_labels(eurostat_csv(ALL_EUROSTAT_LABELS))

    assert labels_found == frozenset(ALL_EUROSTAT_LABELS)


# --------------------------------------------------------------------
# World Bank
# --------------------------------------------------------------------


def test_world_bank_records_every_page_of_every_indicator() -> None:
    url_fetcher = create_url_fetcher(all_responses())

    fixtures = recorder.record_world_bank(url_fetcher)

    assert [fixture.path.as_posix() for fixture in fixtures] == [
        "worldbank/NY.GDP.MKTP.PP.KD/page-1.json",
        "worldbank/NY.GDP.MKTP.PP.KD/page-2.json",
        "worldbank/SP.POP.TOTL/page-1.json",
        "worldbank/SP.POP.TOTL/page-2.json",
    ]


def test_world_bank_error_message_fails() -> None:
    first_page_url = worldbank.GDP_PPP.create_request_url(
        recorder.SLICE_YEARS,
        worldbank.FIRST_PAGE_NUMBER,
        recorder.WORLD_BANK_URL_SETTINGS,
    )
    api_error = [{"message": [{"id": "120", "value": "Invalid value"}]}]
    url_fetcher = create_url_fetcher(
        {first_page_url: json.dumps(api_error).encode()}
    )

    with pytest.raises(ResponseFormatError, match="unexpected response"):
        recorder.record_world_bank(url_fetcher)


def test_world_bank_single_page_fails() -> None:
    url_fetcher = create_url_fetcher(
        world_bank_pages(worldbank.GDP_PPP, total_pages=1)
    )

    with pytest.raises(recorder.RecordingError, match="one page"):
        recorder.record_world_bank(url_fetcher)


# --------------------------------------------------------------------
# Output and entry point
# --------------------------------------------------------------------


def test_main_saves_fixtures_and_manifest(tmp_path: Path) -> None:
    exit_status = recorder.main(create_url_fetcher(all_responses()), tmp_path)

    manifest = json.loads((tmp_path / recorder.MANIFEST_NAME).read_text())
    assert exit_status == 0
    assert len(manifest["files"]) == 8
    for manifest_entry in manifest["files"]:
        fixture_content = (tmp_path / manifest_entry["path"]).read_bytes()
        fixture_sha256 = hashlib.sha256(fixture_content).hexdigest()
        assert fixture_sha256 == manifest_entry["sha256"]


def test_main_writes_nothing_when_a_source_fails(tmp_path: Path) -> None:
    canned_responses = all_responses()
    del canned_responses[owid.ENERGY_CSV_URL]

    exit_status = recorder.main(create_url_fetcher(canned_responses), tmp_path)

    assert exit_status == 1
    assert list(tmp_path.iterdir()) == []


def test_failed_source_is_reported_with_error_type_and_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    canned_responses = all_responses()
    del canned_responses[owid.ENERGY_CSV_URL]

    recorder.main(create_url_fetcher(canned_responses), tmp_path)

    expected_line = f"FAILED owid: FetchError: HTTP 404: {owid.ENERGY_CSV_URL}"
    assert expected_line in capsys.readouterr().err


def test_save_fixtures_replaces_only_source_folders(tmp_path: Path) -> None:
    stale_fixture = tmp_path / "eurostat" / "old.csv"
    unrelated_file = tmp_path / "handmade" / "keep.txt"
    for existing_file in (stale_fixture, unrelated_file):
        existing_file.parent.mkdir(parents=True)
        existing_file.write_text("x")
    new_fixture = recorder.Fixture(
        Path("eurostat") / "new.csv", "https://x", b"y"
    )

    recorder.save_fixtures([new_fixture], tmp_path, RECORDED_AT)

    assert not stale_fixture.exists()
    assert unrelated_file.exists()
    assert (tmp_path / "eurostat" / "new.csv").read_bytes() == b"y"


def test_build_manifest_describes_each_fixture() -> None:
    fixture = recorder.Fixture(Path("owid") / "a.csv", "https://x", b"abc")

    manifest = recorder.build_manifest([fixture], RECORDED_AT)

    assert manifest == {
        "recorded_at": "2026-10-26T12:00:00+00:00",
        "files": [
            {
                "path": "owid/a.csv",
                "url": "https://x",
                "bytes": 3,
                "sha256": hashlib.sha256(b"abc").hexdigest(),
            }
        ],
    }
