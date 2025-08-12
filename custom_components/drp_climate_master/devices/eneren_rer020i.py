""" ... """
from enum import Enum
import operator
from statistics import mean
from typing import Any, Optional
import logging
from datetime import datetime, date

from homeassistant.helpers.entity import Entity

from homeassistant.core import HomeAssistant
from homeassistant.components.climate.const import HVACMode
from homeassistant.const import (
    CONF_SENSORS,
    STATE_OFF,
    STATE_ON,
)

from ..utils.psychrometric import dew_point
# from sqlalchemy import false

from ..utils.const import (
    CONF_ACTUATOR,
    CONF_ALARM, 
    CONF_ALARMS,
    CONF_AREA,
    CONF_AREAS,
    CONF_COMPRESSOR_MANAGEMENT,
    CONF_COOLING,
    CONF_COOLING_MANAGEMENT,
    CONF_DEHUMIDIFICATION,
    CONF_DELTA_DEW_POINT_SETPOINT, 
    CONF_DEW_POINT,
    CONF_DEW_POINT_SETPOINT, 
    CONF_FORCE_COOLING,
    CONF_FORCE_FREE_COOLING, 
    CONF_FORCE_HEATING, 
    CONF_H_AMBIENT, 
    CONF_H_SETPOINT,
    CONF_HEATING, 
    CONF_HIGH_PRESSURE, 
    CONF_HIGH_WATER_TEMP,
    CONF_HOME_WINDOWS_STATE,
    CONF_HUMIDITY, 
    # CONF_HUMIDITY, 
    CONF_LOW_WATER_TEMP, 
    CONF_POWER, 
    CONF_POWER_ON_NIGHT, 
    CONF_POWER_ON_TODAY,
    CONF_REQUESTS, 
    CONF_SEASON, 
    CONF_SPARE_SETPOINT,
    CONF_SPRING,
    # CONF_SPRING,
    CONF_SUMMER, 
    CONF_T_AMBIENT, 
    CONF_T_OUTDOOR, 
    CONF_T_SETPOINT, 
    CONF_T_WATER,
    CONF_TEMPERATURE,
    CONF_TERRACE, 
    CONF_VENT_RECIRCULATION,
    CONF_WATER,
    CONF_WINTER,
    COOLING,
    DOMAIN,
    HEATING,
    PROCESSING_OFF,
    SENSOR_CURRENT_DWP_UID,
    SENSOR_CURRENT_HNX_UID,
    SENSOR_CURRENT_HUMI_UID,
    STATES,
    TURN_OFF,
    TURN_ON,
    SeasonSetpoint,
    Seasons,
    TimeRange,
    TimeRangeSet,
)
from ..utils.database import enquiry_entity_for_time_in_maxi_value, enquiry_entity_seconds_in_state
from ..utils.helpers import (
    async_input_select_set_value,
    async_number_set_value,
    async_switch_turn,
    convert_to_float,
    # filter_dict_by_key_prefix,
    get_entity_state,
    is_entity_state,
    setup_entity_change
)

_LOGGER = logging.getLogger(__name__)

class DeviceAction(str, Enum):
    """ ... """
    FREECOOLING = "freecooling"
    EXTERNAL_AIR = "external_air"
    INTERNAL_RECIRCULATION = "internal_recirculation"

