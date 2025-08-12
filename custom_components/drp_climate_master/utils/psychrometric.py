"""Helper functions for .... component."""

import copy
from datetime import datetime, date
import logging
import math
from statistics import mean
from typing import Any, cast

import psychrolib

from homeassistant.core import HomeAssistant
# from homeassistant.const import (
#     STATE_OFF,
#     STATE_ON,
# )
from .helpers import is_leap_year
from .const import (
    # CONF_HUMIDITY,
    # CONF_TEMPERATURE,
    BIAS_HUMI_SEASON_MAP,
    BIAS_TEMP_SEASON_MAP,
    CONFORT_ZONES,
    COOLING,
    HEATING,
    HYSTERESIS_SEASON_MAP,
    # COOLING,
    # DEHUMIDIFICATION,
    # DEW_POINT,
    # HEATING,
    # MIN_CT_NAME_POSTFIX,
    # MAX_CT_NAME_POSTFIX,
    # MIN_CH_NAME_POSTFIX,
    # MAX_CH_NAME_POSTFIX,
    SEASONS_BY_DATE,
    ConfortAttr,
    DewPointPerception,
    DewPointPerceptions,
    HvacThreshold,
    SeasonData,
    SeasonSetpoint,
    SeasonThreshold,
    Seasons,
)
from .weather import async_weather_prediction

_LOGGER = logging.getLogger(__name__)

def heat_index(temperature: float, humidity: float) -> float:
    """ Calculate the Ottava Steadman apparent temperature """
    t = temperature * 9 / 5 + 32
    hi = 0.5 * (t + 61.0 + ((t - 68.0) * 1.2) + (humidity * 0.094))
    return ((hi - 32) * 5 / 9)

def relative_humidity(temperature: float, target_dew_point: float) -> float:
    """Calculate the relative humidity for the area based on temperature and target dew point."""
    dp: float = 0

    try:
        dp = psychrolib.GetRelHumFromTDewPoint(temperature, target_dew_point ) * 100
    except ValueError:
        _LOGGER.error("temperature: %s target dew point: %s", str(temperature), str(target_dew_point))

    return (dp)

def dew_point(temperature: float, humidity: float) -> float:
    """Calculate the dew point for the area."""

    dp: float = 0

    try:
        dp = psychrolib.GetTDewPointFromRelHum(temperature, humidity / 100)
    except ValueError:
        _LOGGER.error("temperature: %s humidity: %s", str(temperature), str(humidity))

    return (dp)

def dew_point_perception(dewpoint: float | None) -> (DewPointPerception) | None:
    """Dew Point <https://en.wikipedia.org/wiki/Dew_point>."""

    perception : DewPointPerception = None # type: ignore

    if dewpoint is None:
        return None
    elif dewpoint < 10:
        perception = DewPointPerceptions.DRY.value
    elif dewpoint < 13:
        perception = DewPointPerceptions.VERY_COMFORTABLE.value
    elif dewpoint < 16:
        perception = DewPointPerceptions.COMFORTABLE.value
    elif dewpoint < 18:
        perception = DewPointPerceptions.OK_BUT_HUMID.value
    elif dewpoint < 21:
        perception = DewPointPerceptions.SOMEWHAT_UNCOMFORTABLE.value
    elif dewpoint < 24:
        perception = DewPointPerceptions.QUITE_UNCOMFORTABLE.value
    elif dewpoint < 26:
        perception = DewPointPerceptions.EXTREMELY_UNCOMFORTABLE.value
    else:
        perception = DewPointPerceptions.SEVERELY_HIGH.value

    return perception

