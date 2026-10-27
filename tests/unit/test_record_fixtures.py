"""Unit tests for ``scripts/record_fixtures.py``.

Every test injects a fake ``fetch`` that serves canned responses by
URL: no network access and no patching of module globals.
"""

import hashlib
import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest

import record_fixtures as recorder
from myetl.scope import COUNTRIES
from myetl.sources import owid, worldbank

OWID_HEADER = ["country", "year", "iso_code", *owid.COLUMNS]
RECORDED_AT = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


# --------------------------------------------------------------------
# Canned responses
# --------------------------------------------------------------------


def owid_csv(
    rows: Iterable[tuple[str, int, str]], header: list[str] = OWID_HEADER
) -> bytes:
    """Build an OWID-like CSV from (name, year, iso3) rows."""
    values = ["1.0"] * (len(header) - 3)
    lines = [",".join(header)]
    lines += [
        f"{name},{year},{iso3}," + ",".join(values)
        for name, year, iso3 in rows
    ]
    return ("\n".join(lines) + "\n").encode()


def owid_all_countries(years: Iterable[int]) -> bytes:
    """Build an OWID CSV with every country in scope and one region."""
    rows = [(c.name, year, c.iso3) for c in COUNTRIES for year in years]
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
    lines = [",".join(header)]
    lines += [
        f"dataflow,ESTAT:X(1.0),A: Annual,XX: {label},2008,1.0,,"
        for label in labels
    ]
    return ("\n".join(lines) + "\n").encode()


def header_without(code: str) -> list[str]:
    """Return the Eurostat header without the column ``code``."""
    return [name for name in EUROSTAT_HEADER if not name.startswith(code)]


ALL_EUROSTAT_LABELS = [c.eurostat_name for c in COUNTRIES] + [
    "European Union - 27 countries (from 2020)"
]


def world_bank_pages(
    indicator: worldbank.Indicator, total_pages: int = 2
) -> dict[str, bytes]:
    """Spread the countries in scope over ``total_pages`` pages."""
    responses = {}
    for number in range(1, total_pages + 1):
        rows = [
            {"countryiso3code": c.iso3, "date": "2008", "value": 1.0}
            for c in COUNTRIES[number - 1 :: total_pages]
        ]
        url = indicator.create_request_url(
            recorder.SLICE_YEARS, number, recorder.WORLD_BANK_URL_SETTINGS
        )
        document = [{"page": number, "pages": total_pages}, rows]
        responses[url] = json.dumps(document).encode()
    return responses


def eurostat_url(eurostat_slice: recorder.EurostatSlice) -> str:
    """Return the URL the recorder requests for a Eurostat slice."""
    dataset = eurostat_slice.dataset
    return dataset.create_request_url(eurostat_slice.period_range)


def all_responses() -> dict[str, bytes]:
    """Return valid responses for every URL the recorder requests."""
    responses = {owid.ENERGY_CSV_URL: owid_all_countries([2007, 2008, 2009])}
    for eurostat_slice in recorder.EUROSTAT_SLICES:
        responses[eurostat_url(eurostat_slice)] = eurostat_csv(
            ALL_EUROSTAT_LABELS
        )
    for indicator in worldbank.INDICATORS:
        responses |= world_bank_pages(indicator)
    return responses


def fake_fetch(responses: Mapping[str, bytes]) -> recorder.Fetch:
    """Return a fetch serving ``responses``; other URLs fail."""

    def fetch(url: str) -> bytes:
        if url not in responses:
            raise OSError(f"unexpected URL: {url}")
        return responses[url]

    return fetch


# --------------------------------------------------------------------
# OWID
# --------------------------------------------------------------------


def test_owid_keeps_header_and_only_slice_years() -> None:
    fetch = fake_fetch(all_responses())

    [fixture] = recorder.record_owid(fetch)

    header, *rows = fixture.content.decode().splitlines()
    assert header == ",".join(OWID_HEADER)
    assert {row.split(",")[1] for row in rows} == {"2008", "2009"}
    assert any(row.startswith("Africa (Ember),") for row in rows)
    assert fixture.path == recorder.OWID_FIXTURE


def test_owid_without_required_column_fails() -> None:
    header = [c for c in OWID_HEADER if c != "fossil_electricity"]
    rows = [(c.name, 2008, c.iso3) for c in COUNTRIES]
    fetch = fake_fetch({owid.ENERGY_CSV_URL: owid_csv(rows, header)})

    with pytest.raises(recorder.RecordingError, match="fossil_electricity"):
        recorder.record_owid(fetch)


def test_owid_without_country_in_scope_fails() -> None:
    rows = [(c.name, 2008, c.iso3) for c in COUNTRIES if c.iso3 != "MLT"]
    fetch = fake_fetch({owid.ENERGY_CSV_URL: owid_csv(rows)})

    with pytest.raises(recorder.RecordingError, match="MLT"):
        recorder.record_owid(fetch)


# --------------------------------------------------------------------
# Eurostat
# --------------------------------------------------------------------


def test_eurostat_stores_each_dataset_byte_for_byte() -> None:
    responses = all_responses()

    fixtures = recorder.record_eurostat(fake_fetch(responses))

    assert [f.path.name for f in fixtures] == [
        "nrg_ind_ren.csv",
        "nrg_pc_204.csv",
        "nrg_cb_pem.csv",
    ]
    assert all(f.content == responses[f.url] for f in fixtures)


