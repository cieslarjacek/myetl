"""Unit tests for ``myetl.cache``.

File-system tests use pytest's ``tmp_path``; no test touches the real
cache directory.
"""

import hashlib
import io
import json
import logging
import pickle
import threading
import zipfile
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from myetl.cache import (
    CACHE_DIRECTORY_VARIABLE,
    DEFAULT_CACHE_DIRECTORY,
    DEFAULT_CACHE_POLICY,
    CachePolicy,
    Freshness,
    MissingResourceError,
    Snapshot,
    SnapshotCache,
    SnapshotFormatError,
    cache_directory_from_environment,
    classify_age,
    decode_snapshot,
    describe_age,
    encode_snapshot,
    utc_now,
)
from myetl.sources.response_validation import SourceResponse

FETCHED_AT = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)

#: How long a thread test waits before concluding a thread is blocked.
BLOCKED_WAIT_SECONDS = 0.2

#: Fixed part of a zip local file header, before the member name and
#: extra field (ZIP specification, section 4.3.7).
ZIP_LOCAL_HEADER_FIXED_BYTES = 30


def create_snapshot(
    source_name: str = "owid", fetched_at: datetime = FETCHED_AT
) -> Snapshot:
    """Build a two-response snapshot of ``source_name``."""
    responses = (
        SourceResponse(
            "table-a", "https://example.test/a.csv", b"country,year\n"
        ),
        SourceResponse(
            "table-b", "https://example.test/b.json", b'[{"pages": 1}, []]'
        ),
    )
    return Snapshot(source_name, fetched_at, responses)


def rewrite_manifest(
    snapshot_file_content: bytes, manifest_changes: dict[str, object]
) -> bytes:
    """Return a copy of a snapshot file with manifest fields changed."""
    source_archive = zipfile.ZipFile(io.BytesIO(snapshot_file_content))
    archive_buffer = io.BytesIO()
    with source_archive, zipfile.ZipFile(archive_buffer, "w") as archive:
        for member_name in source_archive.namelist():
            member_content = source_archive.read(member_name)
            if member_name == "manifest.json":
                manifest = json.loads(member_content) | manifest_changes
                member_content = json.dumps(manifest).encode()
            archive.writestr(member_name, member_content)
    return archive_buffer.getvalue()


# --------------------------------------------------------------------
# Values
# --------------------------------------------------------------------


@pytest.mark.parametrize(
    "source_name", ["", "OWID", "world bank", "../owid", "1owid"]
)
def test_snapshot_rejects_unsafe_source_name(source_name: str) -> None:
    with pytest.raises(ValueError, match="invalid source name"):
        create_snapshot(source_name)


def test_snapshot_rejects_time_without_zone() -> None:
    with pytest.raises(ValueError, match="no time zone"):
        create_snapshot(fetched_at=datetime(2026, 10, 28, 12, 0))


def test_snapshot_rejects_empty_responses() -> None:
    with pytest.raises(ValueError, match="empty"):
        Snapshot("owid", FETCHED_AT, ())


def test_snapshot_content_size_sums_bodies() -> None:
    snapshot = create_snapshot()

    assert snapshot.content_size_bytes == 13 + 18


def test_select_responses_returns_matches_in_request_order() -> None:
    first_page = SourceResponse("SP.POP.TOTL", "https://x/1", b"1")
    dataset = SourceResponse("nrg_ind_ren", "https://x/csv", b"csv")
    second_page = SourceResponse("SP.POP.TOTL", "https://x/2", b"2")
    snapshot = Snapshot(
        "worldbank", FETCHED_AT, (first_page, dataset, second_page)
    )

    selected_responses = snapshot.select_responses("SP.POP.TOTL")

    assert selected_responses == (first_page, second_page)


def test_select_responses_of_missing_resource_fails() -> None:
    snapshot = create_snapshot("eurostat")

    with pytest.raises(MissingResourceError) as raised:
        snapshot.select_responses("nrg_cb_pem")

    assert str(raised.value) == (
        "eurostat snapshot has no response for 'nrg_cb_pem'"
    )


def test_missing_resource_error_survives_pickling() -> None:
    error = MissingResourceError("eurostat", "nrg_cb_pem")

    # Safe: the bytes come from ``pickle.dumps`` one call earlier.
    restored_error = pickle.loads(pickle.dumps(error))  # noqa: S301

    assert restored_error.source_name == "eurostat"
    assert restored_error.resource_code == "nrg_cb_pem"
    assert str(restored_error) == str(error)


