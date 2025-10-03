from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional

from custom_components.drp_climate_master_v2.domain.models.season import SeasonState


@dataclass(slots=True)
class ZoneSnapshot:
    """
    Istantanea dei **sensori** e dello **stato impianto** per una singola *zona/stanza*
    al tempo `ts`.

    Unità:
      - Temperature in **°C**
      - Umidità relativa (RH) in **%**
      - Comandi/attuazioni on/off come **bool**
    Il timestamp deve essere **timezone-aware** (consigliato UTC).

    Questa struttura funge da input coerente per:
      • lo **stimatore di stato** del modello termico (es. Kalman),
      • il **generatore di target di comfort** (adaptive),
      • il **controllore** (PID/MPC-lite) e i **guardiani di sicurezza** (dew-point).

    Attributi
    ---------
    ts : datetime
        Timestamp dell’istantanea (timezone-aware). Usato per allineare snapshot,
        forecast e scheduling del ciclo di controllo (rilevazione di dati stantii).
    room_t : float
        Temperatura aria **nella stanza** (variabile controllata principale).
    room_rh : float
        Umidità relativa **nella stanza**. Utile per strategie estive/VMC e,
        in assenza di `room_dp`, per calcolare il dew point.
    room_dp : float
        **Dew point** in stanza. Vincolo di sicurezza per raffrescamento radiante:
        il sistema deve garantire `T_flow >= room_dp + safety` (tip. 1.5–2.0 °C).
    room_hi : float
        **Heat Index** percepito (da T+RH). Può introdurre bias al target estivo
        o guidare l’aumento della ventilazione quando il caldo è opprimente.
    mean_apt_t : Optional[float]
        Temperatura **media appartamento** (aggregata su più stanze). Proxy della
        massa termica/accoppiamenti inter-zona, utile a stabilizzare stime e target.
    mean_apt_rh : Optional[float]
        Umidità relativa **media appartamento**. Utile per strategie VMC/globali.
    out_t : float
        Temperatura **esterna** istantanea. Disturbo principale del modello
        e base per il **comfort adattivo** (running mean esterna).
    out_rh : Optional[float]
        Umidità relativa **esterna**. Utile per stimare il dew point esterno o
        valutare opportunità di free-cooling/deumidifica con VMC.
    flow_t : Optional[float]
        Temperatura **mandata** del circuito di zona/collettore. Necessaria per
        Dew-Point Guard e, assieme a `return_t`, per stimare la potenza termica.
    return_t : Optional[float]
        Temperatura **ritorno** del circuito di zona. Con `flow_t` dà ΔT idronico,
        proxy della potenza assorbita/erogata per penalizzazioni energetiche.
    act_state : Optional[bool]
        **Ultimo comando** applicato all’attuatore della zona (on/off). Serve per
        predizione di stato, **rate-limiting** e vincoli di variazione (|Δu|).

    Note
    ----
    • Campi opzionali mancanti (es. `flow_t`, `return_t`, `out_rh`) consentono modalità
      ridotte: DP-guard più conservativo, costi energetici stimati, ecc.
    • Validazioni tipiche (da fare a monte):
        – T interna: ~[-10, 50] °C; T esterna: ~[-30, 60] °C
        – RH: [0, 100] %
        – `ts` non troppo distante dall’ora corrente (evitare dati stantii)
    """

    ts: datetime
    room_t: float
    room_rh: float
    room_dp: float
    room_hi: float

    mean_apt_t: Optional[float]
    mean_apt_rh: Optional[float]

    out_t: float
    out_rh: Optional[float] = None

    flow_t: Optional[float] = None
    return_t: Optional[float] = None

    act_state: Optional[bool] = None  # ultimo comando all’attuatore (on/off)


