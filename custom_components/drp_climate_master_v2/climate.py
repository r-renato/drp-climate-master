# custom_components/drp_climate_master/climate.py
from __future__ import annotations

from typing import Any, Dict, List, Optional

from homeassistant.core import HomeAssistant
# from homeassistant.const import TEMP_CELSIUS
from homeassistant.const import UnitOfTemperature
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.components.climate import (
    ClimateEntity,
    # ClimateEntityFeature,
    # HVACMode,
    # HVACAction,
)
from homeassistant.components.climate.const import (
    ClimateEntityFeature,
    HVACMode,
    HVACAction,
)
from homeassistant.helpers.device_registry import DeviceInfo

from drp_climate_master_v2.controller.coordinator import DRPClimateCoordinator

from .const import (
    DOMAIN,
    DEFAULT_CLIMATE_NAME,
    COORDINATORS,
    # DEFAULT_TEMP_UNIT,
    CONF_AREAS,
    CONF_AREA,
)

# ---------- Setup tramite ConfigEntry (HA 2025.4.4) ----------
# ------------------------------ Setup platform ------------------------------ #

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Crea le entity Climate a partire dal coordinator."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: DRPClimateCoordinator = data["coordinator"]

    # Se hai più zone, costruiscile qui; per ora una singola entity
    entity = DrpClimateEntity(
        coordinator=coordinator,
        entry=entry,
    )
    async_add_entities([entity])


# async def async_setup_entry(
#     hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
# ) -> None:
#     """Crea entità Climate per questo ConfigEntry."""
#     domain_data = hass.data.get(DOMAIN, {})
#     coordinator = domain_data.get(COORDINATORS, {}).get(entry.entry_id)

#     # Estrarre aree dall'entry (options). Se mancano, creiamo una sola entity aggregata.
#     areas: List[dict] = list(entry.options.get(CONF_AREAS, [])) if entry.options else []
#     entities: List[DrpClimateEntity] = []

#     if not areas:
#         # Fallback: un'unica entità "aggregata"
#         entities.append(
#             DrpClimateEntity(
#                 coordinator=coordinator,
#                 entry=entry,
#                 zone_name=entry.data.get("climate_name", DEFAULT_CLIMATE_NAME),
#                 unique_suffix="aggregate",
#             )
#         )
#     else:
#         for area in areas:
#             name = area.get(CONF_AREA)
#             if not isinstance(name, str) or not name:
#                 # Salta voci malformate
#                 continue
#             entities.append(
#                 DrpClimateEntity(
#                     coordinator=coordinator,
#                     entry=entry,
#                     zone_name=name,
#                     unique_suffix=_slugify(name),
#                 )
#             )

#     if entities:
#         async_add_entities(entities)


# ---------- Entity ----------

class DrpClimateEntity(CoordinatorEntity[DRPClimateCoordinator], ClimateEntity):
    """Entity Climate per una singola zona/area (o aggregata)."""

    # Feature minime sicure per 2025.4.4: bersaglio temperatura
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_temperature_unit = UnitOfTemperature.CELSIUS  # coerente con DEFAULT_TEMP_UNIT "°C"
    _attr_hvac_modes = [
        HVACMode.OFF,
        HVACMode.HEAT,
        HVACMode.COOL,
        HVACMode.DRY,
        HVACMode.AUTO,
    ]

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        # self._zone = zone_name
        # self._attr_name = zone_name
        # self._attr_unique_id = f"{entry.entry_id}_{unique_suffix}"
        # cache locale come fallback se l'engine non fornisce dati
        self._fallback_target: Optional[float] = None
        self._fallback_mode: HVACMode = HVACMode.AUTO

    # ---- Device info per raggruppare le entità sotto l'integrazione
    @property
    def device_info(self) -> DeviceInfo:
        title = self._entry.data.get("climate_name") or DEFAULT_CLIMATE_NAME
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=title,
            manufacturer="DRP",
            model="Climate Master",
            configuration_url=None,
        )

    # ---- Proprietà richieste

    @property
    def hvac_mode(self) -> HVACMode:
        snap = self._get_snapshot()
        mode = snap.get("hvac_mode")
        if isinstance(mode, HVACMode):
            return mode
        # consentire anche stringhe ("heat","cool",...) da engine non ancora tipizzato
        if isinstance(mode, str):
            try:
                return HVACMode(mode)
            except Exception:
                pass
        return self._fallback_mode

    @property
    def hvac_action(self) -> HVACAction | None:
        snap = self._get_snapshot()
        action = snap.get("action")
        if isinstance(action, HVACAction):
            return action
        if isinstance(action, str):
            try:
                return HVACAction(action)
            except Exception:
                return None
        return None

    @property
    def current_temperature(self) -> float | None:
        snap = self._get_snapshot()
        val = snap.get("current_temp")
        try:
            return float(val) if val is not None else None
        except Exception:
            return None

    @property
    def target_temperature(self) -> float | None:
        snap = self._get_snapshot()
        val = snap.get("target_temp")
        if val is None:
            return self._fallback_target
        try:
            return float(val)
        except Exception:
            return self._fallback_target

    # ---- Comandi utente

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        engine = getattr(self.coordinator, "engine", None)
        if engine and hasattr(engine, "async_set_zone_hvac_mode"):
            await engine.async_set_zone_hvac_mode(self._zone, hvac_mode)
        else:
            # fallback silenzioso: memorizza in cache locale
            self._fallback_mode = hvac_mode
        # Notifica aggiornamento stato
        self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temperature = kwargs.get("temperature")
        if temperature is None:
            return
        engine = getattr(self.coordinator, "engine", None)
        if engine and hasattr(engine, "async_set_zone_target"):
            await engine.async_set_zone_target(self._zone, float(temperature))
        else:
            self._fallback_target = float(temperature)
        self.async_write_ha_state()

    # ---- Helper

    def _get_snapshot(self) -> Dict[str, Any]:
        """Recupera lo snapshot della zona dal coordinator/engine, con fallback robusto."""
        # 1) Se il coordinator non c'è (fase very-early), restituisci fallback
        if self.coordinator is None:
            return self._fallback_snapshot()

        # 2) Prova a leggere dall'engine (preferito)
        engine = getattr(self.coordinator, "engine", None)
        if engine and hasattr(engine, "async_get_zone_snapshot"):
            # L'engine potrebbe essere solo async; ma qui siamo in proprietà sync.
            # Offriamo una cache lato coordinator: `coordinator.data` deve essere aggiornato.
            pass

        # 3) Prova `coordinator.data` (es. una struttura aggregata aggiornata dal Coordinator)
        data = getattr(self.coordinator, "data", None)
        if isinstance(data, dict):
            zones = data.get("zones", {})
            snap = zones.get(self._zone)
            if isinstance(snap, dict):
                # Normalizza chiavi attese
                return {
                    "current_temp": snap.get("current_temp"),
                    "target_temp": snap.get("target_temp"),
                    "hvac_mode": snap.get("hvac_mode"),
                    "action": snap.get("action"),
                }

        # 4) Fallback: snapshot locale
        return self._fallback_snapshot()

    def _fallback_snapshot(self) -> Dict[str, Any]:
        return {
            "current_temp": None,
            "target_temp": self._fallback_target,
            "hvac_mode": self._fallback_mode,
            "action": None,
        }
        

# ---------- Util ----------

def _slugify(text: str) -> str:
    out = []
    for ch in text.lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in (" ", "-", "_"):
            out.append("_")
    slug = "".join(out).strip("_")
    return slug or "zone"
