"""Engine/pool statement-cache regression tests (app/db/engine.py).

Staging `POST /v1/search` intermittently 500'd with
`asyncpg InvalidSQLStatementNameError: prepared statement
"__asyncpg_stmt_N__" does not exist`: Supabase's pooler runs pgbouncer
in transaction mode, which drops server-side prepared statements
between checkouts while asyncpg's statement cache kept stale refs.
    asyncpg and SQLAlchemy each maintain a distinct prepared-statement cache.
    Both must be disabled for the SQLAlchemy engine; the raw asyncpg pool only
    needs asyncpg's native cache disabled. Factory tests verify the options,
    and an adapter test verifies SQLAlchemy consumes its option. No live DB.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock


def _stub_settings() -> SimpleNamespace:
    return SimpleNamespace(
        database_url="postgresql+asyncpg://postgres.ref:pw@aws-0-x.pooler.supabase.com:6543/postgres",
        database_echo=False,
    )


class TestEngineStatementCache:
    def test_engine_disables_statement_cache(self, monkeypatch: Any) -> None:
        import app.db.engine as engine_module

        monkeypatch.setattr(engine_module, "_engine", None)
        monkeypatch.setattr(engine_module, "get_settings", lambda: _stub_settings())
        captured: dict[str, Any] = {}

        def fake_create_async_engine(url: str, **kwargs: Any) -> MagicMock:
            captured["url"] = url
            captured.update(kwargs)
            return MagicMock()

        monkeypatch.setattr(engine_module, "create_async_engine", fake_create_async_engine)
        engine_module.get_engine()
        assert captured["connect_args"] == {
            "statement_cache_size": 0,
            "prepared_statement_cache_size": 0,
        }

        # Verify the installed SQLAlchemy asyncpg adapter maps this DBAPI
        # argument to its own per-connection LRU cache, independently of the
        # asyncpg-native `statement_cache_size` option above.
        import asyncpg
        import sqlalchemy.dialects.postgresql.asyncpg as sqlalchemy_asyncpg

        forwarded: dict[str, Any] = {}

        def fake_connect(**kwargs: Any) -> object:
            forwarded.update(kwargs)
            return object()

        # `await_only` normally requires a running SQLAlchemy greenlet. The
        # factory boundary is sufficient here: no socket is opened.
        monkeypatch.setattr(sqlalchemy_asyncpg, "await_only", lambda connection: connection)
        adapter = sqlalchemy_asyncpg.AsyncAdapt_asyncpg_dbapi(asyncpg).connect(
            async_creator_fn=fake_connect,
            **captured["connect_args"],
        )
        assert adapter._prepared_statement_cache is None
        assert forwarded["statement_cache_size"] == 0
        assert "prepared_statement_cache_size" not in forwarded

    async def test_pg_pool_disables_statement_cache(self, monkeypatch: Any) -> None:
        import app.db.engine as engine_module

        monkeypatch.setattr(engine_module, "_pg_pool", None)
        monkeypatch.setattr(engine_module, "get_settings", lambda: _stub_settings())
        captured: dict[str, Any] = {}

        async def fake_create_pool(dsn: str, **kwargs: Any) -> MagicMock:
            captured["dsn"] = dsn
            captured.update(kwargs)
            return MagicMock()

        monkeypatch.setattr("asyncpg.create_pool", fake_create_pool)
        await engine_module.get_pg_pool()
        # DSN is converted to the plain asyncpg scheme for the raw pool.
        assert captured["dsn"] == (
            "postgresql://postgres.ref:pw@aws-0-x.pooler.supabase.com:6543/postgres"
        )
        assert captured["statement_cache_size"] == 0
