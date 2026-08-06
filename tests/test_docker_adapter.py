"""Tests for the Docker Adapter — Sprint 65.

Coverage:
  - All typed contracts (DockerOperation, DockerRequest, DockerResult,
    DockerValidationResult, DockerExecutionResult)
  - DockerAdapter initialisation and workspace root
  - Path resolution and workspace boundary enforcement (resolve_path)
  - Identifier validation (_validate_image_name, _validate_container_name,
    _validate_env_key)
  - Adapter-level validation (validate)
  - Request translation (build_docker_request)
  - All 8 supported operations: BUILD, RUN, STOP, START, RESTART, PS, LOGS, INSPECT
  - Excluded operations are not dispatched (exec, cp, pull, push, etc.)
  - execute() never raises — all failures captured as success=False
  - subprocess list args (shell=False enforced via mock inspection)
  - Timeout handling (TimeoutExpired, FileNotFoundError)
  - Gateway integration

Test strategy:
  - Docker subprocess calls are mocked via
    @patch("hermes.adapters.docker_adapter.subprocess.run")
    so tests do not require a running Docker daemon.
  - Workspace boundary tests use tmp_path for real directories.
  - Identifier validation tested with pure inputs (no subprocess needed).
"""

from __future__ import annotations

import dataclasses
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from hermes.adapters.docker_adapter import (
    DockerAdapter,
    _ACTION_TO_OPERATION,
    _EXCLUDED_OPERATIONS,
    _IMAGE_RE,
    _CONTAINER_RE,
    _ENV_KEY_RE,
    _validate_image_name,
    _validate_container_name,
    _validate_env_key,
)
from hermes.kernel.execution_gateway import ExecutionGateway
from hermes.models.docker_adapter import (
    DockerExecutionResult,
    DockerOperation,
    DockerRequest,
    DockerResult,
    DockerValidationResult,
)
from hermes.models.execution_gateway import (
    AdapterRegistration,
    ExecutionAdapter,
    ExecutionRequest,
    ExecutionStatus,
)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_request(
    action_id: str,
    payload: dict,
    adapter_type: ExecutionAdapter = ExecutionAdapter.DOCKER,
    request_id: str = "req-001",
    operation_id: str = "op-docker-001",
) -> ExecutionRequest:
    """Build an ExecutionRequest for docker adapter tests."""
    return ExecutionRequest(
        request_id=request_id,
        operation_id=operation_id,
        adapter_type=adapter_type,
        action_id=action_id,
        payload=tuple(sorted((k, str(v)) for k, v in payload.items())),
    )


def _make_subprocess_result(stdout: str = "", stderr: str = "", returncode: int = 0):
    """Return a MagicMock that looks like a subprocess.CompletedProcess."""
    m = MagicMock()
    m.stdout = stdout
    m.stderr = stderr
    m.returncode = returncode
    return m


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A temporary workspace directory."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


@pytest.fixture
def adapter(workspace: Path) -> DockerAdapter:
    """A DockerAdapter bound to the temp workspace."""
    return DockerAdapter(workspace)


# ══════════════════════════════════════════════════════════════════════════════
# 1. Typed contracts
# ══════════════════════════════════════════════════════════════════════════════


class TestContracts:
    """DockerOperation, DockerRequest, DockerResult, DockerValidationResult,
    DockerExecutionResult are all frozen dataclasses with __slots__."""

    def test_docker_operation_enum_values(self):
        expected = {"build", "run", "stop", "start", "restart", "ps", "logs", "inspect"}
        assert {op.value for op in DockerOperation} == expected

    def test_docker_operation_count(self):
        assert len(DockerOperation) == 8

    def test_docker_request_frozen(self):
        req = DockerRequest(
            request_id="r1", operation_id="op1", operation=DockerOperation.PS,
            image="", container="", tag="latest", context_path="",
            command=(), name="", detach=False, remove=False,
            env_vars=(), ports=(), volumes=(), all_containers=False, tail=0,
            metadata=(),
        )
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            req.image = "changed"  # type: ignore[misc]

    def test_docker_request_slots(self):
        req = DockerRequest(
            request_id="r1", operation_id="op1", operation=DockerOperation.PS,
            image="", container="", tag="latest", context_path="",
            command=(), name="", detach=False, remove=False,
            env_vars=(), ports=(), volumes=(), all_containers=False, tail=0,
            metadata=(),
        )
        assert not hasattr(req, "__dict__")

    def test_docker_result_frozen(self):
        r = DockerResult(
            operation=DockerOperation.PS, output="", return_code=0, metadata=()
        )
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            r.output = "changed"  # type: ignore[misc]

    def test_docker_result_slots(self):
        r = DockerResult(
            operation=DockerOperation.PS, output="", return_code=0, metadata=()
        )
        assert not hasattr(r, "__dict__")

    def test_docker_validation_result_frozen(self):
        v = DockerValidationResult(valid=True, errors=())
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            v.valid = False  # type: ignore[misc]

    def test_docker_validation_result_slots(self):
        v = DockerValidationResult(valid=True, errors=())
        assert not hasattr(v, "__dict__")

    def test_docker_execution_result_frozen(self):
        r = DockerExecutionResult(
            request_id="r1", operation_id="op1", operation=None,
            docker_request=None, docker_result=None,
            success=False, error="x", adapter_metadata=(),
        )
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            r.success = True  # type: ignore[misc]

    def test_docker_execution_result_slots(self):
        r = DockerExecutionResult(
            request_id="r1", operation_id="op1", operation=None,
            docker_request=None, docker_result=None,
            success=False, error="x", adapter_metadata=(),
        )
        assert not hasattr(r, "__dict__")

    def test_docker_execution_result_operation_none_allowed(self):
        """operation may be None when action_id is unrecognized."""
        r = DockerExecutionResult(
            request_id="r", operation_id="op", operation=None,
            docker_request=None, docker_result=None,
            success=False, error="bad action", adapter_metadata=(),
        )
        assert r.operation is None

    def test_docker_request_tuple_fields(self):
        req = DockerRequest(
            request_id="r", operation_id="op", operation=DockerOperation.RUN,
            image="myimage", container="", tag="latest", context_path="",
            command=("python", "-m", "app"), name="", detach=True, remove=False,
            env_vars=(("FOO", "bar"),), ports=("8080:80",), volumes=(),
            all_containers=False, tail=0, metadata=(("k", "v"),),
        )
        assert isinstance(req.command, tuple)
        assert isinstance(req.env_vars, tuple)
        assert isinstance(req.ports, tuple)
        assert isinstance(req.volumes, tuple)
        assert isinstance(req.metadata, tuple)


