"""FastAPI dependency that verifies the Firebase ID token and returns the
signed-in user, upserting a `users` row on first login.

Usage:
    @router.get("/some")
    async def handler(user: CurrentUser, session: DbSession) -> ...:
        # `user.id` is our internal ULID; `user.firebase_uid` is Firebase's.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from app.auth.firebase import FirebaseAuthError, verify_id_token
from app.db.engine import get_session
from app.db.models import User
from app.observability.logging import get_logger

_logger = get_logger(__name__)

DbSession = Annotated[AsyncSession, Depends(get_session)]


async def _current_user(
    authorization: Annotated[str | None, Header()] = None,
    session: DbSession = ...,  # type: ignore[assignment]
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.split(" ", 1)[1].strip()

    try:
        claims = await verify_id_token(token)
    except FirebaseAuthError as e:
        _logger.warning("auth.token_invalid", reason=str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e

    firebase_uid = claims["uid"]
    email = claims.get("email")
    display_name = claims.get("name")

    result = await session.execute(select(User).where(User.firebase_uid == firebase_uid))
    user = result.scalar_one_or_none()

    if user is None:
        user = User(
            id=str(ULID()),
            firebase_uid=firebase_uid,
            email=email,
            display_name=display_name,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        _logger.info("auth.user_created", user_id=user.id, firebase_uid=firebase_uid)
    elif user.deleted_at is not None:
        # Soft-deleted account still owns this Firebase UID — refuse.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="account deleted"
        )

    return user


CurrentUser = Annotated[User, Depends(_current_user)]
