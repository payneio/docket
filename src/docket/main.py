"""The service.

One listener, on loopback. Caddy proxies `docket.civil.payne.io` to it and
terminates TLS; wild-life's API reaches it directly; agents anywhere present a
credential. There is nothing on this machine that gets in a shorter way, which
is a deliberate simplification rather than an omission — see ``auth.py`` for what
used to be here and why it went.
"""

import uvicorn
from fastapi import FastAPI

from docket import mcp as mcp_module
from docket import tracing
from docket.config import settings
from docket.routers import board, credentials, enrolments, entries, health, stream

app = FastAPI(
    title="Docket",
    description="A register of what is owed, by whom, to whom, and how it is going.",
    version="0.1.0",
)
# Which run a request belongs to, read from the caller's `traceparent` before
# anything else sees the request. See ``docket.tracing``.
app.add_middleware(tracing.TraceContext)
app.include_router(health.router)
app.include_router(entries.router)
app.include_router(credentials.router)
app.include_router(enrolments.router)
app.include_router(stream.router)
app.include_router(board.router)

# --- The register, as tools ------------------------------------------------
# Derived from the app above, so a tool cannot outlive its route. The route maps
# are an exclude-list on purpose; see ``docket.mcp`` for why, and
# ``tests/test_mcp.py`` for what stops a new route becoming a tool unnoticed.
#
# `from_fastapi` needs the finished app, and the MCP session manager needs a
# lifespan to run in — so it is attached after construction, the same way
# wild-life does it.
mcp = mcp_module.build(app)
mcp_app = mcp.http_app(path="/")
app.router.lifespan_context = mcp_app.lifespan
app.mount("/mcp", mcp_app)


def run() -> None:
    uvicorn.run(app, host=settings.host, port=settings.port)
