# Founder Development Playbook

How Hermes OS is actually built. Based on the engineering practices followed from Sprint 37 through Sprint 47.

---

## Who builds Hermes

One person. The Founder (Jerome Cornet) is architect, developer, reviewer, and release manager. There is no team, no code review queue, no handoff. Every commit in the repository is authored by the Founder.

Claude Code (Opus) is the implementation partner. Every feature commit carries `Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>`. Claude Code writes the code. The Founder directs, reviews, and approves.

ChatGPT is used for architecture exploration and design conversations before implementation begins. It does not touch the codebase.

---

## The sprint model

A sprint is one feature delivered as one atomic commit on `main`. There are no intermediate commits, no work-in-progress pushes, no partial merges. A sprint either ships completely or does not exist in the history.

### Sprint cadence

Sprints are not calendar-bound. They are completion-bound. Sprint 37 through Sprint 47 shipped across approximately 28 hours of continuous development. A sprint ends when its commit lands on `main` with passing tests and a version tag.

### What a sprint contains

Every sprint delivers:

1. **Production code** in `src/hermes/` -- the feature implementation
2. **Tests** in `tests/` -- comprehensive coverage for the new feature
3. **UI integration** in `gateway/static/index.html` -- workspace views and API wiring (when applicable)
4. **Service integration** in `service.py` -- HermesService methods exposing the feature
5. **Gateway endpoints** in `gateway/app.py` -- REST API routes (when applicable)

Typical sprint size: 2,500-3,000 lines of insertions. Tests consistently account for 40-50% of the code in each sprint.

### Sprint numbering

Sprint numbers are sequential integers. They appear in the commit message and in the version tag. The mapping is:

| Sprint | Version | Feature |
|--------|---------|---------|
| 37 | v1.5.0 | Dashboard (4-question executive layout) |
| 38 | v1.6.0 | People Runtime (registry, workload) |
| 39 | v1.7.0 | Goals Runtime (registry, cross-references) |
| 40 | v1.8.0 | Context Graph (attention summary) |
| 41 | v1.9.0 | GitHub Runtime (provider-independent model) |
| 42 | v2.0.0 | Infrastructure Runtime (Docker, Traefik, Host) |
| 43 | v2.1.0 | n8n Runtime (workflow provider) |
| 44 | v2.2.0 | NocoDB Runtime (data provider) |
| 45 | v2.3.0 | LLM Runtime (multi-provider architecture) |
| 46 | v2.4.0 | Impact & Risk Engines (dependency analysis) |
| 47 | v2.5.0 | Readiness Engine (scenario-based evaluation) |

Major version bumps mark significant capability thresholds, not breaking changes. v1.0.0 was the first operational runtime (Sprint 32). v2.0.0 was the first infrastructure-aware release (Sprint 42).

---

## Commit conventions

Every commit follows conventional commit format with the sprint number:

```
feat(scope): Description (Sprint N)
```

Scopes match the feature area: `readiness`, `impact`, `llm`, `nocodb`, `n8n`, `infrastructure`, `github`, `context`, `goals`, `people`, `dashboard`.

Other commit types used outside of sprint deliveries:

- `fix(scope): Description` -- bug fixes
- `docs(scope): Description` -- documentation changes
- `chore(scope): Description` -- maintenance

The commit body includes a detailed description of what changed and why. The `Co-Authored-By` trailer is always present on feature commits.

---

## Branching and merging

The primary development model is **linear commits on `main`**. Sprint 37 through Sprint 47 are all linear -- no merge commits, no branches.

Feature branches are used for large cross-cutting work. The `feature/context-engine` branch was used for the Context Engine work and merged into `main` as a single merge commit introducing 124 files (8,736 insertions). After the merge, development resumed as linear commits on `main`.

The rule: use a feature branch when the work is too large or experimental for a single sprint commit. Otherwise, commit directly to `main`.

---

## Version tagging

Every sprint gets a semantic version tag applied to its commit:

```
git tag v2.5.0
```

