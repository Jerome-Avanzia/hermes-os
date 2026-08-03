"""Business Knowledge Operation ID generator.

Scans an existing Operations.md for the highest OPS-NNN ID
and returns the next sequential ID.

This is distinct from operation_id.py which generates OP-YYYYMMDD-NNN
IDs for workspace YAML Operations.  The OPS-NNN format matches the
Business Knowledge convention (DEC-NNN, EXP-NNN, LES-NNN).
"""

import re
from pathlib import Path


def generate_bk_operation_id(business_dir: Path) -> str:
    """Generate the next Operation ID by scanning Operations.md."""
    operations_path = business_dir / "Operations.md"

    max_seq = 0
    if operations_path.is_file():
        text = operations_path.read_text(encoding="utf-8")
        for match in re.finditer(r"OPS-(\d{3})", text):
            seq = int(match.group(1))
            if seq > max_seq:
                max_seq = seq

    return f"OPS-{max_seq + 1:03d}"
