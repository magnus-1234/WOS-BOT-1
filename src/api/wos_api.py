from typing import Optional
import aiohttp
import hashlib
import time
import os
import json
import ssl
import logging

logger = logging.getLogger(__name__)

async def _get_cached_player_info(player_id: str) -> Optional[dict]:
    """Helper to query local SQLite database for existing player records."""
    try:
        import sqlite3
        db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "db", "giftcode.sqlite")
        if os.path.exists(db_path):
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT fid, nickname, furnace_lv, state_id FROM auto_redeem_members WHERE fid = ?", (str(player_id),))
            row = cursor.fetchone()
            conn.close()
            if row:
                return {
                    "id": str(row[3]) if row[3] else "0",
                    "name": row[1] if row[1] else f"Player_{player_id}",
                    "level": int(row[2]) if row[2] else 0,
                    "power": 0,
                    "avatar_image": ""
                }
    except Exception as e:
        logger.warning(f"Failed to fetch cached player info for {player_id}: {e}")
    return None


async def fetch_player_info(player_id: str) -> Optional[dict]:
    """
    Fetch player info from the WOS giftcode API.
    Falls back to local database or basic metadata if the API returns 404.
    """
    url = "https://wos-giftcode-api.centurygame.com/api/player"
    secret = "tB87#kPtkxqOS2"
    
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://wos-giftcode.centurygame.com",
        "Referer": "https://wos-giftcode.centurygame.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    }

    try:
        current_time = int(time.time() * 1000)
        form = f"fid={player_id}&time={current_time}"
        sign = hashlib.md5((form + secret).encode("utf-8")).hexdigest()
        payload = f"sign={sign}&{form}"

        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_context)) as session:
            async with session.post(url, data=payload, headers=headers, timeout=5) as resp:
                if resp.status == 200:
                    try:
                        js = await resp.json()
                        if js.get("code") == 0:
                            data = js.get("data", {})
                            return {
                                "id": data.get("kid"),
                                "name": data.get("nickname"),
                                "level": int(data.get("stove_lv", 0)) if data.get("stove_lv") else 0,
                                "power": data.get("stove_lv_content"),
                                "avatar_image": data.get("avatar_image")
                            }
                    except Exception:
                        pass
    except Exception as e:
        logger.warning(f"wos_api request exception for {player_id}: {e}")

    # Fallback: check local database for existing player records
    cached = await _get_cached_player_info(player_id)
    if cached:
        return cached

    # Basic fallback if player ID is numeric/valid
    if player_id and str(player_id).isdigit():
        return {
            "id": "0",
            "name": f"Player_{player_id}",
            "level": 0,
            "power": 0,
            "avatar_image": ""
        }

    return None


async def fetch_wos_player(player_id: str) -> Optional[dict]:
    """
    Fetch live WOS player stats in the format used by angel_personality and app.py.
    Returns dict with keys: player_id, nickname, furnace_level, state_id.
    Returns fallback dict if API is unavailable.
    """
    data = await fetch_player_info(player_id)
    if not data:
        return {
            "player_id": str(player_id),
            "nickname": f"Player_{player_id}",
            "furnace_level": 0,
            "state_id": "0"
        }
    return {
        "player_id": str(player_id),
        "nickname": data.get("name", f"Player_{player_id}"),
        "furnace_level": data.get("level", 0),
        "state_id": str(data.get("id", "0")),
    }
