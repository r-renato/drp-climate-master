#
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Dict, Mapping, Optional, List
from datetime import timedelta, date
from .enums import Seasons

# ======================================================
# Entities
# ======================================================

# ---- Aree ---------------------------------------------------------------
@dataclass(frozen=True)
class SensorPair:
    temperature: str
    humidity: str

@dataclass(frozen=True)
class AreaConfig:
    name: str
    indoor: bool
    radiant: bool
    sensors: SensorPair
    thermal_collector_valve_switch: Optional[str] = None
    mq: Optional[float] = None

# ---- Supply units -------------------------------------------------------
@dataclass(frozen=True)
class SupplyUnitSensors:
    boiler_temp_system_supply: str
    boiler_temp_system_return: str
    adjustable_temp_system_supply: str
    adjustable_temp_system_return: str
    direct_temp_system_supply: str
    direct_temp_system_return: str

@dataclass(frozen=True)
class SupplyUnitsConfig:
    direct_supply_unit: str
    adjustable_supply_unit: str
    three_point_mixing_valve: str
    sensors: SupplyUnitSensors

# ---- Radiant ------------------------------------------------------------
@dataclass(frozen=True)
class ModeConfig:
    actuator: str
    heating: int
    cooling: int

@dataclass(frozen=True)
class SetpointConfig:
    actuator: str
    value: float

@dataclass(frozen=True)
class RadiantSensors:
    pdc_temp_water_in: str
    pdc_temp_water_out: str

@dataclass(frozen=True)
class RadiantConfig:
    fm_power: str
    power: str
    mode: ModeConfig
    heating_t_setpoint: SetpointConfig
    heating_dt_setpoint: SetpointConfig
    cooling_t_setpoint: SetpointConfig
    cooling_dt_setpoint: SetpointConfig
    sensors: RadiantSensors

# ---- VMC ----------------------------------------------------------------
@dataclass(frozen=True)
class SeasonConfig:
    actuator: str
    winter: str
    summer: str
    autumn: str
    spring: str

@dataclass(frozen=True)
class CompressorManagementConfig:
    actuator: str
    dehumidification_or_cooling: int
    dehumidification_only: int
    cooling_only: int

@dataclass(frozen=True)
class CoolingManagementConfig:
    actuator: str
    compressor_only: int
    water_only: int
    first_water_then_compressor: int

@dataclass(frozen=True)
class VMCRequestsConfig:
    water: str
    dehumidification: str
    heating: str
    cooling: str

@dataclass(frozen=True)
class VMCSensorsConfig:
    t_ambient: str
    h_ambient: str
    t_water: str
    t_outdoor: str
    power_on_night: str
    power_on_today: str

@dataclass(frozen=True)
class VMCAlarmsConfig:
    high_pressure: str
    dew_point: str
    low_water_temp: str
    high_water_temp: str
    alarm: str

@dataclass(frozen=True)
class VMCConfig:
    power: str
    t_setpoint: str
    h_setpoint: str
    t_dew_point_setpoint: str
    delta_t_dew_point_setpoint: str
    spare_setpoint: str
    vent_recirculation: str
    force_heating: str
    force_cooling: str
    force_free_cooling: str
    season: SeasonConfig
    compressor_management: CompressorManagementConfig
    cooling_management: CoolingManagementConfig
    requests: VMCRequestsConfig
    sensors: VMCSensorsConfig
    alarms: VMCAlarmsConfig

# ---- Dispositivi e scenari ----------------------------------------------
@dataclass(frozen=True)
class DevicesConfig:
    supply_units: SupplyUnitsConfig
    radiant: Optional[RadiantConfig] = None
    vmc: Optional[VMCConfig] = None

@dataclass(frozen=True)
class ScenariosConfig:
    vacation: str
    nobodysin: str

@dataclass(frozen=True)
class ClimateConfig:
    name: str
    unique_id: str
    areas: List[AreaConfig]
    devices: DevicesConfig
    home_windows_state: str
    weather: str
    scenarios: ScenariosConfig
    temperature_unit: str

# ---- Runtime -------------------------------------------------------------
@dataclass(frozen=True)
class PlantCapabilities:
    supports_heating: bool = False
    supports_cooling: bool = False
    supports_dehumidifying: bool = False
    supports_ventilation: bool = False
    setpoint_step_c: float = 0.5

@dataclass(frozen=True)
class RuntimeConfig:
    update_interval: timedelta
    capabilities: PlantCapabilities
    manual_override_minutes: int
    climate: ClimateConfig

