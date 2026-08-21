"""Test rig.

Runs against a real Postgres — the same engine, the same dialect, the same
migrations — but never the real *register*. ``tests/__init__.py`` redirects
``DOCKET_DATABASE_URL`` at ``docket_test`` before anything imports docket, and
``_test_database`` below refuses to start if that redirection did not take.

The suite creates the database, runs the migrations into it, and drops the
schema afterwards. Migrating every session is deliberate: it means every run
also proves the migrations apply to an empty database, which is the one thing
a long-lived development database can never tell you.
"""

import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import psycopg
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from docket.config import DB_SCHEMA, settings
from docket.db.session import AsyncSessionLocal
from docket.main import app
from docket.models.identities import LocalIdentity
from tests import _ROOT, PRODUCTION_DATABASE_URL, TEST_DATABASE_URL

MARK = "test:mark"

PAUL = settings.owner_party
SARA = "mailto:sara@example.invalid"
AGENT = "wild-life:person:11111111-1111-1111-1111-111111111111"
AGENT_UID = 999001  # not a real account; only this suite registers it

#: Tables in dependency order, for the truncate between tests.
_TABLES = ("moves", "entries", "local_identities")


def subject() -> str:
    """A unique subject URI in the test namespace."""
    return f"{MARK}:task:{uuid.uuid4()}"


def _psycopg_dsn(url: str, database: str | None = None) -> str:
    """The same server, as plain libpq. Alembic and the create/drop steps are
    synchronous; only the application is async."""
    dsn = url.replace("+asyncpg", "").replace("+psycopg", "")
    return f"{dsn.rpartition('/')[0]}/{database}" if database else dsn


def _create_database_if_absent() -> None:
    name = TEST_DATABASE_URL.rpartition("/")[2]
    with psycopg.connect(_psycopg_dsn(TEST_DATABASE_URL, "postgres")) as conn:
        conn.autocommit = True
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (name,)
        ).fetchone()
        if not exists:
            conn.execute(f'CREATE DATABASE "{name}"')


def _drop_schema() -> None:
    """Everything the suite made, including alembic's version table.

    Other backends are terminated first. On a shared database that would be
    hostile; on this one the suite is the only legitimate user, and an async
    pool holding idle connections would otherwise block the drop.
    """
    name = TEST_DATABASE_URL.rpartition("/")[2]
    with psycopg.connect(_psycopg_dsn(TEST_DATABASE_URL, "postgres")) as conn:
        conn.autocommit = True
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (name,),
        )
    with psycopg.connect(_psycopg_dsn(TEST_DATABASE_URL)) as conn:
        conn.autocommit = True
        conn.execute(f"DROP SCHEMA IF EXISTS {DB_SCHEMA} CASCADE")


@pytest.fixture(scope="session", autouse=True)
def _test_database() -> Iterator[None]:
    """Create it, migrate it, and refuse to proceed if it is the real one."""
    if settings.database_url != TEST_DATABASE_URL:
        pytest.exit(
            "the suite is not pointed at the test database: "
            f"settings say {settings.database_url!r}",
            returncode=2,
        )
    if TEST_DATABASE_URL == PRODUCTION_DATABASE_URL:
        pytest.exit(
            "DOCKET_TEST_DATABASE_URL is the production database; refusing to run",
            returncode=2,
        )
    _create_database_if_absent()
    cfg = Config(str(_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_ROOT / "migrations"))
    command.upgrade(cfg, "head")
    yield
    _drop_schema()


def client_as(party_uid: int | None = None) -> AsyncClient:
    """A client acting as the owner (bearer), or as a registered uid.

    Passing `client=("uid", n)` puts the same value in the ASGI scope that
    `peercred.PeerCredProtocol` puts there from a real socket, so this exercises
    the identity path rather than stubbing past it. That the kernel actually
    reports the uid is checked separately, in tests/peercred_check.sh.
    """
    kwargs: dict[str, Any] = {}
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
    """Empty the register between tests.

    A truncate rather than the MARK-prefixed delete this used to do: the database
    is the suite's own now, so there is nothing here worth being careful about,
    and a test that forgets to prefix its subject no longer leaks into the next.
    """
    try:
        yield
    finally:
        async with AsyncSessionLocal() as s:
            await s.execute(
                text(f"TRUNCATE {', '.join(f'{DB_SCHEMA}.{t}' for t in _TABLES)}")
            )
            await s.commit()
