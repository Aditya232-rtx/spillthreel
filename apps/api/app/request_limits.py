"""ASGI ingress limits for request bodies that are parsed before route code."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from starlette.responses import JSONResponse

from app.settings import get_settings

ASGIMessage = dict[str, Any]
ASGIReceive = Callable[[], Awaitable[ASGIMessage]]
ASGISend = Callable[[ASGIMessage], Awaitable[None]]

_IMPORT_MULTIPART_OVERHEAD_BYTES = 1024 * 1024


class _BodyLimitExceeded(Exception):
    pass


class ImportRequestBodyLimitMiddleware:
    """Cap multipart bytes before Starlette spools `UploadFile` to disk."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: ASGIReceive, send: ASGISend) -> None:
        if not (
            scope.get("type") == "http"
            and scope.get("method") == "POST"
            and scope.get("path") in {
                "/v1/imports",
                "/v1/imports/",
                "/v1/imports/previews",
            }
        ):
            await self.app(scope, receive, send)
            return

        max_body_bytes = (
            get_settings().import_max_upload_bytes + _IMPORT_MULTIPART_OVERHEAD_BYTES
        )
        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length")
        if content_length:
            try:
                if int(content_length) > max_body_bytes:
                    await _send_too_large(scope, receive, send)
                    return
            except ValueError:
                pass

        consumed = 0

        async def limited_receive() -> ASGIMessage:
            nonlocal consumed
            message = await receive()
            if message.get("type") == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > max_body_bytes:
                    raise _BodyLimitExceeded
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _BodyLimitExceeded:
            await _send_too_large(scope, receive, send)


async def _send_too_large(scope: dict[str, Any], receive: ASGIReceive, send: ASGISend) -> None:
    response = JSONResponse(
        status_code=413,
        content={"detail": {"code": "import_request_body_too_large"}},
    )
    await response(scope, receive, send)
