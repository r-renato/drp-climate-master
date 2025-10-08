from __future__ import annotations

import logging
from typing import List
from datetime import datetime

from .logger import exc_one_line, log_debug, log_warning

from .ha import get_entity_value

from ..domain.models.plant import (
    PDCSnapshot,
    PlantSnapshot,
    SupplyUnitSnapshot,
    VMCSnapshot,
    ZoneSnapshot,
)
from ..domain.models.runtime_schema import (
    AreaConfig,
    RadiantConfig,
    RuntimeConfig,
    SensorPair,
    SupplyUnitSensors,
    SupplyUnitsConfig,
    VMCConfig,
)
from ..domain.models.season import SeasonState
from .utils import as_bool, as_float, as_int, make_class

_LOGGER = logging.getLogger(__name__)

def take_plant_snapshot(
    runtime_config: RuntimeConfig,
    season: SeasonState,
    entities_state: dict,
    timestamp: datetime,
) -> PlantSnapshot:
    """Build a plant snapshot collecting HA entity states."""

    def _build_zones_snapshot(runtime_config: RuntimeConfig, ts: datetime) -> dict[str, ZoneSnapshot]:
        """Helper to build a ZoneSnapshot from area configs."""
        zone_snapshots: dict[str, ZoneSnapshot] = {}

        # log_debug(_LOGGER, "RuntimeConfig: 22 %s", runtime_config.climate.areas)
        supply_unit_sensor: SupplyUnitSensors = runtime_config.climate.devices.supply_units.sensors
        flow_t = as_float(get_entity_value(entities_state, supply_unit_sensor.boiler_temp_system_supply))
        return_t = as_float(get_entity_value(entities_state, supply_unit_sensor.boiler_temp_system_return))

        areas: List[AreaConfig] = runtime_config.climate.areas
        for area in areas:
            # log_debug(_LOGGER, "RuntimeConfig: 22 %s", area.sensors)
            sensors=area.sensors
            timestamp=ts
            name=area.name
            valve_state = None
            room_t = as_float(get_entity_value(entities_state, sensors.temperature))
            room_rh = as_float(get_entity_value(entities_state, sensors.humidity))

            if area.indoor:
                room_dp = as_float(get_entity_value(entities_state, sensors.dew_point))
                room_hi = as_float(get_entity_value(entities_state, sensors.heat_index))
                # log_debug(_LOGGER, f"Entity ids for area {name}: T={sensors.temperature}, RH={sensors.humidity}, DP={sensors.dew_point}, HI={sensors.heat_index}")

            if area.radiant:
                valve_state = as_bool(get_entity_value(entities_state, area.thermal_collector_valve_switch))

            try:
                zone_sensor: SensorPair = make_class(
                    SensorPair,
                    temperature=room_t,
                    humidity=room_rh,
                    dew_point=room_dp if area.indoor else None,
                    heat_index=room_hi if area.indoor else None,
                )
                zone_snapshot: ZoneSnapshot = make_class(
                    ZoneSnapshot,
                    timestamp=timestamp,
                    name=area.name,

                    sensors=zone_sensor,

                    flow_t=flow_t if area.radiant else None,
                    return_t=return_t if area.radiant else None,

                    act_state=valve_state if area.radiant else None,
                )
                zone_snapshots[ area.name ] = zone_snapshot
            except TypeError as ex:
                # Parametri mancanti/extra o mismatch firma costruttore
                line = [
                    f"Error creating ZoneSnapshot for area {name} - {exc_one_line(ex)}",
                    f"room t entity: {sensors.temperature}, value: {get_entity_value(entities_state, sensors.temperature)}",
                    f"room rh entity: {sensors.humidity}, value: {get_entity_value(entities_state, sensors.humidity)}",
                    f"room dp entity: {sensors.dew_point}, value: {get_entity_value(entities_state, sensors.dew_point)}",
                    f"room hi entity: {sensors.heat_index}, value: {get_entity_value(entities_state, sensors.heat_index)}",
                    f"valve entity: {area.thermal_collector_valve_switch}, value: {get_entity_value(entities_state, area.thermal_collector_valve_switch)}",
                ]
                log_warning(_LOGGER, "\n".join(line))
                # _LOGGER.warning("Error creating ZoneSnapshot for area %s: %s", name, ex, exc_info=True)
                continue
            except Exception as ex:
                # Qualsiasi altro errore inaspettato
                _LOGGER.exception("Unexpected error creating ZoneSnapshot for area %s: %s", name, ex)
                continue

        return zone_snapshots

    def _build_pdc_snapshot(runtime_config: RuntimeConfig, ts: datetime) -> PDCSnapshot | None:

        radiant: RadiantConfig | None = runtime_config.climate.devices.radiant
        if radiant is None:
            return None

        try:
            pdc_snapshot: PDCSnapshot = make_class(
                PDCSnapshot,
                timestamp=ts,
                fm_power_on=as_bool(get_entity_value(entities_state, radiant.fm_power)) or False,
                power_on=as_bool(get_entity_value(entities_state, radiant.power)) or False,
                device_mode=as_int(get_entity_value(entities_state, radiant.mode.actuator)),
                wot_heat=as_float(get_entity_value(entities_state, radiant.heating_t_setpoint.actuator)),
                delta_t_heat=as_float(get_entity_value(entities_state, radiant.heating_dt_setpoint.actuator)),
                wot_cool=as_float(get_entity_value(entities_state, radiant.cooling_t_setpoint.actuator)),
                delta_t_cool=as_float(get_entity_value(entities_state, radiant.cooling_dt_setpoint.actuator)),
                sensor_t_water_in_pe=as_float(get_entity_value(entities_state, radiant.sensors.pdc_temp_water_in)),
                sensor_t_water_out_pe=as_float(get_entity_value(entities_state, radiant.sensors.pdc_temp_water_out)),
                minutes_power_on=None, #TODO
                minutes_power_off=None,
            )

            return pdc_snapshot
        except TypeError as ex:
            # Parametri mancanti/extra o mismatch firma costruttore
            _LOGGER.warning("Error creating PDCSnapshot %s", ex, exc_info=True)
            return None
        except Exception as ex:
            # Qualsiasi altro errore inaspettato
            _LOGGER.exception("Unexpected error creating PDCSnapshot %s", ex)
            return None

    def _build_supply_unit_snapshot(runtime_config: RuntimeConfig, ts: datetime) -> SupplyUnitSnapshot | None:

        supply_unit: SupplyUnitsConfig | None = runtime_config.climate.devices.supply_units
        if supply_unit is None:
            return None

        try:
            supply_unic_snapshot: SupplyUnitSnapshot = make_class(
                SupplyUnitSnapshot,
                timestamp=ts,
                direct_su_power_on=as_bool(get_entity_value(entities_state, supply_unit.direct_supply_unit)) or False,
                adjustable_su_power_on=as_bool(get_entity_value(entities_state, supply_unit.adjustable_supply_unit)) or False,
                three_point_mixing_valve=as_int(get_entity_value(entities_state, supply_unit.three_point_mixing_valve)),
                sensor_boiler_temp_system_supply=as_float(get_entity_value(entities_state, supply_unit.sensors.boiler_temp_system_supply)),
                sensor_boiler_temp_system_return=as_float(get_entity_value(entities_state, supply_unit.sensors.boiler_temp_system_return)),
                sensor_adjustable_temp_system_supply=as_float(get_entity_value(entities_state, supply_unit.sensors.adjustable_temp_system_supply)),
                sensor_adjustable_temp_system_return=as_float(get_entity_value(entities_state, supply_unit.sensors.adjustable_temp_system_return)),
                sensor_direct_temp_system_supply=as_float(get_entity_value(entities_state, supply_unit.sensors.direct_temp_system_supply)),
                sensor_direct_temp_system_return=as_float(get_entity_value(entities_state, supply_unit.sensors.direct_temp_system_return)),
            )
            return supply_unic_snapshot
        except TypeError as ex:
            # Parametri mancanti/extra o mismatch firma costruttore
            _LOGGER.warning("Error creating SupplyUnitSnapshot %s", ex, exc_info=True)
            return None
        except Exception as ex:
            # Qualsiasi altro errore inaspettato
            _LOGGER.exception("Unexpected error creating SupplyUnitSnapshot %s", ex)
            return None

    def _build_vmc_snapshot(runtime_config: RuntimeConfig, ts: datetime) -> VMCSnapshot | None:

        vmc: VMCConfig | None = runtime_config.climate.devices.vmc
        if vmc is None:
            return None

        try:
            vmc_snapshot: VMCSnapshot = make_class(
                VMCSnapshot,
                timestamp=ts,
                power_on=as_bool(get_entity_value(entities_state, vmc.power)) or False,
                t_setpoint=as_float(get_entity_value(entities_state, vmc.t_setpoint)),
                rh_setpoint=as_float(get_entity_value(entities_state, vmc.h_setpoint)),
                t_dew_point_setpoint=as_float(get_entity_value(entities_state, vmc.t_dew_point_setpoint)),
                delta_t_dew_point_setpoint=as_float(get_entity_value(entities_state, vmc.delta_t_dew_point_setpoint)),
                spare_setpoint=as_int(get_entity_value(entities_state, vmc.spare_setpoint)),
                
                act_vent_recirculation=as_bool(get_entity_value(entities_state, vmc.vent_recirculation)) or False,
                act_force_heating=as_bool(get_entity_value(entities_state, vmc.force_heating)) or False,
                act_force_cooling=as_bool(get_entity_value(entities_state, vmc.force_cooling)) or False,
                act_force_free_cooling=as_bool(get_entity_value(entities_state, vmc.force_free_cooling)) or False,

                processing_mode=get_entity_value(entities_state, vmc.season.actuator),
                compressor_management=as_int(get_entity_value(entities_state, vmc.compressor_management.actuator)),
                cooling_management=as_int(get_entity_value(entities_state, vmc.cooling_management.actuator)),

                request_water=as_bool(get_entity_value(entities_state, vmc.requests.water)) or False,
                request_dehumidification=as_bool(get_entity_value(entities_state, vmc.requests.dehumidification)) or False,
                request_heating=as_bool(get_entity_value(entities_state, vmc.requests.heating)) or False,
                request_cooling=as_bool(get_entity_value(entities_state, vmc.requests.cooling)) or False,
                
                sensor_t_ambient=as_float(get_entity_value(entities_state, vmc.sensors.t_ambient)),
                sensor_h_ambient=as_float(get_entity_value(entities_state, vmc.sensors.h_ambient)),
                sensor_t_water=as_float(get_entity_value(entities_state, vmc.sensors.t_water)),
                sensor_t_outdoor=as_float(get_entity_value(entities_state, vmc.sensors.t_outdoor)),
                sensor_power_on_night=None,
                sensor_power_on_today=None,
                
                alarm_high_pressure=as_bool(get_entity_value(entities_state, vmc.alarms.high_pressure)) or False,
                alarm_dew_point=as_bool(get_entity_value(entities_state, vmc.alarms.dew_point)) or False,
                alarm_low_water_temp=as_bool(get_entity_value(entities_state, vmc.alarms.low_water_temp)) or False,
                alarm_high_water_temp=as_bool(get_entity_value(entities_state, vmc.alarms.high_water_temp)) or False,
                alarm_alarm=as_bool(get_entity_value(entities_state, vmc.alarms.alarm)) or False,
            )
            return vmc_snapshot
        except TypeError as ex:
            # Parametri mancanti/extra o mismatch firma costruttore
            _LOGGER.warning("Error creating VMCSnapshot %s", ex, exc_info=True)
            return None
        except Exception as ex:
            # Qualsiasi altro errore inaspettato
            _LOGGER.exception("Unexpected error creating VMCSnapshot %s", ex)
            return None
        
    # --- Main logic --------------------------------------------------------
    zones_snapshot: dict[str, ZoneSnapshot] =_build_zones_snapshot(runtime_config, timestamp)
    terrace_area = zones_snapshot.get('Terrace') if zones_snapshot else None

    try:
        mean_apt: SensorPair | None = make_class(
            SensorPair,
            temperature=as_float(get_entity_value(entities_state, runtime_config.climate.mean_apt.temperature)),
            humidity=as_float(get_entity_value(entities_state, runtime_config.climate.mean_apt.humidity)),
            dew_point=as_float(get_entity_value(entities_state, runtime_config.climate.mean_apt.dew_point)),
            heat_index=as_float(get_entity_value(entities_state, runtime_config.climate.mean_apt.heat_index)),
        )
    except TypeError as ex:
        mean_apt = None
    
    # log_debug(_LOGGER, f"zones_snapshot: {zones_snapshot}" )
    # log_debug(_LOGGER, f"terrace_area: {terrace_area}" )
    outdoor: SensorPair = make_class(
        SensorPair,
        temperature=terrace_area.sensors.temperature if terrace_area and terrace_area.sensors else None,
        humidity=terrace_area.sensors.humidity if terrace_area and terrace_area.sensors else None,
    )

    return make_class(
        PlantSnapshot,
        timestamp=timestamp,
        season=season,
        zones=zones_snapshot,

        mean_apt=mean_apt,
        outdoor=outdoor,

        pdc=_build_pdc_snapshot(runtime_config, timestamp),
        supply_unit=_build_supply_unit_snapshot(runtime_config, timestamp),
        vmc=_build_vmc_snapshot(runtime_config, timestamp),

        home_windows_state=as_bool(get_entity_value(entities_state, runtime_config.climate.home_windows_state)) or False,
        presence_vacation=as_bool(get_entity_value(entities_state, runtime_config.climate.scenarios.vacation)) or False,
        presence_nobodysin=as_bool(get_entity_value(entities_state, runtime_config.climate.scenarios.nobodysin)) or False,
    )




