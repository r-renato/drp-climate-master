"""Weather functions for .... component."""
import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .helpers import convert_to_float

_LOGGER = logging.getLogger(__name__)


async def async_weather_prediction( hass : HomeAssistant, weather_entity_it : str ) -> Any:
    """Checks configured weather entity for next two days of temperature predictions.

    Returns
    -------
    bool
            True if the maximum forcast temperature is lower than the off temperature
    None
            if not successful
    """
    # _LOGGER.debug( "_async_weather_prediction in %s", str(hass.states.get("weather.home_rome")) )
    if weather_entity_it is None or hass.states.get( weather_entity_it ) is None:
        _LOGGER.warning(f"DRP Climate Master: weather entity {weather_entity_it} not available.")
        return None

    try:
        forecasts = await hass.services.async_call(
            domain="weather",
            service="get_forecasts",
            service_data={"type": "daily"},
            blocking=True,
            target={"entity_id": weather_entity_it},
            return_response=True,
        )
        return forecasts
    except TypeError as e:
        _LOGGER.warning( "DRP Climate Master: no weather entity data found from %s.", str(weather_entity_it))
        return None
