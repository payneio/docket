"""The conversation, end to end, against a real database."""

from docket.config import settings
from tests.conftest import AGENT, subject


def owner_party() -> str:
    return settings.owner_party


async def _offer(owner, **kw):
    r = await owner.post("/entries", json={"subject": subject(), "delegate": AGENT, **kw})
    assert r.status_code == 201, r.text
    return r.json()


async def test_the_happy_path(owner, agent):
    e = await _offer(owner, terms="Draft the paper", subject_label="Publish the paper")
    assert e["state"] == "offered" and e["your_role"] == "requester"

    e = (await agent.post(f"/entries/{e['id']}/moves", json={"move": "accept"})).json()
    assert e["state"] == "accepted" and e["your_role"] == "delegate"

    e = (await agent.post(f"/entries/{e['id']}/moves", json={"move": "deliver"})).json()
    assert e["state"] == "delivered"

    e = (await owner.post(f"/entries/{e['id']}/moves", json={"move": "settle"})).json()
    assert e["state"] == "settled" and e["is_open"] is False
    assert [m["move"] for m in e["moves"]] == ["offer", "accept", "deliver", "settle"]
    assert [m["seq"] for m in e["moves"]] == [1, 2, 3, 4]


async def test_a_decline_ends_the_conversation_and_not_the_commitment(owner, agent):
    """The done-test for this phase, and the reason the two are separate services.

    Docket cannot end a commitment: the intention lives elsewhere and nothing
    here can write to it. What the register shows afterwards is a closed
    conversation about a subject that is still perfectly offerable to someone
    else — which is exactly what "they said no; you still owe it" looks like.
    """
    subj = subject()
    r = await owner.post("/entries", json={"subject": subj, "delegate": AGENT})
    first = r.json()

    first = (await agent.post(
        f"/entries/{first['id']}/moves", json={"move": "decline", "note": "No capacity"}
    )).json()
    assert first["state"] == "declined" and first["is_open"] is False
    assert first["subject"] == subj  # untouched: the decline said nothing about what is owed

    # Still owed, so it can be offered again — to anyone.
    again = (await owner.post(
        "/entries", json={"subject": subj, "delegate": "mailto:someone@example.invalid"}
    )).json()
    assert again["state"] == "offered"

    open_now = (await owner.get("/entries", params={"subject": subj})).json()
    assert [e["id"] for e in open_now] == [again["id"]]
    both = (await owner.get("/entries", params={"subject": subj, "open_only": False})).json()
    assert len(both) == 2


async def test_a_counter_hands_the_decision_back(owner, agent):
    e = await _offer(owner, terms="By Friday")
    e = (await agent.post(
        f"/entries/{e['id']}/moves", json={"move": "counter", "terms": "By the 14th"}
    )).json()
    assert e["state"] == "countered" and e["terms"] == "By the 14th"
    # The terms on the table are the delegate's now, so accepting them is ours.
    assert "accept" not in e["available_moves"]  # ...not the delegate's, whose view this is
    e = (await owner.post(f"/entries/{e['id']}/moves", json={"move": "accept"})).json()
    assert e["state"] == "accepted"


async def test_a_return_continues_the_same_conversation(owner, agent):
    e = await _offer(owner)
    for who, move in ((agent, "accept"), (agent, "deliver"), (owner, "return"),
                      (agent, "deliver"), (owner, "settle")):
        e = (await who.post(f"/entries/{e['id']}/moves", json={"move": move})).json()
    assert e["state"] == "settled"
    # Six moves on one entry, not two entries of three: "we went round twice" survives.
    assert [m["move"] for m in e["moves"]] == [
        "offer", "accept", "deliver", "return", "deliver", "settle"
    ]
    assert [m["to_state"] for m in e["moves"]][3] == "accepted"


async def test_a_redirect_opens_a_child_and_accountability_stays_put(owner, agent):
    """S48. However long the chain gets, the head answers for it."""
    e = await _offer(owner, terms="Find the source")
    e = (await agent.post(f"/entries/{e['id']}/moves", json={"move": "accept"})).json()
    e = (await agent.post(
        f"/entries/{e['id']}/moves",
        json={"move": "redirect", "to": "mailto:sub@example.invalid"},
    )).json()
    assert e["state"] == "redirected" and e["is_open"] is False

    child = (await owner.get("/entries", params={"parent_id": e["id"]})).json()[0]
    assert child["requester"] == AGENT           # whoever handed it on is asking
    assert child["delegate"] == "mailto:sub@example.invalid"
    assert child["accountable"] == e["accountable"]  # never moves
    assert child["state"] == "offered"
    # It opens with an offer, like any other entry, made by whoever handed it on.
    assert [(m["move"], m["by"], m["to_state"]) for m in child["moves"]] == [
        ("offer", AGENT, "offered")
    ]
    assert child["whose_turn"] == "delegate"


async def test_a_third_party_cannot_move_it(owner, agent):
    """Being accountable is not being in the conversation."""
    e = await _offer(owner)
    e = (await agent.post(f"/entries/{e['id']}/moves", json={"move": "accept"})).json()
    r = await owner.post(f"/entries/{e['id']}/moves", json={"move": "deliver"})
    assert r.status_code == 409 and "only the delegate" in r.json()["detail"]


async def test_a_move_out_of_turn_is_a_conflict_not_a_bad_request(owner, agent):
    e = await _offer(owner)
    r = await agent.post(f"/entries/{e['id']}/moves", json={"move": "deliver"})
    assert r.status_code == 409
    assert "cannot deliver from 'offered'" in r.json()["detail"]


async def test_silence_is_measured_not_stored(owner, agent):
    e = await _offer(owner)
    got = (await owner.get(f"/entries/{e['id']}")).json()
    assert got["quiet_for_seconds"] is not None and got["quiet_for_seconds"] >= 0
    assert "quiet_for_seconds" not in {c for c in got if c.startswith("_")}


