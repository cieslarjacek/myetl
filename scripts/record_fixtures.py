"""Record test fixtures from the live data sources.

Fetches a small time slice of every source, as raw as the pipeline gets
it (all countries and aggregates, all columns), and writes it under
``tests/fixtures/`` with a manifest of URLs and SHA-256 checksums:

* OWID: all rows for 2008-2009, all columns.
* Eurostat: annual 2008-2009, semi-annual 2008-S1..2009-S1, monthly
  2008-01..2009-06. The last year of the two sub-annual datasets is
  incomplete on purpose, to test the aggregation rules.
* World Bank: all economies, 2008-2009, with a small page size so the
  response spans several pages.

The script also checks that every country in scope can be matched in
each source. Nothing is written unless every source succeeds. Run it
with ``make fixtures`` and commit the result.
"""

__all__ = ["main"]

import csv
import hashlib
import io
import itertools
import json
import shutil
import sys
import urllib.request
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Final, TextIO
from urllib.parse import urlsplit

from myetl.scope import EUROSTAT_NAMES, ISO3_CODES, YearRange
from myetl.sources import eurostat, owid, worldbank

type Fetch = Callable[[str], bytes]
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
class WorldBankPage:
    """The parts of a World Bank response page the recorder uses.

    Attributes:
        total_pages: Number of pages in the whole response.
        rows: Observations on this page.
    """

    total_pages: int
    rows: tuple[JsonObject, ...]


@dataclass(frozen=True, slots=True)
class RecordingResult:
    """Outcome of recording every source.

    Attributes:
        fixtures: Fixtures of the sources that succeeded.
        failed_sources: Names of the sources that failed.
    """

    fixtures: tuple[Fixture, ...]
    failed_sources: tuple[str, ...]


type Recorder = Callable[[Fetch], list[Fixture]]

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------

#: Years in the fixture slice.
SLICE_YEARS: Final[YearRange] = YearRange(2008, 2009)

