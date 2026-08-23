from typing import Dict, Any, List, Optional
import time
import logging

from transitions import Machine

from .modules.base import BaseChaosModule
from .modules.chaos_toolkit import ChaosToolkitModule
from .modules.kube_monkey import KubeMonkeyModule
from .modules.pumba import PumbaModule
from .modules.chaos_monkey import ChaosMonkeyModule
from .modules.toxiproxy import ToxiproxyModule
from .modules.muxy import MuxyModule
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
    
    # Module registry
    MODULE_REGISTRY = {
        'chaos-toolkit': ChaosToolkitModule,
        'kube-monkey': KubeMonkeyModule,
        'pumba': PumbaModule,
        'chaos-monkey': ChaosMonkeyModule,
        'toxiproxy': ToxiproxyModule,
        'muxy': MuxyModule
    }

    states = ['idle', 'pending_approval', 'steady_state_check', 'injecting', 'verifying', 'rollback']

    def __init__(self, config_path: Optional[str] = None, history_store=None):
        """
        Initialize the chaos orchestrator.
        
        Args:
            config_path: Path to configuration file
            history_store: Optional P5 analytics store for experiment verdicts
        """
        self.config_loader = ConfigLoader(config_path) if config_path else ConfigLoader()
        self.modules: Dict[str, BaseChaosModule] = {}
        self._initialize_modules()
        
        self.translator = ChaosTranslator()
        self.validator = SteadyStateValidator()
        # MODIFIED: P8 — blast radius from ChaosGenSettings.safety when available
        from chaosgen.config.settings import load_settings
        from chaosgen.safety.governance import SafetyPolicy

        try:
            cg_settings = load_settings()
            safety_policy = SafetyPolicy.from_settings(cg_settings.safety)
        except Exception:
            safety_policy = None
        self.blast_radius_controller = BlastRadiusController(safety_policy)
        self.dead_mans_switch: Optional[DeadMansSwitch] = None
        self.logger = logging.getLogger("ChaosOrchestrator")
        self.history_store = history_store
        self._current_experiment_db_id: Optional[int] = None
        self._experiment_db_ids: Dict[str, int] = {}
        # MODIFIED: P0-B — last operational verdict from verification
        self.last_verdict_report = None

        # HITL approval queue for AI-generated experiments
        self.pending_experiments: List[ChaosExperiment] = []
        self.pending_report: Optional[AdvisorReport] = None

        # Initialize State Machine
        self.machine = Machine(model=self, states=ChaosOrchestrator.states, initial='idle')
        
        # Original transitions
        self.machine.add_transition(trigger='start_experiment', source='idle', dest='steady_state_check', after='_run_steady_state_check')
        self.machine.add_transition(trigger='check_passed', source='steady_state_check', dest='injecting', after='_execute_injection')
        self.machine.add_transition(trigger='check_failed', source='steady_state_check', dest='idle')
        self.machine.add_transition(trigger='injection_complete', source='injecting', dest='verifying', after='_run_verification')
        self.machine.add_transition(trigger='verification_complete', source='verifying', dest='idle', after='_cleanup_safety')
        self.machine.add_transition(trigger='trigger_rollback', source='*', dest='rollback', after='_execute_rollback')
        self.machine.add_transition(trigger='rollback_complete', source='rollback', dest='idle', after='_cleanup_safety')

        # HITL approval gate transitions
        self.machine.add_transition(trigger='submit_for_approval', source='idle', dest='pending_approval')
        self.machine.add_transition(trigger='approve_experiment', source='pending_approval', dest='steady_state_check', after='_run_steady_state_check')
        self.machine.add_transition(trigger='reject_experiment', source='pending_approval', dest='idle', after='_clear_pending')

    def _initialize_modules(self) -> None:
        """Initialize all configured chaos modules."""
        module_configs = self.config_loader.get_all_modules()
        
        for module_name, module_class in self.MODULE_REGISTRY.items():
            config = module_configs.get(module_name, {})
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
        self.logger.info(f"Starting experiment: {experiment.name}")
        self.start_experiment()

    def _run_steady_state_check(self):
        """Verify system health before starting."""
        self.logger.info("Running steady-state check...")
        
        success = True
        
        if self.current_experiment.steady_state_check:
             # Validate Blast Radius before proceeding
             try:
                 self.blast_radius_controller.validate_experiment(self.current_experiment)
             except ValueError as e:
                 self.logger.error(f"Safety Policy Violation: {e}")
                 self.check_failed()
                 return

             success = self.validator.validate(self.current_experiment.steady_state_check)

             # Start Dead Man's Switch if check passes
             if success:
                 self._start_dead_mans_switch()

        if success:
            self.logger.info("Steady-state check passed.")
            self.check_passed()
        else:
            self.logger.error("Steady-state check failed. Aborting.")
            self.check_failed()

    def _start_dead_mans_switch(self):
        """Initialize and start the DMS monitoring thread."""
        if self.current_experiment.steady_state_check:
            self.dead_mans_switch = DeadMansSwitch(
                check_fn=lambda: self.validator.validate(self.current_experiment.steady_state_check),
                trigger_fn=self.trigger_rollback,
                interval=5 # Configurable?
            )
            self.dead_mans_switch.start()

    def _cleanup_safety(self):
        """Stop safety monitoring."""
        if self.dead_mans_switch:
            self.dead_mans_switch.stop()
            self.dead_mans_switch = None

    def _execute_injection(self):
        """Translate and execute faults."""
        self.logger.info("Injecting faults...")
        try:
            plans = self.translator.translate(self.current_experiment)
            self.active_plans = plans
            
            for plan in plans:
                self.logger.info(f"Executing: {plan.tool_name} -> {plan.action}")
                result = self.execute_action(plan.tool_name, plan.action, plan.params)
                if not result.get('success', True): # Assuming modules return success=True/False or raise
                     # In strict mode we might rollback here
                     self.logger.warning(f"Injection failed: {result}")

            # Wait for duration if specified (simple sleep for now, could be async)
            # This blocks the main thread, in async version use await asyncio.sleep
            # For this synchronous implementation, we just move on or sleep if needed.
            
            self.injection_complete()
            
        except Exception as e:
            self.logger.error(f"Injection error: {e}")
            self.trigger_rollback()

    def _run_verification(self):
        """Verify expectations after chaos — operational verdict (P0-B)."""
        self.logger.info("Verifying system against expectations...")

        criteria = self.current_experiment.steady_state_check or {}
        report = None
        success = True

        if criteria:
            # MODIFIED: P0-B — ExpectationVerdictEngine for SLA rationale
            from chaosgen.advisor.catalog_promoter import evaluate_acceptance_detailed
            from chaosgen.config.telemetry_endpoints import resolve_prometheus_url

            try:
                prom_url = resolve_prometheus_url()
            except Exception:
                prom_url = None

            report = evaluate_acceptance_detailed(
                criteria,
                experiment_name=self.current_experiment.name,
                poll=True,
                prometheus_url=prom_url,
            )
            self.last_verdict_report = report
            success = report.verdict.value == "pass" or report.verdict.value == "partial"
            if report.verdict.value == "pass":
                self.logger.info("Verification PASS: %s", report.rationale)
            elif report.verdict.value == "partial":
                self.logger.warning("Verification PARTIAL: %s", report.rationale)
            else:
                self.logger.warning("Verification FAIL: %s", report.rationale)
                success = False

            try:
                from chaosgen.advisor.report_store import save_verdict_report

                save_verdict_report(report)
            except Exception as exc:
                self.logger.debug("Could not persist verdict report: %s", exc)
        else:
            self.last_verdict_report = None
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

        self.verification_complete()

    def _execute_rollback(self):
        """Rollback injected faults."""
        self.logger.info("Rolling back...")
        
        # Stop monitoring immediately to prevent double triggers
        if self.dead_mans_switch:
            self.dead_mans_switch.stop()
            self.dead_mans_switch = None

        # Logic to reverse actions if possible (e.g., delete netem rules)
        # Many chaos tools (like Pumba) have auto-cleanup or specific stop commands.
        self.rollback_complete()

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
