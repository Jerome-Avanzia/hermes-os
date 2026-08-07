#!/usr/bin/env bash
# AT-7 Scenario 1 fixture setup.
# Creates hermes-text-utils under $HERMES_REPOS (default: /opt/avanzia/repos).
# Idempotent: safe to re-run.
set -euo pipefail

REPOS="${HERMES_REPOS:-/opt/avanzia/repos}"
BASE="$REPOS/hermes-text-utils"

echo "==> Creating fixture at $BASE"
rm -rf "$BASE"
mkdir -p "$BASE/tests"
cd "$BASE"

git init -q
git config user.email "at7@hermes.local"
git config user.name "AT-7 Test"

cat > pyproject.toml <<'TOML'
[build-system]
requires = ["setuptools"]
build-backend = "setuptools.backends.legacy:build"

[project]
name = "hermes-text-utils"
version = "0.1.0"
requires-python = ">=3.12"

[tool.pytest.ini_options]
testpaths = ["tests"]

[project.optional-dependencies]
dev = ["pytest>=8.0"]
TOML

touch tests/__init__.py

cat > tests/test_text_utils.py <<'PYTHON'
from text_utils import truncate

def test_no_truncation_needed():
    assert truncate("hi", 10) == "hi"

def test_empty_string():
    assert truncate("", 10) == ""

def test_clean_word_boundary():
    assert truncate("hello world foo bar", 11) == "hello world"

def test_ellipsis_on_mid_word_cut():
    assert truncate("hello world foo bar", 10) == "hello w..."

def test_single_oversized_word():
    assert truncate("averylongword", 5) == "av..."
PYTHON

git add .
git commit -q -m "feat: AT-7 test scaffolding — text_utils tests"

echo "==> Fixture ready. text_utils.py absent: $(test ! -f text_utils.py && echo YES || echo NO)"
