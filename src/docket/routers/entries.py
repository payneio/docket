"""The register, and the moves that change it.

Nothing but HTTP happens here: what a move *means* is in ``docket.service``, so
that the board and this router cannot come to disagree about it.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from docket import service, states
from docket.auth import Identity, current_identity
from docket.db.session import get_session
from docket.models.entries import Entry
from docket.schemas.entries import EntryRead, MoveCreate, OfferCreate

router = APIRouter(prefix="/entries", tags=["entries"])


def _http(exc: service.Rejected) -> HTTPException:
    return HTTPException(exc.status_code, detail=exc.detail)


@router.post("", response_model=EntryRead, status_code=status.HTTP_201_CREATED)
async def offer(
    payload: OfferCreate,
    session: AsyncSession = Depends(get_session),
    identity: Identity = Depends(current_identity),
) -> EntryRead:
    """Ask someone to discharge something. The offer *is* the entry's creation."""
    try:
        entry = await service.offer(session, payload, identity)
    except service.Rejected as exc:
        raise _http(exc) from None
    return service.read_entry(entry, identity.party)


@router.get("", response_model=list[EntryRead])
async def list_entries(
    session: AsyncSession = Depends(get_session),
    identity: Identity = Depends(current_identity),
    party: str | None = Query(None, description="entries where this URI is requester or delegate"),
    subject: str | None = Query(None),
    parent_id: uuid.UUID | None = Query(None, description="the children of a redirected entry"),
    open_only: bool = Query(True, description="exclude ended conversations"),
    turn: str | None = Query(None, description="'requester' | 'delegate' | 'mine'"),
    stalled_over: float | None = Query(
        None, ge=0, description="seconds since the state last changed — the S21 filter"
    ),
    blocked: bool | None = Query(None, description="waiting on another entry that is still open"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[EntryRead]:
    """Entries you are a party to. The owner of the register sees all of them;
    nobody else sees a conversation they are not named on.

    ``turn`` and ``stalled_over`` are folded from the move log rather than read
    off a column, so they are filtered after the query rather than in it. That
    costs a page of rows and buys the thing this register exists for: neither
    number can be stale, because neither is ever written down.
    """
    stmt = service.scope(select(Entry), identity)
    if party:
        stmt = stmt.where((Entry.requester == party) | (Entry.delegate == party))
    if subject:
        stmt = stmt.where(Entry.subject == subject)
    if parent_id:
        stmt = stmt.where(Entry.parent_id == parent_id)
    if open_only:
        stmt = stmt.where(Entry.state.notin_(tuple(states.TERMINAL)))
    stmt = stmt.order_by(Entry.updated_at.desc()).limit(limit).offset(offset)

    rows = list((await session.execute(stmt)).scalars().all())
    blockers = await service.open_blockers(session, rows)
    read = [service.read_entry(e, identity.party, blockers) for e in rows]

    if turn:
        wanted = turn
        if turn == "mine":
            read = [r for r in read if r.whose_turn is not None and r.whose_turn == r.your_role]
        else:
            read = [r for r in read if r.whose_turn == wanted]
    if stalled_over is not None:
        read = [
            r for r in read
            if r.stalled_for_seconds is not None and r.stalled_for_seconds >= stalled_over
        ]
    if blocked is not None:
        read = [r for r in read if r.is_blocked is blocked]
    return read


@router.get("/{entry_id}", response_model=EntryRead)
async def get_entry(
    entry_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    identity: Identity = Depends(current_identity),
) -> EntryRead:
    entry = await session.get(Entry, entry_id)
    # 404 and not 403: telling a stranger that an entry exists but is not theirs
    # tells them somebody owes somebody something, which is the fact the
    # register holds. There is nothing left to protect after that.
    if entry is None or not service.may_read(entry, identity):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
    return service.read_entry(entry, identity.party, await service.open_blockers(session, [entry]))


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
    # Locked for the length of the transaction. Without it two movers each read
    # the state, each decide their move is legal, and each write `max(seq) + 1`:
    # either they collide on ix_moves_entry_seq -- a 500, where the honest answer
    # is 409 -- or one party's state write disappears under the other's. Three
    # writers make this reachable rather than theoretical: two agents on a timer
    # and a person at the board.
    entry = await session.get(Entry, entry_id, with_for_update=True)
    if entry is None or not service.may_read(entry, identity):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
    try:
        entry = await service.apply_move(session, entry, payload, identity)
    except service.Rejected as exc:
        raise _http(exc) from None
    return service.read_entry(entry, identity.party, await service.open_blockers(session, [entry]))


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
