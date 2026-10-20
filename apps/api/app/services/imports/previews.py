"""Pure helpers for building private, selectable import-preview candidates."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from app.db.models import ImportPreviewCandidate
from app.services.imports.admission import (
    reservation_key_for_saved_audio,
    reservation_key_for_url,
)
from app.services.imports.ig_parser import ParsedExport, SavedAudio, SavedPost
from app.services.ingest.platform_detect import (
    UnsupportedPlatformError,
    detect_platform,
    normalize_url,
)


@dataclass(slots=True)
class _Candidate:
    reservation_key: str
    source_bucket: str
    kind: str
    source_url_norm: str | None
    title: str | None = None
    artist: str | None = None
    saved_at: datetime | None = None
    collection_names: list[str] = field(default_factory=list)


def build_preview_candidates(
    parsed: ParsedExport, *, preview_id: str, owner_id: str
) -> list[ImportPreviewCandidate]:
    """Return one selectable row per supported canonical reservation identity.

    Candidate order follows the export's stable parser order: top-level posts,
    then collection occurrences, then saved audio. Repeated media URLs collapse
    to one reservation and retain all distinct collection names.
    """

    candidates: dict[str, _Candidate] = {}

    def add_post(post: SavedPost, collection_name: str | None = None) -> None:
        try:
            platform = detect_platform(post.url)
            normalized_url = normalize_url(post.url)
            key = reservation_key_for_url(post.url)
        except (UnsupportedPlatformError, ValueError):
            return
        candidate = candidates.get(key)
        if candidate is None:
            candidate = _Candidate(
                reservation_key=key,
                source_bucket="instagram" if platform == "instagram" else "other",
                kind="media",
                source_url_norm=normalized_url,
                saved_at=post.saved_at,
            )
            candidates[key] = candidate
        if collection_name:
            normalized_name = " ".join(collection_name.split())
            if normalized_name and normalized_name not in candidate.collection_names:
                candidate.collection_names.append(normalized_name)

    for post in parsed.posts:
        add_post(post)
    for collection in parsed.collections:
        for post in collection.media:
            add_post(post, collection.name)

    for audio in parsed.audio:
        key = reservation_key_for_saved_audio(audio.title, audio.artist, audio.saved_at)
        candidates.setdefault(
            key,
            _Candidate(
                reservation_key=key,
                source_bucket="instagram",
                kind="audio",
                source_url_norm=None,
                title=audio.title,
                artist=audio.artist,
                saved_at=audio.saved_at,
            ),
        )
    return [
        ImportPreviewCandidate(
            preview_id=preview_id,
            candidate_id=str(uuid.uuid4()),
            owner_id=owner_id,
            ordinal=ordinal,
            reservation_key=candidate.reservation_key,
            source_bucket=candidate.source_bucket,
            kind=candidate.kind,
            source_url_norm=candidate.source_url_norm,
            title=candidate.title,
            artist=candidate.artist,
            saved_at=candidate.saved_at,
            collection_names=list(candidate.collection_names),
        )
        for ordinal, candidate in enumerate(candidates.values())
    ]

