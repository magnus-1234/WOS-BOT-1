import asyncio
import json
import re
import logging
import sqlite3
import os
from datetime import datetime, timezone
from urllib.request import Request as UrlRequest, urlopen
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Request, Body
from pydantic import BaseModel

try:
    from db.mongo_adapters import (
        GiftCodesAdapter, 
        GiftCodeRedemptionAdapter, 
        mongo_enabled,
        AutoRedeemSettingsAdapter,
        AutoRedeemChannelsAdapter,
        AutoRedeemMembersAdapter,
        IDChannelsAdapter,
        GiftcodeStateAdapter
    )
except ImportError:
    mongo_enabled = lambda: False
    GiftCodesAdapter = GiftCodeRedemptionAdapter = AutoRedeemSettingsAdapter = \
    AutoRedeemChannelsAdapter = AutoRedeemMembersAdapter = IDChannelsAdapter = \
    GiftcodeStateAdapter = None

try:
    from gift_codes import get_active_gift_codes
except Exception:
    get_active_gift_codes = None

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Gift Codes"])
WOSTOOLS_GIFT_CODES_URL = "https://wostools.net/api/gift-codes"
WOSGIFTCODES_URL = "https://wosgiftcodes.com/"

# ─────────────────────────────────────────
# Automation & Settings
# ─────────────────────────────────────────

class GiftCodeAutomationSettings(BaseModel):
    auto_redeem_enabled: bool
    auto_redeem_channel_id: Optional[str] = None
    registration_channel_id: Optional[str] = None
    latest_code_channel_id: Optional[str] = None

class AutoRedeemMemberAdd(BaseModel):
    id: str
    nickname: Optional[str] = "Unknown"
    furnace_lv: Optional[int] = 0
    avatar_image: Optional[str] = ""

