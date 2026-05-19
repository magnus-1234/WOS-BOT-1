from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from pydantic import BaseModel
import logging
import os
import uuid
from datetime import datetime
from typing import Optional

try:
    from db.mongo_adapters import WelcomeChannelAdapter, BirthdaysAdapter, BirthdayChannelAdapter, AutoTranslateAdapter, mongo_enabled
except ImportError:
    mongo_enabled = lambda: False
    WelcomeChannelAdapter = None

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/settings", tags=["Settings"])

# ─────────────────────────────────────────
# Image Upload
# ─────────────────────────────────────────
@router.post("/upload")
async def upload_image(file: UploadFile = File(...)):
    if not file:
        return {"status": "error", "message": "No file uploaded"}

    content = await file.read()
    # Limit size to 4MB to safely fit inside MongoDB 16MB document limit
    if len(content) > 4 * 1024 * 1024:
        return {"status": "error", "message": "Image too large (max 4MB)"}

    import base64
    ext = file.filename.split('.')[-1].lower() if '.' in file.filename else 'png'
    mime_type = "image/jpeg" if ext in ['jpg', 'jpeg'] else f"image/{ext}"
    
    b64_data = base64.b64encode(content).decode('utf-8')
    data_uri = f"data:{mime_type};base64,{b64_data}"

    return {"url": data_uri}


# ─────────────────────────────────────────
# Welcome Settings
# ─────────────────────────────────────────
DEFAULT_WELCOME_TEXT  = "Hi {mention} Welcome to the {server}🥳"
DEFAULT_FOOTER_TEXT   = "Member joined • {date}"
DEFAULT_EMBED_COLOR   = "#e53e3e"   # Red (matches the demo embed)

class WelcomeSettings(BaseModel):
    enabled: bool = False
    channel_id: str = ""
    bg_image_url: str = ""
    welcome_text: str = DEFAULT_WELCOME_TEXT
    embed_title: str = "Welcome {username}"
    embed_subtitle: str = "to {server}"
    embed_color: str = DEFAULT_EMBED_COLOR
    footer_text: str = DEFAULT_FOOTER_TEXT


def _default_welcome():
    return {
        "enabled": False,
        "channel_id": "",
        "bg_image_url": "",
        "welcome_text": DEFAULT_WELCOME_TEXT,
        "embed_title": "Welcome {username}",
        "embed_subtitle": "to {server}",
        "embed_color": DEFAULT_EMBED_COLOR,
        "footer_text": DEFAULT_FOOTER_TEXT,
    }


@router.get("/welcome/{guild_id}")
async def get_welcome_settings(guild_id: int):
    if not mongo_enabled():
        return _default_welcome()

    doc = await WelcomeChannelAdapter.get_async(guild_id)
    if not doc:
        return _default_welcome()

    return {
        "enabled":       doc.get("enabled", False),
        "channel_id":    str(doc.get("channel_id", "")) if doc.get("channel_id") else "",
        "bg_image_url":  doc.get("bg_image_url", ""),
        "welcome_text":  doc.get("welcome_text",  DEFAULT_WELCOME_TEXT),
        "embed_title":   doc.get("embed_title",   "Welcome {username}"),
        "embed_subtitle": doc.get("embed_subtitle", "to {server}"),
        "embed_color":   doc.get("embed_color",   DEFAULT_EMBED_COLOR),
        "footer_text":   doc.get("footer_text",   DEFAULT_FOOTER_TEXT),
    }


@router.post("/welcome/{guild_id}")
async def save_welcome_settings(guild_id: int, settings: WelcomeSettings):
    if not mongo_enabled():
        return {"status": "error", "message": "MongoDB not enabled"}

    channel_id = int(settings.channel_id) if settings.channel_id else 0
    await WelcomeChannelAdapter.set_async(guild_id, channel_id, settings.enabled)

    if settings.bg_image_url:
        await WelcomeChannelAdapter.set_bg_image_async(guild_id, settings.bg_image_url)

    # Persist custom text fields directly on the document
    try:
        from db.mongo_adapters import _get_db_main_async
        db = await _get_db_main_async()
        await db[WelcomeChannelAdapter.COLL].update_one(
            {"_id": str(guild_id)},
            {"$set": {
                "welcome_text":  settings.welcome_text,
                "embed_title":   settings.embed_title,
                "embed_subtitle": settings.embed_subtitle,
                "embed_color":   settings.embed_color,
                "footer_text":   settings.footer_text,
                "updated_at":    datetime.utcnow().isoformat()
            }},
            upsert=True
        )
    except Exception as e:
        logger.warning(f"Could not persist extra welcome fields: {e}")

    return {"status": "success"}


