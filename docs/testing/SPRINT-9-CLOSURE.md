# Sprint 9 Closure Report
**Hermes OS — AVANZIA Founder Runtime**
**Date:** 2026-08-08
**Status:** CLOSED

---

## 1. Sprint Objective

Sprint 9 had two parallel objectives:

**AT-9 (Phase 9 bootstrap):** Connect the existing autonomous engineering pipeline to the Workspace REST API, enabling engineering jobs to be dispatched, tracked, and reviewed without touching the CLI.

**Founder Runtime v1:** Implement the first general-purpose job execution surface — the ability for the Founder to assign any namespaced Capability to any Profile from either the CLI or the Workspace UI, with full Job persistence, SSE streaming, and cloud LLM execution.

Both objectives share the same underlying goal: make Hermes operational as an AI-native OS, not just as a CLI tool.

---

## 2. Deliverables Completed

### Organization Model
- Extended `Organization` dataclass from 6 to 12 identity facets: added `values`, `target_customers`, `tone_of_voice`, `visual_identity`, `site_map`, `homepage_copy`
- `WorkspaceEngine._load_organization()` passes all 12 facets through
- `Conductor._compose_system_prompt()` injects all 12 facets into the system prompt with labelled sections

### AVANZIA Identity Documents (Sprint 2A–2D)
- `sprint-2a`: brand foundation — purpose, vision, mission, values, positioning
- `sprint-2b`: target customers, services, tone of voice
- `sprint-2c`: visual identity direction
- `sprint-2d`: site map and homepage copy
- All documents registered in `workspaces/AVANZIA/workspace.yaml`

### Brand Strategist Profile and Identity Review Capability
- `profiles/brand-strategist.yaml`: company-agnostic profile with a structured reasoning contract — evaluate against stated criteria, distinguish observation from recommendation, three-rating output system (Pass / Needs Revision / Question)
- `skills/brand-strategy/skill.yaml`: v1.1.0 — added `brand.identity-review` capability with dot-notation namespace
- `skills/brand-strategy/sops/identity-review.md`: 8-step identity review SOP covering foundation, market, voice, visual, and web document categories plus cross-document consistency check. Entirely company-agnostic.

### Founder Runtime CLI (`hermes job run`)
- New Typer subcommand group: `hermes job` → `src/hermes/cli/commands/job/`
- `hermes job run --workspace W --profile P --capability C`
- Full pipeline: `CapabilityRegistry` → `SOPRegistry` → `generate_job_id` → `JobStore.save(running)` → `ContextEngine.build_conversation` → `Conductor.stream_chat_with_context` → stdout streaming → `JobStore.save(completed/failed)`
- Mandatory `JOB-YYYYMMDD-NNN` record created before execution begins
- `_fail_job()` helper for clean failure persistence

### Founder Runtime Gateway Endpoint
- `POST /v1/workspaces/{workspace_id}/jobs/run`
- Accepts `{ profile, capability }` — validates both exist before creating job record
- Creates `JOB-YYYYMMDD-NNN` immediately (visible in job list during execution)
- Streams response via SSE: metadata frame → content frames → `[DONE]`
- `_sse_job_stream()` helper handles completion and failure persistence

### Workspace UI — New Job Panel
- "New Job" button added to the Jobs list header
- Panel: profile selector (from `/profiles`), capability selector (from `/capabilities`), Run Job button
- SSE output area with streaming display — same pump pattern as Chat view
- Profile and capability loaded from existing API endpoints (no new endpoints added)

### Engineering Job Dispatch (AT-9)
- `EngineeringJob` model: `job_id`, `workspace_id`, `task`, `status`, `dispatched_at`, `completed_at`, `commit_sha`, `files_changed`, `error`
- `EngineeringJobRunner`: async bridge driving `EngineeringCoordinator`, writing final state to `JobStore`
- `POST /v1/workspaces/{workspace_id}/engineering/jobs` — 202 Accepted, async dispatch
- `GET /v1/workspaces/{workspace_id}/engineering/jobs/{job_id}` — status and result
- `GET /v1/workspaces/{workspace_id}/engineering/jobs` — list all
- `HermesService.dispatch_engineering_job()` and `get_engineering_job()` entry points
- `EngineeringCoordinator`, `CorrectionEngine`, `JobStore` not modified

