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
| `amplifier-agent` | `run --output json --display ndjson` | `--session-id` + `--resume` | **chosen**, and resume is explicit |

| `amplifier` (the CLI) | `run --output-format json` | `--resume <id>` | captured |

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

Three channels, and the runner takes the first that answers:

| | how | who has it |
|---|---|---|
| 1. `report_outcome` tool | MCP, validated, a bad call is refused with a reason it can act on | `claude-code`, `amplifier-agent` |
| 2. a block in the final response | ```` ```outcome ```` … ```` ``` ```` at the end | **everyone** |
| 3. its prose | grepped for a blocker; may only be wrong towards `progressed` | last resort |

```json
{ "outcome": "blocked",
  "note": "Cannot pick a schema without knowing the retention rule.",
  "question": "Delete old rows after 90 days, or archive them?" }
```

**The response is the floor, not the file.** An assistant that is hosted,
sandboxed, or behind a gateway may have no filesystem the runner can see; all of
them answer. `AGENTS.md` (copied into every workspace on every run, and again as
`CLAUDE.md`, since not every tool follows a symlink) asks for the block.

`.harness/outcome.json` still exists and is invisible to the assistant: it is how
the MCP server, being a subprocess, hands its answer back to the runner. Wrappers
consume it, and the runner clears it before each run, so a stale one cannot be
read as this run's answer.

The last block wins, so an assistant that sketches the format while thinking
aloud is not taken at its example. A `blocked` without a question is rejected on
every channel — it is as useless in a response as in a tool call.

## Setting up a principal

A fleetix principal is its own unix account with its own PATH, so it has none of
the operator's tools. Four steps, and only the third differs between agents:

```bash
sudo -u <name> bash -lc 'curl -fsSL <install.sh> | bash -s -- --harness echo'
sudo -u <name> bash -lc '~/.local/bin/docket-echo'      # reaches the register?
```

Prove it can reach the register and act as the right party **before** installing
an assistant. `echo` runs no model, so the answer costs nothing and the failure,
if there is one, is unambiguous.

Then install the assistant for that account, install the harness for it, and
write `~/agent`. Keep `~/agent` at mode `700` — `fleetix doctor` checks it.

The three on this machine, as examples of the same four steps:

| | assistant | reaches a model by |
|---|---|---|
| `researcher` | `amplifier-agent` (via `uv`) | the LAN proxy's native Anthropic endpoint |
| `planner` | `claude-code` | its own `ANTHROPIC_API_KEY` |
| `coder` | `claude-coder` | its own `ANTHROPIC_API_KEY`, plus `GITHUB_TOKEN` to clone |

## Scheduling one

The runner is **single-pass**: it asks what is owed, works what is due, and
exits. Something else decides how often.

**Do not let a supervisor's restart provide the cadence.** It is the obvious
thing to reach for and it quietly spends signals you need elsewhere. Under
`Restart=always` with a one-minute pass, a fleetix principal reached
`NRestarts=219` in a few hours — past the 20 at which `fleetix doctor` reports
*"running, but not for long at a time"* — so a real crash loop could no longer be
told from an idle one, and uptime sat at about a second forever. The check that
exists to notice an agent which cannot start was the one thing the arrangement
destroyed.

Put the loop in the thing you own instead:

```bash
while :; do
  timeout "${AGENT_TICK_TIMEOUT:-1800}" docket-<harness>
  docket-<harness> --wait || sleep "${AGENT_INTERVAL:-60}"
done
```

`--wait` follows `GET /stream` and returns as soon as somebody else moves
something you are named on — so offered work is picked up in about a second
rather than in up to a minute. It is a separate mode, not a flag on the pass:
the pass stays exactly what it was, and waiting does not hold the lock.

Three things it does that a bare `sleep` cannot, and one it does not:

