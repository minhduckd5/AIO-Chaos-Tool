"""
Entity-keyed long-format feature schema.

Locks the three properties the refactor exists for:
  1. the column set is fixed and window-independent (no service in column names),
  2. service attribution comes from the row index and follows an explicit
     per-signal rule (including the reserved ``_system`` entity),
  3. a model trained on the legacy wide layout is rejected, never zero-aligned.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from chaosgen.config.settings import FeatureSettings
from chaosgen.ingestion.analysis import analyze_dataset
from chaosgen.ingestion.collector import TelemetryCollector
from chaosgen.ml.anomaly_detector import AnomalyDetector
from chaosgen.ml.canonical_features import is_concrete_service
from chaosgen.ml.feature_engineering import (
    ENTITY_FEATURE_COLUMNS,
    ENTITY_KEYED_LAYOUT,
    ENTITY_SIGNALS,
    OTHER_SIGNAL,
    SIGNAL_ENTITY_POLICY,
    SYSTEM_ENTITY,
    SYSTEM_SCOPED_SIGNALS,
    WIDE_LAYOUT,
    FeatureEngineer,
    FeatureLayoutMismatchError,
    entity_key_for_signal,
    resolve_entity_key,
    resolve_signal_stem,
)
from chaosgen.schemas.telemetry import (
    LogEntry,
    LogStream,
    MetricSample,
    TelemetryDataset,
    TimeSeries,
)
from chaosgen.telemetry.pack_loader import PackQuery

BASE_TIME = 1700000000.0


def _samples(values, start=BASE_TIME, step=60.0):
    return [
        MetricSample(timestamp=start + i * step, value=float(v))
        for i, v in enumerate(values)
    ]


def _series(metric_name, labels, values):
    return TimeSeries(metric_name=metric_name, labels=labels, samples=_samples(values))


def _dataset(metrics, logs=None, n=60):
    return TelemetryDataset(
        metrics=metrics,
        logs=logs or [],
        collection_start=datetime.fromtimestamp(BASE_TIME, tz=timezone.utc),
        collection_end=datetime.fromtimestamp(BASE_TIME + n * 60, tz=timezone.utc),
    )


def _boutique_dataset(services=("frontend", "checkoutservice")):
    rng = np.random.default_rng(7)
    metrics = []
    for svc in services:
        metrics.append(
            _series("pack__request_rate", {"service": svc}, rng.normal(50, 3, 60))
        )
        errors = rng.normal(0.2, 0.05, 60)
        errors[40:50] = rng.normal(9.0, 0.5, 10)
        metrics.append(_series("pack__error_rate", {"service": svc}, errors))
    # Label-less cluster-wide series (KSM deployment counts, blackbox probe).
    metrics.append(_series("pack__replicas_available", {}, np.full(60, 2.0)))
    metrics.append(_series("pack__frontend_health", {}, np.full(60, 1.0)))
    return _dataset(metrics)


class TestSignalAndEntityRules:
    @pytest.mark.parametrize(
        "metric_name,expected",
        [
            ("pack__error_rate", "error_rate"),
            ("pack__replicas_available", "replicas_available"),
            ("pack__frontend_health", "frontend_health"),
            ("pack__log_error_rate", "log_error_rate"),
            ("pack__otel_request_rate", "otel_request_rate"),
            ("cpu_usage__sum(rate(container_cpu_usage_seconds_total[5m]))", "cpu_usage"),
            ("latency_p99__histogram_quantile(0.99, x)", "latency_p99"),
            ("infra__up__up", "infra_up"),
            ("export__cpu__container_cpu", OTHER_SIGNAL),
            ("json__rcaeval__checkout_latency", OTHER_SIGNAL),
            ("pack__brand_new_signal", OTHER_SIGNAL),
        ],
    )
    def test_signal_stem_resolution(self, metric_name, expected):
        assert resolve_signal_stem(metric_name) == expected

    @pytest.mark.parametrize(
        "labels,expected",
        [
            ({"service": "checkoutservice"}, "checkoutservice"),
            ({"service_name": "frontend"}, "frontend"),
            ({"app": "loki-agent"}, "loki-agent"),
            ({"pod": "checkout-7f9-abc"}, "checkout-7f9-abc"),
            # KSM deployment series: `deployment` is deliberately NOT a candidate.
            ({"deployment": "checkoutservice", "namespace": "boutique"}, SYSTEM_ENTITY),
            ({}, SYSTEM_ENTITY),
            ({"service": "   "}, SYSTEM_ENTITY),
        ],
    )
    def test_entity_key_resolution(self, labels, expected):
        assert resolve_entity_key(labels) == expected

    def test_signal_entity_policy_covers_every_declared_signal(self):
        assert set(SIGNAL_ENTITY_POLICY) == set(ENTITY_SIGNALS)
        assert SYSTEM_SCOPED_SIGNALS == frozenset(
            {"replicas_available", "frontend_health"}
        )

    @pytest.mark.parametrize("stem", ["replicas_available", "frontend_health"])
    def test_system_scoped_signals_ignore_service_labels(self, stem):
        """These two are always _system — even if a service label leaked through."""
        assert (
            entity_key_for_signal(stem, {"service": "checkoutservice"})
            == SYSTEM_ENTITY
        )
        assert entity_key_for_signal(stem, {"deployment": "checkoutservice"}) == SYSTEM_ENTITY
        assert entity_key_for_signal(stem, {}) == SYSTEM_ENTITY

    def test_label_scoped_signals_keep_service_identity(self):
        assert (
            entity_key_for_signal("error_rate", {"service": "checkoutservice"})
            == "checkoutservice"
        )
        assert entity_key_for_signal("error_rate", {"deployment": "checkoutservice"}) == SYSTEM_ENTITY

    def test_replicas_with_leaked_service_label_stay_on_system(self):
        dataset = _dataset(
            [
                _series(
                    "pack__replicas_available",
                    {"service": "checkoutservice"},
                    np.full(60, 2.0),
                ),
                _series(
                    "pack__request_rate",
                    {"service": "checkoutservice"},
                    np.full(60, 10.0),
                ),
            ]
        )
        features = FeatureEngineer(window_size=300, step=60).transform(dataset)
        system = features.xs(SYSTEM_ENTITY, level="service")
        checkout = features.xs("checkoutservice", level="service")
        assert system["replicas_available__mean"].abs().max() > 0
        assert float(checkout["replicas_available__mean"].abs().max()) == 0.0

    def test_frontend_health_with_leaked_service_label_stay_on_system(self):
        dataset = _dataset(
            [
                _series(
                    "pack__frontend_health",
                    {"service": "frontend"},
                    np.full(60, 1.0),
                ),
                _series(
                    "pack__request_rate",
                    {"service": "frontend"},
                    np.full(60, 10.0),
                ),
            ]
        )
        features = FeatureEngineer(window_size=300, step=60).transform(dataset)
        system = features.xs(SYSTEM_ENTITY, level="service")
        frontend = features.xs("frontend", level="service")
        assert system["frontend_health__mean"].abs().max() > 0
        assert float(frontend["frontend_health__mean"].abs().max()) == 0.0

    def test_replicas_available_lands_on_system_end_to_end(self):
        """KSM labels survive the collector but yield no service identity."""
        query = PackQuery(
            signal="replicas_available",
            source="prometheus",
            query='kube_deployment_status_replicas_available{namespace="boutique"}',
            pack_id="boutique",
            service_label="service_name",
            metric_name="pack__replicas_available",
        )
        raw = _series(
            "kube_deployment_status_replicas_available",
            {"deployment": "checkoutservice", "namespace": "boutique", "job": "ksm"},
            [2.0, 2.0, 2.0],
        )
        annotated = TelemetryCollector._annotate_pack_series([raw], query)
        assert annotated[0].labels == {}
        assert resolve_entity_key(annotated[0].labels) == SYSTEM_ENTITY

    def test_frontend_health_lands_on_system_end_to_end(self):
        query = PackQuery(
            signal="frontend_health",
            source="prometheus",
            query='min(probe_success{job="blackbox-frontend"})',
            pack_id="boutique",
            service_label="service_name",
            metric_name="pack__frontend_health",
        )
        raw = _series("probe_success", {}, [1.0, 1.0, 1.0])
        annotated = TelemetryCollector._annotate_pack_series([raw], query)
        assert resolve_entity_key(annotated[0].labels) == SYSTEM_ENTITY

    def test_system_entity_is_not_a_concrete_service(self):
        assert not is_concrete_service(SYSTEM_ENTITY)
        assert is_concrete_service("checkoutservice")


class TestFixedSchema:
    def test_columns_are_exactly_the_fixed_set(self):
        features = FeatureEngineer(window_size=300, step=60).transform(
            _boutique_dataset()
        )
        assert list(features.columns) == ENTITY_FEATURE_COLUMNS
        # `frontend_health` is a signal name; no *service* identity may appear.
        assert not any("checkoutservice" in c for c in features.columns)
        assert all(c.count("__") == 1 for c in features.columns)

    def test_index_is_timestamp_service_multiindex(self):
        features = FeatureEngineer(window_size=300, step=60).transform(
            _boutique_dataset()
        )
        assert list(features.index.names) == ["timestamp", "service"]
        entities = set(features.index.get_level_values("service"))
        assert {"frontend", "checkoutservice"} <= entities
        assert SYSTEM_ENTITY in entities

    def test_column_set_stable_when_a_service_disappears(self):
        """The core portability property: schema must not follow the service set."""
        fe = FeatureEngineer(window_size=300, step=60)
        full = fe.transform(_boutique_dataset(("frontend", "checkoutservice")))
        reduced = fe.transform(_boutique_dataset(("frontend",)))
        assert list(full.columns) == list(reduced.columns)
        assert "checkoutservice" not in set(
            reduced.index.get_level_values("service")
        )

    def test_unknown_metric_names_pool_into_other(self, caplog):
        fe = FeatureEngineer(window_size=300, step=60)
        dataset = _dataset(
            [_series("json__rcaeval__weird_metric", {"service": "cartservice"}, range(60))]
        )
        with caplog.at_level("WARNING"):
            features = fe.transform(dataset)
        assert list(features.columns) == ENTITY_FEATURE_COLUMNS
        cart = features.xs("cartservice", level="service")
        assert cart[f"{OTHER_SIGNAL}__mean"].abs().max() > 0
        assert "json__rcaeval__weird_metric" in caplog.text

    def test_layout_default_from_settings(self):
        fe = FeatureEngineer(settings=FeatureSettings())
        assert fe.layout == ENTITY_KEYED_LAYOUT


class TestAttributionFromIndex:
    def _fitted(self):
        features = FeatureEngineer(window_size=300, step=60).transform(
            _boutique_dataset()
        )
        detector = AnomalyDetector(contamination=0.15, n_clusters=3)
        detector.fit(features)
        return detector, features

    def test_clusters_attribute_to_concrete_service(self):
        detector, features = self._fitted()
        clusters, summaries = detector.detect_and_summarize(features)
        assert clusters
        concrete = [
            s.service_name for s in summaries if is_concrete_service(s.service_name)
        ]
        assert concrete, "entity-keyed attribution must yield a concrete service"

    def test_concrete_services_rank_before_system(self):
        entities = np.array([SYSTEM_ENTITY, "checkoutservice", SYSTEM_ENTITY], dtype=object)
        names = AnomalyDetector._extract_service_names([("error_rate__mean", 2.0)], entities)
        assert names[0] == "checkoutservice"
        assert names[-1] == SYSTEM_ENTITY

    def test_system_only_cluster_reports_system(self):
        entities = np.array([SYSTEM_ENTITY, SYSTEM_ENTITY], dtype=object)
        names = AnomalyDetector._extract_service_names([("replicas_available__roc", 1.0)], entities)
        assert names == [SYSTEM_ENTITY]

    def test_error_pattern_carries_signal_not_service(self):
        from chaosgen.schemas.scenarios import AnomalyCluster, AnomalySeverity

        cluster = AnomalyCluster(
            cluster_id=0,
            severity=AnomalySeverity.HIGH,
            affected_services=["checkoutservice"],
            dominant_features=[("error_ratio__mean", 3.2), ("request_rate__std", 1.1)],
            sample_count=12,
        )
        pattern = AnomalyDetector._derive_error_pattern(cluster)
        assert pattern is not None
        assert "error" in pattern.lower()
        assert "checkoutservice" not in pattern

    def test_timeline_collapses_to_timestamp_index(self):
        detector, features = self._fitted()
        detector.detect(features)
        timeline = detector.get_timeline_data(features)
        assert isinstance(timeline.index, pd.DatetimeIndex)
        assert not timeline.index.has_duplicates
        assert "service" in timeline.columns


class TestLayoutCompatibility:
    def _wide_model(self, tmp_path):
        wide = FeatureEngineer(window_size=300, step=60, layout=WIDE_LAYOUT).transform(
            _boutique_dataset()
        )
        detector = AnomalyDetector(contamination=0.15)
        detector.fit(wide)
        assert detector.feature_layout == WIDE_LAYOUT
        path = str(tmp_path / "wide.joblib")
        detector.save_model(path)
        return path

    def test_load_model_rejects_wide_layout(self, tmp_path):
        path = self._wide_model(tmp_path)
        with pytest.raises(FeatureLayoutMismatchError, match="wide"):
            AnomalyDetector().load_model(path, expected_layout=ENTITY_KEYED_LAYOUT)

    def test_legacy_model_without_layout_field_treated_as_wide(self, tmp_path):
        import joblib

        path = self._wide_model(tmp_path)
        state = joblib.load(path)
        state.pop("feature_layout")
        joblib.dump(state, path)
        with pytest.raises(FeatureLayoutMismatchError):
            AnomalyDetector().load_model(path, expected_layout=ENTITY_KEYED_LAYOUT)

    def test_align_refuses_cross_layout_zero_padding(self, tmp_path):
        path = self._wide_model(tmp_path)
        detector = AnomalyDetector().load_model(path)
        entity_features = FeatureEngineer(window_size=300, step=60).transform(
            _boutique_dataset()
        )
        with pytest.raises(FeatureLayoutMismatchError):
            detector.detect(entity_features)

    def test_analyze_dataset_refits_with_notice_instead_of_traceback(self, tmp_path):
        from chaosgen.config.settings import ChaosGenSettings

        path = self._wide_model(tmp_path)
        settings = ChaosGenSettings()
        settings.anomaly.contamination = 0.15
        notices: list[str] = []

        clusters, summaries, rows = analyze_dataset(
            _boutique_dataset(),
            model_path=path,
            settings=settings,
            notice_cb=notices.append,
        )

        assert rows > 0
        assert notices and "wide" in notices[0]
        assert "train-model --live" in notices[0]
        assert isinstance(clusters, list)
        assert len(clusters) == len(summaries)

    def test_entity_keyed_model_round_trips(self, tmp_path):
        features = FeatureEngineer(window_size=300, step=60).transform(
            _boutique_dataset()
        )
        detector = AnomalyDetector(contamination=0.15)
        detector.fit(features)
        path = str(tmp_path / "entity.joblib")
        detector.save_model(path)

        reloaded = AnomalyDetector().load_model(
            path, expected_layout=ENTITY_KEYED_LAYOUT
        )
        assert reloaded.feature_layout == ENTITY_KEYED_LAYOUT
        assert reloaded._feature_names == ENTITY_FEATURE_COLUMNS
        assert isinstance(reloaded.detect(features), list)
