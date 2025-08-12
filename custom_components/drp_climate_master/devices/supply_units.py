

import operator
from typing import Any
import logging

from homeassistant.helpers.entity import Entity

from homeassistant.core import HomeAssistant
from homeassistant.components.climate.const import HVACMode
from homeassistant.const import (
    # CONF_NAME,
    CONF_SENSORS,
    STATE_OFF,
    STATE_ON,
)

from ..utils.const import (
    # CONF_ACTUATOR,
    CONF_ADJUSTABLE_SUPPLY_UNIT,
    CONF_ADJUSTABLE_TEMP_SYSTEM_RETURN,
    CONF_ADJUSTABLE_TEMP_SYSTEM_SUPPLY,
    # CONF_ALARM, 
    CONF_ALARMS,
    CONF_AREA,
    CONF_AREAS,
    CONF_BOILER_TEMP_SYSTEM_RETURN,
    CONF_BOILER_TEMP_SYSTEM_SUPPLY,
    # CONF_COMPRESSOR_MANAGEMENT,
    # CONF_COOLING,
    # CONF_COOLING_MANAGEMENT,
    # CONF_DEHUMIDIFICATION,
    # CONF_DELTA_DEW_POINT_SETPOINT, 
    # CONF_DEW_POINT,
    # CONF_DEW_POINT_SETPOINT,
    CONF_DIRECT_SUPPLY_UNIT,
    CONF_DIRECT_TEMP_SYSTEM_RETURN,
    CONF_DIRECT_TEMP_SYSTEM_SUPPLY,
    # CONF_FM_POWER, 
    # CONF_FORCE_COOLING, 
    # CONF_FORCE_HEATING, 
    # CONF_H_AMBIENT, 
    # CONF_H_SETPOINT,
    # CONF_HEATING, 
    # CONF_HIGH_PRESSURE, 
    CONF_HIGH_WATER_TEMP, 
    # CONF_HOME_WINDOWS_STATE,
    # CONF_HUMIDITY,
    CONF_INDOOR, 
    # CONF_LOW_WATER_TEMP,
    # CONF_MODE,
    CONF_MQ,
    # CONF_PDC_TEMP_WATER_IN,
    # CONF_PDC_TEMP_WATER_OUT, 
    CONF_POWER, 
    # CONF_POWER_ON_NIGHT, 
    # CONF_POWER_ON_TODAY,
    CONF_RADIANT,
    CONF_REQUESTS, 
    # CONF_SEASON, 
    # CONF_SPARE_SETPOINT, 
    # CONF_T_AMBIENT, 
    # CONF_T_OUTDOOR, 
    # CONF_T_SETPOINT, 
    # CONF_T_WATER,
    CONF_TCOLLECTOR,
    CONF_TEMPERATURE,
    CONF_THREE_POINT_MIXING_VALVE, 
    # CONF_VENT_RECIRCULATION,
    CONF_VMC,
    CONF_WATER,
    # CONF_WINTER,
    DOMAIN,
    HNX_NAME_POSTFIX,
    SENSOR_CURRENT_HNX_UID,
    # STATE_UNAVAILABLE,
    STATES,
    T_BOILER_SYSTEM_RETURN_POWER_OFF,
    T_BOILER_SYSTEM_RETURN_POWER_ON,
    T_BOILER_SYSTEM_SUPPLY_POWER_ON,
    TURN_OFF,
    TURN_ON,
    SeasonSetpoint,
    Seasons,
)
from ..utils.database import enquiry_entity_seconds_in_state
from ..utils.helpers import (
    async_number_set_value,
    async_switch_turn,
    convert_to_float,
    # filter_dict_by_key_prefix,
    get_entity_state,
    is_entity_state,
    is_number,
    setup_entity_change
)

_LOGGER = logging.getLogger(__name__)

