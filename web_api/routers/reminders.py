from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from fastapi.responses import Response
from pydantic import BaseModel
from typing import Optional, List
import httpx
import logging
import pytz
import discord
import uuid
import re
from datetime import datetime

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
    save_as_preset: bool = False
    preset_title: Optional[str] = None

MAX_REMINDER_IMAGE_BYTES = 8 * 1024 * 1024
ALLOWED_REMINDER_IMAGE_TYPES = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
}

def _normalize_external_image_url(url: str) -> str:
    drive_match = re.search(r"drive\.google\.com/file/d/([^/]+)", url)
    if drive_match:
        return f"https://drive.google.com/uc?export=download&id={drive_match.group(1)}"
    drive_id_match = re.search(r"[?&]id=([^&]+)", url)
    if "drive.google.com" in url and drive_id_match:
        return f"https://drive.google.com/uc?export=download&id={drive_id_match.group(1)}"
    if "dropbox.com" in url:
        return url.replace("www.dropbox.com", "dl.dropboxusercontent.com").replace("?dl=0", "").replace("?dl=1", "")
    return url

def _sniff_image_content_type(content: bytes, fallback: str = "") -> str:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return fallback

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

def _storage(request: Request):
    _bot = getattr(request.app.state, 'bot', None)
    return getattr(_bot, 'reminder_system', None).storage if _bot and hasattr(_bot, 'reminder_system') else ReminderStorage()

def _normalize_reminder_id(reminder_id: str):
    try:
        return int(reminder_id)
    except Exception:
        return reminder_id

def _reminder_belongs_to_guild(reminder: dict, guild_id: int, guild_channel_ids: set[str]) -> bool:
    r_guild = str(reminder.get("guild_id")) if reminder.get("guild_id") else None
    r_channel = str(reminder.get("channel_id", ""))
    return r_guild == str(guild_id) or r_channel in guild_channel_ids

def _active_reminder_query() -> dict:
    return {
        "$and": [
            {"$or": [{"is_active": True}, {"is_active": 1}, {"is_active": "1"}, {"is_active": "true"}, {"is_active": "True"}, {"is_active": {"$exists": False}}]},
            {"$or": [{"is_sent": False}, {"is_sent": 0}, {"is_sent": "0"}, {"is_sent": "false"}, {"is_sent": "False"}, {"is_sent": {"$exists": False}}]},
        ]
    }

def _serialize_reminder(reminder: dict) -> dict:
    item = dict(reminder)
    for k, v in list(item.items()):
        if hasattr(v, "isoformat"):
            item[k] = v.isoformat()
        elif k == "_id":
            item[k] = str(v)
    if "id" not in item and "_id" in item:
        item["id"] = str(item["_id"])
    elif "id" in item:
        item["id"] = str(item["id"])
    return item

def _is_discord_url_expired(url: str) -> bool:
    if not url:
        return False
    if "cdn.discordapp.com" not in url and "media.discordapp.net" not in url:
        return False
    import time
    import urllib.parse
    try:
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)
        ex_hex = query.get("ex", [None])[0]
        if not ex_hex:
            return True
        ex_time = int(ex_hex, 16)
        if ex_time - time.time() < 7200:  # Expired or expiring within 2 hours
            return True
    except Exception:
        return True
    return False

