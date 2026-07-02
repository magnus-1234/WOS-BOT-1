from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
import logging
from typing import List, Optional, Dict, Any, Union
from datetime import datetime, timedelta
from src.bot.admin_utils import format_furnace_level
from src.api.wos_api import fetch_player_info

try:
    from db.mongo_adapters import (
        AllianceMonitoringAdapter, 
        AllianceMembersAdapter, 
        FurnaceHistoryAdapter, 
        AlliancesAdapter,
        AllianceEventsAdapter,
        ServerAllianceAdapter,
        mongo_enabled
    )
except ImportError:
    mongo_enabled = lambda: False
    AllianceMonitoringAdapter = None
    AllianceMembersAdapter = None
    FurnaceHistoryAdapter = None
    AlliancesAdapter = None
    AllianceEventsAdapter = None
    ServerAllianceAdapter = None

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/alliance-monitor", tags=["Alliance Monitor"])

class MonitorSettings(BaseModel):
    guild_id: int
    alliance_id: Optional[int] = None
    channel_id: int
    enabled: bool = True
    check_interval: int = 240

class AddMemberRequest(BaseModel):
    guild_id: int
    fid: str
    nickname: str
    furnace_lv: int
    avatar_image: Optional[str] = None

class RemoveBatchRequest(BaseModel):
    fids: List[str]

@router.get("/alliances")
async def get_all_alliances():
    if not mongo_enabled():
        return []
    
    try:
        alliances = await AlliancesAdapter.get_all()
        return [{"id": a['alliance_id'], "name": a['alliance_name']} for a in alliances]
    except Exception as e:
        logger.error(f"Error getting alliances: {e}")
        return []

@router.get("/status/{guild_id}")
async def get_monitor_status(guild_id: int):
    if not mongo_enabled():
        return {"enabled": False, "message": "MongoDB not enabled"}
    
    try:
        monitors = await AllianceMonitoringAdapter.get_all_monitors_async()
        # Find monitor for this guild
        monitor = next((m for m in monitors if m['guild_id'] == guild_id), None)
        
        if not monitor:
            # Try to get alliance ID from ServerAllianceAdapter
            alliance_id = await ServerAllianceAdapter.get_alliance_async(guild_id)
            return {
                "enabled": False,
                "guild_id": guild_id,
                "alliance_id": alliance_id or 0,
                "channel_id": 0,
                "check_interval": 240
            }
        
        return {
            "enabled": bool(monitor['enabled']),
            "guild_id": monitor['guild_id'],
            "alliance_id": monitor['alliance_id'],
            "channel_id": str(monitor['channel_id']),
            "check_interval": monitor['check_interval']
        }
    except Exception as e:
        logger.error(f"Error getting monitor status: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/settings")
async def save_monitor_settings(settings: MonitorSettings):
    if not mongo_enabled():
        return {"status": "error", "message": "MongoDB not enabled"}
    
    try:
        # If alliance_id is not provided, try to get it from ServerAllianceAdapter
        alliance_id = settings.alliance_id
        if not alliance_id or alliance_id == 0:
            alliance_id = await ServerAllianceAdapter.get_alliance_async(settings.guild_id)
            
        if not alliance_id:
            return {"status": "error", "message": "No alliance assigned to this server. Please assign an alliance first."}

        success = await AllianceMonitoringAdapter.upsert_monitor_async(
            guild_id=settings.guild_id,
            alliance_id=alliance_id,
            channel_id=settings.channel_id,
            enabled=1 if settings.enabled else 0,
            check_interval=settings.check_interval
        )
        
        if success:
            return {"status": "success", "message": "Settings saved"}
        else:
            return {"status": "error", "message": "Failed to save settings"}
    except Exception as e:
        logger.error(f"Error saving monitor settings: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/history/{guild_id}")
async def get_monitor_history(guild_id: int, limit: int = 50):
    if not mongo_enabled():
        return []
    
    try:
        events = await AllianceEventsAdapter.get_recent_events_async(guild_id, limit=limit)
        
        # Format for frontend
        history = []
        for e in events:
            # Standardize type names for frontend CSS
            e_type = e.get('type', 'event').replace('_change', '')
            
            val_text = ""
            formatted_old = None
            formatted_new = None
            if e_type == 'furnace':
                old_value = e.get('old_value')
                new_value = e.get('new_value')
                formatted_old = format_furnace_level(old_value) if old_value is not None else None
                formatted_new = format_furnace_level(new_value) if new_value is not None else None
                if formatted_old and formatted_new:
                    val_text = f"changed furnace from {formatted_old} to {formatted_new}"
                elif formatted_new:
                    val_text = f"reached {formatted_new}"
                else:
                    val_text = "updated furnace level"
            elif e_type == 'name':
                val_text = f"changed name to {e.get('new_value')}"
            elif e_type == 'avatar':
                val_text = "updated their avatar"
            elif e_type == 'state':
                val_text = f"moved to State #{e.get('new_value')}"
            else:
                val_text = "updated their profile"

            history.append({
                "type": e_type,
                "id": e.get("fid"),
                "nickname": e.get("nickname"),
                "value_text": val_text,
                "old_value_formatted": formatted_old,
                "new_value_formatted": formatted_new,
                "timestamp": e.get("timestamp")
            })
            
        return history
    except Exception as e:
        logger.error(f"Error getting monitor history: {e}")
        return []