def _season_by_date(data: date = date.today()) -> SeasonData:
    """
    Restituisce l'oggetto SeasonData associato alla data specificata.
    Non ritorna mai None: se non trova niente, solleva eccezione.
    """
    today = data
    current_year = today.year

    for label, intervallo in SEASONS_BY_DATE.items():
        deltayear = intervallo[0].year - intervallo[1].year

        start = intervallo[0].replace(year=current_year + (deltayear if deltayear < 0 else 0))
        end = intervallo[1].replace(year=current_year + (0 if deltayear <= 0 else deltayear))

        if is_leap_year(current_year + 1) and end.month == 2:
            end = end.replace(day=29)

        if end < start:
            end = end.replace(year=current_year + 1)

        if start <= today <= end:
            total_days = (end - start).days + 1
            days_passed = (today - start).days
            days_remaining = (end - today).days

            return SeasonData(
                label=label,
                days=total_days,
                passed=days_passed,
                remaining=days_remaining,
                overridden=label,  # inizialmente uguale
                weather_anomaly=False
            )

    raise ValueError(f"Nessuna stagione trovata per la data {today}")

def _season_boost_by_date() -> dict[Seasons, float]:
    """Calcola il boost stagionale dinamico basato sulla data attuale usando una curva gaussiana."""
    today = date.today()
    season_boost = {season: 0.0 for season in Seasons}

    for season, (start_date, end_date) in SEASONS_BY_DATE.items():
        # Adatta anni dinamicamente
        start_date = start_date.replace(year=today.year)
        end_date = end_date.replace(year=today.year)

        if start_date > end_date:
            # Stagioni che attraversano l'anno (es. inverno)
            if today >= start_date or today <= end_date:
                if today.month <= 2:
                    start_date = start_date.replace(year=today.year - 1)
        else:
            if not (start_date <= today <= end_date):
                continue

        total_days = (end_date - start_date).days or 1
        passed_days = (today - start_date).days if today >= start_date else (today - start_date.replace(year=today.year - 1)).days
        progress = passed_days / total_days

        # Calcolo boost con funzione Gaussiana
        center = 0.5    # metà stagione
        sigma = 0.2     # spread: più piccolo => curva più stretta

        exponent = -((progress - center) ** 2) / (2 * sigma ** 2)
        boost = 0.2 * math.exp(exponent)  # massimo 0.2
        season_boost[season] = round(boost, 4)

    return season_boost

def season_data(hass: HomeAssistant, weather_entity_it: str) -> SeasonData:
    """Determina la stagione corrente sulla base delle previsioni meteo e delle zone di comfort definite."""

    def _fallback_season() -> SeasonData:
        fallback_dict = _season_by_date()
        return SeasonData(
            label=fallback_dict.label,
            days=fallback_dict.days,
            passed=fallback_dict.passed,
            remaining=fallback_dict.remaining,
            overridden=fallback_dict.label,
            weather_anomaly=False,
            season_scores={},
            season_probabilities={}
        )

    weather_forecasts = hass.async_create_task( async_weather_prediction(hass, weather_entity_it) ).result()

    if not weather_forecasts:
        return _fallback_season()

    forecast = weather_forecasts.get(weather_entity_it, {}).get("forecast", [])
    if not forecast:
        return _fallback_season()

    season_scores: dict[Seasons, float] = {season: 0.0 for season in CONFORT_ZONES}

    season_weight = {
        Seasons.SUMMER: 1.2,
        Seasons.WINTER: 1.2,
        Seasons.SPRING: 1.0,
        Seasons.AUTUMN: 1.0
    }

    for i, day_forecast in enumerate(forecast):
        try:
            temp_min = day_forecast["templow"]
            temp_max = day_forecast["temperature"]
        except KeyError:
            continue

        weight = max(0.0, 1.0 - (i * 0.1))

        for season, comfort in CONFORT_ZONES.items():
            t_min = comfort[ConfortAttr.T_MIN.value] - comfort[ConfortAttr.DT.value]
            t_max = comfort[ConfortAttr.T_MAX.value] + comfort[ConfortAttr.DT.value]
            t_avg = (t_min + t_max) / 2

            min_dev = abs(temp_min - t_avg)
            max_dev = abs(temp_max - t_avg)
            score = weight * (1 / (1 + min_dev + max_dev))

            season_scores[season] += score * season_weight.get(season, 1.0)

    boost_factors = _season_boost_by_date()
    for season, boost in boost_factors.items():
        season_scores[season] += boost

    total_score = sum(season_scores.values())
    season_probabilities = {
        s: round((v / total_score) * 100, 1) if total_score else 0.0
        for s, v in season_scores.items()
    }

    selected_season = max(season_scores.items(), key=lambda x: x[1])[0]

    current_season: SeasonData = _season_by_date().copy()
    current_season.overridden = selected_season
    current_season.weather_anomaly = (selected_season != current_season.label)
    current_season.season_scores = season_scores
    current_season.season_probabilities = season_probabilities

    return current_season


