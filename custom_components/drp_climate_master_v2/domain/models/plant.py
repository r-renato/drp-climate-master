from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional
import logging

from ...domain.models.runtime_schema import SensorPair

from ...helpers.utils import pad

from .season import SeasonState

_LOGGER = logging.getLogger(__name__)

@dataclass(slots=True)
class ZoneSnapshot:
    """
    Istantanea dei **sensori** e dello **stato impianto** per una singola *zona/stanza*
    all’istante `ts`. Funziona da input coerente per stimatore di stato (es. Kalman),
    generatore di target di comfort (adaptive) e controllore (PID/MPC-lite), oltre che
    per i guardiani di sicurezza (dew-point).

    Unità e convenzioni
    -------------------
    • Temperature: **°C** — Umidità relativa: **%** — Attuazioni on/off: **bool**  
    • `ts` deve essere **timezone-aware** (consigliato **UTC**).

    Note di validazione (da eseguire a monte)
    -----------------------------------------
    • T interna ~[-10, 50] °C; T esterna ~[-30, 60] °C; RH in [0, 100] %.  
    • Rifiutare snapshot obsoleti (es. `now - ts > 2*step`).  
    • Se campi opzionali mancano (es. `flow_t`, `return_t`, `out_rh`), degradare le funzioni:
      DP-guard più conservativo, costo energetico stimato, ecc.

    Attributi
    ---------
    timestamp : datetime
        Timestamp dell’istantanea (timezone-aware). Usato per allineare snapshot, forecast
        e scheduling del ciclo di controllo (rilevazione di dati stantii).

    room_t : float
        Temperatura aria **nella stanza** (variabile controllata principale / uscita del modello).

    room_rh : float
        Umidità relativa **nella stanza**. Utile per strategie estive/VMC e, in mancanza di `room_dp`,
        per calcolare il dew point.

    room_dp : float
        **Dew point** in stanza. Vincolo di sicurezza per raffrescamento radiante:
        richiede `flow_t >= room_dp + safety` (tip. 1.5–2.0 °C).

    room_hi : float
        **Heat Index** percepito (da T+RH). Può introdurre bias al target estivo o guidare
        l’aumento della ventilazione quando il caldo è opprimente.

    mean_apt_t : Optional[float]
        Temperatura **media appartamento** (aggregata su più stanze). Proxy della massa termica
        e degli accoppiamenti inter-zona; stabilizza stime e target.

    mean_apt_rh : Optional[float]
        Umidità relativa **media appartamento**. Utile per strategie VMC/globali.

    out_t : float
        Temperatura **esterna** istantanea. Disturbo principale del modello e base per il
        **comfort adattivo** (running mean esterna).

    out_rh : Optional[float]
        Umidità relativa **esterna**. Utile per stimare dew point esterno e valutare free-cooling/deumidifica.

    flow_t : Optional[float]
        Temperatura **mandata** del circuito di zona/collettore. Necessaria per Dew-Point Guard e,
        assieme a `return_t`, per stimare la potenza termica (ΔT idronico).

    return_t : Optional[float]
        Temperatura **ritorno** del circuito di zona. Con `flow_t` fornisce ΔT idronico (proxy potenza/efficienza).

    act_state : Optional[bool]
        **Ultimo comando** on/off applicato all’attuatore della zona. Utile per predizione di stato,
        **rate limiting** e vincoli di variazione (|Δu|).
    """

    timestamp: datetime
    name: str

    sensors: Optional[SensorPair] = None

    flow_t: Optional[float] = None
    return_t: Optional[float] = None

    act_state: Optional[bool] | None = None  # ultimo comando all’attuatore (on/off)


