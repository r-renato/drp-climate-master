""" ... """
from typing import Any
from datetime import datetime
import math
import logging

from homeassistant.helpers.entity import Entity

from homeassistant.core import HomeAssistant
from homeassistant.components.climate.const import HVACMode
from homeassistant.const import (
    CONF_SENSORS,
    STATE_OFF,
    STATE_ON,
)
from simple_pid import PID
from ..utils.const import (
    # ATTR_DELTA_TEMPERATURE,
    # ATTR_TEMPERATURE,
    CONF_ACTUATOR,
    CONF_ADJUSTABLE_SUPPLY_UNIT,
    # CONF_ADJUSTABLE_TEMP_SYSTEM_SUPPLY,
    # CONF_ALARM, 
    # CONF_ALARMS,
    CONF_AREA,
    CONF_AREAS,
    CONF_AUTUMN,
    # CONF_BOILER_TEMP_SYSTEM_RETURN,
    CONF_BOILER_TEMP_SYSTEM_SUPPLY,
    # CONF_COMPRESSOR_MANAGEMENT,
    CONF_COOLING,
    CONF_COOLING_DT_SETPOINT,
    # CONF_COOLING_MANAGEMENT,
    CONF_COOLING_T_SETPOINT,
    # CONF_DEHUMIDIFICATION,
    # CONF_DELTA_DEW_POINT_SETPOINT, 
    # CONF_DEW_POINT,
    # CONF_DEW_POINT_SETPOINT,
    CONF_FM_POWER, 
    # CONF_FORCE_COOLING, 
    # CONF_FORCE_HEATING, 
    # CONF_H_AMBIENT, 
    # CONF_H_SETPOINT,
    CONF_HEATING,
    CONF_HEATING_DT_SETPOINT,
    CONF_HEATING_T_SETPOINT, 
    # CONF_HIGH_PRESSURE, 
    # CONF_HIGH_WATER_TEMP, 
    CONF_HOME_WINDOWS_STATE, 
    # CONF_LOW_WATER_TEMP,
    CONF_MODE,
    CONF_NOBODYSIN,
    CONF_PDC_TEMP_WATER_IN,
    CONF_PDC_TEMP_WATER_OUT, 
    CONF_POWER, 
    # CONF_POWER_ON_NIGHT, 
    # CONF_POWER_ON_TODAY,
    # CONF_REQUESTS,
    CONF_SCENARIOS, 
    # CONF_SEASON, 
    # CONF_SPARE_SETPOINT,
    CONF_SPRING,
    CONF_SUMMER,
    CONF_SUPPLY_UNITS, 
    # CONF_T_AMBIENT, 
    # CONF_T_OUTDOOR, 
    # CONF_T_SETPOINT, 
    # CONF_T_WATER,
    CONF_TEMPERATURE,
    CONF_TERRACE,
    CONF_VACATION,
    # CONF_VALUE, 
    # CONF_VENT_RECIRCULATION,
    # CONF_WATER,
    CONF_WINTER,
    DOMAIN,
    SENSOR_CURRENT_HNX_UID,
    STATES,
    # THERMAL_CONDUCTIVITY,
    # THICKNESS_INSULATION,
    TURN_OFF,
    TURN_ON,
    SeasonSetpoint,
    # VOLUME_BOILER,
    Temperature,
    TimeRange,
)
from ..utils.database import enquiry_entity_seconds_in_state
from ..utils.helpers import (
    async_number_set_value,
    async_switch_turn,
    convert_to_float,
    # filter_dict_by_key_prefix,
    get_entity_state,
    is_entity_state,
    # is_number,
    is_working_day,
    setup_entity_change
)

_LOGGER = logging.getLogger(__name__)

class DeviceSetPointController:
    """ ... """
    def __init__(self):
        # 🔥 PID persistente
        self._pid_controller = PID(1.2, 0.02, 0.1)
        self._pid_controller.output_limits = (7, 50)  # Clamp temperatura in °C

    def _estimate_boiler_heat_loss(self, T_boiler: float, T_ambient: float) -> float:
        volume_m3 = 25 / 1000
        height_m = 0.79
        radius_ext_m = 0.166
        thermal_conductivity = 0.022

        radius_int_m = math.sqrt(volume_m3 / (math.pi * height_m))
        insulation_thickness = radius_ext_m - radius_int_m

        side_area = 2 * math.pi * radius_ext_m * height_m
        bases_area = 2 * math.pi * (radius_ext_m ** 2)
        surface_area = side_area + bases_area

        U = thermal_conductivity / insulation_thickness
        delta_T = T_boiler - T_ambient
        Q_loss = abs(U * surface_area * delta_T)

        return round(Q_loss, 2)

    def device_setpoint(
        self,
        target_temp: float,
        target_temp_delta: float,
        outdoor_temp: float,
        boiler_sensor_temp: float
    ) -> Temperature:
        water_heat_capacity = 4180  # J/kg*K
        boiler_volume_liters = 25
        water_density = 1
        operational_margin = 2

        Q_loss = self._estimate_boiler_heat_loss(target_temp, outdoor_temp)
        diff_from_target = abs(boiler_sensor_temp - target_temp)

        min_cycle_time = 1 * 3600  # 1h
        max_cycle_time = 5 * 3600  # 5h

        # 📈 Introduzione dynamic_factor sul ciclo
        dynamic_factor = min(1.0, Q_loss / 100)  # Normalizzazione
        cycle_time = int(
            max_cycle_time - (max_cycle_time - min_cycle_time) * (1 - math.exp(-diff_from_target * dynamic_factor))
        )

        # 🔥 PID persistente
        self._pid_controller.setpoint = target_temp
        T_set = self._pid_controller(boiler_sensor_temp) or target_temp

        Delta_T_set = max(2, min(5, (Q_loss * cycle_time) / (water_heat_capacity * water_density * boiler_volume_liters)))

        # 🛠️ Recupero margini operativi
        if boiler_sensor_temp < (target_temp - target_temp_delta):
            T_set = target_temp + target_temp_delta + operational_margin
        elif boiler_sensor_temp > (target_temp + target_temp_delta):
            T_set = target_temp - operational_margin

        # 📋 Debug log
        results_msg = f"""
---------------------------------------------------------------
DeviceSetPointController::device_setpoint (Smart PID + Dynamic Cycle)
- Boiler              :: Sensor T={boiler_sensor_temp:.2f}°C, Target={target_temp:.2f}°C (Diff {diff_from_target:.2f}°C)
- Heat loss           :: {Q_loss:.2f} W
- Cycle Time          :: {cycle_time // 3600}h
- Setpoint Raw        :: T_set={T_set:.2f}°C, Delta_T_set={Delta_T_set:.2f}°C
- Setpoint Final      :: T={max(7, min(T_set, 50)):.2f}°C, Delta_T={max(2, min(Delta_T_set, 5)):.2f}°C
---------------------------------------------------------------
"""
        _LOGGER.debug(results_msg)

        return Temperature( round(max(7, min(T_set, 50))) ,round(max(2, min(Delta_T_set, 5))))



