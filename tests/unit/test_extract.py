"""Unit tests for ``myetl.extract``.

Every test uses a real :class:`~myetl.cache.SnapshotCache` in pytest's
``tmp_path``, a fake clock and fake source fetchers: no network access
and no patching of module globals.
"""

import json
import logging
import pickle
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import pytest

from myetl.cache import SnapshotCache
from myetl.extract import (
    Extractor,
    Source,
    SourceUnavailableError,
    create_sources,
)
from myetl.http_client import Fetcher, FetchError
from myetl.sources import eurostat, owid, worldbank
from myetl.sources.response_validation import (
    ResponseFormatError,
    SourceResponse,
)

START_TIME = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
RESPONSES = (
    SourceResponse("table-a", "https://example.test/data.csv", b"a,b\n1,2\n"),
)
FETCH_FAILURE = FetchError("https://example.test/data.csv", "HTTP 503")

#: How long a thread test waits for a thread to reach a blocking call.
THREAD_SETTLE_SECONDS = 0.2


class FakeClock:
    """A clock that only moves when told to."""

    def __init__(self, now: datetime = START_TIME) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, time_step: timedelta) -> None:
        self.now += time_step


class FakeSourceFetcher:
    """Returns canned responses, or raises, and counts its calls."""

    def __init__(
        self,
        failure: Exception | None = None,
        canned_responses: tuple[SourceResponse, ...] = RESPONSES,
    ) -> None:
        self.failure = failure
        self.canned_responses = canned_responses
        self.call_count = 0

    def __call__(self, url_fetcher: Fetcher) -> tuple[SourceResponse, ...]:
        self.call_count += 1
        if self.failure is not None:
            raise self.failure
        return self.canned_responses


def refuse_url(url: str) -> bytes:
    """URL fetcher for tests whose sources never download anything."""
    raise AssertionError(f"unexpected download: {url}")


@pytest.fixture
def cache(tmp_path: Path) -> SnapshotCache:
    """Return an empty cache in a temporary directory."""
    return SnapshotCache(tmp_path / "cache")


@pytest.fixture
def clock() -> FakeClock:
    """Return a fake clock at :data:`START_TIME`."""
    return FakeClock()


@pytest.fixture
def extractor(cache: SnapshotCache, clock: FakeClock) -> Extractor:
    """Return an extractor over ``cache`` and ``clock``."""
    return Extractor(cache, refuse_url, clock)


def cache_snapshot_now(extractor: Extractor) -> None:
    """Cache a snapshot of ``owid`` fetched at the clock's time."""
    extractor.extract(Source("owid", FakeSourceFetcher()))


# --------------------------------------------------------------------
# Fresh and refreshed snapshots
# --------------------------------------------------------------------


def test_empty_cache_is_filled_by_fetching(
    extractor: Extractor, cache: SnapshotCache
) -> None:
    source_fetcher = FakeSourceFetcher()

    snapshot = extractor.extract(Source("owid", source_fetcher))

    assert source_fetcher.call_count == 1
    assert snapshot.responses == RESPONSES
    assert snapshot.fetched_at == START_TIME
    assert cache.read("owid") == snapshot


def test_snapshot_at_fresh_limit_is_used_without_fetching(
    extractor: Extractor, clock: FakeClock
) -> None:
    cache_snapshot_now(extractor)
    clock.advance(timedelta(hours=24))
    source_fetcher = FakeSourceFetcher()

    snapshot = extractor.extract(Source("owid", source_fetcher))

    assert source_fetcher.call_count == 0
    assert snapshot.fetched_at == START_TIME


def test_snapshot_past_fresh_limit_is_fetched_again(
    extractor: Extractor, clock: FakeClock
) -> None:
    cache_snapshot_now(extractor)
    clock.advance(timedelta(hours=24, seconds=1))
    source_fetcher = FakeSourceFetcher()

    snapshot = extractor.extract(Source("owid", source_fetcher))

    assert source_fetcher.call_count == 1
    assert snapshot.fetched_at == clock.now


def test_snapshot_time_is_when_fetch_started(
    extractor: Extractor, clock: FakeClock
) -> None:
    def fetch_slowly(url_fetcher: Fetcher) -> tuple[SourceResponse, ...]:
        clock.advance(timedelta(minutes=5))
        return RESPONSES

    snapshot = extractor.extract(Source("owid", fetch_slowly))

    assert snapshot.fetched_at == START_TIME


def test_damaged_cache_file_is_replaced_by_fetching(
    extractor: Extractor, cache: SnapshotCache
) -> None:
    cache.directory.mkdir(parents=True)
    (cache.directory / "owid.zip").write_bytes(b"not a zip archive")
    source_fetcher = FakeSourceFetcher()

    snapshot = extractor.extract(Source("owid", source_fetcher))

    assert source_fetcher.call_count == 1
    assert cache.read("owid") == snapshot


