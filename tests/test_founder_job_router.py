"""Tests for FounderJobRouter — deterministic capability dispatch.

Coverage:
  - find_executable_capability: match vs no-match
  - route(): executable path (workflow → CEO review)
  - route(): CEO-only fallback
  - route(): executor load error → CEO-only fallback
  - route(): workflow exception → error token yielded
  - _load_executor(): valid dotted path
  - _load_executor(): bad path → ExecutorLoadError
  - _extract_competitor_name(): extraction patterns + fallback
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from hermes.kernel.founder_job_router import (
    ExecutorLoadError,
    FounderJobRouter,
    _load_executor,
)
from hermes.models.capability import Capability
from hermes.workflows.competitor_intelligence_workflow import _extract_competitor_name


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_cap(
    cap_id: str = "competitor.intelligence",
    executor: str | None = "hermes.workflows.competitor_intelligence_workflow.execute_from_objective",
) -> Capability:
    return Capability(
        id=cap_id,
        name="Competitor Intelligence",
        version="1.0",
        provides=[],
        keywords=["competitor"],
        workflow_executor=executor,
    )


def _make_router(
    matched_caps: list[Capability] | None = None,
    conductor: object | None = None,
) -> FounderJobRouter:
    registry = MagicMock()
    registry.match.return_value = matched_caps if matched_caps is not None else []
    conductor = conductor or MagicMock()
    return FounderJobRouter(conductor=conductor, capability_registry=registry)


# ---------------------------------------------------------------------------
# find_executable_capability
# ---------------------------------------------------------------------------


def test_find_executable_capability_returns_cap_with_executor():
    cap = _make_cap()
    router = _make_router(matched_caps=[cap])
    result = router.find_executable_capability("Research competitor Acme", "ws1")
    assert result is cap


def test_find_executable_capability_skips_cap_without_executor():
    cap = _make_cap(executor=None)
    router = _make_router(matched_caps=[cap])
    result = router.find_executable_capability("Research competitor Acme", "ws1")
    assert result is None


def test_find_executable_capability_returns_none_on_no_match():
    router = _make_router(matched_caps=[])
    result = router.find_executable_capability("Write a board update", "ws1")
    assert result is None


def test_find_executable_capability_first_with_executor_wins():
    cap_no_exec = _make_cap(cap_id="other", executor=None)
    cap_exec = _make_cap()
    router = _make_router(matched_caps=[cap_no_exec, cap_exec])
    result = router.find_executable_capability("Research competitor Acme", "ws1")
    assert result is cap_exec


# ---------------------------------------------------------------------------
# route() — CEO-only fallback
# ---------------------------------------------------------------------------


def test_route_ceo_only_when_no_match():
    conductor = MagicMock()
    conductor.stream_chat.return_value = iter(["CEO ", "response"])
    router = _make_router(matched_caps=[], conductor=conductor)

    tokens = list(router.route("Write a board update", "ws1"))
    assert tokens == ["CEO ", "response"]
    conductor.stream_chat.assert_called_once()
    _, kwargs = conductor.stream_chat.call_args
    assert kwargs.get("profile_id") == "ceo"


def test_route_ceo_only_when_no_executable_cap():
    conductor = MagicMock()
    conductor.stream_chat.return_value = iter(["CEO only"])
    cap_no_exec = _make_cap(executor=None)
    router = _make_router(matched_caps=[cap_no_exec], conductor=conductor)

    tokens = list(router.route("Research competitor Acme", "ws1"))
    assert "CEO only" in tokens
    conductor.stream_chat.assert_called_once()


# ---------------------------------------------------------------------------
# route() — executable path: workflow → CEO review
# ---------------------------------------------------------------------------


def test_route_calls_workflow_executor_and_ceo_review():
    conductor = MagicMock()
    conductor.stream_chat.return_value = iter(["CEO review text"])

    cap = _make_cap()
    router = _make_router(matched_caps=[cap], conductor=conductor)

    fake_executor = MagicMock(return_value="Workflow result text")

    with patch("hermes.kernel.founder_job_router._load_executor", return_value=fake_executor):
        tokens = list(router.route("Research competitor Acme", "ws1"))

    # Executor was called with correct kwargs
    fake_executor.assert_called_once_with(
        objective="Research competitor Acme",
        workspace_id="ws1",
        conductor=conductor,
    )

    # CEO review was called (not CEO-only direct path)
    conductor.stream_chat.assert_called_once()
    args, kwargs = conductor.stream_chat.call_args
    assert kwargs.get("profile_id") == "ceo"
    # The review prompt must embed the workflow result
    review_prompt = args[0][0].content
    assert "Workflow result text" in review_prompt

    # Tokens come from CEO review
    assert "CEO review text" in tokens


def test_route_executor_load_error_falls_back_to_ceo():
    conductor = MagicMock()
    conductor.stream_chat.return_value = iter(["fallback"])
    cap = _make_cap()
    router = _make_router(matched_caps=[cap], conductor=conductor)

    with patch(
        "hermes.kernel.founder_job_router._load_executor",
        side_effect=ExecutorLoadError("bad path"),
    ):
        tokens = list(router.route("Research competitor Acme", "ws1"))

    assert "fallback" in tokens
    conductor.stream_chat.assert_called_once()


def test_route_workflow_exception_yields_error_token():
    conductor = MagicMock()
    cap = _make_cap()
    router = _make_router(matched_caps=[cap], conductor=conductor)

    fake_executor = MagicMock(side_effect=RuntimeError("fetch failed"))

    with patch("hermes.kernel.founder_job_router._load_executor", return_value=fake_executor):
        tokens = list(router.route("Research competitor Acme", "ws1"))

    assert any("fetch failed" in t for t in tokens)
    # CEO review must NOT be called — error path returns early
    conductor.stream_chat.assert_not_called()


# ---------------------------------------------------------------------------
# _load_executor
# ---------------------------------------------------------------------------


def test_load_executor_returns_callable():
    fn = _load_executor(
        "hermes.workflows.competitor_intelligence_workflow.execute_from_objective"
    )
    assert callable(fn)


def test_load_executor_raises_on_bad_dotted_path():
    with pytest.raises(ExecutorLoadError):
        _load_executor("no_dots_at_all")


def test_load_executor_raises_on_missing_module():
    with pytest.raises(ExecutorLoadError, match="Cannot import module"):
        _load_executor("hermes.workflows.nonexistent_module.execute")


def test_load_executor_raises_on_missing_attr():
    with pytest.raises(ExecutorLoadError, match="no attribute"):
        _load_executor(
            "hermes.workflows.competitor_intelligence_workflow.nonexistent_function"
        )


def test_load_executor_raises_on_non_callable():
    # Patch a module attribute to be a non-callable
    with patch(
        "hermes.workflows.competitor_intelligence_workflow.logger",
        new="not_a_callable_string",
    ):
        with pytest.raises(ExecutorLoadError, match="not callable"):
            _load_executor("hermes.workflows.competitor_intelligence_workflow.logger")


# ---------------------------------------------------------------------------
# _extract_competitor_name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "objective, expected",
    [
        ("Research competitor Acme", "Acme"),
        ("Analyse competitor Notion Corp", "Notion Corp"),
        ("Profile competitor acme.com", "acme.com"),
        # Natural phrasing "Research X as a competitor..." — must extract only "X"
        ("Research Notion as a competitor. Compare their main products, target customers, positioning, and publicly visible pricing.", "Notion"),
        ("Research HubSpot as a competitor", "HubSpot"),
        ("Research Notion Labs as a competitor", "Notion Labs"),
        ("competitor analysis for Stripe", "Stripe"),
        ("competitive intelligence on HubSpot", "HubSpot"),
        ("competitor analysis of Salesforce", "Salesforce"),
        # "what does X do?" — regex captures trailing verb; main patterns cover common phrasing
        ("tell me about Notion", "Notion"),
        # Fallback: unrecognised phrasing → full string returned
        ("Write a board update", "Write a board update"),
    ],
)
def test_extract_competitor_name(objective: str, expected: str):
    result = _extract_competitor_name(objective)
    assert result == expected


def test_extract_competitor_name_fallback_returns_full_string():
    objective = "Some completely unrelated objective text"
    result = _extract_competitor_name(objective)
    assert result == objective
