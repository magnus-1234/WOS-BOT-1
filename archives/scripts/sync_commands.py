"""
Quick script to sync Discord bot commands globally.
This registers all slash commands with Discord's API so they appear in the bot's profile page.

Usage: python sync_commands.py
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
    print("   Make sure your .env file contains: DISCORD_TOKEN=your_bot_token")
    sys.exit(1)

# Create bot instance with same setup as app.py
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.presences = True
bot = commands.Bot(command_prefix='!', intents=intents)

async def main():
    """Load all cogs and sync commands globally"""
    
    # List of cogs to load (same as app.py)
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
        "cogs.music",
        "cogs.voice_conversation",
        "cogs.auto_translate",
        "cogs.message_extractor",
        "cogs.tictactoe",
        "cogs.alliance_monitor",
    ]
    
    print("🔄 Loading cogs...")
    loaded = 0
    failed = 0
    
    for cog_name in cogs_to_load:
        try:
            await bot.load_extension(cog_name)
            print(f"   ✅ {cog_name}")
            loaded += 1
        except Exception as e:
            print(f"   ❌ {cog_name}: {e}")
            failed += 1
    
    print(f"\n📦 Cogs: {loaded} loaded, {failed} failed")
    
    # Login and sync
    print("\n🔐 Logging in to Discord...")
    
    @bot.event
    async def on_ready():
        print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
        print(f"📊 Connected to {len(bot.guilds)} guild(s)")
        
        # Get list of commands before sync
        commands_before = bot.tree.get_commands()
        print(f"\n📋 Commands registered in tree: {len(commands_before)}")
        for cmd in commands_before[:20]:  # Show first 20
            print(f"   • /{cmd.name}: {cmd.description[:50]}...")
        if len(commands_before) > 20:
            print(f"   ... and {len(commands_before) - 20} more")
        
        # Sync globally
        print("\n🌐 Syncing commands globally with Discord API...")
        try:
            synced = await bot.tree.sync()
            print(f"\n✅ Successfully synced {len(synced)} commands globally!")
            print("\n📝 Synced commands:")
            for cmd in synced:
                print(f"   • /{cmd.name}")
            
            print("\n" + "="*60)
            print("✅ DONE! Commands are now registered with Discord.")
            print("   They should appear in your bot's profile page.")
            print("   Note: It may take up to 1 hour for Discord to propagate")
            print("   the changes to all servers globally.")
            print("="*60)
        except Exception as e:
            print(f"❌ Sync failed: {e}")
        
        # Close the bot
        await bot.close()
    
    try:
        await bot.start(TOKEN)
    except discord.LoginFailure:
        print("❌ Invalid token! Check your DISCORD_TOKEN in .env")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    print("="*60)
    print("🤖 Discord Bot Command Sync Tool")
    print("="*60)
    asyncio.run(main())