class EnerenRER020I(Entity):
    """Enerer RER020I device class."""

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
        self._season_config = self._config.get(CONF_SEASON, [])
        self._compressor_management_config = self._config.get(CONF_COMPRESSOR_MANAGEMENT, [])
        self._cooling_management_config = self._config.get(CONF_COOLING_MANAGEMENT, [])
        self._requests_config = self._config.get(CONF_REQUESTS, [])
        self._config_sensors = self._config.get(CONF_SENSORS, [])
        self._config_alarms = self._config.get(CONF_ALARMS, [])
        
        self._max_of_day_date = None
        self._max_of_day_result = None
        self._current_season : Optional[Seasons] = None
        self._current_climate_setpoints: Optional[SeasonSetpoint] = None

        # _LOGGER.info( "__init__ '%s'", str(self._config) )
        values : list[str] = []
        self._power_entity_id = self._config.get(CONF_POWER, [])
        values.append(self._power_entity_id)
        self._t_setpoint_entity_id = self._config.get(CONF_T_SETPOINT, [])
        values.append(self._t_setpoint_entity_id)
        self._h_setpoint_entity_id = self._config.get(CONF_H_SETPOINT, [])
        values.append(self._h_setpoint_entity_id)
        self._dewp_setpoint_entity_id = self._config.get(CONF_DEW_POINT_SETPOINT, [])
        values.append(self._dewp_setpoint_entity_id)
        self._delta_dewp_setpoint_entity_id = self._config.get(CONF_DELTA_DEW_POINT_SETPOINT, [])
        values.append(self._delta_dewp_setpoint_entity_id)
        self._spare_setpoint_id = self._config.get(CONF_SPARE_SETPOINT, [])
        values.append(self._spare_setpoint_id)
        self._vent_recirculation_id = self._config.get(CONF_VENT_RECIRCULATION, [])
        values.append(self._vent_recirculation_id)
        self._force_heating_id = self._config.get(CONF_FORCE_HEATING, [])
        values.append(self._force_heating_id)
        self._force_cooling_id = self._config.get(CONF_FORCE_COOLING, [])
        values.append(self._force_cooling_id)
        self._force_free_cooling_id = self._config.get(CONF_FORCE_FREE_COOLING, [])
        values.append(self._force_free_cooling_id)

        values.append(self._season_config.get(CONF_ACTUATOR, []))
        values.append(self._compressor_management_config.get(CONF_ACTUATOR, []))
        values.append(self._cooling_management_config.get(CONF_ACTUATOR, []))

        values.append(self._requests_config.get(CONF_WATER))
        values.append(self._requests_config.get(CONF_DEHUMIDIFICATION))
        values.append(self._requests_config.get(CONF_HEATING))
        values.append(self._requests_config.get(CONF_COOLING))

        self._t_ambient = self._config_sensors.get(CONF_T_AMBIENT)
        values.append(self._t_ambient)
        self._h_ambient = self._config_sensors.get(CONF_H_AMBIENT)
        values.append(self._h_ambient)
        self._t_water = self._config_sensors.get(CONF_T_WATER)
        values.append(self._t_water)
        self._t_outdoor = self._config_sensors.get(CONF_T_OUTDOOR)
        values.append(self._t_outdoor)
        self._power_on_night = self._config_sensors.get(CONF_POWER_ON_NIGHT)
        values.append(self._power_on_night)
        self._power_on_today = self._config_sensors.get(CONF_POWER_ON_TODAY)
        values.append(self._power_on_today)

        self._high_pressure = self._config_alarms.get(CONF_HIGH_PRESSURE)
        values.append(self._high_pressure) 
        self._dew_point = self._config_alarms.get(CONF_DEW_POINT)
        values.append(self._dew_point)
        self._low_water_temp = self._config_alarms.get(CONF_LOW_WATER_TEMP)
        values.append(self._low_water_temp)
        self._high_water_temp = self._config_alarms.get(CONF_HIGH_WATER_TEMP)
        values.append(self._high_water_temp)
        self._alarm = self._config_alarms.get(CONF_ALARM)
        values.append(self._alarm)

        setup_entity_change(self, self._async_entity_changed, values)

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
    def _power_state(self) -> str:
        """Return the power entity state"""
        return self._global_entities[self._power_entity_id]

    @property
    def _processing_mode(self) -> str:
        """Return the power entity state"""
        return self._global_entities[self._season_config.get(CONF_ACTUATOR)]
    
    @property
    def _spare_value(self) -> int:
        """Return the power entity state"""
        return self._global_entities[self._spare_setpoint_id]
    
    @property
    def _home_felt_temperature(self) -> (float | None):
        return convert_to_float( get_entity_state( self._global_entities, SENSOR_CURRENT_HNX_UID ) )
    
    @property
    def _home_humidity(self) -> (float | None):
        return convert_to_float( get_entity_state( self._global_entities, SENSOR_CURRENT_HUMI_UID ) )    

    @property
    def _home_dew_point(self) -> (float | None):
        return convert_to_float( get_entity_state( self._global_entities, SENSOR_CURRENT_DWP_UID ) )   
    
    # @property
    # def device_data(self) -> Any:
    #     return self._global_entities
    
    # @property
    # def sensor_data(self) -> Any:
    #     return filter_dict_by_key_prefix(self._global_entities, "sensor.")

    @property
    def _is_home_windows_closed(self) -> (bool | None):
        entity_state = is_entity_state( self._global_entities, self._shared_config.get(CONF_HOME_WINDOWS_STATE, ""), STATE_ON )
        return (False if entity_state is None else entity_state) 
    
    @property
    def _terrace_temperature(self) -> float | None:
        terrace_area_config = {}
        for area in self._shared_config.get( CONF_AREAS, []):
            if area.get(CONF_AREA).lower() == CONF_TERRACE:
                terrace_area_config = area
                break 
        return convert_to_float( get_entity_state( self._global_entities, terrace_area_config.get(CONF_SENSORS, []).get(CONF_TEMPERATURE, "") ) )

    @property
    def _terrace_humidity(self) -> float | None:
        terrace_area_config = {}
        for area in self._shared_config.get( CONF_AREAS, []):
            if area.get(CONF_AREA).lower() == CONF_TERRACE:
                terrace_area_config = area
                break 
        return convert_to_float( get_entity_state( self._global_entities, terrace_area_config.get(CONF_SENSORS, []).get(CONF_HUMIDITY, "") ) )

    @property
    def _terrace_temperature_entity_id(self) -> str:
        terrace_area_config = {}
        for area in self._shared_config.get( CONF_AREAS, []):
            if area.get(CONF_AREA).lower() == CONF_TERRACE:
                terrace_area_config = area
                break 
        return terrace_area_config.get(CONF_SENSORS, []).get(CONF_TEMPERATURE, "")

    @property
    def _is_vmc_device_power_on(self) -> (bool | None):    
        entity_state = is_entity_state( self._global_entities, self._power_entity_id, STATE_ON )
        return (False if entity_state is None else entity_state) 

    @property
    def _is_vmc_device_power_off(self) -> (bool | None):    
        return not self._is_vmc_device_power_on 

    @property
    def _is_device_processing_off(self) -> (bool | None):    
        entity_state = is_entity_state( self._global_entities, self._season_config.get(CONF_ACTUATOR), PROCESSING_OFF )
        return (False if entity_state is None else entity_state) 

    @property
    def _is_device_processing_winter(self) -> (bool | None):    
        entity_state = is_entity_state( self._global_entities, self._season_config.get(CONF_ACTUATOR), self._season_config.get(CONF_WINTER) )
        return (False if entity_state is None else entity_state) 
    
    @property
    def _is_device_processing_summer(self) -> (bool | None):    
        entity_state = is_entity_state( self._global_entities, self._season_config.get(CONF_ACTUATOR), self._season_config.get(CONF_SUMMER) )
        return (False if entity_state is None else entity_state) 
    
    @property
    def _is_device_vent_recirculation_off(self) -> (bool | None):    
        return not self._is_device_vent_recirculation_on

    @property
    def _is_device_vent_recirculation_on(self) -> (bool | None):    
        entity_state = is_entity_state( self._global_entities, self._vent_recirculation_id, STATE_ON )
        return (False if entity_state is None else entity_state) 

    @property
    def _is_device_free_cooling_on(self) -> (bool | None):    
        entity_state = is_entity_state( self._global_entities, self._force_free_cooling_id, STATE_ON )
        return (False if entity_state is None else entity_state) 

    @property
    def _is_device_free_cooling_off(self) -> (bool | None): 
        return not self._is_device_free_cooling_on
    
    @property
    def _time_range_max_temperature( self ) -> TimeRange:
        """Restituisce una fascia oraria come oggetto TimeRange centrato sull'ora media del massimo valore giornaliero."""
    
        now = datetime.now()

        if self._max_of_day_date is None or self._max_of_day_date != date.today():
            self._max_of_day_date = date.today()
            self._max_of_day_result = enquiry_entity_for_time_in_maxi_value( self._terrace_temperature_entity_id )
        
        if self._max_of_day_result and len(self._max_of_day_result) == 3:
            return TimeRange(
                start = now.replace(
                    hour=(self._max_of_day_result[0]-2), 
                    minute=(self._max_of_day_result[1]), 
                    second=(self._max_of_day_result[2]), 
                    microsecond=0
                ),
                end = now.replace(
                    hour=(self._max_of_day_result[0]+2), 
                    minute=(self._max_of_day_result[1]), 
                    second=(self._max_of_day_result[2]), 
                    microsecond=0
                )
            )

        # Fallback se non ci sono dati
        return TimeRange.from_hours(12, 0, 16, 0)

    def _is_device_t_setpoint_eq(self, temp: float) -> (bool | None):  
        entity_state = is_entity_state( self._global_entities, self._t_setpoint_entity_id, temp, operator.eq )
        return (False if entity_state is None else entity_state) 

    def _is_device_h_setpoint_eq(self, humidity: float) -> (bool | None):  
        entity_state = is_entity_state( self._global_entities, self._h_setpoint_entity_id, humidity, operator.eq )
        return (False if entity_state is None else entity_state) 

    def _is_device_dp_setpoint_eq(self, temp: float) -> (bool | None):  
        entity_state = is_entity_state( self._global_entities, self._dewp_setpoint_entity_id, temp, operator.eq )
        # _LOGGER.info( "_is_device_dp_setpoint_eq - %s %s %s", self._dewp_setpoint_entity_id, str(temp), entity_state  )
        return (False if entity_state is None else entity_state) 
    
    def _is_device_spare_eq(self, spare: float) -> (bool | None):  
        entity_state = is_entity_state( self._global_entities, self._spare_setpoint_id, spare, operator.eq )
        return (False if entity_state is None else entity_state) 

