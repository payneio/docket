"""Who is calling.

One question, and one kind of answer: a token, presented in an ``Authorization:
Bearer`` header or carried in a cookie for the board. It is one of two things.

The **owner's** ``DOCKET_TOKEN`` acts as the register's owner and reads past the
parties on an entry. It is the credential that issues every other credential,
which is why it is configuration rather than a row: something has to be able to
mint the first one.

Anything else is looked up in ``credentials`` and acts as **exactly one party and
nothing more** — it cannot issue credentials, cannot erase, and cannot see a
conversation it is not named on.

Being the owner is a *field* on ``Identity``, never inferred from how the caller
arrived. It was once ``via != "peercred"``, which was true of both cases that
existed and would have quietly promoted every credential added afterwards.

**On what was here before.** Docket used to identify local callers by ``uid``
over a unix socket, with the kernel vouching via ``SO_PEERCRED`` and no
credential presented at all. It was the better answer where it applied — nothing
to store, leak or rotate — and it applied to three processes on one machine,
required unix accounts to exist, and decided nothing: no rule ever consulted the
``attested`` flag it produced. Credentials do everything it did and work from
anywhere, so it went. What it was protecting against is real and now costs
something: a leaked credential works from any host that can reach the register,
where a stolen uid worked only while you were on that box being that user.
"""

import hmac
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from docket.config import settings
from docket.db.session import get_session
from docket.models.credentials import Credential, digest

#: Where the board keeps its token. HttpOnly and SameSite=Strict, so no script
#: reads it and no other site posts a move with it.
SESSION_COOKIE = "docket_session"

#: How stale `last_used_at` is allowed to get. An agent polling every minute
#: would otherwise turn every read of the register into a write of this row.
_LAST_USED_RESOLUTION = timedelta(minutes=1)


@dataclass(frozen=True)
class Identity:
    """The party a request acts as, and how we know."""

    party: str
    via: str  # "bearer" | "cookie" -- how it arrived, not who it is
    credential_id: uuid.UUID | None = None
    #: Reads past the two parties named on an entry. The register's owner, and
    #: nothing else. Explicit rather than derived — see the module note.
    is_operator: bool = False


def _constant_time_eq(given: str, expected: str) -> bool:
    return bool(given) and hmac.compare_digest(given, expected)


async def identify_token(session: AsyncSession, presented: str, via: str) -> Identity | None:
    """The owner's token, or a credential's, or nothing."""
    if _constant_time_eq(presented, settings.token):
        return Identity(party=settings.owner_party, via=via, is_operator=True)

    # Looked up by hash: an index probe on a value the caller cannot usefully
    # collide with, rather than a scan comparing every row's secret.
    row = (
        await session.execute(
            select(Credential).where(
                Credential.token_hash == digest(presented),
                Credential.revoked_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None

    now = datetime.now(UTC)
    if row.last_used_at is None or now - row.last_used_at > _LAST_USED_RESOLUTION:
        row.last_used_at = now
    return Identity(party=row.party, via=via, credential_id=row.id)


async def resolve_identity(request: Request, session: AsyncSession) -> Identity | None:
    """Who the caller is, or ``None``.

    The board needs to be able to ask without being refused: an unauthenticated
    browser should meet a sign-in page, not a 401 with a JSON body in it.
    """
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer" and value:
        found = await identify_token(session, value, "bearer")
        if found is not None:
            return found

    cookie = request.cookies.get(SESSION_COOKIE, "")
    if cookie:
        return await identify_token(session, cookie, "cookie")
    return None


async def current_identity(
    request: Request, session: AsyncSession = Depends(get_session)
) -> Identity:
    identity = await resolve_identity(request, session)
    if identity is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail="No identity: no valid token"
        )
    return identity


def owner_only(identity: Identity) -> None:
    """Guard for the things only the register's owner does: issuing credentials
    and enrolment keys, erasing an entry that should never have existed.

    Takes the flag, never the carrier. Checking ``via == "bearer"`` was the same
    thing when the owner's token was the only token there was; it stopped being
    the same thing the moment a credential could arrive in the same header.
    """
    if not identity.is_operator:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="only the register's owner may do this"
        )