class SupplyUnits(Entity):
    """Supply Units device class."""

    def __init__(
            self, 
            hass: HomeAssistant, 
            device_config: dict[str, Any],
            shared_config: dict[str, Any],
    ) -> None:
        """Initialize the class"""
        self._hass = hass
        self._config = device_config
        self._shared_config = shared_config
        self._sensors_config = self._config.get(CONF_SENSORS, {})

        entity_ids : list[str] = []

        self._power_supply_direct_id = self._config.get(CONF_DIRECT_SUPPLY_UNIT, "")
        entity_ids.append(self._power_supply_direct_id)
        self._power_supply_adjustable_id = self._config.get(CONF_ADJUSTABLE_SUPPLY_UNIT, "")
        entity_ids.append(self._power_supply_adjustable_id)
        self._power_supply_mixing_valve_id = self._config.get(CONF_THREE_POINT_MIXING_VALVE, "")
        entity_ids.append(self._power_supply_mixing_valve_id)

        self._boiler_temp_system_supply_id = self._sensors_config.get(CONF_BOILER_TEMP_SYSTEM_SUPPLY)
        entity_ids.append(self._boiler_temp_system_supply_id)
        self._boiler_temp_system_return_id = self._sensors_config.get(CONF_BOILER_TEMP_SYSTEM_RETURN)
        entity_ids.append(self._boiler_temp_system_return_id)
        self._adjustable_temp_system_supply_id = self._sensors_config.get(CONF_ADJUSTABLE_TEMP_SYSTEM_SUPPLY)
        entity_ids.append(self._adjustable_temp_system_supply_id)
        self._adjustable_temp_system_return_id = self._sensors_config.get(CONF_ADJUSTABLE_TEMP_SYSTEM_RETURN)
        entity_ids.append(self._adjustable_temp_system_return_id)
        self._direct_temp_system_supply_id = self._sensors_config.get(CONF_DIRECT_TEMP_SYSTEM_SUPPLY)
        entity_ids.append(self._direct_temp_system_supply_id)
        self._direct_temp_system_return_id = self._sensors_config.get(CONF_DIRECT_TEMP_SYSTEM_RETURN)
        entity_ids.append(self._direct_temp_system_return_id)

        setup_entity_change(self, self._async_entity_changed, entity_ids)

    async def _async_entity_changed(self, event):
        """Handle sensor changes."""
        entity_id = event.data.get("entity_id")
        new_state = event.data.get("new_state")
        self._hass.data[DOMAIN][STATES][entity_id] = new_state
        # _LOGGER.debug( "_async_entity_changed '%s' status change '%s'.", str(entity_id), str(new_state) )

    @property
    def _global_entities(self) -> dict:
        """Return the name of the device."""
        return self._hass.data[DOMAIN][STATES]
    
    @property
    def _home_felt_temperature(self) -> (float | None):
        return convert_to_float( get_entity_state( self._global_entities, SENSOR_CURRENT_HNX_UID ) )
    
    # @property
    # def sensor_data(self) -> Any:
    #     return filter_dict_by_key_prefix(self._global_entities, "sensor.")

    @property
    def _is_vmc_device_high_water_t_on(self) -> (bool | None):    
        entity_state = is_entity_state( self._global_entities, self._shared_config.get(CONF_VMC, {}).get(CONF_ALARMS).get(CONF_HIGH_WATER_TEMP), STATE_ON )
        return (False if entity_state is None else entity_state) 
    
    @property
    def _is_vmc_device_high_water_t_off(self) -> (bool | None):    
        return not self._is_vmc_device_high_water_t_on
    
    @property
    def _is_vmc_device_water_request_on(self) -> (bool | None):    
        entity_state = is_entity_state( self._global_entities, self._shared_config.get(CONF_VMC, {}).get(CONF_REQUESTS).get(CONF_WATER), STATE_ON )
        return (False if entity_state is None else entity_state) 

    @property
    def _is_vmc_device_water_request_off(self) -> (bool | None):    
        return not self._is_vmc_device_water_request_on 
    
    @property
    def _is_vmc_device_power_on(self) -> (bool | None):    
        entity_state = is_entity_state( self._global_entities, self._shared_config.get(CONF_VMC, {}).get(CONF_POWER), STATE_ON )
        return (False if entity_state is None else entity_state) 

    @property
    def _is_vmc_device_power_off(self) -> (bool | None):    
        return not self._is_vmc_device_power_on
    
    @property
    def _is_pdc_device_power_on(self) -> (bool | None):    
        entity_state = is_entity_state( self._global_entities, self._shared_config.get(CONF_RADIANT, {}).get(CONF_POWER), STATE_ON )
        return (False if entity_state is None else entity_state) 

    @property
    def _is_pdc_device_power_off(self) -> bool:
        return not self._is_pdc_device_power_on
    
    @property
    def _is_power_supply_adjustable_on(self) -> (bool | None):    
        entity_state = is_entity_state( self._global_entities, self._power_supply_adjustable_id, STATE_ON )
        return (False if entity_state is None else entity_state) 
    
    @property
    def _is_power_supply_adjustable_off(self) -> (bool | None):  
        # entity_state = is_entity_state( self._global_entities, self._power_supply_adjustable_id, STATE_OFF )
        return not self._is_power_supply_adjustable_on

    def _thermal_collector_request_on(self, entity_id: str, temperature: float) -> (bool | None):  
        entity_state = is_entity_state( self._global_entities, entity_id, temperature, operator.le )
        return (False if entity_state is None else entity_state) 

    def _thermal_collector_request_off(self, entity_id: str, temperature: float) -> (bool | None):  
        entity_state = is_entity_state( self._global_entities, entity_id, temperature, operator.ge )
        return (False if entity_state is None else entity_state) 
    
    def _is_entity_on(self, entity_id: str) -> (bool | None):  
        entity_state = is_entity_state( self._global_entities, entity_id, STATE_ON )
        return (False if entity_state is None else entity_state)

    def _is_power_supply_ready_to(self, entity_id: str, turn: str) -> (bool | None):    
        entity_state = is_entity_state( self._global_entities, entity_id, (STATE_ON if TURN_OFF == turn else STATE_OFF) )
        return (False if entity_state is None else entity_state) 

    def _is_power_supply_adjustable_ready_to(self, turn: str) -> (bool | None):    
        entity_state = is_entity_state( self._global_entities, self._power_supply_adjustable_id, (STATE_ON if TURN_OFF == turn else STATE_OFF) )
        return (False if entity_state is None else entity_state) 
    
    def _is_power_supply_direct_ready_to(self, turn: str) -> (bool | None):    
        entity_state = is_entity_state( self._global_entities, self._power_supply_direct_id, (STATE_ON if TURN_OFF == turn else STATE_OFF) )
        return (False if entity_state is None else entity_state) 
    
    def _is_mixing_valve_percentage_ne(self, percentage: float) -> (bool | None):    
        entity_state = is_entity_state( self._global_entities, self._power_supply_mixing_valve_id, percentage, operator.ne )
        return (False if entity_state is None else entity_state) 

    def _is_boiler_hot_water_ready(self, sensor_water_temp: float) -> (bool | None):
        entity_state = is_entity_state( self._global_entities, self._boiler_temp_system_supply_id, min(27, sensor_water_temp), operator.gt )
        return (False if entity_state is None else entity_state) 
            
    def _is_boiler_system_supply_t_gt(self, temp: float) -> (bool | None):    
        entity_state = is_entity_state( self._global_entities, self._boiler_temp_system_supply_id, temp, operator.gt )
        return (False if entity_state is None else entity_state) 

    def _is_boiler_system_return_t_lt(self, temp: float) -> (bool | None):    
        entity_state = is_entity_state( self._global_entities, self._boiler_temp_system_return_id, temp, operator.lt )
        return (False if entity_state is None else entity_state) 

    def _is_boiler_system_return_t_gt(self, temp: float) -> (bool | None):    
        entity_state = is_entity_state( self._global_entities, self._boiler_temp_system_return_id, temp, operator.gt )
        return (False if entity_state is None else entity_state) 
    
    # async def _async_power_supply_mixing_valve_turn(
    #     self,
    #     turn : str,
    #     context : str = '_async_power_supply_mixing_valve_turn'
    # ) -> None:
    #     """Power Supply Mixing Valve Turn"""

    #     if self._is_power_supply_adjustable_ready_to( turn ):
    #         await async_switch_turn( self, self._power_supply_adjustable_id, turn )
    #         _LOGGER.info( "%s - Adjustable device power '%s'", context, turn.upper() )

    # async def _async_power_supply_direct_turn(
    #     self,
    #     turn : str,
    #     context : str = '_async_power_supply_direct_turn'
    # ) -> None:
    #     """Direct Power Supply Turn"""
    #     if self._is_power_supply_direct_ready_to( turn ):
    #         await async_switch_turn( self, self._power_supply_direct_id, turn )
    #         _LOGGER.info( "%s - Direct device power '%s'", context, turn.upper() )

    async def _async_mixing_valve_setpoint(
        self,
        # device_data : dict,
        percentage : float,
        context : str = '_async_mixing_valve_setpoint'
    ) -> None:
        """mixing valve setpoint"""
        # adjustable_power_on_state = is_entity_state( device_data, self._power_supply_adjustable_id, STATE_ON )
        # adjustable_power_on_state = (adjustable_power_on_state is not None and adjustable_power_on_state)

        if self._is_power_supply_adjustable_on:
            # mixing_valve_value_state = is_entity_state( device_data, self._power_supply_mixing_valve_id, percentage, operator.ne )
            # mixing_valve_value_state = (mixing_valve_value_state is not None and mixing_valve_value_state)
            if self._is_mixing_valve_percentage_ne( percentage ):
                await async_number_set_value( self, self._power_supply_mixing_valve_id, percentage )
                _LOGGER.info( "%s - Set %s to %s", context, str(self._power_supply_mixing_valve_id), str(percentage) )

    async def _async_thermal_collector_valve_turn(
        self,
        # device_data : dict,
        entity_id : str,
        turn : str,
        context : str = '_async_thermal_collector_valve_turn'
    ) -> Any:
        """Thermal Collector valve turn"""
        if self._is_power_supply_ready_to( entity_id, turn ):
            await async_switch_turn( self, entity_id, turn )
            _LOGGER.info( "%s - Collector valve '%s' switch '%s'", context, entity_id, turn.upper() )

    # async def _async_thermal_collector_switch_off(
    #     self,
    #     context : str = '_async_thermal_collector_switch_off'
    # ) -> Any:
    #     """Thermal Collector switch OFF"""
    #     for area in self._shared_config[CONF_AREAS]:
    #         if area.get(CONF_INDOOR) and area.get(CONF_RADIANT):
    #             await self._async_thermal_collector_valve_turn( area.get(CONF_TCOLLECTOR), TURN_OFF, context )
    #             # _LOGGER.info( "_async_thermal_collector_switch_off - Collector valve '%s' switch OFF", area.get(CONF_TCOLLECTOR) )

