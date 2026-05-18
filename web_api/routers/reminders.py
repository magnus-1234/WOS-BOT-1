from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional, List
import httpx
import logging
import pytz
import discord
from datetime import datetime, timezone as dt_timezone

from cogs.reminder_system import ReminderStorage, TimeParser

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/reminders", tags=["Reminders"])

class ReminderCreate(BaseModel):
    message: str = ""
    channel_id: str = ""
    time_str: Optional[str] = None
    target_time: Optional[str] = None  # ISO format from frontend
    timezone: str = "UTC"    # Timezone from frontend
    recurrence_type: str = "none" # none, daily, weekly, custom
    recurrence_interval: int = 1
    body: Optional[str] = None
    mention: str = "everyone"
    image_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    footer_text: Optional[str] = None
    footer_icon_url: Optional[str] = None
    author_url: Optional[str] = None

def _build_reminder_embed(payload: ReminderCreate, user: dict, *, is_test: bool = False) -> discord.Embed:
    embed = discord.Embed(
        title=payload.message or "Reminder",
        description=payload.body,
        color=0xb4a7d6
    )
    if payload.image_url:
        embed.set_image(url=payload.image_url)
    if payload.thumbnail_url:
        embed.set_thumbnail(url=payload.thumbnail_url)
    footer_text = payload.footer_text or ("Test reminder preview" if is_test else None)
    if footer_text or payload.footer_icon_url:
        embed.set_footer(text=footer_text or "", icon_url=payload.footer_icon_url)
    if payload.author_url:
        embed.set_author(
            name=user.get("global_name") or user.get("username") or "Reminder Author",
            url=payload.author_url,
            icon_url=f"https://cdn.discordapp.com/avatars/{user.get('id')}/{user.get('avatar')}.png" if user.get("avatar") else None
        )
    return embed

async def _get_discord_user(auth_header: str) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.get('https://discord.com/api/users/@me', headers={"Authorization": auth_header})
        if r.status_code != 200:
            raise HTTPException(status_code=401, detail="Invalid token")
        return r.json()

@router.get("/{guild_id}")
async def get_reminders(request: Request, guild_id: str):
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(status_code=401, detail="Unauthorized")

    async with httpx.AsyncClient() as client:
        r = await client.get('https://discord.com/api/users/@me', headers={"Authorization": auth_header})
        if r.status_code != 200:
            raise HTTPException(status_code=401, detail="Invalid token")
        user = r.json()
        user_id = user["id"]

    _bot = getattr(request.app.state, 'bot', None)
    storage = getattr(_bot, 'reminder_system', None).storage if _bot and hasattr(_bot, 'reminder_system') else ReminderStorage()
    
    reminders = storage.get_user_reminders(user_id, limit=50)
    
    guild_channel_ids = set()
    if _bot:
        guild = _bot.get_guild(guild_id)
        if guild:
            guild_channel_ids = {str(c.id) for c in guild.channels}
    
    # Filter by guild_id if available, or check if channel_id belongs to the guild
    server_reminders = []
    for r in reminders:
        # Convert MongoDB ObjectId to string for JSON serialization
        if "_id" in r:
            r["_id"] = str(r["_id"])
            
        r_guild = str(r.get("guild_id")) if r.get("guild_id") else None
        r_channel = str(r.get("channel_id"))
        
        if r_guild == str(guild_id):
            server_reminders.append(r)
        elif r_channel in guild_channel_ids:
            server_reminders.append(r)
            
    return {"reminders": server_reminders}

