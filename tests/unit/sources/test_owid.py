"""Unit tests for ``myetl.sources.owid``."""

import pytest

from myetl.sources import owid
from myetl.sources.response_validation import ResponseFormatError

HEADER = ("country", "year", "iso_code", *owid.COLUMNS, "coal_share_elec")


def energy_csv(header: tuple[str, ...] = HEADER) -> bytes:
    """Build a one-row CSV under ``header``."""
    data_row = ["Austria", "2008", "AUT"] + ["1.0"] * (len(header) - 3)
    return (",".join(header) + "\n" + ",".join(data_row) + "\n").encode()


def test_fetch_requests_the_energy_csv_url() -> None:
    requested_urls: list[str] = []

    def fetch_and_record(url: str) -> bytes:
        requested_urls.append(url)
        return energy_csv()

    owid.fetch_energy_csv(fetch_and_record)

    assert requested_urls == [owid.ENERGY_CSV_URL]


def test_fetch_returns_whole_csv_byte_for_byte() -> None:
    csv_content = energy_csv()

    response = owid.fetch_energy_csv(lambda url: csv_content)

    assert response.url == owid.ENERGY_CSV_URL
    assert response.content == csv_content


def test_fetch_tags_response_with_resource_code() -> None:
    response = owid.fetch_energy_csv(lambda url: energy_csv())

    assert response.resource_code == owid.RESOURCE_CODE == "owid-energy-data"


@pytest.mark.parametrize("required_column", owid.REQUIRED_COLUMNS)
def test_fetch_without_required_column_fails(required_column: str) -> None:
    header = tuple(
        column_name for column_name in HEADER if column_name != required_column
    )

    with pytest.raises(ResponseFormatError, match=required_column):
        owid.fetch_energy_csv(lambda url: energy_csv(header))


def test_fetch_of_header_only_csv_fails() -> None:
    header_only_csv = (",".join(HEADER) + "\n").encode()

    with pytest.raises(ResponseFormatError, match="no data rows"):
        owid.fetch_energy_csv(lambda url: header_only_csv)
