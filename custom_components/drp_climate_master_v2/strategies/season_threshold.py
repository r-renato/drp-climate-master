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

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
import logging
import math
from statistics import mean
from typing import Dict, Mapping, Optional

from ..domain.models.plant import PlantSnapshot

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

    def __str__(self) -> str:
        # ----- helpers compatti -----
        def fenum(x):
            if x is None:
                return "-"
            return getattr(x, "value", None) or getattr(x, "name", None) or str(x)

        def fnum(x, nd=1, unit=""):
            return "-" if x is None else f"{x:.{nd}f}{unit}"

        def fperc(x):
            return "-" if x is None else f"{x:.0f}%"

        def line(label: str, value: str) -> str:
            return f"  {label:<20} :: {value}"

        # ----- header -----
        lines: list[str] = []
        lines.append("------------------------------------------------------------------")
        lines.append("SEASON THRESHOLDS")

        # ----- campi principali -----
        lines.append(line("Season",            fenum(self.season)))
        lines.append(line("Profile",           fenum(self.profile)))
        lines.append(line("Temperature Min",   fnum(self.temperature_min, 1, "°C")))
        lines.append(line("Temperature Max",   fnum(self.temperature_max, 1, "°C")))
        lines.append(line("Humidity Min",      fperc(self.humidity_min)))
        lines.append(line("Humidity Max",      fperc(self.humidity_max)))
        lines.append(line("DP Target",         fnum(self.dew_point_target, 1, "°C")))
        lines.append(line("Computed At",       self.computed_at.isoformat()))
        vu = self.valid_until
        lines.append(line("Valid Until",       vu.isoformat() if vu else "-"))
        lines.append(line("TTL",               f"{self.ttl_seconds}s" if self.ttl_seconds is not None else "-"))
        lines.append(line("Expired",           "yes" if self.is_expired() else "no"))

        # ----- HVAC hysteresis -----
        try:
            hv = self.hvac.to_dict()
        except Exception:
            hv = None

        if isinstance(hv, dict) and hv:
            lines.append(line("HVAC", ""))  # titolo sezione
            for k in sorted(hv):
                # es.: heat_hyst -> "Heat Hyst"
                pretty_k = k.replace("_", " ").title()
                v = hv[k]
                # prova a formattare numeri con 1 decimale, altrimenti str()
                v_str = f"{v:.1f}" if isinstance(v, (int, float)) else str(v)
                lines.append(line(f"  {pretty_k}", v_str))
        else:
            lines.append(line("HVAC", str(self.hvac)))

        # ----- Zone -----
        if self.zones:
            lines.append(line("Zones", str(len(self.zones))))
            for name, zthr in sorted(self.zones.items()):
                # Prova ad estrarre alcuni campi comuni via to_dict()
                tmin = tmax = hmin = hmax = dpt = None
                try:
                    zd = zthr.to_dict()  # type: ignore[attr-defined]
                    tmin = zd.get("temperature_min")
                    tmax = zd.get("temperature_max")
                    hmin = zd.get("humidity_min")
                    hmax = zd.get("humidity_max")
                    dpt  = zd.get("dew_point_target")
                except Exception:
                    pass

                lines.append(f"  {name}")
                if any(v is not None for v in (tmin, tmax, hmin, hmax, dpt)):
                    lines.append(line("    T Range", f"{fnum(tmin,1,'°C')} – {fnum(tmax,1,'°C')}"))
                    lines.append(line("    RH Range", f"{fperc(hmin)} – {fperc(hmax)}"))
                    lines.append(line("    DP Target", fnum(dpt,1,'°C')))
                else:
                    # fallback: stampa l’oggetto così com’è
                    lines.append(line("    Threshold", str(zthr)))
        else:
            lines.append(line("Zones", "0"))

        return "\n".join(lines)



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
        """Build a dynamic comfort band using the current plant snapshot."""

        snapshot = self._plant_snapshot

        def _safe_mean(values: list[float]) -> Optional[float]:
            return mean(values) if values else None

        def _clamp(value: float, lower: float, upper: float) -> float:
            return max(lower, min(upper, value))

        def _estimate_dew_point(temp: Optional[float], rh: Optional[float]) -> Optional[float]:
            if temp is None or rh is None:
                return None
            if rh <= 0.0 or rh > 100.0:
                return None
            a = 17.625
            b = 243.04
            gamma = (a * temp) / (b + temp) + math.log(rh / 100.0)
            return (b * gamma) / (a - gamma)

        zones = snapshot.zones or {}
        zone_temps: list[float] = []
        zone_humidities: list[float] = []
        zone_dew_points: list[float] = []

        for zone in zones.values():
            sensors = zone.sensors
            if sensors is None:
                continue
            if sensors.temperature is not None:
                zone_temps.append(float(sensors.temperature))
            if sensors.humidity is not None:
                zone_humidities.append(float(sensors.humidity))
            if sensors.dew_point is not None:
                zone_dew_points.append(float(sensors.dew_point))
            else:
                dp_estimate = _estimate_dew_point(
                    float(sensors.temperature) if sensors.temperature is not None else None,
                    float(sensors.humidity) if sensors.humidity is not None else None,
                )
                if dp_estimate is not None:
                    zone_dew_points.append(dp_estimate)

        indoor_temp_avg = _safe_mean(zone_temps)
        if indoor_temp_avg is None and snapshot.mean_apt and snapshot.mean_apt.temperature is not None:
            indoor_temp_avg = float(snapshot.mean_apt.temperature)

        indoor_humidity_avg = _safe_mean(zone_humidities)
        if indoor_humidity_avg is None and snapshot.mean_apt and snapshot.mean_apt.humidity is not None:
            indoor_humidity_avg = float(snapshot.mean_apt.humidity)

        dew_point_avg = _safe_mean(zone_dew_points)
        if (
            dew_point_avg is None
            and indoor_temp_avg is not None
            and indoor_humidity_avg is not None
        ):
            dew_point_avg = _estimate_dew_point(indoor_temp_avg, indoor_humidity_avg)

        season_state = snapshot.season
        season = season_state.overridden if season_state else Seasons.WINTER

        base_center_map: Dict[Seasons, float] = {
            Seasons.WINTER: 21.0,
            Seasons.SPRING: 21.5,
            Seasons.SUMMER: 24.5,
            Seasons.AUTUMN: 21.5,
        }
        base_center = base_center_map.get(season, 21.0)

        if indoor_temp_avg is not None:
            center_temp = 0.7 * base_center + 0.3 * indoor_temp_avg
        else:
            center_temp = base_center

        if snapshot.presence_vacation:
            profile = HVACOperatingProfile.VACATION
            profile_bias = 2.0
        elif snapshot.presence_nobodysin:
            profile = HVACOperatingProfile.ECO
            profile_bias = 1.0
        else:
            profile = HVACOperatingProfile.COMFORT
            profile_bias = 0.0

        if season in (Seasons.WINTER, Seasons.AUTUMN):
            center_temp -= profile_bias
        else:
            center_temp += profile_bias

        base_half_range_map: Dict[Seasons, float] = {
            Seasons.WINTER: 0.7,
            Seasons.SPRING: 0.6,
            Seasons.SUMMER: 1.0,
            Seasons.AUTUMN: 0.7,
        }
        base_half_range = base_half_range_map.get(season, 0.7)

        temp_spread = (max(zone_temps) - min(zone_temps)) if len(zone_temps) >= 2 else 0.0
        half_range = base_half_range + min(temp_spread * 0.25, 0.5)

        if snapshot.apt_windows_open:
            half_range += 0.2

        if profile is HVACOperatingProfile.VACATION:
            half_range += 0.3

        half_range = _clamp(half_range, 0.5, 1.5)

        temperature_min = center_temp - half_range
        temperature_max = center_temp + half_range

        humidity_center_map: Dict[Seasons, float] = {
            Seasons.WINTER: 45.0,
            Seasons.SPRING: 50.0,
            Seasons.SUMMER: 55.0,
            Seasons.AUTUMN: 50.0,
        }
        humidity_center = humidity_center_map.get(season, 50.0)
        if indoor_humidity_avg is not None:
            humidity_center = 0.6 * humidity_center + 0.4 * indoor_humidity_avg

        humidity_half_range_map: Dict[Seasons, float] = {
            Seasons.WINTER: 7.5,
            Seasons.SPRING: 8.0,
            Seasons.SUMMER: 10.0,
            Seasons.AUTUMN: 8.0,
        }
        humidity_half_range = humidity_half_range_map.get(season, 8.0)

        if profile is HVACOperatingProfile.VACATION:
            humidity_half_range += 1.0
        humidity_half_range = _clamp(humidity_half_range, 6.0, 12.0)

        humidity_min = _clamp(humidity_center - humidity_half_range, 30.0, 65.0)
        humidity_max = _clamp(humidity_center + humidity_half_range, 35.0, 70.0)
        if humidity_min > humidity_max:
            humidity_min = humidity_max

        dew_point_margin = 2.0 if season in (Seasons.WINTER, Seasons.AUTUMN) else 3.0
        dew_point_target: Optional[float] = None

        if indoor_temp_avg is not None:
            safe_limit = indoor_temp_avg - dew_point_margin
            if dew_point_avg is not None:
                dew_point_target = min(dew_point_avg, safe_limit)
            else:
                dew_point_target = safe_limit

        heating_gap = _clamp(half_range * 0.9, 0.4, 1.0)
        cooling_gap = heating_gap

        hvac = HVACHysteresis(
            heating_on=temperature_min,
            heating_off=min(temperature_max, temperature_min + heating_gap),
            cooling_on=temperature_max,
            cooling_off=max(temperature_min, temperature_max - cooling_gap),
            dehumidifying_on=dew_point_target,
            dehumidifying_off=(
                dew_point_target - 1.0 if dew_point_target is not None else None
            ),
        )

        zone_thresholds: Dict[str, ZoneThreshold] = {}
        for zone_name, zone in zones.items():
            sensors = zone.sensors
            zone_temp = float(sensors.temperature) if sensors and sensors.temperature is not None else None
            zone_humidity = float(sensors.humidity) if sensors and sensors.humidity is not None else None
            zone_dew_point = float(sensors.dew_point) if sensors and sensors.dew_point is not None else None

            zone_temp_offset = 0.0
            if zone_temp is not None and indoor_temp_avg is not None:
                zone_temp_offset = _clamp(zone_temp - indoor_temp_avg, -2.0, 2.0)

            zone_temp_adjust = 0.3 * zone_temp_offset
            zone_temp_min = temperature_min + zone_temp_adjust
            zone_temp_max = temperature_max + zone_temp_adjust

            zone_humidity_adjust = 0.0
            if zone_humidity is not None:
                zone_humidity_adjust = _clamp(
                    zone_humidity - humidity_center,
                    -10.0,
                    10.0,
                )
            zone_humidity_min = _clamp(humidity_min + 0.3 * zone_humidity_adjust, 28.0, 70.0)
            zone_humidity_max = _clamp(humidity_max + 0.3 * zone_humidity_adjust, 32.0, 75.0)
            if zone_humidity_min > zone_humidity_max:
                zone_humidity_min = zone_humidity_max

            zone_dew_target = dew_point_target
            if zone_dew_point is not None:
                zone_dew_target = zone_dew_point if zone_dew_target is None else min(zone_dew_point, zone_dew_target)

            if zone_temp is not None and zone_dew_target is not None:
                zone_dew_target = min(zone_dew_target, zone_temp - 0.8)

            zone_dew_margin: Optional[float] = None
            if zone.flow_t is not None:
                reference = zone_dew_target if zone_dew_target is not None else zone_temp
                if reference is not None:
                    zone_dew_margin = _clamp(zone.flow_t - reference, 1.0, 6.0)
            elif zone_dew_target is not None:
                zone_dew_margin = 2.5

            zone_hvac = HVACHysteresis(
                heating_on=zone_temp_min,
                heating_off=min(zone_temp_max, zone_temp_min + heating_gap),
                cooling_on=zone_temp_max,
                cooling_off=max(zone_temp_min, zone_temp_max - cooling_gap),
                dehumidifying_on=zone_dew_target,
                dehumidifying_off=(
                    zone_dew_target - 1.0 if zone_dew_target is not None else None
                ),
            )

            zone_thresholds[zone_name] = ZoneThreshold(
                zone=zone_name,
                temperature_min=zone_temp_min,
                temperature_max=zone_temp_max,
                humidity_min=zone_humidity_min,
                humidity_max=zone_humidity_max,
                dew_point_target=zone_dew_target,
                hvac=zone_hvac,
                dew_point_margin=zone_dew_margin,
            )

        threshold = SeasonThreshold(
            season=season,
            profile=profile,
            temperature_min=temperature_min,
            temperature_max=temperature_max,
            humidity_min=humidity_min,
            humidity_max=humidity_max,
            dew_point_target=dew_point_target,
            hvac=hvac,
            zones=zone_thresholds,
            ttl_seconds=self._default_ttl_seconds,
        )

        _LOGGER.debug("Season thresholds built", extra=threshold.to_dict())

        return threshold





