# Hermes Principles

## Purpose

These principles define how Hermes should behave as a system, not just how it should be built. They exist to guide decisions that fall between what the engineering standards prescribe and what the architecture documents specify — the grey areas where judgment is required.

Principles are expected to remain stable. Implementations change. Capabilities are added and removed. Models are replaced. The principles beneath those changes should not need to be rewritten. When a principle requires revision, it signals a fundamental shift in direction, not a routine update.

---

## Principle 1 — Hermes owns intelligence. Reasoning providers supply reasoning.

Hermes makes decisions: which project to resolve, which knowledge to load, which capabilities apply, what plan to create, when execution is complete. These decisions belong to the kernel.

Reasoning providers generate text. They take a composed prompt and return a response. They do not decide what context is relevant, what plan is valid, or what constitutes a complete result. Those judgments belong to Hermes.

This distinction is not semantic. It determines the system boundary. Providers are plug-in components. Hermes is the authority. If the reasoning behind a task could only be explained by referring to what a provider decided, the architecture has drifted in the wrong direction.

---

## Principle 2 — Context before prompting.

The quality of a reasoning output depends almost entirely on the quality of the context it receives. Assembling accurate, relevant, complete context is a more valuable engineering investment than constructing clever prompts.

Hermes builds context deterministically before any reasoning is invoked. It resolves the project, loads knowledge, reads the workspace, and discovers capabilities. The prompt is a delivery mechanism for that context. It is not where intelligence lives.

A system that skips context assembly and compensates with elaborate prompting is fragile. When the prompt changes, the output changes unpredictably. When the context is correct, the output follows.

---

## Principle 3 — The filesystem is the registry.

Capabilities are discovered by their presence on disk. A skill exists because its manifest exists. An agent is registered because its declaration exists in the repository. No separate database, service, or configuration file is the authoritative source of what is available.

This means the system can be fully inspected with standard file tools. It means adding a capability requires adding a file, not editing a registry elsewhere. It means the state of the system at any point in time is readable from Git.

Hidden configuration — environment-only state, in-memory registries with no persistent backing, runtime state that cannot be reconstructed from the repository — violates this principle.

---

## Principle 4 — Architecture before implementation.

Hermes evolves through a deliberate sequence:

```
Observe → Design → Implement → Test → Validate
```

Observation comes from real executions. Gaps are identified from evidence, not assumption. Design produces a documented decision before a line of code is written. Implementation follows the design. Tests verify the implementation. Validation — running the system against a real task — confirms the architecture worked.

Skipping design produces implementations that solve the wrong problem. Skipping validation produces implementations that solve the problem only in theory. Both errors waste effort. The sequence is not bureaucracy; it is how Hermes avoids rebuilding the same things repeatedly.

---

## Principle 5 — Small, reversible changes.

Each change to Hermes should solve one problem. It should be independently testable. It should be committable in isolation. It should leave the system in a working state.

Large rewrites that touch many components simultaneously are harder to validate, harder to revert, and harder to reason about when something breaks. Incremental changes are easier to approve, easier to debug, and easier to explain.

The cost of a small change that turns out to be wrong is low. The cost of a large change that turns out to be wrong is high. Prefer the smaller risk.

---

## Principle 6 — One source of truth.

Every piece of knowledge, configuration, or documentation should exist in exactly one authoritative location. When something needs to change, it should be changed in one place.

Duplication is not a convenience. It is a debt. Two copies of the same fact will eventually diverge. When they diverge, one is wrong, and the system must decide which one to trust. Hermes should never be in that position.

When duplication cannot be avoided, designate one location as canonical and make all other references point to it explicitly.

---

## Principle 7 — Execution validates architecture.

A design that has never been executed is a hypothesis. Real executions reveal gaps that design reviews do not. A capability that matches no tasks in practice is not a working capability. A knowledge base that never reaches the reasoning provider does not ground the output.

When an execution reveals a gap, that gap takes priority over theoretical improvements. The architecture is updated to reflect what the execution taught. This is not a failure of planning; it is the feedback loop working correctly.

Architectural confidence comes from accumulated executions, not from accumulated documents.

---

## Principle 8 — Model independence.

Hermes must not be coupled to any specific reasoning provider. The provider interface exists so that the underlying reasoning engine can be replaced without changing the kernel.

This is not a contingency plan. It is a structural requirement. Reasoning providers change: their capabilities improve, their APIs evolve, their pricing shifts, their availability varies. A system that can only operate with one provider has delegated its reliability to that provider.

The prompt format, the context assembly, and the response handling should all be implemented in terms of the provider interface, not in terms of any specific vendor's API.

---

## Principle 9 — Knowledge is an asset.

Knowledge must be loaded, preserved, composed, and delivered — not merely indexed. A system that knows a document exists but does not deliver its contents to the reasoning provider has not used its knowledge; it has catalogued it.

When Hermes loads knowledge, the goal is to make that knowledge present in the reasoning context. Titles and filenames are pointers. The content is the asset. Pointers without content produce outputs that are ungrounded, generic, and unreliable.

The value of the knowledge base is realised only when its content reaches the point where reasoning occurs.

---

## Principle 10 — The kernel never disappears.

Hermes has a kernel capability: a general-purpose reasoning fallback that activates when no specialist capability matches a task. In v1, it is a fallback. It may become always present in future versions — participating in every execution as a synthesis layer, a reviewer, or a coordinator.

The kernel represents Hermes reasoning as a system, not as a router to specialist tools. As the system matures, the kernel's role will expand. Components added today should be designed with the expectation that the kernel will eventually be a persistent participant in execution, not merely a safety net.

This is an architectural direction. It does not change the current implementation. It should inform how new components relate to the kernel when they are designed.
