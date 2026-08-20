"""Configuration for docket."""

from pydantic_settings import BaseSettings

DB_SCHEMA = "docket"


class Settings(BaseSettings):
    """Service settings loaded from environment variables (prefix ``DOCKET_``)."""

    # Loopback only. Docket is never reached from off this box directly:
    # wild-life proxies for the browser, and everything local uses the socket.
    host: str = "127.0.0.1"
    port: int = 9006
    # Additionally listen here. A caller arriving on the socket is identified by
    # SO_PEERCRED — by the kernel, rather than by a token it could have obtained
    # anywhere. Both listeners serve the same app; they differ only in what they
    # can know about who is calling.
    uds: str = ""
    #: Group given access to the socket. Fleetix principals are all in `fleet`.
    uds_group: str = "fleet"

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
