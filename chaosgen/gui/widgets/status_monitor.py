from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QProgressBar, QTextEdit, 
    QGroupBox, QHBoxLayout, QPushButton
)
from PySide6.QtCore import Qt, Signal

class StatusMonitor(QWidget):
    stop_requested = Signal()

    def __init__(self):
        super().__init__()
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()
        
        # Header
        self.status_label = QLabel("Status: IDLE")
        self.status_label.setObjectName("headerLabel")
        layout.addWidget(self.status_label)

        # Progress
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        # Log Console
        log_group = QGroupBox("Execution Log")
        log_layout = QVBoxLayout()
        self.log_console = QTextEdit()
        self.log_console.setReadOnly(True)
        log_layout.addWidget(self.log_console)
        log_group.setLayout(log_layout)
        layout.addWidget(log_group)

        # Controls
        controls_layout = QHBoxLayout()
        self.stop_btn = QPushButton("EMERGENCY STOP")
        self.stop_btn.setObjectName("stopButton")
        self.stop_btn.clicked.connect(self.stop_requested.emit)
        controls_layout.addWidget(self.stop_btn)
        
        layout.addLayout(controls_layout)
        self.setLayout(layout)

    def log(self, message: str):
        self.log_console.append(message)
        # Scroll to bottom
        cursor = self.log_console.textCursor()
        cursor.movePosition(cursor.End)
        self.log_console.setTextCursor(cursor)

    def set_status(self, status: str, progress: int = 0):
        self.status_label.setText(f"Status: {status.upper()}")
        self.progress_bar.setValue(progress)
