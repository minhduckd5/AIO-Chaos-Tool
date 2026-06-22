"""
Incident gatekeeper schemas (P1 — Gatekeeper Real Filter).

These models formalize the advisor's `?? real ??` gate: an anomaly cluster is
classified by a frequency x severity matrix (with strict log correlation) into
one of four verdicts. Only REAL / CHRONIC proceed downstream to describe/chaos.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class IncidentVerdict(str, Enum):
    NOISE = "noise"           # low freq + low severity -> discard
    TRANSIENT = "transient"   # monitor only, no auto chaos
    REAL = "real"             # proceed to describe/chaos
    CHRONIC = "chronic"       # high freq -> predictive maintenance candidate


class IncidentCandidate(BaseModel):
    """A gatekeeper-evaluated anomaly cluster with a routing verdict."""

    cluster_id: int
    frequency: float = Field(ge=0.0, description="Events per hour in lookback window")
    severity: float = Field(ge=0.0, le=1.0, description="Normalized severity 0-1")
    log_correlated: bool = Field(
        False,
        description="True only when metric error signal AND severe log signal co-occur",
    )
    service_target: Optional[str] = Field(
        None, description="Primary affected service for downstream targeting"
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Auxiliary context (log signal payload, window range, etc.)",
    )
    verdict: IncidentVerdict
    rationale: str = Field(description="Human-readable explanation for the verdict")

    @property
    def passes_downstream(self) -> bool:
        """REAL and CHRONIC proceed; TRANSIENT is monitor-only; NOISE is dropped."""
        return self.verdict in (IncidentVerdict.REAL, IncidentVerdict.CHRONIC)
