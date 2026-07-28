from dataclasses import dataclass


@dataclass(slots=True)
class DiagnosticsReport:
    # Project
    project_id: str

    # Repositories
    repositories: list[str]

    # Knowledge
    knowledge_documents: list[str]  # titles

    # Workspace
    files_scanned: int  # total files before FileSelector

    # Selection
    files_selected: list[str]  # paths chosen by FileSelector

    # Content
    files_read: int          # files injected into prompt
    chars_read: int          # total chars injected
    chars_truncated: int     # chars cut by budget limits

    # Provider (prompt accounting)
    knowledge_chars: int      # size of knowledge section
    file_content_chars: int   # size of file content section
    prompt_chars: int         # total prompt size (approximation)
