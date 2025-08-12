"""Helper functions for .... component."""

import logging
import operator
from datetime import date
from typing import Any, Callable, Iterable, Optional, Union

import holidays
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import async_get_platforms
from homeassistant.helpers.event import (
    async_track_state_change_event,
)
from homeassistant.exceptions import HomeAssistantError

# from custom_components.drp_climate_master.utils.const import TURN 

_LOGGER = logging.getLogger(__name__)

def is_number(string):
    """ Returns True is string is a number. """
    try:
        float(string)
        return True
    except (ValueError, TypeError):
        return False
    
def convert_to_int(
    value: Union[str, int, float, None],
    instance_name: str = "DRP Climate Master",
    context: str = "unknown"
) -> Union[int, None]:
    """Convert value to int or print error message.

    Parameters
    ----------
    value : str, int, float, None
            The value to convert to int
    instance_name : str
            The name of the instance for logging
    context : str
            The context in which the conversion is attempted

    Returns
    -------
    int or None
            The converted value as int, or None if conversion fails
    """
    if isinstance(value, int):
        return value
    elif value is None or value == "None":
        return None
    else:
        try:
            return int(float(value))
        except (ValueError, TypeError, AttributeError, KeyError):
            _LOGGER.debug(
                "convert_to_int - %s: Could not convert '%s' to int in %s",
                instance_name, value, context
            )
            return None
        
def convert_to_float(
    value: Union[str, int, float, None], instance_name: str = "DRP Climate Master", context: str = "unknown"
) -> Union[float, None]:
    """Convert value to float or print error message.

    Parameters
    ----------
    value : str, int, float
            the value to convert to float
    instance_name : str
            the name of the instance thermostat
    context : str
            the name of the function which is using this, for printing an error message

    Returns
    -------
    float
            the converted value
    None
            If error occurred and cannot convert the value.
    """
    if isinstance(value, float):
        return round(value, 1)
    elif value is None or value == "None":
        return None
    else:
        try:
            return round(float(str(format(float(value), ".1f"))), 1)
        except (ValueError, TypeError, AttributeError, KeyError):
            _LOGGER.debug(
                "convert_to_float - %s: Could not convert '%s' to float in %s",
                instance_name, value, context
            )
            return None

def is_working_day(check_date: date) -> bool:
    """
    Determina se una data è un giorno lavorativo in Italia.

    Args:
        check_date (date): La data da controllare.

    Returns:
        bool: True se è un giorno lavorativo, False altrimenti.
    """
    # Festività italiane
    # it_holidays = holidays.Italy(years=check_date.year)
    it_holidays = holidays.country_holidays("IT", subdiv='RM', years=check_date.year)

    # Controlla se è un sabato, una domenica o una festività
    if check_date.weekday() >= 5 or check_date in it_holidays:
        return False
    return True

def is_leap_year(year):
    return (year % 4 == 0) and (year % 100 != 0 or year % 400 == 0)

def get_platform(hass, name):
    platform_list = async_get_platforms(hass, name)

    for platform in platform_list:
        if platform.domain == name:
            return platform

    return None

async def async_platform_add_entities(hass: HomeAssistant, platform: str, entities: list, discovery_info=True) -> None:
    """ ... """
    if discovery_info is None:
        _LOGGER.warning( 'discovery_info is None' )
        return

    entity_platform = get_platform(hass, platform)

    if entity_platform:
        await entity_platform.async_add_entities(entities, discovery_info)
    else:
        _LOGGER.warning( 'Platform %s not found.', platform )

def setup_entity_change(self, callback: Callable, entity_id : Iterable[str]=()) -> bool:
    """ ... """
    # _LOGGER.info( "setup_entity_change for '%s'.", str(entity_id) )
    async def _async_track_state_change_event(self, callback: Callable, entity_id : Iterable[str]=() ):
        self.async_on_remove(
            async_track_state_change_event(
                hass=self._hass, entity_ids=entity_id, action=callback) # pylint: disable=protected-access
        ) 
        _LOGGER.info( "setup_entity_change for '%s'.", str(entity_id) )

    if entity_id and callback:
        self._hass.async_create_task( _async_track_state_change_event(self, callback, entity_id ) ) # pylint: disable=protected-access
    else:
        _LOGGER.error( "setup_entity_change for '%s' not setted.", entity_id )

    return True

async def async_switch_turn(self, entity_id : str, state: str):
    await self._hass.services.async_call(
        "homeassistant", state, {"entity_id": entity_id}
    )

async def async_number_set_value(self, entity_id, value):
    await self._hass.services.async_call(
        "number", "set_value", {"entity_id": entity_id, "value": value}
    )

