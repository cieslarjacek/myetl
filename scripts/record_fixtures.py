"""Record test fixtures from the live data sources.

Fetches a small time slice of every source, as raw as the pipeline gets
it (all countries and aggregates, all columns), and writes it under
``tests/fixtures/`` with a manifest of URLs and SHA-256 checksums:

* OWID: all rows for 2008-2009, all columns.
* Eurostat: annual 2008-2009, semi-annual 2008-S1..2009-S1, monthly
  2017-01..2018-06 (the first months with every monthly series). The
  last year of the two sub-annual datasets is incomplete on purpose,
  to test the aggregation rules.
* World Bank: all economies, 2008-2009, with a small page size so the
  response spans several pages.

Each source checks its responses as the pipeline does (see
:mod:`myetl.sources`). The script also checks that every country in
scope can be matched in each source. Nothing is written unless every
source succeeds. Run it with ``make fixtures`` and commit the result.
"""

__all__ = ["main", "record_live_sources"]

import csv
import hashlib
import io
import json
import logging
import shutil
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Final, TextIO

from myetl import __version__
from myetl.http_client import (
    Fetcher,
    FetchError,
    HttpFetcher,
    create_http_client,
)
from myetl.scope import EUROSTAT_NAMES, ISO3_CODES, YearRange
from myetl.sources import eurostat, owid, worldbank
from myetl.sources.response_validation import ResponseFormatError

type JsonObject = dict[str, object]


class RecordingError(RuntimeError):
    """A source returned something other than the expected response."""


@dataclass(frozen=True, slots=True)
class Fixture:
    """One recorded response, held in memory until all sources succeed.

    Attributes:
        path: Destination, relative to the fixtures directory.
        url: URL the content was fetched from.
        content: Bytes to write.
    """

    path: Path
    url: str
    content: bytes


@dataclass(frozen=True, slots=True)
class EurostatSlice:
    """A Eurostat dataset limited to a time range.

    Attributes:
        dataset: Dataset to request.
        period_range: First and last period (inclusive).
    """

    dataset: eurostat.Dataset
    period_range: eurostat.PeriodRange


@dataclass(frozen=True, slots=True)
class RecordingResult:
    """Outcome of recording every source.

    Attributes:
        fixtures: Fixtures of the sources that succeeded.
        failed_sources: Names of the sources that failed.
    """

    fixtures: tuple[Fixture, ...]
    failed_sources: tuple[str, ...]


type Recorder = Callable[[Fetcher], list[Fixture]]

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------

#: Years in the fixture slice.
SLICE_YEARS: Final[YearRange] = YearRange(2008, 2009)

#: Eurostat time ranges. The sub-annual ones end mid-year on purpose.
#: The monthly one starts in 2017, when the renewable and combustible
#: fuel series begin.
EUROSTAT_SLICES: Final[tuple[EurostatSlice, ...]] = (
    EurostatSlice(eurostat.RES_SHARE, ("2008", "2009")),
    EurostatSlice(eurostat.HOUSEHOLD_PRICE, ("2008-S1", "2009-S1")),
    EurostatSlice(eurostat.MONTHLY_GENERATION, ("2017-01", "2018-06")),
)

#: Small page size, so the World Bank slice spans several pages.
WORLD_BANK_URL_SETTINGS: Final[worldbank.RequestUrlSettings] = (
    worldbank.RequestUrlSettings(page_size=200)
)

#: Output locations (this file lives in ``<repo>/scripts/``).
FIXTURES_DIR: Final[Path] = (
    Path(__file__).resolve().parent.parent / "tests" / "fixtures"
)
MANIFEST_NAME: Final[str] = "manifest.json"
OWID_FIXTURE: Final[Path] = Path("owid") / "owid-energy-data.csv"

#: Tells the source servers that a recording, not the pipeline, runs.
USER_AGENT: Final[str] = (
    f"myetl-fixture-recorder/{__version__}"
    " (+https://github.com/cieslarjacek/myetl)"
)

#: Console log format: the HTTP client logs each request and retry.
LOG_FORMAT: Final[str] = "%(levelname)s %(message)s"