#     async def _async_thermal_collector_control_auto(
#         self,
#         # climate_setpoints : SeasonSetpoint,
#         climate_setpoint_off: float,
#         climate_setpoint_on: float,
#     ) -> Any:
#         """Thermal Collector controller"""
#         adjust_valve_percent = 0
#         indoor_area_count = 0
#         ceiling_area = 0
#         opened_area = 0

#         # climate_setpoint_off = climate_setpoints.get(CONF_TEMPERATURE).get(STATE_OFF)
#         # climate_setpoint_on = climate_setpoints.get(CONF_TEMPERATURE).get(STATE_ON)
#         climate_setpoint_on = climate_setpoint_avg = (climate_setpoint_on + climate_setpoint_off) / 2

#         enquiry_response = enquiry_entity_seconds_in_state( 
#             self._shared_config.get(CONF_RADIANT, {}).get(CONF_POWER), STATE_ON
#         )
#         # _LOGGER.debug( "_async_thermal_collector_control_auto - '%s'", str(enquiry_response) )
#         pdc_on_minutes = enquiry_response['updated'] / 60 if enquiry_response else -1

#         if self._is_pdc_device_power_off and self._is_power_supply_adjustable_off:
#             await self._async_thermal_collector_switch_off()
#             return convert_to_float(adjust_valve_percent)

