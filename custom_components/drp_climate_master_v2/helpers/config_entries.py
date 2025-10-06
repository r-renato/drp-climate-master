# custom_components/drp_climate_master_v2/helpers/config_entries.py
from __future__ import annotations

import logging
from dataclasses import is_dataclass, fields
from datetime import timedelta
from typing import Any, Mapping, Callable, Iterable, Optional, Union, List

from homeassistant.core import HomeAssistant, Event, CALLBACK_TYPE, callback, EventStateChangedData
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_NAME,
    CONF_FRIENDLY_NAME,
    CONF_SENSORS,
    CONF_UNIQUE_ID,
    CONF_TEMPERATURE_UNIT,
)

from custom_components.drp_climate_master_v2.helpers.logger import log_info

from ..helpers.utils import as_int
from ..domain.models.runtime_schema import (
    AreaConfig,
    ClimateConfig,
    CompressorManagementConfig,
    CoolingManagementConfig,
    DevicesConfig,
    ForecastDataConfig,
    HistoricalDataConfig,
    ModeConfig,
    PlantCapabilities,
    RadiantConfig,
    RadiantSensors,
    RuntimeConfig,
    ScenariosConfig,
    SeasonConfig,
    SensorPair,
    SetpointConfig,
    SupplyUnitSensors,
    SupplyUnitsConfig,
    VMCAlarmsConfig,
    VMCConfig,
    VMCRequestsConfig,
    VMCSensorsConfig,
    WeatherConfig
)
from ..const import (
    CONF_ADJUSTABLE_SUPPLY_UNIT,
    CONF_ALARMS,
    CONF_AREA,
    CONF_AREAS,
    CONF_CLIMATE,
    CONF_COMPRESSOR_MANAGEMENT,
    CONF_COOLING_DT_SETPOINT,
    CONF_COOLING_MANAGEMENT,
    CONF_COOLING_T_SETPOINT,
    CONF_DELTA_DEW_POINT_SETPOINT,
    CONF_DEVICES,
    CONF_DEW_POINT_SETPOINT,
    CONF_DIRECT_SUPPLY_UNIT,
    CONF_FM_POWER,
    CONF_FORCE_COOLING,
    CONF_FORCE_FREE_COOLING,
    CONF_FORCE_HEATING,
    CONF_FORECAST_DATA,
    CONF_H_SETPOINT,
    CONF_HEATING_DT_SETPOINT,
    CONF_HEATING_T_SETPOINT,
    CONF_HISTORICAL_DATA,
    CONF_HOME_WINDOWS_STATE,
    CONF_INDOOR,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_MODE,
    CONF_MQ,
    CONF_POWER,
    CONF_PROVIDER,
    CONF_RADIANT,
    CONF_REQUESTS,
    CONF_SCENARIOS,
    CONF_SEASON,
    CONF_SPARE_SETPOINT,
    CONF_SUPPLY_UNITS,
    CONF_T_SETPOINT,
    CONF_TCOLLECTOR,
    CONF_THREE_POINT_MIXING_VALVE,
    CONF_TOKEN,
    CONF_UNITS,
    CONF_VENT_RECIRCULATION,
    CONF_VMC,
    CONF_WEATHER,
    DEFAULT_TEMP_UNIT,
    DEFAULT_UNITS,
    OPT_UPDATE_INTERVAL_S,
)

_LOGGER = logging.getLogger(__name__)

