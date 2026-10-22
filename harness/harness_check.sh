#!/usr/bin/env bash
# Drive the runner through every outcome, against a real docket.
#
# Uses `wrappers/echo`, so the whole loop is exercised with no model, no API key
# and no waiting -- which is the point of the wrapper contract being a subprocess
# and a JSON shape. Runs against `docket_test` on its own port, so it is safe
# beside a live service, and refuses to start if it is pointed at the register.
# Checks the checkout it lives in, so a worktree tests its own code;
# `DOCKET_TEST_DATABASE_URL` and `HARNESS_CHECK_PORT` move it out of the way of
# another checkout running the same thing.
#
#   ./harness/harness_check.sh
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; export ROOT
cd "$ROOT"
PROD="$(grep -oP '(?<=^DOCKET_DATABASE_URL=).*' .env | tr -d "'\"")"
export DOCKET_DATABASE_URL="${DOCKET_TEST_DATABASE_URL:-${PROD%/*}/docket_test}"
[ "$DOCKET_DATABASE_URL" = "$PROD" ] && { echo "refusing to run against the register"; exit 2; }
PORT="${HARNESS_CHECK_PORT:-19007}"
export DOCKET_TOKEN=harness-check-token DOCKET_PORT=$PORT
export DOCKET_OWNER_PARTY="wild-life:person:aaaaaaaa-0000-0000-0000-000000000001"
uv run python -c "
import os, psycopg
u=os.environ['DOCKET_DATABASE_URL'].replace('+asyncpg','')
h,_,n=u.rpartition('/')
with psycopg.connect(f'{h}/postgres') as c:
    c.autocommit=True
    if not c.execute('SELECT 1 FROM pg_database WHERE datname=%s',(n,)).fetchone():
        c.execute(f'CREATE DATABASE \"{n}\"')" || exit 1
uv run alembic upgrade head >/dev/null 2>&1

uv run docket & SRV=$!
trap 'kill $SRV 2>/dev/null' EXIT
D=http://127.0.0.1:$PORT
for _ in $(seq 60); do curl -sf $D/health >/dev/null 2>&1 && break; sleep 0.25; done
curl -sf $D/health >/dev/null 2>&1 || { echo "server never came up on $D"; exit 1; }

A="Authorization: Bearer $DOCKET_TOKEN"
AGENT="wild-life:person:bbbbbbbb-0000-0000-0000-000000000002"
CRED=$(curl -s -X POST $D/credentials -H "$A" -H 'Content-Type: application/json' \
       -d "{\"party\":\"$AGENT\",\"label\":\"harness check\"}" | jq -r '.token // empty')
[ -z "$CRED" ] && { echo "could not issue a credential"; exit 1; }

export HARNESS_STATE=$(mktemp -d) HARNESS_WORKSPACES=$(mktemp -d)
export DOCKET_URL=$D DOCKET_CREDENTIAL_FILE="$HARNESS_STATE/cred"
printf '%s' "$CRED" > "$DOCKET_CREDENTIAL_FILE"
export HARNESS_WRAPPER=$ROOT/harness/wrappers/echo
# Spans are exercised in their own section, against a sink of our own. Nowhere
# else in this script may they reach whatever might be listening on 4318.
export OTEL_TRACES_EXPORTER=none
export CRED

fail=0
ok(){ printf '  ok   %s\n' "$*"; }
bad(){ printf '  FAIL %s\n' "$*"; fail=1; }
offer(){ curl -s -X POST $D/entries -H "$A" -H 'Content-Type: application/json' \
  -d "{\"subject\":\"test:mark:task:$1\",\"delegate\":\"$AGENT\",\"terms\":\"$2\"}" | jq -r .id; }
state(){ curl -s "$D/entries/${1:?}" -H "$A" | jq -r .state; }
lastnote(){ curl -s "$D/entries/${1:?}" -H "$A" | jq -r '.moves|last|.note'; }
moves(){ curl -s "$D/entries/${1:?}" -H "$A" | jq -r '[.moves[].move]|join(",")'; }
#: The trace ids of an entry's moves after the offer, de-duplicated: one id
#: means every move the runner made on it was one run.
runs(){ curl -s "$D/entries/${1:?}" -H "$A" | jq -r '[.moves[1:][].trace_id]|unique|join(",")'; }

