"""Tests for DepartmentRegistry — deterministic department discovery."""

from pathlib import Path

import pytest
import yaml

from hermes.kernel.department_registry import DepartmentRegistry
from hermes.models.department import Department, DEPARTMENT_STATUSES

_DEPTS_ROOT = Path("departments")


# -- Lifecycle -----------------------------------------------------------------


def test_registry_build_indexes_all_departments():
    reg = DepartmentRegistry(departments_root=_DEPTS_ROOT)
    reg.build()
    depts = reg.list()
    assert len(depts) == 4  # business, marketing, platform, technology


def test_registry_build_is_idempotent():
    reg = DepartmentRegistry(departments_root=_DEPTS_ROOT)
    reg.build()
    count1 = len(reg.list())
    reg.build()
    assert len(reg.list()) == count1


def test_registry_reload_rebuilds():
    reg = DepartmentRegistry(departments_root=_DEPTS_ROOT)
    reg.build()
    assert len(reg.list()) > 0
    reg.reload()
    assert len(reg.list()) > 0


def test_registry_invalidate_clears_index():
    reg = DepartmentRegistry(departments_root=_DEPTS_ROOT)
    reg.build()
    assert len(reg.list()) > 0
    reg.invalidate()
    depts = reg.list()
    assert len(depts) > 0


def test_registry_auto_builds_on_first_access():
    reg = DepartmentRegistry(departments_root=_DEPTS_ROOT)
    depts = reg.list()
    assert len(depts) == 4


# -- Queries -------------------------------------------------------------------


def test_registry_list_returns_sorted_by_name():
    reg = DepartmentRegistry(departments_root=_DEPTS_ROOT)
    depts = reg.list()
    names = [d.name for d in depts]
    assert names == sorted(names)


def test_registry_get_returns_department():
    reg = DepartmentRegistry(departments_root=_DEPTS_ROOT)
    dept = reg.get("marketing")
    assert dept is not None
    assert dept.id == "marketing"
    assert dept.name == "Marketing"


def test_registry_get_unknown_returns_none():
    reg = DepartmentRegistry(departments_root=_DEPTS_ROOT)
    assert reg.get("nonexistent") is None


# -- Enriched fields -----------------------------------------------------------


def test_registry_marketing_fields():
    reg = DepartmentRegistry(departments_root=_DEPTS_ROOT)
    dept = reg.get("marketing")
    assert dept.description == "Brand, content, and market positioning."
    assert "brand" in dept.mission.lower()
    assert dept.owner == "Chief Marketing Officer"
    assert dept.status == "active"
    assert "brand" in dept.tags


def test_registry_platform_fields():
    reg = DepartmentRegistry(departments_root=_DEPTS_ROOT)
    dept = reg.get("platform")
    assert dept.name == "Platform"
    assert "hermes" in dept.tags


def test_registry_defaults_for_missing_fields(tmp_path):
    dept_dir = tmp_path / "minimal"
    dept_dir.mkdir()
    manifest = {"id": "minimal", "name": "Minimal"}
    with open(dept_dir / "department.yaml", "w") as f:
        yaml.safe_dump(manifest, f)

    reg = DepartmentRegistry(departments_root=tmp_path)
    dept = reg.get("minimal")
    assert dept is not None
    assert dept.description == ""
    assert dept.mission == ""
    assert dept.owner is None
    assert dept.status == "active"
    assert dept.tags == []


# -- Edge cases ----------------------------------------------------------------


def test_registry_empty_dir(tmp_path):
    reg = DepartmentRegistry(departments_root=tmp_path)
    assert reg.list() == []


def test_registry_nonexistent_dir():
    reg = DepartmentRegistry(departments_root=Path("/nonexistent/path"))
    assert reg.list() == []


def test_registry_dir_without_manifest(tmp_path):
    (tmp_path / "empty-dept").mkdir()
    reg = DepartmentRegistry(departments_root=tmp_path)
    assert reg.list() == []


# -- Status values -------------------------------------------------------------


def test_all_statuses_valid():
    reg = DepartmentRegistry(departments_root=_DEPTS_ROOT)
    for dept in reg.list():
        assert dept.status in DEPARTMENT_STATUSES
