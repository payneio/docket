"""Wire shapes."""

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, field_validator

from docket import refs
from docket.states import MOVES


def _ref(value: str | None) -> str | None:
    if value is None:
        return None
    if not refs.is_ref(value):
        raise ValueError(f"not a reference: {value!r} (expected e.g. wild-life:task:<uuid>)")
    return value


class OfferCreate(BaseModel):
    """An entry is created by making its first move, which is always an offer.

    There is no draft state. A commitment nobody has been asked to take on is an
    intention, and intentions live in the system that authored them.
    """

    subject: str
    subject_label: str | None = None
    delegate: str
    #: Defaults to the requester. Whoever it is, it never moves again.
    accountable: str | None = None
    terms: str | None = None
    expected_by: date | None = None
    external_ref: str | None = None
    note: str | None = None

    _v = field_validator("subject", "delegate", "accountable")(classmethod(lambda cls, v: _ref(v)))


class MoveCreate(BaseModel):
    move: str
    note: str | None = None
    #: Only for `redirect`: who it is being handed to.
    to: str | None = None
    #: Only for `counter` and a re-`offer`: the revised terms.
    terms: str | None = None
    expected_by: date | None = None

    @field_validator("move")
    @classmethod
    def _known(cls, v: str) -> str:
        if v not in MOVES:
            raise ValueError(f"unknown move {v!r}; expected one of {', '.join(MOVES)}")
        return v

    _v = field_validator("to")(classmethod(lambda cls, v: _ref(v)))


class MoveRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    seq: int
    at: datetime
    by: str
    move: str
    from_state: str | None
    to_state: str
    note: str | None


class EntryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    subject: str
    subject_label: str | None
    label_as_of: datetime | None
    requester: str
    delegate: str | None
    accountable: str
    state: str
    terms: str | None
    expected_by: date | None
    parent_id: uuid.UUID | None
    external_ref: str | None
    created_at: datetime
    updated_at: datetime
    moves: list[MoveRead] = []

    # --- derived, never stored -------------------------------------------
    #: The conversation has ended. **The commitment has not** — docket cannot
    #: end one, which is why this is named for the conversation.
    is_open: bool
    #: Seconds since the last move. Staleness is read, never written: a stored
    #: silence cannot be cleared by anything happening.
    quiet_for_seconds: float | None
    #: What the caller could do next. Derived from the same table the server
    #: enforces, so a UI cannot come to disagree with it.
    available_moves: list[str] = []
    your_role: str | None = None
