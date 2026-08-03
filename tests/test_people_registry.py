"""Tests for PeopleRegistry — deterministic people discovery."""

import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from hermes.kernel.people_registry import PeopleRegistry
from hermes.models.person import Person, PERSON_STATUSES

_PEOPLE_ROOT = Path("people")
ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"
EXAMPLES = ROOT / "examples"


# -- Lifecycle -----------------------------------------------------------------


def test_registry_build_indexes_all_people():
    reg = PeopleRegistry(people_root=_PEOPLE_ROOT)
    reg.build()
    people = reg.list()
    assert len(people) == 5


def test_registry_build_is_idempotent():
    reg = PeopleRegistry(people_root=_PEOPLE_ROOT)
    reg.build()
    count1 = len(reg.list())
    reg.build()
    assert len(reg.list()) == count1


def test_registry_reload_rebuilds():
    reg = PeopleRegistry(people_root=_PEOPLE_ROOT)
    reg.build()
    assert len(reg.list()) > 0
    reg.reload()
    assert len(reg.list()) > 0


def test_registry_invalidate_clears_index():
    reg = PeopleRegistry(people_root=_PEOPLE_ROOT)
    reg.build()
    assert len(reg.list()) > 0
    reg.invalidate()
    people = reg.list()
    assert len(people) > 0


def test_registry_auto_builds_on_first_access():
    reg = PeopleRegistry(people_root=_PEOPLE_ROOT)
    people = reg.list()
    assert len(people) == 5


# -- Queries -------------------------------------------------------------------


def test_registry_list_returns_sorted_by_name():
    reg = PeopleRegistry(people_root=_PEOPLE_ROOT)
    people = reg.list()
    names = [p.name for p in people]
    assert names == sorted(names)


def test_registry_get_returns_person():
    reg = PeopleRegistry(people_root=_PEOPLE_ROOT)
    person = reg.get("jerome-cornet")
    assert person is not None
    assert person.id == "jerome-cornet"
    assert person.name == "Jerome Cornet"


def test_registry_get_unknown_returns_none():
    reg = PeopleRegistry(people_root=_PEOPLE_ROOT)
    assert reg.get("nonexistent") is None


# -- Enriched fields -----------------------------------------------------------


def test_registry_founder_fields():
    reg = PeopleRegistry(people_root=_PEOPLE_ROOT)
    person = reg.get("jerome-cornet")
    assert person.title == "Founder & CEO"
    assert "business" in person.department_ids
    assert person.email == "jerome@avanzia.com"
    assert person.status == "active"
    assert "strategy" in person.responsibilities
    assert "Founder" in person.owner_aliases
    assert "CEO" in person.owner_aliases


def test_registry_engineering_lead_fields():
    reg = PeopleRegistry(people_root=_PEOPLE_ROOT)
    person = reg.get("engineering-lead")
    assert person.title == "Chief Technology Officer"
    assert "technology" in person.department_ids
    assert "platform" in person.department_ids
    assert "Chief Technology Officer" in person.owner_aliases


def test_registry_department_ids_is_list():
    """Amendment 2: department_ids is always a list."""
    reg = PeopleRegistry(people_root=_PEOPLE_ROOT)
    for person in reg.list():
        assert isinstance(person.department_ids, list)


def test_registry_responsibilities_are_tags():
    """Amendment 4: responsibilities are stable tags, not free text."""
    reg = PeopleRegistry(people_root=_PEOPLE_ROOT)
    person = reg.get("jerome-cornet")
    for tag in person.responsibilities:
        assert " " not in tag or "-" in tag  # tags use kebab-case


def test_registry_owner_aliases_are_explicit():
    """Amendment 3: ownership resolved via explicit aliases, not title."""
    reg = PeopleRegistry(people_root=_PEOPLE_ROOT)
    for person in reg.list():
        assert isinstance(person.owner_aliases, list)


def test_registry_defaults_for_missing_fields(tmp_path):
    person_dir = tmp_path / "minimal"
    person_dir.mkdir()
    manifest = {"id": "minimal", "name": "Minimal Person", "title": "Intern"}
    with open(person_dir / "person.yaml", "w") as f:
        yaml.safe_dump(manifest, f)

    reg = PeopleRegistry(people_root=tmp_path)
    person = reg.get("minimal")
    assert person is not None
    assert person.department_ids == []
    assert person.email is None
    assert person.status == "active"
    assert person.responsibilities == []
    assert person.owner_aliases == []


# -- Edge cases ----------------------------------------------------------------


def test_registry_empty_dir(tmp_path):
    reg = PeopleRegistry(people_root=tmp_path)
    assert reg.list() == []


def test_registry_nonexistent_dir():
    reg = PeopleRegistry(people_root=Path("/nonexistent/path"))
    assert reg.list() == []


def test_registry_dir_without_manifest(tmp_path):
    (tmp_path / "empty-person").mkdir()
    reg = PeopleRegistry(people_root=tmp_path)
    assert reg.list() == []


# -- Status values -------------------------------------------------------------


def test_all_statuses_valid():
    reg = PeopleRegistry(people_root=_PEOPLE_ROOT)
    for person in reg.list():
        assert person.status in PERSON_STATUSES


# -- Schema & Example ---------------------------------------------------------


class TestPersonSchema:
    def test_example_validates_against_schema(self):
        schema = json.loads((CONTRACTS / "person.schema.json").read_text())
        example = json.loads((EXAMPLES / "person.example.json").read_text())
        validator = Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(example), key=lambda e: list(e.path))
        assert not errors, f"Schema validation errors: {[e.message for e in errors]}"

    def test_schema_required_fields(self):
        schema = json.loads((CONTRACTS / "person.schema.json").read_text())
        assert set(schema["required"]) == {"id", "name", "title"}

    def test_schema_status_enum(self):
        schema = json.loads((CONTRACTS / "person.schema.json").read_text())
        assert set(schema["properties"]["status"]["enum"]) == {"active", "inactive"}
