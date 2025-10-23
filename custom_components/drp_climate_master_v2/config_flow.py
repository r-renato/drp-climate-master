# custom_components/drp_climate_master/config_flow.py
from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any, Dict, Mapping
from homeassistant import config_entries
from homeassistant.core import callback
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
    CONF_AREAS,
    CONF_DEVICES,
    CONF_SCENARIOS,
    CONF_MAX_TEMP,
    CONF_MIN_TEMP,
    CONF_STEP,
    CONF_APT_WINDOWS,
    CONF_CONFORT_ZONES,
    CONF_STATE,
    # devices
    CONF_RADIANT,
    CONF_SUPPLY_UNITS,
    CONF_VMC,
    # ids
    CONF_HUB_NAME,
    CONF_CLIMATE_NAME,
    CONF_CLIMATE_UNIQUE_ID,
)
from .helpers.config_flow import (
    normalize_yaml_hub,
    normalize_weather_block,
    split_devices,
    assemble_devices,
    validate_areas,
    validate_devices,
    validate_scenarios,
    validate_historical_data,
    validate_apt_windows,
    validate_confort_zones,
)
from .helpers.config_flow_entries_payload import yaml_climate_to_entry_payload

from .helpers.config_flow_ui_schemas import (
    OPT_UPDATE_INTERVAL_S,
    schema_user,
    schema_dynamic,
    schema_areas,
    schema_device,
    schema_weather,
    schema_historical,
    schema_advanced,
)

_LOGGER = logging.getLogger(__name__)

