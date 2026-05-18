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
    from db.mongo_adapters import PendingConfigAdapter, ServerAllianceAdapter, mongo_enabled
except ImportError:
    mongo_enabled = lambda: False
    PendingConfigAdapter = None
    ServerAllianceAdapter = None

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/register", tags=["Registration"])

BOT_OWNER_ID = os.getenv("BOT_OWNER_ID", "")


# ── Request Models ─────────────────────────────────────────────────────────────

class SubmitRegistrationRequest(BaseModel):
    guild_id: str
    guild_name: str
    alliance_name: str = Field(..., min_length=1, max_length=100)
    access_code: str = Field(..., min_length=4, max_length=64)
    discord_user_id: str
    discord_username: str


class ReviewRequest(BaseModel):
    guild_id: str
    action: str  # "approve" or "deny"
    admin_user_id: str


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/status")
async def check_registration_status(guild_id: str):
    """
    Check the registration status for a guild.
    Returns: { status: 'none' | 'pending' | 'approved' | 'denied', data: {...} }
    """
    if not mongo_enabled() or not PendingConfigAdapter:
        raise HTTPException(status_code=500, detail="Database not available")

    doc = await PendingConfigAdapter.get_by_guild_async(int(guild_id))
    
    if doc and doc.get("status") == "approved":
        safe_doc = {
            "guild_id": doc.get("guild_id"),
            "guild_name": doc.get("guild_name"),
            "alliance_name": doc.get("alliance_name"),
            "status": "approved",
            "submitted_at": doc.get("submitted_at"),
            "discord_username": doc.get("discord_username"),
        }
        return {"status": "approved", "data": safe_doc}

    # If not approved in PendingConfigAdapter, check if it has a bot-configured legacy password
    if ServerAllianceAdapter:
        stored_password = await ServerAllianceAdapter.get_password_async(int(guild_id))
        if stored_password:
            alliance_id = await ServerAllianceAdapter.get_alliance_async(int(guild_id))
            alliance_name = f"Configured Alliance (ID: {alliance_id})" if alliance_id else "Configured Alliance"
            return {
                "status": "approved",
                "data": {
                    "guild_id": int(guild_id),
                    "alliance_name": alliance_name,
                    "status": "approved",
                    "legacy_config": True
                }
            }

    # Return pending, denied, or none
    if not doc:
        return {"status": "none", "data": None}
        
    safe_doc = {
        "guild_id": doc.get("guild_id"),
        "guild_name": doc.get("guild_name"),
        "alliance_name": doc.get("alliance_name"),
        "status": doc.get("status"),
        "submitted_at": doc.get("submitted_at"),
        "discord_username": doc.get("discord_username"),
    }
    return {"status": doc.get("status", "none"), "data": safe_doc}


@router.get("/user-check")
async def check_user_registration(discord_user_id: str):
    """
    Check if a Discord user already has a pending/approved registration on any server.
    Enforces the one-user-one-server rule.
    """
    if not mongo_enabled() or not PendingConfigAdapter:
        raise HTTPException(status_code=500, detail="Database not available")

    doc = await PendingConfigAdapter.get_by_user_async(int(discord_user_id))
    if not doc:
        return {"has_registration": False}

    return {
        "has_registration": True,
        "guild_id": doc.get("guild_id"),
        "guild_name": doc.get("guild_name"),
        "status": doc.get("status")
    }


@router.post("/submit")
async def submit_registration(body: SubmitRegistrationRequest, request: Request):
    """
    Submit a self-service registration request.
    Validates one-user-one-server rule, stores as pending, DMs global admin.
    """
    if not mongo_enabled() or not PendingConfigAdapter:
        raise HTTPException(status_code=500, detail="Database not available")

    # Enforce: this user cannot have another active (pending/approved) registration
    existing_user = await PendingConfigAdapter.get_by_user_async(int(body.discord_user_id))
    if existing_user and existing_user.get("guild_id") != body.guild_id:
        raise HTTPException(
            status_code=409,
            detail=f"You already have a registration on server '{existing_user.get('guild_name', 'another server')}'. "
                   f"One registration per user is allowed."
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