async def test_a_party_is_whatever_its_credential_says(agent):
    me = (await agent.get("/me")).json()
    assert me["party"] == AGENT and me["via"] == "bearer" and me["owner"] is False


async def test_an_unknown_caller_gets_nothing(owner):
    from tests.conftest import client_as
    async with client_as("dk_nobody-issued-this") as stranger:
        assert (await stranger.get("/me")).status_code == 401
    async with client_as("") as silent:  # presenting nothing at all
        assert (await silent.get("/me")).status_code == 401


async def test_a_party_may_record_the_other_partys_move(owner):
    """Someone with no account here cannot act, so the requester writes it down.

    The log keeps both facts apart: whose move it was, and who typed it. Without
    that, "Sara declined" is either forbidden or filed as Paul's own decline, and
    both are lies.
    """
    subj = subject()
    sara = "mailto:sara@example.invalid"
    e = (await owner.post("/entries", json={"subject": subj, "delegate": sara})).json()

    e = (await owner.post(
        f"/entries/{e['id']}/moves",
        json={"move": "decline", "on_behalf_of": sara, "note": "said no by email"},
    )).json()
    assert e["state"] == "declined"

    decline = e["moves"][-1]
    assert decline["by"] == sara                    # whose move it was
    assert decline["recorded_by"] == owner_party()  # who wrote it down


async def test_the_log_records_which_credential_made_the_move(owner, agent):
    """What is left of `attested`, and the half that was doing any work: revoke a
    credential and this is how you find out what it touched."""
    e = await _offer(owner)
    e = (await agent.post(f"/entries/{e['id']}/moves", json={"move": "accept"})).json()
    offer_move, accept = e["moves"][0], e["moves"][-1]
    assert accept["by"] == accept["recorded_by"] == AGENT
    assert accept["via_credential"] is not None
    # The owner's own token is configuration, not a row, so it names none.
    assert offer_move["via_credential"] is None


async def test_a_stranger_cannot_record_moves_for_others(owner, agent):
    """Recording on someone's behalf is a privilege of being in the conversation.

    The refusal is a 404 rather than a 403, and it comes from reading rather than
    from moving: a stranger does not get to learn that the entry exists. Saying
    "that is not yours" would already say somebody owes somebody something, which
    is the whole of what the register holds.
    """
    from tests.conftest import client_as, issue

    e = await _offer(owner)
    outsider = await issue("wild-life:person:22222222-2222-2222-2222-222222222222")
    async with client_as(outsider) as stranger:
        r = await stranger.post(
            f"/entries/{e['id']}/moves", json={"move": "accept", "on_behalf_of": AGENT}
        )
        assert r.status_code == 404


async def test_a_party_to_nothing_sees_nothing(owner, agent):
    """Before this, any authenticated caller could read the whole register.

    Credentials are handed out freely by design, so "you may not move it" was
    never the same protection as "you may not see it". The listing scopes and
    the fetch 404s.
    """
    from tests.conftest import client_as, issue

    e = await _offer(owner, terms="not the stranger's business")
    stranger_party = "wild-life:person:33333333-3333-3333-3333-333333333333"
    async with client_as(await issue(stranger_party, "uninvolved")) as stranger:
        assert (await stranger.get("/entries")).json() == []
        assert (await stranger.get(f"/entries/{e['id']}")).status_code == 404

    # The delegate, who is a party, sees exactly the one.
    assert [x["id"] for x in (await agent.get("/entries")).json()] == [e["id"]]


async def test_accountable_reads_and_still_does_not_move(owner, agent):
    """Answering for an outcome you cannot see is not accountability. Being able
    to see a conversation is still not being in it, so the moves stay empty."""
    from tests.conftest import client_as, issue

    boss = "wild-life:person:44444444-4444-4444-4444-444444444444"
    e = await _offer(owner, accountable=boss)
    async with client_as(await issue(boss, "accountable")) as watcher:
        got = (await watcher.get(f"/entries/{e['id']}")).json()
        assert got["id"] == e["id"]
        assert got["your_role"] is None and got["available_moves"] == []
        r = await watcher.post(f"/entries/{e['id']}/moves", json={"move": "accept"})
        assert r.status_code == 409


async def test_two_moves_at_once_give_one_answer_and_one_conflict(owner, agent):
    """The entry row is locked for the move, so the second mover waits and then
    reads the state the first one left. Without the lock both read `offered`,
    both allow the accept, and both write seq 2 — a 500 on the unique index, or
    a lost state write, depending on who commits first."""
    import asyncio

    e = await _offer(owner)
    both = await asyncio.gather(
        agent.post(f"/entries/{e['id']}/moves", json={"move": "accept"}),
        agent.post(f"/entries/{e['id']}/moves", json={"move": "accept"}),
    )
    assert sorted(r.status_code for r in both) == [200, 409]

    final = (await owner.get(f"/entries/{e['id']}")).json()
    assert final["state"] == "accepted"
    assert [m["seq"] for m in final["moves"]] == [1, 2]


async def test_a_self_offer_is_refused(owner):
    """It is an intention, and intentions live in the system that authored them."""
    r = await owner.post("/entries", json={"subject": subject(), "delegate": owner_party()})
    assert r.status_code == 400 and "self-offer" in r.json()["detail"]


async def test_the_listing_is_bounded(owner, agent):
    for _ in range(3):
        await _offer(owner)
    assert len((await owner.get("/entries", params={"limit": 2})).json()) == 2
    assert len((await owner.get("/entries", params={"limit": 2, "offset": 2})).json()) == 1
    assert (await owner.get("/entries", params={"limit": 9999})).status_code == 422
