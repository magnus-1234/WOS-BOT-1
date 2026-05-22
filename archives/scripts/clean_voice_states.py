import discord
from discord.ext import commands
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()
token = os.getenv("DISCORD_TOKEN")

class CleanupBot(discord.Client):
    async def on_ready(self):
        print(f"Logged in as {self.user}")
        for guild in self.guilds:
            if guild.voice_client:
                print(f"Disconnecting from {guild.name}...")
                await guild.voice_client.disconnect(force=True)
            
            # Send a raw disconnect packet to be sure
            await self.ws.voice_state(guild.id, None)
            print(f"Cleared voice state for {guild.name}")
        
        print("Cleanup complete!")
        await self.close()

if __name__ == "__main__":
    client = CleanupBot(intents=discord.Intents.default())
    client.run(token)
