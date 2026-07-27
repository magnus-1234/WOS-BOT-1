import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request

from admin_utils import format_furnace_level

try:
    from db.mongo_adapters import (
        AllianceEventsAdapter,
        AllianceMembersAdapter,
        AllianceMonitoringAdapter,
        AutoRedeemSettingsAdapter,
        BotActivityAdapter,
        GiftCodesAdapter,
        GiftCodeRedemptionAdapter,
        mongo_enabled,
    )
except ImportError:
    mongo_enabled = lambda: False
    AllianceEventsAdapter = AllianceMembersAdapter = AllianceMonitoringAdapter = AutoRedeemSettingsAdapter = BotActivityAdapter = GiftCodesAdapter = GiftCodeRedemptionAdapter = None

try:
    from gift_codes import get_active_gift_codes
except ImportError:
    try:
        from src.utils.gift_codes import get_active_gift_codes
    except ImportError:
        get_active_gift_codes = None

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/bot-feed", tags=["Bot Feed"])

_CACHE: Dict[str, Any] = {"expires_at": 0.0, "payload": None}
_CACHE_TTL_SECONDS = 3


@router.get("")
@router.get("/")
async def get_bot_feed(request: Request, limit: int = 40):
    """Public, cached overview of bot activity for the marketing frontend."""
    now = time.monotonic()
    if _CACHE["payload"] is not None and now < _CACHE["expires_at"]:
        return _CACHE["payload"]

    safe_limit = max(10, min(int(limit or 40), 100))
    bot = getattr(request.app.state, "bot", None)
    guilds = list(getattr(bot, "guilds", []) or [])
    server_lookup = _guild_name_lookup(guilds)
    gift_codes = await _get_gift_codes(bot)
    members = await _get_recent_members(limit=60)
    monitored_member_count = await _count_monitored_members()
    activity_events = await _get_activity_events(safe_limit)
    events = [*activity_events, *await _build_events(safe_limit, server_lookup, gift_codes)]
    monitors = await _get_monitors()
    auto_redeem_enabled = await _get_auto_redeem_enabled()
    runtime_events, runtime_summary = await _get_runtime_events(bot, server_lookup)
    events = [*runtime_events, *events]
    events.sort(key=_event_sort_key, reverse=True)
    events = events[:safe_limit]
    has_activity = bool(activity_events)
    has_live_process = has_activity or any(event.get("live") for event in runtime_events)

    payload = {
        "status": _runtime_status(bot, runtime_summary),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cache_ttl_seconds": _CACHE_TTL_SECONDS,
        "latest_event": events[0] if events else None,
        "summary": {
            "servers": len(guilds),
            "members": sum((getattr(guild, "member_count", 0) or 0) for guild in guilds),
            "active_monitors": len(monitors),
            "auto_redeem_servers": auto_redeem_enabled,
            "active_gift_codes": len(gift_codes),
            "monitored_members": monitored_member_count,
            "latency_ms": round(bot.latency * 1000) if (bot and bot.latency != float('inf')) else None,
            **runtime_summary,
        },
        "servers": _serialize_guilds(guilds),
        "members": members,
        "events": events,
        "gift_codes": gift_codes,
        "source": "live_activity" if has_activity else ("live_process" if has_live_process else ("live_cache" if events or guilds or gift_codes or members else "idle")),
    }
    _CACHE["payload"] = payload
    _CACHE["expires_at"] = now + _CACHE_TTL_SECONDS
    return payload


async def _get_activity_events(limit: int) -> List[Dict[str, Any]]:
    if not mongo_enabled() or BotActivityAdapter is None:
        return []
    try:
        docs = await BotActivityAdapter.get_recent_activity_async(limit=limit)
    except Exception as exc:
        logger.warning("Unable to load structured bot activity: %s", exc)
        return []
    return [_normalize_activity_event(doc) for doc in docs]


