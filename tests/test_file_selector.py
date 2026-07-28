from hermes.kernel.file_selector import FileSelector
from hermes.models import Task, WorkspaceFile, WorkspaceSnapshot


def _make_file(path: str, repository: str = "") -> WorkspaceFile:
    from pathlib import Path
    return WorkspaceFile(
        path=path,
        extension=Path(path).suffix,
        size=10,
        content="x",
        repository=repository,
    )


def _make_snapshot(paths: list[str]) -> WorkspaceSnapshot:
    return WorkspaceSnapshot(
        root="test",
        files=[_make_file(p) for p in paths],
    )


def _make_snapshot_with_repos(entries: list[tuple[str, str]]) -> WorkspaceSnapshot:
    return WorkspaceSnapshot(
        root="test",
        files=[_make_file(path, repo) for path, repo in entries],
    )


def _task(request: str) -> Task:
    return Task(id="t", business="AVANZIA", request=request)


def _paths(snapshot: WorkspaceSnapshot) -> list[str]:
    return [f.path for f in snapshot.files]


# ---------------------------------------------------------------------------
# Website task
# ---------------------------------------------------------------------------

def test_website_task_ranks_website_repository_files_first():
    snapshot = _make_snapshot_with_repos([
        ("app/page.tsx", "avanzia-website"),
        ("components/navbar.tsx", "avanzia-website"),
        ("package.json", "avanzia-website"),
        ("src/hermes/kernel/executor.py", "hermes-os"),
        ("src/hermes/providers/claude_provider.py", "hermes-os"),
    ])
    selected = FileSelector().select(snapshot, _task("Review the AVANZIA website"))

    website_indices = [i for i, f in enumerate(selected.files) if f.repository == "avanzia-website"]
    hermes_indices = [i for i, f in enumerate(selected.files) if f.repository == "hermes-os"]

    # All website-repo files should rank before the lowest-ranked hermes-os file.
    assert max(website_indices) < max(hermes_indices)


def test_website_task_includes_package_manifest():
    snapshot = _make_snapshot_with_repos([
        ("package.json", "avanzia-website"),
        ("src/hermes/kernel/executor.py", "hermes-os"),
    ])
    selected = FileSelector().select(snapshot, _task("Review the AVANZIA website"))

    assert "package.json" in _paths(selected)


# ---------------------------------------------------------------------------
# Provider task
# ---------------------------------------------------------------------------

def test_provider_task_ranks_provider_file_first():
    snapshot = _make_snapshot([
        "src/hermes/providers/claude_provider.py",
        "tests/test_claude_provider.py",
        "src/hermes/kernel/knowledge_engine.py",
        "src/hermes/kernel/executor.py",
        "src/hermes/runtime/context_engine.py",
    ])
    selected = FileSelector().select(snapshot, _task("Improve ClaudeProvider"))
    paths = _paths(selected)

    # claude_provider.py scores highest ("claude" + "provider" both in filename).
    assert paths[0] == "src/hermes/providers/claude_provider.py"


def test_provider_task_ranks_provider_test_above_unrelated_files():
    snapshot = _make_snapshot([
        "tests/test_claude_provider.py",
        "src/hermes/kernel/knowledge_engine.py",
    ])
    selected = FileSelector().select(snapshot, _task("Improve ClaudeProvider"))
    paths = _paths(selected)

    assert paths.index("tests/test_claude_provider.py") < paths.index("src/hermes/kernel/knowledge_engine.py")


def test_provider_task_camelcase_is_split_correctly():
    # "ClaudeProvider" → ["claude", "provider"]; both must match the filename.
    snapshot = _make_snapshot([
        "src/hermes/providers/claude_provider.py",
        "src/hermes/kernel/executor.py",
    ])
    selected = FileSelector().select(snapshot, _task("Debug ClaudeProvider"))
    paths = _paths(selected)

    assert paths.index("src/hermes/providers/claude_provider.py") < paths.index("src/hermes/kernel/executor.py")


# ---------------------------------------------------------------------------
# Knowledge task
# ---------------------------------------------------------------------------

def test_knowledge_task_ranks_knowledge_engine_first():
    snapshot = _make_snapshot([
        "src/hermes/kernel/knowledge_engine.py",
        "src/hermes/providers/claude_provider.py",
        "src/hermes/kernel/executor.py",
    ])
    selected = FileSelector().select(snapshot, _task("Improve the Knowledge Engine"))
    paths = _paths(selected)

    assert paths[0] == "src/hermes/kernel/knowledge_engine.py"


def test_knowledge_task_ranks_context_engine_above_unrelated():
    # "engine" matches both knowledge_engine.py and context_engine.py.
    snapshot = _make_snapshot([
        "src/hermes/kernel/knowledge_engine.py",
        "src/hermes/runtime/context_engine.py",
        "src/hermes/providers/claude_provider.py",
    ])
    selected = FileSelector().select(snapshot, _task("Improve the Knowledge Engine"))
    paths = _paths(selected)

    assert paths.index("src/hermes/runtime/context_engine.py") < paths.index("src/hermes/providers/claude_provider.py")


# ---------------------------------------------------------------------------
# Structural (baseline) scoring
# ---------------------------------------------------------------------------

def test_readme_always_scores_positively():
    snapshot = _make_snapshot(["README.md", "src/obscure_module.py"])
    selected = FileSelector().select(snapshot, _task("Deploy to production"))
    paths = _paths(selected)

    assert paths[0] == "README.md"


def test_manifest_scores_above_unrelated_code():
    snapshot = _make_snapshot(["package.json", "src/some_module.py"])
    selected = FileSelector().select(snapshot, _task("Deploy to production"))
    paths = _paths(selected)

    assert paths[0] == "package.json"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_empty_snapshot_returns_empty():
    snapshot = WorkspaceSnapshot(root="test", files=[])
    selected = FileSelector().select(snapshot, _task("Do something"))

    assert selected.files == []


def test_snapshot_root_is_preserved():
    snapshot = _make_snapshot(["README.md"])
    selected = FileSelector().select(snapshot, _task("Do something"))

    assert selected.root == "test"


def test_result_never_exceeds_max_selected_files():
    from hermes.kernel.file_selector import MAX_SELECTED_FILES

    paths = [f"src/module_{i}.py" for i in range(MAX_SELECTED_FILES + 20)]
    snapshot = _make_snapshot(paths)
    selected = FileSelector().select(snapshot, _task("Review all modules"))

    assert len(selected.files) <= MAX_SELECTED_FILES
