"""
Music Bot Web Control Server
Provides HTTP API for the website to control and monitor the Discord music bot.
Runs on a separate port alongside the Discord bot.

Endpoints:
  GET  /status?guildId=XXX       - Get current playback status
  POST /control                  - Send control command to bot
  GET  /health                   - Health check

Authentication: Bearer token via MUSIC_API_SECRET env var
"""

import asyncio
import json
import logging
import os
import time
from typing import Optional, TYPE_CHECKING

from aiohttp import web

if TYPE_CHECKING:
    from music_bot.bot import MusicBot

logger = logging.getLogger("music_bot.web_server")

MUSIC_API_SECRET = os.getenv("MUSIC_API_SECRET", "")
WEB_SERVER_PORT = int(os.getenv("MUSIC_WEB_SERVER_PORT", "8090"))
WEB_SERVER_HOST = os.getenv("MUSIC_WEB_SERVER_HOST", "0.0.0.0")

# Allowed actions
VALID_ACTIONS = {"pause", "resume", "skip", "previous", "stop", "volume", "loop", "shuffle", "play_playlist"}


def _verify_token(request: web.Request) -> bool:
    """Verify the bearer token in the request."""
    if not MUSIC_API_SECRET:
        # No secret configured — allow all (development mode)
        return True
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:] == MUSIC_API_SECRET
    return False


def _cors_headers():
    """Return CORS headers for web responses."""
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Authorization, Content-Type",
    }


def _json_response(data: dict, status: int = 200) -> web.Response:
    return web.Response(
        text=json.dumps(data),
        content_type="application/json",
        status=status,
        headers=_cors_headers(),
    )


def _get_player_status(bot: "MusicBot", guild_id: int) -> Optional[dict]:
    """Extract playback status from a guild's voice player."""
    import wavelink
    guild = bot.get_guild(guild_id)
    if not guild:
        return None

    player = guild.voice_client
    if not player or not isinstance(player, wavelink.Player):
        return None

    current = getattr(player, "current", None)
    queue = getattr(player, "queue", None)

    current_track = None
    if current:
        current_track = {
            "title": getattr(current, "title", "Unknown"),
            "author": getattr(current, "author", "Unknown"),
            "uri": getattr(current, "uri", ""),
            "length": getattr(current, "length", 0),
            "position": getattr(player, "position", 0),
            "artwork": getattr(current, "artwork", None),
        }

    queue_tracks = []
    if queue and not queue.is_empty:
        for i, track in enumerate(list(queue)[:20]):
            queue_tracks.append({
                "title": getattr(track, "title", "Unknown"),
                "author": getattr(track, "author", "Unknown"),
                "uri": getattr(track, "uri", ""),
                "length": getattr(track, "length", 0),
            })

    # Voice channel info
    voice_channel = None
    if player.channel:
        voice_channel = {
            "id": str(player.channel.id),
            "name": player.channel.name,
        }

    return {
        "guildId": str(guild_id),
        "guildName": guild.name,
        "playing": player.playing if hasattr(player, "playing") else False,
        "paused": player.paused if hasattr(player, "paused") else False,
        "volume": getattr(player, "volume", 100),
        "loopMode": getattr(player, "loop_mode", "off"),
        "currentTrack": current_track,
        "queue": queue_tracks,
        "queueSize": len(queue_tracks),
        "voiceChannel": voice_channel,
        "playlistName": getattr(player, "current_playlist_name", None),
        "updatedAt": time.time(),
    }


async def _handle_health(request: web.Request) -> web.Response:
    """Health check endpoint."""
    return _json_response({"status": "ok", "service": "music-bot-api"})


async def _handle_status(request: web.Request) -> web.Response:
    """Get current playback status for a guild."""
    if not _verify_token(request):
        return _json_response({"error": "Unauthorized"}, 401)

    bot: "MusicBot" = request.app["bot"]
    guild_id_str = request.rel_url.query.get("guildId", "")

    if not guild_id_str:
        # Return status for all active guilds
        statuses = []
        for guild in bot.guilds:
            status = _get_player_status(bot, guild.id)
            if status and status.get("playing"):
                statuses.append(status)
        return _json_response({"guilds": statuses})

    try:
        guild_id = int(guild_id_str)
    except ValueError:
        return _json_response({"error": "Invalid guildId"}, 400)

    status = _get_player_status(bot, guild_id)
    if status is None:
        return _json_response({"guildId": guild_id_str, "playing": False, "currentTrack": None, "queue": []})

    return _json_response(status)


