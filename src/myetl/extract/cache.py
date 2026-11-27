"""Local cache of raw source snapshots.

Each source has one snapshot file, ``<directory>/<source>.zip``, in the
format of :mod:`myetl.extract.snapshot`. A new snapshot replaces the
old one atomically, so readers see either the old or the new file,
never a partial one.

How old a snapshot may be is decided by :class:`CachePolicy`; the
pure functions here decide, :class:`SnapshotCache` reads and writes.
"""

__all__ = [
    "CACHE_DIRECTORY_VARIABLE",
    "DEFAULT_CACHE_DIRECTORY",
    "DEFAULT_CACHE_POLICY",
    "CachePolicy",
    "Clock",
    "Freshness",
    "SnapshotCache",
    "cache_directory_from_environment",
    "classify_age",
    "describe_age",
    "utc_now",
]

import enum
import fcntl
import logging
import os
import tempfile
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

from myetl.extract.snapshot import (
    Snapshot,
    SnapshotFormatError,
    decode_snapshot,
    encode_snapshot,
    validate_source_name,
)

#: Callable that returns the current time (timezone-aware) like a clock.
type Clock = Callable[[], datetime]

#: Environment variable that overrides the cache directory.
CACHE_DIRECTORY_VARIABLE: Final[str] = "MYETL_CACHE_DIR"

#: Cache directory when the variable is unset or empty, relative to the
#: working directory (ignored by git as ``/data/``).
DEFAULT_CACHE_DIRECTORY: Final[Path] = Path("data") / "cache"

_SNAPSHOT_SUFFIX: Final[str] = ".zip"
_LOCK_SUFFIX: Final[str] = ".lock"

_logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    """Return the current time in UTC - production :data:`Clock`."""
    return datetime.now(UTC)


class Freshness(enum.Enum):
    """How usable a snapshot is at a given time."""

    #: Young enough to use without fetching.
    FRESH = "fresh"
    #: Too old to use by choice, but usable if a fetch fails.
    STALE_USABLE = "stale but usable"
    #: Too old to use at all.
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class CachePolicy:
    """How old a snapshot may be.

    Attributes:
        max_fresh_age: Oldest snapshot used without fetching.
        max_stale_age: Oldest snapshot used when a fetch fails.

    Raises:
        ValueError: If an age is not positive, or the stale limit is
            below the fresh limit.
    """

    max_fresh_age: timedelta = timedelta(hours=24)
    max_stale_age: timedelta = timedelta(days=90)

    def __post_init__(self) -> None:
        """Reject limits that are not positive or out of order."""
        if self.max_fresh_age <= timedelta(0):
            raise ValueError(f"max_fresh_age must be positive: {self}")
        if self.max_stale_age < self.max_fresh_age:
            raise ValueError(f"max_stale_age below max_fresh_age: {self}")


DEFAULT_CACHE_POLICY: Final[CachePolicy] = CachePolicy()


# ----------------------------------------------------------------------
# Pure logic
# ----------------------------------------------------------------------


# TODO: Think about wrapping it into a class.
def classify_age(
    fetched_at: datetime, now: datetime, policy: CachePolicy
) -> Freshness:
    """Classify a snapshot by its age.

    Both limits are inclusive: a snapshot exactly 24h old is fresh. A
    snapshot from the future (the clock was set back) is expired, so
    it is fetched again rather than trusted.

    Args:
        fetched_at: When the snapshot was fetched.
        now: Current time.
        policy: Age limits.

    Returns:
        The freshness of the snapshot at ``now``.
    """
    snapshot_age = now - fetched_at
    if snapshot_age < timedelta(0):
        return Freshness.EXPIRED
    if snapshot_age <= policy.max_fresh_age:
        return Freshness.FRESH
    if snapshot_age <= policy.max_stale_age:
        return Freshness.STALE_USABLE

    return Freshness.EXPIRED


def describe_age(snapshot_age: timedelta) -> str:
    """Describe an age for log messages, to the minute.

    Args:
        snapshot_age: Age to describe.

    Returns:
        Days, hours and minutes, e.g. ``"3 d 4 h 5 min"``.
    """
    days, part_of_day = divmod(snapshot_age, timedelta(days=1))
    hours, part_of_hour = divmod(part_of_day, timedelta(hours=1))
    minutes = part_of_hour // timedelta(minutes=1)
    return f"{days} d {hours} h {minutes} min"


