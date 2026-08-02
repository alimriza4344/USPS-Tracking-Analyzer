"""
tracker.py
Core automation engine that drives a real browser (via Playwright) to look up
each tracking number on the official USPS tracking website and classify it
as Scanned / No Scan / Failed.

Design notes:
  - Uses Playwright (sync API) driven from a background thread so the GUI
    stays responsive.
  - Respects a configurable delay between requests (politeness / rate limiting).
  - Detects CAPTCHA / bot-check pages and pauses for manual human intervention
    rather than attempting to bypass them.
  - Restarts the browser periodically to avoid memory bloat over long runs.
  - Every tracking number result is reported via a callback so the GUI (or
    any other consumer) can update live and so progress can be checkpointed
    for resume-after-restart.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import Enum
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
from utils import retry_backoff_delay, safe_sleep

logger = get_logger(__name__)

USPS_TRACKING_URL = "https://tools.usps.com/go/TrackConfirmAction?tLabels={tracking_number}"


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
    """
    Thread-safe control object shared between the GUI and the worker thread.
    The GUI flips flags on this object; the worker thread checks them between
    (and during) tracking number lookups.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = ControlSignal.RUNNING
        self._pause_event = threading.Event()
        self._pause_event.set()  # set = not paused
        self._stop_event = threading.Event()
        self._captcha_resolved_event = threading.Event()

    @property
    def state(self) -> ControlSignal:
        with self._lock:
            return self._state

    def pause(self) -> None:
        with self._lock:
            self._state = ControlSignal.PAUSED
        self._pause_event.clear()

    def resume(self) -> None:
        with self._lock:
            if self._state != ControlSignal.STOPPED:
                self._state = ControlSignal.RUNNING
        self._pause_event.set()

    def stop(self) -> None:
        with self._lock:
            self._state = ControlSignal.STOPPED
        self._stop_event.set()
        self._pause_event.set()  # unblock any wait so it can exit

    def is_stopped(self) -> bool:
        return self._stop_event.is_set()

    def wait_if_paused(self) -> None:
        """Blocks the calling (worker) thread while paused."""
        self._pause_event.wait()

    def signal_captcha(self) -> None:
        with self._lock:
            self._state = ControlSignal.CAPTCHA_WAIT
        self._captcha_resolved_event.clear()
        self._pause_event.clear()

    def resolve_captcha(self) -> None:
        """Called by the user (via GUI) once they've solved the CAPTCHA."""
        with self._lock:
            if self._state != ControlSignal.STOPPED:
                self._state = ControlSignal.RUNNING
        self._captcha_resolved_event.set()
        self._pause_event.set()


class CaptchaDetectedException(Exception):
    """Raised internally when a CAPTCHA / bot-check page is detected."""


