"""Registering a local account as a party.

Called by ``fleetix-provision`` when it gives an agent a uid. Bearer-only: a
principal must not be able to register itself, or peer credentials would attest
nothing but the caller's own claim.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from docket import refs
from docket.auth import Identity, current_identity, owner_only
from docket.db.session import get_session
from docket.models.identities import LocalIdentity

router = APIRouter(prefix="/identities", tags=["identities"])


class IdentityIn(BaseModel):
    uid: int
    party: str
    label: str | None = None

    @field_validator("party")
    @classmethod
    def _ref(cls, v: str) -> str:
        if not refs.is_ref(v):
            raise ValueError(f"not a reference: {v!r}")
        return v


@router.put("/{uid}", status_code=status.HTTP_200_OK)
async def register(
    uid: int,
    payload: IdentityIn,
    session: AsyncSession = Depends(get_session),
    identity: Identity = Depends(current_identity),
) -> dict:
    owner_only(identity)
    row = await session.get(LocalIdentity, uid)
    if row is None:
        row = LocalIdentity(uid=uid)
        session.add(row)
    row.party, row.label, row.revoked_at = payload.party, payload.label, None
    await session.flush()
    return {"uid": uid, "party": row.party}


@router.delete("/{uid}", status_code=status.HTTP_200_OK)
async def revoke(
    uid: int,
    session: AsyncSession = Depends(get_session),
    identity: Identity = Depends(current_identity),
) -> dict:
    """Revoke, never delete. The uid is not reused, so the row is worth keeping:
    a move made in the past should still resolve to who made it."""
    owner_only(identity)
    row = await session.get(LocalIdentity, uid)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
    row.revoked_at = datetime.now(UTC)
    await session.flush()
    return {"uid": uid, "revoked": True}


@router.get("", status_code=status.HTTP_200_OK)
async def listing(
    session: AsyncSession = Depends(get_session),
    identity: Identity = Depends(current_identity),
) -> list[dict]:
    owner_only(identity)
    rows = (await session.execute(select(LocalIdentity).order_by(LocalIdentity.uid))).scalars()
    return [
        {"uid": r.uid, "party": r.party, "label": r.label, "revoked_at": r.revoked_at}
        for r in rows
    ]
