"""The board: the page you open to see what is going on.

It is served by docket itself, so the one thing it can never do is make an
outage look like an empty inbox — the page is either there or it is not. What it
*can* get wrong is showing a conversation to somebody not in it, or coming to
disagree with the API about what a move means, and both are checked here.
"""

from urllib.parse import unquote

from docket.auth import SESSION_COOKIE
from docket.config import settings
from tests.conftest import AGENT, client_as, issue, subject


async def _offer(owner, **kw):
    r = await owner.post("/entries", json={"subject": subject(), "delegate": AGENT, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def _browser():
    """A browser: no header, nothing but cookies."""
    return client_as("")


async def test_a_browser_with_no_token_meets_a_sign_in_page(owner):
    async with _browser() as b:
        r = await b.get("/board")
        assert r.status_code == 401
        assert "sign in" in r.text.lower()
        assert "<form" in r.text


async def test_the_wrong_token_is_refused_and_the_right_one_is_not(owner):
    async with _browser() as b:
        assert (await b.post("/board/sign-in", data={"token": "nope"})).status_code == 401

        r = await b.post("/board/sign-in", data={"token": settings.token})
        assert r.status_code == 303
        assert r.cookies.get(SESSION_COOKIE) == settings.token
        assert "httponly" in r.headers["set-cookie"].lower()
        assert "samesite=strict" in r.headers["set-cookie"].lower()


async def test_the_board_shows_what_is_waiting_on_you(owner, agent):
    """Both piles, and the right entry in each."""
    await _offer(owner, subject_label="Something I asked for")
    mine = await _offer(owner, subject_label="Something I owe")
    await agent.post(f"/entries/{mine['id']}/moves", json={"move": "counter", "terms": "later?"})

    async with _browser() as b:
        await b.post("/board/sign-in", data={"token": settings.token})
        page = (await b.get("/board")).text

    assert "Waiting on me" in page and "I'm waiting on" in page
    # A counter hands the decision back, so that one is ours to answer.
    assert page.index("Waiting on me") < page.index("Something I owe")
    assert page.index("I'm waiting on") < page.index("Something I asked for")
    # And the snapshot caveat is on the page, not only in the docs.
    assert "snapshot" in page


async def test_a_move_from_the_board_is_the_same_move(owner, agent):
    """The board posts a form; the API posts JSON; one of them must not be able
    to do something the other cannot."""
    e = await _offer(owner)
    await agent.post(f"/entries/{e['id']}/moves", json={"move": "accept"})
    await agent.post(f"/entries/{e['id']}/moves", json={"move": "deliver"})

    async with _browser() as b:
        await b.post("/board/sign-in", data={"token": settings.token})
        r = await b.post(f"/board/entries/{e['id']}/moves",
                         data={"move": "settle", "note": "thanks"})
        assert r.status_code == 303  # post-then-redirect: a reload must not re-move

    got = (await owner.get(f"/entries/{e['id']}")).json()
    assert got["state"] == "settled"
    last = got["moves"][-1]
    assert last["move"] == "settle" and last["note"] == "thanks"
    # The owner's own token is configuration, not a row, so it names no credential.
    assert last["via_credential"] is None


async def test_a_refused_move_comes_back_to_the_page_with_the_reason(owner, agent):
    """Usually a race: the other party moved while the page was open. The answer
    is to show what it says now, not an error document."""
    e = await _offer(owner)
    async with _browser() as b:
        await b.post("/board/sign-in", data={"token": settings.token})
        r = await b.post(f"/board/entries/{e['id']}/moves", data={"move": "settle"})
        assert r.status_code == 303
        assert "error=" in r.headers["location"]
        assert "cannot settle from 'offered'" in unquote(r.headers["location"])


async def test_the_log_reads_without_the_transition_table_beside_it(owner, agent):
    e = await _offer(owner)
    await agent.post(f"/entries/{e['id']}/moves", json={"move": "accept", "note": "picked it up"})

    async with _browser() as b:
        await b.post("/board/sign-in", data={"token": settings.token})
        page = (await b.get(f"/board/entries/{e['id']}")).text

    assert "picked it up" in page
    assert "test agent" in page        # the credential's label, not its URI
    assert "Every move here is testimony" in page


async def test_the_board_never_shows_a_conversation_you_are_not_in(owner, agent):
    """The board authenticates as the owner, so this is really a check that it
    goes through the same scoping the API does rather than round it."""
    from tests.conftest import issue

    e = await _offer(owner)
    outsider = await issue("wild-life:person:55555555-5555-5555-5555-555555555555")
    async with client_as(outsider) as stranger:
        assert (await stranger.get(f"/board/entries/{e['id']}")).status_code == 404
        assert e["id"] not in (await stranger.get("/board")).text


async def test_an_ended_conversation_says_the_commitment_is_not(owner, agent):
    e = await _offer(owner)
    await agent.post(f"/entries/{e['id']}/moves", json={"move": "decline"})
    async with _browser() as b:
        await b.post("/board/sign-in", data={"token": settings.token})
        page = (await b.get(f"/board/entries/{e['id']}")).text
    assert "The commitment has not" in page


async def test_the_favicon_is_served_and_linked(owner):
    async with _browser() as b:
        r = await b.get("/favicon.svg")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("image/svg+xml")
        assert r.text.startswith("<svg") and r.text.endswith("</svg>")

        # Asked for whether or not the page said to. Nothing is wrong, so it is
        # not an error: an error in the log should mean something went wrong.
        assert (await b.get("/favicon.ico")).status_code == 204

        # And no page can render without pointing at it, sign-in included.
        assert 'href="/favicon.svg"' in (await b.get("/board")).text


async def test_ended_conversations_are_visible(owner, agent):
    """They vanished entirely: the board asked for open entries only, so anything
    settled or declined left the page and there was nowhere to see last week."""
    done = await _offer(owner, subject_label="Something finished")
    await agent.post(f"/entries/{done['id']}/moves", json={"move": "decline"})
    await _offer(owner, subject_label="Something open")

    async with _browser() as b:
        await b.post("/board/sign-in", data={"token": settings.token})
        page = (await b.get("/board")).text

    assert "Ended" in page
    assert "Something finished" in page and "Something open" in page
    # The distinction that matters is stated where somebody would misread it.
    assert "The commitment did not" in page


async def test_the_activity_stream_is_every_move_newest_first(owner, agent):
    a = await _offer(owner, subject_label="First thing")
    b_ = await _offer(owner, subject_label="Second thing")
    await agent.post(f"/entries/{a['id']}/moves", json={"move": "accept", "note": "took it"})
    await agent.post(f"/entries/{b_['id']}/moves", json={"move": "decline", "note": "not mine"})

    async with _browser() as br:
        await br.post("/board/sign-in", data={"token": settings.token})
        page = (await br.get("/board/activity")).text

    # Across entries, not within one.
    assert "First thing" in page and "Second thing" in page
    assert "took it" in page and "not mine" in page
    # Newest first: the decline came last, so it is above the accept.
    assert page.index("not mine") < page.index("took it")


async def test_activity_shows_only_what_you_are_party_to(owner, agent):
    from tests.conftest import issue

    e = await _offer(owner, subject_label="Private business")
    await agent.post(f"/entries/{e['id']}/moves", json={"move": "accept", "note": "mine to do"})
    outsider = await issue("wild-life:person:77770000-0000-0000-0000-000000000000", "outsider")

    async with client_as(outsider) as stranger:
        page = (await stranger.get("/board/activity")).text
    assert "Private business" not in page and "mine to do" not in page


async def test_every_page_says_who_you_are_signed_in_as(owner):
    """"You" is whoever the token names. A browser holding an agent's credential
    must say so, or its owner reads the agent's moves as their own."""
    token = await issue(AGENT, "cos on civil")
    async with _browser() as b:
        await b.post("/board/sign-in", data={"token": token})
        for path in ("/board", "/board/activity"):
            page = (await b.get(path)).text
            assert "signed in as <b>cos on civil</b>" in page, path

    async with _browser() as b:
        await b.post("/board/sign-in", data={"token": settings.token})
        assert "signed in as <b>the owner</b>" in (await b.get("/board")).text


async def test_signing_out_forgets_the_token(owner):
    async with _browser() as b:
        await b.post("/board/sign-in", data={"token": settings.token})
        r = await b.post("/board/sign-out")
        assert r.status_code == 303
        assert SESSION_COOKIE not in b.cookies
        assert (await b.get("/board")).status_code == 401


async def test_the_menu_shows_access_only_to_the_owner(owner):
    token = await issue(AGENT, "cos on civil")
    async with _browser() as b:
        await b.post("/board/sign-in", data={"token": token})
        page = (await b.get("/board")).text
        assert 'href="/board/activity"' in page and 'href="/board/access"' not in page

    async with _browser() as b:
        await b.post("/board/sign-in", data={"token": settings.token})
        for path in ("/board", "/board/activity", "/board/access"):
            assert 'href="/board/access"' in (await b.get(path)).text, path
