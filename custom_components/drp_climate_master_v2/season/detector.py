#
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta, datetime
from typing import (
    Any,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Protocol,
    Tuple,
    Literal,
    cast,
)
import calendar
import logging

from homeassistant.util import dt as dt_util

from ..weather.provider import WeatherForecastProvider, WeatherHistoricalProvider

from ..domain.models import SeasonState

from ..domain.enums import Seasons

_LOGGER = logging.getLogger(__name__)

class SeasonCalendar:
    """
    Calendarizzatore di stagioni **meteorologiche** (DJF, MAM, JJA, SON)
    per anno ed emisfero, con gestione robusta degli anni bisestili e degli
    attraversamenti di anno (Winter a cavallo Dicembre→Febbraio).

    Convenzione sull'anno:
        `year` è l'anno in cui l'INVERNO termina (Febbraio di `year`).
        Esempio: SeasonCalendar(2025) → Winter: 2024-12-01 .. 2025-02-28/29.

    Emisferi:
        - "north":  WINTER=Dec-Feb, SPRING=Mar-May, SUMMER=Jun-Aug, AUTUMN=Sep-Nov
        - "south":  SUMMER=Dec-Feb, AUTUMN=Mar-May, WINTER=Jun-Aug, SPRING=Sep-Nov

    Caratteristiche:
        - `Seasons` come StrEnum per label stabili
        - `SeasonWindow` (dataclass immutabile, con metodo `contains`)
        - API principali: `windows()`, `as_dict()`, `season_for(date)`
        - Fluent helpers: `with_year()`, `with_hemisphere()`
        - Sicura rispetto ad anni bisestili (chiusura Feb via `calendar.monthrange`)

    Note su `season_for(date)`:
        Per gestire l'inverno a cavallo dell'anno, la ricerca copre le finestre
        dell'anno precedente, corrente e successivo. In pratica:
            for y in (d.year - 1, d.year, d.year + 1): ...
        Questo evita ambiguità sul cambio anno e mantiene O(1) in ogni caso.

    Esempi
    -------
    >>> from datetime import date
    >>> cal = SeasonCalendar(2025)              # emisfero nord
    >>> wins = cal.windows()
    >>> wins[Seasons.WINTER].start, wins[Seasons.WINTER].end
    (date(2024, 12, 1), date(2025, 2, 28))  # o 29 se bisestile
    >>> cal.season_for(date(2024, 12, 15))
    <Seasons.WINTER: 'winter'>
    >>> cal.season_for(date(2025, 6, 10))
    <Seasons.SUMMER: 'summer'>

    Best practice d'uso:
        - Usare `SeasonCalendar.today().season_for(date.today())` come baseline
          per detector/strategie, poi applicare eventuali override (ondate di
          caldo/freddo, dew point elevato, ecc.).
        - Non assumere alcun ordinamento della dict: se serve una sequenza
          ordinata, derivarla esplicitamente (es. [WINTER, SPRING, SUMMER, AUTUMN]).
    """

    @dataclass(frozen=True, slots=True)
    class SeasonWindow:
        """
        Finestra stagionale inclusiva [start, end] per una determinata `season`.

        Attributi:
            season: Istanza di `Seasons` (WINTER, SPRING, SUMMER, AUTUMN).
            start:  Data di inizio (inclusa).
            end:    Data di fine (inclusa).

        Proprietà:
            - Immutabile (frozen=True).
            - Efficiente in memoria (slots=True).
        """
        season: Seasons
        start: date  # inclusive
        end: date    # inclusive

        def contains(self, d: date) -> bool:
            """
            Verifica se una data rientra nella finestra stagionale inclusiva.

            Args:
                d: Data da testare.

            Returns:
                True se `start <= d <= end`, altrimenti False.

            Complessità:
                O(1).
            """
            return self.start <= d <= self.end

    def __init__(
        self,
        year: Optional[int] = None,
        *,
        hemisphere: Literal["north", "south"] = "north",
    ) -> None:
        """
        Inizializza il calendario stagionale per un `year` logico e un `hemisphere`.

        Args:
            year: Anno logico in cui termina l'inverno (Febbraio di `year`).
                  Se None, viene usato l'anno corrente (`date.today().year`).
            hemisphere: Emisfero di riferimento ("north" o "south").

        Effetti:
            Imposta `self._year` e `self._hemisphere`.

        Note:
            La scelta di `year` come "anno di termine inverno" semplifica il mapping
            della finestra DJF che attraversa il confine di anno.
        """
        self._year: int = year if year is not None else date.today().year
        self._hemisphere: Literal["north"] | Literal["south"] = hemisphere

    # ---- fluent helpers ------------------------------------------------------
    def with_year(self, year: int) -> "SeasonCalendar":
        """
        Crea una nuova istanza con lo stesso emisfero ma anno diverso.

        Args:
            year: Nuovo anno logico (anno in cui termina l'inverno).

        Returns:
            SeasonCalendar: nuova istanza con `year` aggiornato.

        Use-case:
            Iterare facilmente su anni adiacenti senza mutare l’istanza originale.
        """
        return SeasonCalendar(year, hemisphere=self._hemisphere)

    def with_hemisphere(self, hemisphere: Literal["north", "south"]) -> "SeasonCalendar":
        """
        Crea una nuova istanza con lo stesso anno ma emisfero diverso.

        Args:
            hemisphere: "north" o "south".

        Returns:
            SeasonCalendar: nuova istanza con `hemisphere` aggiornato.

        Use-case:
            Supportare deployment multi-sito su emisferi differenti.
        """
        return SeasonCalendar(self._year, hemisphere=hemisphere)

    # ---- API -----------------------------------------------------------------
    def windows(self) -> Dict[Seasons, "SeasonCalendar.SeasonWindow"]:
        """
        Restituisce le finestre stagionali meteorologiche per l'anno/emisfero correnti.

        Returns:
            Dict[Seasons, SeasonWindow]: mapping dalle 4 stagioni alle rispettive finestre
            inclusive [start, end].

        Proprietà:
            - Le finestre NON si sovrappongono e coprono l'intero anno logico.
            - Per l'emisfero nord, WINTER: Dec(y-1) → Feb(y). Per il sud, SUMMER: Dec(y-1) → Feb(y).

        Complessità:
            O(1).

        Note:
            Internamente delega a `_north_windows()` o `_south_windows()`.
        """
        if self._hemisphere == "south":
            return self._south_windows(self._year)
        return self._north_windows(self._year)

    def as_dict(self) -> Dict[Seasons, Tuple[date, date]]:
        """
        Restituisce un mapping comodo (retro-compat) delle finestre come tuple (start, end).

        Returns:
            Dict[Seasons, Tuple[date, date]]: dizionario stagione → (start, end).

        Use-case:
            Interfacce legacy o serializzazione minimale senza dataclass.

        Complessità:
            O(1).
        """
        wins = self.windows()
        return {s: (w.start, w.end) for s, w in wins.items()}

    def season_for(self, d: date = date.today()) -> Seasons:
        """
        Determina la stagione meteorologica di una data, gestendo correttamente DJF a cavallo anno.

        Args:
            d: Data da classificare (emisfero già implicito nell'istanza).

        Returns:
            Seasons: Stagione corrispondente a `d`.

        Strategia:
            Per evitare ambiguità su DJF, valuta le finestre dell'anno `d.year-1`,
            `d.year` e `d.year+1`, restituendo la prima finestra che contiene `d`.

        Complessità:
            O(1) (al più 12 verifiche: 3 anni × 4 stagioni).

        Edge cases:
            - Date su 1 Dicembre o ultimo giorno di Febbraio mappano correttamente su WINTER (emisfero nord).
            - In emisfero sud lo shift stagionale è correttamente ruotato.

        Fallback:
            In caso (teoricamente impossibile) nessuna finestra contenga `d`,
            viene restituita `Seasons.SUMMER` per evitare bias verso il riscaldamento.
        """
        # Check windows around the date's year to safely span DJF boundaries
        for y in (d.year - 1, d.year, d.year + 1):
            wins = (self.with_year(y)).windows().values()
            for w in wins:
                if w.contains(d):
                    return w.season
        # Fallback should be unreachable; pick SUMMER to avoid heating bias
        return Seasons.SUMMER

    # ---- internals -----------------------------------------------------------
    @staticmethod
    def _eom(y: int, m: int) -> int:
        """
        End-Of-Month: ultimo giorno del mese `m` per l'anno `y`.

        Args:
            y: Anno (es. 2025).
            m: Mese (1..12).

        Returns:
            int: Giorno finale del mese (28..31), corretto anche per anni bisestili (Feb=29).

        Complessità:
            O(1).

        Dipendenze:
            Usa `calendar.monthrange(y, m)`.
        """
        return calendar.monthrange(y, m)[1]

    @classmethod
    def _north_windows(cls, year: int) -> Dict[Seasons, "SeasonCalendar.SeasonWindow"]:
        """
        Costruisce le finestre stagionali per l'emisfero nord nell'anno logico dato.

        Args:
            year: Anno logico (in cui termina l'inverno a Febbraio).

        Returns:
            Dict[Seasons, SeasonCalendar.SeasonWindow]: mapping stagione → finestra.

        Definizioni:
            WINTER:  1 Dec (year-1) .. last Feb (year)
            SPRING:  1 Mar (year)   .. 31 May (year)
            SUMMER:  1 Jun (year)   .. 31 Aug (year)
            AUTUMN:  1 Sep (year)   .. 30 Nov (year)

        Complessità:
            O(1).
        """
        winter = cls.SeasonWindow(
            Seasons.WINTER,
            date(year - 1, 12, 1),
            date(year, 2, cls._eom(year, 2)),
        )
        spring = cls.SeasonWindow(Seasons.SPRING, date(year, 3, 1), date(year, 5, 31))
        summer = cls.SeasonWindow(Seasons.SUMMER, date(year, 6, 1), date(year, 8, 31))
        autumn = cls.SeasonWindow(Seasons.AUTUMN, date(year, 9, 1), date(year, 11, 30))
        return {
            Seasons.WINTER: winter,
            Seasons.SPRING: spring,
            Seasons.SUMMER: summer,
            Seasons.AUTUMN: autumn,
        }

    @classmethod
    def _south_windows(cls, year: int) -> Dict[Seasons, "SeasonCalendar.SeasonWindow"]:
        """
        Costruisce le finestre stagionali per l'emisfero sud nell'anno logico dato.

        Args:
            year: Anno logico (in cui termina l'estate a Febbraio).

        Returns:
            Dict[Seasons, SeasonCalendar.SeasonWindow]: mapping stagione → finestra.

        Definizioni:
            SUMMER:  1 Dec (year-1) .. last Feb (year)
            AUTUMN:  1 Mar (year)   .. 31 May (year)
            WINTER:  1 Jun (year)   .. 31 Aug (year)
            SPRING:  1 Sep (year)   .. 30 Nov (year)

        Complessità:
            O(1).
        """
        summer = cls.SeasonWindow(
            Seasons.SUMMER,
            date(year - 1, 12, 1),
            date(year, 2, cls._eom(year, 2)),
        )
        autumn = cls.SeasonWindow(Seasons.AUTUMN, date(year, 3, 1), date(year, 5, 31))
        winter = cls.SeasonWindow(Seasons.WINTER, date(year, 6, 1), date(year, 8, 31))
        spring = cls.SeasonWindow(Seasons.SPRING, date(year, 9, 1), date(year, 11, 30))
        return {
            Seasons.SUMMER: summer,
            Seasons.AUTUMN: autumn,
            Seasons.WINTER: winter,
            Seasons.SPRING: spring,
        }


