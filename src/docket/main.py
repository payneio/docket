"""The service."""

import uvicorn
from fastapi import FastAPI

from docket.config import settings
from docket.peercred import PeerCredProtocol
from docket.routers import entries, health, identities

app = FastAPI(
    title="Docket",
    description="A register of what is owed, by whom, to whom, and how it is going.",
    version="0.1.0",
)
app.include_router(health.router)
app.include_router(entries.router)
app.include_router(identities.router)


def run() -> None:
    if settings.uds:
        # Peer credentials are only available over a unix socket, and they are
        # the reason to prefer one on this machine.
        uvicorn.run(app, uds=settings.uds, http=PeerCredProtocol)
    else:
        uvicorn.run(app, host=settings.host, port=settings.port)