def _normalize_activity_event(doc: Dict[str, Any]) -> Dict[str, Any]:
    workflow = str(doc.get("workflow") or "system")
    event_type = str(doc.get("event_type") or "activity")
    status = str(doc.get("status") or "info")
    type_key = _activity_type_key(workflow, event_type)
    title = _activity_title(workflow, event_type, status)
    timestamp = _iso(doc.get("created_at"))
    details = doc.get("details") if isinstance(doc.get("details"), dict) else {}
    gift_code = doc.get("gift_code")
    old_value = doc.get("old_value")
    new_value = doc.get("new_value")
    return {
        "id": str(doc.get("id") or f"activity-{workflow}-{event_type}-{timestamp}"),
        "type": type_key,
        "workflow": workflow,
        "event_type": event_type,
        "status": status,
        "title": title,
        "message": doc.get("message") or title,
        "player": doc.get("nickname"),
        "fid": str(doc.get("fid") or ""),
        "state": doc.get("state_id"),
        "state_id": doc.get("state_id"),
        "fc_lvl": details.get("furnace_lv") or doc.get("furnace_lv"),
        "server": doc.get("guild_name"),
        "guild_id": doc.get("guild_id"),
        "guild_name": doc.get("guild_name"),
        "alliance_id": doc.get("alliance_id"),
        "alliance_name": doc.get("alliance_name"),
        "gift_code": gift_code,
        "new_value": gift_code if type_key == "redeem" else new_value,
        "old_value": old_value,
        "reason": doc.get("reason"),
        "details": details,
        "timestamp": timestamp,
        "live": _is_recent_iso(timestamp, max_age_seconds=300),
        "priority": _activity_priority(workflow, event_type, status),
        "old_avatar": old_value if type_key == "avatar" else None,
        "new_avatar": new_value if type_key == "avatar" else details.get("avatar_image"),
    }


def _activity_type_key(workflow: str, event_type: str) -> str:
    if workflow == "auto_redeem" or event_type.startswith("redeem_"):
        return "redeem"
    if "furnace" in event_type:
        return "furnace"
    if "nickname" in event_type or "name" in event_type:
        return "name"
    if "avatar" in event_type:
        return "avatar"
    if "state" in event_type:
        return "state"
    if workflow == "alliance_monitor":
        return "monitor"
    return "system"


def _activity_title(workflow: str, event_type: str, status: str) -> str:
    titles = {
        "redeem_success": "Gift code redeemed",
        "redeem_already_claimed": "Gift code already claimed",
        "redeem_failed": "Gift code redeem failed",
        "redeem_rate_limited": "Redeem rate limited",
        "redeem_skipped_cached": "Gift code already in records",
        "server_redeem_started": "Auto redeem started",
        "server_redeem_processing": "Auto redeem processing",
        "server_redeem_completed": "Auto redeem completed",
        "server_redeem_skipped": "Auto redeem skipped",
        "furnace_changed": "Furnace upgrade detected",
        "nickname_changed": "Name change detected",
        "avatar_changed": "Avatar updated",
        "state_changed": "State transfer detected",
    }
    return titles.get(event_type, "Bot activity update" if workflow == "system" else workflow.replace("_", " ").title())


def _activity_priority(workflow: str, event_type: str, status: str) -> int:
    if event_type in {"redeem_success", "redeem_failed", "redeem_rate_limited"}:
        return 120
    if event_type in {"furnace_changed", "nickname_changed", "avatar_changed", "state_changed"}:
        return 115
    if event_type in {"redeem_already_claimed", "redeem_skipped_cached"}:
        return 105
    if workflow == "auto_redeem":
        return 95
    if workflow == "alliance_monitor":
        return 80
    return 30


