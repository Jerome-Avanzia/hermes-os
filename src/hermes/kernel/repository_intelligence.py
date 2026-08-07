"""Repository Intelligence — deterministic engine for scanning repository structure.

Architecture position:
  Deterministic Kernel → RepositoryIntelligence → RepositorySnapshot

The RepositoryIntelligence engine reads the filesystem, detects structural
properties, and returns an immutable RepositorySnapshot. It:

  * makes no LLM calls
  * writes nothing to the filesystem
  * stays within the workspace boundary (workspace_root)
  * never raises — errors produce a best-effort snapshot with available data

Usage:
  engine = RepositoryIntelligence(workspace_root="/workspace")
  snapshot = engine.scan("my-repo")
"""

from __future__ import annotations

import hashlib
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

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


# ── Directory names to skip during tree walk ──────────────────────────────────

_IGNORE_DIRS: frozenset[str] = frozenset({
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    ".next",
    "coverage",
    "target",
    ".idea",
    ".vscode",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "htmlcov",
    ".tox",
    ".eggs",
})


# ── File extension → canonical language name ──────────────────────────────────

_EXTENSION_TO_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".pyw": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".scala": "scala",
    ".rb": "ruby",
    ".erb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hh": "cpp",
    ".hpp": "cpp",
    ".hxx": "cpp",
    ".swift": "swift",
    ".r": "r",
    ".R": "r",
    ".lua": "lua",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".fish": "shell",
    ".ps1": "powershell",
    ".ex": "elixir",
    ".exs": "elixir",
    ".erl": "erlang",
    ".hrl": "erlang",
    ".hs": "haskell",
    ".lhs": "haskell",
    ".ml": "ocaml",
    ".mli": "ocaml",
    ".dart": "dart",
    ".nim": "nim",
    ".zig": "zig",
    ".vue": "vue",
    ".svelte": "svelte",
}

# Extensions that count as "source files" for language detection
# (excludes config, data, docs formats)
_SOURCE_EXTENSIONS: frozenset[str] = frozenset(_EXTENSION_TO_LANGUAGE.keys())


# ── Build system indicators (ordered by priority) ────────────────────────────

# Each entry: (indicator_file, name, build_command, test_command)
_BUILD_SYSTEM_INDICATORS: list[tuple[str, str, str, str]] = [
    ("Cargo.toml",      "cargo",       "cargo build",        "cargo test"),
    ("go.mod",          "go-modules",  "go build ./...",     "go test ./..."),
    ("pom.xml",         "maven",       "mvn package",        "mvn test"),
    ("build.gradle",    "gradle",      "./gradlew build",    "./gradlew test"),
    ("build.gradle.kts","gradle",      "./gradlew build",    "./gradlew test"),
    ("mix.exs",         "mix",         "mix compile",        "mix test"),
    ("Gemfile",         "bundler",     "bundle exec rake",   "bundle exec rspec"),
    ("CMakeLists.txt",  "cmake",       "cmake --build .",    "ctest"),
    ("Makefile",        "make",        "make",               "make test"),
]

# Python-specific: checked after file scan (pyproject.toml can be poetry/setuptools)
# test_command column is unused — _resolve_python_test_command() is called instead.
_PYTHON_BUILD_INDICATORS: list[tuple[str, str, str, str]] = [
    ("pyproject.toml",  "poetry",      "poetry build",       ""),
    ("setup.py",        "setuptools",  "python setup.py build", ""),
    ("setup.cfg",       "setuptools",  "python setup.py build", ""),
    ("requirements.txt","pip",         "pip install -r requirements.txt", ""),
]

