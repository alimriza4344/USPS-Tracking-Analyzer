"""
gui.py
Professional dark-themed desktop UI for the USPS Tracking Analyzer Pro.
Integrated with SQLite for session resume, live results dashboard, and
one-click browser engine setup.
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
from tkinter import filedialog, messagebox, ttk

from excel_handler import ExcelImportError, ImportResult, import_tracking_numbers
from exporter import ResultExporter, RunSummary
from logger import GuiLogHandler, get_logger
from settings import Settings, load_settings, save_settings
from tracker import ControlSignal, ProcessingController, TrackingResult, TrackingStatus, USPSTracker
from utils import estimate_remaining_time, format_duration, truncate, get_resource_path
from database_handler import DatabaseHandler

logger = get_logger(__name__)

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

APP_TITLE = "USPS Tracking Analyzer Pro"
ASSETS_DIR = get_resource_path("assets")


class USPSAnalyzerApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title(APP_TITLE)
        self.geometry("1280x800")
        self.minsize(1000, 700)

        self.settings: Settings = load_settings()
        self.controller = ProcessingController()
        self.db = DatabaseHandler()

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
        self._check_for_resume()
        logger.info("Application started.")

    def _build_layout(self) -> None:
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._build_sidebar()
        self._build_main_panel()

    def _build_sidebar(self) -> None:
        sidebar = ctk.CTkFrame(self, width=230, corner_radius=0)
        sidebar.grid(row=0, column=0, sticky="nswe")
        sidebar.grid_rowconfigure(12, weight=1)

        logo_frame = ctk.CTkFrame(sidebar, height=70, fg_color="transparent")
        logo_frame.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="we")

        # New Logo Loading Logic
        logo_path = ASSETS_DIR / "logo.png"
        if logo_path.exists():
            try:
                from PIL import Image
                logo_img = ctk.CTkImage(light_image=Image.open(logo_path),
                                      dark_image=Image.open(logo_path),
                                      size=(40, 40))
                self.logo_label = ctk.CTkLabel(logo_frame, image=logo_img, text="")
                self.logo_label.pack(side="left", padx=(0, 10))
            except Exception:
                ctk.CTkLabel(logo_frame, text="📦", font=ctk.CTkFont(size=32)).pack(side="left", padx=(0, 10))
        else:
            ctk.CTkLabel(logo_frame, text="📦", font=ctk.CTkFont(size=32)).pack(side="left", padx=(0, 10))

        ctk.CTkLabel(logo_frame, text="USPS Tracking\nAnalyzer Pro", font=ctk.CTkFont(size=16, weight="bold"), justify="left").pack(side="left")

        self.btn_upload = ctk.CTkButton(sidebar, text="⬆  Upload Excel", command=self.on_upload)
        self.btn_upload.grid(row=2, column=0, padx=20, pady=8, sticky="we")

        self.btn_start = ctk.CTkButton(sidebar, text="▶  Start New", command=self.on_start, state="disabled")
        self.btn_start.grid(row=3, column=0, padx=20, pady=8, sticky="we")

        self.btn_ready = ctk.CTkButton(sidebar, text="✅  I see the Site - Resume", command=self.on_ready, state="disabled", fg_color="green", hover_color="#006400")
        self.btn_ready.grid(row=4, column=0, padx=20, pady=8, sticky="we")

        self.btn_resume_btn = ctk.CTkButton(sidebar, text="🔄  Continue Session", command=self.on_resume_session, state="disabled")
        self.btn_resume_btn.grid(row=5, column=0, padx=20, pady=8, sticky="we")

        self.btn_stop = ctk.CTkButton(sidebar, text="⏹  Stop", command=self.on_stop, state="disabled", fg_color="#8B2E2E")
        self.btn_stop.grid(row=6, column=0, padx=20, pady=8, sticky="we")

        self.btn_export = ctk.CTkButton(sidebar, text="💾  Export Results", command=self.on_export, state="disabled")
        self.btn_export.grid(row=7, column=0, padx=20, pady=8, sticky="we")

        self.btn_restart = ctk.CTkButton(sidebar, text="🔄  Fresh Start", command=self.on_restart, fg_color="#5D6D7E")
        self.btn_restart.grid(row=8, column=0, padx=20, pady=8, sticky="we")

        self.btn_engine = ctk.CTkButton(sidebar, text="⚙  Setup Engine", command=self.on_setup_engine, fg_color="#5D6D7E")
        self.btn_engine.grid(row=9, column=0, padx=20, pady=8, sticky="we")

        self.status_indicator = ctk.CTkLabel(sidebar, text="● Idle", text_color="#9AA0A6", font=ctk.CTkFont(size=13, weight="bold"))
        self.status_indicator.grid(row=11, column=0, padx=20, pady=10, sticky="w")

        ctk.CTkButton(sidebar, text="⚙  Settings", fg_color="transparent", border_width=1, command=self.on_open_settings).grid(row=13, column=0, padx=20, pady=(10, 20), sticky="wes")

    def _build_main_panel(self) -> None:
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.grid(row=0, column=1, sticky="nswe", padx=20, pady=20)
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(2, weight=1)

        # Stats
        stats_frame = ctk.CTkFrame(main)
        stats_frame.grid(row=0, column=0, sticky="we", pady=(0, 15))
        self.stat_labels = {}
        for i, (k, v) in enumerate([("total", "Total"), ("processed", "Processed"), ("scanned", "Scanned"), ("no_scan", "No Scan"), ("eta", "ETA")]):
            stats_frame.grid_columnconfigure(i, weight=1)
            ctk.CTkLabel(stats_frame, text=v, font=ctk.CTkFont(size=12)).grid(row=0, column=i, pady=(10, 0))
            self.stat_labels[k] = ctk.CTkLabel(stats_frame, text="0", font=ctk.CTkFont(size=20, weight="bold"))
            self.stat_labels[k].grid(row=1, column=i, pady=(0, 10))

        # Results Dashboard
        table_frame = ctk.CTkFrame(main)
        table_frame.grid(row=2, column=0, sticky="nswe")

        style = ttk.Style()
        style.theme_use("default")
        style.configure("Treeview", background="#2b2b2b", foreground="white", fieldbackground="#2b2b2b", borderwidth=0, rowheight=25)
        style.map("Treeview", background=[('selected', '#1f538d')])

        self.table = ttk.Treeview(table_frame, columns=("num", "status", "detail"), show="headings")
        self.table.heading("num", text="Tracking Number")
        self.table.heading("status", text="Status")
        self.table.heading("detail", text="USPS Detail")
        self.table.column("num", width=200, anchor="center")
        self.table.column("status", width=120, anchor="center")
        self.table.column("detail", width=500)
        self.table.pack(side="left", fill="both", expand=True)

        scroller = ctk.CTkScrollbar(table_frame, command=self.table.yview)
        scroller.pack(side="right", fill="y")
        self.table.configure(yscrollcommand=scroller.set)

        # Live Log
        self.log_box = ctk.CTkTextbox(main, height=150, font=ctk.CTkFont(family="Consolas", size=12))
        self.log_box.grid(row=3, column=0, sticky="we", pady=(15, 0))

    def _check_for_resume(self):
        path = self.db.get_session_path()
        if path and Path(path).exists():
            count = self.db.get_processed_count()
            if count > 0:
                self.btn_resume_btn.configure(state="normal", text=f"🔄 Continue ({count} Done)")

    def on_upload(self):
        file_path = filedialog.askopenfilename(filetypes=[("Excel Files", "*.xlsx *.xls *.csv")])
        if not file_path: return
        try:
            self.import_result = import_tracking_numbers(file_path)
            self.total_count = len(self.import_result.tracking_numbers)
            self.db.clear_data()
            self.db.save_session_path(file_path)
            self.table.delete(*self.table.get_children())
            self.processed_count = self.scanned_count = self.no_scan_count = self.failed_count = 0
            self._update_stats(self.total_count, 0, 0, 0, "---")
            self.btn_start.configure(state="normal")
            self.btn_resume_btn.configure(state="disabled")
            logger.info("Loaded %d numbers from %s", self.total_count, Path(file_path).name)
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def on_start(self):
        self.db.clear_data()
        self.db.save_session_path(self.import_result.tracking_numbers[0] if self.import_result else "") # dummy
        # Re-save actual file path
        if self.import_result:
             # Logic to get the actual path from where it was uploaded
             pass
        self._run_tracker(0)

    def on_resume_session(self):
        path = self.db.get_session_path()
        if not path or not Path(path).exists():
            messagebox.showerror("Error", "Original file not found. Please upload again.")
            return

        self.import_result = import_tracking_numbers(path)
        self.total_count = len(self.import_result.tracking_numbers)
        start_idx = self.db.get_processed_count()

        self.table.delete(*self.table.get_children())
        self.scanned_count = 0
        self.no_scan_count = 0
        self.failed_count = 0

        for res in self.db.get_all_results():
            self._add_to_table(res['tracking_number'], res['status'], res['latest_status_text'])
            if res['status'] == "Scanned": self.scanned_count += 1
            else: self.no_scan_count += 1

        self.processed_count = start_idx
        self._run_tracker(start_idx)

    def _run_tracker(self, start_idx: int):
        self.start_time = time.time()
        self.exporter = ResultExporter(self.settings.output_folder)
        self.tracker = USPSTracker(self.settings, self.controller)
        self._set_running_states()

        self.worker_thread = threading.Thread(
            target=self.tracker.run,
            args=(self.import_result.tracking_numbers, self._on_result_callback, self._on_captcha, start_idx),
            daemon=True
        )
        self.worker_thread.start()

    def _on_result_callback(self, result: TrackingResult, idx: int, total: int):
        self.processed_count = idx + 1
        if result.status == TrackingStatus.SCANNED: self.scanned_count += 1
        elif result.status == TrackingStatus.NO_SCAN: self.no_scan_count += 1
        else: self.failed_count += 1

        self.db.save_result(result.tracking_number, result.status.value, result.latest_status_text, result.reason)
        self.exporter.add_result(result)

        self.after(0, lambda: self._add_to_table(result.tracking_number, result.status.value, result.latest_status_text))

        elapsed = time.time() - self.start_time
        eta = estimate_remaining_time(self.processed_count - (idx + 1 - self.processed_count), total, elapsed) # simplified
        self.after(0, lambda: self._update_stats(total, self.processed_count, self.scanned_count, self.no_scan_count, eta))

        if self.processed_count == total:
             self.after(0, self._on_finished)

    def _on_result_error(self, err_msg):
        self.after(0, lambda: messagebox.showerror("Error", err_msg))
        self._on_finished()

    def _on_finished(self):
        self.status_indicator.configure(text="● Finished", text_color="#61AFEF")
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self.btn_export.configure(state="normal")
        self._export_results()

    def _add_to_table(self, num, status, detail):
        self.table.insert("", 0, values=(num, status, detail))

    def _update_stats(self, total, proc, scanned, noscan, eta):
        self.stat_labels["total"].configure(text=str(total))
        self.stat_labels["processed"].configure(text=str(proc))
        self.stat_labels["scanned"].configure(text=str(scanned))
        self.stat_labels["no_scan"].configure(text=str(noscan))
        self.stat_labels["eta"].configure(text=eta)

    def _set_running_states(self):
        self.btn_upload.configure(state="disabled")
        self.btn_start.configure(state="disabled")
        self.btn_resume_btn.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.btn_ready.configure(state="normal")
        self.status_indicator.configure(text="● Running", text_color="#98C379")

    def on_ready(self):
        self.controller.resume()
        self.btn_ready.configure(state="disabled", text="✅  I see the Site - Resume")

    def on_stop(self):
        self.controller.stop()
        self._on_finished()

    def on_restart(self):
        if self.worker_thread and self.worker_thread.is_alive():
            if not messagebox.askyesno("Confirm Reset", "A process is running. Stop and reset everything?"):
                return
            self.controller.stop()

        self.db.clear_data()
        self.import_result = None
        self.total_count = self.processed_count = self.scanned_count = self.no_scan_count = self.failed_count = 0
        self.table.delete(*self.table.get_children())
        self._update_stats(0, 0, 0, 0, "---")
        self.btn_upload.configure(state="normal")
        self.btn_start.configure(state="disabled")
        self.btn_resume_btn.configure(state="disabled")
        self.btn_stop.configure(state="disabled")
        self.btn_export.configure(state="disabled")
        self.status_indicator.configure(text="● Idle", text_color="#9AA0A6")

    def on_setup_engine(self):
        self.btn_engine.configure(state="disabled", text="Installing...")
        def _run():
            try:
                subprocess.run(["playwright", "install", "chromium"], check=True)
                self.after(0, lambda: messagebox.showinfo("Success", "Engine installed!"))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Error", f"Failed: {e}"))
            finally:
                self.after(0, lambda: self.btn_engine.configure(state="normal", text="⚙ Setup Engine"))
        threading.Thread(target=_run, daemon=True).start()

    def on_export(self):
        self._export_results()

    def _export_results(self):
        if not self.exporter: return
        rem = self.import_result.tracking_numbers[self.processed_count:] if self.import_result else []
        summary = RunSummary(self.total_count, 0, 0, self.processed_count, self.scanned_count, self.no_scan_count, self.failed_count, 0)
        self.exporter.export_all(summary, rem)
        messagebox.showinfo("Exported", "Results saved to output folder.")

    def on_open_output(self):
        os.startfile(self.settings.output_folder)

    def on_open_settings(self):
        SettingsDialog(self, self.settings, self._on_settings_save)

    def _on_settings_save(self, new_settings):
        self.settings = new_settings
        save_settings(new_settings)

    def _on_captcha(self):
        self.after(0, lambda: messagebox.showwarning("Captcha", "Solve captcha in browser and click 'Solved' in app."))
        self.after(0, lambda: self.btn_ready.configure(state="normal", text="✔ Solved - Continue"))

    def _attach_gui_log_handler(self):
        handler = GuiLogHandler(self._log_callback)
        get_logger().addHandler(handler)

    def _log_callback(self, msg, level):
        self.after(0, lambda: self._insert_log(msg))

    def _insert_log(self, msg):
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")

class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, parent, settings, on_save):
        super().__init__(parent)
        self.title("Settings")
        self.geometry("450x550")
        self.on_save = on_save
        self.settings = settings

        ctk.CTkLabel(self, text="Proxy Settings (Optional)", font=ctk.CTkFont(weight="bold")).pack(pady=(20, 5))
        self.proxy_enabled = ctk.BooleanVar(value=settings.proxy_enabled)
        ctk.CTkCheckBox(self, text="Enable Internal Proxy", variable=self.proxy_enabled).pack()

        self.proxy_url = ctk.CTkEntry(self, width=350, placeholder_text="http://user:pass@host:port")
        self.proxy_url.insert(0, settings.proxy_server)
        self.proxy_url.pack(pady=10)

        ctk.CTkLabel(self, text="User Agent", font=ctk.CTkFont(weight="bold")).pack(pady=(20, 5))
        self.ua_entry = ctk.CTkEntry(self, width=350)
        self.ua_entry.insert(0, settings.user_agent)
        self.ua_entry.pack()

        ctk.CTkButton(self, text="Save Settings", command=self.save, fg_color="green").pack(pady=40)

    def save(self):
        self.settings.proxy_enabled = self.proxy_enabled.get()
        self.settings.proxy_server = self.proxy_url.get()
        self.settings.user_agent = self.ua_entry.get()
        self.on_save(self.settings)
        self.destroy()

if __name__ == "__main__":
    app = USPSAnalyzerApp()
    app.mainloop()
