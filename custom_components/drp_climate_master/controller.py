"""Support for Climate Devices."""
from __future__ import annotations

# import asyncio
# from datetime import datetime
# from statistics import mean
from typing import Any
# import copy

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.const import (
    CONF_SENSORS,
    Platform,
    # STATE_OFF,
    # STATE_ON,
)
from homeassistant.components.climate.const import (
    HVACMode,
)

from .entities_state import PlatformEntityStates

from .strategies import ClimateStrategies

from .utils.const import CONFORT_ZONES, COOLING, HEATING, HvacThreshold, SeasonData, Seasons

from .devices.aermec_hmi080 import AermecHDMI080
from .devices.eneren_rer020i import EnerenRER020I
from .devices.supply_units import SupplyUnits
from .utils.const import (
    # CONF_ACTUATOR, 
    CONF_AREA, 
    CONF_AREAS, 
    CONF_AVG_DEW_POINT, 
    CONF_AVG_H_INDEX, 
    CONF_DEVICES, 
    CONF_HOME_WINDOWS_STATE, 
    CONF_HUM, 
    CONF_HUMIDITY, 
    # CONF_INDOOR, 
    CONF_MQ, 
    CONF_NOBODYSIN, 
    CONF_POWER, 
    CONF_RADIANT, 
    CONF_REQUESTS, 
    CONF_SCENARIOS,
    # CONF_SPRING, 
    CONF_SUPPLY_UNITS, 
    CONF_TCOLLECTOR, 
    CONF_TEMP, 
    CONF_TEMPERATURE, 
    CONF_VACATION, 
    CONF_VMC, 
    CONF_WEATHER, 
    # CONF_WINTER, 
    # CONFORT_ZONES, 
    DOMAIN, 
    HNX_NAME_POSTFIX, 
    SENSOR_CURRENT_DWP_UID, 
    SENSOR_CURRENT_HNX_UID, 
    SENSOR_CURRENT_HUMI_UID, 
    SENSOR_CURRENT_TEMP_UID,
    SENSOR_MAX_CONFORT_H_UID,
    SENSOR_MAX_CONFORT_T_UID,
    SENSOR_MIN_CONFORT_H_UID,
    SENSOR_MIN_CONFORT_T_UID, 
    STATES,
    ConfortAttr,
    SeasonSetpoint,
    # Seasons
    )
from .utils.helpers import (
    convert_to_float,
    copy_structure,
    copy_tuple,
    # filter_dict_by_key_prefix, 
    # get_entity_state,
    setup_entity_change,
    weighted_average,
)
from .utils.psychrometric import confort_zone, dew_point, heat_index, season_data, seasons_setpoints, season_threshold
# from .utils.weather import async_weather_prediction

_LOGGER = logging.getLogger(__name__)