def test_fresh_snapshot_is_logged_with_its_age(
    extractor: Extractor,
    clock: FakeClock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    cache_snapshot_now(extractor)
    clock.advance(timedelta(hours=3, minutes=20))

    with caplog.at_level(logging.INFO, logger="myetl.extract"):
        extractor.extract(Source("owid", FakeSourceFetcher()))

    [info_record] = caplog.records
    assert info_record.getMessage() == (
        "owid: using cached snapshot, 0 d 3 h 20 min old"
    )


# --------------------------------------------------------------------
# Failed fetches
# --------------------------------------------------------------------


@pytest.mark.parametrize(
    "fetch_failure",
    [FETCH_FAILURE, ResponseFormatError("https://x", "no data rows")],
    ids=["fetch-error", "response-format-error"],
)
def test_failed_fetch_falls_back_to_stale_snapshot(
    extractor: Extractor, clock: FakeClock, fetch_failure: Exception
) -> None:
    cache_snapshot_now(extractor)
    clock.advance(timedelta(days=2))

    snapshot = extractor.extract(
        Source("owid", FakeSourceFetcher(fetch_failure))
    )

    assert snapshot.fetched_at == START_TIME


def test_fallback_is_logged_as_warning_with_age(
    extractor: Extractor,
    clock: FakeClock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    cache_snapshot_now(extractor)
    clock.advance(timedelta(days=2, hours=5))

    with caplog.at_level(logging.WARNING, logger="myetl.extract"):
        extractor.extract(Source("owid", FakeSourceFetcher(FETCH_FAILURE)))

    [warning_record] = caplog.records
    assert warning_record.levelno == logging.WARNING
    assert warning_record.getMessage() == (
        "owid: FetchError: HTTP 503: https://example.test/data.csv; "
        "using cached snapshot, 2 d 5 h 0 min old"
    )


def test_snapshot_at_stale_limit_is_still_a_fallback(
    extractor: Extractor, clock: FakeClock
) -> None:
    cache_snapshot_now(extractor)
    clock.advance(timedelta(days=90))

    snapshot = extractor.extract(
        Source("owid", FakeSourceFetcher(FETCH_FAILURE))
    )

    assert snapshot.fetched_at == START_TIME


def test_failed_fetch_keeps_the_cached_snapshot(
    extractor: Extractor, clock: FakeClock, cache: SnapshotCache
) -> None:
    cache_snapshot_now(extractor)
    cached_file_content = (cache.directory / "owid.zip").read_bytes()
    clock.advance(timedelta(days=2))

    extractor.extract(Source("owid", FakeSourceFetcher(FETCH_FAILURE)))

    assert (cache.directory / "owid.zip").read_bytes() == cached_file_content


def test_failed_fetch_without_snapshot_raises(extractor: Extractor) -> None:
    with pytest.raises(SourceUnavailableError) as raised:
        extractor.extract(Source("owid", FakeSourceFetcher(FETCH_FAILURE)))

    assert raised.value.source_name == "owid"
    assert "no cached snapshot" in str(raised.value)
    assert raised.value.__cause__ is FETCH_FAILURE


def test_failed_fetch_with_expired_snapshot_raises(
    extractor: Extractor, clock: FakeClock
) -> None:
    cache_snapshot_now(extractor)
    clock.advance(timedelta(days=90, seconds=1))

    with pytest.raises(SourceUnavailableError) as raised:
        extractor.extract(Source("owid", FakeSourceFetcher(FETCH_FAILURE)))

    assert str(raised.value) == (
        "owid unavailable: FetchError: HTTP 503: "
        "https://example.test/data.csv; cached snapshot from "
        "2026-10-28T12:00+00:00 is past the 90 d 0 h 0 min limit"
    )


def test_unexpected_error_is_not_hidden_by_snapshot(
    extractor: Extractor, clock: FakeClock
) -> None:
    cache_snapshot_now(extractor)
    clock.advance(timedelta(days=2))
    programming_error = KeyError("countryiso3code")

    with pytest.raises(KeyError):
        extractor.extract(Source("owid", FakeSourceFetcher(programming_error)))


def test_unavailable_error_survives_pickling() -> None:
    error = SourceUnavailableError("owid", "no cached snapshot")

    # Safe: the bytes come from ``pickle.dumps`` one call earlier.
    restored_error = pickle.loads(pickle.dumps(error))  # noqa: S301

    assert restored_error.source_name == "owid"
    assert str(restored_error) == "owid unavailable: no cached snapshot"


# --------------------------------------------------------------------
# Concurrency
# --------------------------------------------------------------------


def test_concurrent_extracts_of_one_source_fetch_once(
    extractor: Extractor,
) -> None:
    fetch_started = threading.Event()
    fetch_may_finish = threading.Event()
    fetch_count = 0

    def fetch_when_released(
        url_fetcher: Fetcher,
    ) -> tuple[SourceResponse, ...]:
        nonlocal fetch_count
        fetch_count += 1
        fetch_started.set()
        fetch_may_finish.wait()
        return RESPONSES

    source = Source("owid", fetch_when_released)
    extracted_snapshots = []

    def extract_in_thread() -> None:
        extracted_snapshots.append(extractor.extract(source))

    first_thread = threading.Thread(target=extract_in_thread)
    second_thread = threading.Thread(target=extract_in_thread)
    first_thread.start()
    fetch_started.wait()
    second_thread.start()
    # Let the second thread reach the lock before the fetch finishes.
    threading.Event().wait(THREAD_SETTLE_SECONDS)
    fetch_may_finish.set()
    first_thread.join()
    second_thread.join()

    assert fetch_count == 1
    assert extracted_snapshots[0] == extracted_snapshots[1]


# --------------------------------------------------------------------
# Several sources
# --------------------------------------------------------------------


def test_extract_all_returns_snapshots_by_source_in_order(
    extractor: Extractor,
) -> None:
    sources = [
        Source("owid", FakeSourceFetcher()),
        Source("eurostat", FakeSourceFetcher()),
    ]

    snapshots = extractor.extract_all(sources)

    assert list(snapshots) == ["owid", "eurostat"]
    assert snapshots["eurostat"].source_name == "eurostat"


def test_extract_all_stops_at_unavailable_source(
    extractor: Extractor,
) -> None:
    last_source_fetcher = FakeSourceFetcher()
    sources = [
        Source("owid", FakeSourceFetcher()),
        Source("eurostat", FakeSourceFetcher(FETCH_FAILURE)),
        Source("worldbank", last_source_fetcher),
    ]

    with pytest.raises(SourceUnavailableError, match="eurostat"):
        extractor.extract_all(sources)

    assert last_source_fetcher.call_count == 0


# --------------------------------------------------------------------
# Pipeline sources
# --------------------------------------------------------------------


def test_pipeline_sources_in_extraction_order() -> None:
    sources = create_sources(current_year=2026)

    assert [source.name for source in sources] == [
        "owid",
        "eurostat",
        "worldbank",
    ]


def test_world_bank_source_requests_first_year_to_current_year() -> None:
    requested_urls: list[str] = []

    def serve_single_pages(url: str) -> bytes:
        requested_urls.append(url)
        return json.dumps([{"pages": 1}, []]).encode()

    [world_bank_source] = [
        source
        for source in create_sources(current_year=2026)
        if source.name == "worldbank"
    ]
    world_bank_source.responses_fetcher(serve_single_pages)

    requested_year_ranges = {
        dict(parse_qsl(urlsplit(url).query))["date"] for url in requested_urls
    }
    assert requested_year_ranges == {f"{worldbank.FIRST_YEAR}:2026"}
    assert len(requested_urls) == len(worldbank.PIPELINE_INDICATORS)


def test_owid_source_returns_the_energy_csv_alone() -> None:
    energy_csv = (
        ",".join(owid.REQUIRED_COLUMNS)
        + "\n"
        + ",".join(["Austria", "2008", "AUT"] + ["1.0"] * len(owid.COLUMNS))
        + "\n"
    ).encode()
    [owid_source] = [
        source
        for source in create_sources(current_year=2026)
        if source.name == "owid"
    ]

    owid_responses = owid_source.responses_fetcher(lambda url: energy_csv)

    assert owid_responses == (
        SourceResponse(owid.RESOURCE_CODE, owid.ENERGY_CSV_URL, energy_csv),
    )


def test_eurostat_source_requests_pipeline_datasets_full_range() -> None:
    sdmx_csv = (
        b"geo: Geo,TIME_PERIOD: Time,OBS_VALUE: Value\nAT: Austria,2008,1.0\n"
    )
    requested_urls: list[str] = []

    def fetch_and_record(url: str) -> bytes:
        requested_urls.append(url)
        return sdmx_csv

    [eurostat_source] = [
        source
        for source in create_sources(current_year=2026)
        if source.name == "eurostat"
    ]
    dataset_responses = eurostat_source.responses_fetcher(fetch_and_record)

    assert requested_urls == [
        dataset.create_request_url() for dataset in eurostat.PIPELINE_DATASETS
    ]
    assert [response.resource_code for response in dataset_responses] == [
        dataset.code for dataset in eurostat.PIPELINE_DATASETS
    ]
