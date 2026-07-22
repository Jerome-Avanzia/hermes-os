# Capability Registry Specification

**Component:** Capability Registry\
**Status:** Draft v2.0

## Purpose

The Capability Registry is the runtime component that discovers, validates, registers, and exposes the capabilities Hermes Agent and the specialist agents can invoke. It answers one question, mechanically and at runtime: *what can Hermes actually call right now, and how?*

It is the runtime mechanism behind the discovery steps in `docs/24-hermes-agent-integration.md` §4 and the routing responsibilities in `docs/15-agent-orchestrator.md`. It assumes capabilities are already approved per `docs/21-agent-registry.md`, `docs/22-shared-services.md`, and `docs/23-extension-framework.md` — this document validates technical correctness, not approval.

## 1. Objectives

- Give Hermes Agent and the Agent Orchestrator one reliable place to ask what capabilities exist and whether they are usable right now.
- Let a capability be added, updated, or removed without changing the registry or any consuming agent.
- Catch malformed or inconsistent declarations before runtime.
- Stay a read-heavy lookup surface — the registry indexes capabilities, it does not execute them.
- Serve every capability category (agent, shared service, extension, tool) through one mechanism.
- Keep manifests declarative and human-readable without running code.

## 2. Architecture

```text
Capability Sources
       ↓
Discovery Scanner
       ↓
Manifest Parser
       ↓
Validator
       ↓
Registry Store  ←→  Registry API  ←→  Consumers
                                        (Hermes Agent,
                                         Agent Orchestrator,
                                         Dashboards)
```

| Component | Responsibility |
|---|---|
| Capability Sources | Locations the registry is configured to scan (§3). |
| Discovery Scanner | Walks capability sources and locates `capability.yaml` files. Does not parse or interpret them. |
| Manifest Parser | Loads a manifest into structured form. Fails per-file, not per-run. |
| Validator | Applies the rules in §6 and produces a registration or a structured error (§8). |
| Registry Store | The authoritative index of currently valid, registered capabilities. |
| Registry API | The read interface in §7. Consumers never touch the Store directly. |

The registry never loads or executes a capability's implementation code — only its manifest. Invocation belongs to the Agent Orchestrator.

## 3. Repository Structure

A manifest lives beside its implementation, not in a central directory:

```text
<capability-root>/
  capability.yaml       Manifest (required, see §4)
  README.md             Human-readable description (optional)
  <implementation>/     Capability-specific code, prompts, or config
```

The registry is configured with one or more **capability sources** — root paths it scans recursively for `capability.yaml` files. For this repository:

```text
runtime/capabilities/          Capabilities that ship with Hermes OS itself (e.g. built-in shared services).
```

Business or venture repositories are expected to be added as additional capability sources; this specification does not require them to be restructured, only to expose a `capability.yaml` per capability.

## 4. `capability.yaml` Format

### 4.1 Fields

| Field | Required | Type | Description |
|---|---|---|---|
| `capability_id` | ✓ | String | Globally unique, stable identifier. |
| `name` | ✓ | String | Human-readable name. |
| `category` | ✓ | Enum | `agent`, `shared_service`, `extension`, `tool` (§4.2). |
| `version` | ✓ | String | Semantic version. |
| `owner` | ✓ | String | Accountable owner, matching the governing registry entry. |
| `status` | ✓ | Enum | `proposed`, `approved`, `active`, `deprecated`, `suspended`, `retired`. |
| `description` | ✓ | String | One or two sentences on what the capability does. |
| `interface` | ✓ | Object | How the capability is invoked (§4.3). |
| `dependencies` | – | List\<String\> | `capability_id`s this capability requires. |
| `inputs` | – | List\<String\> | Hermes business object types consumed (from `specs/`). |
| `outputs` | – | List\<String\> | Hermes business object types produced. |
| `tags` | – | List\<String\> | Free-text labels for discovery/filtering. |

### 4.2 Category values

`category` is a structural label used only for discovery and routing. It does not encode organizational agent taxonomy — how pipeline agents (`docs/14-agent-architecture.md`) relate to specialist agents (`docs/20`, `docs/21`) is out of scope here. A `category: agent` entry may carry a free-text `role` tag if useful; the registry does not interpret it.

