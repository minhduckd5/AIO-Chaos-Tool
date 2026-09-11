"""
Scenario Catalog View — browse and queue pre-built chaos scenarios.

Displays the built-in scenario catalog filtered by the detected (or manually
selected) architecture type. Users can search by keyword, filter by fault type,
and add scenarios directly to the HITL approval queue.

Promoted (Unknown→Known) entries support GUI CRUD: edit metadata / delete.
Builtin catalog entries remain read-only.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from chaosgen.advisor.promoted_store import get_default_store
from chaosgen.advisor.scenario_catalog import CatalogEntry, ScenarioCatalog
from chaosgen.config.profile_presets import default_environment_for
from chaosgen.schemas.discovery import ArchitectureType
from chaosgen.schemas.faults import FaultType

logger = logging.getLogger(__name__)


class ScenarioCatalogView(QWidget):
    """
    Pre-built scenario catalog browser.

    Signals:
        scenario_queued(experiment, metadata): catalog payload plus GUI-only
        architecture / suggested environment (does not mutate ChaosExperiment).
    """

    # --- START MODIFICATION ---
    # GUI-only metadata rides a second signal arg; ChaosExperiment schema stays unchanged.
    # --- END MODIFICATION ---
    scenario_queued = Signal(object, dict)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._catalog = ScenarioCatalog()
        self._store = get_default_store()
        self._entries: list[CatalogEntry] = []
        self._setup_ui()
        self._refresh()

    # ------------------------------------------------------------------
    # UI setup
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        title = QLabel("Scenario Catalog")
        title.setObjectName("viewTitle")
        root.addWidget(title)

        subtitle = QLabel(
            "Browse built-in and promoted chaos scenarios. "
            "Filter by architecture and fault type, then add to the approval queue. "
            "Promoted entries (Unknown→Known) can be edited or deleted here."
        )
        subtitle.setWordWrap(True)
        subtitle.setObjectName("viewSubtitle")
        root.addWidget(subtitle)

        # --- Filter row ---
        filter_row = QHBoxLayout()

        filter_row.addWidget(QLabel("Architecture:"))
        self._arch_combo = QComboBox()
        self._arch_combo.addItem("All", None)
        for arch in ArchitectureType:
            self._arch_combo.addItem(arch.value, arch)
        self._arch_combo.currentIndexChanged.connect(self._refresh)
        filter_row.addWidget(self._arch_combo)

        filter_row.addWidget(QLabel("Fault type:"))
        self._fault_combo = QComboBox()
        self._fault_combo.addItem("All", None)
        for ft in FaultType:
            self._fault_combo.addItem(ft.value, ft)
        self._fault_combo.currentIndexChanged.connect(self._refresh)
        filter_row.addWidget(self._fault_combo)

        filter_row.addWidget(QLabel("Source:"))
        self._source_combo = QComboBox()
        self._source_combo.addItem("All", None)
        self._source_combo.addItem("Builtin", "builtin")
        self._source_combo.addItem("Promoted", "promoted")
        self._source_combo.currentIndexChanged.connect(self._refresh)
        filter_row.addWidget(self._source_combo)

        filter_row.addWidget(QLabel("Search:"))
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("keyword…")
        self._search_input.textChanged.connect(self._refresh)
        filter_row.addWidget(self._search_input)

        filter_row.addStretch()
        root.addLayout(filter_row)

        # --- Splitter: list + detail ---
        splitter = QSplitter(Qt.Horizontal)

        # Left: scenario list
        list_panel = QWidget()
        list_layout = QVBoxLayout(list_panel)
        list_layout.setContentsMargins(0, 0, 0, 0)
        self._count_label = QLabel()
        list_layout.addWidget(self._count_label)
        self._list = QListWidget()
        self._list.currentRowChanged.connect(self._on_selection_changed)
        list_layout.addWidget(self._list)
        splitter.addWidget(list_panel)

        # Right: detail panel
        detail_panel = QGroupBox("Details")
        detail_layout = QVBoxLayout(detail_panel)

        self._detail_name = QLabel()
        self._detail_name.setObjectName("detailTitle")
        self._detail_name.setWordWrap(True)
        detail_layout.addWidget(self._detail_name)

        self._detail_badges = QLabel()
        detail_layout.addWidget(self._detail_badges)

        self._detail_desc = QTextEdit()
        self._detail_desc.setReadOnly(True)
        self._detail_desc.setMaximumHeight(100)
        detail_layout.addWidget(self._detail_desc)

        self._detail_experiment = QTextEdit()
        self._detail_experiment.setReadOnly(True)
        self._detail_experiment.setPlaceholderText("Experiment YAML will appear here.")
        detail_layout.addWidget(self._detail_experiment)

        action_row = QHBoxLayout()
        self._queue_btn = QPushButton("Add to Approval Queue")
        self._queue_btn.setObjectName("primaryButton")
        self._queue_btn.setEnabled(False)
        self._queue_btn.clicked.connect(self._on_queue)
        action_row.addWidget(self._queue_btn)

        # --- START MODIFICATION ---
        # Promoted-only CRUD (Create remains Triage → Promote).
        self._edit_btn = QPushButton("Edit promoted…")
        self._edit_btn.setEnabled(False)
        self._edit_btn.clicked.connect(self._on_edit_promoted)
        action_row.addWidget(self._edit_btn)

        self._delete_btn = QPushButton("Delete promoted")
        self._delete_btn.setObjectName("stopButton")
        self._delete_btn.setEnabled(False)
        self._delete_btn.clicked.connect(self._on_delete_promoted)
        action_row.addWidget(self._delete_btn)
        # --- END MODIFICATION ---

        action_row.addStretch()
        detail_layout.addLayout(action_row)

        splitter.addWidget(detail_panel)
        splitter.setSizes([320, 480])
        root.addWidget(splitter)

    # ------------------------------------------------------------------
    # Filter + refresh
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        arch: ArchitectureType | None = self._arch_combo.currentData()
        fault: FaultType | None = self._fault_combo.currentData()
        source = self._source_combo.currentData()
        query = self._search_input.text().strip()

        if query:
            entries = self._catalog.search(query)
        elif arch is not None:
            entries = self._catalog.get(arch, fault_type=fault)
        else:
            entries = self._catalog.iter_all()
            if fault is not None:
                entries = [e for e in entries if e.fault_type == fault]

        if source:
            entries = [e for e in entries if getattr(e, "source", "builtin") == source]

        self._entries = entries
        self._list.clear()

        for entry in entries:
            src = getattr(entry, "source", "builtin")
            marker = "★" if src == "promoted" else "·"
            item = QListWidgetItem(
                f"{marker} [{entry.architecture.value}] {entry.name}"
            )
            self._list.addItem(item)

        self._count_label.setText(f"{len(entries)} scenario(s)")
        self._queue_btn.setEnabled(False)
        self._edit_btn.setEnabled(False)
        self._delete_btn.setEnabled(False)
        self._detail_name.setText("")
        self._detail_badges.setText("")
        self._detail_desc.clear()
        self._detail_experiment.clear()

    def set_architecture(self, arch: ArchitectureType) -> None:
        """Pre-select architecture from DiscoveryView result."""
        for i in range(self._arch_combo.count()):
            if self._arch_combo.itemData(i) == arch:
                self._arch_combo.setCurrentIndex(i)
                break

    def _current_entry(self) -> CatalogEntry | None:
        row = self._list.currentRow()
        if row < 0 or row >= len(self._entries):
            return None
        return self._entries[row]

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_selection_changed(self, row: int) -> None:
        if row < 0 or row >= len(self._entries):
            self._queue_btn.setEnabled(False)
            self._edit_btn.setEnabled(False)
            self._delete_btn.setEnabled(False)
            return

        entry = self._entries[row]
        src = getattr(entry, "source", "builtin")
        self._detail_name.setText(entry.name)
        self._detail_badges.setText(
            f"Source: {src}  |  "
            f"Arch: {entry.architecture.value}  |  "
            f"Fault: {entry.fault_type.value}  |  "
            f"Tags: {', '.join(entry.tags) or '—'}"
        )
        self._detail_desc.setPlainText(entry.description)

        try:
            exp = entry.build()
            import yaml

            exp_dict = exp.model_dump(mode="json")
            self._detail_experiment.setPlainText(
                yaml.dump(exp_dict, default_flow_style=False)
            )
        except Exception as exc:
            self._detail_experiment.setPlainText(f"Error building experiment: {exc}")

        self._queue_btn.setEnabled(True)
        is_promoted = src == "promoted"
        self._edit_btn.setEnabled(is_promoted)
        self._delete_btn.setEnabled(is_promoted)

    def _on_queue(self) -> None:
        entry = self._current_entry()
        if entry is None:
            return

        try:
            exp = entry.build()
            # --- START MODIFICATION ---
            # Attach catalog classification for Experiments staging (env combo).
            # --- END MODIFICATION ---
            arch = getattr(entry, "architecture", ArchitectureType.MICROSERVICES)
            arch_value = getattr(arch, "value", str(arch or "microservices"))
            try:
                env_value = default_environment_for(arch).value
            except Exception:
                env_value = "kubernetes"
            metadata = {
                "architecture": arch_value,
                "environment": env_value,
            }
            self.scenario_queued.emit(exp, metadata)
            logger.info("Queued catalog scenario: %s", exp.name)
        except Exception as exc:
            logger.error("Failed to build catalog experiment: %s", exc)

    def _on_delete_promoted(self) -> None:
        entry = self._current_entry()
        if entry is None or getattr(entry, "source", "builtin") != "promoted":
            return
        confirm = QMessageBox.question(
            self,
            "Delete promoted scenario",
            f"Remove promoted scenario '{entry.name}' from the catalog store?\n"
            "(Built-in scenarios cannot be deleted.)",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        if self._store.delete_by_name(entry.name):
            logger.info("Deleted promoted catalog scenario: %s", entry.name)
            self._refresh()
        else:
            QMessageBox.warning(
                self,
                "Delete failed",
                f"No promoted record named '{entry.name}' was found on disk.",
            )

    def _on_edit_promoted(self) -> None:
        entry = self._current_entry()
        if entry is None or getattr(entry, "source", "builtin") != "promoted":
            return
        dialog = _EditPromotedDialog(entry, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        name, description, criteria_text = dialog.values()
        acceptance = None
        clear_acceptance = False
        if criteria_text.strip():
            try:
                import yaml

                parsed = yaml.safe_load(criteria_text) or {}
                if not isinstance(parsed, dict):
                    raise ValueError("acceptance criteria must be a YAML mapping")
                acceptance = parsed
            except Exception as exc:
                QMessageBox.warning(self, "Invalid criteria", str(exc))
                return
        else:
            clear_acceptance = True

        ok = self._store.update_by_name(
            entry.name,
            new_name=name,
            description=description,
            acceptance_criteria=acceptance,
            clear_acceptance=clear_acceptance,
        )
        if not ok:
            QMessageBox.warning(
                self,
                "Update failed",
                f"No promoted record named '{entry.name}' was found on disk.",
            )
            return
        logger.info("Updated promoted catalog scenario: %s -> %s", entry.name, name)
        self._refresh()


class _EditPromotedDialog(QDialog):
    """Edit name / description / acceptance criteria for a promoted entry."""

    def __init__(self, entry: CatalogEntry, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit promoted scenario")
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._name = QLineEdit(entry.name)
        form.addRow("Name:", self._name)

        self._description = QPlainTextEdit(entry.description or "")
        self._description.setMinimumHeight(80)
        form.addRow("Description:", self._description)
        layout.addLayout(form)

        layout.addWidget(QLabel("Acceptance criteria (YAML — leave empty to clear):"))
        self._criteria = QPlainTextEdit()
        existing = entry.acceptance_criteria or {}
        if existing:
            import yaml

            self._criteria.setPlainText(
                yaml.dump(existing, default_flow_style=False)
            )
        self._criteria.setPlaceholderText(
            "http_health: http://frontend:8080/health\n"
            "# or:\n# prometheus:\n#   url: http://10.50.1.220:9090\n"
            "#   query: up{job=\"frontend\"} == 1"
        )
        self._criteria.setMinimumHeight(120)
        layout.addWidget(self._criteria)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> tuple[str, str, str]:
        return (
            self._name.text().strip(),
            self._description.toPlainText().strip(),
            self._criteria.toPlainText(),
        )