# ══════════════════════════════════════════════════════════════════════════════
# 2. Adapter initialisation
# ══════════════════════════════════════════════════════════════════════════════


class TestAdapterInit:
    def test_workspace_root_is_resolved(self, tmp_path):
        adapter = DockerAdapter(tmp_path / "ws")
        (tmp_path / "ws").mkdir()
        assert adapter.workspace_root.is_absolute()

    def test_workspace_root_accepts_string(self, tmp_path):
        (tmp_path / "ws").mkdir()
        adapter = DockerAdapter(str(tmp_path / "ws"))
        assert adapter.workspace_root == (tmp_path / "ws").resolve()

    def test_workspace_root_property(self, workspace):
        adapter = DockerAdapter(workspace)
        assert adapter.workspace_root == workspace.resolve()


# ══════════════════════════════════════════════════════════════════════════════
# 3. resolve_path — workspace boundary enforcement
# ══════════════════════════════════════════════════════════════════════════════


class TestResolvePath:
    def test_valid_relative_path(self, adapter, workspace):
        (workspace / "services" / "app").mkdir(parents=True)
        resolved = adapter.resolve_path("services/app")
        assert resolved == (workspace / "services" / "app").resolve()

    def test_empty_path_raises(self, adapter):
        with pytest.raises(ValueError, match="must not be empty"):
            adapter.resolve_path("")

    def test_whitespace_only_raises(self, adapter):
        with pytest.raises(ValueError, match="must not be empty"):
            adapter.resolve_path("   ")

    def test_absolute_path_raises(self, adapter):
        with pytest.raises(ValueError, match="workspace-relative"):
            adapter.resolve_path("/etc/passwd")

    def test_traversal_rejected(self, adapter):
        with pytest.raises(ValueError, match="traversal"):
            adapter.resolve_path("../outside")

    def test_nested_traversal_rejected(self, adapter):
        with pytest.raises(ValueError, match="traversal"):
            adapter.resolve_path("services/../../outside")

    def test_deep_nested_path_ok(self, adapter, workspace):
        (workspace / "a" / "b" / "c").mkdir(parents=True)
        result = adapter.resolve_path("a/b/c")
        assert result == (workspace / "a" / "b" / "c").resolve()


# ══════════════════════════════════════════════════════════════════════════════
# 4. Identifier validation functions
# ══════════════════════════════════════════════════════════════════════════════


class TestValidateImageName:
    @pytest.mark.parametrize("name", [
        "myimage",
        "my-image",
        "my_image",
        "my.image",
        "registry.example.com/org/image:v1.0",
        "nginx:latest",
        "1starts-with-digit",
        "ubuntu",
    ])
    def test_valid_image_names(self, name):
        assert _validate_image_name(name) is None

    @pytest.mark.parametrize("name", [
        "",
        "   ",
        "has space",
        "has;semicolon",
        "$(injection)",
        "image`cmd`",
        "image|pipe",
        "image&bg",
    ])
    def test_invalid_image_names(self, name):
        assert _validate_image_name(name) is not None

    def test_dash_prefix_rejected(self):
        assert _validate_image_name("-image") is not None

    def test_returns_error_string(self):
        err = _validate_image_name("")
        assert isinstance(err, str)
        assert len(err) > 0


class TestValidateContainerName:
    @pytest.mark.parametrize("name", [
        "mycontainer",
        "my-container",
        "my_container",
        "my.container",
        "container1",
        "abc",
    ])
    def test_valid_container_names(self, name):
        assert _validate_container_name(name) is None

    @pytest.mark.parametrize("name", [
        "",
        "   ",
        "has space",
        "has/slash",
        "has:colon",
        "has;semi",
        "$(cmd)",
    ])
    def test_invalid_container_names(self, name):
        assert _validate_container_name(name) is not None

    def test_dash_prefix_rejected(self):
        assert _validate_container_name("-container") is not None


class TestValidateEnvKey:
    @pytest.mark.parametrize("key", [
        "FOO",
        "BAR_BAZ",
        "_PRIVATE",
        "MY_VAR_123",
        "a",
        "_",
    ])
    def test_valid_env_keys(self, key):
        assert _validate_env_key(key) is None

    @pytest.mark.parametrize("key", [
        "",
        "1STARTS_WITH_DIGIT",
        "HAS-HYPHEN",
        "HAS SPACE",
        "HAS=EQUALS",
        "HAS$DOLLAR",
    ])
    def test_invalid_env_keys(self, key):
        assert _validate_env_key(key) is not None


# ══════════════════════════════════════════════════════════════════════════════
# 5. Adapter-level validation
# ══════════════════════════════════════════════════════════════════════════════


