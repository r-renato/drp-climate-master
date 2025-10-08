# #
# from __future__ import annotations

# from datetime import date, datetime, time
# from statistics import mean
# from typing import Protocol, Optional, Dict, Tuple
# import logging
# import time as _time

# from ..domain.models import SeasonState, SensorPair

# _LOGGER = logging.getLogger(__name__)

# # ===== Implementation ===========================================================
# class SeasonThresholdStrategy:
#     """
#     Strategia stagionale **dinamica** (senza comfort zone statica).

#     Scopo
#     -----
#     Calcolare, in funzione della stagione e dei segnali indoor/outdoor, una banda
#     di comfort **dinamica** (T_min/T_max, RH_min/RH_max, DP_set) e le soglie
#     operative **ON/OFF** per i modi HVAC (heating, cooling, dehumidifying).
#     I risultati sono pensati per alimentare il controllo valvole/stati clima.

#     Parametri
#     ---------
#     season_state : SeasonState
#         Contesto stagionale corrente/target (es. WINTER/SPRING/SUMMER/AUTUMN)
#         ed eventuale profilo di isteresi (comfort/eco/away) e/o fattori custom.
#     core_rooms : list[SensorPair]
#         Stanze principali (prioritarie) con coppie sensore (temperatura, umidità).
#         Guidano il calcolo del centro banda, la stima del bias e gli interlock DP.
#     secondary_rooms : list[SensorPair]
#         Stanze secondarie usate per robustezza (outlier rejection/media robusta).
#     indoor_sensors : SensorPair
#         Sensori “aggregati appartamento” (tipicamente media/mediana T e UR). Utili
#         per bias/stabilità globale e come riferimento quando i segnali per stanza
#         sono scarsi o rumorosi.
#     outdoor_sensors : SensorPair
#         Sensori esterni T/UR (corrente + storico/forecast a valle dell’adapter)
#         usati per running-mean outdoor temperature (adaptive comfort) e contesto
#         igrometrico/ventilazione.

#     Responsabilità
#     --------------
#     1) Normalizzare/validare letture (filtri base, mediane/mediane pesate, outliers).
#     2) Stimare il **centro termico** della banda (es. modello adaptive da T_esterna
#        “running mean” + bias appreso da storico indoor).
#     3) Determinare l’**ampiezza termica** ΔT in base a profilo (comfort/eco/away),
#        varianza dei segnali, inerzia impianto e volatilità meteo.
#     4) Costruire la **banda igrometrica** con **vincolo anti-condensa**:
#        DP_set ≤ (T_superficie_min − margine); in assenza di T_superficie, usare
#        regole conservative in funzione di T_indoor.
#     5) Derivare soglie HVAC con isteresi sicura:
#        - Heating: ON vicino a T_min, OFF vicino a T_max
#        - Cooling: ON vicino a T_max (offset), OFF vicino a T_min (offset)
#        - Dehumidifying: ON a DP_set, OFF a DP_set − ΔDP (solo stagioni umide)
#     6) Esporre helper/interlock per stanza (es. chiusura valvola se DP_stanza ≥ DP_set).
#     7) Gestire **cache/TTL** e invalidazione per ricalcoli efficienti.

#     Output atteso
#     -------------
#     - Oggetti `SeasonThreshold` per la stagione/profilo correnti, con:
#       setpoint dinamici (T/RH/DP) e soglie HVAC (heating/cooling/dehumidifying).
#     - (Opzionale) metriche diagnostiche: bias stimati, gap d’isteresi applicati,
#       clamp su RH/DP, motivazioni di interlock.

#     Note d’uso
#     ----------
#     - La classe non effettua I/O diretto verso Home Assistant/Influx: si aspetta
#       che i `SensorPair` e gli adapter a monte forniscano i valori correnti/storici.
#     - Metodi che eseguono calcoli intensivi o accesso a storici/forecast sono
#       previsti asincroni e con caching per ridurre latenza e carico.
#     - Validazioni/Clamp: garantire ordine min≤max, 0≤RH≤100, gap di isteresi ≥ soglia.

#     Esempio d’uso (schematico)
#     --------------------------
#         sts = SeasonThresholdStrategy(season_state, core_rooms, secondary_rooms,
#                                       indoor_sensors, outdoor_sensors)
#         await sts.compute()                      # calcolo/refresh thresholds
#         thr = sts.get_threshold()               # SeasonThreshold corrente
#         # usare `thr` per orchestrare heating/cooling/dehumidifying e interlock DP
#     """
#     def __init__(
#         self,
#         season_state: SeasonState,
#         core_rooms: list[SensorPair],
#         secondary_rooms: list[SensorPair],
#         indoor_sensors: SensorPair,
#         outdoor_sensors: SensorPair,
#     ) -> None:



#
from __future__ import annotations

from datetime import date, datetime, time
from statistics import mean
from typing import Protocol, Optional, Dict, Tuple
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Dict, Mapping, Optional
import logging
import time as _time

