"""P6 deliverable smoke tests — demo scripts and criteria on disk."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from chaosgen.advisor.catalog_promoter import validate_acceptance_criteria

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


class TestP6DemoArtifacts:
    def test_demo_criteria_yaml_exists_and_validates(self):
        path = EXAMPLES / "demo-criteria.yaml"
        assert path.is_file()
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        errors = validate_acceptance_criteria(data)
        assert errors == []

    def test_demo_e2e_scripts_exist(self):
        assert (EXAMPLES / "demo-e2e.sh").is_file()
        assert (EXAMPLES / "demo-e2e-resilience.sh").is_file()
        assert (EXAMPLES / "demo-e2e.ps1").is_file()

    def test_e2e_doc_exists(self):
        assert (ROOT / "docs" / "e2e-demo.md").is_file()
        assert (ROOT / "docs" / "load-test-report.md").is_file()

    def test_happy_script_mentions_p4_promote_flags(self):
        text = (EXAMPLES / "demo-e2e.sh").read_text(encoding="utf-8")
        assert "--from-report" in text
        assert "--criteria-file" in text
        assert "--incident-id 0" not in text or "--from-report" in text

    def test_resilience_script_expects_promote_failure(self):
        text = (EXAMPLES / "demo-e2e-resilience.sh").read_text(encoding="utf-8")
        assert "demo-report-resilience" in text
        assert "Promote correctly rejected" in text
