"""Docker Adapter — typed contracts for the Docker Adapter.

Architecture position:
  Execution Gateway → Docker Adapter → Docker Engine

The Docker Adapter is NOT Docker. The adapter is an execution adapter
that translates Hermes execution contracts into Docker subprocess invocations
and translates Docker results back into Hermes execution results.

  Dispatch contract vs execution (invariant from Sprint 59):
    The Gateway determines which adapter receives the request and whether
    dispatch is valid. The Docker Adapter performs execution outside the
    deterministic Hermes kernel. No engine ever invokes Docker directly.

  Every docker operation follows this path:
    Execution Gateway → Docker Adapter → Docker Engine (subprocess)

  Where docker-specific logic lives:
    DockerAdapter.build_docker_request → translates ExecutionRequest → DockerRequest
    DockerAdapter.execute              → performs the operation and produces DockerExecutionResult
    DockerAdapter.resolve_path         → workspace boundary enforcement (for build context)

Sprint 65 scope:
  Eight operations supported: build, run, stop, start, restart, ps, logs, inspect.

  Intentionally NOT supported:
    docker exec   → arbitrary command execution inside a running container is
                    a security risk outside the scope of controlled operations.
    docker cp     → file transfer bypasses the Filesystem Adapter boundary.
    docker network create / docker volume create → infrastructure provisioning
                    is outside current operational scope.
    docker system prune → destructive system-wide operation outside scope.
    docker login / docker push / docker pull → registry operations involving
                    credentials and remote network access outside scope.

All contracts in this module are immutable after construction.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


# ── DockerOperation ────────────────────────────────────────────────────────────


class DockerOperation(enum.Enum):
    """Enumeration of supported Docker operations.

    Each value is the string that maps to the ExecutionRequest.action_id
    for Docker requests. The Docker Adapter uses the action_id to determine
    which operation to perform.

    Only operations with a narrow, predefined command structure are supported.
    Operations that allow arbitrary code execution (exec), file transfer (cp),
    or infrastructure modification (network, volume, prune) are intentionally
    excluded.

    BUILD   → docker build (build image from Dockerfile)
    RUN     → docker run (create and start a container)
    STOP    → docker stop (stop a running container)
    START   → docker start (start a stopped container)
    RESTART → docker restart (restart a container)
    PS      → docker ps (list containers)
    LOGS    → docker logs (retrieve container log output)
    INSPECT → docker inspect (return low-level container/image information)
    """

    BUILD = "build"
    RUN = "run"
    STOP = "stop"
    START = "start"
    RESTART = "restart"
    PS = "ps"
    LOGS = "logs"
    INSPECT = "inspect"


# ── DockerRequest ──────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DockerRequest:
    """Immutable normalized Hermes-side representation of a Docker request.

    Produced by DockerAdapter.build_docker_request() from an ExecutionRequest.
    Represents the canonical request before any Docker subprocess invocation.

    Each field maps directly to a Docker CLI flag or argument. No raw shell
    string is accepted — all Docker command construction happens from
    these typed fields. This structural approach prevents shell injection.

    Payload key extraction:
      "image"          → image name[:tag] for BUILD and RUN
      "container"      → container name or ID for STOP/START/RESTART/LOGS/INSPECT
      "tag"            → explicit image tag for BUILD (separate from image name)
      "context_path"   → workspace-relative path to Dockerfile directory (BUILD)
      "command"        → space-separated command and arguments for RUN
      "name"           → --name <name> for RUN
      "detach"         → "-d" flag for RUN ("true"/"false")
      "remove"         → "--rm" flag for RUN ("true"/"false")
      "env_vars"       → KEY=VALUE pairs, comma-separated, for RUN (-e flags)
      "ports"          → host:container port mappings, comma-separated (-p flags)
      "volumes"        → host:container volume mappings, comma-separated (-v flags)
      "all_containers" → include stopped containers in PS (-a flag, "true"/"false")
      "tail"           → number of log lines for LOGS (0 = all, as string int)

    request_id      → from ExecutionRequest.request_id
    operation_id    → from ExecutionRequest.operation_id
    operation       → derived from ExecutionRequest.action_id (DockerOperation enum)
    image           → image name for BUILD and RUN
    container       → container name or ID for STOP/START/RESTART/LOGS/INSPECT
    tag             → image tag for BUILD (default "latest")
    context_path    → workspace-relative Dockerfile context directory (BUILD)
    command         → command arguments inside the container (RUN)
    name            → container name (RUN --name)
    detach          → run container in background (RUN -d)
    remove          → remove container after exit (RUN --rm)
    env_vars        → environment variable pairs for RUN (-e KEY=VALUE)
    ports           → port mapping strings for RUN (-p host:container)
    volumes         → volume mapping strings for RUN (-v host:container)
    all_containers  → include stopped containers (PS -a)
    tail            → number of log lines from end (LOGS --tail; 0 = all)
    metadata        → sorted tuple of additional key-value pairs from the payload

    Immutable after construction.
    """

    request_id: str
    operation_id: str
    operation: DockerOperation
    image: str                           # image name for BUILD, RUN
    container: str                       # container name/id for STOP/START/RESTART/LOGS/INSPECT
    tag: str                             # image tag for BUILD (default "latest")
    context_path: str                    # workspace-relative Dockerfile directory (BUILD)
    command: tuple[str, ...]             # command+args for RUN (empty = use image default)
    name: str                            # --name for RUN
    detach: bool                         # -d flag for RUN
    remove: bool                         # --rm flag for RUN
    env_vars: tuple[tuple[str, str], ...]  # (-e KEY=VALUE) pairs for RUN
    ports: tuple[str, ...]               # (-p host:container) strings for RUN
    volumes: tuple[str, ...]             # (-v host:container) strings for RUN
    all_containers: bool                 # -a flag for PS
    tail: int                            # --tail N for LOGS (0 = all)
    metadata: tuple[tuple[str, str], ...]  # sorted (key, value) pairs


# ── DockerResult ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DockerResult:
    """Immutable result of a single completed Docker operation.

    Produced by the Docker Adapter after the docker subprocess completes
    and before it is wrapped in a DockerExecutionResult.

    operation   → the operation that was performed
    output      → stdout from the docker command (stderr on failure)
    return_code → docker process exit code (0 = success)
    metadata    → sorted tuple of adapter-level key-value pairs

    Immutable after construction.
    """

    operation: DockerOperation
    output: str
    return_code: int
    metadata: tuple[tuple[str, str], ...]   # sorted (key, value) pairs


# ── DockerValidationResult ─────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DockerValidationResult:
    """Immutable outcome of adapter-level validation for a Docker request.

    This is ADAPTER-LEVEL validation only:
      - adapter_type must be ExecutionAdapter.DOCKER
      - action_id must map to a known DockerOperation
      - image must be non-empty and valid for BUILD and RUN
      - container must be non-empty and valid for STOP/START/RESTART/LOGS/INSPECT
      - context_path must be non-empty and workspace-relative for BUILD
      - image and container identifiers must match safe naming rules

    The adapter does NOT re-validate:
      - request_id / operation_id → Gateway already validated these
      - operation correctness     → OperationEngine is the authority

    valid=True  → all adapter preconditions met; errors is empty
    valid=False → one or more failures; errors is non-empty

    Immutable after construction.
    """

    valid: bool
    errors: tuple[str, ...]


# ── DockerExecutionResult ──────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DockerExecutionResult:
    """Immutable outcome of a complete Docker Adapter execution cycle.

    Produced by DockerAdapter.execute() and returned to the caller.
    Contains the complete record of translation, validation, and execution.

    Lifecycle states:
      success=True  → docker_request populated, docker_result populated
      success=False → error describes failure reason
        - docker_request=None → failed before translation (validation failure)
        - docker_request set  → failed after translation (subprocess failure)
        - docker_result=None  → always None when success=False, or when the
                                docker process exited non-zero

    Note: docker_result is populated even on non-zero exit codes so callers
    can inspect the exit code and output. success=False is set whenever
    docker exits non-zero.

    request_id       → from the originating ExecutionRequest
    operation_id     → from the originating ExecutionRequest
    operation        → the DockerOperation that was attempted
    docker_request   → the normalized request (None if pre-translation failure)
    docker_result    → the subprocess result (None if pre-execution failure)
    success          → True if docker exited 0
    error            → error description if success=False; None otherwise
    adapter_metadata → sorted tuple of adapter-level key-value pairs

    Immutable after construction.
    """

    request_id: str
    operation_id: str
    operation: DockerOperation | None    # None if action_id was unrecognized
    docker_request: DockerRequest | None  # None if failed before translation
    docker_result: DockerResult | None    # None if failed before subprocess
    success: bool
    error: str | None
    adapter_metadata: tuple[tuple[str, str], ...]   # sorted (key, value) pairs


# ── Public API ─────────────────────────────────────────────────────────────────


__all__ = [
    "DockerExecutionResult",
    "DockerOperation",
    "DockerRequest",
    "DockerResult",
    "DockerValidationResult",
]
