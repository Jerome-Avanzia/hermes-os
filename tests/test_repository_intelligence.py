"""Tests for the Repository Intelligence engine.

Coverage:
  - Typed contracts (frozen, slots, immutability)
  - RepositoryIntelligence initialisation and resolve_path
  - scan() on missing / empty / populated directories
  - Language detection (primary / secondary / minor confidence)
  - Build system detection (poetry, setuptools, npm, yarn, pnpm, cargo, go, maven, pip)
  - Entry point detection (Python, Rust, Node, Go)
  - Test location detection (directories, standalone files, framework inference)
  - Config file detection (env, docker, CI, git, package, test, lint)
  - Documentation detection (readme, changelog, license, contributing, api, directory)
  - Tree walk (ignored directories, size/extension accounting)
  - Snapshot determinism (same path → same snapshot_id)
  - Path safety (resolve_path escapes workspace_root → ValueError)
  - Snapshot immutability (frozen dataclass)
  - Empty-repository edge cases
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from hermes.kernel.repository_intelligence import (
    RepositoryIntelligence,
    _make_snapshot_id,
    _root_file_names,
)
from hermes.models.repository_intelligence import (
    BuildSystemDetection,
    ConfigFile,
    DocumentationFile,
    EntryPoint,
    LanguageDetection,
    RepositoryFile,
    RepositorySnapshot,
    TestLocation,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _write(path: Path, content: str = "") -> Path:
    """Create parent dirs and write content to path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")
    return path


# ── Contract tests ────────────────────────────────────────────────────────────


class TestContracts:
    """All model dataclasses must be frozen and use __slots__."""

    def test_language_detection_frozen(self):
        ld = LanguageDetection(language="python", file_count=5, confidence="primary", extensions=(".py",))
        with pytest.raises((AttributeError, TypeError)):
            ld.language = "rust"  # type: ignore[misc]

    def test_build_system_detection_frozen(self):
        bsd = BuildSystemDetection(name="poetry", config_file="pyproject.toml", build_command="poetry build", test_command="pytest")
        with pytest.raises((AttributeError, TypeError)):
            bsd.name = "cargo"  # type: ignore[misc]

    def test_entry_point_frozen(self):
        ep = EntryPoint(path="main.py", kind="main", language="python")
        with pytest.raises((AttributeError, TypeError)):
            ep.path = "other.py"  # type: ignore[misc]

    def test_test_location_frozen(self):
        tl = TestLocation(path="tests", kind="directory", framework="pytest", file_count=10)
        with pytest.raises((AttributeError, TypeError)):
            tl.file_count = 0  # type: ignore[misc]

    def test_config_file_frozen(self):
        cf = ConfigFile(path=".env", kind="environment")
        with pytest.raises((AttributeError, TypeError)):
            cf.kind = "docker"  # type: ignore[misc]

    def test_documentation_file_frozen(self):
        df = DocumentationFile(path="README.md", kind="readme")
        with pytest.raises((AttributeError, TypeError)):
            df.kind = "license"  # type: ignore[misc]

    def test_repository_file_frozen(self):
        rf = RepositoryFile(path="src/main.py", extension=".py", size_bytes=512, is_directory=False)
        with pytest.raises((AttributeError, TypeError)):
            rf.size_bytes = 0  # type: ignore[misc]

    def test_repository_snapshot_frozen(self):
        snap = RepositorySnapshot(
            snapshot_id="abc", repository_path=".", scanned_at="2026-01-01T00:00:00+00:00",
            languages=(), primary_language="", build_system=None,
            entry_points=(), test_locations=(), config_files=(), documentation=(),
            files=(), file_count=0, directory_count=0, total_size_bytes=0,
            git_present=False, metadata=(),
        )
        with pytest.raises((AttributeError, TypeError)):
            snap.primary_language = "python"  # type: ignore[misc]

    def test_language_detection_has_slots(self):
        ld = LanguageDetection(language="python", file_count=1, confidence="primary", extensions=(".py",))
        assert hasattr(type(ld), "__slots__")

    def test_repository_snapshot_has_slots(self):
        snap = RepositorySnapshot(
            snapshot_id="x", repository_path=".", scanned_at="2026-01-01T00:00:00+00:00",
            languages=(), primary_language="", build_system=None,
            entry_points=(), test_locations=(), config_files=(), documentation=(),
            files=(), file_count=0, directory_count=0, total_size_bytes=0,
            git_present=False, metadata=(),
        )
        assert hasattr(type(snap), "__slots__")

    def test_extensions_are_tuple(self):
        ld = LanguageDetection(language="ts", file_count=2, confidence="primary", extensions=(".ts", ".tsx"))
        assert isinstance(ld.extensions, tuple)

    def test_metadata_is_tuple_of_tuples(self):
        snap = RepositorySnapshot(
            snapshot_id="x", repository_path=".", scanned_at="2026-01-01T00:00:00+00:00",
            languages=(), primary_language="", build_system=None,
            entry_points=(), test_locations=(), config_files=(), documentation=(),
            files=(), file_count=0, directory_count=0, total_size_bytes=0,
            git_present=False, metadata=(("k", "v"),),
        )
        assert snap.metadata == (("k", "v"),)


