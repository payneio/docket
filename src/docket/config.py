"""Configuration for docket."""

from pathlib import Path

from pydantic_settings import BaseSettings

DB_SCHEMA = "docket"
#: The board's templates. Packaged beside the code, not configurable: they are
#: part of the service, the way the transition table is.
TEMPLATES_DIR = Path(__file__).parent / "templates"


class Settings(BaseSettings):
    """Service settings loaded from environment variables (prefix ``DOCKET_``)."""

    # Loopback only, and reached from elsewhere through Caddy, which terminates
    # TLS. Binding this to anything else would put the register on the network
    # without one.
    host: str = "127.0.0.1"
    port: int = 9006

    database_url: str = "postgresql+asyncpg://castle:castle@localhost:5432/castle"

    # The register's owner. The one credential that is configuration rather than
    # a row, because something has to be able to mint the first one.
    token: str = "dev-token"
    # The party that bearer token acts as. Every party is a URI (see refs.py).
    owner_party: str = "wild-life:person:00000000-0000-0000-0000-000000000000"

    #: Where a trace can be read, with ``{trace_id}`` in it -- for example
    #: ``https://jaeger.civil.payne.io/trace/{trace_id}``. Unset, the board links
    #: nowhere. Shown to the owner only: a trace store does not apply this
    #: register's per-party scoping, so a link into one is a way round it.
    trace_url: str | None = None

    model_config = {"env_prefix": "DOCKET_", "env_file": ".env"}

    @property
    def sync_database_url(self) -> str:
        """Synchronous DSN (psycopg) used by Alembic migrations."""
        return self.database_url.replace("+asyncpg", "+psycopg")


settings = Settings()
