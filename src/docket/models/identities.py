"""uid -> party, for callers this machine can vouch for.

Not a party registry. A party is a URI into whatever system holds people; this
maps a *local* unix account onto one, so a caller arriving over the unix socket
is identified by the kernel rather than by a credential it presents.

Written by fleetix when it provisions a principal. Docket works with the table
empty — such a caller simply has no local identity and must use a bearer token.
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from docket.db.base import Base


class LocalIdentity(Base):
    __tablename__ = "local_identities"

    #: The unix uid. Never reused — see fleetix/docs/uid-policy.md. That is what
    #: makes this table safe to keep forever rather than a source of mistaken
    #: identity after an account is deleted.
    uid: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    party: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    label: Mapped[str | None] = mapped_column(Text)
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
