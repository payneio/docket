# Docket — revised plan

## Context

We are splitting coordination out of wild-life into a third project, `docket`,
alongside `fleetix`. The first version of this plan (committed as `03c5aea`) was
written on a wrong premise and carried a sequencing bug that would have broken
all three agents. This replaces it; the review that found both is below.

**What the review found.** The assignment loop is not unbuilt. wild-life already
has `POST /tasks/{id}/assignment` (`api/src/wild_life/routers/tasks.py:488`) with
`offered | accepted | declined | withdrawn`, which moves `responsible_id` and
never accountable, writes each move as an `exchange` moment
(`schemas/common.py:125`) linked subject→task and participant→person, and is
wired to `web/src/components/AssignmentBar.tsx`. The first plan proposed
rebuilding this in a new service with a different history record, which would
have made a fifth partial copy of the loop — the exact failure the exercise
exists to end.

**The framing that resolves it** (Paul, this session): everything in wild-life is
*his* data — one life stream. If he delegates, receives, or approves, the moment
is his record of what he did. Docket holds the shared machinery.

That is a shape the model already has. `api/docs/domain.md` → *Core and external*
says core is "a moment, and who was in it" and external is "how it was shared:
the iCalendar record, invites sent, RSVPs received." **Docket is to a commitment
what the iCalendar record is to a meeting.** The two records are not duplicates
and neither is derived from the other:

| | holds | lives |
|---|---|---|
| **moment** | *I offered this to Sara. I received her decline.* | wild-life, core, authored, survives docket's deletion |
| **docket entry** | the state two parties share and both can read | docket, external, reachable over iTIP/A2A |

**Decided this session:** a moment is written **only for moves Paul is a party
to**. An agent subcontracting to another agent (S48) changes docket and writes
nothing to his stream. And: **docket is built first**, rather than widening the
existing endpoint in place.

## The boundary

```
fleetix ─────► docket ─────► wild-life
 registers      references     references
 its principals an intention   nothing
```

Corrections to the committed plan:

1. **The fleetix arrow was backwards.** It said fleetix *publishes*
   `/srv/fleet/principals.json` and docket *reads* it — which is docket depending
   on fleetix. Instead `bin/fleetix-provision` POSTs the principal to docket,
   best-effort, skipped when docket is absent. Fleetix works without docket;
   docket works without fleetix.
2. **No `parties` table in docket.** §2 said soft reference and P1 said a table.
   A party is a soft reference `{system, type, id}` to a wild-life Person, which
   is what `fleetix/principals.yaml` already records as `principal_id`. Docket
   keeps only a `uid → party` map for peer-credential auth.
3. **`subject_label` is a display fallback, not a fact.** Store it with
   `label_as_of`; docket's own surfaces mark it as a snapshot. wild-life shows
   the live title because it has it.
4. **wild-life's API proxies to docket; the browser never calls it directly.**
   One bearer token, no CORS, one contract for the SPA. The cost is a runtime
   dependency from wild-life's API to docket — accepted and stated, not hidden.
5. **Docket unreachable must not read as "unassigned."** The assignment band
   renders as unavailable. Silence is never a fact (S21, S39).

## Sequencing

