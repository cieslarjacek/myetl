"""Unit tests for ``myetl._docstrings``."""

import pytest

from myetl._docstrings import share_docstring


def documented(value: int) -> int:
    """Return ``value`` doubled."""
    return value * 2


def test_share_docstring_copies_the_docstring() -> None:
    @share_docstring(documented)
    def target(value: int) -> int:
        return value * 3

    assert target.__doc__ == documented.__doc__


def test_share_docstring_keeps_the_target_function() -> None:
    def target(value: int) -> int:
        return value * 3

    decorated = share_docstring(documented)(target)

    assert decorated is target
    assert decorated(2) == 6
    assert decorated.__name__ == "target"


def test_share_docstring_rejects_source_without_docstring() -> None:
    def undocumented() -> None:
        pass

    with pytest.raises(ValueError, match="undocumented"):
        share_docstring(undocumented)
