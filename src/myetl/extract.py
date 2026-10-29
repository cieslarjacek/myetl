"""Extract: a raw snapshot of every source through the local cache.

For each source, :class:`Extractor`:

1. Waits for the source's cache lock, so concurrent runs fetch once.
2. Returns the cached snapshot if it is fresh.
3. Otherwise fetches the whole source and caches the new snapshot.
4. If that fetch fails, falls back to the cached snapshot while it is
   within the stale limit, with a warning giving its age.

A source is fetched all or nothing: if one of its requests fails, the
previous snapshot is used as a whole, never mixed with new responses.
The age limits come from the cache's :class:`~myetl.cache.CachePolicy`.
"""

__all__ = [
    "Extractor",
    "Source",
    "SourceFetcher",
    "SourceUnavailableError",
    "create_sources",
]

import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from myetl.cache import (
    Clock,
    Freshness,
    Snapshot,
    SnapshotCache,
    classify_age,
    describe_age,
    utc_now,
)
from myetl.http_client import Fetcher, FetchError
from myetl.scope import YearRange
from myetl.sources import eurostat, owid, worldbank
from myetl.sources.response_validation import (
    ResponseFormatError,
    SourceResponse,
)

#: Downloads every response of one source.
type SourceFetcher = Callable[[Fetcher], tuple[SourceResponse, ...]]

#: Failures a cached snapshot may stand in for: a failed download or a
#: response in an unexpected format. Anything else is a bug and
#: propagates.
_FETCH_FAILURES: Final = (FetchError, ResponseFormatError)

_logger = logging.getLogger(__name__)


class SourceUnavailableError(RuntimeError):
    """A source could not be fetched and no usable snapshot exists.

    Attributes:
        source_name: The source, e.g. ``eurostat``.
        reason: Why the fetch failed and why no snapshot could stand in.
    """

    def __init__(self, source_name: str, reason: str) -> None:
        """Store the source name and the reason.

        Args:
            source_name: The source.
            reason: Why the fetch failed and why no snapshot could
                stand in.
        """
        super().__init__(source_name, reason)
        self.source_name = source_name
        self.reason = reason

    def __str__(self) -> str:
        """Return the message, e.g. ``"owid unavailable: ..."``."""
        return f"{self.source_name} unavailable: {self.reason}"


@dataclass(frozen=True, slots=True)
class Source:
    """A data source as the extractor sees it.

    Attributes:
        name: Source name, also its cache file name, e.g. ``owid``.
        responses_fetcher: Downloads every response of the source.
    """

    name: str
    responses_fetcher: SourceFetcher


# TODO: Think how to get rid of external dependencies and nested
# functions. Maybe convert it into a class.
# ----------------------------------------------------------------------
def create_sources(current_year: int) -> tuple[Source, ...]:
    """Return the pipeline's sources in extraction order.

    Args:
        current_year: Last year requested from the World Bank, whose
            range starts at :data:`myetl.sources.worldbank.FIRST_YEAR`.
            The other sources are requested in full.

    Returns:
        OWID, Eurostat and the World Bank.
    """
    world_bank_years = YearRange(worldbank.FIRST_YEAR, current_year)

    def fetch_eurostat(url_fetcher: Fetcher) -> tuple[SourceResponse, ...]:
        return eurostat.fetch_datasets(url_fetcher, eurostat.PIPELINE_DATASETS)

    def fetch_world_bank(url_fetcher: Fetcher) -> tuple[SourceResponse, ...]:
        return worldbank.fetch_indicators(
            url_fetcher, worldbank.PIPELINE_INDICATORS, world_bank_years
        )

    return (
        Source("owid", _fetch_owid),
        Source("eurostat", fetch_eurostat),
        Source("worldbank", fetch_world_bank),
    )


def _fetch_owid(url_fetcher: Fetcher) -> tuple[SourceResponse, ...]:
    """Download the OWID energy CSV as a one-response source.

    Args:
        url_fetcher: A callable that downloads a URL.

    Returns:
        The energy CSV response, alone in a tuple.
    """
    return (owid.fetch_energy_csv(url_fetcher),)


# ----------------------------------------------------------------------


