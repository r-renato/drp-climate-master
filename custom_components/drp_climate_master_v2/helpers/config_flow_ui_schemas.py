# custom_components/drp_climate_master/ui_schemas.py
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

import voluptuous as vol
from homeassistant.helpers import selector
from homeassistant.const import (
    CONF_TEMPERATURE_UNIT,
)
from ..const import (
    CONF_AREAS,
    CONF_DEVICES,
    CONF_SCENARIOS,
    CONF_HISTORICAL_DATA,
    CONF_WEATHER,
    CONF_HOME_WINDOWS_STATE,
    CONF_UNITS,
    CONF_MAX_TEMP,
    CONF_MIN_TEMP,
    CONF_STEP,
    DEFAULT_TEMP_UNIT,
    DEFAULT_UNITS,
    CONF_HUB_NAME,
    CONF_CLIMATE_NAME,
    CONF_CLIMATE_UNIQUE_ID,
    CONF_APT_WINDOWS,
    CONF_CONFORT_ZONES,
)
# Opzioni runtime
OPT_UPDATE_INTERVAL_S = "update_interval_s"
OPT_SUPPORTS_HEATING = "supports_heating"
OPT_SUPPORTS_COOLING = "supports_cooling"
OPT_SUPPORTS_DEHUMIDIFYING = "supports_dehumidifying"
OPT_SETPOINT_STEP_C = "setpoint_step_c"
OPT_MANUAL_OVERRIDE_MIN = "manual_override_minutes"

def schema_user() -> vol.Schema:
    """Form iniziale (user)."""
    return vol.Schema(
        {
            vol.Required(CONF_HUB_NAME): str,
            vol.Required(CONF_CLIMATE_NAME): str,
            vol.Required(CONF_CLIMATE_UNIQUE_ID): str,
            vol.Required(CONF_HOME_WINDOWS_STATE): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="binary_sensor")
            ),
            vol.Required(CONF_WEATHER): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="weather")
            ),
        }
    )

def schema_dynamic(cur: Mapping[str, Any], entry_data: Mapping[str, Any]) -> vol.Schema:
    units_default = str(cur.get(CONF_UNITS, entry_data.get(CONF_UNITS, "")) or DEFAULT_UNITS)
    temp_unit_default = str(cur.get(CONF_TEMPERATURE_UNIT, entry_data.get(CONF_TEMPERATURE_UNIT, DEFAULT_TEMP_UNIT)) or DEFAULT_TEMP_UNIT)
    units_selector = selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=["si", "metric", "imperial"],
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )
    temp_unit_selector = selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=["°C", "°F"],
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )
    return vol.Schema(
        {
            vol.Required(OPT_UPDATE_INTERVAL_S, default=cur.get(OPT_UPDATE_INTERVAL_S, 30)): vol.All(int, vol.Range(min=5, max=3600)),
            vol.Required(OPT_SUPPORTS_HEATING, default=cur.get(OPT_SUPPORTS_HEATING, True)): bool,
            vol.Required(OPT_SUPPORTS_COOLING, default=cur.get(OPT_SUPPORTS_COOLING, False)): bool,
            vol.Required(OPT_SUPPORTS_DEHUMIDIFYING, default=cur.get(OPT_SUPPORTS_DEHUMIDIFYING, False)): bool,
            vol.Required(OPT_SETPOINT_STEP_C, default=cur.get(OPT_SETPOINT_STEP_C, 0.5)): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=2.0)),
            vol.Required(OPT_MANUAL_OVERRIDE_MIN, default=cur.get(OPT_MANUAL_OVERRIDE_MIN, 90)): vol.All(int, vol.Range(min=5, max=720)),
            vol.Required(CONF_MAX_TEMP, default=cur.get(CONF_MAX_TEMP, 35.0)): vol.Coerce(float),
            vol.Required(CONF_MIN_TEMP, default=cur.get(CONF_MIN_TEMP, 5.0)): vol.Coerce(float),
            vol.Required(CONF_STEP, default=cur.get(CONF_STEP, 0.5)): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=2.0)),
            vol.Required(CONF_UNITS, default=units_default): units_selector,
            vol.Required(CONF_TEMPERATURE_UNIT, default=temp_unit_default): temp_unit_selector,
        }
    )

def schema_areas(current: list[Any]) -> vol.Schema:
    return vol.Schema({vol.Required(CONF_AREAS, default=current): selector.ObjectSelector()})

def schema_device(key: str, current: Mapping[str, Any]) -> vol.Schema:
    return vol.Schema({vol.Required(key, default=deepcopy(dict(current))): selector.ObjectSelector()})

def schema_weather(current: Mapping[str, Any]) -> vol.Schema:
    return vol.Schema({vol.Required(CONF_WEATHER, default=deepcopy(dict(current))): selector.ObjectSelector()})

def schema_historical(current: Mapping[str, Any]) -> vol.Schema:
    return vol.Schema({vol.Required(CONF_HISTORICAL_DATA, default=deepcopy(dict(current))): selector.ObjectSelector()})

def schema_advanced(
    scenarios: Mapping[str, Any],
    extras: Mapping[str, Any],
    apt_windows: Mapping[str, Any],
    confort_zones: Mapping[str, Any],
) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_SCENARIOS, default=deepcopy(dict(scenarios))): selector.ObjectSelector(),
            vol.Optional(CONF_APT_WINDOWS, default=deepcopy(dict(apt_windows))): selector.ObjectSelector(),
            vol.Optional(CONF_CONFORT_ZONES, default=deepcopy(dict(confort_zones))): selector.ObjectSelector(),
            vol.Required(CONF_DEVICES, default=deepcopy(dict(extras))): selector.ObjectSelector(),
        }
    )
