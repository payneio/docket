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
uv run pytest tests/ -q              # needs the Wild PC Postgres
./tests/peercred_check.sh            # needs sudo and a fleetix principal
```

`DOCKET_DATABASE_URL`, `DOCKET_TOKEN`, `DOCKET_OWNER_PARTY`, `DOCKET_UDS`.

## Where to start

`docs/plan.md` — the boundary between this, wild-life and fleetix, and the order
of work. `docs/assignment.md` — the specification. `docs/protocol.md` — what a
client sees. `src/docket/states.py` — the conversation, as a table.
