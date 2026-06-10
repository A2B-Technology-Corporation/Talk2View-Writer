"""Unit tests for FormData / multipart proxying (investigations #59).

The SDK's speech-to-text upload sends ``multipart/form-data`` (an audio
``file`` blob + a ``model`` field). bridge.ts serialises that into a
sentinel JSON envelope; ``_decode_multipart_envelope`` /
``_proxy_fetch`` rebuild a real multipart httpx request from it.
"""

from __future__ import annotations

import base64
import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from talk2view_writer.bridge_server import BridgeServer, _decode_multipart_envelope

pytestmark = pytest.mark.unit


def _envelope(*, fields: list[dict[str, str]], files: list[dict[str, str]]) -> str:
    return json.dumps({"__t2v_multipart__": True, "fields": fields, "files": files})


class TestDecodeMultipartEnvelope:
    def test_plain_json_string_is_not_multipart(self) -> None:
        assert _decode_multipart_envelope(json.dumps({"messages": []})) is None

    def test_none_is_not_multipart(self) -> None:
        assert _decode_multipart_envelope(None) is None

    def test_non_sentinel_dict_is_not_multipart(self) -> None:
        # Mentions "file" but lacks the sentinel — must take the content path.
        assert _decode_multipart_envelope(json.dumps({"file": "x"})) is None

    def test_decodes_fields_and_files(self) -> None:
        audio = b"\x00\x01RIFF...fake-audio"
        result = _decode_multipart_envelope(
            _envelope(
                fields=[{"name": "model", "value": "whisper-1"}],
                files=[
                    {
                        "name": "file",
                        "filename": "audio.webm",
                        "type": "audio/webm",
                        "b64": base64.b64encode(audio).decode(),
                    }
                ],
            )
        )
        assert result is not None
        data, files = result
        assert data == {"model": "whisper-1"}
        assert len(files) == 1
        name, (filename, content, ctype) = files[0]
        assert name == "file"
        assert filename == "audio.webm"
        assert content == audio  # base64 round-trips back to the exact bytes
        assert ctype == "audio/webm"

    def test_file_metadata_defaults_when_missing(self) -> None:
        result = _decode_multipart_envelope(
            _envelope(
                fields=[],
                files=[{"name": "file", "b64": base64.b64encode(b"x").decode()}],
            )
        )
        assert result is not None
        _data, files = result
        _name, (filename, content, ctype) = files[0]
        assert filename == "blob"
        assert ctype == "application/octet-stream"
        assert content == b"x"


class _FakeResp:
    def __init__(self) -> None:
        self.status_code = 200
        self.reason_phrase = "OK"
        self.headers = {"content-type": "application/json"}
        self.content = b'{"text": "hi"}'
        self.text = '{"text": "hi"}'


class _FakeClient:
    def __init__(self, captured: dict[str, Any]) -> None:
        self._captured = captured

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *_a: Any) -> None:
        return None

    def request(self, **kwargs: Any) -> _FakeResp:
        self._captured.update(kwargs)
        return _FakeResp()


def _patch_httpx_client(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    import httpx

    captured: dict[str, Any] = {}
    monkeypatch.setattr(httpx, "Client", lambda *_a, **_k: _FakeClient(captured))
    return captured


class TestProxyFetchMultipart:
    def test_multipart_body_uses_files_and_strips_content_type(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = _patch_httpx_client(monkeypatch)
        srv = BridgeServer(ctx=MagicMock(name="ctx"))
        result = srv._proxy_fetch(
            "https://engine.talk2view.com/v1/audio/transcriptions",
            "POST",
            {"Content-Type": "multipart/form-data", "Authorization": "Bearer x"},
            _envelope(
                fields=[{"name": "model", "value": "whisper-1"}],
                files=[
                    {
                        "name": "file",
                        "filename": "a.webm",
                        "type": "audio/webm",
                        "b64": base64.b64encode(b"AUDIO").decode(),
                    }
                ],
            ),
        )
        assert result["status"] == 200
        # Multipart path: data + files passed; NO raw content.
        assert captured.get("data") == {"model": "whisper-1"}
        assert len(captured.get("files", [])) == 1
        assert "content" not in captured
        # Client content-type stripped so httpx sets the boundary itself.
        assert all(k.lower() != "content-type" for k in captured["headers"])
        assert captured["headers"].get("Authorization") == "Bearer x"

    def test_json_body_still_uses_content_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = _patch_httpx_client(monkeypatch)
        srv = BridgeServer(ctx=MagicMock(name="ctx"))
        srv._proxy_fetch(
            "https://engine.talk2view.com/v1/sessions",
            "POST",
            {"Content-Type": "application/json"},
            '{"messages": []}',
        )
        assert captured.get("content") == b'{"messages": []}'
        assert "files" not in captured
        # Ordinary requests keep their content-type.
        assert captured["headers"].get("Content-Type") == "application/json"