| Category | Meaning |
|---|---|
| `agent` | An AI agent — executive, specialist, or service. |
| `shared_service` | An entry from `docs/22-shared-services.md` §4. |
| `extension` | An entry governed by `docs/23-extension-framework.md`. |
| `tool` | A narrow, single-purpose callable an agent invokes directly. |

### 4.3 `interface` object

| Field | Required | Type | Description |
|---|---|---|---|
| `protocol` | ✓ | Enum | `http`, `mcp`, `function`, or `event`. |
| `entrypoint` | ✓ | String | URL, tool/server name, callable name, or topic — per protocol. |
| `contract` | – | String | Path/URL to the request/response contract (OpenAPI operation, JSON Schema). |

### 4.4 Example

```yaml
capability_id: svc.knowledge-base
name: Knowledge Base
category: shared_service
version: 1.0.0
owner: AVANZIA
status: active
description: >
  Provides read and write access to Hermes OS organizational knowledge
  for agents and services.
interface:
  protocol: http
  entrypoint: https://internal.hermes-os.dev/services/knowledge-base
  contract: api/openapi.yaml#/paths/~1knowledge-base
dependencies:
  - svc.identity
inputs:
  - Lesson
  - Decision
outputs:
  - Lesson
tags:
  - memory
  - knowledge
```

Illustrative only — it does not assert `svc.knowledge-base` is implemented.

## 5. Discovery Process

### 5.1 Startup discovery

1. Load the configured list of capability sources.
2. Walk each source and collect every path containing a `capability.yaml`.
3. Parse each file. A parse failure is a Discovery Error (§8.1) scoped to that path only; the scan continues.
4. Validate each parsed manifest (§6). A failure is a Validation Error (§8.2); the capability is not registered.
5. Write manifests that pass validation to the Registry Store, keyed by `capability_id`.
6. After all sources are scanned, resolve `dependencies` references (§6.4) across the full set of newly registered capabilities.
7. Emit a discovery summary: counts of registered/rejected/skipped capabilities and the full error list from steps 3–4.

A capability is either fully registered or entirely absent — never partially registered.

### 5.2 Ordering

Discovery order across sources is not significant, except where §6.1 (uniqueness) applies. Dependency resolution (step 6) runs only after every source is scanned, so declaration order between sources does not matter.

### 5.3 Re-discovery

The registry supports re-running discovery without a restart:

- Re-scans all configured sources.
- Diffs the result against the current Registry Store.
- Adds new capabilities, updates changed ones, removes capabilities whose manifest was deleted or now fails validation.
- Never silently drops a capability that other active capabilities declare as a dependency (§8.3).

The trigger mechanism (manual, scheduled, file-change signal) is left to implementers; this specification defines only the guarantees re-discovery must uphold.

## 6. Validation Rules

A manifest must pass every rule to be registered. Validation is structural and referential — it does not evaluate whether a capability's implementation actually works (see §7.4 for that).

**6.1 Uniqueness** — `capability_id` must be unique across all sources scanned in the same run. Two manifests sharing an id: neither is registered; both are reported as errors.

**6.2 Required fields** — Every field marked required in §4.1 must be present and non-empty, including `interface.protocol` and `interface.entrypoint`.

**6.3 Value constraints**
- `capability_id` matches `^[a-z][a-z0-9_.-]*$`.
- `version` is a valid semantic version (`MAJOR.MINOR.PATCH`).
- `category` and `status` must be exact matches to the enums in §4.1/§4.2.
- `interface.protocol` must be one of the values in §4.3.
- `inputs`/`outputs`, when present, must each name a business object defined in `specs/`.

**6.4 Referential integrity**
- Every `capability_id` in `dependencies` must resolve to another registered capability after the full discovery run completes.
- An unresolved dependency is an error against the *dependent* capability, which is not registered.
- Circular dependencies (direct or transitive) are detected and rejected; neither capability is registered.