### Provider Resolution
- `OllamaProvider.from_env()`: single factory method reading `OLLAMA_MODE` → selects `OLLAMA_LOCAL_URL` or `OLLAMA_CLOUD_URL`, reads `OLLAMA_API_KEY`, respects `OLLAMA_MODEL`
- Replaces the prior `OLLAMA_BASE_URL` pattern in both the CLI and gateway
- `OllamaProvider.__init__` extended with `api_key` field
- `stream_chat()` sends `Authorization: Bearer {api_key}` when key is present
- `Conductor._resolve_provider()` propagates `api_key` on model-override path

### Deployment (Traefik + Docker)
- `docker-compose.yml`: added `./workspaces:/app/workspaces` bind mount �� required for container user to write job records
- Added Traefik labels: `traefik.enable=true`, host rule `hermes.avanzia.tech`, entrypoint `websecure`, TLS cert resolver `mytlschallenge`, backend port 8000
- Added `avanzia-shared` external network ��� required for Traefik to reach the container
- `configure_logging()`: tolerates `PermissionError` on `/data/logs/hermes.log`, falls back to console-only

---

## 3. Architecture Changes

### A. Capability Namespacing (dot-notation) — FROZEN
`brand.identity-review`, `brand.strategy`. The dot separates domain from function. SOP refs remain filesystem-derived (unchanged). This convention is now the standard for all future capabilities.

**Why:** Prevents naming collisions across departments as the capability surface grows. A flat namespace doesn't scale past one team.

### B. Single Provider Resolution Path — FROZEN
`OllamaProvider.from_env()` is the canonical factory for both CLI and HTTP paths. No other code may read `OLLAMA_BASE_URL`, `OLLAMA_LOCAL_URL`, or `OLLAMA_CLOUD_URL` directly.

**Why:** Both paths previously read `OLLAMA_BASE_URL` (an unset legacy variable), defaulting silently to localhost. The divergence was invisible until cloud deployment.

### C. Job Persistence Before Execution — FROZEN
A `JOB-YYYYMMDD-NNN` record is written with `status: running` before the LLM call begins, not after. The job is visible in the list immediately.

**Why:** If the process dies mid-stream, a partial record is better than no record. The Founder can see what was dispatched.

### D. Workspaces as Bind Mount — FROZEN
`./workspaces:/app/workspaces` must remain a host-side bind mount. Image-layer directories are not writable when the container user UID differs from the build UID.

**Why:** Discovered via `PermissionError` on first job dispatch. The fix is architectural: any directory Hermes writes to at runtime must be bind-mounted, not image-layer.

### E. Traefik Routing via Docker Labels — FROZEN
All Hermes services join `avanzia-shared` and carry Traefik labels. `exposedByDefault: false` means a container without labels is permanently invisible to the router.

**Why:** Traefik's Docker provider requires explicit opt-in. The missing labels caused the 404 at the domain level.

### F. Organization 12-Facet Model — FROZEN
`Organization` dataclass has exactly 12 fields. All 12 are injected into the system prompt. New identity dimensions must be added to this model, not worked around.

**Why:** The original 6-facet model was incomplete for a real brand review. All facets must reach the LLM or the review is partial.

---

## 4. Technical Lessons Learned

### L1. Host environment ≠ container environment
Manual `curl` tests from the VPS host used `$OLLAMA_MODEL` from the host shell — which was empty. The test proved nothing. All validation of container behavior must occur inside the container or via the instrumented application path.

**Rule:** Never run manual API tests using env vars sourced from the host shell when testing container behavior.

### L2. First-file-wins in Docker Compose `env_file`
When two `env_file` entries define the same key, Docker Compose uses the **first** definition. The repo `.env` had `OLLAMA_MODEL=llama3.2`, which silently overrode `hermes.env`'s `OLLAMA_MODEL=kimi-k3`. The container appeared to reload correctly but was running the wrong model.

**Rule:** Never duplicate env keys across `env_file` entries. The secrets file owns runtime config; the repo `.env` owns only build-time defaults.

### L3. Debug at WARNING level, not DEBUG
The application log level was `INFO`. Debug instrumentation placed at `DEBUG` level would have been invisible in production. Temporary diagnostic logging must be `WARNING` or above to guarantee visibility.

### L4. `OLLAMA_BASE_URL` was a dead variable
Both the CLI and gateway had silently been reading an env var that was never set in any environment. The default `http://localhost:11434` masked the error until cloud deployment. Legacy variable names must be audited when the config model changes.