#     async def _async_device_power_off(self,) -> None:
#         if self._is_vmc_device_power_on:
# # ########## # ########## # ########## # ########## #
# # Set processing OFF
# # ########## # ########## # ########## # ########## #
#             if not self._is_device_processing_off:
#                 await async_input_select_set_value( self, self._season_config.get(CONF_ACTUATOR), PROCESSING_OFF )
#                 _LOGGER.info( "_async_device_power_off - Set processing to Off" )
# # ########## # ########## # ########## # ########## #
# # Set vent recirculation OFF
# # ########## # ########## # ########## # ########## #     
#             if self._is_device_vent_recirculation_on:
#                 await async_switch_turn( self, self._vent_recirculation_id, TURN_OFF )
#                 _LOGGER.info( "_async_device_power_off - Set Vent Recirculation OFF" )

# # ########## # ########## # ########## # ########## #
# # Device power OFF
# # ########## # ########## # ########## # ########## #
#             await async_switch_turn( self, self._power_entity_id, TURN_OFF )
#             _LOGGER.info( "_async_device_power_off - Device power OFF" )

    async def _async_device_controller(
            self, 
            power: str | None = None,
            processing_mode: str | None = None,
            spare: int | None = None,
            vent_recirculation: str | None = None,
            force_free_cooling: str | None = None,
            setpoint_t: float | None = None,
            setpoint_h: float | None = None,
            setpoint_dp: float | None = None,
    ) -> None:
        """hvac controller"""
        DEBUG_MESSAGE = "_async_device_controller results:\n"

        if self._is_vmc_device_power_on:

            if any([
                processing_mode == CONF_WINTER and not self._is_device_processing_winter,
                processing_mode == CONF_SUMMER and not self._is_device_processing_summer,
                processing_mode == CONF_SPRING and not self._is_device_processing_off, 
                processing_mode == PROCESSING_OFF and not self._is_device_processing_off,
            ]):
                await async_input_select_set_value( self, self._season_config.get(CONF_ACTUATOR), processing_mode )
                _LOGGER.info( "_async_device_controller - Set processing mode to %s", processing_mode )
            else:
                 DEBUG_MESSAGE += f"processing_mode    :: {processing_mode} no action\n"

            if spare and not self._is_device_spare_eq( spare ):
                await async_number_set_value( self, self._spare_setpoint_id, spare )
                _LOGGER.info( "_async_device_controller - Set Spare to %s", str(spare) )
            else:
                DEBUG_MESSAGE += f"spare              :: {spare} no action \n"

            if vent_recirculation and any([
                vent_recirculation == TURN_ON and self._is_device_vent_recirculation_off,
                vent_recirculation == TURN_OFF and self._is_device_vent_recirculation_on,
            ]):
                await async_switch_turn( self, self._vent_recirculation_id, vent_recirculation )
                _LOGGER.info( "_async_device_controller - Set Vent Recirculation %s", vent_recirculation )
            else:
                DEBUG_MESSAGE += f"vent_recirculation :: {vent_recirculation} no action\n"

            if force_free_cooling and any([
                force_free_cooling == TURN_ON and self._is_device_free_cooling_off,
                force_free_cooling == TURN_OFF and self._is_device_free_cooling_on,
            ]):
                await async_switch_turn( self, self._force_free_cooling_id, force_free_cooling )
                _LOGGER.info( "_async_device_controller - Set Force Free Cooling %s", force_free_cooling )
            else:
                DEBUG_MESSAGE += f"force_free_cooling :: {vent_recirculation} no action\n"

            if power == TURN_OFF:
                await async_input_select_set_value( self, self._season_config.get(CONF_ACTUATOR), PROCESSING_OFF )
                _LOGGER.info( "_async_device_controller - Set processing to Off" )
                await async_switch_turn( self, self._force_free_cooling_id, TURN_OFF )
                _LOGGER.info( "_async_device_controller - Set Force Free Cooling OFF" )
                await async_switch_turn( self, self._vent_recirculation_id, TURN_OFF )
                _LOGGER.info( "_async_device_controller - Set Vent Recirculation OFF" )
                await async_switch_turn( self, self._power_entity_id, TURN_OFF )
                _LOGGER.info( "_async_device_controller - Put device power OFF" )
            else:
                DEBUG_MESSAGE += f"power              :: {power} no action\n"

            if setpoint_t and not self._is_device_t_setpoint_eq( setpoint_t ):
                await async_number_set_value( self, self._t_setpoint_entity_id, setpoint_t )
                _LOGGER.info( "_async_device_controller - Put device T SetPoint to %s", str(setpoint_t) )
            else:
                DEBUG_MESSAGE += f"setpoint T         :: {setpoint_t} no action\n"

            if setpoint_h and not self._is_device_h_setpoint_eq( setpoint_h ):
                await async_number_set_value( self, self._h_setpoint_entity_id, setpoint_h )
                _LOGGER.info( "_async_device_controller - Put device H SetPoint to %s", str(setpoint_h) )
            else:
                DEBUG_MESSAGE += f"setpoint H         :: {setpoint_h} no action\n"

            if setpoint_dp and not self._is_device_dp_setpoint_eq( setpoint_dp ):
                await async_number_set_value( self, self._dewp_setpoint_entity_id, setpoint_dp )
                _LOGGER.info( "_async_device_controller - Put device Dew Point SetPoint to %s", str(setpoint_dp) )
            else:
                DEBUG_MESSAGE += f"setpoint Dew Point :: {setpoint_dp} no action\n"

        else:
            if power == TURN_ON:
                await async_switch_turn( self, self._power_entity_id, TURN_ON )
                _LOGGER.info( "_async_device_controller - Put device power ON" )
                if processing_mode or spare or vent_recirculation:
                    await self._async_device_controller( processing_mode=processing_mode, spare=spare, vent_recirculation=vent_recirculation)
            else:
                DEBUG_MESSAGE += f"power              :: {power} no action\n"

        if _LOGGER.isEnabledFor(logging.DEBUG):
            _LOGGER.debug( DEBUG_MESSAGE ) 


    def _decide_air_strategy(self) -> DeviceAction:
        """
        Determina la strategia di ventilazione ideale in base alla stagione.
        Considera comfort zone, temperatura, umidità e dew point.
        """
        action = DeviceAction.INTERNAL_RECIRCULATION

        # Validazioni iniziali
        if not (
            self._home_felt_temperature
            and self._home_humidity
            and self._home_dew_point
            and self._terrace_temperature
            and self._terrace_humidity
            and self._current_season
            and self._current_climate_setpoints
            and self._current_climate_setpoints.temperature_max
            and self._current_climate_setpoints.temperature_min
            and self._current_climate_setpoints.humidity_max
            and self._current_climate_setpoints.humidity_min
        ):
            return action

        # Letture e conversioni
        temp_in = self._home_felt_temperature
        hum_in = self._home_humidity
        dp_in = self._home_dew_point

        temp_out = self._terrace_temperature
        hum_out = self._terrace_humidity
        dp_out = dew_point(temp_out, hum_out)

        t_upper = self._current_climate_setpoints.temperature_max
        t_lower = self._current_climate_setpoints.temperature_min
        t_comfort_avg = mean([t_upper, t_lower])
        h_max = self._current_climate_setpoints.humidity_max
        h_min = self._current_climate_setpoints.humidity_min

        MESSAGE = f"""
------------------------------------------------------
🌤️ Air Strategy - Season: {self._current_season}
  • Comfort Temp Range = {t_lower:.1f}°C - {t_upper:.1f}°C
  • Comfort Hum Range  = {h_min:.1f}% - {h_max:.1f}%
  • Temp In/Out        = {temp_in:.1f}°C / {temp_out:.1f}°C (ΔT = {temp_in - temp_out:.1f})
  • Hum In/Out         = {hum_in:.1f}% / {hum_out:.1f}% (ΔH = {hum_in - hum_out:.1f})
  • DewPoint In/Out    = {dp_in:.1f}°C / {dp_out:.1f}°C (ΔDP = {dp_in - dp_out:.1f})"""

        # Strategie personalizzate per stagione
        if self._current_season == Seasons.WINTER:
            # Quanto si supera il comfort (in eccesso)
            temp_excess = temp_in - t_upper  # es. 23.6 - 20.2 = 3.4

            # Se interno sopra comfort
            if temp_excess > 0:
                # Delta T dinamico: più si supera il comfort, più il delta richiesto si riduce
                # Delta minimo accettabile: 0.5°C
                dynamic_delta_t = max(0.5, 1.5 - (temp_excess / 2))
                # Esempio: se supera di 3.4°C → delta richiesto ≈ max(0.5, 1.5 - 1.7) → 0.5°C

                if temp_out < temp_in - dynamic_delta_t and dp_out < dp_in:
                    action = DeviceAction.FREECOOLING
                    MESSAGE += f"""
→ Selected             = {temp_excess:.1f}°C → delta T richiesto = {dynamic_delta_t:.1f}°C.
→ Selected condition   = {temp_out:.1f}°C < {temp_in - dynamic_delta_t:.1f}°C and {dp_out:.1f}°C < {dp_in:.1f}°C"""

            # Altrimenti, aria esterna se condizioni miti
            elif temp_out > 16 and dp_out > 6 and dp_out < dp_in - 1:
                action = DeviceAction.EXTERNAL_AIR
                MESSAGE += f"""
→ Selected condition   = {temp_out:.1f}°C > 16°C, {dp_out:.1f}°C > 6°C, {dp_out:.1f}°C < {(dp_in - 1):.1f}°C"""

            # (Opzionale) Forza ricambio se CO2 troppo alta, ignorando temperatura
        #     elif hasattr(self, "_home_co2") and self._home_co2 and self._home_co2 > 1000:
        #         action = DeviceAction.EXTERNAL_AIR
        #         MESSAGE += f"""
        # → Forced EXTERNAL_AIR due to high CO2: {self._home_co2} ppm"""

        elif self._current_season == Seasons.SUMMER:
            if temp_in > t_comfort_avg and temp_out < temp_in - 1.5 and dp_out < dp_in - 1.0:
                action = DeviceAction.FREECOOLING
            elif t_lower <= temp_in <= t_upper and temp_out < temp_in - 1.0 and dp_out < dp_in:
                action = DeviceAction.EXTERNAL_AIR

        elif self._current_season in (Seasons.SPRING, Seasons.AUTUMN):
            if temp_in > t_comfort_avg and temp_out < temp_in - 0.5 and dp_out < dp_in:
                action = DeviceAction.FREECOOLING
            elif t_lower <= temp_in <= t_upper and temp_out < temp_in - 0.5 and dp_out < dp_in + 1.0:
                action = DeviceAction.EXTERNAL_AIR

        # Log di debug
