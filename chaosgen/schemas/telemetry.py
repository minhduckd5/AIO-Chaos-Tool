from datetime import datetime
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, Field


class MetricSample(BaseModel):
    """Single metric data point from Prometheus."""
    timestamp: float
    value: float
    labels: Dict[str, str] = Field(default_factory=dict)


class TimeSeries(BaseModel):
    """Named time-series with ordered samples."""
    metric_name: str
    labels: Dict[str, str] = Field(default_factory=dict)
    samples: List[MetricSample] = Field(default_factory=list)

    @property
    def values(self) -> List[float]:
        return [s.value for s in self.samples]

    @property
    def timestamps(self) -> List[float]:
        return [s.timestamp for s in self.samples]


class LogEntry(BaseModel):
    """Single log line from Loki."""
    timestamp: float
    message: str
    labels: Dict[str, str] = Field(default_factory=dict)
    level: Optional[str] = None


class LogStream(BaseModel):
    """Named log stream with ordered entries."""
    stream_labels: Dict[str, str] = Field(default_factory=dict)
    entries: List[LogEntry] = Field(default_factory=list)


class TelemetrySnapshot(BaseModel):
    """Lightweight point-in-time telemetry state."""
    collected_at: datetime = Field(default_factory=datetime.utcnow)
    metrics: Dict[str, float] = Field(
        default_factory=dict,
        description="Metric name -> current value"
    )
    active_alerts: List[str] = Field(default_factory=list)
    error_count: int = 0


class TelemetryDataset(BaseModel):
    """Full telemetry dataset for ML training / analysis."""
    metrics: List[TimeSeries] = Field(default_factory=list)
    logs: List[LogStream] = Field(default_factory=list)
    collection_start: datetime
    collection_end: datetime
    source_cluster: str = "default"

    @property
    def duration_seconds(self) -> float:
        return (self.collection_end - self.collection_start).total_seconds()

    @property
    def metric_names(self) -> List[str]:
        return list({ts.metric_name for ts in self.metrics})

    @property
    def total_samples(self) -> int:
        return sum(len(ts.samples) for ts in self.metrics)
