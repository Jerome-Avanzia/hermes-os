"""Tests for the Validation Adapter.

Coverage:
  - ValidatorKind enum values
  - ValidationRequest, ValidationResult, ValidationExecutionResult fields
  - Language detection by file extension
  - python_syntax validator: valid Python → success
  - python_syntax validator: invalid Python → failure with stderr
  - NONE validator: non-Python files always pass
  - Path security: absolute paths rejected
  - Path security: path traversal rejected
  - Path security: empty path rejected
  - adapter_type mismatch rejected
  - ValidationResult all fields present and typed correctly
  - duration_ms is a non-negative integer
  - adapter_metadata is a sorted tuple of string pairs
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from hermes.adapters.validation_adapter import ValidationAdapter, _detect_validator
from hermes.models.execution_gateway import ExecutionAdapter, ExecutionRequest
from hermes.models.validation_adapter import (
    ValidatorKind,
    ValidationExecutionResult,
    ValidationRequest,
    ValidationResult,
)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_request(
    path: str,
    *,
    adapter_type: ExecutionAdapter = ExecutionAdapter.VALIDATION,
    action_id: str = "validate",
    request_id: str = "req-test",
    operation_id: str = "op-test",
) -> ExecutionRequest:
    return ExecutionRequest(
        request_id=request_id,
        operation_id=operation_id,
        adapter_type=adapter_type,
        action_id=action_id,
        payload=(("path", path),),
    )


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content)
    return p


# ══════════════════════════════════════════════════════════════════════════════
# Model field tests
# ══════════════════════════════════════════════════════════════════════════════


class TestValidatorKind:
    def test_python_syntax_value(self):
        assert ValidatorKind.PYTHON_SYNTAX.value == "python_syntax"

    def test_none_value(self):
        assert ValidatorKind.NONE.value == "none"

    def test_exactly_two_variants(self):
        assert set(v.value for v in ValidatorKind) == {"python_syntax", "none"}


class TestValidationResult:
    def test_fields_present(self):
        r = ValidationResult(
            success=True,
            validator_used="python_syntax",
            language_detected="python",
            exit_code=0,
            stdout="",
            stderr="",
            duration_ms=12,
        )
        assert r.success is True
        assert r.validator_used == "python_syntax"
        assert r.language_detected == "python"
        assert r.exit_code == 0
        assert r.stdout == ""
        assert r.stderr == ""
        assert r.duration_ms == 12

    def test_immutable(self):
        r = ValidationResult(
            success=True, validator_used="none", language_detected="unknown",
            exit_code=-1, stdout="", stderr="", duration_ms=0,
        )
        with pytest.raises(Exception):
            r.success = False  # type: ignore[misc]


class TestValidationExecutionResult:
    def test_fields_present(self):
        r = ValidationExecutionResult(
            request_id="req-1",
            operation_id="op-1",
            success=True,
            error=None,
            validation_request=None,
            validation_result=None,
            adapter_metadata=(("k", "v"),),
        )
        assert r.request_id == "req-1"
        assert r.operation_id == "op-1"
        assert r.success is True
        assert r.error is None
        assert r.adapter_metadata == (("k", "v"),)


# ══════════════════════════════════════════════════════════════════════════════
# Language detection
# ══════════════════════════════════════════════════════════════════════════════


class TestDetectValidator:
    def test_py_extension(self, tmp_path):
        lang, kind = _detect_validator(tmp_path / "foo.py")
        assert kind == ValidatorKind.PYTHON_SYNTAX
        assert lang == "python"

    def test_pyi_extension(self, tmp_path):
        lang, kind = _detect_validator(tmp_path / "stub.pyi")
        assert kind == ValidatorKind.PYTHON_SYNTAX
        assert lang == "python"

    def test_js_extension(self, tmp_path):
        lang, kind = _detect_validator(tmp_path / "app.js")
        assert kind == ValidatorKind.NONE
        assert lang == "unknown"

    def test_ts_extension(self, tmp_path):
        _, kind = _detect_validator(tmp_path / "app.ts")
        assert kind == ValidatorKind.NONE

    def test_go_extension(self, tmp_path):
        _, kind = _detect_validator(tmp_path / "main.go")
        assert kind == ValidatorKind.NONE

    def test_no_extension(self, tmp_path):
        _, kind = _detect_validator(tmp_path / "Makefile")
        assert kind == ValidatorKind.NONE

    def test_case_insensitive(self, tmp_path):
        _, kind = _detect_validator(tmp_path / "script.PY")
        assert kind == ValidatorKind.PYTHON_SYNTAX


# ══════════════════════════════════════════════════════════════════════════════
# python_syntax validator
# ══════════════════════════════════════════════════════════════════════════════


class TestPythonSyntaxValidator:
    def test_valid_python_passes(self, tmp_path):
        _write(tmp_path, "good.py", "def foo():\n    return 42\n")
        adapter = ValidationAdapter(workspace_root=tmp_path)
        result = adapter.execute(_make_request("good.py"))

        assert result.success is True
        assert result.error is None
        assert result.validation_result is not None
        assert result.validation_result.exit_code == 0
        assert result.validation_result.validator_used == "python_syntax"
        assert result.validation_result.language_detected == "python"

    def test_invalid_python_fails(self, tmp_path):
        _write(tmp_path, "bad.py", "def foo(\n    return 42\n")
        adapter = ValidationAdapter(workspace_root=tmp_path)
        result = adapter.execute(_make_request("bad.py"))

        assert result.success is False
        assert result.error is not None
        assert "validation_failed" in result.error
        assert result.validation_result is not None
        assert result.validation_result.exit_code != 0
        assert result.validation_result.stderr != ""

    def test_empty_file_is_valid_python(self, tmp_path):
        _write(tmp_path, "empty.py", "")
        adapter = ValidationAdapter(workspace_root=tmp_path)
        result = adapter.execute(_make_request("empty.py"))
        assert result.success is True

    def test_duration_ms_is_non_negative(self, tmp_path):
        _write(tmp_path, "ok.py", "x = 1\n")
        adapter = ValidationAdapter(workspace_root=tmp_path)
        result = adapter.execute(_make_request("ok.py"))
        assert result.validation_result is not None
        assert result.validation_result.duration_ms >= 0

    def test_validation_request_populated(self, tmp_path):
        _write(tmp_path, "ok.py", "x = 1\n")
        adapter = ValidationAdapter(workspace_root=tmp_path)
        result = adapter.execute(_make_request("ok.py"))
        assert result.validation_request is not None
        assert result.validation_request.path == "ok.py"
        assert result.validation_request.validator == ValidatorKind.PYTHON_SYNTAX

    def test_syntax_error_stderr_not_empty(self, tmp_path):
        _write(tmp_path, "bad.py", "def (\n")
        adapter = ValidationAdapter(workspace_root=tmp_path)
        result = adapter.execute(_make_request("bad.py"))
        assert result.validation_result is not None
        assert len(result.validation_result.stderr) > 0


# ══════════════════════════════════════════════════════════════════════════════
# NONE validator (non-Python files)
# ══════════════════════════════════════════════════════════════════════════════


class TestNoneValidator:
    def test_js_file_passes(self, tmp_path):
        _write(tmp_path, "app.js", "const x = 1;")
        adapter = ValidationAdapter(workspace_root=tmp_path)
        result = adapter.execute(_make_request("app.js"))
        assert result.success is True
        assert result.error is None

    def test_go_file_passes(self, tmp_path):
        _write(tmp_path, "main.go", "package main")
        adapter = ValidationAdapter(workspace_root=tmp_path)
        result = adapter.execute(_make_request("main.go"))
        assert result.success is True

    def test_none_validator_used_field(self, tmp_path):
        _write(tmp_path, "style.css", "body { margin: 0; }")
        adapter = ValidationAdapter(workspace_root=tmp_path)
        result = adapter.execute(_make_request("style.css"))
        assert result.validation_result is not None
        assert result.validation_result.validator_used == "none"
        assert result.validation_result.language_detected == "unknown"
        assert result.validation_result.exit_code == -1

    def test_none_validator_duration_ms_zero(self, tmp_path):
        _write(tmp_path, "readme.md", "# Hello")
        adapter = ValidationAdapter(workspace_root=tmp_path)
        result = adapter.execute(_make_request("readme.md"))
        assert result.validation_result is not None
        assert result.validation_result.duration_ms == 0


# ══════════════════════════════════════════════════════════════════════════════
# Path security
# ══════════════════════════════════════════════════════════════════════════════


class TestPathSecurity:
    def test_absolute_path_rejected(self, tmp_path):
        adapter = ValidationAdapter(workspace_root=tmp_path)
        result = adapter.execute(_make_request("/etc/passwd"))
        assert result.success is False
        assert "absolute" in (result.error or "")

    def test_traversal_rejected(self, tmp_path):
        adapter = ValidationAdapter(workspace_root=tmp_path)
        result = adapter.execute(_make_request("../../etc/passwd"))
        assert result.success is False
        assert "escapes" in (result.error or "")

    def test_empty_path_rejected(self, tmp_path):
        adapter = ValidationAdapter(workspace_root=tmp_path)
        result = adapter.execute(_make_request(""))
        assert result.success is False
        assert "missing 'path'" in (result.error or "")

    def test_valid_nested_path_accepted(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        _write(sub, "ok.py", "x = 1\n")
        adapter = ValidationAdapter(workspace_root=tmp_path)
        result = adapter.execute(_make_request("sub/ok.py"))
        assert result.success is True


# ══════════════════════════════════════════════════════════════════════════════
# Adapter type mismatch
# ══════════════════════════════════════════════════════════════════════════════


class TestAdapterTypeMismatch:
    def test_wrong_adapter_type_rejected(self, tmp_path):
        adapter = ValidationAdapter(workspace_root=tmp_path)
        request = _make_request("foo.py", adapter_type=ExecutionAdapter.FILESYSTEM)
        result = adapter.execute(request)
        assert result.success is False
        assert result.error is not None
        assert "VALIDATION" in result.error

    def test_correct_adapter_type_accepted(self, tmp_path):
        _write(tmp_path, "ok.py", "x = 1\n")
        adapter = ValidationAdapter(workspace_root=tmp_path)
        result = adapter.execute(_make_request("ok.py", adapter_type=ExecutionAdapter.VALIDATION))
        assert result.success is True


# ══════════════════════════════════════════════════════════════════════════════
# adapter_metadata
# ══════════════════════════════════════════════════════════════════════════════


class TestAdapterMetadata:
    def test_metadata_is_sorted_tuple_of_string_pairs(self, tmp_path):
        _write(tmp_path, "ok.py", "x = 1\n")
        adapter = ValidationAdapter(workspace_root=tmp_path)
        result = adapter.execute(_make_request("ok.py"))
        assert isinstance(result.adapter_metadata, tuple)
        for item in result.adapter_metadata:
            assert isinstance(item, tuple)
            assert len(item) == 2
            assert isinstance(item[0], str)
            assert isinstance(item[1], str)
        keys = [k for k, _ in result.adapter_metadata]
        assert keys == sorted(keys)

    def test_metadata_contains_validator_and_language(self, tmp_path):
        _write(tmp_path, "ok.py", "x = 1\n")
        adapter = ValidationAdapter(workspace_root=tmp_path)
        result = adapter.execute(_make_request("ok.py"))
        meta = dict(result.adapter_metadata)
        assert "validator" in meta
        assert "language" in meta
        assert meta["validator"] == "python_syntax"
        assert meta["language"] == "python"
