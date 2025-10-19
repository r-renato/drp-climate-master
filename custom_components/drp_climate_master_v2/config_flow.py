# custom_components/drp_climate_master/config_flow.py
from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any, Dict, Mapping

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.config_entries import SOURCE_IMPORT
from homeassistant.const import CONF_TEMPERATURE_UNIT

# Tipi flow (compatibilità 2025.4.4 + fallback)
from homeassistant.config_entries import ConfigFlow, OptionsFlow
try:
    from homeassistant.config_entries import ConfigFlowResult  # type: ignore
except Exception:  # pragma: no cover
    from homeassistant.data_entry_flow import FlowResult as ConfigFlowResult  # type: ignore
try:
    from homeassistant.config_entries import OptionsFlowResult  # type: ignore
except Exception:  # pragma: no cover
    from homeassistant.data_entry_flow import FlowResult as OptionsFlowResult  # type: ignore

from .const import (
    DOMAIN,
    INTEGRATION_NAME,
    DEFAULT_TEMP_UNIT,
    DEFAULT_UNITS,
    # keys
    CONF_UNITS,
    CONF_WEATHER,
    CONF_HISTORICAL_DATA,
    CONF_HOME_WINDOWS_STATE,
    CONF_AREAS,
    CONF_DEVICES,
    CONF_SCENARIOS,
    CONF_MAX_TEMP,
    CONF_MIN_TEMP,
    CONF_STEP,
    # devices
    CONF_RADIANT,
    CONF_SUPPLY_UNITS,
    CONF_VMC,
    # ids
    CONF_HUB_NAME,
    CONF_CLIMATE_NAME,
    CONF_CLIMATE_UNIQUE_ID,
)
from .domain.schema import WEATHER_SCHEMA
from .helpers.config_flow import (
    normalize_yaml_hub,
    split_devices,
    assemble_devices,
    coerce_weather_latlon,
    validate_areas,
    validate_devices,
    validate_scenarios,
    validate_historical_data,
    validate_min_max,
)
from .helpers.config_flow_entries_payload import yaml_climate_to_entry_payload

from .helpers.config_flow_ui_schemas import (
    schema_user,
    schema_dynamic,
    schema_areas,
    schema_device,
    schema_weather,
    schema_historical,
    schema_advanced,
)

_LOGGER = logging.getLogger(__name__)

# Opzioni runtime (allineate al runtime_config)
OPT_UPDATE_INTERVAL_S = "update_interval_s"
OPT_SUPPORTS_HEATING = "supports_heating"
OPT_SUPPORTS_COOLING = "supports_cooling"
OPT_SUPPORTS_DEHUMIDIFYING = "supports_dehumidifying"
OPT_SETPOINT_STEP_C = "setpoint_step_c"
OPT_MANUAL_OVERRIDE_MIN = "manual_override_minutes"


