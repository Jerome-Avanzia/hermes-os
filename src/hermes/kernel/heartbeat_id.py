"""Heartbeat ID generator.

Produces unique, human-readable IDs in the format HB-{NNN}.
Sequence numbers are derived from existing files in the heartbeats directory.
"""

from pathlib import Path


def generate_heartbeat_id(heartbeats_dir: Path) -> str:
    """Generate the next Heartbeat ID.

    Scans *heartbeats_dir* for existing HB-NNN files
    and returns the next sequential ID.
    """
    max_seq = 0
    if heartbeats_dir.is_dir():
        for path in heartbeats_dir.iterdir():
            name = path.stem
            if name.startswith("HB-") and path.suffix == ".yaml":
                try:
                    seq = int(name[3:])
                    if seq > max_seq:
                        max_seq = seq
                except ValueError:
                    pass

    return f"HB-{max_seq + 1:03d}"