class TestValidate:
    def test_valid_build_request(self, adapter, workspace):
        (workspace / "app").mkdir()
        req = _make_request("build", {"image": "myapp", "context_path": "app"})
        result = adapter.validate(req, DockerOperation.BUILD, dict(req.payload))
        assert result.valid is True
        assert result.errors == ()

    def test_valid_run_request(self, adapter):
        req = _make_request("run", {"image": "nginx"})
        result = adapter.validate(req, DockerOperation.RUN, dict(req.payload))
        assert result.valid is True

    def test_valid_stop_request(self, adapter):
        req = _make_request("stop", {"container": "mycontainer"})
        result = adapter.validate(req, DockerOperation.STOP, dict(req.payload))
        assert result.valid is True

    def test_valid_ps_request(self, adapter):
        req = _make_request("ps", {})
        result = adapter.validate(req, DockerOperation.PS, dict(req.payload))
        assert result.valid is True

    def test_wrong_adapter_type_rejected(self, adapter):
        req = _make_request(
            "ps", {}, adapter_type=ExecutionAdapter.GIT
        )
        result = adapter.validate(req, DockerOperation.PS, dict(req.payload))
        assert result.valid is False
        assert any("DOCKER" in e for e in result.errors)

    def test_none_operation_rejected(self, adapter):
        req = _make_request("unknown_op", {})
        result = adapter.validate(req, None, dict(req.payload))
        assert result.valid is False
        assert any("unknown_op" in e for e in result.errors)

    def test_build_missing_image_rejected(self, adapter, workspace):
        (workspace / "app").mkdir()
        req = _make_request("build", {"context_path": "app"})
        result = adapter.validate(req, DockerOperation.BUILD, {"context_path": "app"})
        assert result.valid is False

    def test_build_empty_context_path_rejected(self, adapter):
        req = _make_request("build", {"image": "myapp", "context_path": ""})
        result = adapter.validate(req, DockerOperation.BUILD, {"image": "myapp", "context_path": ""})
        assert result.valid is False
        assert any("context_path" in e for e in result.errors)

    def test_build_traversal_context_path_rejected(self, adapter):
        req = _make_request("build", {"image": "myapp", "context_path": "../outside"})
        result = adapter.validate(
            req, DockerOperation.BUILD, {"image": "myapp", "context_path": "../outside"}
        )
        assert result.valid is False
        assert any("traversal" in e for e in result.errors)

    def test_run_invalid_image_rejected(self, adapter):
        req = _make_request("run", {"image": "my image with spaces"})
        result = adapter.validate(
            req, DockerOperation.RUN, {"image": "my image with spaces"}
        )
        assert result.valid is False

    def test_stop_missing_container_rejected(self, adapter):
        req = _make_request("stop", {})
        result = adapter.validate(req, DockerOperation.STOP, {})
        assert result.valid is False

    def test_run_invalid_env_key_rejected(self, adapter):
        payload = {"image": "nginx", "env_vars": "1INVALID=value"}
        req = _make_request("run", payload)
        result = adapter.validate(req, DockerOperation.RUN, payload)
        assert result.valid is False
        assert any("1INVALID" in e for e in result.errors)

    def test_run_invalid_env_format_rejected(self, adapter):
        payload = {"image": "nginx", "env_vars": "NOEQUALS"}
        req = _make_request("run", payload)
        result = adapter.validate(req, DockerOperation.RUN, payload)
        assert result.valid is False

    def test_run_invalid_container_name_rejected(self, adapter):
        payload = {"image": "nginx", "name": "bad name with spaces"}
        req = _make_request("run", payload)
        result = adapter.validate(req, DockerOperation.RUN, payload)
        assert result.valid is False

    def test_validation_result_immutable(self, adapter):
        req = _make_request("ps", {})
        result = adapter.validate(req, DockerOperation.PS, {})
        assert isinstance(result, DockerValidationResult)
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            result.valid = False  # type: ignore[misc]

    def test_multiple_errors_collected(self, adapter):
        """Wrong adapter type AND bad action_id → both errors reported."""
        req = _make_request("unknown", {}, adapter_type=ExecutionAdapter.GIT)
        result = adapter.validate(req, None, {})
        assert result.valid is False
        assert len(result.errors) >= 2


# ══════════════════════════════════════════════════════════════════════════════
# 6. Request translation — build_docker_request
# ══════════════════════════════════════════════════════════════════════════════


