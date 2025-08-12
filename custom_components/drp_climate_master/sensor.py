"""Support for Thermostats."""
from __future__ import annotations

import datetime
from decimal import Decimal
import logging
from typing import Any

import pandas as pd

from homeassistant.core import HomeAssistant, callback, State
from homeassistant.const import (
    # CONF_NAME,
    CONF_SENSORS,
)
from homeassistant.components.sensor import (
    # CONF_STATE_CLASS,
    RestoreSensor,
    SensorEntity,
    SensorDeviceClass
)

from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .controller import Controller
from .utils.const import (
    CONF_AREA, 
    CONF_HUMIDITY, 
    CONF_TEMPERATURE, 
    # DOMAIN,
    DWP_NAME_POSTFIX,
    H_NAME_POSTFIX,
    HNX_NAME_POSTFIX,
    MAX_CH_NAME_POSTFIX,
    MAX_CT_NAME_POSTFIX,
    MIN_CH_NAME_POSTFIX,
    MIN_CT_NAME_POSTFIX,
    NAME,
    SCAN_INTERVAL,
    T_NAME_POSTFIX, 
    # VERSION,
    ClimateSensorType,
)
from .utils.helpers import convert_to_float
from .utils.psychrometric import dew_point, dew_point_perception, heat_index

_LOGGER = logging.getLogger(__name__)

class ClimateSensor(
    CoordinatorEntity[DataUpdateCoordinator[list[int] | None]],
    RestoreSensor,
    SensorEntity,
):
    """Climate register sensor."""

    def __init__(
        self,
        hass: HomeAssistant, 
        hub: Controller,
        coordinator: DataUpdateCoordinator[list[int] | None],
        idx: int,
        config: dict[str, Any],
        area_entry: dict[str, Any],
        unit: str,
        climate_sensor: str,
    ) -> None:
        self._hass = hass
        self._hub = hub        
        self._idx = idx
        self._config = config
        self._area_config = area_entry
        self._climate_sensor = climate_sensor
# ########## # ########## # ########## # ########## #
# Internal attributes
# ########## # ########## # ########## # ########## #
        self._attr_native_value: float | None = None
        self._attr_native_unit_of_measurement = unit   
      
        self._attr_name_postfix = "na"
        if ClimateSensorType.DEW_POINT == climate_sensor:
            self._attr_name_postfix = DWP_NAME_POSTFIX
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
        elif ClimateSensorType.HEAT_INDEX == climate_sensor:
            self._attr_name_postfix = HNX_NAME_POSTFIX
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
        elif ClimateSensorType.CURRENT_TEMPERATURE == climate_sensor:
            self._attr_name_postfix = T_NAME_POSTFIX
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
        elif ClimateSensorType.CURRENT_HUMIDITY == climate_sensor:
            self._attr_name_postfix = H_NAME_POSTFIX
            self._attr_device_class = SensorDeviceClass.HUMIDITY
        elif ClimateSensorType.MAX_CONFORT_TEMPERATURE == climate_sensor:
            self._attr_name_postfix = MAX_CT_NAME_POSTFIX
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
        elif ClimateSensorType.MIN_CONFORT_TEMPERATURE == climate_sensor:
            self._attr_name_postfix = MIN_CT_NAME_POSTFIX
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
        elif ClimateSensorType.MAX_CONFORT_HUMIDITY == climate_sensor:
            self._attr_name_postfix = MAX_CH_NAME_POSTFIX
            self._attr_device_class = SensorDeviceClass.HUMIDITY
        elif ClimateSensorType.MIN_CONFORT_HUMIDITY == climate_sensor:
            self._attr_name_postfix = MIN_CH_NAME_POSTFIX
            self._attr_device_class = SensorDeviceClass.HUMIDITY

        self._attr_name = f"Ambient {area_entry[CONF_AREA]} {self._attr_name_postfix}"
        self._attr_unique_id = f"ambient_{area_entry[CONF_AREA]}_{idx}_{self._attr_name_postfix}"

        self._attr_manufacturer = NAME
        self._attr_state_class = "measurement"

        self._window_size = (3 * 60 * 60) // SCAN_INTERVAL.seconds
        self._data_series = []

        _LOGGER.info( "__init__ - 'sensor.%s'", str(self._attr_name.lower().replace(" ", "_").replace("-", "_")) )

        super().__init__(coordinator)

    async def async_added_to_hass(self) -> None:
        """Handle entity which will be added."""
        await super().async_added_to_hass()
        state = await self.async_get_last_sensor_data()
        if state:
            self._attr_native_value = float( state.native_value ) if isinstance(state.native_value, Decimal) else None

    @property
    def extra_state_attributes(self):
        """Return the state attributes of the device."""
        data: dict[str, Any] = { 
            "coordinated slave id" : self._idx,
            "area" : self._area_config[CONF_AREA]
        }
        if ClimateSensorType.DEW_POINT == self._climate_sensor:
            # _LOGGER.debug( "extra_state_attributes %s", str(dew_point_perception(self._attr_native_value)))
            perception = dew_point_perception(self._attr_native_value)
            data[ "human perception" ] = perception.unique_id if perception else "unknown"

        if self._attr_manufacturer:
            data[ "manufacturer" ] = self._attr_manufacturer
        # if self._attr_model:
        #     data[ "model" ] = self._attr_model
        # if len(self._attr_sensor_registers) > SensorRegIdx.GUIDE:
        #     data[ "guide" ] = self._attr_sensor_registers[SensorRegIdx.GUIDE]
        return data

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""

        if self._area_config.get(CONF_SENSORS):
            temperature_id = (self._area_config.get(CONF_SENSORS) or {}).get(CONF_TEMPERATURE)
            humidity_id = (self._area_config.get(CONF_SENSORS) or {}).get(CONF_HUMIDITY)
        else:
            temperature_id = None
            humidity_id = None
            
            if ClimateSensorType.CURRENT_TEMPERATURE == self._climate_sensor \
                or ClimateSensorType.MAX_CONFORT_TEMPERATURE == self._climate_sensor \
                or ClimateSensorType.MIN_CONFORT_TEMPERATURE == self._climate_sensor:
                temperature_id = "sensor." + (self._attr_name or '').lower().replace(" ", "_").replace("-", "_")
            else:
                humidity_id = "sensor." + (self._attr_name or '').lower().replace(" ", "_").replace("-", "_")

        _attr_board_blocks = self.coordinator.data or []

