"""
Chaos Toolkit Experiment Open API — canonical runtime schema for ChaosGen.

Spec: https://chaostoolkit.org/reference/api/experiment/
Required experiment fields: title, description, method.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class CtkActivity(BaseModel):
    """Probe or action inside method / hypothesis / rollbacks."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    type: Literal["probe", "action"]
    name: str
    provider: Dict[str, Any]
    tolerance: Optional[Any] = None
    pauses: Optional[Dict[str, Any]] = None
    background: Optional[bool] = None
    controls: Optional[List[Dict[str, Any]]] = None
    secrets: Optional[List[str]] = None


class CtkSteadyStateHypothesis(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    title: str
    probes: List[CtkActivity] = Field(default_factory=list)
    controls: Optional[List[Dict[str, Any]]] = None


class CtkExperiment(BaseModel):
    """Canonical Chaos Toolkit experiment document."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    title: str
    description: str
    method: List[CtkActivity] = Field(..., min_length=1)
    tags: Optional[List[str]] = None
    secrets: Optional[Dict[str, Any]] = None
    controls: Optional[List[Dict[str, Any]]] = None
    contributions: Optional[Dict[str, Any]] = None
    extensions: Optional[List[Dict[str, Any]]] = None
    runtime: Optional[Dict[str, Any]] = None
    steady_state_hypothesis: Optional[CtkSteadyStateHypothesis] = Field(
        default=None,
        alias="steady-state-hypothesis",
    )
    rollbacks: Optional[List[CtkActivity]] = None

    def to_ctk_dict(self) -> Dict[str, Any]:
        """Serialize with CTK hyphenated keys for ``chaos run``."""
        return self.model_dump(by_alias=True, exclude_none=True, mode="json")