@dataclass(slots=True, frozen=True)
class _ScoreParams:
    # peso decrescente per i giorni più lontani (0 => niente decadimento)
    day_decay: float = 0.10
    # boost gaussiano sul prior in prossimità del "cuore" della stagione
    boost_sigma: float = 0.20
    boost_amp: float = 0.20
    # dispersioni (°C) per le RBF di scarto dalla climatologia
    sigma_temp: float = 3.0
    sigma_dew: float = 2.0


class WeatherSeasonDetector:
    """
    Rilevamento stagione basato su:
      - prior da calendario (DJF/MAM/JJA/SON)
      - anomalia storica recente (T media e dew point)
      - climatologia stagionale (anno precedente)

    NOTE:
    - Nessun uso del forecast qui (solo storico). Il provider forecast resta
      iniettato per possibili strategie future, ma non è usato in questo detector.
    """

    def __init__(
        self,
        weatherHistorical: WeatherHistoricalProvider,
        calendar: SeasonCalendar = SeasonCalendar(),
        *,
        history_days: int = 21,
        params: Optional[_ScoreParams] = None,
        provider_id: str = "historical",
        weatherForecast: Optional[WeatherForecastProvider] = None,  # opzionale, non usato
    ) -> None:
        self._calendar = calendar
        self._weather_historical = weatherHistorical
        self._weather_forecast = weatherForecast  # tenuto per future estensioni
        self._history_days = max(7, int(history_days))
        self._p = params or _ScoreParams()
        self._provider_id = provider_id

    # ------------------------------- API -------------------------------------
    async def detect(self, target_date: Optional[date] = None) -> Any:
        today = target_date or date.today()

        # 1) prior da calendario (+ boost gaussiano sul cuore stagione)
        baseline = self._calendar.season_for(today)
        prior = self._build_prior(self._calendar, today, baseline)

        # 2) storico recente → medie pesate
        start_hist = date.fromordinal(today.toordinal() - (self._history_days - 1))
        hist_recent = await self._fetch_history_range(start=start_hist, end=today)
        mean_t, mean_dew = self._weighted_recent_means(hist_recent)

        # 3) climatologia (finestre anno precedente rispetto al calendario)
        clim = await self._season_climatology_from_prev_year(today)

        # 4) scoring da climatologia (RBF su scarto T/dew)
        score_hist = self._scores_from_climatology(
            mean_t,
            mean_dew,
            clim,
            sigma_t=self._p.sigma_temp,
            sigma_d=self._p.sigma_dew,
        )

        # 5) combinazione prior ⊙ storico
        combined = self._combine_prior_and_hist(prior, score_hist)

        # 6) decisione con confidenza
        season, confidence, ordered = self._decide(combined)

        details = {
            "date": today.isoformat(),
            "provider_id": self._provider_id,
            "baseline": getattr(baseline, "value", str(baseline)),
            "prior": {s.value: v for s, v in prior.items()},
            "scores_hist": {s.value: v for s, v in score_hist.items()},
            "combined": {s.value: v for s, v in combined.items()},
            "recent_mean_t": mean_t,
            "recent_mean_dew": mean_dew,
            "climatology_t": {s.value: clim[s].get("tavg") for s in (Seasons.WINTER, Seasons.SPRING, Seasons.SUMMER, Seasons.AUTUMN)},
            "climatology_dew": {s.value: clim[s].get("dew") for s in (Seasons.WINTER, Seasons.SPRING, Seasons.SUMMER, Seasons.AUTUMN)},
            "ordered": [(s.value, sc) for s, sc in ordered],
            "method": "calendar_prior + historical_anomaly",
        }

        return self._build_state(
            season=season,
            confidence=confidence,
            scores={s.value: v for s, v in combined.items()},
            baseline=baseline,
            details=details,
        )

    # ---------------------------- internals ----------------------------------
    def _build_prior(
        self, cal: SeasonCalendar, d: date, baseline: Seasons
    ) -> Dict[Seasons, float]:
        def _adjacent(s: Seasons) -> Tuple[Seasons, Seasons]:
            order = (Seasons.WINTER, Seasons.SPRING, Seasons.SUMMER, Seasons.AUTUMN)
            i = order.index(s)
            return (order[(i - 1) % 4], order[(i + 1) % 4])

        def _opposite(s: Seasons) -> Seasons:
            order = (Seasons.WINTER, Seasons.SPRING, Seasons.SUMMER, Seasons.AUTUMN)
            i = order.index(s)
            return order[(i + 2) % 4]

        base_w, adj_w, opp_w = 0.55, 0.20, 0.05
        s_adj_l, s_adj_r = _adjacent(baseline)
        prior = {
            baseline: base_w,
            s_adj_l: adj_w,
            s_adj_r: adj_w,
            _opposite(baseline): opp_w,
        }

        base_win = cal.windows()[baseline]
        x = self._relative_pos_in_window(base_win, d)  # 0..1
        boost = 1.0 + self._p.boost_amp * self._gauss(x, mu=0.5, sigma=self._p.boost_sigma)
        prior[baseline] *= boost
        return self._normalize(prior)

    @staticmethod
    def _relative_pos_in_window(win: SeasonCalendar.SeasonWindow, d: date) -> float:
        span = (win.end - win.start).days or 1
        pos = max(0, min(span, (d - win.start).days))
        return pos / span

    async def _fetch_history_range(self, start: date, end: date) -> List[Mapping[str, Any]]:
        """
        Recupera lo storico preferendo l'API canonica `daily_range(start, end)`.
        Accetta sia Forecast HA-like (con 'datetime') che record custom.
        """
        p = self._weather_historical

        # Calcola "oggi" nella timezone di Home Assistant e la massima data disponibile
        today = dt_util.now().date()
        last_available = today - timedelta(days=2)

        # Clamp della end se in futuro o negli ultimi 2 giorni non ancora disponibili
        if end > last_available:
            _LOGGER.debug(
                "Clamping end date from %s to last available %s (today=%s, -2d).",
                end, last_available, today
            )
            end = last_available

        _LOGGER.debug("_fetch_history_range start %s - end %s", start, end)
        # 1) API canonica async
        if hasattr(p, "daily_range"):
            try:
                res = await p.daily_range(start, end)  # type: ignore[misc]
                return list(res or [])
            except TypeError:
                # firma con kwargs
                res = await p.daily_range(start=start, end=end)  # type: ignore[misc]
                return list(res or [])

        # 2) API per-giorno: `daily(d)`
        if hasattr(p, "daily"):
            rows: List[Mapping[str, Any]] = []
            d = start
            while d <= end:
                try:
                    r = await p.daily(d)  # type: ignore[misc]
                    if isinstance(r, Mapping):
                        rows.append(r)
                except Exception as e:
                    _LOGGER.debug("history daily(%s) failed: %s", d, e)
                d += timedelta(days=1)
            return rows

        _LOGGER.warning("WeatherSeasonDetector: provider storico senza API note; ritorno []")
        return []

    def _weighted_recent_means(
        self, series: List[Mapping[str, Any]]
    ) -> Tuple[Optional[float], Optional[float]]:
        """
        Media pesata (decrescente) di T media e dew point sugli ultimi N giorni.
        Supporta chiavi: 'date' | 'day' | 'time' | 'datetime' (ISO o datetime).
        """
        def _date_of(item: Mapping[str, Any]) -> Optional[date]:
            d_ = item.get("date") or item.get("day") or item.get("time") or item.get("datetime")
            if isinstance(d_, date):
                return d_
            if isinstance(d_, datetime):
                return dt_util.as_local(d_).date()
            if isinstance(d_, str):
                # prova ISO con timezone
                dtp = dt_util.parse_datetime(d_)
                if isinstance(dtp, datetime):
                    return dt_util.as_local(dtp).date()
                # fallback YYYY-MM-DD
                try:
                    y, m, dd = map(int, d_.split("-"))
                    return date(y, m, dd)
                except Exception:
                    return None
            return None

        def _tavg(item: Mapping[str, Any]) -> Optional[float]:
            tmax = item.get("temperature") or item.get("tmax")
            tmin = item.get("templow") or item.get("tmin")
            if isinstance(tmax, (int, float)) and isinstance(tmin, (int, float)):
                return (float(tmax) + float(tmin)) / 2.0
            if isinstance(tmax, (int, float)):
                return float(tmax)
            return None

        def _dew(item: Mapping[str, Any]) -> Optional[float]:
            dp = item.get("dewpoint") or item.get("dew_point")
            return float(dp) if isinstance(dp, (int, float)) else None

        rows = [r for r in series if _date_of(r) is not None]
        rows.sort(key=lambda r: cast(date, _date_of(r)))  # type: ignore[arg-type]
        if not rows:
            return None, None

        rows = rows[-self._history_days :]
        n = len(rows)
        # i=0 sarà il più recente (inverto dopo)
        weights = [max(0.0, 1.0 - i * self._p.day_decay) for i in range(n)]
        ws = sum(weights) or 1.0
        weights = [w / ws for w in weights]

        t_vals: List[float] = []
        d_vals: List[float] = []
        for i, r in enumerate(reversed(rows)):  # i=0 = più recente
            w = weights[i]
            t = _tavg(r)
            d = _dew(r)
            if isinstance(t, (int, float)):
                t_vals.append(w * float(t))
            if isinstance(d, (int, float)):
                d_vals.append(w * float(d))

        t_mean = sum(t_vals) if t_vals else None
        d_mean = sum(d_vals) if d_vals else None
        return t_mean, d_mean

    async def _season_climatology_from_prev_year(
        self, ref_day: date
    ) -> Dict[Seasons, Dict[str, Optional[float]]]:
        """
        Climatologia stagionale sull'anno precedente alle finestre DJF/MAM/JJA/SON.
        Ritorna: {season: {"tavg": float|None, "dew": float|None}}
        """
        cal_prev = self._calendar.with_year(ref_day.year - 1)
        wins_prev = cal_prev.windows()
        start = wins_prev[Seasons.WINTER].start
        end = wins_prev[Seasons.AUTUMN].end

        series = await self._fetch_history_range(start, end)

        by_season_t: Dict[Seasons, List[float]] = {s: [] for s in (Seasons.WINTER, Seasons.SPRING, Seasons.SUMMER, Seasons.AUTUMN)}
        by_season_d: Dict[Seasons, List[float]] = {s: [] for s in (Seasons.WINTER, Seasons.SPRING, Seasons.SUMMER, Seasons.AUTUMN)}

        def _date_of(item: Mapping[str, Any]) -> Optional[date]:
            d_ = item.get("date") or item.get("day") or item.get("time") or item.get("datetime")
            if isinstance(d_, date):
                return d_
            if isinstance(d_, datetime):
                return dt_util.as_local(d_).date()
            if isinstance(d_, str):
                dtp = dt_util.parse_datetime(d_)
                if isinstance(dtp, datetime):
                    return dt_util.as_local(dtp).date()
                try:
                    y, m, dd = map(int, d_.split("-"))
                    return date(y, m, dd)
                except Exception:
                    return None
            return None

        def _tavg(item: Mapping[str, Any]) -> Optional[float]:
            tmax = item.get("temperature") or item.get("tmax")
            tmin = item.get("templow") or item.get("tmin")
            if isinstance(tmax, (int, float)) and isinstance(tmin, (int, float)):
                return (float(tmax) + float(tmin)) / 2.0
            if isinstance(tmax, (int, float)):
                return float(tmax)
            return None

        def _dew(item: Mapping[str, Any]) -> Optional[float]:
            dp = item.get("dewpoint") or item.get("dew_point")
            return float(dp) if isinstance(dp, (int, float)) else None

        for row in series:
            d = _date_of(row)
            if not d:
                continue
            s = cal_prev.season_for(d)
            t = _tavg(row)
            if isinstance(t, (int, float)):
                by_season_t[s].append(float(t))
            dp = _dew(row)
            if isinstance(dp, (int, float)):
                by_season_d[s].append(float(dp))

        def _mean(vals: List[float]) -> Optional[float]:
            return (sum(vals) / len(vals)) if vals else None

        return {
            s: {"tavg": _mean(by_season_t[s]), "dew": _mean(by_season_d[s])}
            for s in (Seasons.WINTER, Seasons.SPRING, Seasons.SUMMER, Seasons.AUTUMN)
        }

    def _scores_from_climatology(
        self,
        mean_t: Optional[float],
        mean_dew: Optional[float],
        clim: Dict[Seasons, Dict[str, Optional[float]]],
        sigma_t: float,
        sigma_d: float,
    ) -> Dict[Seasons, float]:
        scores: Dict[Seasons, float] = {s: 0.0 for s in (Seasons.WINTER, Seasons.SPRING, Seasons.SUMMER, Seasons.AUTUMN)}
        if mean_t is None and mean_dew is None:
            return scores

        for s in scores.keys():
            sc = 0.0
            c = clim.get(s, {})
            if mean_t is not None and isinstance(c.get("tavg"), (int, float)):
                dt_ = abs(mean_t - cast(float, c["tavg"]))
                sc += math.exp(-(dt_ * dt_) / (2.0 * sigma_t * sigma_t))
            if mean_dew is not None and isinstance(c.get("dew"), (int, float)):
                dd_ = abs(mean_dew - cast(float, c["dew"]))
                sc += math.exp(-(dd_ * dd_) / (2.0 * sigma_d * sigma_d))
            scores[s] = sc
        return self._normalize(scores)

    def _combine_prior_and_hist(
        self,
        prior: Dict[Seasons, float],
        hist: Dict[Seasons, float],
    ) -> Dict[Seasons, float]:
        if not hist or sum(hist.values()) == 0:
            return prior
        combined = {s: prior.get(s, 0.0) * hist.get(s, 0.0) for s in prior.keys()}
        return self._normalize(combined)

    @staticmethod
    def _decide(
        scores: Dict[Seasons, float]
    ) -> Tuple[Seasons, float, List[Tuple[Seasons, float]]]:
        ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        best_s, best_v = ordered[0]
        second_v = ordered[1][1] if len(ordered) > 1 else 0.0
        confidence = max(0.0, best_v - second_v)
        return best_s, confidence, ordered

    # --------------------------- utilità locali -------------------------------
    @staticmethod
    def _gauss(x: float, mu: float, sigma: float) -> float:
        if sigma <= 0:
            return 0.0
        z = (x - mu) / sigma
        return math.exp(-0.5 * z * z)

    @staticmethod
    def _normalize(d: Dict[Seasons, float]) -> Dict[Seasons, float]:
        s = sum(max(0.0, v) for v in d.values())
        if s <= 0:
            n = len(d) or 1
            return {k: 1.0 / n for k in d.keys()}
        return {k: max(0.0, v) / s for k, v in d.items()}

    def _build_state(self, **kwargs: Any) -> Any:
        """Costruisce SeasonState se disponibile, altrimenti un dict compatibile."""
        try:
            return SeasonState(**kwargs)  # type: ignore[arg-type]
        except Exception as e:
            _LOGGER.debug("WeatherSeasonDetector: fallback a dict per SeasonState: %s", e)
            return kwargs


