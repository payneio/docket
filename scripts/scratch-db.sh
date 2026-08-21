# Point this shell at a scratch database, or refuse to continue.
#
#   set -a; . scripts/scratch-db.sh; set +a
#
# Every checked-in test script had a guard like this. The one that destroyed the
# register on 2026-08-21 was a command typed inline, which had none -- and an
# ad-hoc command is *more* dangerous than a committed one, not less, because
# nobody reviews it and it is written in a hurry beside something else.
#
# The failure mode is quiet: `docket.db.session` reads DOCKET_DATABASE_URL from
# the environment and falls back to `.env` when it is unset. A cleanup snippet
# that sets DOCKET_URL but forgets DOCKET_DATABASE_URL therefore looks correct,
# runs against production, and says nothing.

_scratch_die() { printf 'scratch-db: %s\n' "$*" >&2; }

#: A URL that cannot connect to anything.
#:
#: Returning non-zero is not enough. A sourced file's `return` ends the file and
#: lets the caller carry on, and under `set -a` a bare assignment is already
#: exported -- so a refusal that merely returns would leave the caller holding
#: whatever it was about to be refused for. Failing sets this instead, so the
#: next command fails loudly on a bad port rather than quietly on the register.
_SCRATCH_POISON="postgresql+asyncpg://refused:refused@127.0.0.1:1/scratch-db-refused"

_scratch_ok() {  # <url>
  case "$1" in
    */docket_test|*_test|*/scratch*) return 0 ;;
    *) return 1 ;;
  esac
}

_scratch_setup() {
  local root prod want
  root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  prod="$(grep -oP '(?<=^DOCKET_DATABASE_URL=).*' "$root/.env" 2>/dev/null)"
  : "${prod:=postgresql+asyncpg://castle:castle@localhost:5432/castle}"

  want="${DOCKET_TEST_DATABASE_URL:-${prod%/*}/docket_test}"
  if [ "$want" = "$prod" ] || ! _scratch_ok "$want"; then
    export DOCKET_DATABASE_URL="$_SCRATCH_POISON"
    _scratch_die "refusing $want — pointing this shell at nothing instead"
    return 2
  fi
  export DOCKET_DATABASE_URL="$want"
  # A separate port too, so a stray client cannot reach the live service and a
  # stray service cannot bind over it.
  export DOCKET_PORT="${DOCKET_PORT:-19100}"
}
_scratch_setup

#: Drop the scratch schema. Refuses on anything it did not just point at.
scratch_drop() {
  _scratch_ok "${DOCKET_DATABASE_URL:-}" || { _scratch_die "not dropping ${DOCKET_DATABASE_URL:-nothing}"; return 2; }
  uv run python -c "
import asyncio
from sqlalchemy import text
from docket.db.session import engine
async def go():
    async with engine.begin() as c:
        await c.execute(text('DROP SCHEMA IF EXISTS docket CASCADE'))
asyncio.run(go())" 2>/dev/null
}
