#!/usr/bin/env bash
# Prove the kernel identifies the caller.
#
# The pytest suite exercises the identity path by putting ("uid", n) in the ASGI
# scope, which is what peercred.PeerCredProtocol puts there. This checks the part
# that stubs cannot: that a *real* process, owned by a *different* uid, arriving
# over a *real* unix socket, is identified without presenting any credential.
#
#   ./tests/peercred_check.sh [uid]        (default 40001, fleetix's researcher)
set -uo pipefail
UID_UNDER_TEST="${1:-40001}"
WHO="$(id -nu "$UID_UNDER_TEST")"
SOCK="/tmp/docket-peercred-$$.sock"
PARTY="wild-life:person:7e1f8dc0-3cb0-468a-9633-f422a04237f8"
SUBJ="test:mark:task:peercred-$$"
TOKEN="$(grep -oP '(?<=^DOCKET_TOKEN=).*' .env 2>/dev/null || echo dev-token)"
fail=0
ok()   { printf '  ok   %s\n' "$*"; }
bad()  { printf '  FAIL %s\n' "$*"; fail=1; }
owner(){ curl -s --unix-socket "$SOCK" -H "Authorization: Bearer $TOKEN" "$@"; }
them() { sudo -u "$WHO" curl -s --unix-socket "$SOCK" "$@"; }

DOCKET_UDS="$SOCK" uv run docket &
SRV=$!
trap 'kill $SRV 2>/dev/null; rm -f "$SOCK"' EXIT
for _ in $(seq 40); do [ -S "$SOCK" ] && break; sleep 0.1; done
[ -S "$SOCK" ] || { echo "server never bound $SOCK"; exit 1; }
chmod 666 "$SOCK"

echo "== $WHO (uid $UID_UNDER_TEST) presents no credential at all"
owner -o /dev/null -X PUT "http://d/identities/$UID_UNDER_TEST" \
  -H 'Content-Type: application/json' -d "{\"uid\":$UID_UNDER_TEST,\"party\":\"$PARTY\"}"
me="$(them http://d/me)"
echo "$me" | grep -q '"via":"peercred"' && ok "identified via peercred" || bad "not peercred: $me"
echo "$me" | grep -q "\"uid\":$UID_UNDER_TEST" && ok "uid $UID_UNDER_TEST" || bad "wrong uid: $me"
echo "$me" | grep -q '"attested":true' && ok "attested" || bad "not attested: $me"

echo "== a registered uid cannot be overridden by a token"
spoof="$(sudo -u "$WHO" curl -s --unix-socket "$SOCK" -H "Authorization: Bearer $TOKEN" http://d/me)"
echo "$spoof" | grep -q "\"uid\":$UID_UNDER_TEST" \
  && ok "kernel wins over a valid bearer token" || bad "token overrode peercred: $spoof"

echo "== the loop, across two real principals"
eid="$(owner -X POST http://d/entries -H 'Content-Type: application/json' \
  -d "{\"subject\":\"$SUBJ\",\"delegate\":\"$PARTY\",\"terms\":\"peercred check\"}" \
  | grep -oP '(?<="id":")[^"]+' | head -1)"
[ -n "$eid" ] && ok "offered ($eid)" || bad "no entry created"
them -o /dev/null -X POST "http://d/entries/$eid/moves" -H 'Content-Type: application/json' -d '{"move":"accept"}'
them -o /dev/null -X POST "http://d/entries/$eid/moves" -H 'Content-Type: application/json' -d '{"move":"deliver"}'
owner -o /dev/null -X POST "http://d/entries/$eid/moves" -H 'Content-Type: application/json' -d '{"move":"settle"}'
final="$(owner "http://d/entries/$eid")"
echo "$final" | grep -q '"state":"settled"' && ok "settled" || bad "not settled: $final"
echo "$final" | grep -q "\"by\":\"$PARTY\"" && ok "moves attributed to the uid that made them" || bad "attribution lost"

echo "== a decline leaves the subject offerable"
d="$(owner -X POST http://d/entries -H 'Content-Type: application/json' \
  -d "{\"subject\":\"$SUBJ\",\"delegate\":\"$PARTY\"}" | grep -oP '(?<="id":")[^"]+' | head -1)"
them -o /dev/null -X POST "http://d/entries/$d/moves" -H 'Content-Type: application/json' -d '{"move":"decline"}'
owner -o /dev/null -X POST http://d/entries -H 'Content-Type: application/json' \
  -d "{\"subject\":\"$SUBJ\",\"delegate\":\"mailto:next@example.invalid\"}"
n="$(owner "http://d/entries?subject=$SUBJ" | grep -o '"state":"offered"' | wc -l)"
[ "$n" = "1" ] && ok "still owed, re-offered to someone else" || bad "expected 1 open offer, got $n"

owner -o /dev/null -X DELETE "http://d/identities/$UID_UNDER_TEST"
uv run python -c "
import asyncio
from sqlalchemy import text
from docket.db.session import engine
async def go():
    async with engine.begin() as c:
        await c.execute(text(\"delete from docket.entries where subject like 'test:mark%'\"))
        await c.execute(text('delete from docket.local_identities where uid = :u'), {'u': $UID_UNDER_TEST})
asyncio.run(go())"
[ $fail = 0 ] && echo "PASS" || echo "FAILED"
exit $fail