@router.get("/members/{guild_id}")
async def get_monitored_members(guild_id: int, alliance_id: Optional[int] = None):
    if not mongo_enabled():
        return []
    
    try:
        if not alliance_id:
            monitors = await AllianceMonitoringAdapter.get_all_monitors_async()
            monitor = next((m for m in monitors if m['guild_id'] == guild_id), None)
            if not monitor:
                # Try getting from ServerAllianceAdapter
                alliance_id = await ServerAllianceAdapter.get_alliance_async(guild_id)
            else:
                alliance_id = monitor['alliance_id']
        
        if not alliance_id:
            return []
            
        all_members = await AllianceMembersAdapter.get_all_members_async()
        
        # Filter by alliance
        alliance_members = [
            m for m in all_members 
            if str(m.get('alliance') or m.get('alliance_id') or 0) == str(alliance_id)
        ]
        
        # Clean up and format
        result = []
        for m in alliance_members:
            furnace_lv = m.get('furnace_lv', 0)
            result.append({
                "id": str(m.get('fid')),
                "nickname": m.get('nickname'),
                "furnace_lv": furnace_lv,
                "furnace_lv_formatted": format_furnace_level(furnace_lv),
                "avatar_image": m.get('avatar_image', ''),
                "state_id": m.get('state_id', ''),
                "last_checked": m.get('last_checked').isoformat() if isinstance(m.get('last_checked'), datetime) else m.get('last_checked')
            })
            
        return result
    except Exception as e:
        logger.error(f"Error getting monitored members: {e}")
        return []

@router.get("/members/lookup/{fid}")
async def lookup_monitor_player(fid: str):
    try:
        player = await fetch_player_info(fid)
        if player:
            return {
                "id": player.get("id"),
                "nickname": player.get("name"),
                "level": player.get("level"),
                "avatar_image": player.get("avatar_image")
            }
        return None
    except Exception as e:
        logger.error(f"Error looking up player {fid}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/members/add")
async def add_monitored_member(request: AddMemberRequest):
    if not mongo_enabled():
        return {"status": "error", "message": "MongoDB not enabled"}
    
    try:
        # Get alliance ID for this guild
        alliance_id = await ServerAllianceAdapter.get_alliance_async(request.guild_id)
        if not alliance_id:
            # Check monitor settings if not in ServerAllianceAdapter
            monitors = await AllianceMonitoringAdapter.get_all_monitors_async()
            monitor = next((m for m in monitors if m['guild_id'] == request.guild_id), None)
            if monitor:
                alliance_id = monitor['alliance_id']
        
        if not alliance_id:
            return {"status": "error", "message": "No alliance assigned to this server."}

        member_data = {
            "fid": request.fid,
            "nickname": request.nickname,
            "furnace_lv": request.furnace_lv,
            "avatar_image": request.avatar_image,
            "alliance_id": int(alliance_id),
            "alliance": int(alliance_id),
            "last_checked": datetime.utcnow().isoformat()
        }
        
        success = await AllianceMembersAdapter.upsert_member_async(request.fid, member_data)
        if success:
            return {"status": "success", "message": f"Added {request.nickname} to monitor"}
        return {"status": "error", "message": "Failed to add member to database"}
    except Exception as e:
        logger.error(f"Error adding monitored member: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/members/{fid}")
async def remove_monitored_member(fid: str):
    if not mongo_enabled():
        return {"status": "error", "message": "MongoDB not enabled"}
    
    try:
        success = await AllianceMembersAdapter.delete_member_async(fid)
        if success:
            return {"status": "success", "message": f"Removed player {fid}"}
        return {"status": "error", "message": "Player not found or failed to remove"}
    except Exception as e:
        logger.error(f"Error removing monitored member: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/members/remove-batch")
async def remove_monitored_members_batch(request: RemoveBatchRequest):
    if not mongo_enabled():
        return {"status": "error", "message": "MongoDB not enabled"}
    
    try:
        count = 0
        for fid in request.fids:
            if await AllianceMembersAdapter.delete_member_async(fid):
                count += 1
        
        return {"status": "success", "message": f"Successfully removed {count} members"}
    except Exception as e:
        logger.error(f"Error removing members batch: {e}")
        raise HTTPException(status_code=500, detail=str(e))
