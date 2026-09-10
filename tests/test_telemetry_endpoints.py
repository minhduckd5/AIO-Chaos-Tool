"""Telemetry URL resolution must honor settings.yaml (demo-critical)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from chaosgen.config.settings import (
    AuthConfig,
    ChaosGenSettings,
    ObservabilityHint,
)
from chaosgen.config.telemetry_endpoints import (
    DEFAULT_PROMETHEUS_URL,
    resolve_loki_url,
    resolve_prometheus_url,
)
from chaosgen.schemas.discovery import ObservabilityTool


def _settings_with_urls(prom: str, loki: str) -> ChaosGenSettings:
    s = ChaosGenSettings()
    s.hints.observability = [
        ObservabilityHint(
            tool=ObservabilityTool.PROMETHEUS, url=prom, auth=AuthConfig()
        ),
        ObservabilityHint(tool=ObservabilityTool.LOKI, url=loki, auth=AuthConfig()),
    ]
    return s


class TestResolvePrometheusUrl:
    def test_uses_settings_when_provided(self):
        settings = _settings_with_urls(
            "http://10.50.1.220:9090", "http://10.50.1.220:3100"
        )
        assert resolve_prometheus_url(settings) == "http://10.50.1.220:9090"
        assert resolve_loki_url(settings) == "http://10.50.1.220:3100"

    def test_loads_disk_when_settings_omitted(self):
        settings = _settings_with_urls(
            "http://10.50.1.220:9090/", "http://10.50.1.220:3100/"
        )
        with patch(
            "chaosgen.config.settings.load_settings", return_value=settings
        ):
            assert resolve_prometheus_url(None) == "http://10.50.1.220:9090"
            assert resolve_loki_url(None) == "http://10.50.1.220:3100"

    def test_falls_back_to_localhost_default(self):
        with patch(
            "chaosgen.config.settings.load_settings",
            side_effect=RuntimeError("no settings"),
        ):
            assert resolve_prometheus_url(None) == DEFAULT_PROMETHEUS_URL


class TestDefaultSteadyState:
    def test_default_ss_uses_resolved_prom_from_disk(self, tmp_path: Path):
        from chaosgen.orchestrator import ChaosOrchestrator

        settings = _settings_with_urls(
            "http://10.50.1.220:9090", "http://10.50.1.220:3100"
        )
        orch = ChaosOrchestrator.__new__(ChaosOrchestrator)
        orch._cg_settings = settings
        orch._settings_path = None
        check = ChaosOrchestrator._default_steady_state(orch)
        assert check["prometheus"]["url"] == "http://10.50.1.220:9090"
        assert check["prometheus"]["query"] == 'up{job=~".+"}'

    def test_default_ss_ignores_appdata_when_cg_settings_bound(self):
        from chaosgen.orchestrator import ChaosOrchestrator

        bound = _settings_with_urls(
            "http://10.50.1.220:9090", "http://10.50.1.220:3100"
        )
        stale_appdata = _settings_with_urls(
            "http://192.168.31.220:9090", "http://192.168.31.220:3100"
        )
        orch = ChaosOrchestrator.__new__(ChaosOrchestrator)
        orch._cg_settings = bound
        orch._settings_path = None
        with patch(
            "chaosgen.config.settings.load_settings", return_value=stale_appdata
        ):
            check = ChaosOrchestrator._default_steady_state(orch)
        assert check["prometheus"]["url"] == "http://10.50.1.220:9090"


class TestReloadCgSettings:
    def test_reload_picks_up_new_kubeconfig(self):
        from chaosgen.orchestrator import ChaosOrchestrator

        orch = ChaosOrchestrator.__new__(ChaosOrchestrator)
        orch.logger = __import__("logging").getLogger("test")
        orch._cg_settings = ChaosGenSettings()
        orch._audit_actor = None
        orch.modules = {}
        orch.config_loader = type("CL", (), {"get_all_modules": lambda self: {}, "config_path": None})()
        orch.blast_radius_controller = None
        orch.translator = None

        updated = _settings_with_urls(
            "http://10.50.1.220:9090", "http://10.50.1.220:3100"
        )
        updated.connect.kubernetes.kubeconfig = "H:/lab/kubeconfig"
        updated.inject.kubeconfig = "H:/lab/kubeconfig"

        with patch(
            "chaosgen.config.settings.load_settings", return_value=updated
        ), patch.object(
            ChaosOrchestrator, "_initialize_modules", lambda self: None
        ), patch(
            "chaosgen.config.connect_routing.apply_connect_profile_to_orchestrator"
        ):
            ChaosOrchestrator.reload_cg_settings(orch)

        assert orch._cg_settings.inject.kubeconfig == "H:/lab/kubeconfig"
        assert orch.blast_radius_controller is not None
        assert orch.translator is not None


class TestKubectlReloadFromConfig:
    def test_instance_fields_follow_config(self):
        from chaosgen.modules.kubectl_chaos import KubectlChaosModule

        mod = KubectlChaosModule({"kubeconfig": None, "context": "old"})
        mod.reload_from_config(
            {"kubeconfig": None, "context": "lab-k3s", "default_namespace": "default"}
        )
        assert mod.context == "lab-k3s"
        assert mod.default_namespace == "default"


class TestMissingKubeconfigInjectGate:
    def test_missing_kubeconfig_path_blocks_inject_clearly(self, tmp_path: Path):
        from chaosgen.orchestrator import ChaosOrchestrator
        from chaosgen.schemas.faults import (
            ChaosExperiment,
            FaultSpec,
            FaultType,
            TargetSpec,
            TargetType,
        )

        missing = tmp_path / "no-such-kubeconfig"
        settings = _settings_with_urls(
            "http://10.50.1.220:9090", "http://10.50.1.220:3100"
        )
        settings.inject.kubeconfig = str(missing)
        settings.connect.kubernetes.kubeconfig = str(missing)
        settings.connect.kubernetes.default_namespace = "default"
        settings.hints.environment = __import__(
            "chaosgen.schemas.discovery", fromlist=["EnvironmentType"]
        ).EnvironmentType.KUBERNETES

        orch = ChaosOrchestrator.__new__(ChaosOrchestrator)
        orch.logger = __import__("logging").getLogger("test-missing-kube")
        orch._cg_settings = settings
        orch._run_id = "test-run"
        orch._injecting = False
        orch._audit_actor = "tester"
        orch._audit_path = "ai_hitl"
        orch._audit_path_override = None
        orch.audit_store = None
        orch.history_store = None
        orch.last_outcome = None
        orch.current_experiment = ChaosExperiment(
            name="missing-kube",
            target=TargetSpec(
                type=TargetType.SERVICE,
                name="checkoutservice",
                namespace="default",
                selector={"app": "checkoutservice"},
            ),
            faults=[
                FaultSpec(
                    fault_type=FaultType.PROCESS_KILL,
                    target=TargetSpec(
                        type=TargetType.SERVICE,
                        name="checkoutservice",
                        namespace="default",
                        selector={"app": "checkoutservice"},
                    ),
                    duration="5s",
                )
            ],
        )
        from chaosgen.modules.kubectl_chaos import KubectlChaosModule

        orch.modules = {
            "kubectl-chaos": KubectlChaosModule({"kubeconfig": str(missing)})
        }
        orch.blast_radius_controller = type(
            "BR",
            (),
            {"validate_experiment": lambda self, exp: None, "policy": None},
        )()
        emitted: list[tuple] = []

        def _emit(event_type, **fields):
            emitted.append((event_type, fields))

        orch._audit_emit = _emit  # type: ignore[method-assign]
        orch._audit_target_context = lambda: type(
            "Ctx",
            (),
            {"is_resolvable": lambda self: True},
        )()
        orch._audit_blast_radius_ref = lambda **kw: None
        orch.trigger_rollback = lambda: None  # type: ignore[method-assign]

        ChaosOrchestrator._execute_injection(orch)

        assert orch.last_outcome == "FAIL"
        assert any(
            ev == "inject_started"
            and fields.get("outcome") == "blocked"
            and "kubeconfig" in str(fields.get("notes") or "").lower()
            for ev, fields in emitted
        )
        assert not any(ev == "inject_finished" for ev, _ in emitted)