def subscribe_entity_state_changes(
    hass: HomeAssistant,
    callback: Callable[[Event[EventStateChangedData]], Any],   # 👈 firma richiesta
    entity_ids: Union[str, Iterable[str]],
    *,
    on_remove: Optional[Callable[[CALLBACK_TYPE], None]] = None,
) -> Optional[CALLBACK_TYPE]:
    """
    Sottoscrive gli eventi `state_changed` per uno o più `entity_id` e restituisce
    la **funzione di unsubscribe**.

    Questo helper è un thin-wrapper su `async_track_state_change_event` che:
    - accetta una stringa singola o un iterabile di `entity_id`;
    - normalizza e **deduplica** gli ID vuoti o ripetuti;
    - opzionalmente registra l’unsubscribe nel ciclo di vita passato in `on_remove`
      (es. `entry.async_on_unload`, `entity.async_on_remove`).

    Parametri
    ---------
    hass : HomeAssistant
        Istanza di Home Assistant.
    callback : Callable[[Event[EventStateChangedData]], Any]
        Handler invocato su ogni evento `state_changed` degli entity monitorati.
        Può essere sincrono (consigliato decorare con `@callback`) o `async def`.
        Firma tip-safe: `def handler(event: Event[EventStateChangedData]) -> None`.
    entity_ids : str | Iterable[str]
        Uno o più `entity_id` (es. `"sensor.t_living"` o `["sensor.t_living", "sensor.h_living"]`).
    on_remove : Callable[[CALLBACK_TYPE], None], opzionale
        Funzione alla quale passare la `unsubscribe` per legarla al ciclo di vita
        (es. `entry.async_on_unload`, `self.async_on_remove`).
    log : bool, opzionale
        Se `True` logga l’attivazione dell’ascolto.

    Ritorno
    -------
    Optional[CALLBACK_TYPE]
        La funzione di **unsubscribe** (richiamala per rimuovere il listener),
        oppure `None` se `entity_ids` non contiene elementi validi.

    Esempi
    -------
    >>> # In config entry setup:
    >>> unsub = subscribe_entity_state_changes(
    ...     hass,
    ...     callback=my_handler,                              # def my_handler(e: Event[EventStateChangedData]) -> None
    ...     entity_ids=["sensor.t_soggiorno", "sensor.h_soggiorno"],
    ...     on_remove=entry.async_on_unload,                  # si pulisce da solo allo unload dell'entry
    ... )
    ...
    >>> # Dentro una Entity:
    >>> self._unsub = subscribe_entity_state_changes(
    ...     self.hass, my_handler, "sensor.t_camera", on_remove=self.async_on_remove
    ... )

    Note
    ----
    - Preferisci handler **sincroni** con `@callback` per ridurre overhead.
    - La firma della callback è tipizzata come `Event[EventStateChangedData]` per
      essere compatibile con `async_track_state_change_event` su HA 2025.4.x+.
    """
    # Normalizza gli entity_id
    ids: List[str]
    if isinstance(entity_ids, str):
        ids = [entity_ids]
    else:
        ids = [e for e in entity_ids if isinstance(e, str) and e.strip()]

    if not ids:
        _LOGGER.error("setup_entity_change: nessun entity_id valido.")
        return None

    unsubscribe: CALLBACK_TYPE = async_track_state_change_event(hass, ids, callback)
    log_info(_LOGGER, "Ascolto attivo per %s", ", ".join(ids))

    if on_remove is not None:
        try:
            on_remove(unsubscribe)
        except Exception:
            _LOGGER.exception("setup_entity_change: on_remove ha generato un'eccezione.")

    return unsubscribe

def _infer_capabilities_from_devices(options: Mapping[str, Any]) -> tuple[bool, bool, bool, bool]:
    """
    Deduce heating/cooling/dehumidifying capabilities from devices config
    when the user did not specify them explicitly.
    """
    devices = options.get(CONF_DEVICES) or {}
    if not isinstance(devices, Mapping):
        return False, False, False, False  # fallback conservativo

    radiant = devices.get(CONF_RADIANT) or {}
    vmc = devices.get(CONF_VMC) or {}

    supports_heating = supports_cooling = bool(radiant)
    supports_dehumidifying = supports_ventilation  = bool(vmc)

    return supports_heating, supports_cooling, supports_dehumidifying, supports_ventilation

