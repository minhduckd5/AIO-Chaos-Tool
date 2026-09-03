"""
AppController -- central controller decoupling GUI views from ChaosOrchestrator.

All blocking I/O is dispatched to QThread workers; results are delivered
via Qt signals so the UI thread never freezes.
"""

import logging
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QObject, QThread, Signal, Slot


class WorkerSignals(QObject):
    """Signals emitted by AsyncWorker."""
    finished = Signal(object)
    error = Signal(str)
    progress = Signal(str)


class AsyncWorker(QThread):
    """Generic background worker for any blocking callable."""

    def __init__(self, fn: Callable, *args: Any, **kwargs: Any):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    def run(self):
        try:
            result = self.fn(*self.args, **self.kwargs)
            self.signals.finished.emit(result)
        except Exception as exc:
            self.signals.error.emit(str(exc))


class AppController(QObject):
    """
    Owns the ChaosOrchestrator instance and provides a signal-based
    interface for the GUI.  The same orchestrator methods are callable
    directly from the CLI, preserving parity.
    """

    # State / lifecycle signals
    state_changed = Signal(str)
    modules_changed = Signal(list)
    log_message = Signal(str)

    # Experiment signals
    experiment_started = Signal(str)
    experiment_finished = Signal(bool, str)

    # Advisor / telemetry signals
    advisor_started = Signal()
    advisor_finished = Signal(object)
    advisor_error = Signal(str)
    telemetry_check_finished = Signal(dict)
    telemetry_progress = Signal(str)
    llm_check_finished = Signal(bool, str)

    # Status refresh
    status_refreshed = Signal(dict)

    def __init__(self):
        super().__init__()
        # Deferred import to keep gui package light when orchestrator is heavy
        from chaosgen.orchestrator import ChaosOrchestrator
        self.orchestrator = ChaosOrchestrator()

        self._workers: List[QThread] = []

        self._setup_logging()

    # ------------------------------------------------------------------
    # Logging bridge
    # ------------------------------------------------------------------

    def _setup_logging(self):
        """Route orchestrator log records into the log_message signal."""
        handler = _SignalLogHandler(self.log_message)
        handler.setFormatter(
            logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s", datefmt="%H:%M:%S")
        )
        for name in ("ChaosOrchestrator", "DeadMansSwitch", "ChaosAdvisor"):
            logger = logging.getLogger(name)
            logger.addHandler(handler)
            logger.setLevel(logging.DEBUG)
        logging.getLogger().addHandler(handler)

    # ------------------------------------------------------------------
    # Synchronous accessors (safe on UI thread -- no I/O)
    # ------------------------------------------------------------------

    @property
    def state(self) -> str:
        return self.orchestrator.state

    def list_modules(self) -> List[str]:
        return self.orchestrator.list_modules()

    def get_module_actions(self, module: str) -> List[str]:
        return self.orchestrator.get_module_actions(module)

    def get_pending_experiments(self):
        return self.orchestrator.get_pending_experiments()

    # ------------------------------------------------------------------
    # Async dispatchers (blocking I/O pushed to QThread)
    # ------------------------------------------------------------------

    def refresh_status_async(self):
        worker = AsyncWorker(self.orchestrator.get_all_status)
        worker.signals.finished.connect(self._on_status_refreshed)
        self._spawn(worker)

    def execute_action_async(self, module: str, action: str, params: Dict[str, Any]):
        worker = AsyncWorker(self.orchestrator.execute_action, module, action, params)
        worker.signals.finished.connect(
            lambda result: self.log_message.emit(f"Action result: {result}")
        )
        worker.signals.error.connect(
            lambda err: self.log_message.emit(f"Action error: {err}")
        )
        self._spawn(worker)

    def run_experiment_async(self, experiment):
        self.experiment_started.emit(experiment.name)
        worker = AsyncWorker(self.orchestrator.run_experiment, experiment)
        worker.signals.finished.connect(
            lambda _: self.experiment_finished.emit(True, "Experiment completed")
        )
        worker.signals.error.connect(
            lambda err: self.experiment_finished.emit(False, err)
        )
        self._spawn(worker)

    # --- START MODIFICATION ---
    # Phase B: multi-service suite runner for cascading blast demos
    def run_experiment_suite_async(self, experiments, delay_seconds: float = 0.0):
        label = f"suite×{len(experiments)}"
        self.experiment_started.emit(label)
        worker = AsyncWorker(
            self.orchestrator.run_experiment_suite,
            experiments,
            delay_seconds=delay_seconds,
        )
        worker.signals.finished.connect(
            lambda result: self.experiment_finished.emit(
                True,
                f"Suite completed: {(result or {}).get('outcome', 'done')}",
            )
        )
        worker.signals.error.connect(
            lambda err: self.experiment_finished.emit(False, err)
        )
        self._spawn(worker)

    def run_ctk_experiment_async(self, **kwargs):
        title = kwargs.get("title") or "ctk-experiment"
        self.experiment_started.emit(title)
        worker = AsyncWorker(self.orchestrator.run_ctk_experiment, **kwargs)
        worker.signals.finished.connect(
            lambda result: self.experiment_finished.emit(
                bool((result or {}).get("success")) or (result or {}).get("verdict") == "pass",
                (result or {}).get("message")
                or (result or {}).get("error")
                or f"CTK finished — verdict={(result or {}).get('verdict') or 'unknown'}",
            )
        )
        worker.signals.error.connect(
            lambda err: self.experiment_finished.emit(False, err)
        )
        self._spawn(worker)
    # --- END MODIFICATION ---

    def run_advisor_async(self, advisor, lookback_hours: int):
        self.advisor_started.emit()
        worker = AsyncWorker(advisor.analyze_and_recommend, lookback_hours=lookback_hours)
        worker.signals.finished.connect(self._on_advisor_done)
        worker.signals.error.connect(self.advisor_error.emit)
        self._spawn(worker)

    def check_telemetry_async(self, prom_url: str, loki_url: str):
        from chaosgen.gui.analysis_pipeline import check_telemetry_endpoints

        worker = AsyncWorker(check_telemetry_endpoints, prom_url, loki_url)
        worker.signals.finished.connect(self.telemetry_check_finished.emit)
        worker.signals.error.connect(self.advisor_error.emit)
        self._spawn(worker)

    def test_llm_async(self, provider: str, model: str | None):
        from chaosgen.gui.analysis_pipeline import test_llm_provider

        worker = AsyncWorker(test_llm_provider, provider, model)
        worker.signals.finished.connect(
            lambda result: self.llm_check_finished.emit(result[0], result[1])
        )
        worker.signals.error.connect(
            lambda err: self.llm_check_finished.emit(False, err)
        )
        self._spawn(worker)

    def run_telemetry_analysis_async(self, request):
        from chaosgen.gui.analysis_pipeline import run_analysis

        self.advisor_started.emit()
        self.telemetry_progress.emit("Collecting telemetry...")
        worker = AsyncWorker(run_analysis, request)
        worker.signals.finished.connect(self._on_telemetry_analysis_done)
        worker.signals.error.connect(self.advisor_error.emit)
        self._spawn(worker)

    def submit_catalog_experiment(self, experiment):
        from chaosgen.schemas.scenarios import AdvisorReport

        report = AdvisorReport(
            anomalies_found=0,
            generated_experiments=[experiment],
        )
        self.orchestrator.run_ai_experiment(report)
        self.state_changed.emit(self.state)

    def approve_and_run(self, index: int):
        worker = AsyncWorker(self.orchestrator.approve_and_run, index)
        worker.signals.finished.connect(
            lambda _: self.experiment_finished.emit(True, "Approved experiment completed")
        )
        worker.signals.error.connect(
            lambda err: self.experiment_finished.emit(False, err)
        )
        self._spawn(worker)

    def reject_all(self):
        self.orchestrator.reject_all()
        self.state_changed.emit(self.state)

    def trigger_rollback(self):
        # --- START MODIFICATION ---
        # HALT: CTK subprocess kill + cluster cleanup before legacy rollback
        # --- END MODIFICATION ---
        self.orchestrator.halt_active_experiment()
        self.state_changed.emit(self.state)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _spawn(self, worker: QThread):
        self._workers.append(worker)
        worker.finished.connect(lambda: self._cleanup_worker(worker))
        worker.start()

    def _cleanup_worker(self, worker: QThread):
        if worker in self._workers:
            self._workers.remove(worker)

    @Slot(object)
    def _on_status_refreshed(self, data):
        self.status_refreshed.emit(data)

    @Slot(object)
    def _on_advisor_done(self, report):
        self.orchestrator.run_ai_experiment(report)
        self.advisor_finished.emit(report)

    @Slot(object)
    def _on_telemetry_analysis_done(self, result):
        if result.report.generated_experiments:
            self.orchestrator.run_ai_experiment(result.report)
        self.advisor_finished.emit(result)


class _SignalLogHandler(logging.Handler):
    """Logging handler that emits records via a Qt Signal."""

    def __init__(self, signal: Signal):
        super().__init__()
        self._signal = signal

    def emit(self, record):
        try:
            self._signal.emit(self.format(record))
        except Exception:
            self.handleError(record)
