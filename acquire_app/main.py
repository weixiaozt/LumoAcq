import sys
from PySide6.QtWidgets import QApplication
from acquire_app.config import APP_NAME, APP_VERSION
from acquire_app.logger import logger
from acquire_app.gui.main_window import MainWindow


def main():
    logger.info(f"{APP_NAME} v{APP_VERSION} starting")
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    window = MainWindow()
    window.show()
    logger.info("ready")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
