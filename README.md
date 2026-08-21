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
uv run docket                        # :9006, loopback; Caddy fronts it
open http://localhost:9006/          # the board; it asks for a token once
./scripts/check.sh                   # ruff, mypy, pytest — before any commit
```

`DOCKET_DATABASE_URL`, `DOCKET_TOKEN`, `DOCKET_OWNER_PARTY`.

## How a party gets in

One way: a token. Nothing requires [fleetix](../fleetix), a unix account, or the
caller being on this machine.

| | what it is | what it can do |
|---|---|---|
| **`DOCKET_TOKEN`** | the register's owner, in configuration rather than a row, because something has to mint the first credential | everything, including reading past the parties on an entry |
| **a credential** — `dk_…` | a secret that names one party | act as that party, and see only what names it |
| **a ticket** — `dke_…` | short-lived, usually single-use, spendable for a credential | exactly one thing: `POST /enrol` |

```bash
# The owner writes a ticket saying who the newcomer will be.
curl -X POST $D/enrolments -H "Authorization: Bearer $DOCKET_TOKEN" \
     -d '{"party":"wild-life:person:<uuid>","label":"coder","expires_in_hours":1}'

# The newcomer spends it, and keeps what comes back.
curl -X POST $D/enrol -H "Authorization: Bearer dke_…" -d '{"label":"coder on wildpc"}'
# → {"credential_id":"…","party":"wild-life:person:<uuid>","token":"dk_…"}
```

**The party is on the ticket, never chosen by the enroller.** A ticket whose
holder could name its own party would be the owner's token with extra steps. An
agent proves it was *sent*, which is all a machine can prove about itself; who it
is stays something somebody decided.

Secrets are shown once and stored as a SHA-256 — not argon2, because 256 bits
from the system's random source has nothing to slow down. Revoked, never deleted:
`moves.via_credential` points back, so revoking one answers what it touched.

Docket used to identify local callers by unix uid over a socket, with the kernel
vouching and no credential presented at all. That was the better answer where it
applied, and it applied to three processes on one machine, needed accounts to
exist, and decided nothing — no rule ever read the `attested` flag it set. It is
gone, and what it protected against is real and now costs something: **a leaked
credential works from anywhere that can reach the register**, where a stolen uid
worked only while you were on that box being that user.

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
