# Shared by the runner and by wrappers that want it. Sourced, never executed.

#: Where the sources are, when there are sources. A bundled runner defines
#: `write_outcome_tool` instead and never reads this -- but without a default
#: here, running from a checkout silently had no tool to offer at all, and the
#: only sign was an MCP server that failed to start.
_HARNESS_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd 2>/dev/null)" || _HARNESS_LIB_DIR="."
: "${HARNESS_TOOL_SRC:=$_HARNESS_LIB_DIR/tools/report-outcome}"

: "${DOCKET_URL:=http://127.0.0.1:9006}"
: "${DK_CRED:=$HOME/.docket/credential}"

#: The credential, from the environment or from a file.
#:
#: `DOCKET_API_KEY` first, because a new box is one `export` away from working
#: and a file is a thing to create, own and forget. If it turns out to be a
#: ticket (`dke_`) it is spent for a real credential on first use -- so a single
#: variable either *is* the identity or *buys* it, and nothing has to know which
#: in advance.
docket_credential() {
  local key="${DOCKET_API_KEY:-}"
  if [ -z "$key" ] && [ -s "${DOCKET_CREDENTIAL_FILE:-$HOME/.docket/credential}" ]; then
    key="$(cat "${DOCKET_CREDENTIAL_FILE:-$HOME/.docket/credential}")"
  fi
  case "$key" in
    dke_*) key="$(docket_enrol "$key")" ;;
  esac
  printf '%s' "$key"
}

#: Spend a ticket, keep what comes back. Once, and then never again -- the
#: credential is written where the next run will find it without the ticket.
docket_enrol() {  # <ticket>
  local file out token
  file="${DOCKET_CREDENTIAL_FILE:-$HOME/.docket/credential}"
  if [ -s "$file" ]; then cat "$file"; return; fi
  out="$(curl -s -X POST "$DOCKET_URL/enrol" -H @<(bearer "$1") \
         -H 'Content-Type: application/json' \
         -d "$(jq -nc --arg l "${HARNESS_LABEL:-$(hostname -s)}" '{label:$l}')")"
  token="$(printf '%s' "$out" | jq -r '.token // empty')"
  if [ -z "$token" ]; then
    printf 'could not enrol: %s\n' "$out" >&2
    return 1
  fi
  mkdir -p "$(dirname "$file")" && chmod 700 "$(dirname "$file")"
  ( umask 077; printf '%s' "$token" > "$file" )
  printf 'enrolled as %s\n' "$(printf '%s' "$out" | jq -r .party)" >&2
  printf '%s' "$token"
}

#: An Authorization header, as a file for `curl -H @...`. Never as an argument:
#: argv is world-readable in /proc, so a token passed with `-H "...$KEY"` can be
#: read by every account on the box -- including the other principals the uid is
#: meant to keep it from. `printf` is a builtin, so the token is in no argv at all.
bearer() { printf 'Authorization: Bearer %s\n' "$1"; }

ts() { date -Is; }
log() { printf '%s %s\n' "$(ts)" "$*" >&2; }

#: Every call carries the credential; nothing else identifies us. Resolved once
#: per process, because enrolling twice would mint two credentials for one agent.
DOCKET_KEY=""
dk() {
  [ -z "$DOCKET_KEY" ] && DOCKET_KEY="$(docket_credential)"
  curl -s -H @<(bearer "$DOCKET_KEY") \
       -H 'Content-Type: application/json' "$@"
}

uri_escape() { printf %s "$1" | jq -sRr @uri; }

#: A directory name for a subject URI. Stable, readable, and collision-free
#: enough for a filesystem: the URI's own characters, with anything a path would
#: object to replaced. Two subjects that differ at all still differ here, because
#: nothing is truncated.
workspace_slug() { printf %s "$1" | tr -c 'a-zA-Z0-9._-' '-' | tr -s '-'; }

#: What a wrapper is told. The entry, the conversation so far, and a directory.
#: Deliberately not: the runner's configuration, the credential, or anything
#: about wild-life -- a wrapper that needed those would be one this contract
#: cannot swap out.
execution_request() {  # <entry-json> <workspace> <me>
  jq -n --argjson e "$1" --arg ws "$2" --arg me "$3" '{
    entry: {
      id: $e.id, subject: $e.subject, subject_label: $e.subject_label,
      terms: $e.terms, state: $e.state, requester: $e.requester,
      delegate: $e.delegate, expected_by: $e.expected_by
    },
    moves: [$e.moves[] | {seq, at, by, move, note}],
    workspace: $ws,
    me: $me
  }'
}

