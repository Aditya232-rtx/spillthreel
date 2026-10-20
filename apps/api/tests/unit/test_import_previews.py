from __future__ import annotations

import hashlib
import io
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import Delete

import app.api.v1.imports as imports
from app.db.models import Import, ImportPreview, ImportPreviewCandidate
from app.services.imports.admission import (
    AdmissionStatus,
    CandidateAdmission,
    SourceBucket,
)
from app.services.imports.ig_parser import ParsedExport, SavedCollection, SavedPost
from app.services.imports.previews import build_preview_candidates

OWNER = "11111111-1111-1111-1111-111111111111"
OTHER_OWNER = "22222222-2222-2222-2222-222222222222"
PREVIEW_ID = "01K7F9R2J5YMXB3Z4Z0H7C1Q8W"
NOW = datetime.now(UTC)
URL_A = "https://www.instagram.com/reel/DbM7eWINqAy/?igsh=abc"
URL_B = "https://www.youtube.com/watch?v=abc123"


class Result:
    def __init__(self, value=None, *, values=None):
        self.value = value
        self.values = values

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.values or []


class FakeSession:
    def __init__(self, results=(), *, bind="fake-bind", commit_error=None):
        self.results = list(results)
        self.bind = bind
        self.commit_error = commit_error
        self.statements = []
        self.added = []
        self.commits = 0
        self.rollbacks = 0
        self.deleted = []

    async def execute(self, statement):
        self.statements.append(statement)
        return self.results.pop(0) if self.results else Result()

    def add(self, value):
        self.added.append(value)

    def add_all(self, values):
        self.added.extend(values)

    async def flush(self):
        return None

    async def commit(self):
        self.commits += 1
        if self.commit_error:
            raise self.commit_error

    async def rollback(self):
        self.rollbacks += 1

    async def delete(self, value):
        self.deleted.append(value)


class FakeUpload:
    def __init__(self, payload=b"synthetic zip", filename=None):
        self.payload = payload
        self.filename = filename
        self.reads = 0

    async def read(self, size=-1):
        if self.reads:
            return b""
        self.reads += 1
        return self.payload


def _parsed() -> ParsedExport:
    return ParsedExport(
        posts=(SavedPost(url=URL_A, saved_at=NOW), SavedPost(url=URL_B, saved_at=NOW)),
        collections=(
            SavedCollection(
                name="Recipes",
                type=None,
                privacy=None,
                update_time=None,
                media=(SavedPost(url=URL_A, saved_at=NOW),),
            ),
        ),
        audio=(),
        parser_schema_hash="parser-v1",
        unknown_label_count=0,
        entries_with_unknowns=0,
    )


def _preview(*, owner=OWNER, state="open", expires=None, selection_digest=None):
    return ImportPreview(
        id=PREVIEW_ID,
        owner_id=owner,
        expires_at=expires or NOW + timedelta(hours=1),
        parser_schema_hash="parser-v1",
        semantic_fingerprint="a" * 64,
        state=state,
        candidate_count=2,
        request_key_hash=None,
        request_fingerprint=None,
        selection_digest=selection_digest,
    )


