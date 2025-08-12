"""Support for DRP Climate."""
from dataclasses import dataclass, field
import os
import json
from datetime import datetime, date, timedelta
import logging

from enum import Enum
# from enum import StrEnum
from typing import Any, List, Literal, Optional

# from homeassistant.const import (
#     CONF_DESCRIPTION,
#     CONF_UNIQUE_ID,
# )
from homeassistant.helpers.entity_component import EntityComponent
from homeassistant.util.hass_dict import HassKey

from homeassistant.components.climate import ClimateEntity

_LOGGER = logging.getLogger(__name__)

DOMAIN = 'drp_climate_master'
SCAN_INTERVAL = timedelta(seconds=60)

DEFAULT_CLIMATE_NAME = "DRP Climate Master"
DEFAULT_TEMP_UNIT = "°C"

DATA_COMPONENT: HassKey[EntityComponent[ClimateEntity]] = HassKey("climate_entity")
HUB_COMPONENT = HassKey("climate_hub")
STATES = "states"
STATE_UNAVAILABLE = "unavailable"

# ########## # ########## # ########## # ########## #

VERSION = "master"

try:
    with open(
        f"{os.path.dirname(os.path.realpath(__file__))}/../manifest.json", encoding="utf-8"
    ) as manifest_file:
        manifest = json.load(manifest_file)
        NAME = manifest["name"]
        VERSION = manifest["version"]
        ISSUE_URL = manifest["issue_tracker"]
except (FileNotFoundError, KeyError, json.JSONDecodeError) as e:
    _LOGGER.error("better_thermostat %s: could not read version from manifest file.", e)

STARTUP_MESSAGE = f"""
-------------------------------------------------------------------
{NAME}
Version: {VERSION}
This is a custom integration!
If you have any issues with this you need to open an issue here:
{ISSUE_URL}
-------------------------------------------------------------------
"""

class TimeRange:
    """Rappresenta un intervallo orario e permette di verificare se l'ora attuale rientra nell'intervallo."""

    def __init__(self, start: datetime, end: datetime):
        self.start = start
        self.end = end

    def is_now_inside(self) -> bool:
        """Restituisce True se l'ora attuale rientra nell'intervallo."""
        now = datetime.now()
        if self.start <= self.end:
            # Caso normale (es: 10:00 - 18:00)
            return self.start <= now <= self.end
        else:
            # Caso overnight (es: 22:00 - 06:00 del giorno dopo)
            return now >= self.start or now <= self.end

    def __str__(self) -> str:
        return f"{self.start.strftime('%H:%M:%S')} -> {self.end.strftime('%H:%M:%S')}"
    
    def time_passed(self) -> int:
        """Restituisce il numero di minuti trascorsi dall'inizio dell'intervallo. Se fuori intervallo, restituisce 0."""
        now = datetime.now()
        if self.start <= now <= self.end:
            delta = now - self.start
            return int(delta.total_seconds() // 60)
        else:
            return 0
    
    @classmethod
    def from_hours(cls, start_hour: int, start_minute: int, end_hour: int, end_minute: int) -> "TimeRange":
        """Costruisce un TimeRange per oggi, solo da ore/minuti."""
        today = datetime.now()
        start = today.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
        end = today.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)
        
        if end <= start:
            # Se l'orario di fine è "prima" di quello di inizio, significa overnight → end è il giorno dopo
            end += timedelta(days=1)
        
        return cls(start=start, end=end)

class TimeRangeSet:
    """Contiene più intervalli orari e verifica se l'ora attuale rientra in almeno uno di essi."""
    
    def __init__(self, ranges: List[TimeRange]):
        self.ranges = ranges

    def is_now_inside(self) -> bool:
        """True se l'ora attuale è dentro almeno uno degli intervalli."""
        return any(r.is_now_inside() for r in self.ranges)

    def __str__(self) -> str:
        return "; ".join(str(r) for r in self.ranges)
    
    @classmethod
    def from_hours_list(cls, hours_list: List[tuple]) -> "TimeRangeSet":
        """Costruisce un TimeRangeSet da una lista di tuple (start_hour, start_minute, end_hour, end_minute)."""
        ranges = [TimeRange.from_hours(*h) for h in hours_list]
        return cls(ranges)

