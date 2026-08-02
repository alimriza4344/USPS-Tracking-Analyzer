"""
gui.py
Professional dark-themed desktop UI for the USPS Tracking Analyzer, built with
CustomTkinter. Handles all user interaction; the actual scraping/processing
work is delegated to tracker.py and run on a background thread so the UI
never freezes.
"""

from __future__ import annotations

import os
import platform
import subprocess
import threading
import time
from pathlib import Path
from typing import List, Optional

import customtkinter as ctk
from tkinter import filedialog, messagebox

from excel_handler import ExcelImportError, ImportResult, import_tracking_numbers
from exporter import ResultExporter, RunSummary
from logger import GuiLogHandler, get_logger
from settings import Settings, load_settings, save_settings
from tracker import ControlSignal, ProcessingController, TrackingResult, TrackingStatus, USPSTracker
from utils import estimate_remaining_time, format_duration, truncate

logger = get_logger(__name__)

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

APP_TITLE = "USPS Tracking Analyzer"
ASSETS_DIR = Path(__file__).parent / "assets"


class USPSAnalyzerApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title(APP_TITLE)
        self.geometry("1180x760")
        self.minsize(1000, 680)

        self.settings: Settings = load_settings()
        self.controller = ProcessingController()
        self.tracker: Optional[USPSTracker] = None
        self.exporter: Optional[ResultExporter] = None

        self.import_result: Optional[ImportResult] = None
        self.worker_thread: Optional[threading.Thread] = None
        self.start_time: float = 0.0
        self.processed_count = 0
        self.scanned_count = 0
        self.no_scan_count = 0
        self.failed_count = 0
        self.total_count = 0

        self._build_layout()
        self._attach_gui_log_handler()
        logger.info("Application started.")

    # ------------------------------------------------------------------ #
    # Layout construction
    # ------------------------------------------------------------------ #

    def _build_layout(self) -> None:
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._build_sidebar()
        self._build_main_panel()

    def _build_sidebar(self) -> None:
        sidebar = ctk.CTkFrame(self, width=230, corner_radius=0)
        sidebar.grid(row=0, column=0, sticky="nswe")
        sidebar.grid_rowconfigure(10, weight=1)

        # NOTE: drop a real logo file at assets/logo.png and swap the emoji
        # label below for a ctk.CTkImage if you want a custom company logo.
        logo_frame = ctk.CTkFrame(sidebar, height=70, fg_color="transparent")
        logo_frame.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="we")
        ctk.CTkLabel(
            logo_frame, text="📦", font=ctk.CTkFont(size=32)
        ).pack(side="left", padx=(0, 10))
        ctk.CTkLabel(
            logo_frame, text="USPS Tracking\nAnalyzer", font=ctk.CTkFont(size=16, weight="bold"), justify="left"
        ).pack(side="left")

        ctk.CTkLabel(sidebar, text="", height=1).grid(row=1, column=0, pady=5)

        self.btn_upload = ctk.CTkButton(sidebar, text="⬆  Upload Excel", command=self.on_upload)
        self.btn_upload.grid(row=2, column=0, padx=20, pady=8, sticky="we")

        self.btn_start = ctk.CTkButton(sidebar, text="▶  Start", command=self.on_start, state="disabled")
        self.btn_start.grid(row=3, column=0, padx=20, pady=8, sticky="we")

        self.btn_pause = ctk.CTkButton(sidebar, text="⏸  Pause", command=self.on_pause, state="disabled")
        self.btn_pause.grid(row=4, column=0, padx=20, pady=8, sticky="we")

        self.btn_resume = ctk.CTkButton(sidebar, text="⏵  Resume", command=self.on_resume, state="disabled")
        self.btn_resume.grid(row=5, column=0, padx=20, pady=8, sticky="we")

        self.btn_stop = ctk.CTkButton(sidebar, text="⏹  Stop", command=self.on_stop, state="disabled",
                                       fg_color="#8B2E2E", hover_color="#6E2424")
        self.btn_stop.grid(row=6, column=0, padx=20, pady=8, sticky="we")

        self.btn_export = ctk.CTkButton(sidebar, text="💾  Export Results", command=self.on_export, state="disabled")
        self.btn_export.grid(row=7, column=0, padx=20, pady=8, sticky="we")

        self.btn_open_output = ctk.CTkButton(sidebar, text="📂  Open Output Folder", command=self.on_open_output)
        self.btn_open_output.grid(row=8, column=0, padx=20, pady=8, sticky="we")

        self.status_indicator = ctk.CTkLabel(
            sidebar, text="● Idle", text_color="#9AA0A6", font=ctk.CTkFont(size=13, weight="bold")
        )
        self.status_indicator.grid(row=9, column=0, padx=20, pady=(20, 10), sticky="w")

        settings_btn = ctk.CTkButton(sidebar, text="⚙  Settings", fg_color="transparent",
                                      border_width=1, command=self.on_open_settings)
        settings_btn.grid(row=11, column=0, padx=20, pady=(10, 20), sticky="wes")

    def _build_main_panel(self) -> None:
        main = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        main.grid(row=0, column=1, sticky="nswe", padx=20, pady=20)
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(3, weight=1)

        # File info bar
        file_frame = ctk.CTkFrame(main)
        file_frame.grid(row=0, column=0, sticky="we", pady=(0, 15))
        self.file_label = ctk.CTkLabel(file_frame, text="No file loaded", anchor="w")
        self.file_label.pack(side="left", padx=15, pady=10, fill="x", expand=True)

        # Stats grid
        stats_frame = ctk.CTkFrame(main)
        stats_frame.grid(row=1, column=0, sticky="we", pady=(0, 15))
        for i in range(7):
            stats_frame.grid_columnconfigure(i, weight=1)

        self.stat_labels = {}
        stat_defs = [
            ("total", "Total Records"),
            ("processed", "Processed"),
            ("remaining", "Remaining"),
            ("scanned", "Scanned"),
            ("no_scan", "No Scan"),
            ("failed", "Failed"),
            ("eta", "Est. Remaining Time"),
        ]
        for i, (key, label) in enumerate(stat_defs):
            cell = ctk.CTkFrame(stats_frame)
            cell.grid(row=0, column=i, padx=6, pady=10, sticky="we")
            ctk.CTkLabel(cell, text=label, font=ctk.CTkFont(size=11), text_color="#9AA0A6").pack(pady=(8, 0))
            value_lbl = ctk.CTkLabel(cell, text="0", font=ctk.CTkFont(size=18, weight="bold"))
            value_lbl.pack(pady=(0, 8))
            self.stat_labels[key] = value_lbl

        # Progress bar + current tracking number
        progress_frame = ctk.CTkFrame(main)
        progress_frame.grid(row=2, column=0, sticky="we", pady=(0, 15))
        self.progress_bar = ctk.CTkProgressBar(progress_frame)
        self.progress_bar.pack(fill="x", padx=15, pady=(15, 5))
        self.progress_bar.set(0)
        self.current_tracking_label = ctk.CTkLabel(
            progress_frame, text="Current: —", anchor="w", text_color="#9AA0A6"
        )
        self.current_tracking_label.pack(fill="x", padx=15, pady=(0, 15))

        # Live log window
        log_frame = ctk.CTkFrame(main)
        log_frame.grid(row=3, column=0, sticky="nswe")
        log_frame.grid_rowconfigure(1, weight=1)
        log_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(log_frame, text="Live Log", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, sticky="w", padx=15, pady=(10, 0)
        )
        self.log_box = ctk.CTkTextbox(log_frame, wrap="word", font=ctk.CTkFont(family="Consolas", size=12))
        self.log_box.grid(row=1, column=0, sticky="nswe", padx=15, pady=15)
        self.log_box.configure(state="disabled")

    # ------------------------------------------------------------------ #
    # Logging into the GUI
    # ------------------------------------------------------------------ #

    def _attach_gui_log_handler(self) -> None:
        handler = GuiLogHandler(self._append_log)
        logger.addHandler(handler)

    def _append_log(self, message: str, level: str) -> None:
        # `level` is accepted for future use (e.g. color-coding log lines by
        # severity); currently all lines are appended in a uniform style.
        def _do_append():
            self.log_box.configure(state="normal")
            self.log_box.insert("end", message + "\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")

        self.after(0, _do_append)

    # ------------------------------------------------------------------ #
    # Button handlers
    # ------------------------------------------------------------------ #

    def on_upload(self) -> None:
        path = filedialog.askopenfilename(
            title="Select tracking numbers file",
            filetypes=[("Spreadsheet files", "*.xlsx *.xls *.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            self.import_result = import_tracking_numbers(path)
        except ExcelImportError as exc:
            messagebox.showerror("Import Error", str(exc))
            logger.error("Import failed: %s", exc)
            return

        self.total_count = len(self.import_result.tracking_numbers)
        self.file_label.configure(
            text=(
                f"Loaded: {os.path.basename(path)}  |  "
                f"{self.total_count} valid tracking numbers  |  "
                f"{self.import_result.duplicates_removed} duplicates removed  |  "
                f"{self.import_result.invalid_rows_removed} invalid rows removed"
            )
        )
        self._update_stats(total=self.total_count, processed=0, remaining=self.total_count,
                            scanned=0, no_scan=0, failed=0, eta="Calculating...")
        self.progress_bar.set(0)
        self.btn_start.configure(state="normal")
        logger.info("Loaded %d tracking numbers from %s", self.total_count, path)

    def on_start(self) -> None:
        if not self.import_result:
            return
        self.controller = ProcessingController()
        self.exporter = ResultExporter(self.settings.output_folder)
        self.tracker = USPSTracker(self.settings, self.controller)

        self.processed_count = self.scanned_count = self.no_scan_count = self.failed_count = 0
        self.start_time = time.time()

        self._set_running_button_states()
        self.status_indicator.configure(text="● Running", text_color="#98C379")

        self.worker_thread = threading.Thread(target=self._run_processing, daemon=True)
        self.worker_thread.start()

    def on_pause(self) -> None:
        self.controller.pause()
        self.status_indicator.configure(text="● Paused", text_color="#E5C07B")
        self.btn_pause.configure(state="disabled")
        self.btn_resume.configure(state="normal")
        logger.info("Processing paused by user.")

    def on_resume(self) -> None:
        self.controller.resume()
        self.status_indicator.configure(text="● Running", text_color="#98C379")
        self.btn_pause.configure(state="normal")
        self.btn_resume.configure(state="disabled")
        logger.info("Processing resumed by user.")

    def on_stop(self) -> None:
        if not messagebox.askyesno("Stop Processing", "Are you sure you want to stop? Progress so far will be exportable."):
            return
        self.controller.stop()
        self.status_indicator.configure(text="● Stopping...", text_color="#E06C75")
        logger.info("Stop requested by user.")

    def on_export(self) -> None:
        if not self.exporter:
            messagebox.showinfo("Nothing to export", "Run processing first before exporting.")
            return
        self._export_results()

    def on_open_output(self) -> None:
        out_dir = Path(self.settings.output_folder)
        out_dir.mkdir(parents=True, exist_ok=True)
        self._open_folder(out_dir)

    def on_open_settings(self) -> None:
        SettingsDialog(self, self.settings, self._on_settings_saved)

    def _on_settings_saved(self, new_settings: Settings) -> None:
        self.settings = new_settings
        save_settings(self.settings)
        logger.info("Settings updated.")

    # ------------------------------------------------------------------ #
    # Background processing
    # ------------------------------------------------------------------ #

    def _run_processing(self) -> None:
        assert self.import_result is not None and self.tracker is not None and self.exporter is not None
        try:
            self.tracker.run(
                self.import_result.tracking_numbers,
                on_result=self._on_result,
                on_captcha=self._on_captcha_detected,
            )
        except Exception as exc:  # noqa: BLE001
            error_message = str(exc)
            logger.error("Processing crashed: %s", error_message)
            self.after(0, lambda msg=error_message: messagebox.showerror("Processing Error", msg))
        finally:
            self.after(0, self._on_processing_finished)

    def _on_result(self, result: TrackingResult, index: int, total: int) -> None:
        self.exporter.add_result(result)
        self.processed_count += 1
        if result.status == TrackingStatus.SCANNED:
            self.scanned_count += 1
        elif result.status == TrackingStatus.NO_SCAN:
            self.no_scan_count += 1
        else:
            self.failed_count += 1

        elapsed = time.time() - self.start_time
        eta = estimate_remaining_time(self.processed_count, total, elapsed)
        remaining = total - self.processed_count

        log_line = f"[{result.status.value}] {result.tracking_number}"
        if result.status == TrackingStatus.FAILED:
            logger.warning(log_line + f" -- {result.error}")
        else:
            logger.info(log_line)

        def _update_ui():
            self._update_stats(
                total=total, processed=self.processed_count, remaining=remaining,
                scanned=self.scanned_count, no_scan=self.no_scan_count,
                failed=self.failed_count, eta=eta,
            )
            self.progress_bar.set(self.processed_count / total if total else 0)
            self.current_tracking_label.configure(text=f"Current: {truncate(result.tracking_number, 40)}")

        self.after(0, _update_ui)

    def _on_captcha_detected(self) -> None:
        def _notify():
            self.status_indicator.configure(text="● CAPTCHA - Action Needed", text_color="#E06C75")
            self.btn_pause.configure(state="disabled")
            self.btn_resume.configure(state="normal", text="✔  I've Solved It — Continue")
            messagebox.showwarning(
                "CAPTCHA Detected",
                "USPS has shown a CAPTCHA challenge in the browser window.\n\n"
                "Please solve it manually in the browser, then click "
                "\"I've Solved It — Continue\" to resume processing."
            )
        self.after(0, _notify)

    def _on_processing_finished(self) -> None:
        self.status_indicator.configure(text="● Finished", text_color="#61AFEF")
        self.btn_start.configure(state="normal")
        self.btn_pause.configure(state="disabled")
        self.btn_resume.configure(state="disabled", text="⏵  Resume")
        self.btn_stop.configure(state="disabled")
        self.btn_export.configure(state="normal")
        logger.info(
            "Processing finished. Scanned=%d, No Scan=%d, Failed=%d",
            self.scanned_count, self.no_scan_count, self.failed_count,
        )
        self._export_results()

    def _export_results(self) -> None:
        if not self.exporter or not self.import_result:
            return
        elapsed = time.time() - self.start_time if self.start_time else 0
        summary = RunSummary(
            total_imported=self.import_result.total_rows_read,
            duplicates_removed=self.import_result.duplicates_removed,
            invalid_removed=self.import_result.invalid_rows_removed,
            processed=self.processed_count,
            scanned=self.scanned_count,
            no_scan=self.no_scan_count,
            failed=self.failed_count,
            processing_time_seconds=elapsed,
        )
        paths = self.exporter.export_all(summary)
        logger.info("Results exported to: %s", ", ".join(str(p) for p in paths.values()))
        messagebox.showinfo(
            "Export Complete",
            f"Results exported to:\n{self.exporter.output_dir}\n\n"
            f"Scanned: {summary.scanned}\nNo Scan: {summary.no_scan}\nFailed: {summary.failed}\n"
            f"Success Rate: {summary.success_rate}%",
        )

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _set_running_button_states(self) -> None:
        self.btn_start.configure(state="disabled")
        self.btn_pause.configure(state="normal")
        self.btn_resume.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.btn_export.configure(state="disabled")
        self.btn_upload.configure(state="disabled")

    def _update_stats(self, total, processed, remaining, scanned, no_scan, failed, eta) -> None:
        self.stat_labels["total"].configure(text=str(total))
        self.stat_labels["processed"].configure(text=str(processed))
        self.stat_labels["remaining"].configure(text=str(remaining))
        self.stat_labels["scanned"].configure(text=str(scanned))
        self.stat_labels["no_scan"].configure(text=str(no_scan))
        self.stat_labels["failed"].configure(text=str(failed))
        self.stat_labels["eta"].configure(text=eta)

    @staticmethod
    def _open_folder(path: Path) -> None:
        system = platform.system()
        try:
            if system == "Windows":
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif system == "Darwin":
                subprocess.run(["open", str(path)], check=False)
            else:
                subprocess.run(["xdg-open", str(path)], check=False)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to open output folder: %s", exc)


class SettingsDialog(ctk.CTkToplevel):
    """Modal dialog for editing application settings."""

    def __init__(self, parent: USPSAnalyzerApp, settings: Settings, on_save):
        super().__init__(parent)
        self.title("Settings")
        self.geometry("420x520")
        self.resizable(False, False)
        self.on_save = on_save
        self.settings = settings
        self.grab_set()

        pad = {"padx": 20, "pady": (10, 0)}

        ctk.CTkLabel(self, text="Browser").pack(anchor="w", **pad)
        self.browser_var = ctk.StringVar(value=settings.browser)
        ctk.CTkOptionMenu(self, values=["chromium", "firefox", "webkit"], variable=self.browser_var).pack(
            fill="x", padx=20, pady=(2, 0)
        )

        self.headless_var = ctk.BooleanVar(value=settings.headless)
        ctk.CTkCheckBox(self, text="Headless Mode (browser hidden)", variable=self.headless_var).pack(
            anchor="w", padx=20, pady=(15, 0)
        )

        ctk.CTkLabel(self, text="Delay Between Requests (seconds)").pack(anchor="w", **pad)
        self.delay_var = ctk.StringVar(value=str(settings.delay_between_requests))
        ctk.CTkEntry(self, textvariable=self.delay_var).pack(fill="x", padx=20, pady=(2, 0))

        ctk.CTkLabel(self, text="Retry Count").pack(anchor="w", **pad)
        self.retry_var = ctk.StringVar(value=str(settings.retry_count))
        ctk.CTkEntry(self, textvariable=self.retry_var).pack(fill="x", padx=20, pady=(2, 0))

        ctk.CTkLabel(self, text="Timeout (ms)").pack(anchor="w", **pad)
        self.timeout_var = ctk.StringVar(value=str(settings.timeout_ms))
        ctk.CTkEntry(self, textvariable=self.timeout_var).pack(fill="x", padx=20, pady=(2, 0))

        ctk.CTkLabel(self, text="Output Folder").pack(anchor="w", **pad)
        folder_frame = ctk.CTkFrame(self, fg_color="transparent")
        folder_frame.pack(fill="x", padx=20, pady=(2, 0))
        self.output_var = ctk.StringVar(value=settings.output_folder)
        ctk.CTkEntry(folder_frame, textvariable=self.output_var).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(folder_frame, text="Browse", width=70, command=self._browse_folder).pack(side="left", padx=(6, 0))

        ctk.CTkLabel(self, text="Browser Restart Interval (# lookups)").pack(anchor="w", **pad)
        self.restart_var = ctk.StringVar(value=str(settings.browser_restart_interval))
        ctk.CTkEntry(self, textvariable=self.restart_var).pack(fill="x", padx=20, pady=(2, 0))

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=25, side="bottom")
        ctk.CTkButton(btn_frame, text="Save", command=self._save).pack(side="right")
        ctk.CTkButton(btn_frame, text="Cancel", fg_color="transparent", border_width=1,
                      command=self.destroy).pack(side="right", padx=(0, 10))

    def _browse_folder(self) -> None:
        folder = filedialog.askdirectory(title="Select output folder")
        if folder:
            self.output_var.set(folder)

    def _save(self) -> None:
        try:
            updated = Settings(
                browser=self.browser_var.get(),
                headless=self.headless_var.get(),
                delay_between_requests=float(self.delay_var.get()),
                retry_count=int(self.retry_var.get()),
                output_folder=self.output_var.get(),
                theme=self.settings.theme,
                threads=self.settings.threads,
                timeout_ms=int(self.timeout_var.get()),
                browser_restart_interval=int(self.restart_var.get()),
                user_agent=self.settings.user_agent,
            )
        except ValueError as exc:
            messagebox.showerror("Invalid Settings", f"Please check numeric fields: {exc}")
            return
        self.on_save(updated)
        self.destroy()
