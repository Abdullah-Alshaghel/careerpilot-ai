"""Career profile use cases."""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from collections.abc import Iterable

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from career_profile_service.models import CareerFact, CareerProfile, FactType, ProcessedEvent, VerificationStatus
from career_profile_service.parser import PARSER_VERSION, parse_cv
from career_profile_service.schemas import (
    Conflict,
    FactOut,
    FactSource,
    InvalidFactValue,
    ProfileOut,
    dedupe_key,
    detail_signature,
    validate_value,
    validity,
)
from careerpilot_common.errors import ServiceError

log = logging.getLogger("career_profile_service.service")


async def get_or_create_profile(db: AsyncSession, user_id: uuid.UUID, *, lock: bool = False) -> CareerProfile:
    stmt = select(CareerProfile).where(CareerProfile.user_id == user_id)
    if lock:
        stmt = stmt.with_for_update()
    profile = await db.scalar(stmt)
    if profile is not None:
        return profile
    # Concurrent first requests: insert-or-ignore, then read whichever row won.
    await db.execute(
        pg_insert(CareerProfile)
        .values(id=uuid.uuid4(), user_id=user_id)
        .on_conflict_do_nothing(index_elements=["user_id"])
    )
    profile = await db.scalar(stmt)
    assert profile is not None  # noqa: S101 - inserted or existed
    return profile


def _bump(profile: CareerProfile) -> None:
    profile.version = (profile.version or 1) + 1


# ------------------------------------------------------------- read model


def find_conflicts(facts: Iterable[CareerFact]) -> list[Conflict]:
    """Disagreements the user must resolve; we never pick a winner ourselves."""
    live = [f for f in facts if f.verification_status != VerificationStatus.REJECTED]
    conflicts: list[Conflict] = []

    names = [f for f in live if f.fact_type == FactType.PERSONAL and f.value.get("field") in ("full_name", "location")]
    by_field: dict[str, dict[str, list[CareerFact]]] = defaultdict(lambda: defaultdict(list))
    for fact in names:
        by_field[fact.value["field"]][fact.dedupe_key].append(fact)
    for groups in by_field.values():
        if len(groups) > 1:
            ids = [f.id for group in groups.values() for f in group]
            conflicts.append(Conflict(kind="multiple_values", fact_type=FactType.PERSONAL, fact_ids=ids))

    same_thing: dict[tuple[FactType, str], list[CareerFact]] = defaultdict(list)
    for fact in live:
        if fact.fact_type in (FactType.EMPLOYMENT, FactType.EDUCATION, FactType.LANGUAGE):
            same_thing[(fact.fact_type, fact.dedupe_key)].append(fact)
    for (fact_type, _), group in same_thing.items():
        if len(group) < 2:
            continue
        if fact_type == FactType.LANGUAGE:
            if len({(f.value.get("level") or "").casefold() for f in group}) > 1:
                conflicts.append(
                    Conflict(kind="different_details", fact_type=fact_type, fact_ids=[f.id for f in group])
                )
        elif len({(f.value.get("start"), f.value.get("end"), f.value.get("current", False)) for f in group}) > 1:
            conflicts.append(Conflict(kind="different_dates", fact_type=fact_type, fact_ids=[f.id for f in group]))
    return conflicts


def fact_out(fact: CareerFact) -> FactOut:
    source = None
    if fact.source_document_id or fact.source_name or fact.source_excerpt:
        source = FactSource(document_id=fact.source_document_id, name=fact.source_name, excerpt=fact.source_excerpt)
    return FactOut(
        id=fact.id,
        fact_type=fact.fact_type,
        value=fact.value,
        source=source,
        confidence=fact.confidence,
        verification_status=fact.verification_status,
        valid_from=fact.valid_from,
        valid_to=fact.valid_to,
        created_at=fact.created_at,
        updated_at=fact.updated_at,
    )


async def read_profile(db: AsyncSession, user_id: uuid.UUID) -> ProfileOut:
    profile = await get_or_create_profile(db, user_id)
    await db.commit()
    facts = list(
        await db.scalars(select(CareerFact).where(CareerFact.profile_id == profile.id).order_by(CareerFact.seq))
    )
    return ProfileOut(
        id=profile.id,
        version=profile.version,
        updated_at=profile.updated_at,
        facts=[fact_out(f) for f in facts],
        conflicts=find_conflicts(facts),
    )


# ----------------------------------------------------------- user actions


async def _owned_fact(db: AsyncSession, user_id: uuid.UUID, fact_id: uuid.UUID) -> tuple[CareerProfile, CareerFact]:
    row = (
        await db.execute(
            select(CareerProfile, CareerFact)
            .join(CareerFact, CareerFact.profile_id == CareerProfile.id)
            .where(CareerProfile.user_id == user_id, CareerFact.id == fact_id)
            .with_for_update()
        )
    ).first()
    if row is None:
        raise ServiceError(404, "not_found", "Fact not found")
    return row[0], row[1]


def _checked_value(fact_type: FactType, value: object) -> dict:
    try:
        return validate_value(fact_type, value)
    except InvalidFactValue as exc:
        raise ServiceError(422, "invalid_fact", "The fact is not valid for its type") from exc


async def add_fact(db: AsyncSession, user_id: uuid.UUID, fact_type: FactType, value: object) -> FactOut:
    clean = _checked_value(fact_type, value)
    profile = await get_or_create_profile(db, user_id, lock=True)
    valid_from, valid_to = validity(fact_type, clean)
    fact = CareerFact(
        profile_id=profile.id,
        fact_type=fact_type,
        value=clean,
        dedupe_key=dedupe_key(fact_type, clean),
        confidence=1.0,
        verification_status=VerificationStatus.USER_VERIFIED,  # the user stated it
        valid_from=valid_from,
        valid_to=valid_to,
    )
    db.add(fact)
    _bump(profile)
    await db.commit()
    await db.refresh(fact)
    return fact_out(fact)


