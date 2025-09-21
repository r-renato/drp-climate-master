#
from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Dict, List, Optional, Protocol, cast

from homeassistant.core import HomeAssistant
from homeassistant.components.weather import Forecast
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.helpers.storage import Store
from homeassistant.helpers.debounce import Debouncer

_LOGGER = logging.getLogger(__name__)

class WeatherHistoricalProvider(Protocol):
    """
    Protocollo per provider meteo **storico** (es. Pirate Weather Time Machine).

    Obiettivi:
    - Esporre API `daily()` e `daily_range()` restituendo `Forecast` normalizzati.
    - Gestire un doppio livello di cache: RAM (TTL) + store persistente su disco.
    - Supportare l'uso opzionale di sessioni HTTP riutilizzabili via context manager.
    - Non propagare errori transitori e, quando sensato, restituire risultati parziali.
    """

    # # ---------- Cache persistente (store su disco) ----------
    # async def _async_cache_load(self) -> None:
    #     """
    #     Carica i dati persistenti dallo store (es. .storage/<key>).
    #     Deve essere **idempotente** e non effettuare IO di rete.
    #     Può eseguire migrazioni di schema se necessario.
    #     """

    # async def _async_cache_save(self) -> None:
    #     """
    #     Forza il salvataggio **immediato** dei dati in store,
    #     bypassando eventuale debouncer. Usare in chiusura/unload.
    #     """

    # async def _async_cache_get(self, day: date) -> Optional[Forecast]:
    #     """
    #     Ritorna il `Forecast` persistito per il giorno richiesto,
    #     senza toccare la cache RAM. `None` se assente.
    #     Il `day` è la **data locale** (mezzanotte locale del giorno).
    #     """

    # async def _async_cache_put(self, day: date, fc: Forecast) -> None:
    #     """
    #     Inserisce/aggiorna il `Forecast` del giorno nello store persistente.
    #     Può programmare il salvataggio in differita (debounced).
    #     Non deve bloccare a lungo il loop event-driven.
    #     """

    # async def _async_cache_prune(self, keep_days: int) -> None:
    #     """
    #     Esegue il pruning dello store, mantenendo solo gli ultimi `keep_days`.
    #     L'ordinamento è generalmente lessicografico su chiave ISO (YYYY-MM-DD).
    #     """

    # ---------- API principali ----------
    async def daily(self, day: date) -> Optional[Forecast]:
        """
        Ritorna il forecast giornaliero per `day`.
        Ordine di risoluzione raccomandato:
        1) Cache RAM (TTL), 2) Store persistente, 3) Rete (HTTP).
        Se ottenuto da rete, deve aggiornare RAM e store.
        """

    async def daily_range(self, start: date, end: date) -> List[Forecast]:
        """
        Ritorna la lista di `Forecast` nell'intervallo [start..end] **inclusivo**,
        idealmente in ordine cronologico (campo `datetime` crescente).

        Requisiti raccomandati:
        - Prima tentare RAM e store per ciascun giorno; solo i miss vanno in rete.
        - Concurrency limit (Semaphore) e retry/backoff sui miss.
        - Possibile budget/timeout per chunk; in caso di superamento, ritornare
          i **risultati parziali** già disponibili.
        - Persistere in blocco i nuovi record scaricati (debounced).
        """
        ...

    # ---------- Gestione risorse ----------
    async def _close_session(self) -> None:
        """
        Chiude eventuali risorse di rete (es. `aiohttp.ClientSession`) e
        reimposta lo stato interno. Deve essere **idempotente**.
        """

    # ---------- Context manager opzionale ----------
    async def __aenter__(self) -> "WeatherHistoricalProvider":
        """
        Inizializza le risorse necessarie (es. aprire la sessione HTTP).
        Ritorna `self` per l'uso con `async with`.
        """
        ...

    async def __aexit__(self, exc_type, exc, tb) -> None:
        """
        Chiusura del context: tipicamente forza `_cache_save()` e poi chiude
        le risorse con `_close_session()`. Non deve sollevare eccezioni.
        """
        ...


class WeatherForecastProvider(Protocol):
    """
    Protocollo per provider di **previsioni** (non storico), normalizzate
    in `Forecast` compatibili con Home Assistant.
    """

    async def async_get_forecast_days(self, *, start: date, days: int) -> List[Forecast]:
        """
        Ritorna un elenco di `Forecast` a partire da `start` per `days` giorni,
        in ordine cronologico. Gli elementi dovrebbero includere almeno:
        - `datetime` (ISO locale a mezzanotte del giorno),
        - `temperature` (max) e `templow` (min),
        più eventuali campi opzionali (es. `humidity`, `dew_point`, ecc.).
        """
        ...


# class HistoricalCacheProvider(WeatherHistoricalProvider):
#     """Cache persistente in .storage/<key>, salvataggio debounced - full async & concurrency-safe."""

#     def __init__(
#         self,
#         hass: HomeAssistant,
#         key: str,
#         version: int = 1,
#         save_cooldown_s: float = 30.0,
#         max_days: int = 730,
#     ) -> None:
#         self._hass = hass
#         self._store = Store(hass, version, key)

