-- SpillTheReel initial schema — pure SQL mirror of the Alembic
-- migration 20260918_0001_initial.py, applied directly via psql
-- for the first setup. Idempotent-safe: wrapped in a single
-- transaction so if anything fails nothing lands.
--
-- Keep in sync with app/db/migrations/versions/20260918_0001_initial.py.
-- After this runs once, `alembic stamp 0001_initial` should be run to
-- mark the migration as applied so future migrations chain correctly.

BEGIN;

-- ==============================================================
-- profiles — mirrors auth.users. FK cascades from Supabase's
-- auth schema so account deletion nukes every downstream row.
-- ==============================================================
CREATE TABLE profiles (
  id            UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  display_name  TEXT,
  avatar_url    TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at    TIMESTAMPTZ
);

ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;
CREATE POLICY profiles_select_own ON profiles FOR SELECT USING (auth.uid() = id);
CREATE POLICY profiles_update_own ON profiles FOR UPDATE USING (auth.uid() = id);

-- Auto-create profile row on new Supabase Auth signup.
CREATE OR REPLACE FUNCTION public.handle_new_user() RETURNS trigger AS $$
BEGIN
  INSERT INTO public.profiles (id, display_name)
  VALUES (
    NEW.id,
    COALESCE(
      NEW.raw_user_meta_data->>'full_name',
      NEW.raw_user_meta_data->>'name',
      NEW.email
    )
  );
  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();

-- ==============================================================
-- items
-- ==============================================================
CREATE TABLE items (
  id                TEXT PRIMARY KEY,           -- ULID, backend-generated
  user_id           UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  source_url        TEXT NOT NULL,
  source_url_norm   TEXT NOT NULL,
  platform          TEXT NOT NULL,
  state             TEXT NOT NULL,
  saved_at          TIMESTAMPTZ NOT NULL,
  source_type       TEXT NOT NULL,
  title             TEXT,
  summary           TEXT,
  transcript        TEXT,
  on_screen_text    TEXT,
  caption           TEXT,
  hashtags          TEXT[] NOT NULL DEFAULT '{}',
  owner_name        TEXT,
  owner_username    TEXT,
  owner_url         TEXT,
  duration_seconds  REAL,
  thumbnail_url     TEXT,
  failure_reason    TEXT,
  cognee_id         TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT items_user_url_unique UNIQUE (user_id, source_url_norm),
  CONSTRAINT items_state_valid CHECK (state IN
    ('queued','downloading','analyzing','indexing',
     'text_indexed','fully_indexed','source_gone','failed'))
);
CREATE INDEX items_user_saved_at_idx ON items(user_id, saved_at DESC);
CREATE INDEX items_user_platform_idx ON items(user_id, platform);
CREATE INDEX items_user_state_idx    ON items(user_id, state);

ALTER TABLE items ENABLE ROW LEVEL SECURITY;
CREATE POLICY items_select_own ON items FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY items_insert_own ON items FOR INSERT WITH CHECK (auth.uid() = user_id);
CREATE POLICY items_update_own ON items FOR UPDATE USING (auth.uid() = user_id);
CREATE POLICY items_delete_own ON items FOR DELETE USING (auth.uid() = user_id);

-- ==============================================================
-- categories + item_categories
-- ==============================================================
CREATE TABLE categories (
  id            TEXT PRIMARY KEY,
  user_id       UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  name          TEXT NOT NULL,
  source        TEXT NOT NULL,
  bg_color      TEXT NOT NULL,
  emoji         TEXT,
  origin_id     TEXT,
  centroid_id   TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT categories_user_name_unique UNIQUE (user_id, name),
  CONSTRAINT categories_source_valid CHECK (source IN ('auto','user','import'))
);
ALTER TABLE categories ENABLE ROW LEVEL SECURITY;
CREATE POLICY categories_all_own ON categories FOR ALL
  USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);

