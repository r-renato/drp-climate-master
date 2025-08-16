# custom_components/drp_climate_master/config_flow.py
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.const import CONF_NAME, CONF_UNIQUE_ID

# Tipi flow: garantiamo compatibilità HA 2025.4.4 con fallback
from homeassistant.config_entries import ConfigFlow, OptionsFlow
try:
    # Presente nelle versioni recenti
    from homeassistant.config_entries import ConfigFlowResult  # type: ignore
except Exception:  # pragma: no cover
    # Fallback raro (per sicurezza): usa FlowResult generico
    from homeassistant.data_entry_flow import FlowResult as ConfigFlowResult  # type: ignore

try:
    # In alcune versioni manca OptionsFlowResult
    from homeassistant.config_entries import OptionsFlowResult  # type: ignore
except Exception:  # pragma: no cover
    from homeassistant.data_entry_flow import FlowResult as OptionsFlowResult  # type: ignore

from .const import DOMAIN, INTEGRATION_NAME

_LOGGER = logging.getLogger(__name__)

# -------------------------
# Chiavi dati / opzioni
# -------------------------
CONF_HUB_NAME = "hub_name"
CONF_CLIMATE_NAME = "climate_name"
CONF_CLIMATE_UNIQUE_ID = "climate_unique_id"

# Riferimenti globali
CONF_HOME_WINDOWS_STATE = "home_windows_state"
CONF_WEATHER = "weather"

# Oggetti complessi (struttura simile alla YAML)
CONF_AREAS = "areas"
CONF_DEVICES = "devices"
CONF_SCENARIOS = "scenarios"

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
    """Validazione minima per il blocco 'areas'."""
    if not isinstance(areas, list):
        return "Il campo 'areas' deve essere una lista."
    seen: set[str] = set()
    for a in areas:
        if not isinstance(a, dict):
            return "Ogni area deve essere un oggetto."
        n = a.get("area")
        if not n or not isinstance(n, str):
            return "Ogni area deve avere 'area' (stringa)."
        if n in seen:
            return f"Area duplicata: {n}"
        seen.add(n)

        sens = a.get("sensors", {})
        if not isinstance(sens, dict) or "temperature" not in sens:
            return f"L'area '{n}' deve avere sensors.temperature."
        # humidity rimane opzionale
    return None


def _validate_devices(dev: dict) -> Optional[str]:
    """Validazione minima dei blocchi devices."""
    if dev is None:
        return None
    if not isinstance(dev, dict):
        return "Il campo 'devices' deve essere un oggetto."
    # Aggiungi qui eventuali check specifici (supply_units/radiant/vmc)
    return None


def _validate_scenarios(sc: dict) -> Optional[str]:
    if sc is None:
        return None
    if not isinstance(sc, dict):
        return "Il campo 'scenarios' deve essere un oggetto."
    return None