class Controller(Entity):
    """Climate entity class."""

    def __init__(self, hass: HomeAssistant, climate_config: dict[str, Any]) -> None:
        """Initialize t"""
        self._hass = hass
        self._climate_config = climate_config
        self._config = climate_config.get(Platform.CLIMATE, [])[0]
        self._areas_config = self._config.get(CONF_AREAS)
        
        shared_config = dict()
        copy_tuple( self._config, shared_config, CONF_HOME_WINDOWS_STATE )
        copy_tuple( self._config.get(CONF_DEVICES), shared_config, CONF_SUPPLY_UNITS )
        copy_tuple( self._config, shared_config, CONF_AREAS )
        copy_tuple( self._config, shared_config, CONF_SCENARIOS )

        self._device_eneren_rer020i = EnerenRER020I( hass, self._config.get(CONF_DEVICES).get(CONF_VMC), shared_config.copy() )
        self._device_aermec_hdmi080 = AermecHDMI080( hass, self._config.get(CONF_DEVICES).get(CONF_RADIANT), shared_config.copy() )
        
        shared_config = dict()
        copy_tuple( self._config, shared_config, CONF_HOME_WINDOWS_STATE )
        copy_tuple( self._config.get(CONF_DEVICES), shared_config, CONF_VMC )
        copy_tuple( self._config.get(CONF_DEVICES), shared_config, CONF_RADIANT )
        copy_tuple( self._config, shared_config, CONF_AREAS )
        copy_tuple( self._config, shared_config, CONF_SCENARIOS )

        self._device_supply_units = SupplyUnits( hass, self._config.get(CONF_DEVICES).get(CONF_SUPPLY_UNITS), shared_config.copy() )

        # _LOGGER.info( "__init__ '%s'", str(self._config.get(CONF_WEATHER)) )
  
        self._weather_entity_it = self._config.get(CONF_WEATHER)
        self._scenarios_config = self._config.get(CONF_SCENARIOS)
        self._home_windows_state = self._config.get(CONF_HOME_WINDOWS_STATE)

        entity_ids : list[str] = []
        for area in self._areas_config:
            area_name = area.get(CONF_AREA)
            sensors = area.get(CONF_SENSORS)
            
            heat_index_entity_id  = f"sensor.ambient {area_name} heat index".lower().replace(" ", "_").replace("-", "_")
            dew_point_entity_id = f"sensor.ambient {area_name} Dew Point".lower().replace(" ", "_").replace("-", "_")
            
            entity_ids.append( sensors.get( CONF_TEMPERATURE ) )
            entity_ids.append( sensors.get( CONF_HUMIDITY ) )
            entity_ids.append( heat_index_entity_id )
            entity_ids.append( dew_point_entity_id )

            if area.get( CONF_RADIANT ):
                entity_ids.append( area.get( CONF_TCOLLECTOR ) )

        entity_ids.append( self._scenarios_config.get( CONF_VACATION ) )
        entity_ids.append( self._scenarios_config.get( CONF_NOBODYSIN ) )
        entity_ids.append( self._home_windows_state )

        entity_ids.append( SENSOR_CURRENT_TEMP_UID )
        entity_ids.append( SENSOR_CURRENT_HUMI_UID )
        entity_ids.append( SENSOR_CURRENT_DWP_UID )
        entity_ids.append( SENSOR_CURRENT_HNX_UID )

        entity_ids.append( SENSOR_MAX_CONFORT_T_UID )
        entity_ids.append( SENSOR_MIN_CONFORT_T_UID )
        entity_ids.append( SENSOR_MAX_CONFORT_H_UID )
        entity_ids.append( SENSOR_MIN_CONFORT_H_UID )

        setup_entity_change(self, self._async_entity_changed, entity_ids)

        self._platform_entity_states = PlatformEntityStates(hass, shared_config)
        self._climateStrategies = ClimateStrategies(hass, shared_config, self._platform_entity_states)

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

    # async def async_update(self, now: datetime | None = None) -> None:
    #     """Update Target & Current Temperature."""

    #     # ambient_data_object = self._ambient_current_sensor_data()
    #     # if ambient_data_object is not None:
    #     #     self._hass.data[DOMAIN][STATES][ SENSOR_CURRENT_TEMP_UID ] = ambient_data_object[ CONF_TEMP ]
    #     #     self._hass.data[DOMAIN][STATES][ SENSOR_CURRENT_HUMI_UID ] = ambient_data_object[ CONF_HUM ]
    #     #     self._hass.data[DOMAIN][STATES][ SENSOR_CURRENT_DWP_UID ] = ambient_data_object[ CONF_AVG_DEW_POINT ]
    #     #     self._hass.data[DOMAIN][STATES][ SENSOR_CURRENT_HNX_UID ] = ambient_data_object[ CONF_AVG_H_INDEX]

    #     #     self._hass.data[DOMAIN][STATES][ SENSOR_MAX_CONFORT_T_UID ] = ambient_data_object[ ConfortAttr.T_MAX.value ]
    #     #     self._hass.data[DOMAIN][STATES][ SENSOR_MIN_CONFORT_T_UID ] = ambient_data_object[ ConfortAttr.T_MIN.value ]
    #     #     self._hass.data[DOMAIN][STATES][ SENSOR_MAX_CONFORT_H_UID ] = ambient_data_object[ ConfortAttr.H_MAX.value ]
    #     #     self._hass.data[DOMAIN][STATES][ SENSOR_MIN_CONFORT_H_UID ] = ambient_data_object[ ConfortAttr.H_MIN.value ]

    async def async_ambient_sensor_data_force_update(self, event) -> None:
        """ ... """
        for area in self._config[CONF_AREAS]:
            sensors = area.get(CONF_SENSORS)
            t_entity_id = sensors.get( CONF_TEMPERATURE )
            t_entity_state = self._hass.states.get(t_entity_id)
            h_entity_id = sensors.get( CONF_HUMIDITY )
            h_entity_state = self._hass.states.get(h_entity_id)
            t_felt_id = (f"sensor.Ambient {area[CONF_AREA]} {HNX_NAME_POSTFIX}").lower().replace(" ", "_").replace("-", "_")
            t_felt_state = self._hass.states.get(t_felt_id)

            if t_entity_state:
                self._global_entities[t_entity_id] = t_entity_state
                self._hass.data[DOMAIN][STATES][t_entity_id] = t_entity_state
            if h_entity_state:
                self._global_entities[h_entity_id] = h_entity_state
                self._hass.data[DOMAIN][STATES][h_entity_id] = h_entity_state
            if t_felt_state:
                self._global_entities[t_felt_id] = t_felt_state  
                self._hass.data[DOMAIN][STATES][t_felt_id] = t_felt_state

            # _LOGGER.debug( "async_ambient_sensor_data_force_update - %s %s %s %s %s %s", 
            #              str(t_entity_id), str(h_entity_id), str(t_felt_id),
            #              self._hass.states.get(t_entity_id).state, # type: ignore
            #              self._hass.states.get(h_entity_id).state, # type: ignore
            #              self._hass.states.get(t_felt_id), # type: ignore
            # )

    @property
    def season_data(self) -> SeasonData:
        """Season data"""
        return season_data(self._hass, self._weather_entity_it)
    
    @property
    def confort_zone(self) -> dict[str, float] | None:
        """"Confort zone data"""
        return confort_zone(self._hass, self._weather_entity_it)

    def _ambient_current_sensor_data(self) -> Any:
        """
        Calcola la temperatura e l'umidità media ponderata per le aree specificate.

        Returns:
            dict: Un dizionario contenente la temperatura media, l'umidità media, il punto di rugiada medio e l'indice di calore medio.
            None: Se non ci sono abbastanza dati per calcolare i valori medi.
        """
        temps = []
        hums = []
        weights = []
        area_count = 0
        area_missing = 0
        sensor_missing = []
        confort_zone_data = confort_zone(self._hass, self._weather_entity_it)

        for area in self._areas_config:
            if "indoor" in area and "radiant" in area and area['indoor'] and area['radiant']:
                area_count += 1
                t_entity_id = area['sensors']['temperature']
                h_entity_id = area['sensors']['humidity']
                if t_entity_id in self._global_entities and h_entity_id in self._global_entities \
                    and self._global_entities[t_entity_id] and self._global_entities[h_entity_id]:
                    temps.append(convert_to_float(self._global_entities[t_entity_id].state))
                    hums.append(convert_to_float(self._global_entities[h_entity_id].state))
                    weights.append(area[CONF_MQ])
                else:
                    area_missing += 1
                    sensor_missing.append((t_entity_id, h_entity_id))
                    # _LOGGER.warning("_ambient_sensor_data - No data for entity %s and %s", str(t_entity_id), str(h_entity_id))
                    # return None

        if confort_zone_data and round(area_count / 3, 0) >= area_missing:
            t_avg = round(weighted_average(temps, weights), 1)
            h_avg = round(weighted_average(hums, weights), 1)
            t_dew_point = round(dew_point(t_avg, h_avg), 1)
            t_h_index = round(heat_index(t_avg, h_avg), 1)
            # _LOGGER.debug("temperature: %s humidity: %s weights: %s %s %s", str(temps), str(hums), str(weights), str(t_avg), str(h_avg))

            return { \
                CONF_TEMP: t_avg, \
                CONF_HUM: h_avg, \
                CONF_AVG_DEW_POINT: t_dew_point,\
                CONF_AVG_H_INDEX: t_h_index, \
                
                ConfortAttr.T_MIN.value: confort_zone_data[ConfortAttr.T_MIN.value], \
                ConfortAttr.T_MAX.value: confort_zone_data[ConfortAttr.T_MAX.value], \
                ConfortAttr.H_MIN.value: confort_zone_data[ConfortAttr.H_MIN.value], \
                ConfortAttr.H_MAX.value: confort_zone_data[ConfortAttr.H_MAX.value], \
                
                "sensors" : (area_count - area_missing)
                }
        else:
            RESULTS = f"""
-------------------------------------------------------------------
async_ambient_sensor_data results:
Sensor n.         :: {str(area_count)}
Sensor min n.     :: {str(round(area_count / 3, 0))}
Sensor missing n. :: {str(area_missing)}
Sensor missing    :: {str(sensor_missing)}
-------------------------------------------------------------------
            """
            _LOGGER.warning(RESULTS)
            return None

    # def slave_sensors_data(self) -> dict:
    #     """ ... """
    #     merged_data = dict()

    #     merged_data.update( filter_dict_by_key_prefix(self._global_entities, "sensor.ambient") )
    #     ambient_data_object = self._ambient_current_sensor_data()
    #     climate_setpoint_data = self.climate_setpoints()

    #     # _LOGGER.debug( "slave_sensors_data %s", ambient_data_object )
    #     if ambient_data_object is not None and climate_setpoint_data:
    #         merged_data[ SENSOR_CURRENT_TEMP_UID ] = ambient_data_object[ CONF_TEMP ]
    #         merged_data[ SENSOR_CURRENT_HUMI_UID ] = ambient_data_object[ CONF_HUM ]
    #         merged_data[ SENSOR_CURRENT_DWP_UID ] = ambient_data_object[ CONF_AVG_DEW_POINT ]
    #         merged_data[ SENSOR_CURRENT_HNX_UID ] = ambient_data_object[ CONF_AVG_H_INDEX]

    #         merged_data[ SENSOR_MAX_CONFORT_T_UID ] = climate_setpoint_data.temperature_max
    #         merged_data[ SENSOR_MIN_CONFORT_T_UID ] = climate_setpoint_data.temperature_min
    #         merged_data[ SENSOR_MAX_CONFORT_H_UID ] = climate_setpoint_data.humidity_max
    #         merged_data[ SENSOR_MIN_CONFORT_H_UID ] = climate_setpoint_data.humidity_min

    #     return merged_data   

    # def sensor_data(self) -> dict:
    #     """ ... """
    #     return (filter_dict_by_key_prefix( self._global_entities, "sensor.")).copy()

