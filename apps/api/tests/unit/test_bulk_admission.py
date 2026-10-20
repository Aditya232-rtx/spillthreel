from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest
from sqlalchemy.sql import Select

from app.db.models import BulkImportReservation
from app.services.imports.admission import (
    AdmissionCandidate,
    AdmissionPolicy,
    AdmissionPolicyNotConfigured,
    AdmissionProfileUnavailable,
    AdmissionStatus,
    AdmissionTier,
    EligibilityMode,
    PremiumHistoryMode,
    SourceBucket,
    admit_bulk_import,
    get_bulk_allowance,
    reservation_key_for_url,
)


def policy(
    *,
    premium_history: PremiumHistoryMode = PremiumHistoryMode.CARRY_FORWARD,
    duplicates: EligibilityMode = EligibilityMode.EXCLUDE,
    manual_shares: EligibilityMode = EligibilityMode.EXCLUDE,
    saved_audio: EligibilityMode = EligibilityMode.EXCLUDE,
) -> AdmissionPolicy:
    return AdmissionPolicy(
        version="test-policy-v1",
        d01_premium_history=premium_history,
        d02_existing_duplicate=duplicates,
        d02_manual_share=manual_shares,
        d03_saved_audio=saved_audio,
    )


class _Rows:
    def __init__(self, values: Sequence[Any]) -> None:
        self.values = values

    def all(self) -> list[Any]:
        return list(self.values)


class _Scalars(_Rows):
    pass


class _Result:
    def __init__(self, *, scalar: Any = None, rows: Sequence[Any] = ()) -> None:
        self.scalar = scalar
        self.rows = rows

    def scalar_one_or_none(self) -> Any:
        return self.scalar

    def all(self) -> list[Any]:
        return list(self.rows)

    def scalars(self) -> _Scalars:
        return _Scalars(self.rows)


class _Session:
    """Small statement-aware fake; transaction semantics are covered by DB integration."""

    def __init__(
        self,
        *,
        profile: Any = type("ProfileRow", (), {"deleted_at": None})(),
        counts: Sequence[tuple[str, str, int]] = (),
        existing_keys: Sequence[str] = (),
    ) -> None:
        self.profile = profile
        self.counts = counts
        self.existing_keys = existing_keys
        self.statements: list[Select[Any]] = []
        self.added: list[BulkImportReservation] = []
        self.flushed = False

    async def execute(self, statement: Select[Any]) -> _Result:
        self.statements.append(statement)
        sql = str(statement).lower()
        if "profiles" in sql:
            return _Result(scalar=self.profile)
        if "group by" in sql:
            return _Result(rows=self.counts)
        return _Result(rows=self.existing_keys)

    def add(self, value: BulkImportReservation) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flushed = True


@pytest.mark.asyncio
async def test_free_caps_are_enforced_by_bucket_and_total_under_owner_lock() -> None:
    session = _Session(
        counts=[
            ("free", "instagram", 149),
            ("free", "other", 100),
        ]
    )
    result = await admit_bulk_import(
        session,  # type: ignore[arg-type]
        user_id="user-1",
        tier=AdmissionTier.FREE,
        candidates=[
            AdmissionCandidate("https://instagram.com/reel/new", SourceBucket.INSTAGRAM),
            AdmissionCandidate("https://instagram.com/reel/over", SourceBucket.INSTAGRAM),
            AdmissionCandidate("https://tiktok.com/video/new", SourceBucket.OTHER),
        ],
        policy=policy(),
    )

    assert [candidate.status for candidate in result.candidates] == [
        AdmissionStatus.ADMITTED,
        AdmissionStatus.LIMIT_REACHED,
        AdmissionStatus.LIMIT_REACHED,
    ]
    assert result.total_used == 250
    assert result.instagram_used == 150
    assert result.other_used == 100
    assert len(session.added) == 1
    assert session.added[0].reservation_key == reservation_key_for_url(
        "https://instagram.com/reel/new"
    )
    assert "FOR UPDATE" in str(session.statements[0]).upper()
    assert session.flushed


@pytest.mark.asyncio
async def test_replay_returns_already_admitted_without_inserting_again() -> None:
    url = "https://example.com/watch?v=1#player"
    key = reservation_key_for_url(url)
    session = _Session(existing_keys=[key])
    result = await admit_bulk_import(
        session,  # type: ignore[arg-type]
        user_id="user-1",
        tier=AdmissionTier.FREE,
        candidates=[AdmissionCandidate(url, SourceBucket.OTHER)],
        policy=policy(),
    )

    assert result.candidates[0].status is AdmissionStatus.ALREADY_ADMITTED
    assert session.added == []


@pytest.mark.asyncio
async def test_only_a_newly_admitted_batch_duplicate_is_an_already_admitted_replay() -> None:
    url = "https://example.com/watch/duplicate"
    session = _Session(counts=[("free", "instagram", 150)])
    result = await admit_bulk_import(
        session,  # type: ignore[arg-type]
        user_id="user-1",
        tier=AdmissionTier.FREE,
        candidates=[
            AdmissionCandidate(url, SourceBucket.INSTAGRAM),
            AdmissionCandidate(url, SourceBucket.INSTAGRAM),
        ],
        policy=policy(),
    )

    assert [candidate.status for candidate in result.candidates] == [
        AdmissionStatus.LIMIT_REACHED,
        AdmissionStatus.LIMIT_REACHED,
    ]
    assert session.added == []


