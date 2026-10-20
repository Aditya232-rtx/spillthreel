"""Local-only contract test for the stored Instagram import worker path.

This test deliberately exercises the real ZIP parser and import processor while
replacing only the object-store download and post-commit task transport. It must
never run against a hosted database or provider.
"""

from __future__ import annotations

import ipaddress
import os
import uuid
import zipfile
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.engine import make_url

from app.db.engine import session_scope
from app.db.models import (
    Base,
    Category,
    Collection,
    CollectionItem,
    Import,
    ImportCollection,
    ImportEntry,
    ImportEntryOccurrence,
    ImportPreview,
    ImportPreviewCandidate,
    ImportStaging,
    Item,
    Profile,
    TaskOutbox,
    UserDeletionJob,
)
from app.services.imports.admission import reservation_key_for_url
from app.services.imports import ig_pipeline
from app.tasks.outbox import record_task_intent
from app.tasks.worker_claims import (
    TargetExecution,
    WorkerIdentity,
    claim_target_intent,
)


pytestmark = pytest.mark.asyncio


def _local_test_url() -> str:
    if os.environ.get("SPILLTHEREEL_ALLOW_LOCAL_SCHEMA_TESTS") != "1":
        pytest.skip(
            "set SPILLTHEREEL_ALLOW_LOCAL_SCHEMA_TESTS=1 only for disposable local PostgreSQL"
        )
    value = os.environ.get("TEST_DATABASE_URL")
    if not value:
        pytest.skip("set TEST_DATABASE_URL explicitly to run local PostgreSQL integration tests")

    parsed = urlsplit(value)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"postgres", "postgresql", "postgresql+asyncpg"}:
        pytest.fail("TEST_DATABASE_URL must use PostgreSQL")
    if parsed.query or parsed.fragment:
        pytest.fail("TEST_DATABASE_URL query and fragment overrides are forbidden")
    if hostname not in {"localhost", "127.0.0.1", "::1"}:
        pytest.fail("TEST_DATABASE_URL must point to localhost")
    return value


async def _verify_local_postgres17(engine: AsyncEngine) -> None:
    """Refuse schema creation unless the connected server is local PG17."""
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text("SELECT inet_server_addr()::text, current_setting('server_version_num')")
            )
        ).one()
    server_address, version_number = row
    try:
        address = ipaddress.ip_interface(server_address).ip
    except ValueError:
        pytest.fail("local PostgreSQL integration tests require a loopback server address")
    if not address.is_loopback:
        pytest.fail("local PostgreSQL integration tests refuse non-loopback server addresses")
    if not str(version_number).startswith("17"):
        pytest.fail("local PostgreSQL integration tests require PostgreSQL 17")


def _zip_payload() -> bytes:
    html = (
        "<html><body>"
        "<div class='pam uiBoxWhite noborder'><table>"
        "<tr><td>URL</td><td><a href='https://www.instagram.com/p/abc0/'>link</a></td></tr>"
        "</table></div>"
        "<div class='pam uiBoxWhite noborder'><table>"
        "<tr><td>URL</td><td><a href='https://www.instagram.com/p/abc1/'>link</a></td></tr>"
        "</table></div>"
        "</body></html>"
    ).encode()
    from io import BytesIO

    archive = BytesIO()
    with zipfile.ZipFile(archive, mode="w", compression=zipfile.ZIP_DEFLATED) as zipped:
        zipped.writestr("saved/saved_posts.html", html)
    return archive.getvalue()