@dataclass(slots=True)
class PDCSnapshot:
    """
    Istantanea degli **stati** e delle **temperature** della Pompa di Calore (PDC)
    e del circuito tecnico associato. È usata da orchestratore (mode/mix), safety
    (min-on/min-off, controlli su ΔT) e controllore (PID/MPC-lite) per vincoli e costi.

    Unità e convenzioni
    -------------------
    • Temperature: **°C** — Durate: **minuti** — Flag/Mode: boolean/int vendor-specific  
    • `timestamp` deve essere **timezone-aware** (consigliato **UTC**).  
    • **WOT** = *Water Outlet Temperature* (setpoint di mandata lato generatore).

    Attributi
    ---------
    timestamp : datetime
        Istante di rilevazione dello snapshot (timezone-aware).

    fm_power_on : bool
        Stato ON/OFF del modulo di movimentazione fluido lato PDC (es. **Flow Manager** /
        circolatore principale / fan module). True ⇒ circolazione attiva.

    power_on : Optional[bool]
        Stato ON/OFF della **PDC** (compressore/centralina in marcia). Può essere `None`
        se il dato non è disponibile.

    device_mode : Optional[int]
        Modalità operativa **grezza** (codice numerico *vendor-specific*: es. 0=standby, 1=heating,
        2=cooling, 3=dhw, 4=defrost). Va normalizzata a valle.

    wot_heat : Optional[float]
        Setpoint **WOT** in **riscaldamento** (spesso da curva climatica + offset).

    delta_t_heat : Optional[float]
        Offset/Delta applicato al setpoint WOT in riscaldamento (boost/eco; >0 ⇒ alza mandata obiettivo).

    wot_cool : Optional[float]
        Setpoint **WOT** in **raffrescamento** (temperatura acqua fredda desiderata).

    delta_t_cool : Optional[float]
        Offset/Delta applicato al setpoint WOT in raffrescamento (più basso ⇒ acqua più fredda).

    t_water_in_pe : Optional[float]
        Temperatura **ingresso** acqua lato scambiatore/plate della PDC (lato impianto).

    t_water_out_pe : Optional[float]
        Temperatura **uscita** acqua lato scambiatore/plate della PDC (lato impianto). Con `t_water_in_pe`
        consente di stimare **ΔT PDC** (= out_pe − in_pe), proxy dello scambio sul generatore.

    boiler_supply_temp : Optional[float]
        **Mandata** circuito tecnico/distribuzione (post PDC/miscelazione). Verifica coerenza con domanda utenze.

    boiler_return_temp : Optional[float]
        **Ritorno** circuito tecnico/distribuzione. Con la mandata fornisce **ΔT idronico** della rete (proxy carico).

    minutes_power_on : Optional[float]
        Minuti consecutivi in **stato acceso** (PDC/gruppo). Usato per policy **min-on time** e per diagnosi cicli brevi.

    minutes_power_off : Optional[float]
        Minuti consecutivi in **stato spento**. Usato per policy **min-off time** e anti-short-cycling.
    """

    timestamp: datetime
    fm_power_on: bool
    power_on: Optional[bool] = None

    device_mode: Optional[int] = None

    wot_heat: Optional[float] = None
    delta_t_heat: Optional[float] = None

    wot_cool: Optional[float] = None
    delta_t_cool: Optional[float] = None

    sensor_t_water_in_pe: Optional[float] = None
    sensor_t_water_out_pe: Optional[float] = None

    # boiler_supply_temp: Optional[float]
    # boiler_return_temp: Optional[float]

    minutes_power_on: Optional[float] = None
    minutes_power_off: Optional[float] = None


