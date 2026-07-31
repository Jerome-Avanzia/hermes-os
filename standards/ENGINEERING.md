# Hermes Engineering Standards

## 1. Philosophy

Hermes is built through small, validated iterations.

Prefer simplicity over flexibility.

Avoid over-engineering.

Production stability is more important than elegance.

Every change should solve a real problem.

---

## 2. Source of Truth

Git is the source of truth.

Production configuration is imported into Git.

Production is never recreated from memory.

Documentation lives in Git.

Secrets never live in Git.

There is only one source of truth for every artifact.

Avoid duplicate documentation.

Avoid duplicate configuration.

When multiple copies exist, designate one canonical location.

---

## 3. Development Workflow

- Architecture: ChatGPT
- Implementation: Claude Code
- Approval: Human
- Runtime: VPS
- Development: Mac

Git commits originate from the development machine.

---

## 4. Repository Rules

Each repository owns one responsibility.

| Repository | Responsibility |
|---|---|
| hermes-os | Brain |
| hermes-infrastructure | Infrastructure |
| hermes-agents | Agents |
| hermes-prompts | Prompts |
| hermes-skills | Reusable capabilities |

Business repositories own business-specific code.

---

## 5. Infrastructure Rules

Prefer one service change per deployment.

Multiple related services may be deployed together when necessary.

Small deployments reduce operational risk.

One change per commit whenever practical.

Deploy through `deploy.sh`.

Validate before deployment.

Always backup before deployment.

---

## 6. Documentation Rules

Every important architectural decision must be documented.

Keep documentation short.

Documentation explains WHY.

Code explains HOW.

---

## 7. Architecture Decisions

Major technical decisions should be recorded as Architecture Decision Records (ADRs).

Each ADR should include:

- Context
- Decision
- Consequences

Keep ADRs concise.

Only create an ADR for decisions with long-term architectural impact.

Avoid revisiting decisions unless requirements change.

---

## 8. Git Rules

- Small commits.
- Meaningful commit messages.
- Never commit generated files unless intentionally versioned.
- Never commit secrets.

---

## 9. AI Collaboration

- ChatGPT: Architecture
- Claude Code: Implementation
- Human: Final approval

Agents never approve themselves.

---

## 10. Definition of Done

- Architecture reviewed
- Implementation complete
- Tested
- Documentation updated
- Committed
- Pushed
- Deployment verified (if applicable)
- Rollback path verified (for production changes)

---

## 11. Core Principle

Hermes exists to accelerate real businesses.

Infrastructure exists to support execution.

Avoid building technology for its own sake.

---

## 12. Continuous Improvement

These standards are living documentation.

Improve them when experience demonstrates a better approach.

Do not introduce new rules without a demonstrated operational need.

Keep Hermes simple.