async def test_stored_import_processes_only_admitted_zip_items_in_isolated_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parse a mocked stored ZIP and persist only its selected/admitted item work."""
    database_url = _local_test_url()
    parsed_url = make_url(database_url)
    if parsed_url.drivername == "postgresql":
        parsed_url = parsed_url.set(drivername="postgresql+asyncpg")

    schema = f"stored_import_{uuid.uuid4().hex}"
    admin_engine = create_async_engine(
        parsed_url,
        pool_pre_ping=True,
        connect_args={
            "statement_cache_size": 0,
            "prepared_statement_cache_size": 0,
        },
    )
    try:
        await _verify_local_postgres17(admin_engine)
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            await connection.execute(text(f'REVOKE ALL ON SCHEMA "{schema}" FROM PUBLIC'))
    except Exception:
        await admin_engine.dispose()
        raise

    try:
        isolated_engine = create_async_engine(
            parsed_url,
            pool_pre_ping=True,
            connect_args={
                "statement_cache_size": 0,
                "prepared_statement_cache_size": 0,
                "server_settings": {"search_path": f'"{schema}",pg_catalog'},
            },
        )
    except Exception:
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()
        raise
    factory = async_sessionmaker(isolated_engine, expire_on_commit=False)
    model_tables = [
        Profile.__table__,
        Category.__table__,
        Item.__table__,
        Collection.__table__,
        CollectionItem.__table__,
        Import.__table__,
        ImportEntry.__table__,
        ImportEntryOccurrence.__table__,
        ImportCollection.__table__,
        ImportStaging.__table__,
        ImportPreview.__table__,
        ImportPreviewCandidate.__table__,
        TaskOutbox.__table__,
        UserDeletionJob.__table__,
    ]
    try:
        async with isolated_engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: Base.metadata.create_all(
                    sync_connection, tables=model_tables
                )
            )

        # `ig_pipeline` and the worker-claim module import the same db.engine
        # module object, so this replaces the session factory only for this test.
        import app.db.engine as db_engine

        monkeypatch.setattr(db_engine, "_session_factory", factory)

        user_id = str(uuid.uuid4())
        import_id = "01J8Z7N5C9S0X3Q7W1M4K6A2B8"
        admitted_key = reservation_key_for_url("https://www.instagram.com/p/abc0/")
        async with session_scope() as session:
            session.add(Profile(id=user_id))
            await session.flush()
            session.add(Import(id=import_id, user_id=user_id, state="processing", admission_enforced=True))
            await session.flush()
            intent = await record_task_intent(
                session,
                "import",
                {
                    "import_id": import_id,
                    "user_id": user_id,
                    "admitted_reservation_keys": [admitted_key],
                },
                target_type="import",
                target_id=import_id,
                generation=1,
            )
            await session.commit()

        identity = WorkerIdentity(intent.id, "import", "import", import_id, 1)
        claim = await claim_target_intent(
            identity,
            owner_id=user_id,
            payload_fields={"admitted_reservation_keys": [admitted_key]},
        )
        assert claim.status == "claimed"
        execution = TargetExecution(identity, claim.token, user_id)

        dispatched: list[tuple[tuple[object, ...], dict[str, object]]] = []

        async def fake_download(bucket: str, storage_key: str, destination: Path) -> None:
            assert storage_key == "synthetic/import.zip"
            Path(destination).write_bytes(_zip_payload())

        async def capture_dispatch(*args: object, **kwargs: object) -> None:
            dispatched.append((args, kwargs))

        import app.services.media.supabase_storage as storage

        monkeypatch.setattr(storage, "download_object", fake_download)
        monkeypatch.setattr(ig_pipeline, "dispatch_after_commit", capture_dispatch)
        monkeypatch.setattr(
            ig_pipeline,
            "get_settings",
            lambda: SimpleNamespace(supabase_bucket_exports="exports"),
        )

        outcome = await ig_pipeline.process_stored_import(
            import_id,
            user_id,
            "synthetic/import.zip",
            admitted_reservation_keys={admitted_key},
            execution=execution,
        )

        assert outcome.ok is True
        assert outcome.total_parsed == 2
        assert outcome.queued == 1
        async with session_scope() as session:
            entries = list(
                (await session.scalars(select(ImportEntry).where(ImportEntry.import_id == import_id))).all()
            )
            occurrences = list(
                (
                    await session.scalars(
                        select(ImportEntryOccurrence).where(
                            ImportEntryOccurrence.import_id == import_id
                        )
                    )
                ).all()
            )
            items = list((await session.scalars(select(Item).where(Item.user_id == user_id))).all())
            outboxes = list((await session.scalars(select(TaskOutbox))).all())
            imported = await session.get(Import, import_id)

        assert imported is not None
        assert imported.total_parsed == 2
        assert imported.full_queued == 1
        assert {entry.reservation_key for entry in entries} == {admitted_key}
        assert entries[0].state == "queued"
        assert len(items) == 1
        assert len(occurrences) == 2
        assert {occurrence.entry_reservation_key for occurrence in occurrences if occurrence.entry_reservation_key} == {
            admitted_key
        }
        admitted_occurrences = [occurrence for occurrence in occurrences if occurrence.outcome == "admitted"]
        assert len(admitted_occurrences) == 1
        assert admitted_occurrences[0].source_kind == "media"
        assert admitted_occurrences[0].entry_reservation_key == admitted_key
        ingest_outboxes = [row for row in outboxes if row.task_type != "import"]
        assert len(ingest_outboxes) == 1
        assert len(dispatched) == 1
    finally:
        await isolated_engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()
