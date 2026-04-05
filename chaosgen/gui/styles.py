DARK_THEME = """
QWidget {
    background-color: #2b2b2b;
    color: #ffffff;
    font-family: 'Segoe UI', sans-serif;
    font-size: 14px;
}

QLineEdit, QComboBox, QSpinBox {
    background-color: #3c3f41;
    border: 1px solid #555;
    border-radius: 4px;
    padding: 5px;
    color: #ffffff;
}

QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
    border: 1px solid #3daee9;
}

QPushButton {
    background-color: #3daee9;
    color: #ffffff;
    border: none;
    border-radius: 4px;
    padding: 8px 16px;
    font-weight: bold;
}

QPushButton:hover {
    background-color: #5bbff0;
}

QPushButton:pressed {
    background-color: #2a7cad;
}

QPushButton#stopButton {
    background-color: #e74c3c;
}

QPushButton#stopButton:hover {
    background-color: #ff6b6b;
}

QProgressBar {
    border: 1px solid #555;
    border-radius: 4px;
    text-align: center;
    background-color: #3c3f41;
}

QProgressBar::chunk {
    background-color: #3daee9;
    border-radius: 3px;
}

QListWidget {
    background-color: #3c3f41;
    border: 1px solid #555;
    border-radius: 4px;
}

QListWidget::item:selected {
    background-color: #3daee9;
}

QTextEdit {
    background-color: #1e1e1e;
    color: #dcdcdc;
    font-family: 'Consolas', monospace;
    border: 1px solid #555;
    border-radius: 4px;
}

QLabel#headerLabel {
    font-size: 18px;
    font-weight: bold;
    color: #3daee9;
    padding-bottom: 5px;
}
"""