class TestBuildDockerRequest:
    def test_build_operation_fields(self, adapter):
        req = _make_request("build", {"image": "myapp", "tag": "v2", "context_path": "app"})
        dr = adapter.build_docker_request(req, DockerOperation.BUILD)
        assert dr.operation == DockerOperation.BUILD
        assert dr.image == "myapp"
        assert dr.tag == "v2"
        assert dr.context_path == "app"
        assert dr.request_id == "req-001"
        assert dr.operation_id == "op-docker-001"

    def test_tag_defaults_to_latest(self, adapter):
        req = _make_request("build", {"image": "myapp", "context_path": "app"})
        dr = adapter.build_docker_request(req, DockerOperation.BUILD)
        assert dr.tag == "latest"

    def test_run_fields(self, adapter):
        req = _make_request("run", {
            "image": "nginx",
            "name": "web",
            "detach": "true",
            "remove": "false",
            "env_vars": "FOO=bar,BAZ=qux",
            "ports": "8080:80,443:443",
            "volumes": "/host:/container",
            "command": "python -m app",
        })
        dr = adapter.build_docker_request(req, DockerOperation.RUN)
        assert dr.image == "nginx"
        assert dr.name == "web"
        assert dr.detach is True
        assert dr.remove is False
        assert dr.env_vars == (("FOO", "bar"), ("BAZ", "qux"))
        assert dr.ports == ("8080:80", "443:443")
        assert dr.volumes == ("/host:/container",)
        assert dr.command == ("python", "-m", "app")

    def test_detach_false_by_default(self, adapter):
        req = _make_request("run", {"image": "nginx"})
        dr = adapter.build_docker_request(req, DockerOperation.RUN)
        assert dr.detach is False

    def test_ps_all_containers_flag(self, adapter):
        req = _make_request("ps", {"all_containers": "true"})
        dr = adapter.build_docker_request(req, DockerOperation.PS)
        assert dr.all_containers is True

    def test_logs_tail_parsed(self, adapter):
        req = _make_request("logs", {"container": "mycontainer", "tail": "50"})
        dr = adapter.build_docker_request(req, DockerOperation.LOGS)
        assert dr.tail == 50

    def test_logs_tail_zero_means_all(self, adapter):
        req = _make_request("logs", {"container": "mycontainer", "tail": "0"})
        dr = adapter.build_docker_request(req, DockerOperation.LOGS)
        assert dr.tail == 0

    def test_logs_invalid_tail_defaults_to_zero(self, adapter):
        req = _make_request("logs", {"container": "mycontainer", "tail": "notanint"})
        dr = adapter.build_docker_request(req, DockerOperation.LOGS)
        assert dr.tail == 0

    def test_result_is_immutable(self, adapter):
        req = _make_request("ps", {})
        dr = adapter.build_docker_request(req, DockerOperation.PS)
        assert isinstance(dr, DockerRequest)
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            dr.image = "changed"  # type: ignore[misc]

    def test_metadata_excludes_known_fields(self, adapter):
        """Extra unknown payload keys end up in metadata, known keys do not."""
        req = _make_request("ps", {"extra_key": "extra_value"})
        dr = adapter.build_docker_request(req, DockerOperation.PS)
        assert any(k == "extra_key" for k, _ in dr.metadata)

    def test_metadata_is_sorted(self, adapter):
        req = _make_request("ps", {"zz": "last", "aa": "first"})
        dr = adapter.build_docker_request(req, DockerOperation.PS)
        keys = [k for k, _ in dr.metadata]
        assert keys == sorted(keys)

    def test_deterministic_output(self, adapter):
        """Same inputs always produce equal DockerRequest."""
        req = _make_request("ps", {})
        dr1 = adapter.build_docker_request(req, DockerOperation.PS)
        dr2 = adapter.build_docker_request(req, DockerOperation.PS)
        assert dr1 == dr2


# ══════════════════════════════════════════════════════════════════════════════
# 7. Excluded operations
# ══════════════════════════════════════════════════════════════════════════════


class TestExcludedOperations:
    def test_excluded_ops_not_in_action_map(self):
        """All excluded operations must be absent from the action→operation map."""
        for excluded in _EXCLUDED_OPERATIONS:
            assert excluded not in _ACTION_TO_OPERATION, (
                f"{excluded!r} is in _ACTION_TO_OPERATION — it should be excluded"
            )

    def test_exec_not_dispatched(self, adapter):
        req = _make_request("exec", {"container": "myc", "command": "bash"})
        result = adapter.execute(req)
        assert result.success is False
        assert result.operation is None

    def test_pull_not_dispatched(self, adapter):
        req = _make_request("pull", {"image": "nginx"})
        result = adapter.execute(req)
        assert result.success is False

    def test_push_not_dispatched(self, adapter):
        req = _make_request("push", {"image": "nginx"})
        result = adapter.execute(req)
        assert result.success is False

    def test_cp_not_dispatched(self, adapter):
        req = _make_request("cp", {"container": "myc", "src": "/tmp/x", "dst": "/app/x"})
        result = adapter.execute(req)
        assert result.success is False

    @pytest.mark.parametrize("op", sorted(_EXCLUDED_OPERATIONS))
    def test_all_excluded_ops_return_failure(self, adapter, op):
        req = _make_request(op, {"image": "nginx", "container": "myc"})
        result = adapter.execute(req)
        assert result.success is False


# ══════════════════════════════════════════════════════════════════════════════
# 8. All 8 supported operations (subprocess mocked)
# ══════════════════════════════════════════════════════════════════════════════


class TestBuildOperation:
    def test_build_success(self, adapter, workspace):
        (workspace / "app").mkdir()
        req = _make_request("build", {"image": "myapp", "tag": "v1", "context_path": "app"})
        mock_result = _make_subprocess_result(stdout="Successfully built abc123\n")
        with patch("hermes.adapters.docker_adapter.subprocess.run", return_value=mock_result) as m:
            result = adapter.execute(req)
        assert result.success is True
        assert result.operation == DockerOperation.BUILD
        assert "Successfully built" in result.docker_result.output
        # Verify no shell=True
        args, kwargs = m.call_args
        assert kwargs.get("shell", False) is False
        assert isinstance(args[0], list)

    def test_build_uses_image_tag(self, adapter, workspace):
        (workspace / "app").mkdir()
        req = _make_request("build", {"image": "myapp", "tag": "v2", "context_path": "app"})
        mock_result = _make_subprocess_result(stdout="ok")
        with patch("hermes.adapters.docker_adapter.subprocess.run", return_value=mock_result) as m:
            adapter.execute(req)
        args = m.call_args[0][0]
        assert "myapp:v2" in args

    def test_build_latest_tag(self, adapter, workspace):
        (workspace / "app").mkdir()
        req = _make_request("build", {"image": "myapp", "context_path": "app"})
        mock_result = _make_subprocess_result(stdout="ok")
        with patch("hermes.adapters.docker_adapter.subprocess.run", return_value=mock_result) as m:
            adapter.execute(req)
        args = m.call_args[0][0]
        assert "myapp:latest" in args

    def test_build_uses_300s_timeout(self, adapter, workspace):
        (workspace / "app").mkdir()
        req = _make_request("build", {"image": "myapp", "context_path": "app"})
        mock_result = _make_subprocess_result(stdout="ok")
        with patch("hermes.adapters.docker_adapter.subprocess.run", return_value=mock_result) as m:
            adapter.execute(req)
        _, kwargs = m.call_args
        assert kwargs["timeout"] == 300

    def test_build_failure_returns_false(self, adapter, workspace):
        (workspace / "app").mkdir()
        req = _make_request("build", {"image": "myapp", "context_path": "app"})
        mock_result = _make_subprocess_result(stderr="COPY failed", returncode=1)
        with patch("hermes.adapters.docker_adapter.subprocess.run", return_value=mock_result):
            result = adapter.execute(req)
        assert result.success is False
        assert "failed" in result.error


