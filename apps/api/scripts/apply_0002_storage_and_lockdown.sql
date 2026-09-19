-- Migration 0002: Storage buckets + Storage RLS + alembic_version lockdown.
-- Applied to Supabase project fcefeiwvnzdfazwxdejy on 2026-09-19 via MCP.
-- Idempotent — safe to reapply on any Supabase project.

BEGIN;

-- Lock down alembic_version — anon/authenticated get zero access; only
-- service_role (which bypasses RLS) can read or write it. Alembic runs
-- via the direct postgres connection which also bypasses RLS.
ALTER TABLE public.alembic_version ENABLE ROW LEVEL SECURITY;

-- Storage buckets (private, per-user prefixed).
INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES
  ('media', 'media', false, 10485760,
   ARRAY['image/jpeg','image/png','image/webp']::text[]),
  ('exports', 'exports', false, 524288000,
   ARRAY['application/json','application/zip']::text[])
ON CONFLICT (id) DO NOTHING;

-- Storage object policies — objects are keyed as `{user_id}/…` so we
-- use storage.foldername(name)[1] to scope by owner.
CREATE POLICY media_select_own   ON storage.objects FOR SELECT USING (bucket_id = 'media'   AND (storage.foldername(name))[1] = auth.uid()::text);
CREATE POLICY media_insert_own   ON storage.objects FOR INSERT WITH CHECK (bucket_id = 'media' AND (storage.foldername(name))[1] = auth.uid()::text);
CREATE POLICY media_update_own   ON storage.objects FOR UPDATE USING (bucket_id = 'media'   AND (storage.foldername(name))[1] = auth.uid()::text);
CREATE POLICY media_delete_own   ON storage.objects FOR DELETE USING (bucket_id = 'media'   AND (storage.foldername(name))[1] = auth.uid()::text);
CREATE POLICY exports_select_own ON storage.objects FOR SELECT USING (bucket_id = 'exports' AND (storage.foldername(name))[1] = auth.uid()::text);
CREATE POLICY exports_insert_own ON storage.objects FOR INSERT WITH CHECK (bucket_id = 'exports' AND (storage.foldername(name))[1] = auth.uid()::text);
CREATE POLICY exports_update_own ON storage.objects FOR UPDATE USING (bucket_id = 'exports' AND (storage.foldername(name))[1] = auth.uid()::text);
CREATE POLICY exports_delete_own ON storage.objects FOR DELETE USING (bucket_id = 'exports' AND (storage.foldername(name))[1] = auth.uid()::text);

COMMIT;