#         for area in self._shared_config[CONF_AREAS]:
#             if area.get(CONF_INDOOR) and area.get(CONF_RADIANT):
#                 indoor_area_count += 1
#                 ceiling_area += area.get(CONF_MQ)

#                 t_entity_id = area.get(CONF_SENSORS).get(CONF_TEMPERATURE)
#                 t_felt_id = (f"sensor.Ambient {area[CONF_AREA]} {HNX_NAME_POSTFIX}").lower().replace(" ", "_").replace("-", "_")

#                 t_area = get_entity_state( self._global_entities, t_entity_id ) or -99.0 # device_data.get(t_entity_id)
#                 t_felt = get_entity_state( self._global_entities, t_felt_id ) or -99.0 # device_data.get(t_felt_id)

#                 if (not is_number(t_area)) or (not is_number(t_felt)) or t_area == -99.0 or t_felt == -99.0:
#                     _LOGGER.warning( "_async_thermal_collector_control_auto - No sensor data for room '%s' '%s' '%s' %s %s", 
#                         area.get(CONF_AREA), t_entity_id, t_felt_id, str(t_area), str(t_felt) )
#                     continue

#                 valve_switch_on_state = self._is_entity_on( area.get(CONF_TCOLLECTOR) )

#                 if valve_switch_on_state:
#                     opened_area += area.get(CONF_MQ)

