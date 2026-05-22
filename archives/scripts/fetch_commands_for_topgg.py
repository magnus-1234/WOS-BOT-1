"""
Fetch Discord slash commands for Top.gg import.
Run this once, copy topgg_commands.json contents, and paste into Top.gg > Commands > Import from Discord.
"""
import asyncio
import json
import os
import aiohttp
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

# Fields that Top.gg does NOT accept - strip them out
STRIP_FIELDS = {"id", "application_id", "version", "dm_permission", "contexts", "integration_types", "guild_id"}


def clean_command(cmd: dict) -> dict:
    """Recursively strip Discord-only metadata fields, keeping only the core schema."""
    cleaned = {k: v for k, v in cmd.items() if k not in STRIP_FIELDS}
    if "options" in cleaned and cleaned["options"]:
        cleaned["options"] = [clean_command(opt) for opt in cleaned["options"]]
    return cleaned


async def fetch_application_id(session: aiohttp.ClientSession) -> str:
    async with session.get(
        "https://discord.com/api/v10/applications/@me",
        headers={"Authorization": f"Bot {TOKEN}"},
    ) as resp:
        data = await resp.json()
        return data["id"]


async def fetch_global_commands(session: aiohttp.ClientSession, app_id: str) -> list:
    async with session.get(
        f"https://discord.com/api/v10/applications/{app_id}/commands",
        headers={"Authorization": f"Bot {TOKEN}"},
    ) as resp:
        return await resp.json()


async def main():
    if not TOKEN:
        print("[ERROR] DISCORD_TOKEN not found in .env")
        return

    async with aiohttp.ClientSession() as session:
        print("[*] Fetching Application ID...")
        app_id = await fetch_application_id(session)
        print(f"[OK] Application ID: {app_id}\n")

        print("[*] Fetching global slash commands...")
        raw_commands = await fetch_global_commands(session, app_id)

        if isinstance(raw_commands, dict) and "code" in raw_commands:
            print(f"[ERROR] Discord API error: {raw_commands}")
            return

        print(f"[OK] Found {len(raw_commands)} global commands.\n")

        # Clean commands for Top.gg compatibility
        cleaned_commands = [clean_command(cmd) for cmd in raw_commands]

        # Save to file
        with open("topgg_commands.json", "w", encoding="utf-8") as f:
            json.dump(cleaned_commands, f, indent=2)

        print("=" * 60)
        print("DONE! Open topgg_commands.json, copy all contents,")
        print("and paste into Top.gg > Commands > Import from Discord.")
        print("=" * 60)
        print(f"\n[SAVED] topgg_commands.json ({len(cleaned_commands)} commands)")


asyncio.run(main())