# ---- SeasonState model ----------------------------------------------
@dataclass(frozen=True, slots=True)
class SeasonState:
    """
    Snapshot of seasonal status and forecast‑aware override.
    - Frozen + slots dataclass (immutable, lightweight)
    - Invariants validation in `__post_init__`
    - Convenience properties (`progress`) and helpers (`with_override`, `to_dict`)
    - Backward‑compatible `copy()` method
    - Friendly `__str__` with optional score/probability table

    `season_probabilities` are expected in percentage (0..100), as in v1.
    """

    # Baseline (calendar) season label
    label: Seasons

    # Season span metrics (inclusive window)
    days: int
    passed: int
    remaining: int

    # Selected season after forecast inference (may equal label)
    overridden: Seasons
    weather_anomaly: bool

    # Diagnostics
    season_scores: Dict[Seasons, float] = field(default_factory=dict)
    season_probabilities: Dict[Seasons, float] = field(default_factory=dict)  # percentage 0..100

    # ---- validation ----------------------------------------------------------
    def __post_init__(self) -> None:  # type: ignore[override]
        if self.days < 1:
            raise ValueError("SeasonState.days must be >= 1")
        if self.passed < 0 or self.remaining < 0:
            raise ValueError("SeasonState.passed/remaining must be >= 0")
        # With inclusive window: passed + remaining == days - 1 (normally)
        if (self.passed + self.remaining) > (self.days - 1):
            raise ValueError("SeasonState: passed + remaining cannot exceed days - 1")

        # Probability sanity (0..100)
        for p in self.season_probabilities.values():
            if p < 0.0 or p > 100.0:
                raise ValueError("SeasonState: probabilities must be in [0, 100]")

    # ---- computed props ------------------------------------------------------
    @property
    def progress(self) -> float:
        """Return seasonal progress in [0, 1].

        Uses: passed / (passed + remaining) with inclusive bounds.
        """
        denom = self.passed + self.remaining
        if denom <= 0:
            return 0.0
        return round(self.passed / denom, 4)

    # ---- helpers -------------------------------------------------------------
    def with_override(
        self,
        *,
        season: Seasons,
        scores: Mapping[Seasons, float] | None = None,
        probabilities: Mapping[Seasons, float] | None = None,
        anomaly: bool | None = None,
    ) -> "SeasonState":
        """Return a new SeasonState with a different override and diagnostics."""
        return replace(
            self,
            overridden=season,
            weather_anomaly=(self.label != season) if anomaly is None else anomaly,
            season_scores=dict(scores) if scores is not None else self.season_scores,
            season_probabilities=dict(probabilities) if probabilities is not None else self.season_probabilities,
        )

    def copy(self) -> "SeasonState":
        """Backward-compatible copy (object is already immutable)."""
        return replace(self)

    def to_dict(self) -> Dict[str, object]:
        """Serialize to a plain dict with enum values for JSON/logging."""
        return {
            "label": self.label.value,
            "days": self.days,
            "passed": self.passed,
            "remaining": self.remaining,
            "overridden": self.overridden.value,
            "weather_anomaly": self.weather_anomaly,
            "season_scores": {k.value: v for k, v in self.season_scores.items()},
            "season_probabilities": {k.value: v for k, v in self.season_probabilities.items()},
            "progress": self.progress,
        }

    def __str__(self) -> str:  # pragma: no cover
        lines = [
            f"Season label       :: {self.label.value}",
            f"Total days         :: {self.days}",
            f"Days passed        :: {self.passed}",
            f"Days remaining     :: {self.remaining}",
            f"Selected override  :: {self.overridden.value}",
            f"Weather anomaly    :: {self.weather_anomaly}",
        ]

        if self.season_scores or self.season_probabilities:
            lines.append("-" * 67)
            lines.append("Season Scores & Probabilities:")
            lines.append(f"{'Season':<10} {'Score':>10} {'Probability':>14}")

            # Order by probability desc (fallback to score)
            seasons = set(self.season_scores) | set(self.season_probabilities)
            ordered = sorted(
                seasons,
                key=lambda s: (
                    self.season_probabilities.get(s, 0.0),
                    self.season_scores.get(s, 0.0),
                ),
                reverse=True,
            )
            for s in ordered:
                score = self.season_scores.get(s, 0.0)
                prob = self.season_probabilities.get(s, 0.0)
                lines.append(f"{s.value:<10} {score:>10.3f} {prob:>13.1f}%")

        return "\n".join(lines)

# ---- Weather model ----------------------------------------------

@dataclass(slots=True, frozen=True)
class WeatherDailySample:
    day: date
    tmin: float | None
    tmax: float | None
    tmean: float | None
    dew_point: float | None
    humidity: float | None  # normalized to 0..100
