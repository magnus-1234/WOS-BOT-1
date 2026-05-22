"""
Quick script to add /playlist and /previous commands to music.py
Run this script to automatically integrate the commands.
"""

import re

# Read the current music.py file
with open('cogs/music.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Code to insert
new_commands = '''        
    @app_commands.command(name="previous", description="Play the previous track from history")
    async def previous(self, interaction: discord.Interaction):
        """Play previous track"""
        player: CustomPlayer = interaction.guild.voice_client
        
        if not player:
            await interaction.response.send_message("❌ Not connected to voice!", ephemeral=True)
            return
        
        if not player.history:
            await interaction.response.send_message("⏮️ No previous tracks in history", ephemeral=True)
            return
        
        # Get the last track from history
        previous_track = player.history.pop()
        await player.play(previous_track)
        await interaction.response.send_message(f"⏮️ Playing previous: **{previous_track.title}**")
        
    @app_commands.command(name="playlist", description="Manage your saved playlists")
    async def playlist(self, interaction: discord.Interaction):
        """Open playlist management panel"""
        if not PlaylistManagementView:
            await interaction.response.send_message(
                "❌ Playlist management is not available. Please contact the bot administrator.",
                ephemeral=True
            )
            return
        
        player: CustomPlayer = interaction.guild.voice_client
        
        # Create player if not exists (for viewing playlists even when not in voice)
        if not player:
            # Check if user is in voice channel
            if interaction.user.voice and interaction.user.voice.channel:
                try:
                    player = await interaction.user.voice.channel.connect(cls=CustomPlayer, timeout=60.0)
                    player.text_channel = interaction.channel
                except Exception as e:
                    await interaction.response.send_message(
                        f"❌ Failed to connect to voice: {e}",
                        ephemeral=True
                    )
                    return
            else:
                # Allow viewing playlists without being in voice
                # Create a dummy player reference
                player = type('DummyPlayer', (), {
                    'queue': type('Queue', (), {'is_empty': True, 'count': 0})(),
                    'current': None,
                    'current_playlist_name': None
                })()
        
        view = PlaylistManagementView(interaction.user.id, interaction.guild.id, player)
        embed = view.get_embed()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

'''

# Find the setup function and insert before it
setup_pattern = r'\n\nasync def setup\(bot: commands\.Bot\):'
if re.search(setup_pattern, content):
    # Insert the new commands before the setup function
    content = re.sub(setup_pattern, new_commands + '\n\nasync def setup(bot: commands.Bot):', content)
    
    # Write back to file
    with open('cogs/music.py', 'w', encoding='utf-8') as f:
        f.write(content)
    
    print("✅ Successfully added /playlist and /previous commands to music.py!")
    print("📝 Please restart your bot to see the new commands.")
else:
    print("❌ Could not find the setup function in music.py")
    print("Please add the commands manually using the integration guide.")
