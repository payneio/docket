# Assignment — the spec

**This is a specification, not a description.** Nothing here is built. It exists
so the concept is settled once, in a form implementable on either side of a
system boundary, before any table is written. It lives here rather than in
wild-life because docket implements it; the migration it forces in wild-life goes
through that repo's `change-the-model` skill, which cites this document.

It closes the first of `wild-life/api/docs/domain.md` → *What the domain has not decided*:
*what is a request, and is it one concept or several?*

---

## 1. The answer: one loop, three payloads

**Asking someone a question, assigning a task, and delegating an outcome are one
loop.** The moves are identical — offer, counter, accept, decline, deliver,
settle, withdraw. What differs is only *what is owed*, and an assignment already
names that by reference. Three loops would triplicate a state machine to express a
difference its referent already carries, which is the same argument the model
already makes for an intention naming one scope by one polymorphic reference
rather than a nullable foreign key per rung.

So: **an Assignment is a conversation about one Intention.** A question you are
owed is an intention to produce an answer; a delegated outcome is an intention
that a claim become true. The loop does not care which.

## 2. What an Assignment is, and is not

> An **Assignment** is the record of who has been asked to discharge an
> intention, what they said, and when they said it.

- It is **not** the intention. The intention says what is owed, to what end, and
  by when. The assignment says who is holding it and how that is going.
- It is **not** authority. Whether the delegate *can* do the thing is enforced
  elsewhere — `wild-life/api/docs/domain.md` → *Delegation asks four questions*. An
  assignment that also granted permission would make permission and promise the
  same fact, and they are not.
- It is **external** by the core/external test: it exists only because other
  actors exist. The intention is core.

**An assignment names exactly one intention.** One act may discharge three
commitments (S14), but that is three assignments or none — it is a fact about the
*moment*, recorded on the existing `discharges` edge, not about the conversation.

## 3. States