The committed plan had P3 (retire wild-life's columns) before P4 (agents move).
`my_tasks` at `routers/tasks.py:283` filters on
`Task.assignee_id == pid OR Task.responsible_id == pid`, so dropping
`assignee_id` first **breaks the queue for all three agents**. P3 and P4 swap.

**P0 · Boundary.** ✅ Done. `assignment.md` moved here from wild-life;
`fleetix/docs/contract.md` split, with the loop half becoming `protocol.md` here
and fleetix keeping only what it implements; this plan replaced.
*Done when:* `fleetix/docs/contract.md` describes nothing fleetix does not
implement, and the assignment spec exists in exactly one place.

**P1 · Docket alone.** `entries` + `moves` + the uid map. Eight states per the
spec. FastAPI / SQLAlchemy 2 async / Alembic, own `docket` schema in the existing
Postgres — matching wild-life so there is one idiom, not two. Peer-credential auth
over a unix socket; bearer for everything else. No wild-life, no agents.
*Done when:* a full loop runs including a decline, and the entry still reports the
commitment outstanding afterwards.

**P2 · wild-life writes and reads it.** Widen `AssignmentEvent`
(`schemas/tasks.py:133`) to the eight states. `record_assignment` keeps writing
the moment via the existing `upsert_moment` / `set_links` and additionally
creates or advances the docket entry. Task detail reads entry state through a
wild-life proxy route. `AssignmentBar.tsx` grows the new moves.
**wild-life's columns stay authoritative for `/tasks/mine` throughout.**
*Done when:* an S21 walkthrough passes — three weeks of silence is visible, and a
decline leaves the task open and owed.

**P3 · Agents speak the loop.** Fleetix agents stop polling `/tasks/mine` and
claiming; they accept, decline and deliver on entries. `agent/reference.sh` and
`agent/coder.sh` change; `fleetix/docs/contract.md`'s "what it should listen to"
becomes what it does listen to.
*Done when:* an agent declines an offer and the task is still open and still owed.

**P4 · Retire the four tables.** Through `change-the-model`. Drop `requests`,
`commitments`, `delegations`; remove `delegated`/`delivered` from `TaskStatus`;
drop `assignee_id` (`responsible_id` stays — RACI's R is a property of the
intention); remove `claimed_by_id`/`claimed_at`, `/claim`, `/release`, and the
matching rules in `authz.py`.
**Backfill is ~nothing:** 446 tasks carry an assignee, 443 of them Paul assigning
to Paul. Self-assignment is not an assignment, so only the 3 Planner rows are
candidates and even those need not be carried.
*Done when:* no wild-life table can assert who is holding work, and `/tasks/mine`
still returns the right queue from `responsible_id` alone.

**P5 · The wire.** A2A Agent Card and task lifecycle out; iTIP `VTODO` out,
reusing the iMIP plumbing in `api/src/wild_life/mail/`.
*Done when:* a party with no account here accepts by replying to an email.

## Critical files

| file | why |
|---|---|
| `api/src/wild_life/routers/tasks.py` | `record_assignment` (488), `my_tasks` (283), `claim_task` (407) |
| `api/src/wild_life/schemas/tasks.py` | `AssignmentEvent` (133) — widen to eight |
| `api/src/wild_life/models/tasks.py` | `assignee_id`, `responsible_id`, `claimed_by_id` |
| `api/src/wild_life/models/{tracking,requests}.py` | the three tables to drop |
| `api/src/wild_life/authz.py` | shrinks with claiming |
| `web/src/components/AssignmentBar.tsx`, `entities/task/Detail.tsx` | the four moves become eight |
| `fleetix/bin/fleetix-provision`, `fleetix/agent/*.sh` | principal registration; the loop |
| `docket/` | new |

**Reuse rather than rebuild:** `upsert_moment` and `set_links` already write the
exchange moment correctly — P2 adds a docket call beside them, it does not replace
them. `record_assignment`'s semantics are already right; only its vocabulary is
short.

## Verification

- **P1:** `pytest` against the Wild PC Postgres, same pattern as `api/tests/`
  (MARK-prefixed rows, cleaned up in a `finally`). Drive the full loop over the
  unix socket as an unprivileged uid to prove `SO_PEERCRED` identifies the caller.
- **P2:** `walk-a-scenario` on **S21**, then **S46**. Offer a real task to a
  person, wait, decline it, and confirm the task is still open, the silence is
  visible, and the moment appears on the day's stream.
- **P3:** offer a task to `researcher` (uid 40001) and have it decline;
  `sudo journalctl _UID=40001` should show the move, and the task should still be
  owed.
- **P4:** `pytest tests/ -v` plus `entities/coverage.test.tsx`, then confirm
  `/tasks/mine` returns Paul's queue with `assignee_id` gone.
- **Throughout:** `pnpm build` must pass before any deploy.

## What would show this is wrong

The committed plan put the checkpoint at P4, which is too late. Move it earlier:

- **End of P2 — has anything but a test created an entry?** If not, the loop is
  not being used and nothing after P2 is justified yet.
- **End of P3 — has a party who is not Paul made a move?** An agent counts. If
  not, docket is a table wild-life could have held, and it should fold back in.
- **S25 gets expensive.** "Everything with this person" now crosses a service.
  Measure it; that is the scenario this design puts at risk.
- **A person becomes a second-class party.** If the API grows agent-shaped
  affordances a human path lacks, the one property worth protecting is gone and
  what remains is a worse A2A.
