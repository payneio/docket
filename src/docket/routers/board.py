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
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from docket import refs, service, states
from docket.auth import SESSION_COOKIE, Identity, identify_token, resolve_identity
from docket.config import TEMPLATES_DIR
from docket.db.session import get_session
from docket.models.credentials import ENROLMENT_PREFIX, Credential, digest, mint
from docket.models.enrolments import Enrolment
from docket.models.entries import Entry
from docket.schemas.entries import EntryRead, MoveCreate

router = APIRouter(tags=["board"])
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

#: Two identical dots, and something owed between them.
#:
#: The dots are the same size and the same colour on purpose. *A person and a
#: machine are the same kind of party here* is the one property this whole
#: project exists to protect, and a mark that drew the asking party larger than
#: the answering one — or an arrow, which would have to point somewhere — would
#: be arguing against the thing it labels. Neither party is the subject of a
#: docket entry; the obligation between them is, so that is what gets the colour.
#:
#: Amber is `--wait` from the board's own palette: the colour it uses for
#: something outstanding. On a dark tile so it reads on a light or a dark tab
#: bar, and at 16px it survives as two pale dots joined by a warm bar.
#:
#: The proportions are set by how it renders at 16px rather than by how it looks
#: large. A thinner bar is better proportioned at 32px and turns to mud at 16,
#: where two device pixels of amber get antialiased against the tile until the
#: colour is gone: 3.2 units holds #d99a2b, 2.2 drops it to a brown.
FAVICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<rect width="32" height="32" rx="7" fill="#1b1b1a"/>'
    '<path d="M14 16h4" stroke="#d99a2b" stroke-width="3.2" stroke-linecap="round"/>'
    '<circle cx="7.5" cy="16" r="5" fill="#faf9f6"/>'
    '<circle cx="24.5" cy="16" r="5" fill="#faf9f6"/>'
    "</svg>"
)


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


def _since(when: datetime | None) -> str:
    return _age((datetime.now(UTC) - when).total_seconds()) if when else "never"


templates.env.filters["age"] = _age
templates.env.filters["since"] = _since


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
    """Whatever we have been told a party is called.

    From the labels on live credentials, and nothing else. A party with no
    credential keeps its URI, which is correct: docket is not a party registry
    and a label is a convenience for reading a page, not a fact about anybody.
    """
    names = _Names()
    rows = (await session.execute(
        select(Credential.party, Credential.label).where(
            Credential.label.isnot(None), Credential.revoked_at.is_(None)
        )
    )).all()
    for party, label in rows:
        names[party] = label
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


@router.get("/favicon.svg", include_in_schema=False)
async def favicon() -> Response:
    """Inline rather than mounted, for the same reason the stylesheet is: one
    file is not worth a static mount, and a mount is one more thing that can be
    misconfigured on the machine where the register lives."""
    return Response(
        FAVICON,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/favicon.ico", include_in_schema=False)
async def favicon_ico() -> Response:
    """Browsers ask for this whether or not they were told to. 204 rather than
    404 because there is nothing wrong: the icon is the SVG the page links, and
    an error in the log should mean something went wrong."""
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/board/sign-in", include_in_schema=False)
async def sign_in(
    request: Request, token: str = Form(""), session: AsyncSession = Depends(get_session)
) -> Response:
    """Any credential, not only the owner's.

    A party with a token of its own signs in and sees its own two piles. That is
    the property this project exists to protect showing up in the furniture: the
    board is not the owner's console that others may borrow, it is the register's
    face, and it shows whoever is looking whatever is theirs.
    """
    if token and await identify_token(session, token, "cookie") is None:
        return _sign_in(request, "That token does not name a party here.")
    if not token:
        return _sign_in(request, "A token is needed.")
    response = RedirectResponse("/board", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        SESSION_COOKIE, token,
        httponly=True,      # no script reads it
        samesite="strict",  # no other site posts a move with it
        max_age=60 * 60 * 24 * 30,
        # Only over HTTPS, and it must be conditional rather than always-on: the
        # register is reached both through Caddy at docket.civil.payne.io and
        # directly on loopback, and a `Secure` cookie on the loopback path would
        # simply never be sent back.
        secure=request.url.scheme == "https",
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
        "ended": [service.read_entry(e, identity.party, blockers)
                  for e in await service.ended(session, identity)],
        "names": await _names(session, identity.party),
        "mine": _by_age(mine), "theirs": _by_age(theirs), "watching": _by_age(watching),
        "owner": identity.is_operator,
        "error": error,
    })


def _by_age(entries: list[EntryRead]) -> list[EntryRead]:
    """Longest stuck first. The thing you have been ignoring the longest is the
    thing the page exists to put in front of you."""
    return sorted(entries, key=lambda e: e.stalled_for_seconds or 0, reverse=True)


@router.get("/board/activity", include_in_schema=False)
async def activity(request: Request, session: AsyncSession = Depends(get_session)) -> Response:
    """What happened, across everything you can see."""
    identity = await _identity_or_none(request, session)
    if identity is None:
        return _sign_in(request)
    return templates.TemplateResponse(request, "activity.html", {
        "moves": await service.recent_moves(session, identity),
        "names": await _names(session, identity.party),
    })


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


# --- access -----------------------------------------------------------------
#
# Who can act as whom, and the two ways of letting somebody new in. Owner only:
# a party may hold a credential and must never be able to see, mint or revoke
# anybody else's.
#
# **No token here can be read back.** Only a hash is stored, which is the point,
# so the page cannot show an existing secret and does not pretend to. What it
# offers instead is *rotate*: mint a replacement for the same party and revoke
# the old one in the same act. That is the honest form of "I have lost it" — and
# unlike handing back the original, it leaves a record that it happened.