async def _build_events(limit: int, server_lookup: Dict[str, str], gift_codes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not mongo_enabled():
        return _get_gift_code_events(gift_codes, limit)
    events = []
    if AllianceEventsAdapter is not None:
        raw_events = await AllianceEventsAdapter.get_global_recent_events_async(limit=limit)
        events.extend(_normalize_event(event) for event in raw_events)
    events.extend(await _get_redemption_events(limit, server_lookup))
    events.extend(_get_gift_code_events(gift_codes, limit))
    events.sort(key=lambda item: item.get("timestamp") or "", reverse=True)
    return events[:limit]


async def _get_redemption_events(limit: int, server_lookup: Dict[str, str]) -> List[Dict[str, Any]]:
    if GiftCodeRedemptionAdapter is None:
        return []
    try:
        docs = await GiftCodeRedemptionAdapter.get_recent_redemptions_async(limit=limit)
    except Exception as exc:
        logger.warning("Unable to load redemption feed: %s", exc)
        return []

    events = []
    for doc in docs:
        redemptions = list(doc.get("redemptions") or [])
        latest = redemptions[-1] if redemptions else {}
        guild_id = str(doc.get("guild_id") or "")
        code = str(doc.get("code") or "").strip()
        fid = str(latest.get("fid") or "").strip()
        status = str(latest.get("status") or "processed").replace("_", " ")
        server_name = server_lookup.get(guild_id, "a connected server")
        events.append(
            {
                "id": f"redeem-{doc.get('id')}",
                "type": "redeem",
                "title": "Gift code redeem",
                "message": f"Redeemed code {code} for ID {fid} at {server_name} ({status})",
                "player": "Tracked player",
                "fid": fid,
                "server": server_name,
                "new_value": code,
                "timestamp": _iso(latest.get("redeemed_at") or doc.get("last_redeemed_at") or doc.get("updated_at")),
            }
        )
    return events


def _get_gift_code_events(gift_codes: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    events = []
    for item in gift_codes[: max(0, min(limit, 12))]:
        code = item.get("code")
        if not code:
            continue
        status = str(item.get("status") or "active")
        auto_processed = bool(item.get("auto_redeem_processed"))
        events.append(
            {
                "id": f"gift-code-{code}",
                "type": "gift_code",
                "title": "Active gift code detected",
                "message": f"Gift code {code} is {status}. Auto redeem {'processed' if auto_processed else 'pending'} in bot records.",
                "player": None,
                "fid": "",
                "server": "Global gift-code system",
                "new_value": code,
                "timestamp": _iso(item.get("updated_at")),
            }
        )
    return events


async def _get_gift_codes(bot: Any = None) -> List[Dict[str, Any]]:
    manage_cog = _get_cog(bot, "ManageGiftCode") if bot else None
    if manage_cog and hasattr(manage_cog, "get_active_gift_codes_consolidated"):
        try:
            active_map = await manage_cog.get_active_gift_codes_consolidated(force_refresh=False)
            codes = [
                {
                    "code": str(code or "").strip(),
                    "status": "active",
                    "auto_redeem_processed": False,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "expiry": expiry,
                    "source": "bot_manage_cog",
                }
                for code, expiry in (active_map or {}).items()
            ]
            codes = [code for code in codes if code["code"]]
            if codes:
                return codes
        except Exception as exc:
            logger.warning("Unable to load active gift codes from manage cog: %s", exc)

    if get_active_gift_codes is not None:
        try:
            live_codes = await get_active_gift_codes()
            codes = []
            for item in live_codes or []:
                status = str(item.get("status") or item.get("validation_status") or "active").strip().lower()
                if status != "active" or item.get("is_active") is False:
                    continue
                codes.append(
                    {
                        "code": str(item.get("code") or "").strip(),
                        "status": "active",
                        "auto_redeem_processed": False,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                        "expiry": item.get("expiry") or item.get("expires"),
                        "source": item.get("source") or "bot_scraper",
                    }
                )
            codes = [code for code in codes if code["code"]]
            if codes:
                return codes
        except Exception as exc:
            logger.warning("Unable to load live active gift codes for bot feed: %s", exc)

    if not mongo_enabled() or GiftCodesAdapter is None:
        return []
    try:
        raw_codes = await GiftCodesAdapter.get_all_with_status_async()
    except Exception as exc:
        logger.warning("Unable to load gift codes for bot feed: %s", exc)
        return []

    active_statuses = {"active", "valid", "validated"}
    codes = []
    for item in raw_codes:
        status = str(item.get("validation_status") or "").strip().lower()
        if status not in active_statuses:
            continue
        codes.append(
            {
                "code": str(item.get("giftcode") or "").strip(),
                "status": status,
                "auto_redeem_processed": bool(item.get("auto_redeem_processed", False)),
                "updated_at": _iso(item.get("updated_at") or item.get("created_at") or item.get("date")),
                "source": "bot_database",
            }
        )
    return [code for code in codes if code["code"]]


async def _get_monitors() -> List[Dict[str, Any]]:
    if not mongo_enabled() or AllianceMonitoringAdapter is None:
        return []
    try:
        return await AllianceMonitoringAdapter.get_all_monitors_async()
    except Exception as exc:
        logger.warning("Unable to load monitor count for bot feed: %s", exc)
        return []


async def _count_monitored_members() -> int:
    if not mongo_enabled() or AllianceMembersAdapter is None:
        return 0
    try:
        return await AllianceMembersAdapter.count_members_async()
    except Exception as exc:
        logger.warning("Unable to count monitored members for bot feed: %s", exc)
        return 0


async def _get_recent_members(limit: int = 60) -> List[Dict[str, Any]]:
    if not mongo_enabled() or AllianceMembersAdapter is None:
        return []
    try:
        raw_members = await AllianceMembersAdapter.get_recent_members_async(limit=limit)
    except Exception as exc:
        logger.warning("Unable to load monitored members for bot feed: %s", exc)
        return []

    members = []
    for item in raw_members:
        furnace_lv = item.get("furnace_lv", 0)
        members.append(
            {
                "fid": str(item.get("fid") or ""),
                "nickname": item.get("nickname") or "Unknown player",
                "furnace_lv": furnace_lv,
                "furnace_lv_formatted": _format_furnace(furnace_lv),
                "avatar_image": item.get("avatar_image") or "",
                "state_id": str(item.get("state_id") or ""),
                "alliance_id": item.get("alliance_id") or item.get("alliance"),
                "last_checked": _iso(item.get("last_checked") or item.get("updated_at")),
            }
        )
    return [member for member in members if member["fid"]]


async def _get_auto_redeem_enabled() -> int:
    if not mongo_enabled() or AutoRedeemSettingsAdapter is None:
        return 0
    try:
        settings = await AutoRedeemSettingsAdapter.get_all_enabled_async()
        return len(settings or [])
    except AttributeError:
        try:
            settings = await AutoRedeemSettingsAdapter.get_all_settings_async()
            return sum(1 for item in settings if item.get("enabled"))
        except Exception:
            return 0
    except Exception:
        return 0


def _serialize_guilds(guilds: list) -> List[Dict[str, Any]]:
    serialized = []
    for guild in sorted(guilds, key=lambda g: (getattr(g, "name", "") or "").lower()):
        serialized.append(
            {
                "id": str(getattr(guild, "id", "")),
                "name": getattr(guild, "name", "Unknown server"),
                "members": getattr(guild, "member_count", 0) or 0,
                "icon_url": str(guild.icon.url) if getattr(guild, "icon", None) else None,
            }
        )
    return serialized[:1000]


def _guild_name_lookup(guilds: list) -> Dict[str, str]:
    return {str(getattr(guild, "id", "")): getattr(guild, "name", "connected server") for guild in guilds}


async def _get_runtime_events(bot: Any, server_lookup: Dict[str, str]) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Read live, in-memory bot cog state so the dashboard shows work before DB writes land."""
    log_events, log_summary = _get_log_runtime_events()
    if not bot:
        return log_events, {
            "auto_redeem_queue_depth": 0,
            "auto_redeem_active_jobs": 0,
            "alliance_monitor_running": False,
            **log_summary,
        }

    events: List[Dict[str, Any]] = list(log_events)
    summary = {
        "auto_redeem_queue_depth": 0,
        "auto_redeem_active_jobs": 0,
        "alliance_monitor_running": False,
        **log_summary,
    }

    manage_cog = _get_cog(bot, "ManageGiftCode")
    if manage_cog:
        redeem_events, redeem_summary = _get_auto_redeem_runtime_events(manage_cog, server_lookup)
        events.extend(redeem_events)
        summary.update(redeem_summary)

    alliance_cog = _get_cog(bot, "Alliance")
    if alliance_cog:
        monitor_events, monitor_summary = _get_alliance_runtime_events(alliance_cog)
        events.extend(monitor_events)
        summary.update(monitor_summary)

    return events, summary


def _get_auto_redeem_runtime_events(cog: Any, server_lookup: Dict[str, str]) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    queue = getattr(cog, "auto_redeem_queue", None)
    queue_depth = queue.qsize() if queue is not None and hasattr(queue, "qsize") else 0
    current_jobs = dict(getattr(cog, "current_jobs", {}) or {})
    live_stats = dict(getattr(cog, "_guild_live_stats", {}) or {})
    completed_jobs = list(getattr(cog, "_last_completed_jobs", []) or [])
    events: List[Dict[str, Any]] = []

    for worker_id, job in sorted(current_jobs.items()):
        try:
            guild_id, code = job
        except Exception:
            continue
        code = str(code or "").upper()
        guild_key = str(guild_id)
        stat = live_stats.get((guild_id, code)) or live_stats.get((_safe_int(guild_id), code)) or {}
        total = int(stat.get("total") or 0)
        done = int(stat.get("done") or 0)
        success = int(stat.get("success") or 0)
        already = int(stat.get("already") or 0)
        failed = int(stat.get("failed") or 0)
        rate_limits = int(stat.get("rate_limits") or 0)
        percent = round((done / total) * 100, 1) if total else 0
        server_name = server_lookup.get(guild_key, f"server {guild_key}")
        status_note = "API rate limiting, retrying slower" if rate_limits else "redeeming members now"
        events.append(
            {
                "id": f"auto-redeem-active-{worker_id}-{guild_key}-{code}",
                "type": "redeem",
                "title": "Auto redeem in progress",
                "message": (
                    f"Worker {worker_id} is redeeming {code} for {server_name}: "
                    f"{done}/{total or '?'} players processed ({percent}%). "
                    f"Success {success}, already {already}, failed {failed}. {status_note}."
                ),
                "server": server_name,
                "new_value": code,
                "timestamp": _epoch_iso(stat.get("started_at")) or datetime.now(timezone.utc).isoformat(),
                "live": True,
                "priority": 100,
            }
        )

    if queue_depth and not current_jobs:
        events.append(
            {
                "id": "auto-redeem-queue-waiting",
                "type": "redeem",
                "title": "Auto redeem queued",
                "message": f"{queue_depth} auto-redeem job(s) are waiting for the worker pool.",
                "server": "Global gift-code system",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "live": True,
                "priority": 90,
            }
        )

    if not current_jobs and not queue_depth and completed_jobs:
        last = completed_jobs[-1]
        guild_key = str(last.get("guild_id") or "")
        server_name = server_lookup.get(guild_key, f"server {guild_key}" if guild_key else "a server")
        events.append(
            {
                "id": f"auto-redeem-completed-{guild_key}-{last.get('code')}-{last.get('finished_at')}",
                "type": "redeem",
                "title": "Auto redeem recently completed",
                "message": (
                    f"{last.get('code', 'Gift code')} finished for {server_name}: "
                    f"success {last.get('success', 0)}, already {last.get('already', 0)}, failed {last.get('failed', 0)}."
                ),
                "server": server_name,
                "new_value": last.get("code"),
                "timestamp": _epoch_iso(last.get("finished_at")) or datetime.now(timezone.utc).isoformat(),
                "live": True,
                "priority": 70,
            }
        )

    if not events:
        worker_count = int(getattr(cog, "guild_worker_count", 0) or 0)
        events.append(
            {
                "id": "auto-redeem-workers-ready",
                "type": "redeem",
                "title": "Auto redeem worker ready",
                "message": f"{worker_count or 1} worker(s) are online and waiting for the next gift code.",
                "server": "Global gift-code system",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "live": False,
                "priority": 10,
            }
        )

    return events, {
        "auto_redeem_queue_depth": queue_depth,
        "auto_redeem_active_jobs": len(current_jobs),
    }


def _get_alliance_runtime_events(cog: Any) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    task = getattr(cog, "monitor_alliances", None)
    running = bool(task and getattr(task, "is_running", lambda: False)())
    current = getattr(cog, "_current_scanning_alliance", None)
    stats = getattr(cog, "_last_cycle_stats", {})
    
    events: List[Dict[str, Any]] = []
    
    if running:
        title = "Alliance monitor active"
        message = "Monitoring alliances for profile changes"
        if current:
            alliance_name = current.get("name") or f"ID {current.get('id')}"
            message = f"Currently scanning alliance: {alliance_name}"
            
        events.append(
            {
                "id": "alliance-monitor-running",
                "type": "monitor",
                "title": title,
                "message": message,
                "server": "Alliance system",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "live": True,
                "priority": 90,
            }
        )

    # Add summary
    summary = {
        "alliance_monitor_running": running,
        "alliance_monitor_current": current,
        "alliance_monitor_last_start": _iso(stats.get("start")),
        "alliance_monitor_last_end": _iso(stats.get("end")),
    }

    latest_log = _read_latest_alliance_log(getattr(cog, "log_file", None))
    if latest_log:
        events.append(
            {
                "id": f"alliance-monitor-log-{abs(hash(latest_log.get('message', '')))}",
                "type": "monitor",
                "title": "Alliance monitor log",
                "message": latest_log.get("message"),
                "timestamp": latest_log.get("timestamp"),
                "live": _is_recent_iso(latest_log.get("timestamp"), 300),
                "priority": 40,
            }
        )

    return events, summary


def _get_cog(bot: Any, name: str) -> Any:
    getter = getattr(bot, "get_cog", None)
    if not callable(getter):
        return None
    return getter(name)


def _get_log_runtime_events() -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for item in _read_recent_bot_log_lines(limit=160):
        event = _event_from_log_line(item)
        if event:
            events.append(event)
    events.sort(key=_event_sort_key, reverse=True)
    events = events[:8]
    has_recent_redeem = any(event.get("type") == "redeem" and event.get("live") for event in events)
    has_recent_monitor = any(event.get("type") == "monitor" and event.get("live") for event in events)
    return events, {
        "auto_redeem_log_active": has_recent_redeem,
        "alliance_monitor_log_active": has_recent_monitor,
    }


def _read_recent_bot_log_lines(limit: int = 120) -> List[Dict[str, str]]:
    paths = [
        Path("discordbot-out.log"),
        Path("discordbot-error.log"),
        Path("last_out.txt"),
        Path("bot_logs_final.txt"),
        Path("log") / "alliance_monitoring.txt",
        Path("logs") / "alliance_monitoring.txt",
    ]
    lines: List[Dict[str, str]] = []
    for path in paths:
        full_path = path if path.is_absolute() else Path(os.getcwd()) / path
        if not full_path.exists() or not full_path.is_file():
            continue
        try:
            file_lines = _tail_text_lines(full_path, max_lines=limit)
        except Exception:
            continue
        for line in file_lines:
            cleaned = _strip_ansi(line).strip()
            if cleaned:
                lines.append({"path": str(path), "line": cleaned})
    return lines[-limit:]


def _tail_text_lines(path: Path, max_lines: int = 120, chunk_size: int = 65536) -> List[str]:
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - chunk_size), os.SEEK_SET)
        text = handle.read().decode("utf-8", errors="ignore")
    return text.splitlines()[-max_lines:]


def _event_from_log_line(item: Dict[str, str]) -> Optional[Dict[str, Any]]:
    line = item.get("line") or ""
    lower = line.lower()
    timestamp = _parse_any_log_timestamp(line)
    is_recent = _is_recent_iso(timestamp, max_age_seconds=900)

    if "auto-redeem" in lower or "auto redeem" in lower or "auto_redeem" in lower:
        title = "Auto redeem log update"
        priority = 75
        live = is_recent
        if "processing queued auto-redeem" in lower or "locked auto-redeem" in lower or "started auto-redeem" in lower or "triggering auto-redeem" in lower:
            title = "Auto redeem in progress"
            priority = 85
        elif "completed" in lower or "unlocked auto-redeem" in lower:
            title = "Auto redeem recently completed"
            priority = 65
        elif "no guilds have auto-redeem enabled" in lower or "critical" in lower:
            title = "Auto redeem needs attention"
            priority = 80
        return {
            "id": f"log-redeem-{abs(hash(line))}",
            "type": "redeem",
            "title": title,
            "message": _clean_log_message(line),
            "server": "Bot log",
            "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
            "live": live,
            "priority": priority,
        }

    if (
        "alliance monitoring cycle" in lower
        or "monitoring " in lower and "alliance" in lower
        or "batch processing complete" in lower
        or "detected " in lower and " changes for alliance" in lower
    ):
        return {
            "id": f"log-monitor-{abs(hash(line))}",
            "type": "monitor",
            "title": "Alliance monitor cycle active" if is_recent else "Alliance monitor log update",
            "message": _clean_log_message(line),
            "server": "Alliance monitor log",
            "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
            "live": is_recent,
            "priority": 60 if is_recent else 35,
        }

    return None


def _strip_ansi(value: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", value)


def _clean_log_message(value: str) -> str:
    cleaned = _strip_ansi(value)
    cleaned = re.sub(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}:\s*", "", cleaned)
    cleaned = re.sub(r"^\[[^\]]+\]\s*", "", cleaned)
    cleaned = re.sub(r"^[^\[]*\[(INFO|WARNING|ERROR|DEBUG)\]\s*[^:]+:\s*", "", cleaned)
    cleaned = re.sub(r"^[✅❌⚠️🔒🔓📊🔧ℹ️]+\s*", "", cleaned).strip()
    return cleaned[:240]


def _parse_any_log_timestamp(value: str) -> Optional[str]:
    match = re.search(r"(?P<iso>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", value)
    if match:
        try:
            return datetime.fromisoformat(match.group("iso")).replace(tzinfo=timezone.utc).isoformat()
        except Exception:
            pass
    match = re.search(r"\[(?P<bracket>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]", value)
    if match:
        return _parse_log_timestamp(match.group("bracket"))
    return None


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except Exception:
        return None


def _read_latest_alliance_log(log_file: Optional[str]) -> Optional[Dict[str, str]]:
    if not log_file:
        return None
    try:
        path = Path(log_file)
        if not path.is_absolute():
            path = Path(os.getcwd()) / path
        if not path.exists():
            return None
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return None
    for line in reversed(lines[-80:]):
        match = re.match(r"^\[(?P<ts>[^\]]+)\]\s*(?P<msg>.+)$", line.strip())
        if not match:
            continue
        timestamp = _parse_log_timestamp(match.group("ts"))
        return {
            "timestamp": timestamp,
            "message": match.group("msg"),
        }
    return None


def _parse_log_timestamp(value: str) -> Optional[str]:
    try:
        dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=timezone.utc).isoformat()
    except Exception:
        return None


def _epoch_iso(value: Any) -> Optional[str]:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat()
    except Exception:
        return None


def _is_recent_iso(value: Optional[str], max_age_seconds: int) -> bool:
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() <= max_age_seconds
    except Exception:
        return False


def _runtime_status(bot: Any, runtime_summary: Dict[str, Any]) -> str:
    if runtime_summary.get("auto_redeem_active_jobs"):
        return "redeeming"
    if runtime_summary.get("auto_redeem_queue_depth"):
        return "queued"
    if runtime_summary.get("auto_redeem_log_active"):
        return "redeeming"
    if runtime_summary.get("alliance_monitor_running"):
        return "monitoring"
    if runtime_summary.get("alliance_monitor_log_active"):
        return "monitoring"
    return "online" if bot and getattr(bot, "is_ready", lambda: False)() else "warming"


def _event_sort_key(event: Dict[str, Any]) -> tuple[int, str]:
    try:
        priority = int(event.get("priority") or 0)
    except Exception:
        priority = 0
    return priority, str(event.get("timestamp") or "")


def _normalize_event(event: Dict[str, Any]) -> Dict[str, Any]:
    event_type = str(event.get("type") or "event").replace("_change", "")
    old_value = event.get("old_value")
    new_value = event.get("new_value")
    title = "Profile updated"
    message = "updated their profile"

    if event_type == "furnace":
        old_level = _format_furnace(old_value)
        new_level = _format_furnace(new_value)
        title = "Furnace upgrade detected"
        if old_level and new_level:
            message = f"upgraded FC from {old_level} to {new_level}"
        elif new_level:
            message = f"reached {new_level}"
    elif event_type == "name":
        title = "Name change detected"
        message = f"changed name from {old_value or 'Unknown'} to {new_value or 'Unknown'}"
    elif event_type == "avatar":
        title = "Avatar change detected"
        message = "changed avatar"
    elif event_type == "state":
        title = "State transfer detected"
        message = f"moved from State {old_value or '?'} to State {new_value or '?'}"

    return {
        "id": str(event.get("id") or f"{event_type}-{event.get('fid', '')}-{event.get('timestamp', '')}"),
        "type": event_type,
        "title": title,
        "message": message,
        "player": event.get("nickname") or "Unknown player",
        "fid": str(event.get("fid") or ""),
        "alliance_id": event.get("alliance_id"),
        "timestamp": _iso(event.get("timestamp")),
        "old_value": old_value,
        "new_value": new_value,
        "old_avatar": old_value if event_type == "avatar" else None,
        "new_avatar": new_value if event_type == "avatar" else event.get("avatar_image"),
    }


def _format_furnace(value: Optional[Any]) -> Optional[str]:
    if value is None or value == "":
        return None
    try:
        return format_furnace_level(int(value))
    except Exception:
        return str(value)


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value)
