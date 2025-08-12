

# from calendar import c
from collections import deque
import math
from statistics import mean
from typing import Any, Optional
from datetime import date, datetime, time as timedelta
import time

from audioop import bias
from homeassistant.core import HomeAssistant
from homeassistant.const import (
    # CONF_NAME,
    CONF_SENSORS,
    STATE_ON,
)

import logging

from .entities_state import PlatformEntityStates

from .utils.psychrometric import dew_point, relative_humidity

from .utils.influxv2 import InfluxClient

from .utils.helpers import convert_to_float, get_entity_state, is_entity_state

from .utils.const import (
    CONF_AREA,
    CONF_AREAS,
    CONF_HUMIDITY,
    CONF_INDOOR,
    CONF_MQ,
    CONF_RADIANT,
    CONF_TCOLLECTOR,
    CONF_TEMPERATURE,
    CONFORT_ZONES,
    COOLING,
    DOMAIN,
    DWP_NAME_POSTFIX,
    HEATING,
    HNX_NAME_POSTFIX,
    SEASONS_BY_DATE,
    SENSOR_CURRENT_DWP_UID,
    SENSOR_CURRENT_TEMP_UID,
    STATES,
    ConfortAttr,
    HvacThreshold,
    SeasonData,
    SeasonThreshold,
    Seasons,
)

_LOGGER = logging.getLogger(__name__)

class StateChangeLog:
    def __init__(self, maxlen=1000):
        self.log = deque(maxlen=maxlen)

    def record(self, entity_id: str, new_state, reason: str | None = None): 
        self.log.append({
            'timestamp': time.time(),
            'entity': entity_id,
            'state': new_state,
            'reason': reason,
        })

    def get_log(self):
        return list(self.log)
    
    def latest_for_entity(self, entity_id: str) -> dict | None:
        for entry in reversed(self.log):
            if entry['entity'] == entity_id:
                return entry
        return None

