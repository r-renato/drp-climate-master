# custom_components/drp_climate_master/config_flow.py
from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any, Dict, Optional, Mapping

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers import config_validation as cv
from homeassistant.config_entries import SOURCE_IMPORT
from homeassistant.const import (
    CONF_NAME,
    CONF_UNIQUE_ID,
    CONF_TEMPERATURE_UNIT,
)

# Tipi flow: garantiamo compatibilità (2025.4.4 e fallback)
from homeassistant.config_entries import ConfigFlow, OptionsFlow

from .domain.schema import (
    WEATHER_SCHEMA,
    BASE_CLIMATE_SCHEMA,
    HISTORICAL_DATA_SCHEMA,
)
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
    # Chiavi schema / YAML
    CONF_AREA,
    CONF_AREAS,
    CONF_DEVICES,
    CONF_SCENARIOS,
    CONF_HOME_WINDOWS_STATE,
    CONF_WEATHER,
    CONF_TEMPERATURE,
    CONF_HUMIDITY,
    CONF_VACATION,
    CONF_NOBODYSIN,
    CONF_MAX_TEMP,
    CONF_MIN_TEMP,
    CONF_STEP,
    CONF_UNITS,
    DEFAULT_TEMP_UNIT,
    DEFAULT_UNITS,
    # Weather nested keys
    CONF_FORECAST_DATA,
    CONF_HISTORICAL_DATA,
    CONF_PROVIDER,
    CONF_TOKEN,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    # Devices
    CONF_SUPPLY_UNITS,
    CONF_RADIANT,
    CONF_VMC,
)


_LOGGER = logging.getLogger(__name__)

# -------------------------
# Chiavi dati / opzioni (flow)
# -------------------------
CONF_HUB_NAME = "hub_name"
CONF_CLIMATE_NAME = "climate_name"
CONF_CLIMATE_UNIQUE_ID = "climate_unique_id"

# Opzioni runtime (allineate al runtime_config)
OPT_UPDATE_INTERVAL_S = "update_interval_s"
OPT_SUPPORTS_HEATING = "supports_heating"
OPT_SUPPORTS_COOLING = "supports_cooling"
OPT_SUPPORTS_DEHUMIDIFYING = "supports_dehumidifying"
OPT_SETPOINT_STEP_C = "setpoint_step_c"
OPT_MANUAL_OVERRIDE_MIN = "manual_override_minutes"


# -------------------------
# Utilità di normalizzazione/validazione
# -------------------------
def _normalize_yaml_hub(hub: Dict[str, Any]) -> Dict[str, Any]:
    """Normalizza un blocco HUB della YAML in un oggetto coerente."""
    name = hub.get(CONF_NAME) or hub.get("name")
    climates = hub.get("climate") or []
    if not isinstance(climates, list):
        climates = [climates]
    return {CONF_NAME: name, "climate": climates}


def _validate_areas(areas: list[dict]) -> Optional[str]:
    """Validazione conforme allo schema: sensors.temperature E sensors.humidity sono obbligatori."""
    if not isinstance(areas, list):
        return "Il campo 'areas' deve essere una lista."
    seen: set[str] = set()
    for a in areas:
        if not isinstance(a, dict):
            return "Ogni area deve essere un oggetto."
        n = a.get(CONF_AREA)
        if not n or not isinstance(n, str):
            return "Ogni area deve avere 'area' (stringa)."
        if n in seen:
            return f"Area duplicata: {n}"
        seen.add(n)

        sens = a.get("sensors")
        if not isinstance(sens, dict):
            return f"L'area '{n}' deve avere 'sensors'."
        if CONF_TEMPERATURE not in sens or CONF_HUMIDITY not in sens:
            return f"L'area '{n}' deve avere sensors.temperature E sensors.humidity."
    return None


def _validate_devices(dev: dict) -> Optional[str]:
    """Validazione minima dei blocchi devices (profonda demandata al runtime builder)."""
    if dev is None:
        return None
    if not isinstance(dev, dict):
        return "Il campo 'devices' deve essere un oggetto."
    for blk in ("supply_units", "radiant", "vmc"):
        if blk in dev and not isinstance(dev[blk], dict):
            return f"'devices.{blk}' deve essere un oggetto."
    return None


