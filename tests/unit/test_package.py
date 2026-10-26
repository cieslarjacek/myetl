"""Smoke tests for the package skeleton.

They verify that the src-layout package is installed into the
environment (not imported from the working tree) and that its metadata
is consistent.
"""

import re
from importlib.metadata import version
from pathlib import Path

import myetl

# PEP 440 release segment used by this project: X.X.X (release) or
# X.X.X.XXXX (DEV build).
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+(\.\d{4})?$")


def test_version_matches_distribution_metadata() -> None:
    assert myetl.__version__ == version("myetl")


def test_version_follows_project_scheme() -> None:
    assert VERSION_PATTERN.fullmatch(myetl.__version__)


def test_package_ships_py_typed_marker() -> None:
    package_dir = Path(myetl.__file__).parent
    assert (package_dir / "py.typed").is_file()
