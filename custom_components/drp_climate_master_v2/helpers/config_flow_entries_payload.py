# custom_components/drp_climate_master/payload.py
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Mapping, Tuple

import voluptuous as vol

from homeassistant.const import (
    CONF_TEMPERATURE_UNIT,
)

from ..const import (
    CONF_AREAS,
    CONF_CLIMATE_NAME,
    CONF_CLIMATE_UNIQUE_ID,
    CONF_DEVICES,
    CONF_HUB_NAME,
    CONF_SCENARIOS,
    CONF_HISTORICAL_DATA,
    CONF_HOME_WINDOWS_STATE,
    CONF_WEATHER,
    CONF_UNITS,
    CONF_MAX_TEMP,
    CONF_MIN_TEMP,
    CONF_STEP,
    DEFAULT_TEMP_UNIT,
    DEFAULT_UNITS,
    CONF_APT_WINDOWS,
    CONF_CONFORT_ZONES,
)
from ..domain.schema import BASE_CLIMATE_SCHEMA, WEATHER_SCHEMA
from .config_flow import (
    coerce_weather_latlon,
    validate_areas,
    validate_devices,
    validate_scenarios,
    validate_historical_data,
    validate_min_max,
    validate_apt_windows,
    validate_confort_zones,
)

# Opzioni runtime (allineate al runtime_config)
OPT_UPDATE_INTERVAL_S = "update_interval_s"
OPT_SUPPORTS_HEATING = "supports_heating"
OPT_SUPPORTS_COOLING = "supports_cooling"
OPT_SUPPORTS_DEHUMIDIFYING = "supports_dehumidifying"
OPT_SETPOINT_STEP_C = "setpoint_step_c"
OPT_MANUAL_OVERRIDE_MIN = "manual_override_minutes"

def yaml_climate_to_entry_payload(hub_name: str, climate: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Converte un blocco 'climate' YAML in (entry.data, entry.options).
    """
    try:
        normalized_climate = BASE_CLIMATE_SCHEMA(climate)
    except vol.Invalid as exc:
        raise ValueError(f"Blocco climate non valido: {exc}") from exc

    normalized_climate = deepcopy(normalized_climate)

    climate_name = normalized_climate.get("name")
    uid = normalized_climate.get("unique_id")

    areas = normalized_climate.get(CONF_AREAS, [])
    devices = normalized_climate.get(CONF_DEVICES, {}) or {}
    scenarios = normalized_climate.get(CONF_SCENARIOS, {})
    historical_data_cfg = normalized_climate.get(CONF_HISTORICAL_DATA, {})
    home_windows_state = normalized_climate.get(CONF_HOME_WINDOWS_STATE)
    weather = normalized_climate.get(CONF_WEATHER)
    apt_windows = normalized_climate.get(CONF_APT_WINDOWS, {})
    confort_zones = normalized_climate.get(CONF_CONFORT_ZONES, {})

    # Parametri climatici (con default come nello schema)
    max_temp = normalized_climate.get(CONF_MAX_TEMP, 35.0)
    min_temp = normalized_climate.get(CONF_MIN_TEMP, 5.0)
    step = normalized_climate.get(CONF_STEP, 0.5)
    temp_unit = normalized_climate.get(CONF_TEMPERATURE_UNIT, DEFAULT_TEMP_UNIT)
    units = normalized_climate.get(CONF_UNITS, DEFAULT_UNITS)

    # Validazioni minime
    if home_windows_state is None:
        raise ValueError("Manca 'home_windows_state' (obbligatorio).")
    if weather is None:
        raise ValueError("Manca 'weather' (obbligatorio).")

    for fn, payload in (
        (validate_areas, areas),
        (validate_devices, devices),
        (validate_scenarios, scenarios),
        (validate_historical_data, historical_data_cfg),
        (validate_apt_windows, apt_windows),
        (validate_confort_zones, confort_zones),
    ):
        err = fn(payload)  # type: ignore[arg-type]
        if err:
            raise ValueError(err)

    err = validate_min_max(min_temp, max_temp)
    if err:
        raise ValueError(err)

    # Valida lo shape di weather e normalizza lat/lon
    weather = WEATHER_SCHEMA(weather)
    weather = coerce_weather_latlon(dict(weather))

    data: Dict[str, Any] = {
        CONF_HUB_NAME: hub_name,
        CONF_CLIMATE_NAME: climate_name,
        CONF_CLIMATE_UNIQUE_ID: uid,
        CONF_HOME_WINDOWS_STATE: home_windows_state,
        CONF_WEATHER: weather,
        CONF_UNITS: str(units),
    }

    options: Dict[str, Any] = {
        CONF_AREAS: deepcopy(areas),
        CONF_DEVICES: deepcopy(devices),
        CONF_SCENARIOS: deepcopy(scenarios),
        CONF_HISTORICAL_DATA: deepcopy(historical_data_cfg),
        CONF_APT_WINDOWS: deepcopy(apt_windows),
        CONF_CONFORT_ZONES: deepcopy(confort_zones),
        # runtime defaults
        OPT_UPDATE_INTERVAL_S: 30,
        OPT_SUPPORTS_HEATING: True,
        OPT_SUPPORTS_COOLING: False,
        OPT_SUPPORTS_DEHUMIDIFYING: False,
        OPT_SETPOINT_STEP_C: 0.5,
        OPT_MANUAL_OVERRIDE_MIN: 90,
        # parametri climatici
        CONF_MAX_TEMP: float(max_temp),
        CONF_MIN_TEMP: float(min_temp),
        CONF_STEP: float(step),
        CONF_TEMPERATURE_UNIT: str(temp_unit),
        CONF_UNITS: str(units),
    }
    return data, options