async def _refresh_discord_urls(urls: list[str]) -> dict[str, str]:
    if not urls:
        return {}
    import os
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.warning("No DISCORD_TOKEN found; cannot refresh Discord attachment URLs.")
        return {}
        
    discord_urls = list(set(u for u in urls if u and ("cdn.discordapp.com/attachments" in u or "media.discordapp.net/attachments" in u)))
    if not discord_urls:
        return {}
        
    endpoint = "https://discord.com/api/v10/attachments/refresh-urls"
    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
        "User-Agent": "FastAPI-WebDashboard/1.0"
    }
    payload = {
        "attachment_urls": discord_urls
    }
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(endpoint, json=payload, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                refreshed_list = data.get("refreshed_urls", [])
                mapping = {}
                for item in refreshed_list:
                    orig = item.get("original")
                    ref = item.get("refreshed")
                    if orig and ref:
                        mapping[orig] = ref
                return mapping
            else:
                logger.error(f"Failed to refresh Discord URLs (status {resp.status_code}): {resp.text}")
    except Exception as e:
        logger.error(f"Error calling Discord refresh-urls endpoint: {e}")
        
    return {}

async def _refresh_items_discord_urls(items: list[dict]) -> list[dict]:
    """
    Given a list of reminder/preset dicts, automatically identify and refresh
    any expired or expiring Discord CDN links in their thumbnail_url, image_url, or footer_icon_url fields.
    """
    expired_urls = []
    for item in items:
        for field in ("thumbnail_url", "image_url", "footer_icon_url"):
            val = item.get(field)
            if val and _is_discord_url_expired(val):
                expired_urls.append(val)
                
    if expired_urls:
        mapping = await _refresh_discord_urls(expired_urls)
        if mapping:
            for item in items:
                for field in ("thumbnail_url", "image_url", "footer_icon_url"):
                    val = item.get(field)
                    if val and val in mapping:
                        item[field] = mapping[val]
                        
    return items

def _dedupe_reminders(reminders: list[dict]) -> list[dict]:
    deduped = []
    seen = set()
    for r in reminders:
        key = str(r.get("id") or r.get("_id") or "")
        if not key:
            key = "|".join(str(r.get(k) or "") for k in ("guild_id", "channel_id", "user_id", "message", "reminder_time"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    return deduped

async def _collect_active_reminders(request: Request) -> list[dict]:
    reminders = []
    storage = _storage(request)
    try:
        reminders.extend(_serialize_reminder(r) for r in storage.get_all_active_reminders())
    except Exception as e:
        logger.warning(f"Reminder storage list failed: {e}")

    try:
        from db.mongo_adapters import _get_db_reminders_async, RemindersAdapter
        db = await _get_db_reminders_async()
        cursor = db[RemindersAdapter.COLL].find(_active_reminder_query()).sort("reminder_time", 1)
        reminders.extend(_serialize_reminder(r) for r in await cursor.to_list(length=1000))
    except Exception as e:
        logger.warning(f"Reminder adapter DB list failed: {e}")

    try:
        from db.mongo_client_wrapper import get_mongo_client
        client = await get_mongo_client()
        db = client["whiteout_survival_bot"]
        cursor = db["reminders"].find(_active_reminder_query()).sort("reminder_time", 1)
        reminders.extend(_serialize_reminder(r) for r in await cursor.to_list(length=1000))
    except Exception as e:
        logger.warning(f"Legacy reminder DB list failed: {e}")

    return _dedupe_reminders(reminders)

async def _get_upload_collection():
    try:
        from db.mongo_adapters import _get_db_reminders_async
        db = await _get_db_reminders_async()
        return db["reminder_uploads"]
    except Exception as e:
        logger.error(f"Mongo reminder upload storage unavailable: {e}")
        raise HTTPException(status_code=503, detail="Image storage is not available.")

def _public_upload_url(request: Request, image_id: str) -> str:
    scheme = request.headers.get("X-Forwarded-Proto", request.url.scheme)
    forwarded_host = request.headers.get("X-Forwarded-Host")
    host = forwarded_host or request.headers.get("Host")
    base_url = f"{scheme}://{host}" if host else str(request.base_url).rstrip("/")
    return f"{base_url}/api/reminders/uploads/{image_id}"

async def _save_uploaded_image(request: Request, *, filename: str, content_type: str, content: bytes, source: str, user_id: str) -> str:
    if not content:
        raise HTTPException(status_code=400, detail="No image data received.")
    if len(content) > MAX_REMINDER_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail="Image too large (max 8MB).")
    content_type = _sniff_image_content_type(content, content_type)
    if content_type not in ALLOWED_REMINDER_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="Only PNG, JPG, GIF, and WebP images are supported.")

    image_id = uuid.uuid4().hex
    col = await _get_upload_collection()
    await col.insert_one({
        "_id": image_id,
        "filename": filename or f"reminder_{image_id}",
        "content_type": content_type,
        "size": len(content),
        "data": content,
        "source": source,
        "created_by": str(user_id),
        "created_at": datetime.utcnow(),
    })
    return _public_upload_url(request, image_id)

@router.post("/upload")
async def upload_reminder_image(request: Request, file: UploadFile = File(...)):
    """Uploads an image for a reminder into MongoDB and returns a public retrieval URL."""
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(status_code=401, detail="Unauthorized")
    user = await _get_discord_user(auth_header)

    if not file:
        raise HTTPException(status_code=400, detail="No file uploaded")

    content = await file.read()
    content_type = (file.content_type or "").lower()
    if not content_type or content_type == "application/octet-stream":
        ext = (file.filename or "").rsplit(".", 1)[-1].lower()
        content_type = {
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "gif": "image/gif",
            "webp": "image/webp",
        }.get(ext, "")

    url = await _save_uploaded_image(
        request,
        filename=file.filename or "",
        content_type=content_type,
        content=content,
        source="file",
        user_id=user["id"],
    )
    return {"status": "success", "url": url, "max_size_mb": 8}

class ReminderImageUrlImport(BaseModel):
    url: str

@router.post("/upload-url")
async def upload_reminder_image_from_url(request: Request, payload: ReminderImageUrlImport):
    """Imports an image URL into MongoDB so remote Drive/CDN images remain available later."""
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(status_code=401, detail="Unauthorized")
    user = await _get_discord_user(auth_header)

    source_url = (payload.url or "").strip()
    if not source_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Enter a valid http(s) image URL.")
    fetch_url = _normalize_external_image_url(source_url)

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=20.0) as client:
            async with client.stream("GET", fetch_url) as resp:
                if resp.status_code != 200:
                    raise HTTPException(status_code=400, detail=f"Could not fetch image (status {resp.status_code}).")
                content_type = (resp.headers.get("content-type") or "").split(";", 1)[0].lower()
                chunks = []
                size = 0
                async for chunk in resp.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_REMINDER_IMAGE_BYTES:
                        raise HTTPException(status_code=400, detail="Image too large (max 8MB).")
                    chunks.append(chunk)
                content = b"".join(chunks)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to import reminder image URL {source_url}: {e}")
        raise HTTPException(status_code=400, detail="Could not import that image URL.")

    filename = source_url.rsplit("/", 1)[-1].split("?", 1)[0] or "remote-image"
    url = await _save_uploaded_image(
        request,
        filename=filename,
        content_type=content_type,
        content=content,
        source=source_url,
        user_id=user["id"],
    )
    return {"status": "success", "url": url, "max_size_mb": 8}