class AermecHDMI080(Entity):
    """Aermec HDMI080 device class."""

    def __init__(
            self, 
            hass: HomeAssistant, 
            device_config: dict[str, Any],
            shared_config: dict[str, Any]
    ) -> None:
        """Initialize the class"""
        self._hass = hass
        self._config = device_config
        self._shared_config = shared_config
        self._mode_config = self._config.get(CONF_MODE, {})
        self._sensors_config = self._config.get(CONF_SENSORS, {})
        self._heating_t_sp_config = self._config.get(CONF_HEATING_T_SETPOINT, {})
        self._heating_dt_sp_config = self._config.get(CONF_HEATING_DT_SETPOINT, {})
        self._cooling_t_sp_config = self._config.get(CONF_COOLING_T_SETPOINT, {})
        self._cooling_dt_sp_config = self._config.get(CONF_COOLING_DT_SETPOINT, {})

        entity_ids : list[str] = []
        self._device_setpoint_controller =  DeviceSetPointController()

        self._fm_power_id = self._config.get(CONF_FM_POWER, "")
        entity_ids.append(self._fm_power_id)
        self._device_power_id = self._config.get(CONF_POWER, "")
        entity_ids.append(self._device_power_id)

        entity_ids.append(self._heating_t_sp_config.get(CONF_ACTUATOR, ""))
        entity_ids.append(self._heating_dt_sp_config.get(CONF_ACTUATOR, ""))
        entity_ids.append(self._cooling_t_sp_config.get(CONF_ACTUATOR, ""))
        entity_ids.append(self._cooling_dt_sp_config.get(CONF_ACTUATOR, ""))

        self._actuator_id = self._mode_config.get(CONF_ACTUATOR)
        entity_ids.append(self._actuator_id)

        self._device_temp_water_in = self._sensors_config.get(CONF_PDC_TEMP_WATER_IN)
        entity_ids.append(self._device_temp_water_in)
        self._device_temp_water_out = self._sensors_config.get(CONF_PDC_TEMP_WATER_OUT)
        entity_ids.append(self._device_temp_water_out)

        setup_entity_change(self, self._async_entity_changed, entity_ids)

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
    
    @property
    def _terrace_temperature(self) -> float | None:
        terrace_area_config = {}
        for area_cfg in self._shared_config.get( CONF_AREAS, []):
            if area_cfg.get(CONF_AREA).lower() == CONF_TERRACE:
                terrace_area_config = area_cfg
                break 
        
        return convert_to_float( get_entity_state( self._global_entities, terrace_area_config.get(CONF_SENSORS, {}).get(CONF_TEMPERATURE, "") ) )

    @property
    def _boiler_system_supply_temperature(self) -> float | None:
        return convert_to_float( # Temperatura del boiler in °C
            get_entity_state( 
                self._global_entities, 
                self._shared_config.get(CONF_SUPPLY_UNITS, {}).get(CONF_SENSORS).get(CONF_BOILER_TEMP_SYSTEM_SUPPLY) 
            )
        )

    @property
    def _is_home_windows_closed(self) -> (bool | None):
        entity_state = is_entity_state( self._global_entities, self._shared_config.get(CONF_HOME_WINDOWS_STATE, ''), STATE_ON )
        return (False if entity_state is None else entity_state) 

    @property
    def _home_felt_temperature(self) -> (float | None):
        return convert_to_float( get_entity_state( self._global_entities, SENSOR_CURRENT_HNX_UID ) )

    @property
    def _is_vacation(self) -> (bool | None):
        entity_state = is_entity_state( self._global_entities, self._shared_config.get( CONF_SCENARIOS, {}).get( CONF_VACATION ), STATE_ON )
        return (False if entity_state is None else entity_state) 

    @property
    def _is_nobodysin(self) -> (bool | None):
        entity_state = is_entity_state( self._global_entities, self._shared_config.get( CONF_SCENARIOS, {}).get( CONF_NOBODYSIN ), STATE_ON )
        return (False if entity_state is None else entity_state) 

    @property
    def _is_device_fm_power_on(self) -> (bool | None): 
        entity_state = is_entity_state( self._global_entities, self._fm_power_id, STATE_ON )
        return (False if entity_state is None else entity_state)

    @property
    def _is_device_fm_power_off(self) -> (bool | None): 
        return not self._is_device_fm_power_on

    @property
    def _is_device_power_on(self) -> (bool | None):    
        entity_state = is_entity_state( self._global_entities, self._device_power_id, STATE_ON )
        return (False if entity_state is None else entity_state) 

    @property
    def _is_device_power_off(self) -> (bool | None):    
        return not self._is_device_power_on

    @property
    def _is_device_processing_heating(self) -> (bool | None):
        entity_state = is_entity_state( self._global_entities, self._mode_config.get(CONF_ACTUATOR), CONF_HEATING )
        return (False if entity_state is None else entity_state)

    @property
    def _is_device_processing_cooling(self) -> (bool | None):
        entity_state = is_entity_state( self._global_entities, self._mode_config.get(CONF_ACTUATOR), CONF_COOLING )
        return (False if entity_state is None else entity_state)

    @property
    def _heat_setpoint_temperature(self) -> (float | None):
        entity_state = get_entity_state( self._heating_t_sp_config.get(CONF_ACTUATOR) )
        return convert_to_float( value=entity_state, context="_heat_setpoint_temperature")

    @property
    def _heat_setpoint_delta_temperature(self) -> (float | None):
        entity_state = get_entity_state( self._heating_dt_sp_config.get(CONF_ACTUATOR) )
        return convert_to_float( value=entity_state, context="_heat_setpoint_delta_temperature")
    
    @property
    def _cool_setpoint_temperature(self) -> (float | None):
        entity_state = get_entity_state( self._cooling_t_sp_config.get(CONF_ACTUATOR) )
        return convert_to_float( value=entity_state, context="_cool_setpoint_temperature")

    @property
    def _cool_setpoint_delta_temperature(self) -> (float | None):
        entity_state = get_entity_state( self._cooling_dt_sp_config.get(CONF_ACTUATOR) )
        return convert_to_float( value=entity_state, context="_cool_setpoint_delta_temperature")

    @property
    def _defrost_required(self) -> (bool | None):
        return self._terrace_temperature <= 3.0 if self._terrace_temperature else False


    # def _is_device_setpoint_t(self, temperature) -> (bool | None):    
    #     entity_state = is_entity_state( self._global_entities, self._heating_t_sp_config.get(CONF_ACTUATOR), temperature )
    #     return (False if entity_state is None else entity_state) 

    # def _is_device_setpoint_delta_t(self, temperature) -> (bool | None):   
    #     entity_state = is_entity_state( self._global_entities, self._heating_dt_sp_config.get(CONF_ACTUATOR), temperature )
    #     return (False if entity_state is None else entity_state)  

    # def _is_device_mode(self, mode) -> (bool | None): 
    #     entity_state = is_entity_state( self._global_entities, self._mode_config.get(CONF_ACTUATOR), mode )
    #     return (False if entity_state is None else entity_state)

    # def _estimate_heat_loss_rate_old(self, T_boiler: float, delta_T_boiler: float, T_ambient: float) -> float:
    #     """
    #     Stima il tasso di perdita di calore dal boiler.

    #     :param T_b: Temperatura target del boiler (°C).
    #     :param Delta_T_b: Delta temperatura consentito nel boiler (°C).
    #     :param T_ambient: Temperatura dell'ambiente (°C).
    #     :param volume_boiler: Volume del boiler (litri).
    #     :param thickness_insulation: Spessore dell'isolamento del boiler (metri).
    #     :param thermal_conductivity: Conduttività termica del materiale isolante (W/m*K).
    #     :return: Tasso di perdita di calore (W).
    #     """
    #     # Costanti
    #     rho = 1  # Densità dell'acqua (kg/L)
    #     volume_m3 = VOLUME_BOILER / 1000  # Convertire il volume in m³
    #     C = 4.18  # Capacità termica specifica dell'acqua (kJ/kg*K)
        
    #     # Calcolo della superficie del boiler (assunto cilindrico verticale)
    #     height_boiler = (volume_m3 / (math.pi * (0.3**2)))  # Altezza stimata (raggio=0.3m)
    #     surface_area = 2 * math.pi * (0.3) * height_boiler + 2 * math.pi * (0.3**2)  # Area laterale + due basi
        
    #     # Coefficiente di trasmissione termica
    #     U = THERMAL_CONDUCTIVITY / THICKNESS_INSULATION  # W/m²K
        
    #     # Delta temperatura medio
    #     delta_T_avg = T_boiler - delta_T_boiler - T_ambient

    #     # Tasso di perdita di calore
    #     Q_loss = U * surface_area * delta_T_avg  # W
        
    #     return Q_loss

