"""
main.py
Entry point for the USPS Tracking Analyzer desktop application.

Run with:
    python main.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from logger import get_logger

# Fix for PyInstaller + Playwright:
# Force Playwright to look for browsers in the standard %USERPROFILE%\AppData\Local\ms-playwright
# instead of the temporary extraction folder (_MEIxxx) created by the EXE.
if getattr(sys, 'frozen', False):
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(Path.home() / "AppData" / "Local" / "ms-playwright")

logger = get_logger(__name__)


def main() -> int:
    try:
        from gui import USPSAnalyzerApp
    except ImportError as exc:
        print(
            "Missing dependency. Please run 'pip install -r requirements.txt' "
            f"and 'playwright install' before launching the app.\n\nDetails: {exc}"
        )
        return 1

    try:
        app = USPSAnalyzerApp()
        app.mainloop()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Fatal error while running the application: %s", exc)
        print(f"A fatal error occurred: {exc}\nSee logs/errors.log for details.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
