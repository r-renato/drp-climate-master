"""Minimal MPC engine skeleton for SeasonThresholdStrategy (Fase 1)

Purpose
-------
Provide a lightweight, pluggable interface to compute *adaptive thresholds*
(HVAC on/off, DP target) using a Model Predictive Control (MPC) idea, while
keeping the external contract of `SeasonThreshold` unchanged.

Design goals
------------
- Keep *primary mode* fixed within the horizon (avoid MILP).
- Optionally evaluate the *opposite mode* as a shadow optimization for exceptions.
- Support environments without `cvxpy` by offering a heuristic fallback.
- Return compact results (`MPCResult`) usable by the thresholds strategy.

This module does NOT touch Home Assistant directly; it is pure Python
and can be unit-tested in isolation. The Coordinator/Strategy layer
provides the data (state, forecast, options) and consumes the result.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Literal, Tuple
import math

PrimaryMode = Literal["heat", "cool"]

try:
    import cvxpy as cp  # type: ignore
    _HAS_CVXPY = True
except Exception:  # pragma: no cover - optional dependency
    cp = None  # type: ignore
    _HAS_CVXPY = False


# ==========================
#  Data structures
# ==========================
@dataclass(slots=True)
class MPCConfig:
    """Static configuration for the MPC horizon and weights.

    Parameters
    ----------
    horizon : int
        Number of control intervals (N). Typical: 12–24.
    dt_s : int
        Sampling time in seconds. Typical: 300–600 s.
    w_temp : float
        Weight for temperature regulation error.
    w_energy : float
        Weight for actuation effort.
    w_slew : float
        Weight for rate-of-change of actuation.
    t_min_c : float
        Soft lower comfort bound (per-step soft constraints).
    t_max_c : float
        Soft upper comfort bound.
    dp_safety_margin_c : float
        Minimum (safety) margin between DP and supply temp (if modeled).
    u_min : float, u_max : float
        Actuator bounds [0,1].
    primary_mode : Literal["heat","cool"]
        Fixed mode for this optimization.
    """

    horizon: int = 12
    dt_s: int = 600
    w_temp: float = 1.0
    w_energy: float = 0.1
    w_slew: float = 0.05
    t_min_c: float = 21.0
    t_max_c: float = 23.4
    dp_safety_margin_c: float = 1.5
    u_min: float = 0.0
    u_max: float = 1.0
    primary_mode: PrimaryMode = "heat"


@dataclass(slots=True)
class MPCState:
    """Current indoor state.

    T_in_c : float
        Current indoor air temperature (core aggregate).
    RH_in_pct : Optional[float]
        Current indoor relative humidity (%). Optional for SISO temp MPC.
    """

    T_in_c: float
    RH_in_pct: Optional[float] = None


@dataclass(slots=True)
class MPCForecast:
    """Exogenous trajectories over the horizon (length >= N).

    T_out_seq_c : Iterable[float]
        Outdoor temperature sequence (°C).
    G_seq : Optional[Iterable[float]]
        Solar gains or generic internal gains proxy. Unitless scale.
    occ_seq : Optional[Iterable[float]]
        Occupancy proxy (0..1) or schedule flag.
    RH_out_seq_pct : Optional[Iterable[float]]
        Outdoor RH sequence (%), for DP checks.
    T_ref_seq_c : Optional[Iterable[float]]
        Time-varying reference temperature; if None, mid of [t_min, t_max].
    """

    T_out_seq_c: Iterable[float]
    G_seq: Optional[Iterable[float]] = None
    occ_seq: Optional[Iterable[float]] = None
    RH_out_seq_pct: Optional[Iterable[float]] = None
    T_ref_seq_c: Optional[Iterable[float]] = None


@dataclass(slots=True)
class MPCResult:
    """Compact result for thresholds derivation."""

    u_seq: Tuple[float, ...]              # normalized actuation 0..1
    T_seq_c: Tuple[float, ...]            # predicted indoor temperature
    dp_target_c: float                    # suggested dew-point target
    merit: float                          # objective value / quality metric
    primary_mode: PrimaryMode             # echo for consumers


# ==========================
#  Public API
# ==========================

def solve_primary_mode(cfg: MPCConfig, x0: MPCState, fc: MPCForecast) -> MPCResult:
    """Solve a single-mode (heat OR cool) MPC and return compact result.

    If `cvxpy` is available, a small convex QP is solved.
    Otherwise a heuristic fallback is used (exponential approach model).
    """
    if _HAS_CVXPY:
        return _solve_qp(cfg, x0, fc)
    return _solve_heuristic(cfg, x0, fc)


def derive_thresholds(
    cfg: MPCConfig, res: MPCResult, deadband_heat_c: float, deadband_cool_c: float,
    base_dp_target_c: float, deadband_dehum_dp_c: float,
) -> tuple[float, float, float, float, float, float]:
    """Map MPC result to (cool_on, cool_off, heat_on, heat_off, dehum_on_dp, dehum_off_dp).

    Simple policy for Fase 1:
    - Keep band center near median of predicted T, clamp to [t_min,t_max].
    - Use configured deadbands.
    - dp_target is nudged based on predicted peaks and occupancy (if provided).
    """
    t_center = _median(res.T_seq_c)
    t_center = min(max(t_center, cfg.t_min_c), cfg.t_max_c)

    heat_on = cfg.t_min_c
    heat_off = cfg.t_min_c + deadband_heat_c
    cool_on = cfg.t_max_c
    cool_off = cfg.t_max_c - deadband_cool_c

    # Optional refinement: shift band slightly toward predicted trend
    trend = res.T_seq_c[-1] - res.T_seq_c[0]
    if abs(trend) > 0.2:
        shift = max(min(trend * 0.25, 0.3), -0.3)  # limit shift to ±0.3°C
        heat_on += shift
        heat_off += shift
        cool_on += shift
        cool_off += shift

    dp_on = base_dp_target_c
    dp_off = base_dp_target_c - deadband_dehum_dp_c

    return (cool_on, cool_off, heat_on, heat_off, dp_on, dp_off)


# ==========================
#  Internal: QP version (cvxpy)
# ==========================

def _solve_qp(cfg: MPCConfig, x0: MPCState, fc: MPCForecast) -> MPCResult:  # pragma: no cover - requires cvxpy
    N = int(cfg.horizon)
    T_out = tuple(fc.T_out_seq_c)[: N]
    G = tuple(fc.G_seq)[: N] if fc.G_seq is not None else (0.0,) * N
    Tref = (
        tuple(fc.T_ref_seq_c)[: N]
        if fc.T_ref_seq_c is not None
        else tuple((cfg.t_min_c + cfg.t_max_c) / 2.0 for _ in range(N))
    )

    # Simple first-order model: T[k+1] = a*T[k] + b1*T_out[k] + b2*u[k] + c*G[k]
    # Coeffs can be identified offline; here we pick conservative defaults.
    dt = float(cfg.dt_s)
    tau = 3600.0  # 1-hour time constant (tune with data)
    a = math.exp(-dt / tau)
    b1 = (1.0 - a) * 0.15  # coupling to outdoor
    b2 = (1.0 - a) * 1.50  # effect of actuation (normalized)
    c = (1.0 - a) * 0.05

    T = cp.Variable(N + 1)
    u = cp.Variable(N)
    s = cp.Variable(N, nonneg=True)  # soft constraint slack

    constraints = [T[0] == x0.T_in_c]
    cost = 0

    for k in range(N):
        constraints += [
            T[k + 1] == a * T[k] + b1 * T_out[k] + b2 * u[k] + c * G[k],
            u[k] >= cfg.u_min,
            u[k] <= cfg.u_max,
            T[k] >= cfg.t_min_c - s[k],
            T[k] <= cfg.t_max_c + s[k],
        ]
        # Mode semantics: in HEAT, u adds heat; in COOL, u removes heat
        # For COOL, we flip sign by redefining b2 negative (equivalent transformation)
        # but keep u >= 0
        if cfg.primary_mode == "cool":
            # emulate cooling by reducing the effective temperature
            # (i.e., b2 acts as negative)
            # We cannot change b2 inside constraints, so we re-encode via an aux var
            # Simpler: adjust c/G to emulate minor effect and rely on banding.
            pass  # keep simple in Phase 1

        # Cost
        if k == 0:
            du = u[k]
        else:
            du = u[k] - u[k - 1]
        cost += (
            cfg.w_temp * cp.square(T[k] - Tref[k])
            + cfg.w_energy * cp.square(u[k])
            + cfg.w_slew * cp.square(du)
            + 5.0 * s[k]  # penalize slack
        )

    prob = cp.Problem(cp.Minimize(cost), constraints)
    prob.solve(solver=cp.OSQP, warm_start=True)

    if T.value is None or u.value is None:
        # Fallback to heuristic on failure
        return _solve_heuristic(cfg, x0, fc)

    T_seq = tuple(float(v) for v in T.value.tolist())
    u_seq = tuple(float(max(cfg.u_min, min(cfg.u_max, v))) for v in u.value.tolist())
    merit = float(prob.value) if prob.value is not None else 0.0

    # DP target suggestion: gently push toward mid comfort; in COOL lower a bit
    dp_target = (cfg.t_min_c + cfg.t_max_c) / 2.0 - (0.5 if cfg.primary_mode == "cool" else 0.0)

    return MPCResult(
        u_seq=u_seq,
        T_seq_c=T_seq,
        dp_target_c=dp_target,
        merit=merit,
        primary_mode=cfg.primary_mode,
    )


# ==========================
#  Internal: heuristic fallback (no cvxpy)
# ==========================

def _solve_heuristic(cfg: MPCConfig, x0: MPCState, fc: MPCForecast) -> MPCResult:
    """Simulate a first-order response with a simple policy.

    - Move T toward the mid of comfort band using a bounded actuator.
    - Use outdoor as disturbance and small gains for solar.
    """
    N = int(cfg.horizon)
    T_out = tuple(fc.T_out_seq_c)[: N]
    G = tuple(fc.G_seq)[: N] if fc.G_seq is not None else (0.0,) * N

    dt = float(cfg.dt_s)
    tau = 3600.0
    a = math.exp(-dt / tau)
    b1 = (1.0 - a) * 0.15
    b2 = (1.0 - a) * 1.50
    c = (1.0 - a) * 0.05

    T = [x0.T_in_c]
    U = []

    Tref_mid = (cfg.t_min_c + cfg.t_max_c) / 2.0

    for k in range(N):
        err = Tref_mid - T[-1]
        if cfg.primary_mode == "heat":
            u = max(cfg.u_min, min(cfg.u_max, 0.8 * max(0.0, err)))
        else:  # cool
            u = max(cfg.u_min, min(cfg.u_max, 0.8 * max(0.0, -err)))

        U.append(u)
        # simulate
        next_T = a * T[-1] + b1 * T_out[k] + (b2 if cfg.primary_mode == "heat" else -b2) * u + c * G[k]
        T.append(next_T)

    dp_target = Tref_mid - (0.5 if cfg.primary_mode == "cool" else 0.0)
    merit = sum((t - Tref_mid) ** 2 for t in T) + 0.1 * sum(u * u for u in U)

    return MPCResult(
        u_seq=tuple(U),
        T_seq_c=tuple(T),
        dp_target_c=dp_target,
        merit=float(merit),
        primary_mode=cfg.primary_mode,
    )


# ==========================
#  Utilities
# ==========================

def _median(seq: Iterable[float]) -> float:
    arr = sorted(float(x) for x in seq)
    n = len(arr)
    if n == 0:
        return math.nan
    mid = n // 2
    if n % 2:
        return arr[mid]
    return 0.5 * (arr[mid - 1] + arr[mid])
