# custom_components/drp_climate/coordinator.py
"""
DRP Climate Master v2 — COORDINATOR
===================================

RUOLO (in breve)
----------------
Il Coordinator è l’orchestratore **data-driven**: legge sensori/entità di Home Assistant,
costruisce uno **snapshot coerente** dello stato dell’impianto (PlantSnapshot) e
fornisce un loop di controllo rapido per i regolatori locali (es. PID della miscelatrice,
controllo umidità VMC). È la **singola fonte di verità** a cui si appoggiano Supervisor
e le Entity (CoordinatorEntity).

RESPONSABILITÀ PRINCIPALI
-------------------------
1) **Acquisizione dati** (loop SLOW con DataUpdateCoordinator):
   - Legge T/H per area, T_out/H_out, T_supply/T_return, stati attuatori, richieste VMC, allarmi.
   - Calcola grandezze derivate (dew point stanza, VPD, entalpia esterna, flag free cooling).
   - Stima domanda per zona (weighted demand) e aggiorna il PlantSnapshot.

2) **Controlli locali** (loop FAST interno):
   - Applica il **PID 3-punti** della valvola miscelatrice verso `T_supply_target`.
   - Regola la **deumidifica VMC** verso `target_rh_pct` (PID H%).
   - Fa rispettare **runtime guards** (min_on/min_off) e **rate-limit** ai comandi.

3) **Persistenza configurazione**:
   - Carica la `PlantConfig` consolidata (da config_flow/options o import legacy).
   - Espone i parametri di controllo (PID, soglie dew-point, policy free-cooling).

4) **Pubblicazione stato**:
   - Mantiene `self.snapshot` (PlantSnapshot) sempre consistente.
   - Notifica gli observer (Supervisor, Entity) quando ci sono aggiornamenti validi.

5) **Affidabilità & sicurezza**:
   - Protegge da errori di I/O e mancanza di entità; applica fallback/valori sicuri.
   - Non blocca mai il thread di evento di HA (tutte le operazioni sono async).

INTERAZIONI
-----------
- Con **Adapters**: unico punto di accesso a entità HA (letture/atti). Gli Adapters 
  implementano retry/backoff, clamp e validazioni.
- Con **Supervisor**: il Supervisor legge `snapshot` e decide la strategia high-level;
  il Coordinator non decide modalità HVAC, ma fornisce dati e applica controlli locali.
- Con **ClimateEntity**: le entity ereditano da `CoordinatorEntity` e leggono lo snapshot
  per UI/telemetria.

LOOP E TEMPISTICHE
------------------
- SLOW: 30–60 s (DataUpdateCoordinator) → sensori, psicrometria, domanda zone, flags.
- FAST: 5–10 s (task interno) → PID miscelatrice, PID umidità VMC, runtime guards.

INGRESSI / USCITE
-----------------
- Ingressi: entità HA (sensori/attuatori), opzioni/parametri da config entry.
- Uscite: comandi verso attuatori (tramite Adapters), PlantSnapshot aggiornato.

INVARIANTI & LINEE GUIDA
------------------------
- Nessun **side-effect** nel metodo `_async_update_data` oltre alle letture/calcoli.
- PlantSnapshot deve essere **auto-consistente** in ogni istante.
- I comandi agli attuatori passano **solo** dagli Adapters (mai diretti dal Coordinator).
- Tutte le eccezioni del loop SLOW producono `UpdateFailed` e non interrompono il servizio.
- Il loop FAST deve essere **idempotente** e **tollerante** a snapshot parziali.

ANTI-PATTERN (da evitare)
-------------------------
- Mettere decisioni high-level (scelta modalità HVAC) qui dentro → competenza del Supervisor.
- Fare I/O bloccante o cicli `sleep` lunghi nel thread di evento.
- Accedere direttamente a `hass.services`/`hass.states` fuori dagli Adapters.
"""

from __future__ import annotations

import asyncio
import logging
import contextlib

from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Any, Optional, TYPE_CHECKING

import async_timeout
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from ..helpers.config_entries import build_runtime_config
from ..helpers.utils import _as_float, _as_bool

from ..const import DOMAIN

_LOGGER = logging.getLogger(__name__)


