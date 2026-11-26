"""Readers: the responses of one resource to one table.

A reader knows a file format, not a source: :class:`ReadCsv` reads the
OWID and Eurostat CSVs alike, :class:`ReadJsonRecords` any JSON
document that holds a list of records. Responses were checked on
arrival (:mod:`myetl.sources`), so readers trust their shape.
"""

__all__ = [
    "CSV_ENCODING",
    "ReadCsv",
    "ReadJsonRecords",
    "Reader",
]

import io
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Protocol

import pandas as pd

from myetl.sources.response_validation import SourceResponse
from myetl.transform.observations import TableFormatError, require_columns

#: Encoding of every CSV source of the project.
CSV_ENCODING: Final[str] = "utf-8"


class Reader(Protocol):
    """Turns the responses of one resource into one table."""

    def read(self, responses: Sequence[SourceResponse]) -> pd.DataFrame:
        """Return the responses as one table.

        Args:
            responses: Every response of one resource, e.g. all pages of
                one World Bank indicator.

        Returns:
            The rows of all responses, in response order.
        """
        ...


@dataclass(frozen=True, slots=True)
class ReadCsv:
    """Read CSV responses as text, keeping empty fields as ``""``.

    Every column is read as text: converting values is the job of
    :class:`~myetl.transform.steps.ToObservations`, which can tell an
    empty field from a malformed number.

    Attributes:
        encoding: Text encoding of the CSV.
    """

    encoding: str = CSV_ENCODING

    def read(self, responses: Sequence[SourceResponse]) -> pd.DataFrame:
        """Return the rows of every CSV response under one header.

        Args:
            responses: CSV responses with the same header.

        Returns:
            One text column per CSV column.

        Raises:
            TableFormatError: If there are no responses.
        """
        _require_responses(responses, self.__class__.__name__)
        csv_tables = [
            pd.read_csv(
                io.BytesIO(response.content),
                dtype=str,
                keep_default_na=False,
                encoding=self.encoding,
            )
            for response in responses
        ]

        return pd.concat(csv_tables, ignore_index=True)


@dataclass(frozen=True, slots=True)
class ReadJsonRecords:
    """Read the records of JSON responses into chosen columns.

    Attributes:
        fields: Record field -> column name. Nested fields are written
            with dots, e.g. ``"indicator.id"``.
        records_position: Where the record list sits in each document:
            the World Bank sends ``[metadata, records]``, so 1.
    """

    fields: Mapping[str, str]
    records_position: int = 1

    def read(self, responses: Sequence[SourceResponse]) -> pd.DataFrame:
        """Return the chosen fields of every record of every response.

        Args:
            responses: JSON responses, e.g. all pages of one indicator.

        Returns:
            One column per entry of :attr:`fields`, in that order.

        Raises:
            TableFormatError: If there are no responses, or a record
                lacks a field.
        """
        _require_responses(responses, self.__class__.__name__)
        records = [
            record
            for response in responses
            for record in json.loads(response.content)[self.records_position]
        ]
        if not records:
            return pd.DataFrame(columns=list(self.fields.values()))

        record_table = pd.json_normalize(records)
        require_columns(record_table, self.fields, self.__class__.__name__)
        selected_table = record_table.loc[:, list(self.fields)]

        return selected_table.rename(columns=dict(self.fields))


def _require_responses(
    responses: Sequence[SourceResponse], reader_name: str
) -> None:
    """Raise if a reader got no responses.

    Args:
        responses: Responses to read.
        reader_name: Reader name, for the message.

    Raises:
        TableFormatError: If ``responses`` is empty.
    """
    if not responses:
        raise TableFormatError(f"{reader_name}: no responses to read")
