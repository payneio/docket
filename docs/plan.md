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

Since P10 the first arrow is optional as well as best-effort: a party can hold a
credential instead of a unix account, so docket runs with no fleetix, no
principals table and no accounts on the box at all. Peer credentials remain the
*better* answer where they apply — they are the only ones the kernel vouches for
— but they are no longer the only one.

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

**P1 · Docket alone.** ✅ Done. `entries` + `moves` + `local_identities`, the
transition table in `states.py`, peer-credential auth over a unix socket, bearer
for everything else. 17 tests green plus `tests/peercred_check.sh`, which drives
the loop from uid 40001 presenting no credential at all.
*Done when:* a full loop runs including a decline, and the entry still reports the
commitment outstanding afterwards.

One thing the build settled that the spec had left ambiguous: **`return` is a
move, not a state.** The spec listed it among the states and also said it
"returns to `accepted`", which cannot both be true. A return does not rest
anywhere — it puts the work back in the delegate's hands — so its resulting state
is `accepted` and the log still records that it happened. That is what keeps "we
went round three times" legible without inventing a state nobody sits in.

**P2 · wild-life writes and reads it.** ✅ Done — see the checkpoint below before
starting P3. Widen `AssignmentEvent`
(`schemas/tasks.py:133`) to the eight states. `record_assignment` keeps writing
the moment via the existing `upsert_moment` / `set_links` and additionally
creates or advances the docket entry. Task detail reads entry state through a
wild-life proxy route. `AssignmentBar.tsx` grows the new moves.
**wild-life's columns stay authoritative for `/tasks/mine` throughout.**
*Done when:* an S21 walkthrough passes — three weeks of silence is visible, and a
decline leaves the task open and owed.

**P3 · Agents speak the loop.** ✅ Done. Fleetix agents stop polling `/tasks/mine` and
claiming; they accept, decline and deliver on entries. `agent/reference.sh` and
`agent/coder.sh` change; `fleetix/docs/contract.md`'s "what it should listen to"
becomes what it does listen to.
*Done when:* an agent declines an offer and the task is still open and still owed.

**P4 · Retire the four tables.** ✅ Done. Through `change-the-model`. Drop `requests`,
`commitments`, `delegations`; remove `delegated`/`delivered` from `TaskStatus`;
drop `assignee_id` (`responsible_id` stays — RACI's R is a property of the
intention); remove `claimed_by_id`/`claimed_at`, `/claim`, `/release`, and the
matching rules in `authz.py`.
**Backfill is ~nothing:** 446 tasks carry an assignee, 443 of them Paul assigning
to Paul. Self-assignment is not an assignment, so only the 3 Planner rows are
candidates and even those need not be carried.
*Done when:* no wild-life table can assert who is holding work, and `/tasks/mine`
still returns the right queue from `responsible_id` alone.

**P6 · Ground it.** ✅ Done. The suite ran against the live castle database and
cleaned up by `DELETE ... WHERE subject LIKE 'test:mark%'`, which put one typo in
a predicate between a test run and the register — and meant no CI could exist,
because there is only one castle. `tests/__init__.py` redirects at `docket_test`
before docket is imported at all; `conftest` creates it, migrates into it, and
drops the schema after. `peercred_check.sh` needed it more, being a real server.
Plus ruff, mypy and `scripts/check.sh`.
*Done when:* the suite passes and the register's row counts do not move.

**P7 · Close the holes.** ✅ Done. Reading was unscoped — any registered uid could
read every conversation with anyone, including the `mailto:` parties. Now scoped
to requester, delegate or accountable, and a stranger gets 404 rather than 403,
because "that exists and is not yours" already says somebody owes somebody
something. A move takes `SELECT ... FOR UPDATE`; without it two movers both wrote
`max(seq) + 1`. Self-offers and self-redirects refused; the listing bounded.
*Done when:* a concurrent-move test returns one 200 and one 409 rather than a
500, and fails when the lock is removed.

**P8 · Quiet is not stuck.** ✅ Done. The register could say how long since
somebody spoke and not how long since anything happened, and on the first real
day that was the whole story: seven identical updates in thirty-one minutes kept
`quiet_for_seconds` under sixty while nothing had moved since the accept.
Derived, never stored: `last_progress_at`, `stalled_for_seconds`,
`updates_since_progress`, `whose_turn`. `blocked_on` makes the edge that was
already there in prose — a stuck delegate opened a second entry and recorded the
link as a sentence in a note. Whether a block still binds is derived from whether
that entry is open, so answering the question unblocks the work by itself.
`service.py` holds what a move means, so the board and the API cannot disagree.
*Done when:* the live register reports `updates_since_progress: 7` on `2ee5a2b7`
with stalled measured from 16:09 rather than 16:18. It does.

