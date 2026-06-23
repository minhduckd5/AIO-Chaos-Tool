"""Tests for DeadMansSwitch (P6 coverage)."""
from __future__ import annotations

import threading
import time

from chaosgen.safety.monitor import DeadMansSwitch


class TestDeadMansSwitch:
    def test_triggers_on_unhealthy_check(self):
        triggered = threading.Event()

        def check():
            return False

        def trigger():
            triggered.set()

        dms = DeadMansSwitch(check, trigger, interval=1)
        dms.start()
        triggered.wait(timeout=3.0)
        dms.stop()
        assert triggered.is_set()

    def test_continues_while_healthy(self):
        triggered = threading.Event()
        calls = {"n": 0}

        def check():
            calls["n"] += 1
            return True

        dms = DeadMansSwitch(check, triggered.set, interval=1)
        dms.start()
        time.sleep(2.5)
        dms.stop()
        assert not triggered.is_set()
        assert calls["n"] >= 2

    def test_triggers_on_check_exception(self):
        triggered = threading.Event()

        def check():
            raise RuntimeError("probe failed")

        dms = DeadMansSwitch(check, triggered.set, interval=1)
        dms.start()
        triggered.wait(timeout=3.0)
        dms.stop()
        assert triggered.is_set()

    def test_start_idempotent(self):
        dms = DeadMansSwitch(lambda: True, lambda: None, interval=5)
        dms.start()
        thread = dms.thread
        dms.start()
        assert dms.thread is thread
        dms.stop()