def _yaml_climate_to_entry_payload(hub_name: str, climate: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Converte un blocco 'climate' YAML in (entry.data, entry.options).
    - data: informazioni identitarie/immutabili
    - options: configurazioni runtime e strutture complesse
    """
    climate_name = climate.get(CONF_NAME) or climate.get("name")
    uid = climate.get(CONF_UNIQUE_ID) or climate.get("unique_id")

    areas = climate.get(CONF_AREAS, [])
    devices = climate.get(CONF_DEVICES, {})
    scenarios = climate.get(CONF_SCENARIOS, {})
    home_windows_state = climate.get(CONF_HOME_WINDOWS_STATE)
    weather = climate.get(CONF_WEATHER)

    # Validazioni minime
    err = _validate_areas(areas)
    if err:
        raise ValueError(err)
    err = _validate_devices(devices)
    if err:
        raise ValueError(err)
    err = _validate_scenarios(scenarios)
    if err:
        raise ValueError(err)

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
    }
    return data, options


# -------------------------
# Config Flow (UI + Import)
# -------------------------
class DrpClimateMasterConfigFlow(ConfigFlow, domain=DOMAIN):
    """Gestisce il Config Flow di DRP Climate Master."""

    VERSION = 1

    def __init__(self) -> None:
        self._stored_user_input: Dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Step iniziale da UI: crea un entry con campi base e riferimenti globali."""
        if user_input is not None:
            hub_name = user_input[CONF_HUB_NAME]
            climate_name = user_input[CONF_CLIMATE_NAME]
            unique_id = user_input[CONF_CLIMATE_UNIQUE_ID]
            home_windows = user_input.get(CONF_HOME_WINDOWS_STATE)
            weather = user_input.get(CONF_WEATHER)

            # Unicità basata su unique_id del climate
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()

            data = {
                CONF_HUB_NAME: hub_name,
                CONF_CLIMATE_NAME: climate_name,
                CONF_CLIMATE_UNIQUE_ID: unique_id,
                CONF_HOME_WINDOWS_STATE: home_windows,
                CONF_WEATHER: weather,
            }

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
            }

            return self.async_create_entry(
                title=f"{INTEGRATION_NAME} - {climate_name}",
                data=data,
                options=options,
            )

        data_schema = vol.Schema(
            {
                vol.Required(CONF_HUB_NAME): str,
                vol.Required(CONF_CLIMATE_NAME): str,
                vol.Required(CONF_CLIMATE_UNIQUE_ID): str,
                vol.Optional(CONF_HOME_WINDOWS_STATE): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="binary_sensor")
                ),
                vol.Optional(CONF_WEATHER): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="weather")
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=data_schema)

    async def async_step_import(self, import_config: Dict[str, Any]) -> ConfigFlowResult:
        """Import da YAML: converte la YAML in uno (o più) ConfigEntry."""
        hubs = import_config.get(DOMAIN)
        if not hubs:
            return self.async_abort(reason="invalid_yaml")

        # Importiamo il PRIMO climate valido trovato (comportamento standard HA);
        # se desideri importare più entry, potresti iterare e crearne più.
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
                if unique_id:
                    await self.async_set_unique_id(unique_id)
                    # Se già configurato, aggiorna titolo ed esci
                    self._abort_if_unique_id_configured(
                        updates={"title": f"{INTEGRATION_NAME} - {data.get(CONF_CLIMATE_NAME)}"}
                    )

                # Evita duplicazioni naive basate su nome/uid
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
        """Opzioni runtime generali."""
        if user_input is not None:
            new_options = dict(self.entry.options)
            new_options[OPT_UPDATE_INTERVAL_S] = user_input[OPT_UPDATE_INTERVAL_S]
            new_options[OPT_SUPPORTS_HEATING] = user_input[OPT_SUPPORTS_HEATING]
            new_options[OPT_SUPPORTS_COOLING] = user_input[OPT_SUPPORTS_COOLING]
            new_options[OPT_SUPPORTS_DEHUMIDIFYING] = user_input[OPT_SUPPORTS_DEHUMIDIFYING]
            new_options[OPT_SETPOINT_STEP_C] = float(user_input[OPT_SETPOINT_STEP_C])
            new_options[OPT_MANUAL_OVERRIDE_MIN] = user_input[OPT_MANUAL_OVERRIDE_MIN]
            return self.async_create_entry(title="", data=new_options)

        cur = self.entry.options
        data_schema = vol.Schema(
            {
                vol.Required(OPT_UPDATE_INTERVAL_S, default=cur.get(OPT_UPDATE_INTERVAL_S, 30)): vol.All(int, vol.Range(min=5, max=3600)),
                vol.Required(OPT_SUPPORTS_HEATING, default=cur.get(OPT_SUPPORTS_HEATING, True)): bool,
                vol.Required(OPT_SUPPORTS_COOLING, default=cur.get(OPT_SUPPORTS_COOLING, False)): bool,
                vol.Required(OPT_SUPPORTS_DEHUMIDIFYING, default=cur.get(OPT_SUPPORTS_DEHUMIDIFYING, False)): bool,
                vol.Required(OPT_SETPOINT_STEP_C, default=cur.get(OPT_SETPOINT_STEP_C, 0.5)): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=2.0)),
                vol.Required(OPT_MANUAL_OVERRIDE_MIN, default=cur.get(OPT_MANUAL_OVERRIDE_MIN, 90)): vol.All(int, vol.Range(min=5, max=720)),
            }
        )
        return self.async_show_form(step_id="general", data_schema=data_schema)

    async def async_step_structure(self, user_input: dict[str, Any] | None = None) -> OptionsFlowResult:
        """Modifica la struttura complessa tramite selector Object (aree/devices/scenarios)."""
        if user_input is not None:
            areas = user_input.get(CONF_AREAS, [])
            devices = user_input.get(CONF_DEVICES, {})
            scenarios = user_input.get(CONF_SCENARIOS, {})

            # Validazioni minime
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

            new_options = dict(self.entry.options)
            new_options[CONF_AREAS] = areas
            new_options[CONF_DEVICES] = devices
            new_options[CONF_SCENARIOS] = scenarios
            return self.async_create_entry(title="", data=new_options)

        return self.async_show_form(step_id="structure", data_schema=self._structure_schema())

    def _structure_schema(self) -> vol.Schema:
        """Schema con selector Object per strutture JSON-like (compatibile 2025.4.4)."""
        cur = self.entry.options
        return vol.Schema(
            {
                vol.Required(CONF_AREAS, default=cur.get(CONF_AREAS, [])): selector.ObjectSelector(),
                vol.Required(CONF_DEVICES, default=cur.get(CONF_DEVICES, {})): selector.ObjectSelector(),
                vol.Required(CONF_SCENARIOS, default=cur.get(CONF_SCENARIOS, {})): selector.ObjectSelector(),
            }
        )
