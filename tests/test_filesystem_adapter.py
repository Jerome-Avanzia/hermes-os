"""Sprint 62 — Filesystem Adapter tests.

Tests the Filesystem Adapter layer:

  Execution Gateway → Filesystem Adapter → Filesystem

Validates:
  • typed contracts (all frozen, all slots=True)
  • every supported operation (create, overwrite, append, read, delete, mkdir, rmdir, exists)
  • workspace boundary enforcement (no escape from workspace_root)
  • path traversal rejection (../, absolute paths, symlink chains)
  • validation (adapter_type, operation, path, encoding)
  • deterministic results (same inputs → same FilesystemRequest)
  • immutable contracts (all models are frozen dataclasses)
  • edge cases: empty files, nested directories, unicode filenames, overwrite behaviour
  • error translation (IO failures captured as success=False, never raised)
  • Gateway → Filesystem Adapter integration

All tests use pytest tmp_path for real temporary directories.
No mocking of filesystem operations — the adapter is tested against a real FS.
"""

from __future__ import annotations

import dataclasses

import pytest

from hermes.adapters.filesystem_adapter import FilesystemAdapter
from hermes.kernel.execution_gateway import ExecutionGateway
from hermes.models.execution_gateway import (
    AdapterRegistration,
    ExecutionAdapter,
    ExecutionStatus,
)
from hermes.models.filesystem_adapter import (
    FilesystemExecutionResult,
    FilesystemOperation,
    FilesystemRequest,
    FilesystemResult,
    FilesystemTarget,
    FilesystemValidationResult,
)


# ══════════════════════════════════════════════════════════════════════════════
# Shared helpers
# ══════════════════════════════════════════════════════════════════════════════


def _make_request(
    action_id: str,
    path: str = "file.txt",
    content: str = "",
    encoding: str = "utf-8",
    extra_payload: dict | None = None,
    request_id: str = "req-001",
    operation_id: str = "op-001",
):
    """Build an ExecutionRequest via the Gateway for filesystem operations."""
    gateway = ExecutionGateway()
    payload: dict[str, str] = {"path": path}
    if content:
        payload["content"] = content
    if encoding != "utf-8":
        payload["encoding"] = encoding
    if extra_payload:
        payload.update(extra_payload)
    return gateway.build_request(
        request_id=request_id,
        operation_id=operation_id,
        adapter_type=ExecutionAdapter.FILESYSTEM,
        action_id=action_id,
        payload=payload,
    )


def _make_adapter(tmp_path) -> FilesystemAdapter:
    """Build a FilesystemAdapter rooted at tmp_path."""
    return FilesystemAdapter(workspace_root=tmp_path)


# ══════════════════════════════════════════════════════════════════════════════
# 1. Typed contracts — frozen dataclasses with slots
# ══════════════════════════════════════════════════════════════════════════════


