"""Tests for SOPRegistry — deterministic SOP discovery."""

from pathlib import Path

import pytest
import yaml

from hermes.kernel.sop_registry import SOPRegistry
from hermes.models.sop import SOP, SOP_STATUSES

_SKILLS_ROOT = Path("skills")


# -- Lifecycle -----------------------------------------------------------------


def test_registry_build_indexes_seed_sops():
    reg = SOPRegistry(skills_root=_SKILLS_ROOT)
    reg.build()
    sops = reg.list()
    assert len(sops) >= 2  # content-review + brand-audit


def test_registry_build_is_idempotent():
    reg = SOPRegistry(skills_root=_SKILLS_ROOT)
    reg.build()
    count1 = len(reg.list())
    reg.build()  # should be a no-op
    assert len(reg.list()) == count1


def test_registry_reload_rebuilds():
    reg = SOPRegistry(skills_root=_SKILLS_ROOT)
    reg.build()
    assert len(reg.list()) > 0
    reg.reload()
    assert len(reg.list()) > 0


def test_registry_invalidate_clears_index():
    reg = SOPRegistry(skills_root=_SKILLS_ROOT)
    reg.build()
    assert len(reg.list()) > 0
    reg.invalidate()
    # After invalidate, accessing list() should auto-build
    sops = reg.list()
    assert len(sops) > 0


def test_registry_auto_builds_on_first_access():
    reg = SOPRegistry(skills_root=_SKILLS_ROOT)
    sops = reg.list()
    assert len(sops) >= 2


# -- Queries -------------------------------------------------------------------


def test_registry_list_returns_sorted_by_title():
    reg = SOPRegistry(skills_root=_SKILLS_ROOT)
    sops = reg.list()
    titles = [s.title for s in sops]
    assert titles == sorted(titles)


def test_registry_get_returns_sop():
    reg = SOPRegistry(skills_root=_SKILLS_ROOT)
    sop = reg.get("copywriting/content-review")
    assert sop is not None
    assert sop.id == "copywriting/content-review"
    assert sop.title == "Content Review Process"
    assert sop.skill_id == "copywriting"


def test_registry_get_unknown_returns_none():
    reg = SOPRegistry(skills_root=_SKILLS_ROOT)
    assert reg.get("nonexistent/sop") is None


def test_registry_list_by_skill():
    reg = SOPRegistry(skills_root=_SKILLS_ROOT)
    sops = reg.list_by_skill("copywriting")
    assert len(sops) == 1
    assert sops[0].skill_id == "copywriting"


def test_registry_list_by_skill_empty():
    reg = SOPRegistry(skills_root=_SKILLS_ROOT)
    sops = reg.list_by_skill("kernel")
    assert sops == []


# -- Parsing -------------------------------------------------------------------


def test_sop_title_extracted_from_h1():
    reg = SOPRegistry(skills_root=_SKILLS_ROOT)
    sop = reg.get("copywriting/content-review")
    assert sop.title == "Content Review Process"


def test_sop_description_extracted():
    reg = SOPRegistry(skills_root=_SKILLS_ROOT)
    sop = reg.get("copywriting/content-review")
    assert "reviewing marketing copy" in sop.description


def test_sop_content_is_full_file():
    reg = SOPRegistry(skills_root=_SKILLS_ROOT)
    sop = reg.get("copywriting/content-review")
    assert "# Content Review Process" in sop.content
    assert "## Steps" in sop.content


def test_sop_filename():
    reg = SOPRegistry(skills_root=_SKILLS_ROOT)
    sop = reg.get("copywriting/content-review")
    assert sop.filename == "content-review.md"


# -- Frontmatter --------------------------------------------------------------


def test_sop_version_from_frontmatter():
    reg = SOPRegistry(skills_root=_SKILLS_ROOT)
    sop = reg.get("copywriting/content-review")
    assert sop.version == "1.0.0"


def test_sop_category_from_frontmatter():
    reg = SOPRegistry(skills_root=_SKILLS_ROOT)
    sop = reg.get("copywriting/content-review")
    assert sop.category == "Marketing"


