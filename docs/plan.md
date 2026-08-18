# Plan — three projects, one boundary each

Written before the code. The point of the plan is the **boundary**: what each
project owns, what it may reference, and what it must never call. Get that right
and the phases are ordinary work.

## 1. The three

| project | owns | one line |
|---|---|---|
| **wild-life** | intentions, attention, occurrences | one person's life. What is owed *to yourself*, what you attend to, what happened. |
| **docket** | commitments and the conversation about discharging them | what is owed *between parties*, and how it is going. |
| **fleetix** | unix accounts for agents | who an actor is, and what it can touch, enforced by the kernel. |

Each is meaningful with the other two absent. That is the test, and it is why
they are three repositories rather than three directories.

## 2. Who may reference whom

**One rule, and everything below is a consequence: a dependency may point from
the outer project to the inner one, never back.**

```
fleetix ─────► docket ─────► wild-life
(publishes a       (references          (references
 uid→party map)     an intention)        nothing)
```

Concretely:

- **docket stores a soft reference**, never a foreign key:
  `subject = {system: "wild-life", type: "task", id: <uuid>}`, plus a
  `subject_label` snapshot so an entry is readable when wild-life is down or
  gone. **Docket never calls wild-life.**
- **wild-life gains no column.** Its *read surface* queries docket — "assignments
  for this task", "what is outstanding with this person" — at render time. A
  read-time query is not a schema dependency, which is the distinction
  `api/docs/domain.md` → *Core and external* already draws: external may
  reference core, core may not reference external.
- **fleetix depends on neither.** It publishes `/srv/fleet/principals.json`
  (uid → party) as a plain file on provision. Docket reads it if present and
  falls back to bearer auth if not. Fleetix works with docket absent; docket
  works with fleetix absent.

**The failure this prevents:** three projects that can only be run together,
which is a monorepo with extra steps.

## 3. How a caller is known

| caller | how | why |
|---|---|---|
| a fleetix principal, on this box | unix socket + `SO_PEERCRED` → uid → party | the kernel says who it is; nothing is asserted and no token can be replayed |
| wild-life's UI, this box | bearer token | one trusted service, one credential |
| an off-box party | bearer token, later a scoped credential | a uid does not survive the network |

The local case is the unusual one and worth keeping: **on one machine, identity
is stronger than any protocol specifies**, because the caller cannot lie about
it. A2A authenticates with bearer credentials only because it must cross a
network.

## 4. What moves, what stays

| thing | today | after |
|---|---|---|
| a task, an outcome | wild-life | **stays** — it is the intention |
| `responsible_id` | wild-life `tasks` | **stays** — RACI's R is a property of the intention |
| `assignee_id` | wild-life `tasks` | **moves** — becomes the entry's `delegate` |
| `TaskStatus.delegated`, `.delivered` | wild-life | **deleted** — assignment states wearing an intention's clothes |
| `requests`, `commitments`, `delegations` | wild-life | **deleted** — three partial copies of one loop |
| `claimed_by_id`, `/claim`, `/release` | wild-life | **moves** — claiming is accepting an offer |
| most of `authz.py` | wild-life | **shrinks** — scope rules exist because there was no other place to put them |

## 5. Stack

Match wild-life so there is one idiom to maintain, not two: FastAPI, SQLAlchemy
2.0 async, Alembic, Postgres — its own `docket` schema in the same instance, the
way `wild_life` already is. Separate schema keeps the projects independent;
a separate instance would be ceremony. Deployed by Wild PC as a service like
everything else on the box. **No UI at first** — wild-life's read surface is the
front end, and a board of its own is a later question.

## 6. Phases

Each has one deliverable and one done-test. Do not start the next until the
done-test passes.

**P0 · The boundary is written down.**
Move the assignment spec here from wild-life; leave a pointer. Split
`fleetix/docs/contract.md`: the substrate half stays there, the loop half comes
here as `docs/protocol.md`.
*Done when:* no project's documentation describes something it does not implement.

**P1 · Docket alone.**
`entries`, `moves`, `parties`. The eight states and their transitions. REST, and
peer-credential auth. No wild-life, no agents.
*Done when:* a full loop runs including a decline, and the entry still reports
the commitment outstanding afterwards.

**P2 · wild-life reads it.**
Task detail shows its entries; Person shows what is outstanding in both
directions. No wild-life schema change.
*Done when:* an S21 walkthrough passes — three weeks of silence is visible, and a
decline leaves the task open and owed.

**P3 · Retire the four tables.**
Through `change-the-model`. Drop `requests`, `commitments`, `delegations`; remove
the two assignment states from `TaskStatus`; `assignee_id` gives way to the
entry's delegate.
*Done when:* no table in wild-life can assert who is holding work.

**P4 · Agents speak the loop.**
Fleetix agents stop polling `/tasks/mine` and claiming; they accept, decline and
deliver on entries. `/claim`, `/release` and the claim columns leave wild-life.
*Done when:* an agent declines an offer, and the task is still open and still
owed by the person who committed to it.

**P5 · The wire.**
A2A Agent Card and task lifecycle out; iTIP `VTODO` out, reusing wild-life's iMIP
plumbing.
*Done when:* a party with no account here accepts by replying to an email.

## 7. What would show this is wrong

Named now, because it never gets asked later.

- **Only one client ever appears.** If by P4 the sole caller is wild-life's UI
  and no agent has accepted an entry, the split bought nothing and docket should
  fold back in. Check at P4, not before.
- **S25 gets expensive.** "Everything with this person" now crosses a service
  boundary. If that read becomes slow or awkward enough to discourage using it,
  the cost was underestimated — that scenario is the one this design put at risk
  and it must be measured, not assumed.
- **The party models disagree.** If docket's idea of who someone is drifts from
  wild-life's Person, the soft reference was too soft.
- **A person becomes a second-class party.** If the API grows agent-shaped
  affordances that a human path lacks, the one property worth protecting has been
  lost and the rest is a worse A2A.
