# Hermes OS — Deployment Guide

This guide covers running Hermes in production via Docker Compose: building
the image, starting it, updating it, reading logs, and troubleshooting.

---

## Prerequisites

- Docker and Docker Compose (v2).
- An Anthropic API key, if you intend to use `hermes generate`.

---

## 1. Configure

Copy the environment template and fill in your values:

```bash
cp .env.example .env
```

```env
ANTHROPIC_API_KEY=sk-ant-...

HERMES_REPOSITORIES=/data/repos
HERMES_KNOWLEDGE=/data/knowledge
HERMES_SKILLS=/data/skills
HERMES_LOGS=/data/logs
```

`.env` is git-ignored — never commit real secrets. `docker-compose.yml`
already sets `HERMES_*` to sensible in-container defaults; the values in
`.env` mainly exist so the same file also works if you run Hermes outside
Docker.

`ANTHROPIC_API_KEY` is only required for `hermes generate`. Every other
command works without it.

---

## 2. Build

```bash
docker compose build
```

This runs a multi-stage build: dependencies are resolved with `uv` from
`uv.lock` (reproducible, cached separately from application code), then the
final image copies only the built virtual environment plus `knowledge/`,
`skills/`, and `workspaces/` — no compilers, no source control metadata, no
test suite. The container runs as a non-root user (`hermes`, uid 1000).

---

## 3. Start

```bash
docker compose up -d
```

Acceptance check — the container should report healthy within a few
seconds:

```bash
docker compose ps
```

By default the container runs `hermes --help` and exits (`command:
["--help"]` in `docker-compose.yml`), which is enough to prove the image
and entrypoint work. For real usage, run one-off commands against the
running service:

```bash
docker compose run --rm hermes inspect AVANZIA
docker compose run --rm hermes knowledge AVANZIA
docker compose run --rm hermes generate "Update the AVANZIA homepage copy"
```

Or override `command:` in `docker-compose.yml` to run a specific command as
the service's main process.

### Mounted directories

| Host path | Container path | Purpose |
|---|---|---|
| `./knowledge` | `/data/knowledge` (read-only) | Business knowledge documents, bundled from this repo. |
| `./skills` | `/data/skills` (read-only) | Skill manifests, bundled from this repo. |
| `./data/repos` | `/data/repos` (read-only) | Project code repositories for `hermes read` / `hermes workspace` to scan. Empty by default — mount your own project checkouts here. |
| `./data/logs` | `/data/logs` | Hermes' log output (`hermes.log`). |

**Path resolution note:** `workspaces/registry.yaml` (bundled in the image,
not mounted) resolves each project's `path` relative to
`$HERMES_REPOSITORIES`. If a registry entry uses a relative path like
`../avanzia-website` (as the bundled `AVANZIA` entry does, for local-dev
convenience), mount the corresponding host directory one level *above*
`HERMES_REPOSITORIES`'s mount point to match — e.g. add a volume like
`../avanzia-website:/data/avanzia-website:ro` alongside `HERMES_REPOSITORIES:
/data/repos`. For new projects, prefer registry paths without `../` (e.g.
`path: my-project`) and mount them directly under `./data/repos/my-project`.

---

## 4. Update

```bash
git pull
docker compose build
docker compose up -d
```

Compose recreates the container with the new image; named volumes and
bind-mounted data under `./data/` are untouched.

---

## 5. Logs

Two ways to read logs:

```bash
docker compose logs -f hermes
```

or read the log file directly on the host (via the `HERMES_LOGS` bind mount):

```bash
tail -f data/logs/hermes.log
```

Hermes logs via Python's standard `logging` module — every CLI invocation
configures a stream handler (stdout, visible in `docker compose logs`) and,
whenever `HERMES_LOGS` is set, a file handler writing to
`$HERMES_LOGS/hermes.log`. Kernel components never use `print()`.

---

## 6. Troubleshooting

**`docker compose up` fails with "env file .env not found"**
Run `cp .env.example .env` first (step 1). Compose requires the file to
exist even if you haven't filled in every value yet.

**Healthcheck shows `unhealthy`**
The healthcheck runs `hermes --help` inside the container. If it's failing,
check `docker compose logs hermes` for an import or startup error — this
almost always means the image build didn't complete cleanly rather than a
runtime data problem, since `--help` doesn't touch mounted data.

**Permission denied writing to `data/logs` or reading `data/repos`**
Docker creates bind-mount host directories as `root` if they don't already
exist before the first `up`. Since Hermes runs as a non-root user (uid
1000) inside the container, this can cause permission errors. Fix by
pre-creating the directories with the right owner before starting:

```bash
mkdir -p data/repos data/logs
sudo chown -R 1000:1000 data/
```

**`hermes generate` fails with a configuration error**
`ANTHROPIC_API_KEY` isn't set. Confirm it's present in `.env` and that
`docker-compose.yml`'s `env_file: .env` is being picked up
(`docker compose config` prints the resolved environment for the service).

**A project's workspace shows `Exists: False`**
The path `workspaces/registry.yaml` resolves for that project doesn't
exist inside the container. See the path resolution note in step 3 — this
is almost always a mismatch between `HERMES_REPOSITORIES` and where the
project's repository is actually mounted.

**Changes to `knowledge/` or `skills/` on the host aren't showing up**
Confirm you're editing the files under the mounted host paths (`./knowledge`,
`./skills`), not inside the container's image layer. Mounted paths reflect
host changes immediately, no rebuild needed; anything baked into the image
via `docker compose build` requires a rebuild to update.