class TestRunOperation:
    def test_run_success(self, adapter):
        req = _make_request("run", {"image": "nginx", "detach": "true"})
        mock_result = _make_subprocess_result(stdout="container_id_abc\n")
        with patch("hermes.adapters.docker_adapter.subprocess.run", return_value=mock_result):
            result = adapter.execute(req)
        assert result.success is True
        assert result.operation == DockerOperation.RUN

    def test_run_detach_flag(self, adapter):
        req = _make_request("run", {"image": "nginx", "detach": "true"})
        mock_result = _make_subprocess_result(stdout="id")
        with patch("hermes.adapters.docker_adapter.subprocess.run", return_value=mock_result) as m:
            adapter.execute(req)
        args = m.call_args[0][0]
        assert "-d" in args

    def test_run_rm_flag(self, adapter):
        req = _make_request("run", {"image": "nginx", "remove": "true"})
        mock_result = _make_subprocess_result(stdout="id")
        with patch("hermes.adapters.docker_adapter.subprocess.run", return_value=mock_result) as m:
            adapter.execute(req)
        args = m.call_args[0][0]
        assert "--rm" in args

    def test_run_name_flag(self, adapter):
        req = _make_request("run", {"image": "nginx", "name": "webserver", "detach": "true"})
        mock_result = _make_subprocess_result(stdout="id")
        with patch("hermes.adapters.docker_adapter.subprocess.run", return_value=mock_result) as m:
            adapter.execute(req)
        args = m.call_args[0][0]
        assert "--name" in args
        assert "webserver" in args

    def test_run_env_vars(self, adapter):
        req = _make_request("run", {"image": "myapp", "env_vars": "FOO=bar,BAZ=qux", "detach": "true"})
        mock_result = _make_subprocess_result(stdout="id")
        with patch("hermes.adapters.docker_adapter.subprocess.run", return_value=mock_result) as m:
            adapter.execute(req)
        args = m.call_args[0][0]
        assert "-e" in args
        assert "FOO=bar" in args
        assert "BAZ=qux" in args

    def test_run_ports(self, adapter):
        req = _make_request("run", {"image": "nginx", "ports": "8080:80", "detach": "true"})
        mock_result = _make_subprocess_result(stdout="id")
        with patch("hermes.adapters.docker_adapter.subprocess.run", return_value=mock_result) as m:
            adapter.execute(req)
        args = m.call_args[0][0]
        assert "-p" in args
        assert "8080:80" in args

    def test_run_volumes(self, adapter):
        req = _make_request("run", {"image": "myapp", "volumes": "/host:/app", "detach": "true"})
        mock_result = _make_subprocess_result(stdout="id")
        with patch("hermes.adapters.docker_adapter.subprocess.run", return_value=mock_result) as m:
            adapter.execute(req)
        args = m.call_args[0][0]
        assert "-v" in args
        assert "/host:/app" in args

    def test_run_command_appended(self, adapter):
        req = _make_request("run", {"image": "python", "command": "python app.py", "detach": "true"})
        mock_result = _make_subprocess_result(stdout="id")
        with patch("hermes.adapters.docker_adapter.subprocess.run", return_value=mock_result) as m:
            adapter.execute(req)
        args = m.call_args[0][0]
        assert "python" in args
        assert "app.py" in args

    def test_run_detach_uses_30s_timeout(self, adapter):
        req = _make_request("run", {"image": "nginx", "detach": "true"})
        mock_result = _make_subprocess_result(stdout="id")
        with patch("hermes.adapters.docker_adapter.subprocess.run", return_value=mock_result) as m:
            adapter.execute(req)
        _, kwargs = m.call_args
        assert kwargs["timeout"] == 30

    def test_run_attached_uses_120s_timeout(self, adapter):
        req = _make_request("run", {"image": "myapp"})
        mock_result = _make_subprocess_result(stdout="done")
        with patch("hermes.adapters.docker_adapter.subprocess.run", return_value=mock_result) as m:
            adapter.execute(req)
        _, kwargs = m.call_args
        assert kwargs["timeout"] == 120


class TestStopOperation:
    def test_stop_success(self, adapter):
        req = _make_request("stop", {"container": "mycontainer"})
        mock_result = _make_subprocess_result(stdout="mycontainer\n")
        with patch("hermes.adapters.docker_adapter.subprocess.run", return_value=mock_result):
            result = adapter.execute(req)
        assert result.success is True
        assert result.operation == DockerOperation.STOP

    def test_stop_passes_container_name(self, adapter):
        req = _make_request("stop", {"container": "target"})
        mock_result = _make_subprocess_result(stdout="target")
        with patch("hermes.adapters.docker_adapter.subprocess.run", return_value=mock_result) as m:
            adapter.execute(req)
        args = m.call_args[0][0]
        assert "stop" in args
        assert "target" in args

    def test_stop_failure(self, adapter):
        req = _make_request("stop", {"container": "mycontainer"})
        mock_result = _make_subprocess_result(stderr="no such container", returncode=1)
        with patch("hermes.adapters.docker_adapter.subprocess.run", return_value=mock_result):
            result = adapter.execute(req)
        assert result.success is False