@dataclass
class Temperature:
    """Classe che rappresenta una temperatura e il suo delta."""
    value: float
    delta: float

    def init(self, temperature: float, delta: float) -> None:
        """Metodo per inizializzare o aggiornare i valori."""
        self.value = temperature
        self.delta = delta

    @property
    def min(self) -> float:
        """Valore minimo accettabile (value - delta)."""
        return self.value - self.delta

    @property
    def max(self) -> float:
        """Valore massimo accettabile (value + delta)."""
        return self.value + self.delta

    def is_within_bounds(self, temp: float) -> bool:
        """Controlla se una temperatura è all'interno dell'intervallo [min, max]."""
        return self.min <= temp <= self.max


@dataclass
class HvacThreshold:
    """ ... """
    mode: Optional[Literal["heating", "cooling", "dehumidifying"]] = None
    state_on: Optional[float] = None
    state_off: Optional[float] = None
    
    def __str__(self) -> str:
        return (
            f"HvacThreshold(mode={self.mode}, "
            f"state_on={self.state_on}, "
            f"state_off={self.state_off})"
        )

@dataclass
class SeasonSetpoint:
    """ ... """
    temperature_min: float
    temperature_max: float
    humidity_min: float
    humidity_max: float
    dew_point: Optional[float] = None
    heating: Optional[HvacThreshold] = None
    cooling: Optional[HvacThreshold] = None

    def as_dict(self) -> dict[str, Any]:
        """ ... """
        return {
            "temperature_min": self.temperature_min,
            "temperature_max": self.temperature_max,
            "humidity_min": self.humidity_min,
            "humidity_max": self.humidity_max,
            "dew_point": self.dew_point,
            "heating": vars(self.heating) if self.heating else None,
            "cooling": vars(self.cooling) if self.cooling else None,
        }
    
    def action_by_temperature(self, current_temp: float) -> (HvacThreshold | None):
        """
        Determina l'azione consigliata ("heat", "cool", "none") basata sulla temperatura corrente.
        """
        if self.heating and self.heating.state_on and current_temp < self.heating.state_on:
            return self.heating
        elif self.cooling and self.cooling.state_on and current_temp > self.cooling.state_on:
            return self.cooling
        return None
    
    def __str__(self) -> str:
        """Restituisce una rappresentazione leggibile del setpoint della stagione."""
        results = f"""Temperature comfort zone :: {self.temperature_min}°C - {self.temperature_max}°C
Humidity comfort zone    :: {self.humidity_min}% - {self.humidity_max}%
Dew Point target         :: {self.dew_point if self.dew_point is not None else 'N/A'}°C
"""
        if self.heating:
            results += f"""Heating thresholds: 🔥
  ON  < {self.heating.state_on}°C
  OFF > {self.heating.state_off}°C
"""
        if self.cooling:
            results += f"""Cooling thresholds: ❄️
  ON  > {self.cooling.state_on}°C
  OFF < {self.cooling.state_off}°C
"""
        # results += "-------------------------------------------------------------------"
        return results
        
class ClimateSensorType(str, Enum):
    """Climate Sensor Types"""
    CURRENT_TEMPERATURE = "current_temperature"
    CURRENT_HUMIDITY = "current_humidity"
    CURRENT_DEW_POINT = "current_dew_point"
    CURRENT_HEAT_INDEX = "current_heat_index"
    CURRENT_DEW_POINT_PERCEPTION = "current_dew_point_perception"
    MIN_CONFORT_TEMPERATURE = "min_confort_temperature"
    MAX_CONFORT_TEMPERATURE = "max_confort_temperature"
    MIN_CONFORT_HUMIDITY = "min_confort_humidity"
    MAX_CONFORT_HUMIDITY = "max_confort_humidity"
    DEW_POINT = "dew_point"
    HEAT_INDEX = "heat_index"