**P9 · The board.** ✅ Done. `http://localhost:9006/` — served by docket, not
added to wild-life's SPA, because the register must not require wild-life to be
running and a surface that did would make that only half true. Server-rendered,
no build step, no JS. A browser cannot present peer credentials, so it asks for
the token once and keeps it in an HttpOnly SameSite=Strict cookie whose value is
the token: the cookie *is* the credential, and signing it would only hide that.
*Done when:* opening it shows the planner's unanswered question with its 27 hours
on it. It did — and the question had been invisible for a day, which is the
answer to the end-of-P2 checkpoint below.

**P10 · A party without a box.** ✅ Done. Until now everything that was not on
this machine authenticated with one shared secret that acted as the owner, so a
second assistant could not be a second party — only the owner again. And the only
way to *be* a party was a unix account, which ties docket to a single box and to
fleetix provisioning it.

`credentials` is the counterpart to `local_identities`: that table maps a unix
account onto a party, this one maps a presented secret onto a party. Owner-issued,
shown once, stored as a hash, revoked and never deleted. A credential names
exactly one party and inherits nothing.

The change that had to land first: **`is_operator` was `via != "peercred"`**,
which was true of both cases that existed and would have quietly promoted every
credential to reading the whole register, issuing more of itself, and erasing
entries. It is an explicit field now. Two routes were also guarding on
`via == "bearer"` as a stand-in for "the owner", which stopped being the same
thing the moment a second kind of token could arrive in that header.

Reached over Caddy at `docket.civil.payne.io`, so TLS is the proxy's; the board's
session cookie is `Secure` when the request came over HTTPS and not when it came
over loopback, because a `Secure` cookie on the loopback path is never sent back.
*Done when:* a party that exists only as a token runs the whole loop from off-box
and its moves are attributed to it and marked unattested. They are.

**P11 · One way in.** ✅ Done. Peer credentials removed. They were the better
answer where they applied — nothing to store, leak or rotate — and they applied
to three processes on one machine, required unix accounts, and **decided
nothing**: no rule ever consulted the `attested` flag they produced. `local_identities`,
the socket listener, `peercred.py` and `attested` are gone;
`moves.via_credential` replaces the flag with the half that does work, so
revoking a credential answers what it touched.

Agents enrol themselves. The owner writes a ticket naming a party; the agent
spends it for a credential it keeps. What sits in provisioning is a secret that
expires, and the long-lived one is minted at the far end and never travels. A
ticket is a separate table from a credential rather than a flag on one, so an
enrolment key structurally cannot be presented as a party.

*The cost, stated rather than discovered:* a leaked credential works from
anywhere that can reach the register, where a stolen uid worked only while you
were on that box being that user. mTLS was considered and declined — Caddy
terminates TLS, so docket would need client certs forwarded, a CA, renewal and
revocation, and the only thing it buys over an enrolment-minted token is that the
secret never transits. Revisit if agents span networks docket does not trust.

**P5 · The wire.** A2A Agent Card and task lifecycle out; iTIP `VTODO` out,
reusing the iMIP plumbing in `api/src/wild_life/mail/`. Two prerequisites belong
with it: `external_ref` is not unique, so a retried inbound `REQUEST` opens a
second conversation; and `on_behalf_of` is accepted even for a party that has a
local identity and could have spoken for itself, which is what makes `attested`
advisory rather than meaningful.
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

- **End of P2 — has anything but a test created an entry?** ✅ **Yes, and the
  answer took a day to read.** Two real entries, one of them the planner asking
  whether a public-facing brief was inside its delegation scope — a question it
  says it had already asked four times through the old Requests table. It sat
  `offered` and unanswered for twenty-seven hours. The register held the right
  fact the whole time and nothing put it in front of anybody, which is what P9
  is for. The loop earning its place and the loop being *read* turned out to be
  two different questions, and only the first one was on this list.
- **End of P3 — has a party who is not Paul made a move?** ✅ Yes. The planner
  accepted an offer and reported on it from uid 40002, over its own socket, with
  no credential presented. Both moves are `attested: true`; Paul's offer is not.
- **S25 gets expensive.** "Everything with this person" now crosses a service.
  Measure it; that is the scenario this design puts at risk.
- **A person becomes a second-class party.** If the API grows agent-shaped
  affordances a human path lacks, the one property worth protecting is gone and
  what remains is a worse A2A.
