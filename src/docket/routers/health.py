"""Liveness, and what the caller looks like from here."""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from docket.auth import Identity, current_identity
from docket.db.session import get_session

router = APIRouter(tags=["ops"])


@router.get("/health")
async def health(session: AsyncSession = Depends(get_session)) -> dict:
    """Touches the database, because the question this answers is "can it serve a
    request" and every real request needs a session."""
    await session.execute(text("SELECT 1"))
    return {"status": "ok"}


@router.get("/me", operation_id="docket_me")
async def me(identity: Identity = Depends(current_identity)) -> dict:
    """Who docket thinks you are, and on the strength of which credential."""
    return {
        "party": identity.party,
        "via": identity.via,
        "credential": str(identity.credential_id) if identity.credential_id else None,
        "owner": identity.is_operator,
    }