def _resolve_python_test_command(root: Path, root_files: set[str]) -> str:
    """Return the repository-specific test command for a Python project.

    Priority (highest to lowest):
      1. uv.lock present                          → 'uv run pytest'
      2. [tool.poetry] in pyproject.toml          → 'poetry run pytest'
      3. [tool.hatch] in pyproject.toml           → 'hatch run test'
      4. tox.ini present                          → 'tox'
      5. Makefile present with a 'test:' target   → 'make test'
      6. .venv/bin/pytest exists at repo root     → '.venv/bin/pytest'
      7. no supported test command                → ''

    Declared toolchain (1–5) takes precedence over local executable
    discovery (6). An empty string triggers the skip path in the
    EngineeringWorkflow (executed=False, success=True).
    """
    root_lower = {f.lower() for f in root_files}

    # 1. uv — lock file signals uv-managed project
    if "uv.lock" in root_lower:
        return "uv run pytest"

    # 2 & 3. pyproject.toml content — poetry or hatch
    if "pyproject.toml" in root_lower:
        pyproject = root / "pyproject.toml"
        try:
            text = pyproject.read_text(encoding="utf-8", errors="ignore")
            if "[tool.poetry]" in text:
                return "poetry run pytest"
            if "[tool.hatch]" in text or "[tool.hatch." in text:
                return "hatch run test"
        except OSError:
            pass

    # 4. tox
    if "tox.ini" in root_lower:
        return "tox"

    # 5. Makefile with a test target (target lines start at column 0)
    if "makefile" in root_lower:
        makefile_name = next(
            (f for f in root_files if f.lower() == "makefile"), "Makefile"
        )
        try:
            for line in (root / makefile_name).read_text(
                encoding="utf-8", errors="ignore"
            ).splitlines():
                if line.startswith("test:") or line.startswith("test :"):
                    return "make test"
        except OSError:
            pass

    # 6. Local venv — repository carries its own test runner
    if (root / ".venv" / "bin" / "pytest").is_file():
        return ".venv/bin/pytest"

    # 7. No supported test command — triggers skip path
    return ""


# Node-specific: npm vs yarn vs pnpm
_NODE_LOCK_FILES: dict[str, str] = {
    "pnpm-lock.yaml": "pnpm",
    "yarn.lock":      "yarn",
    "package-lock.json": "npm",
}


# ── Config file patterns ──────────────────────────────────────────────────────

# Mapping: filename_lower → kind
_CONFIG_FILE_NAMES: dict[str, str] = {
    # environment
    ".env":                 "environment",
    ".env.local":           "environment",
    ".env.example":         "environment",
    ".env.sample":          "environment",
    ".env.test":            "environment",
    ".env.production":      "environment",
    ".env.development":     "environment",
    # docker
    "dockerfile":           "docker",
    "docker-compose.yml":   "docker",
    "docker-compose.yaml":  "docker",
    "docker-compose.dev.yml":  "docker",
    "docker-compose.prod.yml": "docker",
    # ci
    ".gitlab-ci.yml":       "ci",
    "jenkinsfile":          "ci",
    ".travis.yml":          "ci",
    "circle.yml":           "ci",
    # git
    ".gitignore":           "git",
    ".gitattributes":       "git",
    ".gitmodules":          "git",
    # editor / lint
    ".editorconfig":        "editor",
    ".prettierrc":          "editor",
    ".prettierrc.json":     "editor",
    ".prettierrc.js":       "editor",
    ".eslintrc":            "editor",
    ".eslintrc.json":       "editor",
    ".eslintrc.js":         "editor",
    ".eslintrc.yml":        "editor",
    ".eslintrc.yaml":       "editor",
    ".eslintignore":        "editor",
    # package
    "pyproject.toml":       "package",
    "setup.py":             "package",
    "setup.cfg":            "package",
    "requirements.txt":     "package",
    "requirements-dev.txt": "package",
    "requirements-test.txt":"package",
    "package.json":         "package",
    "package-lock.json":    "package",
    "yarn.lock":            "package",
    "pnpm-lock.yaml":       "package",
    "cargo.toml":           "package",
    "cargo.lock":           "package",
    "gemfile":              "package",
    "gemfile.lock":         "package",
    "go.mod":               "package",
    "go.sum":               "package",
    "pom.xml":              "package",
    "build.gradle":         "package",
    "build.gradle.kts":     "package",
    "mix.exs":              "package",
    "mix.lock":             "package",
    # test
    "pytest.ini":           "test",
    "conftest.py":          "test",
    ".pytest.ini":          "test",
    "jest.config.js":       "test",
    "jest.config.ts":       "test",
    "jest.config.mjs":      "test",
    "vitest.config.js":     "test",
    "vitest.config.ts":     "test",
    "vitest.config.mts":    "test",
    "mocha.opts":           "test",
    ".mocharc.yml":         "test",
    ".mocharc.json":        "test",
    # lint
    "mypy.ini":             "lint",
    ".mypy.ini":            "lint",
    ".ruff.toml":           "lint",
    "ruff.toml":            "lint",
    ".flake8":              "lint",
    ".pylintrc":            "lint",
    "tox.ini":              "lint",
    ".rubocop.yml":         "lint",
    # ci (prefix-matched separately)
    ".circleci":            "ci",
    "appveyor.yml":         "ci",
    "azure-pipelines.yml":  "ci",
}