class SeasonThresholdStrategy:
    def __init__(
        self,
        # hass: HomeAssistant, 
        # shared_config: dict[str, Any],
    ):    
        """ """
        self._hysteresis_season_map = {
            Seasons.WINTER: {"comfort": 1.5, "eco": 2.5, "away": 3.0},
            Seasons.SUMMER: {"comfort": 1.5, "eco": 2.5, "away": 3.0},
            Seasons.SPRING: {"comfort": 1.7, "eco": 3.0, "away": 3.5},
            Seasons.AUTUMN: {"comfort": 1.7, "eco": 3.0, "away": 3.5},
        }

        self._temp_bias_season_map: dict[Seasons, float] = {}
        self._humi_bias_season_map: dict[Seasons, float] = {}

        self._season_threshold_strategy_data: dict[Seasons, SeasonThreshold] = {}

        self._influx_client = InfluxClient(
            organization='drp',
            bucket='drp',
            url='http://127.0.0.1:8086',
            token='lEjXciGoTt4E2G-KfFEIUVfSLDF4jzoZ-2_Ylr9jTvNq9p3kgnpHOfYOTdVtqi7L50A1XI8QuEa2APC0NmxeEg==' 
        )

    def season_threshold(self, season: Seasons) -> SeasonThreshold | None:
        """
        Restituisce le soglie stagionali calcolate per la stagione specificata.

        Args:
            season (Seasons): La stagione per cui si vogliono le soglie.

        Returns:
            SeasonThreshold | None: Le soglie stagionali o None se non sono state calcolate.
        """
        return self._season_threshold_strategy_data.get(season, None)
    
    async def _async_compute_bias(self, entity_id: str, temp: float, start_date: date, end_date: date) -> float| None:
        current_year = date.today().year
        year_diff = end_date.year - start_date.year

        start = start_date.replace(year=current_year)
        end = end_date.replace(year=current_year + year_diff if year_diff > 0 else current_year)

        # Imposta il tempo
        start_dt = datetime.combine(start, timedelta.min)               # 00:00:00
        end_dt = datetime.combine(end, timedelta(hour=23, minute=59, second=59))  # 23:59:59

        # Converte in ISO 8601 UTC
        start_iso = start_dt.isoformat() + "Z"
        end_iso = end_dt.isoformat() + "Z"

        bias = await self._influx_client.async_query_entity_bias(
            entity_id=entity_id,
            date_start=start_iso,
            date_stop=end_iso,
            ideal_temp=temp,
        )

        # Debug (facoltativo)
        # _LOGGER.debug(f"Season range: {start_iso} to {end_iso}, {entity_id} bias={bias}")

        return bias

    async def _async_initialize_bias_maps(self):
        """"""
        for season, (start_date, end_date) in SEASONS_BY_DATE.items():
            temp = ((CONFORT_ZONES[season][ConfortAttr.T_MIN.value] + CONFORT_ZONES[season][ConfortAttr.T_MAX.value]) / 2.0) + CONFORT_ZONES[season][ConfortAttr.DT.value]
           
            humi = ((CONFORT_ZONES[season][ConfortAttr.H_MIN.value] + CONFORT_ZONES[season][ConfortAttr.H_MAX.value]) / 2.0) + CONFORT_ZONES[season][ConfortAttr.DH.value]

            self._temp_bias_season_map[season] = await self._async_compute_bias('ambient_home_current_temperature', temp, start_date, end_date) or 0.0
            self._humi_bias_season_map[season] = await self._async_compute_bias('ambient_home_current_humidity', humi, start_date, end_date) or 0.0

        _LOGGER.debug(f"Season bias map: temp {self._temp_bias_season_map} humi {self._humi_bias_season_map}") 

    async def _async_compute_dynamic_confort_zone(
        self, 
        static_season_confort_zone,
        hysteresis_factor: float,
        temp_bias: float,
        humi_bias: float,
    ):
        cz_t_min = static_season_confort_zone[ConfortAttr.T_MIN.value] # temperatura minima
        cz_t_max = static_season_confort_zone[ConfortAttr.T_MAX.value] # temperatura massima
        cz_dt    = static_season_confort_zone[ConfortAttr.DT.value]    # delta temperatura
        cz_h_min = static_season_confort_zone[ConfortAttr.H_MIN.value] # umidità minima
        cz_h_max = static_season_confort_zone[ConfortAttr.H_MAX.value] # umidità massima
        cz_dh    = static_season_confort_zone[ConfortAttr.DH.value]    # delta umidità

        # Calcola le soglie (setpoint) dinamiche
        cz_temp_avg = mean([cz_t_min, cz_t_max])
        cz_hum_avg = mean([cz_h_min, cz_h_max])

        # _LOGGER.debug(
        #     "Computing dynamic zone – T_avg: %.1f, H_avg: %.1f, ΔT: %.1f, ΔH: %.1f, bias T: %.2f, H: %.2f",
        #     cz_temp_avg, cz_hum_avg, cz_dt, cz_dh, temp_bias, humi_bias
        # )

        hysteresis_delta_temp = cz_dt * hysteresis_factor
        hysteresis_delta_hum = cz_dh * hysteresis_factor

        dynamic_t_min = round(cz_temp_avg + temp_bias - hysteresis_delta_temp, 1)
        dynamic_t_max = round(cz_temp_avg + temp_bias + hysteresis_delta_temp, 1)

        dynamic_h_min = round(cz_hum_avg + humi_bias - hysteresis_delta_hum, 1)
        dynamic_h_max = round(cz_hum_avg + humi_bias + hysteresis_delta_hum, 1)

        dynamic_dew_point = dew_point( dynamic_t_max, dynamic_h_max ) # Calcola il punto di rugiada dinamico
        # static_season_confort_zone[ConfortAttr.DP.value] 

        if dynamic_dew_point < static_season_confort_zone[ConfortAttr.DP.value]:
            dynamic_dew_point = static_season_confort_zone[ConfortAttr.DP.value]
            dynamic_h_max = round( relative_humidity( dynamic_t_max, dynamic_dew_point), 1)

        return {
            'setpoint_temp_min': dynamic_t_min,
            'setpoint_temp_max': dynamic_t_max,
            'setpoint_humi_min': dynamic_h_min,
            'setpoint_humi_max': dynamic_h_max,
            'setpoint_dew_point': dynamic_dew_point,            
        }

    async def _async_compute_threshold(
        self,
        season: Seasons,
        setpoint_temp_min: float,
        setpoint_temp_max: float,
        setpoint_dew_point: float,
    ):

        heating = None
        cooling = None
        dehumidifying = None

        # Logica base: solo per stagioni umide o se h_max supera comfort massimo
        if season in (Seasons.SUMMER, Seasons.AUTUMN, Seasons.SPRING): # and h_max >= data[ConfortAttr.H_MAX.value] - 2:
            # Soglia di dew point sopra la quale c'è disagio percepito o rischio condensa
            dew_point_on = setpoint_dew_point + 0.0   # Es. 18.5 + 0.5 = 19.0 °C
            dew_point_off = setpoint_dew_point - 1.5  # Es. 18.5 - 1.0 = 17.5 °C

            dehumidifying = HvacThreshold(
                mode="dehumidifying",
                state_on=round(dew_point_on, 1),
                state_off=round(dew_point_off, 1)
            )

        match season:
            case Seasons.WINTER:
                heating = HvacThreshold(
                    mode=HEATING,
                    state_on=setpoint_temp_min,
                    state_off=setpoint_temp_max
                )
            case Seasons.SUMMER:
                cooling = HvacThreshold(
                    mode=COOLING,
                    state_on=round(setpoint_temp_max - 1.0, 1),
                    state_off=round(setpoint_temp_min + 0.5, 1)
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
                    state_off=setpoint_temp_min
                )
            case Seasons.AUTUMN:
                heating = HvacThreshold(
                    mode=HEATING,
                    state_on=round(setpoint_temp_min - (CONFORT_ZONES[Seasons.AUTUMN][ConfortAttr.DT.value] * 2), 1),
                    state_off=round(setpoint_temp_min + 0.5, 1)
                )
                cooling = HvacThreshold(
                    mode=COOLING,
                    state_on=round(CONFORT_ZONES[Seasons.SUMMER][ConfortAttr.T_MIN.value] + 1.5, 1),
                    state_off=round(CONFORT_ZONES[Seasons.SUMMER][ConfortAttr.T_MIN.value] + 0.5, 1)
                )

        return {
            'threshold_heating': heating,
            'threshold_cooling': cooling,
            'threshold_dehumidifying': dehumidifying,
        }

    async def async_compute_season_threshold(self, hysteresis_mode: str = "comfort") -> None:
        for season, (start_date, end_date) in SEASONS_BY_DATE.items():
            # Inizializza i bias stagionali se non sono già stati calcolati
            if season not in self._temp_bias_season_map or season not in self._humi_bias_season_map:
                await self._async_initialize_bias_maps()

            # Calcola i bias stagionali
            temp_bias = self._temp_bias_season_map.get(season, 0.0)
            humi_bias = self._humi_bias_season_map.get(season, 0.0)

            # Recupera la zona di comfort statica per la stagione
            static_season_confort_zone = CONFORT_ZONES[season]

            # Calcola le soglie dinamiche
            dynamic_confort_zone = await self._async_compute_dynamic_confort_zone(
                static_season_confort_zone,
                hysteresis_factor=self._hysteresis_season_map[season][hysteresis_mode],
                temp_bias=temp_bias,
                humi_bias=humi_bias,
            )

            # Calcola le soglie operative
            thresholds = await self._async_compute_threshold(
                season=season,
                setpoint_temp_min=dynamic_confort_zone['setpoint_temp_min'],
                setpoint_temp_max=dynamic_confort_zone['setpoint_temp_max'],
                setpoint_dew_point=dynamic_confort_zone['setpoint_dew_point'],
            )

            # Crea l'oggetto SeasonThreshold
            season_threshold = SeasonThreshold(
                season=season,
                setpoint_temp_min=dynamic_confort_zone['setpoint_temp_min'],
                setpoint_temp_max=dynamic_confort_zone['setpoint_temp_max'],
                setpoint_humi_min=dynamic_confort_zone['setpoint_humi_min'],
                setpoint_humi_max=dynamic_confort_zone['setpoint_humi_max'],
                setpoint_dew_point=dynamic_confort_zone['setpoint_dew_point'],
                threshold_heating=thresholds['threshold_heating'],
                threshold_cooling=thresholds['threshold_cooling'],
                threshold_dehumidifying=thresholds['threshold_dehumidifying'],
            )

            _LOGGER.debug(f"Season '{season}'\n{season_threshold}")

            self._season_threshold_strategy_data[season] = season_threshold

        # _LOGGER.debug(f"Temp bias '{self._temp_bias_season_map}'\nHumi bias {self._humi_bias_season_map}\nHysteresis {self._hysteresis_season_map}")

