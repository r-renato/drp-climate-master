# custom_components/drp_climate_master_v2/domain/models.py
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, List

# -------------------- High-level operating enums -------------------- #

class HVACOperatingProfile(Enum):
    """Preset/Profilo operativo esposto nel Climate."""
    COMFORT = "comfort"
    ECO = "eco"
    # AWAY = "away"
    VACATION = "vacation"
    
    @classmethod
    def values(cls) -> list[str]:
        """Restituisce i valori come lista di stringhe (es. ['comfort', 'eco', ...])."""
        return [m.value for m in cls]

    