# ── Initialisation tests ──────────────────────────────────────────────────────


class TestInit:
    def test_accepts_string_workspace_root(self, tmp_path):
        engine = RepositoryIntelligence(str(tmp_path))
        assert engine._workspace_root == tmp_path.resolve()

    def test_accepts_path_workspace_root(self, tmp_path):
        engine = RepositoryIntelligence(tmp_path)
        assert engine._workspace_root == tmp_path.resolve()

    def test_resolves_symlinks_in_workspace_root(self, tmp_path):
        target = tmp_path / "real"
        target.mkdir()
        link = tmp_path / "link"
        link.symlink_to(target)
        engine = RepositoryIntelligence(link)
        assert engine._workspace_root == target.resolve()


# ── resolve_path tests ────────────────────────────────────────────────────────


class TestResolvePath:
    def test_resolves_relative_path(self, tmp_path):
        engine = RepositoryIntelligence(tmp_path)
        result = engine.resolve_path("my-repo")
        assert result == (tmp_path / "my-repo").resolve()

    def test_resolves_nested_relative_path(self, tmp_path):
        engine = RepositoryIntelligence(tmp_path)
        result = engine.resolve_path("a/b/c")
        assert result == (tmp_path / "a" / "b" / "c").resolve()

    def test_resolves_dot_path(self, tmp_path):
        engine = RepositoryIntelligence(tmp_path)
        result = engine.resolve_path(".")
        assert result == tmp_path.resolve()

    def test_raises_on_path_traversal(self, tmp_path):
        engine = RepositoryIntelligence(tmp_path)
        with pytest.raises(ValueError, match="escapes workspace root"):
            engine.resolve_path("../outside")

    def test_raises_on_absolute_path_outside_workspace(self, tmp_path):
        engine = RepositoryIntelligence(tmp_path)
        with pytest.raises(ValueError, match="escapes workspace root"):
            engine.resolve_path("/etc/passwd")


# ── scan() basic tests ────────────────────────────────────────────────────────