class DrpClimateMasterConfigFlow(ConfigFlow, domain=DOMAIN):
    """Config Flow: orchestrazione UI e gestione import."""

    VERSION = 2

    def __init__(self) -> None:
        self._entry_data: dict[str, Any] = {}
        self._entry_options: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """STEP: user — raccoglie le informazioni principali dell'impianto."""
        if user_input is None:
            self._entry_data = {}
            self._entry_options = {}
            return self.async_show_form(step_id="user", data_schema=schema_user())

        hub_name = user_input[CONF_HUB_NAME]
        climate_name = user_input[CONF_CLIMATE_NAME]
        unique_id = user_input[CONF_CLIMATE_UNIQUE_ID]
        apt_windows_state = user_input[CONF_APT_WINDOWS]
        weather_entity = user_input[CONF_WEATHER]

        if unique_id:
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()

        self._entry_data = {
            CONF_HUB_NAME: hub_name,
            CONF_CLIMATE_NAME: climate_name,
            CONF_CLIMATE_UNIQUE_ID: unique_id,
            CONF_WEATHER: {
                "forecast_data": {"provider": str(weather_entity)},
                "historical_data": {
                    "provider": "pirateweather",
                    "token": "",
                    "latitude": "",
                    "longitude": "",
                },
            },
            CONF_UNITS: str(DEFAULT_UNITS),
            CONF_TEMPERATURE_UNIT: str(DEFAULT_TEMP_UNIT),
        }

        self._entry_options = {
            CONF_AREAS: [],
            CONF_DEVICES: {},
            CONF_SCENARIOS: {},
            CONF_HISTORICAL_DATA: {},
            CONF_APT_WINDOWS: {CONF_STATE: str(apt_windows_state)},
            CONF_CONFORT_ZONES: {},
            CONF_UNITS: str(DEFAULT_UNITS),
            CONF_TEMPERATURE_UNIT: str(DEFAULT_TEMP_UNIT),
            OPT_UPDATE_INTERVAL_S: 30,
        }

        return await self.async_step_dynamic()

    async def async_step_dynamic(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """STEP: dynamic — parametri climatici generali."""
        if user_input is None:
            return self.async_show_form(
                step_id="dynamic",
                data_schema=schema_dynamic(self._entry_options, self._entry_data),
            )

        new_options: Dict[str, Any] = dict(self._entry_options)
        new_options.update(
            {
                OPT_UPDATE_INTERVAL_S: user_input[OPT_UPDATE_INTERVAL_S],
                CONF_UNITS: str(user_input[CONF_UNITS]),
                CONF_TEMPERATURE_UNIT: str(user_input[CONF_TEMPERATURE_UNIT]),
            }
        )

        self._entry_options = new_options
        self._entry_data[CONF_UNITS] = str(user_input[CONF_UNITS])
        self._entry_data[CONF_TEMPERATURE_UNIT] = str(user_input[CONF_TEMPERATURE_UNIT])

        return await self.async_step_areas()

    async def async_step_areas(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """STEP: areas — configurazione delle zone."""
        current = deepcopy(self._entry_options.get(CONF_AREAS, []))
        if user_input is None:
            return self.async_show_form(step_id="areas", data_schema=schema_areas(current))

        areas = user_input.get(CONF_AREAS, [])
        err = validate_areas(areas)
        if err:
            return self.async_show_form(
                step_id="areas",
                data_schema=schema_areas(current),
                errors={"base": err},
            )

        self._entry_options[CONF_AREAS] = areas
        return await self.async_step_supply_units()

    async def async_step_supply_units(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """STEP: supply_units — configurazione del generatore."""
        supply, radiant, vmc, extras = split_devices(self._entry_options.get(CONF_DEVICES))
        if user_input is None:
            return self.async_show_form(
                step_id="supply_units",
                data_schema=schema_device(CONF_SUPPLY_UNITS, supply),
            )

        new_supply = user_input.get(CONF_SUPPLY_UNITS, {}) or {}
        if not isinstance(new_supply, dict):
            return self.async_show_form(
                step_id="supply_units",
                data_schema=schema_device(CONF_SUPPLY_UNITS, supply),
                errors={"base": "Il blocco supply_units deve essere un oggetto."},
            )

        devices = assemble_devices(new_supply, radiant, vmc, extras)
        err = validate_devices(devices)
        if err:
            return self.async_show_form(
                step_id="supply_units",
                data_schema=schema_device(CONF_SUPPLY_UNITS, new_supply),
                errors={"base": err},
            )

        self._entry_options[CONF_DEVICES] = devices
        return await self.async_step_radiant()

    async def async_step_radiant(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """STEP: radiant — configurazione radiante (opzionale)."""
        supply, radiant, vmc, extras = split_devices(self._entry_options.get(CONF_DEVICES))
        if user_input is None:
            return self.async_show_form(
                step_id="radiant",
                data_schema=schema_device(CONF_RADIANT, radiant),
            )

        new_radiant = user_input.get(CONF_RADIANT, {}) or {}
        if not isinstance(new_radiant, dict):
            return self.async_show_form(
                step_id="radiant",
                data_schema=schema_device(CONF_RADIANT, radiant),
                errors={"base": "Il blocco radiant deve essere un oggetto."},
            )

        devices = assemble_devices(supply, new_radiant, vmc, extras)
        err = validate_devices(devices)
        if err:
            return self.async_show_form(
                step_id="radiant",
                data_schema=schema_device(CONF_RADIANT, new_radiant),
                errors={"base": err},
            )

        self._entry_options[CONF_DEVICES] = devices
        return await self.async_step_vmc()

    async def async_step_vmc(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """STEP: vmc — configurazione ventilazione meccanica (opzionale)."""
        supply, radiant, vmc, extras = split_devices(self._entry_options.get(CONF_DEVICES))
        if user_input is None:
            return self.async_show_form(step_id="vmc", data_schema=schema_device(CONF_VMC, vmc))

        new_vmc = user_input.get(CONF_VMC, {}) or {}
        if not isinstance(new_vmc, dict):
            return self.async_show_form(
                step_id="vmc",
                data_schema=schema_device(CONF_VMC, vmc),
                errors={"base": "Il blocco vmc deve essere un oggetto."},
            )

        devices = assemble_devices(supply, radiant, new_vmc, extras)
        err = validate_devices(devices)
        if err:
            return self.async_show_form(
                step_id="vmc",
                data_schema=schema_device(CONF_VMC, new_vmc),
                errors={"base": err},
            )

        self._entry_options[CONF_DEVICES] = devices
        return await self.async_step_weather()

    async def async_step_weather(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """STEP: weather — configurazione dati meteo."""
        current_weather = deepcopy(self._entry_data.get(CONF_WEATHER, {}))
        if user_input is None:
            return self.async_show_form(step_id="weather", data_schema=schema_weather(current_weather))

        weather_cfg = user_input.get(CONF_WEATHER, {}) or {}
        error, normalized = normalize_weather_block(weather_cfg)
        if error:
            return self.async_show_form(
                step_id="weather",
                data_schema=schema_weather(weather_cfg),
                errors={"base": error},
            )

        self._entry_data[CONF_WEATHER] = normalized
        return await self.async_step_historical_data()

    async def async_step_historical_data(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """STEP: historical_data — configurazione sorgenti storiche esterne."""
        current_hist = deepcopy(self._entry_options.get(CONF_HISTORICAL_DATA, {}))
        if user_input is None:
            return self.async_show_form(
                step_id="historical_data",
                data_schema=schema_historical(current_hist),
            )

        hist = user_input.get(CONF_HISTORICAL_DATA, {}) or {}
        if not isinstance(hist, dict):
            return self.async_show_form(
                step_id="historical_data",
                data_schema=schema_historical(current_hist),
                errors={"base": "Il blocco historical_data deve essere un oggetto."},
            )

        err = validate_historical_data(hist)
        if err:
            return self.async_show_form(
                step_id="historical_data",
                data_schema=schema_historical(hist),
                errors={"base": err},
            )

        self._entry_options[CONF_HISTORICAL_DATA] = hist
        return await self.async_step_advanced()

    async def async_step_advanced(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """STEP: advanced — scenari, finestre e device aggiuntivi."""
        supply, radiant, vmc, extras = split_devices(self._entry_options.get(CONF_DEVICES))
        current_scenarios = deepcopy(self._entry_options.get(CONF_SCENARIOS, {}))
        current_apt = deepcopy(self._entry_options.get(CONF_APT_WINDOWS, {}))
        current_confort = deepcopy(self._entry_options.get(CONF_CONFORT_ZONES, {}))

        if user_input is None:
            return self.async_show_form(
                step_id="advanced",
                data_schema=schema_advanced(current_scenarios, extras, current_apt, current_confort),
            )

        scenarios = user_input.get(CONF_SCENARIOS, {}) or {}
        if not isinstance(scenarios, dict):
            return self.async_show_form(
                step_id="advanced",
                data_schema=schema_advanced(current_scenarios, extras, current_apt, current_confort),
                errors={"base": "Il blocco scenarios deve essere un oggetto."},
            )
        err = validate_scenarios(scenarios)
        if err:
            return self.async_show_form(
                step_id="advanced",
                data_schema=schema_advanced(scenarios, extras, current_apt, current_confort),
                errors={"base": err},
            )

        apt_windows = user_input.get(CONF_APT_WINDOWS) or {}
        err = validate_apt_windows(apt_windows)
        if err:
            return self.async_show_form(
                step_id="advanced",
                data_schema=schema_advanced(scenarios, extras, apt_windows, current_confort),
                errors={"base": err},
            )

        confort_zones = user_input.get(CONF_CONFORT_ZONES) or {}
        err = validate_confort_zones(confort_zones)
        if err:
            return self.async_show_form(
                step_id="advanced",
                data_schema=schema_advanced(scenarios, extras, apt_windows, confort_zones),
                errors={"base": err},
            )

        extra_devices = user_input.get(CONF_DEVICES, {}) or {}
        if not isinstance(extra_devices, dict):
            return self.async_show_form(
                step_id="advanced",
                data_schema=schema_advanced(scenarios, extras, apt_windows, confort_zones),
                errors={"base": "Il blocco devices deve essere un oggetto."},
            )

        devices = assemble_devices(supply, radiant, vmc, extra_devices)
        err = validate_devices(devices)
        if err:
            return self.async_show_form(
                step_id="advanced",
                data_schema=schema_advanced(scenarios, extra_devices, apt_windows, confort_zones),
                errors={"base": err},
            )

        self._entry_options[CONF_SCENARIOS] = scenarios
        self._entry_options[CONF_DEVICES] = devices
        self._entry_options[CONF_APT_WINDOWS] = apt_windows
        self._entry_options[CONF_CONFORT_ZONES] = confort_zones

        climate_name = self._entry_data.get(CONF_CLIMATE_NAME, "")
        title = f"{INTEGRATION_NAME} - {climate_name}" if climate_name else INTEGRATION_NAME
        return self.async_create_entry(title=title, data=self._entry_data, options=self._entry_options)

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

    def _entry_title(self, suffix: str) -> str:
        climate_name = self.entry.data.get(CONF_CLIMATE_NAME)
        base = f"{INTEGRATION_NAME} - {climate_name}" if climate_name else INTEGRATION_NAME
        return f"{base} ({suffix})"

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
            return self.async_show_form(step_id="dynamic", data_schema=schema_dynamic(cur, self.entry.data))

        new_options: Dict[str, Any] = dict(cur)
        for deprecated in (
            CONF_MAX_TEMP,
            CONF_MIN_TEMP,
            CONF_STEP,
            "setpoint_step_c",
            "manual_override_minutes",
            "supports_heating",
            "supports_cooling",
            "supports_dehumidifying",
        ):
            new_options.pop(deprecated, None)
        new_options.update(
            {
                OPT_UPDATE_INTERVAL_S: user_input[OPT_UPDATE_INTERVAL_S],
                CONF_UNITS: str(user_input[CONF_UNITS]),
                CONF_TEMPERATURE_UNIT: str(user_input[CONF_TEMPERATURE_UNIT]),
            }
        )
        new_data: Dict[str, Any] = dict(self.entry.data)
        new_data[CONF_UNITS] = str(user_input[CONF_UNITS])
        new_data[CONF_TEMPERATURE_UNIT] = str(user_input[CONF_TEMPERATURE_UNIT])
        self.hass.config_entries.async_update_entry(self.entry, data=new_data)
        return self.async_create_entry(title=self._entry_title("impostazioni dinamiche"), data=new_options)

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
        return self.async_create_entry(title=self._entry_title("aree aggiornate"), data=new_options)

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
        return self.async_create_entry(title=self._entry_title("radiant aggiornato"), data=new_options)

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
        return self.async_create_entry(title=self._entry_title("supply units aggiornati"), data=new_options)

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
        return self.async_create_entry(title=self._entry_title("vmc aggiornato"), data=new_options)

    async def async_step_weather(self, user_input: dict[str, Any] | None = None) -> OptionsFlowResult:
        current_weather = deepcopy(self.entry.data.get(CONF_WEATHER, {}))
        if user_input is None:
            return self.async_show_form(step_id="weather", data_schema=schema_weather(current_weather))

        weather_cfg = user_input.get(CONF_WEATHER, {}) or {}
        if not isinstance(weather_cfg, dict):
            return self.async_show_form(
                step_id="weather",
                data_schema=schema_weather(current_weather),
                errors={"base": "Il blocco weather deve essere un oggetto."},
            )

        error, normalized = normalize_weather_block(weather_cfg)
        if error:
            return self.async_show_form(
                step_id="weather",
                data_schema=schema_weather(weather_cfg),
                errors={"base": error},
            )

        new_data = dict(self.entry.data)
        new_data[CONF_WEATHER] = normalized
        self.hass.config_entries.async_update_entry(self.entry, data=new_data)
        return self.async_create_entry(title=self._entry_title("meteo aggiornato"), data=dict(self.entry.options))

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
        return self.async_create_entry(title=self._entry_title("dati storici aggiornati"), data=new_options)

    async def async_step_advanced(self, user_input: dict[str, Any] | None = None) -> OptionsFlowResult:
        supply, radiant, vmc, extras = split_devices(self.entry.options.get(CONF_DEVICES))
        current_scenarios = deepcopy(self.entry.options.get(CONF_SCENARIOS, {}))
        current_apt = deepcopy(self.entry.options.get(CONF_APT_WINDOWS, {}))
        current_confort = deepcopy(self.entry.options.get(CONF_CONFORT_ZONES, {}))

        if user_input is None:
            return self.async_show_form(
                step_id="advanced",
                data_schema=schema_advanced(current_scenarios, extras, current_apt, current_confort),
            )

        scenarios = user_input.get(CONF_SCENARIOS, {}) or {}
        if not isinstance(scenarios, dict):
            return self.async_show_form(
                step_id="advanced",
                data_schema=schema_advanced(current_scenarios, extras, current_apt, current_confort),
                errors={"base": "Il blocco scenarios deve essere un oggetto."},
            )
        err = validate_scenarios(scenarios)
        if err:
            return self.async_show_form(
                step_id="advanced",
                data_schema=schema_advanced(scenarios, extras, current_apt, current_confort),
                errors={"base": err},
            )

        apt_windows = user_input.get(CONF_APT_WINDOWS) or {}
        err = validate_apt_windows(apt_windows)
        if err:
            return self.async_show_form(
                step_id="advanced",
                data_schema=schema_advanced(scenarios, extras, apt_windows, current_confort),
                errors={"base": err},
            )

        confort_zones = user_input.get(CONF_CONFORT_ZONES) or {}
        err = validate_confort_zones(confort_zones)
        if err:
            return self.async_show_form(
                step_id="advanced",
                data_schema=schema_advanced(scenarios, extras, apt_windows, confort_zones),
                errors={"base": err},
            )

        extra_devices = user_input.get(CONF_DEVICES, {}) or {}
        if not isinstance(extra_devices, dict):
            return self.async_show_form(
                step_id="advanced",
                data_schema=schema_advanced(scenarios, extras, apt_windows, confort_zones),
                errors={"base": "Il blocco devices deve essere un oggetto."},
            )

        devices = assemble_devices(supply, radiant, vmc, extra_devices)
        err = validate_devices(devices)
        if err:
            return self.async_show_form(
                step_id="advanced",
                data_schema=schema_advanced(scenarios, extra_devices, apt_windows, confort_zones),
                errors={"base": err},
            )

        new_options: Dict[str, Any] = dict(self.entry.options)
        new_options[CONF_SCENARIOS] = scenarios
        new_options[CONF_DEVICES] = devices
        new_options[CONF_APT_WINDOWS] = apt_windows
        new_options[CONF_CONFORT_ZONES] = confort_zones
        return self.async_create_entry(title=self._entry_title("avanzate aggiornate"), data=new_options)
