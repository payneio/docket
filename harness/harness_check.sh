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
export DOCKET_URL=$D DK_CRED="$HARNESS_STATE/cred"
printf '%s' "$CRED" > "$DK_CRED"
export HARNESS_WRAPPER=/data/repos/docket/harness/wrappers/echo

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
