# custom_components/drp_climate/__init__.py
from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry, SOURCE_IMPORT
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import ( # es.: DOMAIN="drp_climate", PLATFORMS=[Platform.CLIMATE]
    DOMAIN,
    PLATFORMS,
    COORDINATORS,
  )
from .domain.schema import (
    CONFIG_SCHEMA,  # Importa lo schema di configurazione
    CLIMATE_SCHEMA,  # Importa lo schema per la sezione climate
)
from .controller.coordinator import DRPClimateCoordinator

_LOGGER = logging.getLogger(__name__)

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Entry point quando Home Assistant legge configuration.yaml.

    - Prepara hass.data[DOMAIN]
    - Se trova la sezione YAML del dominio, innesca il flow di IMPORT,
      che convertirà la YAML in un ConfigEntry (gestito poi da async_setup_entry).
    """
    hass.data.setdefault(DOMAIN, {COORDINATORS: {}, "yaml": []})

    domain_cfg = config.get(DOMAIN)
    if not domain_cfg:
        _LOGGER.debug("%s: nessuna configurazione YAML trovata (ok).", DOMAIN)
        return True

    # Conserva la YAML grezza (può tornare utile per debug/diagnostica)
    hass.data[DOMAIN]["yaml"] = domain_cfg

    _LOGGER.info("%s: configurazione YAML rilevata, avvio import flow…", DOMAIN)
    # Avvia l’import: passerà dentro config_flow.async_step_import(...)
    # Passiamo l’intero 'config' così il flow può leggere la chiave DOMAIN.
    await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_IMPORT},
        data=config,
    )
    _LOGGER.info("%s: configurazione YAML importata.", DOMAIN)    
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Configura l'integrazione da un ConfigEntry (UI o import da YAML)."""
    # Lazy import per evitare cicli durante lo sviluppo
    from .controller.coordinator import DRPClimateCoordinator

    domain_data = hass.data.setdefault(DOMAIN, {})
    coordinators = domain_data.setdefault(COORDINATORS, {})

    # Istanzia e avvia il Coordinator legato a questo entry
    coordinator: DRPClimateCoordinator = DRPClimateCoordinator(hass=hass, entry=entry)
    # Se il tuo coordinator espone runtime_config, popolalo in __init__ o qui
    # es: coordinator.runtime_config = build_runtime_config_from_options(entry.options)
    await coordinator.async_config_entry_first_refresh()
    coordinators[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _LOGGER.info("%s: setup entry '%s' completato.", DOMAIN, entry.entry_id)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Smonta piattaforme e risorse per un ConfigEntry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        try:
            coordinator = hass.data[DOMAIN][COORDINATORS].pop(entry.entry_id, None)
            if coordinator and hasattr(coordinator, "async_close"):
                await coordinator.async_close()
        except KeyError:
            pass

        _LOGGER.info("%s: unload entry %s completato.", DOMAIN, entry.entry_id)
    else:
        _LOGGER.warning("%s: unload entry %s non riuscito.", DOMAIN, entry.entry_id)

    return unload_ok


async def _options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Triggera un reload quando cambiano le Options da UI."""
    _LOGGER.debug("Options updated for %s → reloading", entry.entry_id)
    await hass.config_entries.async_reload(entry.entry_id)


# (Opzionale) migrazione versioni di config entry
# async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
#     """Migra i dati dell'entry tra versioni (se modelli/options cambiano)."""
#     version = entry.version
#     data: dict[str, Any] = {**entry.data}
#     options: dict[str, Any] = {**entry.options}

#     # Esempio di migrazione: v1 → v2 rinomina di una option
#     if version == 1:
#         if "hysteresis" in options and "temp_hysteresis" not in options:
#             options["temp_hysteresis"] = options.pop("hysteresis")
#         entry.version = 2
#         hass.config_entries.async_update_entry(entry, data=data, options=options)
#         _LOGGER.info("Migrated %s entry_id=%s from v1 to v2", DOMAIN, entry.entry_id)

#     return True
