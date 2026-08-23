"""
P0-A: Persistent human-readable labels for KMeans cluster IDs.

Sidecar JSON next to a trained model joblib, e.g.:
  baseline_model.joblib
  baseline_model.labels.json

Operators edit ``name`` / ``description`` after ``chaosgen train-model``.
Inference paths load the sidecar and annotate AnomalySummary.service_name
when a label exists — cluster IDs stay stable across runs.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from chaosgen.schemas.scenarios import AnomalySummary

logger = logging.getLogger(__name__)


class ClusterLabel(BaseModel):
    cluster_id: int
    name: str = Field(description="Short operator label, e.g. cpu_spike")
    description: str = ""
    notes: str = Field(
        default="",
        description="Free-form thesis notes; optional severity / playbook hints",
    )


class ClusterLabelCatalog(BaseModel):
    version: int = 1
    model_path: str | None = None
    labels: list[ClusterLabel] = Field(default_factory=list)

    def by_id(self) -> dict[int, ClusterLabel]:
        return {item.cluster_id: item for item in self.labels}


class ClusterLabelStore:
    """Load / save cluster label sidecar JSON beside a model file."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._catalog = ClusterLabelCatalog()

    @classmethod
    def sidecar_for_model(cls, model_path: str | Path) -> "ClusterLabelStore":
        model = Path(model_path)
        sidecar = model.with_suffix(model.suffix + ".labels.json")
        if model.suffix == ".joblib":
            sidecar = model.with_name(model.stem + ".labels.json")
        return cls(sidecar)

    def load(self) -> ClusterLabelCatalog:
        if not self.path.exists():
            self._catalog = ClusterLabelCatalog()
            return self._catalog
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self._catalog = ClusterLabelCatalog.model_validate(raw)
        logger.info("Loaded %d cluster labels from %s", len(self._catalog.labels), self.path)
        return self._catalog

    def save(self, catalog: ClusterLabelCatalog | None = None) -> Path:
        if catalog is not None:
            self._catalog = catalog
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = self._catalog.model_dump()
        self.path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        logger.info("Wrote cluster label stub to %s", self.path)
        return self.path

    def write_stub(
        self,
        cluster_ids: list[int],
        *,
        model_path: str | Path | None = None,
        overwrite: bool = False,
    ) -> Path:
        """
        Create placeholder labels for operator editing.

        Existing labeled names are preserved unless *overwrite* is True.
        """
        existing = self.load().by_id() if self.path.exists() and not overwrite else {}
        labels: list[ClusterLabel] = []
        for cid in sorted(set(cluster_ids)):
            if cid in existing and not overwrite:
                labels.append(existing[cid])
            else:
                labels.append(
                    ClusterLabel(
                        cluster_id=cid,
                        name=f"cluster_{cid}",
                        description="TODO: replace with operational label after reviewing features",
                    )
                )
        catalog = ClusterLabelCatalog(
            model_path=str(model_path) if model_path else None,
            labels=labels,
        )
        return self.save(catalog)

    def annotate_summaries(self, summaries: list[AnomalySummary]) -> list[AnomalySummary]:
        """Replace service_name with labeled name when cluster_id is known."""
        mapping = self._catalog.by_id()
        if not mapping:
            return summaries
        out: list[AnomalySummary] = []
        for summary in summaries:
            label = mapping.get(summary.source_cluster_id)
            if label is None:
                out.append(summary)
                continue
            # Stub names (cluster_N) leave service_name unchanged until operator edits
            if label.name == f"cluster_{label.cluster_id}":
                out.append(summary)
                continue
            out.append(summary.model_copy(update={"service_name": label.name}))
        return out
