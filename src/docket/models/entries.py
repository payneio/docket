"""Entry and Move — a commitment between parties, and the conversation about it.

An **Entry** is the state two parties share. A **Move** is one thing a party did
to it. State is a fold over the moves; it is stored as well, because a reader
asking "where does this stand" should not have to replay a log to find out, and
the two are written in the same transaction.

Every reference out of here is a URI (see ``refs.py``), never a foreign key.
Docket must stay meaningful when the system that authored the subject is
unreachable — that is the difference between a separate service and a schema in
someone else's database.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from docket.db.base import Base
from docket.models.mixins import TimestampMixin, UUIDPrimaryKey


class Entry(UUIDPrimaryKey, TimestampMixin, Base):
    """What one party has been asked to discharge for another."""

    __tablename__ = "entries"

    #: What is owed, as a URI into the system that authored it. Docket never
    #: resolves this and never requires it to resolve.
    subject: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    #: A copy of the subject's title, for docket's own surfaces only. It is a
    #: snapshot, not a fact: rename the task and this is out of date and stays
    #: that way. `label_as_of` is stored so a reader can see how stale, and any
    #: caller that can reach the authoring system should show the live title
    #: instead.
    subject_label: Mapped[str | None] = mapped_column(Text)
    label_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: The three parties, each a URI. A person, an agent and an email address are
    #: the same kind of thing here, which is the property worth protecting.
    requester: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    delegate: Mapped[str | None] = mapped_column(Text, index=True)
    #: RACI's A. Set once and never moved: delegation moves Responsible, never
    #: Accountable, however long a chain of redirects gets.
    accountable: Mapped[str] = mapped_column(Text, nullable=False, index=True)

    state: Mapped[str] = mapped_column(Text, nullable=False, index=True)

    #: What specifically is asked. May narrow what is owed, never widen it.
    terms: Mapped[str | None] = mapped_column(Text)
    #: What the delegate undertook. Deliberately *not* the intention's due date:
    #: what they promised and what you owe are different dates and both matter.
    expected_by: Mapped[date | None] = mapped_column(Date)

    #: The entry this was redirected from, so a chain is walkable (S48).
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entries.id", ondelete="SET NULL"), index=True
    )
    #: The entry this one is waiting on. Distinct from `parent_id`, which means
    #: *redirected from*: this one means *cannot move until that one does*.
    #:
    #: It exists because it was already happening in prose. A delegate that was
    #: stuck opened a second entry asking the question, and recorded the
    #: connection between the two as a sentence in an `update` note -- so
    #: "stalled because it asked you something" and "stalled because nobody is
    #: home" read identically, which is the one distinction S21 is about.
    #:
    #: Nothing clears it. Whether it still *binds* is derived from whether that
    #: entry is still open, for the same reason staleness is derived: a stored
    #: blockage cannot be cleared by the block going away.
    blocked_on: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entries.id", ondelete="SET NULL"), index=True
    )
    #: iTIP UID+SEQUENCE, or an A2A task id — so an exchange with a system we do
    #: not control round-trips without being paraphrased.
    external_ref: Mapped[str | None] = mapped_column(Text, index=True)

    moves: Mapped[list["Move"]] = relationship(
        back_populates="entry", order_by="Move.seq", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (Index("ix_entries_open", "state", "delegate"),)


class Move(Base, UUIDPrimaryKey):
    """One thing a party did. Append-only: nothing here is ever updated."""

    __tablename__ = "moves"

    entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entries.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Monotonic within an entry. Two moves in the same millisecond still have an
    #: order, and it is the order they were accepted in.
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Whose move this is, as a URI. The role check uses this.
    by: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    #: Who wrote it down. Usually the same, and different in the case that makes
    #: this worth separating: a delegate with no account here cannot act, so the
    #: requester records "Sara declined". Collapsing the two would either forbid
    #: that or file Sara's decline as Paul's own, and both are lies.
    recorded_by: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    #: Whether the kernel vouched for the recorder (S47). A move a party made
    #: over its own socket is evidence; one somebody typed on its behalf is
    #: testimony. Both belong in the log; only one of them is corroborated.
    attested: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    move: Mapped[str] = mapped_column(Text, nullable=False)
    #: Recorded on the move so the log reads without the transition table beside
    #: it, and so a later change to that table cannot rewrite what happened.
    from_state: Mapped[str | None] = mapped_column(Text)
    to_state: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)

    entry: Mapped[Entry] = relationship(back_populates="moves")

    __table_args__ = (Index("ix_moves_entry_seq", "entry_id", "seq", unique=True),)