async def update_fact(
    db: AsyncSession, user_id: uuid.UUID, fact_id: uuid.UUID, value: object | None, status: str | None
) -> FactOut:
    profile, fact = await _owned_fact(db, user_id, fact_id)
    if value is not None:
        clean = _checked_value(fact.fact_type, value)
        fact.value = clean
        fact.dedupe_key = dedupe_key(fact.fact_type, clean)
        fact.valid_from, fact.valid_to = validity(fact.fact_type, clean)
        # Editing is the user vouching for the value, unless they reject it.
        fact.verification_status = VerificationStatus.USER_VERIFIED
    if status is not None:
        fact.verification_status = VerificationStatus(status)
    _bump(profile)
    await db.commit()
    await db.refresh(fact)
    return fact_out(fact)


async def set_status_bulk(db: AsyncSession, user_id: uuid.UUID, fact_ids: list[uuid.UUID], status: str) -> int:
    profile = await get_or_create_profile(db, user_id, lock=True)
    result = await db.execute(
        update(CareerFact)
        .where(CareerFact.profile_id == profile.id, CareerFact.id.in_(fact_ids))
        .values(verification_status=VerificationStatus(status))
    )
    if result.rowcount:
        _bump(profile)
    await db.commit()
    return result.rowcount or 0


async def delete_fact(db: AsyncSession, user_id: uuid.UUID, fact_id: uuid.UUID) -> None:
    profile, fact = await _owned_fact(db, user_id, fact_id)
    await db.delete(fact)
    _bump(profile)
    await db.commit()


# ------------------------------------------------------------ event intake


async def _first_time(db: AsyncSession, event_id: uuid.UUID, event_type: str) -> bool:
    """Record the event id; False if it was already processed (redelivery)."""
    result = await db.execute(
        pg_insert(ProcessedEvent)
        .values(event_id=event_id, event_type=event_type)
        .on_conflict_do_nothing(index_elements=["event_id"])
        .returning(ProcessedEvent.event_id)
    )
    return result.scalar_one_or_none() is not None


async def ingest_document_text(
    db: AsyncSession,
    *,
    event_id: uuid.UUID,
    user_id: uuid.UUID,
    document_id: uuid.UUID,
    filename: str,
    text: str,
) -> int:
    """Turn a document's text into suggested facts. Returns how many were added.

    Idempotent per event. Re-extraction of the same document replaces that
    document's *unreviewed* suggestions; anything the user already approved or
    rejected stays, and is not suggested again.
    """
    if not await _first_time(db, event_id, "document.text_extracted"):
        await db.rollback()
        return 0
    profile = await get_or_create_profile(db, user_id, lock=True)
    await db.execute(
        delete(CareerFact).where(
            CareerFact.profile_id == profile.id,
            CareerFact.source_document_id == document_id,
            CareerFact.verification_status == VerificationStatus.EXTRACTED,
        )
    )
    existing = await db.execute(
        select(CareerFact.fact_type, CareerFact.dedupe_key, CareerFact.value).where(CareerFact.profile_id == profile.id)
    )
    known = {(key, detail_signature(fact_type, value)) for fact_type, key, value in existing}

    added = 0
    for candidate in parse_cv(text):
        fact_type = FactType(candidate.fact_type)
        try:
            clean = validate_value(fact_type, candidate.value)
        except InvalidFactValue:
            continue  # a rule produced something malformed: drop it, don't guess
        key = dedupe_key(fact_type, clean)
        # Same thing with the same details: a duplicate. Same thing with
        # different details (e.g. other dates): kept, and shown as a conflict.
        identity = (key, detail_signature(fact_type, clean))
        if identity in known:
            continue
        known.add(identity)
        db.add(
            CareerFact(
                profile_id=profile.id,
                fact_type=fact_type,
                value=clean,
                dedupe_key=key,
                source_document_id=document_id,
                source_name=filename[:255],
                source_excerpt=candidate.excerpt,
                extractor_version=PARSER_VERSION,
                confidence=round(candidate.confidence, 2),
                verification_status=VerificationStatus.EXTRACTED,
                valid_from=candidate.valid_from,
                valid_to=candidate.valid_to,
            )
        )
        added += 1
    _bump(profile)
    await db.commit()
    log.info("document facts suggested", extra={"document_id": str(document_id), "facts_added": added})
    return added


async def forget_document(db: AsyncSession, *, event_id: uuid.UUID, user_id: uuid.UUID, document_id: uuid.UUID) -> int:
    """The user deleted a document: drop its unreviewed suggestions. Approved
    facts stay (the user vouched for them) and keep their recorded excerpt."""
    if not await _first_time(db, event_id, "document.deleted"):
        await db.rollback()
        return 0
    profile = await db.scalar(select(CareerProfile).where(CareerProfile.user_id == user_id).with_for_update())
    if profile is None:
        await db.commit()
        return 0
    result = await db.execute(
        delete(CareerFact).where(
            CareerFact.profile_id == profile.id,
            CareerFact.source_document_id == document_id,
            CareerFact.verification_status == VerificationStatus.EXTRACTED,
        )
    )
    await db.execute(
        update(CareerFact)
        .where(CareerFact.profile_id == profile.id, CareerFact.source_document_id == document_id)
        .values(source_document_id=None)
    )
    _bump(profile)
    await db.commit()
    return result.rowcount or 0
