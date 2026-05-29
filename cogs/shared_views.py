import discord
import sqlite3
import logging
import os
import json
from pathlib import Path
from datetime import datetime
import asyncio
import giftcode_poster
from command_animator import animator
from db.mongo_adapters import AlliancesAdapter
# Import from reminder_system (assuming it's in cogs package)
# We use local import inside methods if needed to avoid circular imports, 
# but since this is a shared views file, we might need to be careful.
# However, reminder_system.py probably doesn't import shared_views.py, so it should be fine.
from cogs.reminder_system import TimeParser, set_user_timezone, get_user_timezone
from bson import ObjectId

class MongoJSONEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, ObjectId):
            return str(o)
        return super().default(o)

def mongo_dump(obj, f, **kwargs):
    return json.dump(obj, f, cls=MongoJSONEncoder, **kwargs)

logger = logging.getLogger('shared_views')

# --- Feedback Utils (Moved from app.py) ---
FEEDBACK_STATE_PATH = Path(__file__).parent.parent / "feedback_state.json"
FEEDBACK_LOG_PATH = Path(__file__).parent.parent / "feedback_log.txt"

def load_feedback_state():
    if FEEDBACK_STATE_PATH.exists():
        try:
            with open(FEEDBACK_STATE_PATH, 'r') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_feedback_state(state: dict):
    try:
        with open(FEEDBACK_STATE_PATH, 'w') as f:
            mongo_dump(state, f)
    except Exception as e:
        logger.error(f"Failed to save feedback state: {e}")

def get_feedback_channel_id():
    # Check env var first
    env_id = os.getenv('FEEDBACK_CHANNEL_ID')
    if env_id: 
        return env_id
    
    # Fallback to state file
    state = load_feedback_state()
    return state.get('channel_id')

