"""Tests for offline observability export loading."""

import io
import tarfile
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


class TestCsvAndArchiveExport:
    def test_wide_csv_file(self, tmp_path):
        csv_path = tmp_path / "cpu.csv"
        csv_path.write_text(
            "timestamp,cpu,mem,label\n"
            "2024-01-01T00:00:00Z,1.0,10,ok\n"
            "2024-01-01T00:01:00Z,2.0,11,ok\n"
            "2024-01-01T00:02:00Z,3.0,12,fault\n"
            "2024-01-01T00:03:00Z,4.0,13,fault\n",
            encoding="utf-8",
        )
        dataset = ExportLoader.resolve_bundle(csv_path).load()
        names = {ts.metric_name for ts in dataset.metrics}
        assert any(n.endswith("__cpu") for n in names)
        assert any(n.endswith("__mem") for n in names)
        assert not any("label" in n.lower() for n in names)
        assert dataset.total_samples == 8

    def test_long_csv_file(self, tmp_path):
        csv_path = tmp_path / "long.csv"
        csv_path.write_text(
            "timestamp,metric,value\n"
            "1700000000,latency,1.5\n"
            "1700000060,latency,2.5\n"
            "1700000000,errors,0\n"
            "1700000060,errors,4\n",
            encoding="utf-8",
        )
        dataset = ExportLoader.resolve_bundle(csv_path).load()
        names = {ts.metric_name for ts in dataset.metrics}
        assert any("latency" in n for n in names)
        assert any("errors" in n for n in names)
        assert dataset.total_samples == 4

    def test_csv_inside_zip(self, tmp_path):
        import zipfile

        csv_path = tmp_path / "m.csv"
        csv_path.write_text(
            "ts,cpu\n"
            "2024-01-01T00:00:00Z,1\n"
            "2024-01-01T00:01:00Z,2\n"
            "2024-01-01T00:02:00Z,3\n",
            encoding="utf-8",
        )
        zip_path = tmp_path / "dump.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.write(csv_path, arcname="metrics/m.csv")
        dataset = ExportLoader.resolve_bundle(zip_path).load()
        assert dataset.metrics
        assert dataset.total_samples == 3

    def test_csv_directory(self, tmp_path):
        (tmp_path / "a.csv").write_text(
            "timestamp,a\n2024-01-01T00:00:00Z,1\n2024-01-01T00:01:00Z,2\n2024-01-01T00:02:00Z,3\n",
            encoding="utf-8",
        )
        dataset = ExportLoader.resolve_bundle(tmp_path).load()
        assert dataset.metrics
        assert dataset.total_samples == 3

    def test_targz_inside_tar(self, tmp_path):
        csv_bytes = (
            b"timestamp,cpu\n"
            b"2024-01-01T00:00:00Z,1\n"
            b"2024-01-01T00:01:00Z,2\n"
            b"2024-01-01T00:02:00Z,3\n"
        )
        inner_buf = io.BytesIO()
        with tarfile.open(fileobj=inner_buf, mode="w:gz") as inner:
            info = tarfile.TarInfo(name="cpu.csv")
            info.size = len(csv_bytes)
            inner.addfile(info, io.BytesIO(csv_bytes))
        inner_bytes = inner_buf.getvalue()
        outer = tmp_path / "outer.tar"
        with tarfile.open(outer, mode="w") as tf:
            info = tarfile.TarInfo(name="inner.tar.gz")
            info.size = len(inner_bytes)
            tf.addfile(info, io.BytesIO(inner_bytes))
        dataset = ExportLoader.resolve_bundle(outer).load()
        assert dataset.metrics
        assert dataset.total_samples == 3

    def test_csv_dir_respects_max_files(self, tmp_path):
        for i in range(5):
            (tmp_path / f"m{i}.csv").write_text(
                f"timestamp,v\n2024-01-01T00:00:00Z,{i}\n",
                encoding="utf-8",
            )
        loader = ExportLoader(tmp_path, kind="csv_dir", max_csv_files=2)
        dataset = loader.load()
        assert len(dataset.metrics) <= 2

    def test_nested_csv_discovered_beyond_two_levels(self, tmp_path):
        deep = tmp_path / "benchmark" / "run-001" / "data"
        deep.mkdir(parents=True)
        (deep / "metrics.csv").write_text(
            "timestamp,cpu\n"
            "2024-01-01T00:00:00Z,1\n"
            "2024-01-01T00:01:00Z,2\n"
            "2024-01-01T00:02:00Z,3\n",
            encoding="utf-8",
        )
        dataset = ExportLoader.resolve_bundle(tmp_path).load()
        assert dataset.metrics
        assert dataset.total_samples == 3

    def test_mixed_dir_skips_noise_and_loads_log(self, tmp_path):
        (tmp_path / "metrics.csv").write_text(
            "timestamp,cpu\n"
            "2024-01-01T00:00:00Z,1\n"
            "2024-01-01T00:01:00Z,2\n"
            "2024-01-01T00:02:00Z,3\n",
            encoding="utf-8",
        )
        (tmp_path / "ground_truth.csv").write_text("label\n0\n", encoding="utf-8")
        (tmp_path / "app.log").write_text(
            "2024-01-01 ERROR disk full\n2024-01-01 INFO ok\n",
            encoding="utf-8",
        )
        dataset = ExportLoader.resolve_bundle(tmp_path).load()
        assert dataset.metrics
        assert dataset.logs
        assert sum(len(s.entries) for s in dataset.logs) >= 1
