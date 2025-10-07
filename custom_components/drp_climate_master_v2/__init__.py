# custom_components/drp_climate/__init__.py
from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry, SOURCE_IMPORT
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.typing import ConfigType

from .const import ( # es.: DOMAIN="drp_climate", PLATFORMS=[Platform.CLIMATE]
    DOMAIN,
    ENTITIES_STATE,
    PLATFORMS,
    COORDINATOR,
    SUPERVISOR,
  )
from .domain.schema import (
    CONFIG_SCHEMA,  # Importa lo schema di configurazione
    CLIMATE_SCHEMA,  # Importa lo schema per la sezione climate
)
from .controller.coordinator import ClimateCoordinator
from .controller.supervisor import ClimateSupervisor

_LOGGER = logging.getLogger(__name__)

from homeassistant.config_entries import SOURCE_IMPORT

async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Entry point quando Home Assistant legge configuration.yaml.

    - Prepara hass.data[DOMAIN]
    - Se trova la sezione YAML del dominio, innesca il flow di IMPORT
      SENZA attenderlo (no await), così non blocchiamo il setup.
    """
    # Inizializza lo store del dominio
    store = hass.data.setdefault(DOMAIN, {})

    # Recupera la sezione YAML del dominio (se assente, nulla da fare)
    domain_cfg = config.get(DOMAIN)
    if not domain_cfg:
        return True

    # Se esiste già almeno una ConfigEntry, non rilanciare import
    if hass.config_entries.async_entries(DOMAIN):
        _LOGGER.info("%s: entry già presente; salto import YAML", DOMAIN)
        return True

    # Evita di lanciare più volte l'import nello stesso avvio
    if store.get("_import_started"):
        _LOGGER.debug("%s: import YAML già avviato; skip", DOMAIN)
        return True
    store["_import_started"] = True

    # (opzionale) conserva la YAML grezza per diagnostica
    store["yaml"] = domain_cfg

    _LOGGER.info("%s: configurazione YAML rilevata, avvio import flow…", DOMAIN)

    # Avvia l’import in background (NON await!)
    # Passiamo SOLO la sezione del dominio, incapsulata sotto DOMAIN
    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_IMPORT},
            data={DOMAIN: domain_cfg},
        )
    )

    # Non attendere il flow: lascia completare il setup del dominio
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Configura l'integrazione da un ConfigEntry (UI o import da YAML)."""
    # Lazy import per evitare cicli durante lo sviluppo
    from .controller.coordinator import ClimateCoordinator
    from .controller.supervisor import ClimateSupervisor

    # Evita di mantenere attive più entry quando esiste già una configurazione UI.
    if entry.source == SOURCE_IMPORT:
        active_entries = [
            existing
            for existing in hass.config_entries.async_entries(DOMAIN)
            if existing.entry_id != entry.entry_id and not existing.disabled_by
        ]
        if active_entries:
            _LOGGER.info(
                "%s: entry %s (import) ignorata perché esiste già una configurazione attiva (%s).",
                DOMAIN,
                entry.entry_id,
                active_entries[0].entry_id,
            )
            hass.async_create_task(
                hass.config_entries.async_remove(entry.entry_id)
            )
            return True
    else:
        redundant_imports = [
            existing
            for existing in hass.config_entries.async_entries(DOMAIN)
            if existing.entry_id != entry.entry_id
            and existing.source == SOURCE_IMPORT
            and not existing.disabled_by
        ]
        for redundant in redundant_imports:
            _LOGGER.info(
                "%s: rimuovo entry import %s in favore dell'entry UI %s.",
                DOMAIN,
                redundant.entry_id,
                entry.entry_id,
            )
            removed = await hass.config_entries.async_remove(redundant.entry_id)
            if not removed:
                _LOGGER.warning(
                    "%s: impossibile rimuovere l'entry import %s.",
                    DOMAIN,
                    redundant.entry_id,
                )

    # Istanzia e avvia il Coordinator legato a questo entry
    coordinator: ClimateCoordinator = ClimateCoordinator(hass=hass, entry=entry)
    # Se il tuo coordinator espone runtime_config, popolalo in __init__ o qui
    # es: coordinator.runtime_config = build_runtime_config_from_options(entry.options)
    await coordinator.async_config_entry_first_refresh()

    supervisor: ClimateSupervisor = ClimateSupervisor(hass=hass, coordinator=coordinator)
    await supervisor.async_start()

    hass.data[DOMAIN].setdefault(entry.entry_id, {})
    hass.data[DOMAIN][entry.entry_id][COORDINATOR] = coordinator
    hass.data[DOMAIN][entry.entry_id][SUPERVISOR] = supervisor
    hass.data[DOMAIN][entry.entry_id].setdefault(ENTITIES_STATE, {})

    # Piattaforme (climate, sensor, ecc.)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reload su change delle options
    entry.async_on_unload(entry.add_update_listener(_options_updated))

    _LOGGER.info("%s: setup entry '%s' completato.", DOMAIN, entry.entry_id)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Smonta piattaforme e risorse per un ConfigEntry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    store = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if store:
        supervisor = store.get(SUPERVISOR)
        coordinator = store.get(COORDINATOR)

        # Stop supervisor (chiude listener/task decisionali)
        if supervisor and hasattr(supervisor, "async_stop"):
            await supervisor.async_stop()

        # Stop coordinator (chiude fast loop se presente)
        for method in ("async_stop", "async_close"):
            if coordinator and hasattr(coordinator, method):
                await getattr(coordinator, method)()
                break

    if unload_ok:
        _LOGGER.info("%s: unload entry %s completato.", DOMAIN, entry.entry_id)
    else:
        _LOGGER.warning("%s: unload entry %s non riuscito.", DOMAIN, entry.entry_id)

    return unload_ok


async def _options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload quando cambiano le Options da UI."""
    _LOGGER.debug("%s: options aggiornate per %s → reload", DOMAIN, entry.entry_id)
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