from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(slots=True)
class PDCSnapshot:
    """
    Istantanea degli **stati e temperature della Pompa di Calore (PDC)** e del
    circuito tecnico associato, utile per orchestrazione (mode/mix), vincoli di
    sicurezza e controllo avanzato (PID/MPC-lite). Tutte le temperature sono in **°C**.
    Il timestamp **deve** essere timezone-aware (consigliato **UTC**).

    Uso tipico
    ----------
    - **Orchestratore**: selezione modalità (heating/cooling/DHW/defrost/standby),
      consenso all’attuazione, logiche di pre-heating/pre-cooling.
    - **Safety layer**: gestione min-on/min-off, controlli su ΔT, diagnosi anomalie.
    - **Controllore/MPC**: vincoli su temperature disponibili, stima potenza,
      penalizzazioni energetiche.

    Attributi
    ---------
    timestamp : datetime
        Istante di rilevazione dello snapshot (timezone-aware). Serve ad allineare
        i cicli di controllo e a rilevare dati stantii.

    pdc_fm_power_on : bool
        Stato ON/OFF del modulo di movimentazione fluido lato PDC (es. **Flow Manager** /
        circolatore principale / fan module del gruppo). True ⇒ circolazione attiva.

    pdc_power_on : Optional[bool]
        Stato ON/OFF della **Pompa di Calore** (compressore/centralina in marcia).
        Può essere `None` se il dato non è disponibile (sensoristica parziale).

    pdc_device_mode : Optional[int]
        Modalità operativa **grezza** della PDC come codice numerico **vendor-specific**
        (esempi tipici da mappare: 0=standby, 1=heating, 2=cooling, 3=dhw, 4=defrost).
        Va **normalizzato** a valle per le logiche applicative.

    pdc_wot_heat : Optional[float]
        **Water Outlet Temperature (WOT) setpoint** lato PDC in **riscaldamento**. In molte macchine
        è il risultato della **curva climatica** (compensazione climatica) + eventuali offset.

    pdc_delta_t_heat : Optional[float]
        **Offset/Delta** applicato al setpoint WOT in riscaldamento (boost/eco). Utile per
        strategie temporanee o pre-heating (valore positivo ⇒ alza la mandata obiettivo).

    pdc_wot_cool : Optional[float]
        **WOT setpoint** lato PDC in **raffrescamento** (temperatura acqua fredda desiderata).

    pdc_delta_t_cool : Optional[float]
        **Offset/Delta** applicato al setpoint WOT in raffrescamento (più basso ⇒ acqua più fredda).

    pdc_t_water_in_pe : Optional[float]
        Temperatura **ingresso** acqua lato **plate/exchanger** PDC (lato impianto). Misura utile
        per diagnosi scambi e calcolo ΔT locale sul generatore.

    pdc_t_water_out_pe : Optional[float]
        Temperatura **uscita** acqua lato **plate/exchanger** PDC (lato impianto). Con `pdc_t_water_in_pe`
        consente di ricavare **ΔT PDC** (= out_pe − in_pe) come proxy di scambio/potenza sul generatore.

    boiler_supply_temp : Optional[float]
        **Mandata** circuito tecnico/distribuzione (post PDC/miscelazione). Serve per verificare
        che la temperatura disponibile sia coerente con la domanda utenze (radianti/fancoil).

    boiler_return_temp : Optional[float]
        **Ritorno** circuito tecnico/distribuzione. Con la mandata fornisce **ΔT idronico** della
        rete (proxy del carico reale lato impianto).

    minutes_power_on : Optional[float]
        Minuti consecutivi in **stato acceso** (PDC/gruppo). Usato per policy **min-on time**
        e per individuare cicli troppo brevi.

    minutes_power_off : Optional[float]
        Minuti consecutivi in **stato spento**. Usato per policy **min-off time** e anticycling.

    Note
    ----
    - **WOT** = *Water Outlet Temperature* (setpoint di mandata lato generatore).
    - I codici di `pdc_device_mode` sono **fornitore-specifici**: definire una mappa di normalizzazione.
    - Range indicativi:
        * WOT riscaldamento: ~30–55 °C; WOT raffrescamento: ~7–20 °C
        * ΔT idronico rete (boiler_supply − boiler_return): tip. 3–10 °C
        * ΔT PDC (out_pe − in_pe): variabile per macchina/condizioni
    - Validazioni consigliate: timestamp recente, temperature plausibili, coerenza
      tra mode e setpoint (es. in cooling non impostare WOT > 25 °C).
    """

    timestamp: datetime
    pdc_fm_power_on: bool
    pdc_power_on: Optional[bool]

    pdc_device_mode: Optional[int]

    pdc_wot_heat: Optional[float]
    pdc_delta_t_heat: Optional[float]

    pdc_wot_cool: Optional[float]
    pdc_delta_t_cool: Optional[float]

    pdc_t_water_in_pe: Optional[float]
    pdc_t_water_out_pe: Optional[float]

    boiler_supply_temp: Optional[float]
    boiler_return_temp: Optional[float]

    minutes_power_on: Optional[float]
    minutes_power_off: Optional[float]