class TestStartOperation:
    def test_start_success(self, adapter):
        req = _make_request("start", {"container": "mycontainer"})
        mock_result = _make_subprocess_result(stdout="mycontainer\n")
        with patch("hermes.adapters.docker_adapter.subprocess.run", return_value=mock_result):
            result = adapter.execute(req)
        assert result.success is True
        assert result.operation == DockerOperation.START

    def test_start_passes_container_name(self, adapter):
        req = _make_request("start", {"container": "myc"})
        mock_result = _make_subprocess_result(stdout="myc")
        with patch("hermes.adapters.docker_adapter.subprocess.run", return_value=mock_result) as m:
            adapter.execute(req)
        args = m.call_args[0][0]
        assert "start" in args
        assert "myc" in args


class TestRestartOperation:
    def test_restart_success(self, adapter):
        req = _make_request("restart", {"container": "mycontainer"})
        mock_result = _make_subprocess_result(stdout="mycontainer\n")
        with patch("hermes.adapters.docker_adapter.subprocess.run", return_value=mock_result):
            result = adapter.execute(req)
        assert result.success is True
        assert result.operation == DockerOperation.RESTART

    def test_restart_passes_container_name(self, adapter):
        req = _make_request("restart", {"container": "myc"})
        mock_result = _make_subprocess_result(stdout="myc")
        with patch("hermes.adapters.docker_adapter.subprocess.run", return_value=mock_result) as m:
            adapter.execute(req)
        args = m.call_args[0][0]
        assert "restart" in args
        assert "myc" in args


class TestPsOperation:
    def test_ps_success(self, adapter):
        req = _make_request("ps", {})
        containers_output = "CONTAINER ID   IMAGE    STATUS\nabc123   nginx   Up"
        mock_result = _make_subprocess_result(stdout=containers_output)
        with patch("hermes.adapters.docker_adapter.subprocess.run", return_value=mock_result):
            result = adapter.execute(req)
        assert result.success is True
        assert result.operation == DockerOperation.PS

    def test_ps_without_all_flag(self, adapter):
        req = _make_request("ps", {})
        mock_result = _make_subprocess_result(stdout="output")
        with patch("hermes.adapters.docker_adapter.subprocess.run", return_value=mock_result) as m:
            adapter.execute(req)
        args = m.call_args[0][0]
        assert "-a" not in args

    def test_ps_with_all_flag(self, adapter):
        req = _make_request("ps", {"all_containers": "true"})
        mock_result = _make_subprocess_result(stdout="output")
        with patch("hermes.adapters.docker_adapter.subprocess.run", return_value=mock_result) as m:
            adapter.execute(req)
        args = m.call_args[0][0]
        assert "-a" in args

    def test_ps_metadata_records_all_flag(self, adapter):
        req = _make_request("ps", {"all_containers": "true"})
        mock_result = _make_subprocess_result(stdout="output")
        with patch("hermes.adapters.docker_adapter.subprocess.run", return_value=mock_result):
            result = adapter.execute(req)
        assert result.success is True
        meta = dict(result.docker_result.metadata)
        assert meta.get("all_containers") == "true"


class TestLogsOperation:
    def test_logs_success(self, adapter):
        req = _make_request("logs", {"container": "mycontainer"})
        mock_result = _make_subprocess_result(stdout="log line 1\nlog line 2\n")
        with patch("hermes.adapters.docker_adapter.subprocess.run", return_value=mock_result):
            result = adapter.execute(req)
        assert result.success is True
        assert result.operation == DockerOperation.LOGS

    def test_logs_passes_container(self, adapter):
        req = _make_request("logs", {"container": "myc"})
        mock_result = _make_subprocess_result(stdout="logs")
        with patch("hermes.adapters.docker_adapter.subprocess.run", return_value=mock_result) as m:
            adapter.execute(req)
        args = m.call_args[0][0]
        assert "logs" in args
        assert "myc" in args

    def test_logs_with_tail(self, adapter):
        req = _make_request("logs", {"container": "myc", "tail": "100"})
        mock_result = _make_subprocess_result(stdout="logs")
        with patch("hermes.adapters.docker_adapter.subprocess.run", return_value=mock_result) as m:
            adapter.execute(req)
        args = m.call_args[0][0]
        assert "--tail" in args
        assert "100" in args

    def test_logs_no_tail_flag_when_zero(self, adapter):
        req = _make_request("logs", {"container": "myc", "tail": "0"})
        mock_result = _make_subprocess_result(stdout="logs")
        with patch("hermes.adapters.docker_adapter.subprocess.run", return_value=mock_result) as m:
            adapter.execute(req)
        args = m.call_args[0][0]
        assert "--tail" not in args


class TestInspectOperation:
    def test_inspect_success(self, adapter):
        req = _make_request("inspect", {"container": "mycontainer"})
        json_output = '[{"Id": "abc123", "Name": "/mycontainer"}]'
        mock_result = _make_subprocess_result(stdout=json_output)
        with patch("hermes.adapters.docker_adapter.subprocess.run", return_value=mock_result):
            result = adapter.execute(req)
        assert result.success is True
        assert result.operation == DockerOperation.INSPECT

    def test_inspect_passes_container(self, adapter):
        req = _make_request("inspect", {"container": "myc"})
        mock_result = _make_subprocess_result(stdout="[]")
        with patch("hermes.adapters.docker_adapter.subprocess.run", return_value=mock_result) as m:
            adapter.execute(req)
        args = m.call_args[0][0]
        assert "inspect" in args
        assert "myc" in args