#                 thc_valve_request_on = self._thermal_collector_request_on( t_felt_id, climate_setpoint_on )
#                 thc_valve_request_off = self._thermal_collector_request_off( t_felt_id, climate_setpoint_off )
#                 thc_valve_request_off_in_time = self._thermal_collector_request_off( t_felt_id, climate_setpoint_off + 0.5)

#                 if (pdc_on_minutes <= 120 and not thc_valve_request_off_in_time) or thc_valve_request_on:
#                     await self._async_thermal_collector_valve_turn( area.get(CONF_TCOLLECTOR), TURN_ON)                    
#                 elif thc_valve_request_off: # t_felt_id >= climate_setpoint_off
#                     await self._async_thermal_collector_valve_turn( area.get(CONF_TCOLLECTOR), TURN_OFF) 

#                 RESULTS = f"""
# -------------------------------------------------------------------
# _async_thermal_collector_control_auto results:
# collector_valve_switch :: '{str(area.get(CONF_AREA))}' state: {str(valve_switch_on_state)} request ON: {thc_valve_request_on} request OFF: {thc_valve_request_off} last on minute: {pdc_on_minutes}
# Temperature Felt       :: {str(t_felt_id)} {float(t_felt):.2f}
# Set points             :: min: {climate_setpoint_on:.2f} felt: {float(t_felt):.2f} real: {float(t_area):.2f} max: {climate_setpoint_off:.2f} 
# -------------------------------------------------------------------
#             """
#                 _LOGGER.debug(RESULTS)

#         adjust_valve_percent = round( (1 - ( ( ceiling_area - opened_area) / ceiling_area)) * 100, 0 )

#         RESULTS = f"""
# -------------------------------------------------------------------
# _async_thermal_collector_control_auto results:
# Thermal collector valves :: {indoor_area_count:.2f}
# Radiant panel area       :: {opened_area:.2f} mq \\ {ceiling_area:.2f} mq - {adjust_valve_percent:.2f}%
# -------------------------------------------------------------------
#             """
#         _LOGGER.debug(RESULTS)