@dataclass(slots=True)
class VMCSnapshot:
    """
    Istantanea della **Ventilazione Meccanica Controllata (VMC)** / UTA domestica.
    Fornisce stato, richieste termiche e setpoint utili a orchestratore/safety/MPC.

    Unità e convenzioni
    -------------------
    • Temperature: **°C** — Umidità: **%** — Flag: **bool** — Impostazioni vendor: **int/str**  
    • I campi non disponibili possono restare `None`; le logiche degradano in sicurezza.

    Attributi (principali)
    ----------------------
    timestamp : datetime
        Istante di rilevazione (timezone-aware).

    power_on : Optional[bool]
        Stato ON/OFF VMC/UTA.

    t_setpoint : Optional[float]
        Setpoint di temperatura di mandata aria (se supportato).

    rh_setpoint : Optional[float]
        Setpoint di umidità (se supportato).

    t_dew_point_setpoint : Optional[float]
        Setpoint di punto di rugiada per controllo anti-condensa sull’aria trattata.

    delta_t_dew_point_setpoint : Optional[float]
        Offset dinamico al setpoint di dew point (safety margin o correzioni temporanee).

    spare_setpoint : Optional[int]
        Setpoint ausiliario (segnaposto per funzioni vendor-specific).

    act_vent_recirculation / act_force_heating / act_force_cooling / act_force_free_cooling : Optional[bool]
        Forzature operative (ricircolo, heating/cooling, free-cooling). Da usare con cautela nelle politiche.

    processing_mode : Optional[str]
        Modo operativo (stringa controllata: es. "auto", "manual", "eco", "boost", ecc.).

    compressor_management / cooling_management : Optional[int]
        Gestione compressore/raffrescamento (codici vendor-specific).

    request_water / request_dehumidification / request_heating / request_cooling : Optional[bool]
        **Richieste** della VMC verso il circuito idronico o l’impianto (domanda di fluido o di servizio).

    sensor_t_ambient / sensor_h_ambient : Optional[float]
        Sensori aria ambiente lato VMC (T e RH).

    sensor_t_water : Optional[float]
        Temperatura acqua allo scambiatore VMC (se presente).

    sensor_t_outdoor : Optional[float]
        Sensore esterno integrato VMC (se diverso dal meteo provider).

    sensor_power_on_night / sensor_power_on_today : Optional[float]
        Minuti di funzionamento notturno/odierno (telemetria statistica).

    alarm_high_pressure / alarm_dew_point / alarm_low_water_temp / alarm_high_water_temp / alarm_alarm : Optional[bool]
        Segnali d’allarme principali (alta pressione, dew-point, acqua troppo fredda/calda, fault generico).
    """

    timestamp: datetime
    power_on: Optional[bool] = None

    t_setpoint: Optional[float] = None
    rh_setpoint: Optional[float] = None
    t_dew_point_setpoint: Optional[float] = None
    delta_t_dew_point_setpoint: Optional[float] = None

    spare_setpoint: Optional[int] = None

    act_vent_recirculation: Optional[bool] = None
    act_force_heating: Optional[bool] = None
    act_force_cooling: Optional[bool] = None
    act_force_free_cooling: Optional[bool] = None

    processing_mode: Optional[str] = None
    compressor_management: Optional[int] = None
    cooling_management: Optional[int] = None

    request_water: Optional[bool] = None
    request_dehumidification: Optional[bool] = None
    request_heating: Optional[bool] = None
    request_cooling: Optional[bool] = None

    sensor_t_ambient: Optional[float] = None
    sensor_h_ambient: Optional[float] = None
    sensor_t_water: Optional[float] = None
    sensor_t_outdoor: Optional[float] = None

    alarm_high_pressure: Optional[bool] = None
    alarm_dew_point: Optional[bool] = None
    alarm_low_water_temp: Optional[bool] = None
    alarm_high_water_temp: Optional[bool] = None
    alarm_alarm: Optional[bool] = None

    sensor_power_on_night: Optional[float] = None
    sensor_power_on_today: Optional[float] = None

@dataclass(slots=True)
class SupplyUnitSnapshot:
    """
    Istantanea dell’**unità di distribuzione** (es. miscelatrice, valvole, pompe,
    UTA di mandata). Descrive potenza idronica disponibile e stato dei rami.

    Attributi
    ---------
    timestamp : datetime
        Istante di rilevazione (timezone-aware).

    direct_su_power_on / adjustable_su_power_on : Optional[bool]
        Stato rami **diretto** e **miscelato/modulabile** (abilitazione pompe/valvole).

    three_point_mixing_valve : Optional[int]
        Stato comando **valvola a 3 punti** (-1 = chiudi/raffredda, 0 = stop, +1 = apri/riscalda).
        Convenzione da confermare con l’hardware.

    sensor_boiler_temp_system_supply / sensor_boiler_temp_system_return : Optional[float]
        Temperature mandata/ritorno sul circuito tecnico.

    sensor_adjustable_temp_system_supply / sensor_adjustable_temp_system_return : Optional[float]
        Temperature mandata/ritorno sul ramo miscelato/modulabile.

    sensor_direct_temp_system_supply / sensor_direct_temp_system_return : Optional[float]
        Temperature mandata/ritorno sul ramo diretto.
    """

    timestamp: datetime

    direct_su_power_on: Optional[bool] = None
    adjustable_su_power_on: Optional[bool] = None
    three_point_mixing_valve: Optional[int] = None

    sensor_boiler_temp_system_supply: Optional[float] = None
    sensor_boiler_temp_system_return: Optional[float] = None
    sensor_adjustable_temp_system_supply: Optional[float] = None
    sensor_adjustable_temp_system_return: Optional[float] = None
    sensor_direct_temp_system_supply: Optional[float] = None
    sensor_direct_temp_system_return: Optional[float] = None

