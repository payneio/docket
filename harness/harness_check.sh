#!/usr/bin/env bash
# Drive the runner through every outcome, against a real docket.
#
# Uses `wrappers/echo`, so the whole loop is exercised with no model, no API key
# and no waiting -- which is the point of the wrapper contract being a subprocess
# and a JSON shape. Runs against `docket_test` on its own port, so it is safe
# beside a live service, and refuses to start if it is pointed at the register.
#
#   ./harness/harness_check.sh
set -uo pipefail
cd /data/repos/docket
PROD="$(grep -oP '(?<=^DOCKET_DATABASE_URL=).*' .env)"
export DOCKET_DATABASE_URL="${PROD%/*}/docket_test"
[ "$DOCKET_DATABASE_URL" = "$PROD" ] && { echo "refusing to run against the register"; exit 2; }
export DOCKET_TOKEN=harness-check-token DOCKET_PORT=19007
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
D=http://127.0.0.1:19007
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
export HARNESS_WRAPPER=/data/repos/docket/harness/wrappers/echo
export CRED

fail=0
ok(){ printf '  ok   %s\n' "$*"; }
bad(){ printf '  FAIL %s\n' "$*"; fail=1; }
offer(){ curl -s -X POST $D/entries -H "$A" -H 'Content-Type: application/json' \
  -d "{\"subject\":\"test:mark:task:$1\",\"delegate\":\"$AGENT\",\"terms\":\"$2\"}" | jq -r .id; }
state(){ curl -s "$D/entries/${1:?}" -H "$A" | jq -r .state; }
lastnote(){ curl -s "$D/entries/${1:?}" -H "$A" | jq -r '.moves|last|.note'; }
moves(){ curl -s "$D/entries/${1:?}" -H "$A" | jq -r '[.moves[].move]|join(",")'; }

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
. /data/repos/docket/harness/lib.sh
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
. /data/repos/docket/harness/lib.sh
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