def test_policy_rejects_non_positive_fresh_age() -> None:
    with pytest.raises(ValueError, match="positive"):
        CachePolicy(max_fresh_age=timedelta(0))


def test_policy_rejects_stale_age_below_fresh_age() -> None:
    with pytest.raises(ValueError, match="below"):
        CachePolicy(timedelta(hours=24), timedelta(hours=23))


def test_production_clock_returns_utc_time() -> None:
    current_time = utc_now()

    assert current_time.tzinfo is UTC


def test_default_policy_is_24_hours_and_90_days() -> None:
    assert DEFAULT_CACHE_POLICY.max_fresh_age == timedelta(hours=24)
    assert DEFAULT_CACHE_POLICY.max_stale_age == timedelta(days=90)


# --------------------------------------------------------------------
# Age
# --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("snapshot_age", "expected_freshness"),
    [
        (timedelta(0), Freshness.FRESH),
        (timedelta(hours=24), Freshness.FRESH),
        (timedelta(hours=24, seconds=1), Freshness.STALE_USABLE),
        (timedelta(days=90), Freshness.STALE_USABLE),
        (timedelta(days=90, seconds=1), Freshness.EXPIRED),
        (timedelta(seconds=-1), Freshness.EXPIRED),
    ],
    ids=[
        "just-fetched",
        "fresh-limit",
        "past-fresh-limit",
        "stale-limit",
        "past-stale-limit",
        "from-the-future",
    ],
)
def test_classify_age_limits_are_inclusive(
    snapshot_age: timedelta, expected_freshness: Freshness
) -> None:
    now = FETCHED_AT + snapshot_age

    freshness = classify_age(FETCHED_AT, now, DEFAULT_CACHE_POLICY)

    assert freshness is expected_freshness


def test_classify_age_compares_across_time_zones() -> None:
    two_hours_east = timezone(timedelta(hours=2))
    now = datetime(2026, 10, 29, 14, 0, tzinfo=two_hours_east)

    freshness = classify_age(FETCHED_AT, now, DEFAULT_CACHE_POLICY)

    assert freshness is Freshness.FRESH


@pytest.mark.parametrize(
    ("snapshot_age", "expected_text"),
    [
        (timedelta(0), "0 d 0 h 0 min"),
        (timedelta(minutes=59, seconds=59), "0 d 0 h 59 min"),
        (timedelta(days=3, hours=4, minutes=5), "3 d 4 h 5 min"),
        (timedelta(days=90), "90 d 0 h 0 min"),
    ],
)
def test_describe_age_truncates_to_minutes(
    snapshot_age: timedelta, expected_text: str
) -> None:
    assert describe_age(snapshot_age) == expected_text


# --------------------------------------------------------------------
# Cache directory
# --------------------------------------------------------------------


def test_cache_directory_defaults_when_variable_unset() -> None:
    assert cache_directory_from_environment({}) == DEFAULT_CACHE_DIRECTORY


def test_cache_directory_defaults_when_variable_empty() -> None:
    environment = {CACHE_DIRECTORY_VARIABLE: ""}

    assert cache_directory_from_environment(environment) == (
        DEFAULT_CACHE_DIRECTORY
    )


def test_cache_directory_comes_from_variable() -> None:
    environment = {CACHE_DIRECTORY_VARIABLE: "/var/cache/myetl"}

    assert cache_directory_from_environment(environment) == Path(
        "/var/cache/myetl"
    )


# --------------------------------------------------------------------
# Snapshot file format
# --------------------------------------------------------------------


def test_snapshot_survives_encoding() -> None:
    snapshot = create_snapshot()

    decoded_snapshot = decode_snapshot(encode_snapshot(snapshot))

    assert decoded_snapshot == snapshot


def test_encoding_is_deterministic() -> None:
    snapshot = create_snapshot()

    assert encode_snapshot(snapshot) == encode_snapshot(snapshot)


