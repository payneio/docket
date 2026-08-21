"""The register, and the moves that change it."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from docket import states
from docket.auth import Identity, current_identity
from docket.db.session import get_session
from docket.models.entries import Entry, Move
from docket.schemas.entries import EntryRead, MoveCreate, OfferCreate

router = APIRouter(prefix="/entries", tags=["entries"])


def _role(entry: Entry, party: str) -> str | None:
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


def _read(entry: Entry, party: str) -> EntryRead:
    role = _role(entry, party)
    last = max((m.at for m in entry.moves), default=None)
    quiet = (datetime.now(UTC) - last).total_seconds() if last else None
    return EntryRead(
        **{c.name: getattr(entry, c.name) for c in entry.__table__.columns},
        moves=list(entry.moves),
        is_open=entry.state not in states.TERMINAL,
        quiet_for_seconds=quiet,
        available_moves=states.moves_available(entry.state, role),
        your_role=role,
    )


async def _next_seq(session: AsyncSession, entry_id: uuid.UUID) -> int:
    current = await session.scalar(select(func.max(Move.seq)).where(Move.entry_id == entry_id))
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
            # Only a move a party made itself, over a socket the kernel vouched
            # for, is corroborated by anything other than the writer.
            attested=identity.attested and by == identity.party,
            move=move,
            from_state=before,
            to_state=after,
            note=note,
        )
    )


@router.post("", response_model=EntryRead, status_code=status.HTTP_201_CREATED)
async def offer(
    payload: OfferCreate,
    session: AsyncSession = Depends(get_session),
    identity: Identity = Depends(current_identity),
) -> EntryRead:
    """Ask someone to discharge something. The offer *is* the entry's creation."""
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
    return _read(entry, identity.party)


@router.get("", response_model=list[EntryRead])
async def list_entries(
    session: AsyncSession = Depends(get_session),
    identity: Identity = Depends(current_identity),
    party: str | None = Query(None, description="entries where this URI is requester or delegate"),
    subject: str | None = Query(None),
    parent_id: uuid.UUID | None = Query(None, description="the children of a redirected entry"),
    open_only: bool = Query(True, description="exclude ended conversations"),
) -> list[EntryRead]:
    stmt = select(Entry)
    if party:
        stmt = stmt.where((Entry.requester == party) | (Entry.delegate == party))
    if subject:
        stmt = stmt.where(Entry.subject == subject)
    if parent_id:
        stmt = stmt.where(Entry.parent_id == parent_id)
    if open_only:
        stmt = stmt.where(Entry.state.notin_(tuple(states.TERMINAL)))
    stmt = stmt.order_by(Entry.updated_at.desc())
    rows = (await session.execute(stmt)).scalars().all()
    return [_read(e, identity.party) for e in rows]


@router.get("/{entry_id}", response_model=EntryRead)
async def get_entry(
    entry_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    identity: Identity = Depends(current_identity),
) -> EntryRead:
    entry = await session.get(Entry, entry_id)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
    return _read(entry, identity.party)


@router.post("/{entry_id}/moves", response_model=EntryRead)
async def make_move(
    entry_id: uuid.UUID,
    payload: MoveCreate,
    session: AsyncSession = Depends(get_session),
    identity: Identity = Depends(current_identity),
) -> EntryRead:
    """Advance the conversation.

    Refusals are 409, not 400: the request is well formed and the caller may be
    entitled to make this move later. "You cannot deliver something you have not
    accepted" is a fact about where the conversation stands, not a malformed
    request.
    """
    entry = await session.get(Entry, entry_id)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")

    # Whose move is this? Normally the caller's. A party may also write down the
    # other party's move -- somebody with no account here cannot make it
    # themselves, and "Sara declined" has to be recordable or the register can
    # only describe people who use it.
    mover = identity.party
    if payload.on_behalf_of and payload.on_behalf_of != identity.party:
        if _role(entry, identity.party) is None:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail="only a party to this entry may record the other party's move",
            )
        mover = payload.on_behalf_of

    before = entry.state
    try:
        after = states.apply(before, payload.move, _role(entry, mover))
    except states.Refused as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from None

    if payload.move == "redirect" and not payload.to:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="redirect needs `to`")
    if payload.terms is not None:
        entry.terms = payload.terms
    if payload.expected_by is not None:
        entry.expected_by = payload.expected_by

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
    return _read(entry, identity.party)


@router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def erase(
    entry_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    identity: Identity = Depends(current_identity),
) -> None:
    """Remove an entry that should never have existed.

    **This is not how a conversation ends.** Ending one is a move -- settle,
    decline, withdraw -- and those stay, because the history is the point. This
    is for an entry created in error, and for tests, which otherwise leave
    fictions in a live register.

    Owner only, and it takes the moves with it: a move belongs to its entry, and
    half-deleting would leave a log about a conversation nobody can read.
    """
    if identity.via != "bearer":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="a party ends a conversation with a move, not by erasing it",
        )
    entry = await session.get(Entry, entry_id)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
    await session.delete(entry)
