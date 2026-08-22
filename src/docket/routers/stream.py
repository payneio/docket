"""The move log, as it happens.

``GET /stream`` — Server-Sent Events, one event per move, filtered to what the
caller may read. See ``docket.events`` for why publication happens after commit
and why this does not make the register an orchestrator.

**Heartbeats are not noise.** A subscriber that has heard nothing needs to be
able to tell a quiet register from a dead connection, and "nothing is happening"
is the answer this stream gives most of the time. Reachability is reported
separately from contents here as it is everywhere else in this stack.
"""

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import StreamingResponse

from docket import events, service
from docket.auth import Identity, current_identity
from docket.db.session import AsyncSessionLocal

router = APIRouter(tags=["stream"])

#: Seconds of silence before a comment frame is sent. Under the 60s most proxies
#: idle out at, and short enough that a client notices a dead link promptly.
HEARTBEAT_SECONDS = 20.0

#: How far back a reconnecting subscriber may ask to be caught up.
CATCHUP_LIMIT = 200


def _frame(name: str, data: dict, event_id: str | None = None) -> str:
    out = f"event: {name}\n"
    if event_id:
        out += f"id: {event_id}\n"
    return out + f"data: {json.dumps(data, separators=(',', ':'))}\n\n"


async def _catchup(identity: Identity, since: datetime) -> list[events.Event]:
    """Moves this caller missed while disconnected, oldest first.

    Its own session: the dependency's is closed once the response begins, and a
    streaming body outlives the handler that returned it.

    Bounded by ``at > since`` on a microsecond timestamp. Two moves in the same
    microsecond would lose one, which needs two writers to land inside a single
    tick of a service that awaits a database flush between them — worth stating
    rather than implying it cannot happen.
    """
    async with AsyncSessionLocal() as session:
        rows = await service.recent_moves(session, identity, limit=CATCHUP_LIMIT)
    missed = [events.from_move(entry, move) for move, entry in rows if move.at and move.at > since]
    return sorted(missed, key=lambda e: e.at)


@router.get("/stream")
async def stream(
    identity: Identity = Depends(current_identity),
    since: datetime | None = Query(
        None, description="replay moves after this instant before following live"
    ),
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
) -> StreamingResponse:
    """Follow the register. The owner sees every move; anybody else sees only
    moves on conversations they are named on."""
    # Read off the identity now. The generator below runs after this handler has
    # returned, and must not touch anything the request's lifecycle owns.
    party, is_owner = identity.party, identity.is_operator
    captured = Identity(party=party, via=identity.via, is_operator=is_owner)

    resume_from = since
    if resume_from is None and last_event_id:
        try:
            resume_from = datetime.fromisoformat(last_event_id)
        except ValueError:
            resume_from = None

    async def body() -> AsyncIterator[str]:
        # Subscribe *before* catching up, so a move landing between the two is
        # queued rather than lost. It may be delivered twice; every event
        # carries `move_id`, so a duplicate is recognisable and a gap is not.
        with events.broker.subscribe() as queue:
            yield _frame(
                "open",
                {
                    "party": party,
                    "owner": is_owner,
                    "at": datetime.now(UTC).isoformat(),
                    "heartbeat_seconds": HEARTBEAT_SECONDS,
                },
            )
            if resume_from is not None:
                for missed in await _catchup(captured, resume_from):
                    if missed.visible_to(party, is_owner):
                        yield _frame("move", missed.payload(), missed.at.isoformat())

            async for event in events.heartbeats(queue, HEARTBEAT_SECONDS):
                # No `is_disconnected()` poll. Starlette cancels this generator
                # when the client goes away, which is the signal that actually
                # works; the poll needs a receive channel and blocks forever on
                # transports that do not provide one.
                if event is None:
                    # A comment frame: keeps the connection warm and says
                    # "still here, nothing to report" without inventing a move.
                    yield f": heartbeat {datetime.now(UTC).isoformat()}\n\n"
                    continue
                if event.visible_to(party, is_owner):
                    yield _frame("move", event.payload(), event.at.isoformat())

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # Caddy and nginx both buffer by default, which turns a live stream
            # into a very slow batch.
            "X-Accel-Buffering": "no",
        },
    )
