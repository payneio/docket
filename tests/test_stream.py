"""The feed, and the two things it must never get wrong.

A subscriber must not learn that an entry it is not named on just moved — that
alone tells it somebody owes somebody something, which is the fact the register
holds. And a move that rolled back was not a move: publishing before the commit
lands would announce one, and a subscriber cannot un-see it.
"""

import asyncio
import json
import uuid
from datetime import UTC, datetime

import pytest

from docket import events
from docket.auth import Identity
from docket.routers.stream import stream
from tests.conftest import AGENT, PAUL, client_as, subject

STRANGER = "wild-life:person:99999999-9999-9999-9999-999999999999"


def _parse(raw: str) -> tuple[str, dict | None]:
    name, payload = "", None
    for line in raw.splitlines():
        if line.startswith("event: "):
            name = line[7:]
        elif line.startswith("data: "):
            payload = json.loads(line[6:])
        elif line.startswith(":"):
            name = name or "heartbeat"
    return name, payload


async def _frames(response, until: str, limit: float = 5.0) -> list[tuple[str, dict | None]]:
    """Read frames off the response body until one named `until` arrives.

    Straight off ``body_iterator`` rather than through an HTTP client, because
    httpx's ``ASGITransport`` buffers a whole response before yielding any of
    it — measured: the first chunk of a stream whose second frame is five
    seconds out arrives at t=5.01s, carrying both. An endless feed never yields
    through it at all. The real HTTP path is proved against the running service
    instead; this exercises the generator, the filtering and the framing.
    """
    got: list[tuple[str, dict | None]] = []

    async def pump() -> None:
        async for raw in response.body_iterator:
            for part in raw.split("\n\n"):
                if part.strip():
                    got.append(_parse(part))
                    if got[-1][0] == until:
                        return

    await asyncio.wait_for(pump(), timeout=limit)
    return got


# --- the module, on its own -------------------------------------------------

def test_visibility_follows_the_parties_on_the_entry():
    event = events.Event(
        entry_id=uuid.uuid4(), move_id=uuid.uuid4(), seq=1, at=datetime.now(UTC),
        move="offer", by=PAUL,
        from_state=None, to_state="offered", note=None,
        subject="x:y:1", subject_label=None,
        requester=PAUL, delegate=AGENT, accountable=PAUL,
    )
    assert event.visible_to(PAUL, False)
    assert event.visible_to(AGENT, False)
    assert not event.visible_to(STRANGER, False)
    # The owner reads past the parties, here as everywhere else.
    assert event.visible_to(STRANGER, True)


def test_a_full_subscriber_is_dropped_rather_than_blocking_the_writer():
    broker = events.Broker()
    with broker.subscribe() as queue:
        assert queue in broker.subscribers
        for i in range(events.QUEUE_DEPTH + 5):
            broker.publish(
                events.Event(
                    entry_id=uuid.uuid4(), move_id=uuid.uuid4(), seq=i,
                    at=datetime.now(UTC), move="update", by=PAUL,
                    from_state="accepted", to_state="accepted", note=None,
                    subject="x:y:1", subject_label=None,
                    requester=PAUL, delegate=AGENT, accountable=PAUL,
                )
            )
        # It fell behind, so it is gone; the writer never blocked and never raised.
        assert queue not in broker.subscribers


# --- the endpoint -----------------------------------------------------------

def _as(party: str, owner: bool = False) -> Identity:
    return Identity(party=party, via="bearer", is_operator=owner)


async def _open(identity: Identity):
    """The response, with its subscription already live.

    Reading the `open` frame is what starts the generator, and the generator
    subscribes before it yields anything — so once this returns, a move made
    afterwards is guaranteed to be queued rather than raced past.
    """
    response = await stream(identity=identity, since=None, last_event_id=None)
    frames = await _frames(response, until="open")
    assert frames[-1][0] == "open"
    return response


@pytest.mark.asyncio
async def test_a_move_reaches_a_subscriber(owner):
    response = await _open(_as(PAUL, owner=True))
    made = await owner.post(
        "/entries", json={"subject": subject(), "delegate": AGENT, "terms": "watch this land"}
    )
    assert made.status_code == 201

    name, payload = (await _frames(response, until="move"))[-1]
    assert name == "move"
    assert payload is not None
    assert payload["move"] == "offer"
    assert payload["to_state"] == "offered"
    assert payload["delegate"] == AGENT
    assert payload["entry_id"] == made.json()["id"]
    assert payload["requester"] == PAUL


@pytest.mark.asyncio
async def test_a_stranger_is_not_told_that_an_entry_moved(owner):
    """The register's first privacy rule, on the feed as on every other surface.

    Learning that *something* moved is itself the disclosure: it says somebody
    owes somebody something, which is the whole of what the register holds.
    """
    response = await _open(_as(STRANGER))
    await owner.post(
        "/entries", json={"subject": subject(), "delegate": AGENT, "terms": "none of theirs"}
    )
    with pytest.raises(asyncio.TimeoutError):
        await _frames(response, until="move", limit=1.0)


@pytest.mark.asyncio
async def test_a_delegate_is_told_about_its_own_entry(owner):
    response = await _open(_as(AGENT))
    made = await owner.post(
        "/entries", json={"subject": subject(), "delegate": AGENT, "terms": "yours"}
    )
    _, payload = (await _frames(response, until="move"))[-1]
    assert payload is not None
    assert payload["entry_id"] == made.json()["id"]


@pytest.mark.asyncio
async def test_a_rejected_move_is_never_published(owner):
    """A move that did not land was not a move. `deliver` on an entry nobody has
    accepted is a 409, and the feed must stay silent about it."""
    made = await owner.post(
        "/entries", json={"subject": subject(), "delegate": AGENT, "terms": "will not deliver"}
    )
    entry_id = made.json()["id"]

    response = await _open(_as(PAUL, owner=True))
    refused = await owner.post(f"/entries/{entry_id}/moves", json={"move": "deliver"})
    assert refused.status_code == 409
    with pytest.raises(asyncio.TimeoutError):
        await _frames(response, until="move", limit=1.0)


@pytest.mark.asyncio
async def test_the_stream_refuses_a_caller_with_no_token():
    async with client_as(token="") as anonymous:
        assert (await anonymous.get("/stream")).status_code == 401


@pytest.mark.asyncio
async def test_the_new_delegate_hears_about_a_redirect(owner, agent):
    """It never did: the only event described the parent, which the new
    delegate is not on, so it was offered work and told nothing."""
    made = (await owner.post("/entries", json={"subject": subject(), "delegate": AGENT})).json()
    await agent.post(f"/entries/{made['id']}/moves", json={"move": "accept"})
    response = await _open(_as("mailto:sub@example.invalid"))
    await agent.post(f"/entries/{made['id']}/moves",
                     json={"move": "redirect", "to": "mailto:sub@example.invalid"})

    _, payload = (await _frames(response, until="move"))[-1]
    assert payload is not None
    assert payload["move"] == "offer" and payload["delegate"] == "mailto:sub@example.invalid"
    assert payload["entry_id"] != made["id"]
