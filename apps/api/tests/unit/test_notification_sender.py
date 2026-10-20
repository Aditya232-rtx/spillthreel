from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from sqlalchemy.dialects import postgresql

from app.db.models import PushToken
from app.services.notifications import sender

OWNER_ID = "00000000-0000-0000-0000-000000000001"
IMPORT_ID = "imp_01HXYZ"


@dataclass
class _Result:
    row: tuple[str] | None

    def first(self) -> tuple[str] | None:
        return self.row


class _Session:
    def __init__(self, *, state: str | None, tokens: list[str]) -> None:
        self.state = state
        self.tokens = tokens
        self.statements: list[Any] = []
        self.commits = 0

    async def execute(self, statement: Any) -> _Result:
        self.statements.append(statement)
        if "imports" in str(statement):
            return _Result((self.state,) if self.state is not None else None)
        return _Result(None)

    async def scalars(self, _statement: Any) -> Any:
        class _Scalars:
            def all(inner_self: Any) -> list[str]:
                return list(self.tokens)

        return _Scalars()

    async def commit(self) -> None:
        self.commits += 1


class _Scope:
    def __init__(self, session: _Session) -> None:
        self.session = session

    async def __aenter__(self) -> _Session:
        return self.session

    async def __aexit__(self, *_args: object) -> None:
        return None


class _Response:
    def __init__(self, payload: Any, *, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("provider failed", request=None, response=None)  # type: ignore[arg-type]

    def json(self) -> Any:
        return self.payload


class _Client:
    def __init__(
        self,
        responses: list[_Response] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.responses = responses or []
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def post(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append({"url": url, **kwargs})
        if self.error is not None:
            raise self.error
        return self.responses.pop(0)


def _install_session(monkeypatch: pytest.MonkeyPatch, session: _Session) -> None:
    monkeypatch.setattr(sender, "session_scope", lambda: _Scope(session))


def _ok_tickets(count: int) -> dict[str, Any]:
    return {"data": [{"status": "ok", "id": f"ticket-{i}"} for i in range(count)]}


@pytest.mark.asyncio
async def test_terminal_import_sends_generic_payload_in_batches_of_100(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session(state="complete", tokens=[f"ExpoToken-{i}" for i in range(205)])
    _install_session(monkeypatch, session)
    client = _Client([_Response(_ok_tickets(n)) for n in (100, 100, 5)])

    result = await sender.send_import_summary(OWNER_ID, IMPORT_ID, client=client)  # type: ignore[arg-type]

    assert result == "sent"
    assert [len(call["json"]) for call in client.calls] == [100, 100, 5]
    for call in client.calls:
        assert call["url"] == "https://exp.host/--/api/v2/push/send"
        assert call["timeout"] == 10.0
        for message in call["json"]:
            assert message["data"] == {
                "type": "import_summary",
                "owner_id": OWNER_ID,
                "import_id": IMPORT_ID,
            }
            assert set(message["data"]) == {"type", "owner_id", "import_id"}
            assert "item_id" not in message and "item" not in message


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [None, "processing", "indexing"])
async def test_missing_or_nonterminal_import_is_stale(
    monkeypatch: pytest.MonkeyPatch, state: str | None
) -> None:
    session = _Session(state=state, tokens=["token"])
    _install_session(monkeypatch, session)
    client = _Client()

    assert await sender.send_import_summary(OWNER_ID, IMPORT_ID, client=client) == "stale"  # type: ignore[arg-type]
    assert client.calls == []


@pytest.mark.asyncio
async def test_terminal_import_without_tokens_returns_no_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_session(monkeypatch, _Session(state="failed", tokens=[]))
    client = _Client()

    assert await sender.send_import_summary(OWNER_ID, IMPORT_ID, client=client) == "no_tokens"  # type: ignore[arg-type]
    assert client.calls == []


@pytest.mark.asyncio
async def test_device_not_registered_token_is_deleted_for_this_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session(state="complete", tokens=["invalid", "valid"])
    _install_session(monkeypatch, session)
    client = _Client(
        [
            _Response(
                {
                    "data": [
                        {
                            "status": "error",
                            "message": "gone",
                            "details": {"error": "DeviceNotRegistered"},
                        },
                        {"status": "ok", "id": "ticket-valid"},
                    ]
                }
            )
        ]
    )

    assert await sender.send_import_summary(OWNER_ID, IMPORT_ID, client=client) == "sent"  # type: ignore[arg-type]
    assert session.commits == 1
    delete_sql = str(session.statements[-1].compile(dialect=postgresql.dialect()))
    assert "push_tokens.user_id" in delete_sql
    assert "push_tokens.token" in delete_sql
    compiled = str(
        session.statements[-1].compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert OWNER_ID.replace("-", "") in compiled


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"data": []},
        {"data": [{"status": "unknown"}]},
        {"data": [{"status": "ok"}]},
        {"data": [{"status": "error", "details": {"error": "MessageRateExceeded"}}]},
    ],
)
async def test_invalid_or_retryable_provider_response_raises(
    monkeypatch: pytest.MonkeyPatch, payload: Any
) -> None:
    _install_session(monkeypatch, _Session(state="complete", tokens=["token"]))
    client = _Client([_Response(payload)])

    with pytest.raises(RuntimeError):
        await sender.send_import_summary(OWNER_ID, IMPORT_ID, client=client)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_provider_http_failure_raises_for_durable_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_session(monkeypatch, _Session(state="complete", tokens=["token"]))
    client = _Client([_Response({}, status_code=503)])

    with pytest.raises(httpx.HTTPStatusError):
        await sender.send_import_summary(OWNER_ID, IMPORT_ID, client=client)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_network_failure_is_propagated_for_durable_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_session(monkeypatch, _Session(state="complete", tokens=["token"]))
    client = _Client(error=httpx.ConnectError("connection failed"))

    with pytest.raises(httpx.ConnectError):
        await sender.send_import_summary(OWNER_ID, IMPORT_ID, client=client)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_invalid_token_cleanup_is_committed_before_retryable_ticket_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session(state="complete", tokens=["invalid", "rate-limited"])
    _install_session(monkeypatch, session)
    client = _Client(
        [
            _Response(
                {
                    "data": [
                        {"status": "error", "details": {"error": "DeviceNotRegistered"}},
                        {"status": "error", "details": {"error": "MessageRateExceeded"}},
                    ]
                }
            )
        ]
    )

    with pytest.raises(RuntimeError, match="delivery failed") as exc_info:
        await sender.send_import_summary(OWNER_ID, IMPORT_ID, client=client)  # type: ignore[arg-type]
    assert "MessageRateExceeded" not in str(exc_info.value)
    assert session.commits == 1
    assert "push_tokens.user_id" in str(session.statements[-1])