#: Logger of the httpx library. At INFO it repeats every request that
#: :class:`~myetl.http_client.HttpFetcher` already logs.
HTTPX_LOGGER: Final[str] = "httpx"

#: Errors that fail one source without stopping the others: failed
#: downloads (FetchError), responses in the wrong format
#: (ResponseFormatError), other malformed content (ValueError,
#: KeyError) and failed recorder checks (RecordingError).
SOURCE_ERRORS: Final = (
    FetchError,
    ResponseFormatError,
    ValueError,
    KeyError,
    RecordingError,
)


# ----------------------------------------------------------------------
# Edges: console
# ----------------------------------------------------------------------


def report(status: str, message: str, stream: TextIO | None = None) -> None:
    """Print one console line: a status word, then a message.

    Args:
        status: Short status word, e.g. ``fetched``.
        message: Rest of the line.
        stream: Output stream; ``None`` means standard output.
    """
    print(f"{status} {message}", file=stream)


# ----------------------------------------------------------------------
# Serialization and checks (pure)
# ----------------------------------------------------------------------


def to_csv_bytes(
    header: Sequence[str], rows: Iterable[Sequence[str]]
) -> bytes:
    """Serialize CSV rows under a header, with Unix line endings.

    Args:
        header: Column names.
        rows: Data rows.

    Returns:
        UTF-8 encoded CSV.
    """
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def to_json_bytes(document: object) -> bytes:
    """Serialize a JSON document with indentation, for readable diffs.

    Args:
        document: Parsed JSON value.

    Returns:
        Indented JSON, UTF-8 encoded, newline-terminated.
    """
    text = json.dumps(document, indent=2, ensure_ascii=False)
    return (text + "\n").encode("utf-8")


def validate_countries(
    found: Iterable[str], expected: frozenset[str], source: str
) -> None:
    """Raise if a country key in scope is missing from a source.

    Args:
        found: Country keys present in the source.
        expected: Country keys in scope.
        source: Source name for the error message.

    Raises:
        RecordingError: If a country key is missing.
    """
    missing = sorted(expected - set(found))
    if missing:
        raise RecordingError(f"{source} lacks countries: {missing}")


# ----------------------------------------------------------------------
# OWID
# ----------------------------------------------------------------------


def record_owid(url_fetcher: Fetcher) -> list[Fixture]:
    """Download the OWID energy CSV and keep the slice years.

    All countries, regions and columns are kept; rows are copied
    unchanged under the original header.

    Args:
        url_fetcher: A callable that downloads a URL.

    Returns:
        One fixture with the slice rows.

    Raises:
        ResponseFormatError: If the CSV has no data rows or lacks a
            required column.
        RecordingError: If a country in scope is missing.
    """
    response = owid.fetch_energy_csv(url_fetcher)
    csv_text = response.content.decode("utf-8")
    csv_header, *rows = csv.reader(io.StringIO(csv_text, newline=""))
    year_column_index = csv_header.index("year")
    iso_column_index = csv_header.index("iso_code")
    slice_rows = [
        row for row in rows if int(row[year_column_index]) in SLICE_YEARS
    ]

    validate_countries(
        (row[iso_column_index] for row in slice_rows), ISO3_CODES, "OWID"
    )
    slice_csv = to_csv_bytes(csv_header, slice_rows)

    return [Fixture(OWID_FIXTURE, response.url, slice_csv)]


# ----------------------------------------------------------------------
# Eurostat
# ----------------------------------------------------------------------


def record_eurostat(url_fetcher: Fetcher) -> list[Fixture]:
    """Fetch every Eurostat dataset for its slice time range.

    A country missing from one dataset is reported but allowed (it
    becomes empty values downstream). A country label found in none of
    the datasets means its Eurostat name is wrong, and fails.

    Args:
        url_fetcher: A callable that downloads a URL.

    Returns:
        One SDMX-CSV fixture per dataset, stored byte for byte.

    Raises:
        RecordingError: If a response is malformed or a country label
            matches no dataset.
    """
    fixtures: list[Fixture] = []
    labels_seen: set[str] = set()
    for eurostat_slice in EUROSTAT_SLICES:
        fixture, labels = _record_eurostat_slice(url_fetcher, eurostat_slice)
        fixtures.append(fixture)
        labels_seen |= labels
    validate_countries(labels_seen, EUROSTAT_NAMES, "Eurostat")
    return fixtures