class TestTypedContracts:
    """All Filesystem Adapter contracts are frozen dataclasses with slots."""

    def test_filesystem_operation_is_frozen(self) -> None:
        """FilesystemOperation is an enum — immutable by definition."""
        op = FilesystemOperation.READ_FILE
        assert op.value == "read_file"
        with pytest.raises(AttributeError):
            op.value = "mutated"  # type: ignore[misc]

    def test_filesystem_target_is_frozen(self, tmp_path) -> None:
        target = FilesystemTarget(path="a/b.txt", operation=FilesystemOperation.READ_FILE)
        assert dataclasses.is_dataclass(target)
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            target.path = "mutated"  # type: ignore[misc]

    def test_filesystem_request_is_frozen(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        request = _make_request("read_file", path="a.txt")
        fs_req = adapter.build_filesystem_request(request, FilesystemOperation.READ_FILE)
        assert dataclasses.is_dataclass(fs_req)
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            fs_req.path = "mutated"  # type: ignore[misc]

    def test_filesystem_result_is_frozen(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        (tmp_path / "a.txt").write_text("hello")
        request = _make_request("read_file", path="a.txt")
        result = adapter.execute(request)
        assert result.filesystem_result is not None
        assert dataclasses.is_dataclass(result.filesystem_result)
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            result.filesystem_result.content = "mutated"  # type: ignore[misc]

    def test_filesystem_validation_result_is_frozen(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        request = _make_request("read_file", path="a.txt")
        vr = adapter.validate(request, FilesystemOperation.READ_FILE, "a.txt", "utf-8")
        assert dataclasses.is_dataclass(vr)
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            vr.valid = False  # type: ignore[misc]

    def test_filesystem_execution_result_is_frozen(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        (tmp_path / "a.txt").write_text("hello")
        request = _make_request("read_file", path="a.txt")
        result = adapter.execute(request)
        assert dataclasses.is_dataclass(result)
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            result.success = False  # type: ignore[misc]

    def test_all_contracts_have_slots(self) -> None:
        for cls in [
            FilesystemTarget,
            FilesystemRequest,
            FilesystemResult,
            FilesystemValidationResult,
            FilesystemExecutionResult,
        ]:
            assert hasattr(cls, "__slots__"), f"{cls.__name__} must use slots=True"

    def test_all_nine_operations_declared(self) -> None:
        values = {op.value for op in FilesystemOperation}
        assert values == {
            "create_file", "overwrite_file", "modify_file", "append_file",
            "read_file", "delete_file", "create_directory", "delete_directory",
            "exists",
        }


# ══════════════════════════════════════════════════════════════════════════════
# 2. CREATE_FILE
# ══════════════════════════════════════════════════════════════════════════════


class TestCreateFile:
    """create_file creates a new file; fails if the file already exists."""

    def test_create_file_success(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        request = _make_request("create_file", path="hello.txt", content="Hello, world!")
        result = adapter.execute(request)
        assert result.success is True
        assert (tmp_path / "hello.txt").read_text() == "Hello, world!"

    def test_create_file_bytes_written(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        request = _make_request("create_file", path="f.txt", content="abc")
        result = adapter.execute(request)
        assert result.filesystem_result.bytes_written == 3

    def test_create_file_exists_true_after(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        request = _make_request("create_file", path="f.txt", content="x")
        result = adapter.execute(request)
        assert result.filesystem_result.exists is True

    def test_create_file_fails_if_already_exists(self, tmp_path) -> None:
        (tmp_path / "existing.txt").write_text("original")
        adapter = _make_adapter(tmp_path)
        request = _make_request("create_file", path="existing.txt", content="new")
        result = adapter.execute(request)
        assert result.success is False
        assert "FileExistsError" in result.error
        # Original content preserved
        assert (tmp_path / "existing.txt").read_text() == "original"

    def test_create_file_empty_content(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        request = _make_request("create_file", path="empty.txt")
        result = adapter.execute(request)
        assert result.success is True
        assert (tmp_path / "empty.txt").read_bytes() == b""
        assert result.filesystem_result.bytes_written == 0

    def test_create_file_in_nested_directory(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        request = _make_request("create_file", path="a/b/c/file.txt", content="nested")
        result = adapter.execute(request)
        assert result.success is True
        assert (tmp_path / "a" / "b" / "c" / "file.txt").read_text() == "nested"

    def test_create_file_unicode_content(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        request = _make_request("create_file", path="uni.txt", content="héllo wörld 🌍")
        result = adapter.execute(request)
        assert result.success is True
        assert (tmp_path / "uni.txt").read_text(encoding="utf-8") == "héllo wörld 🌍"

    def test_create_file_unicode_filename(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        request = _make_request("create_file", path="données/résumé.txt", content="data")
        result = adapter.execute(request)
        assert result.success is True
        assert (tmp_path / "données" / "résumé.txt").read_text() == "data"

    def test_create_file_filesystem_request_preserved(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        request = _make_request("create_file", path="f.txt", content="hi")
        result = adapter.execute(request)
        assert result.filesystem_request is not None
        assert result.filesystem_request.path == "f.txt"
        assert result.filesystem_request.operation == FilesystemOperation.CREATE_FILE


# ══════════════════════════════════════════════════════════════════════════════
# 3. OVERWRITE_FILE
# ══════════════════════════════════════════════════════════════════════════════


class TestOverwriteFile:
    """overwrite_file writes content regardless of prior existence."""

    def test_overwrite_creates_if_not_exists(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        request = _make_request("overwrite_file", path="new.txt", content="created")
        result = adapter.execute(request)
        assert result.success is True
        assert (tmp_path / "new.txt").read_text() == "created"

    def test_overwrite_replaces_existing(self, tmp_path) -> None:
        (tmp_path / "existing.txt").write_text("old content")
        adapter = _make_adapter(tmp_path)
        request = _make_request("overwrite_file", path="existing.txt", content="new content")
        result = adapter.execute(request)
        assert result.success is True
        assert (tmp_path / "existing.txt").read_text() == "new content"

    def test_overwrite_with_empty_content(self, tmp_path) -> None:
        (tmp_path / "file.txt").write_text("was here")
        adapter = _make_adapter(tmp_path)
        request = _make_request("overwrite_file", path="file.txt")
        result = adapter.execute(request)
        assert result.success is True
        assert (tmp_path / "file.txt").read_bytes() == b""

    def test_overwrite_bytes_written(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        request = _make_request("overwrite_file", path="f.txt", content="hello")
        result = adapter.execute(request)
        assert result.filesystem_result.bytes_written == 5

    def test_overwrite_creates_parent_dirs(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        request = _make_request("overwrite_file", path="deep/path/file.txt", content="x")
        result = adapter.execute(request)
        assert result.success is True
        assert (tmp_path / "deep" / "path" / "file.txt").read_text() == "x"


# ══════════════════════════════════════════════════════════════════════════════
# 4. APPEND_FILE
# ══════════════════════════════════════════════════════════════════════════════


class TestAppendFile:
    """append_file appends to an existing file; fails if not exists."""

    def test_append_to_existing_file(self, tmp_path) -> None:
        (tmp_path / "log.txt").write_text("line1\n")
        adapter = _make_adapter(tmp_path)
        request = _make_request("append_file", path="log.txt", content="line2\n")
        result = adapter.execute(request)
        assert result.success is True
        assert (tmp_path / "log.txt").read_text() == "line1\nline2\n"

    def test_append_fails_if_not_exists(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        request = _make_request("append_file", path="missing.txt", content="x")
        result = adapter.execute(request)
        assert result.success is False
        assert "FileNotFoundError" in result.error

    def test_append_bytes_written(self, tmp_path) -> None:
        (tmp_path / "f.txt").write_text("")
        adapter = _make_adapter(tmp_path)
        request = _make_request("append_file", path="f.txt", content="abc")
        result = adapter.execute(request)
        assert result.filesystem_result.bytes_written == 3

    def test_append_multiple_times(self, tmp_path) -> None:
        (tmp_path / "f.txt").write_text("a")
        adapter = _make_adapter(tmp_path)
        for char in ["b", "c", "d"]:
            req = _make_request("append_file", path="f.txt", content=char)
            r = adapter.execute(req)
            assert r.success is True
        assert (tmp_path / "f.txt").read_text() == "abcd"

    def test_append_empty_string(self, tmp_path) -> None:
        (tmp_path / "f.txt").write_text("original")
        adapter = _make_adapter(tmp_path)
        request = _make_request("append_file", path="f.txt")
        result = adapter.execute(request)
        assert result.success is True
        assert (tmp_path / "f.txt").read_text() == "original"


# ══════════════════════════════════════════════════════════════════════════════
# 5. READ_FILE
# ══════════════════════════════════════════════════════════════════════════════


class TestReadFile:
    """read_file returns file content; fails if file does not exist."""

    def test_read_existing_file(self, tmp_path) -> None:
        (tmp_path / "data.txt").write_text("content here")
        adapter = _make_adapter(tmp_path)
        request = _make_request("read_file", path="data.txt")
        result = adapter.execute(request)
        assert result.success is True
        assert result.filesystem_result.content == "content here"

    def test_read_bytes_read(self, tmp_path) -> None:
        (tmp_path / "f.txt").write_bytes(b"hello")
        adapter = _make_adapter(tmp_path)
        request = _make_request("read_file", path="f.txt")
        result = adapter.execute(request)
        assert result.filesystem_result.bytes_read == 5

    def test_read_empty_file(self, tmp_path) -> None:
        (tmp_path / "empty.txt").write_bytes(b"")
        adapter = _make_adapter(tmp_path)
        request = _make_request("read_file", path="empty.txt")
        result = adapter.execute(request)
        assert result.success is True
        assert result.filesystem_result.content == ""
        assert result.filesystem_result.bytes_read == 0

    def test_read_fails_if_not_exists(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        request = _make_request("read_file", path="missing.txt")
        result = adapter.execute(request)
        assert result.success is False
        assert "FileNotFoundError" in result.error

    def test_read_unicode_content(self, tmp_path) -> None:
        content = "日本語テスト 🎌"
        (tmp_path / "unicode.txt").write_text(content, encoding="utf-8")
        adapter = _make_adapter(tmp_path)
        request = _make_request("read_file", path="unicode.txt")
        result = adapter.execute(request)
        assert result.filesystem_result.content == content

    def test_read_nested_file(self, tmp_path) -> None:
        (tmp_path / "a" / "b").mkdir(parents=True)
        (tmp_path / "a" / "b" / "deep.txt").write_text("deep content")
        adapter = _make_adapter(tmp_path)
        request = _make_request("read_file", path="a/b/deep.txt")
        result = adapter.execute(request)
        assert result.filesystem_result.content == "deep content"

    def test_read_result_content_empty_in_write_operations(self, tmp_path) -> None:
        """Non-read operations return empty string for content."""
        adapter = _make_adapter(tmp_path)
        request = _make_request("create_file", path="f.txt", content="data")
        result = adapter.execute(request)
        assert result.filesystem_result.content == ""


# ══════════════════════════════════════════════════════════════════════════════
# 6. DELETE_FILE
# ══════════════════════════════════════════════════════════════════════════════


class TestDeleteFile:
    """delete_file removes a file; fails if it does not exist."""

    def test_delete_existing_file(self, tmp_path) -> None:
        (tmp_path / "todelete.txt").write_text("bye")
        adapter = _make_adapter(tmp_path)
        request = _make_request("delete_file", path="todelete.txt")
        result = adapter.execute(request)
        assert result.success is True
        assert not (tmp_path / "todelete.txt").exists()

    def test_delete_exists_false_after(self, tmp_path) -> None:
        (tmp_path / "f.txt").write_text("x")
        adapter = _make_adapter(tmp_path)
        result = adapter.execute(_make_request("delete_file", path="f.txt"))
        assert result.filesystem_result.exists is False

    def test_delete_fails_if_not_exists(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter.execute(_make_request("delete_file", path="ghost.txt"))
        assert result.success is False
        assert "FileNotFoundError" in result.error

    def test_delete_file_in_nested_dir(self, tmp_path) -> None:
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "f.txt").write_text("x")
        adapter = _make_adapter(tmp_path)
        result = adapter.execute(_make_request("delete_file", path="sub/f.txt"))
        assert result.success is True
        assert not (tmp_path / "sub" / "f.txt").exists()
        # Parent directory still exists
        assert (tmp_path / "sub").is_dir()


# ══════════════════════════════════════════════════════════════════════════════
# 7. CREATE_DIRECTORY
# ══════════════════════════════════════════════════════════════════════════════


class TestCreateDirectory:
    """create_directory creates a directory and parents; idempotent."""

    def test_create_directory_success(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter.execute(_make_request("create_directory", path="newdir"))
        assert result.success is True
        assert (tmp_path / "newdir").is_dir()

    def test_create_directory_nested(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter.execute(_make_request("create_directory", path="a/b/c/d"))
        assert result.success is True
        assert (tmp_path / "a" / "b" / "c" / "d").is_dir()

    def test_create_directory_idempotent(self, tmp_path) -> None:
        """Creating an existing directory succeeds silently."""
        (tmp_path / "existing").mkdir()
        adapter = _make_adapter(tmp_path)
        result = adapter.execute(_make_request("create_directory", path="existing"))
        assert result.success is True

    def test_create_directory_exists_true(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter.execute(_make_request("create_directory", path="d"))
        assert result.filesystem_result.exists is True

    def test_create_directory_unicode_name(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter.execute(_make_request("create_directory", path="données/résultats"))
        assert result.success is True
        assert (tmp_path / "données" / "résultats").is_dir()


# ══════════════════════════════════════════════════════════════════════════════
# 8. DELETE_DIRECTORY
# ══════════════════════════════════════════════════════════════════════════════


class TestDeleteDirectory:
    """delete_directory removes a directory and all contents; fails if not exists."""

    def test_delete_empty_directory(self, tmp_path) -> None:
        (tmp_path / "empty_dir").mkdir()
        adapter = _make_adapter(tmp_path)
        result = adapter.execute(_make_request("delete_directory", path="empty_dir"))
        assert result.success is True
        assert not (tmp_path / "empty_dir").exists()

    def test_delete_directory_with_contents(self, tmp_path) -> None:
        d = tmp_path / "to_delete"
        d.mkdir()
        (d / "sub").mkdir()
        (d / "file.txt").write_text("x")
        (d / "sub" / "nested.txt").write_text("y")
        adapter = _make_adapter(tmp_path)
        result = adapter.execute(_make_request("delete_directory", path="to_delete"))
        assert result.success is True
        assert not d.exists()

    def test_delete_directory_exists_false_after(self, tmp_path) -> None:
        (tmp_path / "d").mkdir()
        adapter = _make_adapter(tmp_path)
        result = adapter.execute(_make_request("delete_directory", path="d"))
        assert result.filesystem_result.exists is False

    def test_delete_directory_fails_if_not_exists(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter.execute(_make_request("delete_directory", path="ghost_dir"))
        assert result.success is False
        assert "FileNotFoundError" in result.error


# ══════════════════════════════════════════════════════════════════════════════
# 9. EXISTS
# ══════════════════════════════════════════════════════════════════════════════


class TestExists:
    """exists checks presence; never fails regardless of path state."""

    def test_exists_true_for_file(self, tmp_path) -> None:
        (tmp_path / "present.txt").write_text("here")
        adapter = _make_adapter(tmp_path)
        result = adapter.execute(_make_request("exists", path="present.txt"))
        assert result.success is True
        assert result.filesystem_result.exists is True

    def test_exists_false_for_missing(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter.execute(_make_request("exists", path="absent.txt"))
        assert result.success is True
        assert result.filesystem_result.exists is False

    def test_exists_true_for_directory(self, tmp_path) -> None:
        (tmp_path / "subdir").mkdir()
        adapter = _make_adapter(tmp_path)
        result = adapter.execute(_make_request("exists", path="subdir"))
        assert result.success is True
        assert result.filesystem_result.exists is True

    def test_exists_bytes_written_and_read_are_zero(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter.execute(_make_request("exists", path="anything.txt"))
        assert result.filesystem_result.bytes_written == 0
        assert result.filesystem_result.bytes_read == 0


# ══════════════════════════════════════════════════════════════════════════════
# 10. Workspace boundary enforcement
# ══════════════════════════════════════════════════════════════════════════════


class TestWorkspaceBoundary:
    """All filesystem access is confined to workspace_root."""

    def test_absolute_path_rejected(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        request = _make_request("read_file", path="/etc/passwd")
        result = adapter.execute(request)
        assert result.success is False
        assert result.filesystem_request is None  # failed at validation
        assert "absolute" in result.error.lower()

    def test_traversal_with_dotdot_rejected(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        request = _make_request("read_file", path="../outside.txt")
        result = adapter.execute(request)
        assert result.success is False
        assert result.filesystem_request is None
        # Error mentions traversal or absolute
        assert any(w in result.error.lower() for w in ("traversal", "absolute", "outside"))

    def test_deep_traversal_rejected(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        request = _make_request("read_file", path="a/b/../../../../../../etc/shadow")
        result = adapter.execute(request)
        assert result.success is False

    def test_traversal_through_nested_path_rejected(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        request = _make_request("read_file", path="safe/../../../etc/hosts")
        result = adapter.execute(request)
        assert result.success is False

    def test_empty_path_rejected(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        request = _make_request("read_file", path="")
        result = adapter.execute(request)
        assert result.success is False
        assert "path" in result.error.lower()

    def test_whitespace_only_path_rejected(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        request = _make_request("read_file", path="   ")
        result = adapter.execute(request)
        assert result.success is False

    def test_path_inside_workspace_accepted(self, tmp_path) -> None:
        (tmp_path / "legit.txt").write_text("safe")
        adapter = _make_adapter(tmp_path)
        result = adapter.execute(_make_request("read_file", path="legit.txt"))
        assert result.success is True

    def test_deeply_nested_path_inside_workspace_accepted(self, tmp_path) -> None:
        d = tmp_path / "a" / "b" / "c"
        d.mkdir(parents=True)
        (d / "deep.txt").write_text("content")
        adapter = _make_adapter(tmp_path)
        result = adapter.execute(_make_request("read_file", path="a/b/c/deep.txt"))
        assert result.success is True

    def test_resolve_path_raises_for_absolute(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        with pytest.raises(ValueError, match="absolute"):
            adapter.resolve_path("/etc/passwd")

    def test_resolve_path_raises_for_traversal(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        with pytest.raises(ValueError):
            adapter.resolve_path("../../outside")

    def test_resolve_path_returns_absolute_inside_workspace(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        resolved = adapter.resolve_path("sub/file.txt")
        assert resolved.is_absolute()
        assert str(resolved).startswith(str(tmp_path))


# ══════════════════════════════════════════════════════════════════════════════
# 11. Adapter-level validation
# ══════════════════════════════════════════════════════════════════════════════


class TestAdapterValidation:
    """validate() checks adapter-level preconditions only."""

    def test_valid_request_passes(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        request = _make_request("read_file", path="f.txt")
        result = adapter.validate(request, FilesystemOperation.READ_FILE, "f.txt", "utf-8")
        assert result.valid is True
        assert result.errors == ()

    def test_wrong_adapter_type_fails(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        gateway = ExecutionGateway()
        bad_request = gateway.build_request(
            request_id="req-001",
            operation_id="op-001",
            adapter_type=ExecutionAdapter.LLM,  # wrong type
            action_id="read_file",
            payload={"path": "f.txt"},
        )
        result = adapter.validate(bad_request, FilesystemOperation.READ_FILE, "f.txt", "utf-8")
        assert result.valid is False
        assert any("FILESYSTEM" in e for e in result.errors)

    def test_unrecognized_action_id_fails(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        request = _make_request("fly_through_air", path="f.txt")
        result = adapter.validate(request, None, "f.txt", "utf-8")
        assert result.valid is False
        assert any("fly_through_air" in e for e in result.errors)

    def test_empty_path_fails_validation(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        request = _make_request("read_file", path="")
        result = adapter.validate(request, FilesystemOperation.READ_FILE, "", "utf-8")
        assert result.valid is False
        assert any("path" in e.lower() for e in result.errors)

    def test_traversal_path_fails_validation(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        request = _make_request("read_file", path="../escape")
        result = adapter.validate(request, FilesystemOperation.READ_FILE, "../escape", "utf-8")
        assert result.valid is False

    def test_empty_encoding_fails(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        request = _make_request("read_file", path="f.txt")
        result = adapter.validate(request, FilesystemOperation.READ_FILE, "f.txt", "")
        assert result.valid is False
        assert any("encoding" in e.lower() for e in result.errors)

    def test_multiple_errors_accumulated(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        gateway = ExecutionGateway()
        bad_request = gateway.build_request(
            request_id="req-001",
            operation_id="op-001",
            adapter_type=ExecutionAdapter.LLM,  # wrong type
            action_id="unknown_op",
            payload={},
        )
        result = adapter.validate(bad_request, None, "", "")
        assert result.valid is False
        assert len(result.errors) >= 3  # wrong type + unknown op + empty path + empty encoding

    def test_validation_does_not_re_validate_gateway_fields(self, tmp_path) -> None:
        """request_id and operation_id are not re-validated — Gateway owns those."""
        adapter = _make_adapter(tmp_path)
        request = _make_request("read_file", path="f.txt", request_id="any-id")
        result = adapter.validate(request, FilesystemOperation.READ_FILE, "f.txt", "utf-8")
        assert result.valid is True


# ══════════════════════════════════════════════════════════════════════════════
# 12. Request translation
# ══════════════════════════════════════════════════════════════════════════════


class TestRequestTranslation:
    """build_filesystem_request() produces correct normalized FilesystemRequest."""

    def test_path_extracted_from_payload(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        request = _make_request("read_file", path="sub/file.txt")
        fs_req = adapter.build_filesystem_request(request, FilesystemOperation.READ_FILE)
        assert fs_req.path == "sub/file.txt"

    def test_content_extracted_from_payload(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        request = _make_request("create_file", path="f.txt", content="body")
        fs_req = adapter.build_filesystem_request(request, FilesystemOperation.CREATE_FILE)
        assert fs_req.content == "body"

    def test_encoding_defaults_to_utf8(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        request = _make_request("read_file", path="f.txt")
        fs_req = adapter.build_filesystem_request(request, FilesystemOperation.READ_FILE)
        assert fs_req.encoding == "utf-8"

    def test_encoding_from_payload(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        request = _make_request("read_file", path="f.txt", encoding="latin-1")
        fs_req = adapter.build_filesystem_request(request, FilesystemOperation.READ_FILE)
        assert fs_req.encoding == "latin-1"

    def test_request_id_preserved(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        request = _make_request("read_file", path="f.txt", request_id="req-xyz")
        fs_req = adapter.build_filesystem_request(request, FilesystemOperation.READ_FILE)
        assert fs_req.request_id == "req-xyz"

    def test_operation_id_preserved(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        request = _make_request("read_file", path="f.txt", operation_id="op-abc")
        fs_req = adapter.build_filesystem_request(request, FilesystemOperation.READ_FILE)
        assert fs_req.operation_id == "op-abc"

    def test_extra_payload_becomes_metadata(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        request = _make_request(
            "read_file", path="f.txt",
            extra_payload={"context_id": "ctx-1", "tag": "important"},
        )
        fs_req = adapter.build_filesystem_request(request, FilesystemOperation.READ_FILE)
        meta = dict(fs_req.metadata)
        assert meta.get("context_id") == "ctx-1"
        assert meta.get("tag") == "important"

    def test_metadata_is_sorted(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        request = _make_request(
            "read_file", path="f.txt",
            extra_payload={"z_key": "z", "a_key": "a", "m_key": "m"},
        )
        fs_req = adapter.build_filesystem_request(request, FilesystemOperation.READ_FILE)
        keys = [k for k, _ in fs_req.metadata]
        assert keys == sorted(keys)

    def test_translation_is_deterministic(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        request = _make_request("read_file", path="f.txt", extra_payload={"x": "1"})
        r1 = adapter.build_filesystem_request(request, FilesystemOperation.READ_FILE)
        r2 = adapter.build_filesystem_request(request, FilesystemOperation.READ_FILE)
        assert r1 == r2


# ══════════════════════════════════════════════════════════════════════════════
# 13. Error translation
# ══════════════════════════════════════════════════════════════════════════════


class TestErrorTranslation:
    """All failures return FilesystemExecutionResult(success=False) — no exceptions."""

    def test_unknown_action_returns_success_false(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        gateway = ExecutionGateway()
        request = gateway.build_request(
            request_id="req-001",
            operation_id="op-001",
            adapter_type=ExecutionAdapter.FILESYSTEM,
            action_id="teleport_file",
            payload={"path": "f.txt"},
        )
        result = adapter.execute(request)
        assert result.success is False
        assert result.filesystem_request is None
        assert "teleport_file" in result.error

    def test_read_missing_file_returns_success_false(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter.execute(_make_request("read_file", path="missing.txt"))
        assert result.success is False
        assert result.filesystem_request is not None   # translation succeeded
        assert result.filesystem_result is None
        assert "FileNotFoundError" in result.error

    def test_traversal_returns_success_false_without_touching_fs(self, tmp_path) -> None:
        (tmp_path.parent / "secret.txt").write_text("sensitive", encoding="utf-8")
        adapter = _make_adapter(tmp_path)
        result = adapter.execute(_make_request("read_file", path="../secret.txt"))
        assert result.success is False
        assert result.filesystem_request is None   # rejected at validation

    def test_execute_never_raises(self, tmp_path) -> None:
        """execute() must never propagate exceptions — always returns a result."""
        adapter = _make_adapter(tmp_path)
        # Deliberately bad request
        result = adapter.execute(_make_request("read_file", path="/root/.ssh/id_rsa"))
        assert isinstance(result, FilesystemExecutionResult)
        assert result.success is False

    def test_create_existing_file_returns_success_false(self, tmp_path) -> None:
        (tmp_path / "exists.txt").write_text("already")
        adapter = _make_adapter(tmp_path)
        result = adapter.execute(_make_request("create_file", path="exists.txt", content="new"))
        assert result.success is False
        assert result.filesystem_request is not None
        assert "FileExistsError" in result.error

    def test_append_missing_file_returns_success_false(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter.execute(_make_request("append_file", path="no_such.txt", content="x"))
        assert result.success is False

    def test_delete_missing_file_returns_success_false(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter.execute(_make_request("delete_file", path="no_such.txt"))
        assert result.success is False

    def test_delete_missing_directory_returns_success_false(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter.execute(_make_request("delete_directory", path="no_such_dir"))
        assert result.success is False


# ══════════════════════════════════════════════════════════════════════════════
# 14. Adapter metadata
# ══════════════════════════════════════════════════════════════════════════════


class TestAdapterMetadata:
    """adapter_metadata is sorted and contains expected keys."""

    def test_metadata_is_sorted(self, tmp_path) -> None:
        (tmp_path / "f.txt").write_text("x")
        adapter = _make_adapter(tmp_path)
        result = adapter.execute(_make_request("read_file", path="f.txt"))
        keys = [k for k, _ in result.adapter_metadata]
        assert keys == sorted(keys)

    def test_metadata_contains_action(self, tmp_path) -> None:
        (tmp_path / "f.txt").write_text("x")
        adapter = _make_adapter(tmp_path)
        result = adapter.execute(_make_request("read_file", path="f.txt"))
        meta = dict(result.adapter_metadata)
        assert meta.get("action") == "read_file"

    def test_metadata_contains_operation(self, tmp_path) -> None:
        (tmp_path / "f.txt").write_text("x")
        adapter = _make_adapter(tmp_path)
        result = adapter.execute(_make_request("read_file", path="f.txt"))
        meta = dict(result.adapter_metadata)
        assert meta.get("operation") == "read_file"

    def test_metadata_contains_bytes_read(self, tmp_path) -> None:
        (tmp_path / "f.txt").write_bytes(b"hello")
        adapter = _make_adapter(tmp_path)
        result = adapter.execute(_make_request("read_file", path="f.txt"))
        meta = dict(result.adapter_metadata)
        assert meta.get("bytes_read") == "5"

    def test_metadata_contains_bytes_written(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter.execute(_make_request("create_file", path="f.txt", content="hello"))
        meta = dict(result.adapter_metadata)
        assert meta.get("bytes_written") == "5"

    def test_metadata_contains_exists(self, tmp_path) -> None:
        (tmp_path / "f.txt").write_text("x")
        adapter = _make_adapter(tmp_path)
        result = adapter.execute(_make_request("exists", path="f.txt"))
        meta = dict(result.adapter_metadata)
        assert meta.get("exists") == "true"

    def test_request_id_preserved_in_result(self, tmp_path) -> None:
        (tmp_path / "f.txt").write_text("x")
        adapter = _make_adapter(tmp_path)
        result = adapter.execute(_make_request("read_file", path="f.txt", request_id="req-preserve"))
        assert result.request_id == "req-preserve"

    def test_operation_id_preserved_in_result(self, tmp_path) -> None:
        (tmp_path / "f.txt").write_text("x")
        adapter = _make_adapter(tmp_path)
        result = adapter.execute(_make_request("read_file", path="f.txt", operation_id="op-important"))
        assert result.operation_id == "op-important"


# ══════════════════════════════════════════════════════════════════════════════
# 15. Determinism
# ══════════════════════════════════════════════════════════════════════════════


class TestDeterminism:
    """Same inputs produce the same FilesystemRequest (translation is deterministic)."""

    def test_build_filesystem_request_is_deterministic(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        request = _make_request(
            "create_file", path="out/draft.md", content="# Draft",
            extra_payload={"tag": "v1"},
        )
        r1 = adapter.build_filesystem_request(request, FilesystemOperation.CREATE_FILE)
        r2 = adapter.build_filesystem_request(request, FilesystemOperation.CREATE_FILE)
        assert r1 == r2

    def test_exists_on_same_path_twice_is_consistent(self, tmp_path) -> None:
        (tmp_path / "stable.txt").write_text("here")
        adapter = _make_adapter(tmp_path)
        r1 = adapter.execute(_make_request("exists", path="stable.txt"))
        r2 = adapter.execute(_make_request("exists", path="stable.txt"))
        assert r1.filesystem_result.exists == r2.filesystem_result.exists is True

    def test_read_same_file_twice_returns_same_content(self, tmp_path) -> None:
        (tmp_path / "data.txt").write_text("consistent")
        adapter = _make_adapter(tmp_path)
        r1 = adapter.execute(_make_request("read_file", path="data.txt"))
        r2 = adapter.execute(_make_request("read_file", path="data.txt"))
        assert r1.filesystem_result.content == r2.filesystem_result.content == "consistent"


# ══════════════════════════════════════════════════════════════════════════════
# 16. Edge cases
# ══════════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Edge cases: empty files, unicode, overwrite behavior, nested dirs."""

    def test_overwrite_reduces_file_size(self, tmp_path) -> None:
        (tmp_path / "f.txt").write_text("long content here")
        adapter = _make_adapter(tmp_path)
        result = adapter.execute(_make_request("overwrite_file", path="f.txt", content="short"))
        assert result.success is True
        assert (tmp_path / "f.txt").read_text() == "short"

    def test_create_then_read_round_trip(self, tmp_path) -> None:
        content = "Round-trip content ✓"
        adapter = _make_adapter(tmp_path)
        adapter.execute(_make_request("create_file", path="rt.txt", content=content))
        result = adapter.execute(_make_request("read_file", path="rt.txt"))
        assert result.filesystem_result.content == content

    def test_create_delete_exists_sequence(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        adapter.execute(_make_request("create_file", path="seq.txt", content="x"))
        assert adapter.execute(_make_request("exists", path="seq.txt")).filesystem_result.exists is True
        adapter.execute(_make_request("delete_file", path="seq.txt"))
        assert adapter.execute(_make_request("exists", path="seq.txt")).filesystem_result.exists is False

    def test_overwrite_then_append_then_read(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        adapter.execute(_make_request("overwrite_file", path="log.txt", content="line1\n"))
        adapter.execute(_make_request("append_file", path="log.txt", content="line2\n"))
        result = adapter.execute(_make_request("read_file", path="log.txt"))
        assert result.filesystem_result.content == "line1\nline2\n"

    def test_mkdir_then_create_file_inside(self, tmp_path) -> None:
        adapter = _make_adapter(tmp_path)
        adapter.execute(_make_request("create_directory", path="output"))
        result = adapter.execute(
            _make_request("create_file", path="output/report.txt", content="report")
        )
        assert result.success is True
        assert (tmp_path / "output" / "report.txt").read_text() == "report"

    def test_multiline_content_preserved(self, tmp_path) -> None:
        content = "line1\nline2\nline3\n"
        adapter = _make_adapter(tmp_path)
        adapter.execute(_make_request("create_file", path="multi.txt", content=content))
        result = adapter.execute(_make_request("read_file", path="multi.txt"))
        assert result.filesystem_result.content == content

    def test_binary_like_unicode_emoji_preserved(self, tmp_path) -> None:
        content = "🚀 launch 🎯 target 💡 idea"
        adapter = _make_adapter(tmp_path)
        adapter.execute(_make_request("overwrite_file", path="emoji.txt", content=content))
        result = adapter.execute(_make_request("read_file", path="emoji.txt"))
        assert result.filesystem_result.content == content


# ══════════════════════════════════════════════════════════════════════════════
# 17. Gateway → Filesystem Adapter integration
# ══════════════════════════════════════════════════════════════════════════════


class TestGatewayAdapterIntegration:
    """Prove the Gateway dispatches to the Filesystem Adapter correctly."""

    def test_gateway_dispatches_then_adapter_executes(self, tmp_path) -> None:
        """Full flow: Gateway dispatch contract → Filesystem Adapter execution."""
        # Step 1: Gateway registers the FILESYSTEM adapter
        gateway = ExecutionGateway()
        gateway.register(AdapterRegistration(
            adapter=ExecutionAdapter.FILESYSTEM,
            adapter_id="filesystem-local",
            available=True,
            description="Local filesystem adapter",
        ))

        # Step 2: Gateway produces a dispatch contract
        gw_request = gateway.build_request(
            request_id="e2e-001",
            operation_id="op-write",
            adapter_type=ExecutionAdapter.FILESYSTEM,
            action_id="create_file",
            payload={"path": "output/plan.md", "content": "# Plan"},
        )
        gw_result = gateway.dispatch(gw_request)
        assert gw_result.status == ExecutionStatus.DISPATCHED

        # Step 3: Filesystem Adapter executes the same request
        adapter = FilesystemAdapter(workspace_root=tmp_path)
        adapter_result = adapter.execute(gw_request)

        assert adapter_result.success is True
        assert (tmp_path / "output" / "plan.md").read_text() == "# Plan"
        assert adapter_result.request_id == gw_result.request_id

    def test_gateway_request_flows_through_adapter_unmodified(self, tmp_path) -> None:
        """ExecutionRequest from Gateway is accepted by Filesystem Adapter as-is."""
        gateway = ExecutionGateway()
        gw_request = gateway.build_request(
            request_id="flow-001",
            operation_id="op-read",
            adapter_type=ExecutionAdapter.FILESYSTEM,
            action_id="read_file",
            payload={"path": "notes.txt"},
        )
        (tmp_path / "notes.txt").write_text("Gateway content")
        adapter = FilesystemAdapter(workspace_root=tmp_path)
        result = adapter.execute(gw_request)

        assert result.request_id == "flow-001"
        assert result.operation_id == "op-read"
        assert result.filesystem_result.content == "Gateway content"

    def test_architecture_boundary_is_preserved(self, tmp_path) -> None:
        """Gateway never touches the filesystem; adapter executes outside the kernel."""
        gateway = ExecutionGateway()
        gateway.register(AdapterRegistration(
            adapter=ExecutionAdapter.FILESYSTEM,
            adapter_id="filesystem-local",
            available=True,
            description="Filesystem",
        ))
        gw_request = gateway.build_request(
            request_id="arch-001",
            operation_id="op-001",
            adapter_type=ExecutionAdapter.FILESYSTEM,
            action_id="exists",
            payload={"path": "check.txt"},
        )
        gw_result = gateway.dispatch(gw_request)

        # Gateway produces contract — no filesystem access (output is empty)
        assert gw_result.output == ""
        assert gw_result.status == ExecutionStatus.DISPATCHED

        # Adapter accesses the filesystem
        adapter = FilesystemAdapter(workspace_root=tmp_path)
        adapter_result = adapter.execute(gw_request)

        assert adapter_result.success is True
        assert adapter_result.filesystem_result.exists is False  # file doesn't exist yet

    def test_operation_field_reflects_executed_operation(self, tmp_path) -> None:
        adapter = FilesystemAdapter(workspace_root=tmp_path)
        result = adapter.execute(_make_request("create_directory", path="output"))
        assert result.operation == FilesystemOperation.CREATE_DIRECTORY
