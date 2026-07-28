import logging
from pathlib import Path

from hermes.models.file_content import FileContent
from hermes.models.workspace_snapshot import WorkspaceSnapshot

logger = logging.getLogger(__name__)

# Maximum number of files whose content is injected into the prompt.
MAX_FILES_IN_CONTEXT = 15

# Total character budget across all injected files.
MAX_TOTAL_CHARS = 150_000

# Per-file character limit before truncation.
MAX_CHARS_PER_FILE = 15_000

# Language identifiers for code fence rendering.
_EXTENSION_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".md": "markdown",
    ".css": "css",
    ".html": "html",
    ".txt": "",
}


class FileContentReader:
    """Converts a WorkspaceSnapshot into a list of FileContent objects ready
    for prompt injection, applying per-file and total character budgets."""

    def read(self, snapshot: WorkspaceSnapshot) -> list[FileContent]:
        files, _, _ = self._read_internal(snapshot)
        return files

    def read_with_stats(self, snapshot: WorkspaceSnapshot) -> tuple[list[FileContent], int, int]:
        """Returns ``(files, chars_read, chars_truncated)``."""
        return self._read_internal(snapshot)

    def _read_internal(
        self, snapshot: WorkspaceSnapshot
    ) -> tuple[list[FileContent], int, int]:
        results: list[FileContent] = []
        total_chars = 0
        chars_truncated = 0

        for file in snapshot.files[:MAX_FILES_IN_CONTEXT]:
            if total_chars >= MAX_TOTAL_CHARS:
                logger.info(
                    "File content budget exhausted at %d chars — stopping at %d files",
                    total_chars,
                    len(results),
                )
                break

            content = file.content
            if not content or not content.strip():
                continue

            original_len = len(content)

            # Truncate to per-file limit first.
            if len(content) > MAX_CHARS_PER_FILE:
                content = content[:MAX_CHARS_PER_FILE] + "\n... [truncated]"
                logger.debug("Truncated %s to %d chars", file.path, MAX_CHARS_PER_FILE)

            # Then respect the remaining total budget.
            remaining = MAX_TOTAL_CHARS - total_chars
            if len(content) > remaining:
                content = content[:remaining] + "\n... [truncated]"

            chars_truncated += max(0, original_len - len(content))
            results.append(FileContent(
                repository=file.repository,
                path=file.path,
                content=content,
            ))
            total_chars += len(content)

        logger.info(
            "FileContentReader: %d file(s) selected, %d total chars, %d chars truncated",
            len(results),
            total_chars,
            chars_truncated,
        )
        return results, total_chars, chars_truncated

    @staticmethod
    def language(path: str) -> str:
        """Returns the code fence language identifier for a given file path."""
        return _EXTENSION_LANGUAGE.get(Path(path).suffix.lower(), "")
