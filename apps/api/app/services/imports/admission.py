"""Lifetime admission for bulk imports.

This service is deliberately separate from individual share-sheet saves. The caller
must provide a server-resolved tier and an explicitly configured policy; this module
does not infer entitlements or activate an unfinished product policy.
"""

from __future__ import annotations

import hashlib
import uuid
from hashlib import sha256
from dataclasses import dataclass
from enum import StrEnum
from typing import Sequence
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BulkImportReservation, Profile
from app.services.ingest.platform_detect import normalize_url


class AdmissionTier(StrEnum):
    FREE = "free"
    PREMIUM = "premium"


class SourceBucket(StrEnum):
    INSTAGRAM = "instagram"
    OTHER = "other"


class PremiumHistoryMode(StrEnum):
    CARRY_FORWARD = "carry_forward"
    SEPARATE_ALLOWANCE = "separate_allowance"


class EligibilityMode(StrEnum):
    EXCLUDE = "exclude"
    ADMIT = "admit"


class AdmissionStatus(StrEnum):
    ADMITTED = "admitted"
    ALREADY_ADMITTED = "already_admitted"
    EXCLUDED = "excluded"
    LIMIT_REACHED = "limit_reached"


@dataclass(frozen=True, slots=True)
class AdmissionPolicy:
    """Explicit decisions needed before admission can be enabled.

    D01: whether free reservations count against a later premium allowance.
    D02: whether existing library duplicates and individual manual shares qualify.
    D03: whether saved-audio candidates qualify.
    """

    version: str
    d01_premium_history: PremiumHistoryMode
    d02_existing_duplicate: EligibilityMode
    d02_manual_share: EligibilityMode
    d03_saved_audio: EligibilityMode

    def __post_init__(self) -> None:
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("admission policy version must be non-empty")
        # Enum annotations are not runtime validation. Fail closed for untyped config.
        if not isinstance(self.d01_premium_history, PremiumHistoryMode):
            raise ValueError("D01 premium history policy is not configured")
        if not isinstance(self.d02_existing_duplicate, EligibilityMode):
            raise ValueError("D02 duplicate policy is not configured")
        if not isinstance(self.d02_manual_share, EligibilityMode):
            raise ValueError("D02 manual-share policy is not configured")
        if not isinstance(self.d03_saved_audio, EligibilityMode):
            raise ValueError("D03 saved-audio policy is not configured")


@dataclass(frozen=True, slots=True)
class AdmissionCandidate:
    """One bulk candidate; duplicate flag must come from a server-side library lookup.

    This admission module does not query `items`; callers must compute
    `existing_library_item` against the canonical item identity before calling.
    """

    url: str
    source_bucket: SourceBucket
    existing_library_item: bool = False
    saved_audio: bool = False
    manual_share: bool = False


@dataclass(frozen=True, slots=True)
class CandidateAdmission:
    reservation_key: str
    source_bucket: SourceBucket
    status: AdmissionStatus


@dataclass(frozen=True, slots=True)
class AdmissionResult:
    tier: AdmissionTier
    candidates: tuple[CandidateAdmission, ...]
    total_used: int
    instagram_used: int
    other_used: int


@dataclass(frozen=True, slots=True)
class AllowanceSnapshot:
    tier: AdmissionTier
    policy_version: str
    total_limit: int
    total_used: int
    instagram_limit: int
    instagram_used: int
    other_limit: int
    other_used: int

    @property
    def total_remaining(self) -> int:
        return max(0, self.total_limit - self.total_used)

    @property
    def instagram_remaining(self) -> int:
        return max(0, self.instagram_limit - self.instagram_used)

    @property
    def other_remaining(self) -> int:
        return max(0, self.other_limit - self.other_used)


class AdmissionError(Exception):
    """Base class for expected admission failures."""


class AdmissionPolicyNotConfigured(AdmissionError):
    pass


class AdmissionProfileUnavailable(AdmissionError):
    pass


