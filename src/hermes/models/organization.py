from dataclasses import dataclass


@dataclass(slots=True)
class Organization:
    """Stable identity of a business, read from markdown source files."""

    name: str
    purpose: str = ""
    vision: str = ""
    mission: str = ""
    positioning: str = ""
    services: str = ""
    brand: str = ""
