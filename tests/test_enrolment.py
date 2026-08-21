"""Registering yourself, with a ticket somebody else wrote.

The point of a ticket is that what sits in a provisioning script expires, and the
agent's long-lived credential is minted at the far end and never travels. The
point of the *party* being on the ticket is that an agent can prove it was sent
and cannot decide who it is.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from docket.db.session import AsyncSessionLocal
from tests.conftest import client_as, subject

CODER = "wild-life:person:77777777-7777-7777-7777-777777777777"


async def _ticket(owner, party=CODER, **kw):
    r = await owner.post("/enrolments", json={"party": party, "label": "coder", **kw})
    assert r.status_code == 201, r.text
    return r.json()


def _bearer(token: str):
    return client_as(token)


async def _expire(enrolment_id: str) -> None:
    async with AsyncSessionLocal() as s:
        await s.execute(
            text("UPDATE docket.enrolments SET expires_at = :t WHERE id = :i"),
            {"t": datetime.now(UTC) - timedelta(minutes=1), "i": enrolment_id},
        )
        await s.commit()


async def test_an_agent_enrols_itself_and_keeps_the_secret(owner):
    ticket = await _ticket(owner)
    assert ticket["token"].startswith("dke_")   # tellable from a credential on sight

    async with _bearer(ticket["token"]) as newcomer:
        got = (await newcomer.post("/enrol", json={"label": "coder on the laptop"})).json()
    assert got["party"] == CODER
    assert got["token"].startswith("dk_")

    async with _bearer(got["token"]) as coder:
        me = (await coder.get("/me")).json()
    assert me["party"] == CODER and me["owner"] is False
    assert me["credential"] == got["credential_id"]

    # And the credential remembers where it came from.
    issued = (await owner.get("/credentials")).json()
    assert [c["label"] for c in issued] == ["coder on the laptop"]


async def test_an_enroller_cannot_choose_who_it_is(owner):
    """The whole security argument. A ticket that let its holder name its own
    party would be the owner's token with extra steps."""
    ticket = await _ticket(owner, party=CODER)
    async with _bearer(ticket["token"]) as newcomer:
        got = (await newcomer.post("/enrol", json={
            "label": "nice try", "party": "wild-life:person:0b085c55-0000-0000-0000-000000000000",
        })).json()
    # The extra field is simply not part of the request; the ticket decides.
    assert got["party"] == CODER


async def test_a_ticket_is_not_a_credential(owner):
    """Separate tables, so this is structural rather than remembered: nothing
    that identifies a party ever looks in `enrolments`."""
    ticket = await _ticket(owner)
    async with _bearer(ticket["token"]) as holder:
        assert (await holder.get("/me")).status_code == 401
        assert (await holder.get("/entries")).status_code == 401
        # 401, not 403: it is not a party with insufficient rights, it is nobody.
        assert (await holder.get("/credentials")).status_code == 401


async def test_a_credential_is_not_a_ticket(owner):
    """And the other way: a party's own credential cannot mint more of itself."""
    ticket = await _ticket(owner)
    async with _bearer(ticket["token"]) as newcomer:
        mine = (await newcomer.post("/enrol", json={})).json()
    async with _bearer(mine["token"]) as coder:
        assert (await coder.post("/enrol", json={})).status_code == 401
        assert (await coder.post("/enrolments", json={"party": CODER})).status_code == 403


async def test_a_ticket_is_spent_once(owner):
    ticket = await _ticket(owner)
    async with _bearer(ticket["token"]) as first:
        assert (await first.post("/enrol", json={})).status_code == 201
        assert (await first.post("/enrol", json={})).status_code == 401


async def test_a_ticket_may_be_spent_more_than_once_if_it_says_so(owner):
    ticket = await _ticket(owner, max_uses=3)
    tokens = []
    async with _bearer(ticket["token"]) as holder:
        for _ in range(3):
            tokens.append((await holder.post("/enrol", json={})).json()["token"])
        assert (await holder.post("/enrol", json={})).status_code == 401
    # Three separate credentials, so one machine can be retired on its own.
    assert len(set(tokens)) == 3
    assert len((await owner.get("/credentials")).json()) == 3


