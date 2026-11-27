"""Unit tests for ``myetl.extract.cache``.

File-system tests use pytest's ``tmp_path``; no test touches the real
cache directory.
"""

import logging
import threading
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from myetl.extract.cache import (
    CACHE_DIRECTORY_VARIABLE,
    DEFAULT_CACHE_DIRECTORY,
    DEFAULT_CACHE_POLICY,
    CachePolicy,
    Freshness,
    SnapshotCache,
    cache_directory_from_environment,
    classify_age,
    describe_age,
    utc_now,
)
from myetl.extract.snapshot import Snapshot, encode_snapshot
from myetl.sources.response_validation import SourceResponse

FETCHED_AT = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)

#: How long a thread test waits before concluding a thread is blocked.
BLOCKED_WAIT_SECONDS = 0.2


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


# --------------------------------------------------------------------
# Values
# --------------------------------------------------------------------


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

    with caplog.at_level(logging.WARNING, logger="myetl.extract.cache"):
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

    with caplog.at_level(logging.WARNING, logger="myetl.extract.cache"):
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