CREATE TABLE item_categories (
  item_id       TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  category_id   TEXT NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
  assigned_by   TEXT NOT NULL,
  confidence    REAL,
  assigned_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (item_id, category_id)
);
CREATE INDEX item_categories_category_idx ON item_categories(category_id);
ALTER TABLE item_categories ENABLE ROW LEVEL SECURITY;
CREATE POLICY item_categories_via_item ON item_categories FOR ALL
  USING (EXISTS (SELECT 1 FROM items i WHERE i.id = item_id AND i.user_id = auth.uid()))
  WITH CHECK (EXISTS (SELECT 1 FROM items i WHERE i.id = item_id AND i.user_id = auth.uid()));

-- ==============================================================
-- collections + collection_items
-- ==============================================================
CREATE TABLE collections (
  id            TEXT PRIMARY KEY,
  user_id       UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  name          TEXT NOT NULL,
  privacy       TEXT NOT NULL,
  source        TEXT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT collections_user_name_unique UNIQUE (user_id, name)
);
ALTER TABLE collections ENABLE ROW LEVEL SECURITY;
CREATE POLICY collections_all_own ON collections FOR ALL
  USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);

CREATE TABLE collection_items (
  collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
  item_id       TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  added_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (collection_id, item_id)
);
ALTER TABLE collection_items ENABLE ROW LEVEL SECURITY;
CREATE POLICY collection_items_via_collection ON collection_items FOR ALL
  USING (EXISTS (SELECT 1 FROM collections c WHERE c.id = collection_id AND c.user_id = auth.uid()))
  WITH CHECK (EXISTS (SELECT 1 FROM collections c WHERE c.id = collection_id AND c.user_id = auth.uid()));

-- ==============================================================
-- imports
-- ==============================================================
CREATE TABLE imports (
  id                   TEXT PRIMARY KEY,
  user_id              UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  state                TEXT NOT NULL,
  total_parsed         INTEGER NOT NULL DEFAULT 0,
  text_indexed_done    INTEGER NOT NULL DEFAULT 0,
  full_queued          INTEGER NOT NULL DEFAULT 0,
  full_done            INTEGER NOT NULL DEFAULT 0,
  failed               INTEGER NOT NULL DEFAULT 0,
  parser_schema_hash   TEXT,
  failure_reason       TEXT,
  started_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at          TIMESTAMPTZ
);
ALTER TABLE imports ENABLE ROW LEVEL SECURITY;
CREATE POLICY imports_all_own ON imports FOR ALL
  USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);

-- ==============================================================
-- saved_audio
-- ==============================================================
CREATE TABLE saved_audio (
  id            TEXT PRIMARY KEY,
  user_id       UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  source_type   TEXT NOT NULL,
  title         TEXT,
  artist        TEXT,
  saved_at      TIMESTAMPTZ
);
ALTER TABLE saved_audio ENABLE ROW LEVEL SECURITY;
CREATE POLICY saved_audio_all_own ON saved_audio FOR ALL
  USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);

-- ==============================================================
-- ingestion_events — read via item ownership; writes via service_role
-- ==============================================================
CREATE TABLE ingestion_events (
  id            TEXT PRIMARY KEY,
  item_id       TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  from_state    TEXT NOT NULL,
  to_state      TEXT NOT NULL,
  detail        JSONB,
  at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE ingestion_events ENABLE ROW LEVEL SECURITY;
CREATE POLICY ingestion_events_via_item ON ingestion_events FOR SELECT
  USING (EXISTS (SELECT 1 FROM items i WHERE i.id = item_id AND i.user_id = auth.uid()));

-- ==============================================================
-- push_tokens
-- ==============================================================
CREATE TABLE push_tokens (
  user_id       UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  token         TEXT NOT NULL,
  platform      TEXT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, token)
);
ALTER TABLE push_tokens ENABLE ROW LEVEL SECURITY;
CREATE POLICY push_tokens_all_own ON push_tokens FOR ALL
  USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);

-- ==============================================================
-- Alembic bookkeeping — mark the initial migration as applied so
-- future `alembic upgrade` runs pick up from here without trying
-- to re-create these tables.
-- ==============================================================
CREATE TABLE IF NOT EXISTS alembic_version (
  version_num VARCHAR(32) NOT NULL PRIMARY KEY
);
INSERT INTO alembic_version (version_num) VALUES ('0001_initial')
  ON CONFLICT (version_num) DO NOTHING;

COMMIT;
