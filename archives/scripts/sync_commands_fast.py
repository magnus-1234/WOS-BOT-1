"""
Quick script to sync Discord bot commands globally.
Simplified version that skips music cog to avoid Lavalink delays.

Usage: python sync_commands_fast.py
"""

import asyncio
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Add repo root to path
repo_root = str(Path(__file__).resolve().parent)
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

import discord
from discord.ext import commands

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

if not TOKEN:
    print("❌ Error: DISCORD_TOKEN not found in environment variables")
    sys.exit(1)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.presences = True
bot = commands.Bot(command_prefix='!', intents=intents)

async def main():
    # Essential cogs only (skip music to avoid Lavalink issues)
    cogs_to_load = [
        "cogs.start_menu",
        "cogs.alliance",
        "cogs.alliance_member_operations",
        "cogs.changes",
        "cogs.web_search",
        "cogs.welcome_channel",
        "cogs.control",
        "cogs.gift_operations",
        "cogs.manage_giftcode",
        "cogs.id_channel",
        "cogs.bot_operations",
        "cogs.remote_access",
        "cogs.fid_commands",
        "cogs.record_commands",
        "cogs.bear_trap",
        "cogs.bear_trap_editor",
        "cogs.attendance",
        "cogs.minister_schedule",
        "cogs.other_features",
        "cogs.support_operations",
        "cogs.minister_menu",
        "cogs.playerinfo",
        "cogs.reminder_system",
        "cogs.birthday_system",
        "cogs.events",
        "cogs.server_age",
        "cogs.personalise_chat",
        # "cogs.music",  # Skip - causes Lavalink timeout
        "cogs.voice_conversation",
        "cogs.auto_translate",
        "cogs.message_extractor",
        "cogs.tictactoe",
        "cogs.alliance_monitor",
    ]
    
    print("🔄 Loading cogs (skipping music to avoid delays)...")
    loaded = 0
    
    for cog_name in cogs_to_load:
        try:
            await bot.load_extension(cog_name)
            loaded += 1
        except Exception as e:
            print(f"   ⚠️  {cog_name}: {e}")
    
    print(f"📦 Loaded {loaded} cogs")
    print("🔐 Connecting to Discord...")
    
    @bot.event
    async def on_ready():
        print(f"✅ Logged in as {bot.user}")
        
        commands_list = bot.tree.get_commands()
        print(f"📋 {len(commands_list)} commands in tree")
        
        print("🌐 Syncing globally...")
        try:
            synced = await bot.tree.sync()
            print(f"\n✅ SUCCESS! Synced {len(synced)} commands:")
            for cmd in sorted(synced, key=lambda x: x.name):
                print(f"   /{cmd.name}")
            
            print("\n" + "="*50)
            print("Commands are now registered in Discord!")
            print("Check your bot's profile page to see them.")
            print("(May take up to 1 hour to propagate globally)")
            print("="*50)
        except Exception as e:
            print(f"❌ Sync failed: {e}")
        
        await bot.close()
    
    try:
        await bot.start(TOKEN)
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    print("="*50)
    print("🤖 Discord Command Sync (Fast)")
    print("="*50)
    asyncio.run(main())
