"""Point the suite at a database of its own, before docket is imported at all.

This is the earliest hook there is: pytest imports the ``tests`` package before
``tests.conftest`` and before any test module. It has to be this early because
``docket.config.settings`` is instantiated at import time and
``docket.db.session.engine`` is built from it at import time — by the time a
fixture runs, where the suite writes has already been decided.

Until this existed the suite ran against the live castle database and cleaned up
after itself with ``DELETE ... WHERE subject LIKE 'test:mark%'``. That worked,
and it meant one typo in a predicate stood between a test run and the real
register. It also meant there could be no CI: there is only one castle.

``DOCKET_TEST_DATABASE_URL`` overrides the derived name.
"""

import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
#: The database the suite must never touch. Named here so the guard in
#: ``conftest`` can compare against it rather than trusting this module ran.
PRODUCTION_DATABASE_URL: str
TEST_DATABASE_URL: str


def _from_dotenv(key: str) -> str | None:
    """What pydantic-settings would read from ``.env``. We cannot ask it, because
    asking means importing ``docket.config``, which is the thing being redirected."""
    try:
        for line in (_ROOT / ".env").read_text().splitlines():
            name, sep, value = line.partition("=")
            if sep and name.strip() == key:
                return value.strip().strip("'\"")
    except OSError:
        pass
    return None


def _swap_database(url: str, name: str) -> str:
    head, sep, _tail = url.rpartition("/")
    return f"{head}{sep}{name}" if sep else url


# Resolved the same way Settings would: a real environment variable first, then
# `.env`, then the packaged default.
PRODUCTION_DATABASE_URL = (
    os.environ.get("DOCKET_DATABASE_URL")
    or _from_dotenv("DOCKET_DATABASE_URL")
    or "postgresql+asyncpg://castle:castle@localhost:5432/castle"
)
TEST_DATABASE_URL = os.environ.get("DOCKET_TEST_DATABASE_URL") or _swap_database(
    PRODUCTION_DATABASE_URL, "docket_test"
)

os.environ["DOCKET_DATABASE_URL"] = TEST_DATABASE_URL
