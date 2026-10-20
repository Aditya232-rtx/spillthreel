from __future__ import annotations

from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

import pytest

import app.request_limits as request_limits
from app.request_limits import ImportRequestBodyLimitMiddleware


def _scope(headers: list[tuple[bytes, bytes]]) -> dict[str, Any]:
    return {
        "type": "http",
        "method": "POST",
        "path": "/v1/imports",
        "headers": headers,
        "asgi": {"version": "3.0"},
    }


def _scope_with_path(path: str, headers: list[tuple[bytes, bytes]]) -> dict[str, Any]:
    scope = _scope(headers)
    scope["path"] = path
    return scope


@pytest.mark.asyncio
async def test_declared_oversized_import_body_is_rejected_before_route(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(request_limits, "get_settings", lambda: SimpleNamespace(import_max_upload_bytes=8))
    monkeypatch.setattr(request_limits, "_IMPORT_MULTIPART_OVERHEAD_BYTES", 4)
    route_called = False

    async def route(_scope, _receive, _send) -> None:
        nonlocal route_called
        route_called = True

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    sent: list[dict[str, Any]] = []
    await ImportRequestBodyLimitMiddleware(route)(
        _scope([(b"content-length", b"13")]), receive, lambda message: _append(sent, message)
    )

    assert not route_called
    assert sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_trailing_slash_import_alias_is_also_body_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(request_limits, "get_settings", lambda: SimpleNamespace(import_max_upload_bytes=8))
    monkeypatch.setattr(request_limits, "_IMPORT_MULTIPART_OVERHEAD_BYTES", 4)
    route_called = False

    async def route(_scope, _receive, _send) -> None:
        nonlocal route_called
        route_called = True

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    sent: list[dict[str, Any]] = []
    await ImportRequestBodyLimitMiddleware(route)(
        _scope_with_path("/v1/imports/", [(b"content-length", b"13")]),
        receive,
        lambda message: _append(sent, message),
    )

    assert not route_called
    assert [message.get("status") for message in sent if message["type"] == "http.response.start"] == [413]


@pytest.mark.asyncio
async def test_preview_upload_body_is_limited_before_multipart_spooling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(request_limits, "get_settings", lambda: SimpleNamespace(import_max_upload_bytes=8))
    monkeypatch.setattr(request_limits, "_IMPORT_MULTIPART_OVERHEAD_BYTES", 4)
    route_called = False

    async def route(_scope, _receive, _send) -> None:
        nonlocal route_called
        route_called = True

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    sent: list[dict[str, Any]] = []
    await ImportRequestBodyLimitMiddleware(route)(
        _scope_with_path("/v1/imports/previews", [(b"content-length", b"13")]),
        receive,
        lambda message: _append(sent, message),
    )

    assert not route_called
    assert [message.get("status") for message in sent if message["type"] == "http.response.start"] == [413]


@pytest.mark.asyncio
async def test_streaming_import_body_overflow_stops_before_route_consumes_more(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(request_limits, "get_settings", lambda: SimpleNamespace(import_max_upload_bytes=8))
    monkeypatch.setattr(request_limits, "_IMPORT_MULTIPART_OVERHEAD_BYTES", 4)
    messages = iter(
        [
            {"type": "http.request", "body": b"1234567", "more_body": True},
            {"type": "http.request", "body": b"123456", "more_body": True},
        ]
    )
    consumed = 0

    async def receive() -> dict[str, Any]:
        nonlocal consumed
        consumed += 1
        return next(messages)

    async def route(_scope, wrapped_receive, _send) -> None:
        await wrapped_receive()
        await wrapped_receive()

    sent: list[dict[str, Any]] = []
    await ImportRequestBodyLimitMiddleware(route)(
        _scope([]), receive, lambda message: _append(sent, message)
    )

    assert consumed == 2
    assert sent[0]["status"] == 413


async def _append(target: list[dict[str, Any]], message: dict[str, Any]) -> None:
    target.append(message)
