from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Mapping, Optional, Literal, cast

import logging
from homeassistant.core import HomeAssistant
from homeassistant.components.weather import Forecast
from homeassistant.util import dt as dt_util

from ..weather.provider import WeatherForecastProvider  # il tuo Protocol

_LOGGER = logging.getLogger(__name__)

def _as_iso_local(dt_like: Any) -> Optional[str]:
    """
    Converte un input (str/datetime) in ISO 8601 **locale** (stringa) per il campo Forecast['datetime'].
    - Se è str: prova a parse con HA (gestisce anche timezone); se è data-only (YYYY-MM-DD) crea mezzanotte locale.
    - Se è datetime: lo porta in timezone locale e lo serializza ISO.
    """
    if isinstance(dt_like, datetime):
        return dt_util.as_local(dt_like).isoformat()
    if isinstance(dt_like, str):
        dt = dt_util.parse_datetime(dt_like)
        if dt is not None:
            return dt_util.as_local(dt).isoformat()
        # tenta formato data "YYYY-MM-DD"
        try:
            y, m, d = map(int, dt_like.split("-"))
            # mezzanotte locale di quel giorno
            return dt_util.start_of_local_day(datetime(y, m, d)).isoformat()
        except Exception:
            return None
    return None


class WeatherForecast(WeatherForecastProvider):
    """
    Provider forecast basato sui servizi Home Assistant dell'integrazione `weather`.

    - Usa `weather.get_forecasts` (nuovo) con `return_response=True`.
    - Fallback su `weather.get_forecast` (vecchio).
    - Normalizza i record e filtra per intervallo [start, start+days).

    I record ritornati hanno chiavi convenzionali:
      - 'datetime' (ISO 8601, se disponibile) + 'date' (date)
      - 'temperature' (tmax o temp per daily/hourly), 'templow' (solo daily)
      - 'dewpoint' se presente (alcune integrazioni la forniscono)
      - 'condition', 'wind_speed', 'wind_bearing', 'precipitation', 'precipitation_probability', ecc.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        weather_component_id: str,
        *,
        forecast_type: Literal["daily", "hourly"] = "daily",
    ) -> None:
        self._hass = hass
        self._entity_id = weather_component_id
        self._type = forecast_type

    def _normalize_row(self, row: Mapping[str, Any], *, kind: str) -> Forecast:
        """
        Converte un record grezzo nel TypedDict Forecast:
        - imposta SEMPRE 'datetime' come stringa ISO locale
        - popola solo le chiavi ammesse da Forecast (niente campi extra come 'dewpoint')
        - per daily: 'temperature' = tmax/temperature, 'templow' = tmin/templow se disponibile
        - per hourly: 'temperature' = temperatura oraria (niente 'templow')
        """
        out: dict[str, Any] = {}

        # datetime (obbligatorio per il filtro): cerca in vari alias comuni
        dt_iso = (
            _as_iso_local(row.get("datetime"))
            or _as_iso_local(row.get("time"))
            or _as_iso_local(row.get("day"))
            or _as_iso_local(row.get("date"))
        )
        if dt_iso is not None:
            out["datetime"] = dt_iso  # Forecast richiede 'datetime'

        # temperature / templow
        tmax = row.get("temperature", row.get("tmax"))
        tmin = row.get("templow", row.get("tmin"))
        if kind == "daily":
            out["temperature"] = _to_float_or_none(tmax)
            tmin_f = _to_float_or_none(tmin)
            if tmin_f is not None:
                out["templow"] = tmin_f
        else:  # hourly
            out["temperature"] = _to_float_or_none(tmax)

        # altri campi standard ammessi da Forecast
        for k in (
            "condition",
            "wind_speed",
            "wind_bearing",
            "precipitation",
            "precipitation_probability",
            "pressure",
            "humidity",
            # aggiungi qui altre chiavi supportate da Forecast se ti servono (es. cloud_coverage, uv_index)
        ):
            if k in row:
                out[k] = row[k]

        return cast(Forecast, out)
    
    def _forecast_date(self, f: Forecast) -> Optional[date]:
        """Estrae la sola 'date' dal campo standard Forecast['datetime'] (ISO o datetime)."""
        dt_val = f.get("datetime")
        if isinstance(dt_val, str):
            dt = dt_util.parse_datetime(dt_val)
            if dt is None:
                return None
            return dt_util.as_local(dt).date()
        if isinstance(dt_val, datetime):
            return dt_util.as_local(dt_val).date()
        return None

    async def async_get_forecast_days(self, *, start: date, days: int) -> List[Forecast]:
        """Ritorna le previsioni normalizzate (TypedDict Forecast) nell'intervallo [start, start+days)."""
        if days <= 0:
            return []

        # 1) chiama il servizio HA con return_response=True
        raw = await self._call_service_with_response()

        # 2) estrai lista grezza (formati diversi gestiti da _extract_rows)
        rows = self._extract_rows(raw)

        # 3) normalizza in Forecast (strongly typed)
        normalized: List[Forecast] = [
            self._normalize_row(r, kind=self._type) for r in rows if isinstance(r, Mapping)
        ]

        # 4) filtra usando la date ricavata da Forecast['datetime']
        end = start + timedelta(days=days)
        filtered: List[Forecast] = []
        for f in normalized:
            d = self._forecast_date(f)
            if d is None:
                continue
            if start <= d < end:
                filtered.append(f)

        # 5) safety: per daily taglia comunque a 'days'
        if self._type == "daily" and len(filtered) > days:
            filtered = filtered[:days]

        return filtered

    # ------------------------------------------------------------------ internals

    async def _call_service_with_response(self) -> Any:
        """Chiama weather.get_forecasts, fallback a weather.get_forecast, e ritorna la response grezza."""
        svc_data = {"type": self._type, "entity_id": self._entity_id}

        # Preferito: weather.get_forecasts (multi-entity, con response)
        try:
            resp = await self._hass.services.async_call(
                "weather",
                "get_forecasts",
                svc_data,
                blocking=True,
                return_response=True,  # fondamentale per ricevere la risposta
            )
            if resp is not None:
                return resp
        except Exception as e:
            _LOGGER.debug("weather.get_forecasts fallita: %s", e)

        # Fallback: weather.get_forecast (singolare) usato in versioni precedenti
        try:
            resp = await self._hass.services.async_call(
                "weather",
                "get_forecast",
                svc_data,
                blocking=True,
                return_response=True,
            )
            return resp
        except Exception as e:
            _LOGGER.warning("weather.get_forecast fallita: %s", e)
            return None

    def _extract_rows(self, resp: Any) -> List[Mapping[str, Any]]:
        """
        Estrae la lista di forecast dalla risposta del servizio, gestendo formati diversi.
        Possibili shape note:
          - { 'weather.entity': [ { ... }, ... ] }
          - { 'forecasts': [ { ... }, ... ] }
          - { 'forecast': [ { ... }, ... ] }
          - [ { ... }, ... ]
        """
        if resp is None:
            return []

        # Caso lista diretta
        if isinstance(resp, list):
            return [r for r in resp if isinstance(r, Mapping)]

        if isinstance(resp, Mapping):
            # Per get_forecasts (nuovo), la risposta tipica mappa entity_id -> list
            rows = resp.get(self._entity_id)
            if isinstance(rows, list):
                return [r for r in rows if isinstance(r, Mapping)]

            # Varianti
            for key in ("forecasts", "forecast"):
                rows = resp.get(key)
                if isinstance(rows, list):
                    return [r for r in rows if isinstance(r, Mapping)]

        # Ultimo tentativo: se c'è una sola chiave con lista
        if isinstance(resp, Mapping) and len(resp) == 1:
            sole = next(iter(resp.values()))
            if isinstance(sole, list):
                return [r for r in sole if isinstance(r, Mapping)]

        return []




# --------------------------- utility locali -----------------------------------

def _to_float_or_none(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None
