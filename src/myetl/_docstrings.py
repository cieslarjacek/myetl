"""Docstring helpers shared across the package."""

__all__ = ["share_docstring"]

from collections.abc import Callable


def share_docstring[F: Callable[..., object]](
    source: Callable[..., object],
) -> Callable[[F], F]:
    """Return a decorator that copies the docstring of ``source``.

    For closely related functions, such as two halves of one operation,
    that are described by a single docstring. The decorated function
    keeps its own signature, type and behavior.

    Static tools read docstrings from the source code, so they do not
    see the shared one. The decorated function needs ``# noqa: D103``
    for ruff, and editors show no docstring on hover.

    Args:
        source: Function whose docstring is shared.

    Returns:
        A decorator that sets the target's ``__doc__`` and returns the
        target unchanged.

    Raises:
        ValueError: If ``source`` has no docstring.
    """
    if not source.__doc__:
        raise ValueError(f"{source.__qualname__} has no docstring to share")
    docstring = source.__doc__

    def decorate(target: F) -> F:
        target.__doc__ = docstring
        return target

    return decorate
