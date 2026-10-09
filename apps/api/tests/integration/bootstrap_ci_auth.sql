-- CI-only auth stub for vanilla Postgres (pgvector/pgvector:pg16).
--
-- Staging/prod run on Supabase, where the `auth` schema (`auth.users`,
-- `auth.uid()`) is platform-managed. Vanilla Postgres has neither, so
-- `alembic upgrade head` from scratch fails resolving the profiles FK and
-- the RLS policies in 0001_initial. This stub provides the minimal surface
-- the migrations + integration tests need. NEVER apply to Supabase DBs.
--
-- Idempotent: safe to re-run (CREATE ... IF NOT EXISTS / OR REPLACE).
-- Consumed by: .github/workflows/api-ci.yml (bootstrap step before pytest).

CREATE SCHEMA IF NOT EXISTS auth;

-- Minimal mirror of Supabase's auth.users: only the columns our schema
-- touches (profiles FK target + handle_new_user() trigger reads).
CREATE TABLE IF NOT EXISTS auth.users (
  id uuid PRIMARY KEY,
  raw_user_meta_data jsonb,
  email text
);

-- Fixed test identity. Integration tests connect as table owner (RLS
-- bypassed), so this only needs to EXIST for policy DDL to resolve; the
-- value matches TEST_USER_ID in test_pgvector_store.py for coherence.
CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid
  LANGUAGE sql STABLE AS
$$ SELECT '11111111-1111-1111-1111-111111111111'::uuid $$;
