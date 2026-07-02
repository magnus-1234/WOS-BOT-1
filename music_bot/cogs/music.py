"""
Music Cog for Discord Bot
Provides comprehensive music playback functionality using Wavelink and Lavalink
"""

import discord
from discord.ext import commands
from discord import app_commands
import wavelink
import asyncio
from typing import Optional, List
import re
from datetime import timedelta
import time
import os
from dotenv import load_dotenv

load_dotenv()

# Import playlist UI components
try:
    from music_bot.cogs.playlist_ui import PlaylistManagementView, AddToPlaylistView
except ImportError:
    PlaylistManagementView = None
    AddToPlaylistView = None

# Import music state storage
try:
    from music_bot.storage.music_state_storage import music_state_storage
except ImportError:
    music_state_storage = None

# Music restart behavior configuration
MUSIC_AUTO_RESUME = os.getenv('MUSIC_AUTO_RESUME', 'false').lower() == 'true'  # Pause by default


class CustomPlayer(wavelink.Player):
    """Custom player with queue management and additional features"""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Don't override queue - use Wavelink's built-in queue
        self.history: List[wavelink.Playable] = []
        self.forward_history: List[wavelink.Playable] = []  # For forward navigation after going back
        self.history_index: int = -1  # For previous track navigation
        self.loop_mode: str = "off"  # off, track, queue
        self.autoplay_enabled: bool = False
        self.dj_role_id: Optional[int] = None
        self.text_channel: Optional[discord.TextChannel] = None
        self.controller_message: Optional[discord.Message] = None
        self.current_playlist_name: Optional[str] = None  # Track loaded playlist name
        self.now_playing_message: Optional[discord.Message] = None  # Store now playing message for editing
        self._progress_update_task: Optional[asyncio.Task] = None  # Background task for progress updates
        self._last_update_time: float = 0  # Track last update time for rate limiting
        
        # Rate limiting and update queue system
        self._update_lock: asyncio.Lock = asyncio.Lock()  # Prevent concurrent edits
        self._pending_update_task: Optional[asyncio.Task] = None  # Track pending update
        self._min_update_interval: float = 2.0  # Minimum 2 seconds between updates
        self._last_message_update: float = 0  # Timestamp of last successful message update
        
        # Audio effects state
        self.bass_boost_level: float = 1.0  # 1.0-5.0
        self.speed_multiplier: float = 1.0  # 0.1-3.0
        self.pitch_multiplier: float = 1.0  # 0.5-2.0
        self.nightcore_enabled: bool = False
        self.slowed_reverb_enabled: bool = False
        
        # Auto-save state task for persistence across restarts
        self._autosave_task: Optional[asyncio.Task] = None
        self._autosave_interval: float = 10.0  # Save state every 10 seconds
    
    async def schedule_message_update(self, music_cog, immediate: bool = False):
        """
        Schedule a debounced update to the now playing message.
        Prevents concurrent edits and enforces minimum interval between updates.
        
        Args:
            music_cog: Reference to the Music cog for creating embeds
            immediate: If True, skip the debounce delay (but still respect minimum interval)
        """
        # Cancel any pending update
        if self._pending_update_task and not self._pending_update_task.done():
            self._pending_update_task.cancel()
            try:
                await self._pending_update_task
            except asyncio.CancelledError:
                pass
        
        async def _perform_update():
            """Internal function to perform the actual update"""
            try:
                # Wait for debounce delay unless immediate
                if not immediate:
                    await asyncio.sleep(0.5)  # 500ms debounce
                
                # Acquire lock to prevent concurrent edits
                async with self._update_lock:
                    # Check if message still exists
                    if not self.now_playing_message or not self.current:
                        return
                    
                    # Enforce minimum interval between updates
                    current_time = time.time()
                    time_since_last_update = current_time - self._last_message_update
                    
                    if time_since_last_update < self._min_update_interval:
                        # Wait for the remaining time
                        wait_time = self._min_update_interval - time_since_last_update
                        await asyncio.sleep(wait_time)
                    
                    # Perform the update
                    try:
                        from music_bot.cogs.music import PlayerControlView  # Import here to avoid circular import
                        embed = music_cog.create_now_playing_embed(self)
                        view = PlayerControlView(self)
                        await self.now_playing_message.edit(embed=embed, view=view)
                        self._last_message_update = time.time()
                        self._last_update_time = self._last_message_update  # Update legacy field too
                    except discord.NotFound:
                        # Message was deleted
                        self.now_playing_message = None
                    except discord.HTTPException as e:
                        if e.status == 429:
                            # Still got rate limited, back off more
                            print(f"⚠️ Rate limited despite throttling, increasing cooldown")
                            self._min_update_interval = min(5.0, self._min_update_interval + 0.5)
                        else:
                            print(f"HTTP error updating now playing message: {e}")
                    except Exception as e:
                        print(f"Error updating now playing message: {e}")
                        
            except asyncio.CancelledError:
                pass  # Update was cancelled, that's fine
            except Exception as e:
                print(f"Unexpected error in message update: {e}")
        
        # Schedule the update
        self._pending_update_task = asyncio.create_task(_perform_update())
        
    async def add_track(self, track: wavelink.Playable, requester: discord.Member):
        """Add a track to the queue"""
        # Store user ID instead of Member object (for JSON serialization)
        track.extras.requester_id = requester.id
        track.extras.requester_name = str(requester)
        self.queue.put(track)  # Use Wavelink's queue.put() method
        
    async def next_track(self) -> Optional[wavelink.Playable]:
        """Get the next track from queue"""
        if self.loop_mode == "track" and self.current:
            return self.current
            
        if self.loop_mode == "queue" and self.current:
            # Re-add current track to queue for looping
            # Preserve the requester info
            if hasattr(self.current.extras, 'requester_id'):
                self.current.extras.requester_id = self.current.extras.requester_id
            if hasattr(self.current.extras, 'requester_name'):
                self.current.extras.requester_name = self.current.extras.requester_name
            self.queue.put(self.current)
            
        if not self.queue.is_empty:
            return self.queue.get()
            
        return None
    
    async def apply_filters(self):
        """Build and apply current filter configuration"""
        try:
            filters = wavelink.Filters()
            
            # Apply bass boost using equalizer
            if self.bass_boost_level > 1.0:
                # Boost low frequency bands (0-2)
                equalizer = [
                    {"band": 0, "gain": 0.2 * (self.bass_boost_level - 1.0)},  # 25 Hz
                    {"band": 1, "gain": 0.15 * (self.bass_boost_level - 1.0)}, # 40 Hz
                    {"band": 2, "gain": 0.1 * (self.bass_boost_level - 1.0)},  # 63 Hz
                ]
                filters.equalizer.set(bands=equalizer)
            
            # Apply preset modes or individual speed/pitch
            if self.nightcore_enabled:
                # Nightcore: faster + higher pitch
                filters.timescale.set(speed=1.3, pitch=1.3, rate=1.0)
            elif self.slowed_reverb_enabled:
                # Slowed & Reverb: slower + lower pitch
                filters.timescale.set(speed=0.8, pitch=0.9, rate=1.0)
            else:
                # Apply individual speed and pitch settings
                if self.speed_multiplier != 1.0 or self.pitch_multiplier != 1.0:
                    filters.timescale.set(
                        speed=self.speed_multiplier,
                        pitch=self.pitch_multiplier,
                        rate=1.0
                    )
            
            # Apply filters to player
            await self.set_filters(filters)
            
        except Exception as e:
            print(f"Error applying filters: {e}")
            import traceback
            traceback.print_exc()
    
    async def reset_filters(self):
        """Reset all audio effects to default"""
        self.bass_boost_level = 1.0
        self.speed_multiplier = 1.0
        self.pitch_multiplier = 1.0
        self.nightcore_enabled = False
        self.slowed_reverb_enabled = False
        
        # Clear all filters
        try:
            await self.set_filters(None)
        except Exception as e:
            print(f"Error resetting filters: {e}")
    
    def get_filter_status(self) -> dict:
        """Get current filter settings as dictionary"""
        return {
            "bass_boost": self.bass_boost_level,
            "speed": self.speed_multiplier,
            "pitch": self.pitch_multiplier,
            "nightcore": self.nightcore_enabled,
            "slowed_reverb": self.slowed_reverb_enabled
        }
    
    async def save_state(self):
        """Save current playback state to database"""
        if not music_state_storage or not self.guild:
            return
        
        try:
            # Prepare current track data
            current_track_data = None
            if self.current:
                current_track_data = {
                    'uri': self.current.uri,
                    'title': self.current.title,
                    'author': self.current.author,
                    'position': self.position
                }
            
            # Prepare queue data
            queue_data = []
            for track in list(self.queue):
                queue_data.append({
                    'uri': track.uri,
                    'title': track.title,
                    'author': track.author,
                    'requester_id': getattr(track.extras, 'requester_id', None),
                    'requester_name': getattr(track.extras, 'requester_name', 'Unknown')
                })
            
            # Save to storage
            await music_state_storage.save_state(
                guild_id=self.guild.id,
                channel_id=self.channel.id if self.channel else None,
                text_channel_id=self.text_channel.id if self.text_channel else None,
                current_track=current_track_data,
                queue=queue_data,
                loop_mode=self.loop_mode,
                volume=self.volume,
                playlist_name=self.current_playlist_name
            )
        except Exception as e:
            print(f"Error saving music state: {e}")
    
    async def start_progress_updates(self, music_cog):
        """Start background task to update progress bar in real-time"""
        # Stop any existing task
        await self.stop_progress_updates()
        
        # Track when the message was created for refresh timing
        self.now_playing_message_created_at = time.time()
        
        async def update_progress():
            """Background task to update the now playing message periodically"""
            import time
            while True:
                try:
                    await asyncio.sleep(15)  # Update every 15 seconds (increased from 10s)
                    
                    # Check if we should still be updating
                    if not self.playing or not self.now_playing_message or not self.current:
                        break
                    
                    # Check if message is older than 14 minutes (840 seconds)
                    # Refresh before 15-minute webhook token expiration
                    current_time = time.time()
                    message_age = current_time - self.now_playing_message_created_at
                    
                    if message_age > 840:  # 14 minutes
                        # Send a fresh message and delete the old one
                        try:
                            if self.text_channel:
                                from music_bot.cogs.music import PlayerControlView
                                embed = music_cog.create_now_playing_embed(self)
                                view = PlayerControlView(self)
                                
                                # Send new message
                                new_message = await self.text_channel.send(embed=embed, view=view)
                                
                                # Delete old message
                                try:
                                    await self.now_playing_message.delete()
                                except:
                                    pass  # Ignore if already deleted
                                
                                # Update reference
                                self.now_playing_message = new_message
                                self.now_playing_message_created_at = current_time
                                self._last_message_update = current_time
                                
                                print(f"🔄 Refreshed now playing message (preventing token expiration)")
                                continue
                        except Exception as e:
                            print(f"Error refreshing now playing message: {e}")
                            break
                    
                    # Use the new centralized update method
                    await self.schedule_message_update(music_cog, immediate=True)
                        
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    print(f"Unexpected error in progress update task: {e}")
                    break
        
        # Start the background task
        self._progress_update_task = asyncio.create_task(update_progress())
    
    async def stop_progress_updates(self):
        """Stop the progress update background task"""
        if self._progress_update_task and not self._progress_update_task.done():
            self._progress_update_task.cancel()
            try:
                await self._progress_update_task
            except asyncio.CancelledError:
                pass
            self._progress_update_task = None

    def start_autosave(self):
        """Start background task to auto-save player state periodically"""
        if self._autosave_task and not self._autosave_task.done():
            return  # Already running
        
        async def autosave_loop():
            """Background task to save player state every N seconds during playback"""
            try:
                while True:
                    await asyncio.sleep(self._autosave_interval)
                    
                    # Only save if we're actively playing something
                    if self.playing and self.current:
                        try:
                            await self.save_state()
                            print(f"💾 Auto-saved music state for {self.guild.name} (Position: {self.position}ms)")
                        except Exception as e:
                            print(f"⚠️ Error in autosave: {e}")
            except asyncio.CancelledError:
                pass
        
        self._autosave_task = asyncio.create_task(autosave_loop())
    
    async def stop_autosave(self):
        """Stop the auto-save background task"""
        if self._autosave_task and not self._autosave_task.done():
            self._autosave_task.cancel()
            try:
                await self._autosave_task
            except asyncio.CancelledError:
                pass
            self._autosave_task = None


