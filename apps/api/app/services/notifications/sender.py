"""Send a small, privacy-preserving push summary for a completed import.

Provider delivery is at-least-once: if Expo accepts a request but its response
is lost, a durable caller retry can send the same summary again. The payload is
therefore only a hint to refetch the import's authenticated server state.
"""

from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy import delete, select

from app.db.engine import session_scope
from app.db.models import Import, Profile, PushToken

_EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
_MAX_BATCH_SIZE = 100
_REQUEST_TIMEOUT_SECONDS = 10.0


async def send_import_summary(
    owner_id: str,
    import_id: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Send one generic summary to each current push token for this owner.

    Returns ``sent`` when at least one ticket is accepted, ``no_tokens`` when
    the import is valid and terminal but the owner has no usable tokens, and
    ``stale`` when the import/owner is missing, inactive, or not terminal.
    Provider/network/response errors are raised so a durable caller can retry.
    """
    async with session_scope() as session:
        row = (
            await session.execute(
                select(Import.state)
                .join(Profile, Profile.id == Import.user_id)
                .where(
                    Import.id == import_id,
                    Import.user_id == owner_id,
                    Profile.deleted_at.is_(None),
                )
            )
        ).first()
        if row is None or row[0] not in {"complete", "failed"}:
            return "stale"

        tokens: list[str] = list(
            (
                await session.scalars(
                    select(PushToken.token).where(PushToken.user_id == owner_id)
                )
            ).all()
        )

    if not tokens:
        return "no_tokens"

    message_base: dict[str, Any] = {
        "title": "Import finished",
        "body": "Open SpillTheReel to see your import summary.",
        "data": {
            "type": "import_summary",
            "owner_id": owner_id,
            "import_id": import_id,
        },
    }
    own_client = client is None
    http_client = client or httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS)
    accepted = False
    invalid_tokens: list[str] = []

    try:
        for offset in range(0, len(tokens), _MAX_BATCH_SIZE):
            batch = tokens[offset : offset + _MAX_BATCH_SIZE]
            response = await http_client.post(
                _EXPO_PUSH_URL,
                json=[{**message_base, "to": token} for token in batch],
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            payload = response.json()
            tickets = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(tickets, list) or len(tickets) != len(batch):
                raise RuntimeError("Expo push response did not contain one ticket per message")

            retryable_errors = 0
            for token, ticket in zip(batch, tickets, strict=True):
                if not isinstance(ticket, dict) or ticket.get("status") not in {"ok", "error"}:
                    raise RuntimeError("Expo push response contained an invalid ticket")
                if ticket["status"] == "ok":
                    if not isinstance(ticket.get("id"), str) or not ticket["id"]:
                        raise RuntimeError("Expo success ticket did not contain an id")
                    accepted = True
                    continue

                details = ticket.get("details")
                error = details.get("error") if isinstance(details, dict) else None
                if error == "DeviceNotRegistered":
                    invalid_tokens.append(token)
                else:
                    # Provider text is untrusted and must not flow to worker
                    # logs or exception reports. Keep only a bounded count.
                    retryable_errors += 1

            # Remove revoked tokens as soon as their provider ticket is known.
            # Scope by owner as well as token to prevent cross-account deletes.
            if invalid_tokens:
                async with session_scope() as session:
                    await session.execute(
                        delete(PushToken).where(
                            PushToken.user_id == owner_id,
                            PushToken.token.in_(invalid_tokens),
                        )
                    )
                    await session.commit()
                invalid_tokens.clear()

            if retryable_errors:
                raise RuntimeError("Expo push ticket delivery failed")
    finally:
        if own_client:
            await http_client.aclose()

    return "sent" if accepted else "no_tokens"
