"""Mock-engine fixtures for SDK-level end-to-end tests.

These tests prove the chat flow through the real ``Talk2ViewSDKClient``
and the real ``talk2view`` SDK by replacing the engine's HTTP boundary
with canned responses. No network calls; deterministic results.

The fixtures:

  - ``mock_httpx_request``: pinned canned responses to non-streaming
    HTTP calls (login, refresh, session create, session delete, tool
    register, resume). Each request matches by ``(method, url)``;
    unhandled calls raise a clear ``AssertionError`` so a new test
    can't silently leak past the mock.

  - ``mock_httpx_stream``: canned SSE chunks for ``stream_request``.
    Tests push their own scripts via the ``with mock_chat_script(...)``
    helper so a tool-interrupt + resume cycle reads from two pre-seeded
    chunk lists in order.

  - ``mock_sdk``: a fully-wired :class:`Talk2ViewSDKClient` whose
    underlying SDK uses the mocks above. Already "logged in" so chat
    tests don't have to call ``login()`` first.

This layer + ``tests/synthetic/`` together cover the full panel ↔ SDK ↔
tool ↔ document loop without the engine or soffice — the missing piece
for CI on forks where T2V_TEST_USER_* secrets aren't available.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

# ---------------------------------------------------------------------------
# httpx mocks
# ---------------------------------------------------------------------------


class _MockResponse:
    """Subset of ``httpx.Response`` the SDK reads."""

    def __init__(
        self,
        status_code: int,
        json_body: dict[str, Any] | None = None,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self.is_success = 200 <= status_code < 300
        self._json = json_body
        self.text = text or (json.dumps(json_body) if json_body is not None else "")
        self.headers = httpx.Headers({"content-type": "application/json"})

    def json(self) -> Any:
        if self._json is None:
            raise ValueError("no JSON body")
        return self._json

    def read(self) -> bytes:
        return self.text.encode()

    def raise_for_status(self) -> None:
        if not self.is_success:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=MagicMock(), response=self  # type: ignore[arg-type]
            )


class _RouteRegistry:
    """A small router: register canned responses by ``(METHOD, path)``.

    The path match is suffix-based so tests don't have to hard-code the
    full base URL ``https://engine.talk2view.com`` — they specify just
    the endpoint (``/v1/auth/login`` etc.) and the mock pairs by suffix.
    """

    def __init__(self) -> None:
        self._routes: dict[tuple[str, str], _MockResponse] = {}
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def add(self, method: str, path: str, response: _MockResponse) -> None:
        self._routes[(method.upper(), path)] = response

    def resolve(self, method: str, url: str, body: Any) -> _MockResponse:
        method = method.upper()
        for (m, path), resp in self._routes.items():
            if m == method and url.endswith(path):
                self.calls.append((method, path, body or {}))
                return resp
        raise AssertionError(
            f"Unhandled mock request: {method} {url}\n"
            f"Registered: {sorted(self._routes)}"
        )


@pytest.fixture
def mock_routes() -> _RouteRegistry:
    """A fresh route registry per test.

    Tests register canned responses via ``mock_routes.add(...)`` and the
    ``mock_httpx_request`` fixture matches every outbound httpx call
    against this registry.
    """
    return _RouteRegistry()


@pytest.fixture
def mock_httpx_request(
    monkeypatch: pytest.MonkeyPatch,
    mock_routes: _RouteRegistry,
) -> _RouteRegistry:
    """Replace ``httpx.request`` + ``httpx.post`` with the route registry."""
    import httpx as httpx_mod

    def _request(method: str, url: str, **kwargs: Any) -> _MockResponse:
        body = kwargs.get("json")
        return mock_routes.resolve(method, url, body)

    def _post(url: str, **kwargs: Any) -> _MockResponse:
        body = kwargs.get("json")
        return mock_routes.resolve("POST", url, body)

    monkeypatch.setattr(httpx_mod, "request", _request)
    monkeypatch.setattr(httpx_mod, "post", _post)
    return mock_routes


# ---------------------------------------------------------------------------
# Streaming mock
# ---------------------------------------------------------------------------


class _MockStreamResponse:
    """``httpx.stream`` context manager + iterable that yields canned SSE."""

    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self._chunks = chunks
        self.status_code = 200
        self.is_success = True
        self.headers = httpx.Headers({"content-type": "text/event-stream"})

    def __enter__(self) -> _MockStreamResponse:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def iter_text(self) -> Iterator[str]:
        for chunk in self._chunks:
            yield f"data: {json.dumps(chunk)}\n\n"
        yield "data: [DONE]\n\n"

    def read(self) -> bytes:
        return b""


class _StreamRegistry:
    """Stack-of-scripts: one stream_request call per registered script.

    Each entry returns the script for one ``stream_request`` call, in
    registration order.

    Use ``push(chunks)`` to seed the next call's chunks. Useful for
    multi-turn tests: a tool-call interrupt cycle calls stream_request
    twice (first the agent emits an interrupt, the SDK resumes via a
    second stream_request).
    """

    def __init__(self) -> None:
        self.scripts: list[list[dict[str, Any]]] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def push(self, chunks: list[dict[str, Any]]) -> None:
        self.scripts.append(chunks)

    def pop(self, url: str, body: dict[str, Any]) -> _MockStreamResponse:
        if not self.scripts:
            raise AssertionError(
                f"Unhandled streaming call to {url}: no script left to play."
            )
        self.calls.append((url, body))
        return _MockStreamResponse(self.scripts.pop(0))


@pytest.fixture
def mock_stream() -> _StreamRegistry:
    return _StreamRegistry()


@pytest.fixture
def mock_httpx_stream(
    monkeypatch: pytest.MonkeyPatch,
    mock_stream: _StreamRegistry,
) -> _StreamRegistry:
    """Replace ``httpx.stream`` with the script registry."""
    import httpx as httpx_mod

    def _stream(method: str, url: str, **kwargs: Any) -> _MockStreamResponse:
        body = json.loads(kwargs.get("content", "{}"))
        return mock_stream.pop(url, body)

    monkeypatch.setattr(httpx_mod, "stream", _stream)
    return mock_stream


# ---------------------------------------------------------------------------
# Logged-in SDK
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_sdk(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    mock_httpx_request: _RouteRegistry,
    mock_httpx_stream: _StreamRegistry,
) -> Any:
    """Build a logged-in :class:`Talk2ViewSDKClient` against the mock engine.

    Pre-seeds the login route. Tests are expected to register session
    + chat scripts before calling ``sdk.chat(...)``.
    """
    from talk2view_writer.sdk_client import Talk2ViewSDKClient
    from talk2view_writer.storage import FileTokenStorage

    mock_httpx_request.add(
        "POST",
        "/v1/auth/login",
        _MockResponse(
            200,
            {
                "user": {
                    "id": "u-test",
                    "email": "test@example.com",
                    "user_metadata": {},
                },
                "access_token": "mock-access-token",
                "refresh_token": "mock-refresh-token",
                "expires_in": 3600,
                "expires_at": 9999999999,
                "token_type": "bearer",
            },
        ),
    )
    # Tool registration call — the extension fires this on auth change.
    mock_httpx_request.add(
        "POST",
        "/v1/tools/register",
        _MockResponse(200, {"registered": 20}),
    )

    storage = FileTokenStorage(tmp_path / "tokens.json")
    client = Talk2ViewSDKClient(storage=storage)
    client.login("test@example.com", "password")
    return client
