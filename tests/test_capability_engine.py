from pathlib import Path

from hermes.kernel.capability_engine import CapabilityEngine
from hermes.models import Capability, Task

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = REPO_ROOT / "skills"


def _engine() -> CapabilityEngine:
    return CapabilityEngine(skills_root=SKILLS_ROOT)


def test_matches_copywriting_for_homepage_copy_task():
    task = Task(
        id="t1", business="AVANZIA", request="Write homepage copy for AVANZIA"
    )
    capabilities = _engine().match(task)

    assert all(isinstance(capability, Capability) for capability in capabilities)
    assert [capability.id for capability in capabilities] == ["copywriting"]


def test_matches_python_for_python_refactor_task():
    task = Task(id="t2", business="AVANZIA", request="Refactor the Python backend")
    capabilities = _engine().match(task)

    assert [capability.id for capability in capabilities] == ["python"]


def test_no_match_for_unknown_task():
    task = Task(
        id="t3", business="AVANZIA", request="Plan a company offsite retreat"
    )
    capabilities = _engine().match(task)

    assert capabilities == []
