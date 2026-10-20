"""FastAPI dependency that verifies the Supabase JWT and returns the
signed-in user, upserting a `profiles` row on first login.

Usage:
    @router.get("/some")
    async def handler(user: CurrentUser, session: DbSession) -> ...:
        # `user.id` is the Supabase auth.users(id) UUID — same value on
        # all downstream tables' user_id columns.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.supabase_auth import SupabaseAuthError, verify_access_token
from app.db.engine import get_session
from app.db.models import Profile, UserDeletionJob
from app.observability.logging import get_logger

_logger = get_logger(__name__)

DbSession = Annotated[AsyncSession, Depends(get_session)]


async def _current_user(
    authorization: Annotated[str | None, Header()] = None,
    session: DbSession = ...,  # type: ignore[assignment]
) -> Profile:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.split(" ", 1)[1].strip()

    try:
        claims = verify_access_token(token)
    except SupabaseAuthError as e:
        _logger.warning("auth.token_invalid", reason=str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e

    user_id: str = claims["sub"]
    email: str | None = claims.get("email")
    display_name: str | None = (
        claims.get("user_metadata", {}).get("full_name")
        or claims.get("user_metadata", {}).get("name")
        or email
    )

    # Set RLS claim on this transaction so any RLS policies (`auth.uid()
    # = user_id`) fire naturally on subsequent queries — defense-in-depth
    # even though the backend connects with the service-role key, which
    # normally bypasses RLS. See TRD §15.3.
    await session.execute(
        text("SELECT set_config('request.jwt.claim.sub', :sub, true)"),
        {"sub": user_id},
    )

    result = await session.execute(select(Profile).where(Profile.id == user_id))
    profile = result.scalar_one_or_none()

    if profile is None:
        # A completed or in-progress deletion tombstone must outlive the
        # profile cascade. Existing JWTs may remain valid briefly after Auth
        # Admin deletion, and must never recreate the account's profile.
        deletion = await session.execute(
            select(UserDeletionJob.user_id)
            .where(UserDeletionJob.user_id == user_id)
            .limit(1)
        )
        if deletion.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "account_deleted", "message": "account deleted"},
            )
        # First login for this UUID — Supabase already inserted the
        # auth.users row on OAuth callback; we mirror our profile row.
        # (In prod, the DB trigger `on_auth_user_created` handles this
        # automatically; this upsert is the belt-and-suspenders path
        # for local dev where the trigger may not be installed yet.)
        profile = Profile(id=user_id, display_name=display_name)
        session.add(profile)
        await session.commit()
        await session.refresh(profile)
        _logger.info("auth.profile_created", user_id=user_id)
    else:
        if profile.deleted_at is not None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "account_deleted", "message": "account deleted"},
            )
        # Keep the DB mirror in sync with user_metadata: renames from
        # signup / your-name / profile-edit / OAuth re-apply arrive here
        # as claims on the next authenticated call. Only write when the
        # value actually changed.
        if display_name and profile.display_name != display_name:
            profile.display_name = display_name
            await session.commit()
            _logger.info("auth.profile_renamed", user_id=user_id)

    return profile


CurrentUser = Annotated[Profile, Depends(_current_user)]
