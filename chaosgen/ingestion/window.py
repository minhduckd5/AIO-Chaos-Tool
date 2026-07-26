from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chaosgen.config.settings import ChaosGenSettings
    from chaosgen.schemas.telemetry import TelemetryDataset


@dataclass
class CollectionWindow:
    start: datetime
    end: datetime

    @property
    def lookback_hours(self) -> float:
        duration = (self.end - self.start).total_seconds() / 3600.0
        return max(duration, 1e-6)


def resolve_collection_window(
    *,
    hours: int | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    dataset: TelemetryDataset | None = None,
    settings: ChaosGenSettings | None = None,
) -> CollectionWindow:
    """
    Derives authoritative collection window and lookback_hours.

    Priority:
    1. If dataset provided (e.g. export bundle) -> use dataset.collection_start/end if available.
    2. If start and end provided -> use absolute range.
    3. Else -> end = now (UTC), start = end - hours (or settings.telemetry.default_lookback_hours).
    """
    if dataset is not None and dataset.collection_start and dataset.collection_end:
        return CollectionWindow(start=dataset.collection_start, end=dataset.collection_end)

    if start is not None and end is not None:
        if end <= start:
            raise ValueError(f"Collection end time ({end}) must be strictly after start time ({start}).")
        # Ensure UTC timezone awareness
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        return CollectionWindow(start=start, end=end)

    now = datetime.now(timezone.utc)
    if hours is None:
        if settings is not None:
            hours = settings.telemetry.default_lookback_hours
        else:
            hours = 24

    start_time = now - timedelta(hours=hours)
    return CollectionWindow(start=start_time, end=now)