#: Eurostat time ranges; the sub-annual ones end mid-2009 on purpose.
EUROSTAT_SLICES: Final[tuple[EurostatSlice, ...]] = (
    EurostatSlice(eurostat.RES_SHARE, ("2008", "2009")),
    EurostatSlice(eurostat.HOUSEHOLD_PRICE, ("2008-S1", "2009-S1")),
    EurostatSlice(eurostat.MONTHLY_GENERATION, ("2008-01", "2009-06")),
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

#: Network settings.
TIMEOUT_SECONDS: Final[float] = 120.0
HTTP_OK: Final[int] = 200
USER_AGENT: Final[str] = (
    "myetl-fixture-recorder (+https://github.com/cieslarjacek/myetl)"
)

#: Errors that fail one source without stopping the others: network
#: errors (OSError covers URLError and timeouts), malformed content
#: (ValueError, KeyError) and failed checks (RecordingError).
SOURCE_ERRORS: Final = (OSError, ValueError, KeyError, RecordingError)


# ----------------------------------------------------------------------
# Edges: network and console
# ----------------------------------------------------------------------


def fetch_https(url: str) -> bytes:
    """Download ``url`` and return the response body.

    Args:
        url: Absolute ``https`` URL.

    Returns:
        The raw response body.

    Raises:
        RecordingError: If the URL is not ``https`` or the server does
            not answer with HTTP 200.
    """
    if urlsplit(url).scheme != "https":
        raise RecordingError(f"refusing non-https URL: {url}")
    request = urllib.request.Request(  # noqa: S310 (https checked above)
        url, headers={"User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(  # noqa: S310 (https checked above)
        request, timeout=TIMEOUT_SECONDS
    ) as response:
        if response.status != HTTP_OK:
            raise RecordingError(f"HTTP {response.status} for {url}")
        body: bytes = response.read()
    return body


def report(status: str, message: str, stream: TextIO | None = None) -> None:
    """Print one console line: a status word, then a message.

    Args:
        status: Short status word, e.g. ``fetched``.
        message: Rest of the line.
        stream: Output stream; ``None`` means standard output.
    """
    print(f"{status} {message}", file=stream)


# ----------------------------------------------------------------------
# Serialisation and checks (pure)
# ----------------------------------------------------------------------


def to_csv_bytes(
    header: Sequence[str], rows: Iterable[Sequence[str]]
) -> bytes:
    """Serialise CSV rows under a header, with Unix line endings.

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
    """Serialise a JSON document with indentation, for readable diffs.

    Args:
        document: Parsed JSON value.

    Returns:
        Indented JSON, UTF-8 encoded, newline-terminated.
    """
    text = json.dumps(document, indent=2, ensure_ascii=False)
    return (text + "\n").encode("utf-8")


def require_columns(
    header: Sequence[str], required: Iterable[str], source: str
) -> None:
    """Raise if a required column is missing from a CSV header.

    Args:
        header: Column names found.
        required: Column names expected.
        source: Source name, for the error message.

    Raises:
        RecordingError: If a required column is missing.
    """
    missing = [column for column in required if column not in header]
    if missing:
        raise RecordingError(f"{source} lacks columns: {missing}")


def require_countries(
    found: Iterable[str], expected: frozenset[str], source: str
) -> None:
    """Raise if a country key in scope is missing from a source.

    Args:
        found: Country keys present in the source.
        expected: Country keys in scope.
        source: Source name, for the error message.

    Raises:
        RecordingError: If a country key is missing.
    """
    missing = sorted(expected - set(found))
    if missing:
        raise RecordingError(f"{source} lacks countries: {missing}")


# ----------------------------------------------------------------------
# OWID
# ----------------------------------------------------------------------


def record_owid(fetch: Fetch) -> list[Fixture]:
    """Download the OWID energy CSV and keep the slice years.

    All countries, regions and columns are kept; rows are copied
    unchanged under the original header.

    Args:
        fetch: Downloads a URL.

    Returns:
        One fixture with the slice rows.

    Raises:
        RecordingError: If a required column or country is missing.
    """
    text = fetch(owid.ENERGY_CSV_URL).decode("utf-8")
    header, *rows = csv.reader(io.StringIO(text, newline=""))
    require_columns(header, owid.REQUIRED_COLUMNS, "OWID")
    year, iso_code = header.index("year"), header.index("iso_code")
    sliced = [row for row in rows if int(row[year]) in SLICE_YEARS]
    require_countries((row[iso_code] for row in sliced), ISO3_CODES, "OWID")
    content = to_csv_bytes(header, sliced)
    return [Fixture(OWID_FIXTURE, owid.ENERGY_CSV_URL, content)]


# ----------------------------------------------------------------------
# Eurostat
# ----------------------------------------------------------------------


def record_eurostat(fetch: Fetch) -> list[Fixture]:
    """Fetch every Eurostat dataset for its slice time range.

    A country missing from one dataset is reported but allowed (it
    becomes empty values downstream). A country label found in none of
    the datasets means its Eurostat name is wrong, and fails.

    Args:
        fetch: Downloads a URL.

    Returns:
        One SDMX-CSV fixture per dataset, stored byte for byte.

    Raises:
        RecordingError: If a response is malformed or a country label
            matches no dataset.
    """
    fixtures: list[Fixture] = []
    labels_seen: set[str] = set()
    for eurostat_slice in EUROSTAT_SLICES:
        fixture, labels = _record_eurostat_slice(fetch, eurostat_slice)
        fixtures.append(fixture)
        labels_seen |= labels
    require_countries(labels_seen, EUROSTAT_NAMES, "Eurostat")
    return fixtures


def _record_eurostat_slice(
    fetch: Fetch, eurostat_slice: EurostatSlice
) -> tuple[Fixture, frozenset[str]]:
    """Fetch one Eurostat slice and note countries it has no data for.

    Args:
        fetch: Downloads a URL.
        eurostat_slice: Dataset and time range to request.

    Returns:
        The fixture and the ``geo`` labels found in it.
    """
    code = eurostat_slice.dataset.dataset_code
    url = eurostat_slice.dataset.create_request_url(
        eurostat_slice.period_range
    )
    content = fetch(url)
    labels = geo_labels(content, f"Eurostat {code}")
    absent = sorted(EUROSTAT_NAMES - labels)
    if absent:
        report("note", f"{code} has no data for: {absent}")
    return Fixture(Path("eurostat") / f"{code}.csv", url, content), labels


def geo_labels(content: bytes, source: str) -> frozenset[str]:
    """Check an SDMX-CSV response and return its ``geo`` labels.

    Columns are matched by code: with ``labels=both`` the header reads
    ``"TIME_PERIOD: Time"``, not ``"TIME_PERIOD"``.

    Args:
        content: Response body.
        source: Source name, for error messages.

    Returns:
        The label of every ``geo`` value, countries and aggregates.

    Raises:
        RecordingError: If a required column is missing.
    """
    header, *rows = csv.reader(io.StringIO(content.decode("utf-8")))
    column_codes = [eurostat.get_value_code(name) for name in header]
    require_columns(column_codes, eurostat.REQUIRED_COLUMNS, source)
    geo = column_codes.index(eurostat.GEO_COLUMN)
    return frozenset(eurostat.get_value_label(row[geo]) for row in rows)


# ----------------------------------------------------------------------
# World Bank
# ----------------------------------------------------------------------


def record_world_bank(fetch: Fetch) -> list[Fixture]:
    """Fetch every page of every World Bank indicator for the slice.

    Args:
        fetch: Downloads a URL.

    Returns:
        The page fixtures of all indicators.
    """
    return [
        fixture
        for indicator in worldbank.INDICATORS
        for fixture in _record_indicator(fetch, indicator)
    ]


def _record_indicator(
    fetch: Fetch, indicator: worldbank.Indicator
) -> list[Fixture]:
    """Fetch every page of one indicator.

    Args:
        fetch: Downloads a URL.
        indicator: Indicator to request.

    Returns:
        One fixture per page: ``worldbank/{code}/page-{n}.json``.

    Raises:
        RecordingError: If a country in scope is missing, or the slice
            fits on one page (pagination would go untested).
    """
    source = f"World Bank {indicator.indicator_code}"
    fixtures: list[Fixture] = []
    countries: set[str] = set()
    for number in itertools.count(1):
        url = indicator.create_request_url(
            SLICE_YEARS, number, WORLD_BANK_URL_SETTINGS
        )
        document = json.loads(fetch(url))
        page = parse_world_bank_page(document, source)
        countries.update(str(row["countryiso3code"]) for row in page.rows)
        folder = Path("worldbank") / indicator.indicator_code
        path = folder / f"page-{number}.json"
        fixtures.append(Fixture(path, url, to_json_bytes(document)))
        if number >= page.total_pages:
            break
    require_countries(countries, ISO3_CODES, source)
    if len(fixtures) < 2:
        raise RecordingError(f"{source} fits on one page; lower page size")
    return fixtures


def parse_world_bank_page(document: object, source: str) -> WorldBankPage:
    """Check the shape of a decoded World Bank response page.

    Args:
        document: Decoded JSON response.
        source: Source name, for the error message.

    Returns:
        The page count and the rows.

    Raises:
        RecordingError: If the document is not ``[metadata, rows]``
            (for example an API error message).
    """
    match document:
        case [{"pages": int() | str() as total_pages}, list(rows)]:
            return WorldBankPage(int(total_pages), tuple(rows))
    raise RecordingError(f"{source}: unexpected response {document!r:.200}")


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
    fetch: Fetch, recorders: Mapping[str, Recorder] = RECORDERS
) -> RecordingResult:
    """Run every recorder; a failing source does not stop the others.

    Args:
        fetch: Downloads a URL.
        recorders: Source name -> recorder.

    Returns:
        The fixtures of the sources that succeeded, and the names of
        those that failed.
    """
    fixtures: list[Fixture] = []
    failed_sources: list[str] = []
    for name, recorder in recorders.items():
        try:
            recorded = recorder(fetch)
        except SOURCE_ERRORS as error:
            failed_sources.append(name)
            report("FAILED", f"{name}: {error!r}", sys.stderr)
            continue
        fixtures.extend(recorded)
        for fixture in recorded:
            report("fetched", f"{fixture.path} ({len(fixture.content)} bytes)")
    return RecordingResult(tuple(fixtures), tuple(failed_sources))


def main(fetch: Fetch = fetch_https, root: Path = FIXTURES_DIR) -> int:
    """Record every source, then save the fixtures if all succeeded.

    Args:
        fetch: Downloads a URL.
        root: Fixtures directory.

    Returns:
        Process exit status: 0 on success, 1 if any source failed.
    """
    result = record_all(fetch)
    if result.failed_sources:
        failed = len(result.failed_sources)
        report(
            "aborted",
            f"{failed} source(s) failed; nothing written",
            sys.stderr,
        )
        return 1
    manifest = save_fixtures(result.fixtures, root, datetime.now(UTC))
    report("written", f"{len(result.fixtures)} fixtures and {manifest.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
