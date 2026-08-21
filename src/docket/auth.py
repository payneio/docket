"""Who is calling.

Three ways of arriving, and their precedence is the point:

1. **Peer credentials.** The caller came over the unix socket and the kernel
   reported its uid (see ``peercred.py``). If that uid is registered, it is
   authoritative and **no presented secret can override it** — evidence beats
   assertion. An unregistered uid falls through, because "I do not know this
   account" is not the same as "this caller is lying".
2. **A token in an ``Authorization: Bearer`` header**, for everything that is not
   on this machine, and for wild-life's API proxying on behalf of the browser.
3. **The same kind of token in a cookie**, for the board. A browser cannot
   present peer credentials and will not attach an Authorization header to a
   form post, so the token travels in a cookie instead. It is not signed and does
   not need to be: the cookie *is* the credential in a different envelope, and
   signing it would only obscure that.

A presented token is one of two things. The **owner's** ``DOCKET_TOKEN`` acts as
the register's owner and reads past the parties on an entry; it is the credential
that issues the others, which is why it is configuration and not a row. Anything
else is looked up in ``credentials`` and acts as **exactly one party and nothing
more**.

Being the owner is a *field* on ``Identity`` and not something derived from how
the caller arrived. It used to be `via != "peercred"`, which was true of the only
two cases that existed and would have quietly promoted every credential added
afterwards to reading the whole register.
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
from docket.models.identities import LocalIdentity

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
    via: str  # "peercred" | "bearer" | "cookie" -- how it arrived, not who it is
    uid: int | None = None
    credential_id: uuid.UUID | None = None
    #: Reads past the two parties named on an entry. The register's owner, and
    #: nothing else. Explicit rather than derived — see the module note.
    is_operator: bool = False

    @property
    def attested(self) -> bool:
        """True when the kernel vouched for this, rather than the caller.

        A token is a claim: whoever holds it says they are this party, and they
        are probably right. Only ``SO_PEERCRED`` is evidence, and only on this
        machine. A credential is deliberately *not* attested however carefully it
        was issued, because what it proves is possession of a secret and not
        presence of a party.
        """
        return self.via == "peercred"


def _peer_uid(request: Request) -> int | None:
    client = request.scope.get("client")
    if isinstance(client, (tuple, list)) and len(client) == 2 and client[0] == "uid":
        return int(client[1])
    return None


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
            status.HTTP_401_UNAUTHORIZED,
            detail="No identity: unregistered uid, and no valid token",
        )
    return identity


def owner_only(identity: Identity) -> None:
    """Guard for the things only the register's owner does: issuing credentials,
    registering principals, erasing an entry that should never have existed.

    Takes the flag, never the carrier. Checking ``via == "bearer"`` was the same
    thing when the owner's token was the only token there was; it stopped being
    the same thing the moment a credential could arrive in the same header.
    """
    if not identity.is_operator:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="only the register's owner may do this",
        )
