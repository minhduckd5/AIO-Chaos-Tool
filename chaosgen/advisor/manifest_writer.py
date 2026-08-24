import logging
import re
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from jinja2 import Environment, FileSystemLoader, TemplateNotFound

from chaosgen.schemas.faults import (
    ChaosExperiment,
    FaultSpec,
    FaultType,
    NetworkFaultSpec,
    ProcessFaultSpec,
)

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent / "templates"

LITMUS_TEMPLATE_MAP: Dict[FaultType, str] = {
    FaultType.PROCESS_KILL: "litmus_pod_delete.yaml.j2",
    FaultType.SERVICE_FAILURE: "litmus_pod_delete.yaml.j2",
    FaultType.NODE_FAILURE: "litmus_pod_delete.yaml.j2",
    FaultType.NETWORK_LATENCY: "litmus_network_chaos.yaml.j2",
    FaultType.PACKET_LOSS: "litmus_network_chaos.yaml.j2",
}

CHAOSMESH_TEMPLATE_MAP: Dict[FaultType, str] = {
    FaultType.PROCESS_KILL: "chaosmesh_pod_kill.yaml.j2",
    FaultType.SERVICE_FAILURE: "chaosmesh_pod_kill.yaml.j2",
    FaultType.NODE_FAILURE: "chaosmesh_pod_kill.yaml.j2",
    FaultType.NETWORK_LATENCY: "chaosmesh_network.yaml.j2",
    FaultType.PACKET_LOSS: "chaosmesh_network.yaml.j2",
}

CHAOSMESH_KIND: Dict[FaultType, str] = {
    FaultType.PROCESS_KILL: "podchaos",
    FaultType.SERVICE_FAILURE: "podchaos",
    FaultType.NODE_FAILURE: "podchaos",
    FaultType.NETWORK_LATENCY: "networkchaos",
    FaultType.PACKET_LOSS: "networkchaos",
}


def _parse_duration_seconds(duration_str: str) -> int:
    """Convert '30s', '1m', '5m' to integer seconds."""
    match = re.match(r"^(\d+)(s|m|h)$", str(duration_str).strip())
    if not match:
        return 30
    value, unit = int(match.group(1)), match.group(2)
    multiplier = {"s": 1, "m": 60, "h": 3600}
    return value * multiplier.get(unit, 1)


def _require_duration(fault: FaultSpec, *, prefer_self_expiring: bool = True) -> str:
    duration = (fault.duration or "").strip()
    if prefer_self_expiring and not duration:
        raise ValueError(
            "prefer_self_expiring_chaos requires fault.duration "
            "(e.g. '30s') so Chaos Mesh can revoke without ChaosGen alive"
        )
    return duration or "30s"


