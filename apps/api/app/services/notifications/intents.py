"""Durable notification intents created with their terminal domain state."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.tasks.outbox import record_task_intent


async def record_import_summary_intent(
    session: AsyncSession, *, owner_id: str, import_id: str
) -> None:
    """Queue one generic summary when an import becomes terminal."""
    await record_task_intent(
        session,
        "import_summary",
        {"user_id": owner_id, "import_id": import_id},
        target_type="import",
        target_id=import_id,
        generation=1,
    )