#         return convert_to_float(adjust_valve_percent)

    async def _async_thermal_collector_control_auto(
        self,
        climate_setpoint_off: float,
        climate_setpoint_on: float,
    ) -> float:
        """Controllo automatico collettore termico."""
        adjust_valve_percent = 0
        indoor_area_count = 0
        ceiling_area = 0
        opened_area = 0

        # Media dei setpoint per apertura anticipata
        climate_setpoint_avg = (climate_setpoint_on + climate_setpoint_off) / 2

        enquiry_response = enquiry_entity_seconds_in_state(
            self._shared_config.get(CONF_RADIANT, {}).get(CONF_POWER), STATE_ON
        )
        pdc_on_minutes = enquiry_response['updated'] / 60 if enquiry_response else -1

        if self._is_pdc_device_power_off and self._is_power_supply_adjustable_off:
            await self._async_device_controller(thermal_collector_switch_power_off=True)
            return convert_to_float(adjust_valve_percent) or 0

        for area in self._shared_config[CONF_AREAS]:
            if not (area.get(CONF_INDOOR) and area.get(CONF_RADIANT)):
                continue

            indoor_area_count += 1
            ceiling_area += area.get(CONF_MQ)

            t_entity_id = area.get(CONF_SENSORS, {}).get(CONF_TEMPERATURE)
            t_felt_id = (f"sensor.Ambient {area[CONF_AREA]} {HNX_NAME_POSTFIX}").lower().replace(" ", "_").replace("-", "_")

            t_area = get_entity_state(self._global_entities, t_entity_id) or -99.0
            t_felt = get_entity_state(self._global_entities, t_felt_id) or -99.0

            if not (is_number(t_area) and is_number(t_felt)) or t_area == -99.0 or t_felt == -99.0:
                _LOGGER.warning(
                    "_async_thermal_collector_control_auto - No sensor data for room '%s' (real: %s, felt: %s)",
                    area.get(CONF_AREA), t_area, t_felt
                )
                continue

            valve_switch_on = self._is_entity_on(area.get(CONF_TCOLLECTOR))
            if valve_switch_on:
                opened_area += area.get(CONF_MQ)

            # Decisione di apertura/chiusura
            request_on = self._thermal_collector_request_on(t_felt_id, climate_setpoint_avg)
            request_off = self._thermal_collector_request_off(t_felt_id, climate_setpoint_off)
            request_off_in_time = self._thermal_collector_request_off(t_felt_id, climate_setpoint_off + 0.5)

            if (pdc_on_minutes <= 120 and not request_off_in_time) or request_on:
                await self._async_thermal_collector_valve_turn(area.get(CONF_TCOLLECTOR), TURN_ON)
            elif request_off:
                await self._async_thermal_collector_valve_turn(area.get(CONF_TCOLLECTOR), TURN_OFF)

            _LOGGER.debug(
                "\nRoom '%s': Valve ON: %s, Request ON: %s, Request OFF: %s, pdc_on_minutes: %.1f\n"
                " - T_felt: %.2f°C, T_real: %.2f°C, Setpoint avg: %.2f°C - Setpoint off: %.2f°C",
                area.get(CONF_AREA), valve_switch_on, request_on, request_off, pdc_on_minutes,
                float(t_felt), float(t_area), climate_setpoint_avg, climate_setpoint_off
            )

        # Calcolo della percentuale di apertura
        if ceiling_area > 0:
            adjust_valve_percent = round((opened_area / ceiling_area) * 100, 0)

        _LOGGER.debug(
            "\n-------------------------------------------------------------------\n"
            "Thermal collector control result:\n"
            "Indoor zones: %d\n"
            "Opened area: %.2f m² / Total ceiling area: %.2f m²\n"
            "Valve opening: %.2f%%\n"
            "-------------------------------------------------------------------",
            indoor_area_count, opened_area, ceiling_area, adjust_valve_percent
        )

        return convert_to_float(adjust_valve_percent) or 0.0

    async def _async_device_controller(
            self, 
            direct_distribution_unit_power: str | None = None,
            mixing_distribution_unit_power: str | None = None,
            thermal_collector_switch_power_off: bool = False,
    ) -> None:
        """hvac controller"""   

        if direct_distribution_unit_power in (TURN_ON, TURN_OFF):
            if self._is_power_supply_direct_ready_to( direct_distribution_unit_power ):
                await async_switch_turn( self, self._power_supply_direct_id, direct_distribution_unit_power )
                _LOGGER.info( "_async_device_controller - Direct Distribution Unit set to power '%s'", direct_distribution_unit_power.upper() )

        if mixing_distribution_unit_power in (TURN_ON, TURN_OFF):
            if self._is_power_supply_adjustable_ready_to( mixing_distribution_unit_power ):
                await async_switch_turn( self, self._power_supply_adjustable_id, mixing_distribution_unit_power )
                _LOGGER.info( "_async_device_controller - Adjustable Distribution Unit set to power '%s'", mixing_distribution_unit_power.upper() )

        if thermal_collector_switch_power_off:
            for area in self._shared_config[CONF_AREAS]:
                if area.get(CONF_INDOOR) and area.get(CONF_RADIANT):
                    await self._async_thermal_collector_valve_turn( area.get(CONF_TCOLLECTOR), TURN_OFF, "_async_device_controller" )

    async def async_hvac_control(
            self, 
            hvac_mode: str,
            season : Seasons,
            climate_setpoints : SeasonSetpoint,
    ) -> None:
        """hvac controller"""    
        
        if hvac_mode == HVACMode.AUTO and self._global_entities is not None:
            if season == Seasons.WINTER:
                await self._async_hvac_control_auto_for_winter( climate_setpoints )
            elif season == Seasons.SPRING:
                felt = self._home_felt_temperature
                if felt and felt <= (climate_setpoints.heating.state_off or 0.0 if climate_setpoints.heating else 0.0):
                    await self._async_hvac_control_auto_for_winter( climate_setpoints )
                    return

                elif felt and felt >= (climate_setpoints.cooling.state_off or 50.0 if climate_setpoints.cooling else 50.0):
                    # await self.async_hvac_control_auto_for_summer()
                    return
                else:
                    await self._async_hvac_control_auto_for_spring( climate_setpoints )
                    return
            return

    async def _async_hvac_control_auto_for_winter(
            self, 
            climate_setpoints : SeasonSetpoint,
    ) -> None:
        """hvac winter controller"""

        enquiry_response = enquiry_entity_seconds_in_state( 
            self._shared_config.get(CONF_RADIANT, {}).get(CONF_POWER), STATE_OFF
        )
        minutes_pdc_power_off = enquiry_response['updated'] / 60 if enquiry_response else -1

