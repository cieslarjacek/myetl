"""Unit tests for ``myetl.http_client``.

Requests go to an ``httpx.MockTransport``; waits go to a list instead
of ``time.sleep``. Nothing touches the network.
"""

import logging
import pickle
from collections.abc import Callable, Iterator

import httpx
import pytest

from myetl.http_client import (
    DEFAULT_RETRY_POLICY,
    USER_AGENT,
    FetchError,
    HttpFetcher,
    RequestTimeouts,
    RetryPolicy,
    create_http_client,
)

URL = "https://example.test/data.csv"

type Handler = Callable[[httpx.Request], httpx.Response]


def respond_in_turn(*outcomes: int | type[httpx.TransportError]) -> Handler:
    """Answer each request with the next status, or raise an error."""
    remaining: Iterator[int | type[httpx.TransportError]] = iter(outcomes)

    def handler(request: httpx.Request) -> httpx.Response:
        outcome = next(remaining)
        if isinstance(outcome, int):
            return httpx.Response(outcome, content=b"body")
        raise outcome("simulated", request=request)

    return handler


class RequestRecorder:
    """Records requests and waits made by a fetcher under test."""

    def __init__(self, handler: Handler) -> None:
        self.requests: list[httpx.Request] = []
        self.waits: list[float] = []

        def recording_handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return handler(request)

        self.client = httpx.Client(
            transport=httpx.MockTransport(recording_handler),
            headers={"User-Agent": USER_AGENT},
        )

    def fetcher(
        self, policy: RetryPolicy = DEFAULT_RETRY_POLICY
    ) -> HttpFetcher:
        return HttpFetcher(
            self.client, retry_policy=policy, sleep=self.waits.append
        )


# --------------------------------------------------------------------
# Success and refusal
# --------------------------------------------------------------------


def test_returns_body_on_first_success() -> None:
    recorder = RequestRecorder(respond_in_turn(200))

    body = recorder.fetcher()(URL)

    assert body == b"body"
    assert len(recorder.requests) == 1
    assert recorder.waits == []


def test_refuses_plain_http_without_requesting() -> None:
    recorder = RequestRecorder(respond_in_turn(200))

    with pytest.raises(FetchError, match="non-https"):
        recorder.fetcher()("http://example.test/data.csv")

    assert recorder.requests == []


def test_default_client_identifies_itself_and_has_timeouts() -> None:
    client = create_http_client(
        RequestTimeouts(connect_seconds=5.0, read_seconds=60.0)
    )

    assert client.headers["User-Agent"] == USER_AGENT
    assert USER_AGENT.startswith("myetl/")
    assert client.timeout.connect == 5.0
    assert client.timeout.read == 60.0
    assert not client.follow_redirects


def test_client_sends_given_user_agent() -> None:
    client = create_http_client(user_agent="probe/1.0")

    assert client.headers["User-Agent"] == "probe/1.0"


# --------------------------------------------------------------------
# Retries
# --------------------------------------------------------------------


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_retries_retryable_status_then_succeeds(status: int) -> None:
    recorder = RequestRecorder(respond_in_turn(status, 200))

    body = recorder.fetcher()(URL)

    assert body == b"body"
    assert recorder.waits == [1.0]


@pytest.mark.parametrize(
    "error", [httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout]
)
def test_retries_transport_error_then_succeeds(
    error: type[httpx.TransportError],
) -> None:
    recorder = RequestRecorder(respond_in_turn(error, error, 200))

    body = recorder.fetcher()(URL)

    assert body == b"body"
    assert recorder.waits == [1.0, 2.0]


def test_gives_up_after_all_retries() -> None:
    recorder = RequestRecorder(respond_in_turn(503, 503, 503, 503))

    with pytest.raises(FetchError, match="HTTP 503 after 4 attempts"):
        recorder.fetcher()(URL)

    assert len(recorder.requests) == 4
    assert recorder.waits == [1.0, 2.0, 4.0]


@pytest.mark.parametrize("status", [301, 400, 403, 404])
def test_does_not_retry_other_statuses(status: int) -> None:
    recorder = RequestRecorder(respond_in_turn(status))

    with pytest.raises(FetchError, match=f"HTTP {status}") as raised:
        recorder.fetcher()(URL)

    assert raised.value.url == URL
    assert len(recorder.requests) == 1
    assert recorder.waits == []


def test_custom_policy_without_retries_fails_on_first_error() -> None:
    recorder = RequestRecorder(respond_in_turn(503))

    with pytest.raises(FetchError, match="after 1 attempts"):
        recorder.fetcher(RetryPolicy(backoff_seconds=()))(URL)

    assert recorder.waits == []


def test_logs_each_retry_as_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    recorder = RequestRecorder(respond_in_turn(503, 200))

    with caplog.at_level(logging.WARNING, logger="myetl.http_client"):
        recorder.fetcher()(URL)

    [record] = caplog.records
    assert "HTTP 503 (attempt 1), retrying in 1 s" in record.getMessage()


# --------------------------------------------------------------------
# Client ownership
# --------------------------------------------------------------------


def test_injected_client_stays_open_after_exit() -> None:
    recorder = RequestRecorder(respond_in_turn(200, 200))

    with recorder.fetcher() as fetcher:
        fetcher(URL)

    assert not recorder.client.is_closed


def test_own_client_is_closed_after_exit() -> None:
    with HttpFetcher() as fetcher:
        # The default client is created internally, so it can only be
        # observed through the private attribute.
        client = fetcher._client

    assert client.is_closed


# --------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------


def test_fetch_error_message_names_reason_and_url() -> None:
    error = FetchError(URL, "HTTP 404")

    assert str(error) == f"HTTP 404: {URL}"


def test_fetch_error_survives_pickling() -> None:
    error = FetchError(URL, "HTTP 404")
    error.add_note("while fetching OWID")

    # Safe: the bytes come from ``pickle.dumps`` one call earlier.
    restored = pickle.loads(pickle.dumps(error))  # noqa: S301

    assert (restored.url, restored.reason) == (URL, "HTTP 404")
    assert str(restored) == str(error)
    assert restored.__notes__ == ["while fetching OWID"]


# --------------------------------------------------------------------
# Value objects
# --------------------------------------------------------------------


def test_retry_policy_rejects_negative_wait() -> None:
    with pytest.raises(ValueError, match="negative"):
        RetryPolicy(backoff_seconds=(1.0, -1.0))


@pytest.mark.parametrize(("connect", "read"), [(0.0, 1.0), (1.0, -1.0)])
def test_timeouts_reject_non_positive_limits(
    connect: float, read: float
) -> None:
    with pytest.raises(ValueError, match="positive"):
        RequestTimeouts(connect, read)


def test_timeouts_convert_to_httpx() -> None:
    timeout = RequestTimeouts(
        connect_seconds=5.0, read_seconds=60.0
    ).as_httpx()

    assert timeout.connect == 5.0
    assert timeout.read == 60.0