# ─────────────────────────────────────────
# Birthday Settings
# ─────────────────────────────────────────
class BirthdaySettings(BaseModel):
    channel_id: str


@router.get("/birthday/{guild_id}")
async def get_birthday_settings(guild_id: int):
    if not mongo_enabled() or not BirthdayChannelAdapter:
        return {"channel_id": ""}

    channel_id = await BirthdayChannelAdapter.get_async(guild_id)
    return {"channel_id": str(channel_id) if channel_id else ""}


@router.post("/birthday/{guild_id}")
async def save_birthday_settings(guild_id: int, settings: BirthdaySettings):
    if not mongo_enabled() or not BirthdayChannelAdapter:
        return {"status": "error", "message": "MongoDB not enabled"}

    channel_id = int(settings.channel_id) if settings.channel_id else 0
    if channel_id:
        await BirthdayChannelAdapter.set_async(guild_id, channel_id)
    else:
        await BirthdayChannelAdapter.remove_async(guild_id)
    return {"status": "success"}


# ─────────────────────────────────────────
# Birthday Records Directory
# ─────────────────────────────────────────
class BirthdayRecordInput(BaseModel):
    user_id: str
    day: int
    month: int
    player_id: Optional[str] = None


@router.get("/birthday/{guild_id}/records")
async def get_birthday_records(guild_id: int, request: Request):
    if not mongo_enabled() or not BirthdaysAdapter:
        return []

    bot = getattr(request.app.state, "bot", None)
    guild = bot.get_guild(guild_id) if bot else None

    # Load all birthdays
    all_bdays = await BirthdaysAdapter.load_all_async()
    records = []

    import calendar
    now = datetime.utcnow()
    current_year = now.year

    for key, data in all_bdays.items():
        is_match = False
        user_id_str = ""

        if "_" in key:
            parts = key.split("_")
            if len(parts) >= 2:
                g_id_str, u_id_str = parts[0], parts[1]
                if str(guild_id) == g_id_str:
                    is_match = True
                    user_id_str = u_id_str
        else:
            # Legacy/global record: match if user is in this guild
            user_id_str = key
            if guild and guild.get_member(int(user_id_str)):
                is_match = True

        if is_match:
            try:
                user_id = int(user_id_str)
            except ValueError:
                continue

            day = data.get("day", 1)
            month = data.get("month", 1)
            player_id = data.get("player_id")

            # Resolve member avatar and username
            username = f"User {user_id}"
            avatar_url = "https://cdn.discordapp.com/embed/avatars/0.png"

            if guild:
                member = guild.get_member(user_id)
                if member:
                    username = member.display_name
                    avatar_url = str(member.display_avatar.url) if member.display_avatar else avatar_url
                elif bot:
                    try:
                        user = bot.get_user(user_id) or await bot.fetch_user(user_id)
                        if user:
                            username = user.name
                            avatar_url = str(user.display_avatar.url) if user.display_avatar else avatar_url
                    except Exception:
                        pass

            # Calculate countdown
            try:
                bday_this_year = datetime(current_year, month, day)
            except ValueError:
                bday_this_year = datetime(current_year, 3, 1)

            if bday_this_year.date() < now.date():
                try:
                    bday_next_year = datetime(current_year + 1, month, day)
                except ValueError:
                    bday_next_year = datetime(current_year + 1, 3, 1)
                days_until = (bday_next_year.date() - now.date()).days
            else:
                days_until = (bday_this_year.date() - now.date()).days

            month_name = calendar.month_name[month]
            date_str = f"{month_name} {day}"

            records.append({
                "user_id": user_id_str,
                "username": username,
                "avatar": avatar_url,
                "day": day,
                "month": month,
                "player_id": player_id or "",
                "days_until": days_until,
                "date_str": date_str
            })

    # Sort by days_until ascending
    records.sort(key=lambda x: x["days_until"])
    return records