class ManifestWriter:
    """
    Renders ChaosExperiment objects into declarative YAML manifests
    for LitmusChaos and Chaos Mesh using Jinja2 templates.
    """

    def __init__(self, output_dir: str = "./generated_scenarios"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.jinja_env = Environment(
            loader=FileSystemLoader(str(TEMPLATE_DIR)),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def write_litmus(self, experiment: ChaosExperiment) -> Path:
        """Render a LitmusChaos ChaosEngine YAML manifest."""
        primary_fault = experiment.faults[0] if experiment.faults else None
        if not primary_fault:
            raise ValueError("Experiment has no faults to render.")

        template_name = LITMUS_TEMPLATE_MAP.get(primary_fault.fault_type)
        if not template_name:
            raise ValueError(f"No Litmus template for fault type: {primary_fault.fault_type}")

        context = self._build_template_context(experiment, primary_fault)
        return self._render_and_write(template_name, context, "litmus")

    def write_chaosmesh(self, experiment: ChaosExperiment) -> Path:
        """Render a Chaos Mesh YAML for the primary fault (compat)."""
        primary_fault = experiment.faults[0] if experiment.faults else None
        if not primary_fault:
            raise ValueError("Experiment has no faults to render.")
        return self.write_chaosmesh_fault(experiment, primary_fault)

    def write_chaosmesh_fault(
        self,
        experiment: ChaosExperiment,
        fault: FaultSpec,
        *,
        run_id: Optional[str] = None,
        label_key: str = "app",
        managed_by: str = "chaosgen",
        ephemeral: str = "true",
        prefer_self_expiring: bool = True,
        suffix: Optional[str] = None,
    ) -> Path:
        """Render one Chaos Mesh CR for a single fault with ownership labels."""
        template_name = CHAOSMESH_TEMPLATE_MAP.get(fault.fault_type)
        if not template_name:
            raise ValueError(f"No ChaosMesh template for fault type: {fault.fault_type}")

        duration = _require_duration(fault, prefer_self_expiring=prefer_self_expiring)
        fault_copy = fault.model_copy(update={"duration": duration})

        context = self._build_template_context(
            experiment,
            fault_copy,
            run_id=run_id or str(uuid.uuid4()),
            label_key=label_key,
            managed_by=managed_by,
            ephemeral=ephemeral,
        )
        # Unique name per fault when multi-fault
        fault_tag = fault.fault_type.value.replace("_", "-")
        base = re.sub(r"[^a-zA-Z0-9-]", "-", experiment.name.lower())[:40]
        context["experiment_name"] = f"{base}-{fault_tag}" + (f"-{suffix}" if suffix else "")
        context["experiment_label"] = re.sub(r"[^a-z0-9-]", "-", experiment.name.lower())[:63]

        return self._render_and_write(
            template_name,
            context,
            f"chaosmesh-{fault_tag}",
        )

    def chaos_kind_for(self, fault: FaultSpec) -> str:
        return CHAOSMESH_KIND.get(fault.fault_type, "podchaos")

    def _build_template_context(
        self,
        experiment: ChaosExperiment,
        fault: Any,
        *,
        run_id: Optional[str] = None,
        label_key: str = "app",
        managed_by: str = "chaosgen",
        ephemeral: str = "true",
    ) -> Dict[str, Any]:
        """Build the Jinja2 template context from experiment + fault."""
        # Prefer selector label value when present
        target_name = experiment.target.name
        if experiment.target.selector and label_key in experiment.target.selector:
            target_name = experiment.target.selector[label_key]

        ctx: Dict[str, Any] = {
            "experiment_name": experiment.name,
            "target_name": target_name,
            "namespace": experiment.target.namespace or "default",
            "duration": fault.duration,
            "duration_seconds": _parse_duration_seconds(fault.duration),
            "label_key": label_key,
            "managed_by": managed_by,
            "ephemeral": ephemeral,
            "run_id": run_id,
            "experiment_label": re.sub(r"[^a-z0-9-]", "-", experiment.name.lower())[:63],
        }

        if isinstance(fault, NetworkFaultSpec):
            ctx.update({
                "latency": fault.latency or "100ms",
                "latency_ms": int(re.sub(r"\D", "", fault.latency or "100") or "100"),
                "jitter": fault.jitter or "0ms",
                "jitter_ms": int(re.sub(r"\D", "", fault.jitter or "0") or "0"),
                "loss_percent": fault.loss_percentage or 10,
                "action": "loss" if fault.fault_type == FaultType.PACKET_LOSS else "delay",
                "interface": fault.interface,
            })
        elif isinstance(fault, ProcessFaultSpec):
            ctx.update({
                "force": "true" if fault.signal == "SIGKILL" else "false",
                "grace_period": fault.grace_period,
            })

        return ctx

    def _render_and_write(
        self, template_name: str, context: Dict[str, Any], prefix: str
    ) -> Path:
        """Render template and write to output directory."""
        try:
            template = self.jinja_env.get_template(template_name)
        except TemplateNotFound:
            raise FileNotFoundError(f"Template not found: {template_name}")

        rendered = template.render(**context)
        safe_name = re.sub(r"[^a-zA-Z0-9_-]", "-", context["experiment_name"])
        filename = f"{prefix}_{safe_name}.yaml"
        output_path = self.output_dir / filename
        output_path.write_text(rendered, encoding="utf-8")
        logger.info("Manifest written: %s", output_path)
        return output_path
