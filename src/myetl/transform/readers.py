"""Readers: the responses of one resource to one data frame.

A reader knows a file format, not a source: :class:`CsvReader` reads the
OWID and Eurostat CSVs alike, :class:`JsonRecordReader` any JSON
document that holds a list of records. Responses were checked on
arrival (:mod:`myetl.sources`), so readers trust their shape.
"""

__all__ = [
    "CSV_ENCODING",
    "CsvReader",
    "JsonRecordReader",
    "Reader",
]

import io
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Protocol

import pandas as pd

from myetl.sources.response_validation import SourceResponse
from myetl.transform.observations import DfFormatError, require_columns

#: Encoding of every CSV source of the project.
CSV_ENCODING: Final[str] = "utf-8"


class Reader(Protocol):
    """Turns the responses of one resource into one data frame."""

    def read(self, responses: Sequence[SourceResponse]) -> pd.DataFrame:
        """Return the responses as one data frame.

        Args:
            responses: Every response of one resource, e.g. all pages of
                one World Bank indicator.

        Returns:
            The rows of all responses, in response order.
        """
        ...


@dataclass(frozen=True, slots=True)
class CsvReader:
    """Reader of CSV responses as text, empty fields kept as ``""``.

    Every column is read as text: converting values is the job of
    :class:`~myetl.transform.steps.ObservationBuilder`, which can tell
    an empty field from a malformed number.

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
            DfFormatError: If there are no responses.
        """
        _require_responses(responses, type(self).__name__)
        csv_df = [
            pd.read_csv(
                io.BytesIO(response.content),
                dtype=str,
                keep_default_na=False,
                encoding=self.encoding,
            )
            for response in responses
        ]

        return pd.concat(csv_df, ignore_index=True)


@dataclass(frozen=True, slots=True)
class JsonRecordReader:
    """Reader of the records in JSON responses, into chosen columns.

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
            DfFormatError: If there are no responses, or a record
                lacks a field.
        """
        _require_responses(responses, type(self).__name__)
        records = [
            record
            for response in responses
            for record in json.loads(response.content)[self.records_position]
        ]
        if not records:
            return pd.DataFrame(columns=list(self.fields.values()))

        record_df = pd.json_normalize(records)
        require_columns(record_df, self.fields, type(self).__name__)
        selected_df = record_df.loc[:, list(self.fields)]

        return selected_df.rename(columns=dict(self.fields))


def _require_responses(
    responses: Sequence[SourceResponse], reader_name: str
) -> None:
    """Raise if a reader got no responses.

    Args:
        responses: Responses to read.
        reader_name: Reader name, for the message.

    Raises:
        DfFormatError: If ``responses`` is empty.
    """
    if not responses:
        raise DfFormatError(f"{reader_name}: no responses to read")