#     def _estimate_boiler_heat_loss(self, T_boiler: float, T_ambient: float) -> float:
#         """
#         Stima migliorata della perdita di calore di un boiler da 25 litri.
#         """
#         # Costanti
#         volume_m3 = 25 / 1000  # 0.025 m³
#         height_m = 0.79  # metri
#         radius_ext_m = 0.166  # metri
#         thermal_conductivity = 0.022  # W/m*K per poliuretano rigido

#         # Raggio interno calcolato dal volume
#         radius_int_m = math.sqrt(volume_m3 / (math.pi * height_m))
#         insulation_thickness = radius_ext_m - radius_int_m

#         # Superficie esterna (laterale + due basi)
#         side_area = 2 * math.pi * radius_ext_m * height_m
#         bases_area = 2 * math.pi * (radius_ext_m ** 2)
#         surface_area = side_area + bases_area

#         # Coefficiente di trasmissione termica U
#         U = thermal_conductivity / insulation_thickness  # W/m²K

#         # Delta temperatura
#         delta_T = T_boiler - T_ambient

#         # Perdita di calore (Watt)
#         Q_loss = abs(U * surface_area * delta_T) # uso di abs per avere un Q loss positivo anche in caso di raffrescamento

#         return round(Q_loss, 2)
    
#     def _calculate_device_t_setpoint(
#         self,
#         target_temp: float,       # T_b - Temperatura obiettivo
#         target_temp_delta: float, # Delta_T_b - Scostamento accettabile
#         outdoor_temp: float,      # Temperatura esterna (per stima dispersioni)
#         boiler_sensor_temp: float # Temperatura attuale letta nel boiler
#     ):
#         """
#         Calcola i setpoint di temperatura (T_set) e DeltaT (Delta_T_set), 
#         regolando dinamicamente il ciclo di intervento per efficienza energetica.

