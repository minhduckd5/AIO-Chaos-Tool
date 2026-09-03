"""
ChaosGen experiment intent — fault-centric schema (targets are subset of fault).

Translates to CTK Experiment JSON via ``chaosgen.ucal.ctk_builder``.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class CtkTargetRef(BaseModel):
    service: str
    namespace: str = "default"
    label_key: str = "app"


class CtkFaultIntent(BaseModel):
    fault_type: str
    duration: str = "30s"
    latency: str = "100ms"
    loss_percentage: float = 10.0
    signal: str = "SIGKILL"
    targets: List[CtkTargetRef] = Field(min_length=1)


class CtkExperimentIntent(BaseModel):
    title: str
    description: str = ""
    faults: List[CtkFaultIntent] = Field(min_length=1)
    action_pause_seconds: float = 0
    include_steady_state: bool = False
    prom_url: Optional[str] = None
    tags: List[str] = Field(default_factory=lambda: ["chaosgen"])
    max_actions: int = 3
    blocked_namespaces: List[str] = Field(
        default_factory=lambda: ["kube-system", "monitoring"]
    )
    kube_context: Optional[str] = None
    auto_rollback: bool = True
