import os
from pathlib import Path


def knowledge_root() -> Path:
    return Path(os.environ.get("HERMES_KNOWLEDGE", "knowledge"))


def skills_root() -> Path:
    return Path(os.environ.get("HERMES_SKILLS", "skills"))


def repositories_root() -> Path:
    return Path(os.environ.get("HERMES_REPOSITORIES", "."))


def profiles_root() -> Path:
    return Path(os.environ.get("HERMES_PROFILES", "profiles"))


def departments_root() -> Path:
    return Path(os.environ.get("HERMES_DEPARTMENTS", "departments"))


def people_root() -> Path:
    return Path(os.environ.get("HERMES_PEOPLE", "people"))


def businesses_root() -> Path:
    return Path(os.environ.get("HERMES_BUSINESSES", "businesses"))


def stale_threshold_hours() -> float:
    return float(os.environ.get("HERMES_STALE_HOURS", "24"))


def logs_dir() -> Path | None:
    value = os.environ.get("HERMES_LOGS")
    return Path(value) if value else None


def github_org() -> str:
    return os.environ.get("HERMES_GITHUB_ORG", "")


def github_token() -> str:
    return os.environ.get("HERMES_GITHUB_TOKEN", "")


def docker_host() -> str:
    return os.environ.get("HERMES_DOCKER_HOST", "unix:///var/run/docker.sock")


def traefik_url() -> str:
    return os.environ.get("HERMES_TRAEFIK_URL", "")


def n8n_url() -> str:
    return os.environ.get("HERMES_N8N_URL", "")


def n8n_api_key() -> str:
    return os.environ.get("HERMES_N8N_API_KEY", "")


def nocodb_url() -> str:
    return os.environ.get("HERMES_NOCODB_URL", "")


def nocodb_token() -> str:
    return os.environ.get("HERMES_NOCODB_TOKEN", "")


def ollama_url() -> str:
    return os.environ.get("HERMES_OLLAMA_URL", "")


def openai_api_key() -> str:
    return os.environ.get("HERMES_OPENAI_API_KEY", "")


def anthropic_api_key() -> str:
    return os.environ.get("HERMES_ANTHROPIC_API_KEY", "")


def openrouter_api_key() -> str:
    return os.environ.get("HERMES_OPENROUTER_API_KEY", "")


def gemini_api_key() -> str:
    return os.environ.get("HERMES_GEMINI_API_KEY", "")


def llm_default_provider() -> str:
    return os.environ.get("HERMES_LLM_DEFAULT_PROVIDER", "")


def llm_default_model() -> str:
    return os.environ.get("HERMES_LLM_DEFAULT_MODEL", "")
