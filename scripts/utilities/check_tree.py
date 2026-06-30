
import asyncio
import os
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

intents = discord.Intents.default()
bot = commands.Bot(command_prefix='!', intents=intents)

async def check_commands():
    # Load music cog
    try:
        await bot.load_extension('music_bot.cogs.music')
        print("✅ Loaded music_bot.cogs.music")
    except Exception as e:
        print(f"❌ Failed to load music_bot.cogs.music: {e}")
        return

    print("\n--- Global Commands in Tree ---")
    for cmd in bot.tree.get_commands():
        print(f"/{cmd.name}: {cmd.description}")
        if isinstance(cmd, discord.app_commands.Group):
            for sub in cmd.commands:
                print(f"  - /{cmd.name} {sub.name}")

    print("\n--- Prefix Commands ---")
    for cmd in bot.commands:
        print(f"!{cmd.name}")

    await bot.close()

if __name__ == "__main__":
    asyncio.run(check_commands())