async def get_bulk_allowance(
    session: AsyncSession,
    *,
    user_id: str,
    tier: AdmissionTier,
    policy: AdmissionPolicy | None,
) -> AllowanceSnapshot:
    """Return an informational lifetime allowance snapshot for the API owner."""
    if policy is None or not isinstance(policy, AdmissionPolicy):
        raise AdmissionPolicyNotConfigured("bulk admission policy is not configured")
    profile = (
        await session.execute(select(Profile).where(Profile.id == user_id))
    ).scalar_one_or_none()
    if profile is None or profile.deleted_at is not None:
        raise AdmissionProfileUnavailable("account is unavailable for bulk admission")
    rows = (
        await session.execute(
            select(
                BulkImportReservation.tier,
                BulkImportReservation.source_bucket,
                func.count(BulkImportReservation.id),
            )
            .where(BulkImportReservation.user_id == user_id)
            .group_by(BulkImportReservation.tier, BulkImportReservation.source_bucket)
        )
    ).all()
    counts = {(str(row[0]), str(row[1])): int(row[2]) for row in rows}
    free_ig = counts.get((AdmissionTier.FREE.value, SourceBucket.INSTAGRAM.value), 0)
    free_other = counts.get((AdmissionTier.FREE.value, SourceBucket.OTHER.value), 0)
    premium_ig = counts.get((AdmissionTier.PREMIUM.value, SourceBucket.INSTAGRAM.value), 0)
    premium_other = counts.get((AdmissionTier.PREMIUM.value, SourceBucket.OTHER.value), 0)
    if tier is AdmissionTier.FREE:
        total = free_ig + free_other
        instagram = free_ig
        other = free_other
        return AllowanceSnapshot(tier, policy.version, 250, total, 150, instagram, 100, other)
    carry = policy.d01_premium_history is PremiumHistoryMode.CARRY_FORWARD
    instagram = premium_ig + (free_ig if carry else 0)
    other = premium_other + (free_other if carry else 0)
    return AllowanceSnapshot(
        tier,
        policy.version,
        1000,
        instagram + other,
        1000,
        instagram,
        1000,
        other,
    )


def reservation_key_for_url(url: str) -> str:
    """Return a stable SHA-256 key without storing or exposing the raw URL.

    The application's canonical item identity is hashed so admission and item
    uniqueness agree, including YouTube URL variants. Credentials and malformed or
    non-web URLs are rejected rather than hashed ambiguously.
    """

    try:
        parts = urlsplit(url.strip())
        scheme = parts.scheme.lower()
        host = parts.hostname
        _ = parts.port  # Validate malformed ports before delegating canonicalization.
    except (ValueError, AttributeError) as exc:
        raise ValueError("candidate URL is invalid") from exc
    if scheme not in {"http", "https"} or not host or parts.username or parts.password:
        raise ValueError("candidate URL must be an HTTP(S) URL without credentials")
    # Validate the input above, then share the exact canonicalizer used by saves and
    # imports. `normalize_url` intentionally drops tracking/query/fragment values.
    normalized = normalize_url(url)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def saved_audio_identity_url(title: str | None, artist: str | None, saved_at: object) -> str:
    """Synthetic stable identity for a metadata-only saved-audio export row."""
    identity = "\0".join((title or "", artist or "", str(saved_at or "")))
    digest = sha256(("saved-audio\0" + identity).encode("utf-8")).hexdigest()
    return f"https://saved-audio.invalid/{digest}"


def reservation_key_for_saved_audio(title: str | None, artist: str | None, saved_at: object) -> str:
    """Return an opaque admission key for one saved-audio export row."""
    return reservation_key_for_url(saved_audio_identity_url(title, artist, saved_at))


