"""
utils.py
Shared utility/helper functions used across the USPS Tracking Analyzer.
"""

from __future__ import annotations

import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional


def get_resource_path(relative_path: str) -> Path:
    """Get absolute path to resource, works for dev and for PyInstaller."""
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = Path(sys._MEIPASS)
    except Exception:
        base_path = Path(__file__).parent
        # If we are in utils.py, parent is project root

    return base_path / relative_path


# USPS tracking numbers come in several formats. This regex covers the most
# common ones (Priority/Express 22 digits, Certified Mail, Intl formats, etc).
# Final validation always happens by actually querying USPS (this is just a
# pre-filter), but the patterns are kept reasonably strict -- specifically,
# every pattern requires digits -- so that plain text (e.g. a stray header
# word or note left in the sheet) doesn't get treated as a tracking number.
_TRACKING_PATTERNS = [
    re.compile(r"^\d{10,34}$"),              # Standard USPS numeric (10-34 digits)
    re.compile(r"^[A-Z]{2}\d{9}[A-Z]{2}$"),  # International S10 format (e.g. EC123456789US)
]

# Alphanumeric tracking numbers (rare, but some commercial/partner formats mix
# letters and digits) must be mostly digits with only a few letters, and must
# contain at least 8 digits, to avoid matching plain English words.
_ALPHANUMERIC_FALLBACK = re.compile(r"^(?=(?:.*\d){8,})[0-9A-Z]{13,34}$")


def clean_tracking_number(raw: object) -> str:
    """Trim whitespace, strip invisible characters, and normalize a tracking number."""
    if raw is None:
        return ""
    text = str(raw).strip()
    # Remove common junk characters Excel sometimes introduces
    text = text.replace("\u200b", "").replace("\xa0", " ").strip()
    # Remove internal whitespace (people sometimes paste with spaces every 4 digits)
    text = re.sub(r"\s+", "", text)
    return text.upper()


def is_plausible_tracking_number(value: str) -> bool:
    """
    Quick sanity check that a cleaned string plausibly looks like a tracking number.
    This is NOT authoritative -- USPS itself is the source of truth. It just filters
    out obviously invalid rows (blank cells, text labels, stray notes, etc.) before
    we waste time hitting the website.
    """
    if not value:
        return False
    if len(value) < 8 or len(value) > 40:
        return False
    if any(pattern.match(value) for pattern in _TRACKING_PATTERNS):
        return True
    return bool(_ALPHANUMERIC_FALLBACK.match(value))


def format_duration(seconds: float) -> str:
    """Format a duration in seconds as H:MM:SS."""
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def estimate_remaining_time(processed: int, total: int, elapsed_seconds: float) -> str:
    """Estimate remaining processing time based on current throughput."""
    if processed <= 0 or total <= 0:
        return "Calculating..."
    remaining = total - processed
    if remaining <= 0:
        return "00:00"
    avg_per_item = elapsed_seconds / processed
    return format_duration(avg_per_item * remaining)


def timestamp() -> str:
    """Return a filesystem-safe timestamp string."""
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def now_iso() -> str:
    """Return the current time in ISO-8601 format."""
    return datetime.now().isoformat(timespec="seconds")


def retry_backoff_delay(attempt: int, base_delay: float = 1.5) -> float:
    """Simple exponential backoff delay generator for retries."""
    return base_delay * (2 ** max(0, attempt - 1))


def safe_sleep(seconds: float) -> None:
    """Sleep wrapped so it can be swapped/mocked easily in tests."""
    if seconds > 0:
        time.sleep(seconds)


def truncate(text: Optional[str], length: int = 60) -> str:
    """Truncate text for display in the UI/log window."""
    if not text:
        return ""
    text = str(text)
    return text if len(text) <= length else text[: length - 1] + "…"
