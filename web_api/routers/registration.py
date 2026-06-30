"""
Registration Router — Self-Service Server Configuration
Allows server admins to submit their alliance name + access code for global admin approval.
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Optional
import os
import logging
import httpx
import sys

import discord

try:
    from db.mongo_adapters import (
        BirthdayChannelAdapter,
        PendingConfigAdapter,
        PersistentViewsAdapter,
        RegistrationUserLimitsAdapter,
        ServerAllianceAdapter,
        WelcomeChannelAdapter,
        mongo_enabled,
    )
except ImportError:
    mongo_enabled = lambda: False
    BirthdayChannelAdapter = None
    PendingConfigAdapter = None
    PersistentViewsAdapter = None
    RegistrationUserLimitsAdapter = None
    ServerAllianceAdapter = None
    WelcomeChannelAdapter = None

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/register", tags=["Registration"])

BOT_OWNER_ID = os.getenv("BOT_OWNER_ID", "")
TOPGG_REVIEW_URL = "https://top.gg/bot/1399025185046134866?s=0e3c921b2b5f8"


# ── Request Models ─────────────────────────────────────────────────────────────

class SubmitRegistrationRequest(BaseModel):
    guild_id: str
    guild_name: str
    alliance_name: str = Field(..., min_length=1, max_length=100)
    access_code: str = Field(..., min_length=4, max_length=64)
    state: int = Field(..., ge=1)
    discord_user_id: str
    discord_username: str


class ReviewRequest(BaseModel):
    guild_id: str
    action: str  # "approve" or "deny"
    admin_user_id: str


class DeleteRegistrationRequest(BaseModel):
    """Request body for a user self-deleting their own server registration."""
    guild_id: str          # The guild_id to delete
    discord_user_id: str   # The Discord user requesting deletion (must match the registration owner)



class QuickSetupRequest(BaseModel):
    guild_id: str
    guild_name: str
    alliance_name: Optional[str] = Field(default=None, max_length=100)
    access_code: Optional[str] = Field(default=None, max_length=64)
    state: Optional[int] = Field(default=None, ge=1)
    discord_user_id: str = "0"
    discord_username: str = "Unknown"


def _approval_dm_message(guild_name: str, alliance_name: str) -> str:
    return (
        f"✅ **Your registration has been approved!**\n\n"
        f"**Server:** {guild_name}\n"
        f"**Alliance:** `{alliance_name}`\n\n"
        f"Your access code is now active. You can use `/manage` on the dashboard.\n"
        f"Use the code you set during registration to unlock the dashboard.\n\n"
        f"If Whiteout Survival Bot helps your server, please share a quick "
        f"review or feedback on Top.gg:\n{TOPGG_REVIEW_URL}"
    )


async def _get_registration_status(guild_id: int):
    doc = await PendingConfigAdapter.get_by_guild_async(guild_id)

    if doc and doc.get("status") == "approved":
        return "approved", doc

    if ServerAllianceAdapter:
        stored_password = await ServerAllianceAdapter.get_password_async(guild_id)
        if stored_password:
            alliance_id = await ServerAllianceAdapter.get_alliance_async(guild_id)
            return "approved", {
                "guild_id": guild_id,
                "alliance_name": f"Configured Alliance (ID: {alliance_id})" if alliance_id else "Configured Alliance",
                "status": "approved",
                "legacy_config": True,
            }

    if not doc:
        return "none", None
    return doc.get("status", "none"), doc


def _safe_registration_doc(doc):
    if not doc:
        return None
    safe = {
        "guild_id": doc.get("guild_id"),
        "guild_name": doc.get("guild_name"),
        "alliance_name": doc.get("alliance_name"),
        "state": doc.get("state"),
        "status": doc.get("status"),
        "submitted_at": doc.get("submitted_at"),
        "discord_username": doc.get("discord_username"),
    }
    if doc.get("legacy_config"):
        safe["legacy_config"] = True
    return safe


async def _quick_setup_feature_status(guild_id: int):
    welcome = await WelcomeChannelAdapter.get_async(guild_id) if WelcomeChannelAdapter else None
    birthday_channel_id = await BirthdayChannelAdapter.get_async(guild_id) if BirthdayChannelAdapter else None
    return {
        "welcome_configured": bool(welcome and welcome.get("enabled") and welcome.get("channel_id")),
        "welcome_channel_id": str(welcome.get("channel_id")) if welcome and welcome.get("channel_id") else "",
        "birthday_configured": bool(birthday_channel_id),
        "birthday_channel_id": str(birthday_channel_id) if birthday_channel_id else "",
    }


async def _ensure_text_channel(guild, preferred_name: str):
    normalized = preferred_name.lower().replace(" ", "-")
    for channel in guild.text_channels:
        if channel.name.lower() == normalized:
            return channel, False

    bot_member = guild.me
    perms = bot_member.guild_permissions if bot_member else None
    if not perms or not perms.manage_channels:
        raise HTTPException(
            status_code=403,
            detail=f"Bot needs Manage Channels permission to create #{normalized}."
        )

    channel = await guild.create_text_channel(
        normalized,
        reason="Dashboard Quick Setup"
    )
    return channel, True


def _build_birthday_manager_embed() -> discord.Embed:
    embed_text = (
        "Never miss a alliance member's birthday again!\n\n"
        "📅 **Add Your Birthday**\n"
        "Click \"Add Birthday\" and select your birth date.\n\n"
        "🎂 **Celebrate Together**\n"
        "Get your special day recognized and join the party vibes with the community.\n\n"
        "🔄 **Need to Update It?**\n"
        "Simply click the button again anytime to edit your entry.\n\n"
        "🗑️ **Want to Remove Your Birthday?**\n"
        "Use the \"Remove My Entry\" button whenever you like.\n\n"
        "✨ More members added = more celebrations, more fun, and a stronger community! 🎉"
    )
    embed = discord.Embed(title="Birthday Manager 🎉", description=embed_text, color=0xff69b4)
    embed.set_thumbnail(
        url="https://cdn.discordapp.com/attachments/1435569370389807144/1496492127494996119/image_34e5650b.png?ex=69ea1466&is=69e8c2e6&hm=d6fa1fe93d7c505e34d5c746c5e36f42d9e076c5dd7aab13a4f0683b1bb1dcde"
    )
    return embed


def _make_birthday_manager_view(bot):
    for module_name in ("app", "__main__"):
        module = sys.modules.get(module_name)
        view_cls = getattr(module, "BirthdayView", None) if module else None
        if view_cls:
            return view_cls(), "birthday"

    birthday_cog = bot.get_cog("BirthdaySystem") if bot else None
    if birthday_cog:
        try:
            import cogs.shared_views as shared_views
            return shared_views.BirthdayDashboardView(birthday_cog), None
        except Exception as view_error:
            logger.warning(f"Could not build fallback birthday dashboard view: {view_error}")

    return None, None


async def _send_birthday_manager_message(guild_id: int, channel, bot):
    try:
        from db.mongo_adapters import _get_db_main_async
        db = await _get_db_main_async()
        doc = await db[BirthdayChannelAdapter.COLL].find_one({"_id": str(guild_id)})
        existing_message_id = doc.get("quick_setup_message_id") if doc else None
        existing_channel_id = doc.get("quick_setup_channel_id") if doc else None

        if existing_message_id and str(existing_channel_id or channel.id) == str(channel.id):
            try:
                await channel.fetch_message(int(existing_message_id))
                return {"sent": False, "message_id": str(existing_message_id), "reason": "already_posted"}
            except Exception:
                pass

        perms = channel.permissions_for(channel.guild.me)
        if not perms.send_messages:
            return {"sent": False, "message_id": "", "reason": "missing_send_messages"}

        view, persistent_view_type = _make_birthday_manager_view(bot)
        message = await channel.send(embed=_build_birthday_manager_embed(), view=view)

        if view:
            try:
                bot.add_view(view, message_id=message.id)
            except Exception as add_view_error:
                logger.debug(f"Could not register birthday view for quick setup message {message.id}: {add_view_error}")

        if persistent_view_type and PersistentViewsAdapter:
            await PersistentViewsAdapter.register_view_async(
                guild_id=guild_id,
                channel_id=int(channel.id),
                message_id=int(message.id),
                view_type=persistent_view_type,
                metadata={"source": "quick_setup"}
            )

        await db[BirthdayChannelAdapter.COLL].update_one(
            {"_id": str(guild_id)},
            {"$set": {
                "quick_setup_message_id": int(message.id),
                "quick_setup_channel_id": int(channel.id),
            }},
            upsert=True,
        )
        return {"sent": True, "message_id": str(message.id), "reason": "posted"}
    except Exception as e:
        logger.warning(f"Could not post birthday manager message for guild {guild_id}: {e}")
        return {"sent": False, "message_id": "", "reason": "send_failed"}


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/status")
async def check_registration_status(guild_id: str):
    """
    Check the registration status for a guild.
    Returns: { status: 'none' | 'pending' | 'approved' | 'denied', data: {...} }
    """
    if not mongo_enabled() or not PendingConfigAdapter:
        raise HTTPException(status_code=500, detail="Database not available")

    status, doc = await _get_registration_status(int(guild_id))
    
    if status == "approved" and doc and not doc.get("legacy_config"):
        safe_doc = {
            "guild_id": doc.get("guild_id"),
            "guild_name": doc.get("guild_name"),
            "alliance_name": doc.get("alliance_name"),
            "state": doc.get("state"),
            "status": "approved",
            "submitted_at": doc.get("submitted_at"),
            "discord_username": doc.get("discord_username"),
        }
        return {"status": "approved", "data": safe_doc}

    if status == "approved" and doc and doc.get("legacy_config"):
        return {"status": "approved", "data": doc}

    # Return pending, denied, or none
    if not doc:
        return {"status": "none", "data": None}
        
    safe_doc = {
        "guild_id": doc.get("guild_id"),
        "guild_name": doc.get("guild_name"),
        "alliance_name": doc.get("alliance_name"),
        "state": doc.get("state"),
        "status": doc.get("status"),
        "submitted_at": doc.get("submitted_at"),
        "discord_username": doc.get("discord_username"),
    }
    return {"status": doc.get("status", "none"), "data": safe_doc}


@router.get("/quick-setup/status")
async def check_quick_setup_status(guild_id: str, discord_user_id: str = "0"):
    if not mongo_enabled() or not PendingConfigAdapter:
        raise HTTPException(status_code=500, detail="Database not available")

    guild_int = int(guild_id)
    reg_status, reg_doc = await _get_registration_status(guild_int)
    features = await _quick_setup_feature_status(guild_int)

    user_registration = {
        "has_registration": False,
        "active_count": 0,
        "max_servers": 1,
        "limit_reached": False,
    }
    if discord_user_id and discord_user_id != "0":
        active_user_regs = await PendingConfigAdapter.get_active_by_user_async(int(discord_user_id))
        max_servers = 1
        if RegistrationUserLimitsAdapter:
            max_servers = await RegistrationUserLimitsAdapter.get_limit_async(int(discord_user_id))
        first = active_user_regs[0] if active_user_regs else {}
        user_registration = {
            "has_registration": bool(active_user_regs),
            "guild_id": first.get("guild_id"),
            "guild_name": first.get("guild_name"),
            "status": first.get("status"),
            "active_count": len(active_user_regs),
            "max_servers": max_servers,
            "limit_reached": len(active_user_regs) >= max_servers,
        }

    return {
        "registration_status": reg_status,
        "registration": _safe_registration_doc(reg_doc),
        "features": features,
        "user_registration": user_registration,
        "all_configured": bool(features["welcome_configured"] and features["birthday_configured"] and reg_status == "approved"),
    }


@router.post("/quick-setup")
async def run_quick_setup(body: QuickSetupRequest, request: Request):
    if not mongo_enabled() or not PendingConfigAdapter or not WelcomeChannelAdapter or not BirthdayChannelAdapter:
        raise HTTPException(status_code=500, detail="Database not available")

    guild_int = int(body.guild_id)
    bot = getattr(request.app.state, "bot", None)
    guild = bot.get_guild(guild_int) if bot else None
    if not guild:
        raise HTTPException(status_code=404, detail="Bot is not available in this server.")

    welcome_channel, welcome_created = await _ensure_text_channel(guild, "welcome")
    birthday_channel, birthday_created = await _ensure_text_channel(guild, "birthday")
    await WelcomeChannelAdapter.set_async(guild_int, int(welcome_channel.id), True)
    await BirthdayChannelAdapter.set_async(guild_int, int(birthday_channel.id))
    birthday_manager_message = await _send_birthday_manager_message(guild_int, birthday_channel, bot)

    reg_status, reg_doc = await _get_registration_status(guild_int)
    submitted_registration = False
    skipped_reason = None

    if reg_status == "approved":
        skipped_reason = "server_already_registered"
    elif reg_status == "pending":
        skipped_reason = "registration_pending"
    else:
        user_id = int(body.discord_user_id or 0)
        active_user_regs = await PendingConfigAdapter.get_active_by_user_async(user_id) if user_id else []
        max_servers = 1
        if user_id and RegistrationUserLimitsAdapter:
            max_servers = await RegistrationUserLimitsAdapter.get_limit_async(user_id)
        has_same_guild = any(str(item.get("guild_id")) == str(body.guild_id) for item in active_user_regs)

        if user_id and not has_same_guild and len(active_user_regs) >= max_servers:
            skipped_reason = "user_limit_reached"
        else:
            alliance_name = (body.alliance_name or "").strip()
            access_code = body.access_code or ""
            if len(alliance_name) < 1 or len(access_code) < 4 or not body.state:
                skipped_reason = "registration_details_required"
            else:
                submit_body = SubmitRegistrationRequest(
                    guild_id=body.guild_id,
                    guild_name=body.guild_name,
                    alliance_name=alliance_name,
                    access_code=access_code,
                    state=int(body.state),
                    discord_user_id=body.discord_user_id,
                    discord_username=body.discord_username,
                )
                submit_result = await submit_registration(submit_body, request)
                submitted_registration = bool(submit_result.get("success"))
                reg_status, reg_doc = await _get_registration_status(guild_int)

    features = await _quick_setup_feature_status(guild_int)
    return {
        "success": True,
        "message": "Quick Setup completed.",
        "features": features,
        "channels": {
            "welcome": {"id": str(welcome_channel.id), "name": welcome_channel.name, "created": welcome_created},
            "birthday": {"id": str(birthday_channel.id), "name": birthday_channel.name, "created": birthday_created},
        },
        "birthday_manager_message": birthday_manager_message,
        "registration_status": reg_status,
        "registration_submitted": submitted_registration,
        "skipped_reason": skipped_reason,
        "registration": _safe_registration_doc(reg_doc),
        "all_configured": bool(features["welcome_configured"] and features["birthday_configured"] and reg_status == "approved"),
    }


@router.get("/user-check")
async def check_user_registration(discord_user_id: str):
    """
    Check if a Discord user already has a pending/approved registration on any server.
    Enforces the one-user-one-server rule.
    """
    if not mongo_enabled() or not PendingConfigAdapter:
        raise HTTPException(status_code=500, detail="Database not available")

    user_id = int(discord_user_id)
    docs = await PendingConfigAdapter.get_active_by_user_async(user_id)
    max_servers = 1
    if RegistrationUserLimitsAdapter:
        max_servers = await RegistrationUserLimitsAdapter.get_limit_async(user_id)

    if not docs:
        return {"has_registration": False, "active_count": 0, "max_servers": max_servers, "limit_reached": False}

    doc = docs[0]

    return {
        "has_registration": True,
        "guild_id": doc.get("guild_id"),
        "guild_name": doc.get("guild_name"),
        "status": doc.get("status"),
        "active_count": len(docs),
        "max_servers": max_servers,
        "limit_reached": len(docs) >= max_servers,
    }


@router.post("/submit")
async def submit_registration(body: SubmitRegistrationRequest, request: Request):
    """
    Submit a self-service registration request.
    Validates one-user-one-server rule, stores as pending, DMs global admin.
    """
    if not mongo_enabled() or not PendingConfigAdapter:
        raise HTTPException(status_code=500, detail="Database not available")

    user_id = int(body.discord_user_id)
    active_user_regs = await PendingConfigAdapter.get_active_by_user_async(user_id)
    max_servers = 1
    if RegistrationUserLimitsAdapter:
        max_servers = await RegistrationUserLimitsAdapter.get_limit_async(user_id)
    has_same_guild = any(str(item.get("guild_id")) == str(body.guild_id) for item in active_user_regs)
    if not has_same_guild and len(active_user_regs) >= max_servers:
        existing_user = active_user_regs[0] if active_user_regs else {}
        raise HTTPException(
            status_code=409,
            detail={
                "code": "limit_reached",
                "message": (
                    f"Limit reached. You already have {len(active_user_regs)} of "
                    f"{max_servers} allowed server registration(s)."
                ),
                "existing_registration": {
                    "guild_id": existing_user.get("guild_id"),
                    "guild_name": existing_user.get("guild_name", "Unknown Server"),
                    "alliance_name": existing_user.get("alliance_name", "Unknown"),
                    "state": existing_user.get("state"),
                    "status": existing_user.get("status"),
                    "submitted_at": existing_user.get("submitted_at"),
                },
                "max_servers": max_servers,
                "active_count": len(active_user_regs),
            }
        )

    # Check if this guild already has an approved registration
    existing_guild = await PendingConfigAdapter.get_by_guild_async(int(body.guild_id))
    if existing_guild and existing_guild.get("status") == "approved":
        raise HTTPException(
            status_code=409,
            detail="This server already has an approved configuration. Use your existing access code."
        )

    # Submit the request
    ok = await PendingConfigAdapter.submit_async(
        guild_id=int(body.guild_id),
        guild_name=body.guild_name,
        alliance_name=body.alliance_name,
        access_code=body.access_code,
        discord_user_id=int(body.discord_user_id),
        discord_username=body.discord_username,
        state=body.state
    )
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to save registration request")

    # Notify global admin via Discord DM
    try:
        bot = getattr(request.app.state, "bot", None)
        if bot and BOT_OWNER_ID:
            admin_user = await bot.fetch_user(int(BOT_OWNER_ID))
            if admin_user:
                msg = (
                    f"📋 **New Server Registration Request**\n\n"
                    f"**Server:** {body.guild_name} (`{body.guild_id}`)\n"
                    f"**Alliance Name:** `{body.alliance_name}`\n"
                    f"**Requested by:** {body.discord_username} (`{body.discord_user_id}`)\n"
                    f"**Access Code:** ||`{body.access_code}`||\n\n"
                    f"To approve or deny, use the admin panel:\n"
                    f"Reply with `/reg-approve {body.guild_id}` or `/reg-deny {body.guild_id}`\n"
                    f"Or visit: `/api/register/pending` (admin API)"
                )
                await admin_user.send(msg)
    except Exception as dm_err:
        logger.warning(f"Could not DM admin about registration: {dm_err}")

    return {
        "success": True,
        "message": "Registration submitted successfully. Awaiting admin approval."
    }


@router.get("/pending")
async def get_pending_registrations(request: Request):
    """
    Admin endpoint: get all pending registration requests.
    Requires the request to come from the bot owner (checked via BOT_OWNER_ID header).
    """
    if not mongo_enabled() or not PendingConfigAdapter:
        raise HTTPException(status_code=500, detail="Database not available")

    # Simple admin check via header
    admin_id = request.headers.get("X-Admin-Id", "")
    if not BOT_OWNER_ID or admin_id != BOT_OWNER_ID:
        raise HTTPException(status_code=403, detail="Admin access required")

    docs = await PendingConfigAdapter.get_all_pending_async()
    # Strip sensitive data
    result = []
    for doc in docs:
        result.append({
            "guild_id": doc.get("guild_id"),
            "guild_name": doc.get("guild_name"),
            "alliance_name": doc.get("alliance_name"),
            "state": doc.get("state"),
            "discord_username": doc.get("discord_username"),
            "discord_user_id": doc.get("discord_user_id"),
            "submitted_at": doc.get("submitted_at"),
        })
    return {"pending": result}


@router.post("/review")
async def review_registration(body: ReviewRequest, request: Request):
    """
    Admin endpoint: approve or deny a pending registration.
    """
    if not mongo_enabled() or not PendingConfigAdapter:
        raise HTTPException(status_code=500, detail="Database not available")

    # Validate admin
    admin_id = request.headers.get("X-Admin-Id", "")
    if not BOT_OWNER_ID or admin_id != BOT_OWNER_ID:
        raise HTTPException(status_code=403, detail="Admin access required")

    if body.action not in ("approve", "deny"):
        raise HTTPException(status_code=400, detail="action must be 'approve' or 'deny'")

    # Get the pending doc to notify the submitter
    doc = await PendingConfigAdapter.get_by_guild_async(int(body.guild_id))
    if not doc or doc.get("status") != "pending":
        raise HTTPException(status_code=404, detail="No pending registration found for this guild")

    if body.action == "approve":
        ok = await PendingConfigAdapter.approve_async(int(body.guild_id), int(body.admin_user_id))
        status_msg = "approved"

        # Create auto-channels
        try:
            bot = getattr(request.app.state, "bot", None)
            if bot:
                guild = bot.get_guild(int(body.guild_id))
                if guild:
                    # player-ids channel
                    player_ids_channel = discord.utils.get(guild.text_channels, name="🆔┃player-ids")
                    if not player_ids_channel:
                        player_ids_channel = await guild.create_text_channel("🆔┃player-ids")
                        
                    # giftcode channel (auto redeem log)
                    giftcode_channel = discord.utils.get(guild.text_channels, name="🎁┃𝐠𝐢𝐟𝐭𝐜𝐨𝐝𝐞")
                    if not giftcode_channel:
                        giftcode_channel = await guild.create_text_channel("🎁┃𝐠𝐢𝐟𝐭𝐜𝐨𝐝𝐞")
                        
                    # welcome channel
                    welcome_channel = discord.utils.get(guild.text_channels, name="🎉┃welcome")
                    if not welcome_channel:
                        welcome_channel = await guild.create_text_channel("🎉┃welcome")
                    
                    import sqlite3
                    from datetime import datetime
                    
                    alliance_name = doc.get("alliance_name", "")
                    submitter_id = doc.get("discord_user_id", "0")
                    
                    # 1. Player IDs config
                    try:
                        alliance_id = 0
                        try:
                            with sqlite3.connect('db/alliance.sqlite', timeout=10) as alliance_db:
                                ac_cursor = alliance_db.cursor()
                                ac_cursor.execute("SELECT alliance_id FROM alliance_list WHERE name = ?", (alliance_name,))
                                row = ac_cursor.fetchone()
                                if row:
                                    alliance_id = row[0]
                                else:
                                    ac_cursor.execute("INSERT INTO alliance_list (name, discord_server_id) VALUES (?, ?)", (alliance_name, int(body.guild_id)))
                                    alliance_id = ac_cursor.lastrowid
                                    alliance_db.commit()
                                    try:
                                        from db.mongo_adapters import AlliancesAdapter
                                        if mongo_enabled() and hasattr(AlliancesAdapter, 'upsert_async'):
                                            await AlliancesAdapter.upsert_async(alliance_id, alliance_name, int(body.guild_id))
                                        elif mongo_enabled() and hasattr(AlliancesAdapter, 'upsert'):
                                            AlliancesAdapter.upsert(alliance_id, alliance_name, int(body.guild_id))
                                    except Exception as mongo_err:
                                        logger.error(f"Error saving new alliance to mongo: {mongo_err}")
                        except Exception as e:
                            logger.error(f"Error finding/creating alliance_id for {alliance_name}: {e}")
                            
                        if alliance_id:
                            try:
                                from db.mongo_adapters import ServerAllianceAdapter
                                if mongo_enabled():
                                    if hasattr(ServerAllianceAdapter, 'set_alliance_async'):
                                        await ServerAllianceAdapter.set_alliance_async(int(body.guild_id), alliance_id, int(submitter_id))
                                    else:
                                        ServerAllianceAdapter.set_alliance(int(body.guild_id), alliance_id, int(submitter_id))
                            except Exception as e:
                                logger.error(f"Failed to set server alliance for {body.guild_id}: {e}")

                            try:
                                with sqlite3.connect('db/settings.sqlite', timeout=10) as sdb:
                                    sc = sdb.cursor()
                                    sc.execute("INSERT OR IGNORE INTO admin (id, is_initial) VALUES (?, 0)", (int(submitter_id),))
                                    sc.execute("INSERT OR IGNORE INTO adminserver (admin, alliances_id) VALUES (?, ?)", (int(submitter_id), alliance_id))
                                    sdb.commit()
                            except Exception as e:
                                logger.error(f"Failed to update sqlite adminserver for {submitter_id}: {e}")

                        with sqlite3.connect('db/id_channel.sqlite', timeout=10) as db:
                            cursor = db.cursor()
                            cursor.execute('''CREATE TABLE IF NOT EXISTS id_channels
                                         (guild_id INTEGER, alliance_id INTEGER, channel_id INTEGER, created_at TEXT, created_by INTEGER, UNIQUE(guild_id, channel_id))''')
                            cursor.execute("INSERT OR REPLACE INTO id_channels (guild_id, alliance_id, channel_id, created_at, created_by) VALUES (?, ?, ?, ?, ?)",
                                         (int(body.guild_id), alliance_id, player_ids_channel.id, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), int(body.admin_user_id)))
                            db.commit()
                            
                        try:
                            from db.mongo_adapters import IDChannelsAdapter
                            if mongo_enabled() and hasattr(IDChannelsAdapter, 'set_channel_async'):
                                await IDChannelsAdapter.set_channel_async(int(body.guild_id), player_ids_channel.id, alliance_id)
                        except Exception:
                            pass
                            
                        await player_ids_channel.send(
                            "This channel has been configured for auto redeem, alliance members can write their player id here to get registered for fastest redeem."
                        )
                    except Exception as e:
                        logger.error(f"Failed to setup player_ids channel: {e}")

                    # 2. Giftcode Channel config
                    try:
                        try:
                            from db.mongo_adapters import AutoRedeemChannelsAdapter, AutoRedeemSettingsAdapter
                            if mongo_enabled():
                                if hasattr(AutoRedeemChannelsAdapter, 'set_channel_async'):
                                    await AutoRedeemChannelsAdapter.set_channel_async(int(body.guild_id), giftcode_channel.id, 0)
                                elif hasattr(AutoRedeemChannelsAdapter, 'set_channel'):
                                    AutoRedeemChannelsAdapter.set_channel(int(body.guild_id), giftcode_channel.id, 0)
                                    
                                if hasattr(AutoRedeemSettingsAdapter, 'update_settings_async'):
                                    await AutoRedeemSettingsAdapter.update_settings_async(int(body.guild_id), {'enabled': True})
                        except Exception:
                            pass
                        
                        with sqlite3.connect('db/giftcode.sqlite', timeout=10) as gcdb:
                            g_cursor = gcdb.cursor()
                            g_cursor.execute("CREATE TABLE IF NOT EXISTS auto_redeem_channels (guild_id INTEGER, channel_id INTEGER, role_id INTEGER, PRIMARY KEY (guild_id))")
                            g_cursor.execute("INSERT OR REPLACE INTO auto_redeem_channels (guild_id, channel_id, role_id) VALUES (?, ?, ?)", (int(body.guild_id), giftcode_channel.id, 0))
                            
                            g_cursor.execute("CREATE TABLE IF NOT EXISTS auto_redeem_settings (guild_id INTEGER PRIMARY KEY, enabled INTEGER DEFAULT 0, priority INTEGER DEFAULT 999, updated_by INTEGER, updated_at TEXT)")
                            g_cursor.execute("""
                                INSERT INTO auto_redeem_settings (guild_id, enabled, updated_by, updated_at)
                                VALUES (?, 1, ?, ?)
                                ON CONFLICT(guild_id) DO UPDATE SET
                                    enabled = 1,
                                    updated_by = excluded.updated_by,
                                    updated_at = excluded.updated_at
                            """, (int(body.guild_id), int(body.admin_user_id), datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
                            gcdb.commit()
                    except Exception as e:
                        logger.error(f"Failed to setup giftcode channel: {e}")
                        
                    # 3. Welcome Channel config
                    try:
                        try:
                            from db.mongo_adapters import WelcomeChannelAdapter
                            if mongo_enabled():
                                if hasattr(WelcomeChannelAdapter, 'set_async'):
                                    await WelcomeChannelAdapter.set_async(int(body.guild_id), welcome_channel.id, enabled=True)
                                elif hasattr(WelcomeChannelAdapter, 'set'):
                                    WelcomeChannelAdapter.set(int(body.guild_id), welcome_channel.id, enabled=True)
                        except Exception:
                            pass
                    except Exception as e:
                        logger.error(f"Failed to setup welcome channel: {e}")
        except Exception as e:
            logger.error(f"Error creating auto-channels for {body.guild_id} via API: {e}")
    else:
        ok = await PendingConfigAdapter.deny_async(int(body.guild_id), int(body.admin_user_id))
        status_msg = "denied"

    if not ok:
        raise HTTPException(status_code=500, detail=f"Failed to {body.action} registration")

    # Try to notify the submitter via DM
    try:
        bot = getattr(request.app.state, "bot", None)
        if bot and doc.get("discord_user_id"):
            user = await bot.fetch_user(int(doc["discord_user_id"]))
            if user:
                if body.action == "approve":
                    msg = _approval_dm_message(
                        doc.get("guild_name", "Unknown Server"),
                        doc.get("alliance_name", "Unknown Alliance"),
                    )
                else:
                    msg = (
                        f"❌ **Your registration request was denied.**\n\n"
                        f"**Server:** {doc.get('guild_name')}\n"
                        f"Please contact the bot administrator for more information.\n"
                        f"You can submit a new registration request when ready."
                    )
                await user.send(msg)
    except Exception as dm_err:
        logger.warning(f"Could not DM submitter about review decision: {dm_err}")

    return {
        "success": True,
        "action": body.action,
        "guild_id": body.guild_id,
        "message": f"Registration {status_msg} successfully"
    }


@router.post("/delete")
async def delete_own_registration(body: DeleteRegistrationRequest, request: Request):
    """
    Self-service registration deletion.

    Allows a Discord user to permanently delete their own server registration so they can
    register a different server. The requesting user must be the original submitter.

    Steps the frontend should take:
      1. Call POST /api/register/submit → receives 409 with code='limit_reached'
      2. Show warning UI with existing server details (from the 409 response)
      3. User confirms twice (Step 1 of 2 / Step 2 of 2)
      4. Frontend calls POST /api/register/delete with guild_id + discord_user_id
      5. On 200: allow the user to submit a new registration immediately
    """
    if not mongo_enabled() or not PendingConfigAdapter:
        raise HTTPException(status_code=500, detail="Database not available")

    guild_id = int(body.guild_id)
    user_id = int(body.discord_user_id)

    # Fetch the registration document for this guild
    doc = await PendingConfigAdapter.get_by_guild_async(guild_id)
    if not doc:
        raise HTTPException(
            status_code=404,
            detail="No registration found for this server. It may have already been deleted."
        )

    # Security: only the original submitter can self-delete
    owner_id = str(doc.get("discord_user_id", ""))
    if owner_id != str(user_id):
        raise HTTPException(
            status_code=403,
            detail=(
                "You are not the owner of this registration. "
                "Only the original submitter can delete it."
            )
        )

    ok = await PendingConfigAdapter.delete_registration_async(guild_id, user_id)
    if not ok:
        raise HTTPException(
            status_code=500,
            detail="Failed to delete registration. Please try again later."
        )

    # Notify user via DM if bot is available
    try:
        bot = getattr(request.app.state, "bot", None)
        if bot and user_id:
            user = await bot.fetch_user(user_id)
            if user:
                await user.send(
                    f"🗑️ **Registration Deleted**\n\n"
                    f"The registration for **{doc.get('guild_name', body.guild_id)}** "
                    f"has been permanently deleted.\n\n"
                    f"You can now register a new server at any time."
                )
    except Exception as dm_err:
        logger.warning(f"Could not DM user {user_id} about self-deletion: {dm_err}")

    return {
        "success": True,
        "message": (
            f"Registration for '{doc.get('guild_name', body.guild_id)}' permanently deleted. "
            "You may now register a new server."
        ),
        "deleted_guild_id": body.guild_id,
        "deleted_guild_name": doc.get("guild_name"),
    }
