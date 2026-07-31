from hermes.kernel.file_content_reader import (
    MAX_CHARS_PER_FILE,
    MAX_FILES_IN_CONTEXT,
    MAX_TOTAL_CHARS,
    FileContentReader,
)
from hermes.models import FileContent, WorkspaceSnapshot
from hermes.models.workspace_file import WorkspaceFile


def _file(path: str, content: str, repository: str = "hermes-os") -> WorkspaceFile:
    return WorkspaceFile(
        path=path,
        extension=path.rsplit(".", 1)[-1] if "." in path else "",
        size=len(content),
        content=content,
        repository=repository,
    )


def _snapshot(*files: WorkspaceFile) -> WorkspaceSnapshot:
    return WorkspaceSnapshot(root="/tmp/avanzia", files=list(files))


def _reader() -> FileContentReader:
    return FileContentReader()


# --- Basic happy path ---

def test_read_returns_file_content_objects():
    snapshot = _snapshot(_file("src/service.py", "class HermesService: pass"))
    results = _reader().read(snapshot)

    assert len(results) == 1
    assert isinstance(results[0], FileContent)


def test_read_preserves_repository_and_path():
    snapshot = _snapshot(_file("src/service.py", "x = 1", repository="my-repo"))
    result = _reader().read(snapshot)[0]

    assert result.repository == "my-repo"
    assert result.path == "src/service.py"


def test_read_preserves_content():
    snapshot = _snapshot(_file("src/service.py", "def foo(): return 42"))
    result = _reader().read(snapshot)[0]

    assert result.content == "def foo(): return 42"


def test_read_skips_empty_files():
    snapshot = _snapshot(
        _file("empty.py", ""),
        _file("service.py", "x = 1"),
    )
    results = _reader().read(snapshot)

    assert len(results) == 1
    assert results[0].path == "service.py"


def test_read_skips_whitespace_only_files():
    snapshot = _snapshot(
        _file("blank.py", "   \n\t  "),
        _file("service.py", "x = 1"),
    )
    results = _reader().read(snapshot)

    assert len(results) == 1
    assert results[0].path == "service.py"


# --- Per-file character limit ---

def test_read_truncates_large_file():
    big_content = "x" * (MAX_CHARS_PER_FILE + 500)
    snapshot = _snapshot(_file("big.py", big_content))
    result = _reader().read(snapshot)[0]

    assert "[truncated]" in result.content
    assert len(result.content) <= MAX_CHARS_PER_FILE + len("\n... [truncated]")


def test_read_does_not_truncate_file_within_limit():
    content = "x" * (MAX_CHARS_PER_FILE - 1)
    snapshot = _snapshot(_file("ok.py", content))
    result = _reader().read(snapshot)[0]

    assert "[truncated]" not in result.content


# --- File count limit ---

def test_read_stops_at_max_files():
    files = [_file(f"file_{i}.py", f"content {i}") for i in range(MAX_FILES_IN_CONTEXT + 5)]
    snapshot = _snapshot(*files)
    results = _reader().read(snapshot)

    assert len(results) <= MAX_FILES_IN_CONTEXT


# --- Total character budget ---

def test_read_respects_total_char_budget():
    # Each file just under per-file limit; together they exceed the total budget.
    file_size = MAX_CHARS_PER_FILE
    num_files = (MAX_TOTAL_CHARS // file_size) + 5
    files = [_file(f"file_{i}.py", "y" * file_size) for i in range(num_files)]
    snapshot = _snapshot(*files)
    results = _reader().read(snapshot)

    total = sum(len(r.content) for r in results)
    assert total <= MAX_TOTAL_CHARS + len("\n... [truncated]")


# --- Language detection ---

def test_language_python():
    assert FileContentReader.language("src/service.py") == "python"


def test_language_typescript():
    assert FileContentReader.language("app/page.tsx") == "typescript"


def test_language_json():
    assert FileContentReader.language("package.json") == "json"


def test_language_unknown_extension_returns_empty_string():
    assert FileContentReader.language("file.xyz") == ""


def test_language_no_extension_returns_empty_string():
    assert FileContentReader.language("Makefile") == ""