def build_runtime_config(entry: ConfigEntry) -> RuntimeConfig:
    climate_cfg: Mapping[str, Any] = entry.options or {}
    # _LOGGER.debug("build_runtime_config (entry.data) %s", entry.data)
    # _LOGGER.debug("build_runtime_config (entry.options) %s", entry.options)

    # update_s = _as_int(opts.get(OPT_UPDATE_INTERVAL_S, 30), 30)
    update_interval = timedelta(seconds=max(30, as_int(OPT_UPDATE_INTERVAL_S, 30, min_value=30, max_value=300) or 30))
    
    supports_heating,\
    supports_cooling,\
    supports_dehumidifying,\
    supports_ventilation = _infer_capabilities_from_devices(climate_cfg)
    # supports_heating = _as_bool(opts.get(OPT_SUPPORTS_HEATING, ih), ih)
    # supports_cooling = _as_bool(opts.get(OPT_SUPPORTS_COOLING, ic), ic)
    # supports_dehumidifying = _as_bool(
    #     opts.get(OPT_SUPPORTS_DEHUMIDIFYING, idh), idh
    # )
    # step = opts.get(OPT_SETPOINT_STEP_C) or opts.get(CONF_STEP)
    # setpoint_step_c = _as_float(step, 0.5)
    # manual_override_minutes = _as_int(
    #     opts.get(OPT_MANUAL_OVERRIDE_MIN, 90), 90
    # )

    # ----- Climate -------------------------------------------------------
    # climate_cfg = (opts.get(CONF_CLIMATE) or [])[0]
    areas = [
        AreaConfig(
            name=a[CONF_AREA],
            indoor=a.get(CONF_INDOOR, True),
            radiant=a.get(CONF_RADIANT, True),
            sensors=SensorPair(**a[CONF_SENSORS]),
            thermal_collector_valve_switch=a.get(CONF_TCOLLECTOR, None),
            mq=a.get(CONF_MQ),
        )
        for a in climate_cfg.get(CONF_AREAS, [])
    ]

    su = climate_cfg[CONF_DEVICES][CONF_SUPPLY_UNITS]
    supply_units = SupplyUnitsConfig(
        direct_supply_unit=su[CONF_DIRECT_SUPPLY_UNIT],
        adjustable_supply_unit=su[CONF_ADJUSTABLE_SUPPLY_UNIT],
        three_point_mixing_valve=su[CONF_THREE_POINT_MIXING_VALVE],
        sensors=SupplyUnitSensors(**su[CONF_SENSORS]),
    )

    dev_cfg = climate_cfg[CONF_DEVICES]
    radiant = None
    if CONF_RADIANT in dev_cfg:
        r = dev_cfg[CONF_RADIANT]
        radiant = RadiantConfig(
            fm_power=r[CONF_FM_POWER],
            power=r[CONF_POWER],
            mode=ModeConfig(**r[CONF_MODE]),
            heating_t_setpoint=SetpointConfig(**r[CONF_HEATING_T_SETPOINT]),
            heating_dt_setpoint=SetpointConfig(**r[CONF_HEATING_DT_SETPOINT]),
            cooling_t_setpoint=SetpointConfig(**r[CONF_COOLING_T_SETPOINT]),
            cooling_dt_setpoint=SetpointConfig(**r[CONF_COOLING_DT_SETPOINT]),
            sensors=RadiantSensors(**r[CONF_SENSORS]),
        )

    vmc = None
    if CONF_VMC in dev_cfg:
        v = dev_cfg[CONF_VMC]
        vmc = VMCConfig(
            power=v[CONF_POWER],
            t_setpoint=v[CONF_T_SETPOINT],
            h_setpoint=v[CONF_H_SETPOINT],
            t_dew_point_setpoint=v[CONF_DEW_POINT_SETPOINT],
            delta_t_dew_point_setpoint=v[CONF_DELTA_DEW_POINT_SETPOINT],
            spare_setpoint=v[CONF_SPARE_SETPOINT],
            vent_recirculation=v[CONF_VENT_RECIRCULATION],
            force_heating=v[CONF_FORCE_HEATING],
            force_cooling=v[CONF_FORCE_COOLING],
            force_free_cooling=v[CONF_FORCE_FREE_COOLING],
            season=SeasonConfig(**v[CONF_SEASON]),
            compressor_management=CompressorManagementConfig(
                **v[CONF_COMPRESSOR_MANAGEMENT]
            ),
            cooling_management=CoolingManagementConfig(
                **v[CONF_COOLING_MANAGEMENT]
            ),
            requests=VMCRequestsConfig(**v[CONF_REQUESTS]),
            sensors=VMCSensorsConfig(**v[CONF_SENSORS]),
            alarms=VMCAlarmsConfig(**v[CONF_ALARMS]),
        )

    w = entry.data[CONF_WEATHER]
    _LOGGER.debug("build_runtime_config %s", entry.data)

    fd = ForecastDataConfig(provider=str(w[CONF_FORECAST_DATA][CONF_PROVIDER]).strip())
    h = w[CONF_HISTORICAL_DATA]
    hd = HistoricalDataConfig(
        provider=str(h[CONF_PROVIDER]).strip().lower(),
        token=str(h[CONF_TOKEN]).strip(),
        latitude=float(h[CONF_LATITUDE]),
        longitude=float(h[CONF_LONGITUDE]),
    )

    climate = ClimateConfig(
        name=entry.data["climate_name"],
        unique_id=entry.data["climate_unique_id"],
        units=entry.data.get(CONF_UNITS, DEFAULT_UNITS),
        areas=areas,
        devices=DevicesConfig(
            supply_units=supply_units, radiant=radiant, vmc=vmc
        ),
        home_windows_state=entry.data[CONF_HOME_WINDOWS_STATE],
        # weather=entry.data[CONF_WEATHER],
        weather=WeatherConfig(forecast_data=fd, historical_data=hd),
        scenarios=ScenariosConfig(**climate_cfg[CONF_SCENARIOS]),
        temperature_unit=climate_cfg.get(CONF_TEMPERATURE_UNIT, DEFAULT_TEMP_UNIT),
        mean_apt=SensorPair("", "")
    )

    caps = PlantCapabilities(
        supports_heating=supports_heating,
        supports_cooling=supports_cooling,
        supports_dehumidifying=supports_dehumidifying,
        supports_ventilation=supports_ventilation,
        setpoint_step_c=0.5,  # TODO: read from options if set
    )
    return RuntimeConfig(
        update_interval=update_interval,
        capabilities=caps,
        manual_override_minutes=90,  # TODO: read from options if set
        climate=climate,
    )

