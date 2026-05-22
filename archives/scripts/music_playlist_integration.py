"""
Music Cog Enhancements - Integration Code
This file contains the code additions needed for music.py to add playlist management features.

INSTRUCTIONS FOR INTEGRATION:
1. Add the imports at the top of music.py (after line 17)
2. Add the Previous button to PlayerControlView (after line 68)
3. Add the /previous and /playlist commands to the Music class (before the setup function around line 813)
"""

# ============================================================================
# SECTION 1: Add these imports after line 17 in music.py
# ============================================================================

# Import playlist UI components
try:
    from cogs.playlist_ui import PlaylistManagementView
except ImportError:
    PlaylistManagementView = None


# ============================================================================
# SECTION 2: Add this button to PlayerControlView class (after line 68, before play_pause button)
# ============================================================================

    @discord.ui.button(emoji="⏮️", style=discord.ButtonStyle.secondary, custom_id="music_previous")
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Go to previous track"""
        try:
            if not self.player.history:
                await interaction.response.send_message("⏮️ No previous tracks in history", ephemeral=True)
                return
            
            # Get the last track from history
            if len(self.player.history) > 0:
                previous_track = self.player.history.pop()
                await self.player.play(previous_track)
                await interaction.response.send_message(f"⏮️ Playing previous: **{previous_track.title}**", ephemeral=True)
            else:
                await interaction.response.send_message("⏮️ No previous track available", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)


# ============================================================================
# SECTION 3: Add these commands to the Music class (before async def setup around line 813)
# ============================================================================

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


# ============================================================================
# SECTION 4: Update the now playing embed to show playlist name (around line 437-441)
# Find this section and add the playlist name field:
# ============================================================================

# Add this after the "Loop and shuffle status" section (around line 437):
        # Show playlist name if loaded from saved playlist
        if player.current_playlist_name:
            embed.add_field(
                name="📁 Playlist",
                value=player.current_playlist_name,
                inline=True
            )
