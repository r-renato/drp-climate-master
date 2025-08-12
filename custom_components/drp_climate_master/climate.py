"""Support for Thermostats."""
from __future__ import annotations

# from abc import ABC
# from statistics import mean
# import time
# from random import randint
from datetime import datetime
# import asyncio
# import json
import logging
from typing import Any, Callable, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.components.climate import (
    ClimateEntity,
    # ClimateEntityFeature,
    # HVACMode,
    # PRESET_NONE,
)
from homeassistant.components.climate.const import (
    HVACMode,
    PRESET_NONE,
)

from homeassistant.core import HomeAssistant
# from homeassistant.helpers import entity_platform
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.entity_component import EntityComponent
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from homeassistant.const import (
    ATTR_TEMPERATURE,
    CONF_NAME,
    # CONF_FRIENDLY_NAME,
    CONF_SENSORS,
    CONF_UNIQUE_ID,
    CONF_TEMPERATURE_UNIT,
    PRECISION_TENTHS,
    PRECISION_WHOLE,
    UnitOfTemperature,
    EVENT_HOMEASSISTANT_START,
    Platform,
)

# from custom_components.drp_climate_master.climate_core import DevicesHub
from .controller import Controller
from .sensor import ClimateSensor
from .utils.const import (
    CONF_AREA, 
    CONF_AREA_HOME, 
    CONF_AREAS,
    CONF_AVG_DEW_POINT,
    CONF_AVG_H_INDEX,
    CONF_HOME,
    CONF_HUM, 
    CONF_HUMIDITY, 
    CONF_INDOOR, 
    CONF_MQ, 
    CONF_RADIANT,
    CONF_TEMP, 
    CONF_TEMPERATURE, 
    DATA_COMPONENT, 
    DOMAIN, 
    HUB_COMPONENT,
    SENSOR_CURRENT_DWP_UID,
    SENSOR_CURRENT_HNX_UID, 
    SENSOR_CURRENT_HUMI_UID, 
    SENSOR_CURRENT_TEMP_UID,
    SENSOR_MAX_CONFORT_H_UID,
    SENSOR_MAX_CONFORT_T_UID,
    SENSOR_MIN_CONFORT_H_UID,
    SENSOR_MIN_CONFORT_T_UID,
    STATES,
    VERSION, 
    ClimateSensorType,
    ConfortAttr
)
from .utils.helpers import (
    async_platform_add_entities, 
    convert_to_float, 
    convert_to_int, 
    filter_dict_by_key_prefix, 
    get_entity_state, 
    weighted_average
)
from .utils.psychrometric import dew_point, dew_point_perception, heat_index

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1