class PlayerControlView(discord.ui.View):
    """Interactive player controls"""
    
    def __init__(self, player: CustomPlayer):
        super().__init__(timeout=None)
        self.player = player
        
        # Update stop button based on player state
        self._update_stop_button_state()
    
    def _update_stop_button_state(self):
        """Update the stop button emoji and style based on player state"""
        # Find the stop button in the view's children
        for item in self.children:
            if isinstance(item, discord.ui.Button) and item.custom_id == "music_stop":
                if self.player.paused:
                    item.emoji = "▶️"
                    item.style = discord.ButtonStyle.success
                else:
                    item.emoji = "⏹️"
                    item.style = discord.ButtonStyle.danger
                break
        
    @discord.ui.button(emoji="⏮️", style=discord.ButtonStyle.primary, custom_id="music_previous")
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Play previous track"""
        try:
            if not self.player.history:
                await interaction.response.send_message("⏮️ No previous tracks in history", ephemeral=True)
                return
            
            await interaction.response.defer(ephemeral=True)
            
            # Save current track to forward history before going back
            if self.player.current:
                self.player.forward_history.append(self.player.current)
            
            # Get the last track from history (don't pop yet)
            previous_track = self.player.history.pop()
            
            # Get the music cog to use safe_play
            music_cog = interaction.client.get_cog('Music')
            if music_cog:
                success = await music_cog.safe_play(self.player, previous_track, interaction)
                
                if success:
                    # Schedule update to the now playing message
                    await self.player.schedule_message_update(music_cog, immediate=True)
                    
                    await interaction.followup.send(f"⏮️ Playing previous: **{previous_track.title}**", ephemeral=True)
                else:
                    # Restore history if play failed
                    self.player.history.append(previous_track)
                    if self.player.forward_history:
                        self.player.forward_history.pop()
                    await interaction.followup.send("❌ Failed to play previous track", ephemeral=True)
            else:
                await interaction.followup.send("❌ Music system error", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)
            
    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary, custom_id="music_skip")
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Skip to next track"""
        try:
            if not self.player.current:
                await interaction.response.send_message("❌ Nothing is playing!", ephemeral=True)
                return
            
            await interaction.response.defer(ephemeral=True)
            
            # Check if we have forward history (user went back and now wants to go forward)
            if self.player.forward_history:
                # Play from forward history
                next_track = self.player.forward_history.pop()
                
                # Add current track to history
                if self.player.current:
                    self.player.history.append(self.player.current)
                
                # Get the music cog to use safe_play
                music_cog = interaction.client.get_cog('Music')
                if music_cog:
                    success = await music_cog.safe_play(self.player, next_track, interaction)
                    
                    if success:
                        # Schedule update to the now playing message
                        await self.player.schedule_message_update(music_cog, immediate=True)
                        
                        await interaction.followup.send(f"⏭️ Playing: **{next_track.title}**", ephemeral=True)
                    else:
                        await interaction.followup.send("❌ Failed to play track", ephemeral=True)
            else:
                # Normal skip - stop current track (will trigger on_wavelink_track_end which plays next)
                await self.player.stop()
                
                # Clear forward history when doing a normal skip
                self.player.forward_history.clear()
                
                # Wait a moment for the next track to start
                await asyncio.sleep(0.5)
                
                # Schedule update to the now playing message
                music_cog = interaction.client.get_cog('Music')
                if music_cog:
                    await self.player.schedule_message_update(music_cog, immediate=True)
                
                await interaction.followup.send("⏭️ Skipped track", ephemeral=True)
        except Exception as e:
            try:
                await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)
            except:
                await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)
            
    @discord.ui.button(emoji="⏹️", style=discord.ButtonStyle.danger, custom_id="music_stop")
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Pause/Resume playback without disconnecting"""
        try:
            if not self.player.current:
                await interaction.response.send_message("❌ Nothing is playing!", ephemeral=True)
                return
            
            music_cog = interaction.client.get_cog('Music')
            
            if self.player.paused:
                # Resume playback
                await self.player.pause(False)
                button.emoji = "⏹️"
                button.style = discord.ButtonStyle.danger
                
                # Schedule update to the now playing message
                if music_cog:
                    await self.player.schedule_message_update(music_cog)
                    # Update voice channel status
                    await music_cog.update_voice_channel_status(self.player)
                
                await interaction.response.send_message("▶️ Resumed playback", ephemeral=True)
            else:
                # Pause playback
                await self.player.pause(True)
                button.emoji = "▶️"
                button.style = discord.ButtonStyle.success
                
                # Schedule update to the now playing message
                if music_cog:
                    await self.player.schedule_message_update(music_cog)
                    # Update voice channel status
                    await music_cog.update_voice_channel_status(self.player)
                
                await interaction.response.send_message("⏸️ Paused playback", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)
            
    @discord.ui.button(emoji="🔁", style=discord.ButtonStyle.secondary, custom_id="music_loop")
    async def loop(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Cycle through loop modes"""
        try:
            modes = ["off", "track", "queue"]
            current_index = modes.index(self.player.loop_mode)
            next_mode = modes[(current_index + 1) % len(modes)]
            self.player.loop_mode = next_mode
            
            # Schedule update to the now playing message with new loop mode
            music_cog = interaction.client.get_cog('Music')
            if music_cog:
                await self.player.schedule_message_update(music_cog)
            
            # Acknowledge interaction silently (no message shown to user)
            try:
                await interaction.response.defer()
            except:
                pass  # Already responded
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)
            
    @discord.ui.button(emoji="🔀", style=discord.ButtonStyle.secondary, custom_id="music_shuffle")
    async def shuffle(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Shuffle the queue"""
        try:
            if self.player.queue.is_empty:
                await interaction.response.send_message("❌ Queue is empty!", ephemeral=True)
                return
                
            # Wavelink queue doesn't support shuffle directly, so we need to rebuild it
            import random
            tracks = list(self.player.queue)
            random.shuffle(tracks)
            self.player.queue.clear()
            for track in tracks:
                self.player.queue.put(track)
            await interaction.response.send_message("🔀 Shuffled the queue", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)
            
    @discord.ui.button(emoji="🔉", style=discord.ButtonStyle.secondary, custom_id="music_volume_down", row=1)
    async def volume_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Decrease volume by 10%"""
        try:
            new_volume = max(0, self.player.volume - 10)
            await self.player.set_volume(new_volume)
            
            # Schedule update to the now playing message with new volume
            music_cog = interaction.client.get_cog('Music')
            if music_cog:
                await self.player.schedule_message_update(music_cog)
            
            # Acknowledge interaction silently (no message shown to user)
            try:
                await interaction.response.defer()
            except:
                pass  # Already responded
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)
            
    @discord.ui.button(emoji="🔊", style=discord.ButtonStyle.secondary, custom_id="music_volume_up", row=1)
    async def volume_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Increase volume by 10%"""
        try:
            new_volume = min(100, self.player.volume + 10)
            await self.player.set_volume(new_volume)
            
            # Schedule update to the now playing message with new volume
            music_cog = interaction.client.get_cog('Music')
            if music_cog:
                await self.player.schedule_message_update(music_cog)
            
            # Acknowledge interaction silently (no message shown to user)
            try:
                await interaction.response.defer()
            except:
                pass  # Already responded
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)
            
    @discord.ui.button(emoji="⏩", style=discord.ButtonStyle.secondary, custom_id="music_seek", row=1)
    async def seek_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Seek to a position in the track"""
        try:
            if not self.player.current:
                await interaction.response.send_message("❌ Nothing is playing!", ephemeral=True)
                return
            
            # Create a modal for seeking
            modal = SeekModal(self.player)
            await interaction.response.send_modal(modal)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)
    
    @discord.ui.button(emoji="🎛️", style=discord.ButtonStyle.secondary, custom_id="music_effects", row=1)
    async def effects_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Open effects control panel"""
        try:
            if not self.player.current:
                await interaction.response.send_message("❌ Nothing is playing!", ephemeral=True)
                return
            
            # Create and send effects control view
            view = EffectsControlView(self.player)
            embed = view.get_embed()
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)
    
    @discord.ui.button(emoji="🔍", style=discord.ButtonStyle.secondary, custom_id="music_search", row=1)
    async def search_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Search for music"""
        try:
            # Open search modal
            modal = SearchModal(self.player, interaction.client)
            await interaction.response.send_modal(modal)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)
    
    @discord.ui.button(emoji="➕", label="Add to Playlist", style=discord.ButtonStyle.success, custom_id="music_add_to_playlist", row=2)
    async def add_to_playlist_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Add current track to a playlist"""
        try:
            if not self.player.current:
                await interaction.response.send_message("❌ Nothing is playing!", ephemeral=True)
                return
            
            # Create and send the add to playlist view
            view = AddToPlaylistView(self.player, interaction.user.id, interaction.guild.id)
            await view.load_playlists()
            embed = view.get_embed()
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)


class VoiceChannelButton(discord.ui.Button):
    """Individual button for each voice channel"""
    
    def __init__(self, channel: discord.VoiceChannel, guild: discord.Guild, music_cog, row: int):
        self.channel = channel
        self.guild = guild
        self.music_cog = music_cog
        
        # Check permissions
        permissions = channel.permissions_for(guild.me)
        has_permissions = permissions.connect and permissions.speak
        
        # Set emoji and style based on permissions
        emoji = "🔊" if has_permissions else "🔒"
        style = discord.ButtonStyle.primary if has_permissions else discord.ButtonStyle.secondary
        
        super().__init__(
            label=channel.name,
            emoji=emoji,
            style=style,
            disabled=not has_permissions,
            row=row
        )
    
    async def callback(self, interaction: discord.Interaction):
        """Handle channel button click"""
        try:
            await interaction.response.defer()
            
            # Move user to the voice channel if they're not already there
            if interaction.user.voice:
                if interaction.user.voice.channel != self.channel:
                    try:
                        await interaction.user.move_to(self.channel)
                    except discord.Forbidden:
                        await interaction.followup.send(
                            f"⚠️ I don't have permission to move you to **{self.channel.name}**. Please join manually.",
                            ephemeral=True
                        )
                    except Exception as e:
                        await interaction.followup.send(
                            f"⚠️ Could not move you to **{self.channel.name}**: {e}",
                            ephemeral=True
                        )
            
            # Connect bot to the voice channel
            try:
                player = await self.channel.connect(cls=CustomPlayer, timeout=60.0, self_deaf=True)
                player.text_channel = interaction.channel
                
                # Optimize voice quality
                await self.music_cog.optimize_voice_quality(self.channel)
                
                # Wait for connection
                await asyncio.sleep(1.5)
                
                # Verify connection
                max_retries = 5
                for i in range(max_retries):
                    if player.connected:
                        break
                    await asyncio.sleep(0.5)
                
                if not player.connected:
                    await interaction.followup.send(
                        "⚠️ Connection may be unstable. Please try again if music doesn't play.",
                        ephemeral=True
                    )
                    return
                
                # Save persistent channel selection
                if music_state_storage:
                    await music_state_storage.set_persistent_channel(self.guild.id, self.channel.id)
                
                # Update the original message
                success_embed = discord.Embed(
                    title="## Welcome to WOS Music",
                    description=(
                        "-# The best way to listen to music on Discord, let's get started\n\n"
                        f"### ✅ Connected to {self.channel.name}\n"
                        f"You're all set! The bot is now in **{self.channel.name}**.\n\n"
                        "-# You can now use `/play` from any text channel to start playing music!"
                    ),
                    color=0x00FF00
                )
                
                # Disable all buttons
                view = self.view
                for item in view.children:
                    item.disabled = True
                
                await interaction.message.edit(embed=success_embed, view=view)
                
            except discord.ClientException:
                # Bot is already connected to a voice channel
                await interaction.followup.send(
                    "❌ Bot is already connected to a voice channel. Please disconnect first.",
                    ephemeral=True
                )
            except Exception as e:
                await interaction.followup.send(
                    f"❌ Failed to connect: {e}",
                    ephemeral=True
                )
                
        except Exception as e:
            try:
                await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)
            except:
                pass


class VoiceChannelSelectView(discord.ui.View):
    """View for selecting a voice channel to connect to"""
    
    def __init__(self, guild: discord.Guild, music_cog):
        super().__init__(timeout=60)
        self.guild = guild
        self.music_cog = music_cog
        
        # Get all voice channels in the guild
        voice_channels = [ch for ch in guild.channels if isinstance(ch, discord.VoiceChannel)]
        
        # Limit to first 20 channels (Discord allows max 25 buttons, but we'll be safe)
        voice_channels = voice_channels[:20]
        
        # Add buttons for each channel (max 5 per row)
        for idx, channel in enumerate(voice_channels):
            row = idx // 5  # 5 buttons per row
            self.add_item(VoiceChannelButton(channel, guild, music_cog, row))


# VoiceChannelSelect class removed - replaced with VoiceChannelButton in VoiceChannelSelectView


class SeekModal(discord.ui.Modal, title="Seek to Position"):
    """Modal for seeking to a specific position"""
    
    def __init__(self, player: CustomPlayer):
        super().__init__()
        self.player = player
        
    position = discord.ui.TextInput(
        label="Position (MM:SS or seconds)",
        placeholder="e.g., 1:30 or 90",
        required=True,
        max_length=10
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        """Handle seek submission"""
        try:
            position_str = self.position.value.strip()
            
            # Parse position
            if ":" in position_str:
                # Format: MM:SS
                parts = position_str.split(":")
                if len(parts) == 2:
                    minutes = int(parts[0])
                    seconds = int(parts[1])
                    total_seconds = minutes * 60 + seconds
                else:
                    await interaction.response.send_message("❌ Invalid format! Use MM:SS or seconds.", ephemeral=True)
                    return
            else:
                # Format: seconds
                total_seconds = int(position_str)
            
            if total_seconds < 0:
                await interaction.response.send_message("❌ Position must be positive!", ephemeral=True)
                return
            
            # Seek to position
            await self.player.seek(total_seconds * 1000)  # Convert to milliseconds
            
            # Format for display
            minutes = total_seconds // 60
            seconds = total_seconds % 60
            await interaction.response.send_message(
                f"⏩ Seeked to **{minutes}:{seconds:02d}**",
                ephemeral=True
            )
        except ValueError:
            await interaction.response.send_message("❌ Invalid position format!", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)


class SearchModal(discord.ui.Modal, title="Search for Music"):
    """Modal for searching music"""
    
    def __init__(self, player: CustomPlayer, bot):
        super().__init__()
        self.player = player
        self.bot = bot
        
    query = discord.ui.TextInput(
        label="Search Query",
        placeholder="e.g., happy nation, ace of base, etc.",
        required=True,
        max_length=100
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        """Handle search submission"""
        try:
            await interaction.response.defer(ephemeral=True)
            
            query_str = self.query.value.strip()
            if not query_str:
                await interaction.followup.send("❌ Please enter a search query!", ephemeral=True)
                return
            
            # Search for tracks
            try:
                results = await wavelink.Playable.search(query_str)
            except Exception as e:
                await interaction.followup.send(f"❌ Search failed: {e}", ephemeral=True)
                return
            
            if not results:
                await interaction.followup.send(f"❌ No results found for: **{query_str}**", ephemeral=True)
                return
            
            # Convert results to list
            if isinstance(results, wavelink.Playlist):
                tracks_list = list(results.tracks)
            elif isinstance(results, list):
                tracks_list = results
            else:
                tracks_list = [results]
            
            if not tracks_list:
                await interaction.followup.send(f"❌ No results found for: **{query_str}**", ephemeral=True)
                return
            
            # Create and send search results view
            view = SearchResultsView(self.player, tracks_list, query_str, self.bot, interaction.user)
            embed = view.get_embed()
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            try:
                await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)
            except:
                pass


class SearchResultsView(discord.ui.View):
    """Interactive search results with filtering and pagination"""
    
    def __init__(self, player: CustomPlayer, results: list, query: str, bot, requester: discord.Member):
        super().__init__(timeout=180)
        self.player = player
        self.all_results = results
        self.query = query
        self.bot = bot
        self.requester = requester
        self.current_page = 0
        self.filter_type = "all"  # all, tracks, playlists, albums
        self.selected_index = None  # Currently selected track index
        self.results_per_page = 5
        
        self.update_buttons()
    
    def get_filtered_results(self):
        """Get results filtered by current filter type"""
        if self.filter_type == "all":
            return self.all_results
        
        filtered = []
        for track in self.all_results:
            # Determine track type based on source and metadata
            track_type = self._get_track_type(track)
            
            if self.filter_type == "tracks" and track_type == "track":
                filtered.append(track)
            elif self.filter_type == "playlists" and track_type == "playlist":
                filtered.append(track)
            elif self.filter_type == "albums" and track_type == "album":
                filtered.append(track)
        
        return filtered if filtered else self.all_results  # Fallback to all if no matches
    
    def _get_track_type(self, track):
        """Determine the type of a track (track, playlist, album)"""
        # Check if it's from a playlist source
        if hasattr(track, 'playlist_info') and track.playlist_info:
            return "playlist"
        
        # Check source and title for indicators
        source = getattr(track, 'source', '').lower()
        title = track.title.lower()
        author = track.author.lower()
        
        # Album indicators
        if 'album' in title or 'full album' in title:
            return "album"
        
        # Playlist indicators (usually longer duration or specific keywords)
        if 'playlist' in title or 'mix' in title or 'compilation' in title:
            return "playlist"
        
        # Check if it's a topic/auto-generated channel (usually albums)
        if 'topic' in author or 'vevo' in author:
            if track.length and track.length > 1800000:  # > 30 minutes
                return "album"
        
        # Default to track
        return "track"
    
    def get_current_page_results(self):
        """Get results for current page"""
        filtered = self.get_filtered_results()
        start_idx = self.current_page * self.results_per_page
        end_idx = start_idx + self.results_per_page
        return filtered[start_idx:end_idx]
    
    def get_total_pages(self):
        """Get total number of pages"""
        filtered = self.get_filtered_results()
        return max(1, (len(filtered) + self.results_per_page - 1) // self.results_per_page)
    
    def get_embed(self):
        """Create search results embed"""
        embed = discord.Embed(
            title=f"🔍 Search Results: {self.query}",
            color=0x9D50FF
        )
        
        page_results = self.get_current_page_results()
        filtered = self.get_filtered_results()
        
        if not page_results:
            embed.description = "No results found for this filter."
            return embed
        
        # Build results list
        results_text = []
        start_idx = self.current_page * self.results_per_page
        
        for i, track in enumerate(page_results, start=1):
            global_idx = start_idx + i
            duration = str(timedelta(milliseconds=track.length)).split('.')[0] if track.length else "Live"
            if duration.startswith('0:'):
                duration = duration[2:]
            
            # Get track type and emoji
            track_type = self._get_track_type(track)
            type_emoji = {"track": "🎵", "playlist": "📋", "album": "💿"}.get(track_type, "🎵")
            
            results_text.append(
                f"**{i}.** {type_emoji} [{track.title}]({track.uri if hasattr(track, 'uri') else 'https://discord.com'})\n"
                f"    by {track.author} • `{duration}`"
            )
        
        embed.description = "\n\n".join(results_text)
        
        # Footer with page info
        total_pages = self.get_total_pages()
        embed.set_footer(text=f"Page {self.current_page + 1}/{total_pages} • {len(filtered)} results • Filter: {self.filter_type.title()}")
        
        return embed
    
    def update_buttons(self):
        """Update button states based on current state"""
        self.clear_items()
        
        # Row 1: Filter buttons
        self.add_item(FilterButton("🎵 All", "all", self))
        self.add_item(FilterButton("🎤 Tracks", "tracks", self))
        self.add_item(FilterButton("📋 Playlists", "playlists", self))
        self.add_item(FilterButton("💿 Albums", "albums", self))
        
        # Row 2-3: Numbered selection buttons (1-5)
        page_results = self.get_current_page_results()
        for i in range(1, 6):
            disabled = i > len(page_results)
            self.add_item(NumberButton(i, self, disabled=disabled))
        
        # Row 4: Navigation buttons
        total_pages = self.get_total_pages()
        prev_disabled = self.current_page == 0
        next_disabled = self.current_page >= total_pages - 1
        
        self.add_item(NavigationButton("⬅️", "prev", self, disabled=prev_disabled))
        self.add_item(NavigationButton("➡️", "next", self, disabled=next_disabled))


class FilterButton(discord.ui.Button):
    """Button for filtering search results"""
    
    def __init__(self, label: str, filter_type: str, parent_view: SearchResultsView):
        style = discord.ButtonStyle.primary if parent_view.filter_type == filter_type else discord.ButtonStyle.secondary
        super().__init__(label=label, style=style, row=0)
        self.filter_type = filter_type
        self.parent_view = parent_view
    
    async def callback(self, interaction: discord.Interaction):
        self.parent_view.filter_type = self.filter_type
        self.parent_view.current_page = 0  # Reset to first page
        self.parent_view.update_buttons()
        
        embed = self.parent_view.get_embed()
        await interaction.response.edit_message(embed=embed, view=self.parent_view)


class NumberButton(discord.ui.Button):
    """Numbered button for selecting a track"""
    
    def __init__(self, number: int, parent_view: SearchResultsView, disabled: bool = False):
        emoji_map = {1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "4️⃣", 5: "5️⃣"}
        super().__init__(emoji=emoji_map[number], style=discord.ButtonStyle.secondary, row=1 if number <= 3 else 2, disabled=disabled)
        self.number = number
        self.parent_view = parent_view
    
    async def callback(self, interaction: discord.Interaction):
        page_results = self.parent_view.get_current_page_results()
        if self.number > len(page_results):
            await interaction.response.send_message("❌ Invalid selection!", ephemeral=True)
            return
        
        track = page_results[self.number - 1]
        
        # Create action view for this track
        action_view = TrackActionView(self.parent_view.player, track, self.parent_view, self.parent_view.bot, self.parent_view.requester)
        action_embed = discord.Embed(
            title=f"Selected Track #{self.number}",
            description=f"**{track.title}**\nby {track.author}",
            color=0x9D50FF
        )
        action_embed.set_footer(text="Choose an action:")
        
        await interaction.response.edit_message(embed=action_embed, view=action_view)


class NavigationButton(discord.ui.Button):
    """Button for navigating pages"""
    
    def __init__(self, emoji: str, direction: str, parent_view: SearchResultsView, disabled: bool = False):
        super().__init__(emoji=emoji, style=discord.ButtonStyle.secondary, row=3, disabled=disabled)
        self.direction = direction
        self.parent_view = parent_view
    
    async def callback(self, interaction: discord.Interaction):
        if self.direction == "prev":
            self.parent_view.current_page = max(0, self.parent_view.current_page - 1)
        else:  # next
            self.parent_view.current_page = min(self.parent_view.get_total_pages() - 1, self.parent_view.current_page + 1)
        
        self.parent_view.update_buttons()
        embed = self.parent_view.get_embed()
        await interaction.response.edit_message(embed=embed, view=self.parent_view)


class TrackActionView(discord.ui.View):
    """View for play/queue actions on a selected track"""
    
    def __init__(self, player: CustomPlayer, track: wavelink.Playable, search_view: SearchResultsView, bot, requester: discord.Member):
        super().__init__(timeout=180)
        self.player = player
        self.track = track
        self.search_view = search_view
        self.bot = bot
        self.requester = requester
    
    @discord.ui.button(label="Play Now", emoji="▶️", style=discord.ButtonStyle.success, row=0)
    async def play_now(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Play the selected track immediately"""
        try:
            # Store requester info
            self.track.extras.requester_id = self.requester.id
            self.track.extras.requester_name = str(self.requester)
            
            # Get music cog
            music_cog = self.bot.get_cog('Music')
            if not music_cog:
                await interaction.response.send_message("❌ Music system not available!", ephemeral=True)
                return
            
            # CRITICAL: Stop old progress updates first to prevent rate limiting
            await self.player.stop_progress_updates()
            
            # Delete old now playing message if it exists
            if self.player.now_playing_message:
                try:
                    await self.player.now_playing_message.delete()
                except:
                    pass  # Ignore if already deleted
                self.player.now_playing_message = None
            
            # Clear queue and reset loop mode to prevent collision with previous track
            self.player.queue.clear()
            self.player.loop_mode = "off"
            
            # Play the track
            success = await music_cog.safe_play(self.player, self.track)
            
            if success:
                # Just defer to acknowledge - don't delete or send message
                # This keeps the search embed visible
                try:
                    await interaction.response.defer()
                except:
                    pass  # Already responded
                
                # Create NEW now playing message in the text channel
                if self.player.text_channel:
                    embed = music_cog.create_now_playing_embed(self.player)
                    view = PlayerControlView(self.player)
                    self.player.now_playing_message = await self.player.text_channel.send(embed=embed, view=view)
                    
                    # Start fresh progress updates
                    await self.player.start_progress_updates(music_cog)
            else:
                await interaction.response.send_message("❌ Failed to play track!", ephemeral=True)
                
        except Exception as e:
            import traceback
            traceback.print_exc()
            try:
                await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)
            except:
                pass
    
    @discord.ui.button(label="Add to Queue", emoji="➕", style=discord.ButtonStyle.primary, row=0)
    async def add_to_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Add the selected track to queue"""
        try:
            # Store requester info
            self.track.extras.requester_id = self.requester.id
            self.track.extras.requester_name = str(self.requester)
            
            # Add to queue
            self.player.queue.put(self.track)
            queue_size = self.player.queue.count
            
            await interaction.response.send_message(
                f"➕ Added to queue at position **{queue_size}**: **{self.track.title}**",
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)
    
    @discord.ui.button(label="Back to Results", emoji="◀️", style=discord.ButtonStyle.secondary, row=0)
    async def back_to_results(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Go back to search results"""
        embed = self.search_view.get_embed()
        await interaction.response.edit_message(embed=embed, view=self.search_view)