def season_threshold( season_request: Seasons, hysteresis_mode="comfort") -> SeasonThreshold:
    """Precalcola i setpoint di temperatura, umidità e modalità per ciascuna stagione."""
    result: dict[Seasons, SeasonThreshold] = {}
    ideal_comfort_zones = CONFORT_ZONES

    for season, data in ideal_comfort_zones.items():
        cz_t_min = data[ConfortAttr.T_MIN.value] 
        cz_t_max = data[ConfortAttr.T_MAX.value]
        cz_dt = data[ConfortAttr.DT.value]
        cz_h_min = data[ConfortAttr.H_MIN.value]
        cz_h_max = data[ConfortAttr.H_MAX.value]
        cz_dh = data[ConfortAttr.DH.value]

        factor = HYSTERESIS_SEASON_MAP[season][hysteresis_mode]
        hysteresis_temp = round(cz_dt * factor, 1)
        hysteresis_hum = round(cz_dh * factor, 1)

        temp_avg = mean([cz_t_min, cz_t_max])
        hum_avg = mean([cz_h_min, cz_h_max])

        t_min = round(temp_avg + BIAS_TEMP_SEASON_MAP[season] - hysteresis_temp, 1)
        t_max = round(temp_avg + BIAS_TEMP_SEASON_MAP[season] + hysteresis_temp, 1)

        h_min = round(hum_avg + BIAS_HUMI_SEASON_MAP[season] - hysteresis_hum, 1)
        h_max = round(hum_avg + BIAS_HUMI_SEASON_MAP[season] + hysteresis_hum, 1)

        heating = None
        cooling = None
        dehumidifying = None

        # Logica base: solo per stagioni umide o se h_max supera comfort massimo
        if season in (Seasons.SUMMER, Seasons.AUTUMN, Seasons.SPRING): # and h_max >= data[ConfortAttr.H_MAX.value] - 2:
            dehumidifying = HvacThreshold(
                mode="dehumidifying",
                state_on=round(h_max - 1.0, 1),
                state_off=round(h_max - 4.0, 1)
            )

        match season:
            case Seasons.WINTER:
                heating = HvacThreshold(
                    mode=HEATING,
                    state_on=t_min,
                    state_off=t_max
                )
            case Seasons.SUMMER:
                cooling = HvacThreshold(
                    mode=COOLING,
                    state_on=round(cz_t_max - 1.0, 1),
                    state_off=round(cz_t_min + 0.5, 1)
                )
            case Seasons.SPRING:
                cooling = HvacThreshold(
                    mode=COOLING,
                    state_on=round(CONFORT_ZONES[Seasons.SUMMER][ConfortAttr.T_MIN.value] + 1.5, 1),
                    state_off=round(CONFORT_ZONES[Seasons.SUMMER][ConfortAttr.T_MIN.value] + 0.5, 1)
                )
                heating = HvacThreshold(
                    mode=HEATING,
                    state_on=round(CONFORT_ZONES[Seasons.WINTER][ConfortAttr.T_MIN.value] -
                                   (CONFORT_ZONES[Seasons.WINTER][ConfortAttr.DT.value] * 2), 1),
                    state_off=cz_t_min
                )
            case Seasons.AUTUMN:
                heating = HvacThreshold(
                    mode=HEATING,
                    state_on=round(cz_t_min - (cz_dt * 2), 1),
                    state_off=round(cz_t_min + 0.5, 1)
                )
                cooling = HvacThreshold(
                    mode=COOLING,
                    state_on=round(CONFORT_ZONES[Seasons.SUMMER][ConfortAttr.T_MIN.value] + 1.5, 1),
                    state_off=round(CONFORT_ZONES[Seasons.SUMMER][ConfortAttr.T_MIN.value] + 0.5, 1)
                )

        result[season] = SeasonThreshold(
            season,
            setpoint_temp_min=t_min,
            setpoint_temp_max=t_max,
            setpoint_humi_min=h_min,
            setpoint_humi_max=h_max,
            setpoint_dew_point=data.get(ConfortAttr.DP.value),
            threshold_heating=heating,
            threshold_cooling=cooling,
            threshold_dehumidifying=dehumidifying,
        )

    # Stampa di debug corretta
    if _LOGGER.isEnabledFor(logging.DEBUG):
        SEASON_RESULTS = "\n-------------------------------------------------------------------\n"
        SEASON_RESULTS += "seasons_threshold results:\n"

        for season, config in result.items():
            SEASON_RESULTS += f"{result[season]}"
            SEASON_RESULTS += "-------------------------------------------------------------------\n"

        # SEASON_RESULTS += "-------------------------------------------------------------------\n"
        _LOGGER.debug(SEASON_RESULTS)

    return result[season_request]