#     def season_setpoints(self, season: SeasonData | None = None, hysteresis_mode="comfort") -> SeasonSetpoint | None:
#         """Climate setpoints"""
#         seasondata: SeasonData | None = season

#         setpoints: SeasonSetpoint | None = None

#         if not seasondata:
#             seasondata = season_data(self._hass, self._weather_entity_it)

#         if seasondata:
#             setpoints = defined_seasons_setpoints( seasondata.overridden, hysteresis_mode )

#         if _LOGGER.isEnabledFor(logging.DEBUG):
#             RESULTS = f"""
# -------------------------------------------------------------------
# climate_setpoints results:
# {season}
# {setpoints}
# -------------------------------------------------------------------
#             """
#             _LOGGER.debug(RESULTS)

#         return setpoints
    

    def climate_setpoints(self) -> SeasonSetpoint | None:
        """Climate setpoints"""
        current_setpoints: SeasonSetpoint | None = None
        season = season_data(self._hass, self._weather_entity_it)


        if season:
            current_setpoints = seasons_setpoints( season.overridden )
            # self._t_confort_setpoint_power_on = current_setpoints.
            # self._t_confort_setpoint_power_off = current_setpoints[CONF_TEMPERATURE][STATE_OFF]
            # self._h_confort_setpoint_power_on = current_setpoints[CONF_HUMIDITY][STATE_ON]
            # self._h_confort_setpoint_power_off = current_setpoints[CONF_HUMIDITY][STATE_OFF]

        if _LOGGER.isEnabledFor(logging.DEBUG):
            RESULTS = f"""
-------------------------------------------------------------------
climate_setpoints results:
{season}
{current_setpoints}
-------------------------------------------------------------------
            """
            _LOGGER.debug(RESULTS)

        return current_setpoints
    
