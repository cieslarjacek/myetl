"""Unit tests for ``myetl.extract.snapshot``."""

import hashlib
import io
import json
import pickle
import zipfile
from datetime import UTC, datetime

import pytest

from myetl.extract.snapshot import (
    MissingResourceError,
    Snapshot,
    SnapshotFormatError,
    decode_snapshot,
    encode_snapshot,
)
from myetl.sources.response_validation import SourceResponse

FETCHED_AT = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)

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
