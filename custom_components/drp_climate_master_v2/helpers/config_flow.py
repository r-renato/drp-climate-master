# custom_components/drp_climate_master/helpers.py
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Dict, Optional
import voluptuous as vol

from homeassistant.const import (
    CONF_NAME,
)

from ..const import (
    CONF_AREA,
    CONF_TEMPERATURE,
    CONF_HUMIDITY,
    CONF_VACATION,
    CONF_NOBODYSIN,
    CONF_SUPPLY_UNITS,
    CONF_RADIANT,
    CONF_VMC,
    CONF_HISTORICAL_DATA,
    CONF_LATITUDE,
    CONF_LONGITUDE,
)

from ..domain.schema import HISTORICAL_DATA_SCHEMA, WEATHER_SCHEMA

def normalize_yaml_hub(hub: Dict[str, Any]) -> Dict[str, Any]:
    """Normalizza un blocco HUB della YAML in un oggetto coerente."""
    name = hub.get(CONF_NAME) or hub.get("name")
    climates = hub.get("climate") or []
    if not isinstance(climates, list):
        climates = [climates]
    return {CONF_NAME: name, "climate": climates}

def coerce_weather_latlon(weather: dict) -> dict:
    """Converte latitude/longitude in float se presenti (in weather.historical_data)."""
    if not isinstance(weather, dict):
        return weather
    hist = weather.get(CONF_HISTORICAL_DATA) or {}
    if not isinstance(hist, dict):
        return weather
    if CONF_LATITUDE in hist:
        hist[CONF_LATITUDE] = float(hist[CONF_LATITUDE])
    if CONF_LONGITUDE in hist:
        hist[CONF_LONGITUDE] = float(hist[CONF_LONGITUDE])
    weather[CONF_HISTORICAL_DATA] = hist
    return weather

def split_devices(devices: Mapping[str, Any] | None) -> tuple[dict, dict, dict, dict]:
    """Ritorna (supply, radiant, vmc, extras) a partire da devices.*"""
    base: dict[str, Any] = {}
    if isinstance(devices, Mapping):
        base = {k: deepcopy(v) for k, v in devices.items()}
    supply = base.pop(CONF_SUPPLY_UNITS, {}) if base else {}
    radiant = base.pop(CONF_RADIANT, {}) if base else {}
    vmc = base.pop(CONF_VMC, {}) if base else {}
    extras = base if isinstance(base, dict) else dict(base)
    return (
        supply if isinstance(supply, dict) else {},
        radiant if isinstance(radiant, dict) else {},
        vmc if isinstance(vmc, dict) else {},
        extras,
    )

def assemble_devices(
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

def validate_areas(areas: list[dict]) -> Optional[str]:
    """Ogni area con sensors.temperature & sensors.humidity (obbligatori)."""
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

def validate_devices(dev: dict) -> Optional[str]:
    """
    Validazione minima dei blocchi devices (coerenze profonde demandate al runtime builder).
    Accetta dict oppure None (nessun devices).
    """
    if dev is None:
        return None
    if not isinstance(dev, dict):
        return "Il campo 'devices' deve essere un oggetto."
    for blk in ("supply_units", "radiant", "vmc"):
        if blk in dev and not isinstance(dev[blk], dict):
            return f"'devices.{blk}' deve essere un oggetto."
    return None

def validate_scenarios(sc: dict) -> Optional[str]:
    """vacation e nobodysin obbligatori (stringhe)."""
    if sc is None:
        return "Il campo 'scenarios' è obbligatorio."
    if not isinstance(sc, dict):
        return "Il campo 'scenarios' deve essere un oggetto."
    for key in (CONF_VACATION, CONF_NOBODYSIN):
        v = sc.get(key)
        if not isinstance(v, str) or not v:
            return f"'scenarios.{key}' è obbligatorio e deve essere una stringa."
    return None

def validate_historical_data(hist: dict | None) -> Optional[str]:
    """Valida historical_data secondo lo schema dedicato (obbligatorio)."""
    if hist is None:
        return "Il campo 'historical_data' è obbligatorio."
    if not isinstance(hist, dict):
        return "Il campo 'historical_data' deve essere un oggetto."
    try:
        HISTORICAL_DATA_SCHEMA(hist)
    except vol.Invalid as err:
        return f"'historical_data' non valido: {err}"
    return None

def validate_min_max(min_temp: Any, max_temp: Any) -> Optional[str]:
    try:
        max_t = float(max_temp)
        min_t = float(min_temp)
    except Exception:
        return "max_temp/min_temp devono essere numerici."
    if min_t >= max_t:
        return "min_temp deve essere < max_temp."
    return None

def ensure_weather_mapping(candidate: Mapping[str, Any]) -> dict:
    """Valida/normalizza un mapping weather rispetto a WEATHER_SCHEMA."""
    try:
        validated = WEATHER_SCHEMA(candidate)
        return dict(validated)
    except vol.Invalid as e:
        raise ValueError(f"Weather non valido: {e}") from e