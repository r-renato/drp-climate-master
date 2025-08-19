# #
# from __future__ import annotations

# import logging
# from dataclasses import dataclass
# from typing import Any

# from homeassistant.components.sensor import SensorEntity, RestoreSensor, SensorDeviceClass, SensorStateClass
# from homeassistant.config_entries import ConfigEntry
# from homeassistant.core import HomeAssistant, callback
# from homeassistant.const import PERCENTAGE, UnitOfTemperature
# from homeassistant.helpers.entity_platform import AddEntitiesCallback
# from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, CoordinatorEntity
# from homeassistant.helpers.device_registry import DeviceInfo
# from homeassistant.const import EntityCategory
# from homeassistant.util import slugify

# from .domain.models import SensorPair
# from .helpers.psychrometric import dew_point_celsius

# from .const import (
#     DOMAIN,
#     ENTITIES_STATE,
#     INTEGRATION_MANUFACTURER,
#     INTEGRATION_NAME,
#     INTEGRATION_VERSION,
# )  # definiscile nel tuo const.py

# _LOGGER = logging.getLogger(__name__)

# class BaseSensor(CoordinatorEntity[DataUpdateCoordinator[dict[str, Any]]], RestoreSensor, SensorEntity):
#     _attr_should_poll = False
#     _attr_has_entity_name = True
#     _attr_entity_category = EntityCategory.DIAGNOSTIC

#     def __init__(
#             self,
#             hass: HomeAssistant, 
#             coordinator: DataUpdateCoordinator[dict[str, Any]],
#             entry: ConfigEntry,
#             name: str,
#             unique_key: str
#     ) -> None:
#         super().__init__(coordinator)
#         self._hass = hass
#         self._entry = entry
#         self._attr_name = name
#         self._attr_unique_id = slugify(f"{entry.entry_id}_{unique_key}")

#     @property
#     def _entities_state(self) -> dict:
#         """
#         Restituisce il dizionario delle entità Home Assistant attive.

#         Returns:
#             dict: Mappa degli stati delle entità registrate in Home Assistant.
#         """
#         return self._hass.data[DOMAIN][self._entry.entry_id][ENTITIES_STATE]
    
#     @property
#     def device_info(self) -> DeviceInfo:
#         return DeviceInfo(
#             identifiers={(DOMAIN, self._entry.entry_id)},
#             name=INTEGRATION_NAME,
#             manufacturer=INTEGRATION_MANUFACTURER,
#             sw_version=INTEGRATION_VERSION,
#         )

#     @property
#     def available(self) -> bool:
#         return self.coordinator.last_update_success

# class DewpointSensor(BaseSensor):
#     """..."""
#     DWP_NAME_POSTFIX = "Dew-Point"

#     def __init__(
#             self,
#             hass: HomeAssistant, 
#             coordinator: DataUpdateCoordinator[dict[str, Any]],
#             entry: ConfigEntry,
#             name: str,
#             sensors: SensorPair,
#             temperature_unit: str,
#     ) -> None:
#         super().__init__(
#             hass,
#             coordinator,
#             entry,
#             f"{name} {self.DWP_NAME_POSTFIX}",
#             f"{name} {self.DWP_NAME_POSTFIX} uid"
#         )
#         self._attr_native_unit_of_measurement = temperature_unit
#         self._attr_device_class = SensorDeviceClass.TEMPERATURE
#         self._attr_state_class = SensorStateClass.MEASUREMENT
#         self._attr_suggested_display_precision = 1

#         self._sensors = sensors

#     @property
#     def native_value(self):
#         """Restituisce il valore del sensore di punto di rugiada."""
#         dew_point = None

#         temperature = self._entities_state.get(self._sensors.temperature, None)
#         humidity = self._entities_state.get(self._sensors.humidity, None)

#         if temperature and humidity:
#             dew_point = dew_point_celsius( temperature, humidity )

#         return dew_point




# sensor.py
from __future__ import annotations

import logging
from typing import Any, Optional

from homeassistant.components.sensor import (
    SensorEntity,
    RestoreSensor,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import UnitOfTemperature, EntityCategory
# from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    CoordinatorEntity,
)
from homeassistant.helpers.device_registry import DeviceInfo

from .helpers.utils import slugify, as_float

from .domain.models import SensorPair
from .helpers.psychrometric import celsius_to_fahrenheit, dew_point_celsius
from .const import (
    DOMAIN,
    ENTITIES_STATE,
    INTEGRATION_MANUFACTURER,
    INTEGRATION_NAME,
    INTEGRATION_VERSION,
)

_LOGGER = logging.getLogger(__name__)


