from typing import Dict, Any, List, Optional
import json
import time
import logging
import uuid
from pathlib import Path

from transitions import Machine

from .modules.base import BaseChaosModule
from .modules.chaos_toolkit import ChaosToolkitModule
from .modules.kube_monkey import KubeMonkeyModule
from .modules.pumba import PumbaModule
from .modules.chaos_monkey import ChaosMonkeyModule
from .modules.toxiproxy import ToxiproxyModule
from .modules.muxy import MuxyModule
from .modules.kubectl_chaos import KubectlChaosModule
from .config.loader import ConfigLoader
from .ucal.translator import ChaosTranslator, ExecutionEnvironment
from .ucal.validation import SteadyStateValidator
from .safety.monitor import DeadMansSwitch
from .safety.governance import BlastRadiusController, SafetyPolicy
from .schemas.faults import ChaosExperiment
from .schemas.scenarios import AdvisorReport


class ChaosOrchestrator:
    """
    Main orchestrator for managing all chaos tools.
    Implements the Event-Driven State Machine with HITL approval gate
    for AI-generated experiments.
    """

    # --- START MODIFICATION ---
    # Real K8s inject via kubectl-chaos + Chaos Mesh manifests
    # --- END MODIFICATION ---
    MODULE_REGISTRY = {
        "chaos-toolkit": ChaosToolkitModule,
        "kube-monkey": KubeMonkeyModule,
        "pumba": PumbaModule,
        "chaos-monkey": ChaosMonkeyModule,
        "toxiproxy": ToxiproxyModule,
        "muxy": MuxyModule,
        "kubectl-chaos": KubectlChaosModule,
    }

    states = ["idle", "pending_approval", "steady_state_check", "injecting", "verifying", "rollback"]

    def __init__(self, config_path: Optional[str] = None, history_store=None):
        self.config_loader = ConfigLoader(config_path) if config_path else ConfigLoader()
        self.modules: Dict[str, BaseChaosModule] = {}
        self._cg_settings = None
        self._run_id: Optional[str] = None
        self.active_manifests: List[Dict[str, Any]] = []
        self.active_plans = []
        self.last_rollback_status: Optional[str] = None
        self.last_outcome: Optional[str] = None

        from chaosgen.config.settings import load_settings
        from chaosgen.safety.governance import SafetyPolicy

        try:
            self._cg_settings = load_settings(config_path)
            safety_policy = SafetyPolicy.from_settings(self._cg_settings.safety)
        except Exception:
            self._cg_settings = None
            safety_policy = None

        self._initialize_modules()
        inject = getattr(self._cg_settings, "inject", None) if self._cg_settings else None
        hints_env = None
        if self._cg_settings and self._cg_settings.hints:
            env = self._cg_settings.hints.environment
            hints_env = env.value if hasattr(env, "value") else str(env) if env else None
        self.translator = ChaosTranslator(
            inject_settings=inject,
            hints_environment=hints_env,
        )
        self.validator = SteadyStateValidator()
        self.blast_radius_controller = BlastRadiusController(safety_policy)
        self.dead_mans_switch: Optional[DeadMansSwitch] = None
        self.logger = logging.getLogger("ChaosOrchestrator")
        self.history_store = history_store
        self._current_experiment_db_id: Optional[int] = None
        self._experiment_db_ids: Dict[str, int] = {}
        self.last_verdict_report = None
        self.current_experiment: Optional[ChaosExperiment] = None

        self.pending_experiments: List[ChaosExperiment] = []
        self.pending_report: Optional[AdvisorReport] = None

        self.machine = Machine(model=self, states=ChaosOrchestrator.states, initial="idle")
        self.machine.add_transition(
            trigger="start_experiment",
            source="idle",
            dest="steady_state_check",
            after="_run_steady_state_check",
        )
        self.machine.add_transition(
            trigger="check_passed",
            source="steady_state_check",
            dest="injecting",
            after="_execute_injection",
        )
        self.machine.add_transition(trigger="check_failed", source="steady_state_check", dest="idle")
        self.machine.add_transition(
            trigger="injection_complete",
            source="injecting",
            dest="verifying",
            after="_run_verification",
        )
        self.machine.add_transition(
            trigger="verification_complete",
            source="verifying",
            dest="idle",
            after="_cleanup_safety",
        )
        self.machine.add_transition(
            trigger="trigger_rollback",
            source="*",
            dest="rollback",
            after="_execute_rollback",
        )
        self.machine.add_transition(
            trigger="rollback_complete",
            source="rollback",
            dest="idle",
            after="_cleanup_safety",
        )
        self.machine.add_transition(
            trigger="submit_for_approval", source="idle", dest="pending_approval"
        )
        self.machine.add_transition(
            trigger="approve_experiment",
            source="pending_approval",
            dest="steady_state_check",
            after="_run_steady_state_check",
        )
        self.machine.add_transition(
            trigger="reject_experiment",
            source="pending_approval",
            dest="idle",
            after="_clear_pending",
        )

    def _initialize_modules(self) -> None:
        module_configs = self.config_loader.get_all_modules()
        inject_cfg: Dict[str, Any] = {}
        if self._cg_settings and self._cg_settings.inject:
            inj = self._cg_settings.inject
            inject_cfg = {
                "kubeconfig": inj.kubeconfig,
                "context": inj.context,
                "default_namespace": inj.default_namespace,
                "dry_run": inj.dry_run,
                "kubectl_timeout_s": inj.kubectl_timeout_s,
                "delete_force_on_timeout": inj.delete_force_on_timeout,
                "managed_by_label": inj.managed_by_label,
                "ephemeral_label": inj.ephemeral_label,
            }
        for module_name, module_class in self.MODULE_REGISTRY.items():
            config = module_configs.get(module_name, {})
            if module_name == "kubectl-chaos":
                config = {**inject_cfg, **config}
            self.modules[module_name] = module_class(config)
    
    def get_module(self, module_name: str) -> Optional[BaseChaosModule]:
        """
        Get a specific chaos module.
        
        Args:
            module_name: Name of the module
            
        Returns:
            BaseChaosModule instance or None
        """
        return self.modules.get(module_name)
    
    def list_modules(self) -> List[str]:
        """
        List all available chaos modules.
        
        Returns:
            List of module names
        """
        return list(self.modules.keys())
    
    def execute_action(self, module_name: str, action: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Direct execution of a module action (Legacy/Direct Mode)."""
        module = self.get_module(module_name)
        if not module:
            return {'success': False, 'error': f'Module not found: {module_name}'}
        try:
            return module.execute(action, params)
        except Exception as e:
            return {'success': False, 'error': str(e)}

    # --- State Machine Callbacks ---

    def run_experiment(self, experiment: ChaosExperiment):
        """Entry point to run a full chaos experiment."""
        self.current_experiment = experiment
        self.active_manifests = []
        self.last_rollback_status = None
        self.last_outcome = None
        self._run_id = str(uuid.uuid4())
        self.logger.info("Starting experiment: %s (run_id=%s)", experiment.name, self._run_id)
        self.start_experiment()

    def _default_steady_state(self) -> Dict[str, Any]:
        """Prom-based default when form does not hardcode localhost health."""
        from chaosgen.config.telemetry_endpoints import resolve_prometheus_url

        try:
            prom_url = resolve_prometheus_url()
        except Exception:
            prom_url = None
        if not prom_url:
            return {}
        return {
            "prometheus": {
                "url": prom_url.rstrip("/"),
                "query": 'up{job=~".+"}',
            }
        }

    def _run_steady_state_check(self):
        """Verify system health before starting."""
        self.logger.info("Running steady-state check...")
        success = True
        check = self.current_experiment.steady_state_check
        if check is None:
            check = self._default_steady_state()
            self.current_experiment.steady_state_check = check or None

        try:
            self.blast_radius_controller.validate_experiment(self.current_experiment)
        except ValueError as e:
            self.logger.error("Safety Policy Violation: %s", e)
            self.last_outcome = "FAIL"
            self.check_failed()
            return

        # Pod count vs blast radius (when kubectl-chaos available)
        kube = self.get_module("kubectl-chaos")
        if kube and self.current_experiment.target:
            label_key = "app"
            if self._cg_settings and self._cg_settings.inject:
                label_key = self._cg_settings.inject.label_key
            selector = self.current_experiment.target.selector or {
                label_key: self.current_experiment.target.name
            }
            counted = kube.execute(
                "count_pods_for_selector",
                {
                    "namespace": self.current_experiment.target.namespace
                    or (self._cg_settings.inject.default_namespace if self._cg_settings else "default"),
                    "label_selector": selector,
                },
            )
            if counted.get("success") and self._cg_settings:
                count = int(counted.get("count") or 0)
                # Soft guard: absolute pod count vs max_affected_nodes * 5 heuristic
                max_pods = max(1, self._cg_settings.safety.max_affected_nodes * 5)
                if count > max_pods:
                    self.logger.error(
                        "Blast radius: %d pods match selector (cap ~%d)", count, max_pods
                    )
                    self.last_outcome = "FAIL"
                    self.check_failed()
                    return

        if check:
            success = self.validator.validate(check)
            if success:
                self._start_dead_mans_switch()

        if success:
            self.logger.info("Steady-state check passed.")
            self.check_passed()
        else:
            self.logger.error("Steady-state check failed. Aborting.")
            self.last_outcome = "FAIL"
            self.check_failed()

    def _start_dead_mans_switch(self):
        if self.current_experiment and self.current_experiment.steady_state_check:
            self.dead_mans_switch = DeadMansSwitch(
                check_fn=lambda: self.validator.validate(
                    self.current_experiment.steady_state_check
                ),
                trigger_fn=self.trigger_rollback,
                interval=5,
            )
            self.dead_mans_switch.start()

    def _cleanup_safety(self):
        if self.dead_mans_switch:
            self.dead_mans_switch.stop()
            self.dead_mans_switch = None

    def _execute_injection(self):
        """Translate faults → ActionPlans → kubectl apply / delete_pod."""
        self.logger.info("Injecting faults...")
        try:
            plans = self.translator.translate(self.current_experiment)
            self.active_plans = plans
            writer = None
            inject = self._cg_settings.inject if self._cg_settings else None

            for plan in plans:
                params = dict(plan.params)
                if plan.tool_name == "kubectl-chaos" and plan.action == "apply_manifest":
                    from chaosgen.advisor.manifest_writer import ManifestWriter

                    if writer is None:
                        writer = ManifestWriter()
                    fault_idx = int(params.get("fault_index", 0))
                    fault = self.current_experiment.faults[fault_idx]
                    path = writer.write_chaosmesh_fault(
                        self.current_experiment,
                        fault,
                        run_id=self._run_id,
                        label_key=(inject.label_key if inject else "app"),
                        managed_by=(inject.managed_by_label if inject else "chaosgen"),
                        ephemeral=(inject.ephemeral_label if inject else "true"),
                        prefer_self_expiring=(
                            inject.prefer_self_expiring_chaos if inject else True
                        ),
                        suffix=str(fault_idx),
                    )
                    import yaml as _yaml

                    doc = _yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                    meta = doc.get("metadata") or {}
                    kind = str(doc.get("kind") or writer.chaos_kind_for(fault)).lower()
                    params["manifest_path"] = str(path)
                    self.active_manifests.append(
                        {
                            "path": str(path),
                            "kind": kind,
                            "name": meta.get("name") or path.stem,
                            "namespace": meta.get("namespace")
                            or params.get("namespace")
                            or "default",
                            "fault_type": fault.fault_type.value,
                        }
                    )

                self.logger.info("Executing: %s -> %s", plan.tool_name, plan.action)
                result = self.execute_action(plan.tool_name, plan.action, params)
                if not result.get("success", False):
                    self.logger.error("Injection failed: %s", result)
                    self.last_outcome = "PARTIAL"
                    if result.get("timeout") or "timeout" in str(result.get("error", "")).lower():
                        self.last_outcome = "INCONCLUSIVE"
                    self.trigger_rollback()
                    return

            # Honor longest fault duration (self-expiring CRs also tick on cluster)
            max_wait = 0
            for fault in self.current_experiment.faults:
                from chaosgen.advisor.manifest_writer import _parse_duration_seconds

                max_wait = max(max_wait, _parse_duration_seconds(fault.duration or "30s"))
            # Cap sleep for responsiveness; CR duration still applies in-cluster
            wait_s = min(max_wait, 120)
            if wait_s > 0 and not (inject and inject.dry_run):
                self.logger.info("Waiting %ss for chaos window...", wait_s)
                time.sleep(wait_s)

            self.injection_complete()

        except Exception as e:
            self.logger.error("Injection error: %s", e)
            self.last_outcome = "PARTIAL"
            self.trigger_rollback()

    def _run_verification(self):
        """Verify expectations after chaos — operational verdict (P0-B)."""
        self.logger.info("Verifying system against expectations...")

        criteria = self.current_experiment.steady_state_check or {}
        report = None
        success = True

        if criteria:
            from chaosgen.advisor.catalog_promoter import evaluate_acceptance_detailed
            from chaosgen.config.telemetry_endpoints import resolve_prometheus_url

            try:
                prom_url = resolve_prometheus_url()
            except Exception:
                prom_url = None

            try:
                report = evaluate_acceptance_detailed(
                    criteria,
                    experiment_name=self.current_experiment.name,
                    poll=True,
                    prometheus_url=prom_url,
                )
                self.last_verdict_report = report
                success = report.verdict.value in ("pass", "partial")
                self.last_outcome = report.verdict.value.upper()
                if report.verdict.value == "pass":
                    self.logger.info("Verification PASS: %s", report.rationale)
                elif report.verdict.value == "partial":
                    self.logger.warning("Verification PARTIAL: %s", report.rationale)
                else:
                    self.logger.warning("Verification FAIL: %s", report.rationale)
                    success = False
            except Exception as exc:
                self.logger.error(
                    "Verification inconclusive (telemetry unreachable): %s", exc
                )
                self.last_outcome = "INCONCLUSIVE"
                self.last_verdict_report = None
                success = False

            try:
                from chaosgen.advisor.report_store import save_verdict_report

                if report is not None:
                    save_verdict_report(report)
            except Exception as exc:
                self.logger.debug("Could not persist verdict report: %s", exc)
        else:
            self.last_verdict_report = None
            self.last_outcome = self.last_outcome or "PASS"
            self.logger.info("No steady_state_check / expectations configured; skipping probes.")

        if self.history_store and self._current_experiment_db_id is not None:
            from datetime import datetime, timezone
            from chaosgen.schemas.scenarios import ExperimentVerdict

            if report is not None:
                verdict = report.verdict
                rationale = report.rationale
            else:
                verdict = ExperimentVerdict.PASS if success else ExperimentVerdict.FAIL
                rationale = None
            try:
                self.history_store.update_verdict(
                    self._current_experiment_db_id,
                    verdict,
                    datetime.now(timezone.utc),
                    rationale=rationale,
                )
            except Exception as exc:
                self.logger.warning("History update_verdict failed: %s", exc)

        # Best-effort cleanup of remaining CRs after verify window
        if self.active_manifests:
            self._rollback_manifests(best_effort=True)

        self.verification_complete()

    def _orphan_path(self) -> Path:
        return Path(".chaosgen") / "orphan_resources.json"

    def _persist_orphans(self, entries: List[Dict[str, Any]]) -> None:
        path = self._orphan_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        existing: List[Dict[str, Any]] = []
        if path.is_file():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                existing = []
        existing.extend(entries)
        path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        self.logger.critical("Orphan resources recorded at %s", path)

    def _rollback_manifests(self, *, best_effort: bool = False) -> str:
        """Delete all tracked manifests; return pass|partial|fail."""
        if not self.active_manifests:
            self.last_rollback_status = "pass"
            return "pass"

        kube = self.get_module("kubectl-chaos")
        if not kube:
            self.last_rollback_status = "fail"
            return "fail"

        failures: List[Dict[str, Any]] = []
        for item in list(self.active_manifests):
            result = kube.execute(
                "delete_manifest",
                {
                    "manifest_path": item.get("path"),
                    "kind": item.get("kind"),
                    "name": item.get("name"),
                    "namespace": item.get("namespace"),
                },
            )
            if not result.get("success"):
                failures.append({**item, "error": result.get("error") or result.get("message")})

        if failures:
            self._persist_orphans(failures)
            # Label sweep fallback (works even without local state on next run)
            kube.execute("gc_ephemeral", {})
            status = "partial"
        else:
            status = "pass"
            self.active_manifests = []

        self.last_rollback_status = status
        if not best_effort:
            self.logger.info("Rollback status: %s", status)
        return status

    def _execute_rollback(self):
        """Rollback injected faults with timeout/force/orphan path."""
        self.logger.info("Rolling back...")
        if self.dead_mans_switch:
            self.dead_mans_switch.stop()
            self.dead_mans_switch = None

        status = self._rollback_manifests(best_effort=False)
        if status != "pass" and not self.last_outcome:
            self.last_outcome = "PARTIAL"
        self.rollback_complete()

    def inject_gc(self) -> Dict[str, Any]:
        """API-label sweep of ephemeral ChaosGen CRs (no local state required)."""
        kube = self.get_module("kubectl-chaos")
        if not kube:
            return {"success": False, "error": "kubectl-chaos module not loaded"}
        return kube.execute("gc_ephemeral", {})

    def get_module_status(self, module_name: str) -> Dict[str, Any]:
        module = self.get_module(module_name)
        if not module:
            return {'success': False, 'error': f'Module not found: {module_name}'}
        return module.get_status()
    
    def get_all_status(self) -> Dict[str, Any]:
        return {name: module.get_status() for name, module in self.modules.items()}

    def get_module_actions(self, module_name: str) -> List[str]:
        module = self.get_module(module_name)
        return module.get_available_actions() if module else []

    def get_all_actions(self) -> Dict[str, List[str]]:
        return {name: module.get_available_actions() for name, module in self.modules.items()}

    # --- HITL Approval Gate ---

    def run_ai_experiment(self, report: AdvisorReport) -> None:
        """
        Submit AI-generated experiments for human approval.
        Experiments enter pending_approval state and require explicit
        approve_experiment() or reject_experiment() to proceed.
        """
        if not report.generated_experiments:
            self.logger.warning("AdvisorReport contains no generated experiments.")
            return

        self.pending_report = report
        self.pending_experiments = list(report.generated_experiments)
        self._experiment_db_ids = dict(getattr(report, "experiment_db_ids", {}) or {})
        self.logger.info(
            "Submitted %d AI-generated experiments for approval.",
            len(self.pending_experiments),
        )
        self.submit_for_approval()

    def approve_and_run(self, experiment_index: int = 0) -> None:
        """Approve a specific pending experiment and execute it."""
        if not self.pending_experiments:
            self.logger.warning("No pending experiments to approve.")
            return

        if experiment_index >= len(self.pending_experiments):
            self.logger.error("Invalid experiment index: %d", experiment_index)
            return

        self.current_experiment = self.pending_experiments[experiment_index]
        self._current_experiment_db_id = self._experiment_db_ids.get(
            self.current_experiment.name
        )
        self.logger.info("Approved experiment: %s", self.current_experiment.name)
        self.approve_experiment()

    def reject_all(self) -> None:
        """Reject all pending experiments and return to idle."""
        if self.state == 'pending_approval':
            self.logger.info("Rejected %d pending experiments.", len(self.pending_experiments))
            self.reject_experiment()

    def _clear_pending(self) -> None:
        """Clear the pending experiments queue."""
        self.pending_experiments = []
        self.pending_report = None
        self._current_experiment_db_id = None
        self._experiment_db_ids = {}

    def get_pending_experiments(self) -> List[ChaosExperiment]:
        """Return the list of experiments awaiting approval."""
        return list(self.pending_experiments)

    # --- AI Scenario Refinement ---

    def refine_scenario(
        self,
        experiment_index: int,
        refinement_prompt: str,
        context: "Optional[Any]" = None,
    ) -> Optional[ChaosExperiment]:
        """
        Re-generate a pending experiment using the user's natural-language
        refinement instruction. The refined experiment replaces the original
        in pending_experiments and remains in pending_approval for re-review.

        Args:
            experiment_index: Index into self.pending_experiments.
            refinement_prompt: Natural-language instruction, e.g.
                "target only the payment service" or "increase duration to 2 minutes".
            context: Optional ScenarioContext from the original generation run.
                     Used to preserve architecture-specific system prompt.

        Returns:
            The refined ChaosExperiment on success, or None on failure.

        The state machine remains in pending_approval — the user must
        explicitly call approve_and_run() again after reviewing the refinement.
        """
        if not self.pending_experiments:
            self.logger.warning("refine_scenario: no pending experiments.")
            return None

        if experiment_index >= len(self.pending_experiments):
            self.logger.error("refine_scenario: invalid index %d", experiment_index)
            return None

        original = self.pending_experiments[experiment_index]
        self.logger.info(
            "Refining experiment '%s' with instruction: %s",
            original.name,
            refinement_prompt,
        )

        try:
            from chaosgen.advisor.llm_advisor import LLMAdvisor
            from chaosgen.schemas.scenarios import AnomalySummary, AnomalySeverity

            advisor = LLMAdvisor()

            # Synthesise a pseudo-AnomalySummary that encodes the refinement request
            refinement_summary = AnomalySummary(
                source_cluster_id=experiment_index,
                severity=AnomalySeverity.MEDIUM,
                description=(
                    f"REFINEMENT REQUEST for experiment '{original.name}': "
                    f"{refinement_prompt}. "
                    f"Original target: {original.target.name}. "
                    f"Original faults: {[f.fault_type.value for f in original.faults]}."
                ),
                dominant_features=[refinement_prompt],
                affected_services=[original.target.name],
            )

            hypotheses = advisor.interpret_anomalies(
                summaries=[refinement_summary],
                context=context,
            )

            if not hypotheses:
                self.logger.warning("Refinement produced no hypotheses — keeping original.")
                return None

            # Re-run through ScenarioGenerator to produce a proper ChaosExperiment
            from chaosgen.advisor.scenario_generator import ScenarioGenerator
            generator = ScenarioGenerator()
            refined_experiments = generator.generate(hypotheses)

            if not refined_experiments:
                self.logger.warning("Refinement generator produced no experiments — keeping original.")
                return None

            refined = refined_experiments[0]
            # Preserve the original name to make HITL review traceable
            refined.name = f"{original.name} (refined)"

            self.pending_experiments[experiment_index] = refined
            self.logger.info("Refinement complete: '%s'", refined.name)
            return refined

        except Exception as exc:
            self.logger.error("Refinement failed: %s", exc)
            return None
