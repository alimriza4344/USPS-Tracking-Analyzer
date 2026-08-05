"""
exporter.py
Writes the final Scanned.xlsx, No_Scan.xlsx, Failed.xlsx, Summary_Report.xlsx,
and Remaining_Tracking.xlsx output files.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List

import pandas as pd

from logger import get_logger
from tracker import TrackingResult, TrackingStatus
from utils import format_duration

logger = get_logger(__name__)


@dataclass
class RunSummary:
    total_imported: int
    duplicates_removed: int
    invalid_removed: int
    processed: int
    scanned: int
    no_scan: int
    failed: int
    processing_time_seconds: float

    @property
    def success_rate(self) -> float:
        if self.processed == 0:
            return 0.0
        return round((self.scanned + self.no_scan) / self.processed * 100, 2)


class ResultExporter:
    """Collects TrackingResults as they arrive and writes final Excel reports."""

    def __init__(self, output_folder: str):
        self.output_dir = Path(output_folder)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.scanned: List[TrackingResult] = []
        self.no_scan: List[TrackingResult] = []
        self.failed: List[TrackingResult] = []

    def add_result(self, result: TrackingResult) -> None:
        if result.status == TrackingStatus.SCANNED:
            self.scanned.append(result)
        elif result.status == TrackingStatus.NO_SCAN:
            self.no_scan.append(result)
        else:
            self.failed.append(result)

    def export_all(self, summary: RunSummary, remaining_numbers: List[str] = None) -> dict:
        """Write all output files and return their paths."""
        paths = {
            "scanned": self._export_scanned(),
            "no_scan": self._export_no_scan(),
            "failed": self._export_failed(),
            "summary": self._export_summary(summary),
        }
        if remaining_numbers:
            paths["remaining"] = self._export_remaining(remaining_numbers)

        logger.info("Exported results to %s", self.output_dir)
        return paths

    def _export_scanned(self) -> Path:
        path = self.output_dir / "Scanned.xlsx"
        rows = [
            {
                "Tracking Number": r.tracking_number,
                "Status": r.status.value,
                "Latest USPS Status": r.latest_status_text,
                "Latest Event Date": r.latest_event_date,
                "Latest Location": r.latest_location,
            }
            for r in self.scanned
        ]
        df = pd.DataFrame(rows, columns=[
            "Tracking Number", "Status", "Latest USPS Status", "Latest Event Date", "Latest Location"
        ])
        df.to_excel(path, index=False)
        return path

    def _export_no_scan(self) -> Path:
        path = self.output_dir / "No_Scan.xlsx"
        rows = [
            {"Tracking Number": r.tracking_number, "Reason": r.reason or "No scan events found"}
            for r in self.no_scan
        ]
        df = pd.DataFrame(rows, columns=["Tracking Number", "Reason"])
        df.to_excel(path, index=False)
        return path

    def _export_failed(self) -> Path:
        path = self.output_dir / "Failed.xlsx"
        rows = [
            {
                "Tracking Number": r.tracking_number,
                "Error": r.error or "Unknown error",
                "Retry Count": r.retry_count,
            }
            for r in self.failed
        ]
        df = pd.DataFrame(rows, columns=["Tracking Number", "Error", "Retry Count"])
        df.to_excel(path, index=False)
        return path

    def _export_summary(self, summary: RunSummary) -> Path:
        path = self.output_dir / "Summary_Report.xlsx"
        rows = [
            {"Metric": "Total Imported", "Value": summary.total_imported},
            {"Metric": "Duplicates Removed", "Value": summary.duplicates_removed},
            {"Metric": "Invalid Rows Removed", "Value": summary.invalid_removed},
            {"Metric": "Processed", "Value": summary.processed},
            {"Metric": "Scanned", "Value": summary.scanned},
            {"Metric": "No Scan", "Value": summary.no_scan},
            {"Metric": "Failed", "Value": summary.failed},
            {"Metric": "Processing Time", "Value": format_duration(summary.processing_time_seconds)},
            {"Metric": "Success Rate", "Value": f"{summary.success_rate}%"},
        ]
        df = pd.DataFrame(rows)
        df.to_excel(path, index=False)
        return path

    def _export_remaining(self, remaining_numbers: List[str]) -> Path:
        path = self.output_dir / "Remaining_Tracking.xlsx"
        df = pd.DataFrame(remaining_numbers, columns=["Tracking Number"])
        df.to_excel(path, index=False)
        return path