class DewPointPerception:
    """ ... """
    def __init__(self, unique_id, description, icon):
        self._unique_id = unique_id
        self._description = description
        self._icon = icon

    @property
    def unique_id(self):
        """ ... """
        return self._unique_id

    @property
    def description(self):
        """ ... """
        return self._description

    @property
    def icon(self):
        """ ... """
        return self._icon


class DewPointPerceptions(Enum):
    """Thermal Perception."""

    DRY = DewPointPerception("dry", "Dry", "mdi:emoticon-cool-outline")
    VERY_COMFORTABLE = DewPointPerception(
        "very_comfortable", "Very comfortable", "mdi:emoticon-happy-outline")
    COMFORTABLE = DewPointPerception(
        "comfortable", "Comfortable", "mdi:emoticon-outline")
    OK_BUT_HUMID = DewPointPerception(
        "ok_but_humid", "Ok but humid", "mdi:emoticon-neutral-outline")
    SOMEWHAT_UNCOMFORTABLE = DewPointPerception(
        "somewhat_uncomfortable", "Somewhat uncomfortable", "mdi:emoticon-sad-outline")
    QUITE_UNCOMFORTABLE = DewPointPerception(
        "quite_uncomfortable", "Quite Uncomfortable", "mdi:emoticon-angry-outline")
    EXTREMELY_UNCOMFORTABLE = DewPointPerception(
        "extremely_uncomfortable", "Extremely uncomfortable", "mdi:emoticon-cry-outline")
    SEVERELY_HIGH = DewPointPerception(
        "severely_high", "Severely high", "mdi:emoticon-dead-outline")

class Seasons(str, Enum):
    """Seasons"""
    WINTER = "winter"
    SPRING = "spring"
    SUMMER = "summer"
    AUTUMN = "autumn"

@dataclass
class SeasonData:
    """ ... """
    label: Seasons
    days: int
    passed: int
    remaining: int
    overridden: Seasons
    weather_anomaly: bool
    season_scores: dict[Seasons, float] = field(default_factory=dict)
    season_probabilities: dict[Seasons, float] = field(default_factory=dict)

    def copy(self) -> "SeasonData":
        """Restituisce una copia indipendente dell'oggetto."""
        return SeasonData(
            label=self.label,
            days=self.days,
            passed=self.passed,
            remaining=self.remaining,
            overridden=self.overridden,
            weather_anomaly=self.weather_anomaly,
            season_scores=dict(self.season_scores),
            season_probabilities=dict(self.season_probabilities),
        )
    def __str__(self) -> str:
        """Restituisce una rappresentazione leggibile della stagione."""
        results = f"""Season label       :: {self.label.value}
Total days         :: {self.days}
Days passed        :: {self.passed}
Days remaining     :: {self.remaining}
Selected override  :: {self.overridden.value}
Weather anomaly    :: {self.weather_anomaly}
-------------------------------------------------------------------
Season Scores & Probabilities:
"""
        results += f"{'Season':<10} {'Score':>10} {'Probability':>14}\n"
        # results += "-" * 38 + "\n"

        # Ordinamento per probabilità decrescente
        sorted_seasons = sorted(
            self.season_probabilities.items(), key=lambda item: item[1], reverse=True
        )

        for season, prob in sorted_seasons:
            score = self.season_scores.get(season, 0.0)
            results += f"{season.value:<10} {score:>10.3f} {prob:>13.1f}%\n"

        # results += "-" * 38
        return results 