def cache_directory_from_environment(environment: Mapping[str, str]) -> Path:
    """Return the cache directory configured in ``environment``.

    Args:
        environment: Environment variables, e.g. ``os.environ``.

    Returns:
        The value of :data:`CACHE_DIRECTORY_VARIABLE`, or
        :data:`DEFAULT_CACHE_DIRECTORY` if it is unset or empty.
    """
    configured_directory = environment.get(CACHE_DIRECTORY_VARIABLE, "")
    if not configured_directory:
        return DEFAULT_CACHE_DIRECTORY
    return Path(configured_directory)


# ----------------------------------------------------------------------
# File system
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SnapshotCache:
    """Snapshot files in one directory, one per source.

    Attributes:
        directory: Where snapshot and lock files live. Created on the
            first lock or write.
        policy: How old a snapshot may be.
    """

    directory: Path
    policy: CachePolicy = DEFAULT_CACHE_POLICY

    @contextmanager
    def lock(self, source_name: str) -> Generator[None]:
        """Hold the exclusive lock of a source; wait while it is held.

        The lock is an ``flock`` on ``<source>.lock``. Each call opens
        the file anew, so it excludes other processes and other threads
        alike. The lock file is kept for the next run.

        Args:
            source_name: Source to lock.

        Yields:
            Nothing; the lock is held inside the ``with`` block.

        Raises:
            ValueError: If the source name is unsafe as a file name.
        """
        lock_path = self._source_path(source_name, _LOCK_SUFFIX)
        self.directory.mkdir(parents=True, exist_ok=True)

        # Mode "a" creates the file if needed and never truncates it.
        with lock_path.open("a") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)

    def read(self, source_name: str) -> Snapshot | None:
        """Return the cached snapshot of a source, if there is one.

        An unreadable snapshot file is logged as a warning and treated
        as missing, so the source is fetched again.

        Args:
            source_name: Source to read.

        Returns:
            The snapshot or ``None`` if there is no usable file.

        Raises:
            ValueError: If the source name is unsafe as a file name.
        """
        snapshot_path = self._source_path(source_name, _SNAPSHOT_SUFFIX)
        try:
            snapshot_file_content = snapshot_path.read_bytes()
        except FileNotFoundError:
            return None

        try:
            snapshot = decode_snapshot(snapshot_file_content)
        except SnapshotFormatError as error:
            _logger.warning("ignoring %s: %s", snapshot_path, error)
            return None
        if snapshot.source_name != source_name:
            _logger.warning(
                "ignoring %s: it holds source %r",
                snapshot_path,
                snapshot.source_name,
            )
            return None

        return snapshot

    def write(self, snapshot: Snapshot) -> None:
        """Replace the snapshot file of a source atomically.

        The file is written under a temporary name in the same
        directory, flushed to disk and then renamed over the old one.
        The temporary file is removed if anything fails.

        Args:
            snapshot: Snapshot to store.
        """
        snapshot_file_content = encode_snapshot(snapshot)
        snapshot_path = self._source_path(
            snapshot.source_name, _SNAPSHOT_SUFFIX
        )
        self.directory.mkdir(parents=True, exist_ok=True)
        file_descriptor, temporary_name = tempfile.mkstemp(
            dir=self.directory, prefix=f".{snapshot.source_name}."
        )
        temporary_path = Path(temporary_name)

        try:
            with os.fdopen(file_descriptor, "wb") as temporary_file:
                temporary_file.write(snapshot_file_content)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, snapshot_path)
        finally:
            # A no-op after a successful rename.
            temporary_path.unlink(missing_ok=True)

    def _source_path(self, source_name: str, suffix: str) -> Path:
        """Return the path of a source's snapshot or lock file.

        Args:
            source_name: Source name.
            suffix: File suffix, e.g. ``".zip"``.

        Returns:
            ``<directory>/<source><suffix>``.

        Raises:
            ValueError: If the source name is unsafe as a file name.
        """
        validate_source_name(source_name)
        return self.directory / f"{source_name}{suffix}"
