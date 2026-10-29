"""SQLite storage and schema migration helpers for the bot."""

import datetime
import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any

from config import DB_PATH

logger = logging.getLogger(__name__)
db_lock = threading.RLock()
_connection: sqlite3.Connection | None = None
MAX_TXID_LENGTH = 256


def get_db() -> sqlite3.Connection:
    """Return the process-wide SQLite connection (callers must hold db_lock)."""
    global _connection
    if _connection is None:
        Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        _connection = sqlite3.connect(
            DB_PATH, timeout=30, check_same_thread=False
        )
        _connection.row_factory = sqlite3.Row
        _connection.execute("PRAGMA busy_timeout = 30000")
        _connection.execute("PRAGMA journal_mode = WAL")
        _connection.execute("PRAGMA foreign_keys = ON")
    return _connection


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, declaration: str) -> None:
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def init_db() -> None:
    """Create a fresh schema or migrate the existing schema in place."""
    with db_lock:
        conn = get_db()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                phone TEXT,
                points INTEGER DEFAULT 0,
                referred_by INTEGER,
                join_date TEXT,
                last_seen TEXT,
                is_banned INTEGER DEFAULT 0,
                sub_status TEXT DEFAULT 'none',
                sub_type TEXT DEFAULT '',
                is_blocked INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                sub_type TEXT,
                pay_method TEXT,
                pay_code TEXT,
                phone TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS messages_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                first_name TEXT,
                message TEXT,
                msg_type TEXT DEFAULT 'text',
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS broadcast_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER,
                message TEXT,
                target TEXT,
                sent_count INTEGER DEFAULT 0,
                fail_count INTEGER DEFAULT 0,
                created_at TEXT,
                targets_json TEXT DEFAULT '[]',
                last_index INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending'
            );
        """)
        # Migrate databases created by earlier bot versions or partially initialized.
        for column, declaration in (
            ("is_blocked", "INTEGER DEFAULT 0"),
        ):
            _ensure_column(conn, "users", column, declaration)
        for column, declaration in (
            ("phone", "TEXT"),
            ("updated_at", "TEXT"),
        ):
            _ensure_column(conn, "payments", column, declaration)
        _ensure_column(conn, "messages_log", "msg_type", "TEXT DEFAULT 'text'")
        for column, declaration in (
            ("targets_json", "TEXT DEFAULT '[]'"),
            ("last_index", "INTEGER DEFAULT 0"),
            ("status", "TEXT DEFAULT 'pending'"),
        ):
            _ensure_column(conn, "broadcast_log", column, declaration)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_payments_pay_code ON payments(pay_code)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_broadcast_status ON broadcast_log(status)")
        conn.commit()


def close_db() -> None:
    global _connection
    with db_lock:
        if _connection is not None:
            try:
                _connection.close()
            finally:
                _connection = None


def db_upsert_user(user: Any) -> bool:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with db_lock:
        conn = get_db()
        existing = conn.execute("SELECT user_id FROM users WHERE user_id=?", (user.id,)).fetchone()
        is_new = existing is None
        if is_new:
            conn.execute(
                "INSERT INTO users (user_id,username,first_name,last_name,join_date,last_seen) VALUES (?,?,?,?,?,?)",
                (user.id, user.username or "", user.first_name or "", user.last_name or "", now, now),
            )
        else:
            conn.execute(
                "UPDATE users SET username=?,first_name=?,last_name=?,last_seen=? WHERE user_id=?",
                (user.username or "", user.first_name or "", user.last_name or "", now, user.id),
            )
        conn.commit()
        return is_new


def db_get_stats() -> dict[str, int]:
    with db_lock:
        conn = get_db()
        total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        active = conn.execute("SELECT COUNT(*) FROM users WHERE sub_status='active'").fetchone()[0]
        pending = conn.execute("SELECT COUNT(*) FROM payments WHERE status='pending'").fetchone()[0]
        today = datetime.date.today().isoformat()
        week_ago = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()
        month_ago = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
        new_today = conn.execute("SELECT COUNT(*) FROM users WHERE join_date >= ?", (today,)).fetchone()[0]
        new_week = conn.execute("SELECT COUNT(*) FROM users WHERE join_date >= ?", (week_ago,)).fetchone()[0]
        new_month = conn.execute("SELECT COUNT(*) FROM users WHERE join_date >= ?", (month_ago,)).fetchone()[0]
        return {
            "total": total, "active": active, "pending": pending,
            "new_today": new_today, "new_week": new_week, "new_month": new_month,
        }


def db_log_message(user_id: int, username: str, first_name: str, message: str, msg_type: str = "text") -> None:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with db_lock:
        conn = get_db()
        conn.execute(
            "INSERT INTO messages_log (user_id,username,first_name,message,msg_type,created_at) VALUES (?,?,?,?,?,?)",
            (user_id, username or "", first_name or "", message, msg_type, now),
        )
        conn.commit()


def is_user_banned(user_id: int) -> bool:
    with db_lock:
        result = get_db().execute("SELECT is_banned FROM users WHERE user_id=?", (user_id,)).fetchone()
        return bool(result and result["is_banned"] == 1)


def db_set_banned(user_id: int, banned: bool) -> None:
    with db_lock:
        conn = get_db()
        conn.execute("UPDATE users SET is_banned=? WHERE user_id=?", (int(banned), user_id))
        conn.commit()


def db_insert_payment_if_new(user_id: int, sub_type: str, pay_method: str, pay_code: str) -> tuple[int | None, bool]:
    """Atomically insert a payment unless its transaction code was already submitted."""
    code = pay_code.strip()
    if not code or len(code) > MAX_TXID_LENGTH:
        raise ValueError("Invalid transaction code length")
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with db_lock:
        conn = get_db()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT id FROM payments WHERE pay_code=? COLLATE NOCASE LIMIT 1", (code,)
            ).fetchone()
            if existing:
                conn.commit()
                return None, True
            cursor = conn.execute(
                "INSERT INTO payments (user_id,sub_type,pay_method,pay_code,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                (user_id, sub_type, pay_method, code, "pending", now, now),
            )
            payment_id = cursor.lastrowid
            conn.commit()
            return int(payment_id), False
        except Exception:
            conn.rollback()
            raise


def db_payment_code_exists(pay_code: str) -> bool:
    code = pay_code.strip()
    if not code or len(code) > MAX_TXID_LENGTH:
        return False
    with db_lock:
        return get_db().execute(
            "SELECT 1 FROM payments WHERE pay_code=? COLLATE NOCASE LIMIT 1", (code,)
        ).fetchone() is not None


def db_decide_payment(payment_id: int, user_id: int, approved: bool) -> bool:
    """Apply an admin decision once, and only to a pending payment for that user."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    status = "approved" if approved else "rejected"
    with db_lock:
        conn = get_db()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                "UPDATE payments SET status=?,updated_at=? WHERE id=? AND user_id=? AND status='pending'",
                (status, now, payment_id, user_id),
            )
            changed = cursor.rowcount == 1
            if changed and approved:
                conn.execute(
                    "UPDATE users SET sub_status='active',sub_type='VIP' WHERE user_id=?", (user_id,)
                )
            conn.commit()
            return changed
        except Exception:
            conn.rollback()
            raise


