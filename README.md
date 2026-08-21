# Docket

> A register of what is owed, by whom, to whom, and how it is going.

A docket entry is a **commitment** and the **conversation** about discharging it:
offered, countered, accepted, delivered, settled — or declined, withdrawn,
redirected. Both parties are named on every move, and **no ending of the
conversation ends the commitment**. They said no; you still owe it.

## Why this exists

Every standard for coordinating work covers the *assignment* and none covers the
*commitment*. iCalendar `VTODO` + iTIP (1998), FIPA-Request, OASIS WS-HumanTask,
OSLC CM, and now A2A all say who is holding the work and whether they took it.
None says what is owed, to what end, or what becomes of the obligation when the
holder refuses.

Surveyed from the other direction the same hole appears: *"agent community
governance constitutes a missing architectural layer above current
interoperability standards, not a missing feature within them"*
([Kang & Diponegoro, 2026](https://arxiv.org/abs/2606.31498)).

Docket is that layer, and only that layer.

## The one property worth protecting

**A person and a machine are the same kind of party here.** No live protocol does
this — A2A's human-facing states are `input-required` and `auth-required`, where
the person is an interrupt rather than a party. Anything that makes agents
first-class and people second-class has broken the point of it.

## What it is not

- **Not a task manager.** What is owed lives in the system that authored it;
  docket holds a reference to it and never the thing itself.
- **Not a marketplace or a job board.** Those decide *who gets offered* the work.
  That produces an `offered` entry; everything after is the same conversation
  whether the delegate bid for it, was picked, or is your sister.
- **Not an orchestrator.** It routes nothing and schedules nothing.
- **Not agent infrastructure.** Unix accounts for agents are
  [fleetix](../fleetix).

## Run it

```bash
uv sync
uv run alembic upgrade head          # creates the `docket` schema
uv run docket                        # :9006, or DOCKET_UDS=/run/docket.sock
open http://localhost:9006/          # the board; it asks for DOCKET_TOKEN once
./scripts/check.sh                   # ruff, mypy, pytest — before any commit
./tests/peercred_check.sh            # needs sudo and a fleetix principal
```

`DOCKET_DATABASE_URL`, `DOCKET_TOKEN`, `DOCKET_OWNER_PARTY`, `DOCKET_UDS`.

## How a party gets in

Three ways, and they differ only in what the register can *know* about who is
calling. Nothing here requires [fleetix](../fleetix), and nothing requires the
caller to be on this machine.

| | who vouches | `attested` |
|---|---|---|
| **unix socket** — a local account registered in `local_identities` | the kernel, via `SO_PEERCRED`; no credential is presented at all | `true` |
| **a credential** — `Authorization: Bearer dk_…`, from anywhere | nobody; the holder says so | `false` |
| **`DOCKET_TOKEN`** — the register's owner | nobody, and it is the only identity that reads past the parties on an entry | `false` |

A credential names exactly one party and does nothing else: it cannot issue more,
cannot register principals, cannot erase, and cannot see a conversation it is not
named on. Issue one with:

```bash
curl -X POST https://docket.example/credentials -H "Authorization: Bearer $DOCKET_TOKEN" \
     -H 'Content-Type: application/json' \
     -d '{"party":"wild-life:person:<uuid>","label":"laptop assistant"}'
```

The token comes back **once** — only its hash is stored, so a lost credential is
replaced, never recovered. `GET /credentials` lists them with `last_used_at`,
which answers the one question worth asking before revoking something.

**A credential is possession, not presence**, which is why its moves are never
attested. A token proves somebody holds a secret; only the kernel, on this
machine, proves a party was there. The register keeps testimony and evidence
apart everywhere else and does not stop here.

Tests use a database of their own (`docket_test`, or `DOCKET_TEST_DATABASE_URL`)
and refuse to start against the register, so they are safe to run on a live box
with the service up.

## The board

`http://localhost:9006/` — what is waiting on you, what you are waiting on, and
how long each has *actually* been stuck. Sign in with any token, not only the
owner's: the board is the register's face rather than the owner's console, and it
shows whoever is looking whatever is theirs. Served by docket itself rather than by
wild-life, because the register must not require wild-life to be running and a
surface that did would make that only half true. The cost is that a subject's
label is a snapshot taken when the entry was made; the page says so.

Quiet and stuck are different numbers and the difference is the point.
`quiet_for_seconds` is time since anybody spoke; `stalled_for_seconds` is time
since anything *moved*. A party reporting every minute without progressing keeps
the first near zero forever, which is what happened here on the first day the
register held anything real.

## Where to start

`docs/plan.md` — the boundary between this, wild-life and fleetix, and the order
of work. `docs/assignment.md` — the specification. `docs/protocol.md` — what a
client sees. `src/docket/states.py` — the conversation, as a table.
