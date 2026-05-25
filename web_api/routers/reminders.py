from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from pydantic import BaseModel
from typing import Optional, List
import httpx
import logging
import pytz
import discord
import uuid
from datetime import datetime, timezone as dt_timezone
import os

from cogs.reminder_system import ReminderStorage, TimeParser

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/reminders", tags=["Reminders"])

class ReminderCreate(BaseModel):
    message: str = ""
    channel_id: str = ""
    time_str: Optional[str] = None
    target_time: Optional[str] = None  # ISO format from frontend
    timezone: str = "UTC"    # Timezone from frontend
    recurrence_type: str = "none" # none, daily, weekly, specific_days, custom
    recurrence_interval: int = 1
    recurrence_days: Optional[List[int]] = None  # 0=Mon..6=Sun for specific_days
    body: Optional[str] = None
    mention: str = "everyone"
    image_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    footer_text: Optional[str] = None
    footer_icon_url: Optional[str] = None
    author_url: Optional[str] = None

class CommunityPresetCreate(BaseModel):
    title: str
    badge: Optional[str] = None
    message: str = ""
    body: Optional[str] = None
    recurrence_type: str = "none"
    recurrence_interval: int = 1
    recurrence_days: Optional[List[int]] = None
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

def _get_presets_collection():
    """Return the MongoDB community_reminder_presets collection or None."""
    import os
    try:
        mongo_uri = os.environ.get('MONGO_URI')
        if not mongo_uri:
            return None
        from pymongo import MongoClient
        client = MongoClient(mongo_uri, serverSelectionTimeoutMS=3000)
        return client['wosbot']['community_reminder_presets']
    except Exception as e:
        logger.warning(f"MongoDB presets collection unavailable: {e}")
        return None

# ─── Static API Routes ────────────────────────────────────────────────────────

@router.post("/upload-url")
async def upload_reminder_image_from_url(request: Request):
    """Validates an external image URL and returns it as-is for use in reminders.
    Discord can render HTTPS URLs directly — no need to re-host locally."""
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(status_code=401, detail="Unauthorized")

    body = await request.json()
    url = body.get("url", "").strip()
    if not url or not url.startswith("http"):
        raise HTTPException(status_code=400, detail="A valid 'url' field is required.")

    # Return the original URL unchanged — Discord embeds support HTTPS URLs natively.
    # Re-hosting locally would give HTTP-only URLs that Discord silently drops.
    return {"status": "success", "url": url}

@router.post("/upload")
async def upload_reminder_image(request: Request, file: UploadFile = File(...)):
    """Uploads a local image for a reminder and returns its public URL."""
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(status_code=401, detail="Unauthorized")

    if not file:
        raise HTTPException(status_code=400, detail="No file uploaded")

    content = await file.read()
    if len(content) > 8 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image too large (max 8MB)")

    ext = file.filename.split('.')[-1].lower() if '.' in file.filename else 'png'
    if ext not in ['png', 'jpg', 'jpeg', 'gif', 'webp']:
        ext = 'png'

    filename = f"reminder_{uuid.uuid4().hex}.{ext}"
    upload_dir = "data/uploads"
    os.makedirs(upload_dir, exist_ok=True)
    
    filepath = os.path.join(upload_dir, filename)
    with open(filepath, "wb") as f:
        f.write(content)

    # Use the request's base URL to construct the public URL
    base_url = str(request.base_url).rstrip("/")
    public_url = f"{base_url}/api/static/{filename}"
    return {"status": "success", "url": public_url}

@router.get("/presets")
async def get_community_presets(request: Request, q: Optional[str] = None):
    """Get all community presets, optionally filtered by search query."""
    try:
        col = _get_presets_collection()
        if col is None:
            return {"presets": []}
        query = {}
        if q and q.strip():
            query = {"$or": [
                {"title": {"$regex": q.strip(), "$options": "i"}},
                {"body": {"$regex": q.strip(), "$options": "i"}},
                {"message": {"$regex": q.strip(), "$options": "i"}}
            ]}
        presets = list(col.find(query, {"_id": 0}).sort("created_at", -1).limit(100))
        return {"presets": presets}
    except Exception as e:
        logger.error(f"Failed to fetch community presets: {e}")
        return {"presets": []}

