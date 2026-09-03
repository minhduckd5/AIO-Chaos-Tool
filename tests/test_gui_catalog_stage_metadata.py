"""GUI-only catalog metadata: architecture/env ride the queue signal."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from chaosgen.config.profile_presets import default_environment_for
from chaosgen.gui.controller import AppController
from chaosgen.gui.views.experiments_view import ExperimentsView
from chaosgen.gui.views.scenario_catalog_view import ScenarioCatalogView
from chaosgen.schemas.discovery import ArchitectureType
from chaosgen.schemas.faults import (
    ChaosExperiment,
    FaultType,
    ProcessFaultSpec,
    TargetSpec,
    TargetType,
)


def _app() -> QApplication:
    existing = QApplication.instance()
    if existing is not None:
        return existing
    return QApplication([])


def _experiment(name: str, target: str) -> ChaosExperiment:
    return ChaosExperiment(
        name=name,
        description="stage metadata test",
        target=TargetSpec(type=TargetType.SERVICE, name=target, namespace="default"),
        faults=[ProcessFaultSpec(fault_type=FaultType.PROCESS_KILL, duration="30s")],
    )


def test_event_driven_and_client_server_stage_to_docker_compose():
    _app()
    view = ExperimentsView(AppController())
    kafka_exp = _experiment("broker-lag", "kafka")
    view.load_staged_scenario(
        kafka_exp,
        metadata={
            "architecture": ArchitectureType.EVENT_DRIVEN.value,
            "environment": default_environment_for(ArchitectureType.EVENT_DRIVEN).value,
        },
    )
    assert view._selected_environment() == "docker_compose"

    cs_exp = _experiment("client-timeout", "client")
    view.load_staged_scenario(
        cs_exp,
        metadata={
            "architecture": ArchitectureType.CLIENT_SERVER.value,
            "environment": default_environment_for(ArchitectureType.CLIENT_SERVER).value,
        },
    )
    assert view._selected_environment() == "docker_compose"


def test_microservices_stage_to_kubernetes_serverless_to_serverless():
    _app()
    view = ExperimentsView(AppController())
    view.load_staged_scenario(
        _experiment("upstream-timeout-cascade", "downstream-service"),
        metadata={
            "architecture": ArchitectureType.MICROSERVICES.value,
            "environment": default_environment_for(ArchitectureType.MICROSERVICES).value,
        },
    )
    assert view._selected_environment() == "kubernetes"

    view.load_staged_scenario(
        _experiment("cold-start", "function-runtime"),
        metadata={
            "architecture": ArchitectureType.SERVERLESS.value,
            "environment": default_environment_for(ArchitectureType.SERVERLESS).value,
        },
    )
    assert view._selected_environment() == "serverless"
    assert view._dry_run.isChecked()
    assert not view._dry_run.isEnabled()


def test_p1_env_locks_dry_run_and_switches_panel():
    _app()
    view = ExperimentsView(AppController())
    for env_key, stack_idx in (
        ("bare_metal", 2),
        ("cloud_vm", 2),
        ("serverless", 3),
    ):
        idx = view._env_combo.findData(env_key)
        assert idx >= 0
        view._env_combo.setCurrentIndex(idx)
        assert view._selected_environment() == env_key
        assert view._conn_stack.currentIndex() == stack_idx
        assert view._dry_run.isChecked()
        assert not view._dry_run.isEnabled()
        assert "P1 Dry-run" in view._conn_status.text()


def test_catalog_queue_emits_experiment_and_metadata():
    _app()
    catalog = ScenarioCatalogView()
    captured: list[tuple] = []
    catalog.scenario_queued.connect(lambda exp, meta: captured.append((exp, meta)))

    event_idx = next(
        i
        for i, entry in enumerate(catalog._entries)
        if entry.architecture == ArchitectureType.EVENT_DRIVEN
    )
    catalog._list.setCurrentRow(event_idx)
    catalog._on_queue()

    assert len(captured) == 1
    exp, meta = captured[0]
    assert exp.name
    assert meta["architecture"] == ArchitectureType.EVENT_DRIVEN.value
    assert meta["environment"] == default_environment_for(ArchitectureType.EVENT_DRIVEN).value