def _validate_scenarios(sc: dict) -> Optional[str]:
    """Validazione conforme allo schema: vacation e nobodysin obbligatori (stringhe)."""
    if sc is None:
        return "Il campo 'scenarios' è obbligatorio."
    if not isinstance(sc, dict):
        return "Il campo 'scenarios' deve essere un oggetto."
    for key in (CONF_VACATION, CONF_NOBODYSIN):
        v = sc.get(key)
        if not isinstance(v, str) or not v:
            return f"'scenarios.{key}' è obbligatorio e deve essere una stringa."
    return None


def _validate_historical_data(hist: dict | None) -> Optional[str]:
    """Valida il blocco historical_data opzionale secondo lo schema dedicato."""
    if hist is None:
        return "Il campo 'historical_data' è obbligatorio."
    if not isinstance(hist, dict):
        return "Il campo 'historical_data' deve essere un oggetto."
    try:
        HISTORICAL_DATA_SCHEMA(hist)
    except vol.Invalid as err:
        return f"'historical_data' non valido: {err}"
    return None


def _coerce_weather_latlon(weather: dict) -> dict:
    """Converte latitude/longitude in float se presenti."""
    if not isinstance(weather, dict):
        return weather
    hist = weather.get(CONF_HISTORICAL_DATA) or {}
    if not isinstance(hist, dict):
        return weather
    if CONF_LATITUDE in hist:
        try:
            hist[CONF_LATITUDE] = float(hist[CONF_LATITUDE])
        except (TypeError, ValueError):
            raise ValueError("historical_data.latitude deve essere numerico.")
    if CONF_LONGITUDE in hist:
        try:
            hist[CONF_LONGITUDE] = float(hist[CONF_LONGITUDE])
        except (TypeError, ValueError):
            raise ValueError("historical_data.longitude deve essere numerico.")
    weather[CONF_HISTORICAL_DATA] = hist
    return weather


def _split_devices(devices: Mapping[str, Any] | None) -> tuple[dict, dict, dict, dict]:
    """Ritorna le sottosezioni note di devices + eventuali restanti."""
    base: dict[str, Any] = {}
    if isinstance(devices, Mapping):
        base = {k: deepcopy(v) for k, v in devices.items()}
    supply = base.pop(CONF_SUPPLY_UNITS, {}) if base else {}
    radiant = base.pop(CONF_RADIANT, {}) if base else {}
    vmc = base.pop(CONF_VMC, {}) if base else {}
    # quanto resta rappresenta gli altri blocchi devices
    extras = base if base else {}
    if not isinstance(extras, dict):  # salvaguardia, ma dovrebbe già essere dict
        extras = dict(extras)
    return (
        supply if isinstance(supply, dict) else {},
        radiant if isinstance(radiant, dict) else {},
        vmc if isinstance(vmc, dict) else {},
        extras,
    )


