"""Tests for the Competitor Intelligence workflow.

Coverage:
  - Domain inference (_infer_domain)
  - FetchResult dataclass
  - WebResearcher stub contract
  - CompetitorIntelligenceWorkflow.execute() with a stub researcher and conductor
  - Evidence block and synthesis prompt builders
  - Report structure and advisory label
  - Failure handling (all fetches fail, partial failure)
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest

from hermes.workflows.competitor_intelligence_workflow import (
    CompetitorIntelligenceReport,
    CompetitorIntelligenceRequest,
    CompetitorIntelligenceWorkflow,
    FetchResult,
    WebResearcher,
    _build_evidence_block,
    _build_synthesis_prompt,
    _infer_domain,
)


# ---------------------------------------------------------------------------
# Domain inference
# ---------------------------------------------------------------------------


class TestInferDomain:
    def test_plain_name(self):
        assert _infer_domain("Acme") == "acme.com"

    def test_multi_word_name(self):
        assert _infer_domain("Acme Corp") == "acmecorp.com"

    def test_parenthetical_domain(self):
        assert _infer_domain("Acme (acme.io)") == "acme.io"

    def test_bare_domain(self):
        assert _infer_domain("acme.io") == "acme.io"

    def test_bare_dotcom(self):
        assert _infer_domain("example.com") == "example.com"

    def test_name_lowercased(self):
        assert _infer_domain("OpenAI") == "openai.com"

    def test_special_chars_stripped(self):
        assert _infer_domain("Acme, Inc.") == "acmeinc.com"

    def test_subdomain_in_parens(self):
        assert _infer_domain("Acme (www.acme.co.uk)") == "www.acme.co.uk"


# ---------------------------------------------------------------------------
# FetchResult
# ---------------------------------------------------------------------------


class TestFetchResult:
    def test_successful_result(self):
        r = FetchResult(url="https://x.com/", http_status=200, success=True, title="X", content="hello")
        assert r.success is True
        assert r.http_status == 200

    def test_failed_result_no_status(self):
        r = FetchResult(url="https://x.com/", http_status=None, success=False, title="", content="")
        assert r.success is False
        assert r.http_status is None

    def test_immutable(self):
        r = FetchResult(url="https://x.com/", http_status=200, success=True, title="X", content="")
        with pytest.raises((AttributeError, TypeError)):
            r.url = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Stub helpers
# ---------------------------------------------------------------------------


def _make_fetch_result(url: str, success: bool = True, content: str = "page content") -> FetchResult:
    return FetchResult(
        url=url,
        http_status=200 if success else 404,
        success=success,
        title="Test Page" if success else "",
        content=content if success else "",
    )


class StubResearcher(WebResearcher):
    """Researcher stub that returns pre-built FetchResult objects."""

    def __init__(self, results: list[FetchResult]) -> None:
        self._results = results

    def fetch(self, url: str) -> FetchResult:
        for r in self._results:
            if r.url == url:
                return r
        return _make_fetch_result(url, success=False)

    def fetch_competitor_profile(self, competitor_name: str) -> list[FetchResult]:
        return self._results


def _make_stub_conductor(response: str = "FOUNDER-INTERNAL — ADVISORY ONLY\n\nFake report.") -> MagicMock:
    """Return a mock Conductor whose stream_chat yields the response token by token."""

    def _stream(messages, profile_id=None) -> Iterator[str]:
        yield response

    conductor = MagicMock()
    conductor.stream_chat = MagicMock(side_effect=_stream)
    return conductor


# ---------------------------------------------------------------------------
# Evidence block builder
# ---------------------------------------------------------------------------


class TestBuildEvidenceBlock:
    def test_successful_page_included(self):
        pages = [_make_fetch_result("https://a.com/", content="homepage text")]
        block = _build_evidence_block(pages)
        assert "https://a.com/" in block
        assert "homepage text" in block

    def test_failed_page_marked(self):
        pages = [_make_fetch_result("https://a.com/pricing", success=False)]
        block = _build_evidence_block(pages)
        assert "FAILED" in block
        assert "https://a.com/pricing" in block

    def test_mixed_pages(self):
        pages = [
            _make_fetch_result("https://a.com/", content="hello"),
            _make_fetch_result("https://a.com/pricing", success=False),
        ]
        block = _build_evidence_block(pages)
        assert "hello" in block
        assert "FAILED" in block


# ---------------------------------------------------------------------------
# Synthesis prompt builder
# ---------------------------------------------------------------------------


class TestBuildSynthesisPrompt:
    def _pages(self) -> list[FetchResult]:
        return [
            _make_fetch_result("https://acme.com/", content="homepage"),
            _make_fetch_result("https://acme.com/pricing", success=False),
        ]

    def test_contains_competitor_name(self):
        prompt = _build_synthesis_prompt("Acme", "How do they price?", "evidence", self._pages())
        assert "Acme" in prompt

    def test_contains_question(self):
        prompt = _build_synthesis_prompt("Acme", "How do they price?", "evidence", self._pages())
        assert "How do they price?" in prompt

    def test_contains_evidence(self):
        prompt = _build_synthesis_prompt("Acme", "q", "EVIDENCE_GOES_HERE", self._pages())
        assert "EVIDENCE_GOES_HERE" in prompt

    def test_contains_advisory_instruction(self):
        prompt = _build_synthesis_prompt("Acme", "q", "evidence", self._pages())
        assert "FOUNDER-INTERNAL" in prompt

    def test_contains_fact_inferred_unknown_instructions(self):
        prompt = _build_synthesis_prompt("Acme", "q", "evidence", self._pages())
        assert "[FACT]" in prompt
        assert "[INFERRED]" in prompt
        assert "[UNKNOWN]" in prompt

    def test_page_counts_in_prompt(self):
        prompt = _build_synthesis_prompt("Acme", "q", "evidence", self._pages())
        assert "Pages attempted: 2" in prompt
        assert "Pages successful: 1" in prompt


# ---------------------------------------------------------------------------
# Workflow — full pipeline with stubs
# ---------------------------------------------------------------------------


class TestCompetitorIntelligenceWorkflow:
    def _pages(self) -> list[FetchResult]:
        return [
            _make_fetch_result("https://acme.com/", content="We are Acme, a B2B SaaS company."),
            _make_fetch_result("https://acme.com/pricing", content="Plans start at $99/month."),
            _make_fetch_result("https://acme.com/about", success=False),
            _make_fetch_result("https://acme.com/blog", success=False),
            _make_fetch_result("https://acme.com/customers", success=False),
        ]

    def _workflow(self, response: str = "FOUNDER-INTERNAL — ADVISORY ONLY\n\nFake report body.") -> tuple[CompetitorIntelligenceWorkflow, MagicMock]:
        conductor = _make_stub_conductor(response)
        researcher = StubResearcher(self._pages())
        workflow = CompetitorIntelligenceWorkflow(conductor=conductor, researcher=researcher)
        return workflow, conductor

    def test_returns_report(self):
        workflow, _ = self._workflow()
        report = workflow.execute(CompetitorIntelligenceRequest(competitor_name="Acme"))
        assert isinstance(report, CompetitorIntelligenceReport)

    def test_competitor_name_preserved(self):
        workflow, _ = self._workflow()
        report = workflow.execute(CompetitorIntelligenceRequest(competitor_name="Acme"))
        assert report.competitor_name == "Acme"

    def test_question_defaults_to_general(self):
        workflow, _ = self._workflow()
        report = workflow.execute(CompetitorIntelligenceRequest(competitor_name="Acme"))
        assert report.question == "General competitive landscape"

    def test_question_preserved_when_supplied(self):
        workflow, _ = self._workflow()
        report = workflow.execute(CompetitorIntelligenceRequest(
            competitor_name="Acme", question="How do they price for SMBs?"
        ))
        assert report.question == "How do they price for SMBs?"

    def test_pages_fetched_count(self):
        workflow, _ = self._workflow()
        report = workflow.execute(CompetitorIntelligenceRequest(competitor_name="Acme"))
        assert report.pages_fetched == 5

    def test_pages_successful_count(self):
        workflow, _ = self._workflow()
        report = workflow.execute(CompetitorIntelligenceRequest(competitor_name="Acme"))
        assert report.pages_successful == 2

    def test_advisory_label(self):
        workflow, _ = self._workflow()
        report = workflow.execute(CompetitorIntelligenceRequest(competitor_name="Acme"))
        assert report.advisory_label == "FOUNDER-INTERNAL — ADVISORY ONLY"

    def test_report_text_from_conductor(self):
        workflow, _ = self._workflow(response="FOUNDER-INTERNAL — ADVISORY ONLY\n\nSynthesised content here.")
        report = workflow.execute(CompetitorIntelligenceRequest(competitor_name="Acme"))
        assert "Synthesised content here." in report.report_text

    def test_source_log_contains_urls(self):
        workflow, _ = self._workflow()
        report = workflow.execute(CompetitorIntelligenceRequest(competitor_name="Acme"))
        assert len(report.source_log) == 5
        assert any("acme.com/" in entry for entry in report.source_log)

    def test_source_log_records_failure(self):
        workflow, _ = self._workflow()
        report = workflow.execute(CompetitorIntelligenceRequest(competitor_name="Acme"))
        failed_entries = [e for e in report.source_log if "HTTP 404" in e]
        assert len(failed_entries) == 3

    def test_conductor_called_with_strategy_consultant_profile(self):
        workflow, conductor = self._workflow()
        workflow.execute(CompetitorIntelligenceRequest(competitor_name="Acme"))
        conductor.stream_chat.assert_called_once()
        _, kwargs = conductor.stream_chat.call_args
        assert kwargs.get("profile_id") == "strategy-consultant"

    def test_prompt_contains_competitor_name(self):
        workflow, conductor = self._workflow()
        workflow.execute(CompetitorIntelligenceRequest(competitor_name="Acme"))
        call_args = conductor.stream_chat.call_args
        messages = call_args[0][0]
        assert any("Acme" in m.content for m in messages)

    def test_prompt_contains_retrieved_evidence(self):
        workflow, conductor = self._workflow()
        workflow.execute(CompetitorIntelligenceRequest(competitor_name="Acme"))
        call_args = conductor.stream_chat.call_args
        messages = call_args[0][0]
        prompt_text = " ".join(m.content for m in messages)
        assert "B2B SaaS" in prompt_text

    def test_prompt_contains_pricing_content(self):
        workflow, conductor = self._workflow()
        workflow.execute(CompetitorIntelligenceRequest(competitor_name="Acme"))
        call_args = conductor.stream_chat.call_args
        messages = call_args[0][0]
        prompt_text = " ".join(m.content for m in messages)
        assert "$99/month" in prompt_text


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------


class TestWorkflowFailureHandling:
    def _all_fail_pages(self) -> list[FetchResult]:
        return [
            _make_fetch_result(f"https://acme.com/{path}", success=False)
            for path in ["", "pricing", "about", "blog", "customers"]
        ]

    def test_all_fetches_fail_still_returns_report(self):
        conductor = _make_stub_conductor("FOUNDER-INTERNAL — ADVISORY ONLY\n\nAll [UNKNOWN].")
        researcher = StubResearcher(self._all_fail_pages())
        workflow = CompetitorIntelligenceWorkflow(conductor=conductor, researcher=researcher)
        report = workflow.execute(CompetitorIntelligenceRequest(competitor_name="Acme"))
        assert isinstance(report, CompetitorIntelligenceReport)
        assert report.pages_successful == 0

    def test_all_fetches_fail_conductor_still_called(self):
        conductor = _make_stub_conductor("FOUNDER-INTERNAL — ADVISORY ONLY\n\nAll [UNKNOWN].")
        researcher = StubResearcher(self._all_fail_pages())
        workflow = CompetitorIntelligenceWorkflow(conductor=conductor, researcher=researcher)
        workflow.execute(CompetitorIntelligenceRequest(competitor_name="Acme"))
        conductor.stream_chat.assert_called_once()

    def test_all_fetches_fail_failed_urls_in_prompt(self):
        conductor = _make_stub_conductor("FOUNDER-INTERNAL — ADVISORY ONLY\n\nAll [UNKNOWN].")
        researcher = StubResearcher(self._all_fail_pages())
        workflow = CompetitorIntelligenceWorkflow(conductor=conductor, researcher=researcher)
        workflow.execute(CompetitorIntelligenceRequest(competitor_name="Acme"))
        call_args = conductor.stream_chat.call_args
        messages = call_args[0][0]
        prompt_text = " ".join(m.content for m in messages)
        assert "Pages successful: 0" in prompt_text


# ---------------------------------------------------------------------------
# Skill and SOP file existence
# ---------------------------------------------------------------------------


class TestSkillArtifactsExist:
    def test_skill_yaml_exists(self, tmp_path):
        import importlib.resources
        from pathlib import Path

        skill_path = Path(__file__).parent.parent / "skills" / "competitor-intelligence" / "skill.yaml"
        assert skill_path.exists(), f"skill.yaml not found at {skill_path}"

    def test_sop_exists(self):
        from pathlib import Path

        sop_path = (
            Path(__file__).parent.parent
            / "skills"
            / "competitor-intelligence"
            / "sops"
            / "competitor-intelligence.md"
        )
        assert sop_path.exists(), f"SOP not found at {sop_path}"

    def test_skill_yaml_has_required_fields(self):
        from pathlib import Path
        import yaml

        skill_path = Path(__file__).parent.parent / "skills" / "competitor-intelligence" / "skill.yaml"
        data = yaml.safe_load(skill_path.read_text())
        assert data["id"] == "competitor-intelligence"
        assert "competitor.intelligence" in data["capabilities"]
        assert "competitor-intelligence/competitor-intelligence" in data["sop_refs"]

    def test_sop_contains_evidence_protocol(self):
        from pathlib import Path

        sop_path = (
            Path(__file__).parent.parent
            / "skills"
            / "competitor-intelligence"
            / "sops"
            / "competitor-intelligence.md"
        )
        content = sop_path.read_text()
        assert "[FACT]" in content
        assert "[INFERRED]" in content
        assert "[UNKNOWN]" in content
        assert "FOUNDER-INTERNAL" in content

    def test_registry_contains_competitor_intelligence(self):
        from pathlib import Path
        import yaml

        registry_path = Path(__file__).parent.parent / "skills" / "registry.yaml"
        data = yaml.safe_load(registry_path.read_text())
        assert "competitor-intelligence" in data["skills"]
