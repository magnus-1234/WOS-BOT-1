import json
import logging
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from db.mongo_adapters import BotActivityAdapter, mongo_enabled
except Exception:
    BotActivityAdapter = None
    mongo_enabled = lambda: False

logger = logging.getLogger(__name__)

_RECENT_EVENTS: List[Dict[str, Any]] = []
_RECENT_EVENTS_LOCK = threading.Lock()
_MAX_RECENT_EVENTS = 100

_SQLITE_DB_PATH = Path("data/bot_activity.db")
_SQLITE_CONN: Optional[sqlite3.Connection] = None
_SQLITE_LOCK = threading.Lock()


def _clean(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _init_sqlite() -> None:
    """Initialize SQLite table for bot activity fallback."""
    global _SQLITE_CONN
    try:
        os.makedirs(_SQLITE_DB_PATH.parent, exist_ok=True)
        conn = sqlite3.connect(str(_SQLITE_DB_PATH), check_same_thread=False)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bot_activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow TEXT, event_type TEXT, status TEXT,
                message TEXT, guild_id TEXT, guild_name TEXT,
                alliance_id TEXT, alliance_name TEXT,
                fid TEXT, nickname TEXT, state_id TEXT,
                gift_code TEXT, old_value TEXT, new_value TEXT,
                reason TEXT, details TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bot_activity_created ON bot_activity(created_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bot_activity_workflow ON bot_activity(workflow, event_type)")
        conn.commit()
        _SQLITE_CONN = conn
        logger.info("bot_activity SQLite table initialized at %s", _SQLITE_DB_PATH)
    except Exception as exc:
        logger.warning("Failed to init bot_activity SQLite: %s", exc)


def _write_sqlite(activity: Dict[str, Any]) -> bool:
    """Write activity record to SQLite fallback."""
    if _SQLITE_CONN is None:
        return False
    try:
        with _SQLITE_LOCK:
            _SQLITE_CONN.execute("""
                INSERT INTO bot_activity
                (workflow, event_type, status, message, guild_id, guild_name,
                 alliance_id, alliance_name, fid, nickname, state_id,
                 gift_code, old_value, new_value, reason, details, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                activity.get("workflow"), activity.get("event_type"),
                activity.get("status"), activity.get("message"),
                activity.get("guild_id"), activity.get("guild_name"),
                activity.get("alliance_id"), activity.get("alliance_name"),
                activity.get("fid"), activity.get("nickname"),
                activity.get("state_id"), activity.get("gift_code"),
                str(activity.get("old_value", "")) if activity.get("old_value") is not None else None,
                str(activity.get("new_value", "")) if activity.get("new_value") is not None else None,
                activity.get("reason"),
                json.dumps(activity.get("details") or {}),
                activity.get("created_at", datetime.utcnow()),
            ))
            _SQLITE_CONN.commit()
        return True
    except Exception as exc:
        logger.warning("Failed to write bot_activity to SQLite: %s", exc)
        return False


def _append_to_recent(event: Dict[str, Any]) -> None:
    """Append event to in-memory circular buffer."""
    with _RECENT_EVENTS_LOCK:
        _RECENT_EVENTS.append(event)
        if len(_RECENT_EVENTS) > _MAX_RECENT_EVENTS:
            _RECENT_EVENTS.pop(0)


def get_recent_events(limit: int = 100) -> List[Dict[str, Any]]:
    """Get recent events from in-memory buffer."""
    with _RECENT_EVENTS_LOCK:
        return list(_RECENT_EVENTS[-limit:])


def get_recent_activity_sqlite(limit: int = 100) -> List[Dict[str, Any]]:
    """Get recent activity from SQLite fallback."""
    if _SQLITE_CONN is None:
        return []
    try:
        with _SQLITE_LOCK:
            rows = _SQLITE_CONN.execute("""
                SELECT workflow, event_type, status, message,
                       guild_id, guild_name, alliance_id, alliance_name,
                       fid, nickname, state_id, gift_code,
                       old_value, new_value, reason, details, created_at
                FROM bot_activity
                ORDER BY created_at DESC
                LIMIT ?
            """, (limit,)).fetchall()
        events = []
        for row in rows:
            details_str = row[15] if len(row) > 15 else None
            details = None
            if details_str:
                try:
                    details = json.loads(details_str)
                except Exception:
                    details = {"raw": details_str}
            events.append({
                "id": None,
                "workflow": row[0], "event_type": row[1], "status": row[2],
                "message": row[3],
                "guild_id": row[4], "guild_name": row[5],
                "alliance_id": row[6], "alliance_name": row[7],
                "fid": row[8], "nickname": row[9], "state_id": row[10],
                "gift_code": row[11],
                "old_value": row[12], "new_value": row[13],
                "reason": row[14], "details": details,
                "created_at": row[16] if len(row) > 16 else None,
            })
        return events
    except Exception as exc:
        logger.warning("Failed to read bot_activity from SQLite: %s", exc)
        return []


# Initialize SQLite on module load
_init_sqlite()


async def publish_bot_activity(
    *,
    workflow: str,
    event_type: str,
    status: str = "info",
    message: str,
    guild_id: Any = None,
    guild_name: Any = None,
    alliance_id: Any = None,
    alliance_name: Any = None,
    fid: Any = None,
    nickname: Any = None,
    state_id: Any = None,
    gift_code: Any = None,
    old_value: Any = None,
    new_value: Any = None,
    reason: Any = None,
    details: Optional[Dict[str, Any]] = None,
) -> bool:
    """Publish a structured live activity event without risking bot workflows."""
    activity = {
        "workflow": _clean(workflow) or "system",
        "event_type": _clean(event_type) or "activity",
        "status": _clean(status) or "info",
        "message": str(message or "Bot activity update")[:500],
        "guild_id": _clean(guild_id),
        "guild_name": _clean(guild_name),
        "alliance_id": _clean(alliance_id),
        "alliance_name": _clean(alliance_name),
        "fid": _clean(fid),
        "nickname": _clean(nickname),
        "state_id": _clean(state_id),
        "gift_code": _clean(gift_code),
        "old_value": old_value,
        "new_value": new_value,
        "reason": _clean(reason),
        "details": details or {},
        "created_at": datetime.utcnow(),
    }

    # Always append to in-memory buffer (real-time)
    _append_to_recent(dict(activity))

    # Try MongoDB first
    if mongo_enabled() and BotActivityAdapter is not None:
        try:
            return await BotActivityAdapter.insert_activity_async(activity)
        except Exception as exc:
            logger.warning("Failed to publish bot activity to MongoDB: %s", exc)

    # Fallback to SQLite
    if _write_sqlite(activity):
        return True

    return False
