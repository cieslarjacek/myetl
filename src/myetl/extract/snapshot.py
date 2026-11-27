"""Snapshot of a source: its value and its file format.

A snapshot holds every response of one successful fetch of a source.
Its file is a zip archive: the response bodies plus ``manifest.json``
(fetch time, and the resource code, URL, size and SHA-256 of each
response). Encoding and decoding are pure; where the file is stored
is decided by :mod:`myetl.extract.cache`.
"""

__all__ = [
    "MissingResourceError",
    "Snapshot",
    "SnapshotFormatError",
    "decode_snapshot",
    "encode_snapshot",
    "validate_source_name",
]

import hashlib
import io
import json
import re
import zipfile
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from myetl.sources.response_validation import SourceResponse

#: Version of the snapshot file layout, stored in every manifest.
_FORMAT_VERSION: Final[int] = 1

_MANIFEST_NAME: Final[str] = "manifest.json"

#: Source names become file names, so they are kept to a safe alphabet:
#: lowercase letter followed by lowercase letters, digits and
#: underscores.
_SOURCE_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(r"[a-z][a-z0-9_]*")

#: Errors that mean a snapshot file is damaged: a broken archive or
#: compressed stream, a missing member, invalid JSON or values.
_DAMAGED_SNAPSHOT_ERRORS: Final = (
    zipfile.BadZipFile,
    zlib.error,
    KeyError,
    ValueError,
)


class SnapshotFormatError(ValueError):
    """Snapshot file content is not a valid snapshot.

    Attributes:
        problem: What was expected and what was found.
    """

    def __init__(self, problem: str) -> None:
        """Store the problem.

        Args:
            problem: What was expected and what was found.
        """
        super().__init__(problem)
        self.problem = problem


class MissingResourceError(LookupError):
    """A snapshot has no response for a requested resource.

    Typically a stale snapshot fetched before the resource was added to
    the source's table.

    Attributes:
        source_name: Source of the snapshot, e.g. ``eurostat``.
        resource_code: The resource looked for, e.g. ``nrg_ind_ren``.
    """

    def __init__(self, source_name: str, resource_code: str) -> None:
        """Store the source name and the resource code.

        Args:
            source_name: Source of the snapshot.
            resource_code: The resource looked for.
        """
        super().__init__(source_name, resource_code)
        self.source_name = source_name
        self.resource_code = resource_code

    def __str__(self) -> str:
        """Return the message naming the source and the resource."""
        return (
            f"{self.source_name} snapshot has no response for "
            f"{self.resource_code!r}"
        )


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Every response of one successful fetch of a source.

    Attributes:
        source_name: Source the responses come from, e.g. ``owid``.
        fetched_at: When the fetch started, timezone-aware.
        responses: The responses, byte for byte, in request order.

    Raises:
        ValueError: If the source name is not lowercase letters, digits
            and underscores, ``fetched_at`` has no time zone, or there
            are no responses.
    """

    source_name: str
    fetched_at: datetime
    responses: tuple[SourceResponse, ...]

    def __post_init__(self) -> None:
        """Reject values that would make an unusable snapshot."""
        validate_source_name(self.source_name)
        if self.fetched_at.tzinfo is None:
            raise ValueError(f"fetched_at has no time zone: {self.fetched_at}")
        if not self.responses:
            raise ValueError(f"snapshot of {self.source_name} is empty")

    @property
    def content_size_bytes(self) -> int:
        """Total size of the response bodies, in bytes."""
        return sum(len(response.content) for response in self.responses)

    def select_responses(
        self, resource_code: str
    ) -> tuple[SourceResponse, ...]:
        """Return the responses for one resource, in request order.

        Args:
            resource_code: The resource, e.g. ``nrg_ind_ren``, or
                ``SP.POP.TOTL`` for all pages of that indicator.

        Returns:
            Every response with that resource code; at least one.

        Raises:
            MissingResourceError: If the snapshot has none.
        """
        matching_responses = tuple(
            response
            for response in self.responses
            if response.resource_code == resource_code
        )
        if not matching_responses:
            raise MissingResourceError(self.source_name, resource_code)
        return matching_responses


def validate_source_name(
    source_name: str, expected_pattern: re.Pattern[str] = _SOURCE_NAME_PATTERN
) -> None:
    """Raise if a source name is unsafe as a file name.

    Args:
        source_name: Name to check.
        expected_pattern: Pattern to match.

    Raises:
        ValueError: If the name does not match the pattern.
    """
    if not expected_pattern.fullmatch(source_name):
        raise ValueError(f"invalid source name: {source_name!r}")


# ----------------------------------------------------------------------
# Snapshot file format
# ----------------------------------------------------------------------


def encode_snapshot(snapshot: Snapshot) -> bytes:
    """Serialize a snapshot to the bytes of a snapshot file.

    The output depends only on the snapshot: member timestamps are the
    fetch time, not the time of writing.

    Args:
        snapshot: Snapshot to serialize.

    Returns:
        A zip archive with the response bodies and the manifest.
    """
    member_time = snapshot.fetched_at.astimezone(UTC).timetuple()[:6]
    response_entries: list[dict[str, object]] = []
    archive_buffer = io.BytesIO()

    with zipfile.ZipFile(archive_buffer, "w") as archive:
        for response_number, response in enumerate(
            snapshot.responses, start=1
        ):
            member_name = f"responses/{response_number:04d}"
            member_info = _create_member_info(member_name, member_time)
            archive.writestr(member_info, response.content)
            response_entries.append(_describe_response(member_name, response))
        manifest = {
            "format_version": _FORMAT_VERSION,
            "source_name": snapshot.source_name,
            "fetched_at": snapshot.fetched_at.isoformat(),
            "responses": response_entries,
        }
        manifest_json = json.dumps(manifest, indent=2).encode("utf-8")
        manifest_info = _create_member_info(_MANIFEST_NAME, member_time)
        archive.writestr(manifest_info, manifest_json)

    return archive_buffer.getvalue()


def _create_member_info(
    member_name: str, member_time: tuple[int, int, int, int, int, int]
) -> zipfile.ZipInfo:
    """Describe one compressed archive member with a fixed timestamp.

    Args:
        member_name: Path of the member inside the archive.
        member_time: Timestamp as year, month, day, hour, minute,
            second.

    Returns:
        Member information for :meth:`zipfile.ZipFile.writestr`.
    """
    member_info = zipfile.ZipInfo(member_name, date_time=member_time)
    member_info.compress_type = zipfile.ZIP_DEFLATED
    return member_info


def _describe_response(
    member_name: str, response: SourceResponse
) -> dict[str, object]:
    """Return the manifest entry of one response.

    Args:
        member_name: Path of the response body inside the archive.
        response: The response.

    Returns:
        Resource code, member name, URL, size and SHA-256 of the body.
    """
    return {
        "resource_code": response.resource_code,
        "member_name": member_name,
        "url": response.url,
        "bytes": len(response.content),
        "sha256": hashlib.sha256(response.content).hexdigest(),
    }


def decode_snapshot(snapshot_file_content: bytes) -> Snapshot:
    """Parse the bytes of a snapshot file, verifying every checksum.

    Args:
        snapshot_file_content: Bytes written by :func:`encode_snapshot`.

    Returns:
        The snapshot.

    Raises:
        SnapshotFormatError: If the content is not a zip archive, the
            manifest is missing or malformed, a response is missing, or
            a checksum does not match.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(snapshot_file_content)) as archive:
            manifest = json.loads(archive.read(_MANIFEST_NAME))
            return _parse_manifest(archive, manifest)
    except SnapshotFormatError:
        raise
    except _DAMAGED_SNAPSHOT_ERRORS as error:
        problem = f"damaged snapshot ({type(error).__name__}: {error})"
        raise SnapshotFormatError(problem) from error


