# custom_components/drp_climate/domain/metrics.py
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

Number = float | int
Labels = Tuple[Tuple[str, str], ...]  # labels canonicalizzate (tuples ordinate)


# ────────────────────────────── helpers ──────────────────────────────

def _canon_labels(labels: Optional[Mapping[str, str]] = None) -> Labels:
    if not labels:
        return tuple()
    return tuple(sorted((str(k), str(v)) for k, v in labels.items()))

def _now_monotonic() -> float:
    return time.perf_counter()

def _now_unix() -> float:
    return time.time()


# ────────────────────────────── base metric ──────────────────────────────

class Metric:
    """Base astratta di una metrica (non esportabile direttamente)."""

    def __init__(self, name: str, description: str = "", unit: str = "") -> None:
        self.name = name
        self.description = description
        self.unit = unit

    def snapshot(self) -> dict[str, Any]:
        raise NotImplementedError


# ────────────────────────────── Counter ──────────────────────────────

class Counter(Metric):
    """
    Contatore **monotonico** (solo incremento).
    Supporta labels opzionali: il valore è tenuto per combinazione di labels.
    """

    def __init__(self, name: str, description: str = "", unit: str = "") -> None:
        super().__init__(name, description, unit)
        self._lock = threading.Lock()
        self._values: Dict[Labels, float] = {}

    def inc(self, amount: Number = 1, labels: Optional[Mapping[str, str]] = None) -> None:
        if amount < 0:
            raise ValueError("Counter cannot be decreased")
        key = _canon_labels(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + float(amount)

    def get(self, labels: Optional[Mapping[str, str]] = None) -> float:
        key = _canon_labels(labels)
        with self._lock:
            return self._values.get(key, 0.0)

    def reset(self) -> None:
        with self._lock:
            self._values.clear()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "type": "counter",
                "name": self.name,
                "unit": self.unit,
                "help": self.description,
                "values": {tuple(k): v for k, v in self._values.items()},
                "ts": _now_unix(),
            }


# ────────────────────────────── Gauge ──────────────────────────────

class Gauge(Metric):
    """Valore istantaneo (si può alzare o abbassare)."""

    def __init__(self, name: str, description: str = "", unit: str = "") -> None:
        super().__init__(name, description, unit)
        self._lock = threading.Lock()
        self._values: Dict[Labels, float] = {}

    def set(self, value: Number, labels: Optional[Mapping[str, str]] = None) -> None:
        key = _canon_labels(labels)
        with self._lock:
            self._values[key] = float(value)

    def add(self, delta: Number, labels: Optional[Mapping[str, str]] = None) -> None:
        key = _canon_labels(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + float(delta)

    def get(self, labels: Optional[Mapping[str, str]] = None) -> float:
        key = _canon_labels(labels)
        with self._lock:
            return self._values.get(key, 0.0)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "type": "gauge",
                "name": self.name,
                "unit": self.unit,
                "help": self.description,
                "values": {tuple(k): v for k, v in self._values.items()},
                "ts": _now_unix(),
            }


# ────────────────────────────── Histogram ──────────────────────────────

@dataclass
class _HistBucket:
    upper_bound: float
    count: int = 0

class Histogram(Metric):
    """
    Istogramma con bucket *cumulativi* in stile Prometheus.
    Default buckets utili per latenza (ms) o durate brevi. Puoi passarne di tuoi.
    """

    DEFAULT_BUCKETS = (
        0.5, 1, 2.5, 5, 10, 25, 50, 100, 250, 500,
        1_000, 2_500, 5_000, 10_000  # millisecondi
    )

    def __init__(
        self,
        name: str,
        description: str = "",
        unit: str = "ms",
        buckets: Iterable[Number] | None = None,
    ) -> None:
        super().__init__(name, description, unit)
        bounds = sorted(float(b) for b in (buckets or self.DEFAULT_BUCKETS))
        self._lock = threading.Lock()
        self._buckets: List[_HistBucket] = [_HistBucket(b) for b in bounds]
        self._sum = 0.0
        self._count = 0

    def observe(self, value: Number) -> None:
        v = float(value)
        with self._lock:
            self._count += 1
            self._sum += v
            for b in self._buckets:
                if v <= b.upper_bound:
                    b.count += 1
        # NB: i bucket sono *cumulativi*: i valori > ultimo bucket non incrementano nessun bucket, ma vanno in +Inf per l'export.

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "type": "histogram",
                "name": self.name,
                "unit": self.unit,
                "help": self.description,
                "buckets": [(b.upper_bound, b.count) for b in self._buckets],
                "sum": self._sum,
                "count": self._count,
                "ts": _now_unix(),
            }


# ────────────────────────────── Timer (ctx & decorator) ──────────────────────────────

class Timer:
    """
    Misura una durata e la registra in una metrica (Histogram in ms o Gauge/Counter).
    Uso:
        with Timer(hist):   # hist è Histogram in ms
            do_work()
    Oppure:
        @Timer.timeit(hist)
        def foo(...): ...
    """

    def __init__(self, metric: Histogram | Gauge | Counter) -> None:
        self.metric = metric
        self._start: Optional[float] = None

    def __enter__(self):
        self._start = _now_monotonic()
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._start is None:
            return False
        elapsed_ms = (_now_monotonic() - self._start) * 1000.0
        if isinstance(self.metric, Histogram):
            self.metric.observe(elapsed_ms)
        elif isinstance(self.metric, Gauge):
            self.metric.set(elapsed_ms)
        elif isinstance(self.metric, Counter):
            # Poco sensato: incrementa di secondi; si preferisce Histogram
            self.metric.inc(elapsed_ms / 1000.0)
        return False  # non sopprime eccezioni

    @staticmethod
    def timeit(metric: Histogram | Gauge | Counter):
        def deco(fn):
            def wrapper(*args, **kwargs):
                with Timer(metric):
                    return fn(*args, **kwargs)
            return wrapper
        return deco