def append_feedback_log(user, user_id, feedback_text, posted_channel=False, posted_owner=False):
    try:
        timestamp = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
        status = []
        if posted_channel: status.append("Channel")
        if posted_owner: status.append("Owner")
        status_str = f"[{', '.join(status)}]" if status else "[Not Posted]"
        
        entry = f"[{timestamp}] {user} ({user_id}): {feedback_text} {status_str}\n"
        
        with open(FEEDBACK_LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(entry)
    except Exception as e:
        logger.error(f"Failed to append to feedback log: {e}")

# --- Views ---

class PersistentFeedbackModal(discord.ui.Modal, title="Your Feedback"):
    feedback = discord.ui.TextInput(label="Your feedback", style=discord.TextStyle.long,
                                    placeholder="Share your feedback or a bug report...",
                                    required=True, max_length=2000)

    async def on_submit(self, modal_interaction: discord.Interaction):
        try:
            feedback_text = self.feedback.value
            posted_channel = False
            posted_owner = False

            feedback_channel_id = get_feedback_channel_id()
            if feedback_channel_id:
                try:
                    ch = modal_interaction.client.get_channel(int(feedback_channel_id))
                    if ch:
                        await ch.send(f"**Feedback from** {modal_interaction.user} (ID: {modal_interaction.user.id}):\n{feedback_text}")
                        posted_channel = True
                except Exception as e:
                    logger.error(f"Failed to post feedback to channel: {e}")

            owner_id = os.getenv('BOT_OWNER_ID')
            if owner_id:
                try:
                    owner = modal_interaction.client.get_user(int(owner_id))
                    if owner is None:
                        try:
                            owner = await modal_interaction.client.fetch_user(int(owner_id))
                        except Exception as e:
                            logger.error(f"Failed to fetch owner user object: {e}")

                    if owner:
                        try:
                            await owner.send(f"**Feedback from** {modal_interaction.user} (ID: {modal_interaction.user.id}):\n{feedback_text}")
                            posted_owner = True
                        except Exception as e:
                            logger.error(f"Failed to DM owner with feedback: {e}")
                            if feedback_channel_id and not posted_channel:
                                try:
                                    ch = modal_interaction.client.get_channel(int(feedback_channel_id))
                                    if ch:
                                        await ch.send(f"⚠️ Could not DM configured owner (ID: {owner_id}). Feedback from {modal_interaction.user} (ID: {modal_interaction.user.id}):\n{feedback_text}")
                                        posted_channel = True
                                except Exception as e2:
                                    logger.error(f"Failed to post fallback notification to feedback channel: {e2}")
                except Exception as e:
                    logger.error(f"Unexpected error while trying to deliver feedback to owner: {e}")

            try:
                append_feedback_log(modal_interaction.user, modal_interaction.user.id, feedback_text, posted_channel=posted_channel, posted_owner=posted_owner)
            except Exception:
                logger.exception("Failed to append feedback to log file")

            try:
                await modal_interaction.response.send_message("Thanks — your feedback has been submitted.", ephemeral=True)
            except Exception:
                logger.debug("Could not send ephemeral confirmation for feedback")
        except Exception as e:
            logger.error(f"Error handling feedback modal submit: {e}")

class PersistentHelpView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Share Feedback", style=discord.ButtonStyle.primary, custom_id="share_feedback")
    async def share_feedback(self, button_interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await button_interaction.response.send_modal(PersistentFeedbackModal())
        except Exception as e:
            logger.error(f"Failed to open feedback modal: {e}")
            try:
                await button_interaction.response.send_message("Couldn't open feedback form right now.", ephemeral=True)
            except Exception:
                pass

class InteractiveHelpView(discord.ui.View):
    """Interactive help view with category dropdown and feedback button"""
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(CategorySelect())
    
    @discord.ui.button(label="Share Feedback", style=discord.ButtonStyle.primary, custom_id="help_share_feedback", row=1)
    async def share_feedback(self, button_interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await button_interaction.response.send_modal(PersistentFeedbackModal())
        except Exception as e:
            logger.error(f"Failed to open feedback modal: {e}")
            try:
                await button_interaction.response.send_message("Couldn't open feedback form right now.", ephemeral=True)
            except Exception:
                pass

class CategorySelect(discord.ui.Select):
    """Dropdown menu for selecting help command categories"""
    def __init__(self):
        options = [
            discord.SelectOption(
                label="🏠 Overview",
                description="Return to main command center",
                value="overview",
                emoji="🏠"
            ),
            discord.SelectOption(
                label="Fun & Games",
                description="Interactive entertainment and AI generation",
                value="fun_games",
                emoji="🎮"
            ),
            discord.SelectOption(
                label="Gift Codes & Rewards",
                description="Whiteout Survival gift code management",
                value="giftcodes",
                emoji="🎁"
            ),
            discord.SelectOption(
                label="Music Player",
                description="Full-featured music playback and playlist management",
                value="music",
                emoji="🎵"
            ),
            discord.SelectOption(
                label="Reminders & Time",
                description="Scheduled notifications and time management",
                value="reminders",
                emoji="⏰"
            ),
            discord.SelectOption(
                label="Community & Stats",
                description="Server analytics and member tracking",
                value="community",
                emoji="👥"
            ),
            discord.SelectOption(
                label="Alliance Management",
                description="Alliance monitoring and operations",
                value="alliance",
                emoji="🛡️"
            ),
            discord.SelectOption(
                label="Auto-Translate",
                description="Automatic message translation between channels",
                value="autotranslate",
                emoji="🌐"
            ),
            discord.SelectOption(
                label="Server Configuration",
                description="Server settings and customization",
                value="config",
                emoji="⚙️"
            ),
            discord.SelectOption(
                label="Utility & Tools",
                description="Additional utilities and features",
                value="utility",
                emoji="🔧"
            ),
        ]
        super().__init__(
            placeholder="📋 Select a category to view commands...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="help_category_select"
        )
    
    async def callback(self, interaction: discord.Interaction):
        category = self.values[0]
        
        if category == "overview":
            embed = discord.Embed(
                title="⚡ Whiteout Survival Bot",
                description=(
                    "Access all bot functions through categorized command modules.\n"
                    "Use the dropdown below to explore each category.\n\n"
                    "**📋 Available Modules**\n\n"
                    "🎮 **Fun & Games** — 3 commands\n"
                    "🎁 **Gift Codes & Rewards** — 3 commands\n"
                    "🎵 **Music Player** — 15 commands\n"
                    "⏰ **Reminders & Time** — 2 commands\n"
                    "👥 **Community & Stats** — 4 commands\n"
                    "🛡️ **Alliance Management** — 4 commands\n"
                    "🌐 **Auto-Translate** — 5 commands\n"
                    "⚙️ **Server Configuration** — 4 commands\n"
                    "🔧 **Utility & Tools** — 2 commands"
                ),
                color=0x00d9ff
            )
            embed.set_thumbnail(url="https://i.postimg.cc/Fzq03CJf/a463d7c7-7fc7-47fc-b24d-1324383ee2ff-removebg-preview.png")
            embed.set_footer(text="Select a category to view detailed commands")
        
        elif category == "fun_games":
            embed = discord.Embed(
                title="🎮 FUN & GAMES // MODULE",
                description=(
                    "```ansi\n"
                    "\u001b[1;36m▸ INTERACTIVE GAMES & ENTERTAINMENT\u001b[0m\n"
                    "```\n"
                ),
                color=0x00d9ff
            )
            embed.add_field(
                name="⚡ `/dice`",
                value=(
                    "Roll a six-sided dice and test your luck!\n"
                    "```yaml\n"
                    "Usage: /dice\n"
                    "Output: Random number 1-6 with animated result\n"
                    "Also: Text command !dice and keyword detection\n"
                    "```"
                ),
                inline=False
            )
            embed.add_field(
                name="⚡ `/dicebattle @user`",
                value=(
                    "Challenge another player to an epic dice battle!\n"
                    "```yaml\n"
                    "Usage: /dicebattle @opponent\n"
                    "Features: Interactive buttons, animated rolls, winner graphics\n"
                    "Special: Custom battle scenes with dynamic images\n"
                    "```"
                ),
                inline=False
            )
            embed.add_field(
                name="⚡ `/tictactoe @user` or `/ttt @user`",
                value=(
                    "Start an epic Tic-Tac-Toe battle with a friend!\n"
                    "```yaml\n"
                    "Usage: /tictactoe @opponent\n"
                    "Alias: /ttt @opponent (quick start)\n"
                    "Features: Interactive board, emoji moves, win celebrations\n"
                    "Stats: Track wins, losses, and draws\n"
                    "```"
                ),
                inline=False
            )
            embed.set_footer(text="ΓÜí Whiteout Survival Bot // Fun & Games Module")
        
        elif category == "giftcodes":
            embed = discord.Embed(
                title="🎁 GIFT CODES & REWARDS // MODULE",
                description=(
                    "```ansi\n"
                    "\u001b[1;36m▸ WHITEOUT SURVIVAL GIFT CODE MANAGEMENT\u001b[0m\n"
                    "```\n"
                ),
                color=0x00d9ff
            )
            embed.add_field(
                name="⚡ `/giftcode`",
                value=(
                    "Display all active Whiteout Survival gift codes\n"
                    "```yaml\n"
                    "Usage: /giftcode\n"
                    "Features: Active codes, expiration dates, redeem buttons\n"
                    "```"
                ),
                inline=False
            )
            embed.add_field(
                name="⚡ `/giftcodesettings` 🔒",
                value=(
                    "Open the server gift code settings dashboard\n"
                    "```yaml\n"
                    "Usage: /giftcodesettings\n"
                    "Permissions: Administrator\n"
                    "Features: Configure auto-send channel, manage settings\n"
                    "```"
                ),
                inline=False
            )
            embed.add_field(
                name="⚡ `/refresh`",
                value=(
                    "Refresh cached alliance and gift code data from Google Sheets\n"
                    "```yaml\n"
                    "Usage: /refresh\n"
                    "Updates: Alliance data, gift codes, player information\n"
                    "```"
                ),
                inline=False
            )
            embed.set_footer(text="ΓÜí Whiteout Survival Bot // Gift Codes & Rewards Module")
        
        elif category == "music":
            embed = discord.Embed(
                title="🎵 MUSIC PLAYER // MODULE",
                description=(
                    "```ansi\n"
                    "\u001b[1;36m▸ FULL-FEATURED MUSIC PLAYBACK & PLAYLIST MANAGEMENT\u001b[0m\n"
                    "```\n"
                ),
                color=0x00d9ff
            )
            embed.add_field(
                name="⚡ `/play <query>`",
                value=(
                    "Play music from YouTube, Spotify, SoundCloud, and more\n"
                    "```yaml\n"
                    "Usage: /play query:\"song name or URL\"\n"
                    "Example: /play query:\"never gonna give you up\"\n"
                    "Sources: YouTube, Spotify, SoundCloud, direct URLs\n"
                    "Features: Auto-queue, playlist support, live streams\n"
                    "```"
                ),
                inline=False
            )
            embed.add_field(
                name="⚡ `/pause` / `/resume`",
                value=(
                    "Pause or resume the current track\n"
                    "```yaml\n"
                    "Usage: /pause or /resume\n"
                    "Quick Control: Use player buttons for instant control\n"
                    "```"
                ),
                inline=False
            )
            embed.add_field(
                name="⚡ `/skip` / `/previous`",
                value=(
                    "Navigate through your music queue\n"
                    "```yaml\n"
                    "Usage: /skip or /previous\n"
                    "Features: Track history, seamless transitions\n"
                    "```"
                ),
                inline=False
            )
            embed.add_field(
                name="⚡ `/stop`",
                value=(
                    "Stop playback and disconnect from voice channel\n"
                    "```yaml\n"
                    "Usage: /stop\n"
                    "Note: Clears queue and disconnects bot\n"
                    "```"
                ),
                inline=False
            )
            embed.add_field(
                name="⚡ `/nowplaying`",
                value=(
                    "Display currently playing track with progress bar\n"
                    "```yaml\n"
                    "Usage: /nowplaying\n"
                    "Shows: Track info, progress, duration, requester\n"
                    "```"
                ),
                inline=False
            )
            embed.add_field(
                name="⚡ `/queue [page]`",
                value=(
                    "View the music queue with pagination\n"
                    "```yaml\n"
                    "Usage: /queue page:1\n"
                    "Shows: Upcoming tracks, requesters, duration\n"
                    "```"
                ),
                inline=False
            )
            embed.add_field(
                name="⚡ `/volume <0-100>`",
                value=(
                    "Adjust playback volume\n"
                    "```yaml\n"
                    "Usage: /volume level:50\n"
                    "Range: 0-100%\n"
                    "Quick Control: Use +/- buttons on player\n"
                    "```"
                ),
                inline=False
            )
            embed.add_field(
                name="⚡ `/shuffle`",
                value=(
                    "Randomize the queue order\n"
                    "```yaml\n"
                    "Usage: /shuffle\n"
                    "Effect: Shuffles all queued tracks\n"
                    "```"
                ),
                inline=False
            )
            embed.add_field(
                name="⚡ `/loop <mode>`",
                value=(
                    "Set loop mode for playback\n"
                    "```yaml\n"
                    "Usage: /loop mode:\"track/queue/off\"\n"
                    "Modes:\n"
                    "  • track - Repeat current track\n"
                    "  • queue - Repeat entire queue\n"
                    "  • off   - No looping\n"
                    "```"
                ),
                inline=False
            )
            embed.add_field(
                name="⚡ `/clear`",
                value=(
                    "Clear all tracks from the queue\n"
                    "```yaml\n"
                    "Usage: /clear\n"
                    "Note: Does not stop current track\n"
                    "```"
                ),
                inline=False
            )
            embed.add_field(
                name="⚡ `/remove <position>`",
                value=(
                    "Remove a specific track from queue\n"
                    "```yaml\n"
                    "Usage: /remove position:3\n"
                    "Tip: Use /queue to see track positions\n"
                    "```"
                ),
                inline=False
            )
            embed.add_field(
                name="⚡ `/seek <seconds>`",
                value=(
                    "Jump to a specific position in the track\n"
                    "```yaml\n"
                    "Usage: /seek seconds:90\n"
                    "Example: /seek seconds:120 (jumps to 2:00)\n"
                    "```"
                ),
                inline=False
            )
            embed.add_field(
                name="⚡ `/playlist`",
                value=(
                    "Manage your saved playlists\n"
                    "```yaml\n"
                    "Usage: /playlist\n"
                    "Features:\n"
                    "  • Save current queue as playlist\n"
                    "  • Load saved playlists\n"
                    "  • List all your playlists\n"
                    "  • Delete playlists\n"
                    "Storage: Persistent across bot restarts\n"
                    "```"
                ),
                inline=False
            )
            embed.add_field(
                name="🎛️ **Interactive Controls**",
                value=(
                    "Every now playing message includes:\n"
                    "```\n"
                    "⏮️ Previous  |  ⏭️ Skip  |  ⏸️ Pause/Resume\n"
                    "🔁 Loop  |  🔀 Shuffle  |  🔊 Volume ±\n"
                    "⏩ Seek  |  🎚️ Effects  |  🔍 Search\n"
                    "```"
                ),
                inline=False
            )
            embed.add_field(
                name="🎨 **Audio Effects**",
                value=(
                    "Available effects via Effects button:\n"
                    "```\n"
                    "• Bass Boost (0-100%)\n"
                    "• Speed Control (0.5x - 2.0x)\n"
                    "• Pitch Shift (-12 to +12 semitones)\n"
                    "• Nightcore Mode (fast + high pitch)\n"
                    "• Slowed & Reverb (slow + echo)\n"
                    "```"
                ),
                inline=False
            )
            embed.set_footer(text="ΓÜí Whiteout Survival Bot // Music Player Module")
        
        elif category == "reminders":
            embed = discord.Embed(
                title="⏰ REMINDERS & TIME // MODULE",
                description=(
                    "```ansi\n"
                    "\u001b[1;36m▸ SCHEDULED NOTIFICATIONS & TIME MANAGEMENT\u001b[0m\n"
                    "```\n"
                ),
                color=0x00d9ff
            )
            embed.add_field(
                name="⚡ `/reminder [time] [message] [channel]`",
                value=(
                    "Create a timed reminder with custom message\n"
                    "```yaml\n"
                    "Usage: /reminder time:\"1h 30m\" message:\"Event starts!\"\n"
                    "Formats: 1h, 30m, 2d, 1h30m, etc.\n"
                    "Optional: Specify channel for reminder\n"
                    "```"
                ),
                inline=False
            )
            embed.add_field(
                name="⚡ `/reminderdashboard`",
                value=(
                    "Open interactive reminder management dashboard\n"
                    "```yaml\n"
                    "Usage: /reminderdashboard\n"
                    "Features:\n"
                    "  • List all your active reminders\n"
                    "  • Delete reminders\n"
                    "  • Set your timezone preference\n"
                    "```"
                ),
                inline=False
            )
            embed.set_footer(text="ΓÜí Whiteout Survival Bot // Reminders & Time Module")
        
        elif category == "community":
            embed = discord.Embed(
                title="👥 COMMUNITY & STATS // MODULE",
                description=(
                    "```ansi\n"
                    "\u001b[1;36m▸ SERVER ANALYTICS & MEMBER TRACKING\u001b[0m\n"
                    "```\n"
                ),
                color=0x00d9ff
            )
            embed.add_field(
                name="⚡ `/serverstats`",
                value=(
                    "View comprehensive server statistics and charts\n"
                    "```yaml\n"
                    "Usage: /serverstats\n"
                    "Shows: Members, channels, roles, activity, boost status\n"
                    "```"
                ),
                inline=False
            )
            embed.add_field(
                name="⚡ `/mostactive`",
                value=(
                    "Show top 3 most active users with activity graph\n"
                    "```yaml\n"
                    "Usage: /mostactive\n"
                    "Analysis: Current month message activity in channel\n"
                    "Output: Leaderboard + daily activity graph\n"
                    "```"
                ),
                inline=False
            )
            embed.add_field(
                name="⚡ `/birthday`",
                value=(
                    "Manage your birthday entry (day & month)\n"
                    "```yaml\n"
                    "Usage: /birthday\n"
                    "Features: Set/remove birthday, get automatic wishes\n"
                    "```"
                ),
                inline=False
            )
            embed.add_field(
                name="⚡ `/server_age`",
                value=(
                    "Check your Whiteout Survival server age and milestones\n"
                    "```yaml\n"
                    "Usage: /server_age\n"
                    "Shows: Server age, upcoming milestones, progression\n"
                    "```"
                ),
                inline=False
            )
            embed.set_footer(text="ΓÜí Whiteout Survival Bot // Community & Stats Module")
        
        elif category == "alliance":
            embed = discord.Embed(
                title="🛡️ ALLIANCE MANAGEMENT // MODULE",
                description=(
                    "```ansi\n"
                    "\u001b[1;36m▸ ALLIANCE MONITORING & OPERATIONS\u001b[0m\n"
                    "```\n"
                ),
                color=0x00d9ff
            )
            embed.add_field(
                name="⚡ `/alliancemonitor`",
                value=(
                    "Alliance monitoring dashboard with quick access to all features\n"
                    "```yaml\n"
                    "Usage: /alliancemonitor\n"
                    "Features: Member tracking, activity logs, statistics\n"
                    "```"
                ),
                inline=False
            )
            embed.add_field(
                name="⚡ `/manage`",
                value=(
                    "Quick access to management operations\n"
                    "```yaml\n"
                    "Usage: /manage\n"
                    "Features: Member operations, records, gift code management\n"
                    "```"
                ),
                inline=False
            )
            embed.add_field(
                name="⚡ `/event [name]`",
                value=(
                    "Get detailed information about Whiteout Survival events\n"
                    "```yaml\n"
                    "Usage: /event name:\"event_name\"\n"
                    "Features: Autocomplete, event tips, rewards info\n"
                    "```"
                ),
                inline=False
            )
            embed.set_footer(text="ΓÜí Whiteout Survival Bot // Alliance Management Module")
        
        elif category == "config":
            embed = discord.Embed(
                title="⚙️ SERVER CONFIGURATION // MODULE",
                description=(
                    "```ansi\n"
                    "\u001b[1;36m▸ SERVER SETTINGS & CUSTOMIZATION\u001b[0m\n"
                    "```\n"
                ),
                color=0x00d9ff
            )
            embed.add_field(
                name="⚡ `/settings` 🔒",
                value=(
                    "Open the main settings menu\n"
                    "```yaml\n"
                    "Usage: /settings\n"
                    "Permissions: Administrator\n"
                    "Features: Bot configuration, server preferences\n"
                    "```"
                ),
                inline=False
            )
            embed.add_field(
                name="⚡ `/welcome` 🔒",
                value=(
                    "Configure welcome message settings for new members\n"
                    "```yaml\n"
                    "Usage: /welcome\n"
                    "Permissions: Administrator\n"
                    "Features: Custom messages, channel selection, formatting\n"
                    "```"
                ),
                inline=False
            )
            embed.add_field(
                name="⚡ `/removewelcomechannel` 🔒",
                value=(
                    "Remove the welcome channel configuration\n"
                    "```yaml\n"
                    "Usage: /removewelcomechannel\n"
                    "Permissions: Administrator\n"
                    "```"
                ),
                inline=False
            )
            embed.add_field(
                name="⚡ `/start`",
                value=(
                    "Show the main interactive menu\n"
                    "```yaml\n"
                    "Usage: /start\n"
                    "Features: Quick navigation to all bot features\n"
                    "```"
                ),
                inline=False
            )
            embed.set_footer(text="ΓÜí Whiteout Survival Bot // Server Configuration Module")
        
        elif category == "autotranslate":
            embed = discord.Embed(
                title="🌐 AUTO-TRANSLATE // MODULE",
                description=(
                    "```ansi\n"
                    "\u001b[1;36m▸ AUTOMATIC MESSAGE TRANSLATION BETWEEN CHANNELS\u001b[0m\n"
                    "```\n"
                ),
                color=0x00d9ff
            )
            embed.add_field(
                name="⚡ `/autotranslatecreate`",
                value=(
                    "Create automatic translation between channels\n"
                    "```yaml\n"
                    "Usage: /autotranslatecreate\n"
                    "Features: Select source/target channels and languages\n"
                    "```"
                ),
                inline=False
            )
            embed.add_field(
                name="⚡ `/autotranslatelist`",
                value=(
                    "View all auto-translate configurations\n"
                    "```yaml\n"
                    "Usage: /autotranslatelist\n"
                    "Shows: Active translations, channels, languages\n"
                    "```"
                ),
                inline=False
            )
            embed.add_field(
                name="⚡ `/autotranslateedit`",
                value=(
                    "Edit an existing auto-translate configuration\n"
                    "```yaml\n"
                    "Usage: /autotranslateedit\n"
                    "Features: Change languages, channels, or settings\n"
                    "```"
                ),
                inline=False
            )
            embed.add_field(
                name="⚡ `/autotranslatetoggle`",
                value=(
                    "Enable/disable an auto-translate configuration\n"
                    "```yaml\n"
                    "Usage: /autotranslatetoggle\n"
                    "Quick: Temporarily disable without deleting\n"
                    "```"
                ),
                inline=False
            )
            embed.add_field(
                name="⚡ `/autotranslatedelete`",
                value=(
                    "Delete an auto-translate configuration\n"
                    "```yaml\n"
                    "Usage: /autotranslatedelete\n"
                    "Removes: Selected translation setup\n"
                    "```"
                ),
                inline=False
            )
            embed.set_footer(text="ΓÜí Whiteout Survival Bot // Auto-Translate Module")
        
        elif category == "utility":
            embed = discord.Embed(
                title="🔧 UTILITY & TOOLS // MODULE",
                description=(
                    "```ansi\n"
                    "\u001b[1;36m▸ ADDITIONAL UTILITIES & FEATURES\u001b[0m\n"
                    "```\n"
                ),
                color=0x00d9ff
            )
            embed.add_field(
                name="⚡ `/websearch [query]`",
                value=(
                    "Search the web with powerful, organized results\n"
                    "```yaml\n"
                    "Usage: /websearch query:\"your search term\"\n"
                    "Example: /websearch query:\"Whiteout Survival tips\"\n"
                    "Features: Top results with summaries and links\n"
                    "```"
                ),
                inline=False
            )
            embed.add_field(
                name="📋 **Legend**",
                value=(
                    "```\n"
                    "🔒 = Administrator permission required\n"
                    "⚡ = Available to all users\n"
                    "```"
                ),
                inline=False
            )
            embed.set_footer(text="ΓÜí Whiteout Survival Bot // Utility & Tools Module")
        
        try:
            await interaction.response.edit_message(embed=embed)
        except Exception as e:
            logger.error(f"Failed to update help embed: {e}")
            try:
                await interaction.response.send_message("Failed to load category.", ephemeral=True)
            except Exception:
                pass


# Import GiftCodeView from giftcode_poster to ensure consistent behavior (Redeem button, etc.)
from giftcode_poster import GiftCodeView

class ReminderDeleteSelect(discord.ui.Select):
    def __init__(self, reminders_list: list, reminder_system):
        self.reminder_system = reminder_system
        options = []
        for idx, r in enumerate(reminders_list):
            rid = str(r.get('id'))
            msg = r.get('message', '')[:60].replace('\n', ' ')
            label = f"{idx+1:02d}"
            desc = (f"ID #{rid} — {msg}") if msg else f"ID #{rid}"
            options.append(discord.SelectOption(label=label, description=desc, value=rid))

        super().__init__(placeholder="Select a reminder to delete", min_values=1, max_values=1, options=options)

    async def callback(self, select_interaction: discord.Interaction):
        try:
            chosen = self.values[0]
            await self.reminder_system.delete_user_reminder(select_interaction, chosen)
        except Exception as e:
            logger.error(f"Failed to delete reminder via dashboard: {e}")
            try:
                await select_interaction.response.send_message("Failed to delete reminder. Try again.", ephemeral=True)
            except Exception:
                pass

class TimezoneSelect(discord.ui.Select):
    def __init__(self):
        options = []
        options.append(discord.SelectOption(label="Clear timezone (use default)", value="__clear__"))
        tz_countries = {
            'utc': 'Universal',
            'gmt': 'UK/UTC',
            'est': 'United States (Eastern)',
            'cst': 'United States (Central)',
            'mst': 'United States (Mountain)',
            'pst': 'United States (Pacific)',
            'ist': 'India',
            'cet': 'Central Europe',
            'cest': 'Central Europe',
            'jst': 'Japan',
            'aest': 'Australia',
            'bst': 'United Kingdom'
        }
        for tz in sorted(TimeParser.TIMEZONE_MAP.keys()):
            country = tz_countries.get(tz.lower(), '')
            desc = country if country else TimeParser.TIMEZONE_MAP.get(tz.lower(), '')
            options.append(discord.SelectOption(label=tz.upper(), description=desc, value=tz))
        super().__init__(placeholder="Select timezone (or clear)", min_values=1, max_values=1, options=options)

    async def callback(self, select_interaction: discord.Interaction):
        try:
            val = self.values[0]
            user_id = select_interaction.user.id
            if val == "__clear__":
                set_user_timezone(user_id, '')
                await select_interaction.response.send_message("✅ Your timezone has been cleared.", ephemeral=True)
                return

            if val.lower() not in TimeParser.TIMEZONE_MAP:
                await select_interaction.response.send_message("Unknown timezone selection.", ephemeral=True)
                return
            set_user_timezone(user_id, val.lower())
            await select_interaction.response.send_message(f"✅ Timezone set to {val.upper()}", ephemeral=True)
        except Exception as e:
            logger.error(f"Failed to set timezone via dashboard: {e}")
            try:
                await select_interaction.response.send_message("Failed to set timezone. Try again.", ephemeral=True)
            except Exception:
                pass

class ReminderDashboardView(discord.ui.View):
    def __init__(self, reminder_system):
        super().__init__(timeout=None)
        self.reminder_system = reminder_system

    @discord.ui.button(label="List", style=discord.ButtonStyle.primary, custom_id="rd_list", emoji="📝")
    async def list_button(self, button_interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await self.reminder_system.list_user_reminders(button_interaction)
        except Exception as e:
            logger.error(f"Failed to list reminders via dashboard: {e}")
            try:
                await button_interaction.response.send_message("Failed to fetch your reminders.", ephemeral=True)
            except Exception:
                pass

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.secondary, custom_id="rd_delete", emoji="🗑️")
    async def delete_button(self, button_interaction: discord.Interaction, button: discord.ui.Button):
        try:
            user_id = str(button_interaction.user.id)
            reminders = self.reminder_system.storage.get_user_reminders(user_id, limit=25)
            if not reminders:
                await button_interaction.response.send_message("You don't have any active reminders to delete.", ephemeral=True)
                return

            select = ReminderDeleteSelect(reminders, self.reminder_system)
            v = discord.ui.View()
            v.add_item(select)
            header = discord.Embed(title="🗑️ Delete Reminder", description="Choose the reminder number (left) then confirm.", color=0x2f3136)
            await button_interaction.response.send_message(embed=header, view=v, ephemeral=True)
        except Exception as e:
            logger.error(f"Failed to open delete reminder select: {e}")
            try:
                await button_interaction.response.send_message("Failed to open reminder deletion UI.", ephemeral=True)
            except Exception:
                pass

    @discord.ui.button(label="Timezone", style=discord.ButtonStyle.success, custom_id="rd_tz", emoji="🌐")
    async def tz_button(self, button_interaction: discord.Interaction, button: discord.ui.Button):
        try:
            select = TimezoneSelect()
            v = discord.ui.View()
            v.add_item(select)
            embed = discord.Embed(title="🌐 Select Timezone", description="Choose how times are displayed for your reminders.", color=0x2f3136)
            await button_interaction.response.send_message(embed=embed, view=v, ephemeral=True)
        except Exception as e:
            logger.error(f"Failed to open timezone select: {e}")
            try:
                await button_interaction.response.send_message("Failed to open timezone selection.", ephemeral=True)
            except Exception:
                pass

class GiftCodeSettingsView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Channel", style=discord.ButtonStyle.primary, custom_id="gcs_channel", emoji="📣")
    async def channel_button(self, button_interaction: discord.Interaction, button: discord.ui.Button):
        try:
            guild_id = button_interaction.guild.id
            channel_id = giftcode_poster.poster.get_channel(guild_id)
            if not channel_id:
                await button_interaction.response.send_message("No gift code channel configured for this server.", ephemeral=True)
                return
            ch = button_interaction.guild.get_channel(channel_id)
            if not ch:
                await button_interaction.response.send_message(f"Configured channel (ID: {channel_id}) not found or inaccessible.", ephemeral=True)
                return
            await button_interaction.response.send_message(f"Current gift code channel is {ch.mention}", ephemeral=True)
        except Exception as e:
            logger.error(f"Error showing gift channel via dashboard: {e}")
            try:
                await button_interaction.response.send_message("Failed to retrieve gift channel.", ephemeral=True)
            except Exception:
                pass

    @discord.ui.button(label="Auto send", style=discord.ButtonStyle.success, custom_id="gcs_set", emoji="✅")
    async def set_here_button(self, button_interaction: discord.Interaction, button: discord.ui.Button):
        try:
            if not button_interaction.user.guild_permissions.administrator:
                await button_interaction.response.send_message("Only server administrators can set the gift channel.", ephemeral=True)
                return
            channel = button_interaction.channel
            if not isinstance(channel, discord.TextChannel):
                await button_interaction.response.send_message("This command must be used in a text channel.", ephemeral=True)
                return
            giftcode_poster.poster.set_channel(button_interaction.guild.id, channel.id)
            await button_interaction.response.send_message(f"✅ Gift code channel set to {channel.mention}", ephemeral=True)
        except Exception as e:
            logger.error(f"Failed to set gift channel via dashboard: {e}")
            try:
                await button_interaction.response.send_message("Failed to set gift channel.", ephemeral=True)
            except Exception:
                pass

    def setup_giftcode_db(self):
        """Ensure auto_register_channels table exists with correct schema"""
        try:
            with sqlite3.connect('db/giftcode.sqlite') as db:
                cursor = db.cursor()
                # Create table if not exists with alliance_id
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS auto_register_channels (
                        guild_id INTEGER PRIMARY KEY,
                        channel_id INTEGER,
                        alliance_id INTEGER,
                        registered_count INTEGER DEFAULT 0
                    )
                """)
                
                # Check if alliance_id column exists (migration for existing table)
                cursor.execute("PRAGMA table_info(auto_register_channels)")
                columns = [info[1] for info in cursor.fetchall()]
                if 'alliance_id' not in columns:
                    cursor.execute("ALTER TABLE auto_register_channels ADD COLUMN alliance_id INTEGER")
                if 'registered_count' not in columns:
                    cursor.execute("ALTER TABLE auto_register_channels ADD COLUMN registered_count INTEGER DEFAULT 0")
                    db.commit()
        except Exception as e:
            logger.error(f"Failed to setup giftcode DB: {e}")

    @discord.ui.button(label="Manage Codes", style=discord.ButtonStyle.primary, custom_id="giftcode_menu", emoji="🎁")
    async def manage_codes_button(self, button_interaction: discord.Interaction, button: discord.ui.Button):
        """
        This button links to the Gift Code Management menu from /manage.
        Now protected by password authentication like /manage command.
        """
        try:
            # Check if MongoDB is available
            from db.mongo_adapters import ServerAllianceAdapter, AuthSessionsAdapter
            from db.mongo_adapters import mongo_enabled
            
            if not mongo_enabled() or not ServerAllianceAdapter:
                await button_interaction.response.send_message(
                    "❌ MongoDB not enabled. Cannot access management operations.",
                    ephemeral=True
                )
                return
            
            # Check if password is configured
            stored_password = ServerAllianceAdapter.get_password(button_interaction.guild.id)
            if not stored_password:
                error_embed = discord.Embed(
                    title="🔒 Access Denied",
                    description="No password configured for management access.",
                    color=0x2B2D31
                )
                error_embed.add_field(
                    name="⚙️ Administrator Action Required",
                    value="Contact a server administrator to set up password via:\\n`/settings` → **Bot Operations** → **Set Member List Password**",
                    inline=False
                )
                error_embed.add_field(
                    name="💬 Need Help?",
                    value="Contact the Global Admin for assistance with bot setup.",
                    inline=False
                )
                
                # Create view with contact button
                class ContactAdminView(discord.ui.View):
                    def __init__(self):
                        super().__init__(timeout=None)
                        self.add_item(discord.ui.Button(
                            label="Contact Global Admin",
                            emoji="👤",
                            style=discord.ButtonStyle.link,
                            url="https://discord.com/users/850786361572720661"
                        ))
                
                view = ContactAdminView()
                await button_interaction.response.send_message(embed=error_embed, view=view, ephemeral=True)
                return
            
            # Check if user has valid authentication session
            if AuthSessionsAdapter and AuthSessionsAdapter.is_session_valid(
                button_interaction.guild.id,
                button_interaction.user.id,
                stored_password
            ):
                # User is authenticated - let the button continue to be handled by ManageGiftcode cog
                # The on_interaction handler will process this
                pass
            else:
                # User needs to authenticate first
                await button_interaction.response.send_message(
                    "🔒 **Authentication Required**\\n\\n"
                    "Please use `/manage` to authenticate first before accessing gift code management.\\n\\n"
                    "This protects sensitive gift code operations.",
                    ephemeral=True
                )
                return
                
        except Exception as e:
            logger.error(f"Error in manage_codes_button authentication: {e}")
            await button_interaction.response.send_message(
                "❌ An error occurred while checking authentication.",
                ephemeral=True
            )


# Birthday Dashboard View
class BirthdayDashboardView(discord.ui.View):
    """Dashboard for birthday management"""
    def __init__(self, birthday_system):
        super().__init__(timeout=None)
        self.birthday_system = birthday_system
    
    @discord.ui.button(label="Set Channel", style=discord.ButtonStyle.primary, custom_id="bd_set_channel", emoji="📺", row=0)
    async def set_channel_button(self, button_interaction: discord.Interaction, button: discord.ui.Button):
        """Allow setting the birthday wishes channel for this guild"""
        try:
            # Create channel select view
            class ChannelSelectView(discord.ui.View):
                def __init__(self, birthday_system):
                    super().__init__(timeout=60)
                    self.birthday_system = birthday_system
                
                @discord.ui.select(cls=discord.ui.ChannelSelect, placeholder="Select birthday wishes channel", channel_types=[discord.ChannelType.text], min_values=1, max_values=1)
                async def channel_select(self, select_interaction: discord.Interaction, select: discord.ui.ChannelSelect):
                    selected_channel = select.values[0]
                    
                    # Save channel to database
                    success = self.birthday_system.set_birthday_channel(select_interaction.guild_id, selected_channel.id)
                    
                    if success:
                        embed = discord.Embed(
                            title="✅ Birthday Channel Set!",
                            description=f"Birthday wishes will now be sent to {selected_channel.mention}",
                            color=0x00FF00
                        )
                        await select_interaction.response.send_message(embed=embed, ephemeral=True)
                    else:
                        await select_interaction.response.send_message(
                            "❌ Failed to save birthday channel. Please try again.",
                            ephemeral=True
                        )
            
            # Get current channel
            current_channel_id = self.birthday_system.get_birthday_channel(button_interaction.guild_id)
            current_info = "Not configured"
            if current_channel_id:
                channel = button_interaction.guild.get_channel(current_channel_id)
                current_info = channel.mention if channel else f"ID: {current_channel_id} (Not found)"
            
            view = ChannelSelectView(self.birthday_system)
            embed = discord.Embed(
                title="📺 Set Birthday Wishes Channel",
                description=f"**Current Channel:** {current_info}\n\nSelect the channel where birthday wishes should be sent:",
                color=discord.Color.blue()
            )
            await button_interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Failed to open set channel: {e}")
            try:
                await button_interaction.response.send_message("Failed to open channel selection.", ephemeral=True)
            except Exception:
                pass
    
    @discord.ui.button(label="Set My Birthday", style=discord.ButtonStyle.success, custom_id="bd_set_my", emoji="🎂", row=0)
    async def set_my_birthday_button(self, button_interaction: discord.Interaction, button: discord.ui.Button):
        """Set your own birthday"""
        try:
            # Check if user already has a birthday
            existing = self.birthday_system.get_birthday(button_interaction.guild_id, button_interaction.user.id)
            if existing:
                await button_interaction.response.send_message(
                    "❌ You already have a birthday set. Use **Remove Birthday** first to change it.",
                    ephemeral=True
                )
                return
            
            # Create modal for birthday input
            class BirthdayModal(discord.ui.Modal, title="🎂 Set Your Birthday"):
                day_input = discord.ui.TextInput(label="Day (1-31)", placeholder="15", style=discord.TextStyle.short, required=True, max_length=2)
                month_input = discord.ui.TextInput(label="Month (1-12)", placeholder="6", style=discord.TextStyle.short, required=True, max_length=2)
                player_id_input = discord.ui.TextInput(
                    label="Player ID (Optional - 8 or 9 digits)",
                    placeholder="e.g. 123456789 (for WOS avatar)",
                    style=discord.TextStyle.short,
                    required=False,
                    max_length=9,
                    min_length=8
                )
                
                def __init__(self, birthday_system):
                    super().__init__()
                    self.birthday_system = birthday_system
                
                async def on_submit(self, modal_interaction: discord.Interaction):
                    # Defer immediately to prevent timeout
                    await modal_interaction.response.defer(ephemeral=True)
                    
                    try:
                        day = int(self.day_input.value.strip())
                        month = int(self.month_input.value.strip())
                        
                        # Validate and process player_id if provided
                        pid = self.player_id_input.value.strip() if self.player_id_input.value else None
                        if pid:
                            # Validate player_id format (must be 8 or 9 digits)
                            if not pid.isdigit() or not (8 <= len(pid) <= 9):
                                await modal_interaction.followup.send(
                                    "❌ Player ID must be 8 or 9 digits. Please try again.",
                                    ephemeral=True
                                )
                                return
                        
                        if not self.birthday_system.is_valid_date(day, month):
                            await modal_interaction.followup.send("❌ Invalid date!", ephemeral=True)
                            return
                        
                        success = self.birthday_system.add_birthday(modal_interaction.guild_id, modal_interaction.user.id, day, month, pid)
                        if success:
                            # Don't reload - add_birthday already updated the cache with player_id
                            import calendar
                            from datetime import datetime
                            month_name = calendar.month_name[month]
                            
                            # Check if birthday is today and send immediate wish
                            now = datetime.utcnow()
                            if day == now.day and month == now.month:
                                # Clear sent wish record to allow immediate wish
                                user_id_str = str(modal_interaction.user.id)
                                if user_id_str in self.birthday_system.sent_wishes_cache:
                                    del self.birthday_system.sent_wishes_cache[user_id_str]
                                    self.birthday_system.save_sent_wishes()
                                
                                # Get birthday channel
                                channel_id = self.birthday_system.get_birthday_channel(modal_interaction.guild_id)
                                if channel_id:
                                    try:
                                        channel = modal_interaction.client.get_channel(channel_id)
                                        if channel:
                                            # Send immediate birthday wish
                                            user = modal_interaction.user
                                            await self.birthday_system.send_birthday_wishes(channel, [user])
                                            # Mark as sent
                                            self.birthday_system.mark_wish_sent(modal_interaction.user.id)
                                            logger.info(f"🎉 Sent immediate birthday wish for user {modal_interaction.user.id}")
                                    except Exception as e:
                                        logger.error(f"Failed to send immediate birthday wish: {e}")
                            
                            success_msg = f"✅ Birthday set to **{month_name} {day}**!"
                            if pid:
                                success_msg += "\n🎮 Player ID linked for WOS avatar"
                            await modal_interaction.followup.send(success_msg, ephemeral=True)
                        else:
                            await modal_interaction.followup.send("❌ Failed to save birthday.", ephemeral=True)
                    except ValueError:
                        await modal_interaction.followup.send("❌ Please enter valid numbers.", ephemeral=True)
            
            modal = BirthdayModal(self.birthday_system)
            await button_interaction.response.send_modal(modal)
        except Exception as e:
            logger.error(f"Failed to open birthday modal: {e}")
    
    @discord.ui.button(label="Set Others Birthday", style=discord.ButtonStyle.secondary, custom_id="bd_set_others", emoji="👥", row=1)
    async def set_others_birthday_button(self, button_interaction: discord.Interaction, button: discord.ui.Button):
        """Set birthday for another user"""
        try:
            class UserSelectView(discord.ui.View):
                def __init__(self, birthday_system):
                    super().__init__(timeout=60)
                    self.birthday_system = birthday_system
                
                @discord.ui.select(cls=discord.ui.UserSelect, placeholder="Select user", min_values=1, max_values=1)
                async def user_select(self, select_interaction: discord.Interaction, select: discord.ui.UserSelect):
                    selected_user = select.values[0]
                    
                    class BirthdayModal(discord.ui.Modal, title=f"🎂 Set Birthday for {selected_user.display_name}"):
                        day_input = discord.ui.TextInput(label="Day (1-31)", placeholder="15", style=discord.TextStyle.short, required=True, max_length=2)
                        month_input = discord.ui.TextInput(label="Month (1-12)", placeholder="6", style=discord.TextStyle.short, required=True, max_length=2)
                        player_id_input = discord.ui.TextInput(
                            label="Player ID (Optional - 8 or 9 digits)",
                            placeholder="e.g. 123456789 (for WOS avatar)",
                            style=discord.TextStyle.short,
                            required=False,
                            max_length=9,
                            min_length=8
                        )
                        
                        def __init__(self, birthday_system, target_user):
                            super().__init__()
                            self.birthday_system = birthday_system
                            self.target_user = target_user
                        
                        async def on_submit(self, modal_interaction: discord.Interaction):
                            # Defer immediately to prevent timeout
                            await modal_interaction.response.defer(ephemeral=True)
                            
                            try:
                                day = int(self.day_input.value.strip())
                                month = int(self.month_input.value.strip())
                                
                                # Validate and process player_id if provided
                                pid = self.player_id_input.value.strip() if self.player_id_input.value else None
                                if pid:
                                    # Validate player_id format (must be 8 or 9 digits)
                                    if not pid.isdigit() or not (8 <= len(pid) <= 9):
                                        await modal_interaction.followup.send(
                                            "❌ Player ID must be 8 or 9 digits. Please try again.",
                                            ephemeral=True
                                        )
                                        return
                                
                                if not self.birthday_system.is_valid_date(day, month):
                                    await modal_interaction.followup.send("❌ Invalid date!", ephemeral=True)
                                    return
                                
                                success = self.birthday_system.add_birthday(modal_interaction.guild_id, self.target_user.id, day, month, pid)
                                if success:
                                    # Don't reload - add_birthday already updated the cache with player_id
                                    import calendar
                                    from datetime import datetime
                                    month_name = calendar.month_name[month]
                                    
                                    # Check if birthday is today and send immediate wish
                                    now = datetime.utcnow()
                                    if day == now.day and month == now.month:
                                        # Clear sent wish record to allow immediate wish
                                        user_id_str = str(self.target_user.id)
                                        if user_id_str in self.birthday_system.sent_wishes_cache:
                                            del self.birthday_system.sent_wishes_cache[user_id_str]
                                            self.birthday_system.save_sent_wishes()
                                        
                                        # Get birthday channel
                                        channel_id = self.birthday_system.get_birthday_channel(modal_interaction.guild_id)
                                        if channel_id:
                                            try:
                                                channel = modal_interaction.client.get_channel(channel_id)
                                                if channel:
                                                    # Send immediate birthday wish
                                                    await self.birthday_system.send_birthday_wishes(channel, [self.target_user])
                                                    # Mark as sent
                                                    self.birthday_system.mark_wish_sent(self.target_user.id)
                                                    logger.info(f"🎉 Sent immediate birthday wish for user {self.target_user.id}")
                                            except Exception as e:
                                                logger.error(f"Failed to send immediate birthday wish: {e}")
                                    
                                    success_msg = f"✅ Set {self.target_user.mention}'s birthday to **{month_name} {day}**!"
                                    if pid:
                                        success_msg += f"\n🎮 Player ID linked for WOS avatar"
                                    await modal_interaction.followup.send(success_msg, ephemeral=True)
                                else:
                                    await modal_interaction.followup.send("❌ Failed to save birthday.", ephemeral=True)
                            except ValueError:
                                await modal_interaction.followup.send("❌ Please enter valid numbers.", ephemeral=True)
                    
                    modal = BirthdayModal(self.birthday_system, selected_user)
                    await select_interaction.response.send_modal(modal)
            
            view = UserSelectView(self.birthday_system)
            await button_interaction.response.send_message("Select a user:", view=view, ephemeral=True)
        except Exception as e:
            logger.error(f"Failed to open user select: {e}")
    
    @discord.ui.button(label="Remove Birthday", style=discord.ButtonStyle.danger, custom_id="bd_remove", emoji="🗑️", row=1)
    async def remove_birthday_button(self, button_interaction: discord.Interaction, button: discord.ui.Button):
        """Remove your birthday"""
        try:
            birthday = self.birthday_system.get_birthday(button_interaction.guild_id, button_interaction.user.id)
            if not birthday:
                await button_interaction.response.send_message("❌ You don't have a birthday set!", ephemeral=True)
                return
            
            success = self.birthday_system.remove_birthday(button_interaction.guild_id, button_interaction.user.id)
            if success:
                self.birthday_system.load_birthdays()  # Refresh cache
                await button_interaction.response.send_message("✅ Birthday removed!", ephemeral=True)
            else:
                await button_interaction.response.send_message("❌ Failed to remove birthday.", ephemeral=True)
        except Exception as e:
            logger.error(f"Failed to remove birthday: {e}")
    
    @discord.ui.button(label="Upcoming Birthdays", style=discord.ButtonStyle.success, custom_id="bd_upcoming", emoji="📅", row=2)
    async def upcoming_birthdays_button(self, button_interaction: discord.Interaction, button: discord.ui.Button):
        """Show upcoming birthdays"""
        try:
            await button_interaction.response.defer(ephemeral=True)
            
            from datetime import datetime
            import calendar
            
            now = datetime.utcnow()
            upcoming = []
            
            guild_prefix = f"{button_interaction.guild_id}_"
            for record_key, birthday_data in self.birthday_system.birthdays_cache.items():
                try:
                    if "_" not in record_key or not record_key.startswith(guild_prefix):
                        continue
                    user_id_str = record_key.split("_", 1)[1]
                    day = birthday_data.get('day')
                    month = birthday_data.get('month')
                    
                    this_year = now.year
                    birthday_this_year = datetime(this_year, month, day)
                    
                    if birthday_this_year < now:
                        birthday_this_year = datetime(this_year + 1, month, day)
                    
                    days_until = (birthday_this_year - now).days
                    
                    if days_until <= 30:
                        user_id = int(user_id_str)
                        try:
                            user = await button_interaction.client.fetch_user(user_id)
                            upcoming.append({'user': user, 'day': day, 'month': month, 'days_until': days_until})
                        except Exception:
                            pass
                except Exception:
                    pass
            
            upcoming.sort(key=lambda x: x['days_until'])
            
            if upcoming:
                embed = discord.Embed(title="🎂 Upcoming Birthdays (Next 30 Days)", color=0xFFD700)
                for item in upcoming[:10]:
                    month_name = calendar.month_name[item['month']]
                    days_text = "Today! 🎉" if item['days_until'] == 0 else f"in {item['days_until']} day(s)"
                    embed.add_field(name=f"{item['user'].display_name}", value=f"📅 {month_name} {item['day']} ({days_text})", inline=False)
                
                if len(upcoming) > 10:
                    embed.set_footer(text=f"Showing 10 of {len(upcoming)} upcoming birthdays")
                
                await button_interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await button_interaction.followup.send("📅 No upcoming birthdays in the next 30 days.", ephemeral=True)
        except Exception as e:
            logger.error(f"Failed to list birthdays: {e}")
    
    @discord.ui.button(label="My Birthday", style=discord.ButtonStyle.secondary, custom_id="bd_my", emoji="🎁", row=2)
    async def my_birthday_button(self, button_interaction: discord.Interaction, button: discord.ui.Button):
        """Check your own birthday"""
        try:
            birthday = self.birthday_system.get_birthday(button_interaction.guild_id, button_interaction.user.id)
            
            if birthday:
                import calendar
                day = birthday.get('day')
                month = birthday.get('month')
                month_name = calendar.month_name[month]
                
                embed = discord.Embed(
                    title=f"🎂 Your Birthday",
                    description=f"Birthday: **{month_name} {day}**",
                    color=0x00BFFF
                )
                embed.set_thumbnail(url=button_interaction.user.display_avatar.url)
                await button_interaction.response.send_message(embed=embed, ephemeral=True)
            else:
                await button_interaction.response.send_message("❌ You haven't set your birthday yet!", ephemeral=True)
        except Exception as e:
            logger.error(f"Failed to check birthday: {e}")
