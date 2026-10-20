"""Preview/commit bulk-import API and owner-scoped import status.

Contract:
  POST /v1/imports (legacy route):
    * Retired with 410 `preview_required`; it has no upload/body parameter.
      Clients must create a preview, choose candidates, then commit explicitly.
  POST /v1/imports/previews (multipart `file` = Meta export ZIP):
    * Creates a bounded, expiring preview without admission or ingestion.
  POST /v1/imports/previews/{id}/commit:
    * Validated and admitted exports are staged to the `exports` Storage bucket;
      the import row, reservations and durable `import` outbox intent commit
      together, then dispatch is attempted after commit (202).
    * The worker path shares `process_import` via `process_stored_import` — no
      request-local parsed source is required for recovery.
    * Unparseable upload → 400, no imports row created.
  GET /v1/imports/{id} (JWT owner) → the imports row + counters, else 404.

The legacy handler remains unmounted for focused regression coverage while the
preview/commit API is the only public route for bulk archive admission.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import concurrent.futures
import hashlib
import json
import os
import tempfile
import threading
import uuid
import zipfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

import ulid
from fastapi import APIRouter, Header, HTTPException, Query, Response, UploadFile, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, async_sessionmaker

from app.auth.middleware import CurrentUser, DbSession
from app.db.models import (
    Import,
    ImportEntry,
    ImportPreview,
    ImportPreviewCandidate,
    ImportStaging,
    IngestionEvent,
    Item,
    Profile,
)
from app.observability.logging import get_logger
from app.services.imports.ig_parser import ArchiveLimitError, ParsedExport, ParserError, parse_zip
from app.services.imports.recovery import (
    ImportCommitReconciliation,
    STAGING_LEASE_SECONDS,
    create_import_staging_marker,
    lock_import_staging_for_admission,
    mark_import_staging_cleanup_pending,
    reconcile_import_commit,
    record_import_processing_intent,
    sweep_import_staging,
)
from app.services.imports.admission import (
    AdmissionCandidate,
    AdmissionPolicy,
    AdmissionPolicyNotConfigured,
    AdmissionProfileUnavailable,
    AdmissionStatus,
    AdmissionTier,
    AllowanceSnapshot,
    EligibilityMode,
    PremiumHistoryMode,
    SourceBucket,
    admit_bulk_import,
    get_bulk_allowance,
    reservation_key_for_url,
    saved_audio_identity_url,
)
from app.services.imports.entries import derive_import_progress
from app.services.imports.previews import build_preview_candidates
from app.services.ingest.platform_detect import (
    UnsupportedPlatformError,
    detect_platform,
    normalize_url,
)
from app.services.media.supabase_storage import StorageError, upload_object
from app.settings import get_settings
from app.tasks.outbox import dispatch_after_commit, next_generation, record_task_intent

router = APIRouter()
_logger = get_logger(__name__)

# Reject absurd uploads before buffering the parse — exports are HTML, and
# even multi-thousand-save dumps stay well under this.
_STAGING_UPLOAD_DEADLINE_SECONDS = 35
_MAX_IMPORT_ENTRY_RETRIES = 3
_MAX_IMPORT_CURSOR_CHARS = 512
_MAX_PREVIEW_CURSOR_CHARS = 256
_MAX_CONCURRENT_ARCHIVE_PARSERS = 2
_ARCHIVE_PARSER_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=_MAX_CONCURRENT_ARCHIVE_PARSERS,
    thread_name_prefix="spill-import-parser",
)
_ARCHIVE_PARSER_SLOTS = threading.BoundedSemaphore(_MAX_CONCURRENT_ARCHIVE_PARSERS)
_STANDALONE_INSTAGRAM_EXPORT_NAME = "saved_posts.html"


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


class ImportAllowanceResponse(BaseModel):
    tier: str
    policy_version: str
    total_limit: int
    total_used: int
    total_remaining: int
    instagram_limit: int
    instagram_used: int
    instagram_remaining: int
    other_limit: int
    other_used: int
    other_remaining: int


class ImportsPage(BaseModel):
    imports: list[ImportStatusResponse]
    next_cursor: str | None


class ImportEntryStatus(BaseModel):
    reservation_key: str
    state: str
    source_bucket: str
    item_id: str | None
    last_error_code: str | None
    work_queued_at: datetime | None
    created_at: datetime


class ImportEntriesPage(BaseModel):
    entries: list[ImportEntryStatus]
    next_cursor: str | None


class ImportPreviewCreateResponse(BaseModel):
    preview_id: str
    expires_at: datetime
    total_candidates: int


class ImportPreviewCandidateResponse(BaseModel):
    candidate_id: str
    ordinal: int
    kind: str
    source_bucket: str
    url: str | None
    title: str | None
    artist: str | None
    saved_at: datetime | None
    collection_names: list[str]


class ImportPreviewCandidatesPage(BaseModel):
    candidates: list[ImportPreviewCandidateResponse]
    next_cursor: str | None


class ImportPreviewCommitRequest(BaseModel):
    candidate_ids: list[uuid.UUID] = Field(min_length=1, max_length=1000)

    @field_validator("candidate_ids")
    @classmethod
    def reject_duplicate_candidate_ids(cls, values: list[uuid.UUID]) -> list[uuid.UUID]:
        if len(set(values)) != len(values):
            raise ValueError("candidate_ids must not contain duplicates")
        return values


class ImportPreviewCommitResponse(BaseModel):
    import_id: str
    state: str
    total_parsed: int


class RetryImportEntriesRequest(BaseModel):
    reservation_keys: list[str] = Field(min_length=1, max_length=50)

    @field_validator("reservation_keys")
    @classmethod
    def validate_reservation_keys(cls, values: list[str]) -> list[str]:
        if any(len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value) for value in values):
            raise ValueError("reservation keys must be lowercase SHA-256 hex")
        if len(set(values)) != len(values):
            raise ValueError("reservation keys must be unique")
        return values


class RetryImportEntriesResponse(BaseModel):
    queued: list[str]
    skipped: list[str]


async def create_import(
    response: Response,
    file: UploadFile,
    user: CurrentUser,
    session: DbSession,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ImportCreateResponse:
    settings = get_settings()
    try:
        admission_policy = _configured_admission_policy(settings)
    except (AdmissionPolicyNotConfigured, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "bulk_import_policy_not_configured"},
        ) from exc

    # Direct-call tests invoke the route function without FastAPI resolving its
    # Header default. At runtime FastAPI supplies only str | None.
    if not isinstance(idempotency_key, str):
        idempotency_key = None
    if idempotency_key is not None:
        idempotency_key = idempotency_key.strip()
        if not idempotency_key or len(idempotency_key) > 200:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "invalid_idempotency_key"},
            )
        request_key_hash = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    else:
        request_key_hash = None
    try:
        tmp_path, raw_digest = await _stream_upload_to_temp(
            file, max_bytes=getattr(settings, "import_max_upload_bytes", 50 * 1024 * 1024)
        )
    except UploadTooLarge as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"code": "import_upload_too_large"},
        ) from exc
    request_fingerprint = (
        hashlib.sha256(b"spillthereel-import-v1\0" + raw_digest).hexdigest()
        if request_key_hash is not None
        else None
    )

    try:
        try:
            parsed = await _parse_archive_bounded(
                tmp_path,
                timeout_seconds=getattr(settings, "import_parse_timeout_seconds", 30.0),
            )
        except ParserCapacityExceeded as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "import_parser_at_capacity", "retryable": True},
                headers={"Retry-After": "5"},
            ) from e
        except TimeoutError as e:
            _logger.info("imports.archive_parse_deadline", user_id=user.id)
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail={"code": "import_archive_processing_limit"},
            ) from e
        except ArchiveLimitError as e:
            _logger.info("imports.rejected_archive_limit", user_id=user.id, reason=str(e))
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail={"code": "import_archive_processing_limit"},
            ) from e
        except (ParserError, zipfile.BadZipFile, OSError, ValueError) as e:
            _logger.info("imports.rejected_bad_zip", user_id=user.id, err=str(e))
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid export zip",
            ) from e

        semantic_fingerprint = _semantic_import_fingerprint(parsed)

        candidates: list[AdmissionCandidate] = []
        for post in (*parsed.posts, *(post for collection in parsed.collections for post in collection.media)):
            try:
                platform = detect_platform(post.url)
            except UnsupportedPlatformError:
                # Unsupported source rows never reserve lifetime allowance.
                continue
            bucket = SourceBucket.INSTAGRAM if platform == "instagram" else SourceBucket.OTHER
            candidates.append(AdmissionCandidate(post.url, bucket))
        # Saved-audio rows have no source URL. A stable synthetic identity lets the
        # explicit D03 policy count them without storing title/artist in the ledger.
        for audio in parsed.audio:
            candidates.append(
                AdmissionCandidate(
                    saved_audio_identity_url(audio.title, audio.artist, audio.saved_at),
                    SourceBucket.INSTAGRAM,
                    saved_audio=True,
                )
            )

        # Fast path for retries after a lost response. Recheck under the account
        # lock below to serialize concurrent requests using the same key.
        existing_request = await _find_idempotent_import(session, user.id, request_key_hash)
        if existing_request is not None:
            _require_matching_fingerprint(existing_request, request_fingerprint)
            response.status_code = status.HTTP_202_ACCEPTED
            return ImportCreateResponse(
                import_id=existing_request.id,
                state=existing_request.state,
                total_parsed=existing_request.total_parsed,
            )

        # A different multipart key or ZIP packaging can still represent the
        # same parsed import. Resolve this before creating any staging state.
        existing_semantic = await _find_semantic_import(
            session, user.id, semantic_fingerprint
        )
        if existing_semantic is not None:
            response.status_code = status.HTTP_202_ACCEPTED
            return _import_create_response(existing_semantic)

        # Allocate the durable identity before staging so the immutable source,
        # Import row and outbox payload all bind to the same owner-scoped key.
        import_id = str(ulid.new())
        storage_bucket = settings.supabase_bucket_exports
        storage_key = f"{user.id}/{import_id}.zip"
        try:
            marker_created = await create_import_staging_marker(
                session,
                owner_id=user.id,
                import_id=import_id,
                bucket=storage_bucket,
                storage_key=storage_key,
                lease_until=datetime.now(UTC) + timedelta(seconds=STAGING_LEASE_SECONDS),
            )
        except Exception as marker_error:
            _logger.warning(
                "imports.staging_marker_create_failed",
                import_id=import_id,
                error_code=type(marker_error).__name__,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="import staging initialization failed",
            ) from marker_error
        if not marker_created:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "account_unavailable"},
            )
        try:
            async with asyncio.timeout(_STAGING_UPLOAD_DEADLINE_SECONDS):
                await upload_object(storage_bucket, storage_key, tmp_path, "application/zip")
        except StorageError as e:
            await _schedule_import_staging_cleanup(
                session.bind,
                owner_id=user.id,
                import_id=import_id,
                bucket=storage_bucket,
                storage_key=storage_key,
                error_code=type(e).__name__,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="export staging failed",
            ) from e
        commit_attempted = False
        intent_id: str | None = None
        try:
            # Start all database work only after the potentially slow Storage
            # upload. admit_bulk_import owns the short per-account lock.
            staging_marker = await lock_import_staging_for_admission(
                session,
                owner_id=user.id,
                import_id=import_id,
                bucket=storage_bucket,
                storage_key=storage_key,
            )
            if staging_marker is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"code": "import_staging_unavailable"},
                )
            existing_request = await _find_idempotent_import(
                session, user.id, request_key_hash
            )
            if existing_request is not None:
                _require_matching_fingerprint(existing_request, request_fingerprint)
                existing_result = (
                    existing_request.id,
                    existing_request.state,
                    existing_request.total_parsed,
                )
                # Keep the staged duplicate registered until its safe cleanup
                # window; release locks before invoking the bounded sweeper.
                await session.commit()
                await _schedule_import_staging_cleanup(
                    session.bind,
                    owner_id=user.id,
                    import_id=import_id,
                    bucket=storage_bucket,
                    storage_key=storage_key,
                    error_code="IdempotentDuplicateUpload",
                )
                response.status_code = status.HTTP_202_ACCEPTED
                return ImportCreateResponse(
                    import_id=existing_result[0],
                    state=existing_result[1],
                    total_parsed=existing_result[2],
                )
            existing_semantic = await _find_semantic_import(
                session, user.id, semantic_fingerprint
            )
            if existing_semantic is not None:
                existing_result = (
                    existing_semantic.id,
                    existing_semantic.state,
                    existing_semantic.total_parsed,
                )
                # The account lock serializes this check with admission and
                # semantic identity creation. This request's staged bytes are
                # tracked and can now be safely removed.
                await session.commit()
                await _schedule_import_staging_cleanup(
                    session.bind,
                    owner_id=user.id,
                    import_id=import_id,
                    bucket=storage_bucket,
                    storage_key=storage_key,
                    error_code="SemanticDuplicateUpload",
                )
                response.status_code = status.HTTP_202_ACCEPTED
                return ImportCreateResponse(
                    import_id=existing_result[0],
                    state=existing_result[1],
                    total_parsed=existing_result[2],
                )
            canonical_urls = {
                normalize_url(candidate.url) for candidate in candidates if not candidate.saved_audio
            }
            existing_item_urls: set[str] = set()
            if canonical_urls:
                existing_item_urls = set(
                    (
                        await session.execute(
                            select(Item.source_url_norm).where(
                                Item.user_id == user.id,
                                Item.source_url_norm.in_(canonical_urls),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            candidates = [
                AdmissionCandidate(
                    candidate.url,
                    candidate.source_bucket,
                    existing_library_item=(
                        not candidate.saved_audio and normalize_url(candidate.url) in existing_item_urls
                    ),
                    saved_audio=candidate.saved_audio,
                )
                for candidate in candidates
            ]
            try:
                admission = await admit_bulk_import(
                    session,
                    user_id=user.id,
                    tier=AdmissionTier.FREE,
                    candidates=candidates,
                    policy=admission_policy,
                )
            except AdmissionProfileUnavailable as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"code": "account_unavailable"},
                ) from exc

            if any(item.status is AdmissionStatus.LIMIT_REACHED for item in admission.candidates):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"code": "import_allowance_exceeded"},
                )

            admitted_keys = {
                item.reservation_key
                for item in admission.candidates
                if item.status is AdmissionStatus.ADMITTED
            }
            already_reserved_keys = {
                item.reservation_key
                for item in admission.candidates
                if item.status is AdmissionStatus.ALREADY_ADMITTED
            }
            eligible_keys = admitted_keys | already_reserved_keys

            def accepted(url: str) -> bool:
                return reservation_key_for_url(url) in eligible_keys

            filtered_collections = tuple(
                filtered
                for collection in parsed.collections
                if (filtered := replace(
                    collection,
                    media=tuple(post for post in collection.media if accepted(post.url)),
                )).media
            )
            admitted_audio = tuple(
                audio
                for audio in parsed.audio
                if reservation_key_for_url(saved_audio_identity_url(audio.title, audio.artist, audio.saved_at))
                in admitted_keys
            )
            parsed = type(parsed)(
                posts=tuple(post for post in parsed.posts if accepted(post.url)),
                collections=filtered_collections,
                audio=admitted_audio,
                parser_schema_hash=parsed.parser_schema_hash,
                unknown_label_count=parsed.unknown_label_count,
                entries_with_unknowns=parsed.entries_with_unknowns,
            )
            entry_count = len(parsed.posts) + sum(len(coll.media) for coll in parsed.collections) + len(parsed.audio)
            if entry_count == 0:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"code": "no_eligible_import_items"},
                )

            import_row = Import(
                id=import_id,
                user_id=user.id,
                state="processing",
                admission_enforced=True,
                request_key_hash=request_key_hash,
                request_fingerprint=request_fingerprint,
                semantic_fingerprint=semantic_fingerprint,
            )
            session.add(import_row)
            await session.flush()
            _logger.info("imports.created", import_id=import_id, user_id=user.id, entries=entry_count)
            intent_id = await record_import_processing_intent(
                session,
                import_id=import_id,
                user_id=user.id,
                storage_key=storage_key,
                admitted_reservation_keys=admitted_keys,
                already_reserved_reservation_keys=already_reserved_keys,
            )
            # Marker removal commits atomically with the Import, reservations
            # and outbox intent. A cleanup-pending marker is never admitted.
            await session.delete(staging_marker)
            commit_attempted = True
            await session.commit()
        except BaseException as exc:
            try:
                await session.rollback()
            except Exception as rollback_error:
                _logger.warning(
                    "imports.transaction_rollback_failed",
                    import_id=import_id,
                    error_code=type(rollback_error).__name__,
                )
            if commit_attempted and intent_id is not None:
                reconciliation = ImportCommitReconciliation.UNKNOWN
                try:
                    bind = session.bind
                    if bind is not None:
                        reconciliation = await reconcile_import_commit(
                            bind,
                            import_id=import_id,
                            user_id=user.id,
                            storage_key=storage_key,
                            outbox_id=intent_id,
                        )
                except Exception as reconciliation_error:
                    _logger.warning(
                        "imports.commit_reconciliation_failed",
                        import_id=import_id,
                        error_code=type(reconciliation_error).__name__,
                    )

                if reconciliation is ImportCommitReconciliation.COMMITTED:
                    _logger.info("imports.commit_reconciled_committed", import_id=import_id)
                    if not isinstance(exc, asyncio.CancelledError):
                        response.status_code = status.HTTP_202_ACCEPTED
                        return ImportCreateResponse(
                            import_id=import_id, state="processing", total_parsed=0
                        )
                elif reconciliation is ImportCommitReconciliation.PROVEN_UNCOMMITTED:
                    _logger.warning(
                        "imports.commit_reconciled_uncommitted",
                        import_id=import_id,
                    )
                    await _schedule_import_staging_cleanup(
                        session.bind,
                        owner_id=user.id,
                        import_id=import_id,
                        bucket=storage_bucket,
                        storage_key=storage_key,
                    )
                else:
                    # A rollback cannot prove that a commit was not accepted by
                    # Postgres. Unknown or partial durable state keeps the source.
                    _logger.warning(
                        "imports.commit_outcome_unknown_source_preserved",
                        import_id=import_id,
                        error_code=type(exc).__name__,
                    )
            elif not commit_attempted:
                await _schedule_import_staging_cleanup(
                    session.bind,
                    owner_id=user.id,
                    import_id=import_id,
                    bucket=storage_bucket,
                    storage_key=storage_key,
                )
            else:
                _logger.warning(
                    "imports.commit_outcome_unknown_source_preserved",
                    import_id=import_id,
                    error_code=type(exc).__name__,
                )
            raise
        await dispatch_after_commit([intent_id])
        _logger.info(
            "imports.enqueued",
            import_id=import_id,
            user_id=user.id,
            storage_key=storage_key,
        )
        response.status_code = status.HTTP_202_ACCEPTED
        return ImportCreateResponse(import_id=import_row.id, state="processing", total_parsed=0)
    finally:
        tmp_path.unlink(missing_ok=True)


@router.post("/imports")
async def reject_legacy_direct_import(user: CurrentUser) -> None:
    """Require explicit selection through the preview/commit workflow.

    This endpoint intentionally has no UploadFile/body parameter, so FastAPI
    does not parse or spool a multipart archive before returning the rejection.
    Keep the previous handler unmounted for now because its admission and
    reconciliation helpers still have focused internal regression coverage.
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "code": "preview_required",
            "message": (
                "Create an import preview, select candidates, then commit the selection."
            ),
            "preview_url": "/v1/imports/previews",
        },
    )


