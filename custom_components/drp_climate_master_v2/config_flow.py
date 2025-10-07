# custom_components/drp_climate_master/config_flow.py
from __future__ import annotations

import logging
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

from .domain.schema import WEATHER_SCHEMA
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
    DEFAULT_TEMP_UNIT,
    # Weather nested keys
    CONF_FORECAST_DATA,
    CONF_HISTORICAL_DATA,
    CONF_PROVIDER,
    CONF_TOKEN,
    CONF_LATITUDE,
    CONF_LONGITUDE,
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
    climate_name = climate.get(CONF_NAME) or climate.get("name")
    uid = climate.get(CONF_UNIQUE_ID) or climate.get("unique_id")

    areas = climate.get(CONF_AREAS, [])
    devices = climate.get(CONF_DEVICES, {})
    scenarios = climate.get(CONF_SCENARIOS, {})
    home_windows_state = climate.get(CONF_HOME_WINDOWS_STATE)
    weather = climate.get(CONF_WEATHER)

    # Parametri climatici (con default come nello schema)
    max_temp = climate.get(CONF_MAX_TEMP, 35)
    min_temp = climate.get(CONF_MIN_TEMP, 5)
    step = climate.get(CONF_STEP, 0.5)
    temp_unit = climate.get(CONF_TEMPERATURE_UNIT, DEFAULT_TEMP_UNIT)

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
    }

    options: Dict[str, Any] = {
        CONF_AREAS: areas,
        CONF_DEVICES: devices,
        CONF_SCENARIOS: scenarios,
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
            }

            # Valori di default coerenti con schema (le strutture si editano in Options)
            options = {
                CONF_AREAS: [],
                CONF_DEVICES: {},
                CONF_SCENARIOS: {},
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
    """Gestisce le opzioni: runtime (general) e struttura (areas/devices/scenarios)."""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self.entry = entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> OptionsFlowResult:
        """Menu iniziale delle opzioni."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["general", "structure"],
        )

    async def async_step_general(self, user_input: dict[str, Any] | None = None) -> OptionsFlowResult:
        """Opzioni runtime generali + parametri climatici (max/min/step/unit)."""
        cur: Mapping[str, Any] = self.entry.options

        if user_input is not None:
            # Validazione coerenza min/max
            try:
                max_t = float(user_input[CONF_MAX_TEMP])
                min_t = float(user_input[CONF_MIN_TEMP])
            except Exception:  # noqa: BLE001
                return self.async_show_form(
                    step_id="general",
                    data_schema=self._general_schema(cur),
                    errors={"base": "Valori non numerici per min/max temp."},
                )
            if min_t >= max_t:
                return self.async_show_form(
                    step_id="general",
                    data_schema=self._general_schema(cur),
                    errors={"base": "min_temp deve essere < max_temp."},
                )

            new_options: Dict[str, Any] = dict(cur)  # copia mutabile
            # runtime
            new_options[OPT_UPDATE_INTERVAL_S] = user_input[OPT_UPDATE_INTERVAL_S]
            new_options[OPT_SUPPORTS_HEATING] = user_input[OPT_SUPPORTS_HEATING]
            new_options[OPT_SUPPORTS_COOLING] = user_input[OPT_SUPPORTS_COOLING]
            new_options[OPT_SUPPORTS_DEHUMIDIFYING] = user_input[OPT_SUPPORTS_DEHUMIDIFYING]
            new_options[OPT_SETPOINT_STEP_C] = float(user_input[OPT_SETPOINT_STEP_C])
            new_options[OPT_MANUAL_OVERRIDE_MIN] = user_input[OPT_MANUAL_OVERRIDE_MIN]
            # climatici
            new_options[CONF_MAX_TEMP] = max_t
            new_options[CONF_MIN_TEMP] = min_t
            new_options[CONF_STEP] = float(user_input[CONF_STEP])
            new_options[CONF_TEMPERATURE_UNIT] = str(user_input[CONF_TEMPERATURE_UNIT])

            return self.async_create_entry(title="", data=new_options)

        return self.async_show_form(step_id="general", data_schema=self._general_schema(cur))

    def _general_schema(self, cur: Mapping[str, Any]) -> vol.Schema:
        """Schema per le opzioni generali."""
        # Unità temperatura: manteniamo stringhe per allineamento allo YAML (es. "C"/"F")
        unit_default = str(cur.get(CONF_TEMPERATURE_UNIT, DEFAULT_TEMP_UNIT)).upper()
        temp_unit_selector = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=["C", "F"],
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
                # climatici
                vol.Required(CONF_MAX_TEMP, default=cur.get(CONF_MAX_TEMP, 35.0)): vol.Coerce(float),
                vol.Required(CONF_MIN_TEMP, default=cur.get(CONF_MIN_TEMP, 5.0)): vol.Coerce(float),
                vol.Required(CONF_STEP, default=cur.get(CONF_STEP, 0.5)): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=2.0)),
                vol.Required(CONF_TEMPERATURE_UNIT, default=unit_default): temp_unit_selector,
            }
        )

    async def async_step_structure(self, user_input: dict[str, Any] | None = None) -> OptionsFlowResult:
        """Modifica la struttura complessa tramite selector Object (aree/devices/scenarios)."""
        if user_input is not None:
            areas = user_input.get(CONF_AREAS, [])
            devices = user_input.get(CONF_DEVICES, {})
            scenarios = user_input.get(CONF_SCENARIOS, {})

            # Validazioni minime (allineate allo schema)
            err = _validate_areas(areas)
            if err:
                return self.async_show_form(
                    step_id="structure",
                    data_schema=self._structure_schema(),
                    errors={"base": err},
                )
            err = _validate_devices(devices)
            if err:
                return self.async_show_form(
                    step_id="structure",
                    data_schema=self._structure_schema(),
                    errors={"base": err},
                )
            err = _validate_scenarios(scenarios)
            if err:
                return self.async_show_form(
                    step_id="structure",
                    data_schema=self._structure_schema(),
                    errors={"base": err},
                )

            new_options: Dict[str, Any] = dict(self.entry.options)
            new_options[CONF_AREAS] = areas
            new_options[CONF_DEVICES] = devices
            new_options[CONF_SCENARIOS] = scenarios
            return self.async_create_entry(title="", data=new_options)

        return self.async_show_form(step_id="structure", data_schema=self._structure_schema())

    def _structure_schema(self) -> vol.Schema:
        """Schema con selector Object per strutture JSON-like (compatibile 2025.4.4)."""
        cur: Mapping[str, Any] = self.entry.options
        return vol.Schema(
            {
                vol.Required(CONF_AREAS, default=cur.get(CONF_AREAS, [])): selector.ObjectSelector(),
                vol.Required(CONF_DEVICES, default=cur.get(CONF_DEVICES, {})): selector.ObjectSelector(),
                vol.Required(CONF_SCENARIOS, default=cur.get(CONF_SCENARIOS, {})): selector.ObjectSelector(),
            }
        )