@router.post("/{guild_id}")
async def create_reminder(request: Request, guild_id: str, payload: ReminderCreate):
    logger.info(f"Creating reminder for guild {guild_id}: {payload.json()}")
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(status_code=401, detail="Unauthorized")

    async with httpx.AsyncClient() as client:
        r = await client.get('https://discord.com/api/users/@me', headers={"Authorization": auth_header})
        if r.status_code != 200:
            raise HTTPException(status_code=401, detail="Invalid token")
        user = r.json()
        user_id = user["id"]

    bot = request.app.state.bot
    cog = bot.get_cog("ReminderSystem")
    if not cog:
        raise HTTPException(status_code=503, detail="Reminder system not active")

    # Parse time
    reminder_time = None
    recurring_info = {}
    
    if payload.target_time:
        try:
            # target_time is usually YYYY-MM-DDTHH:MM:SS
            naive_time = datetime.fromisoformat(payload.target_time.replace('Z', ''))
            
            # Localize to user's timezone
            tz_str = payload.timezone or "UTC"
            try:
                user_tz = pytz.timezone(tz_str)
            except Exception:
                user_tz = pytz.UTC
            
            localized_time = user_tz.localize(naive_time)
            
            # Convert to UTC for storage (bot runs in UTC)
            reminder_time = localized_time.astimezone(pytz.UTC).replace(tzinfo=None)
            
            if payload.recurrence_type != "none":
                recurring_info = {
                    "is_recurring": True,
                    "type": payload.recurrence_type,
                    "interval": payload.recurrence_interval if payload.recurrence_type == "custom" else 1,
                    "pattern": f"Structured: {payload.recurrence_type}"
                }
                if payload.recurrence_type == "weekly":
                    recurring_info["interval"] = 7
        except Exception as e:
            logger.error(f"Error parsing target_time: {e}")

    if not reminder_time and payload.time_str:
        reminder_time, recurring_info = TimeParser.parse_time_string(payload.time_str)
        
    if not reminder_time:
        raise HTTPException(status_code=400, detail="Invalid time format. Please select a date/time or provide a time string.")

    _bot = getattr(request.app.state, 'bot', None)
    storage = getattr(_bot, 'reminder_system', None).storage if _bot and hasattr(_bot, 'reminder_system') else ReminderStorage()
    
    reminder_id = storage.add_reminder(
        user_id=str(user_id),
        channel_id=str(payload.channel_id),
        guild_id=str(guild_id),
        message=payload.message,
        body=payload.body,
        reminder_time=reminder_time,
        is_recurring=recurring_info.get("is_recurring", False),
        recurrence_type=recurring_info.get("type"),
        recurrence_interval=recurring_info.get("interval"),
        original_pattern=recurring_info.get("pattern"),
        mention=payload.mention,
        image_url=payload.image_url,
        thumbnail_url=payload.thumbnail_url,
        footer_text=payload.footer_text,
        footer_icon_url=payload.footer_icon_url,
        author_url=payload.author_url
    )
    
    if reminder_id == -1:
        raise HTTPException(status_code=500, detail="Failed to save reminder.")
        
    return {"status": "success", "reminder_id": reminder_id}

@router.post("/{guild_id}/test")
async def send_test_reminder(request: Request, guild_id: str, payload: ReminderCreate):
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(status_code=401, detail="Unauthorized")

    user = await _get_discord_user(auth_header)

    bot = getattr(request.app.state, 'bot', None)
    if not bot:
        raise HTTPException(status_code=503, detail="Bot is not connected")

    if not payload.channel_id:
        raise HTTPException(status_code=400, detail="Select a channel before sending a test message.")

    try:
        channel = bot.get_channel(int(payload.channel_id))
    except Exception:
        channel = None

    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found or not visible to the bot.")
    if str(getattr(channel.guild, "id", "")) != str(guild_id):
        raise HTTPException(status_code=400, detail="Selected channel does not belong to this server.")

    mention_text = ""
    allow_mentions = discord.AllowedMentions.none()
    if payload.mention == "everyone":
        mention_text = "[TEST] @everyone"
    elif payload.mention == "here":
        mention_text = "[TEST] @here"
    elif payload.mention == "user":
        mention_text = f"<@{user['id']}>"
        allow_mentions = discord.AllowedMentions(users=True)

    embed = _build_reminder_embed(payload, user, is_test=True)
    try:
        await channel.send(embed=embed)
        if mention_text:
            await channel.send(
                content=mention_text,
                allowed_mentions=allow_mentions
            )
    except discord.Forbidden:
        raise HTTPException(status_code=403, detail="The bot cannot send messages in that channel.")
    except Exception as e:
        logger.error(f"Failed to send test reminder to guild {guild_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to send test message.")

    return {"status": "success"}

