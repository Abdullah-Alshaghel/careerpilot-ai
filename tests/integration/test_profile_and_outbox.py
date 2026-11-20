"""Career-profile rules and the outbox relay against real PostgreSQL databases.

Covers critical test #16 (event idempotency) and #15 (isolation) for profile
data. Run with CAREERPILOT_INTEGRATION=1.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from career_profile_service import service
from career_profile_service.models import CareerFact, FactType, VerificationStatus
from careerpilot_common.errors import ServiceError
from careerpilot_common.messaging import enqueue_event, new_event, outbox_table, relay_outbox
from document_service.models import outbox as documents_outbox

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("CAREERPILOT_INTEGRATION") != "1", reason="set CAREERPILOT_INTEGRATION=1"),
]

CV = """Ada Lovelace
ada@example.com

Skills
Python, Go, SQL

Work Experience
Software Engineer, Acme Inc    2019 - 2021
"""


def async_url(url: str) -> str:
    return url.replace("?connect_timeout=5", "")


@pytest.fixture
async def profile_db(scratch_profile_db):
    engine = create_async_engine(async_url(scratch_profile_db))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def ingest(sm, user, document, text_=CV, event_id=None, filename="cv.pdf"):
    async with sm() as db:
        return await service.ingest_document_text(
            db, event_id=event_id or uuid.uuid4(), user_id=user, document_id=document, filename=filename, text=text_
        )


async def facts_of(sm, user):
    async with sm() as db:
        return (await service.read_profile(db, user)).facts


async def test_ingest_suggests_facts_with_provenance(profile_db):
    user, document = uuid.uuid4(), uuid.uuid4()
    added = await ingest(profile_db, user, document)
    facts = await facts_of(profile_db, user)
    assert added == len(facts) > 0
    assert all(f.verification_status == VerificationStatus.EXTRACTED for f in facts)
    assert all(f.source and f.source.document_id == document and f.source.excerpt for f in facts)
    assert [f.value["name"] for f in facts if f.fact_type == FactType.SKILL] == ["Python", "Go", "SQL"]


async def test_redelivered_event_is_processed_once(profile_db):
    user, document, event = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    first = await ingest(profile_db, user, document, event_id=event)
    second = await ingest(profile_db, user, document, event_id=event)
    assert first > 0 and second == 0
    assert len(await facts_of(profile_db, user)) == first


async def test_reextraction_keeps_decisions_and_never_resuggests_rejected_facts(profile_db):
    user, document = uuid.uuid4(), uuid.uuid4()
    await ingest(profile_db, user, document)
    facts = {f.value.get("name"): f for f in await facts_of(profile_db, user) if f.fact_type == FactType.SKILL}
    async with profile_db() as db:
        await service.update_fact(db, user, facts["Python"].id, None, "user_verified")
    async with profile_db() as db:
        await service.update_fact(db, user, facts["Go"].id, None, "rejected")

    await ingest(profile_db, user, document)  # same document extracted again
    skills = [f for f in await facts_of(profile_db, user) if f.fact_type == FactType.SKILL]
    by_name = {f.value["name"]: f.verification_status for f in skills}
    assert by_name == {
        "Python": VerificationStatus.USER_VERIFIED,
        "Go": VerificationStatus.REJECTED,
        "SQL": VerificationStatus.EXTRACTED,
    }
    assert len(skills) == 3  # no duplicates


async def test_second_document_adds_only_new_facts_and_flags_conflicts(profile_db):
    user = uuid.uuid4()
    await ingest(profile_db, user, uuid.uuid4())
    other = CV.replace("Ada Lovelace", "Ada King").replace("2019 - 2021", "2018 - 2021") + "\nSkills\nRust\n"
    await ingest(profile_db, user, uuid.uuid4(), text_=other, filename="old-cv.docx")
    async with profile_db() as db:
        profile = await service.read_profile(db, user)
    names = [f.value["value"] for f in profile.facts if f.value.get("field") == "full_name"]
    assert sorted(names) == ["Ada King", "Ada Lovelace"]
    kinds = {c.kind for c in profile.conflicts}
    assert kinds == {"multiple_values", "different_dates"}
    skills = [f.value["name"] for f in profile.facts if f.fact_type == FactType.SKILL]
    assert skills.count("Python") == 1 and "Rust" in skills


async def test_user_edits_add_and_bulk_review(profile_db):
    user = uuid.uuid4()
    await ingest(profile_db, user, uuid.uuid4())
    facts = await facts_of(profile_db, user)
    job = next(f for f in facts if f.fact_type == FactType.EMPLOYMENT)
    async with profile_db() as db:
        edited = await service.update_fact(db, user, job.id, {**job.value, "title": "Senior Software Engineer"}, None)
    assert edited.verification_status == VerificationStatus.USER_VERIFIED  # editing vouches for it
    assert edited.value["title"] == "Senior Software Engineer"

    async with profile_db() as db:
        manual = await service.add_fact(db, user, FactType.SKILL, {"name": "Kubernetes"})
    assert manual.verification_status == VerificationStatus.USER_VERIFIED
    assert manual.source is None and manual.confidence == 1.0

    pending = [f.id for f in await facts_of(profile_db, user) if f.verification_status == VerificationStatus.EXTRACTED]
    async with profile_db() as db:
        assert await service.set_status_bulk(db, user, pending, "user_verified") == len(pending)
    assert all(f.verification_status != VerificationStatus.EXTRACTED for f in await facts_of(profile_db, user))

    async with profile_db() as db:
        before = (await service.read_profile(db, user)).version
        await service.delete_fact(db, user, manual.id)
    async with profile_db() as db:
        assert (await service.read_profile(db, user)).version == before + 1


async def test_invalid_manual_fact_is_422(profile_db):
    async with profile_db() as db:
        with pytest.raises(ServiceError) as err:
            await service.add_fact(db, uuid.uuid4(), FactType.EMPLOYMENT, {"title": ""})
    assert err.value.status_code == 422


async def test_users_cannot_touch_each_others_facts(profile_db):
    alice, bob = uuid.uuid4(), uuid.uuid4()
    await ingest(profile_db, alice, uuid.uuid4())
    alice_fact = (await facts_of(profile_db, alice))[0]

    for action in (
        lambda db: service.update_fact(db, bob, alice_fact.id, None, "rejected"),
        lambda db: service.delete_fact(db, bob, alice_fact.id),
    ):
        async with profile_db() as db:
            with pytest.raises(ServiceError) as err:
                await action(db)
        assert err.value.status_code == 404
    async with profile_db() as db:
        assert await service.set_status_bulk(db, bob, [alice_fact.id], "rejected") == 0
    assert await facts_of(profile_db, bob) == []
    unchanged = next(f for f in await facts_of(profile_db, alice) if f.id == alice_fact.id)
    assert unchanged.verification_status == VerificationStatus.EXTRACTED


async def test_deleting_a_document_drops_only_its_unreviewed_suggestions(profile_db):
    user, document = uuid.uuid4(), uuid.uuid4()
    await ingest(profile_db, user, document)
    kept = next(f for f in await facts_of(profile_db, user) if f.fact_type == FactType.SKILL)
    async with profile_db() as db:
        await service.update_fact(db, user, kept.id, None, "user_verified")
    async with profile_db() as db:
        removed = await service.forget_document(db, event_id=uuid.uuid4(), user_id=user, document_id=document)
    remaining = await facts_of(profile_db, user)
    assert removed > 0
    assert [f.id for f in remaining] == [kept.id]
    assert remaining[0].source.document_id is None
    assert remaining[0].source.excerpt  # the evidence the user approved is kept


# ---------------------------------------------------------------- outbox


async def test_outbox_relay_publishes_in_order_and_retries_failures(scratch_documents_db):
    engine = create_async_engine(async_url(scratch_documents_db))
    sm = async_sessionmaker(engine, expire_on_commit=False)
    try:
        events = [
            new_event("document.deleted", aggregate_id=str(i), user_id=uuid.uuid4(), producer="t", payload={"n": i})
            for i in range(3)
        ]
        async with sm() as db, db.begin():
            for event in events:
                await enqueue_event(db, documents_outbox, event)

        sent: list[str] = []
        broker = {"up": False}

        async def publish(routing_key, envelope):
            if not broker["up"]:
                raise ConnectionError("broker down")
            sent.append(envelope["aggregate_id"])

        assert await relay_outbox(sm, documents_outbox, publish) == 0  # nothing lost while down
        broker["up"] = True
        assert await relay_outbox(sm, documents_outbox, publish) == 3
        assert sent == ["0", "1", "2"]
        assert await relay_outbox(sm, documents_outbox, publish) == 0  # each published once
        async with sm() as db:
            attempts = (await db.execute(text("SELECT attempts FROM outbox_events ORDER BY created_at"))).scalars()
            assert list(attempts) == [2, 1, 1]
    finally:
        await engine.dispose()


def test_outbox_table_shape_is_shared():
    # Every producer declares the same outbox through careerpilot_common.
    from sqlalchemy import MetaData

    assert [c.name for c in outbox_table(MetaData()).columns] == [c.name for c in documents_outbox.columns]
    assert select(CareerFact.id) is not None
