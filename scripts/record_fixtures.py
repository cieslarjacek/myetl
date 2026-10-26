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
import json
import sys
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit

from myetl.sources import (
    COUNTRIES,
    HOUSEHOLD_PRICE,
    MONTHLY_GENERATION,
    OWID_COLUMNS,
    OWID_ENERGY_CSV_URL,
    RES_SHARE,
    WORLD_BANK_INDICATORS,
    EurostatDataset,
    world_bank_url,
)

#: Years in the fixture slice.
SLICE_FIRST_YEAR: Final[int] = 2008
SLICE_LAST_YEAR: Final[int] = 2009

#: Eurostat time ranges: (dataset, since, until).
EUROSTAT_SLICES: Final[tuple[tuple[EurostatDataset, str, str], ...]] = (
    (RES_SHARE, "2008", "2009"),
    (HOUSEHOLD_PRICE, "2008-S1", "2009-S1"),
    (MONTHLY_GENERATION, "2008-01", "2009-06"),
)

#: Small page size, so the World Bank slice spans several pages.
WORLD_BANK_FIXTURE_PAGE_SIZE: Final[int] = 200

#: Repository root (this file lives in ``<root>/scripts/``).
REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
FIXTURES_DIR: Final[Path] = REPO_ROOT / "tests" / "fixtures"

#: Network settings.
TIMEOUT_SECONDS: Final[float] = 120.0
USER_AGENT: Final[str] = (
    "myetl-fixture-recorder (+https://github.com/cieslarjacek/myetl)"
)

#: OWID columns the pipeline needs besides the mapped value columns.
OWID_KEY_COLUMNS: Final[tuple[str, ...]] = ("country", "year", "iso_code")

#: Eurostat SDMX-CSV columns the pipeline relies on.
EUROSTAT_REQUIRED_COLUMNS: Final[tuple[str, ...]] = (
    "TIME_PERIOD",
    "OBS_VALUE",
)

#: Separator between code and label with ``labels=both``.
EUROSTAT_LABEL_SEPARATOR: Final[str] = ": "


class RecordingError(RuntimeError):
    """A source returned something other than the expected response."""


@dataclass(frozen=True, slots=True)
class Fixture:
    """One recorded response, held in memory until all sources succeed.

    Attributes:
        path: Destination, relative to ``tests/fixtures/``.
        url: URL the content was fetched from.
        content: Bytes to write.
    """

    path: Path
    url: str
    content: bytes


def fetch(url: str) -> bytes:
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
        if response.status != 200:
            raise RecordingError(f"HTTP {response.status} for {url}")
        body: bytes = response.read()
    return body


def pretty_json(document: Any) -> bytes:
    """Serialise a parsed JSON document with indentation.

    Args:
        document: Parsed JSON value.

    Returns:
        Indented JSON, UTF-8 encoded, newline-terminated.
    """
    text = json.dumps(document, indent=2, ensure_ascii=False)
    return (text + "\n").encode("utf-8")


def record_owid() -> list[Fixture]:
    """Download the OWID energy CSV and keep the slice years.

    All countries, regions and columns are kept; rows are copied
    unchanged under the original header.

    Returns:
        One fixture: ``owid/owid-energy-data.csv``.

    Raises:
        RecordingError: If a required column or country is missing.
    """
    raw = fetch(OWID_ENERGY_CSV_URL).decode("utf-8")
    reader = csv.reader(io.StringIO(raw, newline=""))
    header = next(reader)
    missing_columns = [
        column
        for column in (*OWID_KEY_COLUMNS, *OWID_COLUMNS)
        if column not in header
    ]
    if missing_columns:
        raise RecordingError(f"OWID CSV lacks columns: {missing_columns}")

    year_index = header.index("year")
    iso_index = header.index("iso_code")
    years = {str(y) for y in range(SLICE_FIRST_YEAR, SLICE_LAST_YEAR + 1)}
    rows = [row for row in reader if row[year_index] in years]
    found = {row[iso_index] for row in rows}
    missing_countries = sorted({c.iso3 for c in COUNTRIES} - found)
    if missing_countries:
        raise RecordingError(f"OWID lacks countries: {missing_countries}")

    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return [
        Fixture(
            Path("owid") / "owid-energy-data.csv",
            OWID_ENERGY_CSV_URL,
            buffer.getvalue().encode("utf-8"),
        )
    ]


def eurostat_country_labels(content: bytes, url: str) -> set[str]:
    """Check an SDMX-CSV response and return its geo labels.

    Args:
        content: Response body.
        url: Request URL, for error messages.

    Returns:
        The label part of every ``geo`` value (``"EL: Greece"`` gives
        ``"Greece"``), countries and aggregates alike.

    Raises:
        RecordingError: If required columns or the ``geo`` column are
            missing.
    """
    reader = csv.DictReader(io.StringIO(content.decode("utf-8")))
    header = reader.fieldnames or []
    missing = [c for c in EUROSTAT_REQUIRED_COLUMNS if c not in header]
    if missing:
        raise RecordingError(f"columns {missing} missing in {url}")
    geo_columns = [c for c in header if c.lower().startswith("geo")]
    if not geo_columns:
        raise RecordingError(f"no geo column in {url}; header: {header}")
    return {
        row[geo_columns[0]].partition(EUROSTAT_LABEL_SEPARATOR)[2]
        for row in reader
    }


