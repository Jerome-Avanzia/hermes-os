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


def businesses_root() -> Path:
    return Path(os.environ.get("HERMES_BUSINESSES", "businesses"))


def logs_dir() -> Path | None:
    value = os.environ.get("HERMES_LOGS")
    return Path(value) if value else None