def _candidate(candidate_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", *, owner=OWNER):
    return SimpleNamespace(
        preview_id=PREVIEW_ID,
        candidate_id=candidate_id,
        owner_id=owner,
        ordinal=0,
        reservation_key="b" * 64,
        source_bucket="instagram",
        kind="media",
        source_url_norm="https://www.instagram.com/reel/DbM7eWINqAy/",
        title=None,
        artist=None,
        saved_at=NOW,
        collection_names=["Recipes"],
    )


def test_candidate_builder_deduplicates_identity_and_preserves_collection_names() -> None:
    candidates = build_preview_candidates(_parsed(), preview_id=PREVIEW_ID, owner_id=OWNER)
    assert len(candidates) == 2
    assert [candidate.ordinal for candidate in candidates] == [0, 1]
    assert candidates[0].source_bucket == "instagram"
    assert candidates[0].source_url_norm == "https://www.instagram.com/reel/DbM7eWINqAy"
    assert candidates[0].collection_names == ["Recipes"]
    assert candidates[1].source_bucket == "other"


def test_candidate_builder_excludes_non_web_url_even_with_supported_hostname() -> None:
    parsed = ParsedExport(
        posts=(SavedPost(url="file://instagram.com/private/path", saved_at=NOW),),
        collections=(),
        audio=(),
        parser_schema_hash="parser-v1",
        unknown_label_count=0,
        entries_with_unknowns=0,
    )

    assert build_preview_candidates(parsed, preview_id=PREVIEW_ID, owner_id=OWNER) == []


def test_preview_commit_request_rejects_empty_duplicate_and_oversized_selection() -> None:
    one = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    with pytest.raises(ValidationError):
        imports.ImportPreviewCommitRequest.model_validate({"candidate_ids": []})
    with pytest.raises(ValidationError):
        imports.ImportPreviewCommitRequest.model_validate({"candidate_ids": [one, one]})
    with pytest.raises(ValidationError):
        imports.ImportPreviewCommitRequest.model_validate(
            {"candidate_ids": [f"00000000-0000-4000-8000-{n:012d}" for n in range(1001)]}
        )


def test_preview_cursor_is_keyset_scoped_and_round_trips() -> None:
    cursor = imports._encode_preview_cursor(PREVIEW_ID, 17)
    assert imports._decode_preview_cursor(cursor, PREVIEW_ID) == 17
    with pytest.raises(HTTPException) as error:
        imports._decode_preview_cursor(cursor, "01K7F9R2J5YMXB3Z4Z0H7C1Q9X")
    assert error.value.status_code == 422


@pytest.mark.asyncio
async def test_preview_creation_fails_closed_without_owner_approved_retention(monkeypatch) -> None:
    monkeypatch.setattr(imports, "get_settings", lambda: SimpleNamespace())
    upload = AsyncMock()
    monkeypatch.setattr(imports, "upload_object", upload)

    with pytest.raises(HTTPException) as error:
        await imports.create_import_preview(
            file=FakeUpload(),
            user=SimpleNamespace(id=OWNER),
            session=FakeSession(),
            idempotency_key=None,
        )

    assert error.value.status_code == 503
    assert error.value.detail == {"code": "import_preview_retention_policy_not_configured"}
    upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_preview_is_metadata_only_and_does_not_admit_or_dispatch(monkeypatch) -> None:
    profile = SimpleNamespace(id=OWNER, deleted_at=None)
    marker = SimpleNamespace(
        owner_id=OWNER,
        bucket="exports",
        storage_key=f"{OWNER}/{PREVIEW_ID}.zip",
        state="staging",
        lease_until=NOW + timedelta(hours=2),
        cleanup_token=None,
    )
    session = FakeSession([Result(profile), Result(marker)])
    monkeypatch.setattr(imports.ulid, "new", lambda: PREVIEW_ID)
    monkeypatch.setattr(imports, "get_settings", lambda: SimpleNamespace(
        supabase_bucket_exports="exports",
        import_max_upload_bytes=1024,
        import_parse_timeout_seconds=5,
        import_preview_retention_policy_version="test-policy-v1",
        import_preview_ttl_seconds=3600,
    ))
    monkeypatch.setattr(imports, "create_import_staging_marker", AsyncMock(return_value=True))
    upload = AsyncMock()
    monkeypatch.setattr(imports, "upload_object", upload)
    monkeypatch.setattr(imports, "_parse_archive_bounded", AsyncMock(return_value=_parsed()))
    monkeypatch.setattr(imports, "admit_bulk_import", AsyncMock(side_effect=AssertionError("preview reserved quota")))
    dispatch = AsyncMock(side_effect=AssertionError("preview dispatched work"))
    monkeypatch.setattr(imports, "dispatch_after_commit", dispatch)
    monkeypatch.setattr(imports, "_schedule_import_staging_cleanup", AsyncMock())

    response = await imports.create_import_preview(
        file=FakeUpload(),
        user=profile,
        session=session,
        idempotency_key=None,
    )
    assert response.preview_id == PREVIEW_ID
    assert response.total_candidates == 2
    assert len([value for value in session.added if isinstance(value, ImportPreview)]) == 1
    assert len([value for value in session.added if isinstance(value, ImportPreviewCandidate)]) == 2
    assert upload.await_count == 1  # private source staging only
    assert dispatch.await_count == 0


@pytest.mark.asyncio
async def test_standalone_saved_posts_html_is_wrapped_and_raw_digest_is_preserved(monkeypatch) -> None:
    profile = SimpleNamespace(id=OWNER, deleted_at=None)
    marker = SimpleNamespace(
        owner_id=OWNER,
        bucket="exports",
        storage_key=f"{OWNER}/{PREVIEW_ID}.zip",
        state="staging",
        lease_until=NOW + timedelta(hours=2),
        cleanup_token=None,
    )
    session = FakeSession([Result(None), Result(profile), Result(None), Result(marker)])
    monkeypatch.setattr(imports.ulid, "new", lambda: PREVIEW_ID)
    monkeypatch.setattr(imports, "get_settings", lambda: SimpleNamespace(
        supabase_bucket_exports="exports",
        import_max_upload_bytes=1024,
        import_parse_timeout_seconds=5,
        import_preview_retention_policy_version="test-policy-v1",
        import_preview_ttl_seconds=3600,
    ))
    monkeypatch.setattr(imports, "create_import_staging_marker", AsyncMock(return_value=True))
    uploaded: dict[str, bytes] = {}

    async def capture_upload(_bucket, _key, path, content_type):
        uploaded["bytes"] = path.read_bytes()
        uploaded["content_type"] = content_type

    monkeypatch.setattr(imports, "upload_object", capture_upload)

    async def inspect_normalized_archive(path, *, timeout_seconds):
        assert timeout_seconds == 5
        with imports.zipfile.ZipFile(path) as archive:
            assert archive.namelist() == ["saved/saved_posts.html"]
            assert archive.read("saved/saved_posts.html") == b"<html>saved posts</html>"
        return _parsed()

    monkeypatch.setattr(imports, "_parse_archive_bounded", inspect_normalized_archive)
    raw_html = b"<html>saved posts</html>"
    response = await imports.create_import_preview(
        file=FakeUpload(raw_html, filename="/downloads/saved_posts.html"),
        user=profile,
        session=session,
        idempotency_key="standalone-html-1",
    )

    preview = next(value for value in session.added if isinstance(value, ImportPreview))
    expected_digest = hashlib.sha256(
        b"spillthereel-import-preview-v1\0" + hashlib.sha256(raw_html).digest()
    ).hexdigest()
    assert response.preview_id == PREVIEW_ID
    assert response.total_candidates == 2
    assert preview.request_fingerprint == expected_digest
    assert uploaded["content_type"] == "application/zip"
    assert uploaded["bytes"].startswith(b"PK\x03\x04")
    with imports.zipfile.ZipFile(io.BytesIO(uploaded["bytes"])) as archive:
        assert archive.namelist() == ["saved/saved_posts.html"]


@pytest.mark.asyncio
async def test_invalid_standalone_html_with_no_supported_items_is_rejected(monkeypatch) -> None:
    marker = SimpleNamespace(
        owner_id=OWNER,
        bucket="exports",
        storage_key=f"{OWNER}/{PREVIEW_ID}.zip",
        state="staging",
        lease_until=NOW + timedelta(hours=2),
        cleanup_token=None,
    )
    session = FakeSession([Result(SimpleNamespace(id=OWNER, deleted_at=None)), Result(marker)])
    monkeypatch.setattr(imports.ulid, "new", lambda: PREVIEW_ID)
    monkeypatch.setattr(imports, "get_settings", lambda: SimpleNamespace(
        supabase_bucket_exports="exports",
        import_max_upload_bytes=1024,
        import_parse_timeout_seconds=5,
        import_preview_retention_policy_version="test-policy-v1",
        import_preview_ttl_seconds=3600,
    ))
    monkeypatch.setattr(imports, "create_import_staging_marker", AsyncMock(return_value=True))
    monkeypatch.setattr(imports, "upload_object", AsyncMock())

    async def parse_empty_archive(path, *, timeout_seconds):
        with imports.zipfile.ZipFile(path) as archive:
            assert archive.namelist() == ["saved/saved_posts.html"]
            assert archive.read("saved/saved_posts.html") == b"<html>not a Meta export</html>"
        return ParsedExport((), (), (), "parser-v1", 0, 0)

    monkeypatch.setattr(imports, "_parse_archive_bounded", parse_empty_archive)
    cleanup = AsyncMock()
    monkeypatch.setattr(imports, "_schedule_import_staging_cleanup", cleanup)
    with pytest.raises(HTTPException) as error:
        await imports.create_import_preview(
            FakeUpload(b"<html>not a Meta export</html>", filename="saved_posts.html"),
            SimpleNamespace(id=OWNER),
            session,
            idempotency_key=None,
        )

    assert error.value.status_code == 422
    assert error.value.detail == {"code": "no_supported_import_items"}
    cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_nonmatching_html_filename_is_rejected_before_staging(monkeypatch) -> None:
    marker_create = AsyncMock(return_value=True)
    monkeypatch.setattr(imports, "create_import_staging_marker", marker_create)
    monkeypatch.setattr(imports, "get_settings", lambda: SimpleNamespace(
        supabase_bucket_exports="exports",
        import_max_upload_bytes=1024,
        import_preview_retention_policy_version="test-policy-v1",
        import_preview_ttl_seconds=3600,
    ))

    with pytest.raises(HTTPException) as error:
        await imports.create_import_preview(
            FakeUpload(b"<html>anything</html>", filename="saved_collections.html"),
            SimpleNamespace(id=OWNER),
            FakeSession(),
            idempotency_key=None,
        )

    assert error.value.status_code == 400
    assert error.value.detail == {"code": "unsupported_import_file"}
    marker_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_oversized_standalone_html_is_rejected_by_request_byte_cap(monkeypatch) -> None:
    marker_create = AsyncMock(return_value=True)
    monkeypatch.setattr(imports, "create_import_staging_marker", marker_create)
    monkeypatch.setattr(imports, "get_settings", lambda: SimpleNamespace(
        supabase_bucket_exports="exports",
        import_max_upload_bytes=8,
        import_preview_retention_policy_version="test-policy-v1",
        import_preview_ttl_seconds=3600,
    ))

    with pytest.raises(HTTPException) as error:
        await imports.create_import_preview(
            FakeUpload(b"<html>too large</html>", filename="saved_posts.html"),
            SimpleNamespace(id=OWNER),
            FakeSession(),
            idempotency_key=None,
        )

    assert error.value.status_code == 413
    assert error.value.detail == {"code": "import_upload_too_large"}
    marker_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_candidate_page_is_owner_scoped_and_stable(monkeypatch) -> None:
    preview = _preview()
    rows = [_candidate("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"), _candidate("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")]
    session = FakeSession([Result(preview), Result(values=rows)])
    response = await imports.get_import_preview_candidates(
        preview_id=PREVIEW_ID,
        user=SimpleNamespace(id=OWNER),
        session=session,
        limit=1,
        cursor=None,
    )
    assert response.candidates[0].candidate_id == rows[0].candidate_id
    assert response.next_cursor == imports._encode_preview_cursor(PREVIEW_ID, 0)
    query_sql = str(session.statements[1].compile(compile_kwargs={"literal_binds": True}))
    assert "owner_id" in query_sql

    next_session = FakeSession([Result(preview), Result(values=[rows[1]])])
    next_page = await imports.get_import_preview_candidates(
        preview_id=PREVIEW_ID,
        user=SimpleNamespace(id=OWNER),
        session=next_session,
        limit=1,
        cursor=response.next_cursor,
    )
    assert [candidate.candidate_id for candidate in next_page.candidates] == [rows[1].candidate_id]
    assert next_page.next_cursor is None

    foreign_session = FakeSession([Result(None)])
    with pytest.raises(HTTPException) as error:
        await imports.get_import_preview_candidates(
            preview_id=PREVIEW_ID,
            user=SimpleNamespace(id=OTHER_OWNER),
            session=foreign_session,
            limit=50,
            cursor=None,
        )
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_same_selection_commit_replay_returns_existing_import(monkeypatch) -> None:
    candidate_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    request = imports.ImportPreviewCommitRequest(candidate_ids=[candidate_id])
    digest = imports._selection_digest(request.candidate_ids)
    import_row = SimpleNamespace(id=PREVIEW_ID, state="processing", total_parsed=0)
    session = FakeSession([Result(SimpleNamespace(id=OWNER, deleted_at=None)), Result(_preview(state="committed", selection_digest=digest)), Result(import_row)])
    monkeypatch.setattr(imports, "get_settings", lambda: SimpleNamespace(supabase_bucket_exports="exports"))
    monkeypatch.setattr(imports, "_configured_admission_policy", lambda _settings: object())
    response = await imports.commit_import_preview(PREVIEW_ID, request, SimpleNamespace(id=OWNER), session)
    assert response.import_id == PREVIEW_ID
    assert response.state == "processing"
    assert session.commits == 0
    assert session.rollbacks == 1


@pytest.mark.asyncio
async def test_successful_commit_purges_candidate_metadata_after_admission(monkeypatch) -> None:
    candidate_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    request = imports.ImportPreviewCommitRequest(candidate_ids=[candidate_id])
    preview = _preview()
    marker = SimpleNamespace(owner_id=OWNER, bucket="exports", storage_key=f"{OWNER}/{PREVIEW_ID}.zip", state="staging", lease_until=NOW + timedelta(hours=1), cleanup_token=None)
    candidate = _candidate(str(candidate_id))
    session = FakeSession([
        Result(SimpleNamespace(id=OWNER, deleted_at=None)),
        Result(preview),
        Result(marker),
        Result(values=[candidate]),
        Result(values=[]),
    ])
    monkeypatch.setattr(imports, "get_settings", lambda: SimpleNamespace(supabase_bucket_exports="exports"))
    monkeypatch.setattr(imports, "_configured_admission_policy", lambda _settings: object())
    monkeypatch.setattr(imports, "admit_bulk_import", AsyncMock(return_value=SimpleNamespace(
        candidates=(CandidateAdmission("b" * 64, SourceBucket.INSTAGRAM, AdmissionStatus.ADMITTED),)
    )))
    monkeypatch.setattr(imports, "record_import_processing_intent", AsyncMock(return_value="intent-1"))
    dispatch = AsyncMock()
    monkeypatch.setattr(imports, "dispatch_after_commit", dispatch)

    response = await imports.commit_import_preview(
        PREVIEW_ID, request, SimpleNamespace(id=OWNER), session
    )

    assert response.import_id == PREVIEW_ID
    candidate_delete = next(statement for statement in session.statements if isinstance(statement, Delete))
    sql = str(candidate_delete.compile(compile_kwargs={"literal_binds": True}))
    assert "import_preview_candidates.owner_id" in sql
    assert session.commits == 1
    assert dispatch.await_args.args == (["intent-1"],)


@pytest.mark.asyncio
async def test_committed_preview_does_not_serve_candidate_metadata(monkeypatch) -> None:
    preview = _preview(state="committed", selection_digest="c" * 64)
    session = FakeSession([Result(preview)])

    with pytest.raises(HTTPException) as error:
        await imports.get_import_preview_candidates(
            PREVIEW_ID, SimpleNamespace(id=OWNER), session, limit=50, cursor=None
        )

    assert error.value.status_code == 410
    assert error.value.detail == {"code": "import_preview_committed"}
    assert len(session.statements) == 1


@pytest.mark.asyncio
async def test_changed_selection_after_commit_conflicts(monkeypatch) -> None:
    request = imports.ImportPreviewCommitRequest(candidate_ids=[UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")])
    session = FakeSession([Result(SimpleNamespace(id=OWNER, deleted_at=None)), Result(_preview(state="committed", selection_digest="c" * 64))])
    monkeypatch.setattr(imports, "get_settings", lambda: SimpleNamespace(supabase_bucket_exports="exports"))
    monkeypatch.setattr(imports, "_configured_admission_policy", lambda _settings: object())
    with pytest.raises(HTTPException) as error:
        await imports.commit_import_preview(PREVIEW_ID, request, SimpleNamespace(id=OWNER), session)
    assert error.value.status_code == 409
    assert session.rollbacks == 1


@pytest.mark.asyncio
async def test_foreign_or_unknown_candidate_id_is_rejected_without_leaking_identity(monkeypatch) -> None:
    candidate_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    request = imports.ImportPreviewCommitRequest(candidate_ids=[candidate_id])
    preview = _preview()
    marker = SimpleNamespace(
        owner_id=OWNER,
        bucket="exports",
        storage_key=f"{OWNER}/{PREVIEW_ID}.zip",
        state="staging",
        lease_until=NOW + timedelta(hours=1),
        cleanup_token=None,
    )
    session = FakeSession([
        Result(SimpleNamespace(id=OWNER, deleted_at=None)),
        Result(preview),
        Result(marker),
        Result(values=[]),
    ])
    monkeypatch.setattr(imports, "get_settings", lambda: SimpleNamespace(supabase_bucket_exports="exports"))
    monkeypatch.setattr(imports, "_configured_admission_policy", lambda _settings: object())
    with pytest.raises(HTTPException) as error:
        await imports.commit_import_preview(PREVIEW_ID, request, SimpleNamespace(id=OWNER), session)
    assert error.value.status_code == 422
    assert error.value.detail == {"code": "invalid_import_preview_selection"}
    assert session.commits == 0
    assert session.rollbacks == 1


@pytest.mark.asyncio
async def test_expired_preview_becomes_unusable(monkeypatch) -> None:
    expired = _preview(expires=NOW - timedelta(seconds=1))
    session = FakeSession([Result(expired)])
    with pytest.raises(HTTPException) as error:
        await imports.get_import_preview_candidates(
            PREVIEW_ID,
            SimpleNamespace(id=OWNER),
            session,
            limit=50,
            cursor=None,
        )
    assert error.value.status_code == 410
    assert expired.state == "expired"
    assert session.commits == 1


@pytest.mark.asyncio
async def test_deleted_account_cannot_commit_an_open_preview(monkeypatch) -> None:
    request = imports.ImportPreviewCommitRequest(
        candidate_ids=[UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")]
    )
    session = FakeSession([Result(None)])
    monkeypatch.setattr(imports, "get_settings", lambda: SimpleNamespace(supabase_bucket_exports="exports"))
    monkeypatch.setattr(imports, "_configured_admission_policy", lambda _settings: object())
    with pytest.raises(HTTPException) as error:
        await imports.commit_import_preview(
            PREVIEW_ID,
            request,
            SimpleNamespace(id=OWNER),
            session,
        )
    assert error.value.status_code == 409
    assert error.value.detail == {"code": "account_unavailable"}
    assert session.commits == 0
    assert session.rollbacks == 1


@pytest.mark.asyncio
async def test_quota_rejection_rolls_back_all_reservations_and_keeps_preview_open(monkeypatch) -> None:
    candidate_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    request = imports.ImportPreviewCommitRequest(candidate_ids=[candidate_id])
    preview = _preview()
    marker = SimpleNamespace(
        owner_id=OWNER,
        bucket="exports",
        storage_key=f"{OWNER}/{PREVIEW_ID}.zip",
        state="staging",
        lease_until=NOW + timedelta(hours=1),
        cleanup_token=None,
    )
    candidate = _candidate()
    candidate.candidate_id = str(candidate_id)
    session = FakeSession([
        Result(SimpleNamespace(id=OWNER, deleted_at=None)),
        Result(preview),
        Result(marker),
        Result(values=[candidate]),
        Result(values=[]),
    ])
    monkeypatch.setattr(imports, "get_settings", lambda: SimpleNamespace(supabase_bucket_exports="exports"))
    monkeypatch.setattr(imports, "_configured_admission_policy", lambda _settings: object())
    monkeypatch.setattr(imports, "admit_bulk_import", AsyncMock(return_value=SimpleNamespace(
        candidates=(CandidateAdmission("b" * 64, SourceBucket.INSTAGRAM, AdmissionStatus.LIMIT_REACHED),)
    )))
    with pytest.raises(HTTPException) as error:
        await imports.commit_import_preview(PREVIEW_ID, request, SimpleNamespace(id=OWNER), session)
    assert error.value.status_code == 409
    assert error.value.detail == {"code": "import_allowance_exceeded"}
    assert session.rollbacks == 1
    assert session.commits == 0
    assert preview.state == "open"
    assert not any(isinstance(value, Import) for value in session.added)


@pytest.mark.asyncio
async def test_preview_create_unknown_commit_ack_preserves_staged_source(monkeypatch) -> None:
    profile = SimpleNamespace(id=OWNER, deleted_at=None)
    marker = SimpleNamespace(
        owner_id=OWNER,
        bucket="exports",
        storage_key=f"{OWNER}/{PREVIEW_ID}.zip",
        state="staging",
        lease_until=NOW + timedelta(hours=2),
        cleanup_token=None,
    )
    session = FakeSession([Result(profile), Result(marker)], commit_error=RuntimeError("lost ack"))
    monkeypatch.setattr(imports.ulid, "new", lambda: PREVIEW_ID)
    monkeypatch.setattr(imports, "get_settings", lambda: SimpleNamespace(
        supabase_bucket_exports="exports",
        import_max_upload_bytes=1024,
        import_parse_timeout_seconds=5,
        import_preview_retention_policy_version="test-policy-v1",
        import_preview_ttl_seconds=3600,
    ))
    monkeypatch.setattr(imports, "create_import_staging_marker", AsyncMock(return_value=True))
    monkeypatch.setattr(imports, "upload_object", AsyncMock())
    monkeypatch.setattr(imports, "_parse_archive_bounded", AsyncMock(return_value=_parsed()))
    cleanup = AsyncMock()
    monkeypatch.setattr(imports, "_schedule_import_staging_cleanup", cleanup)

    class FreshSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_args):
            return None
        async def execute(self, _statement):
            raise OSError("database unavailable during reconciliation")

    monkeypatch.setattr(imports, "async_sessionmaker", lambda *_args, **_kwargs: lambda: FreshSession())
    with pytest.raises(RuntimeError, match="lost ack"):
        await imports.create_import_preview(
            FakeUpload(), profile, session, idempotency_key=None
        )
    cleanup.assert_not_awaited()
