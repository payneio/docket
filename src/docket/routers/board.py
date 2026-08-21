"""A page you open to see what is going on.

Served by docket itself rather than added to wild-life's SPA, for the reason
docket is a separate service at all: the register must not require wild-life to
be running, and a surface that did would make that claim only half true.

Two costs, both stated on the page rather than hidden:

- **Labels are snapshots.** Docket never resolves a subject, so a renamed task
  still reads by its old name here. A reader who wants the live title has the
  system that authored it.
- **A browser cannot present peer credentials**, so the board asks for the
  token once and keeps it in a cookie. Agents never come this way.

Every move goes through ``docket.service``, the same path the JSON API takes.
The board has no opinion about what a move means, which is the only way to be
sure it does not come to hold a different one.
"""

import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from docket import service, states
from docket.auth import SESSION_COOKIE, Identity, resolve_identity
from docket.config import TEMPLATES_DIR, settings
from docket.db.session import get_session
from docket.models.entries import Entry
from docket.models.identities import LocalIdentity
from docket.schemas.entries import EntryRead, MoveCreate

router = APIRouter(tags=["board"])
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _age(seconds: float | None) -> str:
    """How long, in the largest unit that still says something.

    Rounded down deliberately: "26h" understates nothing a reader would act on,
    and "1.08 days" is a number pretending to a precision the question does not
    have.
    """
    if seconds is None:
        return "—"
    if seconds < 90:
        return f"{int(seconds)}s"
    if seconds < 90 * 60:
        return f"{int(seconds // 60)}m"
    if seconds < 48 * 3600:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


templates.env.filters["age"] = _age


class _Names(dict):
    """Party URIs, as something a person can read.

    A dict that answers for keys it has never seen, because every party is a URI
    into a system docket does not own and most of them will not be in the local
    identity table. Better a readable fragment than the whole `wild-life:person:`
    incantation — and never an empty string, which would silently drop a party
    off a page whose whole job is naming both of them.
    """

    def __missing__(self, key: object) -> str:
        if key is None:
            return "nobody"
        ref = str(key)
        scheme, _, rest = ref.partition(":")
        if scheme == "mailto":
            return rest
        tail = rest.rpartition(":")[2]
        return f"{scheme}:…{tail[-6:]}" if tail else ref


async def _names(session: AsyncSession, me: str) -> _Names:
    names = _Names()
    rows = (await session.execute(
        select(LocalIdentity).where(LocalIdentity.label.isnot(None))
    )).scalars()
    for r in rows:
        names[r.party] = r.label or ""
    names[me] = "you"
    return names


async def _identity_or_none(request: Request, session: AsyncSession) -> Identity | None:
    return await resolve_identity(request, session)


def _sign_in(request: Request, error: str | None = None) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "sign_in.html", {"error": error}, status_code=status.HTTP_401_UNAUTHORIZED
    )


@router.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse("/board", status_code=status.HTTP_302_FOUND)


@router.post("/board/sign-in", include_in_schema=False)
async def sign_in(request: Request, token: str = Form("")) -> Response:
    if token != settings.token:
        return _sign_in(request, "That is not the register's token.")
    response = RedirectResponse("/board", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        SESSION_COOKIE, token,
        httponly=True,      # no script reads it
        samesite="strict",  # no other site posts a move with it
        max_age=60 * 60 * 24 * 30,
        # Not `secure`: this is loopback, and setting it would mean the cookie is
        # never sent at all. If the board is ever put behind TLS this must change.
    )
    return response


@router.get("/board", include_in_schema=False)
async def board(
    request: Request,
    session: AsyncSession = Depends(get_session),
    error: str | None = None,
) -> Response:
    identity = await _identity_or_none(request, session)
    if identity is None:
        return _sign_in(request)

    rows = list((await session.execute(
        service.scope(select(Entry), identity)
        .where(Entry.state.notin_(tuple(states.TERMINAL)))
        .order_by(Entry.updated_at.desc())
        .limit(200)
    )).scalars().all())
    blockers = await service.open_blockers(session, rows)
    read = [service.read_entry(e, identity.party, blockers) for e in rows]

    # Three piles, and the third is the reason it is three: an entry you are
    # accountable for but not in has no move you can make and still needs
    # somewhere to appear, or accountability means watching nothing.
    mine = [e for e in read if e.whose_turn is not None and e.whose_turn == e.your_role]
    theirs = [e for e in read if e.your_role is not None and e not in mine]
    watching = [e for e in read if e.your_role is None]

    return templates.TemplateResponse(request, "board.html", {
        "me": identity.party,
        "names": await _names(session, identity.party),
        "mine": _by_age(mine), "theirs": _by_age(theirs), "watching": _by_age(watching),
        "error": error,
    })


def _by_age(entries: list[EntryRead]) -> list[EntryRead]:
    """Longest stuck first. The thing you have been ignoring the longest is the
    thing the page exists to put in front of you."""
    return sorted(entries, key=lambda e: e.stalled_for_seconds or 0, reverse=True)


@router.get("/board/entries/{entry_id}", include_in_schema=False)
async def entry_page(
    request: Request,
    entry_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    error: str | None = None,
) -> Response:
    identity = await _identity_or_none(request, session)
    if identity is None:
        return _sign_in(request)
    entry = await session.get(Entry, entry_id)
    if entry is None or not service.may_read(entry, identity):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
    read = service.read_entry(
        entry, identity.party, await service.open_blockers(session, [entry])
    )
    return templates.TemplateResponse(request, "entry.html", {
        "entry": read,
        "names": await _names(session, identity.party),
        "error": error,
    })


@router.post("/board/entries/{entry_id}/moves", include_in_schema=False)
async def board_move(
    request: Request,
    entry_id: uuid.UUID,
    move: str = Form(...),
    note: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """One move, from a form. Straight to the service — the board decides nothing
    about what is allowed, it only asks."""
    identity = await _identity_or_none(request, session)
    if identity is None:
        return _sign_in(request)
    entry = await session.get(Entry, entry_id, with_for_update=True)
    if entry is None or not service.may_read(entry, identity):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
    try:
        await service.apply_move(
            session, entry, MoveCreate(move=move, note=note or None), identity
        )
    except service.Rejected as exc:
        # Back to the page with the reason on it. A refusal is usually a race —
        # the other party moved while this page was open — and the answer to that
        # is to show what it says now, not an error document.
        return RedirectResponse(
            f"/board/entries/{entry_id}?error={exc.detail}", status_code=status.HTTP_303_SEE_OTHER
        )
    # POST-then-redirect, so a reload does not make the move twice.
    return RedirectResponse(f"/board/entries/{entry_id}", status_code=status.HTTP_303_SEE_OTHER)
