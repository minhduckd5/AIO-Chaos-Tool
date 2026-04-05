from PySide6.QtWidgets import QListWidget, QVBoxLayout, QLabel, QWidget

class ModuleList(QWidget):
    def __init__(self, orchestrator):
        super().__init__()
        self.orchestrator = orchestrator
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()
        
        header = QLabel("Active Modules")
        header.setObjectName("headerLabel")
        layout.addWidget(header)
        
        self.list_widget = QListWidget()
        self.refresh_modules()
        
        layout.addWidget(self.list_widget)
        self.setLayout(layout)

    def refresh_modules(self):
        self.list_widget.clear()
        modules = self.orchestrator.list_modules()
        self.list_widget.addItems(modules)
