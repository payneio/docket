# The protocol — what an agent listens to

The wire an agent speaks to take on work and report what became of it. A
[fleetix](../../fleetix) principal is one kind of client; so is a person with a
calendar. **The protocol does not distinguish them**, and that is the property
worth protecting.

An agent gets in by holding a credential, which it may be given or may enrol for
with a single-use ticket (`POST /enrol`). Nothing about how it arrived makes it a
lesser party — the register once identified local callers by unix uid and does
not any more, precisely because that made a party's standing depend on where it
was running.

Specified in `assignment.md`; this is what a client sees.

## What it listens to

An agent reads its own inbox and answers it:

| | |
|---|---|
| who am I | `GET /me` — answered from the credential presented |
| what is open | `GET /entries?party=<me>&open_only=true` |
| answer it | `POST /entries/{id}/moves` — `accept`, `counter`, `decline`, `redirect` |
| say where it stands | `POST /entries/{id}/moves` — `update` |
| hand it back finished | `POST /entries/{id}/moves` — `deliver` |

**It is offered work; it never takes work nobody offered it.** The previous
arrangement was a queue — an agent read `/tasks/mine`, claimed a row and began.
On 2026-08-20 that meant an agent started a brief it had never agreed to while
the register still read `offered`, because *asking* had been recorded as
*agreeing*. A queue cannot express a refusal, so an agent working from one can
only ever say yes.

**Delivering is a claim about the world, so the runner checks it.** The agent's
own runtime saying it finished is not evidence; whether the task actually closed
is. A run that changes nothing and exits 0 leaves the entry `accepted` and posts
an `update` saying so — S44's failure is a job that stops working and tells only
`journalctl`, and a shared register is exactly where that should surface instead.

## Chains

An agent may hand work on (S48, `wild-life/docs/scenarios.md`). Two rules hold
however long the chain gets:

- **Accountability does not move.** It is fixed at the head.
- **Authority does not outlive its grant.** A subcontracted principal holds
  authority derived from its delegator's, not one of its own — so withdrawing the
  first leaves nothing downstream still working.

Neither is enforced yet. Both are testable the day a principal is provisioned by
another principal rather than by the operator.
