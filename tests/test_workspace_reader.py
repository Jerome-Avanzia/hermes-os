from pathlib import Path

from hermes.kernel.workspace_reader import MAX_FILE_SIZE_BYTES, WorkspaceReader
from hermes.models import Workspace, WorkspaceContext, WorkspaceSnapshot


def _context_for(path: Path) -> WorkspaceContext:
    return WorkspaceContext(
        workspace=Workspace(project_id="test", path=str(path)),
        exists=True,
        is_git_repo=False,
        branch=None,
        is_clean=None,
        environment=[],
    )


def test_ignored_directories_are_skipped(tmp_path):
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "pkg.json").write_text("{}")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("data")
    (tmp_path / "README.md").write_text("# Hello")

    snapshot = WorkspaceReader().read(_context_for(tmp_path))

    assert {file.path for file in snapshot.files} == {"README.md"}


def test_only_supported_extensions_are_read(tmp_path):
    (tmp_path / "app.py").write_text("print(1)")
    (tmp_path / "notes.md").write_text("# notes")
    (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "archive.zip").write_bytes(b"PK\x03\x04")

    snapshot = WorkspaceReader().read(_context_for(tmp_path))

    assert {file.path for file in snapshot.files} == {"app.py", "notes.md"}


def test_large_files_are_skipped(tmp_path):
    (tmp_path / "small.md").write_text("x" * 100)
    (tmp_path / "large.md").write_text("x" * (MAX_FILE_SIZE_BYTES + 1))

    snapshot = WorkspaceReader().read(_context_for(tmp_path))

    assert {file.path for file in snapshot.files} == {"small.md"}


def test_binary_files_with_supported_extension_are_skipped(tmp_path):
    (tmp_path / "broken.json").write_bytes(b"\xff\xfe\x00\x01binary")
    (tmp_path / "valid.json").write_text('{"ok": true}')

    snapshot = WorkspaceReader().read(_context_for(tmp_path))

    assert {file.path for file in snapshot.files} == {"valid.json"}


def test_snapshot_contains_expected_file_content_and_metadata(tmp_path):
    (tmp_path / "notes.md").write_text("# Notes")

    snapshot = WorkspaceReader().read(_context_for(tmp_path))

    assert isinstance(snapshot, WorkspaceSnapshot)
    assert snapshot.root == str(tmp_path)
    assert len(snapshot.files) == 1

    file = snapshot.files[0]
    assert file.path == "notes.md"
    assert file.extension == ".md"
    assert file.content == "# Notes"
    assert file.size == len("# Notes")


def test_nonexistent_workspace_returns_empty_snapshot(tmp_path):
    missing = tmp_path / "does-not-exist"
    context = WorkspaceContext(
        workspace=Workspace(project_id="test", path=str(missing)),
        exists=False,
        is_git_repo=False,
        branch=None,
        is_clean=None,
        environment=[],
    )

    snapshot = WorkspaceReader().read(context)

    assert snapshot.files == []
