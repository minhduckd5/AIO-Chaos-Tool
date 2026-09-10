from typing import Dict, Any, List, Optional
import json
import os
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
        # MODIFIED: remember settings path so SS/reload do not silently use APPDATA
        self._settings_path = config_path
        self._run_id: Optional[str] = None
        self.active_manifests: List[Dict[str, Any]] = []
        self.suite_manifests: List[Dict[str, Any]] = []
        self.active_plans = []
        self.last_rollback_status: Optional[str] = None
        self.last_outcome: Optional[str] = None
        self._suite_mode: bool = False
        self._suite_abort: bool = False
        self.suite_results: List[Dict[str, Any]] = []
        # --- START MODIFICATION ---
        self._ctk_run_active: bool = False
        # --- END MODIFICATION ---

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
        from chaosgen.config.connect_routing import apply_connect_profile_to_orchestrator

        apply_connect_profile_to_orchestrator(self)
        self.history_store = history_store
        self._current_experiment_db_id: Optional[int] = None
        self._experiment_db_ids: Dict[str, int] = {}
        self.last_verdict_report = None
        self.current_experiment: Optional[ChaosExperiment] = None

        self.pending_experiments: List[ChaosExperiment] = []
        self.pending_report: Optional[AdvisorReport] = None

        # --- START MODIFICATION ---
        # P1 audit trail: emit-only hooks. Audit failures must never change
        # chaos flow, so every emit is best-effort (see _audit_emit).
        self.audit_store = None
        self._audit_actor: Optional[str] = None
        self._audit_path: str = "operator_direct"
        self._audit_path_override: Optional[str] = None
        self._audit_warned: bool = False
        self._injecting: bool = False
        # --- END MODIFICATION ---

        # --- START MODIFICATION ---
        # Form-first inject: operator environment overrides hint/auto-detect
        # --- END MODIFICATION ---

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

    def reload_cg_settings(self, config_path: Optional[str] = None) -> None:
        """
        Re-read settings.yaml mid-session (GUI Settings Save).

        Without this, Approve/inject keeps the kubeconfig and operator_name
        snapshot from process start while Advisor/Experiments forms update.
        """
        # --- START MODIFICATION ---
        from chaosgen.config.connect_routing import apply_connect_profile_to_orchestrator
        from chaosgen.config.settings import load_settings
        from chaosgen.safety.governance import SafetyPolicy

        path = config_path if config_path is not None else getattr(self, "_settings_path", None)
        if config_path is not None:
            self._settings_path = config_path
        try:
            self._cg_settings = load_settings(path)
            safety_policy = SafetyPolicy.from_settings(self._cg_settings.safety)
        except Exception as exc:
            self.logger.warning("reload_cg_settings failed: %s", exc)
            return

        self.blast_radius_controller = BlastRadiusController(safety_policy)
        inject = getattr(self._cg_settings, "inject", None)
        hints_env = None
        if self._cg_settings.hints:
            env = self._cg_settings.hints.environment
            hints_env = env.value if hasattr(env, "value") else str(env) if env else None
        self.translator = ChaosTranslator(
            inject_settings=inject,
            hints_environment=hints_env,
        )
        self._initialize_modules()
        apply_connect_profile_to_orchestrator(self)
        self.logger.info("Reloaded ChaosGen settings from disk")
        # --- END MODIFICATION ---

    def _initialize_modules(self) -> None:
        module_configs = self.config_loader.get_all_modules()
        inject_cfg: Dict[str, Any] = {}
        ctk_cfg: Dict[str, Any] = {}
        connect_cfgs: Dict[str, Dict[str, Any]] = {}
        if self._cg_settings:
            from chaosgen.config.connect_routing import module_connect_configs, sync_inject_from_connect

            sync_inject_from_connect(self._cg_settings)
            connect_cfgs = module_connect_configs(self._cg_settings)
            inj = self._cg_settings.inject
        else:
            inj = None
        if inj:
            # --- START MODIFICATION ---
            # Phase C: native client + optional SSH bastion config
            bastion = inj.ssh_bastion
            inject_cfg = {
                "kubeconfig": inj.kubeconfig,
                "context": inj.context,
                "default_namespace": inj.default_namespace,
                "dry_run": inj.dry_run,
                "client": inj.client,
                "kubectl_timeout_s": inj.kubectl_timeout_s,
                "delete_force_on_timeout": inj.delete_force_on_timeout,
                "managed_by_label": inj.managed_by_label,
                "ephemeral_label": inj.ephemeral_label,
                "ssh_bastion": bastion.model_dump() if bastion else {},
            }
            ctk_cfg = {
                "dry_run": inj.dry_run,
                "timeout_s": max(120, int(inj.kubectl_timeout_s) * 4),
            }
            # --- END MODIFICATION ---
        for module_name, module_class in self.MODULE_REGISTRY.items():
            config = module_configs.get(module_name, {})
            if module_name == "kubectl-chaos":
                config = {**inject_cfg, **connect_cfgs.get("kubectl-chaos", {}), **config}
            if module_name == "pumba":
                config = {**connect_cfgs.get("pumba", {}), **config}
            if module_name == "toxiproxy":
                config = {**connect_cfgs.get("toxiproxy", {}), **config}
            if module_name == "chaos-toolkit" and self._cg_settings and self._cg_settings.inject:
                config = {**ctk_cfg, **config}
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

    # --- START MODIFICATION ---
    # Form-first: operator environment + inject fields override hints/auto-detect
    def set_execution_environment(self, environment: str) -> None:
        """Lock UCAL tool mapping to an operator-selected environment."""
        from chaosgen.ucal.translator import ExecutionEnvironment

        key = (environment or "").strip().lower()
        mapping = {
            "kubernetes": ExecutionEnvironment.KUBERNETES,
            "k8s": ExecutionEnvironment.KUBERNETES,
            "k3s": ExecutionEnvironment.KUBERNETES,
            "docker": ExecutionEnvironment.DOCKER,
            "docker_compose": ExecutionEnvironment.DOCKER,
            "compose": ExecutionEnvironment.DOCKER,
            "systemd": ExecutionEnvironment.SYSTEMD,
        }
        env = mapping.get(key)
        if env is None:
            raise ValueError(
                f"Unsupported execution environment: {environment!r} "
                "(expected kubernetes|docker|systemd)"
            )
        self.translator.set_environment(env)
        self.logger.info("Execution environment locked to %s (form)", env.value)

    def configure_inject_from_form(
        self,
        *,
        environment: str,
        kubeconfig: Optional[str] = None,
        context: Optional[str] = None,
        dry_run: Optional[bool] = None,
        default_namespace: Optional[str] = None,
        label_key: Optional[str] = None,
    ) -> None:
        """Apply Experiments form fields before translate/inject."""
        self.set_execution_environment(environment)
        inj = getattr(self._cg_settings, "inject", None) if self._cg_settings else None
        if inj is not None:
            if kubeconfig is not None:
                inj.kubeconfig = kubeconfig or None
            if context is not None:
                inj.context = context or None
            if dry_run is not None:
                inj.dry_run = bool(dry_run)
            if default_namespace is not None:
                inj.default_namespace = default_namespace or "default"
            if label_key is not None:
                inj.label_key = label_key or "app"
            inj.enabled = True
            self.translator.inject = inj
        if self._cg_settings is not None:
            from chaosgen.config.connect_routing import apply_connect_profile_to_orchestrator

            apply_connect_profile_to_orchestrator(self)
    # --- END MODIFICATION ---

    def run_ctk_experiment(
        self,
        *,
        title: str,
        description: str = "",
        targets: Optional[List[Dict[str, Any]]] = None,
        faults: Optional[List[Dict[str, Any]]] = None,
        dry_run: Optional[bool] = None,
        prom_url: Optional[str] = None,
        label_key: str = "app",
        kube_context: Optional[str] = None,
        include_steady_state: bool = False,
        action_pause_seconds: float = 0,
        auto_rollback: bool = True,
    ) -> Dict[str, Any]:
        """
        Build a CTK experiment from intents and execute via ``chaos run``.

        Prefer ``faults`` (fault-centric with nested targets). Legacy ``targets`` flat list still supported.
        """
        import json
        from datetime import datetime, timezone

        from chaosgen.schemas.chaos_intent import (
            CtkExperimentIntent,
            CtkFaultIntent,
            CtkTargetRef,
        )
        from chaosgen.ucal.ctk_builder import build_experiment_from_intent

        inj = getattr(self._cg_settings, "inject", None) if self._cg_settings else None
        max_n = 3
        blocked = ["kube-system", "monitoring"]
        if self._cg_settings and self._cg_settings.safety:
            max_n = int(self._cg_settings.safety.max_services_per_suite)
            blocked = list(self._cg_settings.safety.blocked_namespaces or blocked)

        ctx = kube_context or (inj.context if inj else None)

        if faults:
            fault_models = []
            for f in faults:
                refs = [
                    CtkTargetRef(
                        service=str(t["service"]),
                        namespace=str(t.get("namespace") or "default"),
                        label_key=str(t.get("label_key") or label_key),
                    )
                    for t in f.get("targets") or []
                ]
                if not refs:
                    raise ValueError("each fault requires at least one target")
                fault_models.append(
                    CtkFaultIntent(
                        fault_type=str(f.get("fault_type") or "process_kill"),
                        duration=str(f.get("duration") or "30s"),
                        latency=str(f.get("latency") or "100ms"),
                        loss_percentage=float(f.get("loss_percentage") or 10.0),
                        signal=str(f.get("signal") or "SIGKILL"),
                        targets=refs,
                    )
                )
            experiment_intent = CtkExperimentIntent(
                title=title,
                description=description or title,
                faults=fault_models,
                max_actions=max_n,
                blocked_namespaces=blocked,
                prom_url=prom_url,
                include_steady_state=include_steady_state,
                kube_context=ctx,
                action_pause_seconds=float(action_pause_seconds or 0),
                auto_rollback=bool(auto_rollback),
            )
        elif targets:
            from chaosgen.ucal.ctk_builder import CtkBuildIntent, CtkTargetIntent, build_experiment

            intent_targets = [
                CtkTargetIntent(
                    service=str(t["service"]),
                    namespace=str(t.get("namespace") or "default"),
                    label_key=str(t.get("label_key") or label_key),
                    fault_type=str(t.get("fault_type") or "process_kill"),
                    duration=str(t.get("duration") or "30s"),
                    latency=str(t.get("latency") or "100ms"),
                    loss_percentage=float(t.get("loss_percentage") or 10.0),
                    signal=str(t.get("signal") or "SIGKILL"),
                )
                for t in (targets or [])
            ]
            experiment = build_experiment(
                CtkBuildIntent(
                    title=title,
                    description=description or title,
                    targets=intent_targets,
                    max_actions=max_n,
                    blocked_namespaces=blocked,
                    prom_url=prom_url,
                    include_steady_state=include_steady_state,
                    kube_context=ctx,
                    action_pause_seconds=float(action_pause_seconds or 0),
                    auto_rollback=bool(auto_rollback),
                )
            )
        else:
            raise ValueError("CTK experiment requires faults[] or targets[]")

        if faults:
            experiment = build_experiment_from_intent(experiment_intent)

        out_dir = Path("scratch") / "ctk"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        exp_path = out_dir / f"experiment-{stamp}.json"
        exp_path.write_text(
            json.dumps(experiment.to_ctk_dict(), indent=2),
            encoding="utf-8",
        )
        self.logger.info("Wrote CTK experiment: %s", exp_path)

        mod = self.get_module("chaos-toolkit")
        if not mod:
            return {"success": False, "error": "chaos-toolkit module not loaded", "path": str(exp_path)}

        use_dry = inj.dry_run if dry_run is None and inj else bool(dry_run)
        if dry_run is not None:
            use_dry = bool(dry_run)
        # Sync module dry-run
        if hasattr(mod, "dry_run"):
            mod.dry_run = use_dry

        # Ensure kubeconfig visible to chaosk8s
        if inj and inj.kubeconfig:
            os.environ.setdefault("KUBECONFIG", str(Path(inj.kubeconfig).expanduser()))
        if ctx:
            os.environ.setdefault("KUBERNETES_CONTEXT", ctx)

        # A2: CTK is the canonical runtime for operator-driven runs — same
        # hatch_used -> inject_started -> inject_finished trio as run_experiment.
        from chaosgen.storage.audit import TargetClusterContext

        ctk_context = TargetClusterContext(
            kube_context=ctx,
            kube_namespace=(inj.default_namespace if inj else None),
            environment_hint="kubernetes",
        )
        self._audit_path = "operator_direct"
        self._run_id = str(uuid.uuid4())
        self._audit_emit(
            "hatch_used", path_used="operator_direct", experiment_name=title
        )
        self._audit_emit(
            "inject_started",
            path_used="operator_direct",
            experiment_name=title,
            target_cluster_context=ctk_context,
        )

        self._ctk_run_active = True
        self._injecting = True
        try:
            result = mod.execute(
                "run_experiment",
                {"experiment_file": str(exp_path), "dry_run": use_dry},
            )
        finally:
            self._ctk_run_active = False
            self._injecting = False

        self._audit_emit(
            "inject_finished",
            path_used="operator_direct",
            experiment_name=title,
            target_cluster_context=ctk_context,
            outcome=self._outcome_from_result({**result, "dry_run": use_dry}),
        )

        result["experiment_path"] = str(exp_path)
        result["ctk_title"] = title
        try:
            report = self._evaluate_ctk_run(
                result,
                title=title,
                description=description or title,
            )
            self.last_verdict_report = report
            result["verdict"] = report.verdict.value
            self.last_outcome = report.verdict.value.upper()
        except Exception as exc:
            self.logger.warning("CTK verdict evaluation failed: %s", exc)
            if result.get("aborted"):
                self.last_outcome = "ABORTED"
            elif result.get("success"):
                self.last_outcome = "PASS" if not result.get("deviated") else "FAIL"
            else:
                self.last_outcome = "FAIL"
        self.logger.info(
            "CTK run finished success=%s aborted=%s status=%s journal=%s verdict=%s",
            result.get("success"),
            result.get("aborted"),
            result.get("journal_status"),
            result.get("journal_path"),
            result.get("verdict"),
        )
        return result

    def _evaluate_ctk_run(
        self,
        result: Dict[str, Any],
        *,
        title: str,
        description: str,
        acceptance_criteria: Optional[Dict[str, Any]] = None,
    ):
        """Parse CTK journal and build ExpectationVerdictReport (+ optional Prom merge)."""
        from chaosgen.evaluation.ctk_verdict import build_verdict_from_ctk_run
        from chaosgen.advisor.report_store import save_verdict_report
        from chaosgen.config.telemetry_endpoints import resolve_prometheus_url

        prom_url: Optional[str] = None
        try:
            prom_url = resolve_prometheus_url()
        except Exception:
            pass

        criteria = acceptance_criteria
        if criteria is None:
            demo = Path("examples/demo-expectation-criteria.yaml")
            if demo.is_file():
                import yaml

                criteria = yaml.safe_load(demo.read_text(encoding="utf-8")) or None

        report = build_verdict_from_ctk_run(
            result,
            experiment_name=title,
            description=description,
            acceptance_criteria=criteria,
            prometheus_url=prom_url,
            poll_telemetry=not bool(result.get("dry_run")),
        )
        save_verdict_report(report)
        return report

    def halt_active_experiment(self) -> Dict[str, Any]:
        """
        HALT / ABORT: stop in-flight CTK ``chaos run`` then legacy rollback + inject-gc.

        Phase 1 — kill orchestrator subprocess (Popen tree).
        Phase 2 — state-machine rollback (manifests + inject-gc).
        """
        outcome: Dict[str, Any] = {"ctk_aborted": False}

        mod = self.get_module("chaos-toolkit")
        if mod and (self._ctk_run_active or getattr(mod, "is_run_active", lambda: False)()):
            if hasattr(mod, "abort_run"):
                outcome["ctk_aborted"] = bool(mod.abort_run())
                self.logger.warning("HALT: CTK subprocess abort requested")

        self.last_outcome = "ABORTED"
        try:
            self.trigger_rollback()
        except Exception as exc:
            self.logger.warning("HALT rollback transition failed: %s", exc)

        return outcome

    def list_modules(self) -> List[str]:
        """
        List all available chaos modules.
        
        Returns:
            List of module names
        """
        return list(self.modules.keys())

    # --- START MODIFICATION ---
    # P1 audit trail (docs/audit-log-schema-proposal.md)
    # --- END MODIFICATION ---

    def set_audit_context(
        self,
        *,
        actor: Optional[str] = None,
        path_used: Optional[str] = None,
        audit_store=None,
    ) -> None:
        """
        Bind operator identity and entry path for audit emission.

        ``path_used`` set here overrides the path inferred from the entry point
        (used by ``chaosgen run --approve-all --force``, A4).
        """
        if actor is not None:
            self._audit_actor = actor.strip() or None
        if path_used is not None:
            self._audit_path_override = path_used or None
        if audit_store is not None:
            self.audit_store = audit_store

    def _get_audit_store(self):
        if self.audit_store is None:
            from chaosgen.storage.audit import AuditStore

            self.audit_store = AuditStore(history_store=self.history_store)
        return self.audit_store

    def _resolve_audit_actor(self) -> Optional[str]:
        """Configured operator only — never prompt or fabricate here (A8)."""
        if self._audit_actor:
            return self._audit_actor
        from chaosgen.storage.audit import AuditActorRequired, resolve_actor

        try:
            self._audit_actor = resolve_actor(settings=self._cg_settings)
        except AuditActorRequired:
            return None
        return self._audit_actor

    def _audit_emit(self, event_type: str, **fields: Any) -> None:
        """Best-effort audit emit; a broken audit path never blocks chaos flow."""
        actor = self._resolve_audit_actor()
        if not actor:
            if not self._audit_warned:
                self._audit_warned = True
                self.logger.warning(
                    "Audit event %s not recorded: no operator_name configured "
                    "(set it via `chaosgen config set-operator <name>`)",
                    event_type,
                )
            return

        fields.setdefault("path_used", self._audit_path_override or self._audit_path)
        fields.setdefault("run_id", self._run_id)
        if "experiment_name" not in fields and self.current_experiment is not None:
            fields["experiment_name"] = self.current_experiment.name
        try:
            self._get_audit_store().emit(
                event_type=event_type, actor=actor, **fields
            )
        except Exception as exc:
            self.logger.warning("Audit emit failed for %s: %s", event_type, exc)

    def _audit_target_context(self):
        """Resolve where an inject is aimed — evidence for A5."""
        from chaosgen.storage.audit import TargetClusterContext

        inject = getattr(self._cg_settings, "inject", None) if self._cg_settings else None
        connect = getattr(self._cg_settings, "connect", None) if self._cg_settings else None
        hints = getattr(self._cg_settings, "hints", None) if self._cg_settings else None

        kube_context = (inject.context if inject else None) or (
            connect.kubernetes.context if connect else None
        )
        namespace = None
        if self.current_experiment is not None and self.current_experiment.target:
            namespace = self.current_experiment.target.namespace
        if not namespace and inject:
            namespace = inject.default_namespace
        docker_host = connect.docker.host if connect else None
        environment = getattr(hints, "environment", None) if hints else None
        environment_hint = (
            environment.value if hasattr(environment, "value") else environment
        )
        return TargetClusterContext(
            kube_context=kube_context,
            kube_namespace=namespace,
            docker_host=docker_host,
            environment_hint=environment_hint,
        )

    def _audit_blast_radius_ref(self, *, validation_ok: bool):
        from chaosgen.storage.audit import BlastRadiusRef

        policy = getattr(self.blast_radius_controller, "policy", None)
        namespaces: List[str] = []
        if self.current_experiment is not None and self.current_experiment.target:
            ns = self.current_experiment.target.namespace
            if ns:
                namespaces.append(ns)
        return BlastRadiusRef(
            namespaces=namespaces,
            blocked_namespaces=list(getattr(policy, "blocked_namespaces", []) or []),
            validation_ok=validation_ok,
        )

    def execute_action(self, module_name: str, action: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Direct execution of a module action (Legacy/Direct Mode)."""
        module = self.get_module(module_name)
        if not module:
            return {'success': False, 'error': f'Module not found: {module_name}'}

        # A7: direct module calls are the highest-risk hatch. Calls made from
        # inside _execute_injection already belong to the FSM inject chain.
        external = not self._injecting
        label = f"{module_name}.{action}"
        if external:
            self._audit_emit(
                "hatch_used", path_used="module_direct", notes=label
            )
            self._audit_emit(
                "inject_started",
                path_used="module_direct",
                notes=label,
                target_cluster_context=self._audit_target_context(),
            )
        try:
            result = module.execute(action, params)
        except Exception as e:
            result = {'success': False, 'error': str(e)}
        if external:
            self._audit_emit(
                "inject_finished",
                path_used="module_direct",
                notes=label,
                outcome=self._outcome_from_result(result),
            )
        return result

    @staticmethod
    def _outcome_from_result(result: Dict[str, Any]) -> str:
        if result.get("aborted") or result.get("timeout"):
            return "aborted"
        if result.get("dry_run"):
            return "dry_run"
        return "success" if result.get("success") else "failure"

    # --- State Machine Callbacks ---

    def run_experiment(self, experiment: ChaosExperiment) -> Dict[str, Any]:
        """Entry point to run a full chaos experiment."""
        self.current_experiment = experiment
        if not self._suite_mode:
            self.active_manifests = []
            self.suite_manifests = []
            self._run_id = str(uuid.uuid4())
            self.last_rollback_status = None
            self.last_outcome = None
        else:
            self.active_manifests = []
            self.last_outcome = None
        if not self._run_id:
            self._run_id = str(uuid.uuid4())
        self.logger.info("Starting experiment: %s (run_id=%s)", experiment.name, self._run_id)
        # A2: direct run is an intentional operator hatch (no AI HITL gate).
        self._audit_path = "operator_direct"
        self._audit_emit("hatch_used", path_used="operator_direct")
        self.start_experiment()
        return {
            "ran": True,
            "outcome": self.last_outcome,
            "experiment": experiment.name,
        }

    def run_experiment_suite(
        self,
        experiments: List[ChaosExperiment],
        *,
        delay_seconds: float = 0.0,
    ) -> Dict[str, Any]:
        """
        Phase B — cascading multi-service blast.

        Runs experiments sequentially under one run_id. Defers CR cleanup until
        suite end (or HALT). Caps length via safety.max_services_per_suite.
        """
        if not experiments:
            raise ValueError("experiment suite is empty")

        max_n = 3
        if self._cg_settings and self._cg_settings.safety:
            max_n = int(self._cg_settings.safety.max_services_per_suite)
        if len(experiments) > max_n:
            raise ValueError(
                f"suite size {len(experiments)} exceeds max_services_per_suite={max_n}"
            )

        self._suite_mode = True
        self._suite_abort = False
        self.suite_manifests = []
        self.suite_results = []
        self.active_manifests = []
        self._run_id = str(uuid.uuid4())
        self.last_rollback_status = None
        self.last_outcome = None

        self.logger.info(
            "Starting experiment suite: %d target(s), run_id=%s, delay=%ss",
            len(experiments),
            self._run_id,
            delay_seconds,
        )
        self._audit_path = "operator_direct"
        self._audit_emit(
            "hatch_used",
            path_used="operator_direct",
            experiment_name=experiments[0].name,
            notes=f"suite entry: {len(experiments)} target(s)",
        )

        try:
            for i, exp in enumerate(experiments):
                if self._suite_abort:
                    self.logger.warning("Suite aborted before remaining targets")
                    self.last_outcome = "PARTIAL"
                    break
                if i > 0 and delay_seconds > 0:
                    time.sleep(float(delay_seconds))
                try:
                    self.run_experiment(exp)
                    self.suite_results.append(
                        {
                            "name": exp.name,
                            "target": exp.target.name,
                            "outcome": self.last_outcome or "PASS",
                        }
                    )
                    if self.last_outcome in ("INCONCLUSIVE", "ABANDONED"):
                        self._suite_abort = True
                        break
                    if self.state not in ("idle",):
                        # Unexpected stuck state — abort suite
                        self._suite_abort = True
                        self.last_outcome = "PARTIAL"
                        break
                except Exception as exc:
                    self.logger.error("Suite member failed: %s", exc)
                    self.suite_results.append(
                        {
                            "name": exp.name,
                            "target": exp.target.name,
                            "outcome": "FAIL",
                            "error": str(exc),
                        }
                    )
                    self._suite_abort = True
                    self.last_outcome = "PARTIAL"
                    break
        finally:
            # Shared rollback for all suite CRs
            self.active_manifests = list(self.suite_manifests) + list(self.active_manifests)
            self.suite_manifests = []
            self._suite_mode = False
            if self.active_manifests:
                status = self._rollback_manifests(best_effort=False)
                if status != "pass" and self.last_outcome not in (
                    "INCONCLUSIVE",
                    "ABANDONED",
                    "FAIL",
                ):
                    self.last_outcome = "PARTIAL"
            self._suite_abort = False

        return {
            "run_id": self._run_id,
            "outcome": self.last_outcome or "PASS",
            "rollback": self.last_rollback_status,
            "results": list(self.suite_results),
        }

    def _default_steady_state(self) -> Dict[str, Any]:
        """Prom-based default when form does not hardcode localhost health."""
        from chaosgen.config.settings import load_settings
        from chaosgen.config.telemetry_endpoints import resolve_prometheus_url

        # --- START MODIFICATION ---
        # Prefer the orchestrator-bound settings (_cg_settings), refreshed by
        # reload_cg_settings after Settings Save. Do NOT call bare load_settings()
        # first — that reads APPDATA and ignores config_path / mid-session reload.
        settings = self._cg_settings
        if settings is None:
            try:
                settings = load_settings(getattr(self, "_settings_path", None))
            except Exception:
                settings = None
        try:
            prom_url = resolve_prometheus_url(settings)
        except Exception:
            prom_url = None
        # --- END MODIFICATION ---
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
            # MODIFIED: inject-time blast-radius gate (G2) — also re-checked in _execute_injection
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

        # --- START MODIFICATION ---
        # G3: always arm Dead Man's Switch before inject when steady-state path succeeds
        if success:
            self._start_dead_mans_switch()
            self.logger.info("Steady-state check passed.")
            self.check_passed()
        else:
            self.logger.error("Steady-state check failed. Aborting.")
            self.last_outcome = "FAIL"
            self.check_failed()
        # --- END MODIFICATION ---

    def _start_dead_mans_switch(self):
        # --- START MODIFICATION ---
        # G3: fall back to default Prom check when experiment has none
        if not self.current_experiment:
            return
        check = self.current_experiment.steady_state_check
        if not check:
            check = self._default_steady_state()
            if check:
                self.current_experiment.steady_state_check = check
        if not check:
            self.logger.warning(
                "Dead man's switch not started: no steady-state check available"
            )
            return
        self.dead_mans_switch = DeadMansSwitch(
            check_fn=lambda: self.validator.validate(
                self.current_experiment.steady_state_check
            ),
            trigger_fn=self.trigger_rollback,
            interval=5,
        )
        self.dead_mans_switch.start()
        # --- END MODIFICATION ---

    def _cleanup_safety(self):
        if self.dead_mans_switch:
            self.dead_mans_switch.stop()
            self.dead_mans_switch = None

    def _execute_injection(self):
        """Translate faults → ActionPlans → kubectl apply / delete_pod."""
        self.logger.info("Injecting faults...")
        # --- START MODIFICATION ---
        # G2: defense-in-depth — re-validate blast radius immediately before inject
        try:
            if self.current_experiment is not None:
                self.blast_radius_controller.validate_experiment(self.current_experiment)
        except ValueError as e:
            self.logger.error("Safety Policy Violation at inject: %s", e)
            self.last_outcome = "FAIL"
            self._audit_emit(
                "inject_started",
                outcome="blocked",
                blast_radius_ref=self._audit_blast_radius_ref(validation_ok=False),
                notes=f"blocked: blast radius violation ({e})",
            )
            self.trigger_rollback()
            return
        # --- END MODIFICATION ---

        # --- START MODIFICATION ---
        # A5: an inject we cannot attribute to a target scope is rejected —
        # "not prod" must be provable, not assumed.
        context = self._audit_target_context()
        if not context.is_resolvable():
            self.logger.error(
                "Inject rejected: target cluster context unresolvable "
                "(no kube context/namespace, docker host, or environment hint)"
            )
            self.last_outcome = "FAIL"
            self._audit_emit(
                "inject_started",
                outcome="blocked",
                target_cluster_context=context,
                notes="blocked: unresolvable target_cluster_context (A5)",
            )
            self.trigger_rollback()
            return
        # --- END MODIFICATION ---

        # --- START MODIFICATION ---
        # Soft Settings Save may leave kubeconfig empty/missing — fail loudly
        # here (not silent ambient kubectl / opaque exception).
        kube = self.get_module("kubectl-chaos")
        if kube is not None:
            kube_path = getattr(kube, "kubeconfig", None)
            if kube_path and not Path(kube_path).is_file():
                msg = (
                    f"Cannot inject: kubeconfig not found at {kube_path}. "
                    "Set Connect → Kubernetes kubeconfig in Settings and Save."
                )
                self.logger.error(msg)
                self.last_outcome = "FAIL"
                self._audit_emit(
                    "inject_started",
                    outcome="blocked",
                    notes=f"blocked: {msg}",
                )
                self.trigger_rollback()
                return
            if not kube_path:
                default_kube = Path.home() / ".kube" / "config"
                env_kube = os.environ.get("KUBECONFIG")
                has_default = bool(env_kube) or default_kube.is_file()
                if not has_default:
                    msg = (
                        "Cannot inject: no kubeconfig configured and no default "
                        "~/.kube/config (or KUBECONFIG) on this machine. "
                        "Set Connect → Kubernetes kubeconfig in Settings and Save."
                    )
                    self.logger.error(msg)
                    self.last_outcome = "FAIL"
                    self._audit_emit(
                        "inject_started",
                        outcome="blocked",
                        notes=f"blocked: {msg}",
                    )
                    self.trigger_rollback()
                    return
        # --- END MODIFICATION ---

        inject = self._cg_settings.inject if self._cg_settings else None
        self._audit_emit(
            "inject_started",
            target_cluster_context=context,
            blast_radius_ref=self._audit_blast_radius_ref(validation_ok=True),
        )
        self._injecting = True
        finished_emitted = False
        try:
            plans = self.translator.translate(self.current_experiment)
            self.active_plans = plans
            writer = None

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
                    self._injecting = False
                    self._audit_emit(
                        "inject_finished",
                        target_cluster_context=context,
                        outcome=(
                            "aborted"
                            if self.last_outcome == "INCONCLUSIVE"
                            else "failure"
                        ),
                        notes=f"{plan.tool_name}.{plan.action} failed",
                    )
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

            self._injecting = False
            self._audit_emit(
                "inject_finished",
                target_cluster_context=context,
                outcome="dry_run" if (inject and inject.dry_run) else "success",
            )
            finished_emitted = True
            self.injection_complete()

        except Exception as e:
            self.logger.error("Injection error: %s", e)
            self.last_outcome = "PARTIAL"
            self._injecting = False
            # Verification runs inside injection_complete(); do not emit a
            # second terminal row if the inject itself already finished.
            if not finished_emitted:
                self._audit_emit(
                    "inject_finished",
                    target_cluster_context=context,
                    outcome="failure",
                    notes=f"injection error: {e}",
                )
            self.trigger_rollback()
        finally:
            self._injecting = False

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
        if self._suite_mode:
            # Defer delete until suite end so cascading faults stay active
            self.suite_manifests.extend(self.active_manifests)
            self.active_manifests = []
        elif self.active_manifests:
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

        if self._suite_mode:
            self._suite_abort = True
            self.active_manifests = list(self.suite_manifests) + list(self.active_manifests)
            self.suite_manifests = []

        # --- START MODIFICATION ---
        # HALT / CTK: sweep ephemeral Chaos Mesh CRs after abort
        # --- END MODIFICATION ---
        try:
            self.inject_gc()
        except Exception as exc:
            self.logger.warning("inject_gc during rollback: %s", exc)

        status = self._rollback_manifests(best_effort=False)
        if status != "pass" and not self.last_outcome:
            self.last_outcome = "PARTIAL"
        self._audit_emit(
            "rollback",
            outcome="success" if status == "pass" else "failure",
            notes=f"rollback status={status}",
        )
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
        # A1: AI-generated experiments only ever enter the queue here.
        self._audit_path = "ai_hitl"
        self._audit_emit(
            "queued",
            path_used=self._audit_path_override or "ai_hitl",
            experiment_name=self.pending_experiments[0].name,
            notes=f"{len(self.pending_experiments)} experiment(s) awaiting approval",
        )
        self.submit_for_approval()

    def approve_and_run(self, experiment_index: int = 0) -> Dict[str, Any]:
        """Approve a specific pending experiment and execute it.

        Returns a small outcome dict for GUI toast honesty (ran/outcome/reason).
        """
        if not self.pending_experiments:
            self.logger.warning("No pending experiments to approve.")
            return {"ran": False, "outcome": None, "reason": "no pending experiments"}

        if experiment_index >= len(self.pending_experiments):
            self.logger.error("Invalid experiment index: %d", experiment_index)
            return {
                "ran": False,
                "outcome": None,
                "reason": f"invalid experiment index {experiment_index}",
            }

        # Clear stale PASS from a previous run before this approve cycle.
        self.last_outcome = None
        self.current_experiment = self.pending_experiments[experiment_index]
        self._current_experiment_db_id = self._experiment_db_ids.get(
            self.current_experiment.name
        )
        self.logger.info("Approved experiment: %s", self.current_experiment.name)
        # A1/A4: the approval decision is its own audit row; inject events that
        # follow carry the same run_id.
        self._audit_path = "ai_hitl"
        if not self._run_id:
            self._run_id = str(uuid.uuid4())
        self._audit_emit("approved")
        self.approve_experiment()
        return {
            "ran": True,
            "outcome": self.last_outcome,
            "experiment": self.current_experiment.name
            if self.current_experiment
            else None,
        }

    def reject_all(self) -> None:
        """Reject all pending experiments and return to idle."""
        if self.state == 'pending_approval':
            self.logger.info("Rejected %d pending experiments.", len(self.pending_experiments))
            self._audit_emit(
                "rejected",
                path_used=self._audit_path_override or "ai_hitl",
                experiment_name=(
                    self.pending_experiments[0].name
                    if self.pending_experiments
                    else None
                ),
                notes=f"{len(self.pending_experiments)} experiment(s) rejected",
            )
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
