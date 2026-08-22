"""The register, as tools.

Derived from the assembled FastAPI app rather than hand-written, so a tool
cannot describe a route that no longer exists. **The route maps below are an
exclude-list**, which is the whole design decision here: wild-life's scoped
worker surface enumerated what was *allowed*, and rotted twice — silently, in
opposite directions — because retiring an endpoint left a permission and a tool
pointing at nothing. A new route on this service becomes a tool automatically;
``tests/test_mcp.py`` is what tells you it happened.

**No token is baked in.** wild-life's owner-facing ``/mcp`` hands its own token
to the internal call, which is right for a surface only the owner reaches. This
one forwards the caller's, so a party moves as itself and the move is attested
to its own credential. A missing forward fails closed with 401 rather than
escalating to the owner.
"""

import httpx
from fastapi import FastAPI
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.providers.openapi import MCPType, RouteMap

#: Everything that is not a move on the register or a question about it.
#:
#: Credentials and enrolments are the owner's alone: a party that could mint a
#: credential could name itself anything, which is the one thing enrolment is
#: built to prevent. Erasing is owner-only too, and it is not how a conversation
#: ends — that is a move, and moves stay.
EXCLUDE = [
    RouteMap(pattern=r"^/health$", mcp_type=MCPType.EXCLUDE),
    # An open-ended SSE body is not a tool call. A client that wants the feed
    # connects to it directly; see routers/stream.py.
    RouteMap(pattern=r"^/stream$", mcp_type=MCPType.EXCLUDE),
    RouteMap(pattern=r"^/credentials.*", mcp_type=MCPType.EXCLUDE),
    RouteMap(pattern=r"^/enrolments.*", mcp_type=MCPType.EXCLUDE),
    RouteMap(pattern=r"^/enrol$", mcp_type=MCPType.EXCLUDE),
    RouteMap(methods=["DELETE"], pattern=r"^/entries/.*", mcp_type=MCPType.EXCLUDE),
    # Server-rendered HTML. Already `include_in_schema=False`, and named here
    # too so the exclusion is a decision rather than a side effect of it.
    RouteMap(pattern=r"^/board.*", mcp_type=MCPType.EXCLUDE),
]


async def _forward_auth(request: httpx.Request) -> None:
    """Hand the caller's bearer to the internal call.

    FastMCP strips ``authorization`` from forwarded headers by default. Without
    this every tool call would arrive at the register unauthenticated and be
    refused — which is the correct failure, and this is what makes the tools
    usable without weakening it.
    """
    auth = get_http_headers(include={"authorization"}).get("authorization")
    if auth:
        request.headers["authorization"] = auth


def build(app: FastAPI) -> FastMCP:
    return FastMCP.from_fastapi(
        app=app,
        name="docket",
        route_maps=EXCLUDE,
        httpx_client_kwargs={"event_hooks": {"request": [_forward_auth]}},
    )