# ------------------------------ Setup platform ------------------------------ #
async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Crea le entity Climate a partire dal coordinator."""

# -----------------------------------------------------------------------------
# Coordinator
# -----------------------------------------------------------------------------

class ClimateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """
    Coordina:
      - Lettura sensori (ports/sensors)
      - Calcolo setpoint/decisioni (engine)   [TODO: collega il tuo motore]
      - Applicazione comandi (ports/actuators)
    Espone:
      - current_env / current_weather
      - last_setpoint / last_decision / controller_state
      - capabilities
    """

    # ------------------------ init & lifecycle ------------------------ #

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry

        # Config di runtime e adapter I/O
        self.runtime = build_runtime_config(entry)
        _LOGGER.debug("Runtime config %s", self.runtime)

        self._fast_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

        # Prepara la mappatura dei sensori per letture rapide nello SLOW loop
        self._sensors: dict[str, Any] = {
            "areas": {a.name: asdict(a.sensors) for a in self.runtime.climate.areas},
            "supply_units": asdict(self.runtime.climate.devices.supply_units.sensors),
        }

        rad = self.runtime.climate.devices.radiant
        if rad is not None:
            self._sensors["radiant"] = asdict(rad.sensors)

        vmc = self.runtime.climate.devices.vmc
        if vmc is not None:
            self._sensors["vmc"] = {
                "sensors": asdict(vmc.sensors),
                "requests": asdict(vmc.requests),
                "alarms": asdict(vmc.alarms),
            }

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}-coordinator",
            update_interval=self.runtime.update_interval,
        )

        _LOGGER.debug("__init__ end.")

    async def async_config_entry_first_refresh(self) -> None:
        """Primo refresh con gestione UpdateFailed → ConfigEntryNotReady a monte."""
        await super().async_config_entry_first_refresh()
        _LOGGER.debug("First refresh completed")

    async def _async_update_data(self) -> dict[str, Any]:
        """Acquisisce tutti i sensori configurati e restituisce uno snapshot.

        Vengono interrogati:
        - sensori di **temperatura/umidità** per ogni area;
        - sensori delle **unità di alimentazione** (supply/return diretta e modulata);
        - sensori del circuito **radiante** (se presenti);
        - sensori, richieste e allarmi della **VMC** (se presente).

        Lo snapshot è un dict strutturato come segue::

            {
                "areas": {area: {"temperature_c": float, "humidity_pct": float}},
                "supply_units": {"boiler_temp_system_supply": float, ...},
                "radiant": {"pdc_temp_water_in": float, ...},           # opzionale
                "vmc": {
                    "sensors": {"t_ambient": float, ...},
                    "requests": {"water": bool, ...},
                    "alarms": {"high_pressure": bool, ...},
                },                                                         # opzionale
            }
        """

        async def _get_state(eid: str) -> Optional[str]:
            state: State | None = self.hass.states.get(eid)
            return state.state if state else None

        jobs: list[tuple[tuple[str, ...], str]] = []
        sensors = self._sensors

        for area_name, pair in sensors["areas"].items():
            jobs.append((("areas", area_name, "temperature_c"), pair["temperature"]))
            jobs.append((("areas", area_name, "humidity_pct"), pair["humidity"]))

        for key, eid in sensors["supply_units"].items():
            jobs.append((("supply_units", key), eid))

        if "radiant" in sensors:
            for key, eid in sensors["radiant"].items():
                jobs.append((("radiant", key), eid))

        if "vmc" in sensors:
            vmc = sensors["vmc"]
            for key, eid in vmc["sensors"].items():
                jobs.append((("vmc", "sensors", key), eid))
            for key, eid in vmc["requests"].items():
                jobs.append((("vmc", "requests", key), eid))
            for key, eid in vmc["alarms"].items():
                jobs.append((("vmc", "alarms", key), eid))

        results = await asyncio.gather(*[_get_state(eid) for _, eid in jobs])

        snapshot: dict[str, Any] = {"areas": {}, "supply_units": {}}

        for (path, _), value in zip(jobs, results):
            if path[0] == "areas":
                area = snapshot["areas"].setdefault(path[1], {})
                area[path[2]] = _as_float(value)
            elif path[0] == "supply_units":
                snapshot["supply_units"][path[1]] = _as_float(value)
            elif path[0] == "radiant":
                snapshot.setdefault("radiant", {})[path[1]] = _as_float(value)
            elif path[0] == "vmc":
                vmc = snapshot.setdefault("vmc", {"sensors": {}, "requests": {}, "alarms": {}})
                if path[1] == "sensors":
                    vmc["sensors"][path[2]] = _as_float(value)
                elif path[1] == "requests":
                    vmc["requests"][path[2]] = _as_bool(value)
                else:
                    vmc["alarms"][path[2]] = _as_bool(value)

        def _check_values(d: dict[str, Any]) -> bool:
            for v in d.values():
                if isinstance(v, dict):
                    if not _check_values(v):
                        return False
                elif v is None:
                    return False
            return True

        if not _check_values(snapshot):
            raise UpdateFailed("Missing data from one or more sensors")

        self.async_set_updated_data(snapshot)
        return snapshot

    async def async_start_fast_loop(self) -> None:
        if self._fast_task:
            return
        self._stop_event.clear()
        self._fast_task = asyncio.create_task(self._fast_loop(), name="drp_fast_loop")

    async def async_stop(self) -> None:
        self._stop_event.set()
        if self._fast_task:
            self._fast_task.cancel()
            with contextlib.suppress(Exception):
                await self._fast_task
        self._fast_task = None

    async def _fast_loop(self) -> None:
        """Ciclo FAST: PID miscelatrice, H% VMC, rate-limit comandi."""
