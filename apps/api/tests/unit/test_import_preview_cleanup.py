from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import Delete, Select

from app.services.imports import preview_cleanup

OWNER_A = "11111111-1111-1111-1111-111111111111"
OWNER_B = "22222222-2222-2222-2222-222222222222"
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


class _Result:
    def __init__(self, *, rows=(), value=None):
        self.rows = list(rows)
        self.value = value
        self.rowcount = 1

    def all(self):
        return self.rows

    def scalar_one_or_none(self):
        return self.value


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _Session:
    def __init__(self, results):
        self.results = iter(results)
        self.statements = []
        self.options = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def begin(self):
        return _Transaction()

    async def connection(self, *, execution_options):
        self.options = execution_options

    async def execute(self, statement):
        self.statements.append(statement)
        return next(self.results)


def _factory(monkeypatch, session, expected_bind):
    created = []

    def make_session_factory(bind, **kwargs):
        assert bind is expected_bind
        assert kwargs["expire_on_commit"] is False
        created.append(True)
        return lambda: session

    monkeypatch.setattr(preview_cleanup, "async_sessionmaker", make_session_factory)
    return created


@pytest.mark.asyncio
async def test_deletes_expired_open_preview_in_one_fresh_read_committed_transaction(monkeypatch):
    bind = object()
    preview_id = "01K7F9R2J5YMXB3Z4Z0H7C1Q8W"
    profile = SimpleNamespace(id=OWNER_A, deleted_at=None)
    preview = SimpleNamespace(
        id=preview_id,
        owner_id=OWNER_A,
        state="open",
        expires_at=NOW - timedelta(seconds=1),
    )
    session = _Session(
        [
            _Result(rows=[(preview_id, OWNER_A)]),
            _Result(value=profile),
            _Result(value=preview),
            _Result(),
        ]
    )
    created = _factory(monkeypatch, session, bind)

    result = await preview_cleanup.sweep_expired_import_previews(
        bind=bind, limit=20, now=NOW
    )

    assert created == [True]
    assert result == preview_cleanup.ImportPreviewCleanupResult(examined=1, deleted=1)
    assert session.options == {"isolation_level": "READ COMMITTED"}
    assert len(session.statements) == 4
    candidate_query, profile_lock, preview_lock, deletion = session.statements
    assert isinstance(candidate_query, Select)
    assert isinstance(profile_lock, Select) and "FOR UPDATE" in str(profile_lock)
    assert isinstance(preview_lock, Select) and "FOR UPDATE" in str(preview_lock)
    assert isinstance(deletion, Delete)
    assert "profiles.id" in str(profile_lock)
    assert "import_previews.owner_id" in str(preview_lock)
    assert "import_previews.state" in str(deletion)
    assert "import_previews.expires_at" in str(deletion)
    assert "LIMIT" in str(candidate_query).upper()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("profile", "expected_skipped_owner"),
    [(None, 1), (SimpleNamespace(id=OWNER_A, deleted_at=NOW), 1)],
)
async def test_missing_or_deleted_owner_is_never_cleaned(
    monkeypatch, profile, expected_skipped_owner
):
    bind = object()
    session = _Session([_Result(rows=[("preview-a", OWNER_A)]), _Result(value=profile)])
    _factory(monkeypatch, session, bind)

    result = await preview_cleanup.sweep_expired_import_previews(
        bind=bind, now=NOW
    )

    assert result.deleted == 0
    assert result.skipped_owner == expected_skipped_owner
    assert len(session.statements) == 2  # No preview lock or delete follows.


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "locked_preview",
    [
        None,
        SimpleNamespace(state="committed", expires_at=NOW - timedelta(days=1)),
        SimpleNamespace(state="open", expires_at=NOW + timedelta(seconds=1)),
    ],
)
async def test_changed_or_committed_preview_is_preserved(monkeypatch, locked_preview):
    bind = object()
    profile = SimpleNamespace(id=OWNER_A, deleted_at=None)
    session = _Session(
        [
            _Result(rows=[("preview-a", OWNER_A)]),
            _Result(value=profile),
            _Result(value=locked_preview),
        ]
    )
    _factory(monkeypatch, session, bind)

    result = await preview_cleanup.sweep_expired_import_previews(bind=bind, now=NOW)

    assert result.deleted == 0
    assert result.skipped_changed == 1
    assert len(session.statements) == 3


@pytest.mark.asyncio
async def test_candidate_scan_is_bounded_and_uses_injected_time(monkeypatch):
    bind = object()
    session = _Session([_Result(rows=[])])
    _factory(monkeypatch, session, bind)

    result = await preview_cleanup.sweep_expired_import_previews(
        bind=bind,
        limit=preview_cleanup.MAX_PREVIEW_CLEANUP_BATCH + 50,
        now=NOW,
    )

    query = session.statements[0]
    rendered = str(query)
    assert result.examined == result.deleted == 0
    assert query._limit_clause.value == preview_cleanup.MAX_PREVIEW_CLEANUP_BATCH
    assert query._where_criteria
    assert "import_previews.expires_at" in rendered
    assert "import_previews.state" in rendered
    assert NOW in query.compile().params.values()


@pytest.mark.asyncio
async def test_zero_limit_still_makes_progress_without_unbounded_scan(monkeypatch):
    bind = object()
    session = _Session([_Result(rows=[])])
    _factory(monkeypatch, session, bind)

    await preview_cleanup.sweep_expired_import_previews(bind=bind, limit=0, now=NOW)

    assert session.statements[0]._limit_clause.value == 1

