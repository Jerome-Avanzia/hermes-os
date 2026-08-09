"""Tests for Lead / Company Research Workflow.

Coverage:
  - _extract_company_name(): all extraction patterns + fallback
  - LeadResearchWorkflow.execute(): happy path, partial failures, all-fail
  - execute_from_objective(): adapter integration
  - skill.yaml: workflow_executor and keywords present
  - CapabilityRegistry: lead.research is discoverable and has an executor
  - FounderJobRouter: routes lead-research objective via /chat
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from hermes.workflows.competitor_intelligence_workflow import FetchResult
from hermes.workflows.lead_research_workflow import (
    LeadResearchReport,
    LeadResearchRequest,
    LeadResearchWorkflow,
    _extract_company_name,
    execute_from_objective,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SKILLS_ROOT = Path(__file__).parent.parent / "skills"


def _stub_researcher(pages: list[FetchResult]) -> MagicMock:
    """Return a WebResearcher stub that yields pre-built FetchResult objects."""
    stub = MagicMock()
    stub.fetch.side_effect = pages
    return stub


def _ok_page(url: str, title: str = "Title", content: str = "Some content") -> FetchResult:
    return FetchResult(url=url, http_status=200, success=True, title=title, content=content)


def _fail_page(url: str) -> FetchResult:
    return FetchResult(url=url, http_status=None, success=False, title="", content="")


def _mock_conductor(response: str = "LEAD INTELLIGENCE REPORT\nAdvisory label: FOUNDER-INTERNAL — ADVISORY ONLY\n\nContent here.") -> MagicMock:
    conductor = MagicMock()
    conductor.stream_chat.return_value = iter([response])
    return conductor


# ---------------------------------------------------------------------------
# _extract_company_name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "objective, expected",
    [
        # Primary acceptance-test phrasing
        ("Research Acme as a potential AVANZIA client.", "Acme"),
        ("Research Notion as a potential client", "Notion"),
        # Multi-word names
        ("Research Acme Corp as a potential client", "Acme Corp"),
        ("Research Linear App as a potential AVANZIA client", "Linear App"),
        # Bare domain
        ("Research acme.com as a lead", "acme.com"),
        # Other trigger verbs
        ("Qualify Acme as a prospect", "Acme"),
        ("Profile Stripe as a potential customer", "Stripe"),
        ("Investigate HubSpot as a potential client", "HubSpot"),
        # Pattern 2: "company research on X"
        ("company research on Intercom", "Intercom"),
        ("account research for Stripe", "Stripe"),
        # Pattern 3: "tell me about X"
        ("tell me about Notion", "Notion"),
        # Fallback
        ("Some random unrecognised prompt", "Some random unrecognised prompt"),
    ],
)
def test_extract_company_name(objective: str, expected: str):
    assert _extract_company_name(objective) == expected


# ---------------------------------------------------------------------------
# LeadResearchWorkflow.execute — happy path
# ---------------------------------------------------------------------------


def test_execute_returns_report():
    pages = [
        _ok_page("https://acme.com/", "Acme — Homepage", "We help teams ship faster."),
        _ok_page("https://acme.com/about", "About Acme", "Founded in 2018."),
        _ok_page("https://acme.com/pricing", "Pricing", "Free tier available."),
        _ok_page("https://acme.com/team", "Team", "CEO: Jane Smith"),
        _ok_page("https://acme.com/blog", "Blog", "Latest news."),
    ]
    conductor = _mock_conductor()
    workflow = LeadResearchWorkflow(conductor=conductor, researcher=_stub_researcher(pages))
    report = workflow.execute(LeadResearchRequest(company_name="Acme"))

    assert isinstance(report, LeadResearchReport)
    assert report.company_name == "Acme"
    assert report.pages_fetched == 5
    assert report.pages_successful == 5
    assert report.advisory_label == "FOUNDER-INTERNAL — ADVISORY ONLY"


def test_execute_fetches_five_pages():
    pages = [_ok_page(f"https://acme.com/{p}") for p in ["", "about", "pricing", "team", "blog"]]
    conductor = _mock_conductor()
    stub = _stub_researcher(pages)
    workflow = LeadResearchWorkflow(conductor=conductor, researcher=stub)
    workflow.execute(LeadResearchRequest(company_name="Acme"))

    assert stub.fetch.call_count == 5


def test_execute_fetches_correct_url_set():
    pages = [_ok_page(f"https://acme.com/x") for _ in range(5)]
    conductor = _mock_conductor()
    stub = _stub_researcher(pages)
    workflow = LeadResearchWorkflow(conductor=conductor, researcher=stub)
    workflow.execute(LeadResearchRequest(company_name="Acme"))

    fetched_urls = [c[0][0] for c in stub.fetch.call_args_list]
    assert "https://acme.com/" in fetched_urls
    assert "https://acme.com/about" in fetched_urls
    assert "https://acme.com/pricing" in fetched_urls
    assert "https://acme.com/team" in fetched_urls
    assert "https://acme.com/blog" in fetched_urls


def test_execute_uses_sales_consultant_profile():
    pages = [_ok_page(f"https://acme.com/x") for _ in range(5)]
    conductor = _mock_conductor()
    stub = _stub_researcher(pages)
    workflow = LeadResearchWorkflow(conductor=conductor, researcher=stub)
    workflow.execute(LeadResearchRequest(company_name="Acme"))

    call_kwargs = conductor.stream_chat.call_args[1]
    assert call_kwargs.get("profile_id") == "sales-consultant"


def test_execute_with_question_passes_question_to_prompt():
    pages = [_ok_page(f"https://acme.com/x") for _ in range(5)]
    conductor = _mock_conductor()
    stub = _stub_researcher(pages)
    workflow = LeadResearchWorkflow(conductor=conductor, researcher=stub)
    workflow.execute(LeadResearchRequest(
        company_name="Acme",
        question="Are they a fit for AVANZIA?",
    ))

    prompt = conductor.stream_chat.call_args[0][0][0].content
    assert "Are they a fit for AVANZIA?" in prompt


def test_execute_partial_failures_counted():
    pages = [
        _ok_page("https://acme.com/", "Home", "content"),
        _ok_page("https://acme.com/about", "About", "content"),
        _fail_page("https://acme.com/pricing"),
        _fail_page("https://acme.com/team"),
        _ok_page("https://acme.com/blog", "Blog", "content"),
    ]
    conductor = _mock_conductor()
    report = LeadResearchWorkflow(
        conductor=conductor, researcher=_stub_researcher(pages)
    ).execute(LeadResearchRequest(company_name="Acme"))

    assert report.pages_fetched == 5
    assert report.pages_successful == 3


def test_execute_all_fail_still_returns_report():
    pages = [_fail_page(f"https://acme.com/{p}") for p in ["", "about", "pricing", "team", "blog"]]
    conductor = _mock_conductor()
    report = LeadResearchWorkflow(
        conductor=conductor, researcher=_stub_researcher(pages)
    ).execute(LeadResearchRequest(company_name="Acme"))

    assert report.pages_fetched == 5
    assert report.pages_successful == 0
    assert report.report_text  # synthesis still runs


def test_execute_source_log_contains_all_urls():
    pages = [
        _ok_page("https://acme.com/", "Home"),
        _fail_page("https://acme.com/about"),
        _ok_page("https://acme.com/pricing", "Pricing"),
        _fail_page("https://acme.com/team"),
        _ok_page("https://acme.com/blog", "Blog"),
    ]
    conductor = _mock_conductor()
    report = LeadResearchWorkflow(
        conductor=conductor, researcher=_stub_researcher(pages)
    ).execute(LeadResearchRequest(company_name="Acme"))

    log_str = " ".join(report.source_log)
    assert "https://acme.com/" in log_str
    assert "https://acme.com/about" in log_str
    assert "HTTP 200" in log_str
    assert "ERR" in log_str


def test_execute_default_question_used_when_empty():
    pages = [_ok_page(f"https://acme.com/x") for _ in range(5)]
    conductor = _mock_conductor()
    report = LeadResearchWorkflow(
        conductor=conductor, researcher=_stub_researcher(pages)
    ).execute(LeadResearchRequest(company_name="Acme"))

    assert report.question == "General lead qualification"


# ---------------------------------------------------------------------------
# execute_from_objective
# ---------------------------------------------------------------------------


def test_execute_from_objective_extracts_name_and_runs():
    conductor = _mock_conductor()
    with patch(
        "hermes.workflows.lead_research_workflow.LeadResearchWorkflow"
    ) as MockWorkflow:
        mock_instance = MagicMock()
        mock_instance.execute.return_value = LeadResearchReport(
            company_name="Acme",
            question="General lead qualification",
            report_text="Report text",
            pages_fetched=5,
            pages_successful=3,
        )
        MockWorkflow.return_value = mock_instance

        result = execute_from_objective(
            objective="Research Acme as a potential AVANZIA client.",
            workspace_id="AVANZIA",
            conductor=conductor,
        )

    assert result == "Report text"
    request_arg = mock_instance.execute.call_args[0][0]
    assert request_arg.company_name == "Acme"
    assert request_arg.workspace_id == "AVANZIA"


# ---------------------------------------------------------------------------
# Skill manifest validation
# ---------------------------------------------------------------------------


def test_skill_yaml_has_workflow_executor():
    import yaml
    skill_path = SKILLS_ROOT / "lead-research" / "skill.yaml"
    assert skill_path.exists(), f"Missing: {skill_path}"
    manifest = yaml.safe_load(skill_path.read_text())
    assert manifest.get("workflow_executor") == \
        "hermes.workflows.lead_research_workflow.execute_from_objective"


def test_skill_yaml_has_lead_keywords():
    import yaml
    skill_path = SKILLS_ROOT / "lead-research" / "skill.yaml"
    manifest = yaml.safe_load(skill_path.read_text())
    keywords = [k.lower() for k in manifest.get("keywords", [])]
    assert "potential client" in keywords or any("client" in k for k in keywords)
    assert any("lead" in k for k in keywords)


# ---------------------------------------------------------------------------
# CapabilityRegistry integration
# ---------------------------------------------------------------------------


def test_capability_registry_discovers_lead_research():
    from hermes.kernel.capability_registry import CapabilityRegistry
    reg = CapabilityRegistry(skills_root=SKILLS_ROOT)
    cap = reg.get("lead.research")
    assert cap is not None, "lead.research capability not found in registry"
    assert cap.workflow_executor == \
        "hermes.workflows.lead_research_workflow.execute_from_objective"


def test_capability_registry_matches_lead_objective():
    from hermes.kernel.capability_registry import CapabilityRegistry
    from hermes.models.task import Task
    reg = CapabilityRegistry(skills_root=SKILLS_ROOT)
    task = Task(
        id="test",
        business="AVANZIA",
        request="Research Acme as a potential AVANZIA client.",
    )
    matches = reg.match(task)
    cap_ids = [c.id for c in matches]
    assert "lead.research" in cap_ids


def test_capability_registry_matches_prospect_keyword():
    from hermes.kernel.capability_registry import CapabilityRegistry
    from hermes.models.task import Task
    reg = CapabilityRegistry(skills_root=SKILLS_ROOT)
    task = Task(id="test", business="AVANZIA", request="Qualify Stripe as a prospect")
    matches = reg.match(task)
    assert any(c.id == "lead.research" for c in matches)


# ---------------------------------------------------------------------------
# FounderJobRouter routes lead objectives from /chat
# ---------------------------------------------------------------------------


def test_founder_job_router_routes_lead_objective():
    """FounderJobRouter.find_executable_capability returns lead.research for a lead objective."""
    from hermes.kernel.capability_registry import CapabilityRegistry
    from hermes.kernel.founder_job_router import FounderJobRouter

    conductor = MagicMock()
    reg = CapabilityRegistry(skills_root=SKILLS_ROOT)
    router = FounderJobRouter(conductor=conductor, capability_registry=reg)

    cap = router.find_executable_capability(
        "Research Acme as a potential AVANZIA client.",
        workspace_id="AVANZIA",
    )
    assert cap is not None
    assert cap.id == "lead.research"


def test_founder_job_router_does_not_route_board_update_to_lead():
    """Non-lead objectives do not match lead.research."""
    from hermes.kernel.capability_registry import CapabilityRegistry
    from hermes.kernel.founder_job_router import FounderJobRouter

    conductor = MagicMock()
    reg = CapabilityRegistry(skills_root=SKILLS_ROOT)
    router = FounderJobRouter(conductor=conductor, capability_registry=reg)

    cap = router.find_executable_capability(
        "Write a board update for this quarter",
        workspace_id="AVANZIA",
    )
    # May return None or a non-lead-research capability
    if cap is not None:
        assert cap.id != "lead.research"
