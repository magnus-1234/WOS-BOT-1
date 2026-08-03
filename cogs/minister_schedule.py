import discord
from discord import app_commands
from discord.ext import commands
import asyncio
import sqlite3
import aiohttp
import hashlib
from aiohttp_socks import ProxyConnector
import time
from typing import Optional
from admin_utils import is_bot_owner

SECRET = 'tB87#kPtkxqOS2'

class ChannelSelectView(discord.ui.View):
    def __init__(self, bot, context: str):
        super().__init__(timeout=None)
        self.add_item(ChannelSelect(bot, context))

class ChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, bot, context: str):
        self.bot = bot
        self.context = context

        super().__init__(
            placeholder="Select a channel...",
            channel_types=[
                discord.ChannelType.text,
                discord.ChannelType.private,
                discord.ChannelType.news,
                discord.ChannelType.forum,
                discord.ChannelType.news_thread,
                discord.ChannelType.public_thread,
                discord.ChannelType.private_thread,
                discord.ChannelType.stage_voice
            ],
            min_values=1,
            max_values=1
        )

    async def callback(self, interaction: discord.Interaction):
        selected_channel = self.values[0]
        channel_id = selected_channel.id

        try:
            svs_conn = sqlite3.connect("db/svs.sqlite")
            svs_cursor = svs_conn.cursor()
            
            # Check if we're updating a minister channel
            if self.context.endswith("channel"):
                # Get the activity name from the context (e.g., "Construction Day channel" -> "Construction Day")
                activity_name = self.context.replace(" channel", "")
                
                # Check if this is one of the minister activity channels
                if activity_name in ["Construction Day", "Research Day", "Troops Training Day"]:
                    # Get the old channel ID if it exists
                    svs_cursor.execute("SELECT context_id FROM reference WHERE context=?", (self.context,))
                    old_channel_row = svs_cursor.fetchone()
                    
                    if old_channel_row:
                        old_channel_id = int(old_channel_row[0])
                        # Get the message ID for this activity
                        svs_cursor.execute("SELECT context_id FROM reference WHERE context=?", (activity_name,))
                        message_row = svs_cursor.fetchone()
                        
                        if message_row and old_channel_id != channel_id:
                            # Delete the old message if channel has changed
                            message_id = int(message_row[0])
                            guild = interaction.guild
                            if guild:
                                old_channel = guild.get_channel(old_channel_id)
                                if old_channel:
                                    try:
                                        old_message = await old_channel.fetch_message(message_id)
                                        await old_message.delete()
                                    except:
                                        pass  # Message might already be deleted
                            
                            # Remove the message reference so it will be recreated in the new channel
                            svs_cursor.execute("DELETE FROM reference WHERE context=?", (activity_name,))
            
            # Update the channel reference
            svs_cursor.execute("""
                INSERT INTO reference (context, context_id)
                VALUES (?, ?)
                ON CONFLICT(context) DO UPDATE SET context_id = excluded.context_id;
            """, (self.context, channel_id))
            svs_conn.commit()
            
            # Trigger message update in the new channel
            if self.context.endswith("channel"):
                activity_name = self.context.replace(" channel", "")
                if activity_name in ["Construction Day", "Research Day", "Troops Training Day"]:
                    minister_menu_cog = self.bot.get_cog("MinisterMenu")
                    if minister_menu_cog:
                        await minister_menu_cog.update_channel_message(activity_name)
            
            svs_conn.close()

            # Check if this is being called from the minister menu system
            minister_menu_cog = self.bot.get_cog("MinisterMenu")
            if minister_menu_cog and self.context.endswith("channel"):
                # Return to channel configuration menu with confirmation
                embed = discord.Embed(
                    title="📝 Channel Setup",
                    description=(
                        f"✅ **{self.context}** set to <#{channel_id}>\n\n"
                        "Configure channels for minister scheduling:\n\n"
                        "**Channel Types**\n"
                        "━━━━━━━━━━━━━━━━━━━━━━\n"
                        "🔨 **Construction Channel** - Shows available Construction Day slots\n"
                        "🔬 **Research Channel** - Shows available Research Day slots\n"
                        "⚔️ **Training Channel** - Shows available Training Day slots\n"
                        "📄 **Log Channel** - Receives add/remove notifications\n"
                        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        "Select a channel type to configure:"
                    ),
                    color=discord.Color.green()
                )

                # Get the ChannelConfigurationView from minister_menu
                import sys
                minister_menu_module = minister_menu_cog.__class__.__module__
                ChannelConfigurationView = getattr(sys.modules[minister_menu_module], 'ChannelConfigurationView')
                
                view = ChannelConfigurationView(self.bot, minister_menu_cog)
                
                await interaction.response.edit_message(
                    content=None, # Clear the "Select a channel for..." content
                    embed=embed,
                    view=view
                )
            else:
                # Fallback for other contexts
                await interaction.response.edit_message(
                    content=f"✅ `{self.context}` set to <#{channel_id}>.\n\nChannel configured successfully!",
                    view=None
                )

        except Exception as e:
            try:
                await interaction.response.send_message(
                    f"❌ Failed to update:\n```{e}```",
                    ephemeral=True
                )
            except discord.InteractionResponded:
                await interaction.followup.send(
                    f"❌ Failed to update:\n```{e}```",
                    ephemeral=True
                )