class TestScanBasic:
    def test_returns_repository_snapshot(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert isinstance(snap, RepositorySnapshot)

    def test_missing_directory_returns_empty_snapshot(self, tmp_path):
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("nonexistent")
        assert snap.file_count == 0
        assert snap.primary_language == ""
        assert snap.build_system is None
        assert snap.git_present is False

    def test_snapshot_id_matches_hash(self, tmp_path):
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("my-repo")
        assert snap.snapshot_id == _make_snapshot_id("my-repo")

    def test_snapshot_id_override(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo", snapshot_id="custom-id")
        assert snap.snapshot_id == "custom-id"

    def test_scanned_at_override(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        engine = RepositoryIntelligence(tmp_path)
        ts = "2026-01-01T12:00:00+00:00"
        snap = engine.scan("repo", scanned_at=ts)
        assert snap.scanned_at == ts

    def test_repository_path_preserved(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert snap.repository_path == "repo"

    def test_git_present_true(self, tmp_path):
        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True)
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert snap.git_present is True

    def test_git_present_false(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert snap.git_present is False

    def test_empty_repo_zero_counts(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert snap.file_count == 0
        assert snap.directory_count == 0
        assert snap.total_size_bytes == 0

    def test_files_sorted_by_path(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "z.py")
        _write(repo / "a.py")
        _write(repo / "m.py")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        paths = [f.path for f in snap.files]
        assert paths == sorted(paths)

    def test_scanned_at_is_iso8601(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        # Must parse without errors and contain timezone info
        from datetime import datetime
        dt = datetime.fromisoformat(snap.scanned_at)
        assert dt.tzinfo is not None


# ── Tree walk tests ───────────────────────────────────────────────────────────


class TestTreeWalk:
    def test_counts_files(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "a.py")
        _write(repo / "b.py")
        _write(repo / "c.py")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert snap.file_count == 3

    def test_counts_directories(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "src" / "main.py")
        _write(repo / "tests" / "test_main.py")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert snap.directory_count == 2

    def test_sums_file_sizes(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "a.py", "x" * 100)
        _write(repo / "b.py", "y" * 200)
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert snap.total_size_bytes >= 300

    def test_skips_git_directory(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / ".git" / "config", "gitconfig")
        _write(repo / "main.py", "print('hello')")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert snap.file_count == 1
        assert all(".git" not in f.path for f in snap.files)

    def test_skips_node_modules(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "node_modules" / "lib" / "index.js", "module")
        _write(repo / "index.js", "app")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert snap.file_count == 1

    def test_skips_pycache(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "__pycache__" / "main.cpython-311.pyc", "bytecode")
        _write(repo / "main.py", "code")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert snap.file_count == 1

    def test_skips_venv(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / ".venv" / "lib" / "python.py", "venv")
        _write(repo / "app.py", "app")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert snap.file_count == 1

    def test_skips_dist(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "dist" / "bundle.js", "bundle")
        _write(repo / "src" / "app.js", "app")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert snap.file_count == 1

    def test_repository_file_extension_lowercase(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "Main.PY", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert snap.files[0].extension == ".py"

    def test_repository_file_no_extension(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "Makefile", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert snap.files[0].extension == ""

    def test_repository_file_is_directory_false_for_files(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "main.py", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert all(not f.is_directory for f in snap.files)

    def test_files_uses_forward_slash_separator(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "src" / "main.py", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert all("/" in f.path or "/" not in f.path for f in snap.files)
        # Specifically: no backslashes
        assert all("\\" not in f.path for f in snap.files)


# ── Language detection tests ──────────────────────────────────────────────────


class TestLanguageDetection:
    def test_detects_python(self, tmp_path):
        repo = tmp_path / "repo"
        for i in range(5):
            _write(repo / f"m{i}.py", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        langs = {l.language for l in snap.languages}
        assert "python" in langs

    def test_primary_language_is_most_common(self, tmp_path):
        repo = tmp_path / "repo"
        for i in range(10):
            _write(repo / f"m{i}.py", "")
        for i in range(3):
            _write(repo / f"m{i}.js", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert snap.primary_language == "python"

    def test_only_one_primary_language(self, tmp_path):
        repo = tmp_path / "repo"
        for i in range(5):
            _write(repo / f"p{i}.py", "")
        for i in range(5):
            _write(repo / f"j{i}.js", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        primaries = [l for l in snap.languages if l.confidence == "primary"]
        assert len(primaries) == 1

    def test_secondary_language_confidence(self, tmp_path):
        repo = tmp_path / "repo"
        # 10 Python, 3 TypeScript → TypeScript is 23% → secondary
        for i in range(10):
            _write(repo / f"p{i}.py", "")
        for i in range(3):
            _write(repo / f"t{i}.ts", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        ts = next((l for l in snap.languages if l.language == "typescript"), None)
        assert ts is not None
        assert ts.confidence == "secondary"

    def test_minor_language_confidence(self, tmp_path):
        repo = tmp_path / "repo"
        # 20 Python, 1 Rust → Rust is 5% → minor
        for i in range(20):
            _write(repo / f"p{i}.py", "")
        _write(repo / "main.rs", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        rust = next((l for l in snap.languages if l.language == "rust"), None)
        assert rust is not None
        assert rust.confidence == "minor"

    def test_languages_sorted_by_file_count_desc(self, tmp_path):
        repo = tmp_path / "repo"
        for i in range(5):
            _write(repo / f"p{i}.py", "")
        for i in range(3):
            _write(repo / f"j{i}.js", "")
        for i in range(1):
            _write(repo / f"r{i}.rs", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        counts = [l.file_count for l in snap.languages]
        assert counts == sorted(counts, reverse=True)

    def test_extensions_sorted_lexicographically(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "a.tsx", "")
        _write(repo / "b.ts", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        ts = next(l for l in snap.languages if l.language == "typescript")
        assert list(ts.extensions) == sorted(ts.extensions)

    def test_no_languages_for_non_source_files(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "README.md", "docs")
        _write(repo / "data.json", "{}")
        _write(repo / ".gitignore", "*.pyc")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert snap.primary_language == ""
        assert len(snap.languages) == 0

    def test_detects_typescript_separately_from_javascript(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "app.ts", "")
        _write(repo / "app.tsx", "")
        _write(repo / "util.js", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        langs = {l.language for l in snap.languages}
        assert "typescript" in langs
        assert "javascript" in langs

    def test_detects_rust(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "src" / "main.rs", "fn main() {}")
        _write(repo / "src" / "lib.rs", "pub fn foo() {}")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert snap.primary_language == "rust"

    def test_detects_go(self, tmp_path):
        repo = tmp_path / "repo"
        for i in range(4):
            _write(repo / f"pkg{i}.go", "package main")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert snap.primary_language == "go"

    def test_file_count_in_language_detection(self, tmp_path):
        repo = tmp_path / "repo"
        for i in range(7):
            _write(repo / f"f{i}.py", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        python = next(l for l in snap.languages if l.language == "python")
        assert python.file_count == 7


# ── Build system detection tests ──────────────────────────────────────────────


class TestBuildSystemDetection:
    def test_detects_poetry_from_pyproject(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "pyproject.toml", "[tool.poetry]\nname = 'myapp'")
        _write(repo / "main.py", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert snap.build_system is not None
        assert snap.build_system.name == "poetry"
        assert snap.build_system.config_file == "pyproject.toml"
        assert snap.build_system.build_command == "poetry build"
        assert snap.build_system.test_command == "poetry run pytest"

    def test_detects_setuptools_from_pyproject(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "pyproject.toml", "[build-system]\nrequires = ['setuptools']")
        _write(repo / "main.py", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert snap.build_system is not None
        assert snap.build_system.name == "setuptools"

    def test_detects_setuptools_from_setup_py(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "setup.py", "from setuptools import setup")
        _write(repo / "main.py", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert snap.build_system is not None
        assert snap.build_system.name == "setuptools"

    def test_detects_pip_from_requirements(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "requirements.txt", "flask\nrequests")
        _write(repo / "main.py", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert snap.build_system is not None
        assert snap.build_system.name == "pip"

    def test_detects_npm_from_package_json(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "package.json", '{"name":"app"}')
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert snap.build_system is not None
        assert snap.build_system.name == "npm"
        assert snap.build_system.config_file == "package.json"

    def test_detects_yarn_from_yarn_lock(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "package.json", '{"name":"app"}')
        _write(repo / "yarn.lock", "# yarn lockfile")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert snap.build_system is not None
        assert snap.build_system.name == "yarn"

    def test_detects_pnpm_from_pnpm_lock(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "package.json", '{"name":"app"}')
        _write(repo / "pnpm-lock.yaml", "lockfileVersion: 6.0")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert snap.build_system is not None
        assert snap.build_system.name == "pnpm"

    def test_detects_cargo(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "Cargo.toml", '[package]\nname = "myapp"')
        _write(repo / "src" / "main.rs", "fn main() {}")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert snap.build_system is not None
        assert snap.build_system.name == "cargo"
        assert snap.build_system.build_command == "cargo build"
        assert snap.build_system.test_command == "cargo test"

    def test_detects_go_modules(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "go.mod", "module myapp\ngo 1.21")
        _write(repo / "main.go", "package main")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert snap.build_system is not None
        assert snap.build_system.name == "go-modules"
        assert snap.build_system.test_command == "go test ./..."

    def test_detects_maven(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "pom.xml", "<project/>")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert snap.build_system is not None
        assert snap.build_system.name == "maven"

    def test_no_build_system_returns_none(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "main.py", "print('hello')")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert snap.build_system is None

    def test_npm_takes_priority_over_python(self, tmp_path):
        """package.json wins over Python files when both are present at root."""
        repo = tmp_path / "repo"
        _write(repo / "package.json", '{"name":"app"}')
        _write(repo / "main.py", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert snap.build_system.name == "npm"

    def test_build_system_is_frozen(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "Cargo.toml", '[package]\nname="x"')
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert snap.build_system is not None
        with pytest.raises((AttributeError, TypeError)):
            snap.build_system.name = "other"  # type: ignore[misc]


# ── Python test-command resolution tests ──────────────────────────────────────


class TestPythonTestCommandResolution:
    """Tests for _resolve_python_test_command() via BuildSystemDetection."""

    def test_uv_lock_emits_uv_run_pytest(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "pyproject.toml", "[build-system]\nrequires=['setuptools']")
        _write(repo / "uv.lock", "version = 1\n")
        _write(repo / "main.py", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert snap.build_system is not None
        assert snap.build_system.test_command == "uv run pytest"

    def test_poetry_pyproject_emits_poetry_run_pytest(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "pyproject.toml", "[tool.poetry]\nname = 'myapp'")
        _write(repo / "main.py", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert snap.build_system is not None
        assert snap.build_system.test_command == "poetry run pytest"

    def test_hatch_pyproject_emits_hatch_run_test(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "pyproject.toml", "[build-system]\n[tool.hatch.envs.default]\n")
        _write(repo / "main.py", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert snap.build_system is not None
        assert snap.build_system.test_command == "hatch run test"

    def test_tox_ini_emits_tox(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "pyproject.toml", "[build-system]\nrequires=['setuptools']")
        _write(repo / "tox.ini", "[tox]\nenvlist=py312")
        _write(repo / "main.py", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert snap.build_system is not None
        assert snap.build_system.test_command == "tox"

    def test_makefile_with_test_target_emits_make_test(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "pyproject.toml", "[build-system]\nrequires=['setuptools']")
        _write(repo / "Makefile", "test:\n\tpython -m pytest\n\nbuild:\n\tpython -m build\n")
        _write(repo / "main.py", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert snap.build_system is not None
        assert snap.build_system.test_command == "make test"

    def test_makefile_without_test_target_falls_through(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "pyproject.toml", "[build-system]\nrequires=['setuptools']")
        _write(repo / "Makefile", "build:\n\tpython -m build\n")
        _write(repo / "main.py", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert snap.build_system is not None
        assert snap.build_system.test_command == ""

    def test_venv_bin_pytest_emits_dotenv_path(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "pyproject.toml", "[build-system]\nrequires=['setuptools']")
        venv_pytest = repo / ".venv" / "bin" / "pytest"
        venv_pytest.parent.mkdir(parents=True, exist_ok=True)
        venv_pytest.write_text("#!/bin/sh\n")
        _write(repo / "main.py", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert snap.build_system is not None
        assert snap.build_system.test_command == ".venv/bin/pytest"

    def test_no_toolchain_returns_empty_string(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "pyproject.toml", "[build-system]\nrequires=['setuptools']")
        _write(repo / "main.py", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert snap.build_system is not None
        assert snap.build_system.test_command == ""

    def test_uv_lock_takes_priority_over_poetry(self, tmp_path):
        """uv.lock wins when both uv.lock and [tool.poetry] are present."""
        repo = tmp_path / "repo"
        _write(repo / "pyproject.toml", "[tool.poetry]\nname = 'myapp'")
        _write(repo / "uv.lock", "version = 1\n")
        _write(repo / "main.py", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert snap.build_system is not None
        assert snap.build_system.test_command == "uv run pytest"

    def test_uv_lock_takes_priority_over_tox(self, tmp_path):
        """uv.lock wins when both uv.lock and tox.ini are present."""
        repo = tmp_path / "repo"
        _write(repo / "pyproject.toml", "[build-system]\nrequires=['setuptools']")
        _write(repo / "uv.lock", "version = 1\n")
        _write(repo / "tox.ini", "[tox]\nenvlist=py312")
        _write(repo / "main.py", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert snap.build_system is not None
        assert snap.build_system.test_command == "uv run pytest"

    def test_tox_takes_priority_over_venv(self, tmp_path):
        """tox.ini wins over .venv/bin/pytest when both are present."""
        repo = tmp_path / "repo"
        _write(repo / "pyproject.toml", "[build-system]\nrequires=['setuptools']")
        _write(repo / "tox.ini", "[tox]\nenvlist=py312")
        venv_pytest = repo / ".venv" / "bin" / "pytest"
        venv_pytest.parent.mkdir(parents=True, exist_ok=True)
        venv_pytest.write_text("#!/bin/sh\n")
        _write(repo / "main.py", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert snap.build_system is not None
        assert snap.build_system.test_command == "tox"

    def test_setup_py_resolution(self, tmp_path):
        """setup.py project with uv.lock emits uv run pytest."""
        repo = tmp_path / "repo"
        _write(repo / "setup.py", "from setuptools import setup; setup()")
        _write(repo / "uv.lock", "version = 1\n")
        _write(repo / "main.py", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert snap.build_system is not None
        assert snap.build_system.test_command == "uv run pytest"

    def test_requirements_txt_no_toolchain_returns_empty(self, tmp_path):
        """requirements.txt project with no toolchain returns empty string."""
        repo = tmp_path / "repo"
        _write(repo / "requirements.txt", "flask\n")
        _write(repo / "main.py", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert snap.build_system is not None
        assert snap.build_system.test_command == ""


# ── Entry point detection tests ───────────────────────────────────────────────


class TestEntryPointDetection:
    def test_detects_main_py(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "main.py", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        paths = [e.path for e in snap.entry_points]
        assert "main.py" in paths

    def test_detects_dunder_main_py(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "__main__.py", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        paths = [e.path for e in snap.entry_points]
        assert "__main__.py" in paths

    def test_detects_cli_py(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "cli.py", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        ep = next(e for e in snap.entry_points if e.path == "cli.py")
        assert ep.kind == "cli"

    def test_detects_manage_py_as_cli(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "manage.py", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        ep = next(e for e in snap.entry_points if e.path == "manage.py")
        assert ep.kind == "cli"

    def test_detects_server_py_as_api(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "server.py", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        ep = next(e for e in snap.entry_points if e.path == "server.py")
        assert ep.kind == "api"

    def test_detects_entry_points_in_src_dir(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "src" / "main.py", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        paths = [e.path for e in snap.entry_points]
        assert "src/main.py" in paths

    def test_detects_rust_src_main(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "src" / "main.rs", "fn main() {}")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        paths = [e.path for e in snap.entry_points]
        assert "src/main.rs" in paths

    def test_detects_rust_bin_as_cli(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "src" / "bin" / "mycli.rs", "fn main() {}")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        ep = next((e for e in snap.entry_points if "mycli" in e.path), None)
        assert ep is not None
        assert ep.kind == "cli"
        assert ep.language == "rust"

    def test_entry_point_language_inferred_from_extension(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "main.py", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        ep = next(e for e in snap.entry_points if e.path == "main.py")
        assert ep.language == "python"

    def test_no_entry_points_for_library(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "src" / "lib.py", "")
        _write(repo / "src" / "utils.py", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        # lib.py and utils.py are not entry point names
        assert len(snap.entry_points) == 0

    def test_entry_points_sorted_by_path(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "server.py", "")
        _write(repo / "cli.py", "")
        _write(repo / "main.py", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        paths = [e.path for e in snap.entry_points]
        assert paths == sorted(paths)

    def test_no_duplicate_entry_points(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "main.py", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        paths = [e.path for e in snap.entry_points]
        assert len(paths) == len(set(paths))

    def test_detects_node_server_js(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "server.js", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        ep = next((e for e in snap.entry_points if e.path == "server.js"), None)
        assert ep is not None
        assert ep.kind == "api"


# ── Test location detection tests ─────────────────────────────────────────────


class TestTestLocationDetection:
    def test_detects_tests_directory(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "tests" / "test_main.py", "def test_foo(): pass")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        paths = [t.path for t in snap.test_locations]
        assert "tests" in paths

    def test_detects_test_directory(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "test" / "test_app.py", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        paths = [t.path for t in snap.test_locations]
        assert "test" in paths

    def test_detects_spec_directory(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "spec" / "app_spec.rb", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        paths = [t.path for t in snap.test_locations]
        assert "spec" in paths

    def test_test_directory_kind_is_directory(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "tests" / "test_main.py", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        tl = next(t for t in snap.test_locations if t.path == "tests")
        assert tl.kind == "directory"

    def test_test_directory_file_count(self, tmp_path):
        repo = tmp_path / "repo"
        for i in range(4):
            _write(repo / "tests" / f"test_{i}.py", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        tl = next(t for t in snap.test_locations if t.path == "tests")
        assert tl.file_count == 4

    def test_detects_pytest_framework(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "pytest.ini", "[pytest]")
        _write(repo / "tests" / "test_main.py", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        tl = next(t for t in snap.test_locations if t.path == "tests")
        assert tl.framework == "pytest"

    def test_detects_pytest_from_pyproject(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "pyproject.toml", "[tool.pytest.ini_options]\n")
        _write(repo / "tests" / "test_main.py", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        tl = next(t for t in snap.test_locations if t.path == "tests")
        assert tl.framework == "pytest"

    def test_detects_cargo_test_framework(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "Cargo.toml", '[package]\nname="x"')
        _write(repo / "tests" / "integration_test.rs", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        tl = next(t for t in snap.test_locations if t.path == "tests")
        assert tl.framework == "cargo-test"

    def test_detects_go_test_framework(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "go.mod", "module x")
        _write(repo / "tests" / "main_test.go", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        tl = next(t for t in snap.test_locations if t.path == "tests")
        assert tl.framework == "go-test"

    def test_detects_jest_framework(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "jest.config.js", "module.exports = {}")
        _write(repo / "__tests__" / "app.test.js", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        tl = next(t for t in snap.test_locations if "__tests__" in t.path)
        assert tl.framework == "jest"

    def test_standalone_test_file_detected(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "test_standalone.py", "def test_x(): pass")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        paths = [t.path for t in snap.test_locations]
        assert "test_standalone.py" in paths

    def test_standalone_test_file_kind_is_file(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "test_standalone.py", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        tl = next(t for t in snap.test_locations if t.path == "test_standalone.py")
        assert tl.kind == "file"
        assert tl.file_count == 1

    def test_test_locations_sorted_by_path(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "tests" / "test_a.py", "")
        _write(repo / "test_z.py", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        paths = [t.path for t in snap.test_locations]
        assert paths == sorted(paths)

    def test_files_inside_test_dir_not_double_counted(self, tmp_path):
        """Test files inside tests/ dir should not also appear as standalone locations."""
        repo = tmp_path / "repo"
        _write(repo / "tests" / "test_main.py", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        # Should have exactly one test location: the tests/ directory
        assert len(snap.test_locations) == 1
        assert snap.test_locations[0].path == "tests"


# ── Config file detection tests ───────────────────────────────────────────────


class TestConfigFileDetection:
    def test_detects_dotenv(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / ".env", "SECRET=x")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        cf = next((c for c in snap.config_files if c.path == ".env"), None)
        assert cf is not None
        assert cf.kind == "environment"

    def test_detects_dotenv_variant(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / ".env.production", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        cf = next((c for c in snap.config_files if ".env" in c.path), None)
        assert cf is not None
        assert cf.kind == "environment"

    def test_detects_dockerfile(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "Dockerfile", "FROM python:3.11")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        cf = next((c for c in snap.config_files if "Dockerfile" in c.path), None)
        assert cf is not None
        assert cf.kind == "docker"

    def test_detects_docker_compose(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "docker-compose.yml", "version: '3'")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        cf = next((c for c in snap.config_files if "docker-compose" in c.path), None)
        assert cf is not None
        assert cf.kind == "docker"

    def test_detects_github_workflows(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / ".github" / "workflows" / "ci.yml", "on: push")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        ci_files = [c for c in snap.config_files if c.kind == "ci"]
        assert len(ci_files) >= 1

    def test_detects_gitignore(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / ".gitignore", "*.pyc")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        cf = next((c for c in snap.config_files if ".gitignore" in c.path), None)
        assert cf is not None
        assert cf.kind == "git"

    def test_detects_package_json(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "package.json", '{}')
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        cf = next((c for c in snap.config_files if "package.json" in c.path), None)
        assert cf is not None
        assert cf.kind == "package"

    def test_detects_pytest_ini(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "pytest.ini", "[pytest]")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        cf = next((c for c in snap.config_files if "pytest.ini" in c.path), None)
        assert cf is not None
        assert cf.kind == "test"

    def test_detects_mypy_ini(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "mypy.ini", "[mypy]")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        cf = next((c for c in snap.config_files if "mypy.ini" in c.path), None)
        assert cf is not None
        assert cf.kind == "lint"

    def test_detects_ruff_toml(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / ".ruff.toml", "[lint]")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        cf = next((c for c in snap.config_files if "ruff" in c.path), None)
        assert cf is not None
        assert cf.kind == "lint"

    def test_config_files_sorted_by_path(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / ".gitignore", "")
        _write(repo / "Dockerfile", "")
        _write(repo / ".env", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        paths = [c.path for c in snap.config_files]
        assert paths == sorted(paths)

    def test_no_duplicate_config_files(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "pyproject.toml", "[tool.poetry]")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        paths = [c.path for c in snap.config_files]
        assert len(paths) == len(set(paths))


# ── Documentation detection tests ────────────────────────────────────────────


class TestDocumentationDetection:
    def test_detects_readme_md(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "README.md", "# My Project")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        doc = next((d for d in snap.documentation if "README" in d.path), None)
        assert doc is not None
        assert doc.kind == "readme"

    def test_detects_readme_rst(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "README.rst", "My Project")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        doc = next((d for d in snap.documentation if "README" in d.path), None)
        assert doc is not None
        assert doc.kind == "readme"

    def test_detects_changelog(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "CHANGELOG.md", "# Changelog")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        doc = next((d for d in snap.documentation if "CHANGELOG" in d.path), None)
        assert doc is not None
        assert doc.kind == "changelog"

    def test_detects_license(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "LICENSE", "MIT License")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        doc = next((d for d in snap.documentation if "LICENSE" in d.path), None)
        assert doc is not None
        assert doc.kind == "license"

    def test_detects_license_md(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "LICENSE.md", "MIT")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        doc = next((d for d in snap.documentation if "LICENSE" in d.path), None)
        assert doc is not None
        assert doc.kind == "license"

    def test_detects_contributing(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "CONTRIBUTING.md", "# Contributing")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        doc = next((d for d in snap.documentation if "CONTRIBUTING" in d.path), None)
        assert doc is not None
        assert doc.kind == "contributing"

    def test_detects_openapi_yaml(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "openapi.yaml", "openapi: 3.0.0")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        doc = next((d for d in snap.documentation if "openapi" in d.path), None)
        assert doc is not None
        assert doc.kind == "api"

    def test_detects_docs_directory(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "docs" / "index.md", "# Docs")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        doc = next((d for d in snap.documentation if d.path == "docs"), None)
        assert doc is not None
        assert doc.kind == "directory"

    def test_documentation_sorted_by_path(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "README.md", "")
        _write(repo / "LICENSE", "")
        _write(repo / "CHANGELOG.md", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        paths = [d.path for d in snap.documentation]
        assert paths == sorted(paths)

    def test_no_duplicate_documentation(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "README.md", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        paths = [d.path for d in snap.documentation]
        assert len(paths) == len(set(paths))


# ── Snapshot determinism tests ────────────────────────────────────────────────


class TestDeterminism:
    def test_same_path_same_snapshot_id(self, tmp_path):
        engine = RepositoryIntelligence(tmp_path)
        id1 = _make_snapshot_id("my-repo")
        id2 = _make_snapshot_id("my-repo")
        assert id1 == id2

    def test_different_path_different_snapshot_id(self, tmp_path):
        id1 = _make_snapshot_id("repo-a")
        id2 = _make_snapshot_id("repo-b")
        assert id1 != id2

    def test_snapshot_id_is_16_chars(self):
        sid = _make_snapshot_id("any-path")
        assert len(sid) == 16

    def test_snapshot_id_is_hex(self):
        sid = _make_snapshot_id("any-path")
        int(sid, 16)  # raises ValueError if not valid hex

    def test_scan_twice_same_snapshot_id(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        engine = RepositoryIntelligence(tmp_path)
        snap1 = engine.scan("repo", scanned_at="2026-01-01T00:00:00+00:00")
        snap2 = engine.scan("repo", scanned_at="2026-01-01T00:00:00+00:00")
        assert snap1.snapshot_id == snap2.snapshot_id

    def test_scan_twice_same_file_list(self, tmp_path):
        repo = tmp_path / "repo"
        _write(repo / "main.py", "")
        _write(repo / "README.md", "")
        engine = RepositoryIntelligence(tmp_path)
        snap1 = engine.scan("repo", scanned_at="2026-01-01T00:00:00+00:00")
        snap2 = engine.scan("repo", scanned_at="2026-01-01T00:00:00+00:00")
        assert snap1.files == snap2.files


# ── Full integration tests ────────────────────────────────────────────────────


class TestFullIntegration:
    def test_python_project_full_scan(self, tmp_path):
        """Simulate a typical Python project and verify complete snapshot."""
        repo = tmp_path / "myapp"
        _write(repo / "pyproject.toml", "[tool.poetry]\nname='myapp'\n[build-system]")
        _write(repo / "README.md", "# MyApp")
        _write(repo / "LICENSE", "MIT")
        _write(repo / ".gitignore", "*.pyc")
        _write(repo / ".env.example", "SECRET=")
        _write(repo / "src" / "main.py", "def main(): pass")
        _write(repo / "src" / "api.py", "from flask import Flask")
        _write(repo / "src" / "utils.py", "")
        _write(repo / "tests" / "test_main.py", "def test_main(): pass")
        _write(repo / "tests" / "test_api.py", "def test_api(): pass")
        _write(repo / ".github" / "workflows" / "ci.yml", "on: push")
        _write(repo / ".git" / "config", "git")

        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("myapp")

        assert snap.primary_language == "python"
        assert snap.build_system is not None
        assert snap.build_system.name == "poetry"
        assert snap.git_present is True

        doc_kinds = {d.kind for d in snap.documentation}
        assert "readme" in doc_kinds
        assert "license" in doc_kinds

        config_kinds = {c.kind for c in snap.config_files}
        assert "git" in config_kinds
        assert "environment" in config_kinds
        assert "ci" in config_kinds

        test_paths = [t.path for t in snap.test_locations]
        assert "tests" in test_paths

        entry_paths = [e.path for e in snap.entry_points]
        assert any("main.py" in p for p in entry_paths)
        assert any("api.py" in p for p in entry_paths)

    def test_rust_project_full_scan(self, tmp_path):
        """Simulate a Rust project."""
        repo = tmp_path / "rustapp"
        _write(repo / "Cargo.toml", '[package]\nname="rustapp"\nversion="0.1.0"')
        _write(repo / "README.md", "")
        _write(repo / "src" / "main.rs", "fn main() {}")
        _write(repo / "src" / "lib.rs", "pub fn foo() {}")
        _write(repo / "src" / "bin" / "cli.rs", "fn main() {}")
        _write(repo / "tests" / "integration_test.rs", "")

        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("rustapp")

        assert snap.primary_language == "rust"
        assert snap.build_system.name == "cargo"
        entry_paths = [e.path for e in snap.entry_points]
        assert any("main.rs" in p for p in entry_paths)
        assert any("cli.rs" in p for p in entry_paths)

        tl = next(t for t in snap.test_locations if "tests" in t.path)
        assert tl.framework == "cargo-test"

    def test_node_project_full_scan(self, tmp_path):
        """Simulate a Node.js project with yarn."""
        repo = tmp_path / "nodeapp"
        _write(repo / "package.json", '{"name":"nodeapp","scripts":{"build":"tsc","test":"jest"}}')
        _write(repo / "yarn.lock", "# yarn lockfile")
        _write(repo / "jest.config.js", "module.exports = {}")
        _write(repo / "src" / "index.ts", "")
        _write(repo / "src" / "server.ts", "")
        _write(repo / "__tests__" / "app.test.ts", "")

        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("nodeapp")

        assert snap.primary_language == "typescript"
        assert snap.build_system.name == "yarn"
        tl = next(t for t in snap.test_locations if "__tests__" in t.path)
        assert tl.framework == "jest"

    def test_snapshot_all_tuples(self, tmp_path):
        """All collection fields in RepositorySnapshot must be tuples."""
        repo = tmp_path / "repo"
        _write(repo / "main.py", "")
        engine = RepositoryIntelligence(tmp_path)
        snap = engine.scan("repo")
        assert isinstance(snap.languages, tuple)
        assert isinstance(snap.entry_points, tuple)
        assert isinstance(snap.test_locations, tuple)
        assert isinstance(snap.config_files, tuple)
        assert isinstance(snap.documentation, tuple)
        assert isinstance(snap.files, tuple)
        assert isinstance(snap.metadata, tuple)


# ── Module-level helper tests ─────────────────────────────────────────────────


class TestHelpers:
    def test_make_snapshot_id_length(self):
        assert len(_make_snapshot_id("x")) == 16

    def test_make_snapshot_id_deterministic(self):
        assert _make_snapshot_id("abc") == _make_snapshot_id("abc")

    def test_make_snapshot_id_different_inputs(self):
        assert _make_snapshot_id("a") != _make_snapshot_id("b")

    def test_root_file_names_returns_files_only(self, tmp_path):
        (tmp_path / "file.txt").write_text("x")
        (tmp_path / "subdir").mkdir()
        names = _root_file_names(tmp_path)
        assert "file.txt" in names
        assert "subdir" not in names

    def test_root_file_names_missing_dir(self, tmp_path):
        names = _root_file_names(tmp_path / "nonexistent")
        assert names == set()
