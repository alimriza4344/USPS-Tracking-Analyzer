"""
tracker.py
Deep-Targeting version: Uses multiple redundant locators to find the tracking box.
"""

from __future__ import annotations

import threading
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, List, Optional

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from logger import get_logger
from settings import Settings
from utils import retry_backoff_delay, safe_sleep, clean_tracking_number

logger = get_logger(__name__)

class TrackingStatus(Enum):
    SCANNED = "Scanned"
    NO_SCAN = "No Scan"
    FAILED = "Failed"

class ControlSignal(Enum):
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    CAPTCHA_WAIT = "captcha_wait"

@dataclass
class TrackingResult:
    tracking_number: str
    status: TrackingStatus
    latest_status_text: str = ""
    latest_event_date: str = ""
    latest_location: str = ""
    reason: str = ""
    error: str = ""
    retry_count: int = 0

class ProcessingController:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = ControlSignal.RUNNING
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._stop_event = threading.Event()
        self._captcha_resolved_event = threading.Event()

    @property
    def state(self) -> ControlSignal:
        with self._lock: return self._state
    def pause(self) -> None:
        with self._lock: self._state = ControlSignal.PAUSED
        self._pause_event.clear()
    def resume(self) -> None:
        with self._lock:
            if self._state != ControlSignal.STOPPED: self._state = ControlSignal.RUNNING
        self._pause_event.set()
    def stop(self) -> None:
        with self._lock: self._state = ControlSignal.STOPPED
        self._stop_event.set()
        self._pause_event.set()
    def is_stopped(self) -> bool: return self._stop_event.is_set()
    def wait_if_paused(self) -> None: self._pause_event.wait()
    def signal_captcha(self) -> None:
        with self._lock: self._state = ControlSignal.CAPTCHA_WAIT
        self._captcha_resolved_event.clear()
        self._pause_event.clear()
    def resolve_captcha(self) -> None:
        with self._lock:
            if self._state != ControlSignal.STOPPED: self._state = ControlSignal.RUNNING
        self._captcha_resolved_event.set()
        self._pause_event.set()

class USPSTracker:
    # Robust Redundant Selectors
    SEL_INPUTS = [
        "textarea#tracking-input",
        "textarea[placeholder*='35']",
        "textarea[placeholder*='tracking']",
        "#tLabels",
        ".tracking-input"
    ]

    SEL_BUTTONS = [
        "button.tracking-btn",
        "button:has-text('Track')",
        ".track-btn",
        "#query-button"
    ]

    SEL_RESULT_CARD = ".track-bar-container, .tracking-result-card, .track-bar"
    SEL_CARD_TRACKING_NUM = ".tracking-number, .tlabel"
    SEL_CARD_STATUS = ".delivery_status h2, .tb-status-detail, .status-container strong"

    def __init__(self, settings: Settings, controller: ProcessingController):
        self.settings = settings
        self.controller = controller
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    def _launch_browser(self) -> None:
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=self.settings.headless,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled"]
        )
        self._context = self._browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            locale="en-US",
            timezone_id="America/New_York"
        )
        self._page = self._context.new_page()
        self._page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")

        try:
            logger.info("Opening Tracking Page...")
            self._page.goto("https://tools.usps.com/tracking/", wait_until="domcontentloaded", timeout=120000)
            logger.info("READY. ONCE SITE IS VISIBLE, CLICK 'I SEE THE SITE' IN APP.")
            self.controller.pause()
            self.controller.wait_if_paused()
        except Exception as e:
            logger.error("Initial load error: %s", e)

    def _find_element(self, selectors: List[str]):
        for selector in selectors:
            try:
                el = self._page.locator(selector).first
                if el.is_visible(timeout=2000):
                    return el
            except: continue
        return None

    def _lookup_batch(self, numbers: List[str], on_captcha: Optional[Callable[[], None]]) -> List[TrackingResult]:
        # Force navigation back to the input page for EVERY batch to ensure we have a fresh search box
        try:
            self._page.goto("https://tools.usps.com/tracking/", wait_until="domcontentloaded", timeout=60000)
        except Exception:
            # If navigation fails, try one more time as a fallback
            self._page.goto("https://tools.usps.com/tracking/", wait_until="load", timeout=60000)

        try:
            # 1. Find the input box using multiple methods
            input_box = self._find_element(self.SEL_INPUTS)
            if not input_box:
                # Last resort: search for the placeholder text directly
                input_box = self._page.get_by_placeholder(re.compile(r"up to 35", re.I))

            input_box.wait_for(state="visible", timeout=30000)
            input_box.click()
            input_box.fill("") # Clear existing
            input_box.fill(",".join(numbers))
            safe_sleep(1)

            # 2. Find and click the Track button
            track_btn = self._find_element(self.SEL_BUTTONS)
            if track_btn:
                track_btn.click()
            else:
                self._page.keyboard.press("Enter")

            logger.info("Batch of %d submitted. Waiting for results...", len(numbers))

        except Exception as e:
            logger.error("Pasting failed: %s", e)
            return [TrackingResult(n, TrackingStatus.FAILED, error="Pasting failed") for n in numbers]

        # 3. Wait for results
        try:
            self._page.wait_for_selector(self.SEL_RESULT_CARD, timeout=120000)
            safe_sleep(3)
        except Exception:
            if "verify" in self._page.content().lower() or "interruption" in self._page.content().lower():
                self.controller.signal_captcha()
                if on_captcha: on_captcha()
                self.controller._captcha_resolved_event.wait()
                self._page.goto("https://tools.usps.com/tracking/")
            return [TrackingResult(n, TrackingStatus.FAILED, error="Blank screen after search") for n in numbers]

        # 4. Extract
        results = []
        cards = self._page.locator(self.SEL_RESULT_CARD).all()
        found_map = {}

        # Classification Keywords (STRICT)
        # These specifically target the "No Scan" categories as requested
        NOSCAN = [
            "usps awaiting item",
            "tracking not available",
            "pre-shipment",
            "label created",
            "not found",
            "status not available"
        ]

        for card in cards:
            try:
                num_text = card.locator(self.SEL_CARD_TRACKING_NUM).first.inner_text().strip()
                match_num = clean_tracking_number(num_text)
                status = card.locator(self.SEL_CARD_STATUS).first.inner_text().strip()

                if any(k in status.lower() for k in NOSCAN):
                    found_map[match_num] = TrackingResult(match_num, TrackingStatus.NO_SCAN, latest_status_text=status)
                else:
                    found_map[match_num] = TrackingResult(match_num, TrackingStatus.SCANNED, latest_status_text=status)
            except: pass

        for n in numbers:
            results.append(found_map.get(clean_tracking_number(n), TrackingResult(n, TrackingStatus.NO_SCAN, reason="No history")))
        return results

    def run(self, tracking_numbers: List[str], on_result: Callable[[TrackingResult, int, int], None], on_captcha: Optional[Callable[[], None]] = None, start_index: int = 0) -> None:
        self._launch_browser()
        BATCH_SIZE = 20
        try:
            for i in range(start_index, len(tracking_numbers), BATCH_SIZE):
                if self.controller.is_stopped(): break
                self.controller.wait_if_paused()
                batch = tracking_numbers[i : i + BATCH_SIZE]
                batch_results = self._lookup_batch(batch, on_captcha)
                for idx, res in enumerate(batch_results):
                    on_result(res, i + idx, len(tracking_numbers))
                safe_sleep(10)
        finally:
            self._close_browser()

    def _close_browser(self):
        try:
            if self._context: self._context.close()
            if self._browser: self._browser.close()
            if self._playwright: self._playwright.stop()
        except: pass
