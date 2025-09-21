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
from homeassistant.util import dt as dt_util

from ..helpers.cache import PersistentCache

from ..const import DOMAIN

from ..helpers.utils import as_float, ratio_or_percent_to_int
from .provider import WeatherHistoricalProvider

_LOGGER = logging.getLogger(__name__)

# =============================================================================
# Shared model & interfaces
# =============================================================================
class _TTLCache:
    """Simple per-key TTL cache for Forecast."""
    __slots__ = ("_ttl", "_data")

    def __init__(self, ttl_seconds: int) -> None:
        self._ttl = ttl_seconds
        self._data: dict[date, Tuple[float, Forecast]] = {}

    def get(self, key: date) -> Optional[Forecast]:
        entry = self._data.get(key)
        if not entry:
            return None
        exp_ts, val = entry
        if time.time() > exp_ts:
            # expired
            self._data.pop(key, None)
            return None
        return val

    def set(self, key: date, value: Forecast) -> None:
        self._data[key] = (time.time() + self._ttl, value)

    def clear(self) -> None:
        self._data.clear()

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


def _date_iter(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


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

class PirateWeatherDayClient:
    """
    Client HTTP per Pirate Weather Time Machine, responsabile SOLO della
    richiesta di un singolo giorno (retry + backoff + parsing). Non gestisce
    cache, cooldown, single-flight o orchestrazione di range.
    """

    def __init__(self, cfg: PirateWeatherConfig, opts: ProviderOptions | None = None) -> None:
        self._cfg = cfg
        self._opts = opts or ProviderOptions()
        self._aiohttp = importlib.import_module("aiohttp")
        self._session: Any = None

    async def ensure_session(self) -> None:
        if self._session is None:
            timeout = getattr(self._aiohttp, "ClientTimeout")(total=self._opts.http_timeout_s)
            self._session = getattr(self._aiohttp, "ClientSession")(timeout=timeout)

    async def close_session(self) -> None:
        if self._session is not None:
            try:
                await self._session.close()
            finally:
                self._session = None

    def _build_url(self, when: datetime) -> str:
        base = self._cfg.base_url or "https://api.pirateweather.net/forecast"
        iso = when.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
        return f"{base}/{self._cfg.api_key}/{self._cfg.lat},{self._cfg.lon},{iso}"

    @staticmethod
    def _parse_daily_payload(d: date, payload: dict[str, Any]) -> Optional[Forecast]:
        daily = payload.get("daily")
        if not isinstance(daily, dict):
            return None
        data = daily.get("data")
        if not isinstance(data, list) or not data:
            return None
        di = data[0]
        if not isinstance(di, dict):
            return None

        tmin = as_float(di.get("temperatureMin"))
        tmax = as_float(di.get("temperatureMax"))
        dp   = as_float(di.get("dewPoint"))
        rh   = ratio_or_percent_to_int(as_float(di.get("humidity")))
        ws   = as_float(di.get("windSpeed"))
        wg   = as_float(di.get("windGust"))
        uvi  = as_float(di.get("uvIndex"))
        pacc = as_float(di.get("precipAccumulation"))
        if pacc is None:
            pi = as_float(di.get("precipIntensity"))
            pacc = pi * 24.0 if pi is not None else None

        dt_local = dt_util.start_of_local_day(datetime(d.year, d.month, d.day)).isoformat()

        base: dict[str, Any] = {"datetime": dt_local}
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
        Esegue la chiamata HTTP con retry/backoff. Ritorna:
          (forecast | None, retry_after_s | None)
        Se fallisce, retry_after_s (se presente) suggerisce il backoff negativo.
        """
        await self.ensure_session()
        assert self._session is not None

        start_t = time.perf_counter()
        res: Optional[Forecast] = None
        last_retry_after_s: float = 0.0

        for attempt in range(self._opts.retries + 1):
            try:
                when = datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=timezone.utc)
                url = self._build_url(when)
                params = {"units": self._cfg.units, "exclude": "hourly,minutely,alerts,flags,currently"}

                async with self._session.get(url, params=params) as resp:
                    status = resp.status
                    if status == 200:
                        payload: dict[str, Any] = await resp.json()
                        res = self._parse_daily_payload(d, payload)
                        if res is not None:
                            break
                    elif status in (429, 500, 502, 503, 504):
                        retry_after = 0.0
                        try:
                            ra = resp.headers.get("Retry-After")
                            if ra:
                                retry_after = float(ra)
                        except Exception:
                            retry_after = 0.0
                        last_retry_after_s = max(last_retry_after_s, retry_after)

                        if attempt >= self._opts.retries:
                            _LOGGER.warning("PW HTTP daily(%s) failed with %s; retry-after=%s", d, status, retry_after)
                        await asyncio.sleep(max(
                            retry_after,
                            _jittered_backoff(self._opts.backoff_base, self._opts.backoff_factor, attempt, self._opts.jitter)
                        ))
                        continue
                    else:
                        if attempt >= self._opts.retries:
                            _LOGGER.warning("PW HTTP daily(%s) unexpected status %s", d, status)
                        await asyncio.sleep(_jittered_backoff(
                            self._opts.backoff_base, self._opts.backoff_factor, attempt, self._opts.jitter
                        ))
                        continue

            except TimeoutError as e:
                status = "timeout"
                if attempt >= self._opts.retries:
                    _LOGGER.warning("PW HTTP daily(%s) timeout: %r %s", d, e, url)
                await asyncio.sleep(_jittered_backoff(
                    self._opts.backoff_base, self._opts.backoff_factor, attempt, self._opts.jitter
                ))
            except Exception as e:
                if attempt >= self._opts.retries:
                    _LOGGER.warning(
                        "PW HTTP daily(%s) exception (%s): %r",
                        d, e.__class__.__name__, e,
                        exc_info=True,   # <— stacktrace completo
                    )
                await asyncio.sleep(_jittered_backoff(
                    self._opts.backoff_base, self._opts.backoff_factor, attempt, self._opts.jitter
                ))

        _LOGGER.debug(
            "PW HTTP daily (%s): %s %.1f ms (ok=%s) %s",
            d,
            status, 
            (time.perf_counter() - start_t) * 1000,
            bool(res), url if not bool(res) else ""
        )
        return res, (last_retry_after_s if res is None and last_retry_after_s > 0 else None)


# --- MANAGER: orchestrazione cache/range/single-flight/concurrency -----------

class PirateWeatherHistorical(WeatherHistoricalProvider):
    """
    Orchestratore storico con policy di cache/persistenza e gestione range.
    Delega la singola chiamata HTTP a PirateWeatherDayClient.
    """

    _MUTABLE_DAYS = 1          # giorni che possono cambiare (oggi; metti 2 per includere ieri)
    # _MIN_REFRESH_HOURS = 6     # frequenza minima di refresh per giorni mutabili

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
        # self._last_refresh: Dict[str, float] = {}  # day ISO -> last ts
        self._neg_cache: Dict[str, float] = {}     # day ISO -> retry-allowed-after ts

        # Store key robusta (dipende anche da units/base_url)
        key = self._make_store_key(cfg)
        self._data = PersistentCache(hass, key=key, version=1, max_days=1095)  # 3 anni

    # ---------- context ----------
    async def __aexit__(self, exc_type, exc, tb) -> None:
        try:
            await self._data.async_save()
        finally:
            await self._close_session()

    # ---------- helpers chiave store ----------
    @staticmethod
    def _make_store_key(cfg: PirateWeatherConfig) -> str:
        import hashlib
        return f"{DOMAIN}.pirateweather.historical.{cfg.units}.{cfg.lat:.4f}.{cfg.lon:.4f}"

    # ---------- context ----------
    async def __aenter__(self) -> "PirateWeatherHistorical":
        await self._client.ensure_session()
        await self._data.async_load()
        return self

    async def _close_session(self) -> None:
        await self._client.close_session()

    # ---------- policy mutabilità/refresh & negative cache ----------
    def _is_mutable_day(self, d: date) -> bool:
        """Rende mutabili gli ultimi `_MUTABLE_DAYS` giorni, dove 1 = solo oggi."""
        today = dt_util.now().date()
        delta = (today - d).days
        return 0 <= delta < self._MUTABLE_DAYS


    def _should_refresh_now(self, d: date, fetched_at: Optional[float] = None) -> bool:
        """
        Per i giorni mutabili, consenti al massimo un refresh per giornata locale.
        - Se mai fetchato → True
        - Se last_fetch è di un giorno locale precedente all'ora attuale → True
        - Altrimenti → False
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

    # ---------- public API ----------
    async def daily(self, day: date) -> Optional[Forecast]:
        """Cache-first; per i giorni mutabili consente al massimo un fetch per giornata locale.
        In caso di failure usa cache esistente e attiva negative-cache in base a Retry-After."""
        await self._data.async_load()

        fc = await self._data.async_get(day)
        fetched_at = float(fc.get("_meta_fetched_at", 0.0)) if fc else 0.0

        # Giorni non mutabili: mai refresh
        if fc and not self._is_mutable_day(day):
            return fc

        # Se già fetchato oggi (giorno locale), non rifare HTTP
        if fc and not self._should_refresh_now(day, fetched_at):
            return fc

        # Negative-cache attiva → non interrogare ora
        if not self._neg_can_query(day):
            return fc

        async def _do():
            async with self._sem:
                return await self._client.fetch_day(day)

        try:
            fetched, retry_after = await self._sf.run(day, _do)  # dedup per-day
        except asyncio.CancelledError:
            return fc

        if fetched:
            now_ts = time.time()
            annotated = cast(Forecast, {**fetched, "_meta_fetched_at": now_ts, "_meta_day": day.isoformat()})
            await self._data.async_put(day, annotated)
            return annotated

        # Fallita: attiva negative-cache (rispetta Retry-After se presente)
        if retry_after is not None:
            self._neg_backoff(day, seconds=int(retry_after))
        else:
            self._neg_backoff(day)

        return fc

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
        """Recupera [start..end] privilegiando cache e limitando la rete con cooldown/backoff.
        - Giorni non mutabili: mai refresh (solo cache persistente).
        - Giorni mutabili: al massimo un fetch HTTP per giornata locale.
        - Resilienza: single-flight per giorno, Semaphore, negative-cache, budget per batch."""
        await self._client.ensure_session()
        await self._data.async_load()

        days = [d for d in _date_iter(start, end)]
        results: List[Forecast] = []
        misses: List[date] = []

        # 1) Prima passata: usa cache e decidi cosa davvero manca/va aggiornato
        for d in days:
            fc = await self._data.async_get(d)
            fetched_at = float(fc.get("_meta_fetched_at", 0.0)) if fc else 0.0

            # non mutabile → prendi cache e non aggiornare
            if fc and not self._is_mutable_day(d):
                results.append(fc)
                continue

            # mutabile ma già fetchato oggi → tieni cache
            if fc and not self._should_refresh_now(d, fetched_at):
                results.append(fc)
                continue

            # negative-cache attiva → non interrogare ora (usa eventuale cache)
            if not self._neg_can_query(d):
                if fc:
                    results.append(fc)
                continue

            # va fetchato
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
                    _LOGGER.warning("PW daily_range: budget esaurito (%.1fs), ritorno %d/%d",
                                    total_budget_s, len(results), len(days))
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
                    if isinstance(fc, dict) and "datetime" in fc:
                        now_ts = time.time()
                        annotated = cast(Forecast, {**fc, "_meta_fetched_at": now_ts, "_meta_day": d.isoformat()})
                        results.append(annotated)
                        new_items.append((d, annotated))
                    else:
                        # fallback a cache esistente se presente
                        fallback = await self._data.async_get(d)
                        if fallback:
                            results.append(fallback)
                        # attiva neg-cache (rispetta Retry-After se presente)
                        if rafter is not None:
                            self._neg_backoff(d, seconds=int(rafter))
                        else:
                            self._neg_backoff(d)

                await asyncio.sleep(inter_batch_sleep)

        except asyncio.CancelledError:
            _LOGGER.warning("PW daily_range cancellata (elapsed=%.1fs, parziali=%d/%d)",
                            time.perf_counter() - t0, len(results), len(days))
        finally:
            # salva solo ciò che è nuovo/aggiornato
            for d, fc in new_items:
                await self._data.async_put(d, fc)
            # opzionale: flush immediato
            # await self._data.async_save()

        results.sort(key=_forecast_sort_key)
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