def record_eurostat() -> list[Fixture]:
    """Fetch every Eurostat dataset for its slice time range.

    A country missing from one dataset is reported but allowed (it
    becomes empty values downstream). A country label found in none of
    the datasets means its ``eurostat_name`` is wrong, and fails.

    Returns:
        One SDMX-CSV fixture per dataset, stored byte for byte.

    Raises:
        RecordingError: If a response is malformed or a country label
            matches no dataset.
    """
    fixtures: list[Fixture] = []
    names = {c.eurostat_name for c in COUNTRIES}
    seen: set[str] = set()
    for dataset, since, until in EUROSTAT_SLICES:
        url = dataset.url(since=since, until=until)
        content = fetch(url)
        labels = eurostat_country_labels(content, url)
        seen |= labels
        absent = sorted(names - labels)
        if absent:
            print(f"note {dataset.dataset_code} has no data for: {absent}")
        fixtures.append(
            Fixture(
                Path("eurostat") / f"{dataset.dataset_code}.csv", url, content
            )
        )
    unmatched = sorted(names - seen)
    if unmatched:
        raise RecordingError(f"labels match no Eurostat data: {unmatched}")
    return fixtures


def record_world_bank_indicator(indicator: str) -> list[Fixture]:
    """Fetch every page of one World Bank indicator for the slice.

    Args:
        indicator: World Development Indicators code.

    Returns:
        One JSON fixture per page:
        ``worldbank/{indicator}/page-{n}.json``.

    Raises:
        RecordingError: If a response is an API error message or a
            country in scope is missing.
    """
    fixtures: list[Fixture] = []
    countries: set[str] = set()
    page, pages = 1, 1
    while page <= pages:
        url = world_bank_url(
            indicator,
            SLICE_FIRST_YEAR,
            SLICE_LAST_YEAR,
            page=page,
            per_page=WORLD_BANK_FIXTURE_PAGE_SIZE,
        )
        document = json.loads(fetch(url))
        # Success is a two-element array [metadata, rows]; errors come
        # back with HTTP 200 as a one-element array holding "message".
        if not (
            isinstance(document, list)
            and len(document) == 2
            and isinstance(document[0], dict)
            and "pages" in document[0]
            and isinstance(document[1], list)
        ):
            raise RecordingError(f"unexpected World Bank response: {url}")
        pages = int(document[0]["pages"])
        countries.update(row["countryiso3code"] for row in document[1])
        fixtures.append(
            Fixture(
                Path("worldbank") / indicator / f"page-{page}.json",
                url,
                pretty_json(document),
            )
        )
        page += 1
    missing = sorted({c.iso3 for c in COUNTRIES} - countries)
    if missing:
        raise RecordingError(f"World Bank {indicator} lacks: {missing}")
    if len(fixtures) < 2:
        raise RecordingError(
            f"World Bank {indicator} fits on one page; lower the page size"
        )
    return fixtures


def record_world_bank() -> list[Fixture]:
    """Fetch every World Bank indicator for the slice.

    Returns:
        The page fixtures of all indicators.
    """
    fixtures: list[Fixture] = []
    for indicator in WORLD_BANK_INDICATORS:
        fixtures.extend(record_world_bank_indicator(indicator))
    return fixtures


def write_fixtures(fixtures: list[Fixture]) -> Path:
    """Replace the recorded fixtures under ``tests/fixtures/``.

    Only the per-source folders written by this script are cleared
    first (so a dropped page or file does not linger); anything else in
    ``tests/fixtures/`` is left alone.

    Args:
        fixtures: Recorded responses from every source.

    Returns:
        Path of the manifest file.
    """
    for folder in {fixture.path.parts[0] for fixture in fixtures}:
        stale_dir = FIXTURES_DIR / folder
        if not stale_dir.is_dir():
            continue
        for stale in sorted(stale_dir.rglob("*"), reverse=True):
            if stale.is_dir():
                stale.rmdir()
            else:
                stale.unlink()
    entries = []
    for fixture in fixtures:
        target = FIXTURES_DIR / fixture.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(fixture.content)
        entries.append(
            {
                "path": fixture.path.as_posix(),
                "url": fixture.url,
                "bytes": len(fixture.content),
                "sha256": hashlib.sha256(fixture.content).hexdigest(),
            }
        )
    manifest = {
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "files": entries,
    }
    manifest_path = FIXTURES_DIR / "manifest.json"
    manifest_path.write_bytes(pretty_json(manifest))
    return manifest_path


def main() -> int:
    """Record every source, then write the fixtures if all succeeded.

    Returns:
        Process exit status: 0 on success, 1 if any source failed.
    """
    recorders: dict[str, Callable[[], list[Fixture]]] = {
        "owid": record_owid,
        "eurostat": record_eurostat,
        "worldbank": record_world_bank,
    }
    fixtures: list[Fixture] = []
    failures = 0
    for name, recorder in recorders.items():
        try:
            recorded = recorder()
        except (OSError, ValueError, KeyError, RecordingError) as error:
            # OSError: network errors (URLError, timeouts).
            # ValueError, KeyError: malformed JSON or CSV content.
            failures += 1
            print(f"FAILED   {name}: {error!r}", file=sys.stderr)
            continue
        fixtures.extend(recorded)
        for fixture in recorded:
            print(f"fetched {fixture.path} ({len(fixture.content)} bytes)")

    if failures:
        print(
            f"{failures} source(s) failed; nothing was written.",
            file=sys.stderr,
        )
        return 1
    manifest = write_fixtures(fixtures)
    print(f"written {len(fixtures)} fixtures and {manifest.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