Tags are created after the commit lands on `main` and tests pass. There are no release candidates in the v2.x series. The v0.x series used `-rc1`, `-rc2` suffixes during early development.

One special tag exists: `hermes-first-chat` marks the first working chat interaction (a milestone, not a version).

---

## The development session

A typical development session follows this sequence:

### 1. Architecture (before code)

The Founder defines what the sprint will deliver. For complex features, this involves design conversations in ChatGPT to explore trade-offs, define data structures, and settle on responsibilities before any code is written.

The architecture review from the previous session's review (if any) informs what to build next.

### 2. Implementation (Claude Code)

The Founder opens a Claude Code session in the `hermes-os` working directory and directs the implementation. Claude Code writes:

- The engine or runtime module
- The test suite
- Service integration methods
- Gateway endpoints
- UI views (when applicable)

Claude Code has pre-approved permissions configured in `.claude/settings.json` and `.claude/settings.local.json` for:

- Running `pytest` and Python compilation checks
- Git commits with custom messages
- Running `hermes` CLI commands
- Docker operations (build, compose, exec)
- Package management (`uv sync`, `pip install`)

### 3. Testing (during implementation)

Tests are written alongside production code, not after. The test suite runs via:

```bash
pytest tests/test_<module>.py -v
```

Tests are deterministic. No mocks for core business logic -- engines are tested with real data structures, real registries, and real traversals. External runtime providers (Docker, n8n, NocoDB, GitHub, LLM APIs) use test doubles at the HTTP boundary only.

The CI pipeline (`.github/workflows/validate.yml`) runs contract validation on every push to `main` and on pull requests:

```bash
python tests/validate_contracts.py
```

### 4. Review (Founder)

The Founder reviews all code before committing. Claude Code does not self-approve. This follows the Delegation Principle from DEC-0002:

> Hermes Agent owns autonomously any responsibility that is operational, reversible, and evidence-traceable. Hermes Agent never owns responsibilities that are governance-defining or strategically irreversible.

Writing code is operational. Committing to `main` is a governance act that requires Founder approval.

### 5. Commit and tag

Once the Founder approves:

```bash
git add <specific files>
git commit -m "feat(scope): Description (Sprint N)

Detailed body describing the change.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"

git tag vX.Y.Z
git push origin main --tags
```

Files are added by name, not with `git add -A`. The commit is atomic -- one sprint, one commit, one tag.

---

## Architecture governance

### Principles

The 10 Hermes Principles (documented in `HERMES_PRINCIPLES.md`) govern all design decisions:

1. Hermes owns intelligence -- providers supply reasoning
2. Context before prompting -- deterministic context assembly first
3. The filesystem is the registry -- capabilities discovered by disk presence
4. Architecture before implementation -- observe, design, implement, test, validate
5. Small, reversible changes -- each change solves one problem
6. One source of truth -- no knowledge duplication
7. Execution validates architecture -- designs are hypotheses until executed
8. Model independence -- no coupling to specific reasoning providers
9. Knowledge is an asset -- loaded, preserved, composed, delivered
10. The kernel never disappears -- general-purpose reasoning as fallback

### Decision records

Significant decisions are recorded in `decisions/`:

- **DEC-prefixed**: Cross-cutting governance decisions (operating model, knowledge system, lifecycle management)
- **ADR-prefixed**: Architecture Decision Records for technical choices (workspace shell, operation model)

A decision record includes context, the decision itself, and consequences. Only decisions with long-term architectural impact get ADRs.

### Architecture review

After a set of sprints, the Founder conducts an architecture review of the current system. The review evaluates cohesion, separation of responsibilities, coupling, extensibility, consistency, naming, API consistency, data flow, reuse opportunities, and technical debt.

The review produces:

- Strengths (things working well)
- Weaknesses (things to address)
- Architectural risks (things that could become problems)
- Things that must never change (invariants)
- Things safe to refactor later (deferred improvements)

The review informs what the next set of sprints should focus on.

---

## Code organization rules