class EffectsControlView(discord.ui.View):
    """Interactive effects control panel"""
    
    def __init__(self, player: CustomPlayer):
        super().__init__(timeout=180)
        self.player = player
        self.update_button_styles()
    
    def update_button_styles(self):
        """Update button styles based on active effects"""
        # Update nightcore button style
        for item in self.children:
            if hasattr(item, 'custom_id'):
                if item.custom_id == "effect_nightcore":
                    item.style = discord.ButtonStyle.success if self.player.nightcore_enabled else discord.ButtonStyle.secondary
                elif item.custom_id == "effect_slowed":
                    item.style = discord.ButtonStyle.success if self.player.slowed_reverb_enabled else discord.ButtonStyle.secondary
    
    def get_embed(self) -> discord.Embed:
        """Generate effects control embed"""
        embed = discord.Embed(
            title="🎛️ Audio Effects Control",
            description="Adjust audio effects in real-time",
            color=0x9B59B6
        )
        
        # Current effects status
        status_lines = []
        
        if self.player.bass_boost_level > 1.0:
            status_lines.append(f"🔊 **Bass Boost:** {self.player.bass_boost_level:.1f}x")
        
        if self.player.speed_multiplier != 1.0:
            status_lines.append(f"⚡ **Speed:** {self.player.speed_multiplier:.1f}x")
        
        if self.player.pitch_multiplier != 1.0:
            status_lines.append(f"🎵 **Pitch:** {self.player.pitch_multiplier:.1f}x")
        
        if self.player.nightcore_enabled:
            status_lines.append("✨ **Nightcore Mode:** Active")
        
        if self.player.slowed_reverb_enabled:
            status_lines.append("🌊 **Slowed & Reverb:** Active")
        
        if status_lines:
            embed.add_field(
                name="Active Effects",
                value="\n".join(status_lines),
                inline=False
            )
        else:
            embed.add_field(
                name="Active Effects",
                value="No effects applied",
                inline=False
            )
        
        embed.set_footer(text="Click buttons below to adjust effects")
        return embed
    
    @discord.ui.button(label="Bass Boost", emoji="🔊", style=discord.ButtonStyle.primary, custom_id="effect_bass")
    async def bass_boost_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Open bass boost modal"""
        modal = BassBoostModal(self.player, self)
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="Speed", emoji="⚡", style=discord.ButtonStyle.primary, custom_id="effect_speed")
    async def speed_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Open speed control modal"""
        modal = SpeedControlModal(self.player, self)
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="Pitch", emoji="🎵", style=discord.ButtonStyle.primary, custom_id="effect_pitch")
    async def pitch_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Open pitch shift modal"""
        modal = PitchShiftModal(self.player, self)
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="Nightcore", emoji="✨", style=discord.ButtonStyle.secondary, custom_id="effect_nightcore", row=1)
    async def nightcore_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Toggle nightcore mode"""
        try:
            self.player.nightcore_enabled = not self.player.nightcore_enabled
            
            if self.player.nightcore_enabled:
                # Disable slowed mode
                self.player.slowed_reverb_enabled = False
                # Reset individual controls
                self.player.speed_multiplier = 1.0
                self.player.pitch_multiplier = 1.0
            
            await self.player.apply_filters()
            self.update_button_styles()
            
            status = "enabled" if self.player.nightcore_enabled else "disabled"
            await interaction.response.edit_message(embed=self.get_embed(), view=self)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)
    
    @discord.ui.button(label="Slowed & Reverb", emoji="🌊", style=discord.ButtonStyle.secondary, custom_id="effect_slowed", row=1)
    async def slowed_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Toggle slowed & reverb mode"""
        try:
            self.player.slowed_reverb_enabled = not self.player.slowed_reverb_enabled
            
            if self.player.slowed_reverb_enabled:
                # Disable nightcore mode
                self.player.nightcore_enabled = False
                # Reset individual controls
                self.player.speed_multiplier = 1.0
                self.player.pitch_multiplier = 1.0
            
            await self.player.apply_filters()
            self.update_button_styles()
            
            status = "enabled" if self.player.slowed_reverb_enabled else "disabled"
            await interaction.response.edit_message(embed=self.get_embed(), view=self)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)
    
    @discord.ui.button(label="Reset All", emoji="🔄", style=discord.ButtonStyle.danger, custom_id="effect_reset", row=1)
    async def reset_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Reset all effects"""
        try:
            await self.player.reset_filters()
            self.update_button_styles()
            await interaction.response.edit_message(embed=self.get_embed(), view=self)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)