#         Logica:
#         - Regola `cycle_time` con funzione esponenziale per una risposta fluida.
#         - Usa un PID per ottimizzare `T_set`, evitando oscillazioni.
#         - `Delta_T_set` viene calcolato dinamicamente in base alla dispersione termica.
        
#         Ritorna:
#             dict con:
#             - ATTR_TEMPERATURE: T_set clampato tra 7 e 50 °C
#             - ATTR_DELTA_TEMPERATURE: Delta_T_set clampato tra 2 e 5 °C
#         """

#         # 🔹 Costanti fisiche
#         water_heat_capacity = 4.18 * 1000   # (J/kg*K) Capacità termica acqua
#         boiler_volume_liters = 80           # Volume boiler (L)
#         water_density = 1                   # Densità acqua (kg/L)
#         operational_margin = 2              # Margine operativo (°C)

#         # 🔥 Stima della perdita di calore
#         Q_loss = self._estimate_boiler_heat_loss(target_temp, outdoor_temp)

#         # 🔄 Differenza tra temperatura del boiler e il target
#         diff_from_target = abs(boiler_sensor_temp - target_temp)

#         # ⏳ Regolazione dinamica del ciclo di intervento (1h - 5h)
#         min_cycle_time = 1 * 3600  # 1 ora
#         max_cycle_time = 5 * 3600  # 5 ore

#         # 📉 Uso della funzione esponenziale per una variazione più fluida
#         cycle_time = int(
#             max_cycle_time - (max_cycle_time - min_cycle_time) * (1 - math.exp(-diff_from_target))
#         )

#         # 🎯 Controllo PID per regolare `T_set`
#         pid = PID(1.2, 0.02, 0.1, setpoint=target_temp)
#         pid.output_limits = (7, 50)  # Clamp del setpoint tra 7 e 50°C

#         # 🚀 Calcolo iniziale di `T_set` tramite PID
#         T_set = pid(boiler_sensor_temp) or target_temp

#         # 📏 Calcolo di Delta_T_set dinamico in base a `Q_loss`
#         Delta_T_set = max(2, min(5, (Q_loss * cycle_time) / (water_heat_capacity * water_density * boiler_volume_liters)))

#         # 🛠️ Correzioni se il boiler è fuori dal range target
#         if boiler_sensor_temp < (target_temp - target_temp_delta):
#             T_set = target_temp + target_temp_delta + operational_margin  # Aumenta per recuperare calore
#         elif boiler_sensor_temp > (target_temp + target_temp_delta):
#             T_set = target_temp - operational_margin  # Riduci per evitare sprechi

#         # 🏷️ Log di debug
#         results_msg = f"""
# ---------------------------------------------------------------
# _calculate_device_t_setpoint (Optimized with PID & Exp Cycle)
# - Boiler              :: System Supply Sensor T={boiler_sensor_temp:.2f}°C, Target T={target_temp:.2f}°C, Delta Target T={target_temp_delta:.2f}°C
#                          Target Diff T={diff_from_target:.2f}°C, Q_loss={Q_loss:.2f} W
# - Ciclo di intervento :: {cycle_time // 3600}h (Exp function), PID {pid.components}
# - Device              :: Raw Setpoint T={T_set:.2f}°C, Raw Delta Setpoint T={Delta_T_set:.2f}°C 
#                 final :: Setpoint T={max(7, min(T_set, 50.0)):.2f}°C, Delta Setpoint T={max(2, min(Delta_T_set, 5)):.2f}°C
# ---------------------------------------------------------------
#         """
#         _LOGGER.debug(results_msg)