#         MESSAGE = f"""
# ------------------------------------------------------
# 🌤️ Air Strategy - Season: {self._current_season}
#   • Comfort Temp Range = {t_lower:.1f}°C - {t_upper:.1f}°C
#   • Comfort Hum Range  = {h_min:.1f}% - {h_max:.1f}%
#   • Temp In/Out        = {temp_in:.1f}°C / {temp_out:.1f}°C (ΔT = {temp_in - temp_out:.1f})
#   • Hum In/Out         = {hum_in:.1f}% / {hum_out:.1f}% (ΔH = {hum_in - hum_out:.1f})
#   • DewPoint In/Out    = {dp_in:.1f}°C / {dp_out:.1f}°C (ΔDP = {dp_in - dp_out:.1f})
#   → Selected Strategy  = {action.name}
# ------------------------------------------------------
# """
        MESSAGE += f"""
  → Selected Strategy  = {action.name}
------------------------------------------------------
"""
        _LOGGER.debug(MESSAGE)
        return action


#     def _decide_air_strategy___( self ) -> DeviceAction:
#         """
#         Determina la strategia di ventilazione ideale in primavera.
#         Considera comfort zone, temperatura, umidità e dew point.
#         """
#         action = DeviceAction.INTERNAL_RECIRCULATION