from ..domain.models.plant import PlantSnapshot, ZoneSnapshot

from ..domain.models.runtime_schema import SensorPair
from ..domain.models.season import SeasonState

from ..domain.enums import HVACOperatingProfile, Seasons

_LOGGER = logging.getLogger(__name__)

# ===== Data models ============================================================


@dataclass(slots=True, frozen=True)
class HVACHysteresis:
    """ON/OFF thresholds for the supported HVAC operating modes."""

    heating_on: Optional[float] = None
    heating_off: Optional[float] = None
    cooling_on: Optional[float] = None
    cooling_off: Optional[float] = None
    dehumidifying_on: Optional[float] = None
    dehumidifying_off: Optional[float] = None

    def __post_init__(self) -> None:  # type: ignore[override]
        if (
            self.heating_on is not None
            and self.heating_off is not None
            and self.heating_on > self.heating_off
        ):
            raise ValueError("Heating ON threshold must be <= OFF threshold")

        if (
            self.cooling_on is not None
            and self.cooling_off is not None
            and self.cooling_on < self.cooling_off
        ):
            raise ValueError("Cooling ON threshold must be >= OFF threshold")

        if (
            self.dehumidifying_on is not None
            and self.dehumidifying_off is not None
            and self.dehumidifying_on < self.dehumidifying_off
        ):
            raise ValueError("Dehumidifying ON threshold must be >= OFF threshold")

    def to_dict(self) -> Dict[str, Optional[float]]:
        """Return a serialisable representation of the hysteresis configuration."""

        return {
            "heating_on": self.heating_on,
            "heating_off": self.heating_off,
            "cooling_on": self.cooling_on,
            "cooling_off": self.cooling_off,
            "dehumidifying_on": self.dehumidifying_on,
            "dehumidifying_off": self.dehumidifying_off,
        }


@dataclass(slots=True, frozen=True)
class ZoneThreshold:
    """Comfort band and HVAC thresholds for a specific zone/room."""

    zone: str
    temperature_min: float
    temperature_max: float
    humidity_min: float
    humidity_max: float
    dew_point_target: Optional[float]
    hvac: HVACHysteresis
    dew_point_margin: Optional[float] = None

    def __post_init__(self) -> None:  # type: ignore[override]
        if self.temperature_min > self.temperature_max:
            raise ValueError("temperature_min must be <= temperature_max")

        if self.humidity_min < 0.0 or self.humidity_max > 100.0:
            raise ValueError("humidity range must stay within [0, 100]")

        if self.humidity_min > self.humidity_max:
            raise ValueError("humidity_min must be <= humidity_max")

        if self.dew_point_margin is not None and self.dew_point_margin < 0:
            raise ValueError("dew_point_margin must be >= 0")

    def to_dict(self) -> Dict[str, Optional[float]]:
        """Return a serialisable representation for diagnostics/export."""

        payload: Dict[str, Optional[float]] = {
            "temperature_min": self.temperature_min,
            "temperature_max": self.temperature_max,
            "humidity_min": self.humidity_min,
            "humidity_max": self.humidity_max,
            "dew_point_target": self.dew_point_target,
        }

        if self.dew_point_margin is not None:
            payload["dew_point_margin"] = self.dew_point_margin

        payload.update({f"hvac_{k}": v for k, v in self.hvac.to_dict().items()})
        return payload


@dataclass(slots=True, frozen=True)
class SeasonThreshold:
    """Aggregated thresholds derived for the current season/profile."""

    season: Seasons
    profile: Optional[HVACOperatingProfile]
    temperature_min: float
    temperature_max: float
    humidity_min: float
    humidity_max: float
    dew_point_target: Optional[float]
    hvac: HVACHysteresis
    zones: Mapping[str, ZoneThreshold] = field(default_factory=dict)
    computed_at: datetime = field(default_factory=lambda: datetime.utcnow())
    ttl_seconds: Optional[int] = None

    def __post_init__(self) -> None:  # type: ignore[override]
        if self.temperature_min > self.temperature_max:
            raise ValueError("temperature_min must be <= temperature_max")

        if self.humidity_min < 0.0 or self.humidity_max > 100.0:
            raise ValueError("humidity range must stay within [0, 100]")

        if self.humidity_min > self.humidity_max:
            raise ValueError("humidity_min must be <= humidity_max")

        if self.ttl_seconds is not None and self.ttl_seconds < 0:
            raise ValueError("ttl_seconds must be >= 0 or None for no expiry")

    @property
    def valid_until(self) -> Optional[datetime]:
        """Return the expiration timestamp if a TTL is defined."""

        if self.ttl_seconds is None:
            return None
        return self.computed_at + timedelta(seconds=self.ttl_seconds)

    def is_expired(self, *, now: Optional[datetime] = None) -> bool:
        """Check whether the threshold cache is still valid."""

        if self.ttl_seconds in (None, 0):
            return False

        reference = now or datetime.utcnow()
        expiry = self.computed_at + timedelta(seconds=self.ttl_seconds)
        return reference >= expiry

    def to_dict(self) -> Dict[str, object]:
        """Serialize the full threshold payload for logging or diagnostics."""

        payload: Dict[str, object] = {
            "season": self.season.value,
            "profile": self.profile.value if self.profile else None,
            "temperature_min": self.temperature_min,
            "temperature_max": self.temperature_max,
            "humidity_min": self.humidity_min,
            "humidity_max": self.humidity_max,
            "dew_point_target": self.dew_point_target,
            "hvac": self.hvac.to_dict(),
            "computed_at": self.computed_at.isoformat(),
            "ttl_seconds": self.ttl_seconds,
        }

        if self.zones:
            payload["zones"] = {
                zone: zone_threshold.to_dict() for zone, zone_threshold in self.zones.items()
            }

        valid_until = self.valid_until
        if valid_until is not None:
            payload["valid_until"] = valid_until.isoformat()

        return payload

    def get_zone(self, zone: str) -> ZoneThreshold:
        """Return the zone threshold or raise ``KeyError`` if missing."""

        if zone not in self.zones:
            raise KeyError(f"Zone '{zone}' not available in current thresholds")
        return self.zones[zone]