def seasons_setpoints( season_request: Seasons, hysteresis_mode="comfort") -> SeasonSetpoint:
    """Precalcola i setpoint di temperatura, umidità e modalità per ciascuna stagione."""
    result: dict[Seasons, SeasonSetpoint] = {}

    # Fattore moltiplicativo opzionale per modulare l’isteresi per stagione
    hysteresis_map = {
        Seasons.WINTER: {"comfort": 2.0, "eco": 2.5, "away": 3.0},
        Seasons.SUMMER: {"comfort": 2.0, "eco": 2.5, "away": 3.0},
        Seasons.SPRING: {"comfort": 2.2, "eco": 3.0, "away": 3.5},
        Seasons.AUTUMN: {"comfort": 2.2, "eco": 3.0, "away": 3.5},
    }

    for season, data in CONFORT_ZONES.items():
        cz_t_min = data[ConfortAttr.T_MIN.value]
        cz_t_max = data[ConfortAttr.T_MAX.value]
        cz_dt = data[ConfortAttr.DT.value]
        cz_h_min = data[ConfortAttr.H_MIN.value]
        cz_h_max = data[ConfortAttr.H_MAX.value]
        cz_dh = data[ConfortAttr.DH.value]

        factor = hysteresis_map[season][hysteresis_mode]
        hysteresis_temp = round(cz_dt * factor, 1)
        hysteresis_hum = round(cz_dh * factor, 1)

        temp_avg = mean([cz_t_min, cz_t_max])
        hum_avg = mean([cz_h_min, cz_h_max])

        t_min = round(temp_avg - hysteresis_temp, 1)
        t_max = round(temp_avg + hysteresis_temp, 1)
        h_min = round(hum_avg - hysteresis_hum, 1)
        h_max = round(hum_avg + hysteresis_hum, 1)

        heating = None
        cooling = None

        if season == Seasons.WINTER:
            heating = HvacThreshold(mode=HEATING, state_on=t_min, state_off=t_max)
        elif season == Seasons.SUMMER:
            cooling = HvacThreshold(mode=COOLING, state_on=round(cz_t_max - 1.0, 1), state_off=round(cz_t_min + 0.5, 1))
        elif season == Seasons.SPRING:
            cooling = HvacThreshold(mode=COOLING, state_on=round(CONFORT_ZONES[Seasons.SUMMER][ConfortAttr.T_MIN.value] + 1.5, 1),
                                    state_off=round(CONFORT_ZONES[Seasons.SUMMER][ConfortAttr.T_MIN.value] + 0.5, 1))
            heating = HvacThreshold(mode=HEATING, state_on=round(CONFORT_ZONES[Seasons.WINTER][ConfortAttr.T_MIN.value] 
                                                                 - (CONFORT_ZONES[Seasons.WINTER][ConfortAttr.DT.value] * 2), 1),
                                    state_off=cz_t_min)
        elif season == Seasons.AUTUMN:
            heating = HvacThreshold(mode=HEATING, state_on=round(cz_t_min - (cz_dt * 2), 1), state_off=round(cz_t_min + 0.5, 1))
            cooling = HvacThreshold(mode=COOLING, state_on=round(CONFORT_ZONES[Seasons.SUMMER][ConfortAttr.T_MIN.value] + 1.5, 1),
                                    state_off=round(CONFORT_ZONES[Seasons.SUMMER][ConfortAttr.T_MIN.value] + 0.5, 1))

        result[season] = SeasonSetpoint(
            temperature_min=t_min,
            temperature_max=t_max,
            humidity_min=h_min,
            humidity_max=h_max,
            dew_point=data.get(ConfortAttr.DP.value),
            heating=heating,
            cooling=cooling,
        )

    # Stampa di debug corretta
    if _LOGGER.isEnabledFor(logging.DEBUG):
        SEASON_RESULTS = "\n-------------------------------------------------------------------\n"
        SEASON_RESULTS += "seasons_setpoints results:\n"

        for season, config in result.items():
            SEASON_RESULTS += f"Season :: {season.value}\n"
            SEASON_RESULTS += f"  - Temp Comfort Zone :: {config.temperature_min}°C - {config.temperature_max}°C\n"
            SEASON_RESULTS += f"  - Humidity Zone     :: {config.humidity_min}% - {config.humidity_max}%\n"
            SEASON_RESULTS += f"  - Dew Point Target  :: {config.dew_point}°C\n"
            SEASON_RESULTS += "-------------------------------------------------------------------\n"

            # 🔥 Heating thresholds
            if config.heating:
                SEASON_RESULTS += f"    🔥 Heating:\n"
                SEASON_RESULTS += f"      ON  < {config.heating.state_on}°C\n"
                SEASON_RESULTS += f"      OFF > {config.heating.state_off}°C\n"

            # ❄️ Cooling thresholds
            if config.cooling:
                SEASON_RESULTS += f"    ❄️ Cooling:\n"
                SEASON_RESULTS += f"      ON  > {config.cooling.state_on}°C\n"
                SEASON_RESULTS += f"      OFF < {config.cooling.state_off}°C\n"

        SEASON_RESULTS += "-------------------------------------------------------------------\n"
        _LOGGER.debug(SEASON_RESULTS)

    return result[season_request]


