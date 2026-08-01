"""Operation ID generator.

Produces unique, human-readable IDs in the format OP-{YYYYMMDD}-{NNN}.
Sequence numbers are derived from existing files in the operations directory.
"""

from datetime import datetime, timezone
from pathlib import Path


def generate_operation_id(operations_dir: Path) -> str:
    """Generate the next Operation ID for today's date.

    Scans *operations_dir* for existing IDs with today's date prefix
    and returns the next sequential ID.
    """
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    prefix = f"OP-{today}-"

    max_seq = 0
    if operations_dir.is_dir():
        for path in operations_dir.iterdir():
            name = path.stem
            if name.startswith(prefix) and path.suffix == ".yaml":
                try:
                    seq = int(name[len(prefix):])
                    if seq > max_seq:
                        max_seq = seq
                except ValueError:
                    pass

    return f"{prefix}{max_seq + 1:03d}"
