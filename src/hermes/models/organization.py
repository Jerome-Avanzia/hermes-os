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
    values: str = ""
    target_customers: str = ""
    tone_of_voice: str = ""
    visual_identity: str = ""
    site_map: str = ""
    homepage_copy: str = ""
