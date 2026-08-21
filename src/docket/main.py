"""The service.

One listener, on loopback. Caddy proxies `docket.civil.payne.io` to it and
terminates TLS; wild-life's API reaches it directly; agents anywhere present a
credential. There is nothing on this machine that gets in a shorter way, which
is a deliberate simplification rather than an omission — see ``auth.py`` for what
used to be here and why it went.
"""

import uvicorn
from fastapi import FastAPI

from docket.config import settings
from docket.routers import board, credentials, enrolments, entries, health

app = FastAPI(
    title="Docket",
    description="A register of what is owed, by whom, to whom, and how it is going.",
    version="0.1.0",
)
app.include_router(health.router)
app.include_router(entries.router)
app.include_router(credentials.router)
app.include_router(enrolments.router)
app.include_router(board.router)


def run() -> None:
    uvicorn.run(app, host=settings.host, port=settings.port)