def season_data_old(hass : HomeAssistant, weather_entity_it : str) -> dict | None:
    """ ... """
    # season_data2( hass, weather_entity_it)
    weather_forecasts = hass.async_create_task( async_weather_prediction( hass, weather_entity_it ) ).result()

    sorted_seasons = None
    selected_season = None
    season_scores: dict[Seasons, float] = {season: 0.0 for season in CONFORT_ZONES}
    current_season: dict[str, Any] = cast(dict[str, Any], copy.copy(_season_by_date()))

    # _LOGGER.debug("get_season_from_weather - current season %s", str(current_season))
    if weather_forecasts is not None:
        forecast = weather_forecasts.get(weather_entity_it).get("forecast")
        for i, day_forecast in enumerate(forecast):
            date_obj = datetime.fromisoformat(day_forecast['datetime'])
            # _LOGGER.debug("_weather_season - forecast %s", str(day_forecast))
            temp_min_day = day_forecast['templow']
            temp_max_day = day_forecast['temperature']
            weight = 1.0 - (i * 0.1)  # Dare più peso ai giorni vicini

            for season, conditions in CONFORT_ZONES.items():
                confort_temp_min = conditions[ConfortAttr.T_MIN.value] - conditions[ConfortAttr.DT.value]
                confort_temp_max = conditions[ConfortAttr.T_MAX.value] + conditions[ConfortAttr.DT.value]
                confort_temp_avg = (confort_temp_min + confort_temp_max) / 2

                # Calcola la deviazione della temperatura minima e massima dalla media della stagione
                min_deviation = abs(temp_min_day - confort_temp_avg)
                max_deviation = abs(temp_max_day - confort_temp_avg)

                # Valuta quanto la temperatura è vicina alla media della stagione
                score = weight * (1 / (1 + min_deviation + max_deviation))  # Inversamente proporzionale alla deviazione

                # Aggiungi il punteggio alla stagione corrispondente
                season_scores[season] += score

        sorted_seasons = sorted(season_scores.items(), key=lambda item: item[1], reverse=True)
        # Seleziona la stagione con il punteggio più alto
        # selected_season = max(season_scores, key=season_scores.get)
        selected_season = max(season_scores, key=lambda season: season_scores[season])
        
        # _LOGGER.debug("get_season_from_weather - selected season %s", str(selected_season))
        current_season['overridden'] = selected_season
        current_season['weather_anomaly'] = selected_season != current_season['label']

    else:
        current_season['overridden'] = current_season['label']
        current_season['weather_anomaly'] = False

    SEASON_RESULTS = f"""
    -------------------------------------------------------------------
    season_data results:
    Scores data     :: {str(sorted_seasons)}
    Weather season  :: {str(selected_season)}
    Season          :: {str(current_season)}
    -------------------------------------------------------------------
    """
    # _LOGGER.debug(SEASON_RESULTS)
    return current_season

