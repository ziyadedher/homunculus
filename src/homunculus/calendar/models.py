from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class TimePeriod:
    start: datetime
    end: datetime


@dataclass(frozen=True)
class FreeBusyResult:
    busy_periods: list[TimePeriod]
    time_min: datetime
    time_max: datetime


@dataclass(frozen=True)
class Event:
    id: str
    summary: str
    start: datetime
    end: datetime
    description: str | None = None
    location: str | None = None