class ThermalCollectorValveStrategy:
    """
    Strategia di controllo per valvola termica in ambiente climatizzato.

    Questa classe implementa una logica di regolazione per valvole di raccolta termica 
    basata su soglie di temperatura e sul controllo del punto di rugiada per evitare 
    la formazione di condensa. Supporta le modalità di riscaldamento e raffrescamento 
    e mantiene uno storico dei cambiamenti di stato.

    Caratteristiche:
    - Logica ON/OFF con soglie separate di accensione/spegnimento.
    - Controllo anti-condensa basato sul punto di rugiada.
    - Tracciamento dei cambiamenti di stato tramite un registro circolare.
    - Supporto per aggiornamenti dinamici di soglie e condizioni ambientali.

    Attributi:
        _name (str): Identificativo della zona o stanza controllata.
        _valveThreshold (HvacThreshold): Soglie operative per la valvola.
        _setpoint_dew_point (float): Punto di rugiada massimo tollerabile.
        _ambient_temp (float): Temperatura corrente dell'ambiente.
        _ambient_dew_point (float): Punto di rugiada corrente.
        _current_state (bool): Stato attuale della valvola (aperta o chiusa).
        _state_change_log (StateChangeLog): Registro dei cambiamenti di stato.

    Metodi principali:
        step(): Esegue un ciclo di valutazione e aggiorna lo stato valvola.
        update_valve_state(): Aggiorna e registra lo stato della valvola.
        update_valvethreshold(): Aggiorna e registra le soglie operative.
        update_setpoint_dew_point(): Aggiorna il limite per il punto di rugiada.
        __str__(): Restituisce una rappresentazione leggibile dello stato corrente.
    """

    def __init__(
        self,
        name: str,
        valveThreshold: HvacThreshold,
        setpoint_dew_point: float,
        ambient_temp: float,
        ambient_dew_point: float,
        current_state: bool,
    ):
        """
        Inizializza la strategia di controllo valvola.

        Args:
            name (str): Nome logico della stanza o zona.
            valveThreshold (HvacThreshold): Oggetto contenente le soglie di controllo.
            setpoint_dew_point (float): Punto di rugiada limite per bloccare la valvola.
            ambient_temp (float): Temperatura iniziale dell'ambiente.
            ambient_dew_point (float): Punto di rugiada iniziale dell'ambiente.
            current_state (bool): Stato iniziale della valvola (True=aperta).
        """

        self._name = name
        self._valveThreshold = valveThreshold
        self._setpoint_dew_point = setpoint_dew_point

        self._ambient_temp = ambient_temp
        self._ambient_dew_point = ambient_dew_point
        self._current_state = current_state
        self._is_dehumidification_request = False

        self._state_change_log = StateChangeLog(100)

    def step(
        self,
        valveThreshold: HvacThreshold,
        setpoint_dew_point: float,
        ambient_temp: float,
        ambient_dew_point: float,
        current_state: bool,
    ) -> bool:
        """
        Esegue un ciclo di valutazione della strategia.

        Args:
            valveThreshold (HvacThreshold): Nuove soglie operative da applicare.
            setpoint_dew_point (float): Punto di rugiada limite per blocco valvola.
            ambient_temp (float): Temperatura ambiente corrente.
            ambient_dew_point (float): Punto di rugiada corrente dell’ambiente.
            current_state (bool): Stato corrente rilevato della valvola.

        Returns:
            bool: Nuovo stato della valvola dopo l'elaborazione.
        """

        prev_valveThreshold = self._valveThreshold
        prev_valve_state = self._current_state
        self.update_valve_state(current_state, "External update")
        self.update_valvethreshold(valveThreshold, "External update")
        self.update_setpoint_dew_point(setpoint_dew_point, "External update")

        self._ambient_temp = ambient_temp
        self._ambient_dew_point = ambient_dew_point
        self._is_dehumidification_request = False

        # Logica ON/OFF in base a temperatura
        match self._valveThreshold.mode:
            case "cooling":
                if self._current_state is True and self._valveThreshold.state_off is not None: # se la valvola è aperta, il fluido termico circola nel circuito
                    if ambient_temp < self._valveThreshold.state_off:
                        self._current_state = False
                        self._state_change_log.record(
                            entity_id=f"valve_state",
                            new_state=False
                        )
                elif self._valveThreshold.state_on is not None: # se la valvola è chiusa, il fluido termico non circola
                    if ambient_temp > self._valveThreshold.state_on:
                        self._current_state = True
                        self._state_change_log.record(
                            entity_id=f"valve_state",
                            new_state=True
                        )

                if self._current_state and self._ambient_dew_point >= self._setpoint_dew_point:
                        self._current_state = False
                        self._is_dehumidification_request = True
                        self._state_change_log.record(
                            entity_id=f"valve_state",
                            new_state=False,
                            reason="Dew point threshold exceeded"
                        )
            case "heating":
                if self._current_state and self._valveThreshold.state_off is not None:
                    if ambient_temp > self._valveThreshold.state_off:
                        self._current_state = False
                        self._state_change_log.record("valve_state", False)
                elif not self._current_state and self._valveThreshold.state_on is not None:
                    if ambient_temp < self._valveThreshold.state_on:
                        self._current_state = True
                        self._state_change_log.record("valve_state", True)

            case _:
                raise ValueError(f"Unsupported mode: {self._valveThreshold.mode}")

        return self._current_state

    def update_valve_state(
        self,
        current_state: bool,
        reason: str | None = None,
    ) -> None:
        """
        Aggiorna lo stato della valvola e registra l'evento se cambia.

        Args:
            current_state (bool): Nuovo stato della valvola.
            reason (str | None): Motivazione del cambiamento (opzionale).
        """

        if self._current_state != current_state:
            self._current_state = current_state
            self._state_change_log.record(
                entity_id=f"valve_state",
                new_state=current_state,
                reason=reason,
            )

    def update_setpoint_dew_point(
        self,
        setpoint_dew_point: float,
        reason: str | None = None,
    ) -> None:
        """
        Aggiorna il valore limite del punto di rugiada.

        Args:
            setpoint_dew_point (float): Nuovo valore di setpoint.
            reason (str | None): Motivazione del cambiamento (opzionale).
        """

        if self._setpoint_dew_point != setpoint_dew_point:
            self._setpoint_dew_point = setpoint_dew_point
            self._state_change_log.record(
                entity_id=f"setpoint_dew_point",
                new_state=setpoint_dew_point,
                reason=reason
            )
        
    def update_valvethreshold(
        self,
        valveThreshold: HvacThreshold,
        reason: str | None = None,
    ) -> None:
        """
        Aggiorna la soglia di attivazione/disattivazione valvola.

        Args:
            valveThreshold (HvacThreshold): Nuove soglie da applicare.
            reason (str | None): Motivazione del cambiamento (opzionale).
        """

        if self._valveThreshold != valveThreshold:
            self._valveThreshold = valveThreshold
            self._state_change_log.record(
                entity_id=f"valveThreshold",
                new_state=valveThreshold,
                reason=reason,
            )

    def __str__(self) -> str:
        """
        Restituisce una rappresentazione leggibile dello stato interno della classe.

        Returns:
            str: Riepilogo testuale dei principali parametri della strategia.
        """

        _STR = f"""
Class ThermalCollectorValveStrategy
  Room              :: {self._name}
  Mode              :: {self._valveThreshold.mode.upper() if self._valveThreshold.mode else 'N/A'}
  Ambient Temp      :: {self._ambient_temp:.2f} °C
  Ambient Dew Point :: {self._ambient_dew_point:.2f}% (limit: {self._setpoint_dew_point}%), request dehumidification: {self._is_dehumidification_request}
  Valve action      :: {'OPEN' if self._current_state else 'CLOSE'} (Closed, no thermal fluid flows)
  Temp Start/Stop   :: {self._valveThreshold.state_on} / {self._valveThreshold.state_off} °C
  Last valve change state :: {self._state_change_log.latest_for_entity('valve_state')}
-------------------------------------------------------------------
            """
        return _STR.strip()

