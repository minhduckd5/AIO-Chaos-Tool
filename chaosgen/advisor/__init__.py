"""
Chaos Advisor: End-to-end pipeline from telemetry analysis to
executable chaos experiment generation.

Pipeline: TelemetryCollector -> FeatureEngineer -> AnomalyDetector
          -> run_advisor_pipeline (gatekeeper -> describer -> LLM -> generate)
"""

import logging
from typing import Dict, List, Optional

from chaosgen.advisor.manifest_writer import ManifestWriter
from chaosgen.advisor.pipeline import run_advisor_pipeline
from chaosgen.config.settings import ChaosGenSettings, load_settings
from chaosgen.ingestion.collector import TelemetryCollector
from chaosgen.ml.anomaly_detector import AnomalyDetector
from chaosgen.ml.feature_engineering import FeatureEngineer
from chaosgen.schemas.faults import TargetSpec
from chaosgen.schemas.scenarios import AdvisorReport
from chaosgen.safety.governance import SafetyPolicy

logger = logging.getLogger(__name__)


class ChaosAdvisor:
    """
    Facade composing the full AI-assisted chaos recommendation pipeline.
    """

    def __init__(
        self,
        collector: TelemetryCollector,
        model_name: str = "llama3.2:3b",
        ollama_url: str = "http://100.65.164.88:11434",
        safety_policy: Optional[SafetyPolicy] = None,
        confidence_threshold: float = 0.6,
        output_dir: str = "./generated_scenarios",
        settings: Optional[ChaosGenSettings] = None,
        skip_gatekeeper: bool = False,
    ):
        self.collector = collector
        self.feature_engineer = FeatureEngineer()
        self.anomaly_detector = AnomalyDetector()
        self.settings = settings or ChaosGenSettings(
            llm_provider="ollama",
            llm_model=model_name,
        )
        if self.settings.llm_model is None:
            self.settings.llm_model = model_name
        self.confidence_threshold = confidence_threshold
        self.output_dir = output_dir
        self.skip_gatekeeper = skip_gatekeeper
        self._is_trained = False

    def train_baseline(self, duration_hours: int = 168) -> None:
        """
        Collect baseline telemetry and train the anomaly detection model.
        Default 168 hours (7 days).
        """
        logger.info("Collecting baseline telemetry for %d hours...", duration_hours)
        dataset = self.collector.collect_baseline(duration_hours=duration_hours)
        features = self.feature_engineer.transform(dataset)

        if features.empty:
            raise RuntimeError("Feature extraction produced empty matrix. Check data sources.")

        self.anomaly_detector.fit(features)
        self._is_trained = True
        logger.info("Baseline training complete.")

    def analyze_and_recommend(
        self,
        lookback_hours: int = 24,
        available_targets: Optional[Dict[str, TargetSpec]] = None,
        write_manifests: bool = True,
        context=None,
    ) -> AdvisorReport:
        """
        Full pipeline execution via shared ``run_advisor_pipeline``.
        """
        if not self._is_trained:
            raise RuntimeError("Must call train_baseline() before analyze_and_recommend().")

        dataset = self.collector.collect_baseline(duration_hours=lookback_hours)
        features = self.feature_engineer.transform(dataset)

        if features.empty:
            logger.warning("No features extracted from recent telemetry.")
            return AdvisorReport(anomalies_found=0)

        clusters, summaries = self.anomaly_detector.detect_and_summarize(features)

        if not clusters:
            logger.info("No anomalies detected in the last %d hours.", lookback_hours)
            return AdvisorReport(anomalies_found=0)

        report = run_advisor_pipeline(
            clusters,
            summaries,
            settings=self.settings,
            lookback_hours=float(lookback_hours),
            context=context,
            skip_gatekeeper=self.skip_gatekeeper,
            generate_chaos=True,
            write_manifests=write_manifests,
            output_dir=self.output_dir,
            confidence_threshold=self.confidence_threshold,
        )

        logger.info(
            "Advisor report: %d anomalies, %d hypotheses, %d experiments, %d manifests",
            report.anomalies_found,
            len(report.hypotheses),
            len(report.generated_experiments),
            len(report.manifest_paths),
        )
        return report

    def save_model(self, path: str) -> None:
        self.anomaly_detector.save_model(path)

    def load_model(self, path: str) -> None:
        self.anomaly_detector.load_model(path)
        self._is_trained = True
