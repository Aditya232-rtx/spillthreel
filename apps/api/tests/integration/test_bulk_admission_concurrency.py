"""Real PostgreSQL admission interleavings in a unique isolated schema."""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.models import BulkImportReservation, Profile
from app.services.imports.admission import (
    AdmissionCandidate,
    AdmissionPolicy,
    AdmissionStatus,
    AdmissionTier,
    EligibilityMode,
    PremiumHistoryMode,
    SourceBucket,
    admit_bulk_import,
)

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


def _async_url(url: str) -> str:
    parsed = make_url(url)
    if parsed.drivername not in {"postgres", "postgresql", "postgresql+asyncpg"}:
        raise ValueError("TEST_DATABASE_URL must use PostgreSQL")
    return parsed.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False)


@dataclass(frozen=True)
class AdmissionDB:
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]


@pytest_asyncio.fixture
async def admission_db() -> AsyncIterator[AdmissionDB]:
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is unset; isolated bulk admission test not run")
    import asyncpg

    parsed = make_url(TEST_DATABASE_URL)
    admin_dsn = parsed.set(drivername="postgresql").render_as_string(hide_password=False)
    schema = f"r03_admission_{uuid.uuid4().hex}"
    try:
        admin = await asyncpg.connect(admin_dsn, timeout=10)
    except Exception as exc:
        pytest.fail(f"isolated schema connection failed ({type(exc).__name__})")
    created = False
    try:
        async with admin.transaction():
            privileges = await admin.fetchrow(
                "SELECT has_database_privilege(current_user, current_database(), 'CREATE') "
                "AS can_create, current_database() IS NOT NULL AS connected"
            )
            if privileges is None or not privileges["can_create"] or not privileges["connected"]:
                pytest.fail("staging role cannot create an isolated private schema")
            await admin.execute(f'CREATE SCHEMA "{schema}" AUTHORIZATION CURRENT_USER')
            created = True
            await admin.execute(f'REVOKE ALL ON SCHEMA "{schema}" FROM PUBLIC')
            # A disposable local PostgreSQL instance may not define Supabase's
            # anon/authenticated roles. Revoke them when present; PUBLIC is
            # always revoked and the schema search_path check remains required.
            existing_roles = await admin.fetch(
                "SELECT rolname FROM pg_roles WHERE rolname = ANY($1::text[])",
                ["anon", "authenticated"],
            )
            for role in existing_roles:
                role_name = role["rolname"]
                if role_name in {"anon", "authenticated"}:
                    await admin.execute(f'REVOKE ALL ON SCHEMA "{schema}" FROM "{role_name}"')
            acl = await admin.fetchval(
                "SELECT nspacl FROM pg_namespace WHERE nspname = $1", schema
            )
            acl_entries = await admin.fetch(
                "SELECT grantee FROM aclexplode($1::aclitem[])", acl
            )
            if any(entry["grantee"] == 0 for entry in acl_entries):
                pytest.fail("isolated schema still grants privileges to PUBLIC")
    except Exception as exc:
        if created:
            await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        pytest.fail(f"isolated schema setup failed ({type(exc).__name__})")
    finally:
        await admin.close()

    engine = create_async_engine(
        _async_url(TEST_DATABASE_URL),
        connect_args={
            "statement_cache_size": 0,
            "prepared_statement_cache_size": 0,
            "server_settings": {"search_path": f'"{schema}",pg_catalog'},
        },
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Profile.__table__.create)
            await conn.run_sync(BulkImportReservation.__table__.create)
            proof = await conn.exec_driver_sql(
                "SELECT current_schema(), current_schemas(false), "
                "(SELECT n.nspname FROM pg_class c JOIN pg_namespace n "
                "ON n.oid=c.relnamespace WHERE c.oid=to_regclass('profiles'))"
            )
            current, visible, profile_schema = proof.one()
            assert current == schema and visible[0] == schema and "public" not in visible
            assert profile_schema == schema
        yield AdmissionDB(engine, sessions)
    finally:
        await engine.dispose()
        admin = await asyncpg.connect(admin_dsn, timeout=10)
        try:
            await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        finally:
            await admin.close()


@pytest.mark.asyncio
async def test_concurrent_admissions_serialize_at_the_last_free_slot(
    admission_db: AdmissionDB,
) -> None:
    user_id = str(uuid.uuid4())
    policy = AdmissionPolicy(
        version="integration-v1",
        d01_premium_history=PremiumHistoryMode.CARRY_FORWARD,
        d02_existing_duplicate=EligibilityMode.EXCLUDE,
        d02_manual_share=EligibilityMode.EXCLUDE,
        d03_saved_audio=EligibilityMode.EXCLUDE,
    )
    async with admission_db.sessions() as session, session.begin():
        session.add(Profile(id=user_id))
        # The reservation rows have only a scalar FK (no ORM relationship), so
        # flush the profile before SQLAlchemy's bulk insert path writes them.
        await session.flush()
        session.add_all(
            [
                BulkImportReservation(
                    id=f"seed-{n}",
                    user_id=user_id,
                    reservation_key=f"{n:064x}",
                    source_bucket="instagram" if n < 149 else "other",
                    tier="free",
                )
                for n in range(249)
            ]
        )

    async def attempt(url: str):
        async with admission_db.sessions() as session, session.begin():
            return await admit_bulk_import(
                session,
                user_id=user_id,
                tier=AdmissionTier.FREE,
                candidates=[AdmissionCandidate(url, SourceBucket.INSTAGRAM)],
                policy=policy,
            )

    first, second = await asyncio.gather(
        attempt("https://instagram.com/reel/last-a"),
        attempt("https://instagram.com/reel/last-b"),
    )
    assert sorted(result.candidates[0].status.value for result in (first, second)) == [
        AdmissionStatus.ADMITTED.value,
        AdmissionStatus.LIMIT_REACHED.value,
    ]

    async with admission_db.sessions() as session:
        count = await session.scalar(
            select(func.count(BulkImportReservation.id)).where(
                BulkImportReservation.user_id == user_id
            )
        )
    assert count == 250