@dataclass(slots=True)
class VMCSnapshot:
    """
    Istantanea della **Ventilazione Meccanica Controllata (VMC)**.

    Campi suggeriti:
      • fan_level: int — step di ventilazione
      • bypass_active: bool — bypass scambiatore attivo (free-cooling)
      • supply_rh / extract_rh: Optional[float] — RH mandata/estrazione
      • supply_t / extract_t: Optional[float] — T mandata/estrazione
      • faults: tuple[str, ...] — codici allarmi/guasti

    Al momento è un segnaposto documentato, pronto per essere esteso.
    """


@dataclass(slots=True)
class SupplyUnitSnapshot:
    """
    Istantanea dell’unità di **trattamento aria/UTA** o di un’unità di distribuzione
    (miscelatrice, valvole, pompe).

    Campi suggeriti:
      • pump_on: bool — stato pompa
      • valve_pos: Optional[float] — posizione valvola 0..100 %
      • supply_t_setpoint: Optional[float] — setpoint mandata
      • faults: tuple[str, ...] — codici allarmi/guasti

    Al momento è un segnaposto documentato, pronto per essere esteso.
    """


@dataclass(slots=True)
class PlantSnapshot:
    """
    Istantanea dello **stato globale dell’impianto** al tempo `ts`.

    Attributi
    ---------
    ts : datetime
        Timestamp della fotografia dell’impianto (timezone-aware).
    season : SeasonState
        Stato di stagione/meteo-stagionale (es. WINTER/SUMMER, regole, bias).
    zones : dict[str, ZoneSnapshot]
        Mappa *nome-zona → ZoneSnapshot*. Deve contenere almeno le zone “core”.
    out_t : Optional[float]
        Temperatura esterna globale (se non presente in ogni zona).
    out_rh : Optional[float]
        Umidità esterna globale (se non presente in ogni zona).
    dew_guard_active : Optional[bool]
        Flag globale del **Dew-Point Guard** (true se attivo in questa iterazione).
    free_cooling_possible : Optional[bool]
        True se le condizioni esterne consentono **free-cooling** (policy lato VMC).
    vmc : Optional[VMCSnapshot]
        Stato VMC, se disponibile.
    pdc : Optional[PDCSnapshot]
        Stato PDC/generatore, se disponibile.
    supply_unit : Optional[SupplyUnitSnapshot]
        Stato unità di distribuzione (pompe/valvole/UTA), se disponibile.
    faults : tuple[str, ...]
        Eventuali **faults**/allarmi di impianto aggregati.

    Metodi utility
    --------------
    mean_indoor_temperature() -> Optional[float]
        Media semplice delle temperature delle zone presenti (esclude None).
    mean_indoor_humidity() -> Optional[float]
        Media semplice delle RH delle zone presenti (esclude None).
    iter_zone_names() -> Iterable[str]
        Iteratore sui nomi delle zone (ordine di dizionario).
    """

    ts: datetime
    season: SeasonState
    zones: dict[str, ZoneSnapshot]

    out_t: Optional[float] = None
    out_rh: Optional[float] = None

    dew_guard_active: Optional[bool] = None
    free_cooling_possible: Optional[bool] = None
    vmc: Optional[VMCSnapshot] = None
    pdc: Optional[PDCSnapshot] = None
    supply_unit: Optional[SupplyUnitSnapshot] = None
    faults: tuple[str, ...] = ()

    def mean_indoor_temperature(self) -> Optional[float]:
        """
        Restituisce la **media semplice** delle temperature delle zone disponibili.

        Esclude valori None; se nessuna temperatura è disponibile, restituisce None.
        """
        temps = [z.room_t for z in self.zones.values() if z.room_t is not None]
        if not temps:
            return None
        return sum(temps) / len(temps)

    def mean_indoor_humidity(self) -> Optional[float]:
        """
        Restituisce la **media semplice** delle umidità relative delle zone disponibili.

        Esclude valori None; se nessuna RH è disponibile, restituisce None.
        """
        humis = [z.room_rh for z in self.zones.values() if z.room_rh is not None]
        if not humis:
            return None
        return sum(humis) / len(humis)

    def iter_zone_names(self) -> Iterable[str]:
        """Itera i nomi delle zone presenti nello snapshot."""
        return self.zones.keys()