# ########## # ########## # ########## # ########## #
# VMC is Power ON
# PDC is Power ON
# VMC Plant Water Request is ON
# VMC High Water Temperature is OFF
# BOILER Hot Water Ready
# ########## # ########## # ########## # ########## #
        should_turn_on = (
            self._is_vmc_device_power_on
            and self._is_pdc_device_power_on
            and self._is_vmc_device_water_request_on
            and self._is_vmc_device_high_water_t_off
            and self._is_boiler_hot_water_ready( (climate_setpoints.heating.state_on or 0.0) if climate_setpoints.heating else 0.0 )
        )

        await self._async_device_controller(
            direct_distribution_unit_power=TURN_ON if should_turn_on else TURN_OFF
        )

# ########## # ########## # ########## # ########## #
#  PDC is Power OFF for more of 120 minutes
# ########## # ########## # ########## # ########## #
        if self._is_pdc_device_power_off and (
            minutes_pdc_power_off >= 120 or self._is_boiler_system_return_t_lt( T_BOILER_SYSTEM_RETURN_POWER_OFF )
        ):
            await self._async_device_controller(mixing_distribution_unit_power=TURN_OFF, thermal_collector_switch_power_off=True)
            return

        if (
            self._is_boiler_system_supply_t_gt( T_BOILER_SYSTEM_SUPPLY_POWER_ON ) \
            and self._is_boiler_system_return_t_gt( T_BOILER_SYSTEM_RETURN_POWER_ON )
        ):
            await self._async_device_controller(mixing_distribution_unit_power=TURN_ON)
        
# ########## # ########## # ########## # ########## #
#  PDC is Power ON
# ########## # ########## # ########## # ########## #
        mixing_valve_percent_open = await self._async_thermal_collector_control_auto( 
            (climate_setpoints.heating.state_off or 0.0) if climate_setpoints.heating else 0.0,
            (climate_setpoints.heating.state_on or 0.0) if climate_setpoints.heating else 0.0,
        )
        if mixing_valve_percent_open == 0:
            await self._async_device_controller(direct_distribution_unit_power=TURN_OFF, thermal_collector_switch_power_off=True)
            return 

        await self._async_mixing_valve_setpoint( max(60, mixing_valve_percent_open), 'async_hvac_control_auto_for_winter')


