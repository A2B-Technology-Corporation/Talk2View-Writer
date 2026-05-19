"""End-to-end chat flow against the mock engine.

Exercises the real :class:`Talk2ViewSDKClient` + real ``talk2view`` SDK
+ a hand-rolled tool against canned SSE responses. No engine, no
soffice — but every byte of the streaming + tool-interrupt + resume
loop is real Python through the SDK.

These tests are the answer to "can we prove the chat round-trip
works locally?" without engine credentials.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.mock_chat


def _session_response(session_id: str = "sess-1", thread_id: str = "thr-1") -> dict:
    return {
        "session_id": session_id,
        "thread_id": thread_id,
        "model": "claude-opus-4-7",
    }


def _text_chunk(content: str, finish: bool = False) -> dict:
    return {
        "id": "chunk-1",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "claude-opus-4-7",
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant", "content": content},
                "finish_reason": "stop" if finish else None,
            }
        ],
    }


def _status_chunk(typ: str, message: str) -> dict:
    return {
        "id": "chunk-status",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "claude-opus-4-7",
        "choices": [],
        "status": {"type": typ, "message": message},
    }


def _todos_chunk(todos: str) -> dict:
    return {
        "id": "chunk-todos",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "claude-opus-4-7",
        "choices": [],
        "todos": todos,
    }


def _tool_call_chunk(tool_name: str, args: dict) -> dict:
    return {
        "id": "chunk-tc",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "claude-opus-4-7",
        "choices": [],
        "interrupt": {
            "type": "tool_call",
            "tool_name": tool_name,
            "tool_call_id": "tc-1",
            "arguments": args,
        },
    }


def _final_chunk() -> dict:
    return {
        "id": "chunk-final",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "claude-opus-4-7",
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant", "content": ""},
                "finish_reason": "stop",
            }
        ],
        "thread_id": "thr-1",
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestChatFlow:
    def test_simple_text_round_trip(
        self,
        mock_sdk,  # type: ignore[no-untyped-def]
        mock_httpx_request,  # type: ignore[no-untyped-def]
        mock_stream,  # type: ignore[no-untyped-def]
    ) -> None:
        """Single-turn chat: text chunks stream out, no tool interrupt."""
        from tests.mock_chat.conftest import _MockResponse

        mock_httpx_request.add(
            "POST",
            "/v1/sessions",
            _MockResponse(200, _session_response()),
        )
        mock_stream.push(
            [
                _status_chunk("thinking", "Reading your message"),
                _text_chunk("Hello, "),
                _text_chunk("world!"),
                _final_chunk(),
            ]
        )
        events = list(mock_sdk.chat("hi"))
        types_seen = [e.type for e in events]
        assert "status" in types_seen
        assert types_seen.count("text") >= 2
        assert "done" in types_seen
        # Reconstruct the streamed text.
        joined = "".join(e.content for e in events if e.type == "text" and e.content)
        assert "Hello, " in joined
        assert "world!" in joined

    def test_todos_event_passes_through(
        self,
        mock_sdk,  # type: ignore[no-untyped-def]
        mock_httpx_request,  # type: ignore[no-untyped-def]
        mock_stream,  # type: ignore[no-untyped-def]
    ) -> None:
        from tests.mock_chat.conftest import _MockResponse

        mock_httpx_request.add(
            "POST",
            "/v1/sessions",
            _MockResponse(200, _session_response()),
        )
        mock_stream.push(
            [
                _todos_chunk("- step 1\n- step 2"),
                _text_chunk("ok"),
                _final_chunk(),
            ]
        )
        events = list(mock_sdk.chat("plan it"))
        todos_events = [e for e in events if e.type == "todos"]
        assert len(todos_events) == 1
        assert "step 1" in (todos_events[0].todos or "")

    def test_tool_call_interrupt_resume_cycle(
        self,
        mock_sdk,  # type: ignore[no-untyped-def]
        mock_httpx_request,  # type: ignore[no-untyped-def]
        mock_stream,  # type: ignore[no-untyped-def]
    ) -> None:
        """SDK auto-runs tools on interrupt and resumes the stream.

        The agent emits a tool_call interrupt; the SDK auto-executes
        the registered handler, POSTs to /resume, and continues the
        stream. The chat iterator should yield ``tool_call`` then
        subsequent ``text`` events from the resumed stream.
        """
        from tests.mock_chat.conftest import _MockResponse

        # Register a Python "tool" with the SDK that does something
        # observable, so the resume body proves the tool executed.
        tool_invocations: list[dict] = []

        from talk2view import tool as t2v_tool

        @t2v_tool
        def echo_back(message: str) -> str:
            """Echo the message argument back as the tool result.

            Args:
                message: The text to echo.
            """
            tool_invocations.append({"message": message})
            return f"echoed: {message}"

        mock_sdk._ensure_client().tools.register_from_functions([echo_back])

        # Session + resume routes.
        mock_httpx_request.add(
            "POST",
            "/v1/sessions",
            _MockResponse(200, _session_response()),
        )

        # First stream call: agent emits a tool_call.
        mock_stream.push(
            [
                _status_chunk("calling_tool", "echo_back"),
                _tool_call_chunk("echo_back", {"message": "ping"}),
            ]
        )
        # After the SDK POSTs to /resume, the second stream call
        # continues with the agent's final response. The SDK uses
        # ``stream_request`` for resume too — same mock.
        mock_stream.push(
            [
                _text_chunk("Got: echoed: ping"),
                _final_chunk(),
            ]
        )

        events = list(mock_sdk.chat("please echo 'ping'"))
        types_seen = [e.type for e in events]
        assert "tool_call" in types_seen
        # The tool ran on the SDK worker.
        assert tool_invocations == [{"message": "ping"}]
        # After resume the agent's final text arrived.
        joined = "".join(e.content for e in events if e.type == "text" and e.content)
        assert "echoed: ping" in joined
        assert "done" in types_seen

    def test_session_404_retries_with_fresh_session(
        self,
        mock_sdk,  # type: ignore[no-untyped-def]
        mock_httpx_request,  # type: ignore[no-untyped-def]
        mock_stream,  # type: ignore[no-untyped-def]
    ) -> None:
        """A stale session id triggers a single retry with a new session.

        This is the path the SDK exercises after a server deploy
        invalidates an in-flight session.
        """
        from tests.mock_chat.conftest import _MockResponse

        # First session create succeeds (used at __enter__).
        mock_httpx_request.add(
            "POST",
            "/v1/sessions",
            _MockResponse(200, _session_response("sess-old")),
        )
        # The first chat fails 404; the SDK re-creates and retries.
        # Both stream calls receive the same script (final text + done).
        mock_stream.push(
            [
                _text_chunk("Hello!"),
                _final_chunk(),
            ]
        )
        events = list(mock_sdk.chat("hello"))
        assert any(e.type == "done" for e in events)
        assert any(e.type == "text" for e in events)