@router.get("/assets")
async def get_builtin_presets(request: Request):
    """Return builtin preset assets from data/assets/presets.json"""
    try:
        import os, json
        assets_file = os.path.join("data", "assets", "presets.json")
        if not os.path.exists(assets_file):
            return {"presets": []}
        with open(assets_file, "r", encoding="utf-8") as f:
            presets = json.load(f)
        return {"presets": presets}
    except Exception as e:
        logger.error(f"Failed to load builtin presets: {e}")
        return {"presets": []}

@router.post("/presets")
async def create_community_preset(request: Request, payload: CommunityPresetCreate):
    """Create a new community reminder preset visible to all users."""
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(status_code=401, detail="Unauthorized")

    user = await _get_discord_user(auth_header)

    if not payload.title or not payload.title.strip():
        raise HTTPException(status_code=400, detail="Preset title is required")

    badge_map = {"daily": "Daily", "weekly": "Weekly", "custom": "Custom", "none": "One-time"}
    badge = payload.badge or badge_map.get(payload.recurrence_type, "One-time")

    preset = {
        "id": str(uuid.uuid4()),
        "title": payload.title.strip(),
        "badge": badge,
        "message": payload.message,
        "body": payload.body,
        "recurrence_type": payload.recurrence_type,
        "recurrence_interval": payload.recurrence_interval,
        "recurrence_days": payload.recurrence_days,
        "mention": payload.mention,
        "image_url": payload.image_url,
        "thumbnail_url": payload.thumbnail_url,
        "footer_text": payload.footer_text,
        "footer_icon_url": payload.footer_icon_url,
        "author_url": payload.author_url,
        "created_by": user.get("global_name") or user.get("username") or "Unknown",
        "created_by_id": user.get("id"),
        "created_at": datetime.utcnow().isoformat()
    }

    try:
        col = _get_presets_collection()
        if col is None:
            raise HTTPException(status_code=503, detail="Storage not available")
        doc = {**preset}
        col.insert_one(doc)
        return {"status": "success", "preset": preset}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to save community preset: {e}")
        raise HTTPException(status_code=500, detail="Failed to save preset")

# ─── Dynamic API Routes ───────────────────────────────────────────────────────

@router.get("/{guild_id}")
async def get_reminders(request: Request, guild_id: str):
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(status_code=401, detail="Unauthorized")

    async with httpx.AsyncClient() as client:
        r = await client.get('https://discord.com/api/users/@me', headers={"Authorization": auth_header})
        if r.status_code != 200:
            raise HTTPException(status_code=401, detail="Invalid token")

    _bot = getattr(request.app.state, 'bot', None)
    storage = getattr(_bot, 'reminder_system', None).storage if _bot and hasattr(_bot, 'reminder_system') else ReminderStorage()

    guild_channel_ids = set()
    try:
        guild_id_int = int(guild_id)
    except Exception:
        guild_id_int = None

    if _bot and guild_id_int:
        guild = _bot.get_guild(guild_id_int)
        if guild:
            guild_channel_ids = {str(c.id) for c in guild.channels}

    # Fetch ALL active reminders (not just for the requesting user)
    try:
        all_reminders = storage.get_all_active_reminders()
    except Exception:
        all_reminders = []

    server_reminders = []
    for r in all_reminders:
        # Serialize datetimes and ObjectIds
        for k, v in list(r.items()):
            if hasattr(v, 'isoformat'):
                r[k] = v.isoformat()
        if "_id" in r:
            r["_id"] = str(r["_id"])
        # Ensure 'id' field is a string
        if "id" in r:
            r["id"] = str(r["id"])

        r_guild = str(r.get("guild_id")) if r.get("guild_id") else None
        r_channel = str(r.get("channel_id", ""))

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
                    "pattern": f"Structured: {payload.recurrence_type}",
                    "days": payload.recurrence_days or []
                }
                if payload.recurrence_type == "weekly":
                    recurring_info["interval"] = 7
                elif payload.recurrence_type == "specific_days":
                    recurring_info["interval"] = 1  # checked daily, fires on matching days
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
        recurrence_days=recurring_info.get("days"),
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