@router.get("/members/{guild_id}")
async def get_auto_redeem_members(guild_id: str, request: Request):
    logger.info(f"Fetching auto-redeem members for guild {guild_id}")
    
    bot = getattr(request.app.state, "bot", None)
    if bot:
        manage_cog = bot.get_cog("ManageGiftCode")
        if manage_cog:
            try:
                members = await manage_cog.AutoRedeemDB.get_members_async(manage_cog, int(guild_id))
                for m in members:
                    if 'fid' in m:
                        m['id'] = m.pop('fid')
                return {"members": members}
            except Exception as e:
                logger.error(f"Error fetching members via cog: {e}")

    if not mongo_enabled() or AutoRedeemMembersAdapter is None:
        logger.warning("MongoDB or Adapter not available, returning empty members list")
        return {"members": []}
    
    try:
        members = await AutoRedeemMembersAdapter.get_members_async(int(guild_id))
        # Rebrand fid to id in response
        for m in members:
            if 'fid' in m:
                m['id'] = m.pop('fid')
        return {"members": members}
    except Exception as e:
        logger.error(f"Failed to get members for guild {guild_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@router.get("/settings/{guild_id}")
async def get_automation_settings(guild_id: str):
    logger.info(f"Fetching automation settings for guild {guild_id}")
    if not mongo_enabled() or any(a is None for a in [AutoRedeemSettingsAdapter, AutoRedeemChannelsAdapter, IDChannelsAdapter, GiftcodeStateAdapter]):
        return {
            "auto_redeem_enabled": False,
            "auto_redeem_channel_id": None,
            "registration_channel_id": None,
            "latest_code_channel_id": None
        }
    
    try:
        gid_int = int(guild_id)
        # Robust path detection for Oracle VM and local dev
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        
        # 1. Auto-redeem enabled state
        ar_settings = await AutoRedeemSettingsAdapter.get_settings_async(gid_int)
        auto_redeem_enabled = ar_settings.get('enabled', False) if ar_settings else False
        
        # Get auto-redeem channel
        ar_channel_doc = await AutoRedeemChannelsAdapter.get_channel_async(gid_int)
        auto_redeem_channel_id = str(ar_channel_doc.get('channel_id')) if ar_channel_doc else None
        
        # Get registration channel
        id_channel_doc = await IDChannelsAdapter.get_channel_async(gid_int)
        registration_channel_id = str(id_channel_doc.get('channel_id')) if id_channel_doc else None
        
        # SQLite Fallbacks if MongoDB is empty
        if not auto_redeem_channel_id or not registration_channel_id:
            try:
                gift_db_path = os.path.join(base_dir, 'db', 'giftcode.sqlite')
                id_db_path = os.path.join(base_dir, 'db', 'id_channel.sqlite')
                
                # Fallback for Auto-Redeem Channel
                if not auto_redeem_channel_id and os.path.exists(gift_db_path):
                    with sqlite3.connect(gift_db_path) as conn:
                        cursor = conn.cursor()
                        cursor.execute("SELECT channel_id FROM auto_redeem_channels WHERE guild_id = ?", (gid_int,))
                        row = cursor.fetchone()
                        if row:
                            auto_redeem_channel_id = str(row[0])
                        
                        if not ar_settings:
                            cursor.execute("SELECT enabled FROM auto_redeem_settings WHERE guild_id = ?", (gid_int,))
                            en_row = cursor.fetchone()
                            if en_row:
                                auto_redeem_enabled = bool(en_row[0])

                # Fallback for Registration Channel
                if not registration_channel_id and os.path.exists(id_db_path):
                    with sqlite3.connect(id_db_path) as conn:
                        cursor = conn.cursor()
                        cursor.execute("SELECT channel_id FROM id_channels WHERE guild_id = ?", (gid_int,))
                        row = cursor.fetchone()
                        if row:
                            registration_channel_id = str(row[0])
                            
            except Exception as e:
                logger.warning(f"SQLite fallback failed: {e}")

        # Mutual Fallback: If one is missing but the other exists, assume they are the same (legacy bot behavior)
        if registration_channel_id and not auto_redeem_channel_id:
            auto_redeem_channel_id = registration_channel_id
        elif auto_redeem_channel_id and not registration_channel_id:
            registration_channel_id = auto_redeem_channel_id

        # 4. Latest code channel (poster)
        poster_state = await GiftcodeStateAdapter.get_state_async()
        poster_channels = poster_state.get('channels', {})
        poster_channel_id = poster_channels.get(str(guild_id))
        
        return {
            "auto_redeem_enabled": auto_redeem_enabled,
            "auto_redeem_channel_id": auto_redeem_channel_id,
            "registration_channel_id": registration_channel_id,
            "latest_code_channel_id": str(poster_channel_id) if poster_channel_id else None
        }
    except Exception as e:
        logger.error(f"Error fetching automation settings for guild {guild_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/settings/{guild_id}")
async def update_automation_settings(guild_id: str, settings: GiftCodeAutomationSettings):
    logger.info(f"Updating automation settings for guild {guild_id}: {settings}")
    if not mongo_enabled() or any(a is None for a in [AutoRedeemSettingsAdapter, AutoRedeemChannelsAdapter, IDChannelsAdapter, GiftcodeStateAdapter]):
        raise HTTPException(status_code=503, detail="MongoDB or Adapter not enabled")
    
    try:
        gid_int = int(guild_id)
        # 1. Update Auto-redeem enabled state
        await AutoRedeemSettingsAdapter.set_enabled_async(gid_int, settings.auto_redeem_enabled, 0)
        
        # 2. Update Auto-redeem channel
        if settings.auto_redeem_channel_id and settings.auto_redeem_channel_id.strip():
            try:
                await AutoRedeemChannelsAdapter.set_channel_async(gid_int, int(settings.auto_redeem_channel_id), 0)
            except (ValueError, TypeError):
                logger.warning(f"Invalid auto_redeem_channel_id: {settings.auto_redeem_channel_id}")
        
        # 3. Update Registration channel
        if settings.registration_channel_id and settings.registration_channel_id.strip():
            try:
                await IDChannelsAdapter.set_channel_async(gid_int, int(settings.registration_channel_id), 0)
            except (ValueError, TypeError):
                logger.warning(f"Invalid registration_channel_id: {settings.registration_channel_id}")
            
        # 4. Update Latest code channel (poster)
        if settings.latest_code_channel_id and settings.latest_code_channel_id.strip():
            try:
                poster_state = await GiftcodeStateAdapter.get_state_async()
                if 'channels' not in poster_state:
                    poster_state['channels'] = {}
                poster_state['channels'][str(guild_id)] = int(settings.latest_code_channel_id)
                await GiftcodeStateAdapter.set_state_async(poster_state)
            except (ValueError, TypeError):
                logger.warning(f"Invalid latest_code_channel_id: {settings.latest_code_channel_id}")
            
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error updating automation settings for guild {guild_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/members/{guild_id}/add")
async def add_auto_redeem_member(guild_id: str, member: AutoRedeemMemberAdd, request: Request):
    try:
        member_data = {
            "nickname": member.nickname,
            "furnace_lv": member.furnace_lv,
            "avatar_image": member.avatar_image,
            "added_by": 0,
            "added_at": datetime.now(timezone.utc).isoformat()
        }
        
        bot = getattr(request.app.state, "bot", None)
        if bot:
            manage_cog = bot.get_cog("ManageGiftCode")
            if manage_cog:
                success = await manage_cog.AutoRedeemDB.add_member_async(manage_cog, int(guild_id), member.id, member_data)
                if success == 'LIMIT_REACHED':
                    raise HTTPException(status_code=400, detail="Server auto-redeem member limit reached")
                elif not success:
                    raise HTTPException(status_code=500, detail="Failed to add member via bot")
                return {"status": "success"}

        if not mongo_enabled() or AutoRedeemMembersAdapter is None:
            raise HTTPException(status_code=503, detail="MongoDB or Adapter not enabled")
            
        success = await AutoRedeemMembersAdapter.add_member_async(int(guild_id), member.id, member_data)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to add member")
            
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding member to guild {guild_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/members/{guild_id}/remove")
async def remove_auto_redeem_member(guild_id: str, request: Request, payload: dict = Body(...)):
    try:
        fid = payload.get("id") or payload.get("fid")
        if not fid:
            raise HTTPException(status_code=400, detail="ID is required")
            
        bot = getattr(request.app.state, "bot", None)
        if bot:
            manage_cog = bot.get_cog("ManageGiftCode")
            if manage_cog:
                success = await manage_cog.AutoRedeemDB.remove_member_async(manage_cog, int(guild_id), fid)
                if not success:
                    raise HTTPException(status_code=500, detail="Failed to remove member via bot")
                return {"status": "success"}

        if not mongo_enabled() or AutoRedeemMembersAdapter is None:
            raise HTTPException(status_code=503, detail="MongoDB or Adapter not enabled")
            
        success = await AutoRedeemMembersAdapter.remove_member_async(int(guild_id), fid)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to remove member")
            
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error removing member from guild {guild_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/test-ping")
async def test_ping():
    return {"status": "ok", "message": "Giftcodes router is active"}

@router.get("")
@router.get("/")
async def get_active_giftcodes(request: Request):
    """Fetch active giftcodes from the bot scraper, with fallbacks."""
    bot_codes = await _fetch_running_bot_active_codes(request)
    if bot_codes:
        return {
            "codes": bot_codes,
            "source": "bot_manage_cog",
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

    if get_active_gift_codes is not None:
        try:
            live_codes = await get_active_gift_codes()
            codes = [
                code
                for code in (_normalize_live_code(code, trusted_active=True) for code in live_codes)
                if code["is_active"]
            ]
            if codes:
                return {
                    "codes": codes,
                    "source": "bot_scraper",
                    "last_updated": datetime.now(timezone.utc).isoformat(),
                }
        except Exception as e:
            logger.warning(f"Bot giftcode scraper failed, falling back: {e}")

    # Try wosgiftcodes.com first (has real reward details in the HTML table)
    wgc_codes = await _fetch_wosgiftcodes_active_codes()
    if wgc_codes:
        return {
            "codes": wgc_codes,
            "source": "wosgiftcodes",
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

    direct_codes = await _fetch_wostools_active_codes()
    if direct_codes:
        return {
            "codes": direct_codes,
            "source": "wostools",
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

    try:
        if not mongo_enabled():
            return {
                "codes": [],
                "source": "unavailable",
                "last_updated": datetime.now(timezone.utc).isoformat(),
            }

        # Fetch bot DB codes + two enrichment sources in parallel
        raw_task = GiftCodesAdapter.get_all_with_status_async()
        wgc_task  = _fetch_wosgiftcodes_active_codes()
        wostools_task = _fetch_wostools_active_codes()
        raw, wgc_codes, wostools_codes = await asyncio.gather(
            raw_task, wgc_task, wostools_task, return_exceptions=True
        )

        if isinstance(raw, Exception):
            logger.error(f"DB fetch failed: {raw}")
            raw = []

        # Build enrichment map: UPPER(code) -> details dict
        # Priority: wosgiftcodes.com (has rewards) > WosTools
        enrichment_map: dict = {}
        for source_list in [wostools_codes, wgc_codes]:   # wgc last = highest priority
            if isinstance(source_list, list):
                for item in source_list:
                    key = str(item.get("code") or "").strip().upper()
                    if key:
                        enrichment_map[key] = item

        codes = [
            code
            for code in (
                _normalize_database_code_rich(c, enrichment_map)
                for c in raw
            )
            if code["is_active"]
        ]
        return {
            "codes": codes,
            "source": "bot_database",
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Failed to fetch giftcodes: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def _fetch_running_bot_active_codes(request: Request) -> list[dict]:
    """Read active gift codes from the loaded Discord bot cog, if available, enriched with scraper data."""
    try:
        bot = getattr(request.app.state, "bot", None)
        if bot is None:
            return []

        manage_cog = bot.get_cog("ManageGiftCode") if hasattr(bot, "get_cog") else None
        if manage_cog is None or not hasattr(manage_cog, "get_active_gift_codes_consolidated"):
            return []

        active_map = await manage_cog.get_active_gift_codes_consolidated(force_refresh=True)
        if not active_map:
            return []

        # Fetch rich data from the scraper logic (which already hits wosgiftcodes + wostools)
        rich_codes = []
        if get_active_gift_codes is not None:
            rich_codes = await get_active_gift_codes()
        
        enrichment_map = {}
        for rc in rich_codes:
            code_key = str(rc.get("code") or "").strip().upper()
            if code_key:
                enrichment_map[code_key] = rc

        return [
            _normalize_bot_cog_code(code, expiry, enrichment_map.get(str(code).strip().upper()))
            for code, expiry in active_map.items()
            if str(code).strip()
        ]
    except Exception as e:
        logger.warning(f"Running bot giftcode cog fetch failed: {e}")
        return []


def _normalize_bot_cog_code(code: str, expiry: str, enrichment: dict = None) -> dict:
    enrichment = enrichment or {}
    rewards = enrichment.get("rewards") or "Rewards not specified"
    final_expiry = enrichment.get("expiry") or str(expiry or "Unknown").strip() or "Unknown"
    
    return {
        "code": str(code).strip(),
        "rewards": rewards,
        "expiry": final_expiry,
        "description": enrichment.get("description", ""),
        "source": "bot_manage_cog",
        "date_added": enrichment.get("date_added", ""),
        "status": "active",
        "validation_status": "active",
        "is_active": True,
    }


async def _fetch_wosgiftcodes_active_codes() -> list[dict]:
    """Scrape active gift codes + reward details from wosgiftcodes.com HTML table."""
    def _scrape() -> list[dict]:
        req = UrlRequest(
            WOSGIFTCODES_URL,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
            },
        )
        with urlopen(req, timeout=12) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        # Extract <tr> rows from the active codes table
        # Each row has: code | rewards | expiry | status
        results = []

        # Find rows inside the active codes section (before the "Expired Codes" heading)
        active_section = html
        expired_idx = html.lower().find("expired code")
        if expired_idx > 0:
            active_section = html[:expired_idx]

        # Match table rows: <tr ...> cells </tr>
        row_pattern = re.compile(r"<tr[^>]*>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
        cell_pattern = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.DOTALL | re.IGNORECASE)
        tag_re = re.compile(r"<[^>]+>")

        def strip_tags(s: str) -> str:
            return tag_re.sub("", s).strip()

        for row_m in row_pattern.finditer(active_section):
            row_html = row_m.group(1)
            cells = [strip_tags(c.group(1)) for c in cell_pattern.finditer(row_html)]
            # Skip header rows or rows with fewer than 2 cells
            if len(cells) < 2:
                continue
            code = cells[0].strip()
            # Skip obvious non-code rows (empty, "Code", "Gift Code" headers)
            if not code or code.lower() in ("code", "gift code", "gift codes", "#", "status", "reward", "rewards"):
                continue
            # Code should look like a gift code: alphanumeric, 4-30 chars
            if not re.match(r"^[A-Za-z0-9]{4,30}$", code):
                continue

            rewards = cells[2].strip() if len(cells) > 2 else "Rewards not specified"
            expiry  = cells[3].strip() if len(cells) > 3 else "Unknown"
            
            if not rewards or rewards.lower() in ("", "n/a", "-"):
                rewards = "Rewards not specified"
            if not expiry or expiry.lower() in ("", "n/a", "-"):
                expiry = "Unknown"

            results.append({
                "code": code,
                "rewards": rewards,
                "expiry": expiry,
                "description": cells[1].strip() if len(cells) > 1 else "",
                "source": "wosgiftcodes",
                "date_added": "",
                "status": "active",
                "validation_status": "active",
                "is_active": True,
            })

        return results

    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _scrape)
    except Exception as e:
        logger.warning(f"wosgiftcodes.com scrape failed: {e}")
        return []


async def _fetch_wostools_active_codes() -> list[dict]:
    def _fetch() -> list[dict]:
        request = UrlRequest(
            WOSTOOLS_GIFT_CODES_URL,
            headers={
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 WhiteoutSurvivalBot/1.0",
            },
        )
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))

        codes = payload.get("codes", []) if isinstance(payload, dict) else []
        return [
            _normalize_wostools_code(code)
            for code in codes
            if str(code.get("status", "")).strip().lower() == "active"
            and str(code.get("code", "")).strip()
        ]

    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _fetch)
    except Exception as e:
        logger.warning(f"WosTools direct giftcode fetch failed: {e}")
        return []


def _normalize_wostools_code(code: dict) -> dict:
    return {
        "code": _first_present(code, ["code"]),
        "rewards": _first_present(
            code,
            ["rewards", "reward", "rewardText", "description", "label"],
            "Rewards not specified",
        ),
        "expiry": _first_present(
            code,
            ["expiry", "expires", "expiresAt", "expiration", "expirationDate"],
            "Unknown",
        ),
        "description": _first_present(code, ["description", "label"]),
        "source": "wostools",
        "date_added": _first_present(code, ["dateAdded", "date_added", "created_at", "date"]),
        "status": "active",
        "validation_status": "active",
        "is_active": True,
    }


def _first_present(data: dict, keys: list[str], default: str = "") -> str:
    for key in keys:
        value = data.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def _normalize_live_code(code: dict, trusted_active: bool = False) -> dict:
    status = _first_present(code, ["status", "validation_status"], "").lower()
    if status:
        is_active = status in ("active", "valid")
    else:
        is_active = trusted_active or bool(code.get("is_active", False))
    rewards = _first_present(
        code,
        ["rewards", "reward", "reward_text", "description", "label"],
        "Rewards not specified",
    )
    expiry = _first_present(
        code,
        ["expiry", "expires", "expires_at", "expire_at", "expiration", "expiration_date"],
        "Unknown",
    )
    return {
        "code": _first_present(code, ["code"]),
        "rewards": rewards,
        "expiry": expiry,
        "description": _first_present(code, ["description", "label"]),
        "source": _first_present(code, ["source"], "live"),
        "date_added": _first_present(code, ["date_added", "dateAdded", "created_at", "date"]),
        "validation_status": status or ("active" if is_active else "inactive"),
        "status": status or ("active" if is_active else "inactive"),
        "is_active": is_active,
    }


def _normalize_database_code(code_tuple) -> dict:
    """Legacy tuple-based normalizer (kept for compatibility)."""
    status = str(code_tuple[2] or "").strip().lower()
    # 'pending' = code added by bot but not yet validated — still usable by players
    is_active = status in ("valid", "active", "pending")
    return {
        "code": code_tuple[0],
        "date": code_tuple[1],
        "date_added": code_tuple[1],
        "validation_status": status or "pending",
        "rewards": "Rewards not specified",
        "expiry": "Unknown",
        "source": "bot_database",
        "is_active": is_active,
    }


def _normalize_database_code_rich(doc: dict, wostools_map: dict | None = None) -> dict:
    """Rich normalizer using the full document from get_all_with_status_async.
    Optionally enriches rewards/expiry from a wostools_map keyed by UPPERCASED code.
    """
    status = str(doc.get("validation_status") or "").strip().lower()
    # 'pending' / '' = freshly scraped by bot, not yet redeemed/validated → still show
    is_active = status in ("valid", "active", "pending", "posted", "")
    code = str(doc.get("giftcode") or "").strip()
    date_added = doc.get("created_at") or doc.get("date") or ""
    updated_at = doc.get("updated_at") or ""

    # Enrich from WosTools if available
    enrichment = (wostools_map or {}).get(code.upper(), {})
    rewards = _first_present(enrichment, ["rewards", "reward", "rewardText", "description"], "Rewards not specified")
    expiry  = _first_present(enrichment, ["expiry", "expires", "expiresAt", "expiration"], "Unknown")

    return {
        "code": code,
        "rewards": rewards,
        "expiry": expiry,
        "description": _first_present(enrichment, ["description", "label"]),
        "source": "bot_database",
        "date_added": date_added,
        "updated_at": updated_at,
        "validation_status": status or "pending",
        "status": status or "pending",
        "is_active": is_active,
        "auto_redeem_processed": doc.get("auto_redeem_processed", False),
    }

@router.get("/{guild_id}/stats")
async def get_giftcode_stats(guild_id: str):
    """Fetch giftcode stats for a specific guild."""
    if not mongo_enabled():
        return {"total_attempts": 0, "successful": 0, "failed": 0, "unique_codes": 0}

    try:
        raw = await GiftCodesAdapter.get_all_async()
        unique = len(raw)
        # Count redemption results for this guild if adapter supports it
        successful = sum(1 for c in raw if c[2] == "valid")
        failed = sum(1 for c in raw if c[2] in ("invalid", "expired"))
        return {
            "total_attempts": unique,
            "successful": successful,
            "failed": failed,
            "unique_codes": unique
        }
    except Exception as e:
        logger.error(f"Failed to get giftcode stats: {e}")
        return {"total_attempts": 0, "successful": 0, "failed": 0, "unique_codes": 0}

@router.post("/add")
async def add_giftcode(code: str, reward_text: str = ""):
    """Add a new giftcode from the web dashboard."""
    return {"status": "success", "message": f"Code {code} added."}

@router.post("/player-info")
async def get_player_info_api(request: Request):
    """Fetch player information for a given player ID."""
    try:
        body = await request.json()
        fid = body.get("id") or body.get("fid")
        if not fid:
            raise HTTPException(status_code=400, detail="ID is required")
        
        # Clean ID
        fid = ''.join(c for c in str(fid) if c.isdigit())
        if not (8 <= len(fid) <= 9):
            raise HTTPException(status_code=400, detail="ID must be 8 or 9 digits")

        bot = getattr(request.app.state, "bot", None)
        if bot is None:
             raise HTTPException(status_code=503, detail="Bot instance not available")
             
        manage_cog = bot.get_cog("ManageGiftCode")
        if manage_cog is None:
             raise HTTPException(status_code=503, detail="Gift Code management module not available")

        player_data = await manage_cog.fetch_player_data(fid, return_full=True)
        if not player_data:
            raise HTTPException(status_code=404, detail="Player not found or API error")
            
        if player_data.get("status") == "rate_limited":
            wait_time = player_data.get("wait_time", 5)
            raise HTTPException(status_code=429, detail=f"WOS API rate limited. Please wait {wait_time:.1f}s.")
            
        if player_data.get("status") == "not_found" or not player_data.get("data"):
            raise HTTPException(status_code=404, detail="Player not found or API error")
            
        data = player_data.get("data", {})
        return {
            "status": "success",
            "data": {
                "id": fid,
                "nickname": data.get("nickname"),
                "furnace_lv": data.get("stove_lv"),
                "furnace_lv_formatted": manage_cog.format_furnace_level(data.get("stove_lv", 0)),
                "avatar_image": data.get("avatar_image"),
                "stateId": data.get("kid")
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching player info: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/redeem")
async def redeem_giftcodes_api(request: Request):
    """Manually redeem gift codes for a player."""
    try:
        body = await request.json()
        fid = body.get("id") or body.get("fid")
        codes = body.get("codes", [])
        guild_id = body.get("guild_id")
        
        if not fid or not codes:
            raise HTTPException(status_code=400, detail="ID and codes are required")
        
        # Clean ID
        fid = ''.join(c for c in str(fid) if c.isdigit())
        
        bot = getattr(request.app.state, "bot", None)
        if bot is None:
             raise HTTPException(status_code=503, detail="Bot instance not available")
             
        manage_cog = bot.get_cog("ManageGiftCode")
        if manage_cog is None:
             raise HTTPException(status_code=503, detail="Gift Code management module not available")

        # Fetch player data to get current nickname/level
        player_data = await manage_cog.fetch_player_data(fid)
        nickname = player_data.get("nickname", "Unknown") if player_data else "Unknown"
        furnace_lv = player_data.get("furnace_lv", 0) if player_data else 0

        results = []
        for code in codes:
            # We use the internal _redeem_for_member logic
            status, success, already_redeemed, failed = await manage_cog._redeem_for_member(
                int(guild_id) if guild_id else 0, fid, nickname, furnace_lv, code
            )
            
            results.append({
                "code": code,
                "status": status,
                "success": bool(success),
                "already_redeemed": bool(already_redeemed),
                "failed": bool(failed)
            })
            
        return {
            "status": "success",
            "results": results
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error redeeming giftcodes: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/trigger-server-redeem")
async def trigger_server_redeem_api(request: Request):
    """Trigger background auto-redeem process for a specific guild and code."""
    try:
        body = await request.json()
        guild_id = body.get("guild_id")
        code = body.get("code")
        
        if not guild_id or not code:
            raise HTTPException(status_code=400, detail="guild_id and code are required")
            
        bot = getattr(request.app.state, "bot", None)
        if bot is None:
             raise HTTPException(status_code=503, detail="Bot instance not available")
             
        manage_cog = bot.get_cog("ManageGiftCode")
        if manage_cog is None:
             raise HTTPException(status_code=503, detail="Gift Code management module not available")

        # Initiate the process in the background
        asyncio.create_task(
            manage_cog.trigger_auto_redeem_for_new_codes(
                new_codes=[(code, "")],
                is_recheck=True,
                specific_guild_id=int(guild_id)
            )
        )
        
        return {
            "status": "success",
            "message": f"Auto-redeem triggered for server {guild_id} with code {code}"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error triggering server redeem: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{guild_id}/history")
async def get_giftcode_history(guild_id: str):
    if not mongo_enabled():
        return {"history": []}
    try:
        from db.mongo_adapters import _get_db_main_async
        db = await _get_db_main_async()
        # Sort by updated_at desc (redeemed_at may not exist on older docs)
        cursor = db["auto_redeemed_codes"].find(
            {"guild_id": int(guild_id)}
        ).sort("updated_at", -1).limit(50)
        docs = await cursor.to_list(length=50)
        history = []
        for doc in docs:
            history.append({
                "code": doc.get("code") or doc.get("giftcode"),
                "fid": doc.get("fid"),
                "status": doc.get("status"),
                "redeemed_at": doc.get("updated_at") or doc.get("redeemed_at") or doc.get("created_at")
            })
        return {"history": history}
    except Exception as e:
        logger.error(f"Failed to get history: {e}")
        return {"history": []}
