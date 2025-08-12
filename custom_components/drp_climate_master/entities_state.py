from typing import Any

from homeassistant.core import HomeAssistant

from .utils.helpers import convert_to_float, get_entity_state

from .utils.const import (
    DOMAIN,
    SENSOR_CURRENT_DWP_UID,
    SENSOR_CURRENT_HNX_UID,
    SENSOR_CURRENT_HUMI_UID,
    SENSOR_CURRENT_TEMP_UID,
    STATES,
)

class PlatformEntityStates:
    """Platform entity states class."""

    def __init__(
        self,
        hass: HomeAssistant, 
        shared_config: dict[str, Any],
    ):
        self._hass: HomeAssistant = hass
        self._shared_config = shared_config

    @property
    def _global_entities(self) -> dict:
        """Return the entity map."""
        return self._hass.data[DOMAIN][STATES]

    # @property
    # def outdoor_temperature_entity_id(self) -> str:
    #     terrace_area_config = {}
    #     for area in self._shared_config.get( CONF_AREAS, []):
    #         if area.get(CONF_AREA).lower() == CONF_TERRACE:
    #             terrace_area_config = area
    #             break 
    #     return terrace_area_config.get(CONF_SENSORS, []).get(CONF_TEMPERATURE, "")

    @property
    def indoor_temperature(self) -> (float | None):
        return convert_to_float( get_entity_state( self._global_entities, SENSOR_CURRENT_TEMP_UID ), 'PlatformEntityStates', 'indoor_dew_point')
    
    @property
    def indoor_felt_temperature(self) -> (float | None):
        return convert_to_float( get_entity_state( self._global_entities, SENSOR_CURRENT_HNX_UID ), 'PlatformEntityStates', 'indoor_dew_point')
    
    @property
    def indoor_humidity(self) -> (float | None):
        return convert_to_float( get_entity_state( self._global_entities, SENSOR_CURRENT_HUMI_UID ), 'PlatformEntityStates', 'indoor_dew_point')    

    @property
    def indoor_dew_point(self) -> (float | None):
        return convert_to_float( get_entity_state( self._global_entities, SENSOR_CURRENT_DWP_UID ), 'PlatformEntityStates', 'indoor_dew_point')   



    # @property
    # def outdoor_humidity(self) -> float | None:
    #     terrace_area_config = {}
    #     for area in self._shared_config.get( CONF_AREAS, []):
    #         if area.get(CONF_AREA).lower() == CONF_TERRACE:
    #             terrace_area_config = area
    #             break 
    #     return convert_to_float( get_entity_state( self._global_entities, terrace_area_config.get(CONF_SENSORS, []).get(CONF_HUMIDITY, "") ) )