echo "== a wrapper that works: offered -> accepted, and only because it worked"
E=$(offer one "do the thing")
HARNESS_ECHO_OUTCOME=progressed ./harness/runner >/dev/null 2>&1
[ "$(moves $E)" = "offer,accept,update" ] && ok "accept then update ($(moves $E))" || bad "moves: $(moves $E)"

echo "== the workspace belongs to the subject and persists"
WS="$HARNESS_WORKSPACES/$(printf 'test:mark:task:one' | tr -c 'a-zA-Z0-9._-' '-' | tr -s '-')"
[ -d "$WS/.harness/echo" ] && ok "workspace kept at $(basename $WS)" || bad "no workspace: $WS"

echo "== it does not act twice while the ball is in the other court"
HARNESS_ECHO_OUTCOME=progressed ./harness/runner >/dev/null 2>&1
[ "$(moves $E)" = "offer,accept,update" ] && ok "second tick did nothing" || bad "acted again: $(moves $E)"

echo "== a refusal is expressible, and nothing was promised first"
E2=$(offer two "not my kind of thing")
HARNESS_ECHO_OUTCOME=declined ./harness/runner >/dev/null 2>&1
[ "$(moves $E2)" = "offer,decline" ] && ok "declined without accepting ($(moves $E2))" || bad "moves: $(moves $E2)"

echo "== blocked opens a question and links it"
E3=$(offer three "needs a decision")
HARNESS_ECHO_OUTCOME=blocked ./harness/runner >/dev/null 2>&1
B=$(curl -s $D/entries/$E3 -H "$A")
[ "$(printf '%s' "$B" | jq -r .is_blocked)" = "true" ] && ok "blocked_on set, derived true" || bad "not blocked"
Q=$(curl -s "$D/entries?subject=test:mark:task:three" -H "$A" | jq -r '[.[]|select(.delegate=="'"$DOCKET_OWNER_PARTY"'")]|first|.terms')
[ "$Q" = "Which of the two approaches did you want?" ] && ok "question offered back to the requester" || bad "question: $Q"

echo "== a pass is one run: its moves, and the question it opened"
R3="$(runs $E3)"
QR=$(curl -s "$D/entries?subject=test:mark:task:three" -H "$A" | jq -r '[.[]|select(.delegate=="'"$DOCKET_OWNER_PARTY"'")]|first|.moves[0].trace_id')
[[ "$R3" =~ ^[0-9a-f]{32}$ ]] && ok "accept and update share one trace" || bad "traces: '$R3'"
[ "$QR" = "$R3" ] && ok "the question was opened in the same run" || bad "question trace: $QR vs $R3"
[ "$(runs $E)" != "$R3" ] && ok "another pass is another run" || bad "two passes shared a trace"

echo "== two entries in one pass are one run, and an inherited trace is not reused"
EA=$(offer ten "first of two"); EB=$(offer eleven "second of two")
TRACEPARENT="00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01" \
  HARNESS_ECHO_OUTCOME=progressed ./harness/runner >/dev/null 2>&1
RA="$(runs $EA)"; RB="$(runs $EB)"
[ -n "$RA" ] && [ "$RA" = "$RB" ] && ok "both entries in one run" || bad "traces: $RA / $RB"
[ "$RA" != "0af7651916cd43dd8448eb211c80319c" ] && ok "a fresh trace, not the loop's" || bad "reused the inherited trace"

echo "== the wrapper is handed the run, as TRACEPARENT"
EC=$(offer twelve "carry the context")
cat > "$HARNESS_STATE/carrier" <<'W'
#!/usr/bin/env bash
cat >/dev/null
printf '%s' "${TRACEPARENT:-}" > "$HARNESS_STATE/seen-traceparent"
jq -nc '{outcome:"progressed",note:"looked"}'
W
chmod +x "$HARNESS_STATE/carrier"
HARNESS_WRAPPER="$HARNESS_STATE/carrier" ./harness/runner >/dev/null 2>&1
SEEN="$(cat "$HARNESS_STATE/seen-traceparent" 2>/dev/null)"
[ "$(printf '%s' "$SEEN" | cut -d- -f2)" = "$(runs $EC)" ] && ok "wrapper's context is the entry's run" || bad "wrapper saw '$SEEN', moves $(runs $EC)"

