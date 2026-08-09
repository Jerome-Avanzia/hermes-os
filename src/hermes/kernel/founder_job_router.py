"""FounderJobRouter — deterministic capability dispatch for Founder objectives.

Architecture position:
  Gateway → FounderJobRouter → CapabilityRegistry.match()
                             → executable workflow (if registered)
                             → CEO review
                             → Founder

Routing rule:
  The Founder's objective is matched against CapabilityRegistry using keyword
  matching on skill manifests. If a matched capability declares a
  workflow_executor, that executor is invoked to produce a result.
  The CEO then reviews and frames the result before it reaches the Founder.

  The CEO's free-form natural-language prose is NEVER parsed as a routing
  signal. All routing decisions are deterministic (keyword match → executor
  lookup) before any LLM is called.

Fallback:
  When no executable capability matches, the objective is passed directly to
  the CEO profile (existing behavior — unchanged).

Executor contract:
  workflow_executor is a dotted Python path to a callable with signature:
      (objective: str, workspace_id: str, conductor: Conductor) -> str
  The callable must return the complete result text as a string.
  It may internally stream or batch — the router only sees the final text.

CEO review:
  After workflow execution the router calls the CEO profile with:
    - the original Founder objective
    - the capability that was invoked
    - the complete workflow result
  The CEO formats its response using the standard CEO output structure.
  This keeps the CEO in the review/framing role without making it a
  control-plane router.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING

from hermes.kernel.capability_registry import CapabilityRegistry
from hermes.models.capability import Capability
from hermes.models.task import Task
from hermes.providers.ollama_provider import ChatMessage

if TYPE_CHECKING:
    from hermes.conductor import Conductor

logger = logging.getLogger(__name__)


class ExecutorLoadError(Exception):
    """Raised when a workflow_executor path cannot be imported or called."""


class FounderJobRouter:
    """Routes Founder objectives through capability discovery → workflow → CEO review.

    Construction::

        router = FounderJobRouter(
            conductor=conductor,
            capability_registry=CapabilityRegistry(),
        )

        # Determine what capability (if any) will be invoked — for metadata/logging.
        cap = router.find_executable_capability(objective, workspace_id)

        # Route and yield tokens for SSE streaming.
        tokens = router.route(objective, workspace_id)

    The router never raises. Executor failures are captured and yielded as
    an error string so the SSE stream can report them without crashing.
    """

    def __init__(
        self,
        conductor: "Conductor",
        capability_registry: CapabilityRegistry,
    ) -> None:
        self._conductor = conductor
        self._registry = capability_registry

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def find_executable_capability(
        self, objective: str, workspace_id: str
    ) -> Capability | None:
        """Return the first matched capability that has a registered workflow_executor.

        Returns None when no executable capability matches — indicating that
        the CEO-only fallback path will be used.
        """
        caps = self._match_capabilities(objective, workspace_id)
        return next((c for c in caps if c.workflow_executor), None)

    def route(self, objective: str, workspace_id: str) -> Iterator[str]:
        """Route a Founder objective and yield response tokens.

        Phase 1: capability match (deterministic, no LLM)
        Phase 2a [executable]: workflow execution → CEO review
        Phase 2b [fallback]:   CEO-only response

        Never raises. Errors are yielded as text for SSE delivery.
        """
        executable_cap = self.find_executable_capability(objective, workspace_id)

        if executable_cap is None:
            logger.info(
                "FounderJobRouter: no executable capability matched — CEO-only path"
            )
            yield from self._ceo_only(objective)
            return

        logger.info(
            "FounderJobRouter: matched capability=%r executor=%r",
            executable_cap.id,
            executable_cap.workflow_executor,
        )

        # Phase 2a — execute workflow, then CEO review
        try:
            executor = _load_executor(executable_cap.workflow_executor)  # type: ignore[arg-type]
        except ExecutorLoadError as exc:
            logger.error("FounderJobRouter: executor load failed: %s", exc)
            yield from self._ceo_only(objective)
            return

        try:
            result_text = executor(
                objective=objective,
                workspace_id=workspace_id,
                conductor=self._conductor,
            )
        except Exception as exc:
            logger.error(
                "FounderJobRouter: workflow executor raised capability=%r: %s",
                executable_cap.id, exc,
            )
            yield f"\n[Workflow execution failed: {exc}]\n"
            return

        yield from self._ceo_review(objective, executable_cap, result_text)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _match_capabilities(self, objective: str, workspace_id: str) -> list[Capability]:
        task = Task(id="founder-routing", business=workspace_id, request=objective)
        return self._registry.match(task)

    def _ceo_only(self, objective: str) -> Iterator[str]:
        """Pass the objective straight to the CEO profile (existing fallback path)."""
        messages = [ChatMessage(role="user", content=objective)]
        yield from self._conductor.stream_chat(messages, profile_id="ceo")

    def _ceo_review(
        self, objective: str, capability: Capability, result_text: str
    ) -> Iterator[str]:
        """Ask the CEO to review the workflow result and frame it for the Founder."""
        prompt = _build_ceo_review_prompt(objective, capability, result_text)
        messages = [ChatMessage(role="user", content=prompt)]
        yield from self._conductor.stream_chat(messages, profile_id="ceo")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_executor(dotted_path: str) -> Callable[..., str]:
    """Import and return the callable at the given dotted Python path.

    Raises ExecutorLoadError if the module cannot be imported or the
    attribute does not exist.

    Example:
        _load_executor("hermes.workflows.competitor_intelligence_workflow.execute_from_objective")
    """
    module_path, _, attr = dotted_path.rpartition(".")
    if not module_path or not attr:
        raise ExecutorLoadError(
            f"Invalid workflow_executor path {dotted_path!r} — "
            f"must be 'module.path.function_name'"
        )
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise ExecutorLoadError(
            f"Cannot import module {module_path!r} from workflow_executor {dotted_path!r}: {exc}"
        ) from exc
    try:
        fn = getattr(module, attr)
    except AttributeError as exc:
        raise ExecutorLoadError(
            f"Module {module_path!r} has no attribute {attr!r} "
            f"(workflow_executor={dotted_path!r}): {exc}"
        ) from exc
    if not callable(fn):
        raise ExecutorLoadError(
            f"workflow_executor {dotted_path!r} resolved to {fn!r} which is not callable"
        )
    return fn


def _build_ceo_review_prompt(
    objective: str, capability: Capability, result_text: str
) -> str:
    """Build the CEO review prompt injected after workflow execution."""
    return f"""A Founder submitted the following objective:

FOUNDER OBJECTIVE:
{objective}

This was routed to the {capability.name} capability ({capability.id}) for execution.
The capability has produced the following result:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CAPABILITY RESULT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{result_text}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Review this result against the Founder's objective. Using your standard CEO output structure:

OBJECTIVE RECEIVED — confirm what was asked
EXECUTION PLAN — which capability was invoked and why
RESULT — present the capability result in full (do not truncate)
EXECUTIVE SUMMARY — key finding and what the Founder should decide next
GAPS IDENTIFIED — anything missing that would improve the result (omit if none)
RECOMMENDED NEXT ACTION — one clear action: Approve / Request Changes / Reject
"""
