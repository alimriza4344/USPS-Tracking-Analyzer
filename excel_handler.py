"""
excel_handler.py
Handles importing tracking numbers from .xlsx / .xls / .csv files, including
automatic column detection, deduplication, and cleanup of invalid rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List

import pandas as pd

from logger import get_logger
from utils import clean_tracking_number, is_plausible_tracking_number

logger = get_logger(__name__)


class ExcelImportError(Exception):
    """Raised when an input file cannot be read or contains no usable data."""


@dataclass
class ImportResult:
    tracking_numbers: List[str]
    total_rows_read: int
    duplicates_removed: int
    invalid_rows_removed: int
    source_column: str


# Column header hints used to auto-detect the right column when there are
# multiple columns and the tracking numbers aren't in column A.
_HEADER_HINTS = [
    "tracking", "tracking number", "tracking#", "tracking_no",
    "usps", "usps tracking", "tracking id", "trackingnumber",
]


def _score_column_by_header(col_name: str) -> int:
    name = str(col_name).strip().lower()
    for hint in _HEADER_HINTS:
        if hint == name:
            return 100
    for hint in _HEADER_HINTS:
        if hint in name:
            return 50
    return 0


def _score_column_by_content(series: pd.Series) -> int:
    """Score a column based on how many of its values look like tracking numbers."""
    sample = series.dropna().astype(str).head(200)
    if sample.empty:
        return 0
    matches = sum(1 for v in sample if is_plausible_tracking_number(clean_tracking_number(v)))
    return int((matches / len(sample)) * 100)


def _detect_tracking_column(df: pd.DataFrame) -> str:
    """
    Pick the best column for tracking numbers using a combination of header
    name matching and content pattern matching. Falls back to column A.
    """
    if df.empty or len(df.columns) == 0:
        raise ExcelImportError("The input file has no columns / is empty.")

    # If there's only one column, use it -- this also covers the common
    # "tracking numbers just in column A, no header" case.
    if len(df.columns) == 1:
        return df.columns[0]

    scores = {}
    for col in df.columns:
        header_score = _score_column_by_header(col)
        content_score = _score_column_by_content(df[col])
        scores[col] = header_score + content_score

    best_col = max(scores, key=scores.get)
    logger.info("Auto-detected tracking number column: '%s' (scores=%s)", best_col, scores)
    return best_col


def _read_any(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    try:
        if suffix == ".csv":
            return pd.read_csv(path, dtype=str, header=None, engine="python")
        elif suffix in (".xlsx", ".xls"):
            # Try reading without assuming headers first, then fix up below
            return pd.read_excel(path, dtype=str, header=None)
        else:
            raise ExcelImportError(f"Unsupported file type: {suffix}. Use .xlsx, .xls, or .csv.")
    except ExcelImportError:
        raise
    except Exception as exc:  # noqa: BLE001 - surface all read errors to the caller
        raise ExcelImportError(f"Failed to read input file: {exc}") from exc


def _promote_header_if_present(df: pd.DataFrame) -> pd.DataFrame:
    """
    If the first row looks like a text header (e.g. 'Tracking Number') rather
    than actual tracking numbers, promote it to be the column header.
    """
    if df.empty:
        return df
    first_row = df.iloc[0].astype(str)
    # Only promote if EVERY cell in the first row fails the tracking-number pattern
    # AND at least one cell contains a recognizable header keyword -- this avoids
    # accidentally eating a real data row.
    header_keyword_present = any(
        any(hint in str(v).strip().lower() for hint in _HEADER_HINTS) for v in first_row
    )
    all_non_matching = all(not is_plausible_tracking_number(clean_tracking_number(v)) for v in first_row)

    if header_keyword_present and all_non_matching:
        df = df[1:].reset_index(drop=True)
        df.columns = first_row.values
    else:
        df.columns = [f"col_{i}" for i in range(len(df.columns))]
    return df


def import_tracking_numbers(file_path: str) -> ImportResult:
    """
    Read an input file and return a cleaned, deduplicated list of tracking
    numbers along with import statistics.
    """
    path = Path(file_path)
    if not path.exists():
        raise ExcelImportError(f"File not found: {file_path}")

    raw_df = _read_any(path)
    if raw_df.empty:
        raise ExcelImportError("The input file appears to be empty.")

    df = _promote_header_if_present(raw_df)
    total_rows_read = len(df)

    column = _detect_tracking_column(df)
    raw_values = df[column].tolist()

    cleaned = [clean_tracking_number(v) for v in raw_values]
    non_empty = [v for v in cleaned if v]

    invalid_rows_removed = 0
    valid_numbers = []
    for v in non_empty:
        if is_plausible_tracking_number(v):
            valid_numbers.append(v)
        else:
            invalid_rows_removed += 1

    seen = set()
    deduped = []
    duplicates_removed = 0
    for num in valid_numbers:
        if num in seen:
            duplicates_removed += 1
            continue
        seen.add(num)
        deduped.append(num)

    if not deduped:
        raise ExcelImportError(
            "No valid tracking numbers were found in the file. "
            "Please check the file format and try again."
        )

    logger.info(
        "Import complete: %d rows read, %d valid, %d duplicates removed, %d invalid removed",
        total_rows_read, len(deduped), duplicates_removed, invalid_rows_removed,
    )

    return ImportResult(
        tracking_numbers=deduped,
        total_rows_read=total_rows_read,
        duplicates_removed=duplicates_removed,
        invalid_rows_removed=invalid_rows_removed,
        source_column=str(column),
    )