def test_manifest_lists_code_url_size_and_checksum() -> None:
    snapshot = create_snapshot()

    with zipfile.ZipFile(io.BytesIO(encode_snapshot(snapshot))) as archive:
        manifest = json.loads(archive.read("manifest.json"))

    first_response = snapshot.responses[0]
    assert manifest["format_version"] == 1
    assert manifest["source_name"] == "owid"
    assert manifest["fetched_at"] == "2026-10-28T12:00:00+00:00"
    assert manifest["responses"][0] == {
        "resource_code": "table-a",
        "member_name": "responses/0001",
        "url": first_response.url,
        "bytes": len(first_response.content),
        "sha256": hashlib.sha256(first_response.content).hexdigest(),
    }


@pytest.mark.parametrize(
    "snapshot_file_content",
    [b"", b"not a zip archive"],
    ids=["empty", "not-zip"],
)
def test_decoding_non_archive_fails(snapshot_file_content: bytes) -> None:
    with pytest.raises(SnapshotFormatError, match="damaged snapshot"):
        decode_snapshot(snapshot_file_content)


def test_decoding_truncated_archive_fails() -> None:
    snapshot_file_content = encode_snapshot(create_snapshot())

    with pytest.raises(SnapshotFormatError, match="damaged snapshot"):
        decode_snapshot(snapshot_file_content[:-30])


def test_decoding_corrupt_compressed_data_fails() -> None:
    long_response = SourceResponse(
        "table-a", "https://x", bytes(range(256)) * 40
    )
    snapshot = Snapshot("owid", FETCHED_AT, (long_response,))
    snapshot_file_content = bytearray(encode_snapshot(snapshot))
    with zipfile.ZipFile(io.BytesIO(snapshot_file_content)) as archive:
        member_info = archive.getinfo("responses/0001")
    data_offset = (
        member_info.header_offset
        + ZIP_LOCAL_HEADER_FIXED_BYTES
        + len(member_info.filename)
        + len(member_info.extra)
    )
    snapshot_file_content[data_offset] ^= 0xFF

    with pytest.raises(SnapshotFormatError, match="decompressing"):
        decode_snapshot(bytes(snapshot_file_content))


def test_decoding_archive_without_manifest_fails() -> None:
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w") as archive:
        archive.writestr("responses/0001", b"data")

    with pytest.raises(SnapshotFormatError, match="KeyError"):
        decode_snapshot(archive_buffer.getvalue())


@pytest.mark.parametrize(
    ("manifest_changes", "expected_problem"),
    [
        ({"format_version": 2}, "unexpected manifest"),
        ({"responses": "none"}, "unexpected manifest"),
        ({"responses": [{"url": "https://x"}]}, "unexpected response entry"),
        (
            {
                "responses": [
                    {
                        "member_name": "responses/0001",
                        "url": "https://x",
                        "sha256": "0",
                    }
                ]
            },
            "unexpected response entry",
        ),
        ({"fetched_at": "yesterday"}, "ValueError"),
        ({"fetched_at": "2026-10-28T12:00:00"}, "no time zone"),
        ({"source_name": "../owid"}, "invalid source name"),
    ],
    ids=[
        "unknown-version",
        "responses-not-list",
        "entry-without-member",
        "entry-without-resource-code",
        "time-not-iso",
        "time-without-zone",
        "unsafe-source-name",
    ],
)
def test_decoding_malformed_manifest_fails(
    manifest_changes: dict[str, object], expected_problem: str
) -> None:
    snapshot_file_content = rewrite_manifest(
        encode_snapshot(create_snapshot()), manifest_changes
    )

    with pytest.raises(SnapshotFormatError, match=expected_problem):
        decode_snapshot(snapshot_file_content)


def test_decoding_tampered_response_fails() -> None:
    snapshot_file_content = encode_snapshot(create_snapshot())
    source_archive = zipfile.ZipFile(io.BytesIO(snapshot_file_content))
    archive_buffer = io.BytesIO()
    with source_archive, zipfile.ZipFile(archive_buffer, "w") as archive:
        for member_name in source_archive.namelist():
            member_content = source_archive.read(member_name)
            if member_name == "responses/0001":
                member_content = b"tampered"
            archive.writestr(member_name, member_content)

    with pytest.raises(SnapshotFormatError, match="checksum mismatch"):
        decode_snapshot(archive_buffer.getvalue())


# --------------------------------------------------------------------
# SnapshotCache: read and write
# --------------------------------------------------------------------