# Directory names that count as CI config directories
_CI_DIRECTORIES: frozenset[str] = frozenset({".github", ".circleci", ".buildkite"})


# ── Documentation patterns ────────────────────────────────────────────────────

def _doc_kind(name_lower: str) -> str | None:
    """Return documentation kind for a filename, or None if not documentation."""
    if name_lower.startswith("readme"):
        return "readme"
    if name_lower.startswith("changelog") or name_lower.startswith("changes") or name_lower.startswith("history"):
        return "changelog"
    if name_lower.startswith("license") or name_lower.startswith("licence"):
        return "license"
    if name_lower.startswith("contributing"):
        return "contributing"
    if name_lower in ("api.md", "openapi.yaml", "openapi.yml", "swagger.yaml", "swagger.yml"):
        return "api"
    return None


_DOC_DIRECTORIES: frozenset[str] = frozenset({"docs", "documentation", "doc"})


# ── Test directory / file name patterns ───────────────────────────────────────

_TEST_DIRECTORY_NAMES: frozenset[str] = frozenset({
    "tests", "test", "spec", "__tests__", "e2e", "integration", "unit",
})


def _is_test_file(filename: str) -> bool:
    name = filename.lower()
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or name.endswith(".test.js")
        or name.endswith(".test.ts")
        or name.endswith(".test.jsx")
        or name.endswith(".test.tsx")
        or name.endswith(".spec.js")
        or name.endswith(".spec.ts")
        or name.endswith(".spec.jsx")
        or name.endswith(".spec.tsx")
        or name.endswith("_spec.rb")
        or name.endswith("_test.go")
        or name.endswith("_test.rs")
    )


def _detect_test_framework(
    test_dir: Path,
    root: Path,
    root_files: set[str],
) -> str:
    """Infer test framework from config files and directory content."""
    root_lower = {f.lower() for f in root_files}

    # Rust
    if "cargo.toml" in root_lower:
        return "cargo-test"
    # Go
    if "go.mod" in root_lower:
        return "go-test"
    # pytest
    if (
        "pytest.ini" in root_lower
        or ".pytest.ini" in root_lower
        or "conftest.py" in root_lower
    ):
        return "pytest"
    # Check pyproject.toml for pytest config
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            text = pyproject.read_text(encoding="utf-8", errors="ignore")
            if "[tool.pytest" in text:
                return "pytest"
            if "[tool.poetry" in text or "[build-system]" in text:
                # Python project — default to pytest
                return "pytest"
        except OSError:
            pass
    # jest / vitest
    for name in root_lower:
        if name.startswith("jest.config"):
            return "jest"
        if name.startswith("vitest.config"):
            return "vitest"
    # mocha
    if ".mocharc.yml" in root_lower or ".mocharc.json" in root_lower or "mocha.opts" in root_lower:
        return "mocha"
    # rspec
    if (root / ".rspec").is_file() or "gemfile" in root_lower:
        return "rspec"
    # Check for conftest.py inside the test dir itself
    if (test_dir / "conftest.py").is_file():
        return "pytest"
    # Python source extension files in the dir → default pytest
    try:
        for f in test_dir.iterdir():
            if f.suffix == ".py":
                return "pytest"
    except OSError:
        pass

    return "unknown"


# ── Entry-point detection ─────────────────────────────────────────────────────

