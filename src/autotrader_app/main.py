from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication

from autotrader_app.database import init_db
from autotrader_app.gui.main_window import MainWindow
from autotrader_app.logging_config import setup_logger


def main() -> int:
    setup_logger()
    init_db()
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