def _record_eurostat_slice(
    url_fetcher: Fetcher, eurostat_slice: EurostatSlice
) -> tuple[Fixture, frozenset[str]]:
    """Fetch one Eurostat slice and note countries it has no data for.

    Args:
        url_fetcher: A callable that downloads a URL.
        eurostat_slice: Dataset and time range to request.

    Returns:
        The fixture and the ``geo`` labels found in it.
    """
    dataset_code = eurostat_slice.dataset.code
    response = eurostat_slice.dataset.fetch_csv(
        url_fetcher, eurostat_slice.period_range
    )
    labels_found = geo_labels(response.content)
    absent_labels = sorted(EUROSTAT_NAMES - labels_found)

    if absent_labels:
        report("note", f"{dataset_code} has no data for: {absent_labels}")
    fixture_path = Path("eurostat") / f"{dataset_code}.csv"

    return Fixture(fixture_path, response.url, response.content), labels_found


def geo_labels(csv_content: bytes) -> frozenset[str]:
    """Return the ``geo`` labels of a checked SDMX-CSV response.

    Columns are matched by code: with ``labels=both`` the header reads
    ``"geo: Geopolitical entity (reporting)"``, not ``"geo"``.

    Args:
        csv_content: Response body, already checked by
            :meth:`myetl.sources.eurostat.Dataset.fetch_csv`.

    Returns:
        The label of every ``geo`` value, countries and aggregates.
    """
    csv_header, *rows = csv.reader(io.StringIO(csv_content.decode("utf-8")))
    column_codes = [
        eurostat.get_value_code(column_name) for column_name in csv_header
    ]
    geo_column_index = column_codes.index(eurostat.GEO_COLUMN)

    return frozenset(
        eurostat.get_value_label(row[geo_column_index]) for row in rows
    )


# ----------------------------------------------------------------------
# World Bank
# ----------------------------------------------------------------------


def record_world_bank(url_fetcher: Fetcher) -> list[Fixture]:
    """Fetch every page of every World Bank indicator for the slice.

    Args:
        url_fetcher: A callable that downloads a URL.

    Returns:
        The page fixtures of all indicators.
    """
    return [
        fixture
        for indicator in worldbank.PIPELINE_INDICATORS
        for fixture in _record_indicator(url_fetcher, indicator)
    ]


def _record_indicator(
    url_fetcher: Fetcher, indicator: worldbank.Indicator
) -> list[Fixture]:
    """Fetch every page of one indicator.

    Args:
        url_fetcher: A callable that downloads a URL.
        indicator: Indicator to request.

    Returns:
        One fixture per page: ``worldbank/{code}/page-{n}.json``.

    Raises:
        ResponseFormatError: If a page is not ``[metadata, rows]``.
        RecordingError: If a country in scope is missing, or the slice
            fits on one page (pagination would go untested).
    """
    source_name = f"World Bank {indicator.code}"
    page_responses = indicator.fetch_pages(
        url_fetcher, SLICE_YEARS, WORLD_BANK_URL_SETTINGS
    )
    indicator_folder = Path("worldbank") / indicator.code
    fixtures: list[Fixture] = []
    country_codes_found: set[str] = set()

    for page_number, page_response in enumerate(
        page_responses, start=worldbank.FIRST_PAGE_NUMBER
    ):
        page_rows = worldbank.parse_page(page_response).rows
        country_codes_found.update(
            str(row["countryiso3code"]) for row in page_rows
        )
        indented_json = to_json_bytes(json.loads(page_response.content))
        fixture_path = indicator_folder / f"page-{page_number}.json"
        fixtures.append(
            Fixture(fixture_path, page_response.url, indented_json)
        )

    validate_countries(country_codes_found, ISO3_CODES, source_name)
    if len(fixtures) < 2:
        raise RecordingError(
            f"{source_name} fits on one page; lower page size"
        )

    return fixtures


