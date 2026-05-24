from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication, QDialog, QLabel, QLineEdit, QPushButton, QVBoxLayout

from autotrader_app.auth import verify_license
from autotrader_app.config import check_config
from autotrader_app.database import init_db
from autotrader_app.gui.main_window import MainWindow
from autotrader_app.logging_config import setup_logger


def _verify_license_at_startup() -> bool:
    dialog = QDialog()
    dialog.setWindowTitle("软件授权")
    dialog.resize(380, 180)

    layout = QVBoxLayout(dialog)
    title = QLabel("<h3>请输入授权码</h3>")
    title.setStyleSheet("color:#111827;")
    layout.addWidget(title)

    tip = QLabel("请联系开发者获取授权码")
    tip.setStyleSheet("color:#6b7280; font-size:12px;")
    layout.addWidget(tip)

    key_input = QLineEdit()
    key_input.setPlaceholderText("输入授权码")
    layout.addWidget(key_input)

    err = QLabel("")
    err.setStyleSheet("color:#ef4444; font-size:12px;")
    layout.addWidget(err)

    btn = QPushButton("验证")
    layout.addWidget(btn)

    result = [False]

    def on_click():
        key = key_input.text().strip()
        if not key:
            err.setText("请输入授权码"); return
        btn.setEnabled(False); btn.setText("验证中...")
        ok, msg = verify_license(key)
        if ok:
            result[0] = True; dialog.accept()
        else:
            err.setText(f"❌ {msg}"); btn.setEnabled(True); btn.setText("验证")

    btn.clicked.connect(on_click)
    key_input.returnPressed.connect(on_click)
    dialog.exec()
    return result[0]


def main() -> int:
    setup_logger()
    init_db()
    if not _verify_license_at_startup():
        return 1
    for warn in check_config():
        import warnings as _warnings
        _warnings.warn(warn)

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
