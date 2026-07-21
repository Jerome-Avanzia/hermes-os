# Per-agent update commands

Rule #1: **update through the same channel the tool was installed with.** Find the channel first (`which <cmd>`, `npm ls -g --depth=0`, `brew list`, look for a git checkout), then use the matching row. If a tool has its own `update`/`upgrade` subcommand, prefer that over reinstalling.

| Agent / tool | Check version | Update |
|---|---|---|
| Hermes Agent | `hermes --version` | Match the install method: try `hermes update` (or `hermes upgrade`) first; if npm-installed, update the global package shown in `npm ls -g`; if it's a git checkout, `git pull` in the install dir and rebuild per its README. Verify the package/repo name from the machine itself — don't guess it. |
| Claude Code | `claude --version` | `claude update` (native installer). If installed via npm: `npm install -g @anthropic-ai/claude-code@latest` |
| Codex CLI | `codex --version` | `npm install -g @openai/codex@latest` |
| Gemini CLI | `gemini --version` | `npm install -g @google/gemini-cli@latest` |
| Ollama (the runtime) | `ollama --version` | Linux: `curl -fsSL https://ollama.com/install.sh \| sh` · macOS: `brew upgrade ollama` or the app's built-in updater |
| Ollama models | `ollama list` | Re-pull every installed model — see loop below |
| Node.js / npm | `node --version` | Only on request — agents may pin Node versions; ask before touching the runtime |
| OS packages | — | `sudo apt update && sudo apt upgrade -y` (Debian/Ubuntu) — **always ask first**, this can restart services |

Any other agent CLI on the machine: same procedure — find the install channel, use its native update command if it has one, otherwise reinstall through that channel at the latest version.

## Update every Ollama model on a machine

```bash
ollama list | tail -n +2 | awk '{print $1}' | while read -r m; do
  echo "== pulling $m"; ollama pull "$m"
done
```

Notes:
- Re-pulling only downloads changed layers; unchanged models finish instantly.
- Check disk space first (`df -h /`) — model updates can be tens of GB.
- Don't run this while the user is mid-inference on that machine without asking.

## Latest-version lookups (for the Step 3 plan)

```bash
npm view @anthropic-ai/claude-code version
npm view @openai/codex version
npm view @google/gemini-cli version
```

## After updating

```bash
<cmd> --version        # confirm the new version actually took
hash -r                # if the shell still resolves the old binary
```

If a version check still prints the old number over SSH, the PATH in non-interactive shells may differ — run it as `bash -lc '<cmd> --version'`.
