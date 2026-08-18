"""Who is calling.

Two ways, and their precedence is the point:

1. **Peer credentials.** The caller came over the unix socket and the kernel
   reported its uid (see ``peercred.py``). If that uid is registered, it is
   authoritative and **a bearer token cannot override it** — evidence beats
   assertion. An unregistered uid falls through, because "I do not know this
   account" is not the same as "this caller is lying".
2. **A bearer token**, for everything off-box and for wild-life's API proxying
   on behalf of the browser.
"""

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


def _peer_uid(request: Request) -> int | None:
    client = request.scope.get("client")
    if isinstance(client, (tuple, list)) and len(client) == 2 and client[0] == "uid":
        return int(client[1])
    return None


async def current_identity(
    request: Request, session: AsyncSession = Depends(get_session)
) -> Identity:
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

    raise HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        detail="No identity: unregistered uid and no valid bearer token",
    )
