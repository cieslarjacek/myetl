"""Extract: a usable raw snapshot of every source.

1. :mod:`~myetl.extract.snapshot`: the snapshot value and its zip file
   format.
2. :mod:`~myetl.extract.cache`: the local cache that stores snapshots
   and judges their age.
3. :mod:`~myetl.extract.extractor`: the sources the pipeline fetches
   and :class:`Extractor`, which reuses a fresh cached snapshot or
   fetches a new one.

The public API of these modules is re-exported here, so callers import
from :mod:`myetl.extract`.
"""

__all__ = [
    "CACHE_DIRECTORY_VARIABLE",
    "DEFAULT_CACHE_DIRECTORY",
    "DEFAULT_CACHE_POLICY",
    "CachePolicy",
    "Clock",
    "Extractor",
    "Freshness",
    "MissingResourceError",
    "Snapshot",
    "SnapshotCache",
    "SnapshotFormatError",
    "Source",
    "SourceFetcher",
    "SourceUnavailableError",
    "cache_directory_from_environment",
    "classify_age",
    "create_sources",
    "decode_snapshot",
    "describe_age",
    "encode_snapshot",
    "utc_now",
    "validate_source_name",
]

from myetl.extract.cache import (
    CACHE_DIRECTORY_VARIABLE,
    DEFAULT_CACHE_DIRECTORY,
    DEFAULT_CACHE_POLICY,
    CachePolicy,
    Clock,
    Freshness,
    SnapshotCache,
    cache_directory_from_environment,
    classify_age,
    describe_age,
    utc_now,
)
from myetl.extract.extractor import (
    Extractor,
    Source,
    SourceFetcher,
    SourceUnavailableError,
    create_sources,
)
from myetl.extract.snapshot import (
    MissingResourceError,
    Snapshot,
    SnapshotFormatError,
    decode_snapshot,
    encode_snapshot,
    validate_source_name,
)
