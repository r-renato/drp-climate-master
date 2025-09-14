from __future__ import annotations

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

import asyncio
import importlib
import logging
import math
import random
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, List, Optional, Protocol, Tuple

from ..domain.models import WeatherDailySample
from ..helpers.utils import as_float
from .provider import WeatherProvider

_LOGGER = logging.getLogger(__name__)

# =============================================================================
# Shared model & interfaces
# =============================================================================

@dataclass(slots=True, frozen=True)
class PirateWeatherConfig:
    api_key: str
    lat: float
    lon: float
    units: str = "si"            # 'si' or 'us'
    base_url: str | None = None  # HTTP only; library uses its default

    def __post_init__(self) -> None:
        if not (-90.0 <= self.lat <= 90.0) or not (-180.0 <= self.lon <= 180.0):
            raise ValueError(f"Invalid lat/lon: {self.lat}, {self.lon}")
        if self.units not in {"si", "us"}:
            raise ValueError(f"Invalid units: {self.units} (allowed: 'si', 'us')")


@dataclass(slots=True, frozen=True)
class ProviderOptions:
    max_concurrency: int = 6
    retries: int = 2                 # total tries = retries + 1
    backoff_base: float = 0.5        # seconds
    backoff_factor: float = 2.0
    jitter: float = 0.25             # +/- fraction
    ttl_seconds: int = 6 * 3600      # per-day cache TTL (6h)
    http_timeout_s: int = 20         # HTTP single request timeout


# =============================================================================
# Helpers (dates, math, cache)
# =============================================================================

def _date_range(start: date, end: date) -> Iterable[date]:
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)

