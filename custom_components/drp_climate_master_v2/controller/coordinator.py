# custom_components/drp_climate/coordinator.py
from __future__ import annotations

import asyncio
import contextlib
from dataclasses import fields, replace
import logging
import json
from typing import Any, List, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, Event, EventStateChangedData, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.const import PERCENTAGE, EVENT_HOMEASSISTANT_STARTED

from ..domain.models.plant import PlantSnapshot

from ..strategies.season_threshold import SeasonThresholdStrategy


from ..domain.models.runtime_schema import AreaConfig, RuntimeConfig, SensorPair
from ..domain.models.season import SeasonState

from ..helpers.plant import take_plant_snapshot
from ..helpers.logger import log_debug, log_info, log_warning
from ..helpers.timeutils import ha_timezone, now_tz
from ..helpers.config_entries import (
    build_runtime_config,
    collect_entity_ids_for_state_changes,
    subscribe_entity_state_changes,
)
from ..weather.provider import WeatherHistoricalProvider
from ..weather.forecast_provider import WeatherForecast
from ..weather.historical_pirateweather import PirateWeatherConfig, get_pirateweather_historical_provider
from ..season.detector import WeatherSeasonDetector

from ..const import (
    CONF_INDOOR,
    CONF_RADIANT,
    DOMAIN,
    ENTITIES_STATE,
    NAME_AREA_HOME,
)

_LOGGER = logging.getLogger(__name__)


class ClimateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """
    Coordina:
      - Lettura sensori (ports/sensors)
      - Calcolo grandezze derivate (psicrometria, domanda, flag)
      - Pubblicazione snapshot per Entity/Supervisor
    NON decide la strategia HVAC (competenza del Supervisor).
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._entry = entry

        # Inizializza la struttura dati una volta e tieni il riferimento
        domain_store = hass.data.setdefault(DOMAIN, {})
        entry_store = domain_store.setdefault(entry.entry_id, {})
        entry_store.setdefault(ENTITIES_STATE, {})  # dict[str, State]
        self._entities_state_store: dict = entry_store[ENTITIES_STATE]

        # Config di runtime e subscribe ai cambi di stato
        self._runtime: RuntimeConfig = build_runtime_config(entry)
        self._runtime_2 = None  # per swap atomico
        # _LOGGER.debug("Runtime config %s", self._runtime)

        eids = collect_entity_ids_for_state_changes(self._runtime)
        log_debug(_LOGGER, "Subscribing state changes for %d", eids)
        # Conserva l'unsubscribe per lo stop/unload
        self._unsub_state_changes = subscribe_entity_state_changes(
            self._hass, callback=self.entity_changed, entity_ids=eids
        )

        # Loop FAST
        self._fast_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

        self._weather_forecast_provider = WeatherForecast(
            self._hass,
            self._runtime.climate.weather.forecast_data.provider,
            forecast_type="daily"
        )
        self._weather_historical_provider: WeatherHistoricalProvider
        if "pirateweather" == self._runtime.climate.weather.historical_data.provider:
            self._weather_historical_provider: WeatherHistoricalProvider = get_pirateweather_historical_provider(
                self._hass, 
                PirateWeatherConfig(
                    api_key=self._runtime.climate.weather.historical_data.token,
                    lat=self._runtime.climate.weather.historical_data.latitude,
                    lon=self._runtime.climate.weather.historical_data.longitude,
                    units=self._runtime.climate.units,
                )
            )
        self._season_detector = WeatherSeasonDetector(
            weatherHistorical=self._weather_historical_provider,
            weatherForecast=self._weather_forecast_provider
        )
        self._season_data: SeasonState

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}-coordinator",
            update_interval=self._runtime.update_interval,  # loop SLOW
        )

        self._unsub_hastarted_event = hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, self._on_started)
        _LOGGER.debug("ClimateCoordinator initialized. Update each %s seconds", self._runtime.update_interval)

    @callback
    def _on_started(self, event: Event):
        # tra 10s esegue il tuo coroutine
        @callback
        def _runner(_now):
            self._hass.async_create_task(self._async_complete_runtime_config(event))
        
        self._unsub_delayed = async_call_later(self._hass, 10, _runner)

    async def _async_complete_runtime_config(self, event: Event) -> None:

        loop = asyncio.get_running_loop()
        start = loop.time()
        comp_store = self._hass.data.setdefault(DOMAIN, {})
        store = comp_store.setdefault(self._entry.entry_id, {})
        log_debug(_LOGGER, "setup_unique_ids_store '%s'. (RuntimeConfig)", store)
        while True:
            comp_store = self._hass.data.setdefault(DOMAIN, {})
            store = comp_store.setdefault(self._entry.entry_id, {})
            setup_unique_ids = store.setdefault("setup_unique_ids", False)
            log_debug(_LOGGER, "setup_unique_ids_store '%s'. (RuntimeConfig)", setup_unique_ids)
            if setup_unique_ids:
                log_info(_LOGGER, "setup_unique_ids_store done. (RuntimeConfig)")
                break

            if (loop.time() - start) >= 60:
                log_warning(_LOGGER, "Timeout waiting for setup_unique_ids_store (RuntimeConfig)")
                return
            
            await asyncio.sleep(5)

        comp_store = self._hass.data.setdefault(DOMAIN, {})
        store = comp_store.setdefault(self._entry.entry_id, {})
        area_unique_ids_store: dict = store.setdefault("area_unique_ids", {})
        home_unique_ids_store: dict = store.setdefault("home_unique_ids", {})

        sensorpair_fields = {f.name for f in fields(SensorPair)}
        old_areas = self._runtime.climate.areas
        new_areas: list[AreaConfig] = []
        changed = False

        for area_cfg in old_areas:
            data = area_unique_ids_store.get(area_cfg.name) or {}
            sp = area_cfg.sensors
            updates: dict[str, str] = {}

            for attr, sensordata in data.items():
                if attr not in sensorpair_fields:
                    log_warning(_LOGGER, "Ignoro attributo sconosciuto SensorPair.%s per area '%s'", attr, area_cfg.name)
                    continue
                eid = getattr(sensordata, "entity_id", None) or str(sensordata)
                # se non vuoi sovrascrivere con None/stringhe vuote, aggiungi guardia
                if eid and getattr(sp, attr) != eid:
                    updates[attr] = eid

            if updates:
                new_sp = replace(sp, **updates)
                area_cfg = replace(area_cfg, sensors=new_sp)
                changed = True
                log_info(_LOGGER, "Area '%s' aggiornata: %s (RuntimeConfig)", area_cfg.name, new_sp)

            new_areas.append(area_cfg)

        # mean_apt (home sensors)
        mean_sp = self._runtime.climate.mean_apt
        mean_updates: dict[str, str] = {}
        for attr, sensor in (home_unique_ids_store or {}).items():
            if attr not in sensorpair_fields:
                log_warning(_LOGGER, "Ignoro attributo sconosciuto SensorPair.%s per mean_apt", attr)
                continue
            eid = getattr(sensor, "entity_id", None) or str(sensor)
            if eid and getattr(mean_sp, attr) != eid:
                mean_updates[attr] = eid

        new_mean = replace(mean_sp, **mean_updates) if mean_updates else mean_sp
        if mean_updates:
            changed = True
            log_info(_LOGGER, "Aggiornato mean_apt: %s", new_mean)

        if changed:
            new_climate = replace(self._runtime.climate, areas=new_areas, mean_apt=new_mean)
            new_runtime = replace(self._runtime, climate=new_climate)
            self._runtime_2 = new_runtime    # swap atomico

        # log_info(_LOGGER, "RuntimeConfig: %s", self._runtime.climate.areas)
        log_info(_LOGGER, "%s Done. (RuntimeConfig) %s", id(self), self._runtime)

    # ----------------- Accesso allo store condiviso ----------------- #

    @property
    def _entities_state(self) -> dict:
        """
        Mappa entity_id -> State (idempotente anche se hass.data viene ricreato).
        """
        domain_store = self._hass.data.setdefault(DOMAIN, {})
        entry_store = domain_store.setdefault(self._entry.entry_id, {})
        return entry_store.setdefault(ENTITIES_STATE, self._entities_state_store)

    # ----------------- Setup delle entity "slave" ------------------- #

    def build_slave_sensor_defs(self) -> list[dict[str, Any]]:
        """Restituisce la lista dei sensori dew-point da creare (name/sensors/unit)."""
        defs: list[dict[str, Any]] = []

        temps: list[str] = []
        humis: list[str] = []
        
        for area in getattr(self._runtime.climate, "areas", []):
            if getattr(area, CONF_INDOOR, False) and getattr(area, CONF_RADIANT, False):
                sensors = getattr(area, "sensors", None)
                if sensors:
                    temps.append(sensors.temperature)
                    humis.append(sensors.humidity)
                    defs.append(
                        {
                            "type" : "DewpointSensor",
                            "area": area.name,
                            "name": f"Ambient {area.name}",
                            "sensors": sensors,  # es. SensorPair o dict compatibile
                            "unit": self._runtime.climate.temperature_unit,
                        }
                    )
                    defs.append(
                        {
                            "type" : "HeatIndexSensor",
                            "area": area.name,
                            "name": f"Ambient {area.name}",
                            "sensors": sensors,  # es. SensorPair o dict compatibile
                            "unit": self._runtime.climate.temperature_unit,
                        }
                    )

        defs.append(
            {
                "type" : "CurrentTemperatureSensor",
                "name": f"Ambient {NAME_AREA_HOME}",
                "temp_sensors": temps,
                "unit": self._runtime.climate.temperature_unit,
            }
        )
        defs.append(
            {
                "type" : "CurrentHumiditySensor",
                "name": f"Ambient {NAME_AREA_HOME}",
                "humi_sensors": humis,
                "unit": PERCENTAGE,
            }
        )
        defs.append(
            {
                "type" : "CurrentDewpointSensor",
                "name": f"Ambient {NAME_AREA_HOME}",
                "temp_sensors": temps,
                "humi_sensors": humis,
                "unit": self._runtime.climate.temperature_unit,
            }
        )
        defs.append(
            {
                "type" : "CurrentHeatIndexSensor",
                "name": f"Ambient {NAME_AREA_HOME}",
                "temp_sensors": temps,
                "humi_sensors": humis,
                "unit": self._runtime.climate.temperature_unit,
            }
        )

        log_debug(_LOGGER, "build_climate_sensor_defs: %d definizioni", len(defs))
        return defs

    # async def async_setup_slave_entities(self) -> List[Any]:
    #     """
    #     Crea e registra le entity "slave" (es. sensori di dew-point per area).
    #     """
    #     slave_sensors = []

    #     # Presumo che self._runtime.climate.areas sia una lista di oggetti con
    #     # attributi: .indoor (bool), .name (str), .sensors (compatibile con DewpointSensor)
    #     for area in getattr(self._runtime.climate, "areas", []):
    #         if not getattr(area, "indoor", False):
    #             continue

    #         sensors = getattr(area, "sensors", None)
    #         if not sensors:
    #             _LOGGER.debug("Area '%s' senza sensors; salto", getattr(area, "name", "?"))
    #             continue

    #         entity_name = f"Ambient {area.name}"
    #         temperature_unit = self._runtime.climate.temperature_unit

    #         slave_sensors.append(
    #             DewpointSensor(
    #                 hass=self._hass,
    #                 coordinator=self,
    #                 entry=self._entry,
    #                 name=entity_name,
    #                 sensors=sensors,
    #                 temperature_unit=temperature_unit,
    #             )
    #         )

    #     return slave_sensors
    # ---------------------- Event handling -------------------------- #

    @callback
    def entity_changed(self, event: Event[EventStateChangedData]) -> None:
        """Gestisce variazioni di stato sensori/attuatori sottoscritti."""
        if getattr(self, "_stop_event", None) and self._stop_event.is_set():
            return

        entity_id = event.data.get("entity_id")
        new_state = event.data.get("new_state")
        if not entity_id or new_state is None:
            return

        try:
            self._entities_state[entity_id] = new_state
            # _LOGGER.debug("State changed: %s -> %s", entity_id, new_state.state)
        except Exception as ex:  # estrema difesa: non far mai esplodere il job
            log_warning(_LOGGER, "Ignore state change for %s (%s)", entity_id, ex)

        # NOTA: non toccare async_set_updated_data qui
        # self.async_set_updated_data({})
        self.async_update_listeners()   # avvisa le entity senza resettare l’interval

    # ---------------------- Lifecycle hooks ------------------------- #

    # async def _async_temp_test_weater(self) -> None:
    #     lat, lon = 41.9238, 12.4125

    #     cfg = PirateWeatherConfig(
    #         api_key="4mtCz6m3gmiEAvgjQdbB9pB6ndZFR67E",
    #         lat=lat,
    #         lon=lon,
    #         units="si",          # °C
    #         # base_url=None      # opzionale, solo per backend HTTP
    #         base_url="https://timemachine.pirateweather.net/forecast",
    #     )

    #     # Opzioni di robustezza/performance
    #     opts = ProviderOptions(
    #         max_concurrency=6,    # limita richieste/conversioni in parallelo
    #         retries=2,            # tentativi aggiuntivi (totale = retries+1)
    #         backoff_base=0.5,
    #         backoff_factor=2.0,
    #         jitter=0.25,
    #         # ttl_seconds=6*3600,   # cache per-day (6h)
    #         http_timeout_s=20,
    #     )
    #     # --- Creazione provider (usa libreria se installata, altrimenti HTTP) ---
    #     historical_provider = get_pirateweather_historical_provider(self._hass, cfg, opts=opts)
    #     weather_forecast = WeatherForecast( self._hass, "weather.home_rome", forecast_type="daily")

    #     weather_season_detector = WeatherSeasonDetector(weatherHistorical=historical_provider, weatherForecast=weather_forecast)

    #     weather_season_data = await weather_season_detector.detect()
    #     _LOGGER.debug("async_config_entry_first_refresh %s", weather_season_data)


    async def async_config_entry_first_refresh(self) -> None:
        """Primo refresh: dopo il SLOW loop, avvia il FAST loop."""
        await super().async_config_entry_first_refresh()
        _LOGGER.debug("First refresh completed")
        await self.async_start_fast_loop()

        


        # # Consigliato: riusare la sessione HTTP con il context manager
        # async with provider:
        #     today = date.today()
        #     window_days = 10

        #     cur_start = today - timedelta(days=window_days - 1)
        #     cur_end = today

        #     # last_start = safe_subtract_one_year(cur_start)
        #     # last_end = safe_subtract_one_year(cur_end)

        #     # --- Leggi gli ultimi 10 giorni ---
        #     current_samples = await provider.daily_range(cur_start, cur_end)

        #     # --- Leggi la finestra “gemella” dell’anno scorso ---
        #     # yearago_samples = await provider.daily_range(last_start, last_end)

        # # --- Stampa riepilogo semplice ---
        # def brief(sample: Forecast) -> str:
        #     return (
        #         f"{sample['datetime']}  "
        #         f"Tmin={sample.get('templow')}  Tmax={sample.get('temperature')}  "
        #         # f"Tmean={sample.tmean}  DP={sample.dew_point}  RH={sample.humidity}"
        #     )

        # print("\n== Ultimi 10 giorni ==")
        # for s in current_samples:
        #     _LOGGER.debug(brief(s))

        # # print("\n== Finestra gemella anno scorso ==")
        # # for s in yearago_samples:
        # #     print(brief(s))


    async def async_start_fast_loop(self) -> None:
        """Avvia il loop FAST (PID miscelatrice / H% VMC / rate limit)."""
        if self._fast_task:
            return
        self._stop_event.clear()
        self._fast_task = asyncio.create_task(self._fast_loop(), name="drp_fast_loop")

    async def async_stop(self) -> None:
        """Stop coordinato del loop FAST e unsubscription eventi."""
        self._stop_event.set()

        if self._fast_task:
            self._fast_task.cancel()
            with contextlib.suppress(Exception):
                await self._fast_task
            self._fast_task = None

        # Unsubscribe ai cambi stato se presente
        unsub = getattr(self, "_unsub_state_changes", None)
        if callable(unsub):
            with contextlib.suppress(Exception):
                unsub()

    async def _fast_loop(self) -> None:
        """
        Ciclo FAST: esegue controlli locali con cadenza breve.
        Deve essere idempotente e tollerante a snapshot parziali.
        """
        interval = getattr(self._runtime, "fast_interval", 5)  # fallback 5s
        try:
            while not self._stop_event.is_set():
                # TODO: PID miscelatrice verso T_supply_target
                # TODO: PID deumidifica VMC verso target_rh_pct
                # TODO: rate-limit, min_on/min_off, guardie runtime
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass

    # --------------------- DataUpdateCoordinator -------------------- #

    def _debug_dump_entities_state(self, *, max_attr_len: int = 400) -> None:
        """Logga l'istantanea di self._entities_state (entity -> State)."""
        try:
            items = list(self._entities_state.items())
            items.sort(key=lambda kv: kv[0])  # ordina per entity_id

            lines: list[str] = []
            for entity_id, st in items:
                if st is None:
                    lines.append(f"- {entity_id}: <None>")
                    continue

                # Attributi (JSON safe + trunc)
                try:
                    attrs_json = json.dumps(st.attributes, ensure_ascii=False, default=str)
                except Exception:
                    attrs_json = str(st.attributes)

                if len(attrs_json) > max_attr_len:
                    attrs_json = attrs_json[:max_attr_len] + f"...(+{len(attrs_json)-max_attr_len} chars)"

                friendly = st.attributes.get("friendly_name")
                lines.append(
                    f"--------------------------------------------\n"
                    f"id: {entity_id} - state={repr(st.state)}\n"
                    f"{f'({friendly})' if friendly else ''}: \n"
                    f"last change={getattr(st, 'last_changed', None)} - last update={getattr(st, 'last_updated', None)}\n"
                    f"attrs={attrs_json}\n"
                )

            _LOGGER.debug("Entities state snapshot (%d items):\n%s", len(items), "\n".join(lines))
        except Exception as ex:
            _LOGGER.debug("Failed dumping entities state: %s", ex)

    async def _async_update_data(self) -> dict[str, Any]:
        """
        Loop SLOW: raccoglie sensori, calcola grandezze derivate e aggiorna lo snapshot.
        Importante: niente side-effect (niente comandi agli attuatori).
        """
        try:
            # _LOGGER.debug("_entities_state keys %s", self._entities_state.keys())
            # TODO: leggere da adapters e costruire snapshot parziale
            # Esempio:
            # snapshot = {
            #     "timestamp": self._hass.helpers.event.async_call_later(...),
            #     "areas": {...},
            # }
            # await self._async_temp_test_weater()
            self._season_data = await self._season_detector.detect()
            log_debug(_LOGGER, "TEST A\n%s", self._season_data)
            log_info(_LOGGER, "(RuntimeConfig) 33 %s %s", id(self), self._runtime_2)
            plat_snapshot: PlantSnapshot = take_plant_snapshot(
                self._runtime,
                self._season_data,
                self._entities_state,
                now_tz(ha_timezone(self._hass)[1])
            )
            log_debug(_LOGGER, "TEST B\n%s", plat_snapshot)

            # core_rooms: list[SensorPair] = []
            # core1 = plat_snapshot.zones.get("Master Bedroom") if plat_snapshot.zones else None
            # if core1 and core1.sensors is not None:
            #     core_rooms.append(core1.sensors)

            # # aggiungi altre zone eventuali con la stessa logica...

            # if core_rooms and plat_snapshot.mean_apt and plat_snapshot.outdoor:
            #     sts = SeasonThresholdStrategy(
            #         season_state=self._season_data,
            #         core_rooms=core_rooms,              # list[SensorPair]
            #         secondary_rooms=[],
            #         indoor_sensors=plat_snapshot.mean_apt,   # assicurati che non siano Optional
            #         outdoor_sensors=plat_snapshot.outdoor,
            #     )

            #     await sts.compute()
            #     thr = sts.get_threshold()

            #     log_info(_LOGGER, "Computed thresholds: %s", thr)


            # self._debug_dump_entities_state()

            return {}
        except Exception as exc:
            log_warning(_LOGGER, "Update failed: %s", exc, exc_info=True)
            raise UpdateFailed(f"Update failed: {exc}") from exc
