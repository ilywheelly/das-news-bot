"""SQLite storage for DAS administration settings and roles."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DB_FILE = Path(__file__).with_name("das.db")
DEFAULT_SETTINGS = {
    "shloka_enabled": "true",
    "shloka_hour": "8",
    "shloka_minute": "0",
    "timezone": "Asia/Tomsk",
}
VALID_ROLES = {"superadmin", "admin"}


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_FILE)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    with _connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS admins (
                telegram_id INTEGER PRIMARY KEY,
                role TEXT NOT NULL CHECK (role IN ('superadmin', 'admin')),
                added_at TEXT,
                added_by INTEGER
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS shloka_schedule (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hour INTEGER NOT NULL CHECK (hour BETWEEN 0 AND 23),
                minute INTEGER NOT NULL CHECK (minute BETWEEN 0 AND 59),
                enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                UNIQUE(hour, minute)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS publication_targets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                target_type TEXT NOT NULL CHECK (target_type IN ('channel', 'group')),
                enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1))
            )
            """
        )
        for key, value in DEFAULT_SETTINGS.items():
            connection.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )
        if connection.execute("SELECT COUNT(*) FROM shloka_schedule").fetchone()[0] == 0:
            connection.execute(
                "INSERT INTO shloka_schedule (hour, minute, enabled) VALUES (8, 0, 1)"
            )
        if connection.execute("SELECT COUNT(*) FROM publication_targets").fetchone()[0] == 0:
            connection.executemany(
                "INSERT INTO publication_targets "
                "(chat_id, title, target_type, enabled) VALUES (?, ?, ?, 1)",
                [
                    ("@t_svt4ok", "Тестовый канал", "channel"),
                    ("@domik_giriraja", "Домик Гирираджа", "channel"),
                ],
            )


def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    with _connect() as connection:
        row = connection.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with _connect() as connection:
        connection.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def get_shloka_settings() -> dict[str, str]:
    return {
        key: get_setting(key, default)
        for key, default in DEFAULT_SETTINGS.items()
    }


