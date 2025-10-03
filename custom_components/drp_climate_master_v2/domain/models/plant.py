from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(slots=True)
class ZoneSnapshot:
    """
    Istantanea dei **sensori** e dello **stato impianto** per una singola *zona/stanza*
    al tempo `ts`. Tutte le temperature sono espresse in **°C**, l'umidità relativa (RH)
    in **%**, i comandi/attuazioni in **%** (0..100). Il timestamp deve essere
    **timezone-aware** (consigliato UTC).

    Questa struttura è l'input minimo e coerente per:
    - lo **stimatore di stato** (es. Kalman) del modello termico,
    - il **generatore di target di comfort** (adaptive),
    - il **controllore** (PID/MPC-lite) e i **guardiani di sicurezza** (dew-point).

    Attributi
    ---------
    ts : datetime
        Timestamp dell’istantanea (timezone-aware). Usato per allineare snapshot,
        forecast e scheduling del ciclo di controllo (rilevazione dati stantii).

    t_room : float
        Temperatura aria **nella stanza** (uscita di riferimento del controllo).
        Entra direttamente nel filtro/kalman e nel calcolo dell’errore di comfort.

    rh_room : float
        Umidità relativa **nella stanza**. Serve per strategie estive, eventuale
        controllo VMC/deumidifica e, in assenza di `dp_room`, per calcolarne il valore.

    dp_room : float
        **Dew point** della stanza. Vincolo di sicurezza in raffrescamento radiante:
        il sistema deve garantire `T_flow >= dp_room + safety` (tip. 1.5–2.0 °C).

    hi_room : float
        **Heat Index** percepito in stanza (da T+RH). Utile per applicare bias estivi
        al target di comfort o per aumentare ventilazione quando il caldo è “opprimente”.

    t_mean_apt : Optional[float]
        Temperatura **media appartamento** (aggregato di più stanze). Proxy della
        massa termica/accoppiamenti inter-zona: stabilizza le stime e i target.

    t_out : float
        Temperatura **esterna** istantanea. Disturbo principale del modello termico
        e base per il **comfort adattivo** (running mean esterna).

    rh_out : Optional[float]
        Umidità relativa **esterna**. Opzionale: utile per stimare dew point esterno
        o opportunità di deumidifica tramite VMC (aria secca fuori).

    t_flow : Optional[float]
        Temperatura **mandata** del circuito di zona (o collettore). Necessaria per
        il **Dew-Point Guard** e, assieme a `t_return`, per stimare la potenza termica.

    t_return : Optional[float]
        Temperatura **ritorno** del circuito di zona. Con `t_flow` fornisce ΔT idronico,
        proxy della potenza assorbita/erogata per la penalizzazione energetica.

    u_prev : Optional[bool]
        **Ultimo comando** applicato all’attuatore di zona (on/off). Serve per
        predizione di stato, **rate limiting** e vincoli di variazione (`|Δu|`).

    Note
    ----
    - Se alcuni campi opzionali mancano (es. `t_flow`, `t_return`, `rh_out`), il controllo
      può funzionare in modalità ridotta (es. DP-guard conservativo, costo energetico stimato).
    - Validazioni tipiche (da implementare a livello di pre-processing):
        * T interna: ~[-10, 50] °C, T esterna: ~[-30, 60] °C
        * RH: [0, 100] %
        * `ts` non deve essere troppo distante dall'orario corrente (dati stantii)
    """

    ts: datetime
    t_room: float
    rh_room: float
    dp_room: float
    hi_room: float
    t_mean_apt: Optional[float]
    t_out: float
    rh_out: Optional[float] = None
    t_flow: Optional[float] = None
    t_return: Optional[float] = None
    u_prev: Optional[bool] = None  # ultimo comando all’attuatore
