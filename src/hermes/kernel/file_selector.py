import re
from pathlib import Path

from hermes.models import Task, WorkspaceFile, WorkspaceSnapshot

MAX_SELECTED_FILES = 30
MIN_SELECTED_FILES = 5

# Words that appear in task descriptions but don't help identify relevant files.
STOPWORDS = frozenset({
    "the", "and", "for", "are", "but", "not", "you", "all", "can",
    "had", "her", "was", "one", "our", "out", "get", "has", "how",
    "its", "let", "new", "now", "old", "see", "way", "who", "did",
    "she", "use", "may", "set", "put", "too", "any", "few", "far",
    "off", "yet", "add", "run", "fix", "try", "ask",
    # Common task verbs that carry intent but not file-relevance.
    "review", "improve", "update", "refactor", "implement", "create",
    "build", "make", "write", "check", "test", "debug", "deploy",
    "analyze", "analyse", "show", "list", "find", "help", "with",
})

MANIFEST_FILENAMES = frozenset({
    "package.json", "pyproject.toml", "requirements.txt",
    "setup.py", "setup.cfg", "cargo.toml", "composer.json",
    "go.mod", "pom.xml", "build.gradle",
})

README_PATTERN = re.compile(r"^readme(\.(md|txt|rst))?$", re.IGNORECASE)

CONFIG_FILENAMES = frozenset({
    "tsconfig.json", "jsconfig.json", ".env.example",
    "next.config.js", "next.config.ts", "next.config.mjs",
    "vite.config.ts", "vite.config.js",
    "docker-compose.yml", "compose.yaml", "makefile",
})

CONFIG_PATTERN = re.compile(r".*\.config\.(js|ts|mjs|cjs)$", re.IGNORECASE)

# Used to split CamelCase/PascalCase before tokenising.
_CAMEL_SPLIT = re.compile(r"([a-z])([A-Z])")
_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


class FileSelector:
    """Ranks workspace files by relevance to a task using simple keyword and
    structural rules. No AI, no embeddings, no vector search."""

    def select(self, snapshot: WorkspaceSnapshot, task: Task) -> WorkspaceSnapshot:
        if not snapshot.files:
            return snapshot

        keywords = self._extract_keywords(task)
        scored = sorted(
            ((self._score(f, keywords), f) for f in snapshot.files),
            key=lambda x: x[0],
            reverse=True,
        )

        positive = [f for score, f in scored if score > 0.0]

        if len(positive) >= MIN_SELECTED_FILES:
            selected = positive[:MAX_SELECTED_FILES]
        else:
            # Not enough positively scored files — supplement from the top.
            selected = [f for _, f in scored[:max(MIN_SELECTED_FILES, len(positive))]]

        return WorkspaceSnapshot(root=snapshot.root, files=selected)

    def _score(self, file: WorkspaceFile, keywords: list[str]) -> float:
        score = self._baseline_score(file)
        filename = Path(file.path).name.lower()
        # Include repository name in searchable text for multi-repo attribution.
        searchable = f"{file.path} {file.repository}".lower()

        for keyword in keywords:
            if keyword in filename:
                score += 3.0
            elif keyword in searchable:
                score += 1.0

        return score

    @staticmethod
    def _baseline_score(file: WorkspaceFile) -> float:
        """Structural score independent of task keywords."""
        score = 0.0
        filename = Path(file.path).name.lower()

        if README_PATTERN.match(filename):
            score += 2.0
        if filename in MANIFEST_FILENAMES:
            score += 1.5
        if filename in CONFIG_FILENAMES:
            score += 1.0
        elif CONFIG_PATTERN.match(filename):
            score += 1.0

        return score

    @staticmethod
    def _extract_keywords(task: Task) -> list[str]:
        text = f"{task.business} {task.request}"
        # Split CamelCase before lowercasing so "ClaudeProvider" → "claude provider".
        text = _CAMEL_SPLIT.sub(r"\1 \2", text).lower()
        tokens = _TOKEN_SPLIT.split(text)
        return [t for t in tokens if len(t) >= 3 and t not in STOPWORDS]
