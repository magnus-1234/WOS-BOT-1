"""
Music Bot Web Control Server
Provides HTTP API for the website to control and monitor the Discord music bot.
Runs on a separate port alongside the Discord bot.

Endpoints:
  GET  /guilds                   - List servers the bot can control
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
WEB_SERVER_PORT = int(os.getenv("MUSIC_WEB_SERVER_PORT", os.getenv("PORT", "8090")))
WEB_SERVER_HOST = os.getenv("MUSIC_WEB_SERVER_HOST", "0.0.0.0")

# Allowed actions
VALID_ACTIONS = {"pause", "resume", "skip", "previous", "stop", "volume", "loop", "shuffle", "play_playlist", "channels", "play"}


def _guild_icon_url(guild) -> Optional[str]:
    """Return a Discord CDN icon URL for a guild when one exists."""
    try:
        return str(guild.icon.url) if guild.icon else None
    except Exception:
        return None


def _guild_payload(guild) -> dict:
    active_player = guild.voice_client
    active_channel = getattr(active_player, "channel", None)
    return {
        "id": str(guild.id),
        "name": guild.name,
        "iconUrl": _guild_icon_url(guild),
        "memberCount": guild.member_count or 0,
        "voiceChannelCount": len(guild.voice_channels),
        "textChannelCount": len(guild.text_channels),
        "activeVoiceChannel": (
            {"id": str(active_channel.id), "name": active_channel.name}
            if active_channel
            else None
        ),
    }


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


async def _handle_guilds(request: web.Request) -> web.Response:
    """List servers the bot is currently in."""
    if not _verify_token(request):
        return _json_response({"error": "Unauthorized"}, 401)

    bot: "MusicBot" = request.app["bot"]
    guilds = sorted((_guild_payload(guild) for guild in bot.guilds), key=lambda item: item["name"].lower())
    return _json_response({"ok": True, "guilds": guilds})


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
    voice_channel_id = body.get("voiceChannelId")
    text_channel_id = body.get("textChannelId")
    user_id = body.get("userId")

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
    if action not in ("channels", "play") and (not player or not isinstance(player, wavelink.Player)):
        return _json_response({"error": "Bot is not in a voice channel in this server"}, 404)

    try:
        if action == "channels":
            return _json_response({
                "ok": True,
                "voiceChannels": [{"id": str(c.id), "name": c.name} for c in guild.voice_channels],
                "textChannels": [{"id": str(c.id), "name": c.name} for c in guild.text_channels],
            })
            
        elif action == "play":
            query = str(value) if value else ""
            if not query:
                return _json_response({"error": "Query is required for play action"}, 400)
            
            if not player or not isinstance(player, wavelink.Player):
                if not voice_channel_id:
                    return _json_response({"error": "Bot is not in a voice channel and no voiceChannelId provided"}, 400)
                vc = bot.get_channel(int(voice_channel_id))
                if not vc:
                    return _json_response({"error": "Voice channel not found"}, 404)
                from music_bot.cogs.music import CustomPlayer
                player = await vc.connect(cls=CustomPlayer)
            if text_channel_id and hasattr(player, "text_channel"):
                text_channel = guild.get_channel(int(text_channel_id))
                if text_channel:
                    player.text_channel = text_channel
                
            tracks = await wavelink.Playable.search(query)
            if not tracks:
                return _json_response({"error": "No tracks found"}, 404)
                
            track = tracks[0] if isinstance(tracks, list) else tracks
            if isinstance(track, wavelink.Playlist):
                for t in track.tracks:
                    await player.queue.put_wait(t)
                if not player.playing:
                    await player.play(player.queue.get())
                return _json_response({"ok": True, "action": "play", "track": track.name, "isPlaylist": True})
            else:
                await player.queue.put_wait(track)
                if not player.playing:
                    await player.play(player.queue.get())
                return _json_response({"ok": True, "action": "play", "track": track.title})
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
            playlist_name = str(value) if value else ""
            if not playlist_name:
                return _json_response({"error": "Playlist name required as value"}, 400)
            if not user_id:
                return _json_response({"error": "userId is required to load a saved playlist"}, 400)

            if not player or not isinstance(player, wavelink.Player):
                if not voice_channel_id:
                    return _json_response({"error": "Select a voice channel before playing a playlist"}, 400)
                vc = bot.get_channel(int(voice_channel_id))
                if not vc:
                    return _json_response({"error": "Voice channel not found"}, 404)
                from music_bot.cogs.music import CustomPlayer
                player = await vc.connect(cls=CustomPlayer)
            if text_channel_id and hasattr(player, "text_channel"):
                text_channel = guild.get_channel(int(text_channel_id))
                if text_channel:
                    player.text_channel = text_channel

            from music_bot.storage.playlist_storage import playlist_storage
            playlist = await playlist_storage.load_playlist(guild_id, int(user_id), playlist_name)
            if not playlist:
                return _json_response({"error": "Playlist not found for this Discord user and server"}, 404)

            loaded = 0
            for saved_track in playlist.get("tracks", []):
                uri = saved_track.get("uri") or saved_track.get("title")
                if not uri:
                    continue
                tracks = await wavelink.Playable.search(str(uri))
                if not tracks:
                    continue
                track = tracks[0] if isinstance(tracks, list) else tracks
                if isinstance(track, wavelink.Playlist):
                    for playlist_track in track.tracks:
                        await player.queue.put_wait(playlist_track)
                        loaded += 1
                else:
                    await player.queue.put_wait(track)
                    loaded += 1

            if loaded == 0:
                return _json_response({"error": "No playable tracks were found in that playlist"}, 404)

            player.current_playlist_name = playlist_name
            if not player.playing and not player.queue.is_empty:
                await player.play(player.queue.get())
            return _json_response({"ok": True, "action": "play_playlist", "playlist": playlist_name, "tracks": loaded})

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
    app.router.add_get("/guilds", _handle_guilds)
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
