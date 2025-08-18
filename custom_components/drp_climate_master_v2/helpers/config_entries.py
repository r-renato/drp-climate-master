# custom_components/drp_climate_master_v2/helpers/config_entries.py

from datetime import timedelta
from typing import Any, Mapping

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_NAME,
    CONF_FRIENDLY_NAME,
    CONF_SENSORS,
    CONF_UNIQUE_ID,
    CONF_TEMPERATURE_UNIT,
)

from ..domain.models import (
    AreaConfig,
    ClimateConfig,
    CompressorManagementConfig,
    CoolingManagementConfig,
    DevicesConfig,
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
    VMCSensorsConfig
)
from ..helpers.utils import _as_int
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
    CONF_H_SETPOINT,
    CONF_HEATING_DT_SETPOINT,
    CONF_HEATING_T_SETPOINT,
    CONF_HOME_WINDOWS_STATE,
    CONF_INDOOR,
    CONF_MODE,
    CONF_MQ,
    CONF_POWER,
    CONF_RADIANT,
    CONF_REQUESTS,
    CONF_SCENARIOS,
    CONF_SEASON,
    CONF_SPARE_SETPOINT,
    CONF_SUPPLY_UNITS,
    CONF_T_SETPOINT,
    CONF_TCOLLECTOR,
    CONF_THREE_POINT_MIXING_VALVE,
    CONF_VENT_RECIRCULATION,
    CONF_VMC,
    CONF_WEATHER,
    OPT_UPDATE_INTERVAL_S,
)

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
    opts: Mapping[str, Any] = entry.options or {}

    # update_s = _as_int(opts.get(OPT_UPDATE_INTERVAL_S, 30), 30)
    update_interval = timedelta(seconds=max(30, _as_int(OPT_UPDATE_INTERVAL_S, 30, min_value=30, max_value=300) or 30))
    
    supports_heating,\
    supports_cooling,\
    supports_dehumidifying,\
    supports_ventilation = _infer_capabilities_from_devices(opts)
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
    climate_cfg = (opts.get(CONF_CLIMATE) or [])[0]
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

    climate = ClimateConfig(
        name=climate_cfg[CONF_NAME],
        unique_id=climate_cfg[CONF_UNIQUE_ID],
        areas=areas,
        devices=DevicesConfig(
            supply_units=supply_units, radiant=radiant, vmc=vmc
        ),
        home_windows_state=climate_cfg[CONF_HOME_WINDOWS_STATE],
        weather=climate_cfg[CONF_WEATHER],
        scenarios=ScenariosConfig(**climate_cfg[CONF_SCENARIOS]),
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
