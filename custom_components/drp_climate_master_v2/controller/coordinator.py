# custom_components/drp_climate/coordinator.py
from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, Event, EventStateChangedData, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.const import PERCENTAGE

from ..helpers.config_entries import (
    build_runtime_config,
    collect_entity_ids_for_state_changes,
    subscribe_entity_state_changes,
)
from ..const import DOMAIN, ENTITIES_STATE, NAME_AREA_HOME

_LOGGER = logging.getLogger(__name__)


class ClimateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """
    Coordina:
      - Lettura sensori (ports/sensors)
      - Calcolo grandezze derivate (psicrometria, domanda, flag)
      - Pubblicazione snapshot per Entity/Supervisor
    NON decide la strategia HVAC (competenza del Supervisor).
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._entry = entry

        # Inizializza la struttura dati una volta e tieni il riferimento
        domain_store = hass.data.setdefault(DOMAIN, {})
        entry_store = domain_store.setdefault(entry.entry_id, {})
        entry_store.setdefault(ENTITIES_STATE, {})  # dict[str, State]
        self._entities_state_store: dict = entry_store[ENTITIES_STATE]

        # Config di runtime e subscribe ai cambi di stato
        self._runtime = build_runtime_config(entry)
        # _LOGGER.debug("Runtime config %s", self._runtime)

        eids = collect_entity_ids_for_state_changes(self._runtime)
        # Conserva l'unsubscribe per lo stop/unload
        self._unsub_state_changes = subscribe_entity_state_changes(
            self._hass, callback=self.entity_changed, entity_ids=eids
        )

        # Loop FAST
        self._fast_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}-coordinator",
            update_interval=self._runtime.update_interval,  # loop SLOW
        )

        _LOGGER.debug("ClimateCoordinator initialized")

    # ----------------- Accesso allo store condiviso ----------------- #

    @property
    def _entities_state(self) -> dict:
        """
        Mappa entity_id -> State (idempotente anche se hass.data viene ricreato).
        """
        domain_store = self._hass.data.setdefault(DOMAIN, {})
        entry_store = domain_store.setdefault(self._entry.entry_id, {})
        return entry_store.setdefault(ENTITIES_STATE, self._entities_state_store)

    # ----------------- Setup delle entity "slave" ------------------- #

    def build_slave_sensor_defs(self) -> list[dict[str, Any]]:
        """Restituisce la lista dei sensori dew-point da creare (name/sensors/unit)."""
        defs: list[dict[str, Any]] = []

        temps: list[str] = []
        humis: list[str] = []
        
        for area in getattr(self._runtime.climate, "areas", []):
            if getattr(area, "indoor", False):
                sensors = getattr(area, "sensors", None)
                if sensors:
                    temps.append(sensors.temperature)
                    humis.append(sensors.humidity)
                    defs.append(
                        {
                            "type" : "DewpointSensor",
                            "name": f"Ambient {area.name}",
                            "sensors": sensors,  # es. SensorPair o dict compatibile
                            "unit": self._runtime.climate.temperature_unit,
                        }
                    )
                    defs.append(
                        {
                            "type" : "HeatIndexSensor",
                            "name": f"Ambient {area.name}",
                            "sensors": sensors,  # es. SensorPair o dict compatibile
                            "unit": self._runtime.climate.temperature_unit,
                        }
                    )

        defs.append(
            {
                "type" : "CurrentTemperatureSensor",
                "name": f"Ambient {NAME_AREA_HOME}",
                "temp_sensors": temps,
                "unit": self._runtime.climate.temperature_unit,
            }
        )
        defs.append(
            {
                "type" : "CurrentHumiditySensor",
                "name": f"Ambient {NAME_AREA_HOME}",
                "humi_sensors": humis,
                "unit": PERCENTAGE,
            }
        )
        defs.append(
            {
                "type" : "CurrentDewpointSensor",
                "name": f"Ambient {NAME_AREA_HOME}",
                "temp_sensors": temps,
                "humi_sensors": humis,
                "unit": self._runtime.climate.temperature_unit,
            }
        )
        defs.append(
            {
                "type" : "CurrentHeatIndexSensor",
                "name": f"Ambient {NAME_AREA_HOME}",
                "temp_sensors": temps,
                "humi_sensors": humis,
                "unit": self._runtime.climate.temperature_unit,
            }
        )

        _LOGGER.debug("build_climate_sensor_defs: %d definizioni", len(defs))
        return defs

    # async def async_setup_slave_entities(self) -> List[Any]:
    #     """
    #     Crea e registra le entity "slave" (es. sensori di dew-point per area).
    #     """
    #     slave_sensors = []

    #     # Presumo che self._runtime.climate.areas sia una lista di oggetti con
    #     # attributi: .indoor (bool), .name (str), .sensors (compatibile con DewpointSensor)
    #     for area in getattr(self._runtime.climate, "areas", []):
    #         if not getattr(area, "indoor", False):
    #             continue

    #         sensors = getattr(area, "sensors", None)
    #         if not sensors:
    #             _LOGGER.debug("Area '%s' senza sensors; salto", getattr(area, "name", "?"))
    #             continue

    #         entity_name = f"Ambient {area.name}"
    #         temperature_unit = self._runtime.climate.temperature_unit

    #         slave_sensors.append(
    #             DewpointSensor(
    #                 hass=self._hass,
    #                 coordinator=self,
    #                 entry=self._entry,
    #                 name=entity_name,
    #                 sensors=sensors,
    #                 temperature_unit=temperature_unit,
    #             )
    #         )

    #     return slave_sensors
    # ---------------------- Event handling -------------------------- #

    @callback
    def entity_changed(self, event: Event[EventStateChangedData]) -> None:
        """Gestisce variazioni di stato sensori/attuatori sottoscritti."""
        if getattr(self, "_stop_event", None) and self._stop_event.is_set():
            return

        entity_id = event.data.get("entity_id")
        new_state = event.data.get("new_state")
        if not entity_id or new_state is None:
            return

        try:
            self._entities_state[entity_id] = new_state
            # _LOGGER.debug("State changed: %s -> %s", entity_id, new_state.state)
        except Exception as ex:  # estrema difesa: non far mai esplodere il job
            _LOGGER.debug("Ignore state change for %s (%s)", entity_id, ex)

        self.async_set_updated_data({})

    # ---------------------- Lifecycle hooks ------------------------- #

    async def async_config_entry_first_refresh(self) -> None:
        """Primo refresh: dopo il SLOW loop, avvia il FAST loop."""
        await super().async_config_entry_first_refresh()
        _LOGGER.debug("First refresh completed")
        await self.async_start_fast_loop()

    async def async_start_fast_loop(self) -> None:
        """Avvia il loop FAST (PID miscelatrice / H% VMC / rate limit)."""
        if self._fast_task:
            return
        self._stop_event.clear()
        self._fast_task = asyncio.create_task(self._fast_loop(), name="drp_fast_loop")

    async def async_stop(self) -> None:
        """Stop coordinato del loop FAST e unsubscription eventi."""
        self._stop_event.set()

        if self._fast_task:
            self._fast_task.cancel()
            with contextlib.suppress(Exception):
                await self._fast_task
            self._fast_task = None

        # Unsubscribe ai cambi stato se presente
        unsub = getattr(self, "_unsub_state_changes", None)
        if callable(unsub):
            with contextlib.suppress(Exception):
                unsub()

    async def _fast_loop(self) -> None:
        """
        Ciclo FAST: esegue controlli locali con cadenza breve.
        Deve essere idempotente e tollerante a snapshot parziali.
        """
        interval = getattr(self._runtime, "fast_interval", 5)  # fallback 5s
        try:
            while not self._stop_event.is_set():
                # TODO: PID miscelatrice verso T_supply_target
                # TODO: PID deumidifica VMC verso target_rh_pct
                # TODO: rate-limit, min_on/min_off, guardie runtime
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass

    # --------------------- DataUpdateCoordinator -------------------- #

    async def _async_update_data(self) -> dict[str, Any]:
        """
        Loop SLOW: raccoglie sensori, calcola grandezze derivate e aggiorna lo snapshot.
        Importante: niente side-effect (niente comandi agli attuatori).
        """
        try:
            # TODO: leggere da adapters e costruire snapshot parziale
            # Esempio:
            # snapshot = {
            #     "timestamp": self._hass.helpers.event.async_call_later(...),
            #     "areas": {...},
            # }
            return {}
        except Exception as exc:
            raise UpdateFailed(f"Update failed: {exc}") from exc