def _assemble_devices(
    supply: Mapping[str, Any] | None,
    radiant: Mapping[str, Any] | None,
    vmc: Mapping[str, Any] | None,
    extras: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Ricompone il blocco devices mantenendo l'ordine logico."""
    new_devices: dict[str, Any] = {}
    if extras:
        new_devices.update(deepcopy(dict(extras)))
    if supply:
        new_devices[CONF_SUPPLY_UNITS] = deepcopy(dict(supply))
    if radiant:
        new_devices[CONF_RADIANT] = deepcopy(dict(radiant))
    if vmc:
        new_devices[CONF_VMC] = deepcopy(dict(vmc))
    return new_devices


def _yaml_climate_to_entry_payload(hub_name: str, climate: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Converte un blocco 'climate' YAML in (entry.data, entry.options).

    data:
      - hub_name, climate_name, climate_unique_id, home_windows_state, weather (mapping)
    options:
      - strutture complesse: areas, devices, scenarios
      - runtime defaults: update_interval_s, supports_*, setpoint_step_c, manual_override_minutes
      - parametri climatici: max/min/step/temperature_unit
    """
    try:
        normalized_climate = BASE_CLIMATE_SCHEMA(climate)
    except vol.Invalid as exc:
        raise ValueError(f"Blocco climate non valido: {exc}") from exc

    normalized_climate = deepcopy(normalized_climate)

    climate_name = normalized_climate.get(CONF_NAME)
    uid = normalized_climate.get(CONF_UNIQUE_ID)

    areas = normalized_climate.get(CONF_AREAS, [])
    devices = normalized_climate.get(CONF_DEVICES, {})
    scenarios = normalized_climate.get(CONF_SCENARIOS, {})
    historical_data_cfg = normalized_climate.get(CONF_HISTORICAL_DATA, {})
    home_windows_state = normalized_climate.get(CONF_HOME_WINDOWS_STATE)
    weather = normalized_climate.get(CONF_WEATHER)

    if devices is None:
        devices = {}

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
    err = _validate_areas(areas)
    if err:
        raise ValueError(err)
    err = _validate_devices(devices)
    if err:
        raise ValueError(err)
    err = _validate_scenarios(scenarios)
    if err:
        raise ValueError(err)
    err = _validate_historical_data(historical_data_cfg)
    if err:
        raise ValueError(err)

    # Coerenza minima max/min
    try:
        max_t = float(max_temp)
        min_t = float(min_temp)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("max_temp/min_temp devono essere numerici.") from exc
    if min_t >= max_t:
        raise ValueError("min_temp deve essere < max_temp.")

    # ✅ Valida lo shape di weather e normalizza lat/lon
    try:
        weather = WEATHER_SCHEMA(weather)
    except vol.Invalid as e:
        raise ValueError(f"Weather non valido: {e}") from e
    weather = _coerce_weather_latlon(dict(weather))

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
        # runtime defaults
        OPT_UPDATE_INTERVAL_S: 30,
        OPT_SUPPORTS_HEATING: True,
        OPT_SUPPORTS_COOLING: False,
        OPT_SUPPORTS_DEHUMIDIFYING: False,
        OPT_SETPOINT_STEP_C: 0.5,
        OPT_MANUAL_OVERRIDE_MIN: 90,
        # parametri climatici
        CONF_MAX_TEMP: max_t,
        CONF_MIN_TEMP: min_t,
        CONF_STEP: float(step),
        CONF_TEMPERATURE_UNIT: str(temp_unit),
    }
    return data, options


# -------------------------
# Config Flow (UI + Import)
# -------------------------
class DrpClimateMasterConfigFlow(ConfigFlow, domain=DOMAIN):
    """Gestisce il Config Flow di DRP Climate Master."""

    VERSION = 2

    def __init__(self) -> None:
        self._stored_user_input: Dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Step iniziale da UI: crea un entry con campi base e riferimenti globali."""
        if user_input is not None:
            hub_name = user_input[CONF_HUB_NAME]
            climate_name = user_input[CONF_CLIMATE_NAME]
            unique_id = user_input[CONF_CLIMATE_UNIQUE_ID]
            home_windows = user_input[CONF_HOME_WINDOWS_STATE]

            # UI: l'utente seleziona l'entity 'weather.*' → incapsuliamo nel mapping coerente con WEATHER_SCHEMA
            weather_entity = user_input[CONF_WEATHER]
            weather_block = {
                CONF_FORECAST_DATA: {CONF_PROVIDER: str(weather_entity)},
                # Preimpostiamo historical con provider base (l'utente completerà via YAML/aggiornamenti)
                CONF_HISTORICAL_DATA: {CONF_PROVIDER: "pirateweather"},
            }

            # ✅ valida il mapping costruito
            try:
                weather_block = WEATHER_SCHEMA(weather_block)
            except vol.Invalid as e:
                data_schema = self._user_schema()
                return self.async_show_form(
                    step_id="user",
                    data_schema=data_schema,
                    errors={"base": f"Weather non valido: {e}"},
                )

            # Unicità basata su unique_id del climate (se fornita)
            if unique_id:
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

            data = {
                CONF_HUB_NAME: hub_name,
                CONF_CLIMATE_NAME: climate_name,
                CONF_CLIMATE_UNIQUE_ID: unique_id,
                CONF_HOME_WINDOWS_STATE: home_windows,
                CONF_WEATHER: dict(weather_block),  # mapping coerente con YAML
                CONF_UNITS: str(DEFAULT_UNITS),
            }

            # Valori di default coerenti con schema (le strutture si editano in Options)
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
                # parametri climatici (default schema)
                CONF_MAX_TEMP: 35.0,
                CONF_MIN_TEMP: 5.0,
                CONF_STEP: 0.5,
                CONF_TEMPERATURE_UNIT: str(DEFAULT_TEMP_UNIT),
            }

            return self.async_create_entry(
                title=f"{INTEGRATION_NAME} - {climate_name}",
                data=data,
                options=options,
            )

        return self.async_show_form(step_id="user", data_schema=self._user_schema())

    def _user_schema(self) -> vol.Schema:
        """Schema per lo step user."""
        return vol.Schema(
            {
                vol.Required(CONF_HUB_NAME): str,
                vol.Required(CONF_CLIMATE_NAME): str,
                vol.Required(CONF_CLIMATE_UNIQUE_ID): str,
                vol.Required(CONF_HOME_WINDOWS_STATE): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="binary_sensor")
                ),
                # UI: l'utente seleziona un entity_id weather.*; lo incapsuliamo in un mapping compatibile con WEATHER_SCHEMA
                vol.Required(CONF_WEATHER): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="weather")
                ),
            }
        )

    async def async_step_import(self, import_config: Dict[str, Any]) -> ConfigFlowResult:
        """Import da YAML: converte la YAML in uno (o più) ConfigEntry, evitando duplicati."""
        hubs = import_config.get(DOMAIN)
        if not hubs:
            return self.async_abort(reason="invalid_yaml")

        # Lista delle entry già presenti per questo dominio
        existing_entries = self._async_current_entries()
        if any(entry.source != SOURCE_IMPORT for entry in existing_entries):
            _LOGGER.info(
                "%s: import YAML ignorato perché esiste già una configurazione tramite UI.",
                DOMAIN,
            )
            return self.async_abort(reason="already_configured")
        existing_uids = {e.data.get(CONF_CLIMATE_UNIQUE_ID) for e in existing_entries if e.data}

        # Importiamo il PRIMO climate valido trovato (comportamento standard HA)
        for hub in hubs:
            hub_norm = _normalize_yaml_hub(hub)
            hub_name = hub_norm.get(CONF_NAME, "Unnamed Hub")
            climates = hub_norm.get("climate") or []

            for climate in climates:
                try:
                    data, options = _yaml_climate_to_entry_payload(hub_name, climate)
                except Exception as exc:
                    _LOGGER.error("%s: YAML import error: %s", DOMAIN, exc)
                    return self.async_abort(reason="invalid_yaml")

                unique_id = data.get(CONF_CLIMATE_UNIQUE_ID)

                # 🔒 Guard esplicito: se esiste già una entry con lo stesso UID, non creare duplicati
                if unique_id and unique_id in existing_uids:
                    _LOGGER.info(
                        "%s: import YAML saltato: unique_id '%s' è già configurato",
                        DOMAIN, unique_id
                    )
                    return self.async_abort(reason="already_configured")

                # Imposta l'UID dell'entry ed abort se già configurata (con eventuale update del titolo)
                if unique_id:
                    await self.async_set_unique_id(unique_id)
                    self._abort_if_unique_id_configured(
                        updates={"title": f"{INTEGRATION_NAME} - {data.get(CONF_CLIMATE_NAME)}"}
                    )

                # Ulteriore protezione: match su (name, uid)
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

        # Nessun climate valido trovato nell'input
        return self.async_abort(reason="nothing_to_import")

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> OptionsFlow:
        return DrpClimateMasterOptionsFlowHandler(config_entry)


