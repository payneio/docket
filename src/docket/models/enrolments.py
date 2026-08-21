"""A ticket that can be spent for a credential.

The problem it solves: a credential is long-lived, and getting one onto a machine
means putting it in a provisioning script, a config repository, or a message —
somewhere it is at rest, in the clear, for as long as the agent lives. An
enrolment key is short-lived and usually single-use, so what sits in provisioning
is a secret that expires, and the agent's actual credential is minted at the far
end and never travels anywhere else.

**The party is set by the owner, never by the enroller.** This is the whole of the
security argument. A key that let the holder name its own party would be the
owner's token with extra steps: whoever had it could enrol as Paul. So the owner
says "this ticket is for `wild-life:person:…`" and the agent redeems it for a
credential that acts as that and nothing else. An agent proves it was *sent*,
which is all a machine can prove about itself; who it is remains something
somebody decided.

Deliberately a separate table from ``credentials`` rather than a flag on one.
``identify_token`` only ever looks in ``credentials``, so an enrolment key
structurally cannot be presented as a party — it is not a weaker credential, it
is not a credential.
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from docket.db.base import Base
from docket.models.mixins import UUIDPrimaryKey


class Enrolment(UUIDPrimaryKey, Base):
    __tablename__ = "enrolments"

    #: What credentials minted from this will act as. Chosen by the owner.
    party: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    #: Carried onto the credential when the ticket is spent, so the board can
    #: name the thing that enrolled without anybody typing it twice.
    label: Mapped[str | None] = mapped_column(Text)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True, index=True)

    #: Null means never, which is allowed and is not the default. A ticket that
    #: does not expire is a credential wearing a disguise.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: One, normally. More than one is for a party that legitimately runs in
    #: several places, or for re-enrolling after a rotation.
    max_uses: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    used_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
