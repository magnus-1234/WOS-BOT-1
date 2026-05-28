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

try:
    from db.mongo_adapters import (
        BirthdayChannelAdapter,
        PendingConfigAdapter,
        RegistrationUserLimitsAdapter,
        ServerAllianceAdapter,
        WelcomeChannelAdapter,
        mongo_enabled,
    )
except ImportError:
    mongo_enabled = lambda: False
    BirthdayChannelAdapter = None
    PendingConfigAdapter = None
    RegistrationUserLimitsAdapter = None
    ServerAllianceAdapter = None
    WelcomeChannelAdapter = None

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/register", tags=["Registration"])

BOT_OWNER_ID = os.getenv("BOT_OWNER_ID", "")


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


class QuickSetupRequest(BaseModel):
    guild_id: str
    guild_name: str
    alliance_name: Optional[str] = Field(default=None, max_length=100)
    access_code: Optional[str] = Field(default=None, max_length=64)
    state: Optional[int] = Field(default=None, ge=1)
    discord_user_id: str = "0"
    discord_username: str = "Unknown"


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
            detail=f"Limit reached. You already have {len(active_user_regs)} of {max_servers} allowed server registration(s). "
                   f"Current server: '{existing_user.get('guild_name', 'another server')}'."
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
                    msg = (
                        f"✅ **Your registration has been approved!**\n\n"
                        f"**Server:** {doc.get('guild_name')}\n"
                        f"**Alliance:** `{doc.get('alliance_name')}`\n\n"
                        f"Your access code is now active. You can use `/manage` on the dashboard.\n"
                        f"Use the code you set during registration to unlock the dashboard."
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
