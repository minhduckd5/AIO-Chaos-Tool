"""
Shared advisor pipeline orchestrator (P4 — Pipeline CLI Integration).

Single entry point wiring P1 gatekeeper → P2 describer → downstream filter →
LLM interpret → scenario generation. Used by CLI, GUI, and ``ChaosAdvisor``.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, TYPE_CHECKING

from chaosgen.advisor.context_builder import ScenarioContext
from chaosgen.advisor.llm_advisor import LLMAdvisor, build_provider
from chaosgen.advisor.manifest_writer import ManifestWriter
from chaosgen.advisor.scenario_describer import ScenarioDescriber
from chaosgen.advisor.scenario_generator import ScenarioGenerator
from chaosgen.config.settings import ChaosGenSettings
from chaosgen.ml.gatekeeper import IncidentGatekeeper
from chaosgen.schemas.faults import TargetSpec
from chaosgen.schemas.incidents import IncidentCandidate, IncidentVerdict
from chaosgen.schemas.scenarios import (
    AdvisorReport,
    AnomalyCluster,
    AnomalySeverity,
    AnomalySummary,
    ScenarioKnowledgeState,
    UnknownScenarioDescription,
)

if TYPE_CHECKING:
    from chaosgen.storage.history import HistoryStore

logger = logging.getLogger(__name__)

_SEVERITY_NUMERIC: Dict[AnomalySeverity, float] = {
    AnomalySeverity.LOW: 0.25,
    AnomalySeverity.MEDIUM: 0.50,
    AnomalySeverity.HIGH: 0.75,
    AnomalySeverity.CRITICAL: 1.0,
}


def filter_chaos_descriptions(
    descriptions: List[UnknownScenarioDescription],
) -> List[UnknownScenarioDescription]:
    """Return only LLM-described incidents eligible for chaos generation."""
    return [
        d
        for d in descriptions
        if d.knowledge_state == ScenarioKnowledgeState.DESCRIBED
        and not d.metadata.get("describe_fallback")
    ]


def summaries_for_chaos(
    summaries: List[AnomalySummary],
    chaos_descriptions: List[UnknownScenarioDescription],
) -> List[AnomalySummary]:
    """Restrict anomaly summaries to clusters that survived the describe filter."""
    cluster_ids = {d.source_incident_id for d in chaos_descriptions}
    return [s for s in summaries if s.source_cluster_id in cluster_ids]


def _bypass_candidates(
    clusters: List[AnomalyCluster],
    summaries: List[AnomalySummary],
    window_hours: float,
) -> List[IncidentCandidate]:
    """Synthetic REAL candidates when --skip-gatekeeper is enabled (debug only)."""
    summary_by_cluster = {s.source_cluster_id: s for s in summaries}
    candidates: List[IncidentCandidate] = []
    for cluster in clusters:
        summary = summary_by_cluster.get(cluster.cluster_id)
        service = (
            cluster.affected_services[0]
            if cluster.affected_services
            else (summary.service_name if summary else None)
        )
        frequency = cluster.sample_count / window_hours if window_hours > 0 else 0.0
        candidates.append(
            IncidentCandidate(
                cluster_id=cluster.cluster_id,
                frequency=round(frequency, 4),
                severity=_SEVERITY_NUMERIC.get(cluster.severity, 0.5),
                log_correlated=False,
                service_target=service,
                metadata={"skip_gatekeeper": True},
                verdict=IncidentVerdict.REAL,
                rationale="skip-gatekeeper debug bypass",
            )
        )
    return candidates


def run_advisor_pipeline(
    clusters: List[AnomalyCluster],
    summaries: List[AnomalySummary],
    *,
    settings: ChaosGenSettings,
    lookback_hours: float,
    context: Optional[ScenarioContext] = None,
    skip_gatekeeper: bool = False,
    generate_chaos: bool = True,
    write_manifests: bool = False,
    output_dir: str = "./generated_scenarios",
    confidence_threshold: float | None = None,
    gatekeeper: Optional[IncidentGatekeeper] = None,
    describer: Optional[ScenarioDescriber] = None,
    llm_advisor: Optional[LLMAdvisor] = None,
    scenario_generator: Optional[ScenarioGenerator] = None,
    manifest_writer: Optional[ManifestWriter] = None,
    available_targets: Optional[Dict[str, TargetSpec]] = None,
    history_store: Optional["HistoryStore"] = None,
    report_path: Optional[str] = None,
) -> AdvisorReport:
    """
    Run gatekeeper → describe → downstream filter → interpret → generate.

    ``analyze_dataset`` / anomaly detection stays outside this function.
    """
    if lookback_hours <= 0:
        raise ValueError("lookback_hours must be > 0")

    # MODIFIED: P8 — advisor knobs from settings when caller omits overrides
    effective_confidence = (
        confidence_threshold
        if confidence_threshold is not None
        else settings.advisor.confidence_threshold
    )

    store = history_store
    if store is None and settings.history.enabled:
        from chaosgen.storage.history import get_default_history_store

        store = get_default_history_store(settings, async_writes=False)

    run_id: Optional[int] = None
    if store is not None:
        run_id = store.begin_run(
            lookback_hours=lookback_hours,
            skip_gatekeeper=skip_gatekeeper,
        )

    gk = gatekeeper or IncidentGatekeeper(settings=settings.gatekeeper)
    lookback = store.lookback_store() if store is not None else None

    if skip_gatekeeper:
        logger.warning(
            "skip_gatekeeper=True: bypassing incident gatekeeper (debug only)"
        )
        # A3: gatekeeper bypass is an analysis-stage hatch — it implies no
        # inject, so the chain stops at hatch_used.
        from chaosgen.storage.audit import emit_best_effort

        emit_best_effort(
            event_type="hatch_used",
            path_used="skip_gatekeeper",
            settings=settings,
            history_store=store,
            notes=(
                f"gatekeeper bypass; analysis_run_id={run_id}, "
                f"lookback_hours={lookback_hours}"
            ),
        )
        candidates = _bypass_candidates(clusters, summaries, lookback_hours)
        dropped_noise = 0
    else:
        candidates, dropped_noise = gk.filter(
            clusters, lookback_hours, summaries=summaries, state_store=lookback
        )

    transient_count = sum(
        1 for c in candidates if c.verdict == IncidentVerdict.TRANSIENT
    )
    passed = [c for c in candidates if c.passes_downstream]

    descriptions: List[UnknownScenarioDescription] = []
    if passed:
        desc = describer or ScenarioDescriber(
            provider_name=settings.llm_provider,
            model=settings.llm_model,
            max_retries=settings.advisor.describer_max_retries,
        )
        descriptions = desc.describe_batch(passed, summaries, context)

    chaos_descriptions = filter_chaos_descriptions(descriptions)
    chaos_summaries = summaries_for_chaos(summaries, chaos_descriptions)
    descriptions_by_cluster = {
        d.source_incident_id: d for d in chaos_descriptions
    }

    hypotheses = []
    experiments = []
    manifest_paths: List[str] = []
    sci_scores = []
    dropped_hypotheses = 0

    if generate_chaos and chaos_summaries:
        advisor = llm_advisor or LLMAdvisor(
            provider=build_provider(settings.llm_provider, model=settings.llm_model)
        )
        from chaosgen.safety.governance import SafetyPolicy

        generator = scenario_generator or ScenarioGenerator(
            confidence_threshold=effective_confidence,
            safety_policy=SafetyPolicy.from_settings(settings.safety),
            preferred_inject_targets=list(
                getattr(settings.advisor, "preferred_inject_targets", None) or []
            ),
        )
        hypotheses = advisor.interpret_anomalies(
            chaos_summaries,
            context=context,
            descriptions_by_cluster=descriptions_by_cluster,
        )
        experiments = generator.generate(hypotheses, available_targets)
        dropped_hypotheses = len(hypotheses) - len(
            [
                h
                for h in hypotheses
                if h.confidence >= generator.confidence_threshold
            ]
        )

        if write_manifests and experiments:
            writer = manifest_writer or ManifestWriter(output_dir=output_dir)
            for exp in experiments:
                try:
                    litmus_path = writer.write_litmus(exp)
                    manifest_paths.append(str(litmus_path))
                except Exception as exc:
                    logger.warning("Litmus manifest generation failed: %s", exc)
                try:
                    cm_path = writer.write_chaosmesh(exp)
                    manifest_paths.append(str(cm_path))
                except Exception as exc:
                    logger.warning("ChaosMesh manifest generation failed: %s", exc)
                sci = ScenarioGenerator.compute_sci(exp)
                sci.compute()
                sci_scores.append(sci)
    elif generate_chaos and passed and not chaos_summaries:
        logger.warning(
            "No DESCRIBED incidents after gatekeeper/describe filter; "
            "skipping chaos generation"
        )

    report = AdvisorReport(
        anomalies_found=len(clusters),
        clusters=clusters,
        summaries=summaries,
        incident_candidates=candidates,
        filtered_noise_count=dropped_noise,
        filtered_transient_count=transient_count,
        descriptions=descriptions,
        hypotheses=hypotheses,
        dropped_hypotheses=dropped_hypotheses,
        generated_experiments=experiments,
        manifest_paths=manifest_paths,
        sci_scores=sci_scores,
        run_id=run_id,
    )

    if store is not None and run_id is not None:
        summary_by_cluster = {s.source_cluster_id: s for s in summaries}
        experiment_cluster_ids = [
            hypotheses[i].source_cluster_id if i < len(hypotheses) else None
            for i in range(len(experiments))
        ]
        try:
            snapshot = store.persist_run_snapshot(
                run_id,
                candidates=candidates,
                summaries_by_cluster=summary_by_cluster,
                descriptions=descriptions,
                experiments=experiments,
                sci_scores=sci_scores,
                filtered_noise_count=dropped_noise,
                report_path=report_path,
                experiment_cluster_ids=experiment_cluster_ids,
            )
            report.description_db_ids = snapshot.description_row_ids
            report.experiment_db_ids = snapshot.experiment_row_ids
        except Exception as exc:
            logger.warning("History persist failed (runtime unaffected): %s", exc)

    logger.info(
        "Pipeline report: anomalies=%d candidates=%d descriptions=%d "
        "chaos_eligible=%d experiments=%d",
        report.anomalies_found,
        len(candidates),
        len(descriptions),
        len(chaos_descriptions),
        len(experiments),
    )
    return report
