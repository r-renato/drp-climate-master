# custom_components/drp_climate/coordinator.py
"""
DRP Climate Master v2 — COORDINATOR
===================================

RUOLO (in breve)
----------------
Il Coordinator è l'orchestratore **data-driven**: legge sensori/entità di Home Assistant,
costruisce uno **snapshot coerente** dello stato dell'impianto (PlantSnapshot) e
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
- SLOW: 30-60 s (DataUpdateCoordinator) → sensori, psicrometria, domanda zone, flags.
- FAST: 5-10 s (task interno) → PID miscelatrice, PID umidità VMC, runtime guards.

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

from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, Event, EventStateChangedData
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from ..helpers.config_entries import build_runtime_config, collect_entity_ids_for_state_changes, subscribe_entity_state_changes

from ..const import DOMAIN, ENTITIES_STATE

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
        self._hass = hass
        self._entry = entry

        # Config di runtime e adapter I/O
        self._runtime = build_runtime_config(entry)
        _LOGGER.debug("Runtime config %s", self._runtime)
        eids = collect_entity_ids_for_state_changes(self._runtime)
        subscribe_entity_state_changes(self._hass, callback=self._async_entity_changed, entity_ids=eids)

        self._fast_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}-coordinator",
            update_interval=self._runtime.update_interval,
        )

        _LOGGER.debug("__init__ end.")

    @property
    def _entities_state(self) -> dict:
        """
        Restituisce il dizionario delle entità Home Assistant attive.

        Returns:
            dict: Mappa degli stati delle entità registrate in Home Assistant.
        """
        return self._hass.data[DOMAIN][self._entry.entry_id][ENTITIES_STATE]
    
    async def _async_entity_changed(self, event: Event[EventStateChangedData]):
        """Handle sensor changes."""
        entity_id = event.data.get("entity_id")
        new_state = event.data.get("new_state")
        self._entities_state[entity_id] = new_state
        # _LOGGER.debug( "_async_entity_changed '%s' status change '%s'.", str(entity_id), str(new_state) )

    async def async_config_entry_first_refresh(self) -> None:
        """Primo refresh con gestione UpdateFailed → ConfigEntryNotReady a monte."""
        await super().async_config_entry_first_refresh()
        _LOGGER.debug("First refresh completed")

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
