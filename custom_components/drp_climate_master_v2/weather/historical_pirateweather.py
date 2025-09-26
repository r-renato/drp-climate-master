"""
Pirate Weather providers for Climate Master v2 (hardened).

Key features
------------
- Decoupled `WeatherProvider` interface (daily / daily_range)
- Library-backed provider (python-pirateweather) with thread offload
- HTTP-backed provider (aiohttp) as fallback
- Concurrency limiting (Semaphore), retry w/ exponential backoff + jitter
- Reusable HTTP session via async context manager
- In-memory TTL cache per-day; sorted outputs; robust parsing & RH clamping
- Config validation (lat/lon/units)

Usage
-----
    cfg = PirateWeatherConfig(api_key=..., lat=..., lon=..., units="si")
    provider = create_pirateweather_provider(cfg)    # library -> http fallback

    # Optional: reuse HTTP session
    async with provider:
        samples = await provider.daily_range(date(2025,8,12), date(2025,8,21))

    # Or without context (HTTP will open a short-lived session per call)
    samples = await provider.daily_range(date(2025,8,12), date(2025,8,21))
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import random
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple, cast
from urllib.parse import urlparse

from homeassistant.core import HomeAssistant
from homeassistant.components.weather import Forecast
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from ..helpers.logger import log_debug

from ..helpers.cache import PersistentForecastCache

from ..const import DOMAIN

from ..helpers.utils import as_float, ratio_or_percent_to_int
from .provider import WeatherHistoricalProvider

_LOGGER = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Config & Opzioni
# -----------------------------------------------------------------------------

@dataclass(slots=True, frozen=True)
class PirateWeatherConfig:
    """
    Configurazione del provider Pirate Weather (Time Machine).

    Questa dataclass, immutabile e con `__slots__`, raccoglie i parametri necessari
    per costruire le richieste al servizio Pirate Weather Time Machine.

    Parametri
    ---------
    api_key : str
        Chiave API per l’accesso al servizio. **Non** loggarla in chiaro.
    lat : float
        Latitudine in gradi decimali (range valido: [-90.0, 90.0]).
    lon : float
        Longitudine in gradi decimali (range valido: [-180.0, 180.0]).
    units : str, default="si"
        Unità di misura: `"si"` (Celsius, m/s, ecc.) oppure `"us"` (Fahrenheit, mph, ecc.).
    base_url : str, default="https://timemachine.pirateweather.net/forecast"
        Endpoint base del servizio Time Machine. È accettato schema `http` o `https`.
        L’URL viene normalizzato rimuovendo l’eventuale slash finale.

    Validazioni
    -----------
    - `api_key` dev’essere non vuota.
    - `lat` e `lon` devono rientrare nei range geografici ammessi.
    - `units` ∈ {"si", "us"}.
    - `base_url` deve avere schema `http/https` e un netloc valido.

    Esempio
    -------
    >>> cfg = PirateWeatherConfig(api_key="XYZ", lat=41.9028, lon=12.4964, units="si")
    >>> cfg.base_url
    'https://timemachine.pirateweather.net/forecast'

    Note
    ----
    - Per comporre una chiamata Time Machine tipica:
      `{base_url}/{api_key}/{lat},{lon},{ISO_UTC}?units={units}&lang=it`
      (es. istante: `2024-01-01T12:00:00Z`)
    """

    api_key: str
    lat: float
    lon: float
    units: str = "si"            # 'si' or 'us'
    base_url: str = "https://timemachine.pirateweather.net/forecast"

    def __post_init__(self) -> None:
        # API key non vuota
        if not self.api_key or not self.api_key.strip():
            raise ValueError("Invalid api_key: must be a non-empty string")

        # Coordinate entro i range validi
        if not (-90.0 <= self.lat <= 90.0) or not (-180.0 <= self.lon <= 180.0):
            raise ValueError(f"Invalid lat/lon: {self.lat}, {self.lon}")

        # Unità ammesse
        if self.units not in {"si", "us"}:
            raise ValueError(f"Invalid units: {self.units} (allowed: 'si', 'us')")

        # base_url valida (http/https) e normalizzazione (senza trailing slash)
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"Invalid base_url: {self.base_url} (expected http(s)://host[/...])")

        # Normalizza rimuovendo lo slash finale (dataclass frozen → serve object.__setattr__)
        if self.base_url.endswith("/"):
            object.__setattr__(self, "base_url", self.base_url.rstrip("/"))

@dataclass(slots=True, frozen=True)
class ProviderOptions:
    """
    Opzioni runtime per un provider HTTP/IO con concorrenza controllata e retry con backoff.

    Questa dataclass è immutabile e con `__slots__`, pensata per essere passata in sicurezza
    tra task asyncio. I parametri governano parallelismo, strategie di retry e timeouts.

    Parametri
    ---------
    max_concurrency : int, default=6
        Numero massimo di operazioni concorrenti (es. per `asyncio.Semaphore`). Deve essere ≥ 1.
    retries : int, default=0
        Numero di retry dopo il primo tentativo. I tentativi totali sono `retries + 1`.
        Esempio: `retries=2` ⇒ fino a 3 prove (attempt 0, 1, 2).
    backoff_base : float, default=0.5
        Delay di base (secondi) per il backoff esponenziale: `delay = base * factor**attempt`.
    backoff_factor : float, default=2.0
        Fattore moltiplicativo del backoff (tipicamente ≥ 1.0). Con 2.0 raddoppia ad ogni retry.
    jitter : float, default=0.25
        Jitter frazionario (0.0–1.0) applicato moltiplicativamente al delay calcolato
        per ridurre la sincronizzazione dei retry. Es.: 0.25 ⇒ 75%–125% del delay.
    http_timeout_s : int, default=20
        Timeout (secondi) per una singola richiesta HTTP.

    Note
    ----
    - Questa classe **non** implementa il calcolo del backoff; definisce solo i parametri.
      Un helper esterno può usare:
          delay = backoff_base * (backoff_factor ** attempt)
          jittered = delay * uniform(1 - jitter, 1 + jitter)
    - `total_tries` è esposto come proprietà di sola lettura per comodità.

    Raises
    ------
    ValueError
        Se uno dei parametri non rispetta i vincoli minimi (es. `max_concurrency < 1`,
        `retries < 0`, `backoff_base <= 0`, `backoff_factor < 1`, `jitter` fuori [0,1],
        `http_timeout_s <= 0`).
    """

    max_concurrency: int = 6
    retries: int = 0                 # total tries = retries + 1
    backoff_base: float = 0.5        # seconds
    backoff_factor: float = 2.0
    jitter: float = 0.25             # +/- fraction (0..1)
    # ttl_seconds: int = 6 * 3600    # per-day cache TTL (6h) – opzionale
    http_timeout_s: int = 20         # HTTP single request timeout

    def __post_init__(self) -> None:
        # Validazioni rapide per fail-fast su config errate
        if self.max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        if self.retries < 0:
            raise ValueError("retries must be >= 0")
        if self.backoff_base <= 0:
            raise ValueError("backoff_base must be > 0")
        if self.backoff_factor < 1:
            raise ValueError("backoff_factor must be >= 1")
        if not (0.0 <= self.jitter <= 1.0):
            raise ValueError("jitter must be between 0.0 and 1.0 (inclusive)")
        if self.http_timeout_s <= 0:
            raise ValueError("http_timeout_s must be > 0")

    @property
    def total_tries(self) -> int:
        """Numero totale di tentativi eseguiti in presenza di retry (prime try + retries)."""
        return self.retries + 1

# -----------------------------------------------------------------------------
# Utility locali (backoff, iter, sort, single-flight)
# -----------------------------------------------------------------------------

def _jittered_backoff(base: float, factor: float, attempt: int, jitter_frac: float) -> float:
    delay = base * (factor ** attempt)
    jitter = delay * jitter_frac
    return max(0.0, delay + random.uniform(-jitter, jitter))

def _forecast_sort_key(fc: Forecast) -> float:
    dtv = fc.get("datetime")
    if isinstance(dtv, str):
        parsed = dt_util.parse_datetime(dtv)
        if isinstance(parsed, datetime):
            return parsed.timestamp()
        return 0.0
    if isinstance(dtv, datetime):
        return dtv.timestamp()
    return 0.0


class _SingleFlight:
    """Deduplica richieste concorrenti per la stessa chiave (qui: date), race-safe."""
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._inflight: dict[date, asyncio.Future] = {}

    async def _cleanup_key(self, key: date) -> None:
        # Rimuove in modo sincronizzato la future dalla registry
        async with self._lock:
            self._inflight.pop(key, None)

    async def run(self, key: date, coro_factory):
        async with self._lock:
            fut = self._inflight.get(key)
            if fut is None or fut.cancelled():
                loop = asyncio.get_running_loop()
                fut = loop.create_future()
                self._inflight[key] = fut

                async def _runner():
                    try:
                        res = await coro_factory()
                        # Evita InvalidStateError se il waiter ha cancellato o è già completata
                        if not fut.cancelled() and not fut.done():
                            fut.set_result(res)
                    except asyncio.CancelledError:
                        # Se il runner viene cancellato, lascia la future com'è (eventuali waiter gestiranno)
                        if not fut.cancelled() and not fut.done():
                            fut.cancel()
                        raise
                    except Exception as e:
                        if not fut.cancelled() and not fut.done():
                            fut.set_exception(e)
                    finally:
                        # Cleanup fuori dal lock del run()
                        asyncio.create_task(self._cleanup_key(key))

                asyncio.create_task(_runner())

        # Tutti i chiamanti attendono la stessa future
        return await fut



            
# -----------------------------------------------------------------------------
# Provider HTTP con pattern anti-timeout
# -----------------------------------------------------------------------------
# --- CLIENT: una sola responsabilità -> fetch di UN giorno -------------------

# Assunti: esistono queste utility nel tuo progetto
# - _LOGGER: logging.Logger
# - _jittered_backoff(base: float, factor: float, attempt: int, jitter: float) -> float
# - as_float(x) -> Optional[float]
# - ratio_or_percent_to_int(x: Optional[float]) -> Optional[int]
# - Forecast: TypedDict | alias a dict[str, Any]
# - ProviderOptions con campi: http_timeout_s, retries, backoff_base, backoff_factor, jitter
# - PirateWeatherConfig con: api_key, lat, lon, units, base_url (opzionale)

class PirateWeatherDayClient:
    """
    Client HTTP per Pirate Weather Time Machine, responsabile della richiesta di un singolo giorno.

    Responsabilità:
    - Nessuna cache/single-flight/orchestrazione intervalli (demandata ad altri layer).
    - Retry con backoff+jitter su 429/5xx e su errori di rete/timeout.
    - Parsing del blocco `daily.data[0]` in un Forecast normalizzato.

    Contratti:
    - `Forecast` include SEMPRE la chiave "datetime" (ISO di inizio giorno locale).
    - Unità:
        * temperature: in unità fornite da Pirate Weather (coerenti con `units`)
        * humidity: percentuale intera [0..100]
        * wind_speed/wind_gust_speed: coerenti con `units`
        * precipitation_accumulation: se derivato da `precipIntensity` ed `units == "us"`,
          il valore viene convertito in mm (SI) per coerenza interna (documenta nel tuo progetto).
    - Ritorno: (forecast|None, retry_after_s|None). Se None, `retry_after_s` può suggerire un cooldown esterno.
    """

    NON_RETRYABLE_STATUSES = {400, 401, 403, 404, 405, 406, 409, 410, 411, 413, 414, 415, 422, 501, 505}
    RETRYABLE_STATUSES = {429, 500, 502, 503, 504}

    def __init__(self, cfg: PirateWeatherConfig, opts: ProviderOptions | None = None) -> None:
        self._cfg = cfg
        self._opts = opts or ProviderOptions()
        self._aiohttp = None  # import lazy
        self._session: Any = None

    async def __aenter__(self) -> "PirateWeatherDayClient":
        await self.ensure_session()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close_session()

    async def ensure_session(self) -> None:
        if self._aiohttp is None:
            try:
                self._aiohttp = importlib.import_module("aiohttp")
            except Exception as e:
                raise RuntimeError("aiohttp non disponibile: installa la dipendenza") from e

        if self._session is None or getattr(self._session, "closed", False):
            timeout = getattr(self._aiohttp, "ClientTimeout")(total=self._opts.http_timeout_s)
            self._session = getattr(self._aiohttp, "ClientSession")(timeout=timeout)

    async def close_session(self) -> None:
        if self._session is not None:
            try:
                await self._session.close()
            finally:
                self._session = None

    def _build_url(self, when: datetime) -> str:
        # Default coerente col Time Machine
        base = self._cfg.base_url or "https://timemachine.pirateweather.net/forecast"
        iso = when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        return f"{base}/{self._cfg.api_key}/{self._cfg.lat},{self._cfg.lon},{iso}"

    @staticmethod
    def _local_day_iso(d: date) -> str:
        if dt_util is not None:
            return dt_util.start_of_local_day(datetime(d.year, d.month, d.day)).isoformat()
        # Fallback: mezzanotte naive (specifica nelle tue API se serve TZ)
        return datetime(d.year, d.month, d.day).isoformat()

    @staticmethod
    def _parse_retry_after(value: Optional[str]) -> float:
        """Supporta sia secondi (es. '120') che HTTP-date. Ritorna 0.0 se non parsabile."""
        if not value:
            return 0.0
        # 1) Tentativo numerico
        try:
            sec = float(value)
            if sec >= 0:
                return sec
        except Exception:
            pass
        # 2) HTTP-date
        try:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(value)
            if dt is not None:
                now = datetime.now(timezone.utc)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                delta = (dt - now).total_seconds()
                return max(0.0, delta)
        except Exception:
            pass
        return 0.0

    @staticmethod
    def _parse_daily_payload(d: date, payload: dict[str, Any], *, units: str) -> Optional[Forecast]:
        daily = payload.get("daily")
        if not isinstance(daily, dict):
            return None
        data = daily.get("data")
        if not isinstance(data, list) or not data or not isinstance(data[0], dict):
            return None

        di = data[0]
        tmin = as_float(di.get("temperatureMin"))
        tmax = as_float(di.get("temperatureMax"))
        dp   = as_float(di.get("dewPoint"))
        rh   = ratio_or_percent_to_int(as_float(di.get("humidity")))
        ws   = as_float(di.get("windSpeed"))
        wg   = as_float(di.get("windGust"))
        uvi  = as_float(di.get("uvIndex"))
        pacc = as_float(di.get("precipAccumulation"))

        if pacc is None:
            # Deriva da intensità * 24 (unità native del provider)
            pi = as_float(di.get("precipIntensity"))
            if pi is not None:
                # se units = 'us', intensità è in inches/hour -> converti a mm/day
                if units == "us":
                    pacc = pi * 24.0 * 25.4  # in/day -> mm/day
                else:
                    pacc = pi * 24.0  # mm/day

        base: dict[str, Any] = {"datetime": PirateWeatherDayClient._local_day_iso(d)}
        optional = {
            "temp_max": float(tmax) if tmax is not None else None,
            "temp_min": float(tmin) if tmin is not None else None,
            "dew_point": float(dp) if dp is not None else None,
            "humidity": rh,
            "wind_speed": float(ws) if ws is not None else None,
            "wind_gust_speed": float(wg) if wg is not None else None,
            "uv_index": float(uvi) if uvi is not None else None,
            "precipitation_accumulation": float(pacc) if pacc is not None else None,
        }
        base.update({k: v for k, v in optional.items() if v is not None})
        return cast(Forecast, base)

    async def fetch_day(self, d: date) -> Tuple[Optional[Forecast], Optional[float]]:
        """
        Esegue la chiamata HTTP con retry/backoff.

        Returns:
            (forecast | None, retry_after_s | None)

        Note:
            - Non ritenta su status non recuperabili (es. 401/403/404...).
            - In caso di `200` ma payload invalido, ritenta (dati non pronti o day fuori copertura).
            - `retry_after_s` deriva da header `Retry-After` (secondi o HTTP-date), solo se non c'è un risultato.
        """
        await self.ensure_session()
        assert self._session is not None

        start_t = time.perf_counter()
        res: Optional[Forecast] = None
        last_retry_after_s: float = 0.0
        status: int | str = "n/a"
        url: str = ""

        for attempt in range(self._opts.retries + 1):
            try:
                when = datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=timezone.utc)
                url = self._build_url(when)
                params = {
                    "units": self._cfg.units,
                    "exclude": "hourly,minutely,alerts,flags,currently",
                    # "lang": "it",  # opzionale, se ti serve
                }
                headers = {"Accept": "application/json"}

                async with self._session.get(url, params=params, headers=headers) as resp:
                    status = resp.status
                    if status == 200:
                        payload: dict[str, Any] = await resp.json()
                        res = self._parse_daily_payload(d, payload, units=self._cfg.units)
                        if res is not None:
                            break  # successo
                        # altrimenti ritenta (payload mancante/strano)
                    elif status in self.RETRYABLE_STATUSES:
                        ra = self._parse_retry_after(resp.headers.get("Retry-After"))
                        last_retry_after_s = max(last_retry_after_s, ra)
                        # ultimo tentativo? solo logga, non dorme oltre
                        if attempt >= self._opts.retries:
                            _LOGGER.warning("PW daily(%s) %s; Retry-After=%.1fs url=%s", d, status, ra, url)
                            break
                        sleep_s = max(ra, _jittered_backoff(self._opts.backoff_base,
                                                            self._opts.backoff_factor,
                                                            attempt, self._opts.jitter))
                        _LOGGER.debug("PW daily(%s) retryable %s attempt=%d sleep=%.2fs", d, status, attempt, sleep_s)
                        await asyncio.sleep(sleep_s)
                        continue
                    elif status in self.NON_RETRYABLE_STATUSES:
                        _LOGGER.warning("PW daily(%s) non-retryable status %s url=%s", d, status, url)
                        break
                    else:
                        # status sconosciuti -> un retry conservativo
                        if attempt >= self._opts.retries:
                            _LOGGER.warning("PW daily(%s) unexpected status %s url=%s", d, status, url)
                            break
                        sleep_s = _jittered_backoff(self._opts.backoff_base,
                                                    self._opts.backoff_factor,
                                                    attempt, self._opts.jitter)
                        _LOGGER.debug("PW daily(%s) unexpected %s attempt=%d sleep=%.2fs", d, status, attempt, sleep_s)
                        await asyncio.sleep(sleep_s)
                        continue

            except asyncio.TimeoutError as e:
                status = "timeout"
                if attempt >= self._opts.retries:
                    _LOGGER.warning("PW daily(%s) timeout: %r url=%s", d, e, url)
                    break
                sleep_s = _jittered_backoff(self._opts.backoff_base, self._opts.backoff_factor, attempt, self._opts.jitter)
                _LOGGER.debug("PW daily(%s) timeout attempt=%d sleep=%.2fs", d, attempt, sleep_s)
                await asyncio.sleep(sleep_s)
            except Exception as e:
                # errori di rete/decoding ecc. (include aiohttp.ClientError se presente)
                if attempt >= self._opts.retries:
                    status = getattr(e, "__class__", type(e)).__name__
                    _LOGGER.warning("PW daily(%s) exception (%s): %r url=%s", d, status, e, url, exc_info=True)
                    break
                sleep_s = _jittered_backoff(self._opts.backoff_base, self._opts.backoff_factor, attempt, self._opts.jitter)
                _LOGGER.debug("PW daily(%s) exception %s attempt=%d sleep=%.2fs", d, type(e).__name__, attempt, sleep_s)
                await asyncio.sleep(sleep_s)

        _LOGGER.debug(
            "PW daily(%s): status=%s elapsed=%.1fms ok=%s %s",
            d, status, (time.perf_counter() - start_t) * 1000, bool(res), ("" if res else url),
        )

        return res, (last_retry_after_s if res is None and last_retry_after_s > 0 else None)



# --- MANAGER: orchestrazione cache/range/single-flight/concurrency -----------

class PirateWeatherHistorical(WeatherHistoricalProvider):
    """
    Orchestratore storico con policy di cache/persistenza e gestione range.
    Delega la singola chiamata HTTP a PirateWeatherDayClient.

    Policy finestra 48h:
      - Non tenta mai il fetch di un giorno D prima di (fine D locale + 48h).
      - Dopo QUALSIASI tentativo (ok/ko), il prossimo tentativo per quel giorno
        è consentito solo nella prossima finestra ≥ 48h dopo.
      - Retry-After del provider viene rispettato ma non anticipa la finestra (si usa max()).
    """

    _MUTABLE_DAYS = 1          # giorni che possono cambiare (oggi; metti 2 per includere ieri)
    _WINDOW_HOURS = 48         # ampiezza finestra per tentativi per-day

    def __init__(self, hass: HomeAssistant, cfg: PirateWeatherConfig, opts: ProviderOptions | None = None) -> None:
        self._hass = hass
        self._cfg = cfg
        self._opts = opts or ProviderOptions()

        # Client HTTP single-day (own session)
        self._client = PirateWeatherDayClient(cfg, self._opts)

        # Concorrenza & dedup
        self._sem = asyncio.Semaphore(self._opts.max_concurrency)
        self._sf = _SingleFlight()

        # Metadati in RAM
        self._neg_cache: Dict[str, float] = {}     # day ISO -> retry-allowed-after ts

        # Store key robusta (dipende anche da units/base_url)
        key = self._make_store_key(cfg)
        self._data = PersistentForecastCache(hass, key=key, version=1, max_days=1095)  # 3 anni
        self._store: Store = Store(hass, version=1, key=f"{key}.meta") 

    # ---------- context ----------
    async def __aenter__(self) -> "PirateWeatherHistorical":
        await self._client.ensure_session()
        await self._data.async_load()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        try:
            await self._data.async_save()
        finally:
            await self._close_session()

    async def _close_session(self) -> None:
        await self._client.close_session()

    # ---------- helpers chiave store ----------
    @staticmethod
    def _date_iter(start: date, end: date):
        d = start
        while d <= end:
            yield d
            d += timedelta(days=1)

    @staticmethod
    def _make_store_key(cfg: PirateWeatherConfig) -> str:
        return f"{DOMAIN}.pirateweather.historical.{cfg.units}.{cfg.lat:.4f}.{cfg.lon:.4f}"

    # ---------- policy mutabilità/refresh & negative cache ----------
    def _is_mutable_day(self, d: date) -> bool:
        """Rende mutabili gli ultimi `_MUTABLE_DAYS` giorni, dove 1 = solo oggi."""
        today = dt_util.now().date()
        delta = (today - d).days
        return 0 <= delta < self._MUTABLE_DAYS

    def _should_refresh_now(self, d: date, fetched_at: Optional[float] = None) -> bool:
        """
        (Legacy) Per i giorni mutabili, consenti al massimo un refresh per giornata locale.
        NB: la policy finestra da 48h prevale comunque e blocca i tentativi fuori finestra.
        """
        if not self._is_mutable_day(d):
            return False
        if not fetched_at:
            return True
        last_local_day = dt_util.as_local(datetime.fromtimestamp(fetched_at)).date()
        today_local = dt_util.now().date()
        return last_local_day < today_local

    def _neg_can_query(self, d: date) -> bool:
        return time.time() >= self._neg_cache.get(d.isoformat(), 0.0)

    def _neg_backoff(self, d: date, *, seconds: int | None = None, minutes: int = 20) -> None:
        delay = seconds if seconds is not None else minutes * 60
        self._neg_cache[d.isoformat()] = time.time() + max(1, delay)

    # ---------- calcolo finestre 48h ----------
    def _end_of_local_day(self, d: date) -> datetime:
        start = dt_util.start_of_local_day(datetime(d.year, d.month, d.day))
        return start + timedelta(days=1)

    def _ready_at_local(self, d: date) -> datetime:
        # Prima possibilità di interrogare quel giorno:
        # fine del giorno (D+1 alle 00:00 locali) + 48h
        return self._end_of_local_day(d) + timedelta(hours=self._WINDOW_HOURS)

    def _too_early_to_fetch(
        self,
        d: date,
        *,
        last_attempt_ts: float | None = None,
        retry_after_s: float | None = None,
    ) -> tuple[bool, float]:
        """
        Applica i vincoli:
          - ready_at (fine giorno + 48h)
          - last_attempt + 48h (finestra successiva)
          - now + Retry-After (se presente)
        Ritorna (too_early, seconds_until_ok).
        """
        now = dt_util.now()
        gates = [self._ready_at_local(d)]

        if last_attempt_ts and last_attempt_ts > 0:
            gates.append(datetime.fromtimestamp(last_attempt_ts, tz=now.tzinfo) + timedelta(hours=self._WINDOW_HOURS))

        if retry_after_s and retry_after_s > 0:
            gates.append(now + timedelta(seconds=retry_after_s))

        allow_at = max(gates)
        delta = (allow_at - now).total_seconds()
        if delta > 0:
            return True, delta
        return False, 0.0

    def _neg_until_next_window(
        self,
        d: date,
        *,
        last_attempt_ts: float | None = None,
        retry_after_s: float | None = None,
    ) -> None:
        """
        Imposta la negative-cache fino al prossimo momento consentito.
        Se già in finestra (too_early=False) forza comunque una pausa di 48h.
        """
        too_early, wait_s = self._too_early_to_fetch(
            d, last_attempt_ts=last_attempt_ts, retry_after_s=retry_after_s
        )
        if not too_early:
            wait_s = max(wait_s, self._WINDOW_HOURS * 3600)
        self._neg_backoff(d, seconds=int(max(1, wait_s)))

    async def _select_misses_and_collect_cached(
        self, days: list[date]
    ) -> tuple[list[Forecast], list[date]]:
        """Prima passata: usa cache e determina i giorni candidati 'misses' per il giro corrente."""
        results: list[Forecast] = []
        misses: list[date] = []

        for d in days:
            fc = await self._data.async_get(d)
            # Giorni non mutabili: usa solo cache (se c'è) e non aggiornare
            if fc and not self._is_mutable_day(d):
                results.append(fc)
                continue

            # Negative-cache attiva → non interrogare ora
            if not self._neg_can_query(d):
                if fc:
                    results.append(fc)
                continue

            # Gate 48h dalla fine del giorno (+ eventuale last attempt / Retry-After)
            too_early, wait_s = self._too_early_to_fetch(d)
            if too_early:
                self._neg_backoff(d, seconds=int(wait_s))
                if fc:
                    results.append(fc)
                continue

            # Candidato per questo giro
            misses.append(d)

        return results, misses


    # ---------- public API ----------
    # async def daily(self, day: date) -> Optional[Forecast]:
    #     """
    #     Cache-first con policy a finestra 48h.
    #     - Giorni non mutabili: mai refresh (solo cache persistente).
    #     - Gate: non tenta prima di (fine giorno + 48h).
    #     - Dopo QUALSIASI tentativo, chiude la finestra per ≥48h (o Retry-After se maggiore).
    #     """
    #     await self._data.async_load()

    #     fc = await self._data.async_get(day)
    #     # fetched_at mantenuto per legacy/telemetria; la policy finestra prevale
    #     fetched_at = float(fc.get("_meta_fetched_at", 0.0)) if fc else 0.0  # noqa: F841

    #     # Giorni non mutabili: mai refresh
    #     if fc and not self._is_mutable_day(day):
    #         return fc

    #     # Negative-cache attiva → non interrogare ora
    #     if not self._neg_can_query(day):
    #         return fc

    #     # Gate 48h dalla fine del giorno + (eventuale) ultimo tentativo
    #     too_early, wait_s = self._too_early_to_fetch(day)
    #     if too_early:
    #         self._neg_backoff(day, seconds=int(wait_s))
    #         return fc

    #     async def _do():
    #         async with self._sem:
    #             return await self._client.fetch_day(day)

    #     try:
    #         fetched, retry_after = await self._sf.run(day, _do)  # dedup per-day
    #     except asyncio.CancelledError:
    #         return fc

    #     # Qualsiasi esito → imposta neg-cache fino alla prossima finestra
    #     now_ts = time.time()
    #     self._neg_until_next_window(day, last_attempt_ts=now_ts, retry_after_s=retry_after)

    #     if fetched:
    #         annotated = cast(Forecast, {**fetched, "_meta_fetched_at": now_ts, "_meta_day": day.isoformat()})
    #         await self._data.async_put(day, annotated)
    #         return annotated

    #     # Fallita: mantieni cache esistente (se c'è)
    #     return fc

    async def _fetch_day_with_dedup(
        self, day: date
    ) -> tuple[Optional[Forecast], Optional[float]]:
        """Esegue un singolo fetch (dedup + semaforo). Ritorna (forecast, retry_after)."""
        async def _do():
            async with self._sem:
                return await self._client.fetch_day(day)

        try:
            return await self._sf.run(day, _do)
        except asyncio.CancelledError:
            return None, None
        
    async def _run_chunk(
        self,
        chunk: list[date],
        timeout_s: float
    ) -> list[tuple[date, Optional[Forecast], Optional[float]]]:
        """
        Esegue in parallelo il fetch di un 'chunk' di giorni con timeout per-batch.

        IN
        ---
        - chunk: list[date]
            Giorni da processare nel batch corrente. L’ordine non è rilevante.
            (La concorrenza effettiva resta limitata dal Semaphore dentro _fetch_day_with_dedup.)

        - timeout_s: float
            Timeout massimo (secondi) per l’intero batch. Se scade, i task rimanenti vengono cancellati.

        OUT
        ----
        - list[tuple[date, Optional[Forecast], Optional[float]]]
            Una tupla per **ogni task completato entro il timeout**:
            ( day, fetched_or_none, retry_after_seconds_or_none )
            Dove:
            * fetched_or_none: risultato del provider (non annotato) oppure None in caso di errore.
            * retry_after_seconds_or_none: finestra 'Retry-After' suggerita dal provider, altrimenti None.

            NOTE:
            * Non c’è una tupla per i task cancellati perché pendenti allo scadere del timeout.
            * L’ordine delle tuple NON è garantito (dipende dall’esito di asyncio.wait).

        Eccezioni
        ---------
        - Propaga `asyncio.CancelledError` se la cancellazione avviene a livello di batch
        (i task creati vengono prima cancellati).
        """
        # Edge case: timeout non positivo → nessun tentativo effettivo
        if timeout_s <= 0 or not chunk:
            return []

        # Crea i task (la concorrenza reale è limitata internamente da _fetch_day_with_dedup)
        tasks: dict[asyncio.Task, date] = {
            asyncio.create_task(self._fetch_day_with_dedup(d)): d for d in chunk
        }

        try:
            done, pending = await asyncio.wait(tasks.keys(), timeout=timeout_s)
        except asyncio.CancelledError:
            # Se il batch viene cancellato, cancella tutti i task e propaga
            for t in tasks:
                t.cancel()
            # Draina comunque per evitare warning
            await asyncio.gather(*tasks.keys(), return_exceptions=True)
            raise

        # Cancella i pendenti allo scadere del timeout e draina eccezioni/Cancelled
        for p in pending:
            p.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        out: list[tuple[date, Optional[Forecast], Optional[float]]] = []
        for t in done:
            d = tasks[t]
            try:
                fetched, rafter = t.result()  # type: ignore[assignment]
                out.append((d, fetched, rafter))
            except Exception:
                # Errore nel singolo task → nessun dato nuovo per quel giorno
                out.append((d, None, None))

        return out

    async def _finalize_attempt(
        self,
        d: date,
        fetched: Optional[Forecast],
        retry_after_s: Optional[float],
    ) -> tuple[Optional[Forecast], Optional[Forecast]]:
        """
        Applica subito la chiusura della finestra (neg-cache) per il giorno d,
        annota il forecast se presente e calcola l'eventuale fallback da cache.
        Ritorna (annotated_or_none, fallback_or_none).
        """
        now_ts = time.time()
        self._neg_until_next_window(d, last_attempt_ts=now_ts, retry_after_s=retry_after_s)

        if isinstance(fetched, dict) and "datetime" in fetched:
            annotated: Forecast = cast(Forecast, {**fetched, "_meta_fetched_at": now_ts, "_meta_day": d.isoformat()})
            return annotated, None

        # Nessun dato nuovo → prova il fallback dalla cache persistente
        fallback = await self._data.async_get(d)
        return None, fallback
    
    async def _persist_new_items(self, items: list[tuple[date, Forecast]]) -> None:
        """Salva solo i forecast nuovi/aggiornati."""
        for d, fc in items:
            await self._data.async_put(d, fc)

    async def daily_range(
        self,
        start: date,
        end: date,
        *,
        batch_size: int = 5,
        per_batch_timeout: float = 10.0,
        inter_batch_sleep: float = 0.25,
        total_budget_s: float = 25.0,
    ) -> List[Forecast]:
        """
        Recupera [start..end] privilegiando cache e limitando la rete con policy a finestra 48h.
        - Giorni non mutabili: mai refresh (solo cache persistente).
        - Giorni “eleggibili” per il giro: solo se dopo (fine giorno + 48h) e non in neg-cache.
        - Dopo QUALSIASI tentativo su un giorno, chiudi la finestra di quel giorno per ≥48h.
        """
        # await self._client.ensure_session()
        await self._data.async_load()
        meta = await self._store.async_load() or {}

        if len(meta) == 0 or meta.get("last_fetch") != dt_util.now().date().isoformat():
            meta = {"last_fetch": date.today()}
            await self._store.async_save(meta)

        log_debug(_LOGGER, "Window [%s..%s] start now", start, end)

        days = [d for d in self._date_iter(start, end)]
        results, misses = await self._select_misses_and_collect_cached(days)

        # log_debug(_LOGGER, "%s %s", meta.get("last_fetch"), meta.get("last_fetch") == dt_util.now().date().isoformat())
        if not misses or meta.get("last_fetch") == dt_util.now().date().isoformat():
            results.sort(key=_forecast_sort_key)
            log_debug(_LOGGER, "Window [%s..%s] end: returning %d results (misses=%s)", start, end, len(results), len(misses))
            return results

        t0 = time.perf_counter()
        new_items: list[tuple[date, Forecast]] = []

        try:
            for i in range(0, len(misses), batch_size):
                # Budget residuo globale
                elapsed = time.perf_counter() - t0
                remaining_budget = total_budget_s - elapsed
                if remaining_budget <= 0:
                    _LOGGER.warning(
                        "PW daily_range: budget esaurito (%.1fs), ritorno %d/%d",
                        total_budget_s, len(results), len(days)
                    )
                    break

                chunk = misses[i : i + batch_size]
                chunk_timeout = min(per_batch_timeout, max(0.0, remaining_budget))

                # Esecuzione chunk (parallelo) con timeout per-batch
                batch_out = await self._run_chunk(chunk, chunk_timeout)

                # Post-processing risultati chunk
                for d, fetched, rafter in batch_out:
                    annotated, fallback = await self._finalize_attempt(d, fetched, rafter)

                    if annotated is not None:
                        results.append(annotated)
                        new_items.append((d, annotated))
                    elif fallback is not None:
                        results.append(fallback)
                    # altrimenti: nessun dato per quel giorno

                # Respiro tra i chunk (se richiesto)
                if inter_batch_sleep > 0:
                    await asyncio.sleep(inter_batch_sleep)

        except asyncio.CancelledError:
            _LOGGER.warning(
                "PW daily_range cancellata (elapsed=%.1fs, parziali=%d/%d)",
                time.perf_counter() - t0, len(results), len(days)
            )
        finally:
            # salva solo ciò che è nuovo/aggiornato
            await self._persist_new_items(new_items)

        results.sort(key=_forecast_sort_key)
        _LOGGER.debug(
            "PW daily_range [%s..%s] end: returning %d results (misses=%d), elapsed=%.1f ms",
            start, end, len(results), len(misses), (time.perf_counter() - t0) * 1000.0
        )
        return results




    async def daily_range_old(
        self,
        start: date,
        end: date,
        *,
        batch_size: int = 5,
        per_batch_timeout: float = 10.0,
        inter_batch_sleep: float = 0.25,
        total_budget_s: float = 25.0,
    ) -> List[Forecast]:
        """
        Recupera [start..end] privilegiando cache e limitando la rete con policy a finestra 48h.
        - Giorni non mutabili: mai refresh (solo cache persistente).
        - Giorni “eleggibili” per il giro: solo se dopo (fine giorno + 48h) e non in neg-cache.
        - Dopo QUALSIASI tentativo su un giorno, chiudi la finestra di quel giorno per ≥48h.
        """
        # await self._client.ensure_session()
        await self._data.async_load()

        log_debug(_LOGGER, "Window [%s..%s] start now", start, end)

        days = [d for d in self._date_iter(start, end)] 
        results: List[Forecast] = []
        misses: List[date] = []

        # 1) Prima passata: usa cache e decidi cosa davvero manca/va provato in questo giro
        for d in days:
            fc = await self._data.async_get(d)
            fetched_at = float(fc.get("_meta_fetched_at", 0.0)) if fc else 0.0  # noqa: F841

            # non mutabile → prendi cache e non aggiornare
            if fc and not self._is_mutable_day(d):
                results.append(fc)
                continue

            # negative-cache attiva → non interrogare ora (usa eventuale cache)
            if not self._neg_can_query(d):
                if fc:
                    results.append(fc)
                continue

            # Gate 48h dalla fine del giorno (+ last attempt/Retry-After se presenti)
            too_early, wait_s = self._too_early_to_fetch(d)
            if too_early:
                self._neg_backoff(d, seconds=int(wait_s))
                if fc:
                    results.append(fc)
                continue

            # è un candidato per questo giro
            misses.append(d)

        if not misses:
            results.sort(key=_forecast_sort_key)
            return results

        # 2) Fetch concorrente con dedup e budget
        async def _fetch_one(day: date) -> Tuple[Optional[Forecast], Optional[float]]:
            async def _do():
                async with self._sem:
                    return await self._client.fetch_day(day)
            try:
                return await self._sf.run(day, _do)
            except asyncio.CancelledError:
                return None, None

        t0 = time.perf_counter()
        new_items: List[Tuple[date, Forecast]] = []

        try:
            for i in range(0, len(misses), batch_size):
                elapsed = time.perf_counter() - t0
                if total_budget_s - elapsed <= 0:
                    _LOGGER.warning(
                        "PW daily_range: budget esaurito (%.1fs), ritorno %d/%d",
                        total_budget_s, len(results), len(days)
                    )
                    break

                chunk = misses[i: i + batch_size]

                async def _batch() -> List[Tuple[date, Optional[Forecast], Optional[float]]]:
                    async def _guarded(d: date) -> Tuple[date, Optional[Forecast], Optional[float]]:
                        try:
                            fetched, rafter = await _fetch_one(d)
                            return d, fetched, rafter
                        except asyncio.CancelledError:
                            return d, None, None
                        except BaseException as e:
                            _LOGGER.debug("PW daily_range: errore chunk item(%s): %s", d, e)
                            return d, None, None

                    tasks_map: Dict[asyncio.Task, date] = {
                        asyncio.create_task(_guarded(d)): d for d in chunk
                    }

                    chunk_timeout = min(per_batch_timeout, max(0.0, total_budget_s - (time.perf_counter() - t0)))
                    try:
                        done, pending = await asyncio.wait(tasks_map.keys(), timeout=chunk_timeout)
                    except asyncio.CancelledError:
                        for t in tasks_map.keys():
                            t.cancel()
                        raise

                    for p in pending:
                        p.cancel()

                    out: List[Tuple[date, Optional[Forecast], Optional[float]]] = []
                    for t in done:
                        try:
                            out.append(t.result())
                        except BaseException:
                            out.append((tasks_map[t], None, None))
                    return out

                for d, fc, rafter in await _batch():
                    # CHIUDI SUBITO LA FINESTRA PER QUESTO GIORNO, indipendentemente dall’esito
                    now_ts = time.time()
                    self._neg_until_next_window(d, last_attempt_ts=now_ts, retry_after_s=rafter)

                    if isinstance(fc, dict) and "datetime" in fc:
                        annotated = cast(Forecast, {**fc, "_meta_fetched_at": now_ts, "_meta_day": d.isoformat()})
                        results.append(annotated)
                        new_items.append((d, annotated))
                    else:
                        # fallback a cache esistente se presente
                        fallback = await self._data.async_get(d)
                        if fallback:
                            results.append(fallback)

                await asyncio.sleep(inter_batch_sleep)

        except asyncio.CancelledError:
            _LOGGER.warning(
                "PW daily_range cancellata (elapsed=%.1fs, parziali=%d/%d)",
                time.perf_counter() - t0, len(results), len(days)
            )
        finally:
            # salva solo ciò che è nuovo/aggiornato
            for d, fc in new_items:
                await self._data.async_put(d, fc)
            # opzionale: flush immediato
            # await self._data.async_save()

        results.sort(key=_forecast_sort_key)
        _LOGGER.debug(
            "PW daily_range [%s..%s] end: returning %d results (misses=%d), elapsed=%.1f ms",
            start, end, len(results), len(misses), (time.perf_counter() - t0) * 1000.0
        )
        return results


# =============================================================================
# Factory
# =============================================================================

def get_pirateweather_historical_provider(
    hass: HomeAssistant,
    cfg: PirateWeatherConfig,
    *,
    opts: ProviderOptions | None = None
) -> WeatherHistoricalProvider:
    """pirateweather HTTP."""

    # Opzioni di robustezza/performance
    _opts = ProviderOptions(
        max_concurrency=6,    # limita richieste/conversioni in parallelo
        retries=2,            # tentativi aggiuntivi (totale = retries+1)
        backoff_base=0.5,
        backoff_factor=2.0,
        jitter=0.25,
        # ttl_seconds=6*3600,   # cache per-day (6h)
        http_timeout_s=20,
    )

    return PirateWeatherHistorical(hass, cfg, opts=opts or _opts)