def test_read_of_empty_cache_returns_none(tmp_path: Path) -> None:
    cache = SnapshotCache(tmp_path / "cache")

    assert cache.read("owid") is None


def test_written_snapshot_is_read_back(tmp_path: Path) -> None:
    cache = SnapshotCache(tmp_path / "cache")
    snapshot = create_snapshot()

    cache.write(snapshot)

    assert cache.read("owid") == snapshot
    assert (tmp_path / "cache" / "owid.zip").is_file()


def test_write_replaces_previous_snapshot(tmp_path: Path) -> None:
    cache = SnapshotCache(tmp_path)
    cache.write(create_snapshot())
    newer_snapshot = create_snapshot(fetched_at=FETCHED_AT + timedelta(days=1))

    cache.write(newer_snapshot)

    assert cache.read("owid") == newer_snapshot


def test_write_leaves_no_temporary_file(tmp_path: Path) -> None:
    cache = SnapshotCache(tmp_path)

    cache.write(create_snapshot())

    assert [path.name for path in tmp_path.iterdir()] == ["owid.zip"]


def test_sources_are_stored_separately(tmp_path: Path) -> None:
    cache = SnapshotCache(tmp_path)
    owid_snapshot = create_snapshot("owid")
    eurostat_snapshot = create_snapshot("eurostat")

    cache.write(owid_snapshot)
    cache.write(eurostat_snapshot)

    assert cache.read("owid") == owid_snapshot
    assert cache.read("eurostat") == eurostat_snapshot


def test_damaged_snapshot_is_ignored_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "owid.zip").write_bytes(b"not a zip archive")
    cache = SnapshotCache(tmp_path)

    with caplog.at_level(logging.WARNING, logger="myetl.cache"):
        cached_snapshot = cache.read("owid")

    assert cached_snapshot is None
    [warning_record] = caplog.records
    assert "damaged snapshot" in warning_record.getMessage()
    assert "owid.zip" in warning_record.getMessage()


def test_snapshot_of_other_source_is_ignored_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    eurostat_file_content = encode_snapshot(create_snapshot("eurostat"))
    (tmp_path / "owid.zip").write_bytes(eurostat_file_content)
    cache = SnapshotCache(tmp_path)

    with caplog.at_level(logging.WARNING, logger="myetl.cache"):
        cached_snapshot = cache.read("owid")

    assert cached_snapshot is None
    assert "holds source 'eurostat'" in caplog.records[0].getMessage()


def test_read_rejects_unsafe_source_name(tmp_path: Path) -> None:
    cache = SnapshotCache(tmp_path)

    with pytest.raises(ValueError, match="invalid source name"):
        cache.read("../owid")


# --------------------------------------------------------------------
# SnapshotCache: lock
# --------------------------------------------------------------------


def test_lock_creates_directory_and_lock_file(tmp_path: Path) -> None:
    cache = SnapshotCache(tmp_path / "new" / "cache")

    with cache.lock("owid"):
        pass

    assert (tmp_path / "new" / "cache" / "owid.lock").is_file()


def test_lock_makes_another_thread_wait(tmp_path: Path) -> None:
    cache = SnapshotCache(tmp_path)
    second_lock_acquired = threading.Event()

    def acquire_lock_in_thread() -> None:
        with cache.lock("owid"):
            second_lock_acquired.set()

    with cache.lock("owid"):
        waiting_thread = threading.Thread(target=acquire_lock_in_thread)
        waiting_thread.start()
        was_acquired_while_held = second_lock_acquired.wait(
            BLOCKED_WAIT_SECONDS
        )
    waiting_thread.join()

    assert not was_acquired_while_held
    assert second_lock_acquired.is_set()


def test_locks_of_different_sources_are_independent(tmp_path: Path) -> None:
    cache = SnapshotCache(tmp_path)
    other_lock_acquired = threading.Event()

    def acquire_other_lock_in_thread() -> None:
        with cache.lock("eurostat"):
            other_lock_acquired.set()

    with cache.lock("owid"):
        other_thread = threading.Thread(target=acquire_other_lock_in_thread)
        other_thread.start()
        was_acquired_while_held = other_lock_acquired.wait(
            BLOCKED_WAIT_SECONDS
        )
    other_thread.join()

    assert was_acquired_while_held
