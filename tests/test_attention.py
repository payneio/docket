"""Quiet, stuck, and whose turn it is.

The register could always say how long since anybody spoke. It could not say how
long since anything *happened*, and on the first day it held anything real the
difference was the whole story: an agent posted seven identical updates in
thirty-one minutes, so `quiet_for_seconds` never rose above sixty while nothing
had moved since the accept.
"""

from sqlalchemy import text

from docket.db.session import AsyncSessionLocal
from tests.conftest import AGENT, subject


async def _offer(owner, **kw):
    r = await owner.post("/entries", json={"subject": subject(), "delegate": AGENT, **kw})
    assert r.status_code == 201, r.text
    return r.json()


async def _age_moves(entry_id: str, minutes: int) -> None:
    """Push every move on an entry back in time.

    Testing a derivation against a clock means either waiting or moving the
    clock; the derivation is what is under test, so move the clock.
    """
    async with AsyncSessionLocal() as s:
        await s.execute(
            text("UPDATE docket.moves SET at = at - make_interval(mins => :m) WHERE entry_id = :e"),
            {"m": minutes, "e": entry_id},
        )
        await s.commit()


async def test_an_update_storm_is_visible_as_one(owner, agent):
    """The live case, reproduced: seven updates and nothing moved.

    Before this the register reported sixty seconds of quiet and had no way to
    say that the last thirty-one minutes had produced nothing.
    """
    e = await _offer(owner, terms="Write the brief")
    e = (await agent.post(f"/entries/{e['id']}/moves", json={"move": "accept"})).json()
    await _age_moves(e["id"], 31)
    for _ in range(7):
        e = (await agent.post(
            f"/entries/{e['id']}/moves",
            json={"move": "update", "note": "run finished without completing the task"},
        )).json()

    assert e["state"] == "accepted"
    assert e["updates_since_progress"] == 7
    assert e["quiet_for_seconds"] < 60          # somebody spoke a moment ago
    assert e["stalled_for_seconds"] > 30 * 60   # and nothing has moved in half an hour


async def test_the_offer_counts_as_progress(owner, agent):
    e = await _offer(owner)
    assert e["updates_since_progress"] == 0
    assert e["last_progress_at"] is not None
    assert e["stalled_for_seconds"] is not None


async def test_a_party_that_has_just_spoken_is_waiting_not_stalling(owner, agent):
    """The rule that lived in a client until it had cost six model calls in six
    minutes. After the delegate reports a blocker the ball is the requester's,
    and it stays theirs however many times the delegate repeats itself."""
    e = await _offer(owner)
    e = (await agent.post(f"/entries/{e['id']}/moves", json={"move": "accept"})).json()
    assert e["whose_turn"] == "delegate"

    e = (await agent.post(
        f"/entries/{e['id']}/moves", json={"move": "update", "note": "blocked"}
    )).json()
    assert e["whose_turn"] == "requester"
    e = (await agent.post(
        f"/entries/{e['id']}/moves", json={"move": "update", "note": "still blocked"}
    )).json()
    assert e["whose_turn"] == "requester"

    # The requester answers, and it is the delegate's again.
    e = (await owner.post(
        f"/entries/{e['id']}/moves", json={"move": "update", "note": "do it this way"}
    )).json()
    assert e["whose_turn"] == "delegate"


async def test_a_nudge_does_not_take_the_ball(owner, agent):
    """An update from the party who is *not* holding it is a nudge. It must not
    hand them the work by accident."""
    e = await _offer(owner)
    e = (await agent.post(f"/entries/{e['id']}/moves", json={"move": "accept"})).json()
    e = (await owner.post(
        f"/entries/{e['id']}/moves", json={"move": "update", "note": "any progress?"}
    )).json()
    assert e["whose_turn"] == "delegate"


async def test_nobody_is_owed_a_turn_once_it_has_ended(owner, agent):
    e = await _offer(owner)
    e = (await agent.post(f"/entries/{e['id']}/moves", json={"move": "decline"})).json()
    assert e["whose_turn"] is None


async def test_blocked_on_is_an_edge_and_clears_itself(owner, agent):
    """What the planner did in prose: got stuck, asked a question, and recorded
    the connection as a sentence in a note. As an edge it can be read, and as a
    *derived* block it goes away when the question is answered."""
    work = await _offer(owner, terms="Write the brief")
    work = (await agent.post(f"/entries/{work['id']}/moves", json={"move": "accept"})).json()

    # The delegate asks the requester something: the same loop, other direction.
    question = (await agent.post("/entries", json={
        "subject": work["subject"], "delegate": work["requester"],
        "terms": "Outline, or research first?",
    })).json()

    work = (await agent.post(f"/entries/{work['id']}/moves", json={
        "move": "update", "note": "asked about the outline", "blocked_on": question["id"],
    })).json()
    assert work["blocked_on"] == question["id"]
    assert work["is_blocked"] is True
    assert work["state"] == "accepted"  # being blocked is not a state

    # Answered. Nothing was cleared, and it no longer binds.
    await owner.post(f"/entries/{question['id']}/moves", json={"move": "accept"})
    await owner.post(f"/entries/{question['id']}/moves", json={"move": "deliver"})
    await agent.post(f"/entries/{question['id']}/moves", json={"move": "settle"})

    work = (await owner.get(f"/entries/{work['id']}")).json()
    assert work["blocked_on"] == question["id"]  # the record stays
    assert work["is_blocked"] is False           # the block does not


async def test_an_entry_cannot_wait_on_itself(owner, agent):
    e = await _offer(owner)
    e = (await agent.post(f"/entries/{e['id']}/moves", json={"move": "accept"})).json()
    r = await agent.post(
        f"/entries/{e['id']}/moves", json={"move": "update", "blocked_on": e["id"]}
    )
    assert r.status_code == 400 and "itself" in r.json()["detail"]


async def test_the_attention_filters(owner, agent):
    stuck = await _offer(owner, terms="stuck")
    stuck = (await agent.post(f"/entries/{stuck['id']}/moves", json={"move": "accept"})).json()
    await _age_moves(stuck["id"], 120)
    fresh = await _offer(owner, terms="fresh")

    over = (await owner.get("/entries", params={"stalled_over": 3600})).json()
    assert [e["id"] for e in over] == [stuck["id"]]

    # Whose turn, from the requester's side: the fresh offer is the delegate's.
    theirs = (await owner.get("/entries", params={"turn": "delegate"})).json()
    assert {e["id"] for e in theirs} == {stuck["id"], fresh["id"]}
    assert (await owner.get("/entries", params={"turn": "mine"})).json() == []

    # And from the delegate's, `mine` is the same set the other way round.
    mine = (await agent.get("/entries", params={"turn": "mine"})).json()
    assert {e["id"] for e in mine} == {stuck["id"], fresh["id"]}


async def test_blocked_filter(owner, agent):
    a = await _offer(owner, terms="a")
    b = await _offer(owner, terms="b")
    await agent.post(f"/entries/{a['id']}/moves", json={"move": "accept"})
    await agent.post(
        f"/entries/{a['id']}/moves", json={"move": "update", "blocked_on": b["id"]}
    )
    assert [e["id"] for e in (await owner.get("/entries", params={"blocked": True})).json()] == [
        a["id"]
    ]
    unblocked = (await owner.get("/entries", params={"blocked": False})).json()
    assert b["id"] in {e["id"] for e in unblocked}