def collect_entity_ids_for_state_changes(runtime: "RuntimeConfig") -> list[str]:
    seen: set[str] = set()
    out: list[str] = []

    def _looks_like_entity_id(s: str) -> bool:
        return "." in s and " " not in s and s[0].islower()

    def _add(e: object) -> None:
        if isinstance(e, str):
            s = e.strip()
            if s and _looks_like_entity_id(s) and s not in seen:
                seen.add(s)
                out.append(s)

    def _walk(obj: Any) -> None:
        """Raccoglie ricorsivamente stringhe che paiono entity_id."""
        if obj is None:
            return
        if isinstance(obj, str):
            _add(obj)
            return
        # SOLO istanze dataclass (non classi): evita l'errore di typing con asdict
        if is_dataclass(obj) and not isinstance(obj, type):
            for f in fields(obj):
                _walk(getattr(obj, f.name))
            return
        if isinstance(obj, Mapping):
            for v in obj.values():
                _walk(v)
            return
        if isinstance(obj, Iterable) and not isinstance(obj, (str, bytes, bytearray, dict)):
            for v in obj:
                _walk(v)
            return
        # altri tipi ignorati

    climate = getattr(runtime, "climate", None)
    if not climate:
        return out

    # --- AREE: sensori T/H + interruttore valvola collettore termico
    for area in getattr(climate, "areas", []) or []:
        sensors = getattr(area, "sensors", None)
        if sensors:
            _add(getattr(sensors, "temperature", None))
            _add(getattr(sensors, "humidity", None))
        _add(getattr(area, "thermal_collector_valve_switch", None))

    # --- SUPPLY UNITS: attuatori + TUTTI i sensori
    su = getattr(getattr(climate, "devices", None), "supply_units", None)
    if su:
        _add(getattr(su, "direct_supply_unit", None))
        _add(getattr(su, "adjustable_supply_unit", None))
        _add(getattr(su, "three_point_mixing_valve", None))
        _walk(getattr(su, "sensors", None))

    # --- RADIANT (se presente): top-level + mode/setpoint/sensors
    radiant = getattr(getattr(climate, "devices", None), "radiant", None)
    if radiant:
        _add(getattr(radiant, "fm_power", None))
        _add(getattr(radiant, "power", None))
        _walk(getattr(radiant, "mode", None))
        _walk(getattr(radiant, "heating_t_setpoint", None))
        _walk(getattr(radiant, "heating_dt_setpoint", None))
        _walk(getattr(radiant, "cooling_t_setpoint", None))
        _walk(getattr(radiant, "cooling_dt_setpoint", None))
        _walk(getattr(radiant, "sensors", None))

    # --- VMC (se presente): top-level + season/management/requests/sensors/alarms
    vmc = getattr(getattr(climate, "devices", None), "vmc", None)
    if vmc:
        for name in (
            "power",
            "t_setpoint",
            "h_setpoint",
            "t_dew_point_setpoint",
            "delta_t_dew_point_setpoint",
            "spare_setpoint",
            "vent_recirculation",
            "force_heating",
            "force_cooling",
            "force_free_cooling",
        ):
            _add(getattr(vmc, name, None))
        _walk(getattr(vmc, "season", None))
        _walk(getattr(vmc, "compressor_management", None))
        _walk(getattr(vmc, "cooling_management", None))
        _walk(getattr(vmc, "requests", None))
        _walk(getattr(vmc, "sensors", None))
        _walk(getattr(vmc, "alarms", None))

    # --- HOME WINDOWS STATE / WEATHER / SCENARIOS
    _walk(getattr(climate, "home_windows_state", None))
    _walk(getattr(climate, "weather", None))
    _walk(getattr(climate, "scenarios", None))

    return out