@dataclass(slots=True)
class PlantSnapshot:
    """
    Istantanea dello **stato globale dell’impianto** all’istante `timestamp`.
    Aggrega zone, stagione, generatori e unità di distribuzione per consentire
    decisioni di alto livello (orchestrazione, sicurezza, ottimizzazione).

    Attributi
    ---------
    timestamp : datetime
        Timestamp della fotografia dell’impianto (timezone-aware).

    season : SeasonState
        Stato di stagione/meteo-stagionale (es. WINTER/SUMMER) con regole/bias applicativi.

    zones : dict[str, ZoneSnapshot]
        Mappa *nome-zona → ZoneSnapshot*. Deve contenere almeno le zone “core”.

    out_t / out_rh : Optional[float]
        Sensori esterni **globali** (se non forniti per-zona). Usati come fallback.

    dew_guard_active : Optional[bool]
        Flag globale del **Dew-Point Guard** (true se attivo in questa iterazione).

    free_cooling_possible : Optional[bool]
        True se le condizioni esterne consentono **free-cooling** (policy lato VMC).

    vmc / pdc / supply_unit : Optional[...Snapshot]
        Stati della VMC, PDC/generatore e unità di distribuzione (se disponibili).

    apt_windows_open : Optional[bool]
        Stato finestre dell'appartamento (True = almeno una finestra aperta).
        Utile per politiche di sicurezza/efficienza (es. stop raffrescamento se
        finestre aperte).

    presence_vacation : Optional[bool]
        Flag presenza “vacanza/assenza prolungata” (True = casa non occupata per periodo esteso).

    presence_nobodysin : Optional[bool]
        Flag presenza “nessuno in casa ora” (True = assente nell’immediato). Consente setback/eco.

    faults : tuple[str, ...]
        Elenco di eventuali **faults/allarmi** di impianto aggregati (codici brevi/slug).

    Metodi utility
    --------------
    mean_indoor_temperature() -> Optional[float]
        Media semplice delle temperature delle zone presenti (esclude None).

    mean_indoor_humidity() -> Optional[float]
        Media semplice delle RH delle zone presenti (esclude None).

    iter_zone_names() -> Iterable[str]
        Iteratore sui nomi delle zone (ordine del dizionario).
    """

    timestamp: datetime
    season: Optional[SeasonState] = None
    zones: Optional[dict[str, ZoneSnapshot]] = None

    mean_apt: Optional[SensorPair] = None
    outdoor: Optional[SensorPair] = None

    vmc: Optional[VMCSnapshot] = None
    pdc: Optional[PDCSnapshot] = None
    supply_unit: Optional[SupplyUnitSnapshot] = None

    apt_windows_open: Optional[bool] = None

    presence_vacation: Optional[bool] = None
    presence_nobodysin: Optional[bool] = None

    dew_guard_active: Optional[bool] = None
    free_cooling_possible: Optional[bool] = None
    faults: tuple[str, ...] = ()

    # def mean_indoor_temperature(self) -> Optional[float]:
    #     """
    #     Restituisce la **media semplice** delle temperature delle zone disponibili.
    #     Esclude valori None; se nessuna temperatura è disponibile, restituisce None.
    #     """
    #     if not self.zones:
    #         return None
    #     temps = [z.room_t for z in self.zones.values() if z.room_t is not None]
    #     if not temps:
    #         return None
    #     return sum(temps) / len(temps)

    # def mean_indoor_humidity(self) -> Optional[float]:
    #     """
    #     Restituisce la **media semplice** delle umidità relative delle zone disponibili.
    #     Esclude valori None; se nessuna RH è disponibile, restituisce None.
    #     """
    #     if not self.zones:
    #         return None
    #     humis = [z.room_rh for z in self.zones.values() if z.room_rh is not None]
    #     if not humis:
    #         return None
    #     return sum(humis) / len(humis)

    def iter_zone_names(self) -> Iterable[str]:
        """Itera i nomi delle zone presenti nello snapshot."""
        if not self.zones:
            return []
        return self.zones.keys()

    def __str__(self) -> str:

        # --- helper di formattazione compatti e robusti ---
        def fnum(x, nd=1):
            return f"{x:.{nd}f}" if x is not None else "-"

        def fbool(b, on="on", off="off"):
            return on if b is True else (off if b is False else "-")

        # --- parti comuni/top-level ---
        ts = self.timestamp.isoformat()

        # --- composizione finale one-line ---
        lines = [
            f"Timestamp            :: {ts}",
            f"  Season             :: {self.season.season.value if self.season else '-'}",
            f"  Total days         :: {self.season.days if self.season else '-'}",
            f"  Days passed        :: {self.season.passed if self.season else '-'}",
            f"  Days remaining     :: {self.season.remaining if self.season else '-'}",
            f"  Selected override  :: {self.season.overridden.value if self.season else '-'}",
            f"  Weather anomaly    :: {self.season.weather_anomaly if self.season else '-'}",
            f"------------------------------------------------------------------",
        ]

        lines += [
            f"Zones                :: {len(self.zones) if self.zones else 0}",
        ]

        if self.zones:
            for z in self.zones.values():
                # _LOGGER.debug("TEST Processing area %s", z)
                if z.sensors:
                    s = f":: [T:{fnum(z.sensors.temperature)}°C RH:{fnum(z.sensors.humidity,0)}% DP:{fnum(z.sensors.dew_point)}°C HI:{fnum(z.sensors.heat_index)}°C]"
                    lines += [
                        f"  {pad(z.name, width=16)}   "
                        f"{pad(s, width=38)}   -   "
                        f"Flow:{fnum(z.flow_t)}°C Ret:{fnum(z.return_t)}°C Valve:{fbool(z.act_state)}"
                    ]

        if self.mean_apt and self.outdoor:
            lines += [
                f"Indoor  means        :: [T:{fnum(self.mean_apt.temperature)}°C RH:{fnum(self.mean_apt.humidity,0)}% " 
                f"DP:{fnum(self.mean_apt.dew_point)}°C HI:{fnum(self.mean_apt.heat_index)}°C]",
                f"Outdoor means        :: [T:{fnum(self.outdoor.temperature)}°C RH:{fnum(self.outdoor.humidity,0)}%]",
                f"------------------------------------------------------------------",
            ]

        lines += [
            f"Home windows stat    :: {fbool(self.apt_windows_open, 'Some Open', 'All Closed')}",
            f"Vacation             :: {fbool(self.presence_vacation, 'Yes', 'No')}",
            f"Nobody's in          :: {fbool(self.presence_nobodysin, 'True', 'False')}",
            f"------------------------------------------------------------------",
        ]

        lines += [
            f"PDC",
            f"  FM power           :: {fbool(self.pdc.fm_power_on, 'On', 'Off') if self.pdc else '-'} ",
            f"  Device Power       :: {fbool(self.pdc.power_on) if self.pdc else '-'} ",
            f"  Mode               :: {self.pdc.device_mode if self.pdc else '-'} ",
            f"  WOT-Heat           :: {fnum(self.pdc.wot_heat) if self.pdc else '-'}°C ",
            f"  ΔT-Heat            :: {fnum(self.pdc.delta_t_heat) if self.pdc else '-'}°C ",
            f"  WOT-Cool           :: {fnum(self.pdc.wot_cool) if self.pdc else '-'}°C ",
            f"  ΔT-Cool            :: {fnum(self.pdc.delta_t_cool) if self.pdc else '-'}°C ",
            f"  In                 :: {fnum(self.pdc.sensor_t_water_in_pe) if self.pdc else '-'}°C ",
            f"  Out                :: {fnum(self.pdc.sensor_t_water_out_pe) if self.pdc else '-'}°C ",
            f"  MinOn              :: {fnum(self.pdc.minutes_power_on,0) if self.pdc else '-'}min ",
            f"  MinOff             :: {fnum(self.pdc.minutes_power_off,0) if self.pdc else '-'}min",
            f"------------------------------------------------------------------",
        ]

        lines += [
            f"Supply Unit",
            f"  Direct Device Power:: {fbool(self.supply_unit.direct_su_power_on, 'On', 'Off') if self.supply_unit else '-'} ",
            f"  Direct Supply Flow :: {fnum(self.supply_unit.sensor_direct_temp_system_supply) if self.supply_unit else '-'}°C ",
            f"  Direct Return Flow :: {fnum(self.supply_unit.sensor_adjustable_temp_system_return) if self.supply_unit else '-'}°C ",
            f"  Adjust Device Power:: {fbool(self.supply_unit.adjustable_su_power_on, 'On', 'Off') if self.supply_unit else '-'} ",
            f"  3-pt Valve         :: {self.supply_unit.three_point_mixing_valve if self.supply_unit else '-'} ",
            f"  Adj Supply         :: {fnum(self.supply_unit.sensor_adjustable_temp_system_supply) if self.supply_unit else '-'}°C ",
            f"  Adj Return         :: {fnum(self.supply_unit.sensor_adjustable_temp_system_return) if self.supply_unit else '-'}°C ",
            f"  Boiler Supply      :: {fnum(self.supply_unit.sensor_boiler_temp_system_supply) if self.supply_unit else '-'}°C ",
            f"  Boiler Return      :: {fnum(self.supply_unit.sensor_boiler_temp_system_return) if self.supply_unit else '-'}°C ",
            f"------------------------------------------------------------------",
        ]

        lines += [
            f"VMC",
            f"  Device Power       :: {fbool(self.vmc.power_on) if self.vmc else '-'} ",
            f"  T-Setpoint         :: {fnum(self.vmc.t_setpoint) if self.vmc else '-'}°C ",
            f"  RH-Setpoint        :: {fnum(self.vmc.rh_setpoint,0) if self.vmc else '-'}% ",
            f"  DP-Setpoint        :: {fnum(self.vmc.t_dew_point_setpoint) if self.vmc else '-'}°C ",
            f"  ΔDP-Setpoint       :: {fnum(self.vmc.delta_t_dew_point_setpoint) if self.vmc else '-'}°C ",
            f"  Mode               :: {self.vmc.processing_mode if self.vmc else '-'} ",
            f"  Req Water          :: {fbool(self.vmc.request_water) if self.vmc else '-'} ",
            f"  Req Dehumidif      :: {fbool(self.vmc.request_dehumidification) if self.vmc else '-'} ",
            f"  Req Heating        :: {fbool(self.vmc.request_heating) if self.vmc else '-'} ",
            f"  Req Cooling        :: {fbool(self.vmc.request_cooling) if self.vmc else '-'} ",
            f"  Sensor Ambient T   :: {fnum(self.vmc.sensor_t_ambient) if self.vmc else '-'}°C ",
            f"  Sensor Ambient RH  :: {fnum(self.vmc.sensor_h_ambient,0) if self.vmc else '-'}% ",
            f"  Sensor Water T     :: {fnum(self.vmc.sensor_t_water) if self.vmc else '-'}°C ",
            f"  Sensor Outdoor T   :: {fnum(self.vmc.sensor_t_outdoor) if self.vmc else '-'}°C ",
            f"  Power Today        :: {fnum(self.vmc.sensor_power_on_today,0) if self.vmc else '-'}min ",
            f"  Power Night        :: {fnum(self.vmc.sensor_power_on_night,0) if self.vmc else '-'}min ",
            f"  Alarm High Press   :: {fbool(self.vmc.alarm_high_pressure) if self.vmc else '-'} ",
            f"  Alarm Dew Point    :: {fbool(self.vmc.alarm_dew_point) if self.vmc else '-'} ",
            f"  Alarm Low Water T  :: {fbool(self.vmc.alarm_low_water_temp) if self.vmc else '-'} ",
            f"  Alarm High Water T :: {fbool(self.vmc.alarm_high_water_temp) if self.vmc else '-'} ",
            f"  Alarm General      :: {fbool(self.vmc.alarm_alarm) if self.vmc else '-'} ",
            f"------------------------------------------------------------------",
        ]

        return "\n".join(lines)
        # return (
        #     f"Timestamp :: {ts}"
        #     f"Season    :: {self.season}"
        #     # "| APTmean[{apt}] | IndoorMean[{indoor}] | Outdoor[{out}] "
        #     # "| Presence[{presence}] | Safety[{safety}] | Faults:{faults} "
        #     # "| Zones:{zones} | {pdc} | {vmc} | {su})"
        # ).format(
        #     ts=ts,
        #     season=season,
        #     # apt=apt_part,
        #     # indoor=indoor_part,
        #     # out=outdoor_part,
        #     # presence=presence_part,
        #     # safety=safety_part,
        #     # faults=faults_part,
        #     # zones=zones_part,
        #     # pdc=pdc_part,
        #     # vmc=vmc_part,
        #     # su=su_part,
        # )