#     async def async_hvac_control_auto_for_winter(
#             self, 
#             climate_setpoints : Any,
#     ) -> Any:
#         """hvac winter controller"""

#         enquiry_response = enquiry_entity_seconds_in_state( 
#             self._shared_config.get(CONF_RADIANT, {}).get(CONF_POWER), STATE_OFF
#         )
#         pdc_on_minutes = enquiry_response['updated'] / 60 if enquiry_response else -1

# # ########## # ########## # ########## # ########## #
# # If VMC is power ON 
# # ########## # ########## # ########## # ########## #
#         if self._is_vmc_device_power_on:
#             if self._is_vmc_device_water_request_on and self._is_boiler_system_supply_t_gt( 27 ) and self._is_vmc_device_high_water_t_off:
#                 await self._async_power_supply_direct_turn( TURN_ON )
#             else:
#                 await self._async_power_supply_direct_turn( TURN_OFF )
#         else:
#             await self._async_power_supply_direct_turn( TURN_OFF )

# # ########## # ########## # ########## # ########## #
# # If PDC is power OFF for more of 120 minutes
# # 1. Switch OFF thermal collector valves
# # 2. Switch OFF adjustable power supply
# # ########## # ########## # ########## # ########## #
#         # _LOGGER.debug( "async_hvac_control_auto_for_winter %s %s", str(pdc_power_off_state), str(enquiry_entity_seconds_in_state( shared_config.get(CONF_RADIANT).get(CONF_POWER), STATE_OFF )) )
#         if self._is_pdc_device_power_off and pdc_on_minutes >= 120:
#             await self._async_thermal_collector_switch_off()
#             await self._async_power_supply_mixing_valve_turn( TURN_OFF )
#             return False          
        
#         mixing_valve_perventage = await self._async_thermal_collector_control_auto( climate_setpoints )
# # ########## # ########## # ########## # ########## #
# # If PDC is power ON or power OFF and Thermal collector valves are closed
# # 1. Switch OFF adjustable power supply 
# # ########## # ########## # ########## # ########## #
#         if mixing_valve_perventage == 0:
#             await self._async_power_supply_mixing_valve_turn( TURN_OFF )
#             return False
#         else:
#             await self._async_mixing_valve_setpoint( max(60, mixing_valve_perventage), 'async_hvac_control_auto_for_winter')
# # ########## # ########## # ########## # ########## #
# # If PDC is power ON and temperature boiler is for ON
# # 1. Switch ON adjustable power supply 
# # ########## # ########## # ########## # ########## #
#         if self._is_pdc_device_power_on:
#             if self._is_boiler_system_supply_t_gt( T_BOILER_SYSTEM_SUPPLY_POWER_ON ) \
#                     and self._is_boiler_system_return_t_gt( T_BOILER_SYSTEM_RETURN_POWER_ON ):
#                 await self._async_power_supply_mixing_valve_turn( TURN_ON )
#                 return True

#         if self._is_pdc_device_power_off:
#             if self._is_boiler_system_return_t_lt( T_BOILER_SYSTEM_RETURN_POWER_OFF ):      
#                 await self._async_power_supply_mixing_valve_turn( TURN_OFF )
#                 return True

    async def _async_hvac_control_auto_for_spring(
            self, 
            climate_setpoints : SeasonSetpoint,
    ) -> None:
        """hvac spring controller"""

        should_turn_on = (
            self._is_vmc_device_power_on
            and self._is_vmc_device_water_request_on
            and self._is_vmc_device_high_water_t_off
            # and self._is_boiler_hot_water_ready( (climate_setpoints.heating.state_on or 0.0) if climate_setpoints.heating else 0.0 )
        )
        await self._async_device_controller(
            direct_distribution_unit_power=TURN_ON if should_turn_on else TURN_OFF
        )