DEPENDENCIES = ['switch', 'sensor']

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_devices: Callable[[list[ClimateEntity]], None],
):
    """Setup sensor platform."""

    # async def async_service_handler(self, data: ServiceCall):
    #     _LOGGER.debug(f"Service call: {self} » {data.service}")

    # platform = entity_platform.async_get_current_platform()

    # platform.async_register_entity_service(
    #     SERVICE_SET_TEMP_TARGET_TEMPERATURE,
    #     BETTERTHERMOSTAT_SET_TEMPERATURE_SCHEMA,  # type: ignore
    #     async_service_handler,
    #     [
    #         BetterThermostatEntityFeature.TARGET_TEMPERATURE,
    #         BetterThermostatEntityFeature.TARGET_TEMPERATURE_RANGE,
    #     ],
    # )
    # platform.async_register_entity_service(
    #     SERVICE_RESTORE_SAVED_TARGET_TEMPERATURE, {}, async_service_handler
    # )
    # platform.async_register_entity_service(
    #     SERVICE_RESET_HEATING_POWER, {}, async_service_handler
    # )

    
    climateComponent : EntityComponent[HomeClimateMaster] = hass.data[DOMAIN][DATA_COMPONENT]
    # config : ConfigType = climateComponent.config.get(DOMAIN, [])[0]
    # climate = config.get("climate")[0]

    config = {}
    if (cfg := getattr(climateComponent, "config", None)) and (domain_list := cfg.get(DOMAIN)):
        config = domain_list[0]
    climate = config.get("climate", [])[0]

    hub : Controller = hass.data[DOMAIN][HUB_COMPONENT][config.get(CONF_NAME)]
    climate_entity = HomeClimateMaster(hass, hub, climate )

    slave_count = 0
    slave_sensors: list[Optional[ClimateSensor]] = []
    home_area = {}
    home_area_mq = 0
    home_area_c = {}

    for area in climate.get(CONF_AREAS):
        _LOGGER.info( 'async_setup_entry - Area: %s', area.get(CONF_AREA) )

        if area.get(CONF_INDOOR):
            home_area_mq = home_area_mq + area.get(CONF_MQ, 0)
            slave_sensors.append(await climate_entity.async_setup_slaves(hass, hub, slave_count, climate, area, ClimateSensorType.DEW_POINT))
            slave_count += 1
            slave_sensors.append(await climate_entity.async_setup_slaves(hass, hub, slave_count, climate, area, ClimateSensorType.HEAT_INDEX))
            slave_count += 1

    home_area[CONF_AREA] = CONF_AREA_HOME
    home_area[CONF_INDOOR] = True
    home_area[CONF_RADIANT] = False
    home_area[CONF_SENSORS] = { 
        CONF_TEMPERATURE : SENSOR_CURRENT_TEMP_UID, 
        CONF_HUMIDITY : SENSOR_CURRENT_HUMI_UID 
        }
    home_area[CONF_MQ] = home_area_mq

    slave_sensors.append(await climate_entity.async_setup_slaves(hass, hub, slave_count, climate, home_area, ClimateSensorType.CURRENT_TEMPERATURE))
    slave_count += 1
    slave_sensors.append(await climate_entity.async_setup_slaves(hass, hub, slave_count, climate, home_area, ClimateSensorType.CURRENT_HUMIDITY))
    slave_count += 1
    slave_sensors.append(await climate_entity.async_setup_slaves(hass, hub, slave_count, climate, home_area, ClimateSensorType.DEW_POINT))
    slave_count += 1
    slave_sensors.append(await climate_entity.async_setup_slaves(hass, hub, slave_count, climate, home_area, ClimateSensorType.HEAT_INDEX))
    slave_count += 1

    home_area_c[CONF_AREA] = CONF_HOME
    home_area_c[CONF_INDOOR] = True
    home_area_c[CONF_RADIANT] = False
    # home_area_c[CONF_SENSORS] = { 
    #     CONF_TEMPERATURE : SENSOR_MAX_CONFORT_T_UID, 
    #     CONF_HUMIDITY : SENSOR_MAX_CONFORT_H_UID 
    #     }
    home_area_c[CONF_MQ] = home_area_mq

    slave_sensors.append(await climate_entity.async_setup_slaves(hass, hub, slave_count, climate, home_area_c, ClimateSensorType.MAX_CONFORT_TEMPERATURE))
    slave_count += 1
    slave_sensors.append(await climate_entity.async_setup_slaves(hass, hub, slave_count, climate, home_area_c, ClimateSensorType.MAX_CONFORT_HUMIDITY))
    slave_count += 1

    # home_area[CONF_AREA] = CONF_HOME
    # home_area[CONF_INDOOR] = True
    # home_area[CONF_RADIANT] = False
    # home_area[CONF_SENSORS] = { 
    #     CONF_TEMPERATURE : SENSOR_MIN_CONFORT_T_UID, 
    #     CONF_HUMIDITY : SENSOR_MIN_CONFORT_H_UID 
    #     }
    # home_area[CONF_MQ] = home_area_mq

    slave_sensors.append(await climate_entity.async_setup_slaves(hass, hub, slave_count, climate, home_area_c, ClimateSensorType.MIN_CONFORT_TEMPERATURE))
    slave_count += 1
    slave_sensors.append(await climate_entity.async_setup_slaves(hass, hub, slave_count, climate, home_area_c, ClimateSensorType.MIN_CONFORT_HUMIDITY))
    slave_count += 1

    await async_platform_add_entities( hass, Platform.SENSOR, slave_sensors, False )

    async_add_devices(
        [
            climate_entity
        ]
    )
    _LOGGER.info( 'async_setup_entry - Done.' )