@dataclass
class SeasonThreshold:
    """ ... """
    season: Seasons
    setpoint_temp_min: float
    setpoint_temp_max: float
    setpoint_humi_min: float
    setpoint_humi_max: float
    setpoint_dew_point: Optional[float] = None
    threshold_heating: Optional[HvacThreshold] = None
    threshold_cooling: Optional[HvacThreshold] = None
    threshold_dehumidifying: Optional[HvacThreshold] = None

    def as_dict(self) -> dict[str, Any]:
        """ ... """
        return {
            "setpoint_temp_min": self.setpoint_temp_min,
            "setpoint_temp_max": self.setpoint_temp_max,
            "setpoint_humi_min": self.setpoint_humi_min,
            "setpoint_humi_max": self.setpoint_humi_max,
            "setpoint_dew_point": self.setpoint_dew_point,
            "threshold_heating": vars(self.threshold_heating) if self.threshold_heating else None,
            "threshold_cooling": vars(self.threshold_cooling) if self.threshold_cooling else None,
            "threshold_dehumidifying": vars(self.threshold_dehumidifying) if self.threshold_dehumidifying else None,
        }

    def __str__(self) -> str:
        """Restituisce una rappresentazione leggibile del setpoint della stagione."""
        season = CONFORT_ZONES[self.season]
        results = f"""Class SeasonThreshold ({self.season.value}):
  Comfort zone temperature     :: {self.setpoint_temp_min}, {season[ConfortAttr.T_MIN]} || {season[ConfortAttr.T_MAX]}, {self.setpoint_temp_max} °C       [dynamic_temp_min, static_temp_min || static_temp_max, dynamic_temp_max]
  Comfort zone humidity        :: {self.setpoint_humi_min}, {season[ConfortAttr.H_MIN]} || {season[ConfortAttr.H_MAX]}% {self.setpoint_humi_max} %            [dynamic_humi_min, static_himi_min || static_himi_max, dynamic_humi_max]
  Comfort zone Dew Point       :: {self.setpoint_dew_point if self.setpoint_dew_point is not None else 'N/A'} || {season[ConfortAttr.DP]} °C                   [dynamic_dew_point || static_dew_point]
"""
        if self.threshold_heating:
            results += f"""  Heating thresholds       🔥  :: turn ON if temp <  {self.threshold_heating.state_on} °C || turn OFF if temp > {self.threshold_heating.state_off} °C
"""
        if self.threshold_cooling:
            results += f"""  Cooling thresholds       ❄️   :: turn ON if temp > {self.threshold_cooling.state_on} °C || turn OFF if temp < {self.threshold_cooling.state_off} °C
"""
        if self.threshold_dehumidifying:
            results += f"""  Dehumidifying thresholds 💧  :: turn ON if Dew Point > {self.threshold_dehumidifying.state_on} °C || turn OFF if Dew Point < {self.threshold_dehumidifying.state_off} °C
"""

        # results += "-------------------------------------------------------------------"
        return results

SEASONS_BY_DATE = {
  Seasons.WINTER: (date(2023, 12, 1), date(2024, 2, 28)),
  Seasons.SPRING: (date(2024, 3,  1), date(2024, 5, 31)),
  Seasons.SUMMER: (date(2024, 6, 1), date(2024, 8, 31)),
  Seasons.AUTUMN: (date(2024, 9, 1), date(2023, 11, 30)),
}

class ConfortAttr(str, Enum):
    """Confort Attributes"""
    T_MIN = "temp_min"
    T_MAX = "temp_max"
    H_MIN = "hum_min"
    H_MAX = "hum_max"
    DT = "delta_temp"
    DH = "delta_hum"
    DP = "dew_point"

