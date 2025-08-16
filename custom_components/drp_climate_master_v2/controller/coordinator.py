# custom_components/drp_climate/coordinator.py
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional, TYPE_CHECKING

import async_timeout
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from ..helpers.config_entries import build_runtime_config

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

class DRPClimateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
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
        """
        1) Legge sensori
        2) Calcola setpoint
        3) Decide azioni
        4) Applica ai plant/valvole
        """

        return {}