def db_create_broadcast(admin_id: int, message: str, targets: list[int]) -> int:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with db_lock:
        conn = get_db()
        cursor = conn.execute(
            "INSERT INTO broadcast_log (admin_id,message,target,sent_count,fail_count,created_at,targets_json,last_index,status) VALUES (?,?,?,0,0,?,?,0,'running')",
            (admin_id, message, "all", now, json.dumps(targets),),
        )
        conn.commit()
        return int(cursor.lastrowid)


def db_broadcast_progress(broadcast_id: int, last_index: int, sent_count: int, fail_count: int) -> None:
    with db_lock:
        conn = get_db()
        conn.execute(
            "UPDATE broadcast_log SET last_index=?,sent_count=?,fail_count=? WHERE id=?",
            (last_index, sent_count, fail_count, broadcast_id),
        )
        conn.commit()


def db_finish_broadcast(broadcast_id: int) -> None:
    with db_lock:
        conn = get_db()
        conn.execute("UPDATE broadcast_log SET status='completed' WHERE id=?", (broadcast_id,))
        conn.commit()


def db_pending_broadcasts() -> list[sqlite3.Row]:
    with db_lock:
        return get_db().execute(
            "SELECT * FROM broadcast_log WHERE status IN ('running','pending') ORDER BY id"
        ).fetchall()


def db_mark_user_blocked(user_id: int) -> None:
    with db_lock:
        conn = get_db()
        conn.execute("UPDATE users SET is_blocked=1 WHERE user_id=?", (user_id,))
        conn.commit()


def db_broadcast_targets() -> list[int]:
    with db_lock:
        return [
            int(row[0]) for row in get_db().execute(
                "SELECT user_id FROM users WHERE COALESCE(is_blocked,0)=0 ORDER BY user_id"
            ).fetchall()
        ]


def db_get_pending_payments(limit: int = 10) -> list[sqlite3.Row]:
    with db_lock:
        return get_db().execute(
            "SELECT p.*,u.first_name FROM payments p JOIN users u ON p.user_id=u.user_id "
            "WHERE p.status='pending' ORDER BY p.id DESC LIMIT ?", (limit,)
        ).fetchall()


def db_backup_to(destination: str | Path) -> None:
    """Create a consistent SQLite snapshot, including committed WAL contents."""
    with db_lock:
        source = get_db()
        target = sqlite3.connect(str(destination), timeout=30)
        try:
            source.backup(target)
        finally:
            target.close()
