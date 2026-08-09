import textwrap
from pathlib import Path

import pytest

from hermes.kernel.profile_loader import ProfileLoader, ProfileNotFoundError
from hermes.models import Profile


REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILES_DIR = REPO_ROOT / "profiles"


# -- Loading from the real profiles directory --------------------------------


def test_loads_seed_profiles():
    loader = ProfileLoader(profiles_dir=PROFILES_DIR)
    profiles = loader.list_all()
    ids = [p.id for p in profiles]
    assert "default" in ids
    assert "developer" in ids
    assert "business" in ids


def test_get_default_returns_default_profile():
    loader = ProfileLoader(profiles_dir=PROFILES_DIR)
    profile = loader.get_default()
    assert profile.id == "default"
    assert profile.name == "Hermes"


def test_get_by_id():
    loader = ProfileLoader(profiles_dir=PROFILES_DIR)
    profile = loader.get("developer")
    assert profile.id == "developer"
    assert profile.name == "Software Engineer"
    assert "technical" in profile.system_prompt.lower()


def test_get_unknown_raises():
    loader = ProfileLoader(profiles_dir=PROFILES_DIR)
    with pytest.raises(ProfileNotFoundError, match="nonexistent"):
        loader.get("nonexistent")


def test_profile_has_system_prompt():
    loader = ProfileLoader(profiles_dir=PROFILES_DIR)
    for profile in loader.list_all():
        assert profile.system_prompt.strip(), f"Profile '{profile.id}' has empty system_prompt"


def test_profile_has_description():
    loader = ProfileLoader(profiles_dir=PROFILES_DIR)
    for profile in loader.list_all():
        assert profile.description, f"Profile '{profile.id}' has empty description"


# -- Loading from a temporary directory --------------------------------------


def test_loads_from_custom_directory(tmp_path):
    (tmp_path / "test.yaml").write_text(textwrap.dedent("""\
        id: test
        name: Test Profile
        description: A test profile.
        system_prompt: You are a test assistant.
        model: mistral
    """))

    loader = ProfileLoader(profiles_dir=tmp_path)
    profiles = loader.list_all()
    assert len(profiles) == 1
    assert profiles[0].id == "test"
    assert profiles[0].name == "Test Profile"
    assert profiles[0].model == "mistral"
    assert profiles[0].system_prompt == "You are a test assistant."


def test_id_defaults_to_filename_stem(tmp_path):
    (tmp_path / "my-profile.yaml").write_text(textwrap.dedent("""\
        name: My Profile
        system_prompt: Hello.
    """))

    loader = ProfileLoader(profiles_dir=tmp_path)
    profile = loader.get("my-profile")
    assert profile.id == "my-profile"


def test_empty_directory_returns_no_profiles(tmp_path):
    loader = ProfileLoader(profiles_dir=tmp_path)
    assert loader.list_all() == []


def test_nonexistent_directory_returns_no_profiles(tmp_path):
    loader = ProfileLoader(profiles_dir=tmp_path / "does-not-exist")
    assert loader.list_all() == []


def test_get_default_with_no_profiles_returns_builtin_fallback(tmp_path):
    loader = ProfileLoader(profiles_dir=tmp_path)
    profile = loader.get_default()
    assert profile.id == "default"
    assert profile.name == "Hermes"
    assert "helpful" in profile.system_prompt.lower()


def test_get_default_without_default_yaml_returns_first(tmp_path):
    (tmp_path / "alpha.yaml").write_text("id: alpha\nname: Alpha\nsystem_prompt: Hi.\n")
    (tmp_path / "beta.yaml").write_text("id: beta\nname: Beta\nsystem_prompt: Hi.\n")

    loader = ProfileLoader(profiles_dir=tmp_path)
    profile = loader.get_default()
    assert profile.id == "alpha"  # sorted, alpha comes first


def test_malformed_yaml_is_skipped(tmp_path):
    (tmp_path / "good.yaml").write_text("id: good\nname: Good\nsystem_prompt: Hi.\n")
    (tmp_path / "bad.yaml").write_text(":\n  :\n    - [invalid")

    loader = ProfileLoader(profiles_dir=tmp_path)
    profiles = loader.list_all()
    assert len(profiles) == 1
    assert profiles[0].id == "good"


def test_non_dict_yaml_is_skipped(tmp_path):
    (tmp_path / "list.yaml").write_text("- item1\n- item2\n")
    (tmp_path / "good.yaml").write_text("id: good\nname: Good\nsystem_prompt: Hi.\n")

    loader = ProfileLoader(profiles_dir=tmp_path)
    profiles = loader.list_all()
    assert len(profiles) == 1


def test_model_field_is_none_when_not_specified(tmp_path):
    (tmp_path / "simple.yaml").write_text("id: simple\nname: Simple\nsystem_prompt: Hi.\n")

    loader = ProfileLoader(profiles_dir=tmp_path)
    assert loader.get("simple").model is None


def test_loader_caches_after_first_load(tmp_path):
    (tmp_path / "a.yaml").write_text("id: a\nname: A\nsystem_prompt: Hi.\n")

    loader = ProfileLoader(profiles_dir=tmp_path)
    first = loader.list_all()

    # Add a new file after first load — should NOT appear (cached)
    (tmp_path / "b.yaml").write_text("id: b\nname: B\nsystem_prompt: Hi.\n")
    second = loader.list_all()

    assert len(first) == len(second) == 1