#         # Restituzione dei valori clampati
#         return {
#             "ATTR_TEMPERATURE":       round( max( 7, min( T_set, 50 ) ) ),
#             "ATTR_DELTA_TEMPERATURE": round( max( 2, min( Delta_T_set, 5 ) ) )
#         }

    async def _async_device_controller(
            self, 
            fm_power: str | None = None,
            power: str | None = None,
            processing_mode: str | None = None, #CONF_HEATING or CONF_COOLING
            heat_setpoint_temp: float | None = None,
            heat_setpoint_delta_temp: float | None = None,
            cool_setpoint_temp: float | None = None,
            cool_setpoint_delta_temp: float | None = None,
    ) -> None:
        """hvac controller"""   

        if self._is_device_power_on:
            if any([
                processing_mode == CONF_HEATING and not self._is_device_processing_heating,
                processing_mode == CONF_COOLING and not self._is_device_processing_cooling,
            ]):
                await async_number_set_value( self, self._mode_config.get(CONF_ACTUATOR), self._mode_config.get( processing_mode ) )
                _LOGGER.info( "_async_device_controller - Set Device Processing Mode to %s", str(self._mode_config.get( processing_mode )) )                

            if self._heat_setpoint_temperature and heat_setpoint_temp and self._heat_setpoint_temperature != heat_setpoint_temp:
                await async_number_set_value( self, self._heating_t_sp_config.get(CONF_ACTUATOR), heat_setpoint_temp )
                _LOGGER.info( "_async_device_controller - Set device heat T to %s", str(heat_setpoint_temp))
                
            if self._heat_setpoint_delta_temperature and heat_setpoint_delta_temp and self._heat_setpoint_delta_temperature != heat_setpoint_delta_temp:
                await async_number_set_value( self, self._heating_dt_sp_config.get(CONF_ACTUATOR), heat_setpoint_delta_temp )
                _LOGGER.info( "_async_device_controller - Set device heat Delta T to %s", str(heat_setpoint_delta_temp))

            if self._cool_setpoint_temperature and cool_setpoint_temp and self._cool_setpoint_temperature != cool_setpoint_temp:
                await async_number_set_value( self, self._cooling_t_sp_config.get(CONF_ACTUATOR), cool_setpoint_temp )
                _LOGGER.info( "_async_device_controller - Set device cool T to %s", str(cool_setpoint_temp))
                
            if self._cool_setpoint_delta_temperature and cool_setpoint_delta_temp and self._cool_setpoint_delta_temperature != cool_setpoint_delta_temp:
                await async_number_set_value( self, self._cooling_dt_sp_config.get(CONF_ACTUATOR), cool_setpoint_delta_temp )
                _LOGGER.info( "_async_device_controller - Set device cool Delta T to %s", str(cool_setpoint_delta_temp))

            if self._is_device_power_on and power == TURN_OFF:
                await async_switch_turn( self, self._device_power_id, TURN_OFF )
                _LOGGER.info( "_async_device_controller - Device power OFF" )
            
            if self._is_device_fm_power_on and fm_power == TURN_OFF:
                await async_switch_turn( self, self._fm_power_id, TURN_OFF )
                _LOGGER.info( "_async_device_controller - Device fm power OFF" )

        else:
            if self._is_device_fm_power_off and fm_power == TURN_ON:
                await async_switch_turn( self, self._fm_power_id, TURN_ON )
                _LOGGER.info( "_async_device_controller - Device fm power ON" )
                return

            if self._is_device_power_off and power == TURN_ON:
                await async_switch_turn( self, self._device_power_id, TURN_ON )
                _LOGGER.info( "_async_device_controller - Device power ON" )

    # async def _async_device_operation_mode(
    #     self, 
    #     boiler_t_target : float,
    #     boiler_delta_t : float,
    #     mode : str,        
    # ):
    #     outdoor_t_sensor = self._terrace_temperature
    #     boiler_t_sensor = self._boiler_system_supply_temperature

    #     if outdoor_t_sensor is not None and boiler_t_sensor is not None: 
    #         device_setpoint = self._device_setpoint_controller.device_setpoint( boiler_t_target, boiler_delta_t, outdoor_t_sensor, boiler_t_sensor)
    #         # device_setpoint = self._calculate_device_t_setpoint( boiler_t_target, boiler_delta_t, outdoor_t_sensor, boiler_t_sensor)

    #         if self._is_device_power_on:
    #             if mode == CONF_HEATING:
    #                 # mode_state = is_entity_state( self._global_entities, self._mode_config.get(CONF_ACTUATOR), self._mode_config.get(CONF_HEATING) )
    #                 if not self._is_device_mode( self._mode_config.get(CONF_HEATING) ):
    #                     await async_number_set_value( self, self._mode_config.get(CONF_ACTUATOR), self._mode_config.get(CONF_HEATING) )
    #                     _LOGGER.info( "_async_device_operation_mode - Set Mode to %s", str(self._mode_config.get(CONF_HEATING)) )
    #             elif mode == CONF_COOLING:
    #                 # TBD
    #                 TBD = 1

    #             # heating_t_sp_state = is_entity_state( self._global_entities, self._heating_t_sp_config.get(CONF_ACTUATOR), device_setpoint.get(ATTR_TEMPERATURE) )
    #             if self._is_device_setpoint_t( device_setpoint.get(ATTR_TEMPERATURE) ):
    #                 await async_number_set_value( self, self._heating_t_sp_config.get(CONF_ACTUATOR), device_setpoint.get(ATTR_TEMPERATURE) )
    #                 _LOGGER.info( "_async_device_operation_mode - Set device T to %s, before %s", 
    #                     str(device_setpoint.get(ATTR_TEMPERATURE)),
    #                     get_entity_state( self._global_entities, self._heating_t_sp_config.get(CONF_ACTUATOR) )
    #                 )
                
    #             # heating_dt_sp_state = is_entity_state( self._global_entities, self._heating_dt_sp_config.get(CONF_ACTUATOR), device_setpoint.get(ATTR_DELTA_TEMPERATURE) )
    #             if self._is_device_setpoint_delta_t( device_setpoint.get(ATTR_DELTA_TEMPERATURE) ):
    #                 await async_number_set_value( self, self._heating_dt_sp_config.get(CONF_ACTUATOR), device_setpoint.get(ATTR_DELTA_TEMPERATURE) )
    #                 _LOGGER.info( "_async_device_operation_mode - Set device Delta T to %s, before %s", 
    #                     str(device_setpoint.get(ATTR_DELTA_TEMPERATURE)), 
    #                     get_entity_state( self._global_entities, self._heating_dt_sp_config.get(CONF_ACTUATOR) )
    #                 )

    # async def _async_device_fm_power_off(self) -> (bool | None):
    #     if self._is_device_fm_power_on:
    #         await async_switch_turn( self, self._fm_power_id, TURN_OFF )
    #         _LOGGER.info( "_async_device_fm_power_on - Device fm power OFF" )
    #         return True
    #     return False

    # async def _async_device_fm_power_on(self) -> (bool | None):
    #     if self._is_device_fm_power_off:
    #         await async_switch_turn( self, self._fm_power_id, TURN_ON )
    #         _LOGGER.info( "_async_device_fm_power_on - Device fm power ON" )
    #         return True
    #     return False

    # async def _async_device_power_off(self) -> (bool | None):
    #     if self._is_device_power_on:
    #         await async_switch_turn( self, self._device_power_id, TURN_OFF )
    #         _LOGGER.info( "_async_device_power_off - Device power OFF" )
    #         return True
    #     return False
     
    # async def _async_device_power_on(self) -> (bool | None):
    #     if self._is_device_power_off:
    #         await async_switch_turn( self, self._device_power_id, TURN_ON )
    #         _LOGGER.info( "_async_device_power_on - Device power ON" )
    #         return True
    #     return False

    async def async_hvac_control(
            self, 
            hvac_mode: str,
            # shared_config : dict,
            # device_data : dict,
            season : str,
            climate_setpoints : SeasonSetpoint,
    ) -> Any:
        """hvac controller"""    
        
        if hvac_mode == HVACMode.AUTO and self._global_entities is not None:
            if self._is_home_windows_closed:
                boiler_t_target = 40  # Temperatura media del boiler in °C
                boiler_delta_t = 4  # Delta temperatura del boiler in °C
                device_mode = CONF_HEATING

                if season == CONF_WINTER:
                    # boiler_t_target = 40  # Temperatura media del boiler in °C
                    # boiler_delta_t = 4  # Delta temperatura del boiler in °C
                    # device_mode = CONF_HEATING
                    await self._async_hvac_control_auto_for_winter( climate_setpoints )
                elif season == CONF_SPRING:
                    #TBD
                    # boiler_t_target = 15  # Temperatura media del boiler in °C
                    # boiler_delta_t = 5  # Delta temperatura del boiler in °C
                    # device_mode = CONF_HEATING
                    await self.async_hvac_control_auto_for_spring( climate_setpoints )
                elif season == CONF_SUMMER:
                    boiler_t_target = 40  # Temperatura media del boiler in °C
                    boiler_delta_t = 5  # Delta temperatura del boiler in °C
                    device_mode = CONF_HEATING
                elif season == CONF_AUTUMN:
                    boiler_t_target = 40  # Temperatura media del boiler in °C
                    boiler_delta_t = 5  # Delta temperatura del boiler in °C
                    device_mode = CONF_HEATING   

                # await self._async_device_operation_mode( boiler_t_target, boiler_delta_t, device_mode )
                # if self._terrace_temperature and self._boiler_system_supply_temperature:
                #     device_setpoint = self._device_setpoint_controller.device_setpoint(
                #         40.0, 4.0, # boiler temp, boiler delta temp
                #         self._terrace_temperature,  
                #         self._boiler_system_supply_temperature,
                #     )
            else:
                #TBD
                return False

