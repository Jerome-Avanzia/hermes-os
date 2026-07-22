import os
from collections.abc import Iterator
from pathlib import Path

from hermes.models import WorkspaceContext, WorkspaceFile, WorkspaceSnapshot

SUPPORTED_EXTENSIONS = {
    ".md",
    ".txt",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".py",
    ".json",
    ".yaml",
    ".yml",
    ".css",
    ".html",
}

IGNORED_DIRECTORIES = {
    "node_modules",
    ".git",
    "dist",
    "build",
    ".next",
    "coverage",
    ".venv",
}

MAX_FILES = 500
MAX_FILE_SIZE_BYTES = 250 * 1024


class WorkspaceReader:
    def read(self, workspace: WorkspaceContext) -> WorkspaceSnapshot:
        root = Path(workspace.workspace.path)

        if not workspace.exists or not root.is_dir():
            return WorkspaceSnapshot(root=str(root), files=[])

        files = []
        for path in sorted(self._iter_candidates(root)):
            if len(files) >= MAX_FILES:
                break

            workspace_file = self._read_file(root, path)
            if workspace_file is not None:
                files.append(workspace_file)

        return WorkspaceSnapshot(root=str(root), files=files)

    def _iter_candidates(self, root: Path) -> Iterator[Path]:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                name for name in dirnames if name not in IGNORED_DIRECTORIES
            ]

            for filename in filenames:
                path = Path(dirpath) / filename
                if path.suffix.lower() in SUPPORTED_EXTENSIONS:
                    yield path

    @staticmethod
    def _read_file(root: Path, path: Path) -> WorkspaceFile | None:
        try:
            size = path.stat().st_size
        except OSError:
            return None

        if size > MAX_FILE_SIZE_BYTES:
            return None

        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            return None

        return WorkspaceFile(
            path=str(path.relative_to(root)),
            extension=path.suffix.lower(),
            size=size,
            content=content,
        )
