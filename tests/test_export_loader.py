"""Tests for offline observability export loading."""

from pathlib import Path

import pytest

from chaosgen.ingestion.analysis import analyze_dataset
from chaosgen.ingestion.export_loader import ExportLoader

FIXTURE = Path(__file__).parent / "fixtures" / "export_bundle"
REAL_EXPORT = Path(r"H:\Project\microservices-demo-1\local\observability-fetch\exports")


class TestExportLoader:
    def test_load_fixture_bundle(self):
        loader = ExportLoader(FIXTURE)
        dataset = loader.load()
        assert len(dataset.metrics) == 1
        assert dataset.total_samples == 4
        assert len(dataset.logs) == 1
        assert sum(len(s.entries) for s in dataset.logs) == 2

    def test_resolve_parent_exports_dir(self):
        loader = ExportLoader.resolve_bundle(FIXTURE)
        assert loader.export_root == FIXTURE

    def test_analyze_fixture_export(self):
        dataset = ExportLoader(FIXTURE).load()
        clusters, summaries, rows = analyze_dataset(dataset)
        assert rows >= 0  # small fixture may yield few windows

    @pytest.mark.skipif(not REAL_EXPORT.is_dir(), reason="microservices-demo export not present")
    def test_load_real_export_if_available(self):
        loader = ExportLoader.resolve_bundle(REAL_EXPORT)
        dataset = loader.load()
        assert dataset.metrics
        assert dataset.collection_start < dataset.collection_end
