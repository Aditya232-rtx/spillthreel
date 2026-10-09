"""POST /v1/notifications/token — store a mobile push token.

Contract (v1):
  * JWT user only. Body {token: str 1..255, platform ∈ {expo, android, ios}}.
  * Upsert on (user_id, token) — the PushToken PK: refresh platform +
    created_at on conflict.
  * Returns {ok: true}.

Out of scope (deliberate): sending pushes — no provider is wired yet.
This store unblocks mobile's "save ready" toggle later.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.auth.middleware import CurrentUser, DbSession
from app.db.models import PushToken
from app.observability.logging import get_logger

router = APIRouter()
_logger = get_logger(__name__)

PushPlatform = Literal["expo", "android", "ios"]


class TokenRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=255)
    platform: PushPlatform


class TokenResponse(BaseModel):
    ok: bool


@router.post("/notifications/token", response_model=TokenResponse)
async def register_token(
    body: TokenRequest, user: CurrentUser, session: DbSession
) -> TokenResponse:
    now = datetime.now(UTC)
    stmt = (
        pg_insert(PushToken)
        .values(user_id=user.id, token=body.token, platform=body.platform, created_at=now)
        .on_conflict_do_update(
            index_elements=["user_id", "token"],
            set_={"platform": body.platform, "created_at": now},
        )
    )
    await session.execute(stmt)
    await session.commit()
    _logger.info("notifications.token_upserted", user_id=user.id, platform=body.platform)
    return TokenResponse(ok=True)
