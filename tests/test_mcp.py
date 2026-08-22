"""What the register exposes as tools, and what it must never.

The route maps in ``docket.mcp`` are an **exclude-list**, which trades one
failure mode for another. An include-list goes stale silently when an endpoint
is retired — wild-life's worker surface rotted that way twice, in opposite
directions, and ended up permitting writes to three endpoints that no longer
existed while not exposing the one write it was built for. An exclude-list
cannot do that. What it can do instead is quietly hand out a *new* route the
moment somebody adds one.

So the snapshot below is the point of this file: adding a route is allowed to
change the tool surface, and is not allowed to change it without somebody
saying so here.
"""

import asyncio
import re

import pytest

from docket import mcp as mcp_module
from docket.main import app, mcp

#: Every tool this register offers. Change deliberately, never to make a test pass.
EXPECTED_TOOLS = {
    "docket_offer",     # POST   /entries
    "docket_entries",   # GET    /entries
    "docket_entry",     # GET    /entries/{id}
    "docket_move",      # POST   /entries/{id}/moves
    "docket_me",        # GET    /me
}


def _routes():
    """Every (method, path) the app actually serves.

    Walks the included routers rather than reading the OpenAPI, because the
    board is ``include_in_schema=False`` and still needs to be checked: an
    exclusion that matches nothing is a claim about this service that stopped
    being true. Each route's ``path`` already carries its router's prefix.
    """
    found = []
    for route in app.routes:
        router = getattr(route, "original_router", None)
        for actual in getattr(router, "routes", None) or ([route] if router is None else []):
            path = getattr(actual, "path", None)
            for method in sorted(getattr(actual, "methods", []) or []):
                if path:
                    found.append((method, path))
    return found


def test_tool_surface_is_exactly_what_we_intend():
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert names == EXPECTED_TOOLS, (
        "the MCP tool surface changed. If a route was added on purpose, add it to "
        "EXPECTED_TOOLS; if it should not be a tool, exclude it in docket.mcp."
    )


@pytest.mark.parametrize("route_map", mcp_module.EXCLUDE, ids=lambda rm: rm.pattern)
def test_every_exclusion_still_matches_a_live_route(route_map):
    """An exclusion for an endpoint that no longer exists is a lie about the
    service, and the thing that let the worker surface rot unnoticed."""
    pattern = re.compile(route_map.pattern)
    # RouteMap spells "any method" as the string "*", not None.
    wanted = None if route_map.methods in (None, "*") else set(route_map.methods)
    assert any(
        pattern.match(path) and (wanted is None or method in wanted)
        for method, path in _routes()
    ), f"nothing matches {route_map.pattern!r} any more — remove it or fix it"


def test_the_owners_powers_are_not_tools():
    """Minting credentials, writing enrolment tickets and erasing entries stay
    the owner's. A party that could mint a credential could name itself
    anything, which is the one thing the enrolment ticket exists to prevent."""
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    forbidden = [n for n in names if any(
        word in n for word in ("credential", "enrol", "erase", "delete", "revoke", "rotate")
    )]
    assert not forbidden, f"owner-only operations exposed as tools: {forbidden}"


def test_no_token_is_baked_into_the_tool_client():
    """wild-life's owner surface hands its own token to the internal call. This
    one must not: a party moves as itself, and a missing forward has to fail
    closed at 401 rather than escalate to the owner."""
    kwargs = mcp_module.build(app)  # rebuilding is cheap and proves the config
    assert kwargs is not None
    import inspect
    source = inspect.getsource(mcp_module)
    assert "settings.token" not in source
    assert "_forward_auth" in source
