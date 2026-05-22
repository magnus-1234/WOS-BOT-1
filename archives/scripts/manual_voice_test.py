import discord
from discord.ext import commands
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()
token = os.getenv("DISCORD_TOKEN")

class ManualVoiceTest(discord.Client):
    async def on_ready(self):
        print(f"Logged in as {self.user}")
        
        # Manually pick a guild and channel
        # You'll need to provide these or I'll pick the first one
        if not self.guilds:
            print("No guilds found!")
            await self.close()
            return
            
        guild = self.guilds[0]
        for g in self.guilds:
            if g.name == "WHITEOUT SURVIVAL BOT" or "test" in g.name.lower():
                guild = g
                break
        
        print(f"Target Guild: {guild.name} ({guild.id})")
        
        # Find a voice channel
        channel = None
        for vc in guild.voice_channels:
            channel = vc
            break
            
        if not channel:
            print("No voice channel found!")
            await self.close()
            return
            
        print(f"Target Channel: {channel.name} ({channel.id})")
        
        # STEP 1: Send the raw join OpCode
        print("🚀 Sending raw VOICE_STATE_UPDATE...")
        await self.ws.voice_state(guild.id, channel.id)
        
        print("⏳ Waiting 10 seconds for VOICE_SERVER_UPDATE event...")
        # We'll see if it arrives in the on_socket_response
        await asyncio.sleep(10)
        
        print("🏁 Test finished.")
        await self.close()

    async def on_socket_response(self, msg):
        t = msg.get('t')
        if t in ['VOICE_STATE_UPDATE', 'VOICE_SERVER_UPDATE']:
            print(f"📡 [GATEWAY_EVENT] {t}: {msg}")

if __name__ == "__main__":
    client = ManualVoiceTest(intents=discord.Intents.default())
    client.run(token)