CONFORT_ZONES = {
    Seasons.SUMMER: {
        ConfortAttr.T_MIN.value: 24.0,    # Mantieni comfort fresco
        ConfortAttr.T_MAX.value: 27.0,
        ConfortAttr.H_MIN.value: 40,      # Non troppo secco
        ConfortAttr.H_MAX.value: 58,      # Maggiore copertura dew point a 27°C
        ConfortAttr.DT.value: 0.9,        # Leggermente più permissiva
        ConfortAttr.DH.value: 2.0,        # Intervallo dinamico più efficace
        ConfortAttr.DP.value: 18.5        # Punto di rugiada target per innesco deumidificazione
    },
    Seasons.AUTUMN: {
        ConfortAttr.T_MIN.value: 18.0,  # Leggero abbassamento rispetto alla primavera
        ConfortAttr.T_MAX.value: 23.7,  # Tendenza a temperature più miti verso la fine
        ConfortAttr.H_MIN.value: 45,      # Leggero abbassamento per evitare condensa
        ConfortAttr.H_MAX.value: 65,
        ConfortAttr.DT.value: 0.6,
        ConfortAttr.DH.value: 1.5,   # Umidità più elevata e variabile,
        ConfortAttr.DP.value: 15.5
    },
    Seasons.WINTER: {
        ConfortAttr.T_MIN.value: 17.5,
        ConfortAttr.T_MAX.value: 20.9,
        ConfortAttr.H_MIN.value: 40.0,
        ConfortAttr.H_MAX.value: 58.0,    # Leggero abbassamento per margine su muffe
        ConfortAttr.DT.value: 0.5,
        ConfortAttr.DH.value: 1.0,
        ConfortAttr.DP.value: 9.5
    },
    Seasons.SPRING: {
        ConfortAttr.T_MIN.value: 19.0,  # Ridotta per i mesi iniziali
        ConfortAttr.T_MAX.value: 24.0,  # Più variabile
        ConfortAttr.H_MIN.value: 45,
        ConfortAttr.H_MAX.value: 65,
        ConfortAttr.DT.value: 0.8,  # Maggiore variabilità termica
        ConfortAttr.DH.value: 1.4,   # Umidità più variabile
        ConfortAttr.DP.value: 14.5
    },
}

# Fattore moltiplicativo opzionale per modulare l’isteresi per stagione
HYSTERESIS_SEASON_MAP = {
    Seasons.WINTER: {"comfort": 2.0, "eco": 2.5, "away": 3.0},
    Seasons.SUMMER: {"comfort": 2.0, "eco": 2.5, "away": 3.0},
    Seasons.SPRING: {"comfort": 2.2, "eco": 3.0, "away": 3.5},
    Seasons.AUTUMN: {"comfort": 2.2, "eco": 3.0, "away": 3.5},
}

BIAS_TEMP_SEASON_MAP = {
    Seasons.WINTER:  +0.762,
    Seasons.SUMMER:  +1.06,
    Seasons.SPRING:   0.0,
    Seasons.AUTUMN:  -0.2,
}

BIAS_HUMI_SEASON_MAP = {
    Seasons.WINTER:  +4,
    Seasons.SUMMER:  -4,
    Seasons.SPRING:   0,
    Seasons.AUTUMN:  -2,
}





PROCESSING_OFF = "Off"

TURN_ON = "turn_on"
TURN_OFF = "turn_off"

TURN = Literal[TURN_ON, TURN_OFF]
HEATING = "heating"
COOLING = "cooling"
DEHUMIDIFICATION = "dehumidification"
DEW_POINT = "dew_point"

SENSOR_CURRENT_TEMP_UID = 'sensor.ambient_home_current_temperature'
SENSOR_CURRENT_HUMI_UID = 'sensor.ambient_home_current_humidity'
SENSOR_CURRENT_DWP_UID = 'sensor.ambient_home_current_dew_point'
SENSOR_CURRENT_HNX_UID = 'sensor.ambient_home_current_heat_index'
SENSOR_MAX_CONFORT_T_UID = 'sensor.ambient_home_max_confort_temperature'
SENSOR_MIN_CONFORT_T_UID = 'sensor.ambient_home_min_confort_temperature'
SENSOR_MAX_CONFORT_H_UID = 'sensor.ambient_home_max_confort_humidity'
SENSOR_MIN_CONFORT_H_UID = 'sensor.ambient_home_min_confort_humidity'

