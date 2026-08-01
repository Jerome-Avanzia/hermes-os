"""Job ID generator.

Produces unique, human-readable IDs in the format JOB-{YYYYMMDD}-{NNN}.
Sequence numbers are derived from existing files in the jobs directory.
"""

from datetime import datetime, timezone
from pathlib import Path


def generate_job_id(jobs_dir: Path) -> str:
    """Generate the next Job ID for today's date.

    Scans *jobs_dir* for existing IDs with today's date prefix
    and returns the next sequential ID.
    """
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    prefix = f"JOB-{today}-"

    max_seq = 0
    if jobs_dir.is_dir():
        for path in jobs_dir.iterdir():
            name = path.stem
            if name.startswith(prefix) and path.suffix == ".yaml":
                try:
                    seq = int(name[len(prefix):])
                    if seq > max_seq:
                        max_seq = seq
                except ValueError:
                    pass

    return f"{prefix}{max_seq + 1:03d}"