# -------------------------
# Options Flow
# -------------------------
class DrpClimateMasterOptionsFlowHandler(OptionsFlow):
    """Gestisce le opzioni suddivise per categorie funzionali."""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self.entry = entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> OptionsFlowResult:
        """Menu iniziale delle opzioni organizzato per categorie."""
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
        """Parametri dinamici/runtime e climatici."""
        cur: Mapping[str, Any] = self.entry.options

        if user_input is not None:
            try:
                max_t = float(user_input[CONF_MAX_TEMP])
                min_t = float(user_input[CONF_MIN_TEMP])
            except Exception:  # noqa: BLE001
                return self.async_show_form(
                    step_id="dynamic",
                    data_schema=self._dynamic_schema(cur),
                    errors={"base": "Valori non numerici per min/max temp."},
                )
            if min_t >= max_t:
                return self.async_show_form(
                    step_id="dynamic",
                    data_schema=self._dynamic_schema(cur),
                    errors={"base": "min_temp deve essere < max_temp."},
                )

            new_options: Dict[str, Any] = dict(cur)
            new_options[OPT_UPDATE_INTERVAL_S] = user_input[OPT_UPDATE_INTERVAL_S]
            new_options[OPT_SUPPORTS_HEATING] = user_input[OPT_SUPPORTS_HEATING]
            new_options[OPT_SUPPORTS_COOLING] = user_input[OPT_SUPPORTS_COOLING]
            new_options[OPT_SUPPORTS_DEHUMIDIFYING] = user_input[OPT_SUPPORTS_DEHUMIDIFYING]
            new_options[OPT_SETPOINT_STEP_C] = float(user_input[OPT_SETPOINT_STEP_C])
            new_options[OPT_MANUAL_OVERRIDE_MIN] = user_input[OPT_MANUAL_OVERRIDE_MIN]
            new_options[CONF_MAX_TEMP] = max_t
            new_options[CONF_MIN_TEMP] = min_t
            new_options[CONF_STEP] = float(user_input[CONF_STEP])
            new_options[CONF_TEMPERATURE_UNIT] = str(user_input[CONF_TEMPERATURE_UNIT])

            return self.async_create_entry(title="", data=new_options)

        return self.async_show_form(step_id="dynamic", data_schema=self._dynamic_schema(cur))

    def _dynamic_schema(self, cur: Mapping[str, Any]) -> vol.Schema:
        unit_default = str(cur.get(CONF_TEMPERATURE_UNIT, DEFAULT_TEMP_UNIT)).upper()
        temp_unit_selector = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=["C", "F"],
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )

        return vol.Schema(
            {
                vol.Required(
                    OPT_UPDATE_INTERVAL_S,
                    default=cur.get(OPT_UPDATE_INTERVAL_S, 30),
                ): vol.All(int, vol.Range(min=5, max=3600)),
                vol.Required(
                    OPT_SUPPORTS_HEATING,
                    default=cur.get(OPT_SUPPORTS_HEATING, True),
                ): bool,
                vol.Required(
                    OPT_SUPPORTS_COOLING,
                    default=cur.get(OPT_SUPPORTS_COOLING, False),
                ): bool,
                vol.Required(
                    OPT_SUPPORTS_DEHUMIDIFYING,
                    default=cur.get(OPT_SUPPORTS_DEHUMIDIFYING, False),
                ): bool,
                vol.Required(
                    OPT_SETPOINT_STEP_C,
                    default=cur.get(OPT_SETPOINT_STEP_C, 0.5),
                ): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=2.0)),
                vol.Required(
                    OPT_MANUAL_OVERRIDE_MIN,
                    default=cur.get(OPT_MANUAL_OVERRIDE_MIN, 90),
                ): vol.All(int, vol.Range(min=5, max=720)),
                vol.Required(
                    CONF_MAX_TEMP,
                    default=cur.get(CONF_MAX_TEMP, 35.0),
                ): vol.Coerce(float),
                vol.Required(
                    CONF_MIN_TEMP,
                    default=cur.get(CONF_MIN_TEMP, 5.0),
                ): vol.Coerce(float),
                vol.Required(
                    CONF_STEP,
                    default=cur.get(CONF_STEP, 0.5),
                ): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=2.0)),
                vol.Required(
                    CONF_TEMPERATURE_UNIT,
                    default=unit_default,
                ): temp_unit_selector,
            }
        )

    async def async_step_areas(self, user_input: dict[str, Any] | None = None) -> OptionsFlowResult:
        current = deepcopy(self.entry.options.get(CONF_AREAS, []))
        if user_input is not None:
            areas = user_input.get(CONF_AREAS, [])
            err = _validate_areas(areas)
            if err:
                return self.async_show_form(
                    step_id="areas",
                    data_schema=self._areas_schema(current),
                    errors={"base": err},
                )

            new_options: Dict[str, Any] = dict(self.entry.options)
            new_options[CONF_AREAS] = areas
            return self.async_create_entry(title="", data=new_options)

        return self.async_show_form(step_id="areas", data_schema=self._areas_schema(current))

    def _areas_schema(self, current: list[Any]) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(CONF_AREAS, default=current): selector.ObjectSelector(),
            }
        )

    async def async_step_radiant(self, user_input: dict[str, Any] | None = None) -> OptionsFlowResult:
        supply, radiant, vmc, extras = _split_devices(self.entry.options.get(CONF_DEVICES))
        if user_input is not None:
            new_radiant = user_input.get(CONF_RADIANT, {}) or {}
            if not isinstance(new_radiant, dict):
                return self.async_show_form(
                    step_id="radiant",
                    data_schema=self._device_schema(CONF_RADIANT, radiant),
                    errors={"base": "Il blocco radiant deve essere un oggetto."},
                )

            devices = _assemble_devices(supply, new_radiant, vmc, extras)
            err = _validate_devices(devices)
            if err:
                return self.async_show_form(
                    step_id="radiant",
                    data_schema=self._device_schema(CONF_RADIANT, new_radiant),
                    errors={"base": err},
                )

            new_options: Dict[str, Any] = dict(self.entry.options)
            new_options[CONF_DEVICES] = devices
            return self.async_create_entry(title="", data=new_options)

        return self.async_show_form(
            step_id="radiant",
            data_schema=self._device_schema(CONF_RADIANT, radiant),
        )

    async def async_step_supply_units(self, user_input: dict[str, Any] | None = None) -> OptionsFlowResult:
        supply, radiant, vmc, extras = _split_devices(self.entry.options.get(CONF_DEVICES))
        if user_input is not None:
            new_supply = user_input.get(CONF_SUPPLY_UNITS, {}) or {}
            if not isinstance(new_supply, dict):
                return self.async_show_form(
                    step_id="supply_units",
                    data_schema=self._device_schema(CONF_SUPPLY_UNITS, supply),
                    errors={"base": "Il blocco supply_units deve essere un oggetto."},
                )

            devices = _assemble_devices(new_supply, radiant, vmc, extras)
            err = _validate_devices(devices)
            if err:
                return self.async_show_form(
                    step_id="supply_units",
                    data_schema=self._device_schema(CONF_SUPPLY_UNITS, new_supply),
                    errors={"base": err},
                )

            new_options: Dict[str, Any] = dict(self.entry.options)
            new_options[CONF_DEVICES] = devices
            return self.async_create_entry(title="", data=new_options)

        return self.async_show_form(
            step_id="supply_units",
            data_schema=self._device_schema(CONF_SUPPLY_UNITS, supply),
        )

    async def async_step_vmc(self, user_input: dict[str, Any] | None = None) -> OptionsFlowResult:
        supply, radiant, vmc, extras = _split_devices(self.entry.options.get(CONF_DEVICES))
        if user_input is not None:
            new_vmc = user_input.get(CONF_VMC, {}) or {}
            if not isinstance(new_vmc, dict):
                return self.async_show_form(
                    step_id="vmc",
                    data_schema=self._device_schema(CONF_VMC, vmc),
                    errors={"base": "Il blocco vmc deve essere un oggetto."},
                )

            devices = _assemble_devices(supply, radiant, new_vmc, extras)
            err = _validate_devices(devices)
            if err:
                return self.async_show_form(
                    step_id="vmc",
                    data_schema=self._device_schema(CONF_VMC, new_vmc),
                    errors={"base": err},
                )

            new_options: Dict[str, Any] = dict(self.entry.options)
            new_options[CONF_DEVICES] = devices
            return self.async_create_entry(title="", data=new_options)

        return self.async_show_form(
            step_id="vmc",
            data_schema=self._device_schema(CONF_VMC, vmc),
        )

    def _device_schema(self, key: str, current: Mapping[str, Any]) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(key, default=deepcopy(dict(current))): selector.ObjectSelector(),
            }
        )

    async def async_step_weather(self, user_input: dict[str, Any] | None = None) -> OptionsFlowResult:
        current_weather = deepcopy(self.entry.data.get(CONF_WEATHER, {}))
        if user_input is not None:
            weather_cfg = user_input.get(CONF_WEATHER, {}) or {}
            if not isinstance(weather_cfg, dict):
                return self.async_show_form(
                    step_id="weather",
                    data_schema=self._weather_schema(current_weather),
                    errors={"base": "Il blocco weather deve essere un oggetto."},
                )
            try:
                validated = WEATHER_SCHEMA(weather_cfg)
                validated = _coerce_weather_latlon(dict(validated))
            except (vol.Invalid, ValueError) as err:
                return self.async_show_form(
                    step_id="weather",
                    data_schema=self._weather_schema(weather_cfg),
                    errors={"base": str(err)},
                )

            new_data = dict(self.entry.data)
            new_data[CONF_WEATHER] = validated
            self.hass.config_entries.async_update_entry(self.entry, data=new_data)
            return self.async_create_entry(title="", data=dict(self.entry.options))

        return self.async_show_form(
            step_id="weather",
            data_schema=self._weather_schema(current_weather),
        )

    def _weather_schema(self, current: Mapping[str, Any]) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(CONF_WEATHER, default=deepcopy(dict(current))): selector.ObjectSelector(),
            }
        )

    async def async_step_historical_data(self, user_input: dict[str, Any] | None = None) -> OptionsFlowResult:
        current_hist = deepcopy(self.entry.options.get(CONF_HISTORICAL_DATA, {}))
        if user_input is not None:
            hist = user_input.get(CONF_HISTORICAL_DATA, {}) or {}
            if not isinstance(hist, dict):
                return self.async_show_form(
                    step_id="historical_data",
                    data_schema=self._historical_schema(current_hist),
                    errors={"base": "Il blocco historical_data deve essere un oggetto."},
                )
            err = _validate_historical_data(hist)
            if err:
                return self.async_show_form(
                    step_id="historical_data",
                    data_schema=self._historical_schema(hist),
                    errors={"base": err},
                )

            new_options: Dict[str, Any] = dict(self.entry.options)
            new_options[CONF_HISTORICAL_DATA] = hist
            return self.async_create_entry(title="", data=new_options)

        return self.async_show_form(
            step_id="historical_data",
            data_schema=self._historical_schema(current_hist),
        )

    def _historical_schema(self, current: Mapping[str, Any]) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(CONF_HISTORICAL_DATA, default=deepcopy(dict(current))): selector.ObjectSelector(),
            }
        )

    async def async_step_advanced(self, user_input: dict[str, Any] | None = None) -> OptionsFlowResult:
        supply, radiant, vmc, extras = _split_devices(self.entry.options.get(CONF_DEVICES))
        current_scenarios = deepcopy(self.entry.options.get(CONF_SCENARIOS, {}))

        if user_input is not None:
            scenarios = user_input.get(CONF_SCENARIOS, {}) or {}
            if not isinstance(scenarios, dict):
                return self.async_show_form(
                    step_id="advanced",
                    data_schema=self._advanced_schema(current_scenarios, extras),
                    errors={"base": "Il blocco scenarios deve essere un oggetto."},
                )
            err = _validate_scenarios(scenarios)
            if err:
                return self.async_show_form(
                    step_id="advanced",
                    data_schema=self._advanced_schema(scenarios, extras),
                    errors={"base": err},
                )

            extra_devices = user_input.get(CONF_DEVICES, {}) or {}
            if not isinstance(extra_devices, dict):
                return self.async_show_form(
                    step_id="advanced",
                    data_schema=self._advanced_schema(scenarios, extras),
                    errors={"base": "Il blocco devices deve essere un oggetto."},
                )

            devices = _assemble_devices(supply, radiant, vmc, extra_devices)
            err = _validate_devices(devices)
            if err:
                return self.async_show_form(
                    step_id="advanced",
                    data_schema=self._advanced_schema(scenarios, extra_devices),
                    errors={"base": err},
                )

            new_options: Dict[str, Any] = dict(self.entry.options)
            new_options[CONF_SCENARIOS] = scenarios
            new_options[CONF_DEVICES] = devices
            return self.async_create_entry(title="", data=new_options)

        return self.async_show_form(
            step_id="advanced",
            data_schema=self._advanced_schema(current_scenarios, extras),
        )

    def _advanced_schema(self, scenarios: Mapping[str, Any], extras: Mapping[str, Any]) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(CONF_SCENARIOS, default=deepcopy(dict(scenarios))): selector.ObjectSelector(),
                vol.Required(CONF_DEVICES, default=deepcopy(dict(extras))): selector.ObjectSelector(),
            }
        )
