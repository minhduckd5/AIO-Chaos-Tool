"""
P0-B — Expectation / operational verdict engine.

Turns post-chaos checks into an advisor-grade verdict:
  claim → measure SLA predicates → PASS/FAIL/PARTIAL + human rationale

Backward compatible with legacy acceptance criteria keys:
  http_health, prometheus
"""

from __future__ import annotations

import logging
import operator
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import requests
from pydantic import BaseModel, Field

from chaosgen.schemas.scenarios import ExperimentVerdict
from chaosgen.ucal.validation import SteadyStateValidator

logger = logging.getLogger(__name__)

_OPS: dict[str, Callable[[float, float], bool]] = {
    ">=": operator.ge,
    "<=": operator.le,
    ">": operator.gt,
    "<": operator.lt,
    "==": operator.eq,
    "!=": operator.ne,
}


class ExpectationCheckResult(BaseModel):
    id: str
    check_type: str
    passed: bool
    required: bool = True
    observed: float | None = None
    threshold: float | None = None
    op: str | None = None
    message: str


class ExpectationVerdictReport(BaseModel):
    """Structured operational verdict for thesis demo / history."""

    claim: str
    verdict: ExperimentVerdict
    rationale: str
    checks: list[ExpectationCheckResult] = Field(default_factory=list)
    experiment_name: str | None = None
    evaluated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_prompt_block(self) -> str:
        lines = [
            f"Claim: {self.claim}",
            f"Verdict: {self.verdict.value.upper()}",
            f"Rationale: {self.rationale}",
            "Checks:",
        ]
        for c in self.checks:
            mark = "PASS" if c.passed else "FAIL"
            lines.append(f"  - [{mark}] {c.id}: {c.message}")
        return "\n".join(lines)

    # --- START MODIFICATION ---
    # P0-B UI: plain-language helpers for non-technical stakeholders
    # --- END MODIFICATION ---

    def stakeholder_headline(self) -> str:
        """One-line outcome for business / non-tech readers."""
        mapping = {
            ExperimentVerdict.PASS: "System met the resilience claim",
            ExperimentVerdict.PARTIAL: "System partly met the claim — residual risk remains",
            ExperimentVerdict.FAIL: "System did not meet the resilience claim",
        }
        return mapping.get(self.verdict, f"Outcome: {self.verdict.value}")

    def stakeholder_status_label(self) -> str:
        mapping = {
            ExperimentVerdict.PASS: "PASSED",
            ExperimentVerdict.PARTIAL: "NEEDS ATTENTION",
            ExperimentVerdict.FAIL: "FAILED — IMPROVE",
        }
        return mapping.get(self.verdict, self.verdict.value.upper())

    def improvement_notes(self) -> list[str]:
        """Actionable, non-PromQL improvement bullets for stakeholders."""
        notes: list[str] = []
        if self.verdict == ExperimentVerdict.PASS:
            notes.append(
                "No blocking gaps from this run. Keep monitoring under peak load "
                "and re-test after infrastructure changes."
            )
            return notes

        for check in self.checks:
            if check.passed:
                continue
            cid = check.id.lower()
            if "scale" in cid or "replica" in cid or check.check_type == "prometheus_threshold":
                notes.append(
                    "Capacity / autoscaling did not meet the agreed time or size target. "
                    "Review scale-up triggers, cooldown, and readiness probes with the platform team."
                )
            elif "http" in cid or check.check_type == "http_health":
                notes.append(
                    "Service health check failed during or after the test. "
                    "Confirm the customer-facing endpoint stays available under stress."
                )
            elif "prom" in cid:
                notes.append(
                    "A measured operational signal missed its threshold. "
                    "Ask engineering which SLO this maps to and adjust capacity or timeouts."
                )
            else:
                notes.append(
                    f"Check “{check.id}” did not pass — treat as a hardening item for the next release."
                )

        if self.verdict == ExperimentVerdict.PARTIAL:
            notes.append(
                "Required checks passed, but optional gaps remain. "
                "Document accepted residual risk or schedule a follow-up experiment."
            )
        elif not notes:
            notes.append(
                "Review the outcome summary with engineering and capture a concrete fix "
                "before the next chaos cycle."
            )

        # De-dupe while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for n in notes:
            if n not in seen:
                seen.add(n)
                unique.append(n)
        return unique


