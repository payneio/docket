# The protocol — what an agent listens to

The wire an agent speaks to take on work and report what became of it. A
[fleetix](../../fleetix) principal is one kind of client; so is a person with a
calendar. **The protocol does not distinguish them**, and that is the property
worth protecting.

Specified in `assignment.md`; this is what a client sees.

## What it listens to — today

Provisional, and inherited rather than designed:

| | |
|---|---|
| poll | `GET /tasks/mine`, `GET /requests/inbox` |
| take | `POST /tasks/{id}/claim` (atomic; 409 if held) |
| report | `PATCH /tasks/{id}` |

This is a bespoke work queue. It says who is holding a task and nothing about what
is owed, so an agent that stops has no way to say "I decline" and no way for the
obligation to outlive its refusal.

## What it should listen to

**The assignment loop** — specified in
`/data/repos/wild-life/docs/assignment.md`:

```
offered → accepted → delivered → settled
   ├─ countered      └─ returned ─┘
   ├─ declined
   ├─ withdrawn
   └─ redirected
```

An agent receives an **offer** and accepts, counters, or declines. It performs. It
**delivers**. The requester **settles**. A decline ends the assignment and never
the commitment.

Two things follow, and they are the reason this is worth the change:

1. **A human delegate and an agent are in the same conversation.** No live
   protocol does this — A2A's human-facing states are `input-required` and
   `auth-required`, where the person is an *interrupt*, not a party.
2. **Substitutability becomes real.** "Speaks the fleetix contract" is a property
   of a documented protocol rather than of a shared shell script, so an agent is
   interchangeable regardless of what is inside it — including one that is a
   person.

The loop maps outward to iCalendar `VTODO`/iTIP for humans and A2A for agents,
losing the same three states in both directions (`settled`, `returned`,
`redirected`), which is the gap named in the README.

## Chains

An agent may hand work on (S48, `wild-life/docs/scenarios.md`). Two rules hold
however long the chain gets:

- **Accountability does not move.** It is fixed at the head.
- **Authority does not outlive its grant.** A subcontracted principal holds
  authority derived from its delegator's, not one of its own — so withdrawing the
  first leaves nothing downstream still working.

Neither is enforced yet. Both are testable the day a principal is provisioned by
another principal rather than by the operator.
