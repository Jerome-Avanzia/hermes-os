"""Decision ID generator.

Scans an existing Decisions.md for the highest DEC-NNN ID
and returns the next sequential ID.
"""

import re
from pathlib import Path


def generate_decision_id(business_dir: Path) -> str:
    """Generate the next Decision ID by scanning Decisions.md."""
    decisions_path = business_dir / "Decisions.md"

    max_seq = 0
    if decisions_path.is_file():
        text = decisions_path.read_text(encoding="utf-8")
        for match in re.finditer(r"DEC-(\d{3})", text):
            seq = int(match.group(1))
            if seq > max_seq:
                max_seq = seq

    return f"DEC-{max_seq + 1:03d}"
