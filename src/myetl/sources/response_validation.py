"""Downloaded source documents and the checks they pass on arrival.

Every source checks a response before returning it, so a malformed
document fails at once and is never cached or parsed downstream.
"""

__all__ = [
    "ResponseFormatError",
    "SourceResponse",
    "check_columns",
    "read_csv_header",
]

import csv
import io
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final

#: Encoding of every CSV source of the project.
_CSV_ENCODING: Final[str] = "utf-8"


class ResponseFormatError(ValueError):
    """A response does not have the format its source promises.

    Attributes:
        url: URL the response came from.
        problem: What was expected and what was found.
    """

    def __init__(self, url: str, problem: str) -> None:
        """Store the URL and the problem.

        Args:
            url: URL the response came from.
            problem: What was expected and what was found.
        """
        super().__init__(url, problem)
        self.url = url
        self.problem = problem

    def __str__(self) -> str:
        """Return the message, e.g. ``"no data rows: https://..."``."""
        return f"{self.problem}: {self.url}"


@dataclass(frozen=True, slots=True)
class SourceResponse:
    """A document downloaded from a source.

    Attributes:
        url: URL the document was downloaded from.
        content: Response body, byte for byte.
    """

    url: str
    content: bytes


def read_csv_header(response: SourceResponse) -> tuple[str, ...]:
    """Return the header of a CSV response that has data rows.

    Args:
        response: Response holding a CSV document.

    Returns:
        The column names, as written in the first line.

    Raises:
        ResponseFormatError: If the content is not UTF-8 text or it
            has no data row after the header.
    """
    try:
        csv_text = response.content.decode(_CSV_ENCODING)
    except UnicodeDecodeError as error:
        problem = f"expected {_CSV_ENCODING} CSV, found {error.reason}"
        raise ResponseFormatError(response.url, problem) from error
    csv_reader = csv.reader(io.StringIO(csv_text, newline=""))
    # A blank line reads as an empty row, so it counts as no data.
    csv_header = next(csv_reader, None)
    if not csv_header or not next(csv_reader, None):
        raise ResponseFormatError(response.url, "no data rows in CSV")
    return tuple(csv_header)


def check_columns(
    response: SourceResponse,
    response_columns: Iterable[str],
    required_columns: Sequence[str],
) -> None:
    """Raise error if a required column is missing from a response.

    Args:
        response: Response the columns come from. Used in the message.
        response_columns: Column names found in the response.
        required_columns: Column names the project reads.

    Raises:
        ResponseFormatError: If a required column is missing.
    """
    present_columns = set(response_columns)
    missing_columns = [
        column for column in required_columns if column not in present_columns
    ]
    if missing_columns:
        problem = f"missing required columns {missing_columns}"
        raise ResponseFormatError(response.url, problem)
