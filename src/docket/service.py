"""What a move does, and what an entry looks like once you have read it.

Extracted from the router so there is one implementation rather than one per
caller. The board posts HTML forms and wild-life posts JSON; both arrive here.
This is the argument ``states.moves_available`` already makes about the
transition table, applied a level up: two paths to one rule is how the two come
to disagree, and the disagreement is always discovered from the outside.

The rest of this module is the *derived* half of an entry — whose turn it is,
how long it has actually been stuck, whether the thing it is waiting on has
happened. None of it is stored. A register that wrote these down would be a
register whose facts could go stale without anybody moving anything, which is
the failure the whole design is arranged against.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from docket import states
from docket.auth import Identity
from docket.models.entries import Entry, Move
from docket.schemas.entries import EntryRead, MoveCreate, OfferCreate


class Rejected(Exception):
    """The register will not take this, and the status a caller should see.

    One exception rather than several because the callers are different shapes —
    a JSON router raises HTTPException, the board re-renders the page with the
    reason on it — and both need the same three facts: it failed, why, and how
    loudly.
    """

    def __init__(self, detail: str, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


#: Whose move it is, before anything anybody said is taken into account.
#: Terminal states are absent: nobody's turn, because there is no turn left.
TURN_BY_STATE: dict[str, str] = {
    "offered": "delegate",
    "countered": "requester",
    "accepted": "delegate",
    "delivered": "requester",
}


# --- reading ---------------------------------------------------------------


def role_of(entry: Entry, party: str) -> str | None:
    """The caller's relation to this entry.

    Being *accountable* is not a role here and gets no moves. Accountability is
    who answers for the outcome, not who is in the conversation — conflating them
    is how a delegator ends up able to accept on someone else's behalf.
    """
    if party == entry.requester:
        return "requester"
    if entry.delegate is not None and party == entry.delegate:
        return "delegate"
    return None


def may_read(entry: Entry, identity: Identity) -> bool:
    """Whether this caller may see this entry at all.

    Being accountable reads and still does not move. The two questions are
    different: answering for an outcome you cannot see is not accountability,
    and being able to see a conversation is not being in it. ``role_of`` stays
    the narrower answer, and it is the one the transition table is asked.
    """
    if identity.is_operator:
        return True
    return identity.party in (entry.requester, entry.delegate, entry.accountable)


def scope(stmt: Select[tuple[Entry]], identity: Identity) -> Select[tuple[Entry]]:
    """``may_read`` pushed into SQL, so a listing does not read the whole
    register and then throw most of it away."""
    if identity.is_operator:
        return stmt
    return stmt.where(
        (Entry.requester == identity.party)
        | (Entry.delegate == identity.party)
        | (Entry.accountable == identity.party)
    )


@dataclass(frozen=True)
class Progress:
    """How far the conversation has actually got, as against how recently
    somebody spoke about it."""

    #: The last move that changed the state. The offer counts: it created one.
    at: datetime | None
    #: Moves since then, all of which are `update` by construction — no other
    #: move can leave the state where it was.
    updates_since: int


def progress(entry: Entry) -> Progress:
    at: datetime | None = None
    since = 0
    for m in entry.moves:
        if m.from_state != m.to_state:
            at, since = m.at, 0
        else:
            since += 1
    return Progress(at=at, updates_since=since)


def whose_turn(entry: Entry) -> str | None:
    """Which party the conversation is waiting on, or ``None`` if it has ended.

    The state says whose move it is. One thing overrides it: **a party that has
    just spoken is waiting, not stalling.** If the last move was an ``update`` by
    the very party whose turn it was, the ball is in the other court until they
    answer.

    That rule was written first in `fleetix/agent/docket.sh`, in a client, after
    an agent re-ran an accepted task every minute and posted an identical update
    each time — six model calls in six minutes to say the same thing. A rule that
    keeps the register honest cannot live in one client, because the next client
    will not have it. So it lives here, and it is *reported* rather than
    enforced: a party with something to say must always be able to say it, which
    is the entire reason `update` exists.
    """
    turn = TURN_BY_STATE.get(entry.state)
    if turn is None or not entry.moves:
        return turn
    last = entry.moves[-1]
    if last.move in states.STATE_PRESERVING and role_of(entry, last.by) == turn:
        return "delegate" if turn == "requester" else "requester"
    return turn


async def open_blockers(session: AsyncSession, entries: list[Entry]) -> set[uuid.UUID]:
    """Of the entries named by ``blocked_on``, the ones still open.

    One query for a whole listing rather than one per row. Whether a block still
    binds is derived for the same reason staleness is: nobody remembers to clear
    a flag, and a blockage that outlives its cause is worse than no record of it.
    """
    ids = {e.blocked_on for e in entries if e.blocked_on is not None}
    if not ids:
        return set()
    rows = await session.execute(
        select(Entry.id).where(
            Entry.id.in_(ids), Entry.state.notin_(tuple(states.TERMINAL))
        )
    )
    return set(rows.scalars())


def read_entry(entry: Entry, party: str, blockers: set[uuid.UUID] | None = None) -> EntryRead:
    role = role_of(entry, party)
    now = datetime.now(UTC)
    last = max((m.at for m in entry.moves), default=None)
    made = progress(entry)
    return EntryRead(
        **{c.name: getattr(entry, c.name) for c in entry.__table__.columns},
        moves=list(entry.moves),
        is_open=entry.state not in states.TERMINAL,
        quiet_for_seconds=(now - last).total_seconds() if last else None,
        last_progress_at=made.at,
        stalled_for_seconds=(now - made.at).total_seconds() if made.at else None,
        updates_since_progress=made.updates_since,
        whose_turn=whose_turn(entry),
        is_blocked=entry.blocked_on is not None and entry.blocked_on in (blockers or set()),
        available_moves=states.moves_available(entry.state, role),
        your_role=role,
    )


# --- writing ---------------------------------------------------------------


async def _next_seq(session: AsyncSession, entry_id: uuid.UUID) -> int:
    current = await session.scalar(select(Move.seq).where(Move.entry_id == entry_id)
                                   .order_by(Move.seq.desc()).limit(1))
    return (current or 0) + 1


async def _record(
    session: AsyncSession, entry: Entry, move: str, *, by: str, identity: Identity,
    before: str | None, after: str, note: str | None,
) -> None:
    session.add(
        Move(
            entry_id=entry.id,
            seq=await _next_seq(session, entry.id),
            at=datetime.now(UTC),
            by=by,
            recorded_by=identity.party,
            # Which credential did it, so that revoking one answers "and what
            # did it touch". Null when the owner's own token made the move.
            via_credential=identity.credential_id,
            move=move,
            from_state=before,
            to_state=after,
            note=note,
        )
    )


async def _resolve_blocker(
    session: AsyncSession, entry: Entry, blocked_on: uuid.UUID, identity: Identity
) -> None:
    if blocked_on == entry.id:
        raise Rejected("an entry cannot be waiting on itself")
    other = await session.get(Entry, blocked_on)
    if other is None or not may_read(other, identity):
        raise Rejected("no such entry to be blocked on", 404)
    entry.blocked_on = blocked_on


async def offer(session: AsyncSession, payload: OfferCreate, identity: Identity) -> Entry:
    """Ask someone to discharge something. The offer *is* the entry's creation."""
    if payload.delegate == identity.party:
        # Something you owe yourself is an intention, and intentions live in the
        # system that authored them. `docs/plan.md` found 443 of 446 wild-life
        # assignments were Paul to Paul, which is what this being expressible
        # costs: a register full of rows that say nothing about anybody.
        raise Rejected("a self-offer is not an offer; that is an intention, not an assignment")
    after = states.apply(None, "offer", "requester")  # the caller is the requester by definition
    entry = Entry(
        subject=payload.subject,
        subject_label=payload.subject_label,
        label_as_of=datetime.now(UTC) if payload.subject_label else None,
        requester=identity.party,
        delegate=payload.delegate,
        accountable=payload.accountable or identity.party,
        state=after,
        terms=payload.terms,
        expected_by=payload.expected_by,
        external_ref=payload.external_ref,
    )
    session.add(entry)
    await session.flush()
    await _record(session, entry, "offer", by=identity.party, identity=identity,
                  before=None, after=after, note=payload.note)
    await session.flush()
    await session.refresh(entry)
    return entry


