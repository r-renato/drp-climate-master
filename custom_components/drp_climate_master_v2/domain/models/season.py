#
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Dict, Mapping

from ..enums import Seasons

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

    # Baseline (calendar) season season
    season: Seasons

    # Season span metrics (inclusive window)
    days: int
    passed: int
    remaining: int

    # Selected season after forecast inference (may equal season)
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
            weather_anomaly=(self.season != season) if anomaly is None else anomaly,
            season_scores=dict(scores) if scores is not None else self.season_scores,
            season_probabilities=dict(probabilities) if probabilities is not None else self.season_probabilities,
        )

    def copy(self) -> "SeasonState":
        """Backward-compatible copy (object is already immutable)."""
        return replace(self)

    def to_dict(self) -> Dict[str, object]:
        """Serialize to a plain dict with enum values for JSON/logging."""
        return {
            "label": self.season.value,
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
            f"Season label       :: {self.season.value}",
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