#     def climate_setpoints_old(self) -> Any:
#         """Climate setpoints"""
#         confortZone = self.confort_zone()
#         season = season_data(self._hass, self._weather_entity_it)

#         if confortZone and season and season['overridden'] == CONF_WINTER:
#             cz_t_min = convert_to_float( confortZone[ConfortAttr.T_MIN.value] ) or 0.0
#             cz_t_max = convert_to_float( confortZone[ConfortAttr.T_MAX.value] ) or 0.0
#             cz_dt = convert_to_float( confortZone[ConfortAttr.DT.value] ) or 0.0
#             self._t_confort_setpoint_power_on = mean( [cz_t_max, cz_t_min] ) - (cz_dt * 2 )
#             self._t_confort_setpoint_power_off = cz_t_max + (cz_dt * 2 )

#             cz_h_min = convert_to_float( confortZone[ConfortAttr.H_MIN.value] ) or 0.0
#             cz_h_max = convert_to_float( confortZone[ConfortAttr.H_MAX.value] ) or 0.0
#             cz_dh = convert_to_float( confortZone[ConfortAttr.DH.value] ) or 0.0
#             self._h_confort_setpoint_power_on = mean( [cz_h_max, cz_h_min] ) - (cz_dh * 2 )
#             self._h_confort_setpoint_power_off = cz_h_max + (cz_dh * 2 )
#         elif confortZone and season and season['overridden'] == CONF_SPRING:
#             cz_t_min = convert_to_float( confortZone[ConfortAttr.T_MIN.value] ) or 0.0
#             cz_t_max = convert_to_float( confortZone[ConfortAttr.T_MAX.value] ) or 0.0
#             cz_dt = convert_to_float( confortZone[ConfortAttr.DT.value] ) or 0.0
#             self._t_confort_setpoint_power_on = mean( [cz_t_max, cz_t_min] ) - (cz_dt * 2 )
#             self._t_confort_setpoint_power_off = cz_t_max + (cz_dt * 2 )