def _parse_manifest(archive: zipfile.ZipFile, manifest: object) -> Snapshot:
    """Build a snapshot from a decoded manifest and its archive.

    Args:
        archive: Snapshot archive open for reading.
        manifest: Decoded ``manifest.json``.

    Returns:
        The snapshot.

    Raises:
        SnapshotFormatError: If the manifest has an unexpected shape or
            version, or a response fails its checks.
        KeyError: If a response member is missing from the archive.
        ValueError: If ``fetched_at`` is not an ISO 8601 time with a
            time zone.
    """
    match manifest:
        case {
            "format_version": int(format_version),
            "source_name": str(source_name),
            "fetched_at": str(fetched_at_text),
            "responses": list(response_entries),
        } if format_version == _FORMAT_VERSION:
            responses = tuple(
                _read_response(archive, response_entry)
                for response_entry in response_entries
            )
            fetched_at = datetime.fromisoformat(fetched_at_text)
            return Snapshot(source_name, fetched_at, responses)
    raise SnapshotFormatError(f"unexpected manifest: {manifest!r:.200}")


def _read_response(
    archive: zipfile.ZipFile, response_entry: object
) -> SourceResponse:
    """Read one response body and verify it against its manifest entry.

    Args:
        archive: Snapshot archive open for reading.
        response_entry: The response's entry in the manifest.

    Returns:
        The response.

    Raises:
        SnapshotFormatError: If the entry has an unexpected shape or
            the body does not match its checksum.
        KeyError: If the body is missing from the archive.
    """
    match response_entry:
        case {
            "resource_code": str(resource_code),
            "member_name": str(member_name),
            "url": str(url),
            "sha256": str(expected_sha256),
        }:
            response_content = archive.read(member_name)
            found_sha256 = hashlib.sha256(response_content).hexdigest()
            if found_sha256 != expected_sha256:
                problem = (
                    f"checksum mismatch in {member_name}: expected "
                    f"{expected_sha256}, found {found_sha256}"
                )
                raise SnapshotFormatError(problem)
            return SourceResponse(resource_code, url, response_content)
    raise SnapshotFormatError(f"unexpected response entry: {response_entry!r}")
