"""HTTP GET with timeouts and retries: the network edge of the pipeline.

Other modules depend only on :data:`Fetch`, a callable that takes a URL
and returns the response body. :class:`HttpFetcher` is the production
implementation. Tests pass their own callables or an ``httpx`` mock
transport, so no test touches the network.
"""

__all__ = [
    "DEFAULT_REQUEST_TIMEOUTS",
    "DEFAULT_RETRY_POLICY",
    "USER_AGENT",
    "Fetch",
    "FetchError",
    "HttpFetcher",
    "RequestTimeouts",
    "RetryPolicy",
    "create_http_client",
]

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from types import TracebackType
from typing import Final, Self
from urllib.parse import urlsplit

import httpx

from myetl import __version__

#: Downloads a URL and returns the response body.
type Fetch = Callable[[str], bytes]

#: Identifies the pipeline to the data providers.
USER_AGENT: Final[str] = (
    f"myetl/{__version__} (+https://github.com/cieslarjacek/myetl)"
)

#: The only status a request succeeds with.
_HTTP_OK: Final[int] = 200

_logger = logging.getLogger(__name__)


class FetchError(RuntimeError):
    """A URL could not be downloaded.

    Attributes:
        url: The requested URL.
        reason: What went wrong, e.g. ``"HTTP 404"``.
    """

    def __init__(self, url: str, reason: str) -> None:
        """Store the URL and the reason.

        Args:
            url: The requested URL.
            reason: What went wrong.
        """
        # ``args`` must match ``__init__``: pickling rebuilds the error
        # as ``FetchError(*args)``, e.g. when it leaves a worker
        # process.
        super().__init__(url, reason)
        self.url = url
        self.reason = reason

    def __str__(self) -> str:
        """Return the message, e.g. ``"HTTP 404: https://..."``."""
        return f"{self.reason}: {self.url}"


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """When and how often a failed request is repeated.

    Attributes:
        backoff_seconds: How many seconds to wait before each retry.
            Its length is the number of retries. ``(1.0, 2.0, 4.0)``
            means up to four attempts in total.
        retry_statuses: HTTP statuses worth retrying (rate limiting
            and server errors). Any other non-200 status fails at once.

    Raises:
        ValueError: If a wait is negative.
    """

    backoff_seconds: tuple[float, ...] = (1.0, 2.0, 4.0)
    retry_statuses: frozenset[int] = frozenset({429, 500, 502, 503, 504})

    def __post_init__(self) -> None:
        """Reject negative waits."""
        if any(wait < 0 for wait in self.backoff_seconds):
            raise ValueError(f"negative backoff: {self.backoff_seconds}")


@dataclass(frozen=True, slots=True)
class RequestTimeouts:
    """Per-request time limits.

    Attributes:
        connect_seconds: Limit for opening the connection.
        read_seconds: Limit between two received chunks of data.

    Raises:
        ValueError: If a limit is not positive.
    """

    connect_seconds: float = 10.0
    read_seconds: float = 120.0

    def __post_init__(self) -> None:
        """Reject limits that are not positive."""
        if self.connect_seconds <= 0 or self.read_seconds <= 0:
            raise ValueError(f"timeouts must be positive: {self}")

    def as_httpx(self) -> httpx.Timeout:
        """Return the limits in the form ``httpx`` expects."""
        return httpx.Timeout(self.read_seconds, connect=self.connect_seconds)


DEFAULT_RETRY_POLICY: Final[RetryPolicy] = RetryPolicy()
DEFAULT_REQUEST_TIMEOUTS: Final[RequestTimeouts] = RequestTimeouts()


def create_http_client(
    timeouts: RequestTimeouts = DEFAULT_REQUEST_TIMEOUTS,
    user_agent: str = USER_AGENT,
) -> httpx.Client:
    """Create the HTTP client the pipeline uses by default.

    Args:
        timeouts: Per-request time limits.
        user_agent: Value of the ``User-Agent`` header sent with every
            request. It identifies the pipeline to source servers.

    Returns:
        A client with the time limits and the ``User-Agent`` header.
        Redirects are not followed, so a moved source fails loudly.
    """
    return httpx.Client(
        timeout=timeouts.as_httpx(),
        headers={"User-Agent": user_agent},
        follow_redirects=False,
    )


class HttpFetcher:
    """Download URLs over HTTPS, retrying transient failures.

    One instance reuses its connections across requests, which matters
    for paginated sources. Use it as a context manager so the
    connections are closed. An injected ``client`` is left open for
    its owner to close.
    """

    def __init__(
        self,
        client: httpx.Client | None = None,
        *,
        retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Create a fetcher.

        Args:
            client: HTTP client to use. By default
                :func:`create_http_client` makes one.
            retry_policy: When to retry and how long to wait.
            sleep: Waits between retries. Tests pass a fake.
        """
        self._owns_client = client is None
        self._client = client or create_http_client()
        self._retry_policy = retry_policy
        self._sleep = sleep

    def __enter__(self) -> Self:
        """Return the fetcher itself."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the client if this fetcher created it."""
        if self._owns_client:
            self._client.close()

    def __call__(self, url: str) -> bytes:
        """Download ``url`` and return the response body.

        Args:
            url: Absolute ``https`` URL.

        Returns:
            The response body of the first successful attempt.

        Raises:
            FetchError: If the URL is not ``https``, the server answers
                with a status that is not retried, or every attempt
                fails.
        """
        if urlsplit(url).scheme != "https":
            raise FetchError(url, "refusing non-https URL")
        waits = self._retry_policy.backoff_seconds
        for attempt, wait in enumerate(waits, start=1):
            try:
                return self._get(url)
            except _TransientError as failure:
                _logger.warning(
                    "%s (attempt %d), retrying in %g s: %s",
                    failure.reason,
                    attempt,
                    wait,
                    url,
                )
                self._sleep(wait)
        try:
            return self._get(url)
        except _TransientError as failure:
            attempts = len(waits) + 1
            reason = f"{failure.reason} after {attempts} attempts"
            raise FetchError(url, reason) from failure

    def _get(self, url: str) -> bytes:
        """Make one request.

        Args:
            url: Absolute ``https`` URL.

        Returns:
            The response body.

        Raises:
            _TransientError: On a timeout, a connection error or a
                retryable status.
            FetchError: On any other status than 200.
        """
        _logger.info("GET %s", url)
        try:
            response = self._client.get(url)
        except httpx.TransportError as error:
            raise _TransientError(type(error).__name__) from error
        if response.status_code == _HTTP_OK:
            _logger.info("%d bytes from %s", len(response.content), url)
            return response.content
        reason = f"HTTP {response.status_code}"
        if response.status_code in self._retry_policy.retry_statuses:
            raise _TransientError(reason)
        raise FetchError(url, reason)


class _TransientError(Exception):
    """A retryable failure: timeout, connection error, 429 or 5xx."""

    def __init__(self, reason: str) -> None:
        """Store the reason.

        Args:
            reason: What went wrong, e.g. ``"HTTP 503"``.
        """
        super().__init__(reason)
        self.reason = reason
