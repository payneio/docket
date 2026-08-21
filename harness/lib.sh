# Shared by the runner and by wrappers that want it. Sourced, never executed.

: "${DOCKET_URL:=http://127.0.0.1:9006}"
: "${DK_CRED:=$HOME/.docket/credential}"

ts() { date -Is; }
log() { printf '%s %s\n' "$(ts)" "$*" >&2; }

#: Every call carries the credential; nothing else identifies us.
dk() {
  curl -s -H "Authorization: Bearer $(cat "$DK_CRED" 2>/dev/null)" \
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