async def apply_move(
    session: AsyncSession, entry: Entry, payload: MoveCreate, identity: Identity
) -> Entry:
    """Advance the conversation. ``entry`` must already be locked by the caller."""
    # Whose move is this? Normally the caller's. A party may also write down the
    # other party's move -- somebody with no account here cannot make it
    # themselves, and "Sara declined" has to be recordable or the register can
    # only describe people who use it.
    mover = identity.party
    if payload.on_behalf_of and payload.on_behalf_of != identity.party:
        if role_of(entry, identity.party) is None:
            raise Rejected("only a party to this entry may record the other party's move", 403)
        mover = payload.on_behalf_of

    before = entry.state
    try:
        after = states.apply(before, payload.move, role_of(entry, mover))
    except states.Refused as exc:
        # 409 and not 400: the request is well formed and the caller may be
        # entitled to make this move later. "You cannot deliver something you
        # have not accepted" is a fact about where the conversation stands.
        raise Rejected(str(exc), 409) from None

    if payload.move == "redirect":
        if not payload.to:
            raise Rejected("redirect needs `to`")
        if payload.to == mover:
            # The child would be an offer to yourself, which `offer` refuses for
            # the same reason. Handing work to yourself is not handing it on.
            raise Rejected("cannot redirect to yourself")
    if payload.terms is not None:
        entry.terms = payload.terms
    if payload.expected_by is not None:
        entry.expected_by = payload.expected_by
    if payload.blocked_on is not None:
        await _resolve_blocker(session, entry, payload.blocked_on, identity)

    entry.state = after
    await _record(session, entry, payload.move, by=mover, identity=identity,
                  before=before, after=after, note=payload.note)

    if payload.move == "redirect":
        # The chain: whoever is handing it on becomes the requester of the child,
        # and **accountable does not move** — that is the rule delegation exists
        # to keep, and it is why this is copied rather than reassigned.
        session.add(
            Entry(
                subject=entry.subject,
                subject_label=entry.subject_label,
                label_as_of=entry.label_as_of,
                requester=mover,
                delegate=payload.to,
                accountable=entry.accountable,
                state="offered",
                terms=payload.terms or entry.terms,
                expected_by=entry.expected_by,
                parent_id=entry.id,
            )
        )

    await session.flush()
    await session.refresh(entry)
    return entry