async def _access_page(
    request: Request, session: AsyncSession, identity: Identity, minted: dict | None = None,
) -> Response:
    creds = list((await session.execute(
        select(Credential).order_by(Credential.revoked_at.isnot(None), Credential.issued_at)
    )).scalars().all())
    tickets = list((await session.execute(
        select(Enrolment).order_by(Enrolment.issued_at.desc())
    )).scalars().all())
    now = datetime.now(UTC)
    for t in tickets:
        t.live = (  # type: ignore[attr-defined]
            t.revoked_at is None
            and (t.expires_at is None or t.expires_at > now)
            and t.used_count < t.max_uses
        )
    parties = sorted(
        {c.party for c in creds}
        | {t.party for t in tickets}
        | set((await session.execute(select(Entry.requester))).scalars())
        | {d for d in (await session.execute(select(Entry.delegate))).scalars() if d}
    )
    return templates.TemplateResponse(request, "access.html", {
        "credentials": creds, "tickets": tickets, "known_parties": parties,
        "names": await _names(session, identity.party), "minted": minted,
    })


async def _owner(request: Request, session: AsyncSession) -> Identity | Response:
    """The owner, or the page to send back instead."""
    identity = await resolve_identity(request, session)
    if identity is None:
        return _sign_in(request)
    if not identity.is_operator:
        return templates.TemplateResponse(
            request, "base.html",
            {"heading": "access", "error": "Only the register's owner sees this."},
            status_code=status.HTTP_403_FORBIDDEN,
        )
    return identity


@router.get("/board/access", include_in_schema=False)
async def access(request: Request, session: AsyncSession = Depends(get_session)) -> Response:
    who = await _owner(request, session)
    if isinstance(who, Response):
        return who
    return await _access_page(request, session, who)


@router.post("/board/access/credentials", include_in_schema=False)
async def issue_credential(
    request: Request,
    party: str = Form(...),
    label: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> Response:
    who = await _owner(request, session)
    if isinstance(who, Response):
        return who
    if not refs.is_ref(party):
        return await _access_page(request, session, who, None)
    token = mint()
    session.add(Credential(party=party, label=label or None, token_hash=digest(token)))
    await session.flush()
    # Rendered rather than redirected: this is the only moment the secret exists
    # outside the holder, and putting it in a redirect would put it in the URL
    # bar, the history and whatever logs the way.
    return await _access_page(request, session, who, {
        "what": "A credential", "token": token,
        "detail": f"Acts as {party}. It can do nothing but be that party.",
    })


@router.post("/board/access/credentials/{credential_id}/rotate", include_in_schema=False)
async def rotate_credential(
    request: Request, credential_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> Response:
    """Mint a replacement and revoke the old one, in one act.

    What "grab their token" has to mean when only a hash is kept. The holder must
    be given the new one before the old stops working, so this is a thing you do
    knowing there is a gap — which is a better problem than a register that could
    hand an old secret back to anybody who asked.
    """
    who = await _owner(request, session)
    if isinstance(who, Response):
        return who
    old = await session.get(Credential, credential_id)
    if old is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
    token = mint()
    session.add(Credential(party=old.party, label=old.label, token_hash=digest(token)))
    if old.revoked_at is None:
        old.revoked_at = datetime.now(UTC)
    await session.flush()
    return await _access_page(request, session, who, {
        "what": "A replacement credential", "token": token,
        "detail": f"Acts as {old.party}. The one it replaces is revoked as of now, "
                  "so whatever was holding it is locked out until this reaches it.",
    })


@router.post("/board/access/credentials/{credential_id}/revoke", include_in_schema=False)
async def revoke_credential(
    request: Request, credential_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> Response:
    who = await _owner(request, session)
    if isinstance(who, Response):
        return who
    row = await session.get(Credential, credential_id)
    if row is not None and row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
    await session.flush()
    return RedirectResponse("/board/access", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/board/access/tickets", include_in_schema=False)
async def write_ticket(
    request: Request,
    party: str = Form(...),
    label: str = Form(""),
    hours: int = Form(1),
    uses: int = Form(1),
    session: AsyncSession = Depends(get_session),
) -> Response:
    who = await _owner(request, session)
    if isinstance(who, Response):
        return who
    if not refs.is_ref(party):
        return await _access_page(request, session, who, None)
    token = mint(ENROLMENT_PREFIX)
    session.add(Enrolment(
        party=party, label=label or None, token_hash=digest(token),
        expires_at=datetime.now(UTC) + timedelta(hours=max(1, min(hours, 720))),
        max_uses=max(1, min(uses, 100)),
    ))
    await session.flush()
    return await _access_page(request, session, who, {
        "what": "A ticket", "token": token,
        "detail": f"Spendable at POST /enrol for a credential acting as {party}. "
                  "It cannot be presented as an identity itself.",
    })


@router.post("/board/access/tickets/{ticket_id}/revoke", include_in_schema=False)
async def revoke_ticket(
    request: Request, ticket_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> Response:
    who = await _owner(request, session)
    if isinstance(who, Response):
        return who
    row = await session.get(Enrolment, ticket_id)
    if row is not None and row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
    await session.flush()
    # Credentials already minted from it keep working; they are separate things.
    return RedirectResponse("/board/access", status_code=status.HTTP_303_SEE_OTHER)
