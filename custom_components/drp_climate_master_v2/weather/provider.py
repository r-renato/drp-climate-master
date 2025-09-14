#
from __future__ import annotations

from datetime import date
from typing import List, Optional, Protocol

from ..domain.models import WeatherDailySample

class WeatherProvider(Protocol):
    async def daily(self, day: date) -> Optional[WeatherDailySample]:
        ...

    async def daily_range(self, start: date, end: date) -> List[WeatherDailySample]:
        ...

    # Optional: allow reusable contexts for HTTP sessions, etc.
    async def __aenter__(self) -> "WeatherProvider": ...
    async def __aexit__(self, exc_type, exc, tb) -> None: ...