"""What just happened, as it happens.

The register is append-only and every move is dated, so a feed is the shape the
data already had — what was missing was anywhere to read it without asking again.
``service.recent_moves`` answers "what happened today" for a page that is being
loaded; this answers it for a client that is already connected, which is the
difference between a chief of staff that polls every minute and one that sleeps.

**This does not make docket an orchestrator.** A read-only feed over the move log
routes nothing and schedules nothing. Nothing about who does what, or when,
moves into the register by adding it.

**Nor does it remove anyone's need for a timer.** Staleness is the *absence* of
events: no move will ever be published saying a delegate has gone quiet for three
days. A subscriber still has to sweep ``stalled_for_seconds`` on its own clock.
What this buys is that the sweep can be hourly instead of every sixty seconds.

Two things worth not rediscovering:

**Publication happens after commit, never before.** Events are staged on the
session and flushed by ``db.session.get_session`` once the transaction has
actually landed. Publishing from inside ``service`` would announce moves that
then rolled back, and a subscriber cannot un-see one.

**Filtering is per subscriber and server-side.** A credential names one party and
sees only what names it; that rule cannot weaken here. Learning that an entry you
are not on just moved tells you somebody owes somebody something, which is the
fact the register holds.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from docket.models.entries import Entry, Move

#: Staged events live here on the session until the transaction commits.
_STAGE_KEY = "docket_pending_events"

#: How many events a slow subscriber may fall behind before it is dropped. A
#: subscriber that cannot keep up with its own register is not one worth
#: blocking every writer for.
QUEUE_DEPTH = 256


@dataclass(frozen=True)
class Event:
    """One move, with just enough of its entry to be read without a round trip."""

    entry_id: uuid.UUID
    move_id: uuid.UUID
    seq: int
    at: datetime
    move: str
    by: str
    from_state: str | None
    to_state: str
    note: str | None
    subject: str
    subject_label: str | None
    requester: str
    delegate: str | None
    accountable: str

    def visible_to(self, party: str, is_owner: bool) -> bool:
        """The same rule ``service.may_read`` applies to an entry."""
        if is_owner:
            return True
        return party in (self.requester, self.delegate, self.accountable)

    def payload(self) -> dict[str, Any]:
        return {
            "entry_id": str(self.entry_id),
            "move_id": str(self.move_id),
            "seq": self.seq,
            "at": self.at.isoformat(),
            "move": self.move,
            "by": self.by,
            "from_state": self.from_state,
            "to_state": self.to_state,
            "note": self.note,
            "subject": self.subject,
            "subject_label": self.subject_label,
            "requester": self.requester,
            "delegate": self.delegate,
            "accountable": self.accountable,
        }


@dataclass
class Broker:
    """In-process fan-out. One uvicorn worker, so there is nothing to coordinate
    between processes — and if that ever stops being true this is where it shows,
    rather than silently delivering half the events to half the subscribers."""

    subscribers: set[asyncio.Queue[Event]] = field(default_factory=set)

    @contextlib.contextmanager
    def subscribe(self) -> Iterator[asyncio.Queue[Event]]:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=QUEUE_DEPTH)
        self.subscribers.add(queue)
        try:
            yield queue
        finally:
            self.subscribers.discard(queue)

    def publish(self, event: Event) -> None:
        for queue in list(self.subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Drop the subscriber, not the event, and not the writer. It will
                # reconnect and catch up with `since`, which is exactly the case
                # that parameter exists for.
                self.subscribers.discard(queue)


broker = Broker()


def stage(session: AsyncSession, event: Event) -> None:
    """Hold an event until the transaction it belongs to commits."""
    session.info.setdefault(_STAGE_KEY, []).append(event)


def publish_staged(session: AsyncSession) -> None:
    """Called by ``get_session`` after a successful commit, and only then."""
    for event in session.info.pop(_STAGE_KEY, []):
        broker.publish(event)


def discard_staged(session: AsyncSession) -> None:
    """Called on rollback. A move that did not land was not a move."""
    session.info.pop(_STAGE_KEY, None)


def from_move(entry: Entry, move: Move) -> Event:
    return Event(
        entry_id=entry.id,
        move_id=move.id,
        seq=move.seq,
        at=move.at or datetime.now(UTC),
        move=move.move,
        by=move.by,
        from_state=move.from_state,
        to_state=move.to_state,
        note=move.note,
        subject=entry.subject,
        subject_label=entry.subject_label,
        requester=entry.requester,
        delegate=entry.delegate,
        accountable=entry.accountable,
    )


async def heartbeats(queue: asyncio.Queue[Event], every: float) -> AsyncIterator[Event | None]:
    """Yield each event, and ``None`` whenever ``every`` seconds pass in silence.

    The silence is the point. A subscriber that has heard nothing cannot
    otherwise tell a quiet register from a dead connection — the same confusion
    wild-life's docket proxy avoids by reporting ``reachable`` separately from
    contents, and the same one that let a retired runner read a 404 as one item
    of work.
    """
    while True:
        try:
            yield await asyncio.wait_for(queue.get(), timeout=every)
        except TimeoutError:
            yield None