def test_eurostat_country_absent_from_one_dataset_is_noted(
    capsys: pytest.CaptureFixture[str],
) -> None:
    responses = all_responses()
    prices = eurostat_url(recorder.EUROSTAT_SLICES[1])
    labels = [label for label in ALL_EUROSTAT_LABELS if label != "Iceland"]
    responses[prices] = eurostat_csv(labels)

    recorder.record_eurostat(fake_fetch(responses))

    assert "nrg_pc_204 has no data for: ['Iceland']" in capsys.readouterr().out


def test_eurostat_label_matching_no_dataset_fails() -> None:
    labels = [label for label in ALL_EUROSTAT_LABELS if label != "Germany"]
    responses = {
        eurostat_url(s): eurostat_csv(labels) for s in recorder.EUROSTAT_SLICES
    }

    with pytest.raises(recorder.RecordingError, match="Germany"):
        recorder.record_eurostat(fake_fetch(responses))


@pytest.mark.parametrize("code", ["geo", "TIME_PERIOD", "OBS_VALUE"])
def test_eurostat_without_required_column_fails(code: str) -> None:
    content = eurostat_csv(ALL_EUROSTAT_LABELS, header_without(code))

    with pytest.raises(recorder.RecordingError, match=code):
        recorder.geo_labels(content, "Eurostat test")


def test_eurostat_columns_match_by_code_with_or_without_label() -> None:
    plain_header = [name.split(": ")[0] for name in EUROSTAT_HEADER]

    labels = recorder.geo_labels(
        eurostat_csv(["Greece"], plain_header), "Eurostat test"
    )

    assert labels == frozenset({"Greece"})


def test_eurostat_geo_labels_from_real_response_lines() -> None:
    # Header and first rows of the live nrg_ind_ren response.
    content = (
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

    labels = recorder.geo_labels(content, "Eurostat nrg_ind_ren")

    assert labels == frozenset({"Albania", "Austria"})


def test_eurostat_geo_labels_include_aggregates() -> None:
    labels = recorder.geo_labels(
        eurostat_csv(ALL_EUROSTAT_LABELS), "Eurostat test"
    )

    assert labels == frozenset(ALL_EUROSTAT_LABELS)


# --------------------------------------------------------------------
# World Bank
# --------------------------------------------------------------------


def test_world_bank_records_every_page_of_every_indicator() -> None:
    fixtures = recorder.record_world_bank(fake_fetch(all_responses()))

    assert [f.path.as_posix() for f in fixtures] == [
        "worldbank/NY.GDP.MKTP.PP.KD/page-1.json",
        "worldbank/NY.GDP.MKTP.PP.KD/page-2.json",
        "worldbank/SP.POP.TOTL/page-1.json",
        "worldbank/SP.POP.TOTL/page-2.json",
    ]


def test_world_bank_error_message_fails() -> None:
    url = worldbank.GDP_PPP.create_request_url(
        recorder.SLICE_YEARS, 1, recorder.WORLD_BANK_URL_SETTINGS
    )
    error = [{"message": [{"id": "120", "value": "Invalid value"}]}]
    fetch = fake_fetch({url: json.dumps(error).encode()})

    with pytest.raises(recorder.RecordingError, match="unexpected response"):
        recorder.record_world_bank(fetch)


def test_world_bank_single_page_fails() -> None:
    fetch = fake_fetch(world_bank_pages(worldbank.GDP_PPP, total_pages=1))

    with pytest.raises(recorder.RecordingError, match="one page"):
        recorder.record_world_bank(fetch)


def test_world_bank_page_count_as_text_is_accepted() -> None:
    page = recorder.parse_world_bank_page([{"pages": "3"}, []], "test")

    assert page.total_pages == 3


# --------------------------------------------------------------------
# Output and entry point
# --------------------------------------------------------------------


def test_main_saves_fixtures_and_manifest(tmp_path: Path) -> None:
    status = recorder.main(fake_fetch(all_responses()), tmp_path)

    manifest = json.loads((tmp_path / recorder.MANIFEST_NAME).read_text())
    assert status == 0
    assert len(manifest["files"]) == 8
    for entry in manifest["files"]:
        content = (tmp_path / entry["path"]).read_bytes()
        assert hashlib.sha256(content).hexdigest() == entry["sha256"]


def test_main_writes_nothing_when_a_source_fails(tmp_path: Path) -> None:
    responses = all_responses()
    del responses[owid.ENERGY_CSV_URL]

    status = recorder.main(fake_fetch(responses), tmp_path)

    assert status == 1
    assert list(tmp_path.iterdir()) == []


def test_save_fixtures_replaces_only_source_folders(tmp_path: Path) -> None:
    stale = tmp_path / "eurostat" / "old.csv"
    unrelated = tmp_path / "handmade" / "keep.txt"
    for path in (stale, unrelated):
        path.parent.mkdir(parents=True)
        path.write_text("x")
    fixture = recorder.Fixture(Path("eurostat") / "new.csv", "https://x", b"y")

    recorder.save_fixtures([fixture], tmp_path, RECORDED_AT)

    assert not stale.exists()
    assert unrelated.exists()
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


def test_fetch_https_refuses_plain_http() -> None:
    with pytest.raises(recorder.RecordingError, match="non-https"):
        recorder.fetch_https("http://example.com/data.csv")
