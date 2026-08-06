"""Repository Intelligence — typed contracts for the Repository Intelligence engine.

Architecture position:
  Deterministic Kernel → Repository Intelligence → RepositorySnapshot

The Repository Intelligence engine produces an immutable RepositorySnapshot
describing the structural properties of a repository. It operates without
LLM calls, without filesystem writes, and without execution outside the
deterministic kernel.

All contracts in this module are immutable after construction.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


# ── LanguageDetection ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class LanguageDetection:
    """Immutable detection result for one programming language in the repository.

    language   → canonical language name (e.g. "python", "javascript", "rust")
    file_count → number of source files with this language's extensions
    confidence → "primary" (most files), "secondary" (significant presence),
                 "minor" (small number of files)
    extensions → unique file extensions that contributed to detection,
                 sorted lexicographically

    Immutable after construction.
    """

    language: str
    file_count: int
    confidence: str                        # "primary" | "secondary" | "minor"
    extensions: tuple[str, ...]            # e.g. (".py",) or (".ts", ".tsx")


# ── BuildSystemDetection ───────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class BuildSystemDetection:
    """Immutable detection result for the repository's build system.

    name          → canonical build system name (e.g. "poetry", "cargo", "npm")
    config_file   → path of the primary build configuration file,
                    relative to the repository root
    build_command → default command to build the project
    test_command  → default command to run the test suite

    Immutable after construction.
    """

    name: str                              # e.g. "poetry", "cargo", "npm"
    config_file: str                       # repo-relative path
    build_command: str                     # e.g. "poetry build"
    test_command: str                      # e.g. "pytest"


# ── EntryPoint ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class EntryPoint:
    """Immutable description of a detected entry point in the repository.

    path     → file path relative to the repository root
    kind     → "main" (script / binary), "cli" (command-line interface),
               "api" (HTTP/REST service), "service" (background service),
               "library" (no standalone entry)
    language → language of this entry point (e.g. "python")

    Immutable after construction.
    """

    path: str
    kind: str                              # "main" | "cli" | "api" | "service" | "library"
    language: str


# ── TestLocation ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TestLocation:
    """Immutable description of a detected test location in the repository.

    path       → directory or file path relative to the repository root
    kind       → "directory" (a test directory) | "file" (a single test file)
    framework  → detected test framework:
                   "pytest" | "unittest" | "jest" | "vitest" | "mocha" |
                   "cargo-test" | "go-test" | "rspec" | "minitest" | "unknown"
    file_count → number of test files at or beneath this location

    Immutable after construction.
    """

    path: str
    kind: str                              # "directory" | "file"
    framework: str
    file_count: int


# ── ConfigFile ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ConfigFile:
    """Immutable description of a detected configuration file.

    path → file path relative to the repository root
    kind → category of configuration:
             "environment"  (.env, .env.*)
             "docker"       (Dockerfile, docker-compose.*)
             "ci"           (.github/workflows, .gitlab-ci.yml, Jenkinsfile)
             "editor"       (.editorconfig, .eslintrc, .prettierrc, pyproject.toml lint)
             "git"          (.gitignore, .gitattributes)
             "package"      (pyproject.toml, setup.cfg, package.json, Cargo.toml)
             "test"         (pytest.ini, jest.config.js, conftest.py)
             "lint"         (mypy.ini, .ruff.toml, .flake8)
             "other"

    Immutable after construction.
    """

    path: str
    kind: str


# ── DocumentationFile ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DocumentationFile:
    """Immutable description of a detected documentation file or directory.

    path → file or directory path relative to the repository root
    kind → category:
             "readme"       (README.md, README.rst, README.txt)
             "changelog"    (CHANGELOG.md, HISTORY.md, CHANGES.md)
             "license"      (LICENSE, LICENSE.md, LICENSE.txt)
             "contributing" (CONTRIBUTING.md)
             "api"          (API.md, openapi.yaml, swagger.yaml)
             "directory"    (docs/, documentation/)
             "other"

    Immutable after construction.
    """

    path: str
    kind: str


# ── RepositoryFile ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RepositoryFile:
    """Immutable description of a single file or directory in the repository tree.

    path         → path relative to the repository root (forward-slash separated)
    extension    → lowercased file extension including the dot (e.g. ".py"),
                   or "" for files with no extension and for directories
    size_bytes   → file size in bytes; 0 for directories
    is_directory → True if this entry is a directory, False if it is a file

    Immutable after construction.
    """

    path: str
    extension: str                         # e.g. ".py", "" for no extension
    size_bytes: int
    is_directory: bool


# ── RepositorySnapshot ─────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RepositorySnapshot:
    """Immutable structural snapshot of a repository.

    Produced by RepositoryIntelligence.scan(). Contains everything the
    Conductor and Engineering Workflow need to understand the repository
    before planning any implementation.

    Lifecycle:
      RepositoryIntelligence.scan(repository_path) → RepositorySnapshot

    Fields:
      snapshot_id      → deterministic identifier derived from repository path
      repository_path  → the path provided to scan() (workspace-relative)
      scanned_at       → ISO-8601 UTC timestamp of when the scan was performed
      languages        → detected programming languages, sorted by file_count desc
      primary_language → language with the most source files, "" if none detected
      build_system     → detected build system, None if not detected
      entry_points     → detected entry points, sorted by path
      test_locations   → detected test directories/files, sorted by path
      config_files     → detected configuration files, sorted by path
      documentation    → detected documentation files, sorted by path
      files            → all files in the repository tree, sorted by path
      file_count       → total number of files (not directories)
      directory_count  → total number of directories
      total_size_bytes → sum of all file sizes in bytes
      git_present      → True if the repository contains a .git directory
      metadata         → sorted tuple of additional key-value pairs

    Immutable after construction.
    """

    snapshot_id: str
    repository_path: str
    scanned_at: str                        # ISO-8601 UTC

    languages: tuple[LanguageDetection, ...]
    primary_language: str                  # "" if no source files found

    build_system: BuildSystemDetection | None

    entry_points: tuple[EntryPoint, ...]
    test_locations: tuple[TestLocation, ...]
    config_files: tuple[ConfigFile, ...]
    documentation: tuple[DocumentationFile, ...]

    files: tuple[RepositoryFile, ...]      # all files (not directories)
    file_count: int
    directory_count: int
    total_size_bytes: int
    git_present: bool

    metadata: tuple[tuple[str, str], ...]  # sorted (key, value) pairs


# ── Public API ─────────────────────────────────────────────────────────────────


__all__ = [
    "BuildSystemDetection",
    "ConfigFile",
    "DocumentationFile",
    "EntryPoint",
    "LanguageDetection",
    "RepositoryFile",
    "RepositorySnapshot",
    "TestLocation",
]
