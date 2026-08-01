"""DecisionWriter — appends Decision rows to a Decisions.md pandoc table.

Writes new rows into the existing fixed-width pandoc table format,
preserving column boundaries and multi-line cell wrapping.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from hermes.models.decision import Decision

# Column widths matching the Decisions.md pandoc table format.
# These correspond to the dash-group widths in the separator line.
_COL_WIDTHS = {
    "id": 9,
    "date": 12,
    "status": 13,
    "decision": 15,
    "impact": 26,
}

# Leading indent (2 spaces) matching existing table format.
_INDENT = "  "


def _wrap_cell(text: str, width: int) -> list[str]:
    """Wrap text to fit within a fixed column width."""
    if not text:
        return [""]
    lines = textwrap.wrap(text, width=width)
    return lines if lines else [""]


def _format_row(
    dec_id: str,
    date: str,
    status: str,
    title: str,
    rationale: str,
) -> str:
    """Format a single Decision as pandoc table row lines."""
    id_lines = _wrap_cell(dec_id, _COL_WIDTHS["id"])
    date_lines = _wrap_cell(date, _COL_WIDTHS["date"])
    status_lines = _wrap_cell(status, _COL_WIDTHS["status"])
    title_lines = _wrap_cell(title, _COL_WIDTHS["decision"])
    impact_lines = _wrap_cell(rationale, _COL_WIDTHS["impact"])

    max_lines = max(
        len(id_lines), len(date_lines), len(status_lines),
        len(title_lines), len(impact_lines),
    )

    # Pad each column's lines to max_lines
    def pad(lines: list[str], width: int) -> list[str]:
        padded = [ln.ljust(width) for ln in lines]
        while len(padded) < max_lines:
            padded.append(" " * width)
        return padded

    id_col = pad(id_lines, _COL_WIDTHS["id"])
    date_col = pad(date_lines, _COL_WIDTHS["date"])
    status_col = pad(status_lines, _COL_WIDTHS["status"])
    title_col = pad(title_lines, _COL_WIDTHS["decision"])
    impact_col = pad(impact_lines, _COL_WIDTHS["impact"])

    row_lines = []
    for i in range(max_lines):
        line = (
            _INDENT
            + id_col[i] + " "
            + date_col[i] + " "
            + status_col[i] + " "
            + title_col[i] + " "
            + impact_col[i].rstrip()
        )
        row_lines.append(line)

    return "\n".join(row_lines)


class DecisionWriter:
    """Appends Decision records to a business's Decisions.md file."""

    def append_decision(self, business_dir: Path, decision: Decision) -> None:
        """Append a Decision row to the Decision Log table in Decisions.md."""
        decisions_path = business_dir / "Decisions.md"
        if not decisions_path.is_file():
            raise FileNotFoundError(f"Decisions.md not found in {business_dir}")

        text = decisions_path.read_text(encoding="utf-8")
        lines = text.split("\n")

        # Find the closing dash-line of the Decision Log table.
        # It's the last line matching the full-boundary pattern after the
        # "## Decision Log" heading.
        section_start = None
        for i, line in enumerate(lines):
            if line.strip() == "## Decision Log":
                section_start = i
                break

        if section_start is None:
            raise ValueError("No '## Decision Log' section found in Decisions.md")

        # Find the closing boundary line — the LAST dash-only line in the
        # Decision Log section.  The pandoc full-boundary table has three
        # dash lines: opening, header-separator, closing.  We want the last.
        closing_idx = None
        for i in range(section_start + 1, len(lines)):
            stripped = lines[i].strip()
            # Stop at the next section heading
            if stripped.startswith("## ") or stripped.startswith("# "):
                break
            if stripped and all(c == "-" or c == " " for c in stripped) and len(stripped.replace(" ", "")) >= 10:
                closing_idx = i

        if closing_idx is None:
            raise ValueError("Could not find closing boundary of Decision Log table")

        # Format the status with title case for display
        display_status = decision.status.replace("_", " ").title()

        new_row = _format_row(
            decision.decision_id,
            decision.decision_date,
            display_status,
            decision.title,
            decision.rationale,
        )

        # Insert the new row before the closing boundary, with a blank
        # line separator (matching existing pandoc table format).
        lines.insert(closing_idx, "")
        lines.insert(closing_idx + 1, new_row)

        decisions_path.write_text("\n".join(lines), encoding="utf-8")
