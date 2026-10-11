"""POST /v1/imports + GET /v1/imports/{id} — IG bulk-import intake.

Contract:
  POST /v1/imports (multipart `file` = Meta DYI export ZIP):
    * ≤ 50 parsed entries → parsed + processed inline, 201
      {import_id, state, total_parsed} (state `'indexing'` on success).
    * > 50 entries → ZIP staged to the `exports` Storage bucket + one
      `import` Cloud Task enqueued, 202 {import_id, state, total_parsed}
      with state `'processing'` and `total_parsed: 0` (unknown until the
      worker parses). The worker path shares `process_import` via
      `process_stored_import` — no duplicated logic.
    * Unparseable upload → 400, no imports row created.
  GET /v1/imports/{id} (JWT owner) → the imports row + counters, else 404.

The imports row is created in state `'processing'`; the pipeline flips it
to `'indexing'` once items are queued, or `'failed'` with a reason.
Terminal states (`complete` + `full_done`/`failed` attribution) are
deferred — see `ig_pipeline` for why.
"""

from __future__ import annotations

import os
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import ulid
from fastapi import APIRouter, HTTPException, Response, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select

from app.auth.middleware import CurrentUser, DbSession
from app.db.models import Import
from app.observability.logging import get_logger
from app.services.imports.ig_parser import ParserError, parse_zip
from app.services.imports.ig_pipeline import INLINE_MAX_ENTRIES, process_import
from app.services.media.supabase_storage import StorageError, upload_object
from app.settings import get_settings
from app.tasks.enqueue import enqueue_task

router = APIRouter()
_logger = get_logger(__name__)

# Reject absurd uploads before buffering the parse — exports are HTML, and
# even multi-thousand-save dumps stay well under this.
_MAX_UPLOAD_BYTES = 50 * 1024 * 1024


class ImportCreateResponse(BaseModel):
    import_id: str
    state: str
    total_parsed: int


class ImportStatusResponse(BaseModel):
    import_id: str
    user_id: str
    state: str
    total_parsed: int
    text_indexed_done: int
    full_queued: int
    full_done: int
    failed: int
    parser_schema_hash: str | None
    failure_reason: str | None
    started_at: datetime | None
    finished_at: datetime | None


@router.post("/imports", response_model=ImportCreateResponse)
async def create_import(
    response: Response,
    file: UploadFile,
    user: CurrentUser,
    session: DbSession,
) -> ImportCreateResponse:
    raw = await file.read()
    if len(raw) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="export file too large",
        )

    fd, tmp_name = tempfile.mkstemp(suffix=".zip", prefix="spill-import-upload-")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fp:
            fp.write(raw)
        try:
            parsed = parse_zip(tmp_path)
        except (ParserError, zipfile.BadZipFile, OSError, ValueError) as e:
            _logger.info("imports.rejected_bad_zip", user_id=user.id, err=str(e))
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid export zip",
            ) from e

        entry_count = (
            len(parsed.posts)
            + sum(len(coll.media) for coll in parsed.collections)
            + len(parsed.audio)
        )
        import_row = Import(id=str(ulid.new()), user_id=user.id, state="processing")
        session.add(import_row)
        await session.commit()
        _logger.info(
            "imports.created",
            import_id=import_row.id,
            user_id=user.id,
            entries=entry_count,
        )

        if entry_count <= INLINE_MAX_ENTRIES:
            outcome = await process_import(import_row.id, user.id, parsed)
            response.status_code = status.HTTP_201_CREATED
            return ImportCreateResponse(
                import_id=import_row.id,
                state="indexing" if outcome.ok else "failed",
                total_parsed=outcome.total_parsed,
            )

        storage_key = f"{user.id}/{import_row.id}.zip"
        try:
            await upload_object(
                get_settings().supabase_bucket_exports,
                storage_key,
                tmp_path,
                "application/zip",
            )
        except StorageError as e:
            import_row.state = "failed"
            import_row.failure_reason = f"export_upload_failed: {e}"[:500]
            import_row.finished_at = datetime.now(UTC)
            await session.commit()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="export staging failed",
            ) from e
        await enqueue_task(
            "import",
            {
                "import_id": import_row.id,
                "user_id": user.id,
                "storage_key": storage_key,
            },
            dedup_key=f"import-{import_row.id}",
        )
        _logger.info(
            "imports.enqueued",
            import_id=import_row.id,
            user_id=user.id,
            storage_key=storage_key,
        )
        response.status_code = status.HTTP_202_ACCEPTED
        return ImportCreateResponse(import_id=import_row.id, state="processing", total_parsed=0)
    finally:
        tmp_path.unlink(missing_ok=True)


@router.get("/imports/{import_id}", response_model=ImportStatusResponse)
async def get_import(import_id: str, user: CurrentUser, session: DbSession) -> ImportStatusResponse:
    stmt = select(Import).where(Import.id == import_id, Import.user_id == user.id)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return ImportStatusResponse(
        import_id=row.id,
        user_id=row.user_id,
        state=row.state,
        total_parsed=row.total_parsed,
        text_indexed_done=row.text_indexed_done,
        full_queued=row.full_queued,
        full_done=row.full_done,
        failed=row.failed,
        parser_schema_hash=row.parser_schema_hash,
        failure_reason=row.failure_reason,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )
