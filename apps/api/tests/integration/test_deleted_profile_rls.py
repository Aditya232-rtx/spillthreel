"""Deleted-account row policies against a per-run PostgreSQL database.

This test applies the repository's real Alembic head to a newly-created local
database. It never changes the shared ``public``/``auth`` schemas of the
TEST_DATABASE_URL database, nor does it fall back to DATABASE_URL or an app
``.env`` file. The temporary database is dropped even when migration or checks
fail.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.engine import make_url

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
REQUIRED_ROLES = ("anon", "authenticated", "service_role")
API_ROOT = Path(__file__).resolve().parents[2]


def _postgres_dsn(url: str) -> str:
    parsed = make_url(url)
    if parsed.drivername not in {"postgres", "postgresql", "postgresql+asyncpg"}:
        raise ValueError("TEST_DATABASE_URL must use PostgreSQL")
    host = parsed.host
    if host not in {None, "localhost", "127.0.0.1", "::1"}:
        raise ValueError("RLS integration tests only accept a local PostgreSQL endpoint")
    return parsed.set(drivername="postgresql").render_as_string(hide_password=False)


def _async_url(url: str, database: str | None = None) -> str:
    parsed = make_url(url)
    parsed = parsed.set(drivername="postgresql+asyncpg")
    if database is not None:
        parsed = parsed.set(database=database)
    return parsed.render_as_string(hide_password=False)


@dataclass(frozen=True)
class IsolatedRlsDatabase:
    name: str
    url: str
    test_user: str


@pytest_asyncio.fixture
async def isolated_rls_database(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[IsolatedRlsDatabase]:
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is unset; isolated deleted-profile RLS test not run")

    try:
        admin_dsn = _postgres_dsn(TEST_DATABASE_URL)
    except ValueError as exc:
        pytest.fail(str(exc))

    try:
        admin = await asyncpg.connect(admin_dsn, timeout=10)
    except Exception as exc:
        pytest.fail(f"local PostgreSQL connection failed ({type(exc).__name__})")

    database_name = f"r03_deleted_rls_{uuid.uuid4().hex}"
    database_created = False
    try:
        vector_available = await admin.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'vector')"
        )
        capabilities = await admin.fetchrow(
            "SELECT r.rolsuper OR r.rolcreatedb AS can_create_database, "
            "current_database() AS database_name "
            "FROM pg_roles r WHERE r.rolname = current_user"
        )
        if capabilities is None or not capabilities["can_create_database"]:
            pytest.skip("local test role lacks CREATEDB; refusing to migrate a shared database")

        roles = await admin.fetch(
            "SELECT rolname FROM pg_roles WHERE rolname = ANY($1::text[])",
            list(REQUIRED_ROLES),
        )
        available_roles = {row["rolname"] for row in roles}
        if available_roles != set(REQUIRED_ROLES):
            pytest.skip(
                "local cluster must already provide anon, authenticated, and service_role; "
                "test will not create cluster-global Supabase roles"
            )

        can_assume_roles = await admin.fetchval(
            "SELECT rolsuper OR "
            "(pg_has_role(current_user, 'authenticated', 'MEMBER') "
            "AND pg_has_role(current_user, 'anon', 'MEMBER') "
            "AND pg_has_role(current_user, 'service_role', 'MEMBER')) "
            "FROM pg_roles WHERE rolname = current_user"
        )
        if not can_assume_roles:
            pytest.skip("local test role cannot SET ROLE to Supabase API roles")

        await admin.execute(f'CREATE DATABASE "{database_name}"')
        await admin.execute(f'ALTER DATABASE "{database_name}" SET search_path TO public')
        database_created = True
    except pytest.skip.Exception:
        raise
    except Exception as exc:
        pytest.fail(f"isolated test database setup failed ({type(exc).__name__})")
    finally:
        await admin.close()

    database_url = _async_url(TEST_DATABASE_URL, database_name)
    test_user = str(uuid.uuid4())
    try:
        isolated = await asyncpg.connect(_postgres_dsn(database_url), timeout=10)
        try:
            await isolated.execute("CREATE SCHEMA auth")
            await isolated.execute(
                "CREATE TABLE auth.users ("
                "id uuid PRIMARY KEY, raw_user_meta_data jsonb, email text)"
            )
            await isolated.execute(
                "CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS "
                "$$ SELECT NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid $$"
            )
            await isolated.execute("GRANT USAGE ON SCHEMA auth TO PUBLIC")
        finally:
            await isolated.close()

        # Alembic's env.py obtains its URL from Settings, so point both settings
        # at this one-run database before invoking the real current head.
        monkeypatch.setenv("DATABASE_URL", database_url)
        monkeypatch.setenv("DATABASE_URL_DIRECT", database_url)
        from app.settings import get_settings

        get_settings.cache_clear()
        current_direct_db = make_url(get_settings().database_url_direct or "").database
        if current_direct_db != database_name:
            raise RuntimeError("migration settings did not select the generated test database")
        config = Config(str(API_ROOT / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
        if vector_available:
            await asyncio.to_thread(command.upgrade, config, "head")
        else:
            # The tombstone policy does not depend on vector storage. On a
            # vanilla PostgreSQL installation, apply the real base migration,
            # provide only the table/policies needed by the later hardening
            # revision, stamp the omitted pgvector extension revision, and
            # then apply every remaining Alembic revision including 0008.
            await asyncio.to_thread(command.upgrade, config, "0001_initial")
            isolated = await asyncpg.connect(_postgres_dsn(database_url), timeout=10)
            try:
                await isolated.execute(
                    "CREATE TABLE public.item_embeddings ("
                    "item_id text PRIMARY KEY REFERENCES public.items(id) ON DELETE CASCADE, "
                    "user_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE, "
                    "embedding text NOT NULL, created_at timestamptz NOT NULL DEFAULT now())"
                )
                await isolated.execute("ALTER TABLE public.item_embeddings ENABLE ROW LEVEL SECURITY")
                await isolated.execute(
                    "CREATE POLICY item_embeddings_select_own ON public.item_embeddings "
                    "FOR SELECT USING (auth.uid() = user_id)"
                )
                await isolated.execute(
                    "CREATE POLICY item_embeddings_insert_own ON public.item_embeddings "
                    "FOR INSERT WITH CHECK (auth.uid() = user_id)"
                )
                await isolated.execute(
                    "CREATE POLICY item_embeddings_update_own ON public.item_embeddings "
                    "FOR UPDATE USING (auth.uid() = user_id)"
                )
                await isolated.execute(
                    "CREATE POLICY item_embeddings_delete_own ON public.item_embeddings "
                    "FOR DELETE USING (auth.uid() = user_id)"
                )
            finally:
                await isolated.close()
            await asyncio.to_thread(command.stamp, config, "20260930_0001_pgvector_memory")
            await asyncio.to_thread(command.upgrade, config, "head")
    except Exception as exc:
        if database_created:
            try:
                cleanup = await asyncpg.connect(admin_dsn, timeout=10)
                try:
                    await cleanup.execute(
                        f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)'
                    )
                finally:
                    await cleanup.close()
            except Exception:
                pass
        detail = str(exc).splitlines()[0][:240]
        pytest.fail(f"isolated migrations or RLS fixture setup failed ({type(exc).__name__}: {detail})")

    try:
        yield IsolatedRlsDatabase(database_name, database_url, test_user)
    finally:
        # The test creates no objects in the source database. DROP DATABASE is
        # scoped to this generated name, and FORCE only closes this run's
        # migration/test connections.
        try:
            cleanup = await asyncpg.connect(admin_dsn, timeout=10)
            try:
                await cleanup.execute(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)')
            finally:
                await cleanup.close()
        except Exception as exc:
            pytest.fail(f"isolated test database cleanup failed ({type(exc).__name__})")
        from app.settings import get_settings

        get_settings.cache_clear()


@asynccontextmanager
async def _as_role(
    connection: asyncpg.Connection, role: str, user_id: str
) -> AsyncIterator[None]:
    # Role names are a fixed allowlist of locally-provisioned Supabase roles.
    if role not in REQUIRED_ROLES:
        raise ValueError("unexpected database role")
    async with connection.transaction():
        await connection.execute(f"SET LOCAL ROLE {role}")
        await connection.execute(
            "SELECT set_config('request.jwt.claim.sub', $1, true)",
            "" if role == "anon" else user_id,
        )
        yield


async def _count(
    connection: asyncpg.Connection, table: str, user_id: str, role: str = "authenticated"
) -> int:
    if table not in {"profiles", "items", "item_categories", "collections", "collection_items"}:
        raise ValueError("unexpected table")
    async with _as_role(connection, role, user_id):
        return await connection.fetchval(f"SELECT count(*) FROM public.{table}")


@pytest.mark.asyncio
async def test_tombstoned_profile_cannot_access_owned_rows_or_child_paths(
    isolated_rls_database: IsolatedRlsDatabase,
) -> None:
    db = isolated_rls_database
    connection = await asyncpg.connect(_postgres_dsn(db.url), timeout=10)
    item_id = f"rls-item-{uuid.uuid4().hex}"
    transient_item_id = f"rls-transient-{uuid.uuid4().hex}"
    category_id = f"rls-category-{uuid.uuid4().hex}"
    collection_id = f"rls-collection-{uuid.uuid4().hex}"
    try:
        profile_locations = await connection.fetch(
            "SELECT n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relname = 'profiles' AND c.relkind = 'r'"
        )
        assert any(row["nspname"] == "public" for row in profile_locations), (
            f"profiles table schema mismatch: {[row['nspname'] for row in profile_locations]}; "
            f"database={await connection.fetchval('SELECT current_database()')}; "
            f"tables={await connection.fetch('SELECT schemaname, tablename FROM pg_tables WHERE tablename = \'profiles\'')}"
        )
        # Make client privileges sufficient so the assertions exercise RLS,
        # rather than passing only because the role lacks table grants. This
        # database is disposable and unique to the current test run.
        await connection.execute(
            "GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role"
        )
        await connection.execute(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "
            "public.profiles, public.items, public.categories, public.item_categories, "
            "public.collections, public.collection_items "
            "TO anon, authenticated, service_role"
        )
        await connection.execute(
            "INSERT INTO auth.users (id, raw_user_meta_data, email) VALUES ($1, '{}'::jsonb, $2)",
            db.test_user,
            f"{uuid.uuid4().hex}@example.test",
        )
        await connection.execute(
            "INSERT INTO public.items "
            "(id, user_id, source_url, source_url_norm, platform, state, saved_at, source_type) "
            "VALUES ($1, $2, 'https://example.test/item', $3, 'youtube', 'queued', now(), 'share_extension')",
            item_id,
            db.test_user,
            f"https://example.test/{uuid.uuid4().hex}",
        )
        await connection.execute(
            "INSERT INTO public.categories (id, user_id, name, source, bg_color) "
            "VALUES ($1, $2, 'Test', 'user', '#fff')",
            category_id,
            db.test_user,
        )
        await connection.execute(
            "INSERT INTO public.item_categories (item_id, category_id, assigned_by) "
            "VALUES ($1, $2, 'user')",
            item_id,
            category_id,
        )
        await connection.execute(
            "INSERT INTO public.collections (id, user_id, name, privacy, source) "
            "VALUES ($1, $2, 'Test collection', 'private', 'user_created')",
            collection_id,
            db.test_user,
        )
        await connection.execute(
            "INSERT INTO public.collection_items (collection_id, item_id, owner_id) "
            "VALUES ($1, $2, $3)",
            collection_id,
            item_id,
            db.test_user,
        )

        # Authenticated owner CRUD is functional while the profile is live.
        async with _as_role(connection, "authenticated", db.test_user):
            assert await connection.fetchval(
                "SELECT id FROM public.profiles WHERE id = $1", db.test_user
            ) == uuid.UUID(db.test_user)
            assert await connection.fetchval(
                "UPDATE public.profiles SET display_name = 'live' WHERE id = $1 RETURNING display_name",
                db.test_user,
            ) == "live"
            assert await connection.fetchval(
                "SELECT id FROM public.items WHERE id = $1", item_id
            ) == item_id
            assert await connection.fetchval(
                "INSERT INTO public.items "
                "(id, user_id, source_url, source_url_norm, platform, state, saved_at, source_type) "
                "VALUES ($1, $2, 'https://example.test/transient', $3, 'youtube', 'queued', now(), 'share_extension') "
                "RETURNING id",
                transient_item_id,
                db.test_user,
                f"https://example.test/{uuid.uuid4().hex}",
            ) == transient_item_id
            assert await connection.fetchval(
                "UPDATE public.items SET title = 'updated' WHERE id = $1 RETURNING title",
                transient_item_id,
            ) == "updated"
            assert await connection.fetchval(
                "DELETE FROM public.items WHERE id = $1 RETURNING id", transient_item_id
            ) == transient_item_id

        assert await _count(connection, "item_categories", db.test_user) == 1
        assert await _count(connection, "collections", db.test_user) == 1
        assert await _count(connection, "collection_items", db.test_user) == 1
        # The public/anonymous role has table privileges in this isolated DB,
        # but no row policy should expose an account or its resources.
        assert await _count(connection, "profiles", db.test_user, "anon") == 0
        assert await _count(connection, "items", db.test_user, "anon") == 0

        # The owner models deletion initiation. Once deleted_at is set, even
        # an attempted client-side clear is filtered by the profile policy.
        await connection.execute(
            "UPDATE public.profiles SET deleted_at = now() WHERE id = $1", db.test_user
        )
        async with _as_role(connection, "authenticated", db.test_user):
            assert await connection.fetchval(
                "SELECT count(*) FROM public.profiles WHERE id = $1", db.test_user
            ) == 0
            assert await connection.fetchval(
                "UPDATE public.profiles SET deleted_at = NULL WHERE id = $1 RETURNING id",
                db.test_user,
            ) is None
            assert await connection.fetchval(
                "UPDATE public.profiles SET display_name = 'after-delete' WHERE id = $1 RETURNING id",
                db.test_user,
            ) is None
            assert await connection.fetchval(
                "SELECT count(*) FROM public.items WHERE id = $1", item_id
            ) == 0
            assert await connection.fetchval(
                "UPDATE public.items SET title = 'after-delete' WHERE id = $1 RETURNING id",
                item_id,
            ) is None
            assert await connection.fetchval(
                "DELETE FROM public.items WHERE id = $1 RETURNING id", item_id
            ) is None
            assert await connection.fetchval(
                "SELECT count(*) FROM public.item_categories WHERE item_id = $1", item_id
            ) == 0
            assert await connection.fetchval(
                "UPDATE public.item_categories SET assigned_by = 'after-delete' "
                "WHERE item_id = $1 RETURNING item_id",
                item_id,
            ) is None
            assert await connection.fetchval(
                "SELECT count(*) FROM public.collections WHERE id = $1", collection_id
            ) == 0
            assert await connection.fetchval(
                "UPDATE public.collections SET name = 'after-delete' "
                "WHERE id = $1 RETURNING id",
                collection_id,
            ) is None
            assert await connection.fetchval(
                "SELECT count(*) FROM public.collection_items WHERE item_id = $1", item_id
            ) == 0
            assert await connection.fetchval(
                "DELETE FROM public.collection_items WHERE item_id = $1 RETURNING item_id",
                item_id,
            ) is None

        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with _as_role(connection, "authenticated", db.test_user):
                await connection.execute(
                    "INSERT INTO public.items "
                    "(id, user_id, source_url, source_url_norm, platform, state, saved_at, source_type) "
                    "VALUES ($1, $2, 'https://example.test/denied', $3, 'youtube', 'queued', now(), 'share_extension')",
                    f"rls-denied-{uuid.uuid4().hex}",
                    db.test_user,
                    f"https://example.test/{uuid.uuid4().hex}",
                )

        # Anonymous access remains closed before and after deletion, even when
        # table privileges exist, and there is no accidental FORCE RLS change.
        assert await _count(connection, "profiles", db.test_user, "anon") == 0
        assert await _count(connection, "items", db.test_user, "anon") == 0
        deleted_at = await connection.fetchval(
            "SELECT deleted_at FROM public.profiles WHERE id = $1", db.test_user
        )
        assert deleted_at is not None

        forced_tables = await connection.fetchval(
            "SELECT count(*) FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relname = ANY($1::text[]) "
            "AND c.relforcerowsecurity",
            ["profiles", "items", "item_categories", "collections", "collection_items"],
        )
        assert forced_tables == 0
        # The database/table owner and the configured service role retain their
        # administrative visibility; this catches an accidental FORCE RLS.
        assert await connection.fetchval(
            "SELECT count(*) FROM public.profiles WHERE id = $1", db.test_user
        ) == 1
        service_role_attrs = await connection.fetchrow(
            "SELECT rolbypassrls FROM pg_roles WHERE rolname = 'service_role'"
        )
        async with _as_role(connection, "service_role", db.test_user):
            service_count = await connection.fetchval(
                "SELECT count(*) FROM public.profiles WHERE id = $1", db.test_user
            )
        if service_role_attrs and service_role_attrs["rolbypassrls"]:
            assert service_count == 1

        assert await connection.fetchval(
            "SELECT has_table_privilege('anon', 'public.task_outbox', 'SELECT') "
            "OR has_table_privilege('authenticated', 'public.task_outbox', 'SELECT')"
        ) is False
        # Deletion checkpoints must be usable by the backend service role but
        # unavailable through the client API roles even though the table lives
        # in Supabase's exposed public schema.
        checkpoint_access = await connection.fetchrow(
            "SELECT has_table_privilege('anon', 'public.user_deletion_jobs', 'SELECT') AS anon_read, "
            "has_table_privilege('authenticated', 'public.user_deletion_jobs', 'SELECT') AS auth_read, "
            "has_table_privilege('service_role', 'public.user_deletion_jobs', 'SELECT') AS service_read, "
            "has_table_privilege('service_role', 'public.user_deletion_jobs', 'INSERT') AS service_insert"
        )
        assert dict(checkpoint_access) == {
            "anon_read": False,
            "auth_read": False,
            "service_read": True,
            "service_insert": True,
        }
        preview_privileges = await connection.fetchrow(
            "SELECT "
            "has_table_privilege('anon', 'public.import_previews', 'SELECT') AS anon_read, "
            "has_table_privilege('authenticated', 'public.import_preview_candidates', 'SELECT') AS auth_read, "
            "has_table_privilege('service_role', 'public.import_previews', 'SELECT') AS service_read, "
            "has_table_privilege('service_role', 'public.import_previews', 'INSERT') AS service_insert, "
            "has_table_privilege('service_role', 'public.import_preview_candidates', 'UPDATE') AS service_update, "
            "has_table_privilege('service_role', 'public.import_preview_candidates', 'DELETE') AS service_delete, "
            "has_table_privilege('service_role', 'public.import_preview_candidates', 'TRUNCATE') AS service_truncate, "
            "has_table_privilege('service_role', 'public.import_previews', 'TRIGGER') AS service_trigger"
        )
        assert dict(preview_privileges) == {
            "anon_read": False,
            "auth_read": False,
            "service_read": True,
            "service_insert": True,
            "service_update": True,
            "service_delete": True,
            "service_truncate": False,
            "service_trigger": False,
        }
        assert await connection.fetchval(
            "SELECT c.relrowsecurity FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relname = 'user_deletion_jobs'"
        ) is True
        await connection.execute(
            "INSERT INTO public.user_deletion_jobs (user_id, next_step) VALUES ($1, 'complete')",
            db.test_user,
        )
        await connection.execute("DELETE FROM public.profiles WHERE id = $1", db.test_user)
        tombstone_step = await connection.fetchval(
            "SELECT next_step FROM public.user_deletion_jobs WHERE user_id = $1",
            db.test_user,
        )
        assert tombstone_step == "complete"
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_expired_preview_cleanup_cascades_candidates_and_preserves_committed_preview(
    isolated_rls_database: IsolatedRlsDatabase,
) -> None:
    from app.services.imports.preview_cleanup import sweep_expired_import_previews

    db = isolated_rls_database
    connection = await asyncpg.connect(_postgres_dsn(db.url), timeout=10)
    user_id = str(uuid.uuid4())
    expired_id = "01K7F9R2J5YMXB3Z4Z0H7C1Q8W"
    raced_id = "01K7F9R2J5YMXB3Z4Z0H7C1Q8Y"
    committed_id = "01K7F9R2J5YMXB3Z4Z0H7C1Q9X"
    expired_candidate_id = str(uuid.uuid4())
    raced_candidate_id = str(uuid.uuid4())
    committed_candidate_id = str(uuid.uuid4())
    engine = create_async_engine(_async_url(db.url), pool_pre_ping=True)
    lock_connection = await asyncpg.connect(_postgres_dsn(db.url), timeout=10)
    monitor_connection = await asyncpg.connect(_postgres_dsn(db.url), timeout=10)
    try:
        await connection.execute(
            "INSERT INTO auth.users (id, raw_user_meta_data, email) VALUES ($1, '{}'::jsonb, $2)",
            user_id,
            f"{uuid.uuid4().hex}@example.test",
        )
        for preview_id, state, digest in (
            (expired_id, "open", None),
            (raced_id, "open", None),
            (committed_id, "committed", "c" * 64),
        ):
            await connection.execute(
                "INSERT INTO public.import_previews "
                "(id, owner_id, state, expires_at, parser_schema_hash, semantic_fingerprint, selection_digest) "
                "VALUES ($1, $2, $3, now() - interval '1 minute', 'parser-v1', $4, $5)",
                preview_id,
                user_id,
                state,
                "a" * 64,
                digest,
            )
        await connection.execute(
            "INSERT INTO public.import_preview_candidates "
            "(preview_id, candidate_id, owner_id, ordinal, reservation_key, source_url_norm, "
            "source_bucket, kind, collection_names) "
            "VALUES ($1, $2, $3, 0, $4, 'https://www.instagram.com/reel/DbM7eWINqAy', "
            "'instagram', 'media', '[]'::jsonb), "
            "($5, $6, $3, 0, $7, 'https://www.instagram.com/reel/DbM7eWINqAz', "
            "'instagram', 'media', '[]'::jsonb), "
            "($8, $9, $3, 0, $10, 'https://www.instagram.com/reel/DbM7eWINqA0', "
            "'instagram', 'media', '[]'::jsonb)",
            expired_id,
            expired_candidate_id,
            user_id,
            "b" * 64,
            committed_id,
            committed_candidate_id,
            "d" * 64,
            raced_id,
            raced_candidate_id,
            "e" * 64,
        )

        await lock_connection.execute("BEGIN")
        await lock_connection.fetchrow(
            "SELECT id FROM public.profiles WHERE id = $1 FOR UPDATE", user_id
        )
        sweep_task = asyncio.create_task(
            sweep_expired_import_previews(bind=engine, limit=10, now=datetime.now(UTC))
        )
        async with asyncio.timeout(10):
            while not await monitor_connection.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                "WHERE datname = current_database() AND pid <> pg_backend_pid() "
                "AND wait_event_type = 'Lock' AND query ILIKE '%profiles%' "
                "AND query ILIKE '%FOR UPDATE%')"
            ):
                await asyncio.sleep(0.01)
        await lock_connection.execute(
            "UPDATE public.import_previews SET state = 'committed', selection_digest = $2 "
            "WHERE id = $1 AND owner_id = $3",
            raced_id,
            "f" * 64,
            user_id,
        )
        await lock_connection.execute("COMMIT")
        result = await sweep_task
        assert result.examined == 2
        assert result.deleted == 1
        assert result.skipped_changed == 1
        assert await connection.fetchval(
            "SELECT count(*) FROM public.import_preview_candidates WHERE preview_id = $1",
            expired_id,
        ) == 0
        assert await connection.fetchval(
            "SELECT count(*) FROM public.import_previews WHERE id = $1", expired_id
        ) == 0
        assert await connection.fetchval(
            "SELECT count(*) FROM public.import_previews WHERE id = $1 AND state = 'committed'",
            committed_id,
        ) == 1
        assert await connection.fetchval(
            "SELECT count(*) FROM public.import_preview_candidates WHERE preview_id = $1",
            committed_id,
        ) == 1
        assert await connection.fetchval(
            "SELECT count(*) FROM public.import_previews WHERE id = $1 AND state = 'committed'",
            raced_id,
        ) == 1
        assert await connection.fetchval(
            "SELECT count(*) FROM public.import_preview_candidates WHERE preview_id = $1",
            raced_id,
        ) == 1
    finally:
        if lock_connection.is_in_transaction():
            await lock_connection.execute("ROLLBACK")
        await monitor_connection.close()
        await lock_connection.close()
        await engine.dispose()
        await connection.close()