#     def _device_power_request(
#         self, 
#         climate_setpoints : Any,
#     ) -> (bool | None):
#         now = datetime.now().time()

#         defrost_required = self._terrace_temperature <= 3.0 if self._terrace_temperature else False
#         conditioning_start_required = False
#         conditioning_stop_required = True
        
#         if self._home_felt_temperature is not None and climate_setpoints is not None:
#             conditioning_start_required = self._home_felt_temperature <= climate_setpoints.get(CONF_TEMPERATURE).get(STATE_ON)
#             conditioning_stop_required = self._home_felt_temperature >= climate_setpoints.get(CONF_TEMPERATURE).get(STATE_OFF)

#         if is_working_day( datetime.today() ):
#             if self._is_nobodysin:
#                 start_time = now.replace(hour=12, minute=30, second=0, microsecond=0)
#             else:
#                 start_time = now.replace(hour=9, minute=30, second=0, microsecond=0)
#         else:
#             start_time = now.replace(hour=7, minute=0, second=0, microsecond=0)

#         start_time_range = start_time <= now < now.replace(hour=20, minute=00, second=00, microsecond=0)
#         workingTimeRange = start_time <= now < now.replace(hour=23, minute=59, second=59, microsecond=0)
#         # _LOGGER.debug( "async_hvac_control_auto_for_winter - Working Time Range %s, %s %s", str(workingTimeRange), str(start_time), str(end_time) )

#         # _LOGGER.debug( "async_hvac_control_auto_for_winter %s %s", shared_config.get(CONF_SUPPLY_UNITS), shared_config.get(CONF_SUPPLY_UNITS).get(CONF_ADJUSTABLE_TEMP_SYSTEM_SUPPLY) )

#         enquiry_result = enquiry_entity_seconds_in_state( 
#                 self._shared_config.get(CONF_SUPPLY_UNITS, {}).get(CONF_ADJUSTABLE_SUPPLY_UNIT), STATE_OFF 
#         )
#         adjustable_supply_unit_poweroff_minutes = round(enquiry_result['updated'] / 60 ) if enquiry_result else -1