class UploadTooLarge(ValueError):
    """Upload exceeded its compressed-byte ceiling while streaming."""


async def _stream_upload_to_temp(
    file: UploadFile, *, max_bytes: int
) -> tuple[Path, bytes]:
    """Bound request buffering while preserving the exact raw-body digest."""
    fd, tmp_name = tempfile.mkstemp(suffix=".zip", prefix="spill-import-upload-")
    path = Path(tmp_name)
    digest = hashlib.sha256()
    size = 0
    try:
        with os.fdopen(fd, "wb") as fp:
            while chunk := await file.read(64 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise UploadTooLarge
                digest.update(chunk)
                fp.write(chunk)
        return path, digest.digest()
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _is_standalone_saved_posts_html(filename: str | None) -> bool:
    """Recognize only Meta's standalone saved-post export filename.

    Upload filenames are untrusted, and browser paths may use either slash
    convention. This check only examines the basename; it never uses the
    submitted path for filesystem access.
    """
    if not filename:
        return False
    basename = filename.replace("\\", "/").rsplit("/", 1)[-1]
    if basename == _STANDALONE_INSTAGRAM_EXPORT_NAME:
        return True
    if basename.lower().endswith(".html"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "unsupported_import_file"},
        )
    return False


def _wrap_saved_posts_html_as_zip(source_path: Path) -> Path:
    """Create the worker-compatible ZIP envelope for one bounded HTML file."""
    fd, tmp_name = tempfile.mkstemp(suffix=".zip", prefix="spill-import-normalized-")
    os.close(fd)
    archive_path = Path(tmp_name)
    try:
        with zipfile.ZipFile(
            archive_path, mode="w", compression=zipfile.ZIP_STORED
        ) as archive:
            archive.write(source_path, arcname="saved/saved_posts.html")
        return archive_path
    except BaseException:
        archive_path.unlink(missing_ok=True)
        raise


