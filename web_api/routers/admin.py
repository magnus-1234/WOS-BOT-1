from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import logging
import os
import sqlite3

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

try:
    from db.mongo_adapters import (
        AdminsAdapter,
        PendingConfigAdapter,
        ServerLimitsAdapter,
        _get_db_main_async,
        mongo_enabled,
    )
except ImportError:
    AdminsAdapter = None
    PendingConfigAdapter = None
    ServerLimitsAdapter = None
    _get_db_main_async = None
    mongo_enabled = lambda: False


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["Admin"])

BOT_OWNER_ID = os.getenv("BOT_OWNER_ID", "")
SETTINGS_DB = Path("db/settings.sqlite")


class LimitPayload(BaseModel):
    max_auto_redeem_members: int = Field(-1, ge=-1, le=100000)
    alliance_monitor_locked: bool = False


class LockPayload(BaseModel):
    locked: bool = False
    feature_locked: bool = False


class AdminPayload(BaseModel):
    user_id: str = Field(..., min_length=3, max_length=32)
    is_global: bool = False


class ReviewPayload(BaseModel):
    action: str


async def _discord_user_from_request(request: Request) -> Dict[str, Any]:
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    async with httpx.AsyncClient(timeout=10) as client:
        res = await client.get(
            "https://discord.com/api/users/@me",
            headers={"Authorization": auth_header},
        )
    if res.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid Discord token")
    return res.json()


def _sqlite_global_admin(user_id: int) -> bool:
    try:
        with sqlite3.connect(SETTINGS_DB) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT is_initial FROM admin WHERE id = ?", (user_id,))
            row = cursor.fetchone()
            return bool(row and int(row[0]) == 1)
    except Exception as exc:
        logger.warning("SQLite global admin check failed: %s", exc)
        return False


async def _is_global_admin_id(user_id: int) -> bool:
    if BOT_OWNER_ID and str(user_id) == str(BOT_OWNER_ID):
        return True

    if AdminsAdapter:
        try:
            doc = await AdminsAdapter.get_async(user_id)
            if doc and int(doc.get("is_initial", 0)) == 1:
                return True
        except Exception as exc:
            logger.warning("Mongo global admin check failed: %s", exc)

    return _sqlite_global_admin(user_id)


async def _require_global_admin(request: Request) -> Dict[str, Any]:
    user = await _discord_user_from_request(request)
    user_id = int(user["id"])
    if not await _is_global_admin_id(user_id):
        raise HTTPException(status_code=403, detail="Global administrator access required")
    return user


