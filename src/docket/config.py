"""Configuration for docket."""

from pydantic_settings import BaseSettings

DB_SCHEMA = "docket"


class Settings(BaseSettings):
    """Service settings loaded from environment variables (prefix ``DOCKET_``)."""

    host: str = "0.0.0.0"
    port: int = 9006
    # When set, listen on this unix socket instead of host:port. A caller on the
    # same machine is then identified by SO_PEERCRED rather than by a token it
    # could have obtained anywhere — see auth.py.
    uds: str = ""

    database_url: str = "postgresql+asyncpg://castle:castle@localhost:5432/castle"

    # The bearer credential for callers that cannot present peer credentials:
    # anything off-box, and wild-life's API proxying for the browser.
    token: str = "dev-token"
    # The party that bearer token acts as. Every party is a URI (see refs.py).
    owner_party: str = "wild-life:person:00000000-0000-0000-0000-000000000000"

    model_config = {"env_prefix": "DOCKET_", "env_file": ".env"}

    @property
    def sync_database_url(self) -> str:
        """Synchronous DSN (psycopg) used by Alembic migrations."""
        return self.database_url.replace("+asyncpg", "+psycopg")


settings = Settings()