class ThermalCollectorStrategy:
    """
    Gestore delle strategie di controllo per valvole di alimentazione termica radianti.

    Questa classe orchestra più istanze di `ThermalCollectorValveStrategy` per 
    applicare logiche di accensione/spegnimento delle valvole in base alla stagione, 
    alla temperatura percepita e al punto di rugiada.

    Attributi:
        _hass (HomeAssistant): Istanza di Home Assistant.
        _shared_config (dict): Configurazione condivisa contenente le aree da gestire.
        _ceiling_radiant_surface (float): Superficie totale dei pannelli radianti.
        _ceiling_radiant_surface_open (float): Superficie attualmente attiva.
        _valve_strategy_map (dict): Mappa delle strategie di controllo per ogni valvola.
    """

    def __init__(
        self,
        hass: HomeAssistant, 
        shared_config: dict[str, Any],
    ):
        """
        Inizializza l'oggetto `ThermalCollectorStrategy`.

        Args:
            hass (HomeAssistant): Oggetto principale di Home Assistant.
            shared_config (dict): Configurazione condivisa contenente le aree da monitorare.
        """

        self._hass: HomeAssistant = hass
        self._shared_config = shared_config

        self._ceiling_radiant_surface = 0.0
        self._ceiling_radiant_surface_open = 0.0
        self._ceiling_radiant_surface_percentage_open = 0.0
        self._valve_strategy_map: dict[str, ThermalCollectorValveStrategy] = {}

    @property
    def _global_entities(self) -> dict:
        """
        Restituisce il dizionario delle entità Home Assistant attive.

        Returns:
            dict: Mappa degli stati delle entità registrate in Home Assistant.
        """
        return self._hass.data[DOMAIN][STATES]
    
    @property
    def ceiling_radiant_surface_percentage_open(self) -> float:
        return self._ceiling_radiant_surface_percentage_open * 100.0

    def _damped_exponential_percentage(self, x: float, k: float = 5, weight: float = 0.7) -> float:
        """
        Calcola una percentuale smorzata con una curva esponenziale invertita, interpolata con una componente lineare.
        
        :param x: Valore di input normalizzato [0.0 - 1.0]
        :param k: Fattore di curvatura dell'esponenziale (maggiore = più curva)
        :param weight: Peso dell'esponenziale rispetto alla linearità [0.0 - 1.0]
        :return: Percentuale risultante [0.0 - 1.0]
        """
        x = max(0.0, min(1.0, x))  # Clamp tra 0 e 1

        # Esponenziale invertita normalizzata
        exp_component = (1 - math.exp(-k * x)) / (1 - math.exp(-k))
        # Interpolazione con componente lineare
        return weight * exp_component + (1 - weight) * x

    async def async_compute_strategy(
        self,
        season_threshold: SeasonThreshold
    ) -> None:
        """
        Calcola e applica la strategia climatica per ogni area configurata.

        Questa funzione:
        - Recupera temperatura percepita e punto di rugiada da entità Home Assistant.
        - Seleziona dinamicamente la soglia (riscaldamento/raffrescamento) più adatta.
        - Aggiorna o crea la strategia associata a ciascuna valvola.
        - Calcola la superficie radiante totale e attiva.

        Args:
            season_threshold (SeasonThreshold): Oggetto contenente le soglie stagionali.
        """
        _STR = "====================>\n"

        surface = 0.0
        surface_open = 0.0
        for area in self._shared_config[CONF_AREAS]:
            if not (area.get(CONF_INDOOR) and area.get(CONF_RADIANT)):
                continue

            valve_state = False
            room_name= area.get(CONF_AREA)
            surface += area.get(CONF_MQ)

            temp_felt_eid = (f"sensor.Ambient {area[CONF_AREA]} {HNX_NAME_POSTFIX}").lower().replace(" ", "_").replace("-", "_")
            dew_point_eid = (f"sensor.Ambient {area[CONF_AREA]} {DWP_NAME_POSTFIX}").lower().replace(" ", "_").replace("-", "_")
            valve_eid = area.get(CONF_TCOLLECTOR)

            temp_felt = convert_to_float(
                value=get_entity_state( self._global_entities, temp_felt_eid ),
                instance_name='_compute_climate_strategy',
                context=f"{room_name}, {temp_felt_eid}",
            )

            dew_point = convert_to_float(
                value=get_entity_state( self._global_entities, dew_point_eid ),
                instance_name='_compute_climate_strategy',
                context=f"{room_name}, {dew_point_eid}",
            )
            
            valve_state = is_entity_state( self._global_entities, valve_eid, STATE_ON) or False

            if temp_felt is None or dew_point is None or valve_state is None:
                continue

            climateThreshold = season_threshold.threshold_heating or season_threshold.threshold_cooling
            if season_threshold.threshold_heating is not None and season_threshold.threshold_cooling is not None:
                # Entrambi i threshold sono definiti: decidiamo in base alla temperatura percepita
                heating_threshold = season_threshold.threshold_heating
                cooling_threshold = season_threshold.threshold_cooling

                # Confrontiamo con i rispettivi setpoint "on" se presenti
                if heating_threshold.state_on is not None and cooling_threshold.state_on is not None:
                    # Usa quello più vicino alla temperatura percepita
                    delta_heating = abs(temp_felt - heating_threshold.state_on)
                    delta_cooling = abs(temp_felt - cooling_threshold.state_on)
                    climateThreshold = heating_threshold if delta_heating < delta_cooling else cooling_threshold
                elif heating_threshold.state_on is not None:
                    climateThreshold = heating_threshold
                elif cooling_threshold.state_on is not None:
                    climateThreshold = cooling_threshold

            if valve_eid in self._valve_strategy_map:
                valve_state = self._valve_strategy_map[valve_eid].step(
                    valveThreshold=climateThreshold, # type: ignore
                    setpoint_dew_point=season_threshold.setpoint_dew_point, # type: ignore
                    ambient_temp=temp_felt,
                    ambient_dew_point=dew_point, # type: ignore
                    current_state=valve_state,  # Inizialmente aperta
                )
            else:
                self._valve_strategy_map[valve_eid] = ThermalCollectorValveStrategy(
                    name=room_name,
                    valveThreshold=climateThreshold, # type: ignore
                    setpoint_dew_point=season_threshold.setpoint_dew_point, # type: ignore
                    ambient_temp=temp_felt,
                    ambient_dew_point=dew_point, # type: ignore
                    current_state=valve_state,  # Inizialmente aperta
                )

            if valve_state:
                surface_open += area.get(CONF_MQ)

            _STR += f"{self._valve_strategy_map[valve_eid]}\n"

        # _LOGGER.debug( f"{_STR}" )

        self._ceiling_radiant_surface = surface
        self._ceiling_radiant_surface_open = surface_open

        self._ceiling_radiant_surface_percentage_open = self._damped_exponential_percentage(
            self._ceiling_radiant_surface_open / self._ceiling_radiant_surface
        )

    def __str__(self) -> str:
        __STR = f"""Class ThermalCollectorStrategy
Radiat surface open {round(self._ceiling_radiant_surface_percentage_open*100)} % of {self._ceiling_radiant_surface:.2f} m²
"""
        for valve, strategy in self._valve_strategy_map.items():
            __STR += f"""{strategy}
"""

        return __STR.strip()

