"""Unit tests for ``myetl.transform.readers``."""

import json

import pytest

from myetl.sources.response_validation import SourceResponse
from myetl.transform.observations import TableFormatError
from myetl.transform.readers import ReadCsv, ReadJsonRecords

WORLD_BANK_FIELDS = {
    "countryiso3code": "country",
    "date": "period",
    "indicator.id": "series",
    "value": "value",
}


def csv_response(csv_content: bytes) -> SourceResponse:
    """Wrap CSV bytes in a response."""
    return SourceResponse("table-a", "https://example.test/a.csv", csv_content)


def json_response(records: list[dict[str, object]]) -> SourceResponse:
    """Wrap records in a World Bank style ``[metadata, records]``."""
    page_document = [{"page": 1, "pages": 1}, records]
    return SourceResponse(
        "SP.POP.TOTL",
        "https://example.test/p",
        json.dumps(page_document).encode(),
    )


def population_record(country: str, value: float | None) -> dict[str, object]:
    """Return one World Bank population record."""
    return {
        "indicator": {"id": "SP.POP.TOTL", "value": "Population, total"},
        "countryiso3code": country,
        "date": "2008",
        "value": value,
    }


# --------------------------------------------------------------------
# ReadCsv
# --------------------------------------------------------------------


def test_csv_is_read_as_text_with_empty_fields_kept() -> None:
    response = csv_response(b"iso_code,year,wind\nAUT,2008,2.010\nAUT,2009,\n")

    table = ReadCsv().read([response])

    assert table.to_dict("records") == [
        {"iso_code": "AUT", "year": "2008", "wind": "2.010"},
        {"iso_code": "AUT", "year": "2009", "wind": ""},
    ]


def test_csv_responses_are_concatenated_in_order() -> None:
    first_response = csv_response(b"a,b\n1,2\n")
    second_response = csv_response(b"a,b\n3,4\n")

    table = ReadCsv().read([first_response, second_response])

    assert table["a"].tolist() == ["1", "3"]


def test_csv_reader_uses_given_encoding() -> None:
    response = csv_response("país\nEspaña\n".encode("latin-1"))

    table = ReadCsv(encoding="latin-1").read([response])

    assert table.to_dict("records") == [{"país": "España"}]


def test_csv_reader_without_responses_fails() -> None:
    with pytest.raises(TableFormatError, match="ReadCsv: no responses"):
        ReadCsv().read([])


# --------------------------------------------------------------------
# ReadJsonRecords
# --------------------------------------------------------------------


def test_json_fields_are_selected_and_renamed() -> None:
    response = json_response([population_record("AUT", 8321496)])

    table = ReadJsonRecords(WORLD_BANK_FIELDS).read([response])

    assert table.to_dict("records") == [
        {
            "country": "AUT",
            "period": "2008",
            "series": "SP.POP.TOTL",
            "value": 8321496,
        }
    ]


def test_json_pages_are_concatenated_in_order() -> None:
    first_page = json_response([population_record("AUT", 1)])
    second_page = json_response([population_record("BEL", 2)])

    table = ReadJsonRecords(WORLD_BANK_FIELDS).read([first_page, second_page])

    assert table["country"].tolist() == ["AUT", "BEL"]


def test_json_null_value_is_kept_as_missing() -> None:
    response = json_response([population_record("AUT", None)])

    table = ReadJsonRecords(WORLD_BANK_FIELDS).read([response])

    assert table["value"].isna().tolist() == [True]


def test_json_page_without_records_gives_empty_table() -> None:
    response = json_response([])

    table = ReadJsonRecords(WORLD_BANK_FIELDS).read([response])

    assert table.empty
    assert list(table.columns) == ["country", "period", "series", "value"]


def test_json_record_without_field_fails() -> None:
    response = json_response([{"countryiso3code": "AUT", "date": "2008"}])

    with pytest.raises(TableFormatError, match=r"indicator\.id"):
        ReadJsonRecords(WORLD_BANK_FIELDS).read([response])


def test_json_records_position_is_configurable() -> None:
    response = SourceResponse(
        "table-a", "https://x", json.dumps([[{"a": 1}], "metadata"]).encode()
    )

    table = ReadJsonRecords({"a": "alpha"}, records_position=0).read(
        [response]
    )

    assert table.to_dict("records") == [{"alpha": 1}]


def test_json_reader_without_responses_fails() -> None:
    with pytest.raises(TableFormatError, match="ReadJsonRecords: no"):
        ReadJsonRecords(WORLD_BANK_FIELDS).read([])