def _mean_pair(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    return (a + b) / 2.0


def _normalize_rh_to_pct(rh: float | None) -> float | None:
    if rh is None:
        return None
    val = rh * 100.0 if rh <= 1.0 else rh
    if math.isnan(val):  # guard
        return None
    # clamp to [0,100]
    return max(0.0, min(100.0, val))


def _jittered_backoff(base: float, factor: float, attempt: int, jitter_frac: float) -> float:
    # attempt: 0,1,2,...
    delay = base * (factor ** attempt)
    jitter = delay * jitter_frac
    return max(0.0, delay + random.uniform(-jitter, jitter))

class _TTLCache:
    """Simple per-key TTL cache for WeatherDailySample."""
    __slots__ = ("_ttl", "_data")

    def __init__(self, ttl_seconds: int) -> None:
        self._ttl = ttl_seconds
        self._data: dict[date, Tuple[float, WeatherDailySample]] = {}

    def get(self, key: date) -> Optional[WeatherDailySample]:
        entry = self._data.get(key)
        if not entry:
            return None
        exp_ts, val = entry
        if time.time() > exp_ts:
            # expired
            self._data.pop(key, None)
            return None
        return val

    def set(self, key: date, value: WeatherDailySample) -> None:
        self._data[key] = (time.time() + self._ttl, value)

    def clear(self) -> None:
        self._data.clear()


# =============================================================================
# Library-backed provider
# =============================================================================

class PirateWeatherLibProvider(WeatherProvider):
    """Provider using `python-pirateweather` (sync) executed via thread executor."""

    def __init__(self, cfg: PirateWeatherConfig, opts: ProviderOptions | None = None) -> None:
        self._cfg = cfg
        self._opts = opts or ProviderOptions()
        try:
            self._lib = importlib.import_module("pirateweather")
        except Exception as e:  # pragma: no cover
            raise RuntimeError("python-pirateweather not installed; cannot use library provider") from e

        self._sem = asyncio.Semaphore(self._opts.max_concurrency)
        self._cache = _TTLCache(self._opts.ttl_seconds)

    async def __aenter__(self) -> "PirateWeatherLibProvider":  # symmetry with HTTP provider
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        pass

    async def daily(self, day: date) -> Optional[WeatherDailySample]:
        cached = self._cache.get(day)
        if cached is not None:
            return cached

        async with self._sem:
            start_t = time.perf_counter()
            res: Optional[WeatherDailySample] = None
            for attempt in range(self._opts.retries + 1):
                try:
                    fc = await asyncio.to_thread(
                        self._lib.load_forecast,
                        self._cfg.api_key,
                        self._cfg.lat,
                        self._cfg.lon,
                        datetime(day.year, day.month, day.day, 12, 0, 0, tzinfo=timezone.utc),
                        self._cfg.units,
                    )
                    res = self._extract_daily(day, fc)
                    if res is not None:
                        break
                except Exception as e:
                    if attempt >= self._opts.retries:
                        _LOGGER.warning("PW Lib daily(%s) failed: %s", day, e)
                        break
                await asyncio.sleep(_jittered_backoff(
                    self._opts.backoff_base, self._opts.backoff_factor, attempt, self._opts.jitter
                ))

            dt = (time.perf_counter() - start_t) * 1000
            _LOGGER.debug("PW Lib daily(%s): %.1f ms (ok=%s)", day, dt, bool(res))
            if res:
                self._cache.set(day, res)
            return res

    async def daily_range(self, start: date, end: date) -> List[WeatherDailySample]:
        tasks = [self.daily(d) for d in _date_range(start, end)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        out: List[WeatherDailySample] = []
        for r in results:
            if isinstance(r, WeatherDailySample):
                out.append(r)
        out.sort(key=lambda s: s.day)
        _LOGGER.debug("PW Lib daily_range(%s..%s) -> %d samples", start, end, len(out))
        return out

    @staticmethod
    def _extract_daily(d: date, forecast_obj: Any) -> Optional[WeatherDailySample]:
        try:
            daily_block = forecast_obj.daily()
            data = getattr(daily_block, "data", None)
            if not data:
                return None
            dp = data[0]  # day bucket
            tmin = as_float(getattr(dp, "temperatureMin", None))
            tmax = as_float(getattr(dp, "temperatureMax", None))
            dpv = as_float(getattr(dp, "dewPoint", None))
            rh = _normalize_rh_to_pct(as_float(getattr(dp, "humidity", None)))
            return WeatherDailySample(day=d, tmin=tmin, tmax=tmax, tmean=_mean_pair(tmin, tmax), dew_point=dpv, humidity=rh)
        except Exception:
            return None


# =============================================================================
# HTTP-backed provider
# =============================================================================

class PirateWeatherHTTPProvider(WeatherProvider):
    """Provider using direct HTTPS calls (aiohttp), with session reuse."""

    def __init__(self, cfg: PirateWeatherConfig, opts: ProviderOptions | None = None) -> None:
        self._cfg = cfg
        self._opts = opts or ProviderOptions()
        self._aiohttp = importlib.import_module("aiohttp")
        self._session: Any = None
        self._sem = asyncio.Semaphore(self._opts.max_concurrency)
        self._cache = _TTLCache(self._opts.ttl_seconds)

    # ---------- context mgmt ----------
    async def __aenter__(self) -> "PirateWeatherHTTPProvider":
        await self._ensure_session()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self._close_session()

    async def _ensure_session(self) -> None:
        if self._session is None:
            timeout = getattr(self._aiohttp, "ClientTimeout")(total=self._opts.http_timeout_s)
            self._session = getattr(self._aiohttp, "ClientSession")(timeout=timeout)

    async def _close_session(self) -> None:
        if self._session is not None:
            try:
                await self._session.close()
            finally:
                self._session = None

    # ---------- public API ----------
    async def daily(self, day: date) -> Optional[WeatherDailySample]:
        cached = self._cache.get(day)
        if cached is not None:
            return cached

        # Use persistent session if available; else ephemeral one-shot
        if self._session is None:
            timeout = getattr(self._aiohttp, "ClientTimeout")(total=self._opts.http_timeout_s)
            async with getattr(self._aiohttp, "ClientSession")(timeout=timeout) as session:
                res = await self._daily_with_session(session, day)
        else:
            res = await self._daily_with_session(self._session, day)

        if res:
            self._cache.set(day, res)
        return res

    async def daily_range(self, start: date, end: date) -> List[WeatherDailySample]:
        await self._ensure_session()
        tasks = [self._daily_with_session(self._session, d) for d in _date_range(start, end)]  # type: ignore[arg-type]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        out: List[WeatherDailySample] = [r for r in results if isinstance(r, WeatherDailySample)]
        out.sort(key=lambda s: s.day)
        _LOGGER.debug("PW HTTP daily_range(%s..%s) -> %d samples", start, end, len(out))
        return out

    # ---------- internals ----------
    def _build_url(self, when: datetime) -> str:
        base = self._cfg.base_url or "https://api.pirateweather.net/forecast"
        iso = when.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
        url = f"{base}/{self._cfg.api_key}/{self._cfg.lat},{self._cfg.lon},{iso}"
        _LOGGER.debug("PW HTTPS url: %s", url)
        return url

    async def _daily_with_session(self, session: Any, d: date) -> Optional[WeatherDailySample]:
        cached = self._cache.get(d)
        if cached is not None:
            return cached

        async with self._sem:
            start_t = time.perf_counter()
            res: Optional[WeatherDailySample] = None
            for attempt in range(self._opts.retries + 1):
                try:
                    when = datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=timezone.utc)
                    url = self._build_url(when)
                    params = {"units": self._cfg.units, "exclude": "hourly,minutely,alerts,flags,currently"}
                    async with session.get(url, params=params) as resp:
                        status = resp.status
                        if status == 200:
                            payload: dict[str, Any] = await resp.json()
                            res = self._parse_daily_payload(d, payload)
                            if res is not None:
                                break
                        elif status in (429, 500, 502, 503, 504):
                            # Respect Retry-After if present
                            retry_after = 0.0
                            try:
                                ra = resp.headers.get("Retry-After")
                                if ra:
                                    retry_after = float(ra)
                            except Exception:
                                retry_after = 0.0
                            # Only log on final attempt
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
                except Exception as e:
                    if attempt >= self._opts.retries:
                        _LOGGER.warning("PW HTTP daily(%s) exception: %s", d, e)
                    await asyncio.sleep(_jittered_backoff(
                        self._opts.backoff_base, self._opts.backoff_factor, attempt, self._opts.jitter
                    ))

            dt = (time.perf_counter() - start_t) * 1000
            _LOGGER.debug("PW HTTP daily(%s): %.1f ms (ok=%s)", d, dt, bool(res))
            if res:
                self._cache.set(d, res)
            return res

    @staticmethod
    def _parse_daily_payload(d: date, payload: dict[str, Any]) -> Optional[WeatherDailySample]:
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
        dpv = as_float(di.get("dewPoint"))
        rh = _normalize_rh_to_pct(as_float(di.get("humidity")))
        return WeatherDailySample(day=d, tmin=tmin, tmax=tmax, tmean=_mean_pair(tmin, tmax), dew_point=dpv, humidity=rh)


# =============================================================================
# Factory
# =============================================================================

def create_pirateweather_provider(
    cfg: PirateWeatherConfig,
    *,
    opts: ProviderOptions | None = None
) -> WeatherProvider:
    """Prefer python-pirateweather; fall back to HTTP."""
    # try:
    #     importlib.import_module("pirateweather")
    #     _LOGGER.info("Using PirateWeatherLibProvider")
    #     return PirateWeatherLibProvider(cfg, opts=opts)
    # except Exception:
    #     _LOGGER.info("Using PirateWeatherHTTPProvider (library unavailable)")
    return PirateWeatherHTTPProvider(cfg, opts=opts)
