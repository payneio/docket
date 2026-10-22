#!/usr/bin/env bash
# Install like a new box would, then drive the installed artifact against docket.
#
# The bundle is a generated file, which is exactly the kind of thing that passes
# review and then does not run. This runs it: `env -i` so it inherits nothing
# from here, a temporary HOME so it cannot find anybody else's credential, and a
# ticket as its only input.
#
# Checks the checkout it lives in; `DOCKET_TEST_DATABASE_URL` and
# `INSTALLED_CHECK_PORT` move it out of another checkout's way.
#
#   ./harness/installed_check.sh
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
PROD="$(grep -oP '(?<=^DOCKET_DATABASE_URL=).*' .env | tr -d "'\"")"
export DOCKET_DATABASE_URL="${DOCKET_TEST_DATABASE_URL:-${PROD%/*}/docket_test}"
[ "$DOCKET_DATABASE_URL" = "$PROD" ] && { echo "refusing"; exit 2; }
PORT="${INSTALLED_CHECK_PORT:-19008}"
export DOCKET_TOKEN=installed-check DOCKET_PORT=$PORT
export DOCKET_OWNER_PARTY="wild-life:person:cccccccc-0000-0000-0000-000000000003"
uv run alembic upgrade head >/dev/null 2>&1
uv run docket & SRV=$!; trap 'kill $SRV 2>/dev/null' EXIT
D=http://127.0.0.1:$PORT
for _ in $(seq 60); do curl -sf $D/health >/dev/null 2>&1 && break; sleep 0.25; done
A="Authorization: Bearer $DOCKET_TOKEN"
AGENT="wild-life:person:dddddddd-0000-0000-0000-000000000004"

fail=0; ok(){ printf '  ok   %s\n' "$*"; }; bad(){ printf '  FAIL %s\n' "$*"; fail=1; }

BIN=$(mktemp -d); HOMEDIR=$(mktemp -d); WSDIR=$(mktemp -d)
echo "== install, the way a new box would"
./harness/install.sh --harness echo --dir "$BIN" >/dev/null 2>&1
[ -x "$BIN/docket-echo" ] && ok "one executable, $(wc -c < "$BIN/docket-echo") bytes" || bad "nothing installed"

echo "== a ticket is the only thing it is given"
TICKET=$(curl -s -X POST $D/enrolments -H "$A" -H 'Content-Type: application/json' \
  -d "{\"party\":\"$AGENT\",\"label\":\"a new box\"}" | jq -r .token)
EID=$(curl -s -X POST $D/entries -H "$A" -H 'Content-Type: application/json' \
  -d "{\"subject\":\"test:mark:task:newbox\",\"delegate\":\"$AGENT\",\"terms\":\"prove the install\"}" | jq -r .id)

env -i PATH="$PATH" HOME="$HOMEDIR" \
    DOCKET_URL=$D DOCKET_API_KEY="$TICKET" \
    HARNESS_WORKSPACES="$WSDIR" HARNESS_STATE="$HOMEDIR/.harness" \
    HARNESS_ECHO_OUTCOME=progressed \
    "$BIN/docket-echo" >/tmp/installed.log 2>&1
grep -q "enrolled as" /tmp/installed.log && ok "enrolled from the ticket" || bad "did not enrol: $(tail -2 /tmp/installed.log)"
[ -s "$HOMEDIR/.docket/credential" ] && ok "credential saved for next time" || bad "no credential kept"

MV=$(curl -s $D/entries/$EID -H "$A" | jq -r '[.moves[].move]|join(",")')
[ "$MV" = "offer,accept,update" ] && ok "accepted and reported ($MV)" || bad "moves: $MV"
TR=$(curl -s $D/entries/$EID -H "$A" | jq -r '[.moves[1:][].trace_id]|unique|join(",")')
[[ "$TR" =~ ^[0-9a-f]{32}$ ]] && ok "and said which run it was" || bad "traces: '$TR'"

WS="$WSDIR/test-mark-task-newbox"
[ -f "$WS/AGENTS.md" ] && ok "instructions written into the workspace" || bad "no AGENTS.md"
grep -q '```outcome' "$WS/AGENTS.md" && ok "carried inside the single file" || bad "instructions empty"
# The floor has to be in the bundle, or an assistant with no filesystem the
# runner can see would have no way to answer from an installed box.
grep -q "response_outcome()" "$BIN/docket-echo" && ok "and the reply parser came with it" || bad "no response_outcome in the bundle"
grep -q "write_outcome_tool()" "$BIN/docket-echo" && ok "and the MCP tool" || bad "no tool in the bundle"

echo "== second run reuses the saved credential, ticket already spent"
env -i PATH="$PATH" HOME="$HOMEDIR" DOCKET_URL=$D \
    HARNESS_WORKSPACES="$WSDIR" HARNESS_STATE="$HOMEDIR/.harness" \
    "$BIN/docket-echo" >/tmp/installed2.log 2>&1
grep -q "cannot reach docket" /tmp/installed2.log && bad "lost its identity" || ok "still itself with no DOCKET_API_KEY"
[ "$(curl -s $D/credentials -H "$A" | jq 'length')" = "1" ] && ok "one credential, not one per run" || bad "minted extras"

kill $SRV 2>/dev/null; wait $SRV 2>/dev/null
uv run python -c "
import asyncio
from sqlalchemy import text
from docket.db.session import engine
async def go():
    async with engine.begin() as c: await c.execute(text('DROP SCHEMA IF EXISTS docket CASCADE'))
asyncio.run(go())" 2>/dev/null
rm -rf "$BIN" "$HOMEDIR" "$WSDIR"
[ $fail = 0 ] && echo "PASS" || echo "FAILED"; exit $fail
