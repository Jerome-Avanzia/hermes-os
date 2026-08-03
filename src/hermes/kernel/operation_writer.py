"""OperationWriter — appends Operation rows to an Operations.md pandoc table.

Writes new rows into the existing fixed-width pandoc table format,
preserving column boundaries and multi-line cell wrapping.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from hermes.models.operation import Operation

# Column widths matching the Operations.md pandoc table format.
# These correspond to the dash-group widths in the separator line.
_COL_WIDTHS = {
    "id": 9,
    "date": 12,
    "status": 13,
    "operation": 15,
    "outcome": 26,
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
    ops_id: str,
    date: str,
    status: str,
    title: str,
    outcome: str,
) -> str:
    """Format a single Operation as pandoc table row lines."""
    id_lines = _wrap_cell(ops_id, _COL_WIDTHS["id"])
    date_lines = _wrap_cell(date, _COL_WIDTHS["date"])
    status_lines = _wrap_cell(status, _COL_WIDTHS["status"])
    title_lines = _wrap_cell(title, _COL_WIDTHS["operation"])
    outcome_lines = _wrap_cell(outcome, _COL_WIDTHS["outcome"])

    max_lines = max(
        len(id_lines), len(date_lines), len(status_lines),
        len(title_lines), len(outcome_lines),
    )

    def pad(lines: list[str], width: int) -> list[str]:
        padded = [ln.ljust(width) for ln in lines]
        while len(padded) < max_lines:
            padded.append(" " * width)
        return padded

    id_col = pad(id_lines, _COL_WIDTHS["id"])
    date_col = pad(date_lines, _COL_WIDTHS["date"])
    status_col = pad(status_lines, _COL_WIDTHS["status"])
    title_col = pad(title_lines, _COL_WIDTHS["operation"])
    outcome_col = pad(outcome_lines, _COL_WIDTHS["outcome"])

    row_lines = []
    for i in range(max_lines):
        line = (
            _INDENT
            + id_col[i] + " "
            + date_col[i] + " "
            + status_col[i] + " "
            + title_col[i] + " "
            + outcome_col[i].rstrip()
        )
        row_lines.append(line)

    return "\n".join(row_lines)


class OperationWriter:
    """Appends Operation records to a business's Operations.md file."""

    def append_operation(
        self,
        business_dir: Path,
        operation: Operation,
        bk_operation_id: str,
    ) -> None:
        """Append an Operation row to the Operation Log table in Operations.md.

        Parameters
        ----------
        business_dir : Path
            The business directory containing Operations.md.
        operation : Operation
            The workspace Operation being recorded.
        bk_operation_id : str
            The Business Knowledge ID (OPS-NNN) for the row.
        """
        operations_path = business_dir / "Operations.md"
        if not operations_path.is_file():
            raise FileNotFoundError(f"Operations.md not found in {business_dir}")

        text = operations_path.read_text(encoding="utf-8")
        lines = text.split("\n")

        # Find the "## Operation Log" section
        section_start = None
        for i, line in enumerate(lines):
            if line.strip() == "## Operation Log":
                section_start = i
                break

        if section_start is None:
            raise ValueError("No '## Operation Log' section found in Operations.md")

        # Find the closing boundary line — the LAST indented dash-line
        # in the Operation Log section.  Pandoc table boundaries are
        # indented (2 spaces), while markdown horizontal rules are not.
        closing_idx = None
        for i in range(section_start + 1, len(lines)):
            stripped = lines[i].strip()
            if stripped.startswith("## ") or stripped.startswith("# "):
                break
            if (lines[i].startswith(_INDENT)
                    and stripped
                    and all(c == "-" or c == " " for c in stripped)
                    and len(stripped.replace(" ", "")) >= 10):
                closing_idx = i

        if closing_idx is None:
            raise ValueError("Could not find closing boundary of Operation Log table")

        # Format status and outcome for display
        display_status = operation.status.replace("_", " ").title()
        outcome_text = operation.outcome or ""
        if operation.outcome_classification:
            prefix = operation.outcome_classification.title()
            if outcome_text:
                outcome_text = f"{prefix} — {outcome_text}"
            else:
                outcome_text = prefix

        # Use operation_date if available, else created_at month
        op_date = operation.created_at.strftime("%Y-%m")

        new_row = _format_row(
            bk_operation_id,
            op_date,
            display_status,
            operation.request,
            outcome_text,
        )

        # Insert the new row before the closing boundary
        lines.insert(closing_idx, "")
        lines.insert(closing_idx + 1, new_row)

        operations_path.write_text("\n".join(lines), encoding="utf-8")