def get_admin_role(telegram_id: int) -> Optional[str]:
    with _connect() as connection:
        row = connection.execute(
            "SELECT role FROM admins WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()
    return row["role"] if row else None


def is_admin(telegram_id: int) -> bool:
    return get_admin_role(telegram_id) in VALID_ROLES


def is_superadmin(telegram_id: int) -> bool:
    return get_admin_role(telegram_id) == "superadmin"


def ensure_superadmin(telegram_id: int) -> None:
    """Create or promote the configured account; never downgrade it."""
    with _connect() as connection:
        row = connection.execute(
            "SELECT role FROM admins WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()
        if row is None:
            connection.execute(
                "INSERT INTO admins (telegram_id, role, added_at, added_by) "
                "VALUES (?, 'superadmin', ?, NULL)",
                (telegram_id, datetime.now(timezone.utc).isoformat()),
            )
        elif row["role"] != "superadmin":
            connection.execute(
                "UPDATE admins SET role = 'superadmin' WHERE telegram_id = ?",
                (telegram_id,),
            )


def add_admin(telegram_id: int, added_by: int) -> bool:
    if telegram_id <= 0 or get_admin_role(telegram_id) is not None:
        return False
    with _connect() as connection:
        connection.execute(
            "INSERT INTO admins (telegram_id, role, added_at, added_by) "
            "VALUES (?, 'admin', ?, ?)",
            (telegram_id, datetime.now(timezone.utc).isoformat(), added_by),
        )
    return True


def remove_admin(telegram_id: int) -> bool:
    if get_admin_role(telegram_id) != "admin":
        return False
    with _connect() as connection:
        deleted = connection.execute(
            "DELETE FROM admins WHERE telegram_id = ? AND role = 'admin'",
            (telegram_id,),
        ).rowcount
    return deleted == 1


def list_admins() -> list[dict]:
    with _connect() as connection:
        rows = connection.execute(
            "SELECT telegram_id, role, added_at, added_by FROM admins "
            "ORDER BY role DESC, telegram_id"
        ).fetchall()
    return [dict(row) for row in rows]


def _validate_schedule_time(hour: int, minute: int) -> None:
    if not isinstance(hour, int) or not 0 <= hour <= 23:
        raise ValueError("hour must be between 0 and 23")
    if not isinstance(minute, int) or not 0 <= minute <= 59:
        raise ValueError("minute must be between 0 and 59")


def list_schedule_slots() -> list[dict]:
    with _connect() as connection:
        rows = connection.execute(
            "SELECT id, hour, minute, enabled FROM shloka_schedule "
            "ORDER BY hour, minute, id"
        ).fetchall()
    return [dict(row) for row in rows]


def get_schedule_slot(slot_id: int) -> Optional[dict]:
    with _connect() as connection:
        row = connection.execute(
            "SELECT id, hour, minute, enabled FROM shloka_schedule WHERE id = ?",
            (slot_id,),
        ).fetchone()
    return dict(row) if row else None


def add_schedule_slot(hour: int, minute: int) -> Optional[int]:
    _validate_schedule_time(hour, minute)
    with _connect() as connection:
        try:
            cursor = connection.execute(
                "INSERT INTO shloka_schedule (hour, minute, enabled) VALUES (?, ?, 1)",
                (hour, minute),
            )
        except sqlite3.IntegrityError:
            return None
    return cursor.lastrowid


def update_schedule_slot(slot_id: int, hour: int, minute: int) -> bool:
    _validate_schedule_time(hour, minute)
    with _connect() as connection:
        try:
            updated = connection.execute(
                "UPDATE shloka_schedule SET hour = ?, minute = ? WHERE id = ?",
                (hour, minute, slot_id),
            ).rowcount
        except sqlite3.IntegrityError:
            return False
    return updated == 1


def delete_schedule_slot(slot_id: int) -> bool:
    with _connect() as connection:
        deleted = connection.execute(
            "DELETE FROM shloka_schedule WHERE id = ?", (slot_id,)
        ).rowcount
    return deleted == 1


def set_schedule_slot_enabled(slot_id: int, enabled: bool) -> bool:
    with _connect() as connection:
        updated = connection.execute(
            "UPDATE shloka_schedule SET enabled = ? WHERE id = ?",
            (1 if enabled else 0, slot_id),
        ).rowcount
    return updated == 1


def list_publication_targets() -> list[dict]:
    with _connect() as connection:
        rows = connection.execute(
            "SELECT id, chat_id, title, target_type, enabled "
            "FROM publication_targets ORDER BY id"
        ).fetchall()
    return [dict(row) for row in rows]


def get_publication_target(target_id: int) -> Optional[dict]:
    with _connect() as connection:
        row = connection.execute(
            "SELECT id, chat_id, title, target_type, enabled "
            "FROM publication_targets WHERE id = ?",
            (target_id,),
        ).fetchone()
    return dict(row) if row else None


def add_publication_target(
    chat_id: str,
    title: str,
    target_type: str,
) -> Optional[int]:
    chat_id = chat_id.strip()
    title = title.strip()
    target_type = target_type.strip().lower()
    if not chat_id or not title or target_type not in {"channel", "group"}:
        raise ValueError("invalid publication target")
    with _connect() as connection:
        try:
            cursor = connection.execute(
                "INSERT INTO publication_targets "
                "(chat_id, title, target_type, enabled) VALUES (?, ?, ?, 1)",
                (chat_id, title, target_type),
            )
        except sqlite3.IntegrityError:
            return None
    return cursor.lastrowid


def set_publication_target_enabled(target_id: int, enabled: bool) -> bool:
    with _connect() as connection:
        updated = connection.execute(
            "UPDATE publication_targets SET enabled = ? WHERE id = ?",
            (1 if enabled else 0, target_id),
        ).rowcount
    return updated == 1


def delete_publication_target(target_id: int) -> bool:
    with _connect() as connection:
        deleted = connection.execute(
            "DELETE FROM publication_targets WHERE id = ?", (target_id,)
        ).rowcount
    return deleted == 1
