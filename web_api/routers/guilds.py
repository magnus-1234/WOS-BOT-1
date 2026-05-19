from fastapi import APIRouter, HTTPException, Depends, Request
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
        "server_age": "Unknown",
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
            if hasattr(guild, 'created_at') and guild.created_at:
                stats["server_age"] = guild.created_at.strftime("%Y-%m-%d")
            
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
            all_reminders = RemindersAdapter.get_all_active_reminders()
            stats["active_reminders"] = sum(1 for r in all_reminders if str(r.get('guild_id')) == str(guild_id))

            # Alliance Name
            alliance_id = await ServerAllianceAdapter.get_alliance_async(guild_id)
            if alliance_id:
                all_alliances = await AlliancesAdapter.get_all_async()
                target_alliance = next((a for a in all_alliances if str(a.get('alliance_id')) == str(alliance_id)), None)
                if target_alliance and target_alliance.get('name'):
                    stats["alliance_name"] = target_alliance.get('name')
            
            # State
            state = await ServerAllianceAdapter.get_state_async(guild_id)
            if state:
                stats["state"] = state
        except Exception as e:
            logger.error(f"Error fetching overview stats for {guild_id}: {e}")
            
    return stats

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