### Layered architecture

Code lives in five layers. Dependencies flow strictly downward:

```
gateway/    -- HTTP protocol boundary (FastAPI)
    |
service.py  -- Orchestration facade (HermesService)
    |
context/    -- Executive Intelligence (ContextGraph, ImpactEngine, RiskEngine, ReadinessEngine)
kernel/     -- Business logic (registries, operations, decisions, execution)
    |
runtime/    -- Provider integration (LLM, Docker, n8n, NocoDB, GitHub)
    |
models/     -- Domain objects (dataclasses, state machines, value objects)
```

No layer calls upward. No circular dependencies between packages.

### Engine design pattern

Every engine in `context/` follows the same pattern:

- **Stateless**: Created fresh per request, no shared mutable state
- **Deterministic**: No AI, no heuristics, no probabilistic scoring
- **Declarative rules**: Business rules defined as data dictionaries, not procedural code
- **Typed inputs/outputs**: Dataclasses with `@dataclass(slots=True)`

### Provider design pattern

Every runtime in `runtime/` follows the same pattern:

- **Abstract base class** with `name`, `configured`, `health()`, and collection method
- **Concrete providers** per integration (DockerProvider, OllamaProvider, N8nProvider, etc.)
- **Runtime aggregator** that combines multiple providers into a unified interface
- **Provider-agnostic models** in `models/` that abstract away native API shapes

### Test file convention

Every production module has a corresponding test file:

```
src/hermes/context/risk_engine.py    -->  tests/test_impact_engine.py (covers risk + impact)
src/hermes/context/readiness_engine.py  -->  tests/test_readiness_engine.py
src/hermes/runtime/llm_runtime.py    -->  tests/test_llm_runtime.py
```

---

## Deployment

### Local development

```bash
uv sync                          # Install dependencies
pytest tests/ -v                 # Run test suite
uvicorn hermes.gateway.app:app   # Start gateway locally
```

### Docker build

```bash
docker compose build             # Build hermes-os:latest
docker compose up -d             # Start services
docker compose ps                # Verify health
```

The Dockerfile uses a multi-stage build with `uv` for dependency resolution, runs as non-root user `hermes` (uid 1000), and mounts knowledge, skills, and workspace directories as read-only volumes.

### Chat stack

The chat deployment (`docker-compose.chat.yml`) adds:

- `hermes-gateway` service on port 8000
- Traefik integration for domain routing and TLS
- Connection to Ollama for local LLM inference
- Health checks against the `/health` endpoint

### Environment configuration

All configuration is via environment variables (documented in `.env.example`):

- `ANTHROPIC_API_KEY` -- Claude API access
- `HERMES_KNOWLEDGE`, `HERMES_SKILLS`, `HERMES_REPOSITORIES` -- data paths
- Infrastructure URLs: `DOCKER_HOST`, `TRAEFIK_URL`, `N8N_URL`, `NOCODB_URL`, `OLLAMA_URL`
- LLM keys: `OPENAI_API_KEY`, `OPENROUTER_API_KEY`, `GEMINI_API_KEY`
- Defaults: `LLM_DEFAULT_PROVIDER`, `LLM_DEFAULT_MODEL`

Secrets never live in Git. The `.env` file is gitignored.

---

## What is not in the workflow

These things do not exist in the current process. They are listed to prevent confusion:

- **No pull request workflow** for sprint commits. Sprints commit directly to `main`.
- **No staging environment**. Development runs locally; deployment targets a VPS.
- **No automated deployment pipeline**. Deployment is manual via `docker compose`.
- **No linting or formatting enforcement**. `make lint` is a placeholder.
- **No code coverage tracking**. Tests are comprehensive but coverage is not measured.
- **No multi-person code review**. The Founder is the sole reviewer.
- **No sprint planning meetings**. Sprint scope is decided by the Founder at the start of each session.
- **No backlog management tool**. The architecture review and the Founder's judgment determine what ships next.
- **No release notes**. The commit message and tag are the release record.
