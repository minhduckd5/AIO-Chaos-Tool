"""Shared pytest fixtures.

The audit trail defaults to the user's config directory; tests must never
append to a developer's real evidence file, so the path is redirected for the
whole session.
"""

import pytest

from chaosgen.storage.audit import AUDIT_PATH_ENV


@pytest.fixture(autouse=True)
def _isolate_audit_log(tmp_path, monkeypatch):
    monkeypatch.setenv(AUDIT_PATH_ENV, str(tmp_path / "audit_events.jsonl"))
    yield
