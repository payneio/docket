"""A secret that names a party, and the line it must not cross.

The register is reached over the network as well as over the socket — Caddy
proxies `docket.civil.payne.io` to it — and until now everything arriving that
way authenticated with one shared secret that acted as the owner. A second
assistant could not be a second party; it could only be the owner again.

The thing most worth testing here is the boundary: a credential acts as exactly
one party and never reads past the entries that name it.
"""

from docket.config import settings
from docket.models.credentials import Credential, digest
from tests.conftest import AGENT, client_as, subject

SARA = "wild-life:person:66666666-6666-6666-6666-666666666666"


async def _issue(owner, party=SARA, label="sara laptop"):
    r = await owner.post("/credentials", json={"party": party, "label": label})
    assert r.status_code == 201, r.text
    return r.json()


def _holder(token: str):
    """A caller with nothing but a token: the whole point."""
    return client_as(token)


async def test_a_credential_acts_as_its_party_and_not_as_the_owner(owner):
    made = await _issue(owner)
    async with _holder(made["token"]) as sara:
        me = (await sara.get("/me")).json()
    assert me["party"] == SARA
    assert me["via"] == "bearer"
    assert me["owner"] is False
    # And the log can point back at exactly this credential.
    assert me["credential"] == made["id"]


async def test_a_credential_is_not_the_owner(owner):
    """The regression this whole change turns on.

    `is_operator` was once `via != "peercred"`, which was true of both cases that
    existed. A credential arrives in the same header the owner's token does, so
    it would have inherited reading the whole register, issuing more credentials,
    and erasing entries.
    """
    made = await _issue(owner)
    private = await owner.post(
        "/entries", json={"subject": subject(), "delegate": AGENT, "terms": "not sara's"}
    )
    private = private.json()

    async with _holder(made["token"]) as sara:
        # Reads nothing it is not a party to.
        assert (await sara.get("/entries")).json() == []
        assert (await sara.get(f"/entries/{private['id']}")).status_code == 404
        # Issues nothing.
        assert (await sara.post("/credentials", json={"party": SARA})).status_code == 403
        assert (await sara.get("/credentials")).status_code == 403
        # Erases nothing.
        assert (await sara.delete(f"/entries/{private['id']}")).status_code == 403


async def test_a_credential_holder_is_a_full_party_to_its_own_entries(owner):
    """Not a lesser kind of party — the same loop, all the moves."""
    made = await _issue(owner)
    e = (await owner.post(
        "/entries", json={"subject": subject(), "delegate": SARA, "terms": "Review the draft"}
    )).json()

    async with _holder(made["token"]) as sara:
        assert [x["id"] for x in (await sara.get("/entries")).json()] == [e["id"]]
        got = (await sara.get(f"/entries/{e['id']}")).json()
        assert got["your_role"] == "delegate" and "accept" in got["available_moves"]

        e = (await sara.post(f"/entries/{e['id']}/moves", json={"move": "accept"})).json()
        assert e["state"] == "accepted"
        e = (await sara.post(f"/entries/{e['id']}/moves", json={"move": "deliver"})).json()

    e = (await owner.post(f"/entries/{e['id']}/moves", json={"move": "settle"})).json()
    assert e["state"] == "settled"
    accept = next(m for m in e["moves"] if m["move"] == "accept")
    assert accept["by"] == accept["recorded_by"] == SARA
    assert accept["via_credential"] == made["id"]


async def test_the_token_is_shown_once_and_only_the_hash_is_kept(owner):
    from tests.conftest import AsyncSessionLocal

    made = await _issue(owner)
    async with AsyncSessionLocal() as s:
        row = await s.get(Credential, __import__("uuid").UUID(made["id"]))
        assert row is not None
        assert row.token_hash == digest(made["token"])
        assert made["token"] not in row.token_hash

    listed = (await owner.get("/credentials")).json()
    assert [c["id"] for c in listed] == [made["id"]]
    assert all("token" not in c and "token_hash" not in c for c in listed)


async def test_revoking_stops_the_token_and_keeps_the_record(owner):
    made = await _issue(owner)
    async with _holder(made["token"]) as sara:
        assert (await sara.get("/me")).status_code == 200
        assert (await owner.delete(f"/credentials/{made['id']}")).status_code == 200
        assert (await sara.get("/me")).status_code == 401

    # The row survives, so a move made last month still resolves to who made it.
    still = (await owner.get("/credentials", params={"include_revoked": True})).json()
    assert [c["id"] for c in still] == [made["id"]]
    assert still[0]["revoked_at"] is not None


async def test_last_used_answers_the_question_you_ask_before_revoking(owner):
    made = await _issue(owner)
    assert (await owner.get("/credentials")).json()[0]["last_used_at"] is None
    async with _holder(made["token"]) as sara:
        await sara.get("/me")
    assert (await owner.get("/credentials")).json()[0]["last_used_at"] is not None


async def test_a_made_up_token_is_nobody(owner):
    async with _holder("dk_not-a-real-token") as nobody:
        assert (await nobody.get("/me")).status_code == 401
    async with _holder("") as blank:
        assert (await blank.get("/me")).status_code == 401


async def test_a_credential_needs_a_party_that_looks_like_one(owner):
    r = await owner.post("/credentials", json={"party": "sara"})
    assert r.status_code == 422


async def test_two_credentials_may_name_the_same_party(owner):
    """A laptop and a phone are the same assistant. Revoking one says nothing
    about the party, which is what keeps this a credential table."""
    a = await _issue(owner, label="laptop")
    b = await _issue(owner, label="phone")
    assert a["token"] != b["token"]
    await owner.delete(f"/credentials/{a['id']}")
    async with _holder(b["token"]) as phone:
        assert (await phone.get("/me")).json()["party"] == SARA


async def test_the_board_signs_in_any_party_not_only_the_owner(owner):
    """The board is the register's face, not the owner's console."""
    made = await _issue(owner)
    e = (await owner.post("/entries", json={
        "subject": subject(), "delegate": SARA, "terms": "Reviewing the draft for Sara",
    })).json()

    async with client_as("") as b:
        assert (await b.post("/board/sign-in", data={"token": made["token"]})).status_code == 303
        page = (await b.get("/board")).text

    assert "Reviewing the draft for Sara" in page
    assert e["id"] in page
    # Rendered as Sara's own board, not as the owner's with Sara's rows on it.
    assert "&nbsp; you" in page  # the heading renders the viewer, and it is her

    # And from the other side, the credential's label is how Sara reads. That is
    # the whole of what a label is for -- not a party registry, just a way to
    # write a URI down so a person can see who it is.
    assert "sara laptop" in (await owner.get("/board")).text


async def test_the_board_refuses_a_token_that_names_nobody(owner):
    async with client_as("") as b:
        r = await b.post("/board/sign-in", data={"token": "dk_nonsense"})
        assert r.status_code == 401 and "does not name a party" in r.text


async def test_the_owners_token_still_works_everywhere(owner):
    """It is the credential that issues the others, so it cannot be one of them."""
    me = (await owner.get("/me")).json()
    assert me["party"] == settings.owner_party and me["via"] == "bearer"
    assert (await owner.get("/credentials")).status_code == 200