# (filename_lower, kind) — checked at repo root and common source directories
_ENTRY_POINT_NAMES: list[tuple[str, str]] = [
    ("__main__.py",  "main"),
    ("main.py",      "main"),
    ("app.py",       "main"),
    ("server.py",    "api"),
    ("api.py",       "api"),
    ("cli.py",       "cli"),
    ("manage.py",    "cli"),    # Django
    ("wsgi.py",      "api"),
    ("asgi.py",      "api"),
    ("main.go",      "main"),
    ("cmd/main.go",  "main"),
    ("src/main.rs",  "main"),
    ("main.rs",      "main"),
    ("index.js",     "main"),
    ("index.ts",     "main"),
    ("server.js",    "api"),
    ("server.ts",    "api"),
]

# Source sub-directories to search for entry points (in addition to root)
_ENTRY_POINT_SOURCE_DIRS: tuple[str, ...] = ("src", "app", "lib", "cmd")


# ── RepositoryIntelligence ────────────────────────────────────────────────────


class RepositoryIntelligence:
    """Deterministic engine that scans a repository and returns a RepositorySnapshot.

    The engine makes no LLM calls, writes nothing, and never raises. All
    errors produce a best-effort snapshot with the data that was available.

    Usage:
        engine = RepositoryIntelligence(workspace_root="/workspace")
        snapshot = engine.scan("my-repo")
    """

    def __init__(self, workspace_root: str | Path) -> None:
        self._workspace_root = Path(workspace_root).resolve()

    # ── Public interface ──────────────────────────────────────────────────────

    def resolve_path(self, relative_path: str) -> Path:
        """Resolve a workspace-relative path, staying within workspace_root.

        Raises ValueError if the resolved path escapes workspace_root.
        """
        resolved = (self._workspace_root / relative_path).resolve()
        try:
            resolved.relative_to(self._workspace_root)
        except ValueError:
            raise ValueError(
                f"Path {relative_path!r} escapes workspace root {self._workspace_root}"
            )
        return resolved

    def scan(
        self,
        repository_path: str,
        *,
        snapshot_id: str | None = None,
        scanned_at: str | None = None,
    ) -> RepositorySnapshot:
        """Scan a repository and return an immutable RepositorySnapshot.

        Args:
            repository_path: Path to the repository, relative to workspace_root
                             or absolute. Must resolve within workspace_root.
            snapshot_id:     Override the deterministic snapshot identifier.
            scanned_at:      Override the ISO-8601 UTC scan timestamp.

        Returns:
            RepositorySnapshot with all detected structural properties.
        """
        try:
            repo_root = self.resolve_path(repository_path)
        except ValueError:
            repo_root = Path(repository_path).resolve()

        sid = snapshot_id or _make_snapshot_id(repository_path)
        ts = scanned_at or datetime.now(timezone.utc).isoformat(timespec="seconds")

        if not repo_root.is_dir():
            return RepositorySnapshot(
                snapshot_id=sid,
                repository_path=repository_path,
                scanned_at=ts,
                languages=(),
                primary_language="",
                build_system=None,
                entry_points=(),
                test_locations=(),
                config_files=(),
                documentation=(),
                files=(),
                file_count=0,
                directory_count=0,
                total_size_bytes=0,
                git_present=False,
                metadata=(),
            )

        all_files, all_dirs = self._walk_tree(repo_root)
        root_files = _root_file_names(repo_root)

        git_present = (repo_root / ".git").exists()

        languages = self._detect_languages(all_files)
        primary_language = languages[0].language if languages else ""

        build_system = self._detect_build_system(repo_root, root_files, languages)
        entry_points = self._detect_entry_points(repo_root, all_files, languages)
        test_locations = self._detect_test_locations(repo_root, all_dirs, all_files, root_files)
        config_files = self._detect_config_files(repo_root, all_files, root_files)
        documentation = self._detect_documentation(repo_root, all_files, all_dirs)

        total_size = sum(f.size_bytes for f in all_files)

        return RepositorySnapshot(
            snapshot_id=sid,
            repository_path=repository_path,
            scanned_at=ts,
            languages=tuple(languages),
            primary_language=primary_language,
            build_system=build_system,
            entry_points=tuple(sorted(entry_points, key=lambda e: e.path)),
            test_locations=tuple(sorted(test_locations, key=lambda t: t.path)),
            config_files=tuple(sorted(config_files, key=lambda c: c.path)),
            documentation=tuple(sorted(documentation, key=lambda d: d.path)),
            files=tuple(sorted(all_files, key=lambda f: f.path)),
            file_count=len(all_files),
            directory_count=len(all_dirs),
            total_size_bytes=total_size,
            git_present=git_present,
            metadata=(),
        )

    # ── Tree walk ─────────────────────────────────────────────────────────────

    def _walk_tree(
        self, root: Path
    ) -> tuple[list[RepositoryFile], list[RepositoryFile]]:
        """Walk the repository tree and return (files, directories).

        Skips ignored directories. Both lists contain RepositoryFile entries
        with paths relative to root.
        """
        files: list[RepositoryFile] = []
        dirs: list[RepositoryFile] = []

        for dirpath_str, dirnames, filenames in os.walk(str(root)):
            dirpath = Path(dirpath_str)

            # Prune ignored dirs in-place so os.walk skips them
            dirnames[:] = [
                d for d in dirnames if d not in _IGNORE_DIRS
            ]

            # Record sub-directories (not root itself)
            if dirpath != root:
                try:
                    rel = dirpath.relative_to(root)
                except ValueError:
                    continue
                dirs.append(RepositoryFile(
                    path=str(rel),
                    extension="",
                    size_bytes=0,
                    is_directory=True,
                ))

            for filename in filenames:
                filepath = dirpath / filename
                try:
                    size = filepath.stat().st_size
                except OSError:
                    size = 0
                try:
                    rel = filepath.relative_to(root)
                except ValueError:
                    continue

                ext = filepath.suffix.lower()
                files.append(RepositoryFile(
                    path=str(rel),
                    extension=ext,
                    size_bytes=size,
                    is_directory=False,
                ))

        return files, dirs

    # ── Language detection ────────────────────────────────────────────────────

    def _detect_languages(
        self, files: list[RepositoryFile]
    ) -> list[LanguageDetection]:
        """Detect programming languages from source file extensions.

        Returns a list sorted by file_count descending with confidence labels.
        """
        lang_files: dict[str, int] = defaultdict(int)
        lang_extensions: dict[str, set[str]] = defaultdict(set)

        for f in files:
            if f.extension in _SOURCE_EXTENSIONS:
                lang = _EXTENSION_TO_LANGUAGE[f.extension]
                lang_files[lang] += 1
                lang_extensions[lang].add(f.extension)

        if not lang_files:
            return []

        sorted_langs = sorted(lang_files.items(), key=lambda x: x[1], reverse=True)
        total_source = sum(lang_files.values())

        result: list[LanguageDetection] = []
        for i, (lang, count) in enumerate(sorted_langs):
            if i == 0:
                confidence = "primary"
            elif count / total_source >= 0.10:
                confidence = "secondary"
            else:
                confidence = "minor"

            result.append(LanguageDetection(
                language=lang,
                file_count=count,
                confidence=confidence,
                extensions=tuple(sorted(lang_extensions[lang])),
            ))

        return result

    # ── Build system detection ────────────────────────────────────────────────

    def _detect_build_system(
        self,
        root: Path,
        root_files: set[str],
        languages: list[LanguageDetection],
    ) -> BuildSystemDetection | None:
        """Detect the build system from root config files."""
        root_lower = {f.lower() for f in root_files}

        # Node.js: package.json → npm/yarn/pnpm
        if "package.json" in root_lower:
            manager = "npm"
            for lock_file, manager_name in _NODE_LOCK_FILES.items():
                if lock_file.lower() in root_lower:
                    manager = manager_name
                    break
            run_cmd = f"{manager} run build" if manager != "npm" else "npm run build"
            test_cmd = f"{manager} test" if manager != "npm" else "npm test"
            if manager == "pnpm":
                run_cmd = "pnpm run build"
                test_cmd = "pnpm test"
            elif manager == "yarn":
                run_cmd = "yarn build"
                test_cmd = "yarn test"
            return BuildSystemDetection(
                name=manager,
                config_file="package.json",
                build_command=run_cmd,
                test_command=test_cmd,
            )

        # Python-specific
        primary_lang = languages[0].language if languages else ""
        if primary_lang == "python" or any(
            f.lower() in root_lower for f in ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt")
        ):
            for indicator, name, build_cmd, _unused_test_cmd in _PYTHON_BUILD_INDICATORS:
                if indicator.lower() in root_lower:
                    # Distinguish poetry from setuptools via pyproject.toml content
                    if indicator == "pyproject.toml":
                        pyproject = root / "pyproject.toml"
                        try:
                            text = pyproject.read_text(encoding="utf-8", errors="ignore")
                            if "[tool.poetry]" in text:
                                name = "poetry"
                                build_cmd = "poetry build"
                            else:
                                name = "setuptools"
                                build_cmd = "python -m build"
                        except OSError:
                            pass
                    return BuildSystemDetection(
                        name=name,
                        config_file=indicator,
                        build_command=build_cmd,
                        test_command=_resolve_python_test_command(root, root_files),
                    )

        # Language-agnostic indicators
        for indicator, name, build_cmd, test_cmd in _BUILD_SYSTEM_INDICATORS:
            if indicator.lower() in root_lower:
                return BuildSystemDetection(
                    name=name,
                    config_file=indicator,
                    build_command=build_cmd,
                    test_command=test_cmd,
                )

        return None

    # ── Entry point detection ─────────────────────────────────────────────────

    def _detect_entry_points(
        self,
        root: Path,
        files: list[RepositoryFile],
        languages: list[LanguageDetection],
    ) -> list[EntryPoint]:
        """Detect entry points by well-known filenames."""
        lang_lookup = {lang.language for lang in languages}

        # Build a set of relative paths for O(1) lookup
        file_paths: set[str] = {f.path for f in files}

        results: list[EntryPoint] = []
        seen: set[str] = set()

        def _add(rel_path: str, kind: str) -> None:
            # Normalise separators
            normalized = rel_path.replace("\\", "/")
            if normalized in seen:
                return
            # Verify the file actually exists in the tree
            if normalized in file_paths or rel_path in file_paths:
                seen.add(normalized)
                ext = Path(normalized).suffix.lower()
                lang = _EXTENSION_TO_LANGUAGE.get(ext, "unknown")
                results.append(EntryPoint(path=normalized, kind=kind, language=lang))

        # Check root-level entry points
        for name_lower, kind in _ENTRY_POINT_NAMES:
            _add(name_lower, kind)

        # Check inside common source directories
        for src_dir in _ENTRY_POINT_SOURCE_DIRS:
            for name_lower, kind in _ENTRY_POINT_NAMES:
                _add(f"{src_dir}/{name_lower}", kind)

        # Rust: src/bin/*.rs → cli entry points
        for f in files:
            normalized = f.path.replace("\\", "/")
            parts = normalized.split("/")
            if (
                len(parts) >= 3
                and parts[0] == "src"
                and parts[1] == "bin"
                and f.extension == ".rs"
                and normalized not in seen
            ):
                seen.add(normalized)
                results.append(EntryPoint(path=normalized, kind="cli", language="rust"))

        return results

    # ── Test location detection ───────────────────────────────────────────────

    def _detect_test_locations(
        self,
        root: Path,
        dirs: list[RepositoryFile],
        files: list[RepositoryFile],
        root_files: set[str],
    ) -> list[TestLocation]:
        """Detect test directories and standalone test files."""
        results: list[TestLocation] = []
        covered_prefixes: set[str] = set()

        # 1. Well-known test directories
        for d in dirs:
            dir_name = Path(d.path).name.lower()
            if dir_name in _TEST_DIRECTORY_NAMES:
                dir_path = d.path.replace("\\", "/")
                test_dir_abs = root / d.path

                # Count test files beneath this directory
                prefix = dir_path + "/"
                count = sum(
                    1 for f in files
                    if f.path.replace("\\", "/").startswith(prefix)
                    and _is_test_file(Path(f.path).name)
                )
                # Also count all files if directory name strongly implies tests
                if count == 0:
                    count = sum(
                        1 for f in files
                        if f.path.replace("\\", "/").startswith(prefix)
                    )

                if count > 0:
                    framework = _detect_test_framework(test_dir_abs, root, root_files)
                    results.append(TestLocation(
                        path=dir_path,
                        kind="directory",
                        framework=framework,
                        file_count=count,
                    ))
                    covered_prefixes.add(prefix)

        # 2. Standalone test files not inside a test directory
        for f in files:
            normalized = f.path.replace("\\", "/")
            if not _is_test_file(Path(f.path).name):
                continue
            # Skip if inside an already-covered test directory
            if any(normalized.startswith(p) for p in covered_prefixes):
                continue
            framework = _detect_test_framework(root / f.path, root, root_files)
            results.append(TestLocation(
                path=normalized,
                kind="file",
                framework=framework,
                file_count=1,
            ))

        return results

    # ── Config file detection ─────────────────────────────────────────────────

    def _detect_config_files(
        self,
        root: Path,
        files: list[RepositoryFile],
        root_files: set[str],
    ) -> list[ConfigFile]:
        """Detect configuration files across the repository."""
        results: list[ConfigFile] = []
        seen: set[str] = set()

        def _add(rel_path: str, kind: str) -> None:
            normalized = rel_path.replace("\\", "/")
            if normalized not in seen:
                seen.add(normalized)
                results.append(ConfigFile(path=normalized, kind=kind))

        for f in files:
            name = Path(f.path).name
            name_lower = name.lower()
            normalized = f.path.replace("\\", "/")

            # Exact name match
            if name_lower in _CONFIG_FILE_NAMES:
                _add(normalized, _CONFIG_FILE_NAMES[name_lower])
                continue

            # .env.* variants
            if name_lower.startswith(".env.") or name_lower == ".env":
                _add(normalized, "environment")
                continue

            # CI: .github/workflows/**
            parts = normalized.split("/")
            if len(parts) >= 2 and parts[0] in _CI_DIRECTORIES:
                _add(normalized, "ci")
                continue

            # Dockerfile variants (e.g. Dockerfile.prod)
            if name_lower.startswith("dockerfile"):
                _add(normalized, "docker")
                continue

            # docker-compose variants
            if name_lower.startswith("docker-compose"):
                _add(normalized, "docker")
                continue

        return results

    # ── Documentation detection ───────────────────────────────────────────────

    def _detect_documentation(
        self,
        root: Path,
        files: list[RepositoryFile],
        dirs: list[RepositoryFile],
    ) -> list[DocumentationFile]:
        """Detect documentation files and directories."""
        results: list[DocumentationFile] = []
        seen: set[str] = set()

        def _add(rel_path: str, kind: str) -> None:
            normalized = rel_path.replace("\\", "/")
            if normalized not in seen:
                seen.add(normalized)
                results.append(DocumentationFile(path=normalized, kind=kind))

        # Files
        for f in files:
            name = Path(f.path).name
            name_lower = name.lower()
            normalized = f.path.replace("\\", "/")

            kind = _doc_kind(name_lower)
            if kind:
                _add(normalized, kind)
                continue

            # openapi / swagger at any depth
            if name_lower in ("openapi.yaml", "openapi.yml", "swagger.yaml", "swagger.yml", "openapi.json"):
                _add(normalized, "api")

        # Documentation directories at top level or one level deep
        for d in dirs:
            dir_name = Path(d.path).name.lower()
            normalized = d.path.replace("\\", "/")
            depth = len(normalized.split("/"))
            if depth <= 2 and dir_name in _DOC_DIRECTORIES:
                _add(normalized, "directory")

        return results


# ── Module-level helpers ──────────────────────────────────────────────────────


def _make_snapshot_id(repository_path: str) -> str:
    """Return a 16-character deterministic hex identifier from repository_path."""
    return hashlib.sha256(repository_path.encode()).hexdigest()[:16]


def _root_file_names(root: Path) -> set[str]:
    """Return the set of filenames (not directories) directly in root."""
    try:
        return {
            entry.name
            for entry in root.iterdir()
            if entry.is_file()
        }
    except OSError:
        return set()


# ── Public API ────────────────────────────────────────────────────────────────

__all__ = ["RepositoryIntelligence"]