@pytest.mark.asyncio
async def test_canonical_youtube_variants_share_one_reservation() -> None:
    session = _Session()
    result = await admit_bulk_import(
        session,  # type: ignore[arg-type]
        user_id="user-1",
        tier=AdmissionTier.FREE,
        candidates=[
            AdmissionCandidate(
                "https://youtu.be/abcdefghijk?si=tracking", SourceBucket.OTHER
            ),
            AdmissionCandidate(
                "https://m.youtube.com/shorts/abcdefghijk?feature=share", SourceBucket.OTHER
            ),
        ],
        policy=policy(),
    )

    assert [candidate.status for candidate in result.candidates] == [
        AdmissionStatus.ADMITTED,
        AdmissionStatus.ALREADY_ADMITTED,
    ]
    assert result.candidates[0].reservation_key == result.candidates[1].reservation_key
    assert len(session.added) == 1


@pytest.mark.asyncio
async def test_premium_cap_is_pooled_and_history_mode_is_explicit() -> None:
    candidate = AdmissionCandidate("https://tiktok.com/video/premium", SourceBucket.OTHER)
    carry_forward = _Session(
        counts=[
            ("free", "instagram", 150),
            ("free", "other", 100),
            ("premium", "other", 750),
        ]
    )
    result = await admit_bulk_import(
        carry_forward,  # type: ignore[arg-type]
        user_id="user-1",
        tier=AdmissionTier.PREMIUM,
        candidates=[candidate],
        policy=policy(premium_history=PremiumHistoryMode.CARRY_FORWARD),
    )
    assert result.candidates[0].status is AdmissionStatus.LIMIT_REACHED

    separate = _Session(
        counts=[
            ("free", "instagram", 150),
            ("free", "other", 100),
            ("premium", "instagram", 999),
        ]
    )
    result = await admit_bulk_import(
        separate,  # type: ignore[arg-type]
        user_id="user-1",
        tier=AdmissionTier.PREMIUM,
        candidates=[candidate, AdmissionCandidate("https://x.com/another", SourceBucket.OTHER)],
        policy=policy(premium_history=PremiumHistoryMode.SEPARATE_ALLOWANCE),
    )
    assert [entry.status for entry in result.candidates] == [
        AdmissionStatus.ADMITTED,
        AdmissionStatus.LIMIT_REACHED,
    ]
    assert result.total_used == 1000


@pytest.mark.asyncio
async def test_policy_is_required_and_missing_or_deleted_profiles_fail_closed() -> None:
    session = _Session()
    with pytest.raises(AdmissionPolicyNotConfigured):
        await admit_bulk_import(
            session,  # type: ignore[arg-type]
            user_id="user-1",
            tier=AdmissionTier.FREE,
            candidates=[],
            policy=None,
        )

    missing = _Session(profile=None)
    with pytest.raises(AdmissionProfileUnavailable):
        await admit_bulk_import(
            missing,  # type: ignore[arg-type]
            user_id="user-1",
            tier=AdmissionTier.FREE,
            candidates=[],
            policy=policy(),
        )
    deleted = _Session(profile=type("DeletedProfile", (), {"deleted_at": object()})())
    with pytest.raises(AdmissionProfileUnavailable):
        await admit_bulk_import(
            deleted,  # type: ignore[arg-type]
            user_id="user-1",
            tier=AdmissionTier.FREE,
            candidates=[],
            policy=policy(),
        )


@pytest.mark.asyncio
async def test_explicit_policy_controls_duplicate_manual_share_and_saved_audio() -> None:
    session = _Session()
    result = await admit_bulk_import(
        session,  # type: ignore[arg-type]
        user_id="user-1",
        tier=AdmissionTier.FREE,
        candidates=[
            AdmissionCandidate("https://x.com/duplicate", SourceBucket.OTHER, existing_library_item=True),
            AdmissionCandidate("https://x.com/share", SourceBucket.OTHER, manual_share=True),
            AdmissionCandidate("https://x.com/audio", SourceBucket.OTHER, saved_audio=True),
        ],
        policy=policy(
            duplicates=EligibilityMode.ADMIT,
            manual_shares=EligibilityMode.ADMIT,
            saved_audio=EligibilityMode.ADMIT,
        ),
    )

    assert [candidate.status for candidate in result.candidates] == [
        AdmissionStatus.ADMITTED,
        AdmissionStatus.ADMITTED,
        AdmissionStatus.ADMITTED,
    ]
    assert len(session.added) == 3


@pytest.mark.asyncio
async def test_allowance_snapshot_reports_confirmed_free_buckets() -> None:
    session = _Session(counts=[("free", "instagram", 120), ("free", "other", 80)])
    snapshot = await get_bulk_allowance(
        session,  # type: ignore[arg-type]
        user_id="user-1",
        tier=AdmissionTier.FREE,
        policy=policy(),
    )

    assert snapshot.total_limit == 250
    assert snapshot.total_used == 200
    assert snapshot.total_remaining == 50
    assert snapshot.instagram_remaining == 30
    assert snapshot.other_remaining == 20


def test_url_hash_is_stable_and_never_contains_the_url() -> None:
    first = reservation_key_for_url("HTTPS://Example.com/reel/1#caption")
    second = reservation_key_for_url("https://example.com/reel/1")

    assert first == second
    assert len(first) == 64
    assert "example.com" not in first
    with pytest.raises(ValueError):
        reservation_key_for_url("https://user:password@example.com/reel/1")
