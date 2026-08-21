"""Issuing a secret that acts as a party.

The way anything that is not on this machine becomes a party to the register: an
assistant on another computer, a service with no unix account, a person's own
client. It needs neither fleetix nor a login here, which is the point — docket
should not require a box it happens to be running on today.

**Owner only.** A party cannot issue itself a credential, for the same reason a
principal cannot register its own uid: a credential the holder minted proves
nothing about who they are.

**A credential is possession, not presence**, and the register does not pretend
otherwise. What it records is which credential made each move, so revoking one
answers the question that gets asked in anger: and what did it touch.
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from docket import refs
from docket.auth import Identity, current_identity, owner_only
from docket.db.session import get_session
from docket.models.credentials import Credential, digest, mint

router = APIRouter(prefix="/credentials", tags=["credentials"])


class CredentialIn(BaseModel):
    #: The party this will act as. A URI, like every party — it does not have to
    #: exist anywhere docket can reach, and docket will not check.
    party: str
    #: Which assistant, which laptop. Only for reading a list of these later and
    #: knowing which one to revoke.
    label: str | None = None

    @field_validator("party")
    @classmethod
    def _ref(cls, v: str) -> str:
        if not refs.is_ref(v):
            raise ValueError(f"not a reference: {v!r} (expected e.g. wild-life:person:<uuid>)")
        return v


class CredentialOut(BaseModel):
    id: uuid.UUID
    party: str
    label: str | None
    #: **Shown once.** Only the hash is stored, so this cannot be recovered — a
    #: lost credential is replaced, never recalled.
    token: str


@router.post("", status_code=status.HTTP_201_CREATED, response_model=CredentialOut)
async def issue(
    payload: CredentialIn,
    session: AsyncSession = Depends(get_session),
    identity: Identity = Depends(current_identity),
) -> CredentialOut:
    owner_only(identity)
    token = mint()
    row = Credential(party=payload.party, label=payload.label, token_hash=digest(token))
    session.add(row)
    await session.flush()
    return CredentialOut(id=row.id, party=row.party, label=row.label, token=token)


@router.get("")
async def listing(
    session: AsyncSession = Depends(get_session),
    identity: Identity = Depends(current_identity),
    include_revoked: bool = False,
) -> list[dict]:
    """Every credential, without any of the secrets.

    ``last_used_at`` is here because it answers the only question worth asking
    before revoking one: is anything still using it.
    """
    owner_only(identity)
    stmt = select(Credential).order_by(Credential.issued_at)
    if not include_revoked:
        stmt = stmt.where(Credential.revoked_at.is_(None))
    return [
        {
            "id": str(r.id), "party": r.party, "label": r.label,
            "issued_at": r.issued_at, "last_used_at": r.last_used_at,
            "revoked_at": r.revoked_at,
        }
        for r in (await session.execute(stmt)).scalars()
    ]


@router.delete("/{credential_id}")
async def revoke(
    credential_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    identity: Identity = Depends(current_identity),
) -> dict:
    """Revoke, never delete.

    The row is what lets a move made last month still resolve to the credential
    that made it. Deleting it would tidy away the answer to "who did this", which
    is the question the register exists to answer.
    """
    owner_only(identity)
    row = await session.get(Credential, credential_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
    await session.flush()
    return {"id": str(row.id), "party": row.party, "revoked_at": row.revoked_at}
