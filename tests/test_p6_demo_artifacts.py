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

    def test_e2e_docs_local_kb_policy(self):
        # MODIFIED: docs/ is a local knowledge base (gitignored). CI checkout
        # must not require operator/thesis markdown; when absent, README must
        # document the publish-later policy.
        docs_e2e = ROOT / "docs" / "e2e-demo.md"
        docs_load = ROOT / "docs" / "load-test-report.md"
        if docs_e2e.is_file() and docs_load.is_file():
            assert docs_e2e.stat().st_size > 0
            assert docs_load.stat().st_size > 0
            return
        readme = (ROOT / "README.md").read_text(encoding="utf-8").lower()
        assert "docs/" in readme
        assert "knowledge base" in readme or "gitignored" in readme or "not published" in readme

    def test_happy_script_mentions_p4_promote_flags(self):
        text = (EXAMPLES / "demo-e2e.sh").read_text(encoding="utf-8")
        assert "--from-report" in text
        assert "--criteria-file" in text
        assert "--incident-id 0" not in text or "--from-report" in text

    def test_resilience_script_expects_promote_failure(self):
        text = (EXAMPLES / "demo-e2e-resilience.sh").read_text(encoding="utf-8")
        assert "demo-report-resilience" in text
        assert "Promote correctly rejected" in text
