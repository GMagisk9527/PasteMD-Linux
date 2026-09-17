#!/usr/bin/env python3
"""冒烟测试窗口：真实 Wayland 会话里的非 WPS 窗口，用于规则匹配流程验证。"""
import sys
from PySide6.QtWidgets import QApplication, QLineEdit, QVBoxLayout, QWidget

app = QApplication(['testwindow', '-platform', 'wayland'])
box = QWidget()
box.setWindowTitle('规则测试窗口')
edit = QLineEdit('等待粘贴…')
layout = QVBoxLayout(box)
layout.addWidget(edit)
box.show()
sys.exit(app.exec())
