# The harness

Scripts that let an assistant be driven by docket. **Not part of the service** —
nothing under `src/docket/` imports any of this, and the register still routes
nothing and schedules nothing. These ship here because they speak the protocol
in `docs/protocol.md` and should not drift from it; they are clients, and the
`harness/` directory is the boundary.

```
runner            polls docket, owns the workspace, invokes a wrapper,
                  turns what it returns into a move
wrappers/<tool>   knows exactly one assistant: how to start it, how to resume
                  it, and how to tell whether it got anywhere
verifiers/<name>  optional. checks a claim of completion against the world
```

## Why wrappers, rather than a flag on the runner

The four assistants do not agree on anything that matters here:

| | non-interactive | continuation | how the handle is obtained |
|---|---|---|---|
| `claude` | `-p --output-format json` | `--resume <id>` | captured from the run's own output |
| `pi` | `-p --mode json` | `--session-id <id>`, created if missing | **chosen by the caller** — nothing to store |
| `opencode` | `run --format json` | `-s <id>` / `--continue` | captured, or implied by `--dir` |
| `amplifier` | `run --output-format json` | `--resume <id>` | captured |

Three incompatible ways of resuming, and one of them needs no stored state at
all. Some assistants have their own loop, their own task source, or no
continuation to speak of. A `case $AGENT_RUNTIME` inside one runner can only
serve the assistants that happen to share a shape, which is how the previous
version came to assume every subject was a wild-life task and that finishing
meant a row in wild-life had changed.

## The contract

One invocation, JSON on stdin, JSON on stdout. A wrapper may write anything it
likes to stderr; logging is free. **Stdout should be the result and nothing
else** — the runner reads all of it as JSON, and falls back to the last line if
that fails, so a wrapper that also chatters on stdout still works and one that
forgets `jq -c` is not punished for it.

```jsonc
// stdin
{
  "entry": {
    "id": "…", "subject": "wild-life:task:…", "subject_label": "…",
    "terms": "what specifically was asked", "state": "offered" | "accepted",
    "requester": "…", "delegate": "…", "expected_by": null
  },
  "moves":     [ { "seq": 1, "at": "…", "by": "…", "move": "offer", "note": null } ],
  "workspace": "/…/wild-life-task-3a5962b4",   // persists across runs; yours
  "me":        "wild-life:person:…"
}
```

```jsonc
// stdout (last line)
{
  "outcome":  "delivered" | "progressed" | "blocked" | "declined" | "failed",
  "note":     "one line for the register",
  "question": "…"     // blocked only: opens an entry back to the requester
}
```

### Outcomes, and the moves they become

| outcome | on an `offered` entry | on an `accepted` entry |
|---|---|---|
| `declined` | `decline` | `decline` — reneging is a real move |
| `delivered` | `accept` then `deliver` | `deliver` |
| `progressed` | `accept` then `update` | `update` |
| `blocked` | `accept`, then ask and link | ask the requester, `blocked_on` |
| `failed` | `update`; stays offered, still yours to answer | `update` |

**Accepting happens because the wrapper chose to work, never before it looked.**
The previous runner accepted everything it was offered and then ran, so a refusal
was unexpressible — which is the failure `docs/protocol.md` describes. A wrapper
that returns `declined` has declined; nothing was promised on its behalf.

### `workspace`

A directory belonging to the **subject**, not the entry. A subject that is
declined and re-offered to somebody else is the same work, and its files should
not be stranded in a conversation that ended. Contents are the wrapper's; the
runner creates it and never looks inside.

Continuation state belongs in there too — `<workspace>/.harness/<wrapper>/` by
convention. The runner stores nothing on a wrapper's behalf, which is what lets
`pi` store nothing at all: it can derive a stable session id from the subject and
let its own `--session-id` do the work.

## Verifiers

`docs/protocol.md`: *delivering is a claim about the world, so the runner checks
it.* That principle survives, without the coupling that came with it. Set
`HARNESS_VERIFIER` to a command; it receives the same request JSON and exits
non-zero to reject. A rejected `delivered` becomes an `update` carrying the
verifier's reason, and the entry stays yours.

Unset, nothing is checked and the wrapper is believed. `verifiers/wild-life`
does what the old runner hardcoded for everybody.

## Running one

```bash
DOCKET_URL=http://127.0.0.1:9006 \
DK_CRED=$HOME/.docket/credential \
HARNESS_WRAPPER=$PWD/harness/wrappers/claude \
HARNESS_WORKSPACES=$HOME/workspaces \
  harness/runner
```

Every knob is an environment variable and every component is a path, so
substituting an assistant is a different `HARNESS_WRAPPER` and nothing else.
