"""A secret that names a party.

The counterpart to ``local_identities``, and the reason both exist: that table
maps a *unix account* onto a party, which only works for callers on this
machine and only where something provisions unix accounts. This one maps a
*presented secret* onto a party, which works for anything that can make an HTTP
request.

**It is still not a party registry.** A party is a URI into whatever system
holds people; a credential is one way of proving you are entitled to act as one.
The same party may hold several — an assistant on a laptop and the same
assistant on a phone — and revoking one says nothing about the party, which is
what keeps this a credential table rather than the `parties` table docket
deliberately does not have.

Only the hash is stored, so a credential cannot be recovered, only replaced.
"""

import hashlib
import secrets
from datetime import datetime

from sqlalchemy import DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from docket.db.base import Base
from docket.models.mixins import UUIDPrimaryKey

#: Prefixed so it is recognisable in a log, a config file or a secret scanner as
#: a docket credential rather than an anonymous blob of base64.
TOKEN_PREFIX = "dk_"


def mint() -> str:
    """A new token. Shown once, at creation, and never recoverable after."""
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def digest(token: str) -> str:
    """What gets stored.

    A plain SHA-256, deliberately, and not bcrypt or argon2. Those exist to make
    brute force expensive against secrets a person chose; this is 256 bits from
    the system's random source, and there is nothing to slow down. Iterating a
    hash here would cost every request and buy nothing.
    """
    return hashlib.sha256(token.encode()).hexdigest()


class Credential(UUIDPrimaryKey, Base):
    __tablename__ = "credentials"

    #: The party this speaks for, as a URI. Never resolved, like every other
    #: reference in docket.
    party: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    #: For the board and the audit list. Which laptop, which assistant.
    label: Mapped[str | None] = mapped_column(Text)
    #: Unique, so a lookup is an index probe rather than a scan over every row.
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True, index=True)

    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    #: Answers the only question worth asking before revoking something: is
    #: anything still using it. Written at most once a minute per credential, so
    #: a polling agent does not turn every read into a write.
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Revoked, never deleted — the same argument as `local_identities`. A move
    #: made in the past should still resolve to who made it.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