class ThresholdNotComputedError(RuntimeError):
    """Raised when the strategy cache is accessed before any computation."""


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
@@ -68,25 +247,131 @@ class SeasonThresholdStrategy:

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
        plant_snapshot: PlantSnapshot
    ) -> None:
        self._plant_snapshot = plant_snapshot

        self._default_ttl_seconds: Optional[int] = 15 * 60  # 15 minutes by default
        self._cached_threshold: Optional[SeasonThreshold] = None

    # ------------------------------------------------------------------
    # @property
    # def season_state(self) -> SeasonState:
    #     """Return the current seasonal context used by the strategy."""

    #     return self._season_state

    # def update_season_state(self, season_state: SeasonState) -> None:
    #     """Update the seasonal context and invalidate cached thresholds."""

    #     if season_state != self._season_state:
    #         self._season_state = season_state
    #         self.invalidate_cache()

    def configure_default_ttl(self, ttl_seconds: Optional[int]) -> None:
        """Set the default TTL applied to newly computed thresholds."""

        if ttl_seconds is not None and ttl_seconds < 0:
            raise ValueError("ttl_seconds must be >= 0 or None for no expiry")

        self._default_ttl_seconds = ttl_seconds

    # ------------------------------------------------------------------
    def invalidate_cache(self) -> None:
        """Drop the cached threshold forcing a fresh computation next time."""

        self._cached_threshold = None

    def cache_valid(self) -> bool:
        """Return True if a threshold is cached and still valid."""

        if self._cached_threshold is None:
            return False

        return not self._cached_threshold.is_expired()

    def cache_info(self) -> Dict[str, object]:
        """Expose cache diagnostics for debugging/metrics."""

        if self._cached_threshold is None:
            return {"cached": False, "valid": False}

        threshold = self._cached_threshold
        return {
            "cached": True,
            "valid": not threshold.is_expired(),
            "computed_at": threshold.computed_at,
            "valid_until": threshold.valid_until,
            "ttl_seconds": threshold.ttl_seconds,
        }

    def get_threshold(self, *, allow_stale: bool = False) -> SeasonThreshold:
        """Return the cached threshold, enforcing TTL unless `allow_stale`."""

        if self._cached_threshold is None:
            raise ThresholdNotComputedError("Season thresholds not computed yet")

        if not allow_stale and self._cached_threshold.is_expired():
            raise ThresholdNotComputedError("Cached season thresholds are expired")

        return self._cached_threshold

    def get_zone_threshold(self, zone: str, *, allow_stale: bool = False) -> ZoneThreshold:
        """Return the threshold for a specific zone."""

        return self.get_threshold(allow_stale=allow_stale).get_zone(zone)

    async def compute(
        self,
        *,
        force: bool = False,
        ttl_seconds: Optional[int] = None,
    ) -> SeasonThreshold:
        """Compute thresholds or return the cached value when still valid."""

        if not force and self.cache_valid():
            _LOGGER.debug("SeasonThresholdStrategy cache hit", extra=self.cache_info())
            return self._cached_threshold  # type: ignore[return-value]

        threshold = await self._build_threshold()

        # Apply TTL precedence: explicit parameter > configured default > model value
        effective_ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl_seconds
        if threshold.ttl_seconds != effective_ttl:
            threshold = replace(threshold, ttl_seconds=effective_ttl)

        self._cached_threshold = threshold
        _LOGGER.debug(
            "SeasonThresholdStrategy cache refreshed",
            extra={**self.cache_info(), "force": force, "ttl_override": ttl_seconds},
        )
        return threshold

    async def _build_threshold(self) -> SeasonThreshold:
        """Actual computation placeholder to be implemented in subsequent steps."""

        raise NotImplementedError("SeasonThresholdStrategy._build_threshold is not implemented yet")





