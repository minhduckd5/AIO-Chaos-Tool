"""Tests for the GUI advisor presenter helpers (P4.1).

These cover the pure mapping/guard logic used by advisor_view.py without
spinning up a Qt event loop.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from chaosgen.gui.advisor_presenter import (
    description_rows,
    gatekeeper_rows,
    gatekeeper_summary,
    is_fallback,
    promote_blocked_reason,
)
from chaosgen.schemas.faults import FaultType
from chaosgen.schemas.incidents import IncidentCandidate, IncidentVerdict
from chaosgen.schemas.scenarios import (
    AdvisorReport,
    ScenarioKnowledgeState,
    UnknownScenarioDescription,
)


def _candidate(cluster_id, verdict, service="payments"):
    return IncidentCandidate(
        cluster_id=cluster_id,
        frequency=2.1,
        severity=0.82,
        log_correlated=True,
        service_target=service,
        verdict=verdict,
        rationale=f"verdict {verdict.value}",
    )


def _description(cluster_id, *, fallback=False, state=ScenarioKnowledgeState.DESCRIBED):
    return UnknownScenarioDescription(
        title=f"Incident {cluster_id}",
        root_cause_hypothesis="Connection pool exhausted under retry storm",
        repro_steps=["Send load", "Observe latency climb"],
        blast_radius_estimate="Primary impact: payments service",
        suggested_fault_type=FaultType.NETWORK_LATENCY,
        confidence=0.0 if fallback else 0.8,
        source_incident_id=cluster_id,
        knowledge_state=state,
        metadata={"describe_fallback": True} if fallback else {},
    )


def _report():
    return AdvisorReport(
        anomalies_found=3,
        incident_candidates=[
            _candidate(0, IncidentVerdict.REAL),
            _candidate(1, IncidentVerdict.TRANSIENT),
        ],
        filtered_noise_count=2,
        filtered_transient_count=1,
        descriptions=[
            _description(0),
            _description(2, fallback=True, state=ScenarioKnowledgeState.UNKNOWN),
        ],
    )


class TestGatekeeperRows:
    def test_row_count_excludes_noise(self):
        rows = gatekeeper_rows(_report())
        assert len(rows) == 2  # NOISE is summarized, not listed

    def test_verdict_and_downstream_mapping(self):
        rows = {r["cluster"]: r for r in gatekeeper_rows(_report())}
        assert rows["0"]["verdict"] == "REAL"
        assert rows["0"]["downstream"] == "→ chaos"
        assert rows["0"]["describe"] == "described"
        assert rows["1"]["verdict"] == "TRANSIENT"
        assert rows["1"]["downstream"] == "monitor only"


class TestDescriptionRows:
    def test_all_filter_returns_everything(self):
        assert len(description_rows(_report(), "All")) == 2

    def test_described_filter(self):
        rows = description_rows(_report(), "Described")
        assert len(rows) == 1
        assert rows[0].source_incident_id == 0

    def test_unknown_filter_returns_fallback(self):
        rows = description_rows(_report(), "Unknown")
        assert len(rows) == 1
        assert is_fallback(rows[0])


class TestPromoteGuards:
    def test_fallback_blocked(self):
        desc = _description(2, fallback=True, state=ScenarioKnowledgeState.UNKNOWN)
        assert promote_blocked_reason(desc) is not None

    def test_unknown_state_blocked(self):
        desc = _description(3, state=ScenarioKnowledgeState.UNKNOWN)
        assert promote_blocked_reason(desc) is not None

    def test_described_allowed(self):
        desc = _description(0)
        assert promote_blocked_reason(desc) is None

    def test_promote_blocked_for_non_pass_verdict(self):
        from chaosgen.schemas.scenarios import ExperimentVerdict

        desc = _description(0)
        assert promote_blocked_reason(desc, verdict=ExperimentVerdict.FAIL) is not None
        assert promote_blocked_reason(desc, verdict=ExperimentVerdict.PASS) is None


class TestSummary:
    def test_summary_reports_counts(self):
        summary = gatekeeper_summary(_report())
        assert "noise dropped: 2" in summary
        assert "transient: 1" in summary
        assert "Described: 1" in summary