# ----------------------------------------------------------------------
# Output
# ----------------------------------------------------------------------


def save_fixtures(
    fixtures: Sequence[Fixture], root: Path, recorded_at: datetime
) -> Path:
    """Replace the recorded fixtures under ``root``; add a manifest.

    Only the per-source folders written by this script are cleared
    first (so a dropped page or file does not linger); anything else in
    ``root`` is left alone.

    Args:
        fixtures: Recorded responses from every source.
        root: Fixtures directory.
        recorded_at: Recording time, stored in the manifest.

    Returns:
        Path of the manifest file.
    """
    for folder in {fixture.path.parts[0] for fixture in fixtures}:
        shutil.rmtree(root / folder, ignore_errors=True)
    for fixture in fixtures:
        target = root / fixture.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(fixture.content)
    manifest_path = root / MANIFEST_NAME
    manifest = build_manifest(fixtures, recorded_at)
    manifest_path.write_bytes(to_json_bytes(manifest))
    return manifest_path


def build_manifest(
    fixtures: Sequence[Fixture], recorded_at: datetime
) -> JsonObject:
    """Describe the fixtures: recording time, URL, size and checksum.

    Args:
        fixtures: Recorded responses.
        recorded_at: Recording time.

    Returns:
        The manifest as a JSON-serialisable dictionary.
    """
    files = [
        {
            "path": fixture.path.as_posix(),
            "url": fixture.url,
            "bytes": len(fixture.content),
            "sha256": hashlib.sha256(fixture.content).hexdigest(),
        }
        for fixture in fixtures
    ]
    return {
        "recorded_at": recorded_at.isoformat(timespec="seconds"),
        "files": files,
    }


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------

#: Source name -> recorder, in recording order.
RECORDERS: Final[Mapping[str, Recorder]] = MappingProxyType(
    {
        "owid": record_owid,
        "eurostat": record_eurostat,
        "worldbank": record_world_bank,
    }
)


def record_all(
    url_fetcher: Fetcher, recorders: Mapping[str, Recorder] = RECORDERS
) -> RecordingResult:
    """Run every recorder; a failing source does not stop the others.

    Args:
        url_fetcher: A callable that downloads a URL.
        recorders: Source name -> recorder.

    Returns:
        The fixtures of the sources that succeeded, and the names of
        those that failed.
    """
    fixtures: list[Fixture] = []
    failed_sources: list[str] = []

    for source_name, recorder in recorders.items():
        try:
            source_fixtures = recorder(url_fetcher)
        except SOURCE_ERRORS as error:
            failed_sources.append(source_name)
            failure_message = f"{source_name}: {type(error).__name__}: {error}"
            report("FAILED", failure_message, sys.stderr)
            continue
        fixtures.extend(source_fixtures)
        for fixture in source_fixtures:
            report("fetched", f"{fixture.path} ({len(fixture.content)} bytes)")

    return RecordingResult(tuple(fixtures), tuple(failed_sources))


def main(url_fetcher: Fetcher, root_directory: Path = FIXTURES_DIR) -> int:
    """Record every source, then save the fixtures if all succeeded.

    Args:
        url_fetcher: A callable that downloads a URL.
        root_directory: Fixtures directory.

    Returns:
        Process exit status: 0 on success, 1 if any source failed.
    """
    recording_result = record_all(url_fetcher)
    if recording_result.failed_sources:
        failed_count = len(recording_result.failed_sources)
        report(
            "aborted",
            f"{failed_count} source(s) failed; nothing written",
            sys.stderr,
        )
        return 1
    manifest = save_fixtures(
        recording_result.fixtures, root_directory, datetime.now(UTC)
    )
    report(
        "written",
        f"{len(recording_result.fixtures)} fixtures and {manifest.name}",
    )

    return 0


def record_live_sources() -> int:
    """Record the live sources over HTTPS; the script's entry point.

    Returns:
        Process exit status of :func:`main`.
    """
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    logging.getLogger(HTTPX_LOGGER).setLevel(logging.WARNING)
    with create_http_client(user_agent=USER_AGENT) as client:
        return main(HttpFetcher(client))


if __name__ == "__main__":
    sys.exit(record_live_sources())
