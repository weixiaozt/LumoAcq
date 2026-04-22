import logging
import sys
from datetime import date
from acquire_app.config import LOGS_DIR


def setup_logger() -> logging.Logger:
    log_file = LOGS_DIR / f"acquire_{date.today().strftime('%Y%m%d')}.log"
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    logging.basicConfig(
        level=logging.DEBUG,
        format=fmt,
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return logging.getLogger(f"lumo")


logger = setup_logger()
