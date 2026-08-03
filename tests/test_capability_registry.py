"""Tests for CapabilityRegistry — deterministic capability discovery."""

from pathlib import Path

import pytest
import yaml

from hermes.kernel.capability_registry import CapabilityRegistry
from hermes.models import Task

_SKILLS_ROOT = Path("skills")


# -- Lifecycle -----------------------------------------------------------------


def test_registry_build_indexes_all_skills():
    reg = CapabilityRegistry(skills_root=_SKILLS_ROOT)
    reg.build()
    caps = reg.list()
    # Should have all 6 capabilities (brand-strategy, copywriting, git, kernel, nextjs, python)
    assert len(caps) == 6


def test_registry_build_is_idempotent():
    reg = CapabilityRegistry(skills_root=_SKILLS_ROOT)
    reg.build()
    count1 = len(reg.list())
    reg.build()  # should be a no-op
    assert len(reg.list()) == count1


def test_registry_reload_rebuilds():
    reg = CapabilityRegistry(skills_root=_SKILLS_ROOT)
    reg.build()
    assert len(reg.list()) > 0
    reg.reload()
    assert len(reg.list()) > 0


def test_registry_invalidate_clears_index():
    reg = CapabilityRegistry(skills_root=_SKILLS_ROOT)
    reg.build()
    assert len(reg.list()) > 0
    reg.invalidate()
    # After invalidate, accessing list() should auto-build
    caps = reg.list()
    assert len(caps) > 0


def test_registry_auto_builds_on_first_access():
    reg = CapabilityRegistry(skills_root=_SKILLS_ROOT)
    # Never called build() explicitly
    caps = reg.list()
    assert len(caps) == 6


# -- Queries -------------------------------------------------------------------


def test_registry_list_returns_sorted_by_name():
    reg = CapabilityRegistry(skills_root=_SKILLS_ROOT)
    caps = reg.list()
    names = [c.name for c in caps]
    assert names == sorted(names)


def test_registry_get_returns_capability():
    reg = CapabilityRegistry(skills_root=_SKILLS_ROOT)
    cap = reg.get("copywriting")
    assert cap is not None
    assert cap.id == "copywriting"
    assert cap.name == "Copywriting"


def test_registry_get_unknown_returns_none():
    reg = CapabilityRegistry(skills_root=_SKILLS_ROOT)
    assert reg.get("nonexistent") is None


def test_registry_match_copywriting():
    reg = CapabilityRegistry(skills_root=_SKILLS_ROOT)
    task = Task(id="t1", business="AVANZIA", request="Write homepage copy")
    matches = reg.match(task)
    ids = [c.id for c in matches]
    assert "copywriting" in ids


def test_registry_match_python():
    reg = CapabilityRegistry(skills_root=_SKILLS_ROOT)
    task = Task(id="t1", business="AVANZIA", request="Refactor the Python backend")
    matches = reg.match(task)
    ids = [c.id for c in matches]
    assert "python" in ids


def test_registry_match_fallback_kernel():
    reg = CapabilityRegistry(skills_root=_SKILLS_ROOT)
    task = Task(id="t1", business="AVANZIA", request="Analyze quarterly results")
    matches = reg.match(task)
    assert len(matches) == 1
    assert matches[0].id == "kernel"


def test_registry_match_specialist_excludes_kernel():
    reg = CapabilityRegistry(skills_root=_SKILLS_ROOT)
    task = Task(id="t1", business="AVANZIA", request="Write homepage copy")
    matches = reg.match(task)
    ids = [c.id for c in matches]
    assert "kernel" not in ids


# -- Enriched fields -----------------------------------------------------------


def test_registry_enriched_fields():
    reg = CapabilityRegistry(skills_root=_SKILLS_ROOT)
    cap = reg.get("copywriting")
    assert cap.description == "Drafts and improves marketing copy."
    assert cap.status == "active"
    assert cap.skill_id == "copywriting"
    assert "project brief" in cap.inputs
    assert "marketing copy draft" in cap.outputs
    assert cap.owner == "Marketing"
    assert "brand-strategy" in cap.depends_on


def test_registry_kernel_has_enriched_fields():
    reg = CapabilityRegistry(skills_root=_SKILLS_ROOT)
    cap = reg.get("kernel")
    assert cap.status == "active"
    assert cap.owner == "Operations"
    assert cap.depends_on == []


def test_registry_defaults_for_missing_fields(tmp_path):
    """A minimal manifest without new fields gets sensible defaults."""
    skill_dir = tmp_path / "minimal"
    skill_dir.mkdir()
    manifest = {
        "id": "minimal",
        "name": "Minimal",
        "version": "0.1.0",
        "capabilities": ["minimal"],
        "keywords": ["minimal"],
        "provides": ["something"],
    }
    with open(skill_dir / "skill.yaml", "w") as f:
        yaml.safe_dump(manifest, f)

    reg = CapabilityRegistry(skills_root=tmp_path)
    cap = reg.get("minimal")
    assert cap is not None
    assert cap.description == ""
    assert cap.status == "active"
    assert cap.inputs == []
    assert cap.outputs == []
    assert cap.sop_ref is None
    assert cap.owner is None
    assert cap.depends_on == []


def test_registry_empty_dir(tmp_path):
    reg = CapabilityRegistry(skills_root=tmp_path)
    assert reg.list() == []


def test_registry_nonexistent_dir():
    reg = CapabilityRegistry(skills_root=Path("/nonexistent/path"))
    assert reg.list() == []


# -- Status values -------------------------------------------------------------


def test_registry_all_statuses_valid():
    from hermes.models.capability import CAPABILITY_STATUSES
    reg = CapabilityRegistry(skills_root=_SKILLS_ROOT)
    for cap in reg.list():
        assert cap.status in CAPABILITY_STATUSES