def _serialize_doc(doc: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not doc:
        return {}
    safe = {}
    for key, value in doc.items():
        if key == "_id":
            safe["id"] = str(value)
        elif isinstance(value, datetime):
            safe[key] = value.isoformat()
        else:
            safe[key] = value
    return safe


def _get_sqlite_locks() -> Dict[str, Dict[str, Any]]:
    locks: Dict[str, Dict[str, Any]] = {}
    try:
        with sqlite3.connect(SETTINGS_DB) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS server_locks (
                    guild_id INTEGER PRIMARY KEY,
                    locked INTEGER DEFAULT 0,
                    feature_locked INTEGER DEFAULT 0,
                    locked_by INTEGER,
                    locked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute("SELECT guild_id, locked, feature_locked, locked_by, locked_at FROM server_locks")
            for guild_id, locked, feature_locked, locked_by, locked_at in cursor.fetchall():
                locks[str(guild_id)] = {
                    "locked": bool(locked),
                    "feature_locked": bool(feature_locked),
                    "locked_by": str(locked_by) if locked_by else "",
                    "locked_at": locked_at,
                }
    except Exception as exc:
        logger.warning("Failed to read server_locks: %s", exc)
    return locks


def _set_sqlite_lock(guild_id: int, payload: LockPayload, admin_id: int) -> None:
    locked = bool(payload.locked)
    feature_locked = bool(payload.feature_locked) and not locked
    with sqlite3.connect(SETTINGS_DB) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS server_locks (
                guild_id INTEGER PRIMARY KEY,
                locked INTEGER DEFAULT 0,
                feature_locked INTEGER DEFAULT 0,
                locked_by INTEGER,
                locked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            INSERT OR REPLACE INTO server_locks
            (guild_id, locked, feature_locked, locked_by, locked_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (guild_id, int(locked), int(feature_locked), admin_id),
        )
        conn.commit()


async def _list_admins() -> List[Dict[str, Any]]:
    admins: Dict[str, Dict[str, Any]] = {}
    if mongo_enabled() and _get_db_main_async:
        try:
            db = await _get_db_main_async()
            cursor = db[AdminsAdapter.COLL].find({})
            docs = await cursor.to_list(length=None)
            for doc in docs:
                admin_id = str(doc.get("_id"))
                admins[admin_id] = {
                    "id": admin_id,
                    "is_global": int(doc.get("is_initial", 0)) == 1,
                    "source": "mongo",
                    "updated_at": doc.get("updated_at"),
                }
        except Exception as exc:
            logger.warning("Failed to list Mongo admins: %s", exc)

    try:
        with sqlite3.connect(SETTINGS_DB) as conn:
            cursor = conn.cursor()
            cursor.execute("CREATE TABLE IF NOT EXISTS admin (id INTEGER PRIMARY KEY, is_initial INTEGER)")
            cursor.execute("SELECT id, is_initial FROM admin")
            for admin_id, is_initial in cursor.fetchall():
                key = str(admin_id)
                admins.setdefault(
                    key,
                    {"id": key, "is_global": int(is_initial) == 1, "source": "sqlite", "updated_at": None},
                )
    except Exception as exc:
        logger.warning("Failed to list SQLite admins: %s", exc)

    return sorted(admins.values(), key=lambda item: (not item["is_global"], item["id"]))


async def _pending_registrations() -> List[Dict[str, Any]]:
    if not mongo_enabled() or not PendingConfigAdapter:
        return []
    docs = await PendingConfigAdapter.get_all_pending_async()
    return [
        {
            "guild_id": str(doc.get("guild_id", "")),
            "guild_name": doc.get("guild_name", ""),
            "alliance_name": doc.get("alliance_name", ""),
            "state": doc.get("state"),
            "discord_user_id": str(doc.get("discord_user_id", "")),
            "discord_username": doc.get("discord_username", ""),
            "submitted_at": doc.get("submitted_at", ""),
        }
        for doc in docs
    ]


@router.get("/me")
async def admin_me(request: Request):
    user = await _discord_user_from_request(request)
    is_global = await _is_global_admin_id(int(user["id"]))
    return {"user": user, "is_global_admin": is_global}


@router.get("/overview")
async def admin_overview(request: Request):
    user = await _require_global_admin(request)
    bot = getattr(request.app.state, "bot", None)
    guilds = []
    if bot:
        for guild in bot.guilds:
            guilds.append(
                {
                    "id": str(guild.id),
                    "name": guild.name,
                    "member_count": guild.member_count or 0,
                    "icon_url": str(guild.icon.url) if guild.icon else "",
                }
            )

    limits = {}
    if mongo_enabled() and ServerLimitsAdapter:
        for item in await ServerLimitsAdapter.get_all_async():
            limits[str(item.get("guild_id"))] = item

    locks = _get_sqlite_locks()
    admins = await _list_admins()
    pending = await _pending_registrations()

    for guild in guilds:
        guild["limits"] = limits.get(guild["id"], {})
        guild["lock"] = locks.get(guild["id"], {"locked": False, "feature_locked": False})
        guild["manage_url"] = f"manage.html?id={guild['id']}"

    return {
        "user": {"id": user["id"], "username": user.get("username"), "global_name": user.get("global_name")},
        "stats": {
            "servers": len(guilds),
            "members": sum(g["member_count"] for g in guilds),
            "custom_limits": len(limits),
            "locked_servers": sum(1 for item in locks.values() if item.get("locked")),
            "feature_locked_servers": sum(1 for item in locks.values() if item.get("feature_locked")),
            "pending_registrations": len(pending),
            "admins": len(admins),
        },
        "servers": sorted(guilds, key=lambda item: item["name"].lower()),
        "admins": admins,
        "pending": pending,
        "settings_catalog": [
            {"name": "Welcome", "tab": "welcome", "scope": "server"},
            {"name": "Alliance Monitor", "tab": "alliance", "scope": "server"},
            {"name": "Auto-Translation", "tab": "translate", "scope": "server"},
            {"name": "Birthday Settings", "tab": "birthday", "scope": "server"},
            {"name": "Gift Codes & Auto-Redeem", "tab": "giftcodes", "scope": "server"},
            {"name": "Reminders", "tab": "reminders", "scope": "server"},
            {"name": "Server Limits", "tab": "global-limits", "scope": "global"},
            {"name": "Bot Locks", "tab": "global-locks", "scope": "global"},
            {"name": "Administrator Access", "tab": "global-admins", "scope": "global"},
            {"name": "Registration Review", "tab": "global-registration", "scope": "global"},
        ],
    }


@router.get("/servers/{guild_id}")
async def admin_server_detail(guild_id: int, request: Request):
    await _require_global_admin(request)
    limits = await ServerLimitsAdapter.get_async(guild_id) if ServerLimitsAdapter and mongo_enabled() else None
    lock = _get_sqlite_locks().get(str(guild_id), {"locked": False, "feature_locked": False})
    return {"guild_id": str(guild_id), "limits": _serialize_doc(limits), "lock": lock}


@router.post("/servers/{guild_id}/limits")
async def admin_set_limits(guild_id: int, payload: LimitPayload, request: Request):
    user = await _require_global_admin(request)
    if not mongo_enabled() or not ServerLimitsAdapter:
        raise HTTPException(status_code=500, detail="MongoDB not available")
    ok = await ServerLimitsAdapter.set_async(
        guild_id,
        {
            "max_auto_redeem_members": payload.max_auto_redeem_members,
            "alliance_monitor_locked": payload.alliance_monitor_locked,
            "updated_by": int(user["id"]),
        },
    )
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to save limits")
    return {"status": "success"}


@router.delete("/servers/{guild_id}/limits")
async def admin_reset_limits(guild_id: int, request: Request):
    await _require_global_admin(request)
    if not mongo_enabled() or not ServerLimitsAdapter:
        raise HTTPException(status_code=500, detail="MongoDB not available")
    await ServerLimitsAdapter.delete_async(guild_id)
    return {"status": "success"}


@router.post("/servers/{guild_id}/lock")
async def admin_set_lock(guild_id: int, payload: LockPayload, request: Request):
    user = await _require_global_admin(request)
    _set_sqlite_lock(guild_id, payload, int(user["id"]))
    return {"status": "success"}


@router.post("/admins")
async def admin_upsert(payload: AdminPayload, request: Request):
    await _require_global_admin(request)
    user_id = int(payload.user_id)
    is_initial = 1 if payload.is_global else 0
    if AdminsAdapter:
        await AdminsAdapter.upsert_async(user_id, is_initial)
    with sqlite3.connect(SETTINGS_DB) as conn:
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS admin (id INTEGER PRIMARY KEY, is_initial INTEGER)")
        cursor.execute("INSERT OR REPLACE INTO admin (id, is_initial) VALUES (?, ?)", (user_id, is_initial))
        conn.commit()
    return {"status": "success"}


@router.post("/registrations/{guild_id}/review")
async def admin_review_registration(guild_id: int, payload: ReviewPayload, request: Request):
    user = await _require_global_admin(request)
    if payload.action not in {"approve", "deny"}:
        raise HTTPException(status_code=400, detail="Action must be approve or deny")
    if not mongo_enabled() or not PendingConfigAdapter:
        raise HTTPException(status_code=500, detail="MongoDB not available")
    if payload.action == "approve":
        ok = await PendingConfigAdapter.approve_async(guild_id, int(user["id"]))
    else:
        ok = await PendingConfigAdapter.deny_async(guild_id, int(user["id"]))
    if not ok:
        raise HTTPException(status_code=404, detail="No pending registration found")
    return {"status": "success", "action": payload.action}
