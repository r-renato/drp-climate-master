"""Support for DRP Climate."""
from __future__ import annotations

import logging
from typing import cast
from datetime import timedelta
from asyncio import Lock

import voluptuous as vol

import psychrolib

from homeassistant.core import HomeAssistant

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.typing import ConfigType

import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity_component import EntityComponent

from homeassistant.components.climate.const import DOMAIN as ENTITY_DOMAIN_CLIMATE

from homeassistant.util.hass_dict import HassKey

from homeassistant.const import Platform
from homeassistant.const import (
    CONF_NAME,
    # CONF_FRIENDLY_NAME,
    # CONF_SENSORS,
    # CONF_UNIQUE_ID,
    # CONF_TEMPERATURE_UNIT,
)

# from custom_components.drp_climate_master.climate_core import async_climate_core_setup
from .controller import Controller
from .utils.const import (
    DATA_COMPONENT,
    DOMAIN,
    HUB_COMPONENT,
    STATES,
    SCAN_INTERVAL,
)

from .utils.schema import (
    CONFIG_SCHEMA,
    CLIMATE_SCHEMA,
)

from .climate import HomeClimateMaster
# from .climate_core import DevicesHub, async_climate_core_setup


_LOGGER = logging.getLogger(__name__)

# SCAN_INTERVAL = timedelta(seconds=60)

PLATFORMS = [Platform.CLIMATE]
config_entry_update_listener_lock = Lock()
psychrolib.SetUnitSystem(psychrolib.SI)

async def async_setup(hass: HomeAssistant, config: ConfigType):
    """Set up this integration using YAML is not supported."""

    hass.data[DOMAIN] = {}
    hass.data[DOMAIN][STATES] = {}

    component = hass.data[DOMAIN][DATA_COMPONENT] = EntityComponent[HomeClimateMaster](
        _LOGGER, DOMAIN, hass, SCAN_INTERVAL
    )

    # _LOGGER.info("__init__async_setup '%s' %s", str(DOMAIN), str(config[DOMAIN]))

    await component.async_setup(config)

    hass.data[DOMAIN][HUB_COMPONENT] = {}
    for conf_domain in config[DOMAIN]:
        _LOGGER.info( '__init__async_setup %s starting setup.', conf_domain[CONF_NAME] )
        
        hass.data[DOMAIN][HUB_COMPONENT][conf_domain[CONF_NAME]] = Controller(hass, config[DOMAIN][0])

    # return await async_climate_core_setup(
    #     hass,
    #     config,
    # )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up DRP Climate from a config entry."""
    # hass.data[DOMAIN] = {}
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(config_entry_update_listener))
    return True


async def config_entry_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    async with config_entry_update_listener_lock:
        await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    """Reload the config entry."""
    await async_unload_entry(hass, config_entry)
    await async_setup_entry(hass, config_entry)