T_NAME_POSTFIX = "Temperature"
H_NAME_POSTFIX = "Humidity"
MAX_CT_NAME_POSTFIX = "Max Confort Temperature"
MIN_CT_NAME_POSTFIX = "Min Confort Temperature"
MAX_CH_NAME_POSTFIX = "Max Confort Humidity"
MIN_CH_NAME_POSTFIX = "Min Confort Humidity"
DWP_NAME_POSTFIX = "Dew-Point"
HNX_NAME_POSTFIX = "Heat-Index"
ATTR_TEMPERATURE = "temperature"
ATTR_DELTA_TEMPERATURE = "delta-temperature"

# ########## # ########## # ########## # ########## #
# BASE_CLIMATE_SCHEMA
# ########## # ########## # ########## # ########## #
CONF_CLIMATE = "climate"

CONF_HOME = "home"
CONF_TEMP = "temp"
CONF_HUM = "hum"
CONF_AVG_DEW_POINT = "t_avg_dew_point"
CONF_AVG_H_INDEX = "t_avg_h_index"

CONF_MAX_TEMP = "max_temp"
CONF_MIN_TEMP = "min_temp"
# CONF_TEMP_MAX = "temp_max"
# CONF_TEMP_MIN = "temp_min"
# CONF_HUM_MIN = "hum_min"
# CONF_HUM_MAX = "hum_min"
CONF_STEP = "temp_step"
CONF_AREAS = "areas"
CONF_DEVICES = "devices"
CONF_WEATHER = "weather"
CONF_SCENARIOS = "scenarios"

# ########## # ########## # ########## # ########## #
# SCENARIOS_SCHEMA
# ########## # ########## # ########## # ########## #
CONF_VACATION = "vacation"
CONF_NOBODYSIN = "nobodysin"

# ########## # ########## # ########## # ########## #
# DEVICES_SCHEMA
# ########## # ########## # ########## # ########## #
CONF_SUPPLY_UNITS = "supply_units"
CONF_RADIANT = "radiant"
CONF_VMC = "vmc"

# ########## # ########## # ########## # ########## #
# VMC_SCHEMA
# ########## # ########## # ########## # ########## #
CONF_POWER = "power"
CONF_T_SETPOINT = "t_setpoint"
CONF_H_SETPOINT = "h_setpoint"
CONF_DEW_POINT_SETPOINT = "t_dew_point_setpoint"
CONF_DELTA_DEW_POINT_SETPOINT = "delta_t_dew_point_setpoint"
CONF_SPARE_SETPOINT = "spare_setpoint"
CONF_VENT_RECIRCULATION = "vent_recirculation"
CONF_FORCE_HEATING = "force_heating"
CONF_FORCE_COOLING = "force_cooling"
CONF_FORCE_FREE_COOLING = "force_free_cooling"

CONF_SEASON = "season"
CONF_ACTUATOR = "actuator"
CONF_WINTER = "winter"
CONF_SUMMER = "summer"
CONF_AUTUMN = "autumn"
CONF_SPRING = "spring"

CONF_COMPRESSOR_MANAGEMENT = "compressor_management"
CONF_DEHUMIDIFICATION_OR_COOLING = "dehumidification_or_cooling"
CONF_DEHUMIDIFICATION_ONLY = "dehumidification_only"
CONF_COOLING_ONLY = "cooling_only"

CONF_COOLING_MANAGEMENT = "cooling_management"
CONF_COMPRESSOR_ONLY = "compressor_only"
CONF_WATER_ONLY = "water_only"
CONF_FIRST_WATER_THEN_COMPRESSOR = "first_water_then_compressor"

CONF_REQUESTS = "requests"
CONF_WATER = "water"
CONF_DEHUMIDIFICATION = "dehumidification"
CONF_HEATING = "heating"
CONF_COOLING = "cooling"