@router.post("/birthday/{guild_id}/records")
async def register_birthday_record(guild_id: int, payload: BirthdayRecordInput, request: Request):
    if not mongo_enabled() or not BirthdaysAdapter:
        raise HTTPException(status_code=503, detail="MongoDB is not enabled.")

    try:
        user_id = int(payload.user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid User ID format.")

    day = payload.day
    month = payload.month
    
    try:
        datetime(2000, month, day)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid birthday date details.")

    pid = payload.player_id.strip() if payload.player_id else None
    if pid:
        if not pid.isdigit() or len(pid) != 9:
            raise HTTPException(status_code=400, detail="Player ID must be exactly 9 digits.")

    success = await BirthdaysAdapter.set_async(guild_id, user_id, day, month, pid)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to save birthday record to database.")

    bot = getattr(request.app.state, "bot", None)
    if bot:
        birthday_cog = bot.get_cog("BirthdaySystem")
        if birthday_cog:
            try:
                birthday_cog.load_birthdays()
            except Exception as e:
                logger.error(f"Failed to reload bot birthday cache: {e}")

    return {"status": "success"}


@router.delete("/birthday/{guild_id}/records/{user_id}")
async def delete_birthday_record(guild_id: int, user_id: int, request: Request):
    if not mongo_enabled() or not BirthdaysAdapter:
        raise HTTPException(status_code=503, detail="MongoDB is not enabled.")

    success = await BirthdaysAdapter.remove_async(guild_id, user_id)
    if not success:
        # Fallback delete
        success = await BirthdaysAdapter.remove_async(None, user_id)
        if not success:
            raise HTTPException(status_code=404, detail="Birthday record not found.")

    bot = getattr(request.app.state, "bot", None)
    if bot:
        birthday_cog = bot.get_cog("BirthdaySystem")
        if birthday_cog:
            try:
                birthday_cog.load_birthdays()
            except Exception as e:
                logger.error(f"Failed to reload bot birthday cache: {e}")

    return {"status": "success"}


# ─────────────────────────────────────────
# Auto-Translate Settings
# ─────────────────────────────────────────
@router.get("/translate/{guild_id}")
async def get_translate_configs(guild_id: int):
    if not mongo_enabled():
        return []
    configs = await AutoTranslateAdapter.get_guild_configs_async(guild_id)
    return configs


class TranslateSettings(BaseModel):
    config_id: Optional[str] = None
    name: str
    source_channel_id: str
    target_channel_id: str
    source_language: str
    target_language: str
    style: str
    enabled: bool
    # Extra fields to match bot
    delete_original: bool = False
    auto_disappear: int = 0
    ignore_if_source_is_target: bool = True
    ignore_if_source_is_not_input: bool = False
    skip_attachments: bool = False
    attachment_mode: str = "link"
    min_text_length: int = 10


@router.post("/translate/{guild_id}")
async def save_translate_configs(guild_id: int, settings: TranslateSettings):
    if not mongo_enabled():
        return {"status": "error", "message": "MongoDB not enabled"}

    data = {
        "name": settings.name,
        "source_channel_id": int(settings.source_channel_id) if settings.source_channel_id else 0,
        "target_channel_id": int(settings.target_channel_id) if settings.target_channel_id else 0,
        "source_language": settings.source_language,
        "target_language": settings.target_language,
        "style": settings.style,
        "enabled": settings.enabled,
        # Extra fields
        "delete_original": settings.delete_original,
        "auto_disappear": settings.auto_disappear,
        "ignore_if_source_is_target": settings.ignore_if_source_is_target,
        "ignore_if_source_is_not_input": settings.ignore_if_source_is_not_input,
        "skip_attachments": settings.skip_attachments,
        "attachment_mode": settings.attachment_mode,
        "min_text_length": settings.min_text_length
    }

    if settings.config_id:
        success = await AutoTranslateAdapter.update_config_async(settings.config_id, data)
        if not success:
            return {"status": "error", "message": "Failed to update config"}
    else:
        config_id = await AutoTranslateAdapter.create_config_async(guild_id, data)
        if not config_id:
            return {"status": "error", "message": "Failed to create config"}

    return {"status": "success"}


@router.delete("/translate/{guild_id}/{config_id}")
async def delete_translate_config(guild_id: int, config_id: str):
    if not mongo_enabled():
        return {"status": "error", "message": "MongoDB not enabled"}

    success = await AutoTranslateAdapter.delete_config_async(config_id)
    if success:
        return {"status": "success"}
    return {"status": "error", "message": "Failed to delete config"}