def confort_zone( hass : HomeAssistant, weather_entity_it : str) -> dict[str, float] | None:
    """Get the confort zone for the current season."""
    confort_zone_result: dict[str, float] | None = None
    
    current_season = season_data(hass, weather_entity_it)
    if current_season and current_season.overridden in CONFORT_ZONES:
        confort_zone_result = CONFORT_ZONES[current_season.overridden]

    return confort_zone_result

def estimate_exchange_time(
    volume_m3: float,
    ventilation_m3h: float,
    temp_inside: float,
    temp_outside: float,
    temp_min_comfort: float,
    temp_max_comfort: float
) -> float:
    """
    Stima i minuti per cui si può ventilare senza uscire dalla confort zone.
    Restituisce il tempo minimo tra limite inferiore e superiore.
    """
    capacity_air = 1.2  # kJ/m³°C (aria)

    energy_per_degree = volume_m3 * capacity_air
    delta_temp = temp_outside - temp_inside  # ATTENZIONE! Cambiato il verso!

    if delta_temp == 0:
        return float('inf')  # Nessun problema se le temperature sono uguali

    # Energia persa o guadagnata per m³
    energy_exchange_per_m3 = capacity_air * abs(delta_temp)
    energy_exchange_per_hour = ventilation_m3h * energy_exchange_per_m3

    temperature_change_per_hour = energy_exchange_per_hour / energy_per_degree
    temperature_change_per_hour *= 0.5  # Correzione decadimento esponenziale

    if temperature_change_per_hour <= 0:
        return float('inf')

    # Calcola il tempo per raggiungere il minimo comfort
    if temp_inside > temp_outside:
        # Si sta raffreddando
        delta_to_limit = temp_inside - temp_min_comfort
    else:
        # Si sta scaldando
        delta_to_limit = temp_max_comfort - temp_inside

    if delta_to_limit <= 0:
        return 0  # Già fuori range

    hours = delta_to_limit / temperature_change_per_hour
    minutes = hours * 60

    return max(minutes, 0)

