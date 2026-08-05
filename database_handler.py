"""
database_handler.py
Handles persistent storage of tracking results using SQLite.
Allows the application to resume progress after a crash or restart.
"""

import sqlite3
from pathlib import Path
from typing import List, Dict, Any, Optional

class DatabaseHandler:
    def __init__(self, db_path: str = "config/progress.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(exist_ok=True)
        self._init_db()

    def _get_connection(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS results (
                    tracking_number TEXT PRIMARY KEY,
                    status TEXT,
                    latest_status_text TEXT,
                    reason TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS session_info (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)

    def save_result(self, tracking_number: str, status: str, status_text: str, reason: str):
        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO results (tracking_number, status, latest_status_text, reason)
                VALUES (?, ?, ?, ?)
            """, (tracking_number, status, status_text, reason))

    def get_all_results(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM results")
            return [dict(row) for row in cursor.fetchall()]

    def get_processed_count(self) -> int:
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM results")
            return cursor.fetchone()[0]

    def is_processed(self, tracking_number: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT 1 FROM results WHERE tracking_number = ?", (tracking_number,))
            return cursor.fetchone() is not None

    def clear_data(self):
        with self._get_connection() as conn:
            conn.execute("DELETE FROM results")
            conn.execute("DELETE FROM session_info")

    def save_session_path(self, file_path: str):
        with self._get_connection() as conn:
            conn.execute("INSERT OR REPLACE INTO session_info (key, value) VALUES ('last_file', ?)", (file_path,))

    def get_session_path(self) -> Optional[str]:
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT value FROM session_info WHERE key = 'last_file'")
            row = cursor.fetchone()
            return row[0] if row else None