class DrpClimateMasterConfigFlow(ConfigFlow, domain=DOMAIN):
    """Config Flow: solo UI orchestration e gestione import."""

    VERSION = 2

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """STEP: user — crea la entry con campi base e riferimenti globali."""
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=schema_user())

        hub_name = user_input[CONF_HUB_NAME]
        climate_name = user_input[CONF_CLIMATE_NAME]
        unique_id = user_input[CONF_CLIMATE_UNIQUE_ID]
        home_windows = user_input[CONF_HOME_WINDOWS_STATE]

        # mappatura da entity weather.* a mapping minimo compatibile con WEATHER_SCHEMA
        weather_entity = user_input[CONF_WEATHER]
        weather_block = {
            "forecast_data": {"provider": str(weather_entity)},
            "historical_data": {"provider": "pirateweather"},
        }

        # valida mapping
        try:
            weather_block = WEATHER_SCHEMA(weather_block)
        except vol.Invalid as e:
            return self.async_show_form(
                step_id="user",
                data_schema=schema_user(),
                errors={"base": f"Weather non valido: {e}"},
            )

        if unique_id:
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()

        data = {
            CONF_HUB_NAME: hub_name,
            CONF_CLIMATE_NAME: climate_name,
            CONF_CLIMATE_UNIQUE_ID: unique_id,
            CONF_HOME_WINDOWS_STATE: home_windows,
            CONF_WEATHER: dict(weather_block),
            CONF_UNITS: str(DEFAULT_UNITS),
        }
        options = {
            CONF_AREAS: [],
            CONF_DEVICES: {},
            CONF_SCENARIOS: {},
            CONF_HISTORICAL_DATA: {},
            OPT_UPDATE_INTERVAL_S: 30,
            OPT_SUPPORTS_HEATING: True,
            OPT_SUPPORTS_COOLING: False,
            OPT_SUPPORTS_DEHUMIDIFYING: False,
            OPT_SETPOINT_STEP_C: 0.5,
            OPT_MANUAL_OVERRIDE_MIN: 90,
            CONF_MAX_TEMP: 35.0,
            CONF_MIN_TEMP: 5.0,
            CONF_STEP: 0.5,
            CONF_TEMPERATURE_UNIT: str(DEFAULT_TEMP_UNIT),
        }
        return self.async_create_entry(title=f"{INTEGRATION_NAME} - {climate_name}", data=data, options=options)

    async def async_step_import(self, import_config: Dict[str, Any]) -> ConfigFlowResult:
        """STEP: import — converte YAML → ConfigEntry, evitando duplicati."""
        hubs = import_config.get(DOMAIN)
        if not hubs:
            return self.async_abort(reason="invalid_yaml")

        existing_entries = self._async_current_entries()
        if any(entry.source != SOURCE_IMPORT for entry in existing_entries):
            _LOGGER.info("%s: import YAML ignorato: configurazione UI già presente.", DOMAIN)
            return self.async_abort(reason="already_configured")
        existing_uids = {e.data.get(CONF_CLIMATE_UNIQUE_ID) for e in existing_entries if e.data}

        for hub in hubs:
            hub_norm = normalize_yaml_hub(hub)
            hub_name = hub_norm.get("name", "Unnamed Hub")
            climates = hub_norm.get("climate") or []

            for climate in climates:
                try:
                    data, options = yaml_climate_to_entry_payload(hub_name, climate)
                except Exception as exc:
                    _LOGGER.error("%s: YAML import error: %s", DOMAIN, exc)
                    return self.async_abort(reason="invalid_yaml")

                unique_id = data.get(CONF_CLIMATE_UNIQUE_ID)

                if unique_id and unique_id in existing_uids:
                    _LOGGER.info("%s: import YAML saltato: unique_id '%s' già configurato", DOMAIN, unique_id)
                    return self.async_abort(reason="already_configured")

                if unique_id:
                    await self.async_set_unique_id(unique_id)
                    self._abort_if_unique_id_configured(
                        updates={"title": f"{INTEGRATION_NAME} - {data.get(CONF_CLIMATE_NAME)}"}
                    )

                self._async_abort_entries_match(
                    {
                        CONF_CLIMATE_UNIQUE_ID: unique_id,
                        CONF_CLIMATE_NAME: data.get(CONF_CLIMATE_NAME),
                    }
                )

                return self.async_create_entry(
                    title=f"{INTEGRATION_NAME} - {data.get(CONF_CLIMATE_NAME)}",
                    data=data,
                    options=options,
                )

        return self.async_abort(reason="nothing_to_import")

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> OptionsFlow:
        return DrpClimateMasterOptionsFlowHandler(config_entry)


