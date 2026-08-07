"""Tests for Bootstrap Phase 4 — ValidationAdapter run_tests path.

Coverage:
  - TestRunRequest, TestRunResult, TestRunExecutionResult contract fields
  - run_tests: empty test_command → executed=False, success=True (skip)
  - run_tests: subprocess exit 0 → success=True, executed=True
  - run_tests: subprocess exit 1 → success=False, executed=True, error populated
  - run_tests: subprocess binary not found → success=False, executed=True, no raise
  - run_tests: subprocess timeout → success=False, executed=True, no raise
  - run_tests: absolute repository_path rejected
  - run_tests: path traversal rejected
  - run_tests: missing repository_path key → success=False
  - run_tests: wrong adapter_type → success=False, never executes
  - _parse_test_counts: passed only, failed only, mixed, no match
  - TestRunResult.executed distinguishes "ran and passed" from "not applicable"
  - adapter_metadata is sorted tuple of string pairs
  - all dataclasses are frozen and slotted
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from hermes.adapters.validation_adapter import ValidationAdapter, _parse_test_counts
from hermes.models.execution_gateway import ExecutionAdapter, ExecutionRequest
from hermes.models.validation_adapter import (
    TestRunExecutionResult,
    TestRunRequest,
    TestRunResult,
)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_run_tests_request(
    *,
    repository_path: str = ".",
    test_command: str = "pytest",
    adapter_type: ExecutionAdapter = ExecutionAdapter.VALIDATION,
    request_id: str = "req-test",
    operation_id: str = "op-test",
) -> ExecutionRequest:
    return ExecutionRequest(
        request_id=request_id,
        operation_id=operation_id,
        adapter_type=adapter_type,
        action_id="run_tests",
        payload=(
            ("repository_path", repository_path),
            ("test_command", test_command),
        ),
    )


# ══════════════════════════════════════════════════════════════════════════════
# Contract field tests
# ══════════════════════════════════════════════════════════════════════════════


class TestTestRunRequest:
    def test_fields_present(self):
        r = TestRunRequest(repository_path="my-repo", test_command="pytest")
        assert r.repository_path == "my-repo"
        assert r.test_command == "pytest"

    def test_frozen(self):
        r = TestRunRequest(repository_path=".", test_command="pytest")
        with pytest.raises(Exception):
            r.test_command = "cargo test"  # type: ignore[misc]

    def test_slots(self):
        r = TestRunRequest(repository_path=".", test_command="pytest")
        assert not hasattr(r, "__dict__")


class TestTestRunResult:
    def _make(self, *, executed: bool = True, success: bool = True) -> TestRunResult:
        return TestRunResult(
            success=success,
            executed=executed,
            reason="" if executed else "no_test_command_detected",
            exit_code=0 if success else 1,
            stdout="5 passed in 0.12s" if executed else "",
            stderr="",
            duration_ms=120 if executed else 0,
            tests_run=5 if executed else 0,
            tests_failed=0,
            tests_passed=5 if executed else 0,
        )

    def test_fields_present_executed(self):
        r = self._make(executed=True, success=True)
        assert r.success is True
        assert r.executed is True
        assert r.reason == ""
        assert r.exit_code == 0
        assert r.tests_run == 5

    def test_fields_present_not_executed(self):
        r = self._make(executed=False, success=True)
        assert r.success is True
        assert r.executed is False
        assert r.reason == "no_test_command_detected"
        assert r.exit_code == 0
        assert r.tests_run == 0

    def test_executed_true_not_same_as_success_true(self):
        """executed=False, success=True means 'not applicable', not 'passed'."""
        skipped = self._make(executed=False, success=True)
        passed = self._make(executed=True, success=True)
        assert skipped.success == passed.success
        assert skipped.executed != passed.executed

    def test_frozen(self):
        r = self._make()
        with pytest.raises(Exception):
            r.success = False  # type: ignore[misc]

    def test_slots(self):
        assert not hasattr(self._make(), "__dict__")


class TestTestRunExecutionResult:
    def test_fields_present(self):
        r = TestRunExecutionResult(
            request_id="req-1",
            operation_id="op-1",
            success=True,
            error=None,
            test_run_request=None,
            test_run_result=None,
            adapter_metadata=(("k", "v"),),
        )
        assert r.request_id == "req-1"
        assert r.success is True
        assert r.error is None

    def test_frozen(self):
        r = TestRunExecutionResult(
            request_id="r", operation_id="o", success=True, error=None,
            test_run_request=None, test_run_result=None, adapter_metadata=(),
        )
        with pytest.raises(Exception):
            r.success = False  # type: ignore[misc]

    def test_slots(self):
        r = TestRunExecutionResult(
            request_id="r", operation_id="o", success=True, error=None,
            test_run_request=None, test_run_result=None, adapter_metadata=(),
        )
        assert not hasattr(r, "__dict__")


# ══════════════════════════════════════════════════════════════════════════════
# _parse_test_counts
# ══════════════════════════════════════════════════════════════════════════════


class TestParseTestCounts:
    def test_passed_only(self):
        run, failed, passed = _parse_test_counts("5 passed in 0.12s")
        assert passed == 5
        assert failed == 0
        assert run == 5

    def test_failed_only(self):
        run, failed, passed = _parse_test_counts("2 failed in 0.05s")
        assert failed == 2
        assert passed == 0
        assert run == 2

    def test_mixed(self):
        run, failed, passed = _parse_test_counts("3 passed, 1 failed in 0.09s")
        assert passed == 3
        assert failed == 1
        assert run == 4

    def test_no_match_returns_zeros(self):
        run, failed, passed = _parse_test_counts("error: no tests found")
        assert run == 0
        assert failed == 0
        assert passed == 0

    def test_empty_string_returns_zeros(self):
        run, failed, passed = _parse_test_counts("")
        assert run == 0 and failed == 0 and passed == 0


# ══════════════════════════════════════════════════════════════════════════════
# Empty test_command → not-executed (skip) path
# ══════════════════════════════════════════════════════════════════════════════


class TestEmptyTestCommand:
    def test_empty_command_returns_success(self, tmp_path):
        adapter = ValidationAdapter(workspace_root=tmp_path)
        result = adapter.execute(_make_run_tests_request(test_command=""))
        assert isinstance(result, TestRunExecutionResult)
        assert result.success is True

    def test_empty_command_executed_is_false(self, tmp_path):
        adapter = ValidationAdapter(workspace_root=tmp_path)
        result = adapter.execute(_make_run_tests_request(test_command=""))
        assert result.test_run_result is not None
        assert result.test_run_result.executed is False

    def test_empty_command_reason_set(self, tmp_path):
        adapter = ValidationAdapter(workspace_root=tmp_path)
        result = adapter.execute(_make_run_tests_request(test_command=""))
        assert result.test_run_result is not None
        assert result.test_run_result.reason == "no_test_command_detected"

    def test_empty_command_no_subprocess_invoked(self, tmp_path):
        adapter = ValidationAdapter(workspace_root=tmp_path)
        with patch("subprocess.run") as mock_run:
            adapter.execute(_make_run_tests_request(test_command=""))
            mock_run.assert_not_called()

    def test_empty_command_error_is_none(self, tmp_path):
        adapter = ValidationAdapter(workspace_root=tmp_path)
        result = adapter.execute(_make_run_tests_request(test_command=""))
        assert result.error is None

    def test_whitespace_only_command_treated_as_empty(self, tmp_path):
        adapter = ValidationAdapter(workspace_root=tmp_path)
        result = adapter.execute(_make_run_tests_request(test_command="   "))
        assert result.test_run_result is not None
        assert result.test_run_result.executed is False


# ══════════════════════════════════════════════════════════════════════════════
# Path security
# ══════════════════════════════════════════════════════════════════════════════


class TestRunTestsPathSecurity:
    def test_absolute_repository_path_rejected(self, tmp_path):
        adapter = ValidationAdapter(workspace_root=tmp_path)
        result = adapter.execute(_make_run_tests_request(repository_path="/etc"))
        assert isinstance(result, TestRunExecutionResult)
        assert result.success is False
        assert "absolute" in (result.error or "")

    def test_traversal_repository_path_rejected(self, tmp_path):
        adapter = ValidationAdapter(workspace_root=tmp_path)
        result = adapter.execute(_make_run_tests_request(repository_path="../../etc"))
        assert result.success is False
        assert "escapes" in (result.error or "")

    def test_missing_repository_path_key_rejected(self, tmp_path):
        adapter = ValidationAdapter(workspace_root=tmp_path)
        request = ExecutionRequest(
            request_id="req-1",
            operation_id="op-1",
            adapter_type=ExecutionAdapter.VALIDATION,
            action_id="run_tests",
            payload=(("test_command", "pytest"),),  # repository_path absent
        )
        result = adapter.execute(request)
        assert result.success is False
        assert result.error is not None

    def test_valid_dot_repository_path_accepted_when_command_empty(self, tmp_path):
        """Dot path is valid even if test suite is not run (empty command)."""
        adapter = ValidationAdapter(workspace_root=tmp_path)
        result = adapter.execute(_make_run_tests_request(
            repository_path=".", test_command=""
        ))
        assert result.success is True  # skip path


# ══════════════════════════════════════════════════════════════════════════════
# Wrong adapter type
# ══════════════════════════════════════════════════════════════════════════════


class TestRunTestsAdapterTypeMismatch:
    def test_wrong_adapter_type_rejected(self, tmp_path):
        adapter = ValidationAdapter(workspace_root=tmp_path)
        result = adapter.execute(_make_run_tests_request(
            adapter_type=ExecutionAdapter.FILESYSTEM
        ))
        # Returns ValidationExecutionResult because type check fires first
        assert result.success is False
        assert result.error is not None


# ══════════════════════════════════════════════════════════════════════════════
# Subprocess success and failure
# ══════════════════════════════════════════════════════════════════════════════


class TestRunTestsSubprocess:
    def test_exit_zero_returns_success(self, tmp_path):
        """subprocess.run returning exit code 0 → success=True, executed=True."""
        (tmp_path / "myrepo").mkdir()
        adapter = ValidationAdapter(workspace_root=tmp_path)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = type("R", (), {
                "returncode": 0, "stdout": "1 passed in 0.01s", "stderr": ""
            })()
            result = adapter.execute(_make_run_tests_request(
                repository_path="myrepo", test_command="pytest"
            ))
        assert isinstance(result, TestRunExecutionResult)
        assert result.success is True
        assert result.test_run_result is not None
        assert result.test_run_result.executed is True
        assert result.test_run_result.exit_code == 0

    def test_exit_nonzero_returns_failure(self, tmp_path):
        """subprocess.run returning exit code 1 → success=False, executed=True."""
        (tmp_path / "myrepo").mkdir()
        adapter = ValidationAdapter(workspace_root=tmp_path)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = type("R", (), {
                "returncode": 1,
                "stdout": "1 failed in 0.02s",
                "stderr": "FAILED test_foo.py::test_bar",
            })()
            result = adapter.execute(_make_run_tests_request(
                repository_path="myrepo", test_command="pytest"
            ))
        assert result.success is False
        assert result.error is not None
        assert "test_run_failed" in result.error
        assert result.test_run_result is not None
        assert result.test_run_result.executed is True
        assert result.test_run_result.exit_code == 1

    def test_failure_does_not_raise(self, tmp_path):
        """A test failure must never raise an exception."""
        (tmp_path / "myrepo").mkdir()
        adapter = ValidationAdapter(workspace_root=tmp_path)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = type("R", (), {
                "returncode": 1, "stdout": "", "stderr": "error",
            })()
            result = adapter.execute(_make_run_tests_request(
                repository_path="myrepo", test_command="pytest"
            ))
        assert isinstance(result, TestRunExecutionResult)

    def test_binary_not_found_returns_failure(self, tmp_path):
        """FileNotFoundError → success=False, executed=True, no raise."""
        (tmp_path / "myrepo").mkdir()
        adapter = ValidationAdapter(workspace_root=tmp_path)
        with patch("subprocess.run", side_effect=FileNotFoundError("not found")):
            result = adapter.execute(_make_run_tests_request(
                repository_path="myrepo", test_command="nonexistent-runner"
            ))
        assert isinstance(result, TestRunExecutionResult)
        assert result.success is False
        assert result.test_run_result is not None
        assert result.test_run_result.executed is True
        assert result.test_run_result.exit_code == -1
        assert "not_found" in result.test_run_result.stderr or "binary_not_found" in result.test_run_result.stderr

    def test_timeout_returns_failure(self, tmp_path):
        """TimeoutExpired → success=False, executed=True, no raise."""
        (tmp_path / "myrepo").mkdir()
        adapter = ValidationAdapter(workspace_root=tmp_path)
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("pytest", 120)):
            result = adapter.execute(_make_run_tests_request(
                repository_path="myrepo", test_command="pytest"
            ))
        assert isinstance(result, TestRunExecutionResult)
        assert result.success is False
        assert result.test_run_result is not None
        assert "timeout" in result.test_run_result.stderr

    def test_unexpected_exception_returns_failure(self, tmp_path):
        """Unexpected subprocess exception → success=False, no raise."""
        (tmp_path / "myrepo").mkdir()
        adapter = ValidationAdapter(workspace_root=tmp_path)
        with patch("subprocess.run", side_effect=OSError("disk full")):
            result = adapter.execute(_make_run_tests_request(
                repository_path="myrepo", test_command="pytest"
            ))
        assert result.success is False
        assert result.test_run_result is not None
        assert result.test_run_result.executed is True

    def test_test_counts_populated_from_output(self, tmp_path):
        """Counts parsed from stdout when tests pass."""
        (tmp_path / "myrepo").mkdir()
        adapter = ValidationAdapter(workspace_root=tmp_path)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = type("R", (), {
                "returncode": 0, "stdout": "3 passed in 0.05s", "stderr": ""
            })()
            result = adapter.execute(_make_run_tests_request(
                repository_path="myrepo", test_command="pytest"
            ))
        assert result.test_run_result is not None
        assert result.test_run_result.tests_passed == 3
        assert result.test_run_result.tests_run == 3
        assert result.test_run_result.tests_failed == 0


# ══════════════════════════════════════════════════════════════════════════════
# adapter_metadata
# ══════════════════════════════════════════════════════════════════════════════


class TestRunTestsMetadata:
    def test_metadata_is_sorted_tuple_of_string_pairs(self, tmp_path):
        adapter = ValidationAdapter(workspace_root=tmp_path)
        result = adapter.execute(_make_run_tests_request(test_command=""))
        assert isinstance(result.adapter_metadata, tuple)
        for item in result.adapter_metadata:
            assert isinstance(item, tuple)
            assert len(item) == 2
            assert isinstance(item[0], str)
            assert isinstance(item[1], str)
        keys = [k for k, _ in result.adapter_metadata]
        assert keys == sorted(keys)

    def test_metadata_executed_false_on_skip(self, tmp_path):
        adapter = ValidationAdapter(workspace_root=tmp_path)
        result = adapter.execute(_make_run_tests_request(test_command=""))
        meta = dict(result.adapter_metadata)
        assert meta.get("executed") == "false"

    def test_metadata_executed_true_on_run(self, tmp_path):
        (tmp_path / "repo").mkdir()
        adapter = ValidationAdapter(workspace_root=tmp_path)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = type("R", (), {
                "returncode": 0, "stdout": "1 passed", "stderr": ""
            })()
            result = adapter.execute(_make_run_tests_request(
                repository_path="repo", test_command="pytest"
            ))
        meta = dict(result.adapter_metadata)
        assert meta.get("executed") == "true"
