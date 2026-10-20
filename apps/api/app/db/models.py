"""SQLAlchemy models — mirrors the Postgres schema in trd.md §9 + §8b.2.

Any migration to the schema starts here: change the model, then generate
an Alembic revision with `alembic revision --autogenerate`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    Boolean,
    JSON,
    CheckConstraint,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    MappedAsDataclass,
    mapped_column,
)


class Base(DeclarativeBase, MappedAsDataclass):
    """Base for all ORM models — dataclass style for cleaner constructors."""

    type_annotation_map: ClassVar[dict[type, Any]] = {dict[str, Any]: JSON}


# ---------------------------------------------------------------------------
# profiles — custom fields for Supabase's auth.users.
#
# Supabase provides `auth.users` automatically; we do NOT create our own
# users table. `profiles.id` is the SAME UUID as `auth.users.id`, wired
# up in the migration via `ON DELETE CASCADE` so account deletion
# cleans everything up in one shot.
#
# Note: we can't declare the FK to `auth.users` at the ORM level (it
# lives in a different Postgres schema Supabase manages), so it's
# declared in the raw SQL migration only. From SQLAlchemy's POV this
# is just a table keyed by a UUID whose provenance is external.
# ---------------------------------------------------------------------------
class Profile(Base):
    __tablename__ = "profiles"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)  # UUID == auth.users.id
    display_name: Mapped[str | None] = mapped_column(String, default=None)
    avatar_url: Mapped[str | None] = mapped_column(String, default=None)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=text("now()"),
        init=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=text("now()"),
        init=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)


# ---------------------------------------------------------------------------
# items — indexed content
# ---------------------------------------------------------------------------
ITEM_STATES = (
    "queued",
    "downloading",
    "analyzing",
    "indexing",
    "text_indexed",
    "fully_indexed",
    "source_gone",
    "failed",
)


class Item(Base):
    __tablename__ = "items"
    __table_args__ = (
        UniqueConstraint("user_id", "source_url_norm", name="items_user_url_unique"),
        UniqueConstraint("id", "user_id", name="items_id_user_unique"),
        CheckConstraint(
            "state IN ('queued','downloading','analyzing','indexing',"
            "'text_indexed','fully_indexed','source_gone','failed')",
            name="items_state_valid",
        ),
        CheckConstraint(
            "(worker_lease_token IS NULL AND worker_lease_until IS NULL "
            "AND worker_task_type IS NULL AND worker_generation IS NULL) OR "
            "(worker_lease_token IS NOT NULL AND worker_lease_until IS NOT NULL "
            "AND worker_task_type IS NOT NULL AND worker_generation IS NOT NULL)",
            name="items_worker_lease_complete",
        ),
        CheckConstraint(
            "worker_generation IS NULL OR worker_generation > 0",
            name="items_worker_generation_positive",
        ),
        Index("items_user_saved_at_idx", "user_id", "saved_at"),
        Index("items_user_platform_idx", "user_id", "platform"),
        Index("items_user_state_idx", "user_id", "state"),
        Index(
            "items_active_worker_lease_idx",
            "user_id",
            "worker_lease_until",
            postgresql_where=text("worker_lease_token IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_url_norm: Mapped[str] = mapped_column(Text, nullable=False)
    platform: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    saved_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    source_type: Mapped[str] = mapped_column(
        String, nullable=False
    )  # 'share_extension' | 'instagram_import'

    title: Mapped[str | None] = mapped_column(Text, default=None)
    summary: Mapped[str | None] = mapped_column(Text, default=None)
    transcript: Mapped[str | None] = mapped_column(Text, default=None)
    on_screen_text: Mapped[str | None] = mapped_column(Text, default=None)
    caption: Mapped[str | None] = mapped_column(Text, default=None)
    hashtags: Mapped[list[str]] = mapped_column(
        ARRAY(String), server_default=text("'{}'::text[]"), default_factory=list
    )
    owner_name: Mapped[str | None] = mapped_column(String, default=None)
    owner_username: Mapped[str | None] = mapped_column(String, default=None)
    owner_url: Mapped[str | None] = mapped_column(String, default=None)
    duration_seconds: Mapped[float | None] = mapped_column(Float, default=None)
    thumbnail_url: Mapped[str | None] = mapped_column(Text, default=None)
    failure_reason: Mapped[str | None] = mapped_column(Text, default=None)
    worker_lease_token: Mapped[str | None] = mapped_column(String, default=None)
    worker_lease_until: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), default=None
    )
    worker_task_type: Mapped[str | None] = mapped_column(String, default=None)
    worker_generation: Mapped[int | None] = mapped_column(Integer, default=None)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), init=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), init=False
    )


# ---------------------------------------------------------------------------
# categories + item_categories — per trd.md §8b.2
# ---------------------------------------------------------------------------
CATEGORY_SOURCES = ("auto", "user", "import")


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="categories_user_name_unique"),
        CheckConstraint("source IN ('auto','user','import')", name="categories_source_valid"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    bg_color: Mapped[str] = mapped_column(String, nullable=False)

    emoji: Mapped[str | None] = mapped_column(String, default=None)
    origin_id: Mapped[str | None] = mapped_column(String, default=None)
    # Reserved for a future centroid vector id in the same item_embeddings
    # embedding space (e.g. category auto-discovery). Unused today — no
    # clustering feature reads or writes it.
    centroid_id: Mapped[str | None] = mapped_column(String, default=None)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), init=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), init=False
    )


class ItemEmbedding(Base):
    """One row per indexed item: its Gemini embedding vector.

    `embedding` lives in the same 768-dim space as PgVectorStore queries
    (see app/services/memory/pgvector_store.py EMBEDDING_DIM). Both FKs
    cascade, so item delete and account delete wipe vectors automatically.
    """

    __tablename__ = "item_embeddings"
    __table_args__ = (Index("ix_item_embeddings_user_id", "user_id"),)

    item_id: Mapped[str] = mapped_column(
        String, ForeignKey("items.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    embedding: Mapped[list[float]] = mapped_column(Vector(768), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), init=False
    )


class ItemCategory(Base):
    __tablename__ = "item_categories"
    __table_args__ = (Index("item_categories_category_idx", "category_id"),)

    item_id: Mapped[str] = mapped_column(
        String, ForeignKey("items.id", ondelete="CASCADE"), primary_key=True
    )
    category_id: Mapped[str] = mapped_column(
        String, ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True
    )
    assigned_by: Mapped[str] = mapped_column(
        String, nullable=False
    )  # 'classifier' | 'user' | 'import'
    confidence: Mapped[float | None] = mapped_column(Float, default=None)
    assigned_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), init=False
    )


# ---------------------------------------------------------------------------
# collections + collection_items
# ---------------------------------------------------------------------------
class Collection(Base):
    __tablename__ = "collections"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="collections_user_name_unique"),
        UniqueConstraint("id", "user_id", name="collections_id_user_unique"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    privacy: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(
        String, nullable=False
    )  # 'instagram_import' | 'user_created'
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), init=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), init=False
    )


class CollectionItem(Base):
    __tablename__ = "collection_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["collection_id", "owner_id"], ["collections.id", "collections.user_id"],
            ondelete="CASCADE", name="collection_items_collection_owner_fk",
        ),
        ForeignKeyConstraint(
            ["item_id", "owner_id"], ["items.id", "items.user_id"],
            ondelete="CASCADE", name="collection_items_item_owner_fk",
        ),
        Index("collection_items_item_owner_idx", "item_id", "owner_id"),
    )

    collection_id: Mapped[str] = mapped_column(
        String, primary_key=True
    )
    item_id: Mapped[str] = mapped_column(
        String, primary_key=True
    )
    owner_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    added_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), init=False
    )


# ---------------------------------------------------------------------------
# imports — bulk import job records
# ---------------------------------------------------------------------------
class ImportPreview(Base):
    """Short-lived selection session, separate from physical source staging."""

    __tablename__ = "import_previews"
    __table_args__ = (
        UniqueConstraint("id", "owner_id", name="import_previews_id_owner_unique"),
        UniqueConstraint("owner_id", "request_key_hash", name="import_previews_owner_request_key_unique"),
        CheckConstraint("id ~ '^[0-7][0-9A-HJKMNP-TV-Z]{25}$'", name="import_previews_id_ulid_valid"),
        CheckConstraint("state IN ('open','committed','expired')", name="import_previews_state_valid"),
        CheckConstraint("candidate_count >= 0", name="import_previews_candidate_count_nonnegative"),
        CheckConstraint(
            "semantic_fingerprint ~ '^[0-9a-f]{64}$'",
            name="import_previews_semantic_fingerprint_valid",
        ),
        CheckConstraint(
            "(request_key_hash IS NULL AND request_fingerprint IS NULL) OR "
            "(request_key_hash ~ '^[0-9a-f]{64}$' AND request_fingerprint ~ '^[0-9a-f]{64}$')",
            name="import_previews_request_identity_valid",
        ),
        CheckConstraint(
            "selection_digest IS NULL OR selection_digest ~ '^[0-9a-f]{64}$'",
            name="import_previews_selection_digest_valid",
        ),
        CheckConstraint(
            "(state = 'committed' AND selection_digest IS NOT NULL) OR "
            "(state <> 'committed' AND selection_digest IS NULL)",
            name="import_previews_commit_digest_consistent",
        ),
        ForeignKeyConstraint(
            ["owner_id"], ["profiles.id"], ondelete="CASCADE", name="import_previews_owner_fk"
        ),
        Index("import_previews_owner_expiry_idx", "owner_id", "state", "expires_at"),
        Index(
            "import_previews_open_expiry_idx",
            "expires_at",
            postgresql_where=text("state = 'open'"),
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    owner_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    parser_schema_hash: Mapped[str] = mapped_column(String, nullable=False)
    semantic_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False, default="open", server_default=text("'open'"))
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    request_key_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    request_fingerprint: Mapped[str | None] = mapped_column(String(64), default=None)
    selection_digest: Mapped[str | None] = mapped_column(String(64), default=None)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"), init=False)


class ImportPreviewCandidate(Base):
    """Immutable opaque selectable identity and display metadata for a preview."""

    __tablename__ = "import_preview_candidates"
    __table_args__ = (
        ForeignKeyConstraint(
            ["preview_id", "owner_id"],
            ["import_previews.id", "import_previews.owner_id"],
            ondelete="CASCADE",
            name="import_preview_candidates_preview_owner_fk",
        ),
        CheckConstraint("ordinal >= 0", name="import_preview_candidates_ordinal_nonnegative"),
        CheckConstraint("kind IN ('media','audio')", name="import_preview_candidates_kind_valid"),
        CheckConstraint("source_bucket IN ('instagram','other')", name="import_preview_candidates_bucket_valid"),
        CheckConstraint("reservation_key ~ '^[0-9a-f]{64}$'", name="import_preview_candidates_key_valid"),
        CheckConstraint(
            "(kind = 'media' AND source_url_norm IS NOT NULL) OR "
            "(kind = 'audio' AND source_url_norm IS NULL)",
            name="import_preview_candidates_source_consistent",
        ),
        UniqueConstraint("preview_id", "ordinal", name="import_preview_candidates_preview_ordinal_unique"),
        UniqueConstraint("preview_id", "reservation_key", name="import_preview_candidates_preview_identity_unique"),
        Index("import_preview_candidates_page_idx", "preview_id", "ordinal"),
    )

    preview_id: Mapped[str] = mapped_column(String, primary_key=True)
    candidate_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    owner_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    reservation_key: Mapped[str] = mapped_column(String(64), nullable=False)
    source_bucket: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    source_url_norm: Mapped[str | None] = mapped_column(Text, default=None)
    title: Mapped[str | None] = mapped_column(Text, default=None)
    artist: Mapped[str | None] = mapped_column(Text, default=None)
    saved_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    collection_names: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default_factory=list)


class Import(Base):
    __tablename__ = "imports"
    __table_args__ = (
        UniqueConstraint("id", "user_id", name="imports_id_user_unique"),
        UniqueConstraint("user_id", "request_key_hash", name="imports_user_request_key_unique"),
        UniqueConstraint("user_id", "semantic_fingerprint", name="imports_user_semantic_fingerprint_unique"),
        UniqueConstraint("preview_id", name="imports_preview_unique"),
        ForeignKeyConstraint(
            ["preview_id", "user_id"], ["import_previews.id", "import_previews.owner_id"],
            ondelete="SET NULL (preview_id)", name="imports_preview_owner_fk",
        ),
        CheckConstraint(
            "(request_key_hash IS NULL AND request_fingerprint IS NULL) OR "
            "(request_key_hash ~ '^[0-9a-f]{64}$' AND request_fingerprint ~ '^[0-9a-f]{64}$')",
            name="imports_request_identity_valid",
        ),
        CheckConstraint(
            "semantic_fingerprint IS NULL OR semantic_fingerprint ~ '^[0-9a-f]{64}$'",
            name="imports_semantic_fingerprint_valid",
        ),
        CheckConstraint(
            "total_parsed >= 0 AND text_indexed_done >= 0 AND full_queued >= 0 "
            "AND full_done >= 0 AND failed >= 0",
            name="imports_counters_nonnegative",
        ),
        Index("imports_user_started_id_idx", "user_id", text("started_at DESC"), text("id DESC")),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    state: Mapped[str] = mapped_column(
        String, nullable=False
    )  # 'processing' | 'parsing' | 'indexing' | 'complete' | 'failed'
    admission_enforced: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    request_key_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    request_fingerprint: Mapped[str | None] = mapped_column(String(64), default=None)
    semantic_fingerprint: Mapped[str | None] = mapped_column(String(64), default=None)
    preview_id: Mapped[str | None] = mapped_column(String, default=None)
    total_parsed: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)
    text_indexed_done: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)
    full_queued: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)
    full_done: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)
    failed: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)
    parser_schema_hash: Mapped[str | None] = mapped_column(String, default=None)
    failure_reason: Mapped[str | None] = mapped_column(Text, default=None)
    started_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), init=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)


class ImportEntry(Base):
    """One unique admitted media identity within a bulk import."""

    __tablename__ = "import_entries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["import_id", "owner_id"],
            ["imports.id", "imports.user_id"],
            ondelete="CASCADE",
            name="import_entries_import_owner_fk",
        ),
        ForeignKeyConstraint(
            ["item_id", "owner_id"],
            ["items.id", "items.user_id"],
            ondelete="SET NULL (item_id)",
            name="import_entries_item_owner_fk",
        ),
        CheckConstraint(
            "reservation_key ~ '^[0-9a-f]{64}$'",
            name="import_entries_reservation_key_valid",
        ),
        CheckConstraint(
            "source_bucket IN ('instagram','other')",
            name="import_entries_source_bucket_valid",
        ),
        CheckConstraint(
            "state IN ('queued','text_indexed','completed','retryable_failed','terminal_failed')",
            name="import_entries_state_valid",
        ),
        CheckConstraint("retry_count >= 0", name="import_entries_retry_count_nonnegative"),
        Index("import_entries_owner_import_idx", "owner_id", "import_id"),
        Index("import_entries_import_owner_idx", "import_id", "owner_id"),
        Index("import_entries_item_idx", "item_id"),
        Index("import_entries_import_state_idx", "import_id", "state"),
    )

    import_id: Mapped[str] = mapped_column(String, primary_key=True)
    reservation_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    source_url_norm: Mapped[str] = mapped_column(Text, nullable=False)
    source_bucket: Mapped[str] = mapped_column(String, nullable=False)
    # Null means membership was recorded without minting new work (for example,
    # this item was already owned and complete when the import was admitted).
    work_queued_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), default=None
    )
    item_id: Mapped[str | None] = mapped_column(String, default=None)
    state: Mapped[str] = mapped_column(String, nullable=False, default="queued")
    last_error_code: Mapped[str | None] = mapped_column(String(120), default=None)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), init=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), init=False
    )


class ImportEntryOccurrence(Base):
    """One deterministic parsed occurrence, including repeated folder membership."""

    __tablename__ = "import_entry_occurrences"
    __table_args__ = (
        ForeignKeyConstraint(
            ["import_id", "owner_id"],
            ["imports.id", "imports.user_id"],
            ondelete="CASCADE",
            name="import_occurrences_import_owner_fk",
        ),
        ForeignKeyConstraint(
            ["import_id", "entry_reservation_key"],
            ["import_entries.import_id", "import_entries.reservation_key"],
            ondelete="CASCADE",
            name="import_occurrences_entry_fk",
        ),
        CheckConstraint("source_position >= 0", name="import_occurrences_position_nonnegative"),
        CheckConstraint(
            "occurrence_key ~ '^[0-9a-f]{64}$'",
            name="import_occurrences_key_valid",
        ),
        CheckConstraint(
            "entry_reservation_key IS NULL OR entry_reservation_key ~ '^[0-9a-f]{64}$'",
            name="import_occurrences_entry_key_valid",
        ),
        CheckConstraint(
            "source_kind IN ('media','audio','unsupported')",
            name="import_occurrences_kind_valid",
        ),
        CheckConstraint(
            "outcome IN ('admitted','already_reserved','unsupported','audio_metadata','excluded')",
            name="import_occurrences_outcome_valid",
        ),
        CheckConstraint(
            "((outcome IN ('admitted','already_reserved') AND entry_reservation_key IS NOT NULL) "
            "OR (outcome IN ('unsupported','audio_metadata','excluded') "
            "AND entry_reservation_key IS NULL))",
            name="import_occurrences_outcome_entry_consistent",
        ),
        UniqueConstraint(
            "import_id", "source_position", name="import_occurrences_import_position_unique"
        ),
        Index("import_occurrences_import_entry_idx", "import_id", "entry_reservation_key"),
        Index("import_occurrences_import_owner_idx", "import_id", "owner_id"),
    )

    import_id: Mapped[str] = mapped_column(String, primary_key=True)
    occurrence_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    source_position: Mapped[int] = mapped_column(Integer, nullable=False)
    source_kind: Mapped[str] = mapped_column(String, nullable=False)
    outcome: Mapped[str] = mapped_column(String, nullable=False)
    entry_reservation_key: Mapped[str | None] = mapped_column(String(64), default=None)
    source_url_norm: Mapped[str | None] = mapped_column(Text, default=None)
    collection_key: Mapped[str | None] = mapped_column(String(64), default=None)
    collection_name: Mapped[str | None] = mapped_column(Text, default=None)
    saved_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)


class ImportCollection(Base):
    """Stable mapping from an archive folder identity to its owned collection."""

    __tablename__ = "import_collections"
    __table_args__ = (
        ForeignKeyConstraint(
            ["import_id", "owner_id"], ["imports.id", "imports.user_id"],
            ondelete="CASCADE", name="import_collections_import_owner_fk",
        ),
        ForeignKeyConstraint(
            ["collection_id", "owner_id"], ["collections.id", "collections.user_id"],
            ondelete="CASCADE", name="import_collections_collection_owner_fk",
        ),
        UniqueConstraint("import_id", "collection_id", name="import_collections_import_collection_unique"),
        CheckConstraint("collection_key ~ '^[0-9a-f]{64}$'", name="import_collections_key_valid"),
        Index("import_collections_owner_idx", "owner_id", "import_id"),
    )

    import_id: Mapped[str] = mapped_column(String, primary_key=True)
    owner_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    collection_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    collection_id: Mapped[str] = mapped_column(String, nullable=False)
    collection_name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), init=False
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), init=False
    )


class ImportStaging(Base):
    """Temporary source-upload registry retained until admission or cleanup.

    Deliberately has no Profile FK: the marker must survive account hard-delete
    so a bounded worker can remove an upload that completed after prefix cleanup.
    This is server-only transient state, protected by RLS with no client policies.
    """

    __tablename__ = "import_staging"
    __table_args__ = (
        CheckConstraint(
            "storage_key = owner_id::text || '/' || import_id || '.zip'",
            name="import_staging_key_matches_owner",
        ),
        CheckConstraint(
            "import_id ~ '^[0-7][0-9A-HJKMNP-TV-Z]{25}$'",
            name="import_staging_id_ulid_valid",
        ),
        CheckConstraint(
            "state IN ('staging','cleanup_pending')",
            name="import_staging_state_valid",
        ),
        CheckConstraint(
            "(state = 'staging' AND cleanup_token IS NULL AND cleanup_lease_until IS NULL) OR "
            "(state = 'cleanup_pending' AND cleanup_token IS NOT NULL "
            "AND cleanup_lease_until IS NOT NULL)",
            name="import_staging_cleanup_lease_complete",
        ),
        CheckConstraint("cleanup_attempts >= 0", name="import_staging_attempts_nonnegative"),
        UniqueConstraint("bucket", "storage_key", name="import_staging_bucket_key_unique"),
        Index("import_staging_owner_idx", "owner_id"),
        Index("import_staging_state_lease_idx", "state", "lease_until"),
        Index("import_staging_cleanup_lease_idx", "state", "cleanup_lease_until"),
    )

    import_id: Mapped[str] = mapped_column(String, primary_key=True)
    owner_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    bucket: Mapped[str] = mapped_column(String, nullable=False)
    storage_key: Mapped[str] = mapped_column(String, nullable=False)
    lease_until: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    state: Mapped[str] = mapped_column(
        String, nullable=False, default="staging", server_default=text("'staging'")
    )
    cleanup_token: Mapped[str | None] = mapped_column(String, default=None)
    cleanup_lease_until: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), default=None
    )
    cleanup_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    last_error_code: Mapped[str | None] = mapped_column(String(120), default=None)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), init=False
    )


class BulkImportReservation(Base):
    """Durable once-per-account admission identity, independent of item lifetime."""

    __tablename__ = "bulk_import_reservations"
    __table_args__ = (
        CheckConstraint("source_bucket IN ('instagram','other')", name="bulk_reservation_bucket_valid"),
        CheckConstraint("tier IN ('free','premium')", name="bulk_reservation_tier_valid"),
        UniqueConstraint("user_id", "reservation_key", name="bulk_reservation_user_key_unique"),
        Index("bulk_reservation_user_tier_bucket_idx", "user_id", "tier", "source_bucket"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    reservation_key: Mapped[str] = mapped_column(String(64), nullable=False)
    source_bucket: Mapped[str] = mapped_column(String, nullable=False)
    tier: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), init=False
    )


# ---------------------------------------------------------------------------
# saved_audio, ingestion_events, push_tokens
# ---------------------------------------------------------------------------
class SavedAudio(Base):
    __tablename__ = "saved_audio"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    source_type: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str | None] = mapped_column(String, default=None)
    artist: Mapped[str | None] = mapped_column(String, default=None)
    saved_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)


class IngestionEvent(Base):
    __tablename__ = "ingestion_events"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    item_id: Mapped[str] = mapped_column(
        String, ForeignKey("items.id", ondelete="CASCADE"), nullable=False
    )
    from_state: Mapped[str] = mapped_column(String, nullable=False)
    to_state: Mapped[str] = mapped_column(String, nullable=False)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), init=False
    )


class PushToken(Base):
    __tablename__ = "push_tokens"
    __table_args__ = (UniqueConstraint("token", name="push_tokens_token_unique"),)

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True
    )
    token: Mapped[str] = mapped_column(String, primary_key=True)
    platform: Mapped[str] = mapped_column(String, nullable=False)  # 'ios' | 'android'
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), init=False
    )


# ---------------------------------------------------------------------------
# task_outbox — durable intent to dispatch background work
# ---------------------------------------------------------------------------
class UserDeletionJob(Base):
    """Minimal resumable deletion checkpoint that outlives the user profile.

    The active task_outbox execution lease fences step advancement. This table
    deliberately has no Profile FK so an Auth failure after profile deletion
    can resume without restoring the account.
    """

    __tablename__ = "user_deletion_jobs"
    __table_args__ = (
        CheckConstraint(
            "next_step IN ('storage_media','storage_exports','vectors','profile','auth','complete')",
            name="user_deletion_jobs_step_valid",
        ),
    )

    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    next_step: Mapped[str] = mapped_column(
        String(32), nullable=False, default="storage_media", server_default=text("'storage_media'")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), init=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), nullable=False, init=False
    )


class TaskOutbox(Base):
    """One immutable logical background job, retried until Cloud Tasks accepts it.

    Intentionally has no FK to the task target: a user-deletion job must remain
    dispatchable while the profile and its user-owned rows are being purged.
    This table is internal to the API/worker DB role; it has RLS enabled with no
    client policies in the matching Alembic revision.
    """

    __tablename__ = "task_outbox"
    __table_args__ = (
        UniqueConstraint(
            "task_type",
            "target_type",
            "target_id",
            "generation",
            name="task_outbox_logical_job_unique",
        ),
        CheckConstraint("generation > 0", name="task_outbox_generation_positive"),
        CheckConstraint("attempt_count >= 0", name="task_outbox_attempts_nonnegative"),
        CheckConstraint(
            "worker_attempt_count >= 0", name="task_outbox_worker_attempts_nonnegative"
        ),
        CheckConstraint(
            "execution_status IN ('pending','completed','stale','terminal')",
            name="task_outbox_execution_status_valid",
        ),
        CheckConstraint(
            "execution_status NOT IN ('completed','stale','terminal') "
            "OR worker_completed_at IS NOT NULL",
            name="task_outbox_execution_terminal_timestamp",
        ),
        CheckConstraint(
            "(execution_lease_token IS NULL AND execution_lease_until IS NULL) OR "
            "(execution_lease_token IS NOT NULL AND execution_lease_until IS NOT NULL)",
            name="task_outbox_execution_lease_complete",
        ),
        CheckConstraint(
            "execution_status = 'pending' OR "
            "(execution_lease_token IS NULL AND execution_lease_until IS NULL)",
            name="task_outbox_terminal_without_execution_lease",
        ),
        Index("task_outbox_dispatch_idx", "dispatched_at", "available_at", "lease_until"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    task_type: Mapped[str] = mapped_column(String, nullable=False)
    target_type: Mapped[str] = mapped_column(String, nullable=False)
    target_id: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    generation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), init=False
    )
    available_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), nullable=False, init=False
    )
    lease_token: Mapped[str | None] = mapped_column(String, default=None)
    lease_until: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    dispatched_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
    execution_status: Mapped[str] = mapped_column(
        String, nullable=False, default="pending", server_default=text("'pending'")
    )
    worker_attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    worker_completed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), default=None
    )
    worker_last_error_code: Mapped[str | None] = mapped_column(String(120), default=None)
    execution_lease_token: Mapped[str | None] = mapped_column(String, default=None)
    execution_lease_until: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), init=False
    )
