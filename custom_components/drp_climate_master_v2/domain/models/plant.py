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


@dataclass(slots=True)
class PDCSnapshot:
    """
    Istantanea della **Pompa di Calore (PDC)** o generatore termico.

    Campi suggeriti (estendibili nel tempo):
      • mode: Literal["heat","cool","dhw","off"] — modalità operativa
      • supply_t / return_t: float — mandata/ritorno lato generatore
      • flow_rate: Optional[float] — portata (l/min o m³/h)
      • compressor_on: bool — stato compressore
      • power_w: Optional[float] — potenza istantanea
      • faults: tuple[str, ...] — codici allarmi/guasti

    Al momento è un segnaposto documentato, pronto per essere esteso.
    """


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
