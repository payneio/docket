"""The access page: who can act as whom, and letting somebody else in.

The thing it must never do is hand back a secret. Only a hash is stored, so it
cannot, and the test worth having is that it does not pretend to — the honest
form of "I have lost it" is a rotation, which mints a replacement and revokes
the old one in the same act.
"""

from contextlib import asynccontextmanager

from docket.config import settings
from tests.conftest import AGENT, client_as, issue, subject

SARA = "wild-life:person:66666666-6666-6666-6666-666666666666"


@asynccontextmanager
async def _signed_in(token):
    """A browser holding nothing but the cookie the sign-in gives it."""
    async with client_as("") as b:
        await b.post("/board/sign-in", data={"token": token})
        yield b


async def test_the_page_lists_who_holds_what(owner, agent):
    async with _signed_in(settings.token) as b:
        page = (await b.get("/board/access")).text
    assert "test agent" in page          # the agent fixture's credential
    assert "Credentials" in page and "Tickets" in page


async def test_only_the_owner_sees_it(owner):
    holder = await issue(SARA, "sara laptop")
    async with _signed_in(holder) as b:
        r = await b.get("/board/access")
        assert r.status_code == 403
        assert "owner" in r.text
    # And none of the doors open either.
    async with client_as(holder) as sara:
        assert (await sara.post("/board/access/credentials",
                                data={"party": SARA})).status_code == 403
        assert (await sara.post("/board/access/tickets",
                                data={"party": SARA})).status_code == 403


async def test_a_new_credential_is_shown_once_and_works(owner):
    async with _signed_in(settings.token) as b:
        page = (await b.post("/board/access/credentials",
                             data={"party": SARA, "label": "sara laptop"})).text
        assert "copy it now" in page
        token = page.split('class="secret">')[1].split("<")[0]
        assert token.startswith("dk_")
        # Revisiting shows the list and not the secret again.
        assert "copy it now" not in (await b.get("/board/access")).text

    async with client_as(token) as sara:
        assert (await sara.get("/me")).json()["party"] == SARA


async def test_a_ticket_is_shown_once_and_can_be_spent(owner):
    async with _signed_in(settings.token) as b:
        page = (await b.post("/board/access/tickets",
                             data={"party": SARA, "label": "sara", "hours": 2, "uses": 1})).text
        ticket = page.split('class="secret">')[1].split("<")[0]
    assert ticket.startswith("dke_")

    async with client_as(ticket) as newcomer:
        got = (await newcomer.post("/enrol", json={"label": "enrolled from the board"})).json()
    assert got["party"] == SARA


async def test_rotating_replaces_the_secret_and_locks_out_the_old_one(owner):
    """What "grab their token" has to mean when only a hash is kept."""
    old = await issue(SARA, "sara laptop")
    listed = (await owner.get("/credentials")).json()
    cid = next(c["id"] for c in listed if c["label"] == "sara laptop")

    async with _signed_in(settings.token) as b:
        page = (await b.post(f"/board/access/credentials/{cid}/rotate")).text
        new = page.split('class="secret">')[1].split("<")[0]

    assert new != old
    async with client_as(old) as stale:
        assert (await stale.get("/me")).status_code == 401
    async with client_as(new) as fresh:
        me = (await fresh.get("/me")).json()
        assert me["party"] == SARA
    # The label carries over, so the list still says which machine it is.
    live = (await owner.get("/credentials")).json()
    assert [c["label"] for c in live] == ["sara laptop"]


async def test_revoking_from_the_page(owner):
    token = await issue(SARA, "doomed")
    cid = next(c["id"] for c in (await owner.get("/credentials")).json()
               if c["label"] == "doomed")
    async with _signed_in(settings.token) as b:
        assert (await b.post(f"/board/access/credentials/{cid}/revoke")).status_code == 303
    async with client_as(token) as gone:
        assert (await gone.get("/me")).status_code == 401
    # Struck through, not vanished: the row is what a past move resolves against.
    async with _signed_in(settings.token) as b:
        assert "doomed" in (await b.get("/board/access")).text


async def test_the_board_only_offers_the_door_to_the_owner(owner, agent):
    await owner.post("/entries", json={"subject": subject(), "delegate": AGENT})
    async with _signed_in(settings.token) as b:
        assert "/board/access" in (await b.get("/board")).text

    holder = await issue(SARA, "sara")
    async with _signed_in(holder) as b:
        assert "/board/access" not in (await b.get("/board")).text