- **It ignores your own moves.** The runner posts one at the end of nearly every
  pass, and waking on that is a loop with no sleep in it. The stream's `open`
  frame says which party you are, so no extra call is needed to know.
- **It returns on a timer anyway** — `HARNESS_SWEEP_SECONDS`, 15 minutes by
  default. Staleness is the *absence* of events: nothing will ever be published
  saying a delegate has gone quiet for three days. The stream turns that sweep
  from every sixty seconds into every fifteen minutes; it does not remove it.
- **It reports an unreachable stream** rather than waiting on nothing, which is
  why the `|| sleep` above is there. A register you cannot follow must degrade
  to polling, never to silence.

It does not make the register push you work. Nothing is delivered to an agent;
it is still offered, and the agent still asks.

Restarts then count only when something died and uptime means how long it has
been healthy. The `timeout` keeps what exiting used to guarantee: a wedged
assistant cannot take the loop with it.

A timer (`Type=oneshot` plus `OnUnitInactiveSec=`) is equally correct and says
the same thing to a supervisor — an activation is not a restart. What is wrong is
only using `Restart=` as a scheduler.

## Verifiers

`docs/protocol.md`: *delivering is a claim about the world, so the runner checks
it.* That principle survives, without the coupling that came with it. Set
`HARNESS_VERIFIER` to a command; it receives the same request JSON and exits
non-zero to reject. A rejected `delivered` becomes an `update` carrying the
verifier's reason, and the entry stays yours.

Unset, nothing is checked and the wrapper is believed. `verifiers/wild-life`
does what the old runner hardcoded for everybody.

### Configuring one

Each wrapper reads its own variables, prefixed for the program it wraps, so a box
running several does not have one name meaning two things:

```
CLAUDE_MODEL  CLAUDE_MAX_TURNS  CLAUDE_MCP_CONFIG  CLAUDE_ALLOWED_TOOLS
PI_PROVIDER  PI_MODEL
OPENCODE_MODEL  OPENCODE_AGENT
AMPLIFIER_PROVIDER  AMPLIFIER_MODEL                     # the `amplifier` CLI
AMPLIFIER_AGENT_PROVIDER  AMPLIFIER_AGENT_MODEL  AMPLIFIER_AGENT_MODE
HARNESS_DEBUG=1      keep each assistant's raw stdout in the workspace
```

**Through a proxy, prefer a provider's native endpoint.** Pointing
`amplifier-agent` at litellm's OpenAI-compatible `/v1` fails the moment a tool is
used — the proxy has to translate tool results into Anthropic's shape and raises
`AnthropicException - 'tool_call_id'`. A plain question works, which is the worst
way for it to be broken. Its native Anthropic endpoint (`ANTHROPIC_BASE_URL`
without `/v1`) works with tools.

`HARNESS_DEBUG` is worth knowing about: every wrapper parses an output shape
nobody documents, and when a guess is wrong the only symptom is a strange note in
the register — by which point the output that would explain it is gone.

## Putting one on a new box

```bash
curl -fsSL https://raw.githubusercontent.com/payneio/docket/main/harness/install.sh | bash -s -- --harness claude-code
export DOCKET_URL=https://docket.example
export DOCKET_API_KEY=dke_…        # a ticket, from /board/access
docket-claude
```

`--harness` takes `claude-code`, `amplifier-agent`, `amplifier`, `pi`,
`opencode`, `echo` — **or a path or URL to a wrapper of your own**:

```bash
./install.sh --harness ./my-wrapper
curl -fsSL https://raw.githubusercontent.com/payneio/docket/main/harness/install.sh | bash -s -- --harness https://example.com/their-wrapper
```

A wrapper is an executable implementing the contract above, so installing a new
one is bundling a file rather than resolving a package. Somebody can ship a
wrapper for an assistant nobody here has heard of without touching this
repository, and it lands as the same single executable.

`--dir` chooses where (default `~/.local/bin`), `--name` what to call it,
`--list` shows what ships.

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
