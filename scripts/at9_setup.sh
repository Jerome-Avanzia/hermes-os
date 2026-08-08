#!/usr/bin/env bash
set -euo pipefail

REPOS_ROOT="${HERMES_REPOSITORIES:-/opt/hermes/repositories}"
REPO_DIR="$REPOS_ROOT/hermes-greeter"

echo "AT-9 setup: creating hermes-greeter fixture in $REPO_DIR"

rm -rf "$REPO_DIR"
mkdir -p "$REPO_DIR/tests"

cat > "$REPO_DIR/tests/test_greeter.py" << 'EOF'
from greeter import greet

def test_greet_returns_string():
    result = greet("Hermes")
    assert isinstance(result, str)

def test_greet_includes_name():
    result = greet("AVANZIA")
    assert "AVANZIA" in result
EOF

cat > "$REPO_DIR/pyproject.toml" << 'EOF'
[build-system]
requires = ["setuptools"]

[tool.pytest.ini_options]
testpaths = ["tests"]
EOF

cd "$REPO_DIR"
git init -q
git config user.email "hermes@avanzia.tech"
git config user.name "Hermes"
git add .
git commit -q -m "feat: hermes-greeter fixture (AT-9 acceptance test)"

echo "AT-9 setup complete: $REPO_DIR"
echo "Files: $(find . -not -path './.git/*' | sort | tr '\n' ' ')"
