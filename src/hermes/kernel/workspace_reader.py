import logging
import os
from collections.abc import Iterator
from pathlib import Path

from hermes.models import WorkspaceContext, WorkspaceFile, WorkspaceSnapshot

logger = logging.getLogger(__name__)

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
        if workspace.repositories:
            return self._read_repositories(workspace)
        return self._read_single(workspace)

    def _read_single(self, workspace: WorkspaceContext) -> WorkspaceSnapshot:
        root = Path(workspace.workspace.path)

        if not workspace.exists or not root.is_dir():
            logger.info("Workspace does not exist, skipping read: %s", root)
            return WorkspaceSnapshot(root=str(root), files=[])

        files = []
        for path in sorted(self._iter_candidates(root)):
            if len(files) >= MAX_FILES:
                logger.warning("Workspace read hit the %d file cap at %s", MAX_FILES, root)
                break

            workspace_file = self._read_file(root, path)
            if workspace_file is not None:
                files.append(workspace_file)

        logger.info("Read %d file(s) from workspace %s", len(files), root)
        return WorkspaceSnapshot(root=str(root), files=files)

    def _read_repositories(self, workspace: WorkspaceContext) -> WorkspaceSnapshot:
        all_files: list[WorkspaceFile] = []

        for repo in workspace.repositories:
            if not repo.exists:
                logger.info("Repository does not exist, skipping: %s", repo.path)
                continue

            root = Path(repo.path)
            for path in sorted(self._iter_candidates(root)):
                if len(all_files) >= MAX_FILES:
                    logger.warning("Workspace read hit the %d file cap", MAX_FILES)
                    break

                wf = self._read_file(root, path)
                if wf is not None:
                    all_files.append(
                        WorkspaceFile(
                            path=wf.path,
                            extension=wf.extension,
                            size=wf.size,
                            content=wf.content,
                            repository=repo.name,
                        )
                    )

        logger.info(
            "Read %d file(s) across %d repositor(ies)",
            len(all_files),
            len(workspace.repositories),
        )
        return WorkspaceSnapshot(root=workspace.workspace.project_id, files=all_files)

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
