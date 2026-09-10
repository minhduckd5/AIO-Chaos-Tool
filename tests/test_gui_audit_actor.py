"""A8 on the GUI surface: a missing operator name blocks the action, never crashes."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject  # noqa: E402

from chaosgen.config.settings import ChaosGenSettings  # noqa: E402
from chaosgen.gui.controller import AppController  # noqa: E402


class _FakeOrchestrator:
    def __init__(self):
        self.audit_context = {}

    def set_audit_context(self, **kwargs):
        self.audit_context.update(kwargs)


def _controller() -> AppController:
    """Controller without the heavy orchestrator/logging bootstrap."""
    controller = AppController.__new__(AppController)
    QObject.__init__(controller)
    controller.orchestrator = _FakeOrchestrator()
    controller._workers = []
    controller._actor_prompt = None
    return controller


@pytest.fixture
def unconfigured_settings(monkeypatch):
    monkeypatch.setattr(
        "chaosgen.config.settings.load_settings",
        lambda path=None: ChaosGenSettings(),
    )
    saved: list[str] = []
    monkeypatch.setattr(
        "chaosgen.config.settings.save_settings",
        lambda settings, path=None: saved.append(settings.operator_name),
    )
    return saved


def test_cancelled_prompt_blocks_action_without_exception(unconfigured_settings):
    controller = _controller()
    messages: list[str] = []
    controller.audit_actor_required.connect(messages.append)
    controller.set_actor_prompt(lambda: None)

    assert controller.ensure_audit_actor() is False
    assert messages and "operator" in messages[0].lower()
    assert controller.orchestrator.audit_context == {}


def test_no_prompt_registered_still_blocks(unconfigured_settings):
    controller = _controller()
    messages: list[str] = []
    controller.audit_actor_required.connect(messages.append)

    assert controller.ensure_audit_actor() is False
    assert messages


def test_entered_name_binds_and_persists(unconfigured_settings):
    controller = _controller()
    controller.set_actor_prompt(lambda: "sre-anh")

    assert controller.ensure_audit_actor() is True
    assert controller.orchestrator.audit_context["actor"] == "sre-anh"
    assert unconfigured_settings == ["sre-anh"]


def test_unexpected_error_is_reported_not_raised(monkeypatch):
    controller = _controller()
    messages: list[str] = []
    controller.audit_actor_required.connect(messages.append)
    monkeypatch.setattr(
        "chaosgen.storage.audit.resolve_actor",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("disk on fire")),
    )

    assert controller.ensure_audit_actor() is False
    assert "disk on fire" in messages[0]
