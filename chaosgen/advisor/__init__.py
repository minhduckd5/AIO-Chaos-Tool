"""
Chaos Advisor: End-to-end pipeline from telemetry analysis to
executable chaos experiment generation.

Pipeline: TelemetryCollector -> FeatureEngineer -> AnomalyDetector
          -> LLMAdvisor -> ScenarioGenerator -> ManifestWriter
"""

import logging
from typing import Dict, List, Optional

from chaosgen.ingestion.collector import TelemetryCollector
from chaosgen.ml.feature_engineering import FeatureEngineer
from chaosgen.ml.anomaly_detector import AnomalyDetector
from chaosgen.ml.llm_advisor import LLMAdvisor
from chaosgen.advisor.scenario_generator import ScenarioGenerator
from chaosgen.advisor.manifest_writer import ManifestWriter
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
    ):
        self.collector = collector
        self.feature_engineer = FeatureEngineer()
        self.anomaly_detector = AnomalyDetector()
        self.llm_advisor = LLMAdvisor(model=model_name, base_url=ollama_url)
        self.scenario_generator = ScenarioGenerator(
            safety_policy=safety_policy,
            confidence_threshold=confidence_threshold,
        )
        self.manifest_writer = ManifestWriter(output_dir=output_dir)
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
    ) -> AdvisorReport:
        """
        Full pipeline execution:
        1. Collect recent telemetry
        2. Extract features
        3. Detect anomalies and compress to summaries
        4. Generate fault hypotheses via LLM
        5. Convert to ChaosExperiments
        6. Write YAML manifests
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

        hypotheses = self.llm_advisor.interpret_anomalies(summaries)
        experiments = self.scenario_generator.generate(hypotheses, available_targets)

        manifest_paths: List[str] = []
        sci_scores = []
        if write_manifests:
            for exp in experiments:
                try:
                    litmus_path = self.manifest_writer.write_litmus(exp)
                    manifest_paths.append(str(litmus_path))
                except Exception as e:
                    logger.warning("Litmus manifest generation failed: %s", e)
                try:
                    cm_path = self.manifest_writer.write_chaosmesh(exp)
                    manifest_paths.append(str(cm_path))
                except Exception as e:
                    logger.warning("ChaosMesh manifest generation failed: %s", e)

                sci = ScenarioGenerator.compute_sci(exp)
                sci.compute()
                sci_scores.append(sci)

        dropped = len(hypotheses) - len(
            [h for h in hypotheses if h.confidence >= self.scenario_generator.confidence_threshold]
        )

        report = AdvisorReport(
            anomalies_found=len(clusters),
            clusters=clusters,
            summaries=summaries,
            hypotheses=hypotheses,
            dropped_hypotheses=dropped,
            generated_experiments=experiments,
            manifest_paths=manifest_paths,
            sci_scores=sci_scores,
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
