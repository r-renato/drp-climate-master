from __future__ import annotations

import logging

# pip install psychrolib
from typing import Optional
from psychrolib import (
    GetTDewPointFromRelHum,
)

def celsius_to_fahrenheit(celsius: float) -> float:
    """Conversione °C → °F senza dipendenze aggiuntive."""
    return celsius * 9.0 / 5.0 + 32.0

def dew_point_celsius(t_c: float, rh_pct: float) -> float:
    """
    Punto di rugiada (°C) da temperatura (°C) e UR (%) usando PsychroLib.
    """
    if not 0.0 <= rh_pct <= 100.0:
        raise ValueError("rh_pct deve essere tra 0 e 100.")

    rh = rh_pct / 100.0
    return float(GetTDewPointFromRelHum(t_c, rh))

def heat_index_celsius(t_c: float, rh_pct: float) -> float:
    # Converti in °F perché l'algoritmo NWS usa °F
    T = t_c * 9/5 + 32.0
    RH = rh_pct

    # Step "semplice" + media con T
    HI = 0.5 * (T + 61.0 + (T - 68.0) * 1.2 + RH * 0.094)
    HI = (HI + T) / 2.0

    if HI >= 80.0:
        # Regressione di Rothfusz
        HI = (-42.379 + 2.04901523*T + 10.14333127*RH
              - 0.22475541*T*RH - 0.00683783*T*T - 0.05481717*RH*RH
              + 0.00122874*T*T*RH + 0.00085282*T*RH*RH
              - 0.00000199*T*T*RH*RH)
        # Aggiustamenti
        if RH < 13 and 80 <= T <= 112:
            HI -= ((13 - RH) / 4) * ((17 - abs(T - 95.0)) / 17) ** 0.5
        elif RH > 85 and 80 <= T <= 87:
            HI += ((RH - 85) / 10) * ((87 - T) / 5)
    # Torna in °C
    return (HI - 32.0) * 5/9
