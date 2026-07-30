"""Loads Profile definitions from YAML files in a profiles directory."""

import logging
from pathlib import Path

import yaml

from hermes.models.profile import Profile

logger = logging.getLogger(__name__)

_DEFAULT_PROFILES_DIR = Path(__file__).resolve().parents[3] / "profiles"


class ProfileNotFoundError(Exception):
    pass


class ProfileLoader:
    def __init__(self, profiles_dir: Path | None = None) -> None:
        self._dir = profiles_dir or _DEFAULT_PROFILES_DIR
        self._cache: dict[str, Profile] | None = None

    def _load_all(self) -> dict[str, Profile]:
        if self._cache is not None:
            return self._cache

        profiles: dict[str, Profile] = {}

        if not self._dir.is_dir():
            logger.warning("Profiles directory does not exist: %s", self._dir)
            self._cache = profiles
            return profiles

        for path in sorted(self._dir.glob("*.yaml")):
            try:
                data = yaml.safe_load(path.read_text())
                if not isinstance(data, dict):
                    continue

                profile_id = data.get("id") or path.stem
                profile = Profile(
                    id=profile_id,
                    name=data.get("name", profile_id),
                    system_prompt=data.get("system_prompt", ""),
                    description=data.get("description", ""),
                    model=data.get("model"),
                )
                profiles[profile_id] = profile
                logger.debug("Loaded profile: %s", profile_id)
            except Exception:
                logger.warning("Failed to load profile from %s", path, exc_info=True)

        logger.info("Loaded %d profiles from %s", len(profiles), self._dir)
        self._cache = profiles
        return profiles

    def get(self, profile_id: str) -> Profile:
        """Return a profile by ID. Raises ProfileNotFoundError if not found."""
        profiles = self._load_all()
        profile = profiles.get(profile_id)
        if profile is None:
            available = ", ".join(sorted(profiles.keys())) or "(none)"
            raise ProfileNotFoundError(
                f"Profile '{profile_id}' not found. Available: {available}"
            )
        return profile

    def get_default(self) -> Profile:
        """Return the 'default' profile, or the first available, or a built-in fallback."""
        profiles = self._load_all()
        if "default" in profiles:
            return profiles["default"]
        if profiles:
            return next(iter(profiles.values()))
        return Profile(
            id="default",
            name="Hermes",
            system_prompt="You are Hermes, a helpful AI assistant.",
            description="Default assistant profile.",
        )

    def list_all(self) -> list[Profile]:
        """Return all loaded profiles, sorted by ID."""
        return sorted(self._load_all().values(), key=lambda p: p.id)
