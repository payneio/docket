"""A secret that names a party.

How anything becomes a party to the register: a secret that names one. Docket
once identified local callers by unix uid over a socket, which needed accounts
to exist and worked for one machine; this works for anything that can make an
HTTP request, and it is now the only way in.

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
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from docket.db.base import Base
from docket.models.mixins import UUIDPrimaryKey

#: Prefixed so it is recognisable in a log, a config file or a secret scanner as
#: a docket secret rather than an anonymous blob of base64 — and so the two kinds
#: can be told apart on sight, which matters when one of them is long-lived and
#: the other is meant to be spent within the hour.
TOKEN_PREFIX = "dk_"
ENROLMENT_PREFIX = "dke_"


def mint(prefix: str = TOKEN_PREFIX) -> str:
    """A new secret. Shown once, at creation, and never recoverable after."""
    return prefix + secrets.token_urlsafe(32)


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

    #: The ticket this was minted from, when it was not issued directly. A
    #: ticket that turns out to have been intercepted leads straight to
    #: everything minted with it, which is the only question worth asking then.
    enrolment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("enrolments.id", ondelete="SET NULL"), index=True
    )

    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    #: Answers the only question worth asking before revoking something: is
    #: anything still using it. Written at most once a minute per credential, so
    #: a polling agent does not turn every read into a write.
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Revoked, never deleted. A move made in the past should still resolve to
    #: who made it, and `moves.via_credential` points here.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