#             cz_h_min = convert_to_float( confortZone[ConfortAttr.H_MIN.value] ) or 0.0
#             cz_h_max = convert_to_float( confortZone[ConfortAttr.H_MAX.value] ) or 0.0
#             cz_dh = convert_to_float( confortZone[ConfortAttr.DH.value] ) or 0.0
#             self._h_confort_setpoint_power_on = mean( [cz_h_max, cz_h_min] ) - (cz_dh * 2 )
#             self._h_confort_setpoint_power_off = cz_h_max + (cz_dh * 2 )

#         result = {
#             CONF_TEMPERATURE : {
#                 STATE_ON : self._t_confort_setpoint_power_on,
#                 STATE_OFF : self._t_confort_setpoint_power_off,
#             },
#             CONF_HUMIDITY : {
#                 STATE_ON : self._h_confort_setpoint_power_on,
#                 STATE_OFF : self._h_confort_setpoint_power_off,
#             },
#         }

#         if _LOGGER.isEnabledFor(logging.DEBUG):
#             RESULTS = f"""
# -------------------------------------------------------------------
# climate_setpoints results:
# season               :: {str(season)}
# temperature setpoint :: {str(self._t_confort_setpoint_power_on)} ({get_entity_state( self._global_entities, SENSOR_CURRENT_HNX_UID )}) {str(self._t_confort_setpoint_power_off)}
# humidity setpoint    :: {str(self._h_confort_setpoint_power_on)} ({get_entity_state( self._global_entities, SENSOR_CURRENT_HUMI_UID )}) {str(self._h_confort_setpoint_power_off)}
# -------------------------------------------------------------------
#             """
#             _LOGGER.debug(RESULTS)

