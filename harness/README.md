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

## What the assistant is told

`AGENTS.md` is copied into every workspace on every run (and as `CLAUDE.md`,
since not every tool follows a symlink out of a directory). It says one thing
that matters: **write `.harness/outcome.json` before stopping.**

```json
{ "outcome": "blocked",
  "note": "Cannot pick a schema without knowing the retention rule.",
  "question": "Delete old rows after 90 days, or archive them?" }
```

Without it a wrapper has to read the assistant's prose for the word "blocked",
which works until it doesn't and then fails silently in the expensive direction.
With it, `declined` becomes something an assistant can actually say — and the
whole arrangement exists so that refusing is sayable.

Wrappers consume the file, so a stale one cannot be read as this run's answer,
and the runner clears it before each run in case a wrapper died before it could.

## Verifiers

`docs/protocol.md`: *delivering is a claim about the world, so the runner checks
it.* That principle survives, without the coupling that came with it. Set
`HARNESS_VERIFIER` to a command; it receives the same request JSON and exits
non-zero to reject. A rejected `delivered` becomes an `update` carrying the
verifier's reason, and the entry stays yours.

Unset, nothing is checked and the wrapper is believed. `verifiers/wild-life`
does what the old runner hardcoded for everybody.

## Putting one on a new box

```bash
curl -fsSL <url>/harness/install.sh | bash -s -- --harness claude-code
export DOCKET_URL=https://docket.example
export DOCKET_API_KEY=dke_…        # a ticket, from /board/access
docket-claude
```

`--harness` takes `claude-code`, `pi`, `opencode`, `amplifier`, or `echo`.
`--dir` chooses where (default `~/.local/bin`), `--name` what to call it,
`--list` shows what is available.

It writes **one executable** containing the runner, the library, the wrapper and
the instructions. It re-executes itself to invoke the wrapper, so the subprocess
contract is unchanged — the subprocess is the same file wearing a different hat.
Nothing points back at a checkout.

`--harness echo` runs no assistant. It is how a new box proves it can reach the
register and act as the right party before anybody spends a token finding out.

`install.sh` is **generated** by `build-installer`, which embeds the sources —
an installer that fetched its own pieces could arrive half-installed, which is a
worse failure than a large file. `scripts/check.sh` regenerates and diffs it, so
it cannot quietly stop matching the sources beside it.

## Running one

From a checkout, without installing:

```bash
DOCKET_URL=https://docket.example \
DOCKET_API_KEY=dk_… \
HARNESS_WRAPPER=$PWD/harness/wrappers/claude \
  harness/runner
```

`DOCKET_API_KEY` may be a credential (`dk_…`) or a **ticket** (`dke_…`). Given a
ticket it is spent once for a credential, which is written to
`DOCKET_CREDENTIAL_FILE` (default `~/.docket/credential`) and used from then on.
So one variable either *is* the identity or *buys* it, and a new box does not
need to know which.

Every knob is an environment variable and every component is a path, so
substituting an assistant is a different `HARNESS_WRAPPER` and nothing else.

```
./harness/harness_check.sh     the loop, with no model and no waiting
./harness/installed_check.sh   the generated bundle, on a simulated new box
```
