---
name: agent-fleet-manager
description: Turn this agent into the manager of every other agent machine on the user's Tailscale network — discover machines, inventory installed agents and models (Hermes, Claude Code, Codex, Gemini CLI, Ollama models), roll out updates machine by machine, and report a before/after version matrix. Use when an agent shipped an update that should reach every machine (e.g. "Hermes got an update, update it everywhere"), when the user asks to update every model, or wants a fleet status/health check.
---

# Agent Fleet Manager

The agent running this skill becomes the **fleet manager**: from this one machine it reaches every other machine on the user's tailnet over SSH, checks what agents and models are installed, updates them, and reports back. Typical trigger: *"Hermes shipped an update — get it on every machine."*

**Prerequisite:** machines are already on the same tailnet with Tailscale SSH working (that's the `tailscale-vps-setup` skill). If `tailscale status` fails or shows no peers, run that skill first.

## Ground rules (safety first)

1. **Inventory before you touch anything.** Steps 1–2 are read-only. Never jump straight to updating.
2. **Show the plan and get a yes** before running any update.
3. **One machine at a time.** Update the first machine, verify it, then move on. If the first one breaks, stop — you just saved the rest of the fleet.
4. **Never interrupt a working agent without asking.** If an agent is mid-task in a tmux/screen session, ask the user before restarting anything.
5. **Update the machine you are running on last**, and warn the user first — updating yourself mid-rollout can kill the rollout.
6. Capture everything: keep a running log so the final report is exact, not from memory.

## Step 1 — Discover the fleet

```bash
tailscale status --json
```

Parse it into a roster: machine name (MagicDNS), tailnet IP, OS, online/offline. Exclude the machine you're running on (manage it last). Show the roster to the user and confirm which machines are in scope. Offline machines: list them, skip them, mention them in the final report.

## Step 2 — Inventory every machine (read-only)

For each online machine, over SSH (`ssh <user>@<machine>`), collect versions. Run each check independently so one missing tool doesn't stop the rest:

```bash
for cmd in "hermes --version" "claude --version" "codex --version" \
           "gemini --version" "ollama --version" "node --version"; do
  echo "== $cmd"; $cmd 2>/dev/null || echo "not installed"
done
echo "== ollama models"; ollama list 2>/dev/null || echo "no ollama"
echo "== npm globals";  npm ls -g --depth=0 2>/dev/null | tail -n +2
echo "== tmux sessions"; tmux ls 2>/dev/null || echo "none"
echo "== disk"; df -h / | tail -1
```

Also determine **how each agent was installed** (this decides how to update it):

```bash
which hermes claude codex gemini 2>/dev/null   # path hints: npm prefix, ~/.local, /usr/local, git checkout
```

Build the version matrix: rows = machines, columns = agents/models, cells = installed version (or —).

## Step 3 — Plan the rollout

- Find the latest available version for each npm-installed agent: `npm view <package> version` (see [references/update-commands.md](references/update-commands.md) for the package names and per-agent update paths).
- Ollama models are updated by re-pulling them — treat every locally installed model as updatable.
- Present a plan: per machine, what gets updated from which version to which, and whether any running tmux session needs a restart. **Wait for the user's OK.**

## Step 4 — Roll out, machine by machine

For each machine, in order:

1. Note running agent sessions (`tmux ls`). If the agent being updated is running, ask the user: restart it after updating, or skip this machine for now?
2. Run the update commands from [references/update-commands.md](references/update-commands.md), matching the install method you found in Step 2. **Update using the same channel the tool was installed with** — never mix (e.g. don't npm-install over a native install).
3. Re-check the version. Success = new version prints. Failure = capture the error, mark the machine, **stop and tell the user** if it's the first machine (canary), otherwise ask whether to continue.
4. Restart sessions per the user's instruction from 4.1.

Then update the local machine (the one running this skill) last, with the user's OK.

## Step 5 — Report

Deliver a fleet report:

- **Version matrix, before → after** — every machine, every agent/model.
- **Failures** — machine, command, error, suggested fix.
- **Skipped** — offline machines and anything the user deferred; suggest re-running for them later.
- Confirm which sessions were restarted.

## Recipes

- **"Hermes shipped an update"** → Steps 1–2 scoped to Hermes only, plan, roll out to every machine that has it, report.
- **"Update every model"** → on each machine: `ollama list`, then re-pull each model (exact loop in the reference file), plus update the agent CLIs so every machine runs the newest model versions.
- **Fleet health check (no changes)** → Steps 1–2 only, plus `uptime` and disk; report the matrix and anything worrying (offline machine, full disk, agent that's far behind).
- **New machine joined the tailnet** → run its inventory, compare to the fleet, bring it up to matching versions.
