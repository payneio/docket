#!/usr/bin/env bash
# Everything that must be true before a commit.
#
# The suite writes to `docket_test`, never to the register — see tests/__init__.py
# — so this is safe to run against a live machine with the service up.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

run() { printf '\n== %s\n' "$1"; shift; "$@"; }

run "ruff"   uv run ruff check .
run "mypy"   uv run mypy
run "pytest" uv run pytest tests/ -q

# The installer embeds the harness sources, so it can be correct at review and
# wrong at install. Regenerate and compare rather than trusting anybody to
# remember.
printf '\n== installer is current\n'
cp harness/install.sh /tmp/install.sh.was
./harness/build-installer >/dev/null
if ! diff -q /tmp/install.sh.was harness/install.sh >/dev/null; then
  rm -f /tmp/install.sh.was
  echo "harness/install.sh is stale — regenerated it; commit the result" >&2
  exit 1
fi
rm -f /tmp/install.sh.was
echo "up to date"

# The runner against a real server: every outcome, the stream, and that a pass
# is one run. The server's own access log is noise unless something failed.
printf '\n== harness, end to end\n'
log="$(mktemp)"
if ./harness/harness_check.sh >"$log" 2>&1; then
  grep -cE '^  ok ' "$log" | xargs printf '%s checks ok\n'; rm -f "$log"
else
  grep -E '^==|^  (ok|FAIL)' "$log" >&2; echo "full output: $log" >&2; exit 1
fi

printf '\nAll checks passed.\n'
