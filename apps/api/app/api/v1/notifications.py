"""Register, rotate, and unregister a mobile push token.

Contract (v1):
  * JWT user only. Body {token: str 1..255, platform ∈ {expo, android, ios}}.
  * Upsert on globally unique token: the authenticated active owner becomes
    the sole owner when a device token is reassigned.
  * Optional `previous_token` removes a rotated token for this same user.
  * POST /token/unregister accepts {token}; removing a missing token is idempotent.

Out of scope (deliberate): sending pushes — no provider is wired yet.
Push delivery remains a separate task.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.auth.middleware import CurrentUser, DbSession
from app.db.models import Profile, PushToken
from app.observability.logging import get_logger

router = APIRouter()
_logger = get_logger(__name__)

PushPlatform = Literal["expo", "android", "ios"]


class TokenRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=255)
    platform: PushPlatform
    previous_token: str | None = Field(default=None, min_length=1, max_length=255)


class TokenUnregisterRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=255)


class TokenResponse(BaseModel):
    ok: bool


async def _lock_active_profile(user_id: str, session: DbSession) -> None:
    """Fence token mutations against concurrent account deletion.

    FOR SHARE serializes with the profile update used by the deletion route,
    keeping the account active through the token mutation's commit.
    """
    profile_id = await session.scalar(
        select(Profile.id)
        .where(Profile.id == user_id, Profile.deleted_at.is_(None))
        .with_for_update(read=True)
    )
    if profile_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "account_deleted", "message": "account deleted"},
        )


@router.post("/notifications/token", response_model=TokenResponse)
async def register_token(
    body: TokenRequest, user: CurrentUser, session: DbSession
) -> TokenResponse:
    await _lock_active_profile(user.id, session)
    now = datetime.now(UTC)
    if body.previous_token and body.previous_token != body.token:
        await session.execute(
            delete(PushToken).where(
                PushToken.user_id == user.id,
                PushToken.token == body.previous_token,
            )
        )
    stmt = (
        pg_insert(PushToken)
        .values(user_id=user.id, token=body.token, platform=body.platform, created_at=now)
        .on_conflict_do_update(
            index_elements=["token"],
            set_={
                "user_id": user.id,
                "platform": body.platform,
                "created_at": now,
            },
            where=PushToken.user_id == user.id,
        )
        .returning(PushToken.user_id, PushToken.token)
    )
    result = await session.execute(stmt)
    if result.scalar_one_or_none() is None:
        # A globally unique token already belongs to another account. Do not
        # steal it, and roll back any same-owner previous-token cleanup above.
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "push_token_owned_by_another_account",
                "message": "push token is already registered to another account",
            },
        )
    await session.commit()
    _logger.info("notifications.token_upserted", user_id=user.id, platform=body.platform)
    return TokenResponse(ok=True)


@router.post("/notifications/token/unregister", response_model=TokenResponse)
async def unregister_token(
    body: TokenUnregisterRequest, user: CurrentUser, session: DbSession
) -> TokenResponse:
    """Remove this user's token; repeated logout/unregister calls are safe."""
    await _lock_active_profile(user.id, session)
    await session.execute(
        delete(PushToken).where(
            PushToken.user_id == user.id,
            PushToken.token == body.token,
        )
    )
    await session.commit()
    _logger.info("notifications.token_unregistered", user_id=user.id)
    return TokenResponse(ok=True)
