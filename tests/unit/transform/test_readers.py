"""Unit tests for ``myetl.transform.readers``."""

import json

import pytest

from myetl.sources.response_validation import SourceResponse
from myetl.transform.observations import DfFormatError
from myetl.transform.readers import CsvReader, JsonRecordReader

WORLD_BANK_FIELDS = {
    "countryiso3code": "country",
    "date": "period",
    "indicator.id": "variable",
    "value": "value",
}


def csv_response(csv_content: bytes) -> SourceResponse:
    """Wrap CSV bytes in a response."""
    return SourceResponse("df-a", "https://example.test/a.csv", csv_content)


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
# CsvReader
# --------------------------------------------------------------------


def test_csv_is_read_as_text_with_empty_fields_kept() -> None:
    response = csv_response(b"iso_code,year,wind\nAUT,2008,2.010\nAUT,2009,\n")

    df = CsvReader().read([response])

    assert df.to_dict("records") == [
        {"iso_code": "AUT", "year": "2008", "wind": "2.010"},
        {"iso_code": "AUT", "year": "2009", "wind": ""},
    ]


def test_csv_responses_are_concatenated_in_order() -> None:
    first_response = csv_response(b"a,b\n1,2\n")
    second_response = csv_response(b"a,b\n3,4\n")

    df = CsvReader().read([first_response, second_response])

    assert df["a"].tolist() == ["1", "3"]


def test_csv_reader_uses_given_encoding() -> None:
    response = csv_response("país\nEspaña\n".encode("latin-1"))

    df = CsvReader(encoding="latin-1").read([response])

    assert df.to_dict("records") == [{"país": "España"}]


def test_csv_reader_without_responses_fails() -> None:
    with pytest.raises(DfFormatError, match="CsvReader: no responses"):
        CsvReader().read([])


# --------------------------------------------------------------------
# JsonRecordReader
# --------------------------------------------------------------------


def test_json_fields_are_selected_and_renamed() -> None:
    response = json_response([population_record("AUT", 8321496)])

    df = JsonRecordReader(WORLD_BANK_FIELDS).read([response])

    assert df.to_dict("records") == [
        {
            "country": "AUT",
            "period": "2008",
            "variable": "SP.POP.TOTL",
            "value": 8321496,
        }
    ]


def test_json_pages_are_concatenated_in_order() -> None:
    first_page = json_response([population_record("AUT", 1)])
    second_page = json_response([population_record("BEL", 2)])

    df = JsonRecordReader(WORLD_BANK_FIELDS).read([first_page, second_page])

    assert df["country"].tolist() == ["AUT", "BEL"]


def test_json_null_value_is_kept_as_missing() -> None:
    response = json_response([population_record("AUT", None)])

    df = JsonRecordReader(WORLD_BANK_FIELDS).read([response])

    assert df["value"].isna().tolist() == [True]


def test_json_page_without_records_gives_empty_df() -> None:
    response = json_response([])

    df = JsonRecordReader(WORLD_BANK_FIELDS).read([response])

    assert df.empty
    assert list(df.columns) == ["country", "period", "variable", "value"]


def test_json_record_without_field_fails() -> None:
    response = json_response([{"countryiso3code": "AUT", "date": "2008"}])

    with pytest.raises(DfFormatError, match=r"indicator\.id"):
        JsonRecordReader(WORLD_BANK_FIELDS).read([response])


def test_json_records_position_is_configurable() -> None:
    response = SourceResponse(
        "df-a", "https://x", json.dumps([[{"a": 1}], "metadata"]).encode()
    )

    df = JsonRecordReader({"a": "alpha"}, records_position=0).read([response])

    assert df.to_dict("records") == [{"alpha": 1}]


def test_json_reader_without_responses_fails() -> None:
    with pytest.raises(DfFormatError, match="JsonRecordReader: no"):
        JsonRecordReader(WORLD_BANK_FIELDS).read([])
