"""Toast/outcome honesty for HITL Approve and direct experiment run."""

from chaosgen.gui.controller import experiment_finish_payload


class TestExperimentFinishPayload:
    def test_pass_outcome(self):
        ok, msg = experiment_finish_payload(
            {"ran": True, "outcome": "PASS"}, None, label="Approved experiment"
        )
        assert ok is True
        assert msg == "Approved experiment PASS"

    def test_fail_outcome_not_hardcoded_success(self):
        ok, msg = experiment_finish_payload(
            {"ran": True, "outcome": "FAIL"}, "PASS", label="Approved experiment"
        )
        assert ok is False
        assert "FAIL" in msg
        assert "passed" not in msg.lower()

    def test_stale_pass_ignored_when_ran_false(self):
        ok, msg = experiment_finish_payload(
            {"ran": False, "reason": "no pending experiments"},
            "PASS",
            label="Approved experiment",
        )
        assert ok is False
        assert "no pending" in msg.lower()

    def test_fallback_to_last_outcome(self):
        ok, msg = experiment_finish_payload(
            None, "FAIL", label="Experiment"
        )
        assert ok is False
        assert msg == "Experiment FAIL"

    def test_unknown_when_no_outcome(self):
        ok, msg = experiment_finish_payload(None, None, label="Experiment")
        assert ok is False
        assert "UNKNOWN" in msg