#         is_adjustable_supply_unit_off = adjustable_supply_unit_poweroff_minutes > 5 and adjustable_supply_unit_poweroff_minutes < 15

#         RESULTS = f"""
# -------------------------------------------------------------------
# _device_power_request >
# device power is on            :: {self._is_device_power_on}
# device power on in time range :: {start_time_range} - start: {str(start_time)} now: {str(now)} end: {str(now.replace(hour=20, minute=00, second=00, microsecond=0))}
# device working in time range  :: {workingTimeRange} - start: {str(start_time)} now: {str(now)} end: {str(now.replace(hour=23, minute=59, second=59, microsecond=0))}
# device power on required      :: {conditioning_start_required} - Felt: {str(self._home_felt_temperature)}° <= setpoint: {climate_setpoints.get(CONF_TEMPERATURE).get(STATE_ON)}°
# device power off required     :: {conditioning_stop_required} - Felt: {str(self._home_felt_temperature)}° >= setpoint: {climate_setpoints.get(CONF_TEMPERATURE).get(STATE_OFF)}°
# device defrost required       :: {defrost_required} - Terrace T: {self._terrace_temperature}°
# device power off minutes      :: {adjustable_supply_unit_poweroff_minutes} {is_adjustable_supply_unit_off}
# -------------------------------------------------------------------
#             """
#         _LOGGER.debug(RESULTS)

#         if self._is_vacation:
#             return False
        
#         if defrost_required:
#             return True
        
#         if self._is_device_power_off and start_time_range and conditioning_start_required:
#             return True
        
#         if workingTimeRange:
#             if conditioning_stop_required:
#                 return False
            
#             if self._is_device_power_on:
#                 return True

#         return False

    async def async_hvac_control_auto_for_spring(
            self, 
            # shared_config : dict,
            # device_data : dict,
            climate_setpoints : SeasonSetpoint,
    ) -> None:
        """hvac spring controller"""

    async def _async_hvac_control_auto_for_winter(
            self, 
            climate_setpoints: SeasonSetpoint,
    ) -> None:
        """hvac winter controller"""
        device_power_requested: bool = False 

        if is_working_day(datetime.today()):
            if self._is_nobodysin:
                start_time_range = TimeRange.from_hours(12,30, 20,00)
                working_time_range = TimeRange.from_hours(12,30, 23,59)
            else:
                start_time_range = TimeRange.from_hours(9,30, 20,00)
                working_time_range = TimeRange.from_hours(9,30, 23,59)        
        else:
            start_time_range = TimeRange.from_hours(7,00, 20,00)
            working_time_range = TimeRange.from_hours(7,00, 23,59)                 

        enquiry_result = enquiry_entity_seconds_in_state( 
                self._shared_config.get(CONF_SUPPLY_UNITS, {}).get(CONF_ADJUSTABLE_SUPPLY_UNIT), STATE_OFF 
        )
        if enquiry_result:
            adjustable_supply_unit_poweroff_minutes = round(enquiry_result['updated'] / 60 )
            is_adjustable_supply_unit_off = adjustable_supply_unit_poweroff_minutes > 5 and adjustable_supply_unit_poweroff_minutes < 15
        else:
            adjustable_supply_unit_poweroff_minutes = None
            is_adjustable_supply_unit_off = None

        if self._is_vacation:
            device_power_requested = False
        
        if self._defrost_required:
            device_power_requested = True

        if (
            climate_setpoints.heating
            and self._home_felt_temperature
            ) \
                and self._is_device_power_off \
                and start_time_range.is_now_inside() \
                and self._home_felt_temperature <= (climate_setpoints.heating.state_on or -99.0):
            device_power_requested = True
        
        if working_time_range.is_now_inside():
            if (
                climate_setpoints.heating
                and self._home_felt_temperature
                ) \
                    and self._home_felt_temperature >= (climate_setpoints.heating.state_off or 99.0):
                device_power_requested = False
            
        if not device_power_requested:
            await self._async_device_controller( power=TURN_OFF )
        else:
            if self._terrace_temperature and self._boiler_system_supply_temperature:
                device_setpoint = self._device_setpoint_controller.device_setpoint(
                    40.0, 4.0, # boiler temp, boiler delta temp
                    self._terrace_temperature,  
                    self._boiler_system_supply_temperature,
                )
                await self._async_device_controller(
                    fm_power=TURN_ON,
                    power=TURN_ON, 
                    processing_mode=CONF_HEATING,
                    heat_setpoint_temp=device_setpoint.value, 
                    heat_setpoint_delta_temp=device_setpoint.delta
                )

        RESULTS = f"""
-------------------------------------------------------------------
_async_hvac_control_auto_for_winter >
season                        :: Winter
device power                  :: FM is {"ON" if self._is_device_fm_power_on else "OFF"} Device is {"ON" if self._is_device_power_on else "OFF"}
device start time range       :: {start_time_range}
device working time range     :: {working_time_range}
device power on required      :: Felt: {str(self._home_felt_temperature)}° <= setpoint: {climate_setpoints.heating.state_on if climate_setpoints.heating else -99.0}°
device power off required     :: Felt: {str(self._home_felt_temperature)}° >= setpoint: {climate_setpoints.heating.state_off if climate_setpoints.heating else 99.0}°
device defrost required       :: {self._defrost_required} - Terrace T: {self._terrace_temperature}°
device power off minutes      :: {adjustable_supply_unit_poweroff_minutes} {is_adjustable_supply_unit_off}
-------------------------------------------------------------------
            """
        _LOGGER.debug(RESULTS)


