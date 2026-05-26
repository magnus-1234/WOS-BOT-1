from fastapi import APIRouter, HTTPException, Depends, Request
from typing import Dict, Any, List, Optional
from pydantic import BaseModel
import logging
import discord
import aiohttp
from db.roles_adapters import ReactionRolesAdapter, AutoRolesAdapter

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Roles"])

class ReactionRoleAdd(BaseModel):
    message_id: int
    emoji: str
    role_id: int

class AutoRolesUpdate(BaseModel):
    role_ids: List[int]

class ReactionRoleMapping(BaseModel):
    emoji: str
    role_id: int

class ReactionRoleMessageCreate(BaseModel):
    channel_id: int
    mode: str = "bot"
    webhook_url: Optional[str] = None
    title: str = "Choose your roles"
    description: str = "React below to receive or remove roles."
    color: str = "#5865f2"
    mappings: List[ReactionRoleMapping]

@router.get("/api/roles/{guild_id}/reaction")
async def get_reaction_roles(guild_id: int):
    roles = await ReactionRolesAdapter.get_reaction_roles(guild_id)
    return roles

@router.post("/api/roles/{guild_id}/reaction")
async def add_reaction_role(guild_id: int, data: ReactionRoleAdd):
    data.emoji = data.emoji.strip()
    if not data.emoji:
        raise HTTPException(status_code=400, detail="Emoji is required")
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

@router.post("/api/roles/{guild_id}/reaction-message")
async def create_reaction_role_message(guild_id: int, data: ReactionRoleMessageCreate, request: Request):
    bot = getattr(request.app.state, "bot", None)
    if not bot:
        raise HTTPException(status_code=503, detail="Discord bot is not available")

    guild = bot.get_guild(int(guild_id))
    if not guild:
        raise HTTPException(status_code=404, detail="Guild not found")

    channel = guild.get_channel(int(data.channel_id))
    if not channel or not hasattr(channel, "send"):
        raise HTTPException(status_code=404, detail="Channel not found or is not a text channel")

    mappings = []
    seen = set()
    for mapping in data.mappings:
        emoji = mapping.emoji.strip()
        if not emoji:
            continue
        role = guild.get_role(int(mapping.role_id))
        if not role:
            raise HTTPException(status_code=400, detail=f"Role {mapping.role_id} was not found")
        bot_member = guild.me
        if role.managed or not bot_member or role >= bot_member.top_role:
            raise HTTPException(status_code=400, detail=f"Bot cannot assign role {role.name}. Move the bot role higher and avoid managed roles.")
        key = emoji
        if key in seen:
            raise HTTPException(status_code=400, detail=f"Duplicate emoji mapping: {emoji}")
        seen.add(key)
        mappings.append((emoji, int(mapping.role_id)))

    if not mappings:
        raise HTTPException(status_code=400, detail="At least one emoji-role mapping is required")

    try:
        color_value = int(data.color.lstrip("#"), 16)
    except Exception:
        color_value = 0x5865F2

    description = data.description.strip() or "React below to receive or remove roles."
    mapping_lines = [f"{emoji} <@&{role_id}>" for emoji, role_id in mappings]
    embed = discord.Embed(
        title=(data.title.strip() or "Choose your roles")[:256],
        description=f"{description}\n\n" + "\n".join(mapping_lines),
        color=color_value,
    )
    mode = data.mode.lower().strip()
    if mode not in {"bot", "webhook"}:
        raise HTTPException(status_code=400, detail="Mode must be bot or webhook")
    if mode == "webhook" and not data.webhook_url:
        raise HTTPException(status_code=400, detail="Webhook URL is required")

    try:
        if mode == "webhook":
            async with aiohttp.ClientSession() as session:
                webhook = discord.Webhook.from_url(data.webhook_url, session=session)
                sent = await webhook.send(embed=embed, wait=True)
            message = await channel.fetch_message(sent.id)
        else:
            message = await channel.send(embed=embed)

        saved = []
        for emoji, role_id in mappings:
            await message.add_reaction(emoji)
            ok = await ReactionRolesAdapter.add_reaction_role(guild_id, message.id, emoji, role_id)
            if ok:
                saved.append({"emoji": emoji, "role_id": role_id})
        return {"status": "success", "message_id": str(message.id), "mappings": saved}
    except discord.Forbidden:
        raise HTTPException(status_code=403, detail="Bot lacks permission to send messages, embed links, add reactions, or manage this channel")
    except discord.HTTPException as e:
        raise HTTPException(status_code=400, detail=f"Discord rejected the role menu: {e}")
    except Exception as e:
        logger.error(f"Failed to create reaction role message for guild {guild_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create reaction role message")

@router.get("/api/roles/{guild_id}/auto")
async def get_auto_roles(guild_id: int):
    roles = await AutoRolesAdapter.get_auto_roles(guild_id)
    return {"role_ids": roles}

@router.post("/api/roles/{guild_id}/auto")
async def update_auto_roles(guild_id: int, data: AutoRolesUpdate, request: Request):
    role_ids = sorted({int(role_id) for role_id in data.role_ids})
    bot = getattr(request.app.state, "bot", None)
    guild = bot.get_guild(int(guild_id)) if bot else None
    if not guild:
        raise HTTPException(status_code=503, detail="Discord bot is not available for this guild")

    bot_member = guild.me
    for role_id in role_ids:
        role = guild.get_role(int(role_id))
        if not role:
            raise HTTPException(status_code=400, detail=f"Role {role_id} was not found")
        if role.managed or not bot_member or role >= bot_member.top_role:
            raise HTTPException(status_code=400, detail=f"Bot cannot assign role {role.name}. Move the bot role higher and avoid managed roles.")

    success = await AutoRolesAdapter.set_auto_roles(guild_id, role_ids)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update auto roles")
    return {"status": "success"}