async def recent_moves(
    session: AsyncSession, identity: Identity, limit: int = 100
) -> list[tuple[Move, Entry]]:
    """Everything that has happened lately, newest first.

    The register is append-only and every move is dated, so a stream is the
    shape the data already has -- what was missing was anywhere to read it. The
    per-entry log answers "how did this go"; this answers "what happened today",
    which is a different question and the one you ask when several parties are
    working at once.

    Scoped like everything else: the owner sees all of it, and nobody else sees
    a move on a conversation they are not named on.
    """
    stmt = (
        select(Move, Entry)
        .join(Entry, Move.entry_id == Entry.id)
        .order_by(Move.at.desc())
        .limit(limit)
    )
    if not identity.is_operator:
        stmt = stmt.where(
            (Entry.requester == identity.party)
            | (Entry.delegate == identity.party)
            | (Entry.accountable == identity.party)
        )
    return [(m, e) for m, e in (await session.execute(stmt)).all()]


async def ended(session: AsyncSession, identity: Identity, limit: int = 12) -> list[Entry]:
    """Conversations that have finished, most recently first.

    They were invisible: the board asked for open entries only, so anything
    settled, declined or withdrawn left the page entirely and there was nowhere
    to see what you finished last week. **An ended conversation is not an ended
    commitment** -- that is the register's first rule -- so hiding them was also
    hiding the cases where something is still owed and nobody is holding it.
    """
    rows = await session.execute(
        scope(select(Entry), identity)
        .where(Entry.state.in_(tuple(states.TERMINAL)))
        .order_by(Entry.updated_at.desc())
        .limit(limit)
    )
    return list(rows.scalars().all())