@router.delete("/{guild_id}/{reminder_id}")
async def delete_reminder(request: Request, guild_id: str, reminder_id: str):
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(status_code=401, detail="Unauthorized")

    async with httpx.AsyncClient() as client:
        r = await client.get('https://discord.com/api/users/@me', headers={"Authorization": auth_header})
        if r.status_code != 200:
            raise HTTPException(status_code=401, detail="Invalid token")
        user = r.json()
        user_id = user["id"]

    _bot = getattr(request.app.state, 'bot', None)
    storage = getattr(_bot, 'reminder_system', None).storage if _bot and hasattr(_bot, 'reminder_system') else ReminderStorage()
    
    success = storage.delete_reminder(reminder_id, str(user_id))
    
    if success:
        return {"status": "success"}
    else:
        raise HTTPException(status_code=400, detail="Failed to delete reminder. It may not exist or does not belong to you.")

@router.patch("/{guild_id}/{reminder_id}")
async def update_reminder(request: Request, guild_id: str, reminder_id: str, payload: ReminderCreate):
    logger.info(f"Updating reminder {reminder_id} for guild {guild_id}: {payload.json()}")
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    # reminder_id can be int or ObjectId string
    try:
        rid = int(reminder_id)
    except:
        rid = reminder_id
        
    async with httpx.AsyncClient() as client:
        r = await client.get('https://discord.com/api/users/@me', headers={"Authorization": auth_header})
        if r.status_code != 200:
            raise HTTPException(status_code=401, detail="Invalid token")
        user = r.json()
        user_id = user["id"]

    # Parse time
    reminder_time = None
    recurring_info = {}
    
    if payload.target_time:
        try:
            # target_time is usually YYYY-MM-DDTHH:MM
            naive_time = datetime.fromisoformat(payload.target_time.replace('Z', ''))
            
            # Localize to user's timezone
            tz_str = payload.timezone or "UTC"
            try:
                user_tz = pytz.timezone(tz_str)
            except Exception:
                user_tz = pytz.UTC
            
            localized_time = user_tz.localize(naive_time)
            
            # Convert to UTC for storage (bot runs in UTC)
            reminder_time = localized_time.astimezone(pytz.UTC).replace(tzinfo=None)
            
            if payload.recurrence_type != "none":
                recurring_info = {
                    "is_recurring": True,
                    "type": payload.recurrence_type,
                    "interval": payload.recurrence_interval if payload.recurrence_type == "custom" else 1,
                    "pattern": f"Structured: {payload.recurrence_type}"
                }
                if payload.recurrence_type == "weekly":
                    recurring_info["interval"] = 7
        except Exception as e:
            logger.error(f"Error parsing target_time: {e}")

    if not reminder_time and payload.time_str:
        reminder_time, recurring_info = TimeParser.parse_time_string(payload.time_str)

    if not reminder_time:
         raise HTTPException(status_code=400, detail="Invalid time format.")

    _bot = getattr(request.app.state, 'bot', None)
    storage = getattr(_bot, 'reminder_system', None).storage if _bot and hasattr(_bot, 'reminder_system') else ReminderStorage()
    
    update_data = {
        "message": payload.message,
        "body": payload.body,
        "reminder_time": reminder_time.isoformat(),
        "channel_id": str(payload.channel_id),
        "mention": payload.mention,
        "image_url": payload.image_url,
        "thumbnail_url": payload.thumbnail_url,
        "footer_text": payload.footer_text,
        "footer_icon_url": payload.footer_icon_url,
        "author_url": payload.author_url,
        "is_recurring": recurring_info.get("is_recurring", False),
        "recurrence_type": recurring_info.get("type"),
        "recurrence_interval": recurring_info.get("interval"),
        "original_time_pattern": recurring_info.get("pattern")
    }
    
    success = storage.update_reminder_fields(rid, update_data)
    
    if success:
        return {"status": "success"}
    else:
        raise HTTPException(status_code=400, detail="Failed to update reminder.")
