from fastapi import APIRouter, HTTPException, Depends, Request
from typing import Dict, Any, List, Optional
from pydantic import BaseModel
import logging
from db.roles_adapters import ReactionRolesAdapter, AutoRolesAdapter

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Roles"])

class ReactionRoleAdd(BaseModel):
    message_id: int
    emoji: str
    role_id: int

class AutoRolesUpdate(BaseModel):
    role_ids: List[int]

@router.get("/api/roles/{guild_id}/reaction")
async def get_reaction_roles(guild_id: int):
    roles = await ReactionRolesAdapter.get_reaction_roles(guild_id)
    return roles

@router.post("/api/roles/{guild_id}/reaction")
async def add_reaction_role(guild_id: int, data: ReactionRoleAdd):
    success = await ReactionRolesAdapter.add_reaction_role(guild_id, data.message_id, data.emoji, data.role_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to add reaction role")
    return {"status": "success"}

@router.delete("/api/roles/{guild_id}/reaction/{message_id}/{emoji}")
async def remove_reaction_role(guild_id: int, message_id: int, emoji: str):
    success = await ReactionRolesAdapter.remove_reaction_role(guild_id, message_id, emoji)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to remove reaction role")
    return {"status": "success"}

@router.get("/api/roles/{guild_id}/auto")
async def get_auto_roles(guild_id: int):
    roles = await AutoRolesAdapter.get_auto_roles(guild_id)
    return {"role_ids": roles}

@router.post("/api/roles/{guild_id}/auto")
async def update_auto_roles(guild_id: int, data: AutoRolesUpdate):
    success = await AutoRolesAdapter.set_auto_roles(guild_id, data.role_ids)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update auto roles")
    return {"status": "success"}