class BaseSensor(
    CoordinatorEntity[DataUpdateCoordinator[dict[str, Any]]],
    RestoreSensor,
    SensorEntity,
):
    """
    Base class per sensori guidati da DataUpdateCoordinator.

    - Disabilita il polling (gli update arrivano dal coordinator).
    - Fornisce `device_info` coerente con l'entry dell'integrazione.
    - Espone un accesso comodo allo store degli stati delle entità esterne
      tenuto dall'integrazione (chiave ENTITIES_STATE).
    """

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC  # default: diagnostico

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: DataUpdateCoordinator[dict[str, Any]],
        entry: ConfigEntry,
        name: str,
        unique_key: str,
    ) -> None:
        super().__init__(coordinator)
        self._hass = hass
        self._entry = entry
        self._attr_name = name
        # unique_id stabile e safe
        self._attr_unique_id = slugify(f"{entry.entry_id}_{unique_key}")

    @property
    def _entities_state(self) -> dict[str, Any]:
        """
        Ritorna il dizionario degli stati delle entità esterne gestito
        dall'integrazione (popolato altrove).

        Returns:
            dict[str, Any]: mappa entity_id -> valore (numero/str/State).
        """
        return self._hass.data[DOMAIN][self._entry.entry_id][ENTITIES_STATE]

    @property
    def device_info(self) -> DeviceInfo:
        """Metadati del dispositivo per raggruppare le entity nel pannello Dispositivi."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=INTEGRATION_NAME,
            manufacturer=INTEGRATION_MANUFACTURER,
            sw_version=str(INTEGRATION_VERSION),
        )

    @property
    def available(self) -> bool:
        """Disponibilità legata allo stato dell'ultimo aggiornamento del coordinator."""
        return bool(self.coordinator.last_update_success)

    async def async_added_to_hass(self) -> None:
        """
        Hook chiamato quando l'entità è aggiunta a Home Assistant.

        Qui ripristiniamo lo stato precedente (se presente) per ridurre
        l'effetto "Unknown" al riavvio.
        """
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and self._attr_native_value is None:
            try:
                # Proviamo a ripristinare il valore numerico dal last_state
                self._attr_native_value = as_float(last_state.state)
                self.async_write_ha_state()
            except Exception as ex:  # noqa: BLE001
                _LOGGER.debug("Restore skipped for %s: %s", self.entity_id, ex)

class DewpointSensor(BaseSensor):
    """
    Sensore di Punto di Rugiada (Dew Point).

    Calcola il dew point a partire da:
    - temperatura aria (°C)
    - umidità relativa (%)

    Il calcolo sfrutta `dew_point_celsius(...)` che a sua volta usa PsychroLib.
    L'unità di misura esposta può essere °C o °F (conversione effettuata qui).
    """

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1
    _attr_entity_category = None  # è una misura "normale", non diagnostica

    DWP_NAME_POSTFIX = "Dew-Point"

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: DataUpdateCoordinator[dict[str, Any]],
        entry: ConfigEntry,
        name: str,
        sensors: SensorPair,
        temperature_unit: UnitOfTemperature | str = UnitOfTemperature.CELSIUS,
    ) -> None:
        super().__init__(
            hass=hass,
            coordinator=coordinator,
            entry=entry,
            name=f"{name} {self.DWP_NAME_POSTFIX}",
            unique_key=f"{name} {self.DWP_NAME_POSTFIX} uid",
        )
        # Normalizza l'unità: accettiamo sia enum sia stringhe ("°C"/"C" o "°F"/"F")
        if isinstance(temperature_unit, str):
            tu = temperature_unit.strip().upper().replace("°", "")
            if tu in ("C", "CELSIUS"):
                temperature_unit = UnitOfTemperature.CELSIUS
            elif tu in ("F", "FAHRENHEIT"):
                temperature_unit = UnitOfTemperature.FAHRENHEIT
            else:
                _LOGGER.warning(
                    "Unità temperatura sconosciuta %r, uso Celsius di default", temperature_unit
                )
                temperature_unit = UnitOfTemperature.CELSIUS

        self._target_temp_unit: UnitOfTemperature = temperature_unit  # unità esposta
        self._attr_native_unit_of_measurement = self._target_temp_unit
        self._sensors = sensors

    @property
    def native_value(self) -> Optional[float]:
        """
        Valore nativo del sensore (dew point).

        Ritorna:
            float | None: dew point nella stessa unità dichiarata dall'entità.
                          None se mancano i dati o non sono validi.
        """
        # Recupero valori grezzi dal registry interno dell’integrazione
        raw_t = self._entities_state.get(self._sensors.temperature)
        raw_rh = self._entities_state.get(self._sensors.humidity)

        t_c = as_float(raw_t)
        rh = as_float(raw_rh)

        if t_c is None or rh is None:
            return None

        # dew_point_celsius richiede T in °C e RH in percento
        try:
            dp_c = dew_point_celsius(t_c, rh)
        except Exception as ex:  # noqa: BLE001
            _LOGGER.debug("Impossibile calcolare il dew point: %s", ex)
            return None

        # Conversione nell'unità richiesta dall'entità
        if self._target_temp_unit == UnitOfTemperature.FAHRENHEIT:
            dp_val = celsius_to_fahrenheit(dp_c)
        else:
            dp_val = dp_c

        # Applica precisione suggerita senza cambiare il tipo (float)
        precision = self._attr_suggested_display_precision or 1
        try:
            return round(float(dp_val), precision)
        except Exception:
            return float(dp_val)
