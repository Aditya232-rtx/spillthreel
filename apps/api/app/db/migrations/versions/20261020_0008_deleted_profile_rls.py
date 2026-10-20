"""Fence direct Supabase data access after account deletion is requested.

Revision ID: 20261020_0008_deleted_profile_rls
Revises: 20261018_0007_import_retry_bound
"""

from __future__ import annotations

from alembic import op

revision = "20261020_0008_deleted_profile_rls"
down_revision = "20261018_0007_import_retry_bound"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Replacing policies matters: permissive policies are ORed, so adding a
    # second live-account policy would leave the original policy effective.
    statements = [
        "DROP POLICY IF EXISTS profiles_select_own ON profiles",
        "CREATE POLICY profiles_select_own ON profiles FOR SELECT USING (auth.uid() = id AND deleted_at IS NULL)",
        "DROP POLICY IF EXISTS profiles_update_own ON profiles",
        "CREATE POLICY profiles_update_own ON profiles FOR UPDATE USING (auth.uid() = id AND deleted_at IS NULL) WITH CHECK (auth.uid() = id AND deleted_at IS NULL)",
    ]
    user_policies = {
        "items_select_own": ("items", "SELECT", "user_id"),
        "items_insert_own": ("items", "INSERT", "user_id"),
        "items_update_own": ("items", "UPDATE", "user_id"),
        "items_delete_own": ("items", "DELETE", "user_id"),
        "categories_all_own": ("categories", "ALL", "user_id"),
        "collections_all_own": ("collections", "ALL", "user_id"),
        "imports_all_own": ("imports", "ALL", "user_id"),
        "saved_audio_all_own": ("saved_audio", "ALL", "user_id"),
        "push_tokens_all_own": ("push_tokens", "ALL", "user_id"),
        "item_embeddings_select_own": ("item_embeddings", "SELECT", "user_id"),
        "item_embeddings_insert_own": ("item_embeddings", "INSERT", "user_id"),
        "item_embeddings_update_own": ("item_embeddings", "UPDATE", "user_id"),
        "item_embeddings_delete_own": ("item_embeddings", "DELETE", "user_id"),
    }
    for policy, (table, command, owner) in user_policies.items():
        predicate = (
            f"auth.uid() = {owner} AND EXISTS "
            f"(SELECT 1 FROM public.profiles p WHERE p.id = {owner} AND p.deleted_at IS NULL)"
        )
        statements.append(f"DROP POLICY IF EXISTS {policy} ON {table}")
        if command == "INSERT":
            statements.append(
                f"CREATE POLICY {policy} ON {table} FOR INSERT WITH CHECK ({predicate})"
            )
        elif command == "UPDATE":
            statements.append(
                f"CREATE POLICY {policy} ON {table} FOR UPDATE USING ({predicate}) WITH CHECK ({predicate})"
            )
        elif command == "DELETE":
            statements.append(
                f"CREATE POLICY {policy} ON {table} FOR DELETE USING ({predicate})"
            )
        else:
            check = f" WITH CHECK ({predicate})" if command == "ALL" else ""
            statements.append(
                f"CREATE POLICY {policy} ON {table} FOR {command} USING ({predicate}){check}"
            )
    statements.extend(
        [
            "DROP POLICY IF EXISTS item_categories_via_item ON item_categories",
            "CREATE POLICY item_categories_via_item ON item_categories FOR ALL USING (EXISTS (SELECT 1 FROM public.items i JOIN public.profiles p ON p.id = i.user_id WHERE i.id = item_id AND i.user_id = auth.uid() AND p.deleted_at IS NULL)) WITH CHECK (EXISTS (SELECT 1 FROM public.items i JOIN public.profiles p ON p.id = i.user_id WHERE i.id = item_id AND i.user_id = auth.uid() AND p.deleted_at IS NULL))",
            "DROP POLICY IF EXISTS collection_items_via_collection ON collection_items",
            "CREATE POLICY collection_items_via_collection ON collection_items FOR ALL USING (EXISTS (SELECT 1 FROM public.collections c JOIN public.profiles p ON p.id = c.user_id JOIN public.items i ON i.id = item_id AND i.user_id = c.user_id WHERE c.id = collection_id AND c.user_id = auth.uid() AND p.deleted_at IS NULL)) WITH CHECK (EXISTS (SELECT 1 FROM public.collections c JOIN public.profiles p ON p.id = c.user_id JOIN public.items i ON i.id = item_id AND i.user_id = c.user_id WHERE c.id = collection_id AND c.user_id = auth.uid() AND p.deleted_at IS NULL))",
            "DROP POLICY IF EXISTS ingestion_events_via_item ON ingestion_events",
            "CREATE POLICY ingestion_events_via_item ON ingestion_events FOR SELECT USING (EXISTS (SELECT 1 FROM public.items i JOIN public.profiles p ON p.id = i.user_id WHERE i.id = item_id AND i.user_id = auth.uid() AND p.deleted_at IS NULL))",
        ]
    )
    for statement in statements:
        op.execute(statement)
    op.execute(
        """DO $$ DECLARE role_name text; BEGIN
        FOREACH role_name IN ARRAY ARRAY['anon', 'authenticated'] LOOP
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
            EXECUTE format('REVOKE ALL ON TABLE public.task_outbox FROM %I', role_name);
          END IF;
        END LOOP;
        REVOKE ALL ON TABLE public.task_outbox FROM PUBLIC;
        END $$"""
    )

    # Storage is an optional Supabase extension in local development. Keep
    # policies fenced where it exists without making local Alembic depend on it.
    op.execute(
        """DO $$ BEGIN
        IF to_regclass('storage.objects') IS NOT NULL THEN
          DROP POLICY IF EXISTS media_select_own ON storage.objects;
          DROP POLICY IF EXISTS media_insert_own ON storage.objects;
          DROP POLICY IF EXISTS media_update_own ON storage.objects;
          DROP POLICY IF EXISTS media_delete_own ON storage.objects;
          DROP POLICY IF EXISTS exports_select_own ON storage.objects;
          DROP POLICY IF EXISTS exports_insert_own ON storage.objects;
          DROP POLICY IF EXISTS exports_update_own ON storage.objects;
          DROP POLICY IF EXISTS exports_delete_own ON storage.objects;
          CREATE POLICY media_select_own ON storage.objects FOR SELECT USING (bucket_id = 'media' AND (storage.foldername(name))[1] = auth.uid()::text AND EXISTS (SELECT 1 FROM public.profiles p WHERE p.id = auth.uid() AND p.deleted_at IS NULL));
          CREATE POLICY media_insert_own ON storage.objects FOR INSERT WITH CHECK (bucket_id = 'media' AND (storage.foldername(name))[1] = auth.uid()::text AND EXISTS (SELECT 1 FROM public.profiles p WHERE p.id = auth.uid() AND p.deleted_at IS NULL));
          CREATE POLICY media_update_own ON storage.objects FOR UPDATE USING (bucket_id = 'media' AND (storage.foldername(name))[1] = auth.uid()::text AND EXISTS (SELECT 1 FROM public.profiles p WHERE p.id = auth.uid() AND p.deleted_at IS NULL)) WITH CHECK (bucket_id = 'media' AND (storage.foldername(name))[1] = auth.uid()::text AND EXISTS (SELECT 1 FROM public.profiles p WHERE p.id = auth.uid() AND p.deleted_at IS NULL));
          CREATE POLICY media_delete_own ON storage.objects FOR DELETE USING (bucket_id = 'media' AND (storage.foldername(name))[1] = auth.uid()::text AND EXISTS (SELECT 1 FROM public.profiles p WHERE p.id = auth.uid() AND p.deleted_at IS NULL));
          CREATE POLICY exports_select_own ON storage.objects FOR SELECT USING (bucket_id = 'exports' AND (storage.foldername(name))[1] = auth.uid()::text AND EXISTS (SELECT 1 FROM public.profiles p WHERE p.id = auth.uid() AND p.deleted_at IS NULL));
          CREATE POLICY exports_insert_own ON storage.objects FOR INSERT WITH CHECK (bucket_id = 'exports' AND (storage.foldername(name))[1] = auth.uid()::text AND EXISTS (SELECT 1 FROM public.profiles p WHERE p.id = auth.uid() AND p.deleted_at IS NULL));
          CREATE POLICY exports_update_own ON storage.objects FOR UPDATE USING (bucket_id = 'exports' AND (storage.foldername(name))[1] = auth.uid()::text AND EXISTS (SELECT 1 FROM public.profiles p WHERE p.id = auth.uid() AND p.deleted_at IS NULL)) WITH CHECK (bucket_id = 'exports' AND (storage.foldername(name))[1] = auth.uid()::text AND EXISTS (SELECT 1 FROM public.profiles p WHERE p.id = auth.uid() AND p.deleted_at IS NULL));
          CREATE POLICY exports_delete_own ON storage.objects FOR DELETE USING (bucket_id = 'exports' AND (storage.foldername(name))[1] = auth.uid()::text AND EXISTS (SELECT 1 FROM public.profiles p WHERE p.id = auth.uid() AND p.deleted_at IS NULL));
        END IF;
        END $$"""
    )


def downgrade() -> None:
    raise NotImplementedError(
        "20261020_0008_deleted_profile_rls is intentionally irreversible: "
        "rolling back would restore direct access for tombstoned accounts"
    )
