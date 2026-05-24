from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication

from autotrader_app.config import check_config
from autotrader_app.database import init_db
from autotrader_app.gui.main_window import MainWindow
from autotrader_app.logging_config import setup_logger


def main() -> int:
    setup_logger()
    init_db()

    # 配置检查（只在控制台输出警告）
    for warn in check_config():
        import warnings as _warnings
        _warnings.warn(warn)

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
