"""Unit tests for ``myetl.transform.steps.reshape``."""

import pandas as pd
import pytest
from observation_dfs import OBSERVATION_HEADER, observation_rows

from myetl.sources import eurostat
from myetl.transform.observations import DfFormatError
from myetl.transform.steps import (
    ColumnMelter,
    ColumnRenamer,
    ObservationBuilder,
    PeriodParser,
    SourceColumns,
    ValueExtractor,
)

# --------------------------------------------------------------------
# Header and values
# --------------------------------------------------------------------


def test_columns_are_renamed_by_the_function() -> None:
    df = pd.DataFrame(columns=["STRUCTURE", "geo: Geopolitical entity"])

    renamed_df = ColumnRenamer(eurostat.get_value_code).apply(df)

    assert list(renamed_df.columns) == ["STRUCTURE", "geo"]


def test_repeated_new_column_names_fail() -> None:
    df = pd.DataFrame(columns=["geo: Country", "geo: Region"])

    with pytest.raises(DfFormatError, match=r"repeated names \['geo'\]"):
        ColumnRenamer(eurostat.get_value_code).apply(df)


def test_values_of_chosen_columns_are_replaced_by_their_part() -> None:
    df = pd.DataFrame(
        {"siec": ["CF_R: Renewable", "TOTAL"], "x": ["a: b", "c: d"]}
    )

    coded_df = ValueExtractor(("siec",), eurostat.get_value_code).apply(df)

    assert coded_df.to_dict("list") == {
        "siec": ["CF_R", "TOTAL"],
        "x": ["a: b", "c: d"],
    }


def test_value_labels_come_from_the_label_function() -> None:
    df = pd.DataFrame({"geo": ["EL: Greece", "XX"]})

    labelled_df = ValueExtractor(("geo",), eurostat.get_value_label).apply(df)

    assert labelled_df["geo"].tolist() == ["Greece", ""]


def test_missing_values_are_not_passed_to_the_function() -> None:
    df = pd.DataFrame({"geo": ["EL: Greece", None]}, dtype="str")

    coded_df = ValueExtractor(("geo",), eurostat.get_value_code).apply(df)

    assert coded_df["geo"].tolist()[0] == "EL"
    assert pd.isna(coded_df["geo"].tolist()[1])


def test_values_of_missing_column_fail() -> None:
    df = pd.DataFrame(columns=["geo"])

    with pytest.raises(DfFormatError, match="ValueExtractor"):
        ValueExtractor(("siec",), eurostat.get_value_code).apply(df)


def test_input_df_is_left_unchanged() -> None:
    df = pd.DataFrame({"geo": ["EL: Greece"]})

    ValueExtractor(("geo",), eurostat.get_value_label).apply(df)

    assert df["geo"].tolist() == ["EL: Greece"]


# --------------------------------------------------------------------
# Into observations
# --------------------------------------------------------------------


def test_melt_turns_value_columns_into_variable_rows() -> None:
    wide_df = pd.DataFrame(
        {"iso_code": ["AUT"], "year": ["2008"], "wind": ["2.0"], "solar": [""]}
    )

    long_df = ColumnMelter(("iso_code", "year"), ("wind", "solar")).apply(
        wide_df
    )

    assert long_df.to_dict("records") == [
        {
            "iso_code": "AUT",
            "year": "2008",
            "variable": "wind",
            "value": "2.0",
        },
        {"iso_code": "AUT", "year": "2008", "variable": "solar", "value": ""},
    ]


def test_melt_of_missing_column_fails() -> None:
    wide_df = pd.DataFrame(columns=["iso_code", "year"])

    with pytest.raises(DfFormatError, match="ColumnMelter"):
        ColumnMelter(("iso_code", "year"), ("wind",)).apply(wide_df)


EUROSTAT_COLUMNS = SourceColumns("geo", "TIME_PERIOD", "siec", "OBS_VALUE")


def test_observations_take_the_named_columns_as_numbers() -> None:
    df = pd.DataFrame(
        {
            "geo": ["AT"],
            "TIME_PERIOD": ["2017-01"],
            "siec": ["CF"],
            "OBS_VALUE": ["9227.314"],
            "OBS_FLAG": ["e"],
        }
    )

    observations = ObservationBuilder(EUROSTAT_COLUMNS).apply(df)

    assert observations.to_dict("records") == [
        {
            "country": "AT",
            "period": "2017-01",
            "variable": "CF",
            "value": 9227.314,
        }
    ]


def test_empty_and_null_values_become_absent_rows() -> None:
    df = pd.DataFrame(
        {
            "geo": ["AT", "BE", "CY"],
            "TIME_PERIOD": ["2008", "2008", "2008"],
            "siec": ["CF", "CF", "CF"],
            "OBS_VALUE": ["", None, "1.5"],
        }
    )

    observations = ObservationBuilder(EUROSTAT_COLUMNS).apply(df)

    assert observations["country"].tolist() == ["CY"]


def test_non_numeric_value_fails() -> None:
    df = pd.DataFrame(
        {
            "geo": ["AT"],
            "TIME_PERIOD": ["2008"],
            "siec": ["CF"],
            "OBS_VALUE": [":"],
        }
    )

    with pytest.raises(
        DfFormatError, match=r"expected numbers, found \[':'\]"
    ):
        ObservationBuilder(EUROSTAT_COLUMNS).apply(df)


def test_observations_from_missing_column_fail() -> None:
    df = pd.DataFrame(columns=["geo", "TIME_PERIOD", "siec"])

    with pytest.raises(DfFormatError, match="OBS_VALUE"):
        ObservationBuilder(EUROSTAT_COLUMNS).apply(df)


def test_periods_become_year_and_sub_period() -> None:
    df = pd.DataFrame(
        {
            "country": ["AUT", "AUT", "AUT"],
            "period": ["2008", "2008-S2", "2017-07"],
            "variable": ["REN", "EUR", "CF"],
            "value": [1.0, 2.0, 3.0],
        }
    )

    parsed_df = PeriodParser().apply(df)

    assert list(parsed_df.columns) == OBSERVATION_HEADER
    assert observation_rows(parsed_df) == [
        ("AUT", 2008, 1, "REN", 1.0),
        ("AUT", 2008, 2, "EUR", 2.0),
        ("AUT", 2017, 7, "CF", 3.0),
    ]


def test_unparseable_period_fails() -> None:
    df = pd.DataFrame(
        {
            "country": ["AUT"],
            "period": ["2008-Q1"],
            "variable": ["X"],
            "value": [1.0],
        }
    )

    with pytest.raises(DfFormatError, match="2008-Q1"):
        PeriodParser().apply(df)