class BassBoostModal(discord.ui.Modal, title="Bass Boost Control"):
    """Modal for bass boost adjustment"""
    
    def __init__(self, player: CustomPlayer, effects_view: EffectsControlView):
        super().__init__()
        self.player = player
        self.effects_view = effects_view
    
    bass_level = discord.ui.TextInput(
        label="Bass Boost Level (1.0 - 5.0)",
        placeholder=f"Current: 1.0 | Enter value between 1.0 and 5.0",
        required=True,
        max_length=4
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        """Handle bass boost submission"""
        try:
            level = float(self.bass_level.value.strip())
            
            if level < 1.0 or level > 5.0:
                await interaction.response.send_message(
                    "❌ Bass boost level must be between 1.0 and 5.0!",
                    ephemeral=True
                )
                return
            
            self.player.bass_boost_level = level
            
            # Disable preset modes when using individual controls
            self.player.nightcore_enabled = False
            self.player.slowed_reverb_enabled = False
            
            await self.player.apply_filters()
            
            await interaction.response.send_message(
                f"🔊 Bass boost set to **{level:.1f}x**",
                ephemeral=True
            )
            
            # Update the effects view if it's still active
            try:
                self.effects_view.update_button_styles()
                await interaction.message.edit(embed=self.effects_view.get_embed(), view=self.effects_view)
            except:
                pass
                
        except ValueError:
            await interaction.response.send_message(
                "❌ Invalid value! Please enter a number between 1.0 and 5.0",
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)


class SpeedControlModal(discord.ui.Modal, title="Speed Control"):
    """Modal for speed adjustment"""
    
    def __init__(self, player: CustomPlayer, effects_view: EffectsControlView):
        super().__init__()
        self.player = player
        self.effects_view = effects_view
    
    speed_value = discord.ui.TextInput(
        label="Speed Multiplier (0.1 - 3.0)",
        placeholder=f"Current: 1.0 | Enter value between 0.1 and 3.0",
        required=True,
        max_length=4
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        """Handle speed control submission"""
        try:
            speed = float(self.speed_value.value.strip())
            
            if speed < 0.1 or speed > 3.0:
                await interaction.response.send_message(
                    "❌ Speed must be between 0.1 and 3.0!",
                    ephemeral=True
                )
                return
            
            self.player.speed_multiplier = speed
            
            # Disable preset modes when using individual controls
            self.player.nightcore_enabled = False
            self.player.slowed_reverb_enabled = False
            
            await self.player.apply_filters()
            
            await interaction.response.send_message(
                f"⚡ Speed set to **{speed:.1f}x**",
                ephemeral=True
            )
            
            # Update the effects view if it's still active
            try:
                self.effects_view.update_button_styles()
                await interaction.message.edit(embed=self.effects_view.get_embed(), view=self.effects_view)
            except:
                pass
                
        except ValueError:
            await interaction.response.send_message(
                "❌ Invalid value! Please enter a number between 0.1 and 3.0",
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)


class PitchShiftModal(discord.ui.Modal, title="Pitch Shift Control"):
    """Modal for pitch adjustment"""
    
    def __init__(self, player: CustomPlayer, effects_view: EffectsControlView):
        super().__init__()
        self.player = player
        self.effects_view = effects_view
    
    pitch_value = discord.ui.TextInput(
        label="Pitch Multiplier (0.5 - 2.0)",
        placeholder=f"Current: 1.0 | Enter value between 0.5 and 2.0",
        required=True,
        max_length=4
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        """Handle pitch shift submission"""
        try:
            pitch = float(self.pitch_value.value.strip())
            
            if pitch < 0.5 or pitch > 2.0:
                await interaction.response.send_message(
                    "❌ Pitch must be between 0.5 and 2.0!",
                    ephemeral=True
                )
                return
            
            self.player.pitch_multiplier = pitch
            
            # Disable preset modes when using individual controls
            self.player.nightcore_enabled = False
            self.player.slowed_reverb_enabled = False
            
            await self.player.apply_filters()
            
            await interaction.response.send_message(
                f"🎵 Pitch set to **{pitch:.1f}x**",
                ephemeral=True
            )
            
            # Update the effects view if it's still active
            try:
                self.effects_view.update_button_styles()
                await interaction.message.edit(embed=self.effects_view.get_embed(), view=self.effects_view)
            except:
                pass
                
        except ValueError:
            await interaction.response.send_message(
                "❌ Invalid value! Please enter a number between 0.5 and 2.0",
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)


class QueuePaginationView(discord.ui.View):
    """Queue display with pagination"""
    
    def __init__(self, player: CustomPlayer, page: int = 0):
        super().__init__(timeout=180)
        self.player = player
        self.page = page
        self.per_page = 10
        
    def get_embed(self) -> discord.Embed:
        """Generate queue embed for current page"""
        queue_count = self.player.queue.count
        total_pages = max(1, (queue_count + self.per_page - 1) // self.per_page)
        self.page = max(0, min(self.page, total_pages - 1))
        
        embed = discord.Embed(
            title="📋 Music Queue",
            color=0x00CED1,
            description=""
        )
        
        # Current track
        if self.player.current:
            current = self.player.current
            requester_name = getattr(current.extras, 'requester_name', 'Unknown')
            duration = str(timedelta(milliseconds=current.length)) if current.length else "Live"
            
            embed.add_field(
                name="🎵 Now Playing",
                value=f"**{current.title}** by {current.author}\n"
                      f"Duration: `{duration}` | Requested by: {requester_name}",
                inline=False
            )
        
        # Queue tracks
        if not self.player.queue.is_empty:
            start = self.page * self.per_page
            end = start + self.per_page
            # Convert queue to list for slicing
            queue_list = list(self.player.queue)
            queue_slice = queue_list[start:end]
            
            queue_text = ""
            for i, track in enumerate(queue_slice, start=start + 1):
                requester_name = getattr(track.extras, 'requester_name', 'Unknown')
                duration = str(timedelta(milliseconds=track.length)) if track.length else "Live"
                queue_text += f"`{i}.` **{track.title}** - {track.author} (`{duration}`) - {requester_name}\n"
            
            embed.add_field(
                name="📝 Up Next",
                value=queue_text or "Queue is empty",
                inline=False
            )
            
            # Footer with stats
            total_duration = sum(t.length for t in list(self.player.queue) if t.length)
            duration_str = str(timedelta(milliseconds=total_duration))
            embed.set_footer(
                text=f"Page {self.page + 1}/{total_pages} | {queue_count} tracks | Total: {duration_str} | Loop: {self.player.loop_mode.title()}"
            )
        else:
            embed.add_field(name="📝 Up Next", value="Queue is empty", inline=False)
            embed.set_footer(text=f"Loop: {self.player.loop_mode.title()}")
        
        return embed
        
    @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.secondary, custom_id="queue_prev")
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Go to previous page"""
        if self.page > 0:
            self.page -= 1
            await interaction.response.edit_message(embed=self.get_embed(), view=self)
        else:
            await interaction.response.send_message("❌ Already on first page", ephemeral=True)
            
    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.secondary, custom_id="queue_next")
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Go to next page"""
        total_pages = max(1, (self.player.queue.count + self.per_page - 1) // self.per_page)
        if self.page < total_pages - 1:
            self.page += 1
            await interaction.response.edit_message(embed=self.get_embed(), view=self)
        else:
            await interaction.response.send_message("❌ Already on last page", ephemeral=True)
            
    @discord.ui.button(emoji="🗑️", style=discord.ButtonStyle.danger, custom_id="queue_clear")
    async def clear_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Clear the queue"""
        self.player.queue.clear()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)


class GenreSelectionView(discord.ui.View):
    """View for selecting music genres"""
    
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="Pop", emoji="🎤", style=discord.ButtonStyle.primary, row=0)
    async def pop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Play pop music"""
        await interaction.response.send_message("🎤 Searching for pop music...", ephemeral=True)
        # Trigger /play command with pop playlist
        # This will be handled by the user manually for now
    
    @discord.ui.button(label="Rock", emoji="🎸", style=discord.ButtonStyle.primary, row=0)
    async def rock_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Play rock music"""
        await interaction.response.send_message("🎸 Searching for rock music...", ephemeral=True)
    
    @discord.ui.button(label="Hip Hop", emoji="🎧", style=discord.ButtonStyle.primary, row=0)
    async def hiphop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Play hip hop music"""
        await interaction.response.send_message("🎧 Searching for hip hop music...", ephemeral=True)
    
    @discord.ui.button(label="Jazz", emoji="🎷", style=discord.ButtonStyle.primary, row=0)
    async def jazz_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Play jazz music"""
        await interaction.response.send_message("🎷 Searching for jazz music...", ephemeral=True)
    
    @discord.ui.button(label="Electronic", emoji="🎹", style=discord.ButtonStyle.primary, row=1)
    async def electronic_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Play electronic music"""
        await interaction.response.send_message("🎹 Searching for electronic music...", ephemeral=True)
    
    @discord.ui.button(label="Classical", emoji="🎻", style=discord.ButtonStyle.primary, row=1)
    async def classical_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Play classical music"""
        await interaction.response.send_message("🎻 Searching for classical music...", ephemeral=True)
    
    @discord.ui.button(label="Country", emoji="🤠", style=discord.ButtonStyle.primary, row=1)
    async def country_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Play country music"""
        await interaction.response.send_message("🤠 Searching for country music...", ephemeral=True)
    
    @discord.ui.button(label="Lofi", emoji="☕", style=discord.ButtonStyle.primary, row=1)
    async def lofi_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Play lofi music"""
        await interaction.response.send_message("☕ Searching for lofi music...", ephemeral=True)


class Music(commands.Cog):
    """Music commands for playing audio in voice channels"""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = bot.logger if hasattr(bot, 'logger') else None
        # Track pending voice channel selections: {user_id: {guild_id, message, text_channel, query, timestamp}}
        self.pending_connections = {}
        # Start cleanup task
        self.cleanup_task = None
        
    async def cog_load(self):
        """Initialize Wavelink node when cog loads"""
        try:
            # Get Lavalink configuration from environment
            host = os.getenv('LAVALINK_HOST', 'localhost')
            port = int(os.getenv('LAVALINK_PORT', '2333'))
            password = os.getenv('LAVALINK_PASSWORD', 'youshallnotpass')
            secure = os.getenv('LAVALINK_SECURE', 'false').lower() == 'true'
            
            # Create node
            node = wavelink.Node(
                uri=f"{'https' if secure else 'http'}://{host}:{port}",
                password=password
            )
            
            # Connect to Lavalink
            await wavelink.Pool.connect(client=self.bot, nodes=[node])
            
            # Wait a bit for connection to establish
            await asyncio.sleep(2)
            
            # Check if connected
            if wavelink.Pool.nodes:
                connected_nodes = [n for n in wavelink.Pool.nodes.values() if n.status == wavelink.NodeStatus.CONNECTED]
                if connected_nodes:
                    if self.logger:
                        self.logger.info(f"🎵 Successfully connected to Lavalink at {host}:{port}")
                    else:
                        print(f"🎵 Successfully connected to Lavalink at {host}:{port}")
                    
                    # Wait for Lavalink to be fully ready before restoring states
                    print("⏳ Waiting for Lavalink to stabilize before restoring music states...")
                    await asyncio.sleep(5)
                    
                    # Restore music states after connection
                    await self.restore_music_states()
                    
                    # Start cleanup task for pending connections
                    self.cleanup_task = self.bot.loop.create_task(self.cleanup_stale_connections())
                else:
                    raise Exception("No nodes in CONNECTED state")
            else:
                raise Exception("No nodes available")
                
        except Exception as e:
            error_msg = f"❌ Failed to connect to Lavalink: {e}"
            if self.logger:
                self.logger.error(error_msg)
            else:
                print(error_msg)
            print("\n" + "="*70)
            print("⚠️  MUSIC FEATURES DISABLED - Lavalink connection failed")
            print("="*70)
            print("\nPossible solutions:")
            print("1. Try a different Lavalink server in your .env file:")
            print("   LAVALINK_HOST=lavalink.oops.wtf")
            print("   LAVALINK_PORT=443")
            print("   LAVALINK_PASSWORD=www.freelavalink.ga")
            print("   LAVALINK_SECURE=true")
            print("\n2. Or use this alternative:")
            print("   LAVALINK_HOST=lava-v3.ajieblogs.eu.org")
            print("   LAVALINK_PORT=80")
            print("   LAVALINK_PASSWORD=https://dsc.gg/ajidevserver")
            print("   LAVALINK_SECURE=false")
            print("\n3. Self-host Lavalink (see MUSIC_SETUP.md)")
            print("="*70 + "\n")
            
    async def cog_unload(self):
        """Cleanup when cog unloads"""
        try:
            # Cancel cleanup task
            if self.cleanup_task:
                self.cleanup_task.cancel()
            await wavelink.Pool.close()
        except:
            pass
    
    def check_lavalink_connected(self) -> bool:
        """Check if any Lavalink nodes are connected"""
        if not wavelink.Pool.nodes:
            return False
        return any(n.status == wavelink.NodeStatus.CONNECTED for n in wavelink.Pool.nodes.values())
    
    async def cleanup_stale_connections(self):
        """Background task to clean up stale pending connections"""
        import time
        while True:
            try:
                await asyncio.sleep(60)  # Run every minute
                
                current_time = time.time()
                stale_timeout = 300  # 5 minutes
                
                # Find stale connections
                stale_users = []
                for user_id, pending_data in self.pending_connections.items():
                    timestamp = pending_data.get('timestamp', 0)
                    if current_time - timestamp > stale_timeout:
                        stale_users.append(user_id)
                
                # Remove stale connections
                for user_id in stale_users:
                    print(f"🧹 Cleaning up stale pending connection for user {user_id}")
                    del self.pending_connections[user_id]
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"⚠️ Error in cleanup task: {e}")
    
    async def restore_music_states(self):
        """Restore music playback states from database"""
        if not music_state_storage:
            return
        
        try:
            states = await music_state_storage.get_all_states()
            
            if not states:
                print("No music states to restore")
                return
            
            print(f"🔄 Restoring {len(states)} music state(s)...")
            
            for state in states:
                try:
                    guild = self.bot.get_guild(state['guild_id'])
                    if not guild:
                        print(f"  ⚠️ Guild {state['guild_id']} not found, skipping...")
                        continue
                    
                    channel = guild.get_channel(state['channel_id'])
                    if not channel or not isinstance(channel, discord.VoiceChannel):
                        print(f"  ⚠️ Voice channel {state['channel_id']} not found in {guild.name}, skipping...")
                        continue
                    
                    # Check bot permissions
                    permissions = channel.permissions_for(guild.me)
                    if not permissions.connect or not permissions.speak:
                        print(f"  ⚠️ No permissions in {channel.name} ({guild.name}), skipping...")
                        continue
                    
                    # Connect to voice channel with retry logic
                    player = None
                    max_connect_retries = 3
                    connect_timeout = 60.0  # Increased from 30s to 60s for stability
                    
                    for attempt in range(max_connect_retries):
                        try:
                            print(f"  🔌 Connecting to {channel.name} in {guild.name} (attempt {attempt + 1}/{max_connect_retries})...")
                            player: CustomPlayer = await channel.connect(
                                cls=CustomPlayer, 
                                timeout=connect_timeout, 
                                self_deaf=True
                            )
                            print(f"  ✅ Connected successfully to {channel.name}")
                            break
                        except asyncio.TimeoutError:
                            print(f"  ⏱️ Connection timeout (attempt {attempt + 1}/{max_connect_retries})")
                            if attempt < max_connect_retries - 1:
                                wait_time = (attempt + 1) * 2  # Exponential backoff: 2s, 4s, 6s
                                print(f"  ⏳ Waiting {wait_time}s before retry...")
                                await asyncio.sleep(wait_time)
                            else:
                                print(f"  ❌ Failed to connect after {max_connect_retries} attempts, skipping...")
                                continue
                        except Exception as e:
                            print(f"  ❌ Connection error: {e}")
                            if attempt < max_connect_retries - 1:
                                await asyncio.sleep(2)
                            else:
                                continue
                    
                    if not player:
                        print(f"  ⚠️ Could not establish connection to {channel.name}, skipping state restoration...")
                        continue
                    
                    # Optimize voice quality
                    await self.optimize_voice_quality(channel)
                    
                    # Set text channel
                    if state.get('text_channel_id'):
                        text_channel = guild.get_channel(state['text_channel_id'])
                        if text_channel:
                            player.text_channel = text_channel
                    
                    # Restore settings
                    player.loop_mode = state.get('loop_mode', 'off')
                    player.current_playlist_name = state.get('playlist_name')
                    
                    # Set volume
                    volume = state.get('volume', 100)
                    await player.set_volume(volume)
                    
                    # Restore queue
                    queue_data = state.get('queue', [])
                    for track_data in queue_data:
                        try:
                            tracks = await wavelink.Playable.search(track_data['uri'])
                            if tracks:
                                track = tracks[0] if isinstance(tracks, list) else tracks
                                track.extras.requester_id = track_data.get('requester_id')
                                track.extras.requester_name = track_data.get('requester_name', 'Unknown')
                                player.queue.put(track)
                        except Exception as e:
                            print(f"    Failed to restore track {track_data.get('title', 'Unknown')}: {e}")
                    
                    # Restore current track and start playing
                    current_track_data = state.get('current_track')
                    if current_track_data:
                        try:
                            tracks = await wavelink.Playable.search(current_track_data['uri'])
                            if tracks:
                                track = tracks[0] if isinstance(tracks, list) else tracks
                                await player.play(track)
                                
                                # Try to seek to saved position (may not work perfectly)
                                position = current_track_data.get('position', 0)
                                if position > 0:
                                    await player.seek(position)
                                
                                # Handle auto-resume setting
                                if not MUSIC_AUTO_RESUME:
                                    # Pause the track - user can resume from Now Playing controls
                                    await player.pause(True)
                                    print(f"  ⏸️ Paused playback - use Now Playing controls to resume")
                                
                                # Start autosave even if paused
                                player.start_autosave()
                                
                                print(f"  ✅ Restored playback in {guild.name}: {track.title}")
                                
                                # Send notification
                                if player.text_channel:
                                    try:
                                        await player.text_channel.send(
                                            f"🔄 **Music Resumed**\nBot restarted - resuming playback from where we left off!\n"
                                            f"**Now Playing:** {track.title}\n"
                                            f"**Queue:** {player.queue.count} tracks\n"
                                            f"**Loop Mode:** {player.loop_mode.title()}"
                                        )
                                    except:
                                        pass
                        except Exception as e:
                            print(f"    Failed to restore current track: {e}")
                            # If current track fails, try to play from queue
                            if not player.queue.is_empty:
                                next_track = player.queue.get()
                                success = await self.safe_play(player, next_track)
                                if success:
                                    print(f"  ✅ Started queue playback in {guild.name}")
                                else:
                                    print(f"  ⚠️ Failed to start queue playback in {guild.name}")
                    elif not player.queue.is_empty:
                        # No current track but queue exists, start playing
                        next_track = player.queue.get()
                        success = await self.safe_play(player, next_track)
                        if success:
                            print(f"  ✅ Started queue playback in {guild.name}")
                        else:
                            print(f"  ⚠️ Failed to start queue playback in {guild.name}")
                    
                except Exception as e:
                    print(f"  ❌ Error restoring state for guild {state.get('guild_id')}: {e}")
                    import traceback
                    traceback.print_exc()
            
            print("✅ Music state restoration complete")
            
        except Exception as e:
            print(f"Error in restore_music_states: {e}")
            import traceback
            traceback.print_exc()
            
    @commands.Cog.listener()
    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload):
        """Handle track end event"""
        player: CustomPlayer = payload.player
        
        if not player:
            return
            
        # Store the track that just ended for looping purposes
        ended_track = payload.track
            
        # Add to history
        if ended_track:
            player.history.append(ended_track)
            if len(player.history) > 10:
                player.history.pop(0)
        
        # Handle looping BEFORE getting next track
        next_track = None
        
        if player.loop_mode == "track" and ended_track:
            # For track loop, replay the same track
            next_track = ended_track
        elif player.loop_mode == "queue" and ended_track:
            # For queue loop, add the ended track back to the queue
            if hasattr(ended_track.extras, 'requester_id'):
                ended_track.extras.requester_id = ended_track.extras.requester_id
            if hasattr(ended_track.extras, 'requester_name'):
                ended_track.extras.requester_name = ended_track.extras.requester_name
            player.queue.put(ended_track)
            
            # Then get the next track from queue
            if not player.queue.is_empty:
                next_track = player.queue.get()
        else:
            # No loop mode, just get next track from queue
            if not player.queue.is_empty:
                next_track = player.queue.get()
        
        if next_track:
            # Use safe_play to handle session errors
            success = await self.safe_play(player, next_track)
            if not success:
                print(f"⚠️ Failed to play next track, will retry on next track end")
                return
            
            # Save state after starting new track
            await player.save_state()
            
            # Start autosave task to periodically save state during playback
            player.start_autosave()
            
            # Update or send now playing message
            if player.text_channel:
                embed = self.create_now_playing_embed(player)
                view = PlayerControlView(player)
                try:
                    # Try to edit existing message if it exists
                    if player.now_playing_message:
                        try:
                            await player.now_playing_message.edit(embed=embed, view=view)
                        except (discord.NotFound, discord.HTTPException):
                            # Message was deleted or can't be edited, send a new one
                            player.now_playing_message = await player.text_channel.send(embed=embed, view=view)
                    else:
                        # No existing message, send a new one
                        player.now_playing_message = await player.text_channel.send(embed=embed, view=view)
                    
                    # Start real-time progress updates for the new track
                    await player.start_progress_updates(self)
                except Exception as e:
                    print(f"Error updating now playing message: {e}")
        else:
            # No next track - update voice channel status to show ready/idle
            await self.update_voice_channel_status(player, None)
        # Bot will stay in voice channel - no auto-disconnect
        # Music will continue if loop mode is enabled
        # Only disconnect on explicit /stop command
                    
    @commands.Cog.listener()
    async def on_wavelink_track_start(self, payload: wavelink.TrackStartEventPayload):
        """Handle track start event"""
        player: CustomPlayer = payload.player
        track = payload.track
        
        if not player:
            return
        
        # Update voice channel status with currently playing track
        await self.update_voice_channel_status(player, track)
    
    @commands.Cog.listener()
    async def on_wavelink_node_ready(self, payload: wavelink.NodeReadyEventPayload):
        """Handle node ready event - triggered when node connects/reconnects"""
        node = payload.node
        print(f"🎵 Lavalink node ready: {node.identifier} (Session: {node.session_id})")
        if self.logger:
            self.logger.info(f"Lavalink node ready: {node.identifier}")
    
    @commands.Cog.listener()
    async def on_wavelink_inactive_player(self, player: wavelink.Player):
        """Handle inactive player - disabled to allow 24/7 music playback"""
        # Don't disconnect on inactivity - allow persistent music playback
        # Bot will stay connected even when inactive
        pass
    
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        """Handle voice state updates - auto-connect bot when user joins a channel"""
        # Check if this user has a pending connection
        if member.id not in self.pending_connections:
            return
        
        # Check if user joined a voice channel (wasn't in one before, now is)
        if before.channel is None and after.channel is not None:
            pending = self.pending_connections[member.id]
            
            # Verify it's the same guild
            if pending['guild_id'] != member.guild.id:
                return
            
            # Check if bot is already connected to a voice channel in this guild
            if member.guild.voice_client is not None:
                print(f"ℹ️ Bot already connected to voice in {member.guild.name}, skipping auto-connect")
                # Clear pending connection since bot is already connected
                if member.id in self.pending_connections:
                    del self.pending_connections[member.id]
                return
            
            try:
                # Connect bot to the voice channel with retry logic
                voice_channel = after.channel
                
                # First, check permissions before attempting to connect
                permissions = voice_channel.permissions_for(member.guild.me)
                if not permissions.connect or not permissions.speak:
                    print(f"❌ Missing permissions to connect to {voice_channel.name}. Required: Connect & Speak")
                    # Clear pending connection
                    if member.id in self.pending_connections:
                        del self.pending_connections[member.id]
                    return
                
                player = None
                max_connect_retries = 3  # More retries with shorter timeout
                connect_timeout = 60.0  # Increased from 15s to 60s for stability on cloud hosting
                
                # Diagnostic logging
                print(f"📊 Voice Connection Diagnostics:")
                print(f"   • Channel: {voice_channel.name} (ID: {voice_channel.id})")
                print(f"   • Guild: {member.guild.name} (ID: {member.guild.id})")
                print(f"   • User count in channel: {len(voice_channel.members)}")
                print(f"   • Bot permissions: Connect={permissions.connect}, Speak={permissions.speak}")
                print(f"   • Timeout: {connect_timeout}s, Max retries: {max_connect_retries}")
                
                for attempt in range(max_connect_retries):
                    try:
                        print(f"🔄 Attempting to connect to {voice_channel.name} (attempt {attempt + 1}/{max_connect_retries})...")
                        
                        # Add connection start time for timeout tracking
                        import time
                        start_time = time.time()
                        
                        player = await voice_channel.connect(
                            cls=CustomPlayer, 
                            timeout=connect_timeout, 
                            self_deaf=True,
                            reconnect=True  # Enable automatic reconnection
                        )
                        
                        elapsed = time.time() - start_time
                        print(f"✅ Successfully connected to {voice_channel.name} in {elapsed:.2f}s")
                        break
                    except asyncio.TimeoutError as timeout_err:
                        elapsed = time.time() - start_time
                        if attempt < max_connect_retries - 1:
                            # Exponential backoff: 2s, 4s, 8s
                            wait_time = 2 ** (attempt + 1)
                            print(f"⏱️ Auto-connect timeout to {voice_channel.name} after {connect_timeout}s, retrying in {wait_time}s... (attempt {attempt + 1}/{max_connect_retries})")
                            await asyncio.sleep(wait_time)
                        else:
                            print(f"❌ Failed to auto-connect to {voice_channel.name}: Timeout exceeded after {max_connect_retries} attempts")
                            # Clear pending to prevent retry loops
                            if member.id in self.pending_connections:
                                del self.pending_connections[member.id]
                            raise
                    except discord.ClientException as client_err:
                        # Already connected or similar client issue
                        print(f"⚠️ Auto-connect client error to {voice_channel.name}: {client_err}")
                        if member.id in self.pending_connections:
                            del self.pending_connections[member.id]
                        raise
                    except Exception as e:
                        if attempt < max_connect_retries - 1:
                            print(f"⚠️ Auto-connect error to {voice_channel.name}: {e}, retrying...")
                            await asyncio.sleep(3)
                        else:
                            print(f"❌ Failed to auto-connect to {voice_channel.name} after {max_connect_retries} attempts: {e}")
                            # Clear pending to prevent retry loops
                            if member.id in self.pending_connections:
                                del self.pending_connections[member.id]
                            raise
                
                if not player:
                    print(f"❌ Failed to auto-connect after {max_connect_retries} attempts")
                    if member.id in self.pending_connections:
                        # Notify user about connection failure
                        try:
                            original_message = pending.get('message')
                            if original_message:
                                error_embed = discord.Embed(
                                    title="❌ Voice Connection Failed",
                                    description=(
                                        f"The bot couldn't connect to **{voice_channel.name}** after {max_connect_retries} attempts.\n\n"
                                        "**Possible causes:**\n"
                                        "• Discord voice server issues\n"
                                        "• Network connectivity problems\n"
                                        "• Bot hosting platform restrictions\n\n"
                                        "**What to try:**\n"
                                        "1. Try using `/play` command manually\n"
                                        "2. Wait a minute and join the voice channel again\n"
                                        "3. Try a different voice channel\n"
                                        "4. Check bot logs for detailed error messages"
                                    ),
                                    color=0xFF0000
                                )
                                await original_message.edit(embed=error_embed)
                        except Exception as notify_error:
                            print(f"Could not notify user about connection failure: {notify_error}")
                        
                        del self.pending_connections[member.id]
                    return
                
                player.text_channel = pending.get('text_channel')
                
                # Optimize voice quality
                await self.optimize_voice_quality(voice_channel)
                
                # Wait for connection
                await asyncio.sleep(1.5)
                
                # Save persistent channel selection
                if music_state_storage:
                    try:
                        await music_state_storage.set_persistent_channel(member.guild.id, voice_channel.id)
                    except Exception as e:
                        print(f"Warning: Could not save persistent channel: {e}")
                
                # Play the requested song if query was provided
                query = pending.get('query')
                if query and player:
                    try:
                        # Search for the track
                        tracks = await wavelink.Playable.search(query)
                        
                        if tracks:
                            # Handle playlists
                            if isinstance(tracks, wavelink.Playlist):
                                for track in tracks.tracks:
                                    track.extras.requester_id = member.id
                                    track.extras.requester_name = str(member)
                                    player.queue.put(track)
                                
                                # Start playing if nothing is playing
                                if not player.playing and not player.queue.is_empty:
                                    next_track = player.queue.get()
                                    await self.safe_play(player, next_track)
                            else:
                                # Handle single track or list of tracks
                                track = None
                                if isinstance(tracks, list) and len(tracks) > 0:
                                    track = tracks[0]
                                elif hasattr(tracks, '__iter__') and not isinstance(tracks, str):
                                    try:
                                        track = next(iter(tracks))
                                    except StopIteration:
                                        pass
                                else:
                                    track = tracks
                                
                                if track:
                                    track.extras.requester_id = member.id
                                    track.extras.requester_name = str(member)
                                    
                                    # Start playing immediately
                                    success = await self.safe_play(player, track)
                                    
                                    if success:
                                        # Edit the welcome message to show now playing
                                        embed = self.create_now_playing_embed(player)
                                        view = PlayerControlView(player)
                                        
                                        original_message = pending.get('message')
                                        if original_message:
                                            try:
                                                # Edit the original message to show now playing
                                                await original_message.edit(embed=embed, view=view)
                                                player.now_playing_message = original_message
                                                await player.start_progress_updates(self)
                                            except discord.NotFound:
                                                # Message was deleted, send a new one
                                                text_channel = pending.get('text_channel')
                                                if text_channel:
                                                    try:
                                                        msg = await text_channel.send(embed=embed, view=view)
                                                        player.now_playing_message = msg
                                                        await player.start_progress_updates(self)
                                                    except:
                                                        pass
                                            except Exception as e:
                                                print(f"Error editing welcome message: {e}")
                                                # Fallback to sending a new message
                                                text_channel = pending.get('text_channel')
                                                if text_channel:
                                                    try:
                                                        msg = await text_channel.send(embed=embed, view=view)
                                                        player.now_playing_message = msg
                                                        await player.start_progress_updates(self)
                                                    except:
                                                        pass
                    except Exception as e:
                        print(f"Failed to play requested song: {e}")
                        import traceback
                        traceback.print_exc()
                
                # Remove from pending connections
                del self.pending_connections[member.id]
                
            except discord.ClientException:
                # Bot already connected
                pass
            except Exception as e:
                print(f"Failed to auto-connect: {e}")
                # Remove from pending even if failed
                if member.id in self.pending_connections:
                    del self.pending_connections[member.id]

    
    async def validate_player(self, player: CustomPlayer) -> bool:
        """Check if player is still valid and connected to Lavalink"""
        if not player:
            return False
        
        # Check if player has a valid node
        if not player.node or player.node.status != wavelink.NodeStatus.CONNECTED:
            return False
        
        # Check if player is connected to voice
        if not player.connected:
            return False
        
        return True
    
    async def optimize_voice_quality(self, channel: discord.VoiceChannel) -> None:
        """Optimize voice channel settings for best audio quality"""
        try:
            # Set bitrate based on server boost tier
            if channel.guild.premium_tier >= 2:  # Tier 2+ (boosted)
                bitrate = 384000  # 384kbps - maximum quality
            elif channel.guild.premium_tier == 1:  # Tier 1
                bitrate = 128000  # 128kbps
            else:  # No boost
                bitrate = 96000   # 96kbps - max for non-boosted
            
            # Only update if current bitrate is lower
            if channel.bitrate < bitrate:
                await channel.edit(bitrate=bitrate)
                print(f"🎵 Set voice channel bitrate to {bitrate//1000}kbps for better quality")
        except Exception as e:
            # Don't fail if we can't set bitrate (permissions issue)
            print(f"⚠️ Could not optimize voice quality: {e}")
    
    async def update_voice_channel_status(self, player: CustomPlayer, track: wavelink.Playable = None) -> None:
        """Update voice channel status to show currently playing music"""
        try:
            if not player or not player.channel:
                return
            
            # Get the track to display (use provided or current)
            display_track = track or player.current
            
            if not display_track:
                # No music playing - clear status or set idle message
                try:
                    await player.channel.edit(status="🎵 Ready to play music")
                except Exception as e:
                    print(f"⚠️ Could not update voice channel status (no track): {e}")
                return
            
            # Create status text with track info
            # Discord voice channel status has a 500 character limit
            track_title = display_track.title
            track_author = display_track.author
            
            # Truncate if too long (leave room for emojis and formatting)
            max_title_length = 60
            max_author_length = 30
            
            if len(track_title) > max_title_length:
                track_title = track_title[:max_title_length-3] + "..."
            if len(track_author) > max_author_length:
                track_author = track_author[:max_author_length-3] + "..."
            
            # Format the status text
            if player.paused:
                status_text = f"⏸️ Paused: {track_title} - {track_author}"
            else:
                status_text = f"🎵 Now Playing: {track_title} - {track_author}"
            
            # Update the voice channel status
            await player.channel.edit(status=status_text)
            print(f"✅ Updated voice channel status: {status_text[:50]}...")
            
        except discord.Forbidden:
            print(f"⚠️ Missing permissions to update voice channel status")
        except discord.HTTPException as e:
            print(f"⚠️ HTTP error updating voice channel status: {e}")
        except Exception as e:
            print(f"⚠️ Could not update voice channel status: {e}")
    
    async def safe_play(self, player: CustomPlayer, track: wavelink.Playable, interaction: discord.Interaction = None) -> bool:
        """Safely play a track with error handling and reconnection logic"""
        try:
            await player.play(track)
            
            # Wait a moment for playback to actually start
            await asyncio.sleep(0.5)
            
            # Verify playback started
            if not player.playing and not player.paused:
                print(f"⚠️ Warning: Track loaded but not playing. Current: {player.current}")
                # Try to force play again
                await player.pause(False)
            
            return True
        except Exception as e:
            error_str = str(e)
            
            # Check if it's a session error (404)
            if "404" in error_str or "Not Found" in error_str or "session" in error_str.lower():
                print(f"⚠️ Session error detected: {e}")
                print(f"🔄 Attempting to reconnect player...")
                
                try:
                    # Disconnect old player
                    await player.disconnect()
                    
                    # Wait a moment
                    await asyncio.sleep(1)
                    
                    # Reconnect to voice channel
                    if player.channel:
                        new_player: CustomPlayer = await player.channel.connect(cls=CustomPlayer, timeout=30.0, self_deaf=True)
                        
                        # Optimize voice quality
                        await self.optimize_voice_quality(player.channel)
                        
                        # Copy settings from old player
                        new_player.text_channel = player.text_channel
                        new_player.loop_mode = player.loop_mode
                        new_player.current_playlist_name = player.current_playlist_name
                        
                        # Copy queue
                        for queued_track in list(player.queue):
                            new_player.queue.put(queued_track)
                        
                        # Set volume
                        await new_player.set_volume(player.volume)
                        
                        # Try to play again with new player
                        await new_player.play(track)
                        
                        print(f"✅ Successfully reconnected and resumed playback")
                        
                        # Notify user if interaction is available
                        if interaction and new_player.text_channel:
                            try:
                                await new_player.text_channel.send(
                                    "🔄 **Reconnected to Lavalink**\n"
                                    "The music session was restored and playback has resumed."
                                )
                            except:
                                pass
                        
                        return True
                except Exception as reconnect_error:
                    print(f"❌ Failed to reconnect: {reconnect_error}")
                    if interaction:
                        try:
                            if not interaction.response.is_done():
                                await interaction.followup.send(
                                    f"❌ **Session Error**\n"
                                    f"The Lavalink session expired and reconnection failed.\n"
                                    f"Please try the command again.\n\n"
                                    f"Error: {reconnect_error}",
                                    ephemeral=True
                                )
                        except:
                            pass
                    return False
            else:
                # Other error, just log it
                print(f"❌ Error playing track: {e}")
                import traceback
                traceback.print_exc()
                return False
            
    def create_now_playing_embed(self, player: CustomPlayer) -> discord.Embed:
        """Create now playing embed with premium design"""
        track = player.current
        
        if not track:
            return discord.Embed(title="❌ Nothing Playing", color=0xFF0000)
            
        # Calculate progress with enhanced visual design
        position = player.position
        duration = track.length
        
        if duration and duration > 0:
            # Create a more visually appealing progress bar
            progress_percent = (position / duration) * 100
            progress = int((position / duration) * 15)  # 15 segments for cleaner look
            
            # Enhanced progress bar with better visual indicators
            filled = "━" * progress
            empty = "━" * (15 - progress)
            progress_bar = f"○{filled}●{empty}○"
            
            # Format time strings
            position_str = str(timedelta(milliseconds=position)).split('.')[0]
            duration_str = str(timedelta(milliseconds=duration)).split('.')[0]
            
            # Remove leading zeros from hours if less than 1 hour
            if position_str.startswith('0:'):
                position_str = position_str[2:]
            if duration_str.startswith('0:'):
                duration_str = duration_str[2:]
                
            time_display = f"`{position_str}` {progress_bar} `{duration_str}`"
        else:
            time_display = "🔴 **LIVE**"
            
        # Premium gradient color (vibrant cyan-purple gradient effect)
        embed = discord.Embed(color=0x9D50FF)
        
        # Set author with animated icon and server name
        guild_name = player.guild.name if player.guild else "Unknown Server"
        embed.set_author(
            name=guild_name,
            icon_url="https://cdn.discordapp.com/attachments/1435569370389807144/1448653894766035064/original-7824f335d663467e4c7a3313f3e20cd8.gif"
        )
        
        # Enhanced title section with better formatting
        title_section = f"# 🎵 Now Playing\n"
        title_section += f"### [{track.title}]({track.uri if hasattr(track, 'uri') else 'https://discord.com'})\n"
        title_section += f"**by** {track.author}"
        
        embed.description = title_section
        
        # Add image if available (use set_image for larger display instead of thumbnail)
        if hasattr(track, 'artwork') and track.artwork:
            embed.set_image(url=track.artwork)
        elif hasattr(track, 'thumbnail') and track.thumbnail:
            embed.set_image(url=track.thumbnail)
            
        # Progress bar as a prominent field
        embed.add_field(
            name="⏱️ **Duration**",
            value=time_display,
            inline=False
        )
        
        # Requester info with better formatting
        requester_name = getattr(track.extras, 'requester_name', 'Unknown')
        embed.add_field(
            name="👤 Initiated by",
            value=f"`{requester_name}`",
            inline=True
        )
        
        # Volume with visual indicator
        volume_bars = "█" * (player.volume // 10) + "░" * (10 - player.volume // 10)
        embed.add_field(
            name="🔊 Volume",
            value=f"`{player.volume}%` {volume_bars}",
            inline=True
        )
        
        # Queue info with better visual
        queue_length = player.queue.count
        queue_display = f"`{queue_length}` track{'s' if queue_length != 1 else ''}"
        if queue_length > 0:
            queue_display += f"\n*Next: {list(player.queue)[0].title[:30]}...*" if len(list(player.queue)[0].title) > 30 else f"\n*Next: {list(player.queue)[0].title}*"
        embed.add_field(
            name="📋 Queue",
            value=queue_display,
            inline=True
        )
        
        # Effects indicator (if any effects are active)
        effects_active = []
        if player.bass_boost_level > 1.0:
            effects_active.append(f"🔊 Bass `{player.bass_boost_level:.1f}x`")
        if player.speed_multiplier != 1.0:
            effects_active.append(f"⚡ Speed `{player.speed_multiplier:.1f}x`")
        if player.pitch_multiplier != 1.0:
            effects_active.append(f"🎵 Pitch `{player.pitch_multiplier:.1f}x`")
        if player.nightcore_enabled:
            effects_active.append("✨ Nightcore")
        if player.slowed_reverb_enabled:
            effects_active.append("🌊 Slowed & Reverb")
        
        if effects_active:
            embed.add_field(
                name="🎛️ Active Effects",
                value=" • ".join(effects_active),
                inline=False
            )
        
        # Loop status with better visual indicators
        loop_emoji = {"off": "➡️", "track": "🔂", "queue": "🔁"}
        loop_text = {"off": "Off", "track": "Track", "queue": "Queue"}
        loop_status = f"{loop_emoji[player.loop_mode]} Loop: {loop_text[player.loop_mode]}"
        
        # Footer with Magnus branding
        guild_name = player.guild.name if player.guild else "Unknown Server"
        footer_text = f"{loop_status} • Magnus || {guild_name}"
        embed.set_footer(
            text=footer_text,
            icon_url="https://cdn.discordapp.com/attachments/1435569370389807144/1449478677053046956/Untitled_video_-_Made_with_Clipchamp.gif?ex=693f0bb6&is=693dba36&hm=655d6a419700be08b1757609fafebbf44f2e54136143869f543eeb83aa6d7df8"
        )
        
        return embed
        
    async def get_player(self, interaction: discord.Interaction, query: str = None) -> Optional[CustomPlayer]:
        """Get or create player for guild"""
        if not interaction.guild:
            return None
            
        player: CustomPlayer = interaction.guild.voice_client
        
        if not player:
            # Determine which voice channel to connect to
            target_channel = None
            
            # First, check if user is in a voice channel
            if interaction.user.voice and interaction.user.voice.channel:
                target_channel = interaction.user.voice.channel
            else:
                # User is not in voice - check for persistent channel
                if music_state_storage:
                    persistent_channel_id = await music_state_storage.get_persistent_channel(interaction.guild.id)
                    if persistent_channel_id:
                        target_channel = interaction.guild.get_channel(persistent_channel_id)
                        
                        # Validate the persistent channel
                        if target_channel:
                            if not isinstance(target_channel, discord.VoiceChannel):
                                # Invalid channel type, clear it
                                await music_state_storage.clear_persistent_channel(interaction.guild.id)
                                target_channel = None
                            else:
                                # Check permissions
                                permissions = target_channel.permissions_for(interaction.guild.me)
                                if not permissions.connect or not permissions.speak:
                                    # No permissions, clear it
                                    await music_state_storage.clear_persistent_channel(interaction.guild.id)
                                    target_channel = None
                        else:
                            # Channel was deleted, clear it
                            await music_state_storage.clear_persistent_channel(interaction.guild.id)
                
                # If no valid persistent channel, show selection UI
                if not target_channel:
                    # Get voice channels for display
                    voice_channels = [ch for ch in interaction.guild.channels if isinstance(ch, discord.VoiceChannel)]
                    
                    # Build channel list for description with clickable channel mentions
                    channel_list = ""
                    if voice_channels:
                        for channel in voice_channels[:10]:  # Show first 10 channels
                            channel_list += f"• <#{channel.id}>\n"
                    else:
                        channel_list = "*No voice channels available*"
                    
                    embed = discord.Embed(
                        title="Welcome to WOS Music",
                        description=(
                            "-# The best way to listen to music on Discord, let's get started\n\n"
                            "### Join a Voice Channel\n"
                            "To get started, join a voice channel:\n"
                            f"{channel_list}"
                            "-# Once you join, this message will automatically update"
                        ),
                        color=0x9D50FF
                    )
                    
                    if not interaction.response.is_done():
                        await interaction.response.send_message(embed=embed, ephemeral=True)
                        message = await interaction.original_response()
                    else:
                        message = await interaction.followup.send(embed=embed, ephemeral=True)
                    
                    # Store pending connection info for voice state listener
                    import time
                    self.pending_connections[interaction.user.id] = {
                        'guild_id': interaction.guild.id,
                        'message': message,
                        'text_channel': interaction.channel,
                        'query': query,  # Store the search query for later playback
                        'timestamp': time.time()  # Track when this pending connection was created
                    }
                    
                    return None
            
            # Create new player with the target channel
            try:
                # Check permissions first
                permissions = target_channel.permissions_for(interaction.guild.me)
                if not permissions.connect or not permissions.speak:
                    raise discord.Forbidden(None, "Bot lacks Connect or Speak permissions in the voice channel")
                
                player = None
                max_connect_retries = 3  # More retries with shorter timeout
                connect_timeout = 60.0  # Increased from 15s to 60s for stability
                
                for attempt in range(max_connect_retries):
                    try:
                        print(f"🔄 Connecting to {target_channel.name} (attempt {attempt + 1}/{max_connect_retries})...")
                        
                        # Ensure any old voice client is disconnected before a new attempt
                        if interaction.guild.voice_client:
                            print(f"🧹 Force disconnecting existing voice client in {interaction.guild.name}")
                            try:
                                await interaction.guild.voice_client.disconnect(force=True)
                                await asyncio.sleep(1)
                            except Exception:
                                pass

                        player = await target_channel.connect(
                            cls=CustomPlayer, 
                            timeout=connect_timeout, 
                            self_deaf=True,
                            reconnect=True
                        )
                        print(f"✅ Connected to {target_channel.name}")
                        break
                    except asyncio.TimeoutError:
                        if attempt < max_connect_retries - 1:
                            wait_time = 2 ** (attempt + 1)
                            print(f"⏱️ Connection timeout to {target_channel.name} after {connect_timeout}s, retrying in {wait_time}s...")
                            await asyncio.sleep(wait_time)
                        else:
                            raise
                    except Exception as e:
                        print(f"❌ Error connecting to voice: {e}")
                        if attempt < max_connect_retries - 1:
                            await asyncio.sleep(2)
                        else:
                            raise asyncio.TimeoutError(f"Unable to connect to {target_channel.name} after {max_connect_retries} attempts (timeout: {connect_timeout}s each). This may be due to network issues or Discord voice server problems.")
                
                if not player:
                    raise Exception(f"Failed to create player for {target_channel.name}")
                
                player.text_channel = interaction.channel
                
                # Optimize voice quality
                await self.optimize_voice_quality(target_channel)
                
                # Wait for voice connection to be fully established
                await asyncio.sleep(1.5)
                
                # Verify connection
                max_retries = 5
                for i in range(max_retries):
                    if player.connected:
                        break
                    print(f"DEBUG: Waiting for connection... attempt {i+1}/{max_retries}")
                    await asyncio.sleep(0.5)
                
                print(f"DEBUG: Voice client created - Ready: {player.connected}")
                print(f"DEBUG: Voice client WS: {player.guild.voice_client}")
                
                if not player.connected:
                    print(f"⚠️ Warning: Player not fully connected after {max_retries} retries")
                
                # Save as persistent channel if user wasn't in voice
                if music_state_storage and (not interaction.user.voice or not interaction.user.voice.channel):
                    await music_state_storage.set_persistent_channel(interaction.guild.id, target_channel.id)
                    
            except Exception as e:
                print(f"DEBUG: Error connecting to voice: {e}")
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        f"❌ Failed to connect to voice channel: {e}",
                        ephemeral=True
                    )
                else:
                    await interaction.followup.send(
                        f"❌ Failed to connect to voice channel: {e}",
                        ephemeral=True
                    )
                return None
            
        return player
        
    @app_commands.command(name="play", description="Play a song or add it to the queue")
    @app_commands.describe(query="Song name, URL, or search query")
    async def play(self, interaction: discord.Interaction, query: str):
        """Play music from various sources"""
        await interaction.response.defer()
        
        # Check if Lavalink is connected
        if not self.check_lavalink_connected():
            await interaction.followup.send(
                "❌ **Music system is not available**\n\n"
                "The bot couldn't connect to the Lavalink server. "
                "Please contact the bot administrator to configure a working Lavalink server.\n\n"
                "See `MUSIC_SETUP.md` for setup instructions.",
                ephemeral=True
            )
            return
        
        try:
            player = await self.get_player(interaction, query)
            if not player:
                return
                
            # Search for tracks with error handling and SoundCloud fallback
            try:
                tracks = await wavelink.Playable.search(query)
            except Exception as e:
                # If the search failed and it is not a direct URL, attempt a SoundCloud fallback
                if not (query.startswith("http://") or query.startswith("https://")):
                    try:
                        tracks = await wavelink.Playable.search(f"scsearch:{query}")
                    except Exception:
                        # If fallback also fails, raise the original exception
                        raise e
                else:
                    raise e
            except wavelink.LavalinkLoadException as e:
                # Check if it's a Spotify-related error
                error_msg = str(e)
                if "spotify" in query.lower() or "spotify.com" in query.lower():
                    await interaction.followup.send(
                        "❌ **Spotify Playlist Error**\n\n"
                        "The Lavalink server couldn't load this Spotify playlist. This usually happens because:\n"
                        "• Spotify plugin is not configured in Lavalink\n"
                        "• Spotify API credentials are missing or invalid\n"
                        "• The playlist is private or region-restricted\n\n"
                        "**Workarounds:**\n"
                        "1. Try searching for the songs by name instead of using the Spotify link\n"
                        "2. Use a YouTube playlist instead\n"
                        "3. Ask the bot administrator to configure Spotify support in Lavalink\n\n"
                        f"Technical error: {error_msg}",
                        ephemeral=True
                    )
                else:
                    await interaction.followup.send(
                        f"❌ **Failed to Load Track**\n\n"
                        f"The music server encountered an error while loading this track.\n\n"
                        f"Error: {error_msg}",
                        ephemeral=True
                    )
                return
            except Exception as e:
                await interaction.followup.send(
                    f"❌ **Search Error**\n\n"
                    f"An unexpected error occurred while searching for tracks.\n\n"
                    f"Error: {e}",
                    ephemeral=True
                )
                return
            
            if not tracks:
                await interaction.followup.send("❌ No tracks found!")
                return
                
            # Handle playlists
            if isinstance(tracks, wavelink.Playlist):
                for track in tracks.tracks:
                    # Store user ID and name instead of Member object
                    track.extras.requester_id = interaction.user.id
                    track.extras.requester_name = str(interaction.user)
                    player.queue.put(track)
                    
                await interaction.followup.send(
                    f"📋 Added **{len(tracks.tracks)}** tracks from playlist: **{tracks.name}**"
                )
                
                # Start playing if nothing is playing
                if not player.playing and not player.queue.is_empty:
                    next_track = player.queue.get()
                    await self.safe_play(player, next_track, interaction)
                    
            else:
                # Handle list of tracks or single track
                track = None
                if isinstance(tracks, list) and len(tracks) > 0:
                    track = tracks[0]
                elif hasattr(tracks, '__iter__') and not isinstance(tracks, str):
                    # It's some iterable, get first item
                    try:
                        track = next(iter(tracks))
                    except StopIteration:
                        await interaction.followup.send("❌ No tracks found!")
                        return
                else:
                    # Assume it's a single track
                    track = tracks
                
                if not track:
                    await interaction.followup.send("❌ No tracks found!")
                    return
                
                # Store user ID and name instead of Member object
                track.extras.requester_id = interaction.user.id
                track.extras.requester_name = str(interaction.user)
                
                # Check if player is actually playing something
                if player.playing:
                    # Add to queue
                    player.queue.put(track)
                    queue_size = player.queue.count
                    await interaction.followup.send(
                        f"📝 Added to queue at position **{queue_size}**: **{track.title}** by {track.author}"
                    )
                else:
                    # Start playing immediately with error handling
                    print(f"DEBUG: About to play track: {track.title}")
                    print(f"DEBUG: Player connected: {player.connected}")
                    print(f"DEBUG: Player channel: {player.channel}")
                    print(f"DEBUG: Lavalink node: {player.node}")
                    print(f"DEBUG: Node status: {player.node.status if player.node else 'No node'}")
                    
                    # Use safe_play to handle session errors
                    success = await self.safe_play(player, track, interaction)
                    
                    if success:
                        print(f"DEBUG: After play - Playing: {player.playing}")
                        print(f"DEBUG: After play - Current: {player.current}")
                        
                        # Get the current player (might be new if reconnected)
                        current_player = interaction.guild.voice_client
                        if current_player:
                            embed = self.create_now_playing_embed(current_player)
                            view = PlayerControlView(current_player)
                            # Store the message for future edits
                            current_player.now_playing_message = await interaction.followup.send(embed=embed, view=view)
                            # Start real-time progress updates
                            await current_player.start_progress_updates(self)
                    else:
                        await interaction.followup.send(
                            "❌ Failed to start playback. The Lavalink session may have expired.\n"
                            "Please try again.",
                            ephemeral=True
                        )
                    
        except Exception as e:
            import traceback
            traceback.print_exc()
            await interaction.followup.send(f"❌ Error: {e}")
            
    @app_commands.command(name="pause", description="Pause the current track")
    async def pause(self, interaction: discord.Interaction):
        """Pause playback"""
        player: CustomPlayer = interaction.guild.voice_client
        
        if not player or not player.current:
            await interaction.response.send_message("❌ Nothing is playing!", ephemeral=True)
            return
            
        if player.paused:
            await interaction.response.send_message("❌ Already paused!", ephemeral=True)
            return
            
        await player.pause(True)
        await interaction.response.send_message("⏸️ Paused playback")
        
    @app_commands.command(name="resume", description="Resume playback")
    async def resume(self, interaction: discord.Interaction):
        """Resume playback"""
        player: CustomPlayer = interaction.guild.voice_client
        
        if not player or not player.current:
            await interaction.response.send_message("❌ Nothing is playing!", ephemeral=True)
            return
            
        if not player.paused:
            await interaction.response.send_message("❌ Not paused!", ephemeral=True)
            return
            
        await player.pause(False)
        await interaction.response.send_message("▶️ Resumed playback")
        
    @app_commands.command(name="skip", description="Skip the current track")
    @app_commands.describe(amount="Number of tracks to skip (default: 1)")
    async def skip(self, interaction: discord.Interaction, amount: int = 1):
        """Skip tracks"""
        try:
            player: CustomPlayer = interaction.guild.voice_client
            
            if not player or not player.current:
                await interaction.response.send_message("❌ Nothing is playing!", ephemeral=True)
                return
                
            if amount < 1:
                await interaction.response.send_message("❌ Amount must be at least 1!", ephemeral=True)
                return
                
            # Skip additional tracks from queue
            skipped = min(amount - 1, player.queue.count)
            for _ in range(skipped):
                if not player.queue.is_empty:
                    player.queue.get()
                    
            await player.stop()
            await interaction.response.send_message(f"⏭️ Skipped **{amount}** track(s)")
        except Exception as e:
            import traceback
            traceback.print_exc()
            await interaction.response.send_message(f"❌ An error occurred: {e}", ephemeral=True)
        
    @app_commands.command(name="stop", description="Stop playback and disconnect")
    async def stop(self, interaction: discord.Interaction):
        """Stop playback"""
        player: CustomPlayer = interaction.guild.voice_client
        
        if not player:
            await interaction.response.send_message("❌ Not connected to voice!", ephemeral=True)
            return
        
        # Clear voice channel status before disconnecting
        if player.channel:
            try:
                await player.channel.edit(status=None)
            except Exception as e:
                print(f"⚠️ Could not clear voice channel status: {e}")
        
        # Stop progress updates
        await player.stop_progress_updates()
        
        player.queue.clear()
        await player.stop()
        await player.disconnect()
        await interaction.response.send_message("⏹️ Stopped playback and disconnected")
        
    @app_commands.command(name="nowplaying", description="Show currently playing track")
    async def nowplaying(self, interaction: discord.Interaction):
        """Show now playing"""
        player: CustomPlayer = interaction.guild.voice_client
        
        if not player or not player.current:
            await interaction.response.send_message("❌ Nothing is playing!", ephemeral=True)
            return
            
        embed = self.create_now_playing_embed(player)
        view = PlayerControlView(player)
        await interaction.response.send_message(embed=embed, view=view)
        # Store the message for future edits
        player.now_playing_message = await interaction.original_response()
        
    @app_commands.command(name="queue", description="Show the music queue")
    @app_commands.describe(page="Page number to view")
    async def queue(self, interaction: discord.Interaction, page: int = 1):
        """Display queue"""
        player: CustomPlayer = interaction.guild.voice_client
        
        if not player:
            await interaction.response.send_message("❌ Not connected to voice!", ephemeral=True)
            return
            
        view = QueuePaginationView(player, page - 1)
        await interaction.response.send_message(embed=view.get_embed(), view=view)
        
    @app_commands.command(name="volume", description="Set playback volume")
    @app_commands.describe(volume="Volume level (0-100)")
    async def volume(self, interaction: discord.Interaction, volume: int):
        """Set volume"""
        player: CustomPlayer = interaction.guild.voice_client
        
        if not player:
            await interaction.response.send_message("❌ Not connected to voice!", ephemeral=True)
            return
            
        if not 0 <= volume <= 100:
            await interaction.response.send_message("❌ Volume must be between 0 and 100!", ephemeral=True)
            return
            
        await player.set_volume(volume)
        await interaction.response.send_message(f"🔊 Volume set to **{volume}%**")
        
    @app_commands.command(name="shuffle", description="Shuffle the queue")
    async def shuffle(self, interaction: discord.Interaction):
        """Shuffle queue"""
        player: CustomPlayer = interaction.guild.voice_client
        
        if not player or player.queue.is_empty:
            await interaction.response.send_message("❌ Queue is empty!", ephemeral=True)
            return
            
        import random
        tracks = list(player.queue)
        random.shuffle(tracks)
        player.queue.clear()
        for track in tracks:
            player.queue.put(track)
        await interaction.response.send_message(f"🔀 Shuffled **{len(tracks)}** tracks")
        
    @app_commands.command(name="loop", description="Set loop mode")
    @app_commands.describe(mode="Loop mode: off, track, or queue")
    @app_commands.choices(mode=[
        app_commands.Choice(name="Off", value="off"),
        app_commands.Choice(name="Track", value="track"),
        app_commands.Choice(name="Queue", value="queue")
    ])
    async def loop(self, interaction: discord.Interaction, mode: str):
        """Set loop mode"""
        player: CustomPlayer = interaction.guild.voice_client
        
        if not player:
            await interaction.response.send_message("❌ Not connected to voice!", ephemeral=True)
            return
            
        player.loop_mode = mode
        mode_emojis = {"off": "🔁", "track": "🔂", "queue": "🔁"}
        await interaction.response.send_message(f"{mode_emojis[mode]} Loop mode set to: **{mode.title()}**")
        
    @app_commands.command(name="clear", description="Clear the queue")
    async def clear(self, interaction: discord.Interaction):
        """Clear queue"""
        player: CustomPlayer = interaction.guild.voice_client
        
        if not player:
            await interaction.response.send_message("❌ Not connected to voice!", ephemeral=True)
            return
            
        count = player.queue.count
        player.queue.clear()
        await interaction.response.send_message(f"🗑️ Cleared **{count}** tracks from queue")
        
    @app_commands.command(name="remove", description="Remove a track from the queue")
    @app_commands.describe(position="Position in queue to remove")
    async def remove(self, interaction: discord.Interaction, position: int):
        """Remove track from queue"""
        player: CustomPlayer = interaction.guild.voice_client
        
        if not player or player.queue.is_empty:
            await interaction.response.send_message("❌ Queue is empty!", ephemeral=True)
            return
            
        queue_count = player.queue.count
        if position < 1 or position > queue_count:
            await interaction.response.send_message(
                f"❌ Position must be between 1 and {queue_count}!",
                ephemeral=True
            )
            return
            
        # Convert to list to remove by index
        queue_list = list(player.queue)
        removed = queue_list.pop(position - 1)
        # Rebuild queue
        player.queue.clear()
        for track in queue_list:
            player.queue.put(track)
        await interaction.response.send_message(f"🗑️ Removed: **{removed.title}** by {removed.author}")
        
    @app_commands.command(name="seek", description="Seek to a position in the current track")
    @app_commands.describe(position="Position in seconds")
    async def seek(self, interaction: discord.Interaction, position: int):
        """Seek to position"""
        player: CustomPlayer = interaction.guild.voice_client
        
        if not player or not player.current:
            await interaction.response.send_message("❌ Nothing is playing!", ephemeral=True)
            return
            
        if position < 0:
            await interaction.response.send_message("❌ Position must be positive!", ephemeral=True)
            return
            
        await player.seek(position * 1000)  # Convert to milliseconds
        await interaction.response.send_message(f"⏩ Seeked to **{timedelta(seconds=position)}**")
        
    @app_commands.command(name="previous", description="Play the previous track from history")
    async def previous(self, interaction: discord.Interaction):
        """Play previous track"""
        await interaction.response.defer()
        
        player: CustomPlayer = interaction.guild.voice_client
        
        if not player:
            await interaction.followup.send("❌ Not connected to voice!", ephemeral=True)
            return
        
        if not player.history:
            await interaction.followup.send("⏮️ No previous tracks in history", ephemeral=True)
            return
        
        # Get the last track from history
        previous_track = player.history.pop()
        success = await self.safe_play(player, previous_track, interaction)
        
        if success:
            # Send now playing message and store reference
            embed = self.create_now_playing_embed(player)
            view = PlayerControlView(player)
            player.now_playing_message = await interaction.followup.send(embed=embed, view=view)
            await interaction.followup.send(f"⏮️ Playing previous: **{previous_track.title}**")
        else:
            await interaction.followup.send(
                "❌ Failed to play previous track. Please try again.",
                ephemeral=True
            )
        
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
        
        # Check if the voice_client is actually a CustomPlayer (not a regular VoiceClient from voice chat)
        if player and not isinstance(player, CustomPlayer):
            # Regular VoiceClient exists (probably from voice chat), treat as no player
            player = None
        
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



async def setup(bot: commands.Bot):
    """Setup function for loading the cog"""
    await bot.add_cog(Music(bot))