class DrpClimateMasterOptionsFlowHandler(OptionsFlow):
    """Options Flow: un metodo per sessione, UI label per step."""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self.entry = entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> OptionsFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "dynamic",
                "areas",
                "radiant",
                "supply_units",
                "vmc",
                "weather",
                "historical_data",
                "advanced",
            ],
        )

    async def async_step_dynamic(self, user_input: dict[str, Any] | None = None) -> OptionsFlowResult:
        cur: Mapping[str, Any] = self.entry.options
        if user_input is None:
            return self.async_show_form(step_id="dynamic", data_schema=schema_dynamic(cur))

        err = validate_min_max(user_input[CONF_MIN_TEMP], user_input[CONF_MAX_TEMP])
        if err:
            return self.async_show_form(step_id="dynamic", data_schema=schema_dynamic(cur), errors={"base": err})

        new_options: Dict[str, Any] = dict(cur)
        new_options.update(
            {
                OPT_UPDATE_INTERVAL_S: user_input[OPT_UPDATE_INTERVAL_S],
                OPT_SUPPORTS_HEATING: user_input[OPT_SUPPORTS_HEATING],
                OPT_SUPPORTS_COOLING: user_input[OPT_SUPPORTS_COOLING],
                OPT_SUPPORTS_DEHUMIDIFYING: user_input[OPT_SUPPORTS_DEHUMIDIFYING],
                OPT_SETPOINT_STEP_C: float(user_input[OPT_SETPOINT_STEP_C]),
                OPT_MANUAL_OVERRIDE_MIN: user_input[OPT_MANUAL_OVERRIDE_MIN],
                CONF_MAX_TEMP: float(user_input[CONF_MAX_TEMP]),
                CONF_MIN_TEMP: float(user_input[CONF_MIN_TEMP]),
                CONF_STEP: float(user_input[CONF_STEP]),
                CONF_TEMPERATURE_UNIT: str(user_input[CONF_TEMPERATURE_UNIT]),
            }
        )
        return self.async_create_entry(title="", data=new_options)

    async def async_step_areas(self, user_input: dict[str, Any] | None = None) -> OptionsFlowResult:
        current = deepcopy(self.entry.options.get(CONF_AREAS, []))
        if user_input is None:
            return self.async_show_form(step_id="areas", data_schema=schema_areas(current))

        areas = user_input.get(CONF_AREAS, [])
        err = validate_areas(areas)
        if err:
            return self.async_show_form(step_id="areas", data_schema=schema_areas(current), errors={"base": err})
        new_options: Dict[str, Any] = dict(self.entry.options)
        new_options[CONF_AREAS] = areas
        return self.async_create_entry(title="", data=new_options)

    async def async_step_radiant(self, user_input: dict[str, Any] | None = None) -> OptionsFlowResult:
        supply, radiant, vmc, extras = split_devices(self.entry.options.get(CONF_DEVICES))
        if user_input is None:
            return self.async_show_form(step_id="radiant", data_schema=schema_device(CONF_RADIANT, radiant))

        new_radiant = user_input.get(CONF_RADIANT, {}) or {}
        if not isinstance(new_radiant, dict):
            return self.async_show_form(step_id="radiant", data_schema=schema_device(CONF_RADIANT, radiant), errors={"base": "Il blocco radiant deve essere un oggetto."})

        devices = assemble_devices(supply, new_radiant, vmc, extras)
        err = validate_devices(devices)
        if err:
            return self.async_show_form(step_id="radiant", data_schema=schema_device(CONF_RADIANT, new_radiant), errors={"base": err})

        new_options: Dict[str, Any] = dict(self.entry.options)
        new_options[CONF_DEVICES] = devices
        return self.async_create_entry(title="", data=new_options)

    async def async_step_supply_units(self, user_input: dict[str, Any] | None = None) -> OptionsFlowResult:
        supply, radiant, vmc, extras = split_devices(self.entry.options.get(CONF_DEVICES))
        if user_input is None:
            return self.async_show_form(step_id="supply_units", data_schema=schema_device(CONF_SUPPLY_UNITS, supply))

        new_supply = user_input.get(CONF_SUPPLY_UNITS, {}) or {}
        if not isinstance(new_supply, dict):
            return self.async_show_form(step_id="supply_units", data_schema=schema_device(CONF_SUPPLY_UNITS, supply), errors={"base": "Il blocco supply_units deve essere un oggetto."})

        devices = assemble_devices(new_supply, radiant, vmc, extras)
        err = validate_devices(devices)
        if err:
            return self.async_show_form(step_id="supply_units", data_schema=schema_device(CONF_SUPPLY_UNITS, new_supply), errors={"base": err})

        new_options: Dict[str, Any] = dict(self.entry.options)
        new_options[CONF_DEVICES] = devices
        return self.async_create_entry(title="", data=new_options)

    async def async_step_vmc(self, user_input: dict[str, Any] | None = None) -> OptionsFlowResult:
        supply, radiant, vmc, extras = split_devices(self.entry.options.get(CONF_DEVICES))
        if user_input is None:
            return self.async_show_form(step_id="vmc", data_schema=schema_device(CONF_VMC, vmc))

        new_vmc = user_input.get(CONF_VMC, {}) or {}
        if not isinstance(new_vmc, dict):
            return self.async_show_form(step_id="vmc", data_schema=schema_device(CONF_VMC, vmc), errors={"base": "Il blocco vmc deve essere un oggetto."})

        devices = assemble_devices(supply, radiant, new_vmc, extras)
        err = validate_devices(devices)
        if err:
            return self.async_show_form(step_id="vmc", data_schema=schema_device(CONF_VMC, new_vmc), errors={"base": err})

        new_options: Dict[str, Any] = dict(self.entry.options)
        new_options[CONF_DEVICES] = devices
        return self.async_create_entry(title="", data=new_options)

    async def async_step_weather(self, user_input: dict[str, Any] | None = None) -> OptionsFlowResult:
        current_weather = deepcopy(self.entry.data.get(CONF_WEATHER, {}))
        if user_input is None:
            return self.async_show_form(step_id="weather", data_schema=schema_weather(current_weather))

        weather_cfg = user_input.get(CONF_WEATHER, {}) or {}
        if not isinstance(weather_cfg, dict):
            return self.async_show_form(step_id="weather", data_schema=schema_weather(current_weather), errors={"base": "Il blocco weather deve essere un oggetto."})
        try:
            validated = WEATHER_SCHEMA(weather_cfg)
            validated = coerce_weather_latlon(dict(validated))
        except Exception as err:
            return self.async_show_form(step_id="weather", data_schema=schema_weather(weather_cfg), errors={"base": str(err)})

        new_data = dict(self.entry.data)
        new_data[CONF_WEATHER] = validated
        self.hass.config_entries.async_update_entry(self.entry, data=new_data)
        return self.async_create_entry(title="", data=dict(self.entry.options))

    async def async_step_historical_data(self, user_input: dict[str, Any] | None = None) -> OptionsFlowResult:
        current_hist = deepcopy(self.entry.options.get(CONF_HISTORICAL_DATA, {}))
        if user_input is None:
            return self.async_show_form(step_id="historical_data", data_schema=schema_historical(current_hist))

        hist = user_input.get(CONF_HISTORICAL_DATA, {}) or {}
        if not isinstance(hist, dict):
            return self.async_show_form(step_id="historical_data", data_schema=schema_historical(current_hist), errors={"base": "Il blocco historical_data deve essere un oggetto."})

        err = validate_historical_data(hist)
        if err:
            return self.async_show_form(step_id="historical_data", data_schema=schema_historical(hist), errors={"base": err})

        new_options: Dict[str, Any] = dict(self.entry.options)
        new_options[CONF_HISTORICAL_DATA] = hist
        return self.async_create_entry(title="", data=new_options)

    async def async_step_advanced(self, user_input: dict[str, Any] | None = None) -> OptionsFlowResult:
        supply, radiant, vmc, extras = split_devices(self.entry.options.get(CONF_DEVICES))
        current_scenarios = deepcopy(self.entry.options.get(CONF_SCENARIOS, {}))

        if user_input is None:
            return self.async_show_form(step_id="advanced", data_schema=schema_advanced(current_scenarios, extras))

        scenarios = user_input.get(CONF_SCENARIOS, {}) or {}
        if not isinstance(scenarios, dict):
            return self.async_show_form(step_id="advanced", data_schema=schema_advanced(current_scenarios, extras), errors={"base": "Il blocco scenarios deve essere un oggetto."})
        err = validate_scenarios(scenarios)
        if err:
            return self.async_show_form(step_id="advanced", data_schema=schema_advanced(scenarios, extras), errors={"base": err})

        extra_devices = user_input.get(CONF_DEVICES, {}) or {}
        if not isinstance(extra_devices, dict):
            return self.async_show_form(step_id="advanced", data_schema=schema_advanced(scenarios, extras), errors={"base": "Il blocco devices deve essere un oggetto."})

        devices = assemble_devices(supply, radiant, vmc, extra_devices)
        err = validate_devices(devices)
        if err:
            return self.async_show_form(step_id="advanced", data_schema=schema_advanced(scenarios, extra_devices), errors={"base": err})

        new_options: Dict[str, Any] = dict(self.entry.options)
        new_options[CONF_SCENARIOS] = scenarios
        new_options[CONF_DEVICES] = devices
        return self.async_create_entry(title="", data=new_options)