class MinisterSchedule(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.users_conn = sqlite3.connect('db/users.sqlite')
        self.users_cursor = self.users_conn.cursor()
        self.settings_conn = sqlite3.connect('db/settings.sqlite')
        self.settings_cursor = self.settings_conn.cursor()
        self.alliance_conn = sqlite3.connect('db/alliance.sqlite')
        self.alliance_cursor = self.alliance_conn.cursor()
        self.svs_conn = sqlite3.connect("db/svs.sqlite")
        self.svs_cursor = self.svs_conn.cursor()

        self.svs_cursor.execute("""
                    CREATE TABLE IF NOT EXISTS appointments (
                        fid INTEGER,
                        appointment_type TEXT,
                        time TEXT,
                        alliance INTEGER,
                        PRIMARY KEY (fid, appointment_type)
                    );
                """)
        self.svs_cursor.execute("""
                    CREATE TABLE IF NOT EXISTS reference (
                        context TEXT PRIMARY KEY,
                        context_id INTEGER
                    );
                """)

        self.svs_conn.commit()

    async def fetch_user_data(self, fid, proxy=None):
        # Bypassed since the endpoint is dead
        return {
            "msg": "success",
            "data": {
                "nickname": "Unknown",
                "stove_lv": 0,
                "kid": "0",
                "avatar_image": ""
            }
        }

    async def send_embed_to_channel(self, embed):
        """Sends the embed message to a specific channel."""
        log_channel_id = await self.get_channel_id("minister log channel")
        log_channel = self.bot.get_channel(log_channel_id)

        if log_channel:
            await log_channel.send(embed=embed)
        else:
            print(f"Error: Could not find the log channel please change it to a valid channel")

    async def is_admin(self, user_id: int) -> bool:
        if await is_bot_owner(self.bot, user_id):
            return True
        self.settings_cursor.execute("SELECT 1 FROM admin WHERE id=?", (user_id,))
        return self.settings_cursor.fetchone() is not None

    async def show_minister_channel_menu(self, interaction: discord.Interaction):
        # Redirect to the MinisterMenu cog
        minister_cog = self.bot.get_cog("MinisterMenu")
        if minister_cog:
            await minister_cog.show_minister_channel_menu(interaction)
        else:
            await interaction.response.send_message(
                "❌ Minister Menu module not found.",
                ephemeral=True
            )

    # Autocomplete handler for appointment type
    async def appointment_autocomplete(self, interaction: discord.Interaction, current: str):
        try:
            choices = [
                discord.app_commands.Choice(name="Construction Day", value="Construction Day"),
                discord.app_commands.Choice(name="Research Day", value="Research Day"),
                discord.app_commands.Choice(name="Troops Training Day", value="Troops Training Day")
            ]
            if current:
                filtered_choices = [choice for choice in choices if current.lower() in choice.name.lower()]
            else:
                filtered_choices = choices

            return filtered_choices
        except Exception as e:
            print(f"Error in appointment type autocomplete: {e}")
            return []

    # Autocomplete handler for names
    async def fid_autocomplete(self, interaction: discord.Interaction, current: str):
        try:
            # Fetch selected appointment type from interaction
            appointment_type = next(
                (option["value"] for option in interaction.data.get("options", []) if option["name"] == "appointment_type"),
                None
            )

            if not appointment_type:
                return []  # If no appointment type is selected, return an empty list

            # Fetch all registered users
            self.users_cursor.execute("SELECT fid, nickname FROM users")
            users = self.users_cursor.fetchall()

            # Fetch users already booked for the selected appointment type
            self.svs_cursor.execute("SELECT fid FROM appointments WHERE appointment_type=?", (appointment_type,))
            booked_fids = {row[0] for row in self.svs_cursor.fetchall()}  # Convert to a set for quick lookup

            # Filter out booked users
            available_choices = [
                discord.app_commands.Choice(name=f"{nickname} ({fid})", value=str(fid))
                for fid, nickname in users if fid not in booked_fids
            ]

            # Apply search filtering if the user is typing
            if current:
                filtered_choices = [choice for choice in available_choices if current.lower() in choice.name.lower()][:25]
            else:
                filtered_choices = available_choices[:25]

            return filtered_choices
        except Exception as e:
            print(f"Autocomplete for fid failed: {e}")
            return []

    # Autocomplete handler for registered names
    async def registered_fid_autocomplete(self, interaction: discord.Interaction, current: str):
        try:
            # Fetch selected appointment type from interaction
            appointment_type = next(
                (option["value"] for option in interaction.data.get("options", []) if option["name"] == "appointment_type"),
                None
            )

            if not appointment_type:
                return []

            # Fetch users already booked for the selected appointment type
            self.svs_cursor.execute("SELECT fid FROM appointments WHERE appointment_type = ?", (appointment_type,))
            fids = [row[0] for row in self.svs_cursor.fetchall()]
            if not fids:
                return []

            placeholders = ",".join("?" for _ in fids)
            query = f"SELECT fid, nickname FROM users WHERE fid IN ({placeholders})"
            self.users_cursor.execute(query, fids)

            registered_users = self.users_cursor.fetchall()

            # Create choices list
            choices = [
                discord.app_commands.Choice(name=f"{nickname} ({fid})", value=str(fid))
                for fid, nickname in registered_users
            ]

            # Apply search filtering if the user is typing
            if current:
                filtered_choices = [choice for choice in choices if current.lower() in choice.name.lower()][:25]
            else:
                filtered_choices = choices[:25]

            return filtered_choices
        except Exception as e:
            print(f"Autocomplete for registered fid failed: {e}")
            return []

    # Autocomplete handler for time
    async def time_autocomplete(self, interaction: discord.Interaction, current: str):
        try:
            appointment_type = next(
                (option["value"] for option in interaction.data.get("options", []) if option["name"] == "appointment_type"),
                None
            )

            if not appointment_type:
                return []

            # Get booked times
            self.svs_cursor.execute("SELECT time FROM appointments WHERE appointment_type=?", (appointment_type,))
            booked_times = {row[0] for row in self.svs_cursor.fetchall()}

            # Generate valid 30-minute interval times in order
            available_times = []
            for hour in range(24):
                for minute in (0, 30):
                    time_slot = f"{hour:02}:{minute:02}"
                    if time_slot not in booked_times:
                        available_times.append(time_slot)

            # Ensure user input is properly formatted (normalize input)
            if current:
                # Normalize single-digit hours (e.g., "1:00" -> "01:00")
                parts = current.split(":")
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    formatted_input = f"{int(parts[0]):02}:{int(parts[1]):02}"
                else:
                    return []  # Invalid format

                # Ensure input is valid 30-minute interval
                valid_times = {f"{hour:02}:{minute:02}" for hour in range(24) for minute in (0, 30)}
                if formatted_input not in valid_times:
                    return []

                # Filter choices based on input
                filtered_choices = [
                    discord.app_commands.Choice(name=time, value=time)
                    for time in available_times if formatted_input in time
                ][:25]
            else:
                filtered_choices = [
                    discord.app_commands.Choice(name=time, value=time)
                    for time in available_times
                ][:25]

            return filtered_choices
        except Exception as e:
            print(f"Error in time autocomplete: {e}")
            return []

    # Autocomplete handler for choices of what to show
    async def choice_autocomplete(self, interaction: discord.Interaction, current: str):
        try:
            choices = [
                discord.app_commands.Choice(name="Show full minister list", value="all"),
                discord.app_commands.Choice(name="Show available slots only", value="available only")
            ]

            if current:
                filtered_choices = [choice for choice in choices if current.lower() in choice.name.lower()]
            else:
                filtered_choices = choices

            return filtered_choices
        except Exception as e:
            print(f"Error in all_or_available autocomplete: {e}")
            return []

    # handler for looping through all times and updating fids to current nickname
    async def update_time_list(self, booked_times, progress_callback=None):
        """
        Generates a list of time slots with their booking details, fetching nicknames from the API.
        Implements rate limit handling and batch processing.
        """
        time_list = []
        booked_fids = {}

        fids_to_fetch = {fid for fid, _ in booked_times.values() if fid}
        fetched_data = {}  # Cache API responses

        for hour in range(24):
            for minute in (0, 30):
                time_slot = f"{hour:02}:{minute:02}"
                booked_fid, booked_alliance = booked_times.get(time_slot, ("", ""))

                booked_nickname = "Unknown"
                if booked_fid:
                    # Check cache first
                    if booked_fid not in fetched_data:
                        while True:
                            if progress_callback:
                                await progress_callback(len(fetched_data), len(fids_to_fetch), waiting=False)

                            data = await self.fetch_user_data(booked_fid)
                            if isinstance(data, dict) and "data" in data:
                                fetched_data[booked_fid] = data["data"].get("nickname", "Unknown")
                                if progress_callback: # Immediate progress update after successful fetch
                                    await progress_callback(len(fetched_data), len(fids_to_fetch), waiting=False)
                                break
                            elif data == 429:
                                if progress_callback:
                                    await progress_callback(len(fetched_data), len(fids_to_fetch), waiting=True)
                                await asyncio.sleep(60) # Rate limit, wait and retry
                            else:
                                fetched_data[booked_fid] = "Unknown"
                                if progress_callback: # Immediate progress update even for failed fetch
                                    await progress_callback(len(fetched_data), len(fids_to_fetch), waiting=False)
                                break

                    booked_nickname = fetched_data.get(booked_fid, "Unknown")

                    # Fetch alliance name
                    self.alliance_cursor.execute("SELECT name FROM alliance_list WHERE alliance_id=?", (booked_alliance,))
                    alliance_data = self.alliance_cursor.fetchone()
                    booked_alliance_name = alliance_data[0] if alliance_data else "Unknown"

                    time_list.append(f"`{time_slot}` - [{booked_alliance_name}]`{booked_nickname}` - `{booked_fid}`")
                else:
                    time_list.append(f"`{time_slot}` - ")

                booked_fids[time_slot] = booked_fid

                # Update progress after processing each time slot
                if progress_callback:
                    await progress_callback(len(fetched_data), len(fids_to_fetch), waiting=False)

        return time_list, booked_fids

    # handler for looping through all times without updating fids
    def generate_time_list(self, booked_times):
        """
        Generates a list of time slots with their booking details.
        """
        time_list = []
        booked_fids = {}
        for hour in range(24):
            for minute in (0, 30):
                time_slot = f"{hour:02}:{minute:02}"
                booked_fid, booked_alliance = booked_times.get(time_slot, ("", ""))
                booked_nickname = ""
                if booked_fid:
                    self.users_cursor.execute("SELECT nickname FROM users WHERE fid=?", (booked_fid,))
                    user = self.users_cursor.fetchone()
                    booked_nickname = user[0] if user else f"ID: {booked_fid}"

                    self.alliance_cursor.execute("SELECT name FROM alliance_list WHERE alliance_id=?", (booked_alliance,))
                    alliance_data = self.alliance_cursor.fetchone()
                    booked_alliance_name = alliance_data[0] if alliance_data else "Unknown"

                    time_list.append(f"`{time_slot}` - [{booked_alliance_name}]`{booked_nickname}` - `{booked_fid}`")
                else:
                    time_list.append(f"`{time_slot}` - ")
                booked_fids[time_slot] = booked_fid

        return time_list, booked_fids

    # handler for looping through available times
    def generate_available_time_list(self, booked_times):
        """
        Generates a list of only available (non-booked) time slots.
        """
        time_list = []
        for hour in range(24):
            for minute in (0, 30):
                time_slot = f"{hour:02}:{minute:02}"
                if time_slot not in booked_times:  # Only add unbooked slots
                    time_list.append(f"`{time_slot}` - ")

        return time_list
    
    # handler for looping through unavailable times
    def generate_booked_time_list(self, booked_times):
        """
        Generates a list of only booked time slots with their details.
        """
        time_list = []
        for hour in range(24):
            for minute in (0, 30):
                time_slot = f"{hour:02}:{minute:02}"
                if time_slot in booked_times:
                    booked_fid, booked_alliance = booked_times[time_slot]
                    booked_nickname = ""
                    if booked_fid:
                        self.users_cursor.execute("SELECT nickname FROM users WHERE fid=?", (booked_fid,))
                        user = self.users_cursor.fetchone()
                        booked_nickname = user[0] if user else f"ID: {booked_fid}"

                        self.alliance_cursor.execute("SELECT name FROM alliance_list WHERE alliance_id=?", (booked_alliance,))
                        alliance_data = self.alliance_cursor.fetchone()
                        booked_alliance_name = alliance_data[0] if alliance_data else "Unknown"

                        time_list.append(f"`{time_slot}` - [{booked_alliance_name}]`{booked_nickname}` - `{booked_fid}`")

        return time_list

    # handler to get minister channel
    async def get_channel_id(self, context: str):
        self.svs_cursor.execute("SELECT context_id FROM reference WHERE context=?", (context,))
        row = self.svs_cursor.fetchone()
        return int(row[0]) if row else None

    # handler to get minister message from channel to edit it
    async def get_or_create_message(self, context: str, message_content: str, channel: discord.TextChannel):
        self.svs_cursor.execute("SELECT context_id FROM reference WHERE context=?", (context,))
        row = self.svs_cursor.fetchone()

        if row:
            message_id = int(row[0])
            try:
                message = await channel.fetch_message(message_id)
                await message.edit(content=message_content)
                return message
            except discord.NotFound:
                pass

        # Send a new message if none found
        new_message = await channel.send(message_content)
        self.svs_cursor.execute(
            "REPLACE INTO reference (context, context_id) VALUES (?, ?)",
            (context, new_message.id)
        )
        self.svs_conn.commit()
        return new_message

    # handler to get guild id
    async def get_log_guild(self, log_guild: discord.Guild) -> Optional[discord.Guild]:
        self.svs_cursor.execute("SELECT context_id FROM reference WHERE context=?", ("minister guild id",))
        row = self.svs_cursor.fetchone()

        if not row:
            # Save the current guild as main guild if not found
            if log_guild:
                self.svs_cursor.execute(
                    "INSERT INTO reference (context, context_id) VALUES (?, ?)",
                    ("minister guild id", log_guild.id)
                )
                self.svs_conn.commit()
                return log_guild
            else:
                return None
        else:
            guild_id = int(row[0])
            guild = self.bot.get_guild(guild_id)
            if guild:
                return guild
            else:
                return None

    @discord.app_commands.command(name='ministerappointment', description='Manage minister appointments.')
    async def ministerappointment(self, interaction: discord.Interaction):
        if not await self.is_admin(interaction.user.id):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        # Redirect to the MinisterMenu cog
        minister_cog = self.bot.get_cog("MinisterMenu")
        if minister_cog:
            await minister_cog.show_minister_channel_menu(interaction)
        else:
            await interaction.response.send_message(
                "❌ Minister Menu module not found.",
                ephemeral=True
            )

async def setup(bot):
    await bot.add_cog(MinisterSchedule(bot))