#!/usr/bin/env bash
# AT-8 fixture setup.
# Creates hermes-dispatch-router under $HERMES_REPOS (default: /opt/avanzia/repos).
# Idempotent: safe to re-run.
#
# Fixture design:
#   - src/routing/dispatcher.py   owns route() and dispatch(); routing dispatch owner
#   - src/routing/middleware.py   logging and auth middleware; not the dispatch owner
#   - src/utils/string_helpers.py string utilities; unrelated to routing
#   - tests/test_dispatcher.py    includes test_fallback_handler() which fails until
#                                 AT-8 implementation adds the fallback to dispatcher.py
#   - tests/test_middleware.py    tests middleware; must continue passing after AT-8
#
# Pass conditions (AT-8):
#   P-1: WorkflowExecutionReport.metadata contains planning_context,
#        planning_snapshot_entries (= "5"), and planning_snapshot_id
#   P-2: PlannedOperation targets src/routing/dispatcher.py (routing dispatch owner)
#   P-3: all tests pass after implementation
#   P-4: exactly one git commit produced
#
# Prerequisites: the hermes container must be running (docker exec hermes ...).
set -euo pipefail

REPOS="${HERMES_REPOS:-/opt/avanzia/repos}"
BASE="$REPOS/hermes-dispatch-router"
CONTAINER_BASE="/data/repos/hermes-dispatch-router"

echo "==> Creating fixture at $BASE"
rm -rf "$BASE"
mkdir -p "$BASE/src/routing" "$BASE/src/utils" "$BASE/tests"
cd "$BASE"

git init -q
git config user.email "at8@hermes.local"
git config user.name "AT-8 Test"

cat > pyproject.toml <<'TOML'
[build-system]
requires = ["setuptools"]
build-backend = "setuptools.backends.legacy:build"

[project]
name = "hermes-dispatch-router"
version = "0.1.0"
requires-python = ">=3.12"

[tool.pytest.ini_options]
testpaths = ["tests"]

[project.optional-dependencies]
dev = ["pytest>=8.0"]
TOML

touch src/__init__.py
touch src/routing/__init__.py
touch src/utils/__init__.py
touch tests/__init__.py

cat > src/routing/dispatcher.py <<'PYTHON'
"""Routing dispatcher — owns route registration and request dispatch."""


_routes: dict[str, object] = {}


def route(path: str, handler: object) -> None:
    """Register a handler for the given path."""
    _routes[path] = handler


def dispatch(path: str) -> object:
    """Dispatch a request to the registered handler for path.

    Returns the handler associated with path, or None if no route matches.
    No fallback handler is registered yet.
    """
    return _routes.get(path)
PYTHON

cat > src/routing/middleware.py <<'PYTHON'
"""Routing middleware — logging and authentication."""


def log_request(path: str) -> str:
    """Return a log line for the incoming request."""
    return f"REQUEST: {path}"


def require_auth(token: str) -> bool:
    """Return True if the token is non-empty (simplified auth check)."""
    return bool(token and token.strip())
PYTHON

cat > src/utils/string_helpers.py <<'PYTHON'
"""String utility functions."""


def slugify(text: str) -> str:
    """Convert text to a URL-safe slug."""
    return text.lower().replace(" ", "-")


def truncate(text: str, max_len: int) -> str:
    """Truncate text to max_len characters."""
    if len(text) <= max_len:
        return text
    return text[:max_len]
PYTHON

cat > tests/test_dispatcher.py <<'PYTHON'
import pytest
from src.routing.dispatcher import route, dispatch


def test_registered_route_is_dispatched():
    route("/hello", "hello_handler")
    assert dispatch("/hello") == "hello_handler"


def test_unregistered_route_returns_none_without_fallback():
    # Before the fallback handler is added, unregistered routes return None.
    result = dispatch("/unknown-path-xyz")
    assert result is None


def test_fallback_handler_invoked_for_unmatched_route():
    """AT-8: Hermes must add a set_fallback() function and a fallback-aware dispatch.

    After the implementation:
    - set_fallback(handler) registers a fallback for unmatched routes
    - dispatch(path) returns the fallback handler when no route matches
    """
    from src.routing.dispatcher import set_fallback

    def my_fallback(path: str) -> str:
        return f"404: {path}"

    set_fallback(my_fallback)
    result = dispatch("/no-such-route")
    assert result is my_fallback
PYTHON

cat > tests/test_middleware.py <<'PYTHON'
from src.routing.middleware import log_request, require_auth


def test_log_request_format():
    assert log_request("/api/users") == "REQUEST: /api/users"


def test_require_auth_accepts_non_empty_token():
    assert require_auth("tok123") is True


def test_require_auth_rejects_empty_token():
    assert require_auth("") is False


def test_require_auth_rejects_whitespace_token():
    assert require_auth("   ") is False
PYTHON

echo "==> Installing pytest into .venv via container Python"
docker exec hermes python3 -m venv "$CONTAINER_BASE/.venv"
docker exec hermes "$CONTAINER_BASE/.venv/bin/pip" install -q "pytest>=8.0"

git add .
git commit -q -m "feat: AT-8 fixture — hermes-dispatch-router with failing fallback test"

echo "==> Fixture ready."
echo "    test_fallback_handler present: $(grep -c 'test_fallback_handler' tests/test_dispatcher.py) occurrence(s)"
echo "    .venv/bin/pytest present:      $(test -f .venv/bin/pytest && echo YES || echo NO)"
echo ""
echo "AT-8 runbook:"
echo "  docker exec hermes hermes implement \\"
echo "    \"Add a fallback handler that is invoked when no route matches\" \\"
echo "    --repo hermes-dispatch-router"