#         return result

    async def async_hvac_control(self, hvac_mode: HVACMode | None) -> Any:
        """hvac controller"""
        season = season_data(self._hass, self._weather_entity_it)

        await self._climateStrategies.async_compute_climate_strategy( season.overridden )

        if season is not None and hvac_mode == HVACMode.AUTO:
            
            # shared_config = dict()
            # copy_tuple( self._config, shared_config, CONF_AREAS )
            # copy_tuple( self._config, shared_config, CONF_SCENARIOS )
            # # copy_tuple( self._config, shared_config, CONF_SUPPLY_UNITS )
            # copy_tuple( self._config, shared_config, CONF_HOME_WINDOWS_STATE )
            # shared_config.update( { 
            #     CONF_VMC : { 
            #         CONF_POWER : copy_structure( self._config.get(CONF_DEVICES).get(CONF_VMC), CONF_POWER )[CONF_POWER],
            #         CONF_REQUESTS : copy_structure( self._config.get(CONF_DEVICES).get(CONF_VMC), CONF_REQUESTS )[CONF_REQUESTS],
            #         CONF_SENSORS : copy_structure( self._config.get(CONF_DEVICES).get(CONF_VMC), CONF_SENSORS )[CONF_SENSORS], 
            #     }
            # } )
            # shared_config.update( { CONF_RADIANT : copy_structure( self._config.get(CONF_DEVICES).get(CONF_RADIANT), CONF_POWER ) } )
            # # shared_config.update( { CONF_SUPPLY_UNITS : copy_structure( self._config.get(CONF_DEVICES).get(CONF_SUPPLY_UNITS), CONF_SENSORS ) } )
            # shared_config.update( { CONF_SCENARIOS : self._scenarios_config.copy() } )
            # shared_config.update( { CONF_SUPPLY_UNITS : self._config.get(CONF_DEVICES).get(CONF_SUPPLY_UNITS).copy() } )

            # _LOGGER.debug( "shared_config %s", str(shared_config)) 
            
            climateSystem_setpoints = self.climate_setpoints()
            # climateSystem_setpoints = self.season_setpoints(season)

            if climateSystem_setpoints:
                await self._device_eneren_rer020i.async_hvac_control(
                    hvac_mode, season.overridden, climateSystem_setpoints
                )
                await self._device_aermec_hdmi080.async_hvac_control(
                    hvac_mode, season.overridden, climateSystem_setpoints
                )
                await self._device_supply_units.async_hvac_control(
                    hvac_mode, season.overridden, climateSystem_setpoints
                )  

    async def compute_climate_strategy(self) -> None:
        """Compute air strategy"""

        if self.season_data:
            seasonData = self.season_data
            seasonThreshold = season_threshold(seasonData.overridden)






    # def compute_climate_strategy(self, season: SeasonData, setpoint: SeasonSetpoint) -> None:
    #     heating: HvacThreshold
    #     cooling: HvacThreshold

    #     match season.overridden:
    #         case Seasons.WINTER:
    #             heating = HvacThreshold(
    #                 mode=HEATING,
    #                 state_on=setpoint.temperature_min,
    #                 state_off=setpoint.temperature_max,
    #             )
    #         case Seasons.SUMMER:
    #             cooling = HvacThreshold(
    #                 mode=COOLING,
    #                 state_on=round(CONFORT_ZONES[Seasons.SUMMER][ConfortAttr.T_MAX.value] - 1.0, 1),
    #                 state_off=round(cz_t_min + 0.5, 1)
    #             )

    #     if season.overridden == Seasons.WINTER:
    #         heating = HvacThreshold(mode=HEATING, state_on=t_min, state_off=t_max)
    #     elif season == Seasons.SUMMER:
    #         cooling = HvacThreshold(mode=COOLING, state_on=round(cz_t_max - 1.0, 1), state_off=round(cz_t_min + 0.5, 1))
    #     elif season == Seasons.SPRING:
    #         cooling = HvacThreshold(mode=COOLING, state_on=round(CONFORT_ZONES[Seasons.SUMMER][ConfortAttr.T_MIN.value] + 1.5, 1),
    #                                 state_off=round(CONFORT_ZONES[Seasons.SUMMER][ConfortAttr.T_MIN.value] + 0.5, 1))
    #         heating = HvacThreshold(mode=HEATING, state_on=round(CONFORT_ZONES[Seasons.WINTER][ConfortAttr.T_MIN.value] 
    #                                                              - (CONFORT_ZONES[Seasons.WINTER][ConfortAttr.DT.value] * 2), 1),
    #                                 state_off=cz_t_min)
    #     elif season == Seasons.AUTUMN:
    #         heating = HvacThreshold(mode=HEATING, state_on=round(cz_t_min - (cz_dt * 2), 1), state_off=round(cz_t_min + 0.5, 1))
    #         cooling = HvacThreshold(mode=COOLING, state_on=round(CONFORT_ZONES[Seasons.SUMMER][ConfortAttr.T_MIN.value] + 1.5, 1),
    #                                 state_off=round(CONFORT_ZONES[Seasons.SUMMER][ConfortAttr.T_MIN.value] + 0.5, 1))