def test_sop_owner_inherited_from_manifest():
    reg = SOPRegistry(skills_root=_SKILLS_ROOT)
    sop = reg.get("copywriting/content-review")
    assert sop.owner == "Marketing"


def test_sop_brand_audit_category():
    reg = SOPRegistry(skills_root=_SKILLS_ROOT)
    sop = reg.get("brand-strategy/brand-audit")
    assert sop.category == "Business"


# -- Defaults / edge cases -----------------------------------------------------


def test_sop_title_fallback_to_filename(tmp_path):
    """When no H1 exists, title is derived from filename."""
    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    sops_dir = skill_dir / "sops"
    sops_dir.mkdir()
    (sops_dir / "no-heading.md").write_text("Just some text without a heading.\n")
    manifest = {"id": "test-skill", "name": "Test"}
    with open(skill_dir / "skill.yaml", "w") as f:
        yaml.safe_dump(manifest, f)

    reg = SOPRegistry(skills_root=tmp_path)
    sop = reg.get("test-skill/no-heading")
    assert sop.title == "No Heading"


def test_sop_defaults_without_frontmatter(tmp_path):
    skill_dir = tmp_path / "plain"
    skill_dir.mkdir()
    sops_dir = skill_dir / "sops"
    sops_dir.mkdir()
    (sops_dir / "simple.md").write_text("# Simple SOP\n\nJust do the thing.\n")
    manifest = {"id": "plain", "name": "Plain", "status": "active", "owner": "Ops"}
    with open(skill_dir / "skill.yaml", "w") as f:
        yaml.safe_dump(manifest, f)

    reg = SOPRegistry(skills_root=tmp_path)
    sop = reg.get("plain/simple")
    assert sop.version == ""
    assert sop.status == "active"
    assert sop.owner == "Ops"
    assert sop.category is None


def test_sop_frontmatter_overrides_manifest(tmp_path):
    skill_dir = tmp_path / "override"
    skill_dir.mkdir()
    sops_dir = skill_dir / "sops"
    sops_dir.mkdir()
    content = "---\nstatus: draft\nowner: Finance\ncategory: Business\n---\n\n# Override SOP\n\nDetails.\n"
    (sops_dir / "custom.md").write_text(content)
    manifest = {"id": "override", "name": "Override", "status": "active", "owner": "Ops"}
    with open(skill_dir / "skill.yaml", "w") as f:
        yaml.safe_dump(manifest, f)

    reg = SOPRegistry(skills_root=tmp_path)
    sop = reg.get("override/custom")
    assert sop.status == "draft"
    assert sop.owner == "Finance"
    assert sop.category == "Business"


def test_registry_empty_dir(tmp_path):
    reg = SOPRegistry(skills_root=tmp_path)
    assert reg.list() == []


def test_registry_nonexistent_dir():
    reg = SOPRegistry(skills_root=Path("/nonexistent/path"))
    assert reg.list() == []


def test_registry_skill_without_sops_dir(tmp_path):
    skill_dir = tmp_path / "no-sops"
    skill_dir.mkdir()
    manifest = {"id": "no-sops", "name": "No SOPs"}
    with open(skill_dir / "skill.yaml", "w") as f:
        yaml.safe_dump(manifest, f)

    reg = SOPRegistry(skills_root=tmp_path)
    assert reg.list() == []


def test_registry_ignores_non_md_files(tmp_path):
    skill_dir = tmp_path / "mixed"
    skill_dir.mkdir()
    sops_dir = skill_dir / "sops"
    sops_dir.mkdir()
    (sops_dir / "real.md").write_text("# Real SOP\n\nContent.\n")
    (sops_dir / "notes.txt").write_text("Not an SOP")
    (sops_dir / "data.json").write_text("{}")
    manifest = {"id": "mixed", "name": "Mixed"}
    with open(skill_dir / "skill.yaml", "w") as f:
        yaml.safe_dump(manifest, f)

    reg = SOPRegistry(skills_root=tmp_path)
    assert len(reg.list()) == 1
    assert reg.list()[0].id == "mixed/real"


# -- Status values -------------------------------------------------------------


def test_all_statuses_valid():
    reg = SOPRegistry(skills_root=_SKILLS_ROOT)
    for sop in reg.list():
        assert sop.status in SOP_STATUSES
