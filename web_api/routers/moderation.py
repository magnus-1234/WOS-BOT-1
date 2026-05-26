from fastapi import APIRouter, HTTPException, Depends, Request
from typing import Dict, Any, List, Optional
from pydantic import BaseModel
import logging
from db.moderation_adapters import ModerationSettingsAdapter, ModerationActionsAdapter, BlacklistAdapter

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Moderation"])

class AutoModSettings(BaseModel):
    enabled: bool
    anti_spam: bool
    anti_link: bool
    anti_invites: bool
    max_mentions: int
    bypass_roles: List[int]

class LoggingSettings(BaseModel):
    enabled: bool
    channel_id: Optional[int]
    events: List[str]

class EscalationRule(BaseModel):
    warn_count: int
    action: str
    duration: Optional[int]

class EscalationSettings(BaseModel):
    enabled: bool
    rules: List[EscalationRule]

class ModSettingsUpdate(BaseModel):
    automod: Optional[AutoModSettings]
    logging: Optional[LoggingSettings]
    escalation: Optional[EscalationSettings]

class BlacklistUpdate(BaseModel):
    words: List[str]

@router.get("/api/moderation/{guild_id}/settings")
async def get_mod_settings(guild_id: int):
    settings = await ModerationSettingsAdapter.get_settings(guild_id)
    return settings

@router.post("/api/moderation/{guild_id}/settings")
async def update_mod_settings(guild_id: int, settings: ModSettingsUpdate):
    success = await ModerationSettingsAdapter.set_settings(guild_id, settings.dict(exclude_unset=True))
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update settings")
    return {"status": "success"}

@router.get("/api/moderation/{guild_id}/blacklist")
async def get_blacklist(guild_id: int):
    words = await BlacklistAdapter.get_blacklist(guild_id)
    return {"words": words}

@router.post("/api/moderation/{guild_id}/blacklist")
async def update_blacklist(guild_id: int, data: BlacklistUpdate):
    success = await BlacklistAdapter.set_blacklist(guild_id, data.words)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update blacklist")
    return {"status": "success"}

@router.get("/api/moderation/{guild_id}/stats")
async def get_mod_stats(guild_id: int):
    stats = await ModerationActionsAdapter.get_stats(guild_id)
    return stats

@router.get("/api/moderation/{guild_id}/actions")
async def get_mod_actions(guild_id: int, user_id: Optional[int] = None, limit: int = 100):
    actions = await ModerationActionsAdapter.get_actions(guild_id, user_id, limit)
    return actions
