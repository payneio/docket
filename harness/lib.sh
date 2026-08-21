# Shared by the runner and by wrappers that want it. Sourced, never executed.

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
  out="$(curl -s -X POST "$DOCKET_URL/enrol" -H "Authorization: Bearer $1" \
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

ts() { date -Is; }
log() { printf '%s %s\n' "$(ts)" "$*" >&2; }

#: Every call carries the credential; nothing else identifies us. Resolved once
#: per process, because enrolling twice would mint two credentials for one agent.
DOCKET_KEY=""
dk() {
  [ -z "$DOCKET_KEY" ] && DOCKET_KEY="$(docket_credential)"
  curl -s -H "Authorization: Bearer $DOCKET_KEY" \
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

#: What the assistant said its own outcome was, if it said.
#:
#: `AGENTS.md` tells it to write `.harness/outcome.json` before stopping. That is
#: worth far more than reading its prose for the word "blocked": an assistant
#: asked to state an outcome states one, and an assistant guessed at is guessed
#: at wrong eventually. Every one of these tools can write a file, which is the
#: only capability this convention assumes.
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
  chmod +x "$dest" && printf '%s' "$dest"
}
