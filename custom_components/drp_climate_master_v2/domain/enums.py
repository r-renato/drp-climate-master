# custom_components/drp_climate_master_v2/domain/models.py
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, StrEnum, unique
from typing import Optional, Dict, List

# -------------------- High-level operating enums -------------------- #

class HVACOperatingProfile(Enum):
    """Preset/Profilo operativo esposto nel Climate."""
    COMFORT = "Comfort"
    ECO = "Eco"
    # AWAY = "away"
    VACATION = "Vacation"
    
    @classmethod
    def values(cls) -> list[str]:
        """Restituisce i valori come lista di stringhe (es. ['comfort', 'eco', ...])."""
        return [m.value for m in cls]

# -------------------- Season enums -------------------- #
@unique
class Seasons(StrEnum):
    """Meteorological seasons."""
    WINTER = "winter"
    SPRING = "spring"
    SUMMER = "summer"
    AUTUMN = "autumn"

    def __str__(self) -> str:
        """Rappresentazione leggibile in italiano."""
        mapping = {
            Seasons.WINTER: "Winter",
            Seasons.SPRING: "Spring",
            Seasons.SUMMER: "Summer",
            Seasons.AUTUMN: "Autumn",
        }
        return mapping.get(self, self.value)