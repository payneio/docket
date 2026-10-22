"""Which run a move was part of.

The register keeps the W3C trace id a caller sends, with no tracing SDK, no
collector and no trace store anywhere — that is the property under test, so
nothing here installs one.
"""

import httpx
import pytest
from opentelemetry import context, propagate

from docket import mcp as mcp_module
from docket.config import settings
from docket.db.session import engine
from tests.conftest import AGENT, client_as, issue, subject

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
RUN = {"traceparent": f"00-{TRACE}-00f067aa0ba902b7-01"}
OTHER_RUN = {"traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"}


async def _offer(owner, headers=None, **kw):
    r = await owner.post(
        "/entries", json={"subject": subject(), "delegate": AGENT, **kw}, headers=headers or {}
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_a_move_keeps_the_trace_it_was_made_in(owner, agent):
    e = await _offer(owner, headers=RUN)
    e = (await agent.post(f"/entries/{e['id']}/moves", json={"move": "accept"},
                          headers=RUN)).json()
    assert [m["trace_id"] for m in e["moves"]] == [TRACE, TRACE]


async def test_no_span_id_is_claimed_when_docket_recorded_no_span(owner):
    """Without an SDK there is no span of docket's own; the caller's span id is
    not ours to store, so the column stays empty rather than meaning two things."""
    e = await _offer(owner, headers=RUN)
    assert e["moves"][0]["span_id"] is None


async def test_a_move_with_no_trace_context_has_none(owner):
    e = await _offer(owner)
    assert e["moves"][0]["trace_id"] is None


@pytest.mark.parametrize("bad", [
    "garbage",
    "00-00000000000000000000000000000000-00f067aa0ba902b7-01",  # all-zero trace id
    "00-4bf92f3577b34da6a3ce929d0e0e4736-0000000000000000-01",  # all-zero span id
])
async def test_an_invalid_traceparent_is_ignored_not_refused(owner, bad):
    """W3C says to restart the trace, not to fail the request. A move is never
    refused for what a caller said about its own run."""
    e = await _offer(owner, headers={"traceparent": bad})
    assert e["moves"][0]["trace_id"] is None


async def test_a_question_asked_mid_run_is_in_the_same_run(owner, agent):
    """What the harness does when it is stuck: open an entry in the other
    direction, then mark the first as waiting on it. One pass, two entries."""
    work = await _offer(owner)
    await agent.post(f"/entries/{work['id']}/moves", json={"move": "accept"}, headers=RUN)
    q = await agent.post("/entries", headers=RUN, json={
        "subject": work["subject"], "delegate": settings.owner_party, "terms": "Which one?"})
    await agent.post(f"/entries/{work['id']}/moves", headers=RUN,
                     json={"move": "update", "blocked_on": q.json()["id"]})

    page = (await owner.get(f"/board/entries/{work['id']}")).text
    assert "same run" in page and f"/board/entries/{q.json()['id']}" in page


async def test_a_redirect_child_is_in_the_run_that_handed_it_on(owner, agent):
    e = await _offer(owner)
    await agent.post(f"/entries/{e['id']}/moves", json={"move": "accept"})
    await agent.post(f"/entries/{e['id']}/moves", headers=RUN,
                     json={"move": "redirect", "to": "mailto:sub@example.invalid"})

    child = (await owner.get("/entries", params={"parent_id": e["id"]})).json()[0]
    assert child["moves"][0]["trace_id"] == TRACE


async def test_the_activity_page_shows_a_run_as_one(owner, agent):
    one = await _offer(owner, subject_label="First thing")
    two = await _offer(owner, subject_label="Second thing")
    for e in (one, two):
        await agent.post(f"/entries/{e['id']}/moves", json={"move": "accept"}, headers=RUN)
    await agent.post(f"/entries/{one['id']}/moves", json={"move": "deliver"}, headers=OTHER_RUN)

    page = (await owner.get("/board/activity")).text
    assert page.count("one run") == 1
    assert "one run &middot; 2 moves on 2 entries" in page


async def test_same_run_never_names_an_entry_you_cannot_see(owner, agent):
    """The run is the agent's, and it touched something that is not everyone's
    to know about. The neighbour list is scoped like every other read: a party
    who may read this entry sees only the neighbours it may also read."""
    watcher = "mailto:watcher@example.invalid"
    mine = await _offer(owner, accountable=watcher)
    await agent.post(f"/entries/{mine['id']}/moves", json={"move": "accept"}, headers=RUN)
    # The agent, in the same run, offers something to a third party.
    hidden = await agent.post("/entries", headers=RUN, json={
        "subject": subject(), "delegate": "mailto:third@example.invalid",
        "subject_label": "Not the watchers business"})
    assert hidden.status_code == 201

    # The agent made it, so the agent sees it as a neighbour.
    assert "Not the watchers business" in (await agent.get(f"/board/entries/{mine['id']}")).text
    # The watcher may read this entry (it is accountable) but not the other one.
    async with client_as(await issue(watcher, "watcher")) as w:
        page = (await w.get(f"/board/entries/{mine['id']}")).text
    assert "same run" not in page
    assert "Not the watchers business" not in page


async def test_trace_links_are_for_the_owner_only(owner, agent, monkeypatch):
    """A trace store does not scope reads by party, so a link into one is a way
    round the register's own rule."""
    monkeypatch.setattr(settings, "trace_url", "https://traces.example.invalid/trace/{trace_id}")
    e = await _offer(owner, headers=RUN)
    await agent.post(f"/entries/{e['id']}/moves", json={"move": "accept"}, headers=RUN)
    link = f"https://traces.example.invalid/trace/{TRACE}"
    assert link in (await owner.get(f"/board/entries/{e['id']}")).text
    assert link not in (await agent.get(f"/board/entries/{e['id']}")).text


async def test_a_tool_call_passes_its_run_to_the_register():
    """The MCP hop is a second request into this app. It carries the current
    context, so a move made through a tool lands in the caller's run."""
    token = context.attach(propagate.extract(RUN))
    try:
        request = httpx.Request("POST", "http://docket/entries")
        await mcp_module._forward_auth(request)
    finally:
        context.detach(token)
    assert request.headers["traceparent"].split("-")[1] == TRACE


def test_a_failed_statement_does_not_carry_its_parameters():
    """Bound values are terms and notes. An exception is what lands in a log or
    on a span, so it must name the statement and not repeat what was said."""
    assert engine.sync_engine.hide_parameters is True