#     async def async_hvac_control_auto_for_winter__old(
#             self, 
#             # shared_config : dict,
#             # device_data : dict,
#             climate_setpoints : Any,
#     ) -> Any:
#         """hvac winter controller"""
#         now = datetime.now().time()

#         t_felt = self._home_felt_temperature
#         is_vacation = self._is_vacation
#         is_nobodysin = self._is_nobodysin
#         conditioning_start_required = False
#         conditioning_end_required = False

#         # t_outdoor = self._terrace_temperature
#         defrost_required = self._terrace_temperature <= 3.0 if self._terrace_temperature else False

#         if t_felt is not None and climate_setpoints is not None:
#             conditioning_start_required = t_felt <= climate_setpoints.get(CONF_TEMPERATURE).get(STATE_ON)
#             conditioning_end_required = t_felt >= climate_setpoints.get(CONF_TEMPERATURE).get(STATE_OFF)

#         if is_working_day(datetime.today()):
#             if is_nobodysin:
#                 start_time = now.replace(hour=12, minute=30, second=0, microsecond=0)
#             else:
#                 start_time = now.replace(hour=9, minute=30, second=0, microsecond=0)
#         else:
#             start_time = now.replace(hour=7, minute=0, second=0, microsecond=0)

#         start_time_range = start_time <= now < now.replace(hour=20, minute=00, second=00, microsecond=0)
#         workingTimeRange = start_time <= now < now.replace(hour=23, minute=59, second=59, microsecond=0)
#         # _LOGGER.debug( "async_hvac_control_auto_for_winter - Working Time Range %s, %s %s", str(workingTimeRange), str(start_time), str(end_time) )

#         # _LOGGER.debug( "async_hvac_control_auto_for_winter %s %s", shared_config.get(CONF_SUPPLY_UNITS), shared_config.get(CONF_SUPPLY_UNITS).get(CONF_ADJUSTABLE_TEMP_SYSTEM_SUPPLY) )

#         enquiry_result = enquiry_entity_seconds_in_state( 
#              self._shared_config.get(CONF_SUPPLY_UNITS, {}).get(CONF_ADJUSTABLE_SUPPLY_UNIT), STATE_OFF 
#         )
#         adjustable_supply_unit_poweroff_minutes = round( enquiry_result['updated'] / 60) if enquiry_result else -1

#         is_adjustable_supply_unit_off = adjustable_supply_unit_poweroff_minutes > 5 and adjustable_supply_unit_poweroff_minutes < 15
        
# #         RESULTS = f"""
# # -------------------------------------------------------------------
# # async_hvac_control_auto_for_winter results:
# # start time range   :: {start_time_range} {str(start_time)} {str(now)} {str(now.replace(hour=20, minute=00, second=00, microsecond=0))}
# # working time range :: {workingTimeRange} {str(start_time)} {str(now)} {str(now.replace(hour=23, minute=59, second=59, microsecond=0))}
# # start required     :: {conditioning_start_required} {str(t_felt)}° <= {climate_setpoints.get(CONF_TEMPERATURE).get(STATE_ON)}°
# # stop required      :: {conditioning_end_required} {str(t_felt)}° >= {climate_setpoints.get(CONF_TEMPERATURE).get(STATE_OFF)}°
# # defrost required   :: {defrost_required} {t_outdoor}°
# # {adjustable_supply_unit_poweroff_minutes} {is_adjustable_supply_unit_off}
# # -------------------------------------------------------------------
# #             """
# #         _LOGGER.debug(RESULTS)

#         device_power_request = self._device_power_request( climate_setpoints )
#         # _LOGGER.debug( "async_hvac_control_auto_for_winter - Device power request: %s", str(device_power_request) )
# # ########## # ########## # ########## # ########## #
# # Disattiva la pompa di calore
# # 1. Power OFF
# # ########## # ########## # ########## # ########## #
#         # if (not defrost_required) and (is_vacation or not workingTimeRange or conditioning_end_required):
#         #     await self._async_device_power_off()
#         #     return False  
        
#         if device_power_request is False:
#             if await self._async_device_power_off():
#                 return True
        
# # ########## # ########## # ########## # ########## #
# # Attiva la pompa di calore solo se la temperatura percepita è inferiore alla temperatura impostata
# # 1. Power ON
# # ########## # ########## # ########## # ########## #
#         elif device_power_request is True:
#             if await self._async_device_fm_power_on():
#                 return True
#             if await self._async_device_power_on():
#                 return True           
#             return True

#         # if (start_time_range and conditioning_start_required) or defrost_required:
#         #     # fm_device_power_off_state = is_entity_state( self._global_entities, self._fm_power_id, STATE_OFF )
#         #     if await self._async_device_fm_power_on():
#         #         return True
#         #     # if self._is_device_fm_power_off:
#         #     #     await async_switch_turn( self, self._fm_power_id, TURN_ON )
#         #     #     _LOGGER.info( "async_hvac_control_auto_for_winter - Device fm power ON" )
#         #     #     return True

#         #     # device_power_off_state = is_entity_state( self._global_entities, self._device_power_id, STATE_OFF )
#         #     if await self._async_device_power_on():
#         #         return True
#         #     # if self._is_device_power_off:
#         #     #     await async_switch_turn( self, self._device_power_id, TURN_ON )
#         #     #     _LOGGER.info( "async_hvac_control_auto_for_winter - Device power ON" )
#         #     #     return True

#         #     return True

