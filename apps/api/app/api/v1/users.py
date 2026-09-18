"""GET/DELETE /v1/me — profile fetch + account deletion.

Deletion is async: the endpoint soft-flags `profiles.deleted_at`, then
enqueues a `delete_user` task. The task calls Supabase Admin API's
`auth.admin.deleteUser(id)` which cascades to `profiles` (FK ON DELETE
CASCADE), which in turn cascades to every user-scoped table. It also
purges the user's Cognee namespace and the `media/{user_id}/` prefix in
Supabase Storage (see architecture.md §4.5).
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, status
from pydantic import BaseModel

from app.auth.middleware import CurrentUser, DbSession
from app.observability.logging import get_logger
from app.tasks.enqueue import enqueue_task

router = APIRouter()
_logger = get_logger(__name__)


class MeResponse(BaseModel):
    id: str
    display_name: str | None
    avatar_url: str | None


@router.get("/me", response_model=MeResponse)
async def get_me(user: CurrentUser) -> MeResponse:
    # `email` lives on Supabase's auth.users, not our profiles mirror.
    # If the client needs it, read from the JWT claims client-side.
    return MeResponse(id=user.id, display_name=user.display_name, avatar_url=user.avatar_url)


@router.delete("/me", status_code=status.HTTP_202_ACCEPTED)
async def delete_me(user: CurrentUser, session: DbSession) -> dict[str, str]:
    user.deleted_at = datetime.now(UTC)
    await session.commit()
    await enqueue_task("delete_user", {"user_id": user.id})
    _logger.info("users.delete_scheduled", user_id=user.id)
    return {"status": "scheduled"}