#         # Validazioni
#         if not (
#             self._home_felt_temperature
#             and self._home_humidity
#             and self._home_dew_point
#             and self._terrace_temperature
#             and self._terrace_humidity
#             and self._current_climate_setpoints
#             and self._current_climate_setpoints.temperature_max
#             and self._current_climate_setpoints.temperature_min
#             and self._current_climate_setpoints.humidity_max
#             and self._current_climate_setpoints.humidity_min
#         ):
#             return DeviceAction.INTERNAL_RECIRCULATION

#         # Comfort zone primavera
#         t_upper = self._current_climate_setpoints.temperature_max 
#         t_lower = self._current_climate_setpoints.temperature_min
#         t_comfort_avg = mean([t_upper, t_lower])
#         h_max = self._current_climate_setpoints.humidity_max
#         h_min = self._current_climate_setpoints.humidity_min

#         temp_in: float = self._home_felt_temperature       # temperatura interna (media o felt)
#         hum_in: float = self._home_humidity                # umidità interna
#         temp_out: float = self._terrace_temperature        # temperatura esterna (es. terrazzo)
#         hum_out: float = self._terrace_humidity            # umidità esterna

#         # Dew point
#         dp_in = self._home_dew_point
#         dp_out = dew_point(temp_out, hum_out)

