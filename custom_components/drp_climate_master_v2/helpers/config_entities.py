from __future__ import annotations

import logging
from typing import Iterable, Optional

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import EntityPlatform, async_get_platforms

_LOGGER = logging.getLogger(__name__)


def _platform_to_str(platform: Platform | str) -> str:
    """Converte Platform enum o string in domain string."""
    return platform.value if isinstance(platform, Platform) else str(platform)


def get_integration_platform(
    hass: HomeAssistant,
    integration_domain: str,
    platform: Platform | str,
) -> Optional[EntityPlatform]:
    """
    Ritorna l'EntityPlatform per una piattaforma (es. 'sensor', 'climate')
    appartenente a una specifica integrazione (es. 'drp_climate_master_v2').

    NOTE:
    - async_get_platforms filtra per *dominio integrazione*, NON per domain della piattaforma.
    - Se la piattaforma non è stata ancora inizializzata/forwardata, ritorna None.
    """
    platform_domain = _platform_to_str(platform)
    platforms = async_get_platforms(hass, integration_domain)  # list[EntityPlatform]

    for p in platforms:
        if p.domain == platform_domain:
            return p

    _LOGGER.debug(
        "EntityPlatform non trovata (integration=%s, platform=%s). "
        "Assicurati di aver forwardato la piattaforma con async_forward_entry_setups "
        "o di averla caricata via discovery.",
        integration_domain,
        platform_domain,
    )
    return None


async def async_platform_add_entities(
    hass: HomeAssistant,
    integration_domain: str,
    platform: Platform | str,
    entities: Iterable,
    update_before_add: bool = False,
) -> bool:
    """
    Aggiunge entità a una piattaforma dell'integrazione.

    Esempio:
        await async_platform_add_entities(
            hass,
            "drp_climate_master_v2",
            Platform.SENSOR,
            [sensor1, sensor2],
            update_before_add=False,
        )
    """
    ent_list = [e for e in entities if e is not None]
    if not ent_list:
        _LOGGER.debug(
            "Nessuna entità da aggiungere (integration=%s, platform=%s).",
            integration_domain,
            _platform_to_str(platform),
        )
        return False

    entity_platform = get_integration_platform(hass, integration_domain, platform)
    if not entity_platform:
        _LOGGER.warning(
            "Piattaforma %s non trovata per integrazione %s: nessuna entità aggiunta.",
            _platform_to_str(platform),
            integration_domain,
        )
        return False

    await entity_platform.async_add_entities(ent_list, update_before_add)
    _LOGGER.debug(
        "Aggiunte %d entità a %s.%s",
        len(ent_list),
        integration_domain,
        _platform_to_str(platform),
    )
    return True


# -----------------------------------------------------------------------------
# SHIM DI RETRO-COMPATIBILITÀ (DEPRECATO)
# -----------------------------------------------------------------------------
# Mantiene la vecchia firma:
#   async_platform_add_entities(hass, platform: str, entities: list, discovery_info=True)
# Usava 'platform' al posto del dominio integrazione → NON funzionava con HA moderno.
# Lo teniamo per non rompere subito, ma logghiamo un warning.
# -----------------------------------------------------------------------------

def get_platform(hass, name):
    """DEPRECATO: usa get_integration_platform(hass, integration_domain, platform)."""
    _LOGGER.warning(
        "get_platform(hass, %r) è deprecato. "
        "Chiama get_integration_platform(hass, integration_domain, platform).",
        name,
    )
    # Tentativo best-effort: senza integration_domain non possiamo filtrare correttamente.
    # Proviamo a cercare tra *tutte* le integrazioni note, ma è inaffidabile.
    for integ in hass.data:
        try:
            platforms = async_get_platforms(hass, integ)
        except Exception:
            continue
        for p in platforms:
            if p.domain == name:
                return p
    return None


async def async_platform_add_entities_legacy(
    hass: HomeAssistant,
    platform: str,
    entities: Iterable,
    discovery_info: bool | None = True,
) -> None:
    """
    DEPRECATO: mantieni compatibilità con vecchia firma.
    - 'platform' era il domain della piattaforma (es. 'sensor'), ma manca l'integration domain.
    - Usa la nuova async_platform_add_entities(...) passando esplicitamente l'integrazione.
    """
    _LOGGER.warning(
        "async_platform_add_entities(hass, platform, entities, discovery_info=...) è deprecato. "
        "Usa async_platform_add_entities(hass, integration_domain, platform, entities, update_before_add=False)."
    )
    # Non potendo conoscere l'integration_domain, proviamo una ricerca globale (best-effort).
    entity_platform = get_platform(hass, platform)
    if not entity_platform:
        _LOGGER.warning("Piattaforma %s non trovata (legacy).", platform)
        return

    ent_list = [e for e in entities if e is not None]
    if not ent_list:
        _LOGGER.debug("Nessuna entità da aggiungere (legacy).")
        return

    # La vecchia versione passava 'discovery_info' al posto di update_before_add:
    update_before_add = bool(discovery_info)
    await entity_platform.async_add_entities(ent_list, update_before_add)
