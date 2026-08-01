from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from hermes.models.transition_record import TransitionRecord


@dataclass(slots=True)
class EntityLifecycleConfig:
    """Transition rules for one entity type."""

    entity_type: str
    valid_transitions: dict[str, list[str]]
    status_field: str = "status"
    id_field: str = "id"


class InvalidLifecycleTransition(Exception):
    pass


class LifecycleService:
    """Generic lifecycle transition engine (DEC-0005).

    Completely stateless.  Accepts caller-supplied EntityLifecycleConfig
    objects at construction time.  Ships with zero registered entity types.
    """

    def __init__(self, configs: dict[str, EntityLifecycleConfig]) -> None:
        self._configs = configs

    def transition(
        self,
        entity_type: str,
        entity: Any,
        new_status: str,
        triggered_by: str = "",
        reason: str = "",
    ) -> TransitionRecord:
        """Validate and apply a lifecycle transition.

        1. Validate transition against the config for *entity_type*.
        2. Mutate entity's status field in place.
        3. Create one TransitionRecord.
        4. Return that TransitionRecord.
        """
        config = self._configs[entity_type]
        current_status = getattr(entity, config.status_field)

        allowed = config.valid_transitions.get(current_status, [])
        if new_status not in allowed:
            raise InvalidLifecycleTransition(
                f"Cannot transition '{entity_type}' from '{current_status}' to '{new_status}'"
            )

        setattr(entity, config.status_field, new_status)

        entity_id = getattr(entity, config.id_field, "")

        return TransitionRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            entity_type=entity_type,
            entity_id=str(entity_id),
            from_status=current_status,
            to_status=new_status,
            triggered_by=triggered_by,
            reason=reason,
        )

    def get_allowed_transitions(
        self, entity_type: str, current_status: str
    ) -> list[str]:
        """Return the list of statuses reachable from *current_status*."""
        config = self._configs[entity_type]
        return list(config.valid_transitions.get(current_status, []))