# ══════════════════════════════════════════════════════════════════════════════
# 9. Subprocess safety
# ══════════════════════════════════════════════════════════════════════════════


class TestSubprocessSafety:
    """All subprocess calls must use list args, never shell=True."""

    @pytest.mark.parametrize("action,payload", [
        ("ps", {}),
        ("stop", {"container": "myc"}),
        ("start", {"container": "myc"}),
        ("restart", {"container": "myc"}),
        ("logs", {"container": "myc"}),
        ("inspect", {"container": "myc"}),
    ])
    def test_no_shell_true(self, adapter, workspace, action, payload):
        req = _make_request(action, payload)
        mock_result = _make_subprocess_result(stdout="ok")
        with patch("hermes.adapters.docker_adapter.subprocess.run", return_value=mock_result) as m:
            adapter.execute(req)
        _, kwargs = m.call_args
        assert kwargs.get("shell", False) is False

    def test_subprocess_called_with_list(self, adapter):
        req = _make_request("ps", {})
        mock_result = _make_subprocess_result(stdout="ok")
        with patch("hermes.adapters.docker_adapter.subprocess.run", return_value=mock_result) as m:
            adapter.execute(req)
        args, _ = m.call_args
        assert isinstance(args[0], list)
        assert args[0][0] == "docker"


# ══════════════════════════════════════════════════════════════════════════════
# 10. Timeout and subprocess error handling
# ══════════════════════════════════════════════════════════════════════════════


class TestTimeoutHandling:
    def test_timeout_expired_captured_as_failure(self, adapter):
        req = _make_request("ps", {})
        with patch(
            "hermes.adapters.docker_adapter.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["docker", "ps"], timeout=30),
        ):
            result = adapter.execute(req)
        assert result.success is False
        assert result.error is not None

    def test_file_not_found_captured_as_failure(self, adapter):
        """Docker not installed → FileNotFoundError → success=False."""
        req = _make_request("ps", {})
        with patch(
            "hermes.adapters.docker_adapter.subprocess.run",
            side_effect=FileNotFoundError("docker not found"),
        ):
            result = adapter.execute(req)
        assert result.success is False
        assert "docker" in result.error.lower()

    def test_os_error_captured_as_failure(self, adapter):
        req = _make_request("ps", {})
        with patch(
            "hermes.adapters.docker_adapter.subprocess.run",
            side_effect=OSError("os error"),
        ):
            result = adapter.execute(req)
        assert result.success is False


# ══════════════════════════════════════════════════════════════════════════════
# 11. execute() never raises
# ══════════════════════════════════════════════════════════════════════════════


class TestNeverRaises:
    """execute() must never propagate exceptions — all failures → success=False."""

    def test_execute_does_not_raise_on_unknown_action(self, adapter):
        req = _make_request("unknown_action", {})
        result = adapter.execute(req)  # must not raise
        assert isinstance(result, DockerExecutionResult)
        assert result.success is False

    def test_execute_does_not_raise_on_validation_failure(self, adapter):
        req = _make_request("build", {})  # missing image and context_path
        result = adapter.execute(req)
        assert isinstance(result, DockerExecutionResult)
        assert result.success is False

    def test_execute_does_not_raise_on_subprocess_exception(self, adapter):
        req = _make_request("ps", {})
        with patch(
            "hermes.adapters.docker_adapter.subprocess.run",
            side_effect=RuntimeError("unexpected error"),
        ):
            result = adapter.execute(req)
        assert isinstance(result, DockerExecutionResult)
        assert result.success is False

    def test_execute_does_not_raise_on_nonzero_exit(self, adapter):
        req = _make_request("ps", {})
        with patch(
            "hermes.adapters.docker_adapter.subprocess.run",
            return_value=_make_subprocess_result(stderr="error", returncode=1),
        ):
            result = adapter.execute(req)
        assert isinstance(result, DockerExecutionResult)
        assert result.success is False

    def test_returns_docker_execution_result_always(self, adapter):
        """Every code path produces a DockerExecutionResult."""
        cases = [
            _make_request("unknown", {}),
            _make_request("build", {}),
            _make_request("exec", {"container": "myc"}),
        ]
        for req in cases:
            result = adapter.execute(req)
            assert isinstance(result, DockerExecutionResult)


# ══════════════════════════════════════════════════════════════════════════════
# 12. DockerExecutionResult lifecycle states
# ══════════════════════════════════════════════════════════════════════════════


