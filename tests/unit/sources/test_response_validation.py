"""Unit tests for ``myetl.sources.response_validation``."""

import pickle

import pytest

from myetl.sources.response_validation import (
    ResponseFormatError,
    SourceResponse,
    read_csv_header,
    validate_columns,
)

URL = "https://example.test/data.csv"
RESOURCE_CODE = "table-a"


def csv_response(csv_content: bytes) -> SourceResponse:
    """Wrap ``csv_content`` in a response from :data:`URL`."""
    return SourceResponse(RESOURCE_CODE, URL, csv_content)


# --------------------------------------------------------------------
# read_csv_header
# --------------------------------------------------------------------


def test_header_of_csv_with_data_rows() -> None:
    response = csv_response(b"country,year\nAustria,2008\n")

    header = read_csv_header(response)

    assert header == ("country", "year")


def test_quoted_header_names_are_unquoted() -> None:
    response = csv_response(b'"geo: Geopolitical, entity",year\nAT,2008\n')

    header = read_csv_header(response)

    assert header == ("geo: Geopolitical, entity", "year")


@pytest.mark.parametrize(
    "csv_content",
    [b"", b"country,year\n", b"country,year", b"country,year\n\n"],
    ids=["empty", "header-only", "header-without-newline", "blank-row"],
)
def test_csv_without_data_rows_fails(csv_content: bytes) -> None:
    with pytest.raises(ResponseFormatError, match="no data rows"):
        read_csv_header(csv_response(csv_content))


def test_csv_that_is_not_utf8_fails() -> None:
    response = csv_response("país,year\nEspaña,2008\n".encode("latin-1"))

    with pytest.raises(ResponseFormatError, match="expected utf-8 CSV"):
        read_csv_header(response)


# --------------------------------------------------------------------
# validate_columns
# --------------------------------------------------------------------


def test_all_required_columns_present_passes() -> None:
    validate_columns(csv_response(b""), ["a", "b", "c"], ("a", "c"))


def test_missing_columns_are_named_in_required_order() -> None:
    with pytest.raises(ResponseFormatError) as raised:
        validate_columns(csv_response(b""), ["b"], ("c", "b", "a"))

    assert raised.value.problem == "missing required columns ['c', 'a']"
    assert raised.value.url == URL


# --------------------------------------------------------------------
# ResponseFormatError
# --------------------------------------------------------------------


def test_format_error_message_names_problem_and_url() -> None:
    error = ResponseFormatError(URL, "no data rows in CSV")

    assert str(error) == f"no data rows in CSV: {URL}"


def test_format_error_is_a_value_error() -> None:
    assert issubclass(ResponseFormatError, ValueError)


def test_format_error_survives_pickling() -> None:
    error = ResponseFormatError(URL, "no data rows in CSV")

    # Safe: the bytes come from ``pickle.dumps`` one call earlier.
    restored_error = pickle.loads(pickle.dumps(error))  # noqa: S301

    assert restored_error.url == URL
    assert restored_error.problem == "no data rows in CSV"
    assert str(restored_error) == str(error)


# --------------------------------------------------------------------
# SourceResponse
# --------------------------------------------------------------------


def test_source_response_rejects_empty_resource_code() -> None:
    with pytest.raises(ValueError, match="empty resource code"):
        SourceResponse("", URL, b"country,year\n")