class HomeClimateMaster(ClimateEntity, RestoreEntity):
    """Representation of a Thermostat."""

    _attr_has_entity_name = True
    _attr_name = None

    def __init__(
        self,
        hass: HomeAssistant,
        hub: Controller,
        config: ConfigType,
    ) -> None:
        """Initialize the thermostat."""
        super().__init__()

        self._hass = hass
        self._hub = hub
        self._config = config

        self._attr_name = config[CONF_NAME]
        self._unique_id = self._attr_unique_id = config.get( CONF_UNIQUE_ID, config[CONF_NAME] ).replace(" ", "_")
        self._unit = config[CONF_TEMPERATURE_UNIT]
        self._attr_temperature_unit = (
            UnitOfTemperature.FAHRENHEIT
            if self._unit == "F"
            else UnitOfTemperature.CELSIUS
        )
        self._precision = 0
        self._attr_precision = (
            PRECISION_TENTHS if self._precision >= 1 else PRECISION_WHOLE
        )
        self._device_class = DOMAIN
        self._state_class = f"{DOMAIN}_state"
        self._hvac_list = self._attr_hvac_modes = [
            HVACMode.OFF, 
            # HVACMode.HEAT, 
            # HVACMode.COOL, 
            # HVACMode.HEAT_COOL, 
            HVACMode.AUTO, 
            # HVACMode.DRY, 
            # HVACMode.FAN_ONLY
            ]
        self._preset_mode = PRESET_NONE
        self.map_on_hvac_mode = self._attr_hvac_mode = HVACMode.AUTO
        
