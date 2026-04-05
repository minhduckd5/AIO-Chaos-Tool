from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QLineEdit, QComboBox, 
    QPushButton, QGroupBox, QLabel, QSpinBox, QHBoxLayout, QCheckBox,
    QStackedWidget
)
from PySide6.QtCore import Signal
from chaosgen.schemas.faults import (
    ChaosExperiment, TargetSpec, TargetType, FaultType, 
    NetworkFaultSpec, ProcessFaultSpec, ResourceFaultSpec, FaultSpec
)

class ExperimentForm(QWidget):
    experiment_created = Signal(ChaosExperiment)

    def __init__(self):
        super().__init__()
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()
        
        # --- Experiment Info ---
        info_group = QGroupBox("Experiment Details")
        info_layout = QFormLayout()
        self.name_input = QLineEdit("My Experiment")
        self.desc_input = QLineEdit("Chaos Test")
        info_layout.addRow("Name:", self.name_input)
        info_layout.addRow("Description:", self.desc_input)
        info_group.setLayout(info_layout)
        layout.addWidget(info_group)

        # --- Target Spec ---
        target_group = QGroupBox("Target Configuration")
        target_layout = QFormLayout()
        
        self.target_type_combo = QComboBox()
        # Add enum values
        for t in TargetType:
            self.target_type_combo.addItem(t.value)
            
        self.target_name_input = QLineEdit("frontend")
        self.target_namespace_input = QLineEdit("default")
        
        target_layout.addRow("Type:", self.target_type_combo)
        target_layout.addRow("Name/Label:", self.target_name_input)
        target_layout.addRow("Namespace:", self.target_namespace_input)
        target_group.setLayout(target_layout)
        layout.addWidget(target_group)

        # --- Fault Spec ---
        fault_group = QGroupBox("Fault Injection")
        fault_layout = QFormLayout()
        
        self.fault_type_combo = QComboBox()
        for f in FaultType:
            self.fault_type_combo.addItem(f.value)
        self.fault_type_combo.currentTextChanged.connect(self.update_fault_params)
            
        self.duration_input = QLineEdit("30s")
        
        # Stacked Widget for Params
        self.params_stack = QStackedWidget()
        
        # Page 0: Network
        self.net_page = QWidget()
        net_layout = QFormLayout()
        self.latency_input = QLineEdit("100ms")
        self.jitter_input = QLineEdit("10ms")
        net_layout.addRow("Latency:", self.latency_input)
        net_layout.addRow("Jitter:", self.jitter_input)
        self.net_page.setLayout(net_layout)
        self.params_stack.addWidget(self.net_page)
        
        # Page 1: Process
        self.proc_page = QWidget()
        proc_layout = QFormLayout()
        self.signal_input = QLineEdit("SIGKILL")
        proc_layout.addRow("Signal:", self.signal_input)
        self.proc_page.setLayout(proc_layout)
        self.params_stack.addWidget(self.proc_page)
        
        # Page 2: Resource
        self.res_page = QWidget()
        res_layout = QFormLayout()
        self.cpu_input = QSpinBox()
        self.cpu_input.setRange(0, 100)
        self.cpu_input.setValue(80)
        res_layout.addRow("CPU %:", self.cpu_input)
        self.res_page.setLayout(res_layout)
        self.params_stack.addWidget(self.res_page)
        
        # Page 3: Empty (for others)
        self.empty_page = QWidget()
        self.params_stack.addWidget(self.empty_page)

        fault_layout.addRow("Fault Type:", self.fault_type_combo)
        fault_layout.addRow("Duration:", self.duration_input)
        fault_layout.addRow("Parameters:", self.params_stack)
        
        fault_group.setLayout(fault_layout)
        layout.addWidget(fault_group)

        # --- Options ---
        options_group = QGroupBox("Options")
        options_layout = QHBoxLayout()
        self.rollback_check = QCheckBox("Auto-Rollback")
        self.rollback_check.setChecked(True)
        options_layout.addWidget(self.rollback_check)
        options_group.setLayout(options_layout)
        layout.addWidget(options_group)

        # --- Submit ---
        self.submit_btn = QPushButton("Create Experiment Plan")
        self.submit_btn.clicked.connect(self.submit_experiment)
        layout.addWidget(self.submit_btn)
        
        self.setLayout(layout)
        self.update_fault_params(self.fault_type_combo.currentText())

    def update_fault_params(self, fault_type):
        if fault_type in ["network_latency", "packet_loss"]:
            self.params_stack.setCurrentWidget(self.net_page)
        elif fault_type == "process_kill":
            self.params_stack.setCurrentWidget(self.proc_page)
        elif fault_type == "resource_exhaustion":
            self.params_stack.setCurrentWidget(self.res_page)
        else:
            self.params_stack.setCurrentWidget(self.empty_page)

    def submit_experiment(self):
        try:
            # Build Target
            target = TargetSpec(
                type=TargetType(self.target_type_combo.currentText()),
                name=self.target_name_input.text(),
                namespace=self.target_namespace_input.text() or None
            )

            # Build Fault
            ftype = self.fault_type_combo.currentText()
            common_args = {
                "fault_type": FaultType(ftype),
                "duration": self.duration_input.text()
            }

            faults = []
            if ftype in ["network_latency", "packet_loss"]:
                fault = NetworkFaultSpec(
                    **common_args,
                    latency=self.latency_input.text(),
                    jitter=self.jitter_input.text()
                )
                faults.append(fault)
            elif ftype == "process_kill":
                fault = ProcessFaultSpec(
                    **common_args,
                    signal=self.signal_input.text()
                )
                faults.append(fault)
            elif ftype == "resource_exhaustion":
                fault = ResourceFaultSpec(
                    **common_args,
                    cpu_percent=self.cpu_input.value()
                )
                faults.append(fault)
            else:
                # Fallback for generic types
                fault = FaultSpec(**common_args)
                faults.append(fault)

            # Build Experiment
            exp = ChaosExperiment(
                name=self.name_input.text(),
                description=self.desc_input.text(),
                target=target,
                faults=faults,
                rollback=self.rollback_check.isChecked(),
                steady_state_check={"http_health": "http://localhost:8080/health"}
            )
            
            self.experiment_created.emit(exp)
            
        except Exception as e:
            # Handle validation errors (e.g. empty fields)
            print(f"Form Error: {e}")