echo "== a pass sends its spans: pass, entry, wrapper, one trace, no content"
SINK=$((PORT + 100)); mkdir -p "$HARNESS_STATE/otlp"
cat > "$HARNESS_STATE/sink.py" <<'PY'
import http.server, os, sys, time
out, slow = sys.argv[2], sys.argv[3] == "slow"
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        if slow: time.sleep(20)
        open(os.path.join(out, f"{time.time_ns()}.json"), "wb").write(body)
        self.send_response(200); self.end_headers()
    def log_message(self, *a): pass
http.server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
PY
python3 "$HARNESS_STATE/sink.py" $SINK "$HARNESS_STATE/otlp" fast & SINKPID=$!; sleep 0.5
ES=$(offer thirteen "SPAN-SECRET-TERMS")
OTEL_TRACES_EXPORTER=otlp OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:$SINK OTEL_SERVICE_NAME=agent.check \
  HARNESS_WRAPPER="$HARNESS_STATE/carrier" ./harness/runner >/dev/null 2>&1
for _ in $(seq 20); do ls "$HARNESS_STATE/otlp"/*.json >/dev/null 2>&1 && break; sleep 0.25; done
kill $SINKPID 2>/dev/null
P="$(cat "$HARNESS_STATE"/otlp/*.json 2>/dev/null)"
S="$(printf '%s' "$P" | jq -c '[.resourceSpans[].scopeSpans[].spans[]]')"
name(){ printf '%s' "$S" | jq -r --arg n "$1" '.[]|select(.name==$n)|'"$2"; }
[ "$(printf '%s' "$S" | jq -r '[.[].name]|sort|join(",")')" = "harness.entry,harness.pass,harness.wrapper" ] \
  && ok "three spans: pass, entry, wrapper" || bad "spans: $(printf '%s' "$S" | jq -r '[.[].name]|join(",")')"
[ "$(printf '%s' "$S" | jq -r '[.[].traceId]|unique|join(",")')" = "$(runs $ES)" ] \
  && ok "in the trace docket recorded for the moves" || bad "trace mismatch"
[ "$(name harness.entry .parentSpanId)" = "$(name harness.pass .spanId)" ] \
  && [ "$(name harness.wrapper .parentSpanId)" = "$(name harness.entry .spanId)" ] \
  && ok "wrapper under entry under pass" || bad "parents wrong"
[ "$(cut -d- -f3 "$HARNESS_STATE/seen-traceparent")" = "$(name harness.wrapper .spanId)" ] \
  && ok "the wrapper's TRACEPARENT is the wrapper span" || bad "wrapper context: $(cat "$HARNESS_STATE/seen-traceparent")"
printf '%s' "$P" | jq -e '.resourceSpans[0].resource.attributes[]|select(.key=="service.name" and .value.stringValue=="agent.check")' >/dev/null \
  && ok "named by OTEL_SERVICE_NAME" || bad "service.name missing"
printf '%s' "$P" | grep -q "SPAN-SECRET-TERMS" && bad "terms leaked into a span" || ok "no terms in any span"

echo "== a wedged collector does not hold a pass up"
rm -f "$HARNESS_STATE"/otlp/*.json
python3 "$HARNESS_STATE/sink.py" $SINK "$HARNESS_STATE/otlp" slow & SINKPID=$!; sleep 0.5
EW2=$(offer fourteen "collector is stuck")
T0=$(date +%s)
OTEL_TRACES_EXPORTER=otlp OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:$SINK \
  HARNESS_ECHO_OUTCOME=progressed ./harness/runner >/dev/null 2>&1
T1=$(date +%s); kill $SINKPID 2>/dev/null
[ $((T1 - T0)) -le 4 ] && [ "$(moves $EW2)" = "offer,accept,update" ] \
  && ok "pass finished in $((T1 - T0))s with the collector wedged" || bad "took $((T1 - T0))s"

echo "== a wrapper that dies still says something"
E4=$(offer four "will explode")
HARNESS_ECHO_OUTCOME=failed ./harness/runner >/dev/null 2>&1
[ "$(state $E4)" = "offered" ] && ok "stayed offered, not promised" || bad "state: $(state $E4)"
printf '%s' "$(lastnote $E4)" | grep -q "exited 3" && ok "reported: $(lastnote $E4)" || bad "note: $(lastnote $E4)"

echo "== a verifier can refuse a claim of completion"
E5=$(offer five "claims done")
export HARNESS_VERIFIER=/tmp/nope-verifier
printf '#!/bin/sh\ncat >/dev/null\necho "the world disagrees"\nexit 1\n' > $HARNESS_VERIFIER; chmod +x $HARNESS_VERIFIER
HARNESS_ECHO_OUTCOME=delivered ./harness/runner >/dev/null 2>&1
[ "$(state $E5)" = "accepted" ] && ok "not delivered on the wrapper's word alone" || bad "state: $(state $E5)"
printf '%s' "$(lastnote $E5)" | grep -q "world disagrees" && ok "carried the reason" || bad "note: $(lastnote $E5)"
unset HARNESS_VERIFIER

echo "== the assistant is told how to declare an outcome"
[ -f "$WS/AGENTS.md" ] && [ -f "$WS/CLAUDE.md" ] && ok "instructions in the workspace" || bad "no AGENTS.md in $WS"
grep -q '```outcome' "$WS/AGENTS.md" && ok "and they ask for a block in the reply" || bad "instructions say nothing about it"
grep -q "report_outcome" "$WS/AGENTS.md" && ok "mentioning the tool for those that have it" || bad "no mention of the tool"

echo "== a declared outcome is taken over a guess"
E7=$(offer seven "will declare")
W7="$HARNESS_WORKSPACES/$(printf 'test:mark:task:seven' | tr -c 'a-zA-Z0-9._-' '-' | tr -s '-')"
mkdir -p "$W7/.harness"
cat > "$HARNESS_STATE/declaring" <<'W'
#!/usr/bin/env bash
. "$ROOT/harness/lib.sh"
REQ="$(cat)"; WS="$(printf '%s' "$REQ" | jq -r .workspace)"
mkdir -p "$WS/.harness"
printf '{"outcome":"declined","note":"not mine to do"}' > "$WS/.harness/outcome.json"
if D="$(declared_outcome "$WS")"; then printf '%s' "$D"; else jq -nc '{outcome:"progressed",note:"guessed"}'; fi
W
chmod +x "$HARNESS_STATE/declaring"
HARNESS_WRAPPER="$HARNESS_STATE/declaring" ./harness/runner >/dev/null 2>&1
[ "$(moves $E7)" = "offer,decline" ] && ok "declined because it said so" || bad "moves: $(moves $E7)"
[ ! -f "$W7/.harness/outcome.json" ] && ok "outcome consumed, not left to be reread" || bad "outcome file survived"

echo "== an assistant that cannot write files is still heard"
E9=$(offer nine "will answer in prose only")
cat > "$HARNESS_STATE/talker" <<'W'
#!/usr/bin/env bash
. "$ROOT/harness/lib.sh"
REQ="$(cat)"
# Never touches the filesystem. All it does is answer -- like a hosted assistant
# with no disk the runner can see.
REPLY='Looked at it. Here is the shape you asked for, e.g.
{"outcome":"progressed","note":"an example, not the answer"}

I could not finish.

```outcome
{"outcome":"blocked","note":"Two ways to do it.","question":"Cursor or offset?"}
```'
if SAID="$(response_outcome "$REPLY")"; then printf '%s' "$SAID"
else jq -nc '{outcome:"failed",note:"could not be read"}'; fi
W
chmod +x "$HARNESS_STATE/talker"
HARNESS_WRAPPER="$HARNESS_STATE/talker" ./harness/runner >/dev/null 2>&1
B9=$(curl -s $D/entries/$E9 -H "$A")
[ "$(printf '%s' "$B9" | jq -r .is_blocked)" = "true" ] && ok "blocked, read from its reply alone" || bad "not blocked: $(moves $E9)"
Q9=$(curl -s "$D/entries?subject=test:mark:task:nine" -H "$A" | jq -r '[.[]|select(.delegate=="'"$DOCKET_OWNER_PARTY"'")]|first|.terms')
[ "$Q9" = "Cursor or offset?" ] && ok "and the real question was asked, not the example" || bad "question: $Q9"

echo "== a ticket in DOCKET_API_KEY buys a credential"
TICKET=$(curl -s -X POST $D/enrolments -H "$A" -H 'Content-Type: application/json' \
  -d "{\"party\":\"$AGENT\",\"label\":\"by ticket\"}" | jq -r .token)
E8=$(offer eight "via a ticket")
NEWCRED="$HARNESS_STATE/fresh-box"
( unset DOCKET_CREDENTIAL_FILE
  DOCKET_API_KEY="$TICKET" DOCKET_CREDENTIAL_FILE="$NEWCRED" \
  HARNESS_ECHO_OUTCOME=progressed ./harness/runner >/dev/null 2>&1 )
[ -s "$NEWCRED" ] && grep -q '^dk_' "$NEWCRED" && ok "enrolled and saved the credential" || bad "no credential written"
[ "$(moves $E8)" = "offer,accept,update" ] && ok "and worked in the same run" || bad "moves: $(moves $E8)"

echo "== --wait wakes on somebody else's move, not on its own"
( ./harness/runner --wait; echo "rc $?" > "$HARNESS_STATE/w1" ) & W=$!; sleep 2
WS_START=$(date +%s)
EW=$(offer waiter "wake up")
wait $W 2>/dev/null; WS_END=$(date +%s)
[ "$(cat "$HARNESS_STATE/w1" 2>/dev/null)" = "rc 0" ] && [ $((WS_END-WS_START)) -le 4 ] \
  && ok "woke in $((WS_END-WS_START))s on an offer" || bad "did not wake: $(cat "$HARNESS_STATE/w1" 2>/dev/null)"

HARNESS_SWEEP_SECONDS=8 ./harness/runner --wait & W=$!; sleep 2
WS_START=$(date +%s)
curl -s -o /dev/null -X POST $D/entries/$EW/moves -H "Authorization: Bearer $CRED" \
  -H 'Content-Type: application/json' -d '{"move":"update","note":"my own move"}'
wait $W 2>/dev/null; WS_END=$(date +%s)
[ $((WS_END-WS_START)) -ge 5 ] && ok "ignored its own move" || bad "woke on its own move"

WS_START=$(date +%s); HARNESS_SWEEP_SECONDS=6 ./harness/runner --wait; RCW=$?; WS_END=$(date +%s)
[ $RCW -eq 0 ] && [ $((WS_END-WS_START)) -ge 5 ] && ok "the sweep still fires with no events" || bad "sweep: rc=$RCW"

WS_START=$(date +%s); DOCKET_URL=http://127.0.0.1:19299 ./harness/runner --wait >/dev/null 2>&1; RCW=$?
[ $RCW -ne 0 ] && ok "an unreachable stream is reported, not waited on" || bad "pretended to wait"

echo "== and delivers when nothing disagrees"
E6=$(offer six "really done")
HARNESS_ECHO_OUTCOME=delivered ./harness/runner >/dev/null 2>&1
[ "$(state $E6)" = "delivered" ] && ok "delivered" || bad "state: $(state $E6)"

kill $SRV 2>/dev/null; wait $SRV 2>/dev/null
uv run python -c "
import asyncio
from sqlalchemy import text
from docket.db.session import engine
async def go():
    async with engine.begin() as c: await c.execute(text('DROP SCHEMA IF EXISTS docket CASCADE'))
asyncio.run(go())" 2>/dev/null
rm -rf "$HARNESS_STATE" "$HARNESS_WORKSPACES" /tmp/nope-verifier
[ $fail = 0 ] && echo "PASS" || echo "FAILED"; exit $fail