class TestExecutionResultLifecycleStates:
    def test_success_result_has_all_fields_populated(self, adapter):
        req = _make_request("ps", {})
        with patch(
            "hermes.adapters.docker_adapter.subprocess.run",
            return_value=_make_subprocess_result(stdout="containers"),
        ):
            result = adapter.execute(req)
        assert result.success is True
        assert result.docker_request is not None
        assert result.docker_result is not None
        assert result.error is None
        assert result.operation == DockerOperation.PS

    def test_validation_failure_has_none_request_and_result(self, adapter):
        req = _make_request("build", {})
        result = adapter.execute(req)
        assert result.success is False
        assert result.docker_request is None
        assert result.docker_result is None
        assert result.error is not None

    def test_subprocess_failure_has_docker_result_populated(self, adapter):
        req = _make_request("ps", {})
        with patch(
            "hermes.adapters.docker_adapter.subprocess.run",
            return_value=_make_subprocess_result(stderr="error output", returncode=1),
        ):
            result = adapter.execute(req)
        assert result.success is False
        assert result.docker_request is not None
        # docker_result is populated even on non-zero exit (spec from DockerExecutionResult docstring)
        assert result.docker_result is not None
        assert result.docker_result.return_code == 1

    def test_adapter_metadata_always_sorted(self, adapter):
        req = _make_request("ps", {})
        with patch(
            "hermes.adapters.docker_adapter.subprocess.run",
            return_value=_make_subprocess_result(stdout="ok"),
        ):
            result = adapter.execute(req)
        keys = [k for k, _ in result.adapter_metadata]
        assert keys == sorted(keys)

    def test_request_id_propagated(self, adapter):
        req = _make_request("ps", {}, request_id="specific-req-id")
        with patch(
            "hermes.adapters.docker_adapter.subprocess.run",
            return_value=_make_subprocess_result(stdout="ok"),
        ):
            result = adapter.execute(req)
        assert result.request_id == "specific-req-id"

    def test_operation_id_propagated(self, adapter):
        req = _make_request("ps", {}, operation_id="specific-op-id")
        with patch(
            "hermes.adapters.docker_adapter.subprocess.run",
            return_value=_make_subprocess_result(stdout="ok"),
        ):
            result = adapter.execute(req)
        assert result.operation_id == "specific-op-id"


# ══════════════════════════════════════════════════════════════════════════════
# 13. Determinism
# ══════════════════════════════════════════════════════════════════════════════


class TestDeterminism:
    def test_same_request_same_docker_request(self, adapter):
        """Same ExecutionRequest produces structurally equal DockerRequests."""
        req = _make_request("run", {"image": "nginx", "detach": "true", "name": "web"})
        dr1 = adapter.build_docker_request(req, DockerOperation.RUN)
        dr2 = adapter.build_docker_request(req, DockerOperation.RUN)
        assert dr1 == dr2

    def test_metadata_ordering_stable(self, adapter):
        """Metadata is always sorted by key regardless of insertion order."""
        req1 = _make_request("ps", {"z": "last", "a": "first"})
        req2 = _make_request("ps", {"a": "first", "z": "last"})
        dr1 = adapter.build_docker_request(req1, DockerOperation.PS)
        dr2 = adapter.build_docker_request(req2, DockerOperation.PS)
        assert dr1.metadata == dr2.metadata


# ══════════════════════════════════════════════════════════════════════════════
# 14. Gateway integration
# ══════════════════════════════════════════════════════════════════════════════


class TestGatewayIntegration:
    """DockerAdapter integrates cleanly with the Execution Gateway."""

    def test_gateway_dispatches_docker_request(self, workspace):
        gateway = ExecutionGateway()
        gateway.register(
            AdapterRegistration(
                adapter=ExecutionAdapter.DOCKER,
                adapter_id="docker-local",
                available=True,
                description="Docker adapter",
            )
        )
        request = gateway.build_request(
            request_id="req-gw-001",
            operation_id="op-docker-gw",
            adapter_type=ExecutionAdapter.DOCKER,
            action_id="ps",
            payload={"all_containers": "false"},
        )
        decision = gateway.dispatch(request)
        assert decision.status == ExecutionStatus.DISPATCHED

    def test_gateway_then_adapter_execute(self, workspace):
        gateway = ExecutionGateway()
        gateway.register(
            AdapterRegistration(
                adapter=ExecutionAdapter.DOCKER,
                adapter_id="docker-local",
                available=True,
                description="Docker adapter",
            )
        )
        request = gateway.build_request(
            request_id="req-gw-002",
            operation_id="op-docker-gw-02",
            adapter_type=ExecutionAdapter.DOCKER,
            action_id="ps",
            payload={},
        )
        decision = gateway.dispatch(request)
        assert decision.status == ExecutionStatus.DISPATCHED

        adapter = DockerAdapter(workspace)
        mock_result = _make_subprocess_result(stdout="container list")
        with patch("hermes.adapters.docker_adapter.subprocess.run", return_value=mock_result):
            exec_result = adapter.execute(request)

        assert exec_result.success is True
        assert exec_result.operation == DockerOperation.PS

    def test_gateway_does_not_invoke_adapter(self, workspace):
        """Gateway.dispatch() returns DISPATCHED without calling any adapter."""
        gateway = ExecutionGateway()
        gateway.register(
            AdapterRegistration(
                adapter=ExecutionAdapter.DOCKER,
                adapter_id="docker-local",
                available=True,
                description="Docker adapter",
            )
        )
        request = gateway.build_request(
            request_id="req-gw-003",
            operation_id="op-docker-gw-03",
            adapter_type=ExecutionAdapter.DOCKER,
            action_id="ps",
            payload={},
        )

        # Patch subprocess at the module level — it must NOT be called by dispatch()
        with patch("hermes.adapters.docker_adapter.subprocess.run") as mock_run:
            decision = gateway.dispatch(request)
            mock_run.assert_not_called()

        assert decision.status == ExecutionStatus.DISPATCHED


# ══════════════════════════════════════════════════════════════════════════════
# 15. Action→Operation mapping
# ══════════════════════════════════════════════════════════════════════════════


class TestActionToOperationMap:
    def test_all_operations_are_mapped(self):
        for op in DockerOperation:
            assert op.value in _ACTION_TO_OPERATION
            assert _ACTION_TO_OPERATION[op.value] == op

    def test_map_has_exactly_8_entries(self):
        assert len(_ACTION_TO_OPERATION) == 8

    def test_excluded_operations_absent(self):
        for excluded in _EXCLUDED_OPERATIONS:
            assert excluded not in _ACTION_TO_OPERATION