### L5. The 404 was not the endpoint — it was the model name
`https://ollama.com/api/chat` is correct and documented. The 404 response body `{"error":"model 'llama3.2' not found"}` was the actual signal. Jumping to "wrong endpoint" without reading the response body wastes an entire debugging cycle.

**Rule:** Always read the full response body before changing the URL.

### L6. Bind mounts must be planned at architecture time
The `PermissionError` on `workspaces/` was entirely predictable: any directory that Hermes writes to at runtime cannot live in the image layer when `HERMES_UID` differs from the build UID. This should have been in the initial `docker-compose.yml`.

---

## 5. Acceptance Criteria

| # | Criterion | Status |
|---|-----------|--------|
| AC-1 | `hermes job run --workspace AVANZIA --profile brand-strategist --capability brand.identity-review` executes and streams to stdout | **PASS** |
| AC-2 | Job record `JOB-YYYYMMDD-NNN` created before execution, persisted as `completed` after | **PASS** |
| AC-3 | `POST /v1/workspaces/AVANZIA/jobs/run` dispatches job and returns SSE stream | **PASS** |
| AC-4 | Workspace UI "New Job" panel loads profiles and capabilities, streams output | **PASS** |
| AC-5 | Capability dot-notation (`brand.identity-review`) resolves correctly | **PASS** |
| AC-6 | All 12 org facets reach the Conductor system prompt | **PASS** |
| AC-7 | `https://hermes.avanzia.tech` serves the Workspace UI | **PASS** |
| AC-8 | Provider selection respects `OLLAMA_MODE=cloud` | **PASS** |
| AC-9 | `Authorization: Bearer` header sent to Ollama Cloud | **PASS** |
| AC-10 | Ollama Cloud (`kimi-k2.7-code`) returns a successful response | **PASS** |
| AC-11 | Engineering job dispatch via `POST /v1/workspaces/{id}/engineering/jobs` (AT-9) | **PASS** |
| AC-12 | Debug instrumentation removed before final sprint close | **PARTIAL** — instrumentation committed, not yet removed |

---

## 6. Demonstrated End-to-End Workflow

```
Founder
  → opens https://hermes.avanzia.tech
  → selects Workspace: AVANZIA
  → navigates to Jobs
  → clicks "New Job"
  → selects Profile: brand-strategist
  → selects Capability: brand.identity-review
  → clicks Run Job

Hermes Gateway (POST /v1/workspaces/AVANZIA/jobs/run)
  → CapabilityRegistry.get("brand.identity-review")
      → resolves to brand-strategy skill
  → SOPRegistry.get("brand-strategy/identity-review")
      → 8-step Identity Review SOP (207 lines, company-agnostic)
  → generate_job_id(jobs_dir)
      → JOB-20260808-001
  → JobStore.save(status=running)          ← record visible immediately
  → HermesService.stream_chat()
      → ContextEngine.build_conversation(AVANZIA, brand-strategist)
          → WorkspaceEngine: loads 12 org facets from AVANZIA workspace
          → ProfileLoader: loads brand-strategist system prompt
          → KnowledgeEngine: loads relevant knowledge documents
      → Conductor.stream_chat_with_context()
          → _compose_system_prompt(): 12 org facets injected
          → OllamaProvider.from_env(): base_url=https://ollama.com, model=kimi-k2.7-code
          → POST https://ollama.com/api/chat
              Authorization: Bearer ****…****
              { "model": "kimi-k2.7-code", "messages": [...], "stream": true }

Ollama Cloud
  → HTTP 200
  → NDJSON stream of tokens

Hermes Gateway
  → SSE frames: data: {"content": "..."} × N
  → SSE final: data: [DONE]
  → JobStore.save(status=completed, output=<full review text>)

Workspace UI
  → streams tokens to output panel in real time
  → job appears in Jobs list as completed

Founder
  → reads Brand Identity Review output
```

---

## 7. Technical Debt