class USPSTracker:
    """
    Drives Playwright to look up tracking numbers on USPS.com.

    Usage:
        tracker = USPSTracker(settings, controller)
        tracker.run(tracking_numbers, on_result=callback, on_progress=callback)
    """

    # Selectors are grouped here so they're easy to update if USPS changes markup.
    SEL_INPUT_BOX = "input#tLabels, input[name='tLabels']"
    SEL_SEARCH_BUTTON = "button#trackButton, button[type='submit']"
    SEL_RESULT_CONTAINER = ".tracking-result, .track-bar, #trackingResultsContainer"
    SEL_EVENT_ROWS = ".tb-status, .tracking-history .tb-step, .tracking_history_details"
    SEL_ERROR_BANNER = ".banner-error, .error-alert, .no-results"
    SEL_LATEST_STATUS = ".tb-status-detail, .delivery_status, h2.tb-status"
    SEL_LATEST_DATE = ".tb-date"
    SEL_LATEST_LOCATION = ".tb-location"
    SEL_CAPTCHA_INDICATORS = [
        "iframe[src*='recaptcha']",
        "iframe[title*='captcha' i]",
        "#px-captcha",
        "text=/verify you are human/i",
    ]

    def __init__(self, settings: Settings, controller: ProcessingController):
        self.settings = settings
        self.controller = controller
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._lookups_since_restart = 0

    # ------------------------------------------------------------------ #
    # Browser lifecycle
    # ------------------------------------------------------------------ #

    def _launch_browser(self) -> None:
        logger.info("Launching browser (%s, headless=%s)", self.settings.browser, self.settings.headless)
        self._playwright = sync_playwright().start()
        browser_type = getattr(self._playwright, self.settings.browser)
        self._browser = browser_type.launch(headless=self.settings.headless)
        self._context = self._browser.new_context(user_agent=self.settings.user_agent)
        self._context.set_default_timeout(self.settings.timeout_ms)
        self._page = self._context.new_page()
        self._lookups_since_restart = 0

    def _close_browser(self) -> None:
        try:
            if self._page:
                self._page.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._context:
                self._context.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._browser:
                self._browser.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:  # noqa: BLE001
            pass
        self._page = self._context = self._browser = self._playwright = None

    def _restart_browser_if_needed(self) -> None:
        if self._lookups_since_restart >= self.settings.browser_restart_interval:
            logger.info("Restarting browser after %d lookups to keep memory stable", self._lookups_since_restart)
            self._close_browser()
            self._launch_browser()

    def _ensure_browser_alive(self) -> None:
        """Detect a crashed browser/page and relaunch if necessary."""
        try:
            if self._page is None or self._page.is_closed():
                raise RuntimeError("page closed")
            # Cheap liveness check
            _ = self._page.url
        except Exception:  # noqa: BLE001
            logger.warning("Browser/page appears to have crashed. Restarting.")
            self._close_browser()
            self._launch_browser()

    # ------------------------------------------------------------------ #
    # CAPTCHA detection
    # ------------------------------------------------------------------ #

    def _detect_captcha(self) -> bool:
        assert self._page is not None
        for selector in self.SEL_CAPTCHA_INDICATORS:
            try:
                if self._page.locator(selector).count() > 0:
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def _wait_for_manual_captcha_resolution(self, on_captcha: Optional[Callable[[], None]]) -> None:
        """
        Pause processing and block until the user resolves the CAPTCHA in the
        (non-headless) browser window and clicks "Resume" in the GUI.
        """
        logger.warning("CAPTCHA detected. Pausing for manual resolution.")
        self.controller.signal_captcha()
        if on_captcha:
            on_captcha()
        # Block here until resume_captcha() is called from the GUI thread
        self.controller._captcha_resolved_event.wait()
        logger.info("CAPTCHA resolved by user. Resuming processing.")

    # ------------------------------------------------------------------ #
    # Single tracking-number lookup
    # ------------------------------------------------------------------ #

    def _lookup_single(self, tracking_number: str, on_captcha: Optional[Callable[[], None]]) -> TrackingResult:
        assert self._page is not None
        url = USPS_TRACKING_URL.format(tracking_number=tracking_number)

        self._page.goto(url, wait_until="domcontentloaded")

        if self._detect_captcha():
            self._wait_for_manual_captcha_resolution(on_captcha)
            # Re-check the page after resolution; reload to get fresh results
            self._page.goto(url, wait_until="domcontentloaded")
            if self._detect_captcha():
                # Still blocked -- treat as failure for this item, don't loop forever
                return TrackingResult(
                    tracking_number=tracking_number,
                    status=TrackingStatus.FAILED,
                    error="CAPTCHA still present after manual resolution attempt",
                )

        # Wait for either a results container or an error banner to appear
        try:
            self._page.wait_for_selector(
                f"{self.SEL_RESULT_CONTAINER}, {self.SEL_ERROR_BANNER}",
                timeout=self.settings.timeout_ms,
            )
        except PlaywrightTimeoutError:
            return TrackingResult(
                tracking_number=tracking_number,
                status=TrackingStatus.FAILED,
                error="Timed out waiting for tracking results to load",
            )

        # No scan / no history detection
        error_banner = self._page.locator(self.SEL_ERROR_BANNER)
        if error_banner.count() > 0:
            banner_text = error_banner.first.inner_text().strip()
            return TrackingResult(
                tracking_number=tracking_number,
                status=TrackingStatus.NO_SCAN,
                reason=banner_text or "USPS reports no tracking information available",
            )

        event_rows = self._page.locator(self.SEL_EVENT_ROWS)
        event_count = event_rows.count()

        latest_status = self._safe_text(self.SEL_LATEST_STATUS)
        latest_date = self._safe_text(self.SEL_LATEST_DATE)
        latest_location = self._safe_text(self.SEL_LATEST_LOCATION)

        if event_count > 0 or latest_status:
            return TrackingResult(
                tracking_number=tracking_number,
                status=TrackingStatus.SCANNED,
                latest_status_text=latest_status,
                latest_event_date=latest_date,
                latest_location=latest_location,
            )

        return TrackingResult(
            tracking_number=tracking_number,
            status=TrackingStatus.NO_SCAN,
            reason="No scan events found for this tracking number",
        )

    def _safe_text(self, selector: str) -> str:
        try:
            loc = self._page.locator(selector)
            if loc.count() > 0:
                return loc.first.inner_text().strip()
        except Exception:  # noqa: BLE001
            pass
        return ""

    # ------------------------------------------------------------------ #
    # Public entry point
    # ------------------------------------------------------------------ #

    def run(
        self,
        tracking_numbers: List[str],
        on_result: Callable[[TrackingResult, int, int], None],
        on_captcha: Optional[Callable[[], None]] = None,
        start_index: int = 0,
    ) -> None:
        """
        Process tracking numbers sequentially, invoking on_result(result, index, total)
        after each one. Honors pause/resume/stop via self.controller.
        start_index allows resuming a previous run (e.g. after a browser restart
        or an app relaunch) without redoing already-completed lookups.
        """
        total = len(tracking_numbers)
        try:
            self._launch_browser()
            for i in range(start_index, total):
                if self.controller.is_stopped():
                    logger.info("Processing stopped by user at index %d/%d", i, total)
                    break

                self.controller.wait_if_paused()
                if self.controller.is_stopped():
                    break

                self._ensure_browser_alive()
                self._restart_browser_if_needed()

                tracking_number = tracking_numbers[i]
                result = self._lookup_with_retries(tracking_number, on_captcha)
                self._lookups_since_restart += 1

                on_result(result, i, total)
                safe_sleep(self.settings.delay_between_requests)
        finally:
            self._close_browser()

    def _lookup_with_retries(
        self, tracking_number: str, on_captcha: Optional[Callable[[], None]]
    ) -> TrackingResult:
        last_error = ""
        for attempt in range(1, self.settings.retry_count + 1):
            try:
                return self._lookup_single(tracking_number, on_captcha)
            except PlaywrightTimeoutError as exc:
                last_error = f"Timeout: {exc}"
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)

            logger.warning(
                "Lookup failed for %s (attempt %d/%d): %s",
                tracking_number, attempt, self.settings.retry_count, last_error,
            )
            self._ensure_browser_alive()
            safe_sleep(retry_backoff_delay(attempt))

        return TrackingResult(
            tracking_number=tracking_number,
            status=TrackingStatus.FAILED,
            error=last_error,
            retry_count=self.settings.retry_count,
        )