async def admit_bulk_import(
    session: AsyncSession,
    *,
    user_id: str,
    tier: AdmissionTier,
    candidates: Sequence[AdmissionCandidate],
    policy: AdmissionPolicy | None,
) -> AdmissionResult:
    """Reserve eligible bulk candidates atomically under the account row lock.

    The caller owns the surrounding transaction and must commit or roll it back.
    All callers that admit for an account must use this lock protocol. Existing
    reservations are lifetime charges; no item/import foreign key can refund them.
    """

    if policy is None or not isinstance(policy, AdmissionPolicy):
        raise AdmissionPolicyNotConfigured("bulk admission policy is not configured")
    if not isinstance(tier, AdmissionTier):
        raise ValueError("admission tier is invalid")
    if any(not isinstance(candidate.source_bucket, SourceBucket) for candidate in candidates):
        raise ValueError("candidate source bucket is invalid")

    # Lock owner first. This serializes account admissions before any usage read.
    profile = (
        await session.execute(select(Profile).where(Profile.id == user_id).with_for_update())
    ).scalar_one_or_none()
    if profile is None or profile.deleted_at is not None:
        raise AdmissionProfileUnavailable("account is unavailable for bulk admission")

    # Count durable reservations only after obtaining the account lock.
    rows = (
        await session.execute(
            select(
                BulkImportReservation.tier,
                BulkImportReservation.source_bucket,
                func.count(BulkImportReservation.id),
            )
            .where(BulkImportReservation.user_id == user_id)
            .group_by(BulkImportReservation.tier, BulkImportReservation.source_bucket)
        )
    ).all()
    counts: dict[tuple[str, str], int] = {(str(t), str(b)): int(c) for t, b, c in rows}

    keys = {reservation_key_for_url(candidate.url) for candidate in candidates}
    existing_keys: set[str] = set()
    if keys:
        existing_keys = set(
            (
                await session.execute(
                    select(BulkImportReservation.reservation_key).where(
                        BulkImportReservation.user_id == user_id,
                        BulkImportReservation.reservation_key.in_(keys),
                    )
                )
            )
            .scalars()
            .all()
        )

    free_ig = counts.get((AdmissionTier.FREE.value, SourceBucket.INSTAGRAM.value), 0)
    free_other = counts.get((AdmissionTier.FREE.value, SourceBucket.OTHER.value), 0)
    premium_count = sum(
        count
        for (stored_tier, _bucket), count in counts.items()
        if stored_tier == AdmissionTier.PREMIUM.value
    )
    free_count = free_ig + free_other
    premium_counted = premium_count
    if tier is AdmissionTier.PREMIUM and policy.d01_premium_history is PremiumHistoryMode.CARRY_FORWARD:
        premium_counted += free_count

    output: list[CandidateAdmission] = []
    admitted_in_batch: set[str] = set()
    for candidate in candidates:
        key = reservation_key_for_url(candidate.url)
        # Existing admissions take precedence over current eligibility/policy changes.
        if key in existing_keys:
            output.append(CandidateAdmission(key, candidate.source_bucket, AdmissionStatus.ALREADY_ADMITTED))
            continue
        if key in admitted_in_batch:
            output.append(CandidateAdmission(key, candidate.source_bucket, AdmissionStatus.ALREADY_ADMITTED))
            continue

        if candidate.manual_share and policy.d02_manual_share is EligibilityMode.EXCLUDE:
            output.append(CandidateAdmission(key, candidate.source_bucket, AdmissionStatus.EXCLUDED))
            continue
        if candidate.existing_library_item and policy.d02_existing_duplicate is EligibilityMode.EXCLUDE:
            output.append(CandidateAdmission(key, candidate.source_bucket, AdmissionStatus.EXCLUDED))
            continue
        if candidate.saved_audio and policy.d03_saved_audio is EligibilityMode.EXCLUDE:
            output.append(CandidateAdmission(key, candidate.source_bucket, AdmissionStatus.EXCLUDED))
            continue

        if tier is AdmissionTier.FREE:
            used_total = free_ig + free_other
            within_limit = (
                used_total < 250
                and (
                    (candidate.source_bucket is SourceBucket.INSTAGRAM and free_ig < 150)
                    or (candidate.source_bucket is SourceBucket.OTHER and free_other < 100)
                )
            )
        else:
            within_limit = premium_counted < 1000

        if not within_limit:
            output.append(CandidateAdmission(key, candidate.source_bucket, AdmissionStatus.LIMIT_REACHED))
            continue

        session.add(
            BulkImportReservation(
                id=str(uuid.uuid4()),
                user_id=user_id,
                reservation_key=key,
                source_bucket=candidate.source_bucket.value,
                tier=tier.value,
            )
        )
        existing_keys.add(key)
        admitted_in_batch.add(key)
        if tier is AdmissionTier.FREE:
            if candidate.source_bucket is SourceBucket.INSTAGRAM:
                free_ig += 1
            else:
                free_other += 1
        else:
            premium_counted += 1
            premium_count += 1
        output.append(CandidateAdmission(key, candidate.source_bucket, AdmissionStatus.ADMITTED))

    await session.flush()
    if tier is AdmissionTier.FREE:
        allowance_used = free_ig + free_other
        allowance_ig = free_ig
        allowance_other = free_other
    else:
        allowance_used = premium_counted
        if policy.d01_premium_history is PremiumHistoryMode.CARRY_FORWARD:
            allowance_ig = free_ig + counts.get(
                (AdmissionTier.PREMIUM.value, SourceBucket.INSTAGRAM.value), 0
            )
            allowance_other = free_other + counts.get(
                (AdmissionTier.PREMIUM.value, SourceBucket.OTHER.value), 0
            )
            # Include admissions made in this call in the bucket breakdown.
            for candidate, admitted in zip(candidates, output, strict=True):
                if admitted.status is AdmissionStatus.ADMITTED:
                    if candidate.source_bucket is SourceBucket.INSTAGRAM:
                        allowance_ig += 1
                    else:
                        allowance_other += 1
        else:
            allowance_ig = counts.get((AdmissionTier.PREMIUM.value, SourceBucket.INSTAGRAM.value), 0)
            allowance_other = counts.get((AdmissionTier.PREMIUM.value, SourceBucket.OTHER.value), 0)
            for candidate, admitted in zip(candidates, output, strict=True):
                if admitted.status is AdmissionStatus.ADMITTED:
                    if candidate.source_bucket is SourceBucket.INSTAGRAM:
                        allowance_ig += 1
                    else:
                        allowance_other += 1
    return AdmissionResult(
        tier=tier,
        candidates=tuple(output),
        total_used=allowance_used,
        instagram_used=allowance_ig,
        other_used=allowance_other,
    )
