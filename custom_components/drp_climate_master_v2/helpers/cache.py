"""
Persistent daily Forecast cache for Home Assistant (.storage-backed) — with optional persistence.

Questo modulo espone la classe `PersistentCache`, una cache **concurrency-safe** che associa
ad ogni giorno (chiave ISO `YYYY-MM-DD`) un payload `Forecast` di HA. La cache mantiene uno
snapshot in RAM e, opzionalmente, persiste su disco tramite `homeassistant.helpers.storage.Store`.
I salvataggi sono coalescati con `Debouncer` per ridurre l’I/O; allo spegnimento di Home Assistant,
se la persistenza è attiva, viene effettuato un flush immediato.

Funzionamento in breve
- **Flag di persistenza**: `persist=True` abilita load/save su `.storage`; `persist=False` mantiene
  la cache solo in memoria (nessun I/O, nessun listener di shutdown, nessun debouncer).
- **Load one-time** (solo se `persist=True`): il backing store viene letto una sola volta in modo race-safe.
- **Merge idempotente** (solo se `persist=True`): in caso di scritture prima del load, lo stato in RAM
  prevale sul contenuto dello store.
- **Concurrency-safety**: lock distinti per dati (`_data_lock`), load (`_load_lock`) e salvataggi (`_save_lock`)
  garantiscono consistenza in contesto asyncio.
- **Retention**: pruning cronologico (FIFO) per mantenere al più `max_days` elementi.
- **Flush su shutdown** (solo se `persist=True`): in risposta a `EVENT_HOMEASSISTANT_STOP` viene eseguito
  un salvataggio immediato.

API pubblica (async)
- `async_load()` → garantisce il bootstrap (no-op se `persist=False`; idempotente).
- `async_save()` → forza un salvataggio immediato (no-op se `persist=False`).
- `async_get(day: date) -> Optional[Forecast]` → ritorna una **copia** del forecast del giorno.
- `async_put(day: date, fc: Forecast)` → scrive/aggiorna (copia shallow), esegue prune e, se `persist=True`,
  pianifica un save debounced.

Note e limitazioni
- Nessuna validazione profonda del payload `Forecast`.
- `async_save()` può salvare anche se non ci sono modifiche recenti (ottimizzabile con `_dirty`).
- Le chiavi sono stringhe ISO `date.isoformat()`: l’ordinamento lessicografico coincide con quello cronologico.
- Il contesto di sicurezza è **asyncio** (non thread-safe tra thread diversi senza ulteriori cautele).

Esempi d’uso

    # Persistenza attiva (default)
    cache = PersistentCache(hass, key="drp_climate_master_v2_history",
                            version=1, save_cooldown_s=60.0, max_days=730)

    # Solo in memoria (nessun file in .storage)
    mem_cache = PersistentCache(hass, key="ignored_when_persist_false",
                                persist=False, max_days=365)

    await cache.async_put(date.today(), forecast_dict)
    today_fc = await cache.async_get(date.today())
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Dict, Optional, cast

from homeassistant.core import HomeAssistant
from homeassistant.components.weather import Forecast
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.helpers.storage import Store
from homeassistant.helpers.debounce import Debouncer

_LOGGER = logging.getLogger(__name__)

class PersistentForecastCache:
    """
    Cache persistente su `.storage/<key>` con stato in RAM e salvataggio debounced;
    può operare anche in modalità **solo memoria** (`persist=False`).

    La struttura memorizza una mappa `YYYY-MM-DD -> Forecast` e offre un’API async
    safe-for-concurrency in ambiente Home Assistant.

    Caratteristiche
    ---------------
    - **Persistenza opzionale**: con `persist=False` la cache evita ogni I/O su disco,
      non registra listener di shutdown e non istanzia il debouncer.
    - **Caricamento one-time** e **merge idempotente** se `persist=True`.
    - **Copia difensiva**: `async_get()` restituisce una copia; `async_put()` salva una shallow copy.
    - **Retention automatica**: pruning FIFO per conservare al più `max_days` giorni.
    - **Debounce salvataggi**: le scritture pianificano un save differito (solo con `persist=True`).
    
    Parametri
    ---------
    hass : HomeAssistant
        Istanza di Home Assistant (necessaria per bus eventi e, se attivo, storage).
    key : str
        Chiave del file in `.storage/` (usata solo se `persist=True`).
    version : int, default=1
        Versione dello schema dello store (utile per eventuali migrazioni esterne).
    save_cooldown_s : float, default=30.0
        Finestra di debounce (secondi) per coalescere i salvataggi (solo se `persist=True`).
    max_days : int, default=730
        Numero massimo di giorni da conservare (pruning automatico oltre tale soglia).
    persist : bool, default=True
        Abilita/disabilita la persistenza su disco. Se `False`, la cache è solo in memoria.

    Garanzie
    --------
    - Consistenza in accesso concorrente asyncio tramite `_data_lock`, `_load_lock`, `_save_lock`.
    - `async_save()` effettua un salvataggio atomico dello snapshot corrente (no-op se `persist=False`);
      in caso di errore, `_dirty` resta `True` per retry futuri (quando `persist=True`).
    - Listener su `EVENT_HOMEASSISTANT_STOP` registrato solo se `persist=True`.

    Limitazioni
    -----------
    - Non thread-safe tra thread diversi (l’uso previsto è nel loop asyncio di HA).
    - Non valida il contenuto del `Forecast`.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        key: str,
        version: int = 1,
        save_cooldown_s: float = 30.0,
        max_days: int = 730,
        persist: bool = True,
    ) -> None:
        self._hass = hass
        self._persist = persist

        # Store e debouncer sono creati solo se la persistenza è attiva
        self._store: Optional[Store] = Store(hass, version, key) if persist else None

        # Stato in RAM + lock async
        self._data: Dict[str, Forecast] = {}
        self._data_lock = asyncio.Lock()

        # Load one-time + salvataggi serializzati
        self._loaded = False
        self._load_lock = asyncio.Lock()
        self._save_lock = asyncio.Lock()
        self._dirty = False  # True se ci sono modifiche non persistite

        self._max_days = max_days
        self._debouncer: Optional[Debouncer] = (
            Debouncer(
                hass,
                _LOGGER,
                cooldown=save_cooldown_s,
                immediate=False,
                function=self._async_cache_save_now,  # funzione async
            )
            if persist
            else None
        )

        if persist:
            async def _async_on_stop(event) -> None:
                # Flush finale su spegnimento HA (best-effort)
                await self._async_cache_save_now()

            hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_on_stop)

    # ---------- helpers interni ----------
    async def _async_cache_prune(self, keep_days: int) -> None:
        """
        Mantiene solo gli ultimi `keep_days` elementi (thread-safe nel contesto asyncio).

        Esegue:
        - bootstrap (se `persist=True`);
        - pruning cronologico basato sull’ordinamento lessicografico delle chiavi ISO;
        - marcatura `_dirty=True` se la dimensione è cambiata.

        Args:
            keep_days: Numero massimo di giorni da mantenere (<=0 → nessuna azione).
        """
        await self._async_ensure_loaded_once()
        async with self._data_lock:
            before = len(self._data)
            self._cache_prune_locked(keep_days)
            after = len(self._data)
            if after != before:
                self._dirty = True

    def _cache_prune_locked(self, keep_days: int) -> None:
        """
        Pruning FIFO **senza** acquisire lock (il chiamante deve detenere `_data_lock`).

        Ordina le chiavi ISO (equivalente a ordinamento cronologico) e rimuove le più
        vecchie fino a conservare `keep_days` elementi. Non modifica `_dirty` direttamente.

        Args:
            keep_days: Numero massimo di elementi da conservare.
        """
        if keep_days <= 0:
            return
        n = len(self._data)
        if n <= keep_days:
            return
        keys_sorted = sorted(self._data.keys())  # ISO => ordine cronologico
        for k in keys_sorted[: n - keep_days]:
            self._data.pop(k, None)

    async def _async_cache_save_now(self) -> None:
        """
        Salvataggio immediato dello snapshot corrente sullo store.

        Se `persist=False` → **no-op** (ritorna subito).

        - Prende uno snapshot protetto da `_data_lock`.
        - Serializza i salvataggi tramite `_save_lock`.
        - In caso di eccezione, logga un warning e mantiene `_dirty=True` per retry futuri.
        - Su successo, setta `_dirty=False`.

        Note:
            Questo metodo viene usato dal debouncer e dal listener di shutdown.
            Non solleva eccezioni verso i chiamanti (best-effort + logging).
        """
        if not self._persist or self._store is None:
            _LOGGER.debug("Persistence disabled: skipping save_now.")
            return

        # snapshot + versione locale
        async with self._data_lock:
            snapshot = dict(self._data)
            # non toccare _dirty qui!
        async with self._save_lock:
            try:
                await self._store.async_save(snapshot)
            except Exception as e:
                _LOGGER.warning("Store save failed: %s", e)
                # segna sporco per riprovare
                async with self._data_lock:
                    self._dirty = True
                return
        # successo: se nessuno ha scritto nel frattempo, pulisci
        async with self._data_lock:
            self._dirty = False
        _LOGGER.debug("HistoricalCacheProvider: saved %d days", len(snapshot))

    # --------- bootstrap/load ----------
    async def _async_ensure_loaded_once(self) -> None:
        """
        Carica lo store una sola volta in modo race-safe, con merge idempotente.

        Se `persist=False`:
            - Marca `_loaded=True` al primo passaggio e ritorna (nessun I/O).

        Se `persist=True`:
            - Protegge la sezione di load con `_load_lock`;
            - Converte il payload dello store in `Dict[str, Forecast]` filtrando voci non-dict;
            - Esegue merge con lo stato già presente in RAM (RAM **vince**);
            - Logga a livello INFO il numero di giorni caricati.
        """
        if self._loaded:
            return

        async with self._load_lock:
            if self._loaded:
                return

            if not self._persist or self._store is None:
                # Modalità memory-only: nessun I/O
                self._loaded = True
                return

            raw = await self._store.async_load() or {}
            if isinstance(raw, dict):
                loaded: Dict[str, Forecast] = {k: cast(Forecast, v) for k, v in raw.items() if isinstance(v, dict)}
            else:
                loaded = {}

            # Merge: i dati già in RAM (eventuali put avvenuti prima del load) VINCONO
            async with self._data_lock:
                merged = dict(loaded)
                merged.update(self._data)
                self._data = merged
                self._loaded = True

            _LOGGER.info("_ensure_loaded_once - Loaded %d days from store", len(self._data))

    def _date_key(self, d: date) -> str:
        """
        Converte una `date` in chiave ISO (`YYYY-MM-DD`) per l’indicizzazione.

        Args:
            d: Giorno da convertire.

        Returns:
            Stringa ISO usata come chiave nel backing store.
        """
        return d.isoformat()
    
    # ---------- API cache persistente ----------
    async def async_load(self) -> None:
        """
        Garantisce il bootstrap del backing store (idempotente).

        - Se `persist=False`, è un no-op e la cache viene considerata pronta.
        - Se `persist=True`, effettua il caricamento one-time (race-safe).
        """
        await self._async_ensure_loaded_once()

    async def async_save(self) -> None:
        """
        Forza un salvataggio immediato (non debounced) dello snapshot corrente.

        - Se `persist=False`, è un no-op.
        - Se `persist=True`, salva lo stato corrente su `.storage/<key>`.
        """
        await self._async_cache_save_now()

    async def async_get(self, day: date) -> Optional[Forecast]:
        """
        Ritorna il forecast persistito per `day`, se presente.

        Concorrenza e mutabilità:
        - Esegue il bootstrap allo store se necessario (no-op se `persist=False`).
        - Restituisce una **copia** del payload (dict) per evitare mutazioni esterne.

        Args:
            day: Giorno da leggere.

        Returns:
            Un dict `Forecast` (copia) se presente, altrimenti `None`.
        """
        await self._async_ensure_loaded_once()
        key = self._date_key(day)
        async with self._data_lock:
            fc = self._data.get(key)
            return cast(Forecast, dict(fc)) if fc is not None else None

    async def async_put(self, day: date, fc: Forecast) -> None:
        """
        Scrive/aggiorna il forecast per `day` e, se `persist=True`, pianifica un save debounced.

        Operazioni:
        - Bootstrap (no-op se `persist=False`).
        - Inserimento/aggiornamento tramite **shallow copy** di `fc`.
        - Pruning secondo `self._max_days`.
        - Marcatura `_dirty=True`.
        - Pianificazione del salvataggio differito via `Debouncer` (solo se `persist=True`).

        Args:
            day: Giorno da aggiornare.
            fc: Payload `Forecast` conforme al tipo HA.
        """
        await self._async_ensure_loaded_once()
        key = self._date_key(day)
        async with self._data_lock:
            self._data[key] = cast(Forecast, dict(fc))  # shallow copy
            self._cache_prune_locked(self._max_days)
            self._dirty = True

        # Programma il salvataggio (fuori dal lock) – non awaited
        if self._debouncer is not None:
            schedule = getattr(self._debouncer, "async_schedule_call", None)
            if callable(schedule):
                schedule()
            else:
                self._hass.async_create_task(self._debouncer.async_call())