# ########## # ########## # ########## # ########## #
# TEMPERATURE
# ########## # ########## # ########## # ########## #
        if temperature_id in _attr_board_blocks and _attr_board_blocks[temperature_id] is not None:
            if isinstance(_attr_board_blocks[temperature_id], State):
                new_temp_value = convert_to_float(_attr_board_blocks[temperature_id].state, '_handle_coordinator_update' ,__name__)
            else:
                # _LOGGER.debug( "_handle_coordinator_update - '%s'", str(type(_attr_board_blocks[temperature_id])) )
                new_temp_value = _attr_board_blocks[temperature_id]
                
        else:
            new_temp_value = None
# ########## # ########## # ########## # ########## #
# HUMIDITY
# ########## # ########## # ########## # ########## #
        if humidity_id in _attr_board_blocks and _attr_board_blocks[humidity_id] is not None:
            if not isinstance(_attr_board_blocks[humidity_id], State):
                new_humi_value = _attr_board_blocks[humidity_id]
            else:    
                new_humi_value = convert_to_float(_attr_board_blocks[humidity_id].state, '_handle_coordinator_update' ,__name__)
        else:
            new_humi_value = None
# ########## # ########## # ########## # ########## #
# SET VALUES
# ########## # ########## # ########## # ########## #
        if new_temp_value is not None and new_humi_value is not None:
            if ClimateSensorType.DEW_POINT == self._climate_sensor:
                self._attr_native_value = round(dew_point(new_temp_value, new_humi_value), 1)
                self._attr_available = True
            elif ClimateSensorType.HEAT_INDEX == self._climate_sensor:
                self._attr_native_value = round(heat_index(new_temp_value, new_humi_value), 1)
                # _LOGGER.debug( "calculate_heat_index '%s %s' %s.", str(new_temp_value), str(new_humi_value), str(self._attr_native_value))
                self._attr_available = True
            elif ClimateSensorType.CURRENT_TEMPERATURE == self._climate_sensor:
                self._attr_native_value = new_temp_value
                self._attr_available = True
            elif ClimateSensorType.CURRENT_HUMIDITY == self._climate_sensor:
                self._attr_native_value = new_humi_value
                self._attr_available = True
        elif new_temp_value is not None:
            if ClimateSensorType.CURRENT_TEMPERATURE == self._climate_sensor \
                    or ClimateSensorType.MAX_CONFORT_TEMPERATURE == self._climate_sensor \
                    or ClimateSensorType.MIN_CONFORT_TEMPERATURE == self._climate_sensor:
                self._attr_native_value = new_temp_value
                self._attr_available = True
        elif new_humi_value is not None:
            if ClimateSensorType.CURRENT_HUMIDITY == self._climate_sensor \
                    or ClimateSensorType.MAX_CONFORT_HUMIDITY == self._climate_sensor \
                    or ClimateSensorType.MIN_CONFORT_HUMIDITY == self._climate_sensor:
                self._attr_native_value = new_humi_value
                self._attr_available = True
        else:
            self._attr_available = False


        if self._attr_available:
            self._data_series.append( self._attr_native_value )

            # Mantiene solo gli ultimi 'window' valori
            if len(self._data_series) > self._window_size:
                self._data_series = self._data_series[-self._window_size:]

            # Calcola la media mobile
            series = pd.Series( self._data_series )
            self._attr_native_value = round( series.mean(), 1) 

        RESULTS = f"""
-------------------------------------------------------------------
_handle_coordinator_update:
area        :: {self._area_config[CONF_AREA]}
sensor T id :: T {temperature_id} {str(new_temp_value)}
sensor H id :: H {humidity_id} {str(new_humi_value)} 
sensor type :: {self._climate_sensor} - {str(self._attr_available)} - {str(self._attr_native_value)}
-------------------------------------------------------------------
            """
        _LOGGER.debug(RESULTS)
        super()._handle_coordinator_update()