#         # Differenze
#         temp_diff = temp_in - temp_out
#         hum_diff = hum_in - hum_out
#         dp_diff = dp_in - dp_out

#         # 🔹 Caso ideale per il FREECOOLING
#         if (
#             temp_in > t_comfort_avg 
#             and temp_out < temp_in - 0.5 
#             and dp_out < dp_in  # L'aria esterna è più asciutta
#         ):
#             action = DeviceAction.FREECOOLING

#         # 🔹 Scambio aria con esterno (senza freecooling)
#         elif (
#             t_lower <= temp_in <= t_upper 
#             and temp_out < temp_in - 0.5 
#             and dp_out < dp_in + 1.0  # Accettabile ma non ottimale
#         ):
#             action = DeviceAction.EXTERNAL_AIR

#         # 🔹 Nessuna condizione favorevole
#         else:
#             action = DeviceAction.INTERNAL_RECIRCULATION

#     # 🔍 LOG di debug
#         MESSAGE = f"""
# ------------------------------------------------------
# 🌿 Spring Air Strategy
#   • Comfort Range = {t_lower}°C - {t_upper}°C | {h_min}% - {h_max}%
#   • Free cooling  = temp_in > t_comfort_avg and temp_out < temp_in - 0.5 and dp_out < dp_in
#                   = {temp_in:.1f}°C > {t_comfort_avg:.1f}°C and {temp_out:.1f}°C < {(temp_in - 0.5):.1f}°C ({temp_in > t_comfort_avg and temp_out < temp_in - 0.5}) and {dp_out}°C < {dp_in}°C ({dp_out < dp_in})
#   • External Air  = t_lower <= temp_in <= t_upper and temp_out < temp_in - 0.5 and dp_out < dp_in + 1.0
#                   = {t_lower:.1f}°C <= {temp_in:.1f}°C <= {t_upper:.1f}°C and {temp_out:.1f}°C < {(temp_in - 0.5):.1f}°C ({t_lower <= temp_in <= t_upper and temp_out < temp_in - 0.5}) and {dp_out}°C < {dp_in + 1.0}°C ({dp_out < dp_in + 1.0})
#   • Temp IN  = {temp_in:.1f}°C
#   • Hum IN   = {hum_in:.1f}%
#   • Temp OUT = {temp_out:.1f}°C
#   • Hum OUT  = {hum_out:.1f}%
#   • Dew Point IN  = {dp_in}°C
#   • Dew Point OUT = {dp_out}°C
#   => Selected Action: {action.name}
# ------------------------------------------------------
# """
#         _LOGGER.debug( MESSAGE )

