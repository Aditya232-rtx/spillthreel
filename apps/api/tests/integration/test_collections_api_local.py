"""Owner-scoped collection API coverage against an isolated local PG17 schema.

Opt in with ``RUN_COLLECTIONS_LOCAL_PG17=1`` and an explicit localhost
``TEST_DATABASE_URL``. This test refuses hosted endpoints and never changes
shared schemas or uses application database settings as a fallback.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime

import asyncpg
import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import text, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.api.v1.collections import router
from app.api.v1 import collections as collections_api
from app.auth.middleware import _current_user
from app.db.engine import get_session
from app.db.models import Base, Collection, CollectionItem, Item, Profile

pytestmark = pytest.mark.asyncio


@dataclass(frozen=True)
class LocalCollections:
    app: FastAPI
    client: httpx.AsyncClient
    sessions: async_sessionmaker[AsyncSession]
    owner_id: str
    other_id: str
    deleted_id: str
    owner_collection_id: str
    other_collection_id: str
    owner_item_id: str
    other_item_id: str


def _url_for_local_pg17() -> tuple[str, str]:
    if os.environ.get("RUN_COLLECTIONS_LOCAL_PG17") != "1":
        pytest.skip("set RUN_COLLECTIONS_LOCAL_PG17=1 to opt into local PostgreSQL test")
    raw_url = os.environ.get("TEST_DATABASE_URL")
    if not raw_url:
        pytest.fail("TEST_DATABASE_URL must be explicitly set; refusing application .env fallback")
    try:
        parsed = make_url(raw_url)
    except Exception as exc:
        pytest.fail(f"TEST_DATABASE_URL is invalid ({type(exc).__name__})")
    if parsed.drivername not in {"postgres", "postgresql", "postgresql+asyncpg"}:
        pytest.fail("TEST_DATABASE_URL must use PostgreSQL")
    if parsed.host not in {"localhost", "127.0.0.1", "::1"}:
        pytest.fail("collection integration tests accept localhost only")
    if parsed.query or "#" in raw_url:
        pytest.fail("TEST_DATABASE_URL query/fragment options are not allowed")
    admin_url = parsed.set(drivername="postgresql").render_as_string(hide_password=False)
    async_url = parsed.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False)
    return admin_url, async_url


@pytest_asyncio.fixture
async def local_collections() -> AsyncIterator[LocalCollections]:
    admin_url, async_url = _url_for_local_pg17()
    try:
        admin = await asyncpg.connect(admin_url, timeout=10)
    except Exception as exc:
        pytest.fail(f"local PostgreSQL connection failed ({type(exc).__name__})")

    schema = f"collections_{uuid.uuid4().hex}"
    created_schema = False
    try:
        identity = await admin.fetchrow(
            "SELECT current_setting('server_version_num')::int AS version_num, "
            "inet_server_addr()::text AS server_addr, "
            "has_database_privilege(current_user, current_database(), 'CREATE') AS can_create"
        )
        addr = identity["server_addr"]
        if not (170000 <= identity["version_num"] < 180000):
            pytest.fail("local integration endpoint must be PostgreSQL 17")
        if addr is None or str(addr).split("/")[0] not in {"127.0.0.1", "::1"}:
            pytest.fail("PostgreSQL server address is not loopback")
        if not identity["can_create"]:
            pytest.skip("local PostgreSQL role cannot create an isolated schema")
        await admin.execute(f'CREATE SCHEMA "{schema}" AUTHORIZATION CURRENT_USER')
        created_schema = True
        await admin.execute(f'REVOKE ALL ON SCHEMA "{schema}" FROM PUBLIC')
    except pytest.skip.Exception:
        raise
    except Exception as exc:
        pytest.fail(f"isolated schema setup failed ({type(exc).__name__})")
    finally:
        await admin.close()

    engine: AsyncEngine = create_async_engine(
        async_url,
        connect_args={
            "statement_cache_size": 0,
            "prepared_statement_cache_size": 0,
            "server_settings": {"search_path": f'"{schema}",pg_catalog'},
        },
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner_id, other_id, deleted_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    owner_collection_id, other_collection_id = "owner-collection", "other-collection"
    owner_item_id, other_item_id = "owner-item", "other-item"

    app = FastAPI()
    app.include_router(router, prefix="/v1")

    async def current_user() -> Profile:
        return Profile(id=owner_id)

    async def db_session() -> AsyncIterator[AsyncSession]:
        async with sessions() as session:
            yield session

    app.dependency_overrides[_current_user] = current_user
    app.dependency_overrides[get_session] = db_session
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    try:
        async with engine.begin() as connection:
            tables = [Profile.__table__, Item.__table__, Collection.__table__, CollectionItem.__table__]
            await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
            proof = await connection.exec_driver_sql(
                "SELECT current_schema(), current_schemas(false), "
                "(SELECT n.nspname FROM pg_class c JOIN pg_namespace n "
                "ON n.oid=c.relnamespace WHERE c.oid=to_regclass('profiles'))"
            )
            current, visible, profile_schema = proof.one()
            assert current == schema and visible[0] == schema and "public" not in visible
            assert profile_schema == schema
        now = datetime.now(UTC)
        async with sessions() as session:
            session.add_all([Profile(id=owner_id), Profile(id=other_id), Profile(id=deleted_id, deleted_at=now)])
            await session.flush()
            session.add_all(
                [
                    Collection(
                        id=owner_collection_id,
                        user_id=owner_id,
                        name="Imported Pins",
                        privacy="private",
                        source="instagram_import",
                    ),
                    Collection(
                        id=other_collection_id,
                        user_id=other_id,
                        name="Other Person's Collection",
                        privacy="private",
                        source="user_created",
                    ),
                    Item(
                        id=owner_item_id,
                        user_id=owner_id,
                        source_url="https://instagram.com/p/owner",
                        source_url_norm="https://instagram.com/p/owner",
                        platform="instagram",
                        state="fully_indexed",
                        saved_at=now,
                        source_type="share_extension",
                    ),
                    Item(
                        id=other_item_id,
                        user_id=other_id,
                        source_url="https://instagram.com/p/other",
                        source_url_norm="https://instagram.com/p/other",
                        platform="instagram",
                        state="fully_indexed",
                        saved_at=now,
                        source_type="share_extension",
                    ),
                ]
            )
            await session.commit()

        yield LocalCollections(
            app=app,
            client=client,
            sessions=sessions,
            owner_id=owner_id,
            other_id=other_id,
            deleted_id=deleted_id,
            owner_collection_id=owner_collection_id,
            other_collection_id=other_collection_id,
            owner_item_id=owner_item_id,
            other_item_id=other_item_id,
        )
    finally:
        await client.aclose()
        await engine.dispose()
        if created_schema:
            admin = await asyncpg.connect(admin_url, timeout=10)
            try:
                await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            finally:
                await admin.close()


async def test_collections_are_owner_scoped_and_membership_is_idempotent(
    local_collections: LocalCollections,
) -> None:
    client = local_collections.client
    response = await client.get("/v1/collections")
    assert response.status_code == 200
    assert response.json() == [
        {
            "id": local_collections.owner_collection_id,
            "name": "Imported Pins",
            "privacy": "private",
            "source": "instagram_import",
            "item_count": 0,
            "contains_item": False,
        }
    ]

    created = await client.post("/v1/collections", json={"name": "  Weekend recipes  "})
    assert created.status_code == 201
    created_row = created.json()
    assert created_row == {
        "id": created_row["id"],
        "name": "Weekend recipes",
        "privacy": "private",
        "source": "user_created",
        "item_count": 0,
        "contains_item": False,
    }
    assert (
        await client.post("/v1/collections", json={"name": "Weekend recipes"})
    ).status_code == 409
    assert (await client.post("/v1/collections", json={"name": "  "})).status_code == 422
    assert (await client.post("/v1/collections", json={"name": "x" * 81})).status_code == 422

    path = f"/v1/collections/{created_row['id']}/items/{local_collections.owner_item_id}"
    assert (await client.put(path)).status_code == 204
    assert (await client.put(path)).status_code == 204
    membership = await client.get(
        "/v1/collections", params={"item_id": local_collections.owner_item_id}
    )
    membership_by_id = {row["id"]: row["contains_item"] for row in membership.json()}
    assert membership_by_id[created_row["id"]] is True
    assert membership_by_id[local_collections.owner_collection_id] is False
    assert (
        await client.get(
            "/v1/collections", params={"item_id": local_collections.other_item_id}
        )
    ).status_code == 404
    listed = await client.get("/v1/collections")
    assert next(row for row in listed.json() if row["id"] == created_row["id"])["item_count"] == 1

    # A known foreign collection or item never reveals whether the other row exists.
    foreign_collection = await client.put(
        f"/v1/collections/{local_collections.other_collection_id}/items/"
        f"{local_collections.owner_item_id}"
    )
    foreign_item = await client.put(
        f"/v1/collections/{local_collections.owner_collection_id}/items/"
        f"{local_collections.other_item_id}"
    )
    assert foreign_collection.status_code == 404
    assert foreign_item.status_code == 404

    assert (await client.delete(path)).status_code == 204
    assert (await client.delete(path)).status_code == 204
    listed = await client.get("/v1/collections")
    assert next(row for row in listed.json() if row["id"] == created_row["id"])["item_count"] == 0
    removed_membership = await client.get(
        "/v1/collections", params={"item_id": local_collections.owner_item_id}
    )
    assert next(row for row in removed_membership.json() if row["id"] == created_row["id"])[
        "contains_item"
    ] is False

    async def deleted_user() -> Profile:
        return Profile(id=local_collections.deleted_id)

    local_collections.app.dependency_overrides[_current_user] = deleted_user
    assert (await client.get("/v1/collections")).status_code == 404


async def test_collection_write_serializes_with_account_deletion(
    local_collections: LocalCollections, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile_locked = asyncio.Event()
    release_write = asyncio.Event()
    original_check = collections_api._require_active_profile

    async def pause_after_live_profile_lock(
        user_id: str, session: AsyncSession, *, lock_for_write: bool = False
    ) -> None:
        await original_check(user_id, session, lock_for_write=lock_for_write)
        if lock_for_write:
            profile_locked.set()
            await release_write.wait()

    monkeypatch.setattr(collections_api, "_require_active_profile", pause_after_live_profile_lock)
    path = (
        f"/v1/collections/{local_collections.owner_collection_id}/items/"
        f"{local_collections.owner_item_id}"
    )
    write_task = asyncio.create_task(local_collections.client.put(path))
    await asyncio.wait_for(profile_locked.wait(), timeout=5)

    deletion_pid: int | None = None
    deletion_attempt_started = asyncio.Event()

    async def soft_delete_owner() -> None:
        nonlocal deletion_pid
        async with local_collections.sessions() as session:
            deletion_pid = await session.scalar(text("SELECT pg_backend_pid()"))
            deletion_attempt_started.set()
            await session.execute(
                update(Profile)
                .where(Profile.id == local_collections.owner_id)
                .values(deleted_at=datetime.now(UTC))
            )
            await session.commit()

    deletion_task = asyncio.create_task(soft_delete_owner())
    try:
        await asyncio.wait_for(deletion_attempt_started.wait(), timeout=5)
        assert deletion_pid is not None
        async with local_collections.sessions() as observer:
            async def waiting_on_profile_lock() -> bool:
                return await observer.scalar(
                    text(
                        "SELECT wait_event_type = 'Lock' FROM pg_stat_activity "
                        "WHERE pid = :pid"
                    ),
                    {"pid": deletion_pid},
                ) or False

            for _ in range(100):
                if await waiting_on_profile_lock():
                    break
                await asyncio.sleep(0.01)
            assert await waiting_on_profile_lock(), "soft-delete UPDATE never waited on the profile lock"
    finally:
        release_write.set()

    assert (await write_task).status_code == 204
    await deletion_task

    async def deleted_owner() -> Profile:
        return Profile(id=local_collections.owner_id)

    local_collections.app.dependency_overrides[_current_user] = deleted_owner
    assert (await local_collections.client.delete(path)).status_code == 404