# ########## # ########## # ########## # ########## #
# Internal attributes
# ########## # ########## # ########## # ########## #
        self._coordinator: DataUpdateCoordinator[list[int] | None] | None = None
        # self._sensor_map = dict()

        self._confort_zone = self._hub.confort_zone
        if self._confort_zone:
            self._attr_min_temp = convert_to_float( self._confort_zone[ConfortAttr.T_MIN.value] ) or 0.0
            self._attr_max_temp = convert_to_float( self._confort_zone[ConfortAttr.T_MAX.value] ) or 0.0
            self._attr_min_humidity = self._confort_zone[ConfortAttr.H_MIN.value]
            self._attr_max_humidity = self._confort_zone[ConfortAttr.H_MAX.value]

        self._attr_current_temperature = None
        self._attr_current_humidity = None
        self._attr_current_dew_point = None
        self._attr_current_h_index = None
        self._attr_target_temperature = None

    async def async_added_to_hass(self) -> None:
        """Handle entity which will be added."""
        await super().async_added_to_hass()
        state = await self.async_get_last_state()

        _LOGGER.info( "async_added_to_hass - State: '%s'", str(state) )
        
        if state and state.attributes.get(ATTR_TEMPERATURE):
            self._attr_target_temperature = float(state.attributes[ATTR_TEMPERATURE])

        self._hass.bus.async_listen_once(EVENT_HOMEASSISTANT_START, self._hub.async_ambient_sensor_data_force_update)

    async def async_setup_slaves(
        self, 
        hass: HomeAssistant, 
        hub: Controller, 
        slave_count: int, 
        entry: dict[str, Any], 
        internal_sensor: dict[str, Any],
        climate_sensor: str
    ) -> ClimateSensor | None:
        """Add slaves as needed (1 read for multiple sensors)."""

        # Add a dataCoordinator for each sensor that have slaves
        # this ensures that idx = bit position of value in result
        # polling is done with the base class
        name = self._attr_name if self._attr_name else "climate_sensor"
        if self._coordinator is None:
            self._coordinator = DataUpdateCoordinator(
                hass,
                _LOGGER,
                name=name,
            )

        if ClimateSensorType.DEW_POINT == climate_sensor \
            or ClimateSensorType.HEAT_INDEX == climate_sensor \
            or ClimateSensorType.CURRENT_TEMPERATURE == climate_sensor \
            or ClimateSensorType.MAX_CONFORT_TEMPERATURE == climate_sensor \
            or ClimateSensorType.MIN_CONFORT_TEMPERATURE == climate_sensor:
                return ClimateSensor(hass, hub, self._coordinator, slave_count, entry, internal_sensor, self._attr_temperature_unit, climate_sensor)
        # elif ClimateSensorType.CURRENT_TEMPERATURE == climate_sensor :
        #     return ClimateSensor(hass, hub, self._coordinator, slave_count, entry, internal_sensor, self._attr_temperature_unit, climate_sensor)
        elif ClimateSensorType.CURRENT_HUMIDITY == climate_sensor \
            or ClimateSensorType.MAX_CONFORT_HUMIDITY == climate_sensor \
            or ClimateSensorType.MIN_CONFORT_HUMIDITY == climate_sensor:
                return ClimateSensor(hass, hub, self._coordinator, slave_count, entry, internal_sensor, "%", climate_sensor)
    
    @property
    def _global_entities(self) -> dict:
        """Return the name of the device."""
        return self._hass.data[DOMAIN][STATES]
    
    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.unique_id)},
            # "name": self.device_name,
            "manufacturer": "DRP (Domotic Residential Platform)",
            # "model": self.model,
            "sw_version": VERSION,
        }

    @property
    def extra_state_attributes(self):
        """Return the state attributes of the device."""

        season = self._hub.season_data
        # device_power_setpoint = self.hub.get_device_setpoint()
        data: dict[str, Any] = {
            "min_hum" : self._attr_min_humidity,
            "max_hum" : self._attr_max_humidity
        }

        if season:
            data[ "season" ] = season.overridden
            data[ "weather_anomaly" ] = season.weather_anomaly

        if self._attr_current_dew_point:
            data[ "current dew point" ] = self._attr_current_dew_point
        if self._attr_current_h_index:
            data[ "current heat index" ] = self._attr_current_h_index

        perception = dew_point_perception(self._attr_current_dew_point)
        if self._attr_current_dew_point and perception:
            data[ "human perception" ] = perception.description
            data[ "human perception id" ] = perception.unique_id

        return data
    
    def set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target hvac mode."""
        self._attr_hvac_mode = hvac_mode

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

        for area in self._config.get(CONF_AREAS, {}):
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

        if self._hub.confort_zone and round(area_count / 3, 0) >= area_missing:
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
                
                ConfortAttr.T_MIN.value: self._hub.confort_zone[ConfortAttr.T_MIN.value], \
                ConfortAttr.T_MAX.value: self._hub.confort_zone[ConfortAttr.T_MAX.value], \
                ConfortAttr.H_MIN.value: self._hub.confort_zone[ConfortAttr.H_MIN.value], \
                ConfortAttr.H_MAX.value: self._hub.confort_zone[ConfortAttr.H_MAX.value], \
                
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
        
    def _current_sensors_data(self) -> dict:
        """ ... """
        merged_data = dict()

        merged_data.update( filter_dict_by_key_prefix(self._global_entities, "sensor.ambient") )
        ambient_data_object = self._ambient_current_sensor_data()
        climate_setpoint_data = self._hub.climate_setpoints()

        # _LOGGER.debug( "slave_sensors_data %s", ambient_data_object )
        if ambient_data_object is not None and climate_setpoint_data:
            merged_data[ SENSOR_CURRENT_TEMP_UID ] = ambient_data_object[ CONF_TEMP ]
            merged_data[ SENSOR_CURRENT_HUMI_UID ] = ambient_data_object[ CONF_HUM ]
            merged_data[ SENSOR_CURRENT_DWP_UID ] = ambient_data_object[ CONF_AVG_DEW_POINT ]
            merged_data[ SENSOR_CURRENT_HNX_UID ] = ambient_data_object[ CONF_AVG_H_INDEX]

            merged_data[ SENSOR_MAX_CONFORT_T_UID ] = climate_setpoint_data.temperature_max
            merged_data[ SENSOR_MIN_CONFORT_T_UID ] = climate_setpoint_data.temperature_min
            merged_data[ SENSOR_MAX_CONFORT_H_UID ] = climate_setpoint_data.humidity_max
            merged_data[ SENSOR_MIN_CONFORT_H_UID ] = climate_setpoint_data.humidity_min

        # _LOGGER.debug( "_current_sensors_data %s", str( merged_data ) )
        return merged_data

    async def async_update(self, now: datetime | None = None) -> None:
        """Update Target & Current Temperature."""

        # await self._hub.async_update()
        # _LOGGER.debug( "async_update '%s' ", str(self._global_entities.keys()) )

        # sensor_data = self._hub.sensor_data()
        # self._confort_zone = self._hub.confort_zone()

        # _LOGGER.debug( "async_update - Sensor data '%s'", str(sensor_data.keys()) )
        # if self._confort_zone:
            # _LOGGER.debug( "async_update: %s", self._confort_zone )
            # self._attr_min_temp = convert_to_float( self._confort_zone[ConfortAttr.T_MIN.value] )
        self._attr_min_temp = convert_to_float( get_entity_state(self._global_entities, SENSOR_MIN_CONFORT_T_UID) ) or 0.0
            # self._attr_max_temp = convert_to_float( self._confort_zone[ConfortAttr.T_MAX.value] )
        self._attr_max_temp = convert_to_float( get_entity_state(self._global_entities, SENSOR_MAX_CONFORT_T_UID) ) or 0.0
            # self._attr_min_humidity = convert_to_float(self._confort_zone[ConfortAttr.H_MIN.value])
        self._attr_min_humidity = convert_to_float( get_entity_state(self._global_entities, SENSOR_MIN_CONFORT_H_UID) ) or 0.0
            # self._attr_max_humidity = convert_to_float(self._confort_zone[ConfortAttr.H_MAX.value])
        self._attr_max_humidity = convert_to_float( get_entity_state(self._global_entities, SENSOR_MAX_CONFORT_H_UID) ) or 0.0

        # if sensor_data is not None:
            # self._attr_current_temperature = sensor_data.get( SENSOR_CURRENT_TEMP_UID )
        self._attr_current_temperature = convert_to_float( get_entity_state(self._global_entities, SENSOR_CURRENT_TEMP_UID) ) or 0.0
            # self._attr_current_humidity = sensor_data.get( SENSOR_CURRENT_HUMI_UID )
        self._attr_current_humidity = convert_to_int( get_entity_state(self._global_entities, SENSOR_CURRENT_HUMI_UID) )
            # self._attr_current_dew_point = sensor_data.get( SENSOR_CURRENT_DWP_UID )
        self._attr_current_dew_point = convert_to_float( get_entity_state(self._global_entities, SENSOR_CURRENT_DWP_UID) ) or 0.0
            # self._attr_current_h_index = sensor_data.get( SENSOR_CURRENT_HNX_UID )
        self._attr_current_h_index = convert_to_float( get_entity_state(self._global_entities, SENSOR_CURRENT_HNX_UID) ) or 0.0

        if self._coordinator:
            # self._coordinator.async_set_updated_data(self._hub.sensor_data())
            self._coordinator.async_set_updated_data( self._current_sensors_data() ) # type: ignore

        await self._hub.async_hvac_control( self._attr_hvac_mode )