#         # Nessuna condizione favorevole, resta in ricircolo
#         return action

    async def async_hvac_control(
            self, 
            hvac_mode: str,
            season : Seasons,
            climate_setpoints: SeasonSetpoint,
    ) -> None:
        """hvac controller"""

        self._current_season = season
        self._current_climate_setpoints = climate_setpoints
        
        if hvac_mode == HVACMode.AUTO and self._global_entities is not None and climate_setpoints is not None:
# ########## # ########## # ########## # ########## #
# Se le finestre sono chiuse controlla il device
# ########## # ########## # ########## # ########## #
            if self._is_home_windows_closed:
                if self._is_vmc_device_power_on:
                    await self._async_device_controller(
                        setpoint_h=climate_setpoints.humidity_max,
                        setpoint_dp=max(10, min(climate_setpoints.dew_point or 10, 40)) 
                    )

                if season == Seasons.WINTER:
                    if self._is_vmc_device_power_on:
                        await self._async_device_controller(
                            processing_mode=self._season_config.get(CONF_WINTER),
                            setpoint_t=climate_setpoints.heating.state_off if climate_setpoints.heating else None,
                            setpoint_h=climate_setpoints.humidity_max,
                            setpoint_dp=max(10, min(climate_setpoints.dew_point or 10, 40))
                        )

                    await self.async_hvac_control_auto_for_winter()
                    return
                
                elif season == Seasons.SPRING:
                    if self._is_vmc_device_power_on and self._home_felt_temperature:
                        felt = self._home_felt_temperature
                        climate_setpoints_mode = climate_setpoints.action_by_temperature( felt )

                        if climate_setpoints_mode is None:
                            await self._async_device_controller(
                                processing_mode=self._season_config.get(CONF_SPRING),
                                setpoint_t=climate_setpoints.temperature_max,
                                setpoint_h=climate_setpoints.humidity_max,
                                setpoint_dp=max(10, min(climate_setpoints.dew_point or 10, 40))
                            )                            
                        elif climate_setpoints_mode.mode == HEATING:
                            await self._async_device_controller(
                                processing_mode=self._season_config.get(CONF_WINTER),
                                setpoint_t=climate_setpoints.heating.state_off if climate_setpoints.heating else None,
                                setpoint_h=climate_setpoints.humidity_max,
                                setpoint_dp=max(10, min(climate_setpoints.dew_point or 10, 40))
                            )
                            await self.async_hvac_control_auto_for_winter()
                            return
                        elif climate_setpoints_mode.mode == COOLING:
                            await self._async_device_controller(
                                processing_mode=self._season_config.get(CONF_SUMMER),
                                setpoint_t=climate_setpoints.cooling.state_off if climate_setpoints.cooling else None,
                                setpoint_h=climate_setpoints.humidity_max,
                                setpoint_dp=max(10, min(climate_setpoints.dew_point or 10, 40))
                            )
                            # await self.self.async_hvac_control_auto_for_summer()
                            return

                    await self._async_hvac_control_auto_for_spring()
            else:
                enquiry_result = enquiry_entity_seconds_in_state( 
                        self._shared_config.get(CONF_HOME_WINDOWS_STATE, ""), STATE_OFF 
                )
                if enquiry_result and round(enquiry_result['updated'] / 60 ) > 10:
                    await self._async_device_controller( power=TURN_OFF )
                

    async def async_hvac_control_auto_for_winter(
            self, 
    ) -> None:
        """hvac Winter controller"""
        _LOGGER.debug( "async_hvac_control_auto_for_winter" )
        night_range = TimeRange.from_hours(0,00, 7,00)
        morning_range = TimeRange.from_hours(7,00, 9,30)
        daytime_range = self._time_range_max_temperature # E' l'intervallo più caldo

        processing_mode = self._season_config.get(CONF_WINTER)
        force_free_cooling=TURN_OFF 
        vent_recirculation = TURN_OFF

        air_strategy = self._decide_air_strategy()
        match air_strategy:
            case DeviceAction.FREECOOLING:
                processing_mode = PROCESSING_OFF
                force_free_cooling=TURN_ON 
                vent_recirculation = TURN_OFF
            case DeviceAction.EXTERNAL_AIR:
                processing_mode = self._season_config.get(CONF_WINTER)
                force_free_cooling=TURN_OFF
                vent_recirculation = TURN_OFF
            case DeviceAction.INTERNAL_RECIRCULATION:
                processing_mode = self._season_config.get(CONF_WINTER)
                force_free_cooling=TURN_OFF
                vent_recirculation = TURN_ON

        if night_range.is_now_inside():
# ########## # ########## # ########## # ########## #
# Scambia aria con l'interno
# 1. Power ON
# 2. Trattamento : Off
# 3. Set Spare to 1
# 4. Vent Recirculation ON
# ########## # ########## # ########## # ########## #
            await self._async_device_controller( 
                power=TURN_ON, 
                processing_mode=PROCESSING_OFF, 
                spare=1,
                force_free_cooling=TURN_OFF,
                vent_recirculation=TURN_ON )
        elif morning_range.is_now_inside():
            await self._async_device_controller( 
                power=TURN_ON, 
                processing_mode=self._season_config.get(CONF_WINTER), 
                spare=5,
                force_free_cooling=TURN_OFF,
                vent_recirculation=TURN_OFF )
        elif daytime_range.is_now_inside():