The conversation for action ([Winograd & Flores, 1986](https://en.wikipedia.org/wiki/Understanding_Computers_and_Cognition)),
with counter-offer and decline as first-class moves rather than error paths.

```
                  ┌──────────── counter ◄────────┐
                  ▼                              │
  (new) ──► offered ──► accepted ──► delivered ──┴──► settled ▪
              │            │             │
              │            │             └── returned ──► accepted
              │            │
              ├── declined ▪                └── redirected ▪ (opens a child)
              └── withdrawn ▪
```

| state | meaning | terminal |
|---|---|---|
| `offered` | asked; nothing agreed | |
| `countered` | the delegate proposes different terms; the requester may accept them, re-offer, or withdraw | |
| `accepted` | the delegate has undertaken it | |
| `delivered` | the delegate asserts performance; awaiting the requester's declaration | |
| `settled` | the requester declares satisfaction | ▪ |
| `returned` | the requester declares *dis*satisfaction; returns to `accepted` | |
| `declined` | the delegate refuses | ▪ |
| `withdrawn` | the requester takes it back | ▪ |
| `redirected` | the delegate passes it on; this assignment ends and a child opens | ▪ |

### The rules that make this worth having

1. **No terminal state of an assignment ends its intention.** The intention's own
   endings — *discharged*, *abandoned*, *voided* — are a separate vocabulary
   written by a separate act. `declined` means "they said no", never "it is over"
   (S21). This is the constraint the current `TaskStatus` violates by containing
   `delegated` and `delivered`.
2. **`settled` does not discharge the intention either.** Discharge is recorded as
   a moment on the existing `discharges` edge. Declaring an assignment satisfied
   and declaring a commitment discharged are two judgements, and collapsing them
   makes contribution read as satisfaction (S15).
3. **Accountable is set at creation and is immutable.** Responsible moves to the
   delegate on `accepted` and returns on every terminal state except `settled`.
   Accountability does not fan out along a chain, however long it gets (S48).
4. **Both parties are named on every move.** No move may make the act appear to be
   the other party's own — the impersonation rule from `domain.md` →
   *Delegation asks four questions*.
5. **Silence is not a state.** Staleness is *derived* from the last move's
   timestamp against the expectation, never stored. Same reason a next action is
   read off the tasks rather than typed (S45): a stored silence cannot be cleared
   by anything happening.
6. **A returned delivery is not a new assignment.** The conversation continues, so
   "we went round three times" stays legible.

## 4. Shape

The only outward reference is `intention`. Everything else is self-contained,
which is what lets the record live on either side of a boundary.

| field | notes |
|---|---|
| `id` | |
| `intention` | polymorphic (type, id) — **the only outward reference** |
| `requester` | principal |
| `delegate` | principal, nullable; `delegate_label` when external and unmodelled |
| `accountable` | principal; immutable after creation |
| `state` | above |
| `terms` | what specifically is asked — may narrow the intention, never widen it |
| `expected_by` | the delegate's undertaking; **distinct from the intention's `due_date`**, because what they promised and what you owe are different dates and both matter |
| `parent` | the assignment this was redirected from (S48 chains) |
| `external_ref` | iTIP `UID`+`SEQUENCE`, or an A2A task id — so a wire exchange round-trips without paraphrase (S35's rule, applied here) |
| `moves[]` | append-only: `(at, by, move, note)` |

**Everything reads off `moves[]`.** State is a fold over it. Staleness, "three
weeks of nothing" (S21), the subcontracting chain (S48), and cross-boundary
reconciliation (S46) all read off the log; syncing a state alone loses every one
of them. This is the one place the model *is* event-sourced, for the reason
`domain.md` already gives: endings and appraisal need the history.

## 5. Expressible on the wire

The point of one loop is that a standard already speaks it. Both mappings are
lossy in the same direction — outward, never inward — which is the test.

| assignment | iCalendar `VTODO` + iTIP | A2A task |
|---|---|---|
| `offered` | `REQUEST`, `PARTSTAT=NEEDS-ACTION` | `submitted` |
| `countered` | `COUNTER` | `input-required` (approximate) |
| `accepted` | `REPLY`, `PARTSTAT=ACCEPTED` | `working` |
| `delivered` | `PARTSTAT=COMPLETED` | `completed` |
| `settled` | *(no equivalent)* | *(no equivalent)* |
| `returned` | *(no equivalent)* | *(no equivalent)* |
| `declined` | `REPLY`, `PARTSTAT=DECLINED` | `rejected` |
| `withdrawn` | `CANCEL` | `canceled` |
| `redirected` | `PARTSTAT=DELEGATED` + `DELEGATED-TO` | *(no equivalent)* |

The three gaps are the same gap, and they are the reason this document exists:
**no standard carries the declaration of satisfaction**, because no standard
carries the commitment that would be satisfied. What we send outward is the
assignment; what stays is the obligation.

## 6. What this replaces

Named for the migration, not done here. All four hold a partial version of the
loop above; `wild-life/api/docs/erd.md` §4 lays them side by side.

- `requests` — becomes an assignment whose intention is "produce an answer".
  `kind` (question/decision/input/deliverable) is a property of what is owed, not
  of the conversation, and does not survive.
- `commitments` — the commitment is the intention; the acceptance half is the
  assignment. Nothing else is left.
- `delegations` — the same loop with a longer status vocabulary. Its
  `accountable_owner_id` is the rule in §3.3 and survives as `accountable`.
- `TaskStatus.delegated` / `.delivered` — deleted. They are assignment states
  wearing an intention's clothes, which is rule §3.1 stated as a defect.
- `tasks.assignee_id` vs `responsible_id` — one concept. `responsible` is RACI's
  R and lives on the intention; who is holding it right now is the assignment's
  `delegate`. Nothing has ever written the two columns apart.

## 7. The two questions `change-the-model` will ask

**Which scenario forces this?** S21 (a decline must not read as an ending),
S22 (both directions, and what has gone quiet), S46 (the assignment may live
elsewhere and the commitment stays here), S48 (chains, with accountability
fixed). None of the four is answerable today, and S21 is actively contradicted by
`TaskStatus`.

**What else can write this fact?** After the migration, one writer: the
assignment's move log. Today four tables and one endpoint can each assert that
someone is holding something, and they disagree. That is the defect being fixed,
and it is why consolidation matters more than any individual field here.
