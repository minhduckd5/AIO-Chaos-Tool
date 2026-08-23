"""Tests for trainable file noise filtering."""

from chaosgen.ingestion.trainable_filter import (
    classify_json_payload,
    is_noise_filename,
    is_noise_path,
)


def test_noise_readme_and_labels(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text("docs", encoding="utf-8")
    assert is_noise_filename(readme.name) is True

    label_csv = tmp_path / "ground_truth.csv"
    label_csv.write_text("x", encoding="utf-8")
    assert is_noise_filename(label_csv.name) is True


def test_classify_prometheus_json():
    payload = {
        "status": "success",
        "data": {
            "result": [
                {"metric": {"__name__": "cpu"}, "values": [[1, "0.5"]]},
            ]
        },
    }
    assert classify_json_payload(payload) == "prometheus"


def test_classify_loki_json():
    payload = {
        "result": [
            {"stream": {"app": "svc"}, "values": [["1", "err line"]]},
        ]
    }
    assert classify_json_payload(payload) == "loki"


def test_classify_k8s_noise():
    payload = {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": "x"}}
    assert classify_json_payload(payload) == "noise"