async def async_input_select_set_value(self, entity_id, value):
    """Imposta il valore di un input_select con gestione errori e propagazione."""
    try:
        await self._hass.services.async_call(
            "input_select", "select_option", {
                "entity_id": entity_id,
                "option": value
            }
        )
    except HomeAssistantError as e:
        _LOGGER.error(f"Errore impostando {entity_id} su {value}: {e}")
        raise  # Rilancia la stessa eccezione verso l'alto
    except Exception as e:
        _LOGGER.exception(f"Errore imprevisto impostando {entity_id}: {e}")
        raise  # Anche qui rilancia l'eccezione originale

def weighted_average(valori, pesi):
  """
  Calcola la media ponderata di un insieme di dati con pesi specifici.

  Argomenti:
    valori: Una lista contenente i valori da ponderare.
    pesi: Una lista contenente i pesi da associare ai valori corrispondenti.

  Restituisce:
    La media ponderata calcolata.
  """

  if len(valori) != len(pesi):
    raise ValueError("Lunghezze di valori e pesi devono essere uguali")

  somma_prodotti = 0
  somma_pesi = 0

  for valore, peso in zip(valori, pesi):
    somma_prodotti += valore * peso
    somma_pesi += peso

  if somma_pesi == 0:
    raise ZeroDivisionError("Somma dei pesi pari a zero")

  media_ponderata = somma_prodotti / somma_pesi
  return media_ponderata

def filter_dict_by_key_prefix(data: dict, prefix: str) -> dict:
    """
    Estrae tutte le coppie chiave-valore da un dizionario che hanno una chiave che inizia con il prefisso specificato.

    Args:
        data (dict): Il dizionario da cui estrarre le coppie chiave-valore.
        prefix (str): Il prefisso delle chiavi da estrarre.

    Returns:
        dict: Un nuovo dizionario contenente solo le coppie chiave-valore con chiavi che iniziano con il prefisso specificato.
    """
    return {k: v for k, v in data.items() if k.lower().startswith(prefix.lower())}

def is_entity_state(
    device_data: Optional[dict] = None, 
    unique_id: str = '', 
    state: Union[str, float, int, None] = None, 
    compare: Callable[[Any, Any], bool] = operator.eq
) -> Optional[bool]:
    """
    Check if the state of a specific entity matches the given state, favoring numeric comparison.

    Args:
        device_data (dict, optional): A dictionary containing device data with unique IDs as keys.
        unique_id (str, optional): The unique identifier of the entity to check.
        state (str | float | int | None, optional): The state to compare against.
        compare (Callable, optional): Comparison function (default operator.eq).

    Returns:
        Optional[bool]: True if the entity's state matches, False if not, None if invalid input.
    """
    if device_data is None or not unique_id or state is None:
        return None

    entity = device_data.get(unique_id)
    if entity is None:
        return None

    entity_state = entity.state

    # Proviamo prima il confronto numerico se entrambi sono numeri
    try:
        entity_state_num = float(entity_state)
        state_num = float(state)
        return compare(entity_state_num, state_num)
    except (ValueError, TypeError):
        # Se non sono numeri validi, facciamo confronto stringhe
        return compare(str(entity_state), str(state))

    return None

def copy_tuple(source_dict: dict, destination_dict: dict, key: str) -> None:
    """
    Copia una tupla da un dizionario a un altro.

    Args:
        source_dict (dict): Il dizionario di origine.
        destination_dict (dict): Il dizionario di destinazione.
        key (str): La chiave della tupla da copiare.
    """
    if key in source_dict:
        destination_dict[key] = source_dict[key]

def copy_structure(data: dict, target_key: str) -> dict:
    """
    Cerca una chiave specifica in un dizionario annidato e restituisce la struttura che contiene quella chiave.

    Args:
        data (dict): Il dizionario da cui cercare la chiave.
        target_key (str): La chiave da cercare.

    Returns:
        dict: La struttura che contiene la chiave specificata, o None se la chiave non è trovata.
    """
    if target_key in data:
        return {k: v for k, v in data.items() if k == target_key}

    for key, value in data.items():
        if isinstance(value, dict):
            result = copy_structure(value, target_key)
            if result:
                return {key: result}

    return {}

def get_entity_state( device_data: dict = {}, unique_id : str = '' ) -> Union[str, None]:
    """
    Get the current state of a specific entity.

    Args:
        device_data (dict, requested): A dictionary containing device data with unique IDs as keys.
        unique_id (str, requested): The unique identifier of the entity to check.

    Returns:
        Union[str, None]: Returns the current state of the entity, or None if the entity is not found.
    """
    if device_data and unique_id:
        entity = device_data.get(unique_id, None)
        if entity:
            return str(entity.state)
            
    return None
