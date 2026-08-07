"""Tests for Bootstrap Phase 5 — repository selection models and LLM propose_target.

Coverage:
  - RepositorySelectionResult: construction, frozen, slots, confidence values
  - SelectionExecutionResult: construction, frozen, slots, success/failure states
  - LlmAdapter._execute_propose_target(): JSON parsing, schema validation,
    markdown fence stripping, ambiguity, field normalization
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from hermes.adapters.llm_adapter import LlmAdapter, ProviderDriver
from hermes.models.execution_gateway import ExecutionAdapter, ExecutionRequest
from hermes.models.llm_adapter import (
    AdapterConfiguration,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    ProviderCapabilities,
)
from hermes.models.repository_selection import (
    RepositorySelectionResult,
    SelectionExecutionResult,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_request(action_id: str = "propose_target") -> ExecutionRequest:
    return ExecutionRequest(
        request_id="req-sel-001",
        operation_id="op-sel-001",
        adapter_type=ExecutionAdapter.LLM,
        action_id=action_id,
        payload=(
            ("prompt", "Write a calculator"),
            ("system_prompt", "You are a repo analyst"),
        ),
    )


def _make_config() -> AdapterConfiguration:
    return AdapterConfiguration(
        provider=LLMProvider.OLLAMA,
        model="test-model",
        base_url="http://localhost:11434",
        api_key="",
        max_tokens=1024,
        timeout_seconds=30,
    )


def _make_capabilities() -> ProviderCapabilities:
    return ProviderCapabilities(
        provider=LLMProvider.OLLAMA,
        supports_streaming=False,
        supports_structured_output=False,
        supports_system_prompt=True,
        supports_tool_use=False,
        max_context_tokens=8192,
        default_model="test-model",
        requires_api_key=False,
    )


def _make_llm_response(content: str) -> LLMResponse:
    return LLMResponse(
        request_id="req-sel-001",
        provider=LLMProvider.OLLAMA,
        model="test-model",
        content=content,
        input_tokens=10,
        output_tokens=20,
        total_tokens=30,
        streaming_used=False,
        structured_output_used=False,
        finish_reason="stop",
        metadata=(),
    )


def _make_driver(response_content: str) -> ProviderDriver:
    """Build a minimal ProviderDriver that returns the given content string."""
    llm_resp = _make_llm_response(response_content)

    def build_payload(req: LLMRequest, cfg: AdapterConfiguration) -> dict:
        return {"model": req.model, "messages": [{"role": "user", "content": req.user_prompt}]}

    def call_provider(endpoint: str, payload: dict, timeout: int, api_key: str) -> dict:
        return {"raw": "response"}

    def parse_response(raw: dict, req: LLMRequest) -> LLMResponse:
        return llm_resp

    return ProviderDriver(
        build_payload=build_payload,
        call_provider=call_provider,
        parse_response=parse_response,
        endpoint_path="/api/chat",
    )


def _make_adapter(response_content: str) -> LlmAdapter:
    adapter = LlmAdapter()
    adapter.register_provider(
        LLMProvider.OLLAMA,
        _make_capabilities(),
        driver=_make_driver(response_content),
    )
    return adapter


# ── TestRepositorySelectionResult ─────────────────────────────────────────────


class TestRepositorySelectionResult:
    def test_construction_high_confidence(self):
        r = RepositorySelectionResult(
            selected_file="src/calculator.py",
            operation="create_file",
            confidence="high",
            basis="Only Python source file matching the task.",
            candidates=(),
        )
        assert r.selected_file == "src/calculator.py"
        assert r.operation == "create_file"
        assert r.confidence == "high"
        assert r.candidates == ()

    def test_construction_ambiguous(self):
        r = RepositorySelectionResult(
            selected_file="",
            operation="",
            confidence="ambiguous",
            basis="Multiple calculator modules found.",
            candidates=("src/calc.py", "src/calculator.py"),
        )
        assert r.confidence == "ambiguous"
        assert r.selected_file == ""
        assert len(r.candidates) == 2

    def test_frozen(self):
        r = RepositorySelectionResult(
            selected_file="f.py", operation="create_file",
            confidence="high", basis="test", candidates=(),
        )
        with pytest.raises((AttributeError, TypeError)):
            r.confidence = "ambiguous"  # type: ignore[misc]

    def test_slots(self):
        r = RepositorySelectionResult(
            selected_file="f.py", operation="modify_file",
            confidence="high", basis="test", candidates=(),
        )
        assert not hasattr(r, "__dict__")

    def test_candidates_is_tuple(self):
        r = RepositorySelectionResult(
            selected_file="", operation="", confidence="ambiguous",
            basis="many", candidates=("a.py", "b.py", "c.py"),
        )
        assert isinstance(r.candidates, tuple)

    def test_equality(self):
        kwargs = dict(
            selected_file="src/f.py", operation="create_file",
            confidence="high", basis="clear", candidates=(),
        )
        assert RepositorySelectionResult(**kwargs) == RepositorySelectionResult(**kwargs)


# ── TestSelectionExecutionResult ──────────────────────────────────────────────


class TestSelectionExecutionResult:
    def _make_success(self) -> SelectionExecutionResult:
        sel = RepositorySelectionResult(
            selected_file="src/f.py", operation="create_file",
            confidence="high", basis="clear", candidates=(),
        )
        return SelectionExecutionResult(
            request_id="req-1",
            operation_id="op-1",
            success=True,
            error=None,
            selection_result=sel,
            adapter_metadata=(("action", "propose_target"), ("confidence", "high")),
        )

    def test_success_construction(self):
        r = self._make_success()
        assert r.success is True
        assert r.error is None
        assert r.selection_result is not None
        assert r.selection_result.confidence == "high"

    def test_failure_construction(self):
        r = SelectionExecutionResult(
            request_id="req-fail",
            operation_id="op-fail",
            success=False,
            error="json_parse_failed: ...",
            selection_result=None,
            adapter_metadata=(("action", "propose_target"),),
        )
        assert r.success is False
        assert r.selection_result is None
        assert r.error is not None

    def test_frozen(self):
        r = self._make_success()
        with pytest.raises((AttributeError, TypeError)):
            r.success = False  # type: ignore[misc]

    def test_slots(self):
        assert not hasattr(self._make_success(), "__dict__")

    def test_adapter_metadata_is_tuple_of_tuples(self):
        r = self._make_success()
        assert isinstance(r.adapter_metadata, tuple)
        assert all(isinstance(pair, tuple) and len(pair) == 2 for pair in r.adapter_metadata)


# ── TestLlmAdapterProposeTarget ───────────────────────────────────────────────


class TestLlmAdapterProposeTarget:
    """Tests for LlmAdapter.execute() when action_id='propose_target'."""

    def _valid_json(
        self,
        selected_file: str = "src/calculator.py",
        operation: str = "create_file",
        confidence: str = "high",
        basis: str = "Single Python source file matching the task.",
        candidates: list | None = None,
    ) -> str:
        return json.dumps({
            "selected_file": selected_file,
            "operation": operation,
            "confidence": confidence,
            "basis": basis,
            "candidates": candidates or [],
        })

    def test_high_confidence_returns_selection_result(self):
        adapter = _make_adapter(self._valid_json())
        request = _make_request()
        config = _make_config()

        result = adapter.execute(request, config)

        assert isinstance(result, SelectionExecutionResult)
        assert result.success is True
        assert result.selection_result is not None
        assert result.selection_result.confidence == "high"
        assert result.selection_result.selected_file == "src/calculator.py"
        assert result.selection_result.operation == "create_file"

    def test_high_confidence_empty_candidates(self):
        adapter = _make_adapter(self._valid_json(candidates=[]))
        result = adapter.execute(_make_request(), _make_config())
        assert result.selection_result.candidates == ()

    def test_ambiguous_returns_selection_result(self):
        json_str = self._valid_json(
            selected_file="",
            operation="",
            confidence="ambiguous",
            basis="Two plausible files.",
            candidates=["src/calc.py", "src/calculator.py"],
        )
        adapter = _make_adapter(json_str)
        result = adapter.execute(_make_request(), _make_config())

        assert result.success is True
        assert result.selection_result.confidence == "ambiguous"
        assert result.selection_result.selected_file == ""
        assert "src/calc.py" in result.selection_result.candidates
        assert "src/calculator.py" in result.selection_result.candidates

    def test_candidates_sorted_alphabetically(self):
        json_str = self._valid_json(
            selected_file="",
            operation="",
            confidence="ambiguous",
            basis="Multiple matches.",
            candidates=["src/z.py", "src/a.py", "src/m.py"],
        )
        adapter = _make_adapter(json_str)
        result = adapter.execute(_make_request(), _make_config())

        assert result.selection_result.candidates == ("src/a.py", "src/m.py", "src/z.py")

    def test_markdown_fence_stripped(self):
        content = "```json\n" + self._valid_json() + "\n```"
        adapter = _make_adapter(content)
        result = adapter.execute(_make_request(), _make_config())

        assert result.success is True
        assert result.selection_result is not None

    def test_markdown_fence_no_lang_tag(self):
        content = "```\n" + self._valid_json() + "\n```"
        adapter = _make_adapter(content)
        result = adapter.execute(_make_request(), _make_config())

        assert result.success is True

    def test_json_parse_failure_returns_failure(self):
        adapter = _make_adapter("not valid json at all")
        result = adapter.execute(_make_request(), _make_config())

        assert isinstance(result, SelectionExecutionResult)
        assert result.success is False
        assert "json_parse_failed" in result.error

    def test_missing_required_fields_returns_failure(self):
        # Missing "candidates" and "basis"
        incomplete = json.dumps({"selected_file": "f.py", "confidence": "high"})
        adapter = _make_adapter(incomplete)
        result = adapter.execute(_make_request(), _make_config())

        assert result.success is False
        assert "json_schema_invalid" in result.error
        assert "missing fields" in result.error

    def test_invalid_confidence_value_returns_failure(self):
        invalid_json = self._valid_json(confidence="low")
        adapter = _make_adapter(invalid_json)
        result = adapter.execute(_make_request(), _make_config())

        assert result.success is False
        assert "json_schema_invalid" in result.error
        assert "confidence" in result.error

    def test_provider_call_exception_returns_failure(self):
        adapter = LlmAdapter()
        driver = ProviderDriver(
            build_payload=lambda req, cfg: {},
            call_provider=lambda *args: (_ for _ in ()).throw(ConnectionError("refused")),
            parse_response=lambda raw, req: None,
            endpoint_path="/api/chat",
        )
        adapter.register_provider(LLMProvider.OLLAMA, _make_capabilities(), driver=driver)
        result = adapter.execute(_make_request(), _make_config())

        assert result.success is False
        assert "provider_call_failed" in result.error

    def test_no_driver_returns_failure(self):
        adapter = LlmAdapter()
        adapter.register_provider(LLMProvider.OLLAMA, _make_capabilities(), driver=None)
        result = adapter.execute(_make_request(), _make_config())

        assert isinstance(result, SelectionExecutionResult)
        assert result.success is False
        assert "no_driver_for_ollama" in result.error

    def test_validation_failure_returns_failure(self):
        # Empty model triggers validation error
        adapter = _make_adapter(self._valid_json())
        config = AdapterConfiguration(
            provider=LLMProvider.OLLAMA,
            model="",   # invalid — triggers validation failure
            base_url="http://localhost:11434",
            api_key="",
        )
        result = adapter.execute(_make_request(), config)

        assert isinstance(result, SelectionExecutionResult)
        assert result.success is False

    def test_adapter_metadata_contains_confidence(self):
        adapter = _make_adapter(self._valid_json())
        result = adapter.execute(_make_request(), _make_config())

        meta_dict = dict(result.adapter_metadata)
        assert "confidence" in meta_dict
        assert meta_dict["confidence"] == "high"

    def test_adapter_metadata_contains_action(self):
        adapter = _make_adapter(self._valid_json())
        result = adapter.execute(_make_request(), _make_config())

        meta_dict = dict(result.adapter_metadata)
        assert meta_dict.get("action") == "propose_target"

    def test_generate_action_still_returns_adapter_execution_result(self):
        """Regular 'generate' action must still return AdapterExecutionResult, not SelectionExecutionResult."""
        from hermes.models.llm_adapter import AdapterExecutionResult

        adapter = _make_adapter("def hello(): pass")
        request = ExecutionRequest(
            request_id="req-gen",
            operation_id="op-gen",
            adapter_type=ExecutionAdapter.LLM,
            action_id="generate",
            payload=(("prompt", "Write a function"),),
        )
        result = adapter.execute(request, _make_config())

        assert isinstance(result, AdapterExecutionResult)
        assert not isinstance(result, SelectionExecutionResult)

    def test_basis_preserved_from_llm_response(self):
        basis = "This is the only file matching the task domain."
        adapter = _make_adapter(self._valid_json(basis=basis))
        result = adapter.execute(_make_request(), _make_config())

        assert result.selection_result.basis == basis

    def test_candidates_non_list_normalised_to_empty_tuple(self):
        data = {
            "selected_file": "",
            "operation": "",
            "confidence": "ambiguous",
            "basis": "unclear",
            "candidates": "not a list",  # malformed
        }
        adapter = _make_adapter(json.dumps(data))
        result = adapter.execute(_make_request(), _make_config())

        # Should succeed; candidates normalised to empty tuple
        assert result.success is True
        assert result.selection_result.candidates == ()
