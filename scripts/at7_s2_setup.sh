#!/usr/bin/env bash
# AT-7 Scenario 2 fixture setup (isolation test).
# Resets hermes-text-utils to pre-implementation state and adds word_utils tests.
# Must be run after at7_setup.sh (and optionally after Scenario 1 completes).
set -euo pipefail

REPOS="${HERMES_REPOS:-/opt/avanzia/repos}"
BASE="$REPOS/hermes-text-utils"

if [ ! -d "$BASE/.git" ]; then
  echo "ERROR: $BASE is not a git repository. Run at7_setup.sh first." >&2
  exit 1
fi

cd "$BASE"

# Remove generated files from Scenario 1 if present
if [ -f text_utils.py ]; then
  git rm -f text_utils.py
  git commit -q -m "reset: remove text_utils.py for Scenario 2"
fi

# Add word_utils test scaffolding
cat > tests/test_word_utils.py <<'PYTHON'
from word_utils import word_count

def test_word_count_basic():
    assert word_count("hello world") == 2

def test_word_count_empty():
    assert word_count("") == 0

def test_word_count_single():
    assert word_count("hello") == 1
PYTHON

git add tests/test_word_utils.py
git commit -q -m "feat: AT-7 Scenario 2 — add word_utils test scaffolding"

echo "==> Scenario 2 fixture ready."
echo "    text_utils.py absent:  $(test ! -f text_utils.py && echo YES || echo NO)"
echo "    word_utils.py absent:  $(test ! -f word_utils.py && echo YES || echo NO)"
