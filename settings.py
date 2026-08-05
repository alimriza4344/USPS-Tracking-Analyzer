"""
settings.py
Loads, validates, and persists application settings from config/settings.json.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict

CONFIG_DIR = Path(__file__).parent / "config"
CONFIG_DIR.mkdir(exist_ok=True)
SETTINGS_PATH = CONFIG_DIR / "settings.json"


@dataclass
class Settings:
    """Strongly-typed application settings, backed by settings.json."""

    browser: str = "chromium"          # chromium | firefox | webkit
    headless: bool = False             # visible browser so user can solve CAPTCHAs
    delay_between_requests: float = 2.5  # seconds, be polite to USPS servers
    retry_count: int = 3
    output_folder: str = "output"
    theme: str = "dark"
    threads: int = 1                   # reserved for future concurrent processing
    timeout_ms: int = 30000            # page navigation / element wait timeout
    browser_restart_interval: int = 250  # restart browser every N tracking numbers
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    proxy_enabled: bool = False
    proxy_server: str = ""             # e.g., http://user:pass@host:port

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Settings":
        valid_keys = {f for f in cls.__dataclass_fields__.keys()}
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)


def load_settings() -> Settings:
    """Load settings from disk, creating a default file if none exists."""
    if not SETTINGS_PATH.exists():
        default = Settings()
        save_settings(default)
        return default

    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return Settings.from_dict(data)
    except (json.JSONDecodeError, OSError):
        # Corrupt settings file -- fall back to defaults but don't crash the app
        default = Settings()
        save_settings(default)
        return default


def save_settings(settings: Settings) -> None:
    """Persist settings to disk as pretty-printed JSON."""
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings.to_dict(), f, indent=4)