# ########## # ########## # ########## # ########## #
# CONF_SENSORS
# ########## # ########## # ########## # ########## #
CONF_T_AMBIENT = "t_ambient"
CONF_H_AMBIENT = "h_ambient"
CONF_T_WATER = "t_water"
CONF_T_OUTDOOR = "t_outdoor"
CONF_POWER_ON_NIGHT = "power_on_night"
CONF_POWER_ON_TODAY = "power_on_today"

CONF_ALARMS = "alarms"
CONF_HIGH_PRESSURE = "high_pressure"
CONF_DEW_POINT = "dew_point"
CONF_LOW_WATER_TEMP = "low_water_temp"
CONF_HIGH_WATER_TEMP = "high_water_temp"
CONF_ALARM = "alarm"
CONF_HOME_WINDOWS_STATE = "home_windows_state"

# ########## # ########## # ########## # ########## #
# RADIANT_SCHEMA
# ########## # ########## # ########## # ########## #
CONF_PDC_TEMP_WATER_IN = "pdc_temp_water_in"
CONF_PDC_TEMP_WATER_OUT = "pdc_temp_water_out"
CONF_BOILER_TEMP_SYSTEM_SUPPLY = "boiler_temp_system_supply"
CONF_BOILER_TEMP_SYSTEM_RETURN = "boiler_temp_system_return"

CONF_FM_POWER = "fm_power"
CONF_MODE = "mode"
CONF_HEATING_T_SETPOINT = "heating_t_setpoint"
CONF_HEATING_DT_SETPOINT = "heating_dt_setpoint"
CONF_COOLING_T_SETPOINT = "cooling_t_setpoint"
CONF_COOLING_DT_SETPOINT = "cooling_dt_setpoint"
CONF_VALUE = "value"

# ########## # ########## # ########## # ########## #
# SUPPLY_UNITS_SCHEMA
# ########## # ########## # ########## # ########## #

CONF_DIRECT_SUPPLY_UNIT = "direct_supply_unit"
CONF_ADJUSTABLE_SUPPLY_UNIT = "adjustable_supply_unit"
CONF_THREE_POINT_MIXING_VALVE = "three_point_mixing_valve"

CONF_ADJUSTABLE_TEMP_SYSTEM_SUPPLY = "adjustable_temp_system_supply"
CONF_ADJUSTABLE_TEMP_SYSTEM_RETURN = "adjustable_temp_system_return"
CONF_DIRECT_TEMP_SYSTEM_SUPPLY = "direct_temp_system_supply"
CONF_DIRECT_TEMP_SYSTEM_RETURN = "direct_temp_system_return"

# ########## # ########## # ########## # ########## #
# AREAS_SCHEMA
# ########## # ########## # ########## # ########## #
CONF_AREA = "area"
CONF_INDOOR = 'indoor'
CONF_TERRACE = 'terrace'
CONF_TEMPERATURE = 'temperature'
CONF_HUMIDITY = 'humidity'
CONF_TCOLLECTOR = "thermal_collector_valve_switch"
CONF_MQ = "mq"

# ########## # ########## # ########## # ########## #
# 
# ########## # ########## # ########## # ########## #
CONF_ELECTROVALVE = 'electrovalve'
CONF_OUTDOOR = 'outdoor'
CONF_WATER_TEMPERATURE = 'water_temperature'
CONF_OUTDOOR_TEMPERATURE = 'outdoor_temperature'
CONF_AREA_HOME = "Home Current"
CONF_SET_POINTS = "set_points"

# ########## # ########## # ########## # ########## #
# CONF_SENSORS
# ########## # ########## # ########## # ########## #

T_BOILER_SYSTEM_SUPPLY_POWER_ON = 29.5
T_BOILER_SYSTEM_RETURN_POWER_ON = 28.0
T_BOILER_SYSTEM_RETURN_POWER_OFF = 25.0

VOLUME_BOILER = 25  # Volume del boiler in litri
THICKNESS_INSULATION = 0.02  # Spessore dell'isolamento (m)
THERMAL_CONDUCTIVITY = 0.025  # Conduttività termica del materiale isolante (W/m*K)



