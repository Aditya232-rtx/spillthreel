"""GET/DELETE /v1/me — profile fetch + account deletion.

Deletion is async: the endpoint soft-flags `users.deleted_at`, then
enqueues a `delete_user` task that cascades to Cognee, GCS, and
Firebase Auth (see architecture.md §4.5).
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
    email: str | None
    display_name: str | None


@router.get("/me", response_model=MeResponse)
async def get_me(user: CurrentUser) -> MeResponse:
    return MeResponse(id=user.id, email=user.email, display_name=user.display_name)


@router.delete("/me", status_code=status.HTTP_202_ACCEPTED)
async def delete_me(user: CurrentUser, session: DbSession) -> dict[str, str]:
    user.deleted_at = datetime.now(UTC)
    await session.commit()
    await enqueue_task("delete_user", {"user_id": user.id})
    _logger.info("users.delete_scheduled", user_id=user.id)
    return {"status": "scheduled"}