async def test_an_expired_ticket_buys_nothing(owner):
    ticket = await _ticket(owner)
    await _expire(ticket["id"])
    async with _bearer(ticket["token"]) as late:
        assert (await late.post("/enrol", json={})).status_code == 401


async def test_a_revoked_ticket_buys_nothing_and_leaves_its_credentials_alone(owner):
    """Revoking a ticket must not take an agent offline. They are separate
    things, and a ticket forgotten last month should not be a time bomb."""
    ticket = await _ticket(owner, max_uses=5)
    async with _bearer(ticket["token"]) as holder:
        mine = (await holder.post("/enrol", json={})).json()
        assert (await owner.delete(f"/enrolments/{ticket['id']}")).status_code == 200
        assert (await holder.post("/enrol", json={})).status_code == 401

    async with _bearer(mine["token"]) as still_working:
        assert (await still_working.get("/me")).status_code == 200


async def test_every_refusal_says_the_same_thing(owner):
    """A caller probing must not learn whether a ticket was wrong, expired,
    spent or revoked. The difference is a free oracle."""
    spent = await _ticket(owner)
    async with _bearer(spent["token"]) as h:
        await h.post("/enrol", json={})
    expired = await _ticket(owner)
    await _expire(expired["id"])
    revoked = await _ticket(owner)
    await owner.delete(f"/enrolments/{revoked['id']}")

    bodies = set()
    for tok in (spent["token"], expired["token"], revoked["token"], "dke_invented", ""):
        async with _bearer(tok) as probe:
            r = await probe.post("/enrol", json={})
            assert r.status_code == 401
            bodies.add(r.text)
    assert len(bodies) == 1


async def test_only_the_owner_writes_tickets(owner):
    ticket = await _ticket(owner)
    async with _bearer(ticket["token"]) as holder:
        mine = (await holder.post("/enrol", json={})).json()
    async with _bearer(mine["token"]) as coder:
        assert (await coder.get("/enrolments")).status_code == 403
        assert (await coder.delete(f"/enrolments/{ticket['id']}")).status_code == 403


async def test_an_enrolled_agent_is_a_full_party(owner):
    """Nothing about arriving by ticket makes it a lesser kind of party."""
    ticket = await _ticket(owner)
    async with _bearer(ticket["token"]) as newcomer:
        mine = (await newcomer.post("/enrol", json={})).json()

    e = (await owner.post("/entries", json={
        "subject": subject(), "delegate": CODER, "terms": "Ship the patch",
    })).json()
    async with _bearer(mine["token"]) as coder:
        e = (await coder.post(f"/entries/{e['id']}/moves", json={"move": "accept"})).json()
        assert e["state"] == "accepted"
        assert e["moves"][-1]["via_credential"] == mine["credential_id"]


async def test_a_ticket_needs_a_party_that_looks_like_one(owner):
    assert (await owner.post("/enrolments", json={"party": "coder"})).status_code == 422


async def test_the_ticket_row_actually_locks(owner):
    """Single-use has to mean it under concurrency, and this is the mechanism the
    endpoint leans on: `SELECT ... FOR UPDATE` on the ticket.

    Tested here at the session level rather than through two overlapping
    requests, because the suite's in-process transport happens to run them one
    after the other — a race test that passes whether or not the lock is there
    would be worse than none, since it would read as coverage.
    """
    import asyncio
    import uuid as _uuid

    from sqlalchemy import select

    from docket.models.enrolments import Enrolment

    ticket = await _ticket(owner)
    eid = _uuid.UUID(ticket["id"])
    grab = select(Enrolment).where(Enrolment.id == eid).with_for_update()

    async with AsyncSessionLocal() as first, AsyncSessionLocal() as second:
        held = (await first.execute(grab)).scalar_one()
        assert held.used_count == 0
        held.used_count += 1
        await first.flush()

        waiting = asyncio.create_task(second.execute(grab))
        await asyncio.sleep(0.25)
        assert not waiting.done(), "the second redeemer was not blocked"

        await first.commit()
        assert (await waiting).scalar_one().used_count == 1  # it sees the spend
        await second.rollback()