async def _parse_archive_bounded(path: Path, *, timeout_seconds: float) -> ParsedExport:
    """Run bounded parsing off-loop, with a hard cap on lingering timed-out work."""
    if not _ARCHIVE_PARSER_SLOTS.acquire(blocking=False):
        raise ParserCapacityExceeded("archive parsers are at capacity")
    try:
        future = _ARCHIVE_PARSER_EXECUTOR.submit(parse_zip, path)
    except BaseException:
        _ARCHIVE_PARSER_SLOTS.release()
        raise
    future.add_done_callback(lambda _future: _ARCHIVE_PARSER_SLOTS.release())
    wrapped = asyncio.wrap_future(future)
    return await asyncio.wait_for(asyncio.shield(wrapped), timeout=timeout_seconds)


class ParserCapacityExceeded(RuntimeError):
    """All bounded archive parser workers are busy, including timed-out work."""


@router.post("/imports/previews", response_model=ImportPreviewCreateResponse)
async def create_import_preview(
    file: UploadFile,
    user: CurrentUser,
    session: DbSession,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ImportPreviewCreateResponse:
    """Parse and stage a ZIP without reserving quota or starting ingestion."""
    settings = get_settings()
    preview_ttl_seconds = _configured_preview_ttl_seconds(settings)
    if not isinstance(idempotency_key, str):
        idempotency_key = None
    if idempotency_key is not None:
        idempotency_key = idempotency_key.strip()
        if not idempotency_key or len(idempotency_key) > 200:
            raise HTTPException(400, detail={"code": "invalid_idempotency_key"})
        request_key_hash = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    else:
        request_key_hash = None

    normalized_path: Path | None = None
    try:
        tmp_path, raw_digest = await _stream_upload_to_temp(
            file, max_bytes=getattr(settings, "import_max_upload_bytes", 50 * 1024 * 1024)
        )
    except UploadTooLarge as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"code": "import_upload_too_large"},
        ) from exc

    try:
        is_standalone_html = _is_standalone_saved_posts_html(
            getattr(file, "filename", None)
        )
        archive_path = tmp_path
        if is_standalone_html:
            normalized_path = _wrap_saved_posts_html_as_zip(tmp_path)
            archive_path = normalized_path
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise

    request_fingerprint = (
        hashlib.sha256(b"spillthereel-import-preview-v1\0" + raw_digest).hexdigest()
        if request_key_hash is not None
        else None
    )
    preview_id = str(ulid.new())
    bucket = settings.supabase_bucket_exports
    storage_key = f"{user.id}/{preview_id}.zip"
    marker_created = False
    commit_attempted = False
    try:
        if request_key_hash is not None:
            existing = (
                await session.execute(
                    select(ImportPreview).where(
                        ImportPreview.owner_id == user.id,
                        ImportPreview.request_key_hash == request_key_hash,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                _require_matching_preview_fingerprint(existing, request_fingerprint)
                return _preview_create_response(existing)

        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=preview_ttl_seconds)
        marker_created = await create_import_staging_marker(
            session,
            owner_id=user.id,
            import_id=preview_id,
            bucket=bucket,
            storage_key=storage_key,
            lease_until=expires_at + timedelta(seconds=STAGING_LEASE_SECONDS),
        )
        if not marker_created:
            raise HTTPException(409, detail={"code": "account_unavailable"})

        try:
            async with asyncio.timeout(_STAGING_UPLOAD_DEADLINE_SECONDS):
                await upload_object(bucket, storage_key, archive_path, "application/zip")
        except (StorageError, TimeoutError) as exc:
            await _schedule_import_staging_cleanup(
                session.bind,
                owner_id=user.id,
                import_id=preview_id,
                bucket=bucket,
                storage_key=storage_key,
                error_code=type(exc).__name__,
            )
            raise HTTPException(500, detail="export staging failed") from exc

        try:
            parsed = await _parse_archive_bounded(
                archive_path,
                timeout_seconds=getattr(settings, "import_parse_timeout_seconds", 30.0),
            )
        except ParserCapacityExceeded as exc:
            raise HTTPException(
                503,
                detail={"code": "import_parser_at_capacity", "retryable": True},
                headers={"Retry-After": "5"},
            ) from exc
        except TimeoutError as exc:
            raise HTTPException(
                413, detail={"code": "import_archive_processing_limit"}
            ) from exc
        except ArchiveLimitError as exc:
            raise HTTPException(
                413, detail={"code": "import_archive_processing_limit"}
            ) from exc
        except (ParserError, zipfile.BadZipFile, OSError, ValueError) as exc:
            raise HTTPException(400, detail="invalid export zip") from exc

        candidates = build_preview_candidates(
            parsed, preview_id=preview_id, owner_id=user.id
        )
        if not candidates:
            raise HTTPException(422, detail={"code": "no_supported_import_items"})
        semantic_fingerprint = _semantic_import_fingerprint(parsed)

        # The account lock serializes same-key preview creation. The ordering
        # matches commit: Profile -> existing Preview -> staging marker.
        profile = (
            await session.execute(
                select(Profile).where(Profile.id == user.id).with_for_update()
            )
        ).scalar_one_or_none()
        if profile is None or profile.deleted_at is not None:
            raise HTTPException(409, detail={"code": "account_unavailable"})
        existing = None
        if request_key_hash is not None:
            existing = (
                await session.execute(
                    select(ImportPreview)
                    .where(
                        ImportPreview.owner_id == user.id,
                        ImportPreview.request_key_hash == request_key_hash,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
        marker = (
            await session.execute(
                select(ImportStaging)
                .where(ImportStaging.import_id == preview_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if (
            marker is None
            or marker.owner_id != user.id
            or marker.bucket != bucket
            or marker.storage_key != storage_key
            or marker.state != "staging"
            or marker.lease_until <= datetime.now(UTC)
            or marker.cleanup_token is not None
        ):
            raise HTTPException(409, detail={"code": "import_staging_unavailable"})
        if existing is not None:
            _require_matching_preview_fingerprint(existing, request_fingerprint)
            await session.commit()
            await _schedule_import_staging_cleanup(
                session.bind,
                owner_id=user.id,
                import_id=preview_id,
                bucket=bucket,
                storage_key=storage_key,
                error_code="IdempotentDuplicatePreview",
            )
            return _preview_create_response(existing)

        preview = ImportPreview(
            id=preview_id,
            owner_id=user.id,
            expires_at=expires_at,
            parser_schema_hash=parsed.parser_schema_hash,
            semantic_fingerprint=semantic_fingerprint,
            state="open",
            candidate_count=len(candidates),
            request_key_hash=request_key_hash,
            request_fingerprint=request_fingerprint,
            selection_digest=None,
        )
        session.add(preview)
        session.add_all(candidates)
        await session.flush()
        commit_attempted = True
        try:
            await session.commit()
        except BaseException:
            # A lost commit acknowledgement cannot authorize source deletion.
            # Read the durable preview identity through a fresh connection.
            await session.rollback()
            try:
                bind = session.bind
                if bind is not None:
                    async with async_sessionmaker(bind, expire_on_commit=False)() as fresh:
                        durable = (
                            await fresh.execute(
                                select(ImportPreview).where(
                                    ImportPreview.id == preview_id,
                                    ImportPreview.owner_id == user.id,
                                    ImportPreview.semantic_fingerprint == semantic_fingerprint,
                                )
                            )
                        ).scalar_one_or_none()
                        if durable is not None:
                            return _preview_create_response(durable)
                        # A successful fresh read proving absence is sufficient
                        # to enqueue cleanup; a failed read falls through and
                        # preserves the staged source.
                        await _schedule_import_staging_cleanup(
                            bind,
                            owner_id=user.id,
                            import_id=preview_id,
                            bucket=bucket,
                            storage_key=storage_key,
                            error_code="PreviewCommitProvenUncommitted",
                        )
            except Exception as reconcile_error:
                _logger.warning(
                    "imports.preview_commit_reconciliation_failed",
                    preview_id=preview_id,
                    error_code=type(reconcile_error).__name__,
                )
            raise

        return _preview_create_response(preview)
    except BaseException:
        if marker_created and not commit_attempted:
            await session.rollback()
            await _schedule_import_staging_cleanup(
                session.bind,
                owner_id=user.id,
                import_id=preview_id,
                bucket=bucket,
                storage_key=storage_key,
            )
        raise
    finally:
        tmp_path.unlink(missing_ok=True)
        if normalized_path is not None:
            normalized_path.unlink(missing_ok=True)


@router.get(
    "/imports/previews/{preview_id}/candidates",
    response_model=ImportPreviewCandidatesPage,
)
async def get_import_preview_candidates(
    preview_id: str,
    user: CurrentUser,
    session: DbSession,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> ImportPreviewCandidatesPage:
    preview = (
        await session.execute(
            select(ImportPreview).where(
                ImportPreview.id == preview_id,
                ImportPreview.owner_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if preview is None:
        raise HTTPException(404)
    if preview.state == "committed":
        raise HTTPException(410, detail={"code": "import_preview_committed"})
    if preview.state == "expired" or (
        preview.state == "open" and preview.expires_at <= datetime.now(UTC)
    ):
        if preview.state == "open":
            preview.state = "expired"
            await session.commit()
        raise HTTPException(410, detail={"code": "import_preview_expired"})

    after_ordinal = -1
    if cursor is not None:
        after_ordinal = _decode_preview_cursor(cursor, preview_id)
    rows = (
        await session.execute(
            select(ImportPreviewCandidate)
            .where(
                ImportPreviewCandidate.preview_id == preview_id,
                ImportPreviewCandidate.owner_id == user.id,
                ImportPreviewCandidate.ordinal > after_ordinal,
            )
            .order_by(ImportPreviewCandidate.ordinal.asc())
            .limit(limit + 1)
        )
    ).scalars().all()
    has_more = len(rows) > limit
    page = rows[:limit]
    return ImportPreviewCandidatesPage(
        candidates=[
            ImportPreviewCandidateResponse(
                candidate_id=row.candidate_id,
                ordinal=row.ordinal,
                kind=row.kind,
                source_bucket=row.source_bucket,
                url=row.source_url_norm,
                title=row.title,
                artist=row.artist,
                saved_at=row.saved_at,
                collection_names=list(row.collection_names or []),
            )
            for row in page
        ],
        next_cursor=(
            _encode_preview_cursor(preview_id, page[-1].ordinal)
            if has_more and page
            else None
        ),
    )


@router.post(
    "/imports/previews/{preview_id}/commit",
    response_model=ImportPreviewCommitResponse,
)
async def commit_import_preview(
    preview_id: str,
    request: ImportPreviewCommitRequest,
    user: CurrentUser,
    session: DbSession,
) -> ImportPreviewCommitResponse:
    settings = get_settings()
    try:
        policy = _configured_admission_policy(settings)
    except (AdmissionPolicyNotConfigured, ValueError) as exc:
        raise HTTPException(
            503, detail={"code": "bulk_import_policy_not_configured"}
        ) from exc

    selection_digest = _selection_digest(request.candidate_ids)
    profile = (
        await session.execute(
            select(Profile).where(Profile.id == user.id).with_for_update()
        )
    ).scalar_one_or_none()
    if profile is None or profile.deleted_at is not None:
        await session.rollback()
        raise HTTPException(409, detail={"code": "account_unavailable"})
    preview = (
        await session.execute(
            select(ImportPreview)
            .where(ImportPreview.id == preview_id, ImportPreview.owner_id == user.id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if preview is None:
        await session.rollback()
        raise HTTPException(404)
    if preview.state == "committed":
        if preview.selection_digest != selection_digest:
            await session.rollback()
            raise HTTPException(409, detail={"code": "import_preview_selection_conflict"})
        existing_import = (
            await session.execute(
                select(Import).where(
                    Import.preview_id == preview_id,
                    Import.user_id == user.id,
                )
            )
        ).scalar_one_or_none()
        if existing_import is None:
            await session.rollback()
            raise HTTPException(409, detail={"code": "import_preview_commit_unavailable"})
        # Build the scalar response before rollback expires ORM attributes.
        # This branch is hit by idempotent retries after another request has
        # committed the preview; touching `existing_import` after rollback
        # can trigger implicit async IO outside SQLAlchemy's greenlet context.
        response = _preview_commit_response(existing_import)
        await session.rollback()
        return response
    if preview.state != "open" or preview.expires_at <= datetime.now(UTC):
        preview.state = "expired"
        await session.commit()
        raise HTTPException(410, detail={"code": "import_preview_expired"})

    bucket = settings.supabase_bucket_exports
    storage_key = f"{user.id}/{preview_id}.zip"
    marker = (
        await session.execute(
            select(ImportStaging)
            .where(ImportStaging.import_id == preview_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if (
        marker is None
        or marker.owner_id != user.id
        or marker.bucket != bucket
        or marker.storage_key != storage_key
        or marker.state != "staging"
        or marker.lease_until <= datetime.now(UTC)
        or marker.cleanup_token is not None
    ):
        await session.rollback()
        raise HTTPException(409, detail={"code": "import_staging_unavailable"})

    candidate_ids = [str(candidate_id) for candidate_id in request.candidate_ids]
    candidates = (
        await session.execute(
            select(ImportPreviewCandidate)
            .where(
                ImportPreviewCandidate.preview_id == preview_id,
                ImportPreviewCandidate.owner_id == user.id,
                ImportPreviewCandidate.candidate_id.in_(candidate_ids),
            )
            .order_by(ImportPreviewCandidate.ordinal.asc())
            .with_for_update()
        )
    ).scalars().all()
    if len(candidates) != len(candidate_ids):
        await session.rollback()
        raise HTTPException(422, detail={"code": "invalid_import_preview_selection"})

    canonical_urls = {
        candidate.source_url_norm
        for candidate in candidates
        if candidate.kind == "media" and candidate.source_url_norm is not None
    }
    existing_item_urls: set[str] = set()
    if canonical_urls:
        existing_item_urls = set(
            (
                await session.execute(
                    select(Item.source_url_norm).where(
                        Item.user_id == user.id,
                        Item.source_url_norm.in_(canonical_urls),
                    )
                )
            )
            .scalars()
            .all()
        )

    admission_candidates: list[AdmissionCandidate] = []
    for candidate in candidates:
        if candidate.kind == "audio":
            url = saved_audio_identity_url(
                candidate.title, candidate.artist, candidate.saved_at
            )
        else:
            assert candidate.source_url_norm is not None
            url = candidate.source_url_norm
        admission_candidates.append(
            AdmissionCandidate(
                url=url,
                source_bucket=SourceBucket(candidate.source_bucket),
                existing_library_item=(url in existing_item_urls),
                saved_audio=candidate.kind == "audio",
            )
        )
    try:
        # Premium remains unavailable until a trusted entitlement source is
        # implemented and verified (D04). This endpoint has no client tier
        # input; all currently admitted imports use the free lifetime policy.
        admission = await admit_bulk_import(
            session,
            user_id=user.id,
            tier=AdmissionTier.FREE,
            candidates=admission_candidates,
            policy=policy,
        )
    except AdmissionProfileUnavailable as exc:
        await session.rollback()
        raise HTTPException(409, detail={"code": "account_unavailable"}) from exc

    if any(item.status is AdmissionStatus.LIMIT_REACHED for item in admission.candidates):
        await session.rollback()
        raise HTTPException(409, detail={"code": "import_allowance_exceeded"})
    admitted_keys = {
        item.reservation_key
        for item in admission.candidates
        if item.status is AdmissionStatus.ADMITTED
    }
    already_reserved_keys = {
        item.reservation_key
        for item in admission.candidates
        if item.status is AdmissionStatus.ALREADY_ADMITTED
    }
    if not admitted_keys and not already_reserved_keys:
        await session.rollback()
        raise HTTPException(409, detail={"code": "no_eligible_import_items"})

    import_row = Import(
        id=preview_id,
        user_id=user.id,
        state="processing",
        admission_enforced=True,
        request_key_hash=None,
        request_fingerprint=None,
        semantic_fingerprint=preview.semantic_fingerprint,
        preview_id=preview_id,
        total_parsed=0,
        text_indexed_done=0,
        full_queued=0,
        full_done=0,
        failed=0,
        parser_schema_hash=preview.parser_schema_hash,
        failure_reason=None,
        finished_at=None,
    )
    session.add(import_row)
    preview.state = "committed"
    preview.selection_digest = selection_digest
    await session.flush()
    intent_id = await record_import_processing_intent(
        session,
        import_id=preview_id,
        user_id=user.id,
        storage_key=storage_key,
        admitted_reservation_keys=admitted_keys,
        already_reserved_reservation_keys=already_reserved_keys,
    )
    # The worker consumes the selected reservation keys from the durable outbox
    # payload and reparses the source ZIP. Candidate URLs and collection metadata
    # are no longer needed after this transaction has admitted the selection.
    await session.execute(
        delete(ImportPreviewCandidate).where(
            ImportPreviewCandidate.preview_id == preview_id,
            ImportPreviewCandidate.owner_id == user.id,
        )
    )
    await session.delete(marker)
    try:
        await session.commit()
    except BaseException as exc:
        await session.rollback()
        reconciliation = ImportCommitReconciliation.UNKNOWN
        try:
            bind = session.bind
            if bind is not None:
                reconciliation = await reconcile_import_commit(
                    bind,
                    import_id=preview_id,
                    user_id=user.id,
                    storage_key=storage_key,
                    outbox_id=intent_id,
                )
        except Exception as reconcile_error:
            _logger.warning(
                "imports.preview_commit_reconciliation_failed",
                preview_id=preview_id,
                error_code=type(reconcile_error).__name__,
            )
        if reconciliation is ImportCommitReconciliation.COMMITTED:
            await dispatch_after_commit([intent_id])
            return ImportPreviewCommitResponse(
                import_id=preview_id, state="processing", total_parsed=0
            )
        if reconciliation is ImportCommitReconciliation.PROVEN_UNCOMMITTED:
            await _schedule_import_staging_cleanup(
                session.bind,
                owner_id=user.id,
                import_id=preview_id,
                bucket=bucket,
                storage_key=storage_key,
                error_code="PreviewCommitProvenUncommitted",
            )
        else:
            _logger.warning(
                "imports.preview_commit_outcome_unknown_source_preserved",
                preview_id=preview_id,
                error_code=type(exc).__name__,
            )
        raise

    await dispatch_after_commit([intent_id])
    return _preview_commit_response(import_row)


def _preview_create_response(preview: ImportPreview) -> ImportPreviewCreateResponse:
    return ImportPreviewCreateResponse(
        preview_id=preview.id,
        expires_at=preview.expires_at,
        total_candidates=preview.candidate_count,
    )


def _preview_commit_response(import_row: Import) -> ImportPreviewCommitResponse:
    return ImportPreviewCommitResponse(
        import_id=import_row.id,
        state=import_row.state,
        total_parsed=import_row.total_parsed,
    )


def _require_matching_preview_fingerprint(
    preview: ImportPreview, request_fingerprint: str | None
) -> None:
    if (
        request_fingerprint is None
        or preview.request_fingerprint is None
        or preview.request_fingerprint != request_fingerprint
    ):
        raise HTTPException(409, detail={"code": "idempotency_key_payload_conflict"})


def _configured_preview_ttl_seconds(settings: object) -> int:
    """Require an owner-approved retention policy before staging preview data."""
    policy_version = getattr(settings, "import_preview_retention_policy_version", None)
    ttl_seconds = getattr(settings, "import_preview_ttl_seconds", None)
    if not policy_version or not isinstance(ttl_seconds, int) or ttl_seconds <= 0:
        raise HTTPException(
            503,
            detail={"code": "import_preview_retention_policy_not_configured"},
        )
    return ttl_seconds


def _selection_digest(candidate_ids: list[uuid.UUID]) -> str:
    canonical = "\0".join(sorted(str(candidate_id) for candidate_id in candidate_ids))
    return hashlib.sha256(b"spillthereel-import-selection-v1\0" + canonical.encode()).hexdigest()


def _encode_preview_cursor(preview_id: str, ordinal: int) -> str:
    raw = f"{preview_id}:{ordinal}".encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_preview_cursor(cursor: str, preview_id: str) -> int:
    try:
        if not cursor or len(cursor) > _MAX_PREVIEW_CURSOR_CHARS:
            raise ValueError("cursor length invalid")
        padded = cursor + "=" * (-len(cursor) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("ascii")
        cursor_preview_id, raw_ordinal = decoded.rsplit(":", 1)
        ordinal = int(raw_ordinal)
        if cursor_preview_id != preview_id or ordinal < -1:
            raise ValueError("cursor scope invalid")
        return ordinal
    except (ValueError, UnicodeEncodeError, UnicodeDecodeError, binascii.Error) as exc:
        raise HTTPException(422, detail={"code": "invalid_cursor"}) from exc


async def _schedule_import_staging_cleanup(
    bind: AsyncEngine | AsyncConnection | None,
    *,
    owner_id: str,
    import_id: str,
    bucket: str,
    storage_key: str,
    error_code: str | None = None,
) -> None:
    if bind is None:
        _logger.warning(
            "imports.staging_cleanup_enqueue_failed",
            import_id=import_id,
            error_code="DatabaseBindUnavailable",
        )
        return
    try:
        queued = await mark_import_staging_cleanup_pending(
            bind,
            owner_id=owner_id,
            import_id=import_id,
            bucket=bucket,
            storage_key=storage_key,
            error_code=error_code,
        )
        if queued:
            result = await sweep_import_staging(
                bind=bind,
                limit=1,
                import_id=import_id,
            )
            if result.failed or result.retained:
                _logger.warning(
                    "imports.staging_cleanup_deferred",
                    import_id=import_id,
                    failed=result.failed,
                    retained=result.retained,
                )
    except Exception as cleanup_error:
        _logger.warning(
            "imports.staging_cleanup_enqueue_failed",
            import_id=import_id,
            error_code=type(cleanup_error).__name__,
        )


async def _find_idempotent_import(
    session: DbSession, owner_id: str, request_key_hash: str | None
) -> Import | None:
    if request_key_hash is None:
        return None
    result = await session.execute(
        select(Import).where(
            Import.user_id == owner_id,
            Import.request_key_hash == request_key_hash,
        )
    )
    return result.scalar_one_or_none()


async def _find_semantic_import(
    session: DbSession, owner_id: str, semantic_fingerprint: str
) -> Import | None:
    result = await session.execute(
        select(Import).where(
            Import.user_id == owner_id,
            Import.semantic_fingerprint == semantic_fingerprint,
        )
    )
    return result.scalar_one_or_none()


def _import_create_response(row: Import) -> ImportCreateResponse:
    return ImportCreateResponse(
        import_id=row.id,
        state=row.state,
        total_parsed=row.total_parsed,
    )


def _semantic_import_fingerprint(parsed: ParsedExport) -> str:
    """Hash stable parsed occurrences, ignoring ZIP bytes and ordering.

    Multiplicity is intentionally retained: duplicate saves are distinct
    occurrences. Collection folder attribution is part of each identity, as
    are saved timestamps and the parser's versioned identity namespace.
    """
    occurrences: list[tuple[str, str, str]] = []

    def timestamp(value: datetime | None) -> str:
        return value.isoformat() if value is not None else ""

    for post in parsed.posts:
        occurrences.append(("post", "", reservation_key_for_url(post.url) + "\0" + timestamp(post.saved_at)))
    for collection in parsed.collections:
        folder = " ".join(collection.name.casefold().split())
        for post in collection.media:
            occurrences.append(
                ("collection_post", folder, reservation_key_for_url(post.url) + "\0" + timestamp(post.saved_at))
            )
    for audio in parsed.audio:
        identity = saved_audio_identity_url(audio.title, audio.artist, audio.saved_at)
        occurrences.append(("audio", "", reservation_key_for_url(identity)))

    canonical = json.dumps(sorted(occurrences), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(b"spillthereel-import-semantic-v1\0" + canonical).hexdigest()


def _require_matching_fingerprint(row: Import, request_fingerprint: str | None) -> None:
    if (
        request_fingerprint is None
        or row.request_fingerprint is None
        or row.request_fingerprint != request_fingerprint
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "idempotency_key_payload_conflict"},
        )


@router.get("/imports", response_model=ImportsPage)
async def list_imports(
    user: CurrentUser,
    session: DbSession,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ImportsPage:
    conditions = [Import.user_id == user.id]
    if cursor is not None:
        cursor_started_at, cursor_id = _decode_import_cursor(cursor)
        conditions.append(
            or_(
                Import.started_at < cursor_started_at,
                and_(Import.started_at == cursor_started_at, Import.id < cursor_id),
            )
        )
    rows = (
        (await session.execute(
            select(Import)
            .where(and_(*conditions))
            .order_by(Import.started_at.desc(), Import.id.desc())
            .limit(limit + 1)
        ))
        .scalars()
        .all()
    )
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    next_cursor = _encode_import_cursor(page_rows[-1]) if has_more and page_rows else None
    return ImportsPage(
        imports=[_status_response(row) for row in page_rows],
        next_cursor=next_cursor,
    )


@router.get("/imports/{import_id}/entries", response_model=ImportEntriesPage)
async def list_import_entries(
    import_id: str,
    user: CurrentUser,
    session: DbSession,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ImportEntriesPage:
    await _owned_import_for_read(import_id, user.id, session)
    conditions = [ImportEntry.import_id == import_id, ImportEntry.owner_id == user.id]
    if cursor is not None:
        if len(cursor) != 64 or any(ch not in "0123456789abcdef" for ch in cursor):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid cursor")
        conditions.append(ImportEntry.reservation_key > cursor)
    rows = (
        (await session.execute(
            select(ImportEntry)
            .where(and_(*conditions))
            .order_by(ImportEntry.reservation_key.asc())
            .limit(limit + 1)
        ))
        .scalars()
        .all()
    )
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    return ImportEntriesPage(
        entries=[ImportEntryStatus.model_validate(row, from_attributes=True) for row in page_rows],
        next_cursor=page_rows[-1].reservation_key if has_more and page_rows else None,
    )


@router.post(
    "/imports/{import_id}/entries/retry",
    response_model=RetryImportEntriesResponse,
)
async def retry_import_entries(
    import_id: str,
    request: RetryImportEntriesRequest,
    user: CurrentUser,
    session: DbSession,
) -> RetryImportEntriesResponse:
    """Retry selected failed Items without changing their import reservation."""
    profile = (
        await session.execute(select(Profile).where(Profile.id == user.id).with_for_update())
    ).scalar_one_or_none()
    if profile is None or profile.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    import_row = (
        await session.execute(
            select(Import)
            .where(Import.id == import_id, Import.user_id == user.id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if import_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    keys = request.reservation_keys
    entries = (
        await session.execute(
            select(ImportEntry)
            .where(
                ImportEntry.import_id == import_id,
                ImportEntry.owner_id == user.id,
                ImportEntry.reservation_key.in_(keys),
            )
            .order_by(ImportEntry.reservation_key)
            .with_for_update()
        )
    ).scalars().all()
    found_keys = {entry.reservation_key for entry in entries}
    missing_keys = set(keys) - found_keys
    if missing_keys:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    entries_by_key = {entry.reservation_key: entry for entry in entries}
    item_ids = sorted({entry.item_id for entry in entries if entry.item_id is not None})
    items = (
        await session.execute(
            select(Item)
            .where(Item.user_id == user.id, Item.id.in_(item_ids))
            .order_by(Item.id)
            .with_for_update()
        )
    ).scalars().all() if item_ids else []
    items_by_id = {item.id: item for item in items}
    queued: list[str] = []
    dispatched_ids: list[str] = []
    skipped: list[str] = []
    exhausted = False
    retry_at = datetime.now(UTC)
    for key in keys:
        entry = entries_by_key[key]
        item = items_by_id.get(entry.item_id)
        if entry.state != "retryable_failed" or item is None or item.state != "failed":
            skipped.append(key)
            continue
        if entry.retry_count >= _MAX_IMPORT_ENTRY_RETRIES:
            entry.state = "terminal_failed"
            entry.last_error_code = "retry_limit_reached"
            entry.updated_at = datetime.now(UTC)
            exhausted = True
            skipped.append(key)
            continue
        item.state = "queued"
        item.failure_reason = None
        entry.state = "queued"
        entry.last_error_code = None
        entry.retry_count += 1
        if entry.work_queued_at is None:
            entry.work_queued_at = retry_at
        entry.updated_at = retry_at
        session.add(
            IngestionEvent(
                id=str(ulid.new()),
                item_id=item.id,
                from_state="failed",
                to_state="queued",
                detail={"trigger": "import_entry_retry", "import_id": import_id},
            )
        )
        generation = await next_generation(session, "ingest", "item", item.id)
        outbox_row = await record_task_intent(
            session,
            "ingest",
            {"item_id": item.id, "user_id": user.id},
            target_type="item",
            target_id=item.id,
            generation=generation,
        )
        dispatched_ids.append(outbox_row.id)
        queued.append(key)

    if queued or exhausted:
        progress = await derive_import_progress(
            session, import_id=import_id, total_parsed=import_row.total_parsed
        )
        import_row.total_parsed = progress.total_parsed
        import_row.full_queued = progress.full_queued
        import_row.text_indexed_done = progress.text_indexed_done
        import_row.full_done = progress.full_done
        import_row.failed = progress.failed
        pending = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(ImportEntry)
                    .where(
                        ImportEntry.import_id == import_id,
                        ImportEntry.owner_id == user.id,
                        ImportEntry.state.not_in(("completed", "terminal_failed")),
                    )
                )
            ).scalar_one_or_none()
            or 0
        )
        terminal = pending == 0
        import_row.state = (
            "failed" if terminal and progress.full_done == 0 and progress.failed > 0
            else "complete" if terminal
            else "indexing"
        )
        import_row.finished_at = datetime.now(UTC) if terminal else None
        await session.commit()
        if dispatched_ids:
            await dispatch_after_commit(dispatched_ids)
    return RetryImportEntriesResponse(queued=queued, skipped=skipped)


@router.get("/imports/{import_id}", response_model=ImportStatusResponse)
async def get_import(import_id: str, user: CurrentUser, session: DbSession) -> ImportStatusResponse:
    stmt = select(Import).where(Import.id == import_id, Import.user_id == user.id)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return _status_response(row)


def _status_response(row: Import) -> ImportStatusResponse:
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


async def _owned_import_for_read(import_id: str, owner_id: str, session: DbSession) -> Import:
    row = (
        await session.execute(
            select(Import).where(Import.id == import_id, Import.user_id == owner_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return row


def _encode_import_cursor(row: Import) -> str:
    payload = json.dumps(
        {"started_at": row.started_at.isoformat(), "id": row.id}, separators=(",", ":")
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_import_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        if len(cursor) > _MAX_IMPORT_CURSOR_CHARS:
            raise ValueError("cursor too long")
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        started_at = datetime.fromisoformat(payload["started_at"])
        import_id = payload["id"]
        if started_at.tzinfo is None or not isinstance(import_id, str) or not import_id:
            raise ValueError("invalid cursor fields")
        return started_at, import_id
    except (ValueError, TypeError, KeyError, binascii.Error, UnicodeEncodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="invalid cursor",
        ) from exc


@router.get("/me/import-allowance", response_model=ImportAllowanceResponse)
async def get_import_allowance(user: CurrentUser, session: DbSession) -> ImportAllowanceResponse:
    """Return the authenticated account's durable free bulk-import allowance."""
    try:
        policy = _configured_admission_policy(get_settings())
        # Premium stays unavailable until a trusted server-side entitlement
        # integration is selected and verified; the mobile client cannot choose it.
        snapshot: AllowanceSnapshot = await get_bulk_allowance(
            session,
            user_id=user.id,
            tier=AdmissionTier.FREE,
            policy=policy,
        )
    except AdmissionPolicyNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "bulk_import_policy_not_configured"},
        ) from exc
    except AdmissionProfileUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "account_unavailable"},
        ) from exc
    return ImportAllowanceResponse(
        tier=snapshot.tier.value,
        policy_version=snapshot.policy_version,
        total_limit=snapshot.total_limit,
        total_used=snapshot.total_used,
        total_remaining=snapshot.total_remaining,
        instagram_limit=snapshot.instagram_limit,
        instagram_used=snapshot.instagram_used,
        instagram_remaining=snapshot.instagram_remaining,
        other_limit=snapshot.other_limit,
        other_used=snapshot.other_used,
        other_remaining=snapshot.other_remaining,
    )


def _configured_admission_policy(settings: object) -> AdmissionPolicy:
    """Build the explicit server policy; incomplete configuration fails closed."""

    version = getattr(settings, "bulk_import_policy_version", None)
    values = (
        getattr(settings, "bulk_import_premium_history_mode", None),
        getattr(settings, "bulk_import_existing_item_mode", None),
        getattr(settings, "bulk_import_manual_share_mode", None),
        getattr(settings, "bulk_import_saved_audio_mode", None),
    )
    if not version or not getattr(settings, "bulk_import_history_reconciled", False) or not all(values):
        raise AdmissionPolicyNotConfigured("bulk import policy is not configured")
    premium_history = PremiumHistoryMode(values[0])
    if premium_history is not PremiumHistoryMode.CARRY_FORWARD:
        raise AdmissionPolicyNotConfigured(
            "separate premium allowance awaits explicit owner approval"
        )
    return AdmissionPolicy(
        version=version,
        d01_premium_history=premium_history,
        d02_existing_duplicate=EligibilityMode(values[1]),
        d02_manual_share=EligibilityMode(values[2]),
        d03_saved_audio=EligibilityMode(values[3]),
    )
