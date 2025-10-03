#
from __future__ import annotations

from datetime import date, datetime, time
from statistics import mean
from typing import Protocol, Optional, Dict, Tuple
import logging
import time as _time

from ..domain.models import SeasonState, SensorPair

_LOGGER = logging.getLogger(__name__)

# ===== Implementation ===========================================================
class SeasonThresholdStrategy:
    """
    Strategia stagionale **dinamica** (senza comfort zone statica).

    Scopo
    -----
    Calcolare, in funzione della stagione e dei segnali indoor/outdoor, una banda
    di comfort **dinamica** (T_min/T_max, RH_min/RH_max, DP_set) e le soglie
    operative **ON/OFF** per i modi HVAC (heating, cooling, dehumidifying).
    I risultati sono pensati per alimentare il controllo valvole/stati clima.

    Parametri
    ---------
    season_state : SeasonState
        Contesto stagionale corrente/target (es. WINTER/SPRING/SUMMER/AUTUMN)
        ed eventuale profilo di isteresi (comfort/eco/away) e/o fattori custom.
    core_rooms : list[SensorPair]
        Stanze principali (prioritarie) con coppie sensore (temperatura, umidità).
        Guidano il calcolo del centro banda, la stima del bias e gli interlock DP.
    secondary_rooms : list[SensorPair]
        Stanze secondarie usate per robustezza (outlier rejection/media robusta).
    indoor_sensors : SensorPair
        Sensori “aggregati appartamento” (tipicamente media/mediana T e UR). Utili
        per bias/stabilità globale e come riferimento quando i segnali per stanza
        sono scarsi o rumorosi.
    outdoor_sensors : SensorPair
        Sensori esterni T/UR (corrente + storico/forecast a valle dell’adapter)
        usati per running-mean outdoor temperature (adaptive comfort) e contesto
        igrometrico/ventilazione.

    Responsabilità
    --------------
    1) Normalizzare/validare letture (filtri base, mediane/mediane pesate, outliers).
    2) Stimare il **centro termico** della banda (es. modello adaptive da T_esterna
       “running mean” + bias appreso da storico indoor).
    3) Determinare l’**ampiezza termica** ΔT in base a profilo (comfort/eco/away),
       varianza dei segnali, inerzia impianto e volatilità meteo.
    4) Costruire la **banda igrometrica** con **vincolo anti-condensa**:
       DP_set ≤ (T_superficie_min − margine); in assenza di T_superficie, usare
       regole conservative in funzione di T_indoor.
    5) Derivare soglie HVAC con isteresi sicura:
       - Heating: ON vicino a T_min, OFF vicino a T_max
       - Cooling: ON vicino a T_max (offset), OFF vicino a T_min (offset)
       - Dehumidifying: ON a DP_set, OFF a DP_set − ΔDP (solo stagioni umide)
    6) Esporre helper/interlock per stanza (es. chiusura valvola se DP_stanza ≥ DP_set).
    7) Gestire **cache/TTL** e invalidazione per ricalcoli efficienti.

    Output atteso
    -------------
    - Oggetti `SeasonThreshold` per la stagione/profilo correnti, con:
      setpoint dinamici (T/RH/DP) e soglie HVAC (heating/cooling/dehumidifying).
    - (Opzionale) metriche diagnostiche: bias stimati, gap d’isteresi applicati,
      clamp su RH/DP, motivazioni di interlock.

    Note d’uso
    ----------
    - La classe non effettua I/O diretto verso Home Assistant/Influx: si aspetta
      che i `SensorPair` e gli adapter a monte forniscano i valori correnti/storici.
    - Metodi che eseguono calcoli intensivi o accesso a storici/forecast sono
      previsti asincroni e con caching per ridurre latenza e carico.
    - Validazioni/Clamp: garantire ordine min≤max, 0≤RH≤100, gap di isteresi ≥ soglia.

    Esempio d’uso (schematico)
    --------------------------
        sts = SeasonThresholdStrategy(season_state, core_rooms, secondary_rooms,
                                      indoor_sensors, outdoor_sensors)
        await sts.compute()                      # calcolo/refresh thresholds
        thr = sts.get_threshold()               # SeasonThreshold corrente
        # usare `thr` per orchestrare heating/cooling/dehumidifying e interlock DP
    """
    def __init__(
        self,
        season_state: SeasonState,
        core_rooms: list[SensorPair],
        secondary_rooms: list[SensorPair],
        indoor_sensors: SensorPair,
        outdoor_sensors: SensorPair,
    ) -> None:
