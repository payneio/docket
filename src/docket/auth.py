"""Who is calling.

Three ways, and their precedence is the point:

1. **Peer credentials.** The caller came over the unix socket and the kernel
   reported its uid (see ``peercred.py``). If that uid is registered, it is
   authoritative and **no presented credential can override it** — evidence beats
   assertion. An unregistered uid falls through, because "I do not know this
   account" is not the same as "this caller is lying".
2. **A bearer token**, for everything off-box and for wild-life's API proxying
   on behalf of the browser.
3. **The same token in a cookie**, for the board. A browser cannot present peer
   credentials and will not attach an Authorization header to a form post, so
   the token has to travel some other way. It is not signed and does not need to
   be: the cookie *is* the credential, carried in a different envelope, and
   signing it would only hide that fact. It ranks with the bearer token because
   it is the bearer token.
"""

import hmac
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from docket.config import settings
from docket.db.session import get_session
from docket.models.identities import LocalIdentity


@dataclass(frozen=True)
class Identity:
    """The party a request acts as, and how we know."""

    party: str
    via: str  # "peercred" | "bearer"
    uid: int | None = None

    @property
    def attested(self) -> bool:
        """True when the kernel vouched for this, rather than the caller."""
        return self.via == "peercred"

    @property
    def is_operator(self) -> bool:
        """Presented the register's own secret rather than being vouched for.

        This is the party that owns the register, and the only one that reads
        past the two parties named on an entry. A principal never is one: it
        arrives over the socket, and what the kernel says about it is exactly
        who it is and no more.
        """
        return self.via != "peercred"


def _peer_uid(request: Request) -> int | None:
    client = request.scope.get("client")
    if isinstance(client, (tuple, list)) and len(client) == 2 and client[0] == "uid":
        return int(client[1])
    return None


#: Where the board keeps the token. HttpOnly and SameSite=Strict, so no script
#: reads it and no other site posts with it.
SESSION_COOKIE = "docket_session"


async def resolve_identity(request: Request, session: AsyncSession) -> Identity | None:
    """Who the caller is, or ``None``. The board needs to be able to ask without
    being refused: an unauthenticated browser should meet a sign-in page, not a
    401 with a JSON body in it."""
    uid = _peer_uid(request)
    if uid is not None:
        row = (
            await session.execute(
                select(LocalIdentity).where(
                    LocalIdentity.uid == uid, LocalIdentity.revoked_at.is_(None)
                )
            )
        ).scalar_one_or_none()
        if row is not None:
            return Identity(party=row.party, via="peercred", uid=uid)

    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() == "bearer" and value and value == settings.token:
        return Identity(party=settings.owner_party, via="bearer")

    if _constant_time_eq(request.cookies.get(SESSION_COOKIE, ""), settings.token):
        return Identity(party=settings.owner_party, via="cookie")

    return None


def _constant_time_eq(given: str, expected: str) -> bool:
    """The cookie arrives on every page load, which is a lot of opportunities to
    time a comparison. The header path predates this and is left alone rather
    than changed in passing."""
    return bool(given) and hmac.compare_digest(given, expected)


async def current_identity(
    request: Request, session: AsyncSession = Depends(get_session)
) -> Identity:
    identity = await resolve_identity(request, session)
    if identity is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="No identity: unregistered uid and no valid bearer token",
        )
    return identity