#         # Stato in RAM + lock async
#         self._data: Dict[str, Forecast] = {}
#         self._data_lock = asyncio.Lock()

#         # Load one-time + salvataggi serializzati
#         self._loaded = False
#         self._load_lock = asyncio.Lock()
#         self._save_lock = asyncio.Lock()
#         self._dirty = False  # true se ci sono modifiche non persistite

#         self._max_days = max_days
#         self._debouncer = Debouncer(
#             hass,
#             _LOGGER,
#             cooldown=save_cooldown_s,
#             immediate=False,
#             function=self._async_cache_save_now,  # funzione async
#         )

#         async def _async_on_stop(event) -> None:
#             # Flush finale su spegnimento HA
#             await self._async_cache_save_now()

#         hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_on_stop)

#     # --------- bootstrap/load ----------
#     async def _async_ensure_loaded_once(self) -> None:
#         """Carica lo store una sola volta in modo race-safe (con merge idempotente)."""
#         if self._loaded:
#             return

#         async with self._load_lock:
#             if self._loaded:
#                 return

#             raw = await self._store.async_load() or {}
#             loaded: Dict[str, Forecast]
#             if isinstance(raw, dict):
#                 loaded = {k: cast(Forecast, v) for k, v in raw.items() if isinstance(v, dict)}
#             else:
#                 loaded = {}

#             # Merge: i dati già in RAM (eventuali put avvenuti prima del load) VINCONO
#             async with self._data_lock:
#                 merged = dict(loaded)
#                 merged.update(self._data)
#                 self._data = merged
#                 self._loaded = True

#             _LOGGER.info("_ensure_loaded_once - Loaded %d days from store", len(self._data))

#     def _date_key(self, d: date) -> str:
#         return d.isoformat()
    
#     # ---------- API cache persistente ----------
#     async def _async_cache_load(self) -> None:
#         """Compat: invoca il loader race-safe."""
#         await self._async_ensure_loaded_once()

#     async def _async_cache_save(self) -> None:
#         """Forza un salvataggio immediato (non debounced)."""
#         await self._async_cache_save_now()

#     async def _async_cache_get(self, day: date) -> Optional[Forecast]:
#         """Ritorna il forecast persistito per il giorno, se presente (ritorna una COPIA)."""
#         await self._async_ensure_loaded_once()
#         key = self._date_key(day)
#         async with self._data_lock:
#             fc = self._data.get(key)
#             # Copia difensiva per evitare mutazioni esterne dello stato interno
#             return cast(Forecast, dict(fc)) if fc is not None else None

#     async def _async_cache_put(self, day: date, fc: Forecast) -> None:
#         """Scrive/aggiorna il forecast persistente per il giorno (prune + save debounced)."""
#         await self._async_ensure_loaded_once()
#         key = self._date_key(day)
#         async with self._data_lock:
#             self._data[key] = cast(Forecast, dict(fc))  # shallow copy
#             self._cache_prune_locked(self._max_days)
#             self._dirty = True

#         # Programma il salvataggio (fuori dal lock) – non awaited
#         schedule = getattr(self._debouncer, "async_schedule_call", None)
#         if callable(schedule):
#             schedule()
#         else:
#             self._hass.async_create_task(self._debouncer.async_call())

#     async def _async_cache_prune(self, keep_days: int) -> None:
#         """Mantiene solo gli ultimi keep_days (thread-safe)."""
#         await self._async_ensure_loaded_once()
#         async with self._data_lock:
#             before = len(self._data)
#             self._cache_prune_locked(keep_days)
#             after = len(self._data)
#             if after != before:
#                 self._dirty = True

#     # ---------- helpers interni ----------
#     def _cache_prune_locked(self, keep_days: int) -> None:
#         if keep_days <= 0:
#             return
#         n = len(self._data)
#         if n <= keep_days:
#             return
#         keys_sorted = sorted(self._data.keys())  # ISO => ordina cronologico
#         for k in keys_sorted[: n - keep_days]:
#             self._data.pop(k, None)


#     async def _async_cache_save_now(self) -> None:
#         # snapshot + versione locale
#         async with self._data_lock:
#             snapshot = dict(self._data)
#             # non toccare _dirty qui!
#         async with self._save_lock:
#             try:
#                 await self._store.async_save(snapshot)
#             except Exception as e:
#                 _LOGGER.warning("Store save failed: %s", e)
#                 # segna sporco per riprovare
#                 async with self._data_lock:
#                     self._dirty = True
#                 return
#         # successo: se nessuno ha scritto nel frattempo, pulisci
#         async with self._data_lock:
#             self._dirty = False
#         _LOGGER.debug("HistoricalCacheProvider: saved %d days", len(snapshot))


#     # ---------- context ----------
#     async def __aexit__(self, exc_type, exc, tb) -> None:
#         try:
#             await self._async_cache_save()
#         finally:
#             await self._close_session()



