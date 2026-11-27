"""Unit tests for ``myetl.transform.steps.reshape``."""

import pandas as pd
import pytest
from observation_tables import OBSERVATION_HEADER, observation_rows

from myetl.transform.observations import TableFormatError
from myetl.transform.steps import (
    ColumnCodeRenamer,
    ColumnMelter,
    ObservationBuilder,
    PeriodParser,
    SourceColumns,
    ValueCodeExtractor,
    ValueLabelExtractor,
)

# --------------------------------------------------------------------
# Header and values
# --------------------------------------------------------------------


def test_coded_column_names_become_codes() -> None:
    table = pd.DataFrame(columns=["STRUCTURE", "geo: Geopolitical entity"])

    renamed_table = ColumnCodeRenamer().apply(table)

    assert list(renamed_table.columns) == ["STRUCTURE", "geo"]


def test_repeated_column_codes_fail() -> None:
    table = pd.DataFrame(columns=["geo: Country", "geo: Region"])

    with pytest.raises(TableFormatError, match=r"repeated codes \['geo'\]"):
        ColumnCodeRenamer().apply(table)


def test_value_codes_keep_part_before_separator() -> None:
    table = pd.DataFrame(
        {"siec": ["CF_R: Renewable", "TOTAL"], "x": ["a", "b"]}
    )

    coded_table = ValueCodeExtractor(("siec",)).apply(table)

    assert coded_table.to_dict("list") == {
        "siec": ["CF_R", "TOTAL"],
        "x": ["a", "b"],
    }


def test_value_labels_keep_part_after_separator() -> None:
    table = pd.DataFrame({"geo": ["EL: Greece", "XX"]})

    labelled_table = ValueLabelExtractor(("geo",)).apply(table)

    assert labelled_table["geo"].tolist() == ["Greece", ""]


def test_value_codes_use_given_separator() -> None:
    table = pd.DataFrame({"geo": ["AT|Austria"]})

    coded_table = ValueCodeExtractor(("geo",), separator="|").apply(table)

    assert coded_table["geo"].tolist() == ["AT"]


def test_value_codes_of_missing_column_fail() -> None:
    with pytest.raises(TableFormatError, match="ValueCodeExtractor"):
        ValueCodeExtractor(("siec",)).apply(pd.DataFrame(columns=["geo"]))


def test_input_table_is_left_unchanged() -> None:
    table = pd.DataFrame({"geo": ["EL: Greece"]})

    ValueLabelExtractor(("geo",)).apply(table)

    assert table["geo"].tolist() == ["EL: Greece"]


# --------------------------------------------------------------------
# Into observations
# --------------------------------------------------------------------


def test_melt_turns_value_columns_into_series_rows() -> None:
    table = pd.DataFrame(
        {"iso_code": ["AUT"], "year": ["2008"], "wind": ["2.0"], "solar": [""]}
    )

    long_table = ColumnMelter(("iso_code", "year"), ("wind", "solar")).apply(
        table
    )

    assert long_table.to_dict("records") == [
        {"iso_code": "AUT", "year": "2008", "series": "wind", "value": "2.0"},
        {"iso_code": "AUT", "year": "2008", "series": "solar", "value": ""},
    ]


def test_melt_of_missing_column_fails() -> None:
    table = pd.DataFrame(columns=["iso_code", "year"])

    with pytest.raises(TableFormatError, match="ColumnMelter"):
        ColumnMelter(("iso_code", "year"), ("wind",)).apply(table)


EUROSTAT_COLUMNS = SourceColumns("geo", "TIME_PERIOD", "siec", "OBS_VALUE")


def test_observations_take_the_named_columns_as_numbers() -> None:
    table = pd.DataFrame(
        {
            "geo": ["AT"],
            "TIME_PERIOD": ["2017-01"],
            "siec": ["CF"],
            "OBS_VALUE": ["9227.314"],
            "OBS_FLAG": ["e"],
        }
    )

    observations = ObservationBuilder(EUROSTAT_COLUMNS).apply(table)

    assert observations.to_dict("records") == [
        {
            "country": "AT",
            "period": "2017-01",
            "series": "CF",
            "value": 9227.314,
        }
    ]


def test_empty_and_null_values_become_absent_rows() -> None:
    table = pd.DataFrame(
        {
            "geo": ["AT", "BE", "CY"],
            "TIME_PERIOD": ["2008", "2008", "2008"],
            "siec": ["CF", "CF", "CF"],
            "OBS_VALUE": ["", None, "1.5"],
        }
    )

    observations = ObservationBuilder(EUROSTAT_COLUMNS).apply(table)

    assert observations["country"].tolist() == ["CY"]


def test_non_numeric_value_fails() -> None:
    table = pd.DataFrame(
        {
            "geo": ["AT"],
            "TIME_PERIOD": ["2008"],
            "siec": ["CF"],
            "OBS_VALUE": [":"],
        }
    )

    with pytest.raises(
        TableFormatError, match=r"expected numbers, found \[':'\]"
    ):
        ObservationBuilder(EUROSTAT_COLUMNS).apply(table)


def test_observations_from_missing_column_fail() -> None:
    table = pd.DataFrame(columns=["geo", "TIME_PERIOD", "siec"])

    with pytest.raises(TableFormatError, match="OBS_VALUE"):
        ObservationBuilder(EUROSTAT_COLUMNS).apply(table)


def test_periods_become_year_and_sub_period() -> None:
    table = pd.DataFrame(
        {
            "country": ["AUT", "AUT", "AUT"],
            "period": ["2008", "2008-S2", "2017-07"],
            "series": ["REN", "EUR", "CF"],
            "value": [1.0, 2.0, 3.0],
        }
    )

    parsed_table = PeriodParser().apply(table)

    assert list(parsed_table.columns) == OBSERVATION_HEADER
    assert observation_rows(parsed_table) == [
        ("AUT", 2008, 1, "REN", 1.0),
        ("AUT", 2008, 2, "EUR", 2.0),
        ("AUT", 2017, 7, "CF", 3.0),
    ]


def test_unparseable_period_fails() -> None:
    table = pd.DataFrame(
        {
            "country": ["AUT"],
            "period": ["2008-Q1"],
            "series": ["X"],
            "value": [1.0],
        }
    )

    with pytest.raises(TableFormatError, match="2008-Q1"):
        PeriodParser().apply(table)