@router.get("/uploads/{image_id}")
async def get_uploaded_reminder_image(image_id: str):
    col = await _get_upload_collection()
    doc = await col.find_one({"_id": image_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Image not found.")
    return Response(
        content=doc.get("data") or b"",
        media_type=doc.get("content_type") or "application/octet-stream",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )

@router.get("/{guild_id:int}")
async def get_reminders(request: Request, guild_id: int):
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(status_code=401, detail="Unauthorized")

    async with httpx.AsyncClient() as client:
        r = await client.get('https://discord.com/api/users/@me', headers={"Authorization": auth_header})
        if r.status_code != 200:
            raise HTTPException(status_code=401, detail="Invalid token")

    _bot = getattr(request.app.state, 'bot', None)
    guild_channel_ids = set()
    try:
        guild_id_int = int(guild_id)
    except Exception:
        guild_id_int = None

    if _bot and guild_id_int:
        guild = _bot.get_guild(guild_id_int)
        if guild:
            guild_channel_ids = {str(c.id) for c in guild.channels}

    # Fetch ALL active reminders (not just for the requesting user), across bot and legacy Mongo paths.
    all_reminders = await _collect_active_reminders(request)

    server_reminders = []
    for r in all_reminders:
        if _reminder_belongs_to_guild(r, guild_id, guild_channel_ids):
            server_reminders.append(r)

    try:
        server_reminders = await _refresh_items_discord_urls(server_reminders)
    except Exception as e:
        logger.error(f"Failed to refresh Discord attachment URLs: {e}")

    return {"reminders": server_reminders}

@router.post("/{guild_id:int}")
async def create_reminder(request: Request, guild_id: int, payload: ReminderCreate):
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

    storage = _storage(request)
    
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
        recurrence_days=payload.recurrence_days,
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
        
    # Save as community preset if requested
    if payload.save_as_preset:
        try:
            col = _get_presets_collection()
            if col is not None:
                badge_map = {"daily": "Daily", "weekly": "Weekly", "custom": "Custom", "none": "One-time", "specific_days": "Specific Days"}
                badge = badge_map.get(payload.recurrence_type, "One-time")
                
                preset = {
                    "id": str(uuid.uuid4()),
                    "title": payload.preset_title or payload.message or "Unnamed Preset",
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
                col.insert_one(preset)
                logger.info(f"✅ Created community preset from reminder creation: {preset['title']}")
        except Exception as e:
            logger.error(f"❌ Failed to save community preset during reminder creation: {e}")
            # Don't fail the whole request if only preset saving fails
            
    return {"status": "success", "reminder_id": reminder_id}

@router.post("/{guild_id:int}/test")
async def send_test_reminder(request: Request, guild_id: int, payload: ReminderCreate):
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

@router.delete("/{guild_id:int}/{reminder_id}")
async def delete_reminder(request: Request, guild_id: int, reminder_id: str):
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
    storage = _storage(request)
    
    success = storage.delete_reminder(reminder_id, str(user_id))

    if not success:
        guild_channel_ids = set()
        if _bot:
            guild = _bot.get_guild(guild_id)
            if guild:
                guild_channel_ids = {str(c.id) for c in guild.channels}
        
        # 1. Try to delete/deactivate in the main storage admin-style
        try:
            all_reminders = storage.get_all_active_reminders()
            target = next((r for r in all_reminders if str(r.get("id") or r.get("_id")) == str(reminder_id)), None)
            if target and _reminder_belongs_to_guild(target, guild_id, guild_channel_ids):
                success = storage.update_reminder_fields(_normalize_reminder_id(reminder_id), {"is_active": False})
        except Exception as e:
            logger.error(f"Failed admin-style reminder delete in main storage for {reminder_id}: {e}")

        # 2. Try to deactivate directly in MongoDB RemindersAdapter database if it was not done
        if not success:
            try:
                from db.mongo_adapters import _get_db_reminders_async, RemindersAdapter
                from bson import ObjectId
                db = await _get_db_reminders_async()
                query_id = reminder_id
                try:
                    query_id = ObjectId(reminder_id)
                except Exception:
                    pass
                
                target = await db[RemindersAdapter.COLL].find_one({"$or": [{"_id": query_id}, {"id": reminder_id}]})
                if target and _reminder_belongs_to_guild(target, guild_id, guild_channel_ids):
                    res = await db[RemindersAdapter.COLL].update_one(
                        {"$or": [{"_id": query_id}, {"id": reminder_id}]},
                        {"$set": {"is_active": 0, "is_active_legacy": False}}
                    )
                    if res.modified_count > 0:
                        success = True
            except Exception as e:
                logger.error(f"Failed direct MongoDB RemindersAdapter deactivation for {reminder_id}: {e}")

        # 3. Try to deactivate directly in the legacy database `whiteout_survival_bot`
        if not success:
            try:
                from db.mongo_client_wrapper import get_mongo_client
                from bson import ObjectId
                client = await get_mongo_client()
                db = client["whiteout_survival_bot"]
                query_id = reminder_id
                try:
                    query_id = ObjectId(reminder_id)
                except Exception:
                    pass
                
                target = await db["reminders"].find_one({"$or": [{"_id": query_id}, {"id": reminder_id}]})
                if target and _reminder_belongs_to_guild(target, guild_id, guild_channel_ids):
                    res = await db["reminders"].update_one(
                        {"$or": [{"_id": query_id}, {"id": reminder_id}]},
                        {"$set": {"is_active": False, "is_active_legacy": False, "is_active_str": "false"}}
                    )
                    if res.modified_count > 0:
                        success = True
            except Exception as e:
                logger.error(f"Failed direct legacy database deactivation for {reminder_id}: {e}")

    if success:
        return {"status": "success"}
    else:
        raise HTTPException(status_code=400, detail="Failed to delete reminder. It may not exist or does not belong to you.")

@router.patch("/{guild_id:int}/{reminder_id}")
async def update_reminder(request: Request, guild_id: int, reminder_id: str, payload: ReminderCreate):
    logger.info(f"Updating reminder {reminder_id} for guild {guild_id}: {payload.json()}")
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    # reminder_id can be int or ObjectId string
    rid = _normalize_reminder_id(reminder_id)
        
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

    storage = _storage(request)
    
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
        "recurrence_days": payload.recurrence_days,
        "original_time_pattern": recurring_info.get("pattern")
    }
    
    success = storage.update_reminder_fields(rid, update_data)
    
    if success:
        return {"status": "success"}
    else:
        raise HTTPException(status_code=400, detail="Failed to update reminder.")

# ─── Community Presets ────────────────────────────────────────────────────────

class CommunityPresetCreate(BaseModel):
    title: str
    badge: str = None
    message: str = ""
    body: str = None
    recurrence_type: str = "none"
    recurrence_interval: int = 1
    mention: str = "everyone"
    image_url: str = None
    thumbnail_url: str = None
    footer_text: str = None
    footer_icon_url: str = None
    author_url: str = None


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
        try:
            presets = await _refresh_items_discord_urls(presets)
        except Exception as e:
            logger.error(f"Failed to refresh presets Discord URLs: {e}")
        return {"presets": presets}
    except Exception as e:
        logger.error(f"Failed to fetch community presets: {e}")
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