# ########## # ########## # ########## # ########## #
# Scambia aria con l'esterno
# 1. Power ON
# 2. Trattamento : Inverno
# 3. Set Spare to 5
# 4. Vent Recirculation OFF
# ########## # ########## # ########## # ########## #
            await self._async_device_controller( 
                power=TURN_ON, 
                processing_mode=processing_mode,
                spare=5, 
                force_free_cooling=force_free_cooling,
                vent_recirculation=vent_recirculation 
            )

        else:
# ########## # ########## # ########## # ########## #
# Put device power off
# ########## # ########## # ########## # ########## #
            await self._async_device_controller( power=TURN_OFF )
            # return True    

    async def _async_hvac_control_auto_for_spring(
            self, 
    ) -> Any:
        """hvac controller"""
        night_range = TimeRangeSet.from_hours_list([
            (23, 00, 00, 00),   # 08:00 -> 10:00
            (00, 00, 7, 00),  # 15:00 -> 18:00
        ])
        morning_range = TimeRange.from_hours(7,00, 10,0)
        afternoon = TimeRange.from_hours(15,00, 23,00)

        processing_mode = self._season_config.get(CONF_WINTER)
        force_free_cooling=TURN_OFF 
        vent_recirculation = TURN_OFF

        air_strategy = self._decide_air_strategy()
        match air_strategy:
            case DeviceAction.FREECOOLING:
                processing_mode = PROCESSING_OFF
                force_free_cooling=TURN_ON 
                vent_recirculation = TURN_OFF
            case DeviceAction.EXTERNAL_AIR:
                processing_mode = self._season_config.get(CONF_WINTER)
                force_free_cooling=TURN_OFF
                vent_recirculation = TURN_OFF
            case DeviceAction.INTERNAL_RECIRCULATION:
                processing_mode = self._season_config.get(CONF_WINTER)
                force_free_cooling=TURN_OFF
                vent_recirculation = TURN_ON

        if night_range.is_now_inside():
# ########## # ########## # ########## # ########## #
# Scambia aria con l'esterno
# 1. Power ON
# 2. Trattamento : Off
# 3. Set Spare to 1
# 4. Vent Recirculation ON
# ########## # ########## # ########## # ########## #
            await self._async_device_controller( 
                power=TURN_ON, 
                processing_mode=processing_mode, # PROCESSING_OFF
                force_free_cooling=force_free_cooling, # TURN_ON
                spare=1, 
                vent_recirculation=vent_recirculation #TURN_OFF
                )
        elif morning_range.is_now_inside():
            await self._async_device_controller( 
                power=TURN_ON, 
                processing_mode=processing_mode, # PROCESSING_OFF
                force_free_cooling=force_free_cooling, # TURN_ON
                spare=5, 
                vent_recirculation=vent_recirculation # TURN_OFF
                )
        # elif afternoon.is_now_inside():
        #     await self._async_device_controller( 
        #         power=TURN_ON, 
        #         processing_mode=processing_mode, # PROCESSING_OFF
        #         force_free_cooling=force_free_cooling, # TURN_ON
        #         spare=5, 
        #         vent_recirculation=vent_recirculation # TURN_OFF
        #         )
        elif (
                self._terrace_temperature
                and self._terrace_humidity
                and self._home_felt_temperature
                and self._home_humidity
                and self._current_climate_setpoints
            ) and afternoon.is_now_inside():
            if (
                self._home_felt_temperature < self._current_climate_setpoints.temperature_max
                and self._terrace_temperature < self._home_felt_temperature - 0.5
                and dew_point(self._terrace_temperature, self._terrace_humidity) < (self._home_dew_point or -1) - 0.5
                ):            
            # ( self._terrace_temperature + 0.3 < self._home_felt_temperature
            #     and self._home_humidity < self._current_climate_setpoints.humidity_max + 5.0
            #     and self._terrace_temperature < self._current_climate_setpoints.humidity_max + 5.0):
                await self._async_device_controller( 
                    power=TURN_ON, 
                    processing_mode=processing_mode, # PROCESSING_OFF
                    force_free_cooling=force_free_cooling, # TURN_ON
                    spare=5, 
                    vent_recirculation=vent_recirculation # TURN_OFF
                )
            else:
                await self._async_device_controller( 
                    power=TURN_ON, 
                    processing_mode=self._season_config.get(CONF_SUMMER),
                    force_free_cooling=TURN_OFF,
                    spare=5, 
                    vent_recirculation=TURN_ON 
                )
        else:
# ########## # ########## # ########## # ########## #
# Put device power off
# ########## # ########## # ########## # ########## #
            await self._async_device_controller( power=TURN_OFF )