**6.5 Status consistency** — A capability with `status: retired` or `status: suspended` is validated normally but registered as **unavailable** (§7.4): queryable, not invocable.

## 7. Registry API

| Operation | Description |
|---|---|
| `list_capabilities(category?, status?, tag?)` | All registered capabilities, optionally filtered. |
| `get_capability(capability_id)` | A single capability's manifest, or not-found. |
| `get_dependencies(capability_id)` | Resolved list of capabilities this one depends on. |
| `get_dependents(capability_id)` | Capabilities that depend on this one. |
| `find_by_input(object_type)` | Capabilities declaring the given object type in `inputs`. |
| `find_by_output(object_type)` | Capabilities declaring the given object type in `outputs`. |
| `get_status(capability_id)` | Registered `status` and current availability (§7.4). |
| `get_discovery_report()` | Summary and errors from the most recent discovery run. |

The Agent Orchestrator uses `list_capabilities` and `find_by_input`/`find_by_output` to route business objects. Hermes Agent's "discover agents / shared services / extensions" steps (`docs/24` §4) are `list_capabilities(category=...)` calls against this API.

Responses reflect the state as of the last completed discovery run, not a live probe. The API does not invoke capabilities, manage credentials, or enforce authorization — those belong to the Agent Orchestrator and the Identity shared service.

**Availability (§7.4):** `status` is declared in the manifest; a computed `available` flag is `true` only when `status` is `active` and every entry in `dependencies` is itself available. `get_status` returns both, so a consumer can distinguish "retired" from "active but blocked by an unavailable dependency."

## 8. Error Handling

Errors are structured and attributable to a single manifest wherever possible. The registry fails closed: any capability with an unresolved error is not registered, and one failure never blocks discovery of unrelated capabilities.

**8.1 Discovery Errors** — A `capability.yaml` cannot be located or read (missing, unreadable, malformed YAML). Scoped to the single path.

**8.2 Validation Errors** — A parsed manifest fails a rule in §6. Each error identifies the `capability_id` (or source path, if that field is itself invalid), the rule violated, and a human-readable message.

**8.3 Registration Conflicts** — Discovery would remove or invalidate a capability that other active capabilities depend on. The registry keeps the previous valid registration in place, reports the conflict with the list of affected dependents, and requires an explicit acknowledgment before the removal takes effect.

**8.4 Runtime Errors** — Failures invoking a capability the registry reported as available are not registry errors; they belong to the Agent Orchestrator's execution handling. The registry's responsibility ends at accurately reporting last-known `status` and availability.

**8.5 Reporting** — All errors from a discovery run are retrievable via `get_discovery_report()`. An unregistered capability always has a corresponding, retrievable reason.

## 9. Extensibility Principles

- Adding a `category` value must not require changes to the Scanner, Validator core, or API — only the enum.
- New manifest fields are additive and optional by default; existing manifests must remain valid.
- Capability sources are pluggable — §3 describes local directories, but the Scanner operates over an abstract source list so other source types can be added without changing discovery, validation, or the API.
- Validation rules are versioned: a manifest may declare which schema revision it targets so the Validator can apply the correct rule set during a migration window.
- The registry only ever reasons about the manifest, never a capability's internal implementation or technology choice.

## 10. Future Enhancements

- Remote capability sources (Git repositories, network manifest index) so venture repositories can register without being colocated.
- Manifest signing and provenance verification.
- Dependency-ordered startup using the graph built in §6.4.
- Health/heartbeat integration so availability reflects live health, not only manifest state.
- Versioned rollout: multiple concurrently registered versions of one `capability_id`, with the API resolving which a consumer receives.
- A capability search UI over `list_capabilities` / `get_discovery_report`.
- Whether Skills Registry / Prompt Library entries should be represented as `tool`-category capabilities, or remain a separate index — not decided by this specification.

## Used By

- Hermes Agent (`docs/24-hermes-agent-integration.md`)
- Agent Orchestrator (`docs/15-agent-orchestrator.md`)
- Agent Registry, Shared Services, and Extension Framework governance processes (`docs/21`, `docs/22`, `docs/23`) as the runtime record of what they have approved