# class CoolingStrategy:

class ClimateStrategies:
    """Climate entity class."""

    def __init__(
        self,
        hass: HomeAssistant, 
        shared_config: dict[str, Any],
        platform_entity_states: PlatformEntityStates,
    ):
        self._hass: HomeAssistant = hass
        self._shared_config = shared_config

        self._state_change_log = StateChangeLog(100)

        self._thermal_collector_strategy = ThermalCollectorStrategy(hass, shared_config)
        self._season_threshold_strategy = SeasonThresholdStrategy()

        self._last_season: Seasons

        self._active_heating_threshold: Optional[HvacThreshold] = None
        self._active_cooling_threshold: Optional[HvacThreshold] = None
        self._active_dehumidifying_threshold: Optional[HvacThreshold] = None

    @property
    def _global_entities(self) -> dict:
        """Return the name of the device."""
        return self._hass.data[DOMAIN][STATES]

    def _evaluate_threshold(self, mode: str, thr: Optional[HvacThreshold], current_value: float) -> None:
        if not thr or thr.state_on is None or thr.state_off is None:
            if mode == "heating":            
                self._active_heating_threshold = None
                self._state_change_log.record(entity_id="heating_mode", new_state=False)
            elif mode == "cooling":
                self._active_cooling_threshold = None
                self._state_change_log.record(entity_id=f"cooling_mode", new_state=False)
            elif mode == "dehumidifying":
                self._active_dehumidifying_threshold = None
                self._state_change_log.record(entity_id=f"dehumidifying_mode", new_state=False)
            return

        if mode == "heating":
            if self._active_heating_threshold is not None:
                if current_value > thr.state_off:
                    self._active_heating_threshold = None
                    self._state_change_log.record("heating_mode", False)
            elif self._active_heating_threshold is None:
                if current_value < thr.state_on:
                    self._active_heating_threshold = thr
                    self._state_change_log.record("heating_mode", True)

        if mode == "cooling":
            if self._active_cooling_threshold is not None and thr.state_off is not None:
                if current_value < thr.state_off:
                    self._active_cooling_threshold = None
                    self._state_change_log.record(entity_id=f"cooling_mode", new_state=False)
            elif self._active_cooling_threshold is None:
                if current_value > thr.state_on:
                    self._active_cooling_threshold = thr
                    self._state_change_log.record(entity_id=f"cooling_mode", new_state=True)

        if mode == "dehumidifying":
            if self._active_dehumidifying_threshold is not None:
                if current_value < thr.state_off:
                    self._active_dehumidifying_threshold = None
                    self._state_change_log.record(entity_id=f"dehumidifying_mode", new_state=False)
            elif self._active_dehumidifying_threshold is None:
                if current_value > thr.state_on:
                    self._active_dehumidifying_threshold = thr
                    self._state_change_log.record(entity_id=f"dehumidifying_mode", new_state=True)

        _LOGGER.debug( f"""
Input threshold {thr}
Evaluate threshold for {mode} with current value {current_value} °C
{self._active_heating_threshold} current_value > thr.state_off {current_value > thr.state_off} current_value < thr.state_on {current_value < thr.state_on}
{self._active_cooling_threshold} current_value < thr.state_off {current_value < thr.state_off} current_value > thr.state_on {current_value > thr.state_on}
{self._active_dehumidifying_threshold} current_value < thr.state_off {current_value < thr.state_off} current_value > thr.state_on {current_value > thr.state_on}


""")

    # def climate_processing_mode(self, ambient_temp: float, ambient_dew_point: float) -> str:
    #     season_threshold = self._season_threshold_strategy.season_threshold(self._last_season)

    async def async_compute_climate_strategy(self, season: Seasons) -> None:
        # _LOGGER.debug("Compute climate strategy, season: %s ========================================", season)

        self._last_season = season

        await self._season_threshold_strategy.async_compute_season_threshold()

        if (threshold := self._season_threshold_strategy.season_threshold(season)):
            await self._thermal_collector_strategy.async_compute_strategy(threshold)

        if threshold and (current_temp := get_entity_state( self._global_entities, SENSOR_CURRENT_TEMP_UID )):
            self._evaluate_threshold('heating', threshold.threshold_heating, convert_to_float(value=current_temp) or 0.0)
            self._evaluate_threshold('cooling', threshold.threshold_cooling, convert_to_float(value=current_temp) or 0.0)

        if threshold and (current_temp := get_entity_state( self._global_entities, SENSOR_CURRENT_DWP_UID )):
            self._evaluate_threshold('dehumidifying', threshold.threshold_dehumidifying, convert_to_float(value=current_temp) or 0.0)

        _LOGGER.debug( self.__str__() )

        # influx_client = InfluxClient(
        #     organization='drp',
        #     bucket='drp',
        #     url='http://127.0.0.1:8086',
        #     token='lEjXciGoTt4E2G-KfFEIUVfSLDF4jzoZ-2_Ylr9jTvNq9p3kgnpHOfYOTdVtqi7L50A1XI8QuEa2APC0NmxeEg==' 
        # )

        # r= await influx_client.async_query_entity_bias(
        #     entity_id='ambient_home_current_temperature',
        #     date_start='2024-12-01T00:00:00Z',
        #     date_stop='2025-02-28T00:00:01Z',
        #     ideal_temp=25.5
        # )

        # _LOGGER.debug(f"Bias temperature: {r} °C")

    def __str__(self) -> str:
        __STR = f"""
==========v==========v========== Climate Strategies ==========v==========v==========
"""
        
        if self._last_season is not None:
            __STR += f"""
Last season: {self._last_season}
{self._season_threshold_strategy.season_threshold(self._last_season)}
{self._thermal_collector_strategy}
Processing strategies
  Heating       is {'ON' if self._active_heating_threshold else 'OFF'}
  Cooling       is {'ON' if self._active_cooling_threshold else 'OFF'}
  Dehumidifying is {'ON' if self._active_dehumidifying_threshold else 'OFF'}
"""
        
        __STR += f"""========== ========== ========== ========== ========== ========== ========== =========="""
        return __STR.strip()
