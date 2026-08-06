"""Docker Adapter — controlled Docker access for Hermes operations.

Architecture position:
  Execution Gateway → Docker Adapter → Docker Engine (subprocess)

The Docker Adapter is NOT Docker. It translates Hermes execution contracts
(ExecutionRequest) into Docker subprocess invocations and translates Docker
results back into Hermes execution results (DockerExecutionResult).

  Dispatch contract vs execution (invariant from Sprint 59):
    The Execution Gateway is the ONLY dispatch boundary. No engine, conductor,
    or planner may invoke Docker directly. All Docker access arrives as an
    ExecutionRequest that has already passed through the Gateway.

  Permanent architecture:
    Execution Gateway
          ↓
    Docker Adapter   (this file — security + translation + execution)
          ↓
    Docker Engine    (subprocess: docker build / run / stop / start / ...)

Security model:
  Image and container identifiers are validated before any subprocess call.
  All subprocess invocations use a list of arguments (shell=False, never
  shell=True). Only predefined operations are dispatched via the handler
  table — arbitrary Docker subcommands cannot be injected.

  The Dockerfile build context path is resolved against workspace_root and
  must remain within the workspace boundary. Path traversal is rejected.

  Identifier validation rules:
    image name    → must match [a-zA-Z0-9][a-zA-Z0-9_.:\-/]* (no spaces,
                    no shell metacharacters, must not start with "-")
    container name → must match [a-zA-Z0-9][a-zA-Z0-9_.\-]* (Docker naming
                    convention, no registry path syntax)
    env var key   → must match [a-zA-Z_][a-zA-Z0-9_]* (POSIX env var name)

Operation semantics:
  BUILD   → docker build -t <image>[:<tag>] <resolved_context_path>
  RUN     → docker run [-d] [--rm] [--name <n>] [-e ...] [-p ...] [-v ...] <image> [cmd]
  STOP    → docker stop <container>
  START   → docker start <container>
  RESTART → docker restart <container>
  PS      → docker ps [-a]
  LOGS    → docker logs [--tail <n>] <container>
  INSPECT → docker inspect <container>

Excluded operations (intentional, not an oversight):
  docker exec   → arbitrary command execution inside a container violates
                  the predefined-command security invariant.
  docker cp     → file transfer into containers bypasses the Filesystem Adapter.
  docker pull/push/login → registry operations with credential handling are
                  outside current scope.
  docker network create / volume create / system prune → infrastructure and
                  destructive operations outside current scope.

Architecture invariants preserved:
  - The adapter never plans work.
  - The adapter never performs routing.
  - The adapter never calls LLMs, filesystems (beyond path resolution), Git, HTTP.
  - The adapter never bypasses the Execution Gateway.
  - The adapter never uses shell=True.
  - The adapter never raises exceptions — all failures are captured in
    DockerExecutionResult(success=False, error=...).

Sources of non-determinism introduced (explicit):
  - Docker image layer cache, build output, container runtime state.
  - Container IDs and image digests are non-deterministic.
  - The adapter's translation logic is deterministic given the same inputs.

Network calls introduced:
  - None directly. Docker Engine may pull image layers during build/run if
    images are not cached locally — this is outside the adapter's control.

Subprocess calls introduced:
  - _run_docker() runs ["docker", ...] via subprocess.run with shell=False.
  - validate() does NOT run docker — validation is pure Python logic only.
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

from hermes.models.docker_adapter import (
    DockerExecutionResult,
    DockerOperation,
    DockerRequest,
    DockerResult,
    DockerValidationResult,
)
from hermes.models.execution_gateway import ExecutionAdapter, ExecutionRequest

logger = logging.getLogger(__name__)

# ── Identifier validation patterns ────────────────────────────────────────────

# Image names: alphanumeric start, then alphanumeric + safe Docker path chars.
# Allows: registry/org/image:tag, image:tag, just image
_IMAGE_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.:\-/]*$")

# Container names: alphanumeric start, then alphanumeric + Docker name chars.
_CONTAINER_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.\-]*$")

# Environment variable key names: POSIX convention.
_ENV_KEY_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

# Operations requiring a valid image identifier
_IMAGE_REQUIRED: frozenset[DockerOperation] = frozenset({
    DockerOperation.BUILD,
    DockerOperation.RUN,
})

# Operations requiring a valid container identifier
_CONTAINER_REQUIRED: frozenset[DockerOperation] = frozenset({
    DockerOperation.STOP,
    DockerOperation.START,
    DockerOperation.RESTART,
    DockerOperation.LOGS,
    DockerOperation.INSPECT,
})

# Map action_id strings → DockerOperation enum values
_ACTION_TO_OPERATION: dict[str, DockerOperation] = {
    op.value: op for op in DockerOperation
}

# Operations intentionally excluded — listed for documentation.
# These strings will never appear in _ACTION_TO_OPERATION and are blocked by
# the unrecognized action_id validation path.
_EXCLUDED_OPERATIONS: frozenset[str] = frozenset({
    "exec", "cp", "pull", "push", "login", "logout",
    "network", "volume", "system", "prune",
})


def _validate_image_name(name: str) -> str | None:
    """Return None if name is valid; return error string if invalid."""
    if not name or not name.strip():
        return "image name must not be empty"
    if name.startswith("-"):
        return f"image name must not start with '-': {name!r}"
    if not _IMAGE_RE.match(name):
        return (
            f"image name contains invalid characters: {name!r} — "
            "only alphanumeric characters and _.:-/ are allowed"
        )
    return None


def _validate_container_name(name: str) -> str | None:
    """Return None if name is valid; return error string if invalid."""
    if not name or not name.strip():
        return "container name must not be empty"
    if name.startswith("-"):
        return f"container name must not start with '-': {name!r}"
    if not _CONTAINER_RE.match(name):
        return (
            f"container name contains invalid characters: {name!r} — "
            "only alphanumeric characters and _.- are allowed"
        )
    return None


def _validate_env_key(key: str) -> str | None:
    """Return None if env var key is valid; return error string if invalid."""
    if not key:
        return "environment variable key must not be empty"
    if not _ENV_KEY_RE.match(key):
        return (
            f"environment variable key {key!r} is not a valid POSIX identifier — "
            "must start with a letter or underscore, contain only alphanumeric or _"
        )
    return None


class DockerAdapter:
    """Controlled Docker adapter for Hermes operations.

    Receives ExecutionRequests from the Execution Gateway, translates them
    into Docker subprocess invocations using predefined command structures,
    and returns immutable DockerExecutionResults.

    Security guarantees:
      - No path may escape workspace_root for build context paths.
      - Only pre-approved Docker subcommands are dispatched.
      - subprocess.run is always called with a list (shell=False).
      - Image and container identifiers are validated before any call.
      - Excluded operations (exec, cp, pull, push, ...) are never dispatched.

    Usage::

        adapter = DockerAdapter(workspace_root="/tmp/hermes-workspace")

        request = gateway.build_request(
            request_id="req-001",
            operation_id="op-docker-build",
            adapter_type=ExecutionAdapter.DOCKER,
            action_id="build",
            payload={
                "image": "my-app",
                "tag": "v1.0",
                "context_path": "services/my-app",
            },
        )
        result = adapter.execute(request)
        # result.success → True if docker build exited 0
        # result.docker_result.output → build output
    """

    def __init__(self, workspace_root: str | Path) -> None:
        """Initialise the adapter with a fixed workspace root.

        The workspace_root constrains the Dockerfile context path for BUILD
        operations. All other operations (run, stop, etc.) do not touch the
        filesystem and use the workspace_root only for the instance boundary.

        Args:
            workspace_root: Absolute path to the workspace directory.
                            Build context paths must remain within this root.
        """
        self._workspace_root = Path(workspace_root).resolve()

    @property
    def workspace_root(self) -> Path:
        """The resolved, absolute workspace root path."""
        return self._workspace_root

    # ── Path resolution ───────────────────────────────────────────────────────

    def resolve_path(self, relative_path: str) -> Path:
        """Resolve a workspace-relative path and verify it stays inside the root.

        Used for BUILD operation context paths. Same security model as
        FilesystemAdapter.resolve_path() and GitAdapter.resolve_path().

        Steps:
          1. Reject empty paths immediately.
          2. Reject paths that are absolute (start with /).
          3. Join workspace_root / relative_path.
          4. Resolve symlinks and ".." components.
          5. Verify the resolved path starts with workspace_root.

        Args:
            relative_path: A workspace-relative path string from the payload.

        Returns:
            Resolved absolute Path inside workspace_root.

        Raises:
            ValueError: If the path is empty, absolute, or escapes the workspace.
        """
        if not relative_path or not relative_path.strip():
            raise ValueError("context_path must not be empty")

        if Path(relative_path).is_absolute():
            raise ValueError(
                f"context_path must be workspace-relative, got absolute path: "
                f"{relative_path!r}"
            )

        resolved = (self._workspace_root / relative_path).resolve()

        try:
            resolved.relative_to(self._workspace_root)
        except ValueError:
            raise ValueError(
                f"path traversal rejected: {relative_path!r} resolves outside workspace"
            )

        return resolved

    # ── Validation ────────────────────────────────────────────────────────────

    def validate(
        self,
        request: ExecutionRequest,
        operation: DockerOperation | None,
        payload_dict: dict[str, str],
    ) -> DockerValidationResult:
        """Validate an ExecutionRequest at the adapter level.

        ADAPTER-LEVEL validation only. The Gateway has already validated
        request_id, operation_id. This checks adapter preconditions:

          1. adapter_type must be ExecutionAdapter.DOCKER
          2. action_id must map to a recognized DockerOperation
          3. image must be non-empty and valid for BUILD and RUN
          4. container must be non-empty and valid for STOP/START/RESTART/LOGS/INSPECT
          5. context_path must be non-empty and workspace-relative for BUILD
          6. env_vars keys must be valid POSIX identifiers for RUN

        Args:
            request:      The ExecutionRequest received from the Gateway.
            operation:    The resolved DockerOperation (None if unrecognized).
            payload_dict: The request payload as a plain dict (for field access).

        Returns:
            DockerValidationResult with valid=True if all checks pass.
        """
        errors: list[str] = []

        if request.adapter_type != ExecutionAdapter.DOCKER:
            errors.append(
                f"adapter_type must be DOCKER, got {request.adapter_type.value!r}"
            )

        if operation is None:
            errors.append(
                f"action_id {request.action_id!r} is not a recognized DockerOperation; "
                f"valid values: {[op.value for op in DockerOperation]}"
            )

        if operation in _IMAGE_REQUIRED:
            image = payload_dict.get("image", "")
            err = _validate_image_name(image)
            if err:
                errors.append(err)

        if operation in _CONTAINER_REQUIRED:
            container = payload_dict.get("container", "")
            err = _validate_container_name(container)
            if err:
                errors.append(err)

        if operation == DockerOperation.BUILD:
            context_path = payload_dict.get("context_path", "")
            if not context_path or not context_path.strip():
                errors.append("context_path must not be empty for BUILD operations")
            else:
                try:
                    self.resolve_path(context_path)
                except ValueError as exc:
                    errors.append(str(exc))

        if operation == DockerOperation.RUN:
            # Validate optional container name
            name = payload_dict.get("name", "")
            if name:
                err = _validate_container_name(name)
                if err:
                    errors.append(f"container name in 'name' field: {err}")

            # Validate env var keys
            raw_env = payload_dict.get("env_vars", "")
            if raw_env:
                for pair in raw_env.split(","):
                    pair = pair.strip()
                    if "=" in pair:
                        key = pair.split("=", 1)[0]
                        err = _validate_env_key(key)
                        if err:
                            errors.append(err)
                    elif pair:
                        errors.append(
                            f"env_vars entry {pair!r} is not in KEY=VALUE format"
                        )

        return DockerValidationResult(
            valid=len(errors) == 0,
            errors=tuple(errors),
        )

    # ── Request translation ───────────────────────────────────────────────────

    def build_docker_request(
        self,
        request: ExecutionRequest,
        operation: DockerOperation,
    ) -> DockerRequest:
        """Translate an ExecutionRequest into a normalized DockerRequest.

        All Docker command parameters are extracted from the payload and
        stored in their typed fields. No raw command string passthrough —
        every field maps to exactly one Docker CLI flag or argument.

        Args:
            request:   The ExecutionRequest from the Execution Gateway.
            operation: The resolved DockerOperation.

        Returns:
            DockerRequest ready for execution.
        """
        payload_dict = dict(request.payload)

        image = str(payload_dict.pop("image", ""))
        container = str(payload_dict.pop("container", ""))
        tag = str(payload_dict.pop("tag", "latest")) or "latest"
        context_path = str(payload_dict.pop("context_path", ""))

        # command: space-separated string → tuple
        raw_cmd = str(payload_dict.pop("command", ""))
        command: tuple[str, ...] = tuple(raw_cmd.split()) if raw_cmd.strip() else ()

        name = str(payload_dict.pop("name", ""))

        raw_detach = payload_dict.pop("detach", "false")
        detach = str(raw_detach).lower() in {"true", "1", "yes"}

        raw_remove = payload_dict.pop("remove", "false")
        remove = str(raw_remove).lower() in {"true", "1", "yes"}

        # env_vars: "KEY=VALUE,KEY2=VALUE2" → tuple of (key, value) pairs
        raw_env = str(payload_dict.pop("env_vars", ""))
        env_vars: tuple[tuple[str, str], ...] = ()
        if raw_env.strip():
            pairs = []
            for pair in raw_env.split(","):
                pair = pair.strip()
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    pairs.append((k.strip(), v))
            env_vars = tuple(pairs)

        # ports: "8080:80,443:443" → tuple of strings
        raw_ports = str(payload_dict.pop("ports", ""))
        ports: tuple[str, ...] = (
            tuple(p.strip() for p in raw_ports.split(",") if p.strip())
            if raw_ports.strip() else ()
        )

        # volumes: "/host/path:/container/path" → tuple of strings
        raw_volumes = str(payload_dict.pop("volumes", ""))
        volumes: tuple[str, ...] = (
            tuple(v.strip() for v in raw_volumes.split(",") if v.strip())
            if raw_volumes.strip() else ()
        )

        raw_all = payload_dict.pop("all_containers", "false")
        all_containers = str(raw_all).lower() in {"true", "1", "yes"}

        raw_tail = payload_dict.pop("tail", "0")
        try:
            tail = int(raw_tail)
        except (ValueError, TypeError):
            tail = 0

        payload_dict.pop("operation", None)
        metadata = tuple(sorted((k, str(v)) for k, v in payload_dict.items()))

        return DockerRequest(
            request_id=request.request_id,
            operation_id=request.operation_id,
            operation=operation,
            image=image,
            container=container,
            tag=tag,
            context_path=context_path,
            command=command,
            name=name,
            detach=detach,
            remove=remove,
            env_vars=env_vars,
            ports=ports,
            volumes=volumes,
            all_containers=all_containers,
            tail=tail,
            metadata=metadata,
        )

    # ── Docker subprocess runner ──────────────────────────────────────────────

    def _run_docker(
        self,
        args: list[str],
        timeout: int = 30,
    ) -> tuple[str, str, int]:
        """Run a docker command via subprocess.

        Security invariants:
          - Called with a list, never a shell string.
          - shell=False is the default and is NOT overridden.
          - Timeout is enforced to prevent indefinite blocking.

        Args:
            args:    Argument list for docker (subcommand + flags + operands).
            timeout: Subprocess timeout in seconds (default 30).

        Returns:
            Tuple of (stdout, stderr, return_code).
        """
        try:
            result = subprocess.run(
                ["docker"] + args,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return result.stdout, result.stderr, result.returncode
        except subprocess.TimeoutExpired:
            return "", f"docker command timed out after {timeout} seconds", 1
        except (FileNotFoundError, OSError) as exc:
            return "", f"docker is not available: {exc}", 1

    # ── Docker operation handlers ─────────────────────────────────────────────

    def _build(self, docker_request: DockerRequest) -> DockerResult:
        """Build a Docker image from a Dockerfile context directory.

        Resolves the context_path within the workspace. Passes the absolute
        resolved path to docker build to prevent path confusion.

        docker build -t <image>:<tag> <resolved_context_path>
        """
        resolved = self.resolve_path(docker_request.context_path)
        image_ref = (
            f"{docker_request.image}:{docker_request.tag}"
            if docker_request.tag and docker_request.tag != "latest"
            else f"{docker_request.image}:latest"
        )
        args = ["build", "-t", image_ref, str(resolved)]
        # Build may take longer — use 300s timeout
        stdout, stderr, code = self._run_docker(args, timeout=300)
        output = stdout if code == 0 else stderr
        return DockerResult(
            operation=DockerOperation.BUILD,
            output=output,
            return_code=code,
            metadata=(("image_ref", image_ref),),
        )

    def _run(self, docker_request: DockerRequest) -> DockerResult:
        """Create and start a Docker container.

        Builds the command from explicit typed fields — no raw string passthrough.

        docker run [-d] [--rm] [--name <n>] [-e K=V]... [-p h:c]... [-v h:c]... <image> [cmd]
        """
        args = ["run"]
        if docker_request.detach:
            args.append("-d")
        if docker_request.remove:
            args.append("--rm")
        if docker_request.name:
            args += ["--name", docker_request.name]
        for key, val in docker_request.env_vars:
            args += ["-e", f"{key}={val}"]
        for port in docker_request.ports:
            args += ["-p", port]
        for vol in docker_request.volumes:
            args += ["-v", vol]
        args.append(docker_request.image)
        if docker_request.command:
            args += list(docker_request.command)
        # Run may need longer timeout if not detached
        timeout = 30 if docker_request.detach else 120
        stdout, stderr, code = self._run_docker(args, timeout=timeout)
        output = stdout if code == 0 else stderr
        return DockerResult(
            operation=DockerOperation.RUN,
            output=output,
            return_code=code,
            metadata=(),
        )

    def _stop(self, docker_request: DockerRequest) -> DockerResult:
        """Stop a running container. docker stop <container>"""
        stdout, stderr, code = self._run_docker(["stop", docker_request.container])
        output = stdout if code == 0 else stderr
        return DockerResult(
            operation=DockerOperation.STOP,
            output=output,
            return_code=code,
            metadata=(),
        )

    def _start(self, docker_request: DockerRequest) -> DockerResult:
        """Start a stopped container. docker start <container>"""
        stdout, stderr, code = self._run_docker(["start", docker_request.container])
        output = stdout if code == 0 else stderr
        return DockerResult(
            operation=DockerOperation.START,
            output=output,
            return_code=code,
            metadata=(),
        )

    def _restart(self, docker_request: DockerRequest) -> DockerResult:
        """Restart a container. docker restart <container>"""
        stdout, stderr, code = self._run_docker(["restart", docker_request.container])
        output = stdout if code == 0 else stderr
        return DockerResult(
            operation=DockerOperation.RESTART,
            output=output,
            return_code=code,
            metadata=(),
        )

    def _ps(self, docker_request: DockerRequest) -> DockerResult:
        """List containers. docker ps [-a]"""
        args = ["ps"]
        if docker_request.all_containers:
            args.append("-a")
        stdout, stderr, code = self._run_docker(args)
        output = stdout if code == 0 else stderr
        return DockerResult(
            operation=DockerOperation.PS,
            output=output,
            return_code=code,
            metadata=(("all_containers", str(docker_request.all_containers).lower()),),
        )

    def _logs(self, docker_request: DockerRequest) -> DockerResult:
        """Retrieve container logs. docker logs [--tail N] <container>"""
        args = ["logs"]
        if docker_request.tail > 0:
            args += ["--tail", str(docker_request.tail)]
        args.append(docker_request.container)
        stdout, stderr, code = self._run_docker(args)
        output = stdout if code == 0 else (stderr or stdout)
        return DockerResult(
            operation=DockerOperation.LOGS,
            output=output,
            return_code=code,
            metadata=(),
        )

    def _inspect(self, docker_request: DockerRequest) -> DockerResult:
        """Inspect a container or image. docker inspect <container>"""
        stdout, stderr, code = self._run_docker(["inspect", docker_request.container])
        output = stdout if code == 0 else stderr
        return DockerResult(
            operation=DockerOperation.INSPECT,
            output=output,
            return_code=code,
            metadata=(),
        )

    _OPERATION_HANDLERS = {
        DockerOperation.BUILD: _build,
        DockerOperation.RUN: _run,
        DockerOperation.STOP: _stop,
        DockerOperation.START: _start,
        DockerOperation.RESTART: _restart,
        DockerOperation.PS: _ps,
        DockerOperation.LOGS: _logs,
        DockerOperation.INSPECT: _inspect,
    }

    # ── Execution ─────────────────────────────────────────────────────────────

    def execute(self, request: ExecutionRequest) -> DockerExecutionResult:
        """Execute a Docker request through the adapter.

        Execution steps:
          1. Extract operation from request.action_id.
          2. Extract payload fields for validation.
          3. Validate at the adapter level.
             → Returns success=False if validation fails.
          4. Build the normalized DockerRequest.
          5. Execute the Docker operation via the operation handler.
             → Returns success=False on non-zero docker exit or subprocess error.
          6. Return DockerExecutionResult with success=True if docker exited 0.

        The adapter never raises exceptions — all failures are captured in
        DockerExecutionResult(success=False, error=...).

        Args:
            request: The ExecutionRequest from the Execution Gateway.

        Returns:
            DockerExecutionResult capturing the full execution outcome.
        """
        payload_dict = dict(request.payload)

        # Resolve action_id → DockerOperation (may be None)
        operation = _ACTION_TO_OPERATION.get(request.action_id)

        adapter_meta_base: dict[str, str] = {
            "action": request.action_id,
            "operation": operation.value if operation is not None else "unknown",
        }

        # ── 1–3. Validate ──────────────────────────────────────────────────
        validation = self.validate(request, operation, payload_dict)
        if not validation.valid:
            logger.warning(
                "DockerAdapter: validation failed for request_id=%r: %r",
                request.request_id,
                validation.errors,
            )
            return DockerExecutionResult(
                request_id=request.request_id,
                operation_id=request.operation_id,
                operation=operation,
                docker_request=None,
                docker_result=None,
                success=False,
                error="; ".join(validation.errors),
                adapter_metadata=tuple(sorted(adapter_meta_base.items())),
            )

        # ── 4. Build normalized request ────────────────────────────────────
        assert operation is not None  # guaranteed by validation
        docker_request = self.build_docker_request(request, operation)

        # ── 5. Execute Docker operation ────────────────────────────────────
        handler = self._OPERATION_HANDLERS[operation]
        try:
            docker_result = handler(self, docker_request)
        except Exception as exc:
            error_msg = f"docker_operation_failed: {type(exc).__name__}: {exc}"
            logger.warning(
                "DockerAdapter: operation %r failed for request_id=%r: %s",
                operation.value,
                request.request_id,
                error_msg,
            )
            return DockerExecutionResult(
                request_id=request.request_id,
                operation_id=request.operation_id,
                operation=operation,
                docker_request=docker_request,
                docker_result=None,
                success=False,
                error=error_msg,
                adapter_metadata=tuple(sorted(adapter_meta_base.items())),
            )

        # ── 6. Evaluate docker exit code ───────────────────────────────────
        if docker_result.return_code != 0:
            error_msg = (
                f"docker_{operation.value}_failed (exit {docker_result.return_code}): "
                f"{docker_result.output.strip()}"
            )
            logger.warning(
                "DockerAdapter: docker %r exited %d for request_id=%r",
                operation.value,
                docker_result.return_code,
                request.request_id,
            )
            return DockerExecutionResult(
                request_id=request.request_id,
                operation_id=request.operation_id,
                operation=operation,
                docker_request=docker_request,
                docker_result=docker_result,
                success=False,
                error=error_msg,
                adapter_metadata=tuple(sorted(adapter_meta_base.items())),
            )

        # ── 7. Assemble success result ─────────────────────────────────────
        logger.info(
            "DockerAdapter: %r complete request_id=%r",
            operation.value,
            request.request_id,
        )

        adapter_meta = dict(adapter_meta_base)
        adapter_meta["return_code"] = str(docker_result.return_code)
        adapter_meta["output_lines"] = str(docker_result.output.count("\n"))

        return DockerExecutionResult(
            request_id=request.request_id,
            operation_id=request.operation_id,
            operation=operation,
            docker_request=docker_request,
            docker_result=docker_result,
            success=True,
            error=None,
            adapter_metadata=tuple(sorted(adapter_meta.items())),
        )
