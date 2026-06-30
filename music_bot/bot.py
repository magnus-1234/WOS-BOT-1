import asyncio
import logging
import os
import sys
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv


for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

load_dotenv(REPO_ROOT / ".env")

logging.basicConfig(
    level=os.getenv("MUSIC_BOT_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("music_bot")


def _guild_ids() -> list[int]:
    configured = os.getenv("MUSIC_GUILD_IDS")
    if configured:
        values = configured.replace(";", ",").split(",")
    else:
        values = [os.getenv("GUILD_ID_1"), os.getenv("GUILD_ID_2")]

    guilds: list[int] = []
    for value in values:
        value = (value or "").strip()
        if not value:
            continue
        try:
            guilds.append(int(value))
        except ValueError:
            logger.warning("Ignoring invalid guild id: %s", value)
    return guilds


class MusicBot(commands.Bot):
    async def setup_hook(self) -> None:
        from music_bot.storage.music_state_storage import music_state_storage
        from music_bot.storage.playlist_storage import playlist_storage

        await playlist_storage.initialize()
        await music_state_storage.initialize()
        await self.load_extension("music_bot.cogs.music")
        logger.info("Loaded music bot cog")

        guild_ids = _guild_ids()
        if guild_ids:
            for guild_id in guild_ids:
                try:
                    guild = discord.Object(id=guild_id)
                    self.tree.copy_global_to(guild=guild)
                    synced = await self.tree.sync(guild=guild)
                    logger.info("Synced %s music commands to guild %s", len(synced), guild_id)
                except discord.Forbidden as e:
                    logger.warning("Could not sync commands to guild %s due to missing permissions (Forbidden): %s", guild_id, e)
                except discord.HTTPException as e:
                    logger.warning("HTTPException when syncing commands to guild %s: %s", guild_id, e)
        else:
            try:
                synced = await self.tree.sync()
                logger.info("Synced %s music commands globally", len(synced))
            except discord.HTTPException as e:
                logger.warning("Failed to sync commands globally: %s", e)


intents = discord.Intents.default()
intents.message_content = False
intents.members = False
intents.voice_states = True

bot = MusicBot(command_prefix=os.getenv("MUSIC_COMMAND_PREFIX", "!"), intents=intents)


@bot.event
async def on_ready() -> None:
    logger.info("Music bot logged in as %s (%s)", bot.user, bot.user.id if bot.user else "unknown")


async def main() -> None:
    token = os.getenv("MUSIC_DISCORD_TOKEN")
    if not token:
        raise RuntimeError("MUSIC_DISCORD_TOKEN is not set")
    async with bot:
        await bot.start(token)


def run_dummy_server():
    import threading
    from http.server import SimpleHTTPRequestHandler, HTTPServer
    
    port = int(os.getenv("PORT", "8080"))
    class DummyHandler(SimpleHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")
        def log_message(self, format, *args):
            pass # Suppress logging to keep output clean

    try:
        server = HTTPServer(("0.0.0.0", port), DummyHandler)
        logger.info(f"Starting dummy web server on port {port} for Render compatibility")
        server.serve_forever()
    except Exception as e:
        logger.error(f"Failed to start dummy web server: {e}")


if __name__ == "__main__":
    import threading
    threading.Thread(target=run_dummy_server, daemon=True).start()
    asyncio.run(main())
