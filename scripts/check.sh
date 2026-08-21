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

printf '\nAll checks passed.\n'
