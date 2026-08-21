"""Tickets, and spending one for a credential.

The owner issues a ticket naming a party; whoever holds it redeems it for a
credential that acts as that party. What ends up in a provisioning script is a
secret that expires, and the long-lived one is minted at the far end and never
travels.

``POST /enrol`` is the only endpoint in docket that is neither the owner's nor a
party's — it authenticates with a ticket, and it can do exactly one thing.
"""

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from docket import refs
from docket.auth import Identity, current_identity, owner_only
from docket.db.session import get_session
from docket.models.credentials import ENROLMENT_PREFIX, Credential, digest, mint
from docket.models.enrolments import Enrolment

router = APIRouter(tags=["enrolment"])


class EnrolmentIn(BaseModel):
    #: Who whatever redeems this will be. **Set here and not by the enroller**:
    #: a ticket that let its holder name its own party would be the owner's token
    #: with extra steps.
    party: str
    label: str | None = None
    #: Short by default. A ticket that outlives its provisioning run is a
    #: long-lived secret that nobody is thinking of as one.
    expires_in_hours: int | None = Field(default=24, ge=1, le=24 * 30)
    max_uses: int = Field(default=1, ge=1, le=100)

    @field_validator("party")
    @classmethod
    def _ref(cls, v: str) -> str:
        if not refs.is_ref(v):
            raise ValueError(f"not a reference: {v!r} (expected e.g. wild-life:person:<uuid>)")
        return v


class EnrolmentOut(BaseModel):
    id: uuid.UUID
    party: str
    label: str | None
    expires_at: datetime | None
    max_uses: int
    #: **Shown once.**
    token: str


class EnrolledOut(BaseModel):
    """What an agent keeps."""

    credential_id: uuid.UUID
    party: str
    #: **Shown once**, and this is the one to save.
    token: str


@router.post("/enrolments", status_code=status.HTTP_201_CREATED, response_model=EnrolmentOut)
async def create(
    payload: EnrolmentIn,
    session: AsyncSession = Depends(get_session),
    identity: Identity = Depends(current_identity),
) -> EnrolmentOut:
    owner_only(identity)
    token = mint(ENROLMENT_PREFIX)
    row = Enrolment(
        party=payload.party,
        label=payload.label,
        token_hash=digest(token),
        expires_at=(
            datetime.now(UTC) + timedelta(hours=payload.expires_in_hours)
            if payload.expires_in_hours is not None
            else None
        ),
        max_uses=payload.max_uses,
    )
    session.add(row)
    await session.flush()
    return EnrolmentOut(
        id=row.id, party=row.party, label=row.label,
        expires_at=row.expires_at, max_uses=row.max_uses, token=token,
    )


@router.get("/enrolments")
async def listing(
    session: AsyncSession = Depends(get_session),
    identity: Identity = Depends(current_identity),
    include_spent: bool = False,
) -> list[dict]:
    owner_only(identity)
    rows = (await session.execute(
        select(Enrolment).order_by(Enrolment.issued_at)
    )).scalars().all()
    return [
        {
            "id": str(r.id), "party": r.party, "label": r.label,
            "expires_at": r.expires_at, "max_uses": r.max_uses,
            "used_count": r.used_count, "revoked_at": r.revoked_at,
            "live": _why_not(r) is None,
        }
        for r in rows
        if include_spent or _why_not(r) is None
    ]


@router.delete("/enrolments/{enrolment_id}")
async def revoke(
    enrolment_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    identity: Identity = Depends(current_identity),
) -> dict:
    """Revoke the ticket. **Credentials already minted from it keep working** —
    they are separate things, and pretending otherwise would mean a ticket you
    forgot to revoke last month could take an agent offline today. Revoke those
    through `/credentials`; the `enrolment_id` on each says which came from where.
    """
    owner_only(identity)
    row = await session.get(Enrolment, enrolment_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
    await session.flush()
    return {"id": str(row.id), "revoked_at": row.revoked_at}


def _why_not(row: Enrolment) -> str | None:
    """Why this ticket cannot be spent, or ``None`` if it can."""
    if row.revoked_at is not None:
        return "revoked"
    if row.expires_at is not None and row.expires_at <= datetime.now(UTC):
        return "expired"
    if row.used_count >= row.max_uses:
        return "already used"
    return None


class EnrolIn(BaseModel):
    #: What to call this particular credential — which machine, which process.
    #: Falls back to the ticket's own label.
    label: str | None = None


@router.post("/enrol", status_code=status.HTTP_201_CREATED, response_model=EnrolledOut)
async def enrol(
    request: Request,
    payload: EnrolIn,
    session: AsyncSession = Depends(get_session),
) -> EnrolledOut:
    """Spend a ticket for a credential.

    Authenticated by the ticket in an ``Authorization: Bearer`` header, which is
    why this route does not take the usual identity dependency: the caller is not
    anybody yet. That is the point of it.

    Every refusal says the same thing. A caller holding a bad ticket learns
    whether it was wrong, expired, spent or revoked only by being told nothing,
    because the difference is a free oracle for anybody probing.
    """
    scheme, _, presented = request.headers.get("authorization", "").partition(" ")
    refused = HTTPException(
        status.HTTP_401_UNAUTHORIZED, detail="not a ticket that can be spent"
    )
    if scheme.lower() != "bearer" or not presented:
        raise refused

    # Locked, or two agents redeeming a single-use ticket at once both read
    # used_count = 0 and both get a credential.
    row = (await session.execute(
        select(Enrolment).where(Enrolment.token_hash == digest(presented)).with_for_update()
    )).scalar_one_or_none()
    if row is None or _why_not(row) is not None:
        raise refused

    token = mint()
    credential = Credential(
        party=row.party,
        label=payload.label or row.label,
        token_hash=digest(token),
        enrolment_id=row.id,
    )
    session.add(credential)
    row.used_count += 1
    await session.flush()
    return EnrolledOut(credential_id=credential.id, party=credential.party, token=token)
