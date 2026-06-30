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
