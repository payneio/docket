"""Test rig.

Writes to the real Wild PC Postgres, like wild-life's suite. Everything created
here carries the MARK prefix in its subject and is deleted afterwards, so a
failed run leaves the register clean rather than seeding it with fictions.
"""

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from docket.config import settings
from docket.db.session import AsyncSessionLocal
from docket.main import app
from docket.models.identities import LocalIdentity

MARK = "test:mark"

PAUL = settings.owner_party
SARA = "mailto:sara@example.invalid"
AGENT = "wild-life:person:11111111-1111-1111-1111-111111111111"
AGENT_UID = 999001  # not a real account; only this suite registers it


def subject() -> str:
    """A unique subject URI in the test namespace."""
    return f"{MARK}:task:{uuid.uuid4()}"


def client_as(party_uid: int | None = None) -> AsyncClient:
    """A client acting as the owner (bearer), or as a registered uid.

    Passing `client=("uid", n)` puts the same value in the ASGI scope that
    `peercred.PeerCredProtocol` puts there from a real socket, so this exercises
    the identity path rather than stubbing past it. That the kernel actually
    reports the uid is checked separately, in tests/peercred_check.sh.
    """
    kwargs = {}
    if party_uid is not None:
        kwargs["client"] = ("uid", party_uid)
    return AsyncClient(
        transport=ASGITransport(app=app, **kwargs),
        base_url="http://docket",
        headers={} if party_uid is not None else {"Authorization": f"Bearer {settings.token}"},
    )


@pytest_asyncio.fixture
async def owner(_cleanup) -> AsyncIterator[AsyncClient]:
    async with client_as() as c:
        yield c


@pytest_asyncio.fixture
async def agent(_cleanup) -> AsyncIterator[AsyncClient]:
    """A second party, identified the way a fleetix principal would be."""
    async with AsyncSessionLocal() as s:
        row = await s.get(LocalIdentity, AGENT_UID)
        if row is None:
            s.add(LocalIdentity(uid=AGENT_UID, party=AGENT, label="test agent"))
        else:
            row.party, row.revoked_at = AGENT, None
        await s.commit()
    async with client_as(AGENT_UID) as c:
        yield c


@pytest_asyncio.fixture
async def _cleanup() -> AsyncIterator[None]:
    try:
        yield
    finally:
        async with AsyncSessionLocal() as s:
            await s.execute(text("DELETE FROM docket.entries WHERE subject LIKE :p"), {"p": f"{MARK}%"})
            await s.execute(text("DELETE FROM docket.local_identities WHERE uid = :u"), {"u": AGENT_UID})
            await s.commit()
