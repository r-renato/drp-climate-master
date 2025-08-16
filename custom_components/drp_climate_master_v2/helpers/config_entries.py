from __future__ import annotations

from datetime import timedelta
from typing import Any, Mapping, Optional
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry

from ..const import (
    # struttura
    CONF_DEVICES,
    CONF_RADIANT,
    CONF_VMC,
    # runtime “storico” (fallback)
    CONF_STEP,        # "temp_step"
    # options flow “nuove”
    # (se le tieni in const.py, importale qui; altrimenti inlined sotto come stringhe)
)

# Se non hai messo queste costanti in const.py, lasciale così:
OPT_UPDATE_INTERVAL_S = "update_interval_s"
OPT_SUPPORTS_HEATING = "supports_heating"
OPT_SUPPORTS_COOLING = "supports_cooling"
OPT_SUPPORTS_DEHUMIDIFYING = "supports_dehumidifying"
OPT_SETPOINT_STEP_C = "setpoint_step_c"
OPT_MANUAL_OVERRIDE_MIN = "manual_override_minutes"

@dataclass(slots=True, frozen=True)
class PlantCapabilities:
    """
    Capacità operative della 'pianta' (impianto di climatizzazione).

    Questi valori determinano le modalità di funzionamento che
    il sistema può offrire all'utente.
    """
    supports_heating: bool = True
    supports_cooling: bool = False
    supports_dehumidifying: bool = False
    setpoint_step_c: float = 0.5


@dataclass(slots=True, frozen=True)
class RuntimeConfig:
    """
    Configurazione runtime derivata dalle opzioni e dai dati
    di configurazione di Home Assistant (config_flow o YAML).

    È immutabile (frozen=True) così che non possa essere alterata
    accidentalmente a runtime.
    """
    update_interval: timedelta
    capabilities: PlantCapabilities
    manual_override_minutes: int

def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(int(value))
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"true", "1", "yes", "on"}:
            return True
        if v in {"false", "0", "no", "off"}:
            return False
    return default


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _infer_capabilities_from_devices(options: Mapping[str, Any]) -> tuple[bool, bool, bool]:
    """
    Deduce capability di base (heating/cooling/dehumidify) dalla struttura devices
    quando l’utente non le ha impostate esplicitamente.
    """
    devices = options.get(CONF_DEVICES, {}) or {}
    if not isinstance(devices, dict):
        devices = {}

    radiant = devices.get(CONF_RADIANT)
    vmc = devices.get(CONF_VMC)

    # euristiche conservative:
    supports_heating = bool(radiant) or bool(vmc)
    # se c'è radiant o vmc con gestione raffrescamento, presumiamo cooling
    supports_cooling = bool(radiant) or bool(vmc)
    # la deumidifica la assumiamo se c'è la VMC configurata
    supports_dehumidifying = bool(vmc)

    return supports_heating, supports_cooling, supports_dehumidifying


def build_runtime_config(entry: ConfigEntry) -> RuntimeConfig:
    """
    Traduce entry.options → RuntimeConfig, con default sensati e
    deduzioni basate sulla presenza dei blocchi devices.radiant/vmc
    del tuo YAML importato in options.
    """
    opts: Mapping[str, Any] = entry.options or {}

    # ---- Update interval (con minimo 5s)
    update_s = _as_int(opts.get(OPT_UPDATE_INTERVAL_S, 30), 30)
    update_interval = timedelta(seconds=max(5, update_s))

    # ---- Capability: leggi esplicito, altrimenti deduci da devices
    ih, ic, idh = _infer_capabilities_from_devices(opts)
    supports_heating = _as_bool(opts.get(OPT_SUPPORTS_HEATING, ih), ih)
    supports_cooling = _as_bool(opts.get(OPT_SUPPORTS_COOLING, ic), ic)
    supports_dehumidifying = _as_bool(opts.get(OPT_SUPPORTS_DEHUMIDIFYING, idh), idh)

    # ---- Step setpoint: preferisci `setpoint_step_c`, fallback a `temp_step`
    step = opts.get(OPT_SETPOINT_STEP_C)
    if step is None:
        step = opts.get(CONF_STEP)  # "temp_step" dal tuo schema precedente
    setpoint_step_c = _as_float(step, 0.5)

    # ---- Manual override (minuti)
    manual_override_minutes = _as_int(opts.get(OPT_MANUAL_OVERRIDE_MIN, 90), 90)

    caps = PlantCapabilities(
        supports_heating=supports_heating,
        supports_cooling=supports_cooling,
        supports_dehumidifying=supports_dehumidifying,
        setpoint_step_c=setpoint_step_c,
    )

    return RuntimeConfig(
        update_interval=update_interval,
        capabilities=caps,
        manual_override_minutes=manual_override_minutes,
    )
