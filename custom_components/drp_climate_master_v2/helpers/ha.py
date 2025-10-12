#
from __future__ import annotations
from typing import Any

def get_entity_value(entities_state: dict, entity_id: str | None) -> Any | None:
    """Restituisce lo stato di un'entità HA dallo store delle entità, o None se non disponibile."""
    if not entity_id:
        return None
    
    if entity_id not in entities_state:
        return None

    return entities_state[entity_id].state


