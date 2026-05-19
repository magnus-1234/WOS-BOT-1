import asyncio
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Optional
import logging

try:
    from db.mongo_adapters import (
        mongo_enabled,
        RemindersAdapter,
        AutoRedeemSettingsAdapter,
        AllianceMonitoringAdapter,
        WelcomeChannelAdapter,
        ServerAllianceAdapter,
        AlliancesAdapter
    )
except ImportError:
    mongo_enabled = lambda: False
    RemindersAdapter = None
    AutoRedeemSettingsAdapter = None
    AllianceMonitoringAdapter = None
    WelcomeChannelAdapter = None
    ServerAllianceAdapter = None
    AlliancesAdapter = None

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/guilds", tags=["Guilds"])

class GuildBotSettingsUpdate(BaseModel):
    alliance_name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    state: Optional[int] = Field(default=None, ge=1, le=99999)

@router.get("/{guild_id}/stats")
async def get_guild_stats(guild_id: int, request: Request):
    """Fetch basic stats for a guild."""
    _bot = getattr(request.app.state, 'bot', None)
    stats = {
        "member_count": 0,
        "alliance_count": 0,
        "active_users": 0,
        "channels": 0,
        "categories": 0,
        "roles": 0,
        "icon_url": None,
        "server_age": "Not set",
        "alliance_name": "Not Set",
        "state": "Not Set",
        "active_reminders": 0,
        "auto_redeem_active": False,
        "alliance_monitor_active": False,
        "welcome_active": False
    }
    
    if _bot:
        guild = _bot.get_guild(guild_id)
        if guild:
            stats["member_count"] = guild.member_count
            stats["channels"] = len(guild.text_channels)
            stats["categories"] = len(guild.categories)
            stats["roles"] = len(guild.roles)
            stats["icon_url"] = str(guild.icon.url) if guild.icon else None
            
    if mongo_enabled():
        try:
            # Welcome Active
            welcome_config = await WelcomeChannelAdapter.get_async(guild_id)
            stats["welcome_active"] = bool(welcome_config and welcome_config.get('enabled'))

            # Auto Redeem Active
            ar_settings = await AutoRedeemSettingsAdapter.get_settings_async(guild_id)
            stats["auto_redeem_active"] = bool(ar_settings and ar_settings.get('enabled'))

            # Alliance Monitor Active
            monitors = await AllianceMonitoringAdapter.get_all_monitors_async()
            monitor = next((m for m in monitors if m['guild_id'] == guild_id), None)
            stats["alliance_monitor_active"] = bool(monitor and monitor.get('enabled'))

            # Active Reminders
            reminder_storage = getattr(getattr(_bot, "reminder_system", None), "storage", None) if _bot else None
            if reminder_storage and hasattr(reminder_storage, "get_all_active_reminders"):
                all_reminders = reminder_storage.get_all_active_reminders()
            else:
                all_reminders = RemindersAdapter.get_all_active_reminders()
            stats["active_reminders"] = sum(1 for r in all_reminders if str(r.get('guild_id')) == str(guild_id))

            alliance_doc = None
            try:
                from db.mongo_adapters import _get_db_main_async
                db = await _get_db_main_async()
                alliance_doc = await db[ServerAllianceAdapter.COLL].find_one({'_id': str(guild_id)})
                if not alliance_doc:
                    alliance_doc = await db[ServerAllianceAdapter.COLL].find_one({'id': int(guild_id)})
            except Exception as doc_error:
                logger.warning(f"Could not read server alliance document for {guild_id}: {doc_error}")

            # Alliance Name
            alliance_id = await ServerAllianceAdapter.get_alliance_async(guild_id)
            if alliance_id:
                all_alliances = await AlliancesAdapter.get_all_async()
                target_alliance = next((a for a in all_alliances if str(a.get('alliance_id')) == str(alliance_id)), None)
                if target_alliance and target_alliance.get('name'):
                    stats["alliance_name"] = target_alliance.get('name')
            if stats["alliance_name"] == "Not Set" and alliance_doc and alliance_doc.get("alliance_name"):
                stats["alliance_name"] = alliance_doc.get("alliance_name")
            
            # State
            state = await ServerAllianceAdapter.get_state_async(guild_id)
            if state:
                stats["state"] = state
                try:
                    from cogs.server_age import fetch_server_age_data
                    age_data = await asyncio.wait_for(fetch_server_age_data(int(state)), timeout=18)
                    active_text = age_data.get("active_text")
                    days = age_data.get("days")
                    stats["server_age"] = active_text or (f"{days} days" if days is not None else "Not set")
                except Exception as age_error:
                    logger.warning(f"Could not fetch server age for state {state}: {age_error}")
                    stats["server_age"] = "Unavailable"
        except Exception as e:
            logger.error(f"Error fetching overview stats for {guild_id}: {e}")
            
    return stats

@router.patch("/{guild_id}/bot-settings")
async def update_guild_bot_settings(guild_id: int, request: Request, payload: GuildBotSettingsUpdate):
    if not mongo_enabled():
        raise HTTPException(status_code=503, detail="MongoDB is not enabled.")

    updates = {}
    if payload.alliance_name is not None:
        name = payload.alliance_name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Alliance name cannot be empty.")
        updates["alliance_name"] = name
    if payload.state is not None:
        updates["state"] = int(payload.state)
    if not updates:
        raise HTTPException(status_code=400, detail="Nothing to update.")

    try:
        from datetime import datetime
        from db.mongo_adapters import _get_db_main_async
        db = await _get_db_main_async()
        now = datetime.utcnow().isoformat()
        updates["id"] = int(guild_id)
        updates["updated_at"] = now
        await db[ServerAllianceAdapter.COLL].update_one(
            {"_id": str(guild_id)},
            {"$set": updates, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )

        if "alliance_name" in updates:
            alliance_id = await ServerAllianceAdapter.get_alliance_async(guild_id)
            if alliance_id:
                await db[AlliancesAdapter.COLL].update_one(
                    {"$or": [{"_id": str(alliance_id)}, {"alliance_id": int(alliance_id)}, {"id": int(alliance_id)}]},
                    {"$set": {"name": updates["alliance_name"], "updated_at": now}},
                )

        return {"status": "success"}
    except Exception as e:
        logger.error(f"Failed to update bot settings for guild {guild_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update bot settings.")

@router.get("/{guild_id}/channels")
async def get_guild_channels(guild_id: int, request: Request):
    """Fetch channels for a guild."""
    _bot = getattr(request.app.state, 'bot', None)
    if not _bot:
        return []
        
    guild = _bot.get_guild(guild_id)
    if not guild:
        return []
        
    channels = []
    for channel in guild.text_channels:
        channels.append({
            "id": str(channel.id),
            "name": channel.name,
            "type": 0
        })
    return channels
