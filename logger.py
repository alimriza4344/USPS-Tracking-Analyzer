"""
logger.py
Centralized logging configuration for the USPS Tracking Analyzer.

Creates three log streams:
  - logs/activity_YYYY-MM-DD.log  -> general daily activity log (INFO+)
  - logs/errors.log               -> errors only, across all sessions (ERROR+)
  - logs/activity.log             -> rolling "latest session" convenience log
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)


def get_logger(name: str = "usps_analyzer") -> logging.Logger:
    """
    Return a configured logger. Safe to call multiple times (handlers are
    only attached once per logger name).
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger  # Already configured

    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(threadName)-12s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Daily rotating log (keeps 30 days of history)
    daily_handler = logging.handlers.TimedRotatingFileHandler(
        LOG_DIR / "activity.log", when="midnight", backupCount=30, encoding="utf-8"
    )
    daily_handler.setLevel(logging.INFO)
    daily_handler.setFormatter(fmt)
    logger.addHandler(daily_handler)

    # Error-only log
    error_handler = logging.FileHandler(LOG_DIR / "errors.log", encoding="utf-8")
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(fmt)
    logger.addHandler(error_handler)

    # Console output for when running from a terminal
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(fmt)
    logger.addHandler(console_handler)

    return logger


class GuiLogHandler(logging.Handler):
    """
    A logging.Handler that forwards formatted log records to a callback,
    typically used to pipe log lines into the GUI's live log window.
    """

    def __init__(self, callback):
        super().__init__()
        self.callback = callback
        self.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self.callback(msg, record.levelname)
        except Exception:
            self.handleError(record)