class ExpectationVerdictEngine:
    """
    Evaluate operational expectations (SLA-style) against live probes.

    Criteria document shape (YAML/JSON)::

        claim: "Autoscaling restores ready replicas within 45s under CPU stress"
        expectations:
          - id: http_alive
            type: http_health
            url: http://frontend/health
          - id: scale_up_sla
            type: prometheus_threshold
            url: http://192.168.31.220:9090
            query: 'sum(kube_deployment_status_replicas_ready{deployment="frontend"})'
            op: ">="
            threshold: 2
            window_seconds: 45
            sample_interval_seconds: 5

    Legacy keys ``http_health`` / ``prometheus`` are still accepted and mapped
    into expectation checks when ``expectations`` is absent.
    """

    def __init__(
        self,
        *,
        default_prometheus_url: str | None = None,
        http_timeout: int = 5,
        session: requests.Session | None = None,
    ) -> None:
        self.default_prometheus_url = default_prometheus_url
        self.http_timeout = http_timeout
        self._session = session or requests.Session()
        self._legacy = SteadyStateValidator()

    def evaluate(
        self,
        criteria: dict[str, Any] | None,
        *,
        experiment_name: str | None = None,
        poll: bool = True,
    ) -> ExpectationVerdictReport:
        criteria = criteria or {}
        claim = str(criteria.get("claim") or "").strip() or (
            "System meets stated steady-state / acceptance criteria after chaos"
        )
        checks_spec = self._normalize_checks(criteria)
        results: list[ExpectationCheckResult] = []

        for spec in checks_spec:
            results.append(self._run_check(spec, poll=poll))

        required_failed = [r for r in results if r.required and not r.passed]
        optional_failed = [r for r in results if not r.required and not r.passed]

        if not results:
            verdict = ExperimentVerdict.PASS
            rationale = (
                f"{claim} — no expectations defined; treated as PASS "
                "(configure expectations for an operational verdict)."
            )
        elif required_failed:
            verdict = ExperimentVerdict.FAIL
            failed_ids = ", ".join(r.id for r in required_failed)
            detail = required_failed[0].message
            rationale = (
                f"{claim} — FAIL. Required expectation(s) missed ({failed_ids}). "
                f"Primary gap: {detail}"
            )
        elif optional_failed:
            verdict = ExperimentVerdict.PARTIAL
            failed_ids = ", ".join(r.id for r in optional_failed)
            rationale = (
                f"{claim} — PARTIAL (accepted residual risk). "
                f"Required checks passed; optional gaps: {failed_ids}."
            )
        else:
            verdict = ExperimentVerdict.PASS
            rationale = (
                f"{claim} — PASS. All {len(results)} expectation check(s) held "
                "within the configured SLA window."
            )

        return ExpectationVerdictReport(
            claim=claim,
            verdict=verdict,
            rationale=rationale,
            checks=results,
            experiment_name=experiment_name,
        )

    def _normalize_checks(self, criteria: dict[str, Any]) -> list[dict[str, Any]]:
        raw = criteria.get("expectations")
        if isinstance(raw, list) and raw:
            return [c for c in raw if isinstance(c, dict)]

        # Legacy shape → synthetic expectation list
        checks: list[dict[str, Any]] = []
        if "http_health" in criteria:
            checks.append(
                {
                    "id": "http_health",
                    "type": "http_health",
                    "url": criteria["http_health"],
                    "required": True,
                }
            )
        prom = criteria.get("prometheus")
        if isinstance(prom, dict):
            has_threshold = prom.get("threshold") is not None and "op" in prom
            if has_threshold:
                checks.append(
                    {
                        "id": "prometheus",
                        "type": "prometheus_threshold",
                        "url": prom.get("url") or self.default_prometheus_url,
                        "query": prom.get("query"),
                        "op": prom.get("op", ">="),
                        "threshold": prom["threshold"],
                        "window_seconds": prom.get("window_seconds", 0),
                        "sample_interval_seconds": prom.get("sample_interval_seconds", 5),
                        "required": True,
                    }
                )
            else:
                checks.append(
                    {
                        "id": "prometheus",
                        "type": "prometheus_exists",
                        "url": prom.get("url") or self.default_prometheus_url,
                        "query": prom.get("query"),
                        "required": True,
                    }
                )
        return checks

    def _run_check(self, spec: dict[str, Any], *, poll: bool) -> ExpectationCheckResult:
        check_type = str(spec.get("type") or "http_health").lower()
        check_id = str(spec.get("id") or check_type)
        required = bool(spec.get("required", True))

        if check_type == "http_health":
            url = str(spec.get("url") or "")
            ok = self._legacy._check_http(url, timeout=self.http_timeout) if url else False
            return ExpectationCheckResult(
                id=check_id,
                check_type=check_type,
                passed=ok,
                required=required,
                message=(
                    f"HTTP health {url} returned success"
                    if ok
                    else f"HTTP health {url} failed or unreachable"
                ),
            )

        if check_type in ("prometheus_exists", "prometheus"):
            if check_type == "prometheus" and not spec.get("exists_only"):
                pass  # fall through to threshold if threshold present
            else:
                return self._prom_exists(check_id, spec, required)

        if check_type == "prometheus_threshold" or (
            check_type == "prometheus" and not spec.get("exists_only")
        ):
            return self._prom_threshold(check_id, spec, required, poll=poll)

        return ExpectationCheckResult(
            id=check_id,
            check_type=check_type,
            passed=False,
            required=required,
            message=f"Unknown expectation type {check_type!r}",
        )

    def _prom_exists(
        self, check_id: str, spec: dict[str, Any], required: bool
    ) -> ExpectationCheckResult:
        url = spec.get("url") or self.default_prometheus_url
        query = spec.get("query")
        if not url or not query:
            return ExpectationCheckResult(
                id=check_id,
                check_type="prometheus_exists",
                passed=False,
                required=required,
                message="prometheus url/query missing",
            )
        ok = self._legacy._check_prometheus({"url": url, "query": query})
        return ExpectationCheckResult(
            id=check_id,
            check_type="prometheus_exists",
            passed=ok,
            required=required,
            message=(
                f"Prometheus query returned series: {query}"
                if ok
                else f"Prometheus query returned no series: {query}"
            ),
        )

    def _prom_threshold(
        self,
        check_id: str,
        spec: dict[str, Any],
        required: bool,
        *,
        poll: bool,
    ) -> ExpectationCheckResult:
        url = spec.get("url") or self.default_prometheus_url
        query = spec.get("query")
        op_s = str(spec.get("op") or ">=")
        threshold = float(spec.get("threshold", 0))
        window = int(spec.get("window_seconds") or 0)
        interval = float(spec.get("sample_interval_seconds") or 5)

        if not url or not query:
            return ExpectationCheckResult(
                id=check_id,
                check_type="prometheus_threshold",
                passed=False,
                required=required,
                threshold=threshold,
                op=op_s,
                message="prometheus url/query missing",
            )
        if op_s not in _OPS:
            return ExpectationCheckResult(
                id=check_id,
                check_type="prometheus_threshold",
                passed=False,
                required=required,
                threshold=threshold,
                op=op_s,
                message=f"unsupported op {op_s!r}",
            )

        deadline = time.monotonic() + max(window, 0)
        last_val: float | None = None
        passed = False

        while True:
            last_val = self._query_prometheus_scalar(str(url), str(query))
            if last_val is not None and _OPS[op_s](last_val, threshold):
                passed = True
                break
            if not poll or window <= 0 or time.monotonic() >= deadline:
                break
            time.sleep(max(interval, 0.5))

        if passed:
            msg = (
                f"Observed {last_val} {op_s} {threshold} within "
                f"{window or 0}s SLA window (query={query})"
            )
        elif last_val is None:
            msg = (
                f"No numeric Prometheus result for query within "
                f"{window or 0}s (query={query}); threshold {op_s} {threshold} unmet"
            )
        else:
            msg = (
                f"Observed {last_val} did not satisfy {op_s} {threshold} "
                f"within {window or 0}s SLA window (query={query}) — "
                f"capacity/recovery claim not met in time"
            )

        return ExpectationCheckResult(
            id=check_id,
            check_type="prometheus_threshold",
            passed=passed,
            required=required,
            observed=last_val,
            threshold=threshold,
            op=op_s,
            message=msg,
        )

    def _query_prometheus_scalar(self, base_url: str, query: str) -> float | None:
        try:
            resp = self._session.get(
                f"{base_url.rstrip('/')}/api/v1/query",
                params={"query": query},
                timeout=self.http_timeout,
            )
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("status") != "success":
                return None
            results = payload.get("data", {}).get("result") or []
            if not results:
                return None
            # vector: value = [ts, "number"]; scalar-like first series
            value = results[0].get("value")
            if not value or len(value) < 2:
                return None
            return float(value[1])
        except Exception as exc:
            logger.debug("Prometheus scalar query failed: %s", exc)
            return None


def criteria_has_expectations(criteria: dict[str, Any] | None) -> bool:
    if not criteria:
        return False
    if isinstance(criteria.get("expectations"), list) and criteria["expectations"]:
        return True
    return "http_health" in criteria or "prometheus" in criteria