# ────────────────────────────── Registry ──────────────────────────────

class MetricsRegistry:
    """Registro centrale. Gestisce creazione, riuso e snapshot/esportazione."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._metrics: Dict[str, Metric] = {}

    # -- factory --

    def counter(self, name: str, *, description: str = "", unit: str = "") -> Counter:
        with self._lock:
            m = self._metrics.get(name)
            if m is None:
                m = Counter(name, description, unit)
                self._metrics[name] = m
            elif not isinstance(m, Counter):
                raise TypeError(f"metric '{name}' already exists with different type")
            return m

    def gauge(self, name: str, *, description: str = "", unit: str = "") -> Gauge:
        with self._lock:
            m = self._metrics.get(name)
            if m is None:
                m = Gauge(name, description, unit)
                self._metrics[name] = m
            elif not isinstance(m, Gauge):
                raise TypeError(f"metric '{name}' already exists with different type")
            return m

    def histogram(
        self,
        name: str,
        *,
        description: str = "",
        unit: str = "ms",
        buckets: Iterable[Number] | None = None,
    ) -> Histogram:
        with self._lock:
            m = self._metrics.get(name)
            if m is None:
                m = Histogram(name, description, unit, buckets=buckets)
                self._metrics[name] = m
            elif not isinstance(m, Histogram):
                raise TypeError(f"metric '{name}' already exists with different type")
            return m

    # -- query/export --

    def get(self, name: str) -> Optional[Metric]:
        with self._lock:
            return self._metrics.get(name)

    def names(self) -> List[str]:
        with self._lock:
            return sorted(self._metrics.keys())

    def snapshot(self) -> Dict[str, dict[str, Any]]:
        with self._lock:
            return {name: m.snapshot() for name, m in self._metrics.items()}

    def export_prometheus(self) -> str:
        """
        Esporta nel formato text exposition (Prometheus).
        Nota: implementazione minimale, sufficiente per test/local scrape.
        """
        lines: List[str] = []
        with self._lock:
            metrics = list(self._metrics.values())

        def esc(s: str) -> str:
            return s.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')

        for m in metrics:
            snap = m.snapshot()
            name = m.name
            help_ = snap.get("help") or ""
            type_ = snap["type"]

            lines.append(f"# HELP {name} {esc(help_)}")
            # map types
            ptype = {"counter": "counter", "gauge": "gauge", "histogram": "histogram"}.get(type_, "untyped")
            lines.append(f"# TYPE {name} {ptype}")

            if type_ in ("counter", "gauge"):
                values: Dict[Labels, float] = snap["values"]
                for raw_labels, value in values.items():
                    if raw_labels:
                        label_str = ",".join(f'{k}="{esc(v)}"' for k, v in raw_labels)
                        lines.append(f'{name}{{{label_str}}} {value}')
                    else:
                        lines.append(f"{name} {value}")
            elif type_ == "histogram":
                buckets: List[Tuple[float, int]] = snap["buckets"]
                cum = 0
                for upper, count in buckets:
                    cum = count  # già cumulativi
                    lines.append(f'{name}_bucket{{le="{upper}"}} {cum}')
                # +Inf
                total = snap["count"]
                if buckets:
                    last_cum = buckets[-1][1]
                else:
                    last_cum = 0
                inf = total  # in stile Prometheus: bucket +Inf = count totale
                lines.append(f'{name}_bucket{{le="+Inf"}} {inf}')
                lines.append(f"{name}_count {total}")
                lines.append(f"{name}_sum {snap['sum']}")
            else:
                # unknown type; skip
                pass

        return "\n".join(lines) + "\n"


# Singleton comodo per l'integrazione
_registry: Optional[MetricsRegistry] = None

def get_registry() -> MetricsRegistry:
    global _registry
    if _registry is None:
        _registry = MetricsRegistry()
    return _registry


# ────────────────────────────── metriche DRP suggerite ──────────────────────────────
# Nota: sono *solo* helper; usale liberamente nel tuo codice.

def setup_default_metrics(reg: Optional[MetricsRegistry] = None) -> Dict[str, Metric]:
    reg = reg or get_registry()
    return {
        "cycles_total": reg.counter(
            "drp_cycles_total",
            description="Numero totale di cicli on/off",
        ),
        "on_time_seconds_total": reg.counter(
            "drp_on_time_seconds_total",
            description="Tempo ON cumulato",
            unit="s",
        ),
        "setpoint_changes_total": reg.counter(
            "drp_setpoint_changes_total",
            description="Cambi setpoint",
        ),
        "decision_latency_ms": reg.histogram(
            "drp_decision_latency_ms",
            description="Latenza loop decisionale",
            unit="ms",
        ),
        "safety_trips_total": reg.counter(
            "drp_safety_trips_total",
            description="Interventi safety/limiti",
        ),
        "dewpoint_margin_c": reg.gauge(
            "drp_dewpoint_margin_c",
            description="Margine da dew point",
            unit="celsius",
        ),
    }





# snapshot = reg.snapshot()            # dict
# prom = reg.export_prometheus()       # testo formattato
# _LOGGER.debug("metrics:\n%s", prom)