class Extractor:
    """Extracts sources through a snapshot cache.

    Reading or refreshing the cache and returning the snapshot happen
    in one call: a read-through cache both queries and changes state
    (an accepted exception to command-query separation).
    """

    def __init__(
        self,
        cache: SnapshotCache,
        url_fetcher: Fetcher,
        clock: Clock = utc_now,
    ) -> None:
        """Create an extractor.

        Args:
            cache: Where snapshots are kept, with their age limits.
            url_fetcher: A callable that downloads a URL.
            clock: Returns the current time when called.
        """
        self._cache = cache
        self._url_fetcher = url_fetcher
        self._clock = clock

    def extract(self, source: Source) -> Snapshot:
        """Return a usable snapshot of one source.

        Args:
            source: Source to extract.

        Returns:
            The fresh cached snapshot, a newly fetched one or the cached
            one within the stale limit (if the fetch fails).

        Raises:
            SourceUnavailableError: If the fetch fails and there is no
                cached snapshot within the stale limit.
        """
        with self._cache.lock(source.name):
            cached_snapshot = self._cache.read(source.name)

            if cached_snapshot is not None and self._is_fresh(cached_snapshot):
                _logger.info(
                    "%s: using cached snapshot, %s old",
                    source.name,
                    describe_age(self._clock() - cached_snapshot.fetched_at),
                )
                return cached_snapshot

            return self._refresh(source, cached_snapshot)

    def extract_all(self, sources: Iterable[Source]) -> Mapping[str, Snapshot]:
        """Return a usable snapshot of every source, one after another.

        Args:
            sources: Sources to extract, e.g. from
                :func:`create_sources`.

        Returns:
            Source name -> snapshot, in extraction order.

        Raises:
            SourceUnavailableError: At the first source that is
                unavailable; the sources after it are not extracted.
        """
        return MappingProxyType(
            {source.name: self.extract(source) for source in sources}
        )

    def _is_fresh(self, snapshot: Snapshot) -> bool:
        """Return whether a snapshot is young enough to skip fetching.

        Args:
            snapshot: Cached snapshot.

        Returns:
            ``True`` if the snapshot is fresh now.
        """
        freshness = classify_age(
            snapshot.fetched_at, self._clock(), self._cache.policy
        )
        return freshness is Freshness.FRESH

    def _refresh(
        self, source: Source, cached_snapshot: Snapshot | None
    ) -> Snapshot:
        """Fetch a source and cache it, or fall back on failure.

        Args:
            source: Source to fetch.
            cached_snapshot: The cached snapshot, if any, to fall back
                on.

        Returns:
            The new snapshot or the cached one if the fetch failed.

        Raises:
            SourceUnavailableError: If the fetch fails and the cached
                snapshot cannot stand in.
        """
        fetch_started_at = self._clock()
        _logger.info("%s: fetching", source.name)
        try:
            responses = source.responses_fetcher(self._url_fetcher)
        except _FETCH_FAILURES as error:
            return self._fall_back(source.name, cached_snapshot, error)

        new_snapshot = Snapshot(source.name, fetch_started_at, responses)
        self._cache.write(new_snapshot)
        _logger.info(
            "%s: cached %d responses, %d bytes",
            source.name,
            len(new_snapshot.responses),
            new_snapshot.content_size_bytes,
        )

        return new_snapshot

    def _fall_back(
        self,
        source_name: str,
        cached_snapshot: Snapshot | None,
        fetch_error: Exception,
    ) -> Snapshot:
        """Return the cached snapshot after a failed fetch, if usable.

        Args:
            source_name: The source whose fetch failed.
            cached_snapshot: The cached snapshot, if any.
            fetch_error: Why the fetch failed.

        Returns:
            The cached snapshot, if it is within the stale limit.

        Raises:
            SourceUnavailableError: If there is no cached snapshot, or
                it is past the stale limit.
        """
        fetch_failure = f"{type(fetch_error).__name__}: {fetch_error}"
        if cached_snapshot is None:
            reason = f"{fetch_failure}; no cached snapshot"
            raise SourceUnavailableError(source_name, reason) from fetch_error

        now = self._clock()
        policy = self._cache.policy
        freshness = classify_age(cached_snapshot.fetched_at, now, policy)
        if freshness is Freshness.EXPIRED:
            reason = (
                f"{fetch_failure}; cached snapshot from "
                f"{cached_snapshot.fetched_at.isoformat(timespec='minutes')}"
                f" is past the {describe_age(policy.max_stale_age)} limit"
            )
            raise SourceUnavailableError(source_name, reason) from fetch_error

        _logger.warning(
            "%s: %s; using cached snapshot, %s old",
            source_name,
            fetch_failure,
            describe_age(now - cached_snapshot.fetched_at),
        )

        return cached_snapshot