async def _handle_control(request: web.Request) -> web.Response:
    """Handle a control command from the web."""
    if not _verify_token(request):
        return _json_response({"error": "Unauthorized"}, 401)

    try:
        body = await request.json()
    except Exception:
        return _json_response({"error": "Invalid JSON body"}, 400)

    action = body.get("action", "")
    guild_id_str = str(body.get("guildId", ""))
    value = body.get("value")

    if action not in VALID_ACTIONS:
        return _json_response({"error": f"Invalid action. Allowed: {', '.join(sorted(VALID_ACTIONS))}"}, 400)

    if not guild_id_str:
        return _json_response({"error": "guildId is required"}, 400)

    try:
        guild_id = int(guild_id_str)
    except ValueError:
        return _json_response({"error": "Invalid guildId"}, 400)

    import wavelink

    bot: "MusicBot" = request.app["bot"]
    guild = bot.get_guild(guild_id)
    if not guild:
        return _json_response({"error": "Guild not found"}, 404)

    player = guild.voice_client
    if not player or not isinstance(player, wavelink.Player):
        return _json_response({"error": "Bot is not in a voice channel in this server"}, 404)

    try:
        if action == "pause":
            await player.pause(True)
            return _json_response({"ok": True, "action": "pause"})

        elif action == "resume":
            await player.pause(False)
            return _json_response({"ok": True, "action": "resume"})

        elif action == "skip":
            await player.skip(force=True)
            return _json_response({"ok": True, "action": "skip"})

        elif action == "previous":
            if hasattr(player, "history") and player.history:
                prev = player.history[-1]
                await player.play(prev)
                return _json_response({"ok": True, "action": "previous"})
            return _json_response({"error": "No previous track"}, 400)

        elif action == "stop":
            player.queue.clear()
            await player.stop()
            return _json_response({"ok": True, "action": "stop"})

        elif action == "volume":
            vol = int(value) if value is not None else 50
            vol = max(0, min(200, vol))
            await player.set_volume(vol)
            return _json_response({"ok": True, "action": "volume", "value": vol})

        elif action == "loop":
            mode = str(value).lower() if value else "off"
            if mode not in ("off", "track", "queue"):
                return _json_response({"error": "loop value must be: off, track, or queue"}, 400)
            player.loop_mode = mode
            return _json_response({"ok": True, "action": "loop", "mode": mode})

        elif action == "shuffle":
            player.queue.shuffle()
            return _json_response({"ok": True, "action": "shuffle"})

        elif action == "play_playlist":
            # value = playlist name (string)
            # This triggers the bot to load the playlist from MongoDB and play it
            # We use the music cog's internal methods
            playlist_name = str(value) if value else ""
            if not playlist_name:
                return _json_response({"error": "Playlist name required as value"}, 400)
            # Signal is sent; actual playlist loading requires the music cog
            # For now return ok — the frontend can use the Discord bot's /play command
            return _json_response({"ok": True, "action": "play_playlist", "note": "Use /play playlist:<name> in Discord"})

    except Exception as e:
        logger.exception("Control action %s failed: %s", action, e)
        return _json_response({"error": str(e)}, 500)

    return _json_response({"error": "Unknown error"}, 500)


async def _handle_options(request: web.Request) -> web.Response:
    """Handle CORS preflight."""
    return web.Response(status=204, headers=_cors_headers())


def create_web_app(bot: "MusicBot") -> web.Application:
    """Create and configure the aiohttp web application."""
    app = web.Application()
    app["bot"] = bot

    app.router.add_get("/health", _handle_health)
    app.router.add_get("/status", _handle_status)
    app.router.add_post("/control", _handle_control)

    # CORS preflight
    app.router.add_options("/{path_info:.*}", _handle_options)

    return app


async def start_web_server(bot: "MusicBot") -> web.AppRunner:
    """Start the web control server and return the runner (for cleanup)."""
    app = create_web_app(bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, WEB_SERVER_HOST, WEB_SERVER_PORT)
    await site.start()
    logger.info(
        "Music bot web control server started on %s:%s",
        WEB_SERVER_HOST,
        WEB_SERVER_PORT,
    )
    return runner