| ID | Item | Severity |
|----|------|----------|
| TD-1 | Debug instrumentation (`WARNING`-level request/response logging) remains in `OllamaProvider.stream_chat()` — must be removed or downgraded to `DEBUG` before Sprint 10 | High |
| TD-2 | `OLLAMA_MODEL=llama3.2` remains in `/opt/avanzia/repos/hermes-os/.env` as a stale default — should be removed entirely so the secrets file is the single source of truth | Medium |
| TD-3 | `_sse_job_stream()` uses `_job_store` singleton directly — not injected, not testable in isolation | Low |
| TD-4 | No streaming progress for engineering jobs — status jumps from `running` to `completed/failed` with no intermediate visibility | Low |
| TD-5 | No job cancellation endpoint | Low |
| TD-6 | Concurrent engineering jobs on the same repo are not serialized | Low |
| TD-7 | `OllamaProvider._generate_sync()` does not send the `Authorization` header — the `api_key` is present but not wired into the non-streaming path | Medium |

---

## 8. Known Limitations

1. **No job queue.** Jobs are dispatched immediately and run concurrently. There is no backpressure mechanism.
2. **No job cancellation.** A running job cannot be stopped from the UI or API.
3. **Profile selection in UI is cosmetic for engineering jobs.** Engineering jobs do not use the profile system — they go directly to `EngineeringCoordinator`.
4. **Model is global.** There is one `OLLAMA_MODEL` for the entire Hermes instance. Profiles cannot yet specify different cloud models.
5. **Capability list in the New Job panel is unfiltered.** All capabilities across all skills appear, regardless of whether they are appropriate for the selected profile.
6. **SSE streaming has no reconnect logic.** If the connection drops mid-stream, the UI shows partial output with no recovery path.
7. **No authentication on the Workspace UI or API.** The gateway is publicly accessible to anyone who reaches `hermes.avanzia.tech`.
8. **Job output is stored as a single string.** No structured sections, no metadata about which SOP steps were executed.
9. **The Ollama Cloud URL is hardcoded as the default in `from_env()`.** Changing providers requires env var changes; there is no provider abstraction at the Conductor level.

---

## 9. Sprint Retrospective

### What went well

- The core Founder Runtime architecture was sound from the start — Capability → SOP → Conductor → Provider required no redesign once implemented correctly.
- The separation of concerns held: company-agnostic SOPs and profiles, AVANZIA-specific data only in the workspace. The brand-strategist profile required zero changes when pointed at AVANZIA's actual identity documents.
- The SSE streaming pattern from the chat endpoint was directly reusable for the job endpoint with minimal adaptation.
- The decision to create the Job record immediately before execution (not after) proved correct — it's the only safe design.
- Traefik configuration was fully diagnosed from first principles without guessing.

### What went poorly

- The `OLLAMA_BASE_URL` legacy variable caused a multi-step debugging chain that should have been caught earlier. Provider configuration had no tests and no documentation.
- Manual VPS curl tests were run before verifying whether the host environment contained the variables being tested. This produced false signal and wasted time.
- Debug logging was added to the wrong log level twice before landing at `WARNING`.
- The `docker-compose.yml` missing the `workspaces` bind mount was a preventable omission — the write path was implemented without auditing what directories the process actually writes to.
- Docker Compose `env_file` precedence was assumed incorrectly. The first-file-wins behavior is documented but not widely understood.

### What should change for Sprint 10

- Before any deployment, audit every directory the process writes to and verify it is bind-mounted.
- Establish a "container-only validation" rule: no env var referenced in a test may be sourced from the host shell.
- Remove temporary debug instrumentation in the same PR it was added, not in a follow-up.
- Provider configuration should be validated at startup — if `OLLAMA_MODE=cloud` and `OLLAMA_API_KEY` is empty, log a warning immediately, not at first request.

---

## 10. Definition of Done

**Sprint 9 is officially complete.**

The Founder Runtime has been demonstrated end-to-end in production:

- A Job was assigned from the Workspace UI
- The Brand Strategist profile was activated with AVANZIA's 12 identity facets
- The Identity Review SOP was executed
- Ollama Cloud responded successfully using `kimi-k2.7-code`
- The Job record was persisted with `status: completed`

One item requires cleanup before Sprint 10 begins: **TD-1** (debug instrumentation in `OllamaProvider.stream_chat()`).

---

## 11. Next Sprint

**Sprint 10 — AVANZIA Organization Charter**

The objectives, deliverables, acceptance criteria, and execution plan for Sprint 10 will be created as the first Workspace Job of Sprint 10.

---

*End of Sprint 9 Closure Report.*
*Document prepared: 2026-08-08*
*Author: Hermes OS / Claude Sonnet 4.6*