#: A serviceable prompt built from the request, for wrappers with nothing better.
#: Any wrapper is free to ignore this and write its own -- which is the point of
#: the prompt living here rather than in the runner, where it used to be one
#: string that assumed every assistant had the same tools.
default_prompt() {  # <request-json>
  printf '%s' "$1" | jq -r '
    "You have taken on the following, and are accountable for it.\n\n"
    + "  What is owed: " + (.entry.subject_label // .entry.subject) + "\n"
    + (if .entry.terms then "  Specifically:  " + .entry.terms + "\n" else "" end)
    + "  Reference:     " + .entry.subject + "\n"
    + "  Working dir:   " + .workspace + "\n\n"
    + (if (.moves | length) > 1 then
        "The conversation so far:\n"
        + ([.moves[] | "  " + .move + (if .note then " — " + .note else "" end)] | join("\n"))
        + "\n\n" else "" end)
    + "Do what you can now, in the working directory. Stop when you reach a\n"
    + "stable point rather than looping. If you need a decision from somebody,\n"
    + "say so plainly instead of guessing."'
}

#: The result, out of whatever the wrapper printed.
#:
#: The whole of stdout first, because `jq` pretty-prints by default and a wrapper
#: that forgets `-c` would otherwise have its result read as a bare `}`. Failing
#: that, the last line, so a wrapper may print freely and answer at the end.
result_of() {
  if printf '%s' "$1" | jq -e '.outcome' >/dev/null 2>&1; then printf '%s' "$1"; return; fi
  printf '%s' "$1" | tail -1
}

#: A well-formed refusal, so a wrapper that dies mid-run still says something the
#: register can hold. Silence is the failure the whole design is arranged against.
failed_result() { jq -nc --arg n "$1" '{outcome:"failed", note:$n}'; }

#: An outcome left by the `report_outcome` tool.
#:
#: The file is the tool's transport, not the assistant's contract -- an MCP
#: server is a subprocess and has no other way to hand an answer to the runner.
#: Assistants are asked for a block in their final response instead, because a
#: filesystem the runner can see is a thing not all of them have and a final
#: response is a thing all of them do.
#:
#: Consumed, not just read. A stale outcome from last week's run reported as this
#: run's would be worse than having none.
declared_outcome() {  # <workspace>
  local f="$1/.harness/outcome.json"
  [ -s "$f" ] || return 1
  if jq -e '.outcome | test("^(delivered|progressed|blocked|declined|failed)$")' "$f" >/dev/null 2>&1; then
    jq -c '{outcome, note: (.note // ""), question: (.question // "")}' "$f"
    rm -f "$f"
    return 0
  fi
  printf 'ignoring an outcome file that is not one: %s\n' "$(head -c 200 "$f")" >&2
  rm -f "$f"
  return 1
}

#: Put the outcome tool where the assistant's config can point at it.
#:
#: Written into the workspace rather than assumed to be beside us, because the
#: installed runner is one file and has no repository behind it. `write_tool` is
#: defined by the bundle; from a checkout the file is simply copied.
install_outcome_tool() {  # <workspace> -> path, or nothing
  local dest="$1/.harness/report-outcome"
  mkdir -p "$1/.harness" || return 1
  if declare -F write_outcome_tool >/dev/null 2>&1; then
    write_outcome_tool "$dest"
  elif [ -f "${HARNESS_TOOL_SRC:-}" ]; then
    cp -f "$HARNESS_TOOL_SRC" "$dest"
  else
    return 1
  fi
  # An unrunnable tool is worse than none: the assistant is offered a server
  # that fails to start, and nothing says why except a status nobody reads.
  chmod +x "$dest" 2>/dev/null || return 1
  [ -s "$dest" ] || return 1
  printf '%s' "$dest"
}

#: Validate and tidy an outcome object, or fail.
#:
#: Shared so that every channel is held to the same shape -- a `blocked` with no
#: question is as useless arriving in a response as it is in a tool call.
normalise_outcome() {  # <json>
  printf '%s' "$1" | jq -ce '
    select(.outcome | type == "string")
    | select(.outcome | test("^(delivered|progressed|blocked|declined|failed)$"))
    | {outcome, note: ((.note // "") | tostring), question: ((.question // "") | tostring)}
    | select(.outcome != "blocked" or .question != "")
  ' 2>/dev/null
}

#: The outcome the assistant stated in its final response.
#:
#: The one channel every assistant has. Not all of them can write a file the
#: runner will ever see -- a hosted agent, a sandboxed one, anything behind a
#: gateway -- but all of them answer, so this is the floor that the tool and the
#: file sit above rather than the other way round.
#:
#: Read as a fenced block tagged `outcome`, and failing that as a bare JSON
#: object on its own line. The **last** one wins, so an assistant that quotes the
#: format earlier while explaining itself is not taken at its example.
response_outcome() {  # <final response text>
  local text="$1" block line
  block="$(printf '%s\n' "$text" | awk '
    /^[[:space:]]*```[[:space:]]*outcome[[:space:]]*$/ { inb=1; buf=""; next }
    inb && /^[[:space:]]*```/                          { inb=0; last=buf; next }
    inb                                                { buf = buf $0 "\n" }
    END { printf "%s", last }')"
  if [ -n "$block" ]; then
    normalise_outcome "$block" && return 0
  fi
  line="$(printf '%s\n' "$text" | grep -oE '\{[^{}]*"outcome"[^{}]*\}' | tail -1)"
  [ -n "$line" ] && normalise_outcome "$line" && return 0
  return 1
}

#: Keep an assistant's raw stdout when HARNESS_DEBUG is set.
#:
#: Every wrapper guesses at an undocumented output shape, and when a guess is
#: wrong the only symptom is a strange note in the register -- by which point the
#: output that would explain it is gone. This keeps it.
debug_dump() {  # <workspace> <name> <text>
  [ -n "${HARNESS_DEBUG:-}" ] || return 0
  mkdir -p "$1/.harness/raw" 2>/dev/null || return 0
  printf '%s' "$3" > "$1/.harness/raw/$2"
  printf 'raw %s kept at %s/.harness/raw/%s\n' "$2" "$1" "$2" >&2
}

#: Block until there is plausibly something to do, then return.
#:
#: `GET /stream` is Server-Sent Events, already filtered server-side to moves on
#: conversations this credential is named on -- so *any* move that arrives is
#: worth a tick, with one exception: our own. The runner posts a move at the end
#: of nearly every pass, and waking on that would be a loop with no sleep in it.
#: The stream's `open` frame says which party we are, so the filter needs no
#: extra call to find out.
#:
#: **It returns on a timeout too, and that is not a fallback.** Staleness is the
#: *absence* of events: nothing will ever be published to say a delegate has gone
#: quiet for three days. So a sweep on our own clock is still required, and this
#: is where it comes from -- the stream turns it from every sixty seconds into
#: every fifteen minutes, which is the whole of what it buys.
#:
#: Returns 0 when it waited properly (event or sweep), 1 when the stream could
#: not be followed at all. A caller that cannot reach the stream must fall back
#: to sleeping, never to stopping.
wait_for_work() {
  local sweep="${HARNESS_SWEEP_SECONDS:-900}" me="" ev="" line data by opened=""
  [ -z "$DOCKET_KEY" ] && DOCKET_KEY="$(docket_credential)"

  exec 3< <(curl -sN --max-time "$sweep" \
              -H @<(bearer "$DOCKET_KEY") \
              -H 'Accept: text/event-stream' "$DOCKET_URL/stream" 2>/dev/null)
  local pid=$!

  while IFS= read -r line <&3; do
    case "$line" in
      "event: "*) ev="${line#event: }" ;;
      ": "*)      ;;                       # heartbeat: still connected, nothing to report
      "data: "*)
        data="${line#data: }"
        if [ "$ev" = "open" ]; then
          me="$(printf '%s' "$data" | jq -r '.party // empty' 2>/dev/null)"
          opened=1
        elif [ "$ev" = "move" ]; then
          by="$(printf '%s' "$data" | jq -r '.by // empty' 2>/dev/null)"
          if [ -n "$by" ] && [ "$by" != "$me" ]; then
            exec 3<&-; kill "$pid" 2>/dev/null
            return 0
          fi
        fi ;;
    esac
  done
  exec 3<&-; kill "$pid" 2>/dev/null
  # Opened and then ended = the sweep timer elapsed, which is a proper wait.
  # Never opened = there is no stream to follow, and the caller must know.
  [ -n "$opened" ] && return 0 || return 1
}
