import discord
from discord.ext import commands
import sqlite3
import asyncio
import time
import hashlib
import aiohttp
from aiohttp_socks import ProxyConnector
from admin_utils import is_bot_owner

SECRET = 'tB87#kPtkxqOS2'

# ─── Colour palette ────────────────────────────────────────────────────────
GOLD     = 0xF5A623   # warm amber – main accent
CRIMSON  = 0xC0392B   # deep red – danger / errors
EMERALD  = 0x1ABC9C   # teal green – success
SLATE    = 0x2C3E50   # dark navy – neutral / info
# ───────────────────────────────────────────────────────────────────────────


class UserFilterModal(discord.ui.Modal, title="Search Players"):
    def __init__(self, parent_view):
        super().__init__()
        self.parent_view = parent_view

        self.filter_input = discord.ui.TextInput(
            label="Search by ID or Name",
            placeholder="Type a player ID or partial name …",
            required=False,
            max_length=100,
            default=self.parent_view.filter_text
        )
        self.add_item(self.filter_input)

    async def on_submit(self, interaction: discord.Interaction):
        self.parent_view.filter_text = self.filter_input.value.strip()
        self.parent_view.page = 0
        self.parent_view.apply_filter()
        self.parent_view.update_select_menu()
        self.parent_view.update_navigation_buttons()
        await self.parent_view.update_embed(interaction)


class FilteredUserSelectView(discord.ui.View):
    def __init__(self, bot, cog, activity_name, users, booked_times, page=0):
        super().__init__(timeout=300)
        self.bot = bot
        self.cog = cog
        self.activity_name = activity_name
        self.users = users
        self.booked_times = booked_times
        self.page = page
        self.filter_text = ""
        self.filtered_users = self.users.copy()
        self.max_page = (len(self.filtered_users) - 1) // 25 if self.filtered_users else 0

        self.booked_fids = {fid for time, (fid, alliance) in self.booked_times.items() if fid}

        self.update_select_menu()
        self.update_navigation_buttons()

    def apply_filter(self):
        if not self.filter_text:
            self.filtered_users = self.users.copy()
        else:
            filter_lower = self.filter_text.lower()
            self.filtered_users = []
            for fid, nickname, alliance_id in self.users:
                if filter_lower in str(fid).lower() or filter_lower in nickname.lower():
                    self.filtered_users.append((fid, nickname, alliance_id))

        self.max_page = (len(self.filtered_users) - 1) // 25 if self.filtered_users else 0
        if self.page > self.max_page:
            self.page = self.max_page

    def update_navigation_buttons(self):
        prev_button  = next((i for i in self.children if hasattr(i, 'custom_id') and i.custom_id == 'prev_page'),  None)
        next_button  = next((i for i in self.children if hasattr(i, 'custom_id') and i.custom_id == 'next_page'),  None)
        clear_button = next((i for i in self.children if hasattr(i, 'custom_id') and i.custom_id == 'clear_filter'), None)

        if prev_button:
            prev_button.disabled = self.page == 0
        if next_button:
            next_button.disabled = self.page >= self.max_page
        if clear_button:
            clear_button.disabled = not bool(self.filter_text)

    def update_select_menu(self):
        for item in self.children[:]:
            if isinstance(item, discord.ui.Select):
                self.remove_item(item)

        start_idx = self.page * 25
        end_idx   = min(start_idx + 25, len(self.filtered_users))
        current_users = self.filtered_users[start_idx:end_idx]

        if not current_users:
            placeholder = "No players found" if self.filter_text else "No players available"
            select = discord.ui.Select(
                placeholder=placeholder,
                options=[discord.SelectOption(label="No players", value="none")],
                disabled=True
            )
        else:
            options = []
            for fid, nickname, alliance_id in current_users:
                emoji  = "🔖" if fid in self.booked_fids else ""
                label  = f"{emoji} {nickname} ({fid})" if emoji else f"{nickname} ({fid})"
                option = discord.SelectOption(label=label[:100], value=str(fid))
                options.append(option)

            select = discord.ui.Select(
                placeholder=f"Choose a player … (Page {self.page + 1}/{self.max_page + 1})",
                options=options,
                min_values=1,
                max_values=1
            )
            select.callback = self.user_select_callback

        self.add_item(select)

    async def user_select_callback(self, interaction: discord.Interaction):
        selected_fid = int(interaction.data['values'][0])
        user_data    = next((u for u in self.users if u[0] == selected_fid), None)
        if not user_data:
            await interaction.response.send_message("❌ Player not found.", ephemeral=True)
            return

        fid, nickname, alliance_id = user_data
        if fid in self.booked_fids:
            current_time = next((t for t, (booked_fid, _) in self.booked_times.items() if booked_fid == fid), None)
            await self.cog.show_time_selection(interaction, self.activity_name, str(fid), current_time)
        else:
            await self.cog.show_time_selection(interaction, self.activity_name, str(fid), None)

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary, custom_id="prev_page", row=1)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self.update_select_menu()
        self.update_navigation_buttons()
        await self.update_embed(interaction)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary, custom_id="next_page", row=1)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(self.max_page, self.page + 1)
        self.update_select_menu()
        self.update_navigation_buttons()
        await self.update_embed(interaction)

    @discord.ui.button(label="Search", style=discord.ButtonStyle.secondary, emoji="🔎", custom_id="filter", row=1)
    async def filter_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = UserFilterModal(self)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Reset", style=discord.ButtonStyle.danger, emoji="✖", custom_id="clear_filter", row=1, disabled=True)
    async def clear_filter_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.filter_text = ""
        self.page = 0
        self.apply_filter()
        self.update_select_menu()
        self.update_navigation_buttons()
        await self.update_embed(interaction)

    @discord.ui.button(label="Roster", style=discord.ButtonStyle.secondary, emoji="📜", custom_id="list", row=1)
    async def list_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.show_current_schedule_list(interaction, self.activity_name)

    @discord.ui.button(label="← Back", style=discord.ButtonStyle.primary, row=2)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.show_minister_channel_menu(interaction)

    async def update_embed(self, interaction: discord.Interaction):
        total_booked   = len(self.booked_fids)
        available_slots = 48 - total_booked

        lines = [
            f"Pick a player to adjust their **{self.activity_name}** slot.\n"
        ]

        if self.filter_text:
            lines.append(f"**Active Search:** `{self.filter_text}`")
            lines.append(f"**Showing:** {len(self.filtered_users)} of {len(self.users)} players\n")

        lines += [
            "**Slot Overview**",
            f"◆  Filled: `{total_booked}/48`",
            f"◇  Open:   `{available_slots}/48`\n",
            "_🔖 = player already has a reservation_"
        ]

        embed = discord.Embed(
            title=f"🗓  {self.activity_name} — Player Roster",
            description="\n".join(lines),
            color=GOLD
        )

        try:
            await interaction.response.edit_message(embed=embed, view=self)
        except discord.InteractionResponded:
            await interaction.edit_original_response(embed=embed, view=self)


class ClearConfirmationView(discord.ui.View):
    def __init__(self, bot, cog, activity_name, is_global_admin, alliance_ids):
        super().__init__(timeout=60)
        self.bot            = bot
        self.cog            = cog
        self.activity_name  = activity_name
        self.is_global_admin = is_global_admin
        self.alliance_ids   = alliance_ids

    @discord.ui.button(label="Yes, wipe them", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()

        minister_schedule_cog = self.bot.get_cog("MinisterSchedule")

        if self.is_global_admin:
            self.cog.svs_cursor.execute("SELECT fid FROM appointments WHERE appointment_type=?", (self.activity_name,))
            cleared_fids  = [row[0] for row in self.cog.svs_cursor.fetchall()]
            self.cog.svs_cursor.execute("DELETE FROM appointments WHERE appointment_type=?", (self.activity_name,))
            self.cog.svs_conn.commit()
            cleared_count = len(cleared_fids)
            message       = f"Wiped all {cleared_count} reservations for **{self.activity_name}**"
        else:
            placeholders = ','.join('?' for _ in self.alliance_ids)
            query = f"SELECT fid FROM appointments WHERE appointment_type=? AND alliance IN ({placeholders})"
            self.cog.svs_cursor.execute(query, [self.activity_name] + self.alliance_ids)
            cleared_fids  = [row[0] for row in self.cog.svs_cursor.fetchall()]
            query = f"DELETE FROM appointments WHERE appointment_type=? AND alliance IN ({placeholders})"
            self.cog.svs_cursor.execute(query, [self.activity_name] + self.alliance_ids)
            self.cog.svs_conn.commit()
            cleared_count = len(cleared_fids)
            message       = f"Wiped {cleared_count} alliance reservations for **{self.activity_name}**"

        if minister_schedule_cog and cleared_count > 0:
            embed = discord.Embed(
                title=f"Reservations Wiped — {self.activity_name}",
                description=f"{cleared_count} reservations removed",
                color=CRIMSON
            )
            embed.set_author(
                name=f"Action by {interaction.user.display_name}",
                icon_url=interaction.user.avatar.url if interaction.user.avatar else None
            )
            await minister_schedule_cog.send_embed_to_channel(embed)
            await self.cog.update_channel_message(self.activity_name)

        embed = discord.Embed(
            title="🔧  Admin Panel",
            description=(
                f"✅ {message}\n\n"
                "**What would you like to do next?**\n\n"
                "◆ **Sync Names** — pull latest nicknames from the game API\n"
                "◆ **Wipe Reservations** — erase all slots for a given event\n"
                "◆ **Reset Channels** — unlink configured channels\n"
                "◆ **Unlink Server** — remove the registered server record\n"
            ),
            color=EMERALD
        )
        view = MinisterSettingsView(self.cog.bot, self.cog)
        await interaction.followup.send(embed=embed, view=view)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="↩️")
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.show_filtered_user_select(interaction, self.activity_name)


class ActivitySelectView(discord.ui.View):
    def __init__(self, bot, cog, action_type):
        super().__init__(timeout=60)
        self.bot         = bot
        self.cog         = cog
        self.action_type = action_type

    @discord.ui.select(
        placeholder="Pick an event …",
        options=[
            discord.SelectOption(label="Construction Day",    value="Construction Day",    emoji="🏗️"),
            discord.SelectOption(label="Research Day",        value="Research Day",        emoji="🧪"),
            discord.SelectOption(label="Troops Training Day", value="Troops Training Day", emoji="🛡️"),
        ]
    )
    async def activity_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        activity_name = select.values[0]
        if self.action_type == "update_names":
            await self.cog.update_minister_names(interaction, activity_name)
        elif self.action_type == "clear_reservations":
            await self.cog.show_clear_confirmation(interaction, activity_name)

    @discord.ui.button(label="← Back", style=discord.ButtonStyle.primary, emoji="↩️")
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.show_settings_menu(interaction)


class MinisterSettingsView(discord.ui.View):
    def __init__(self, bot, cog):
        super().__init__(timeout=None)
        self.bot = bot
        self.cog = cog

    @discord.ui.button(label="Sync Names", style=discord.ButtonStyle.secondary, emoji="🔄")
    async def update_names(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.cog.is_admin(interaction.user.id):
            await interaction.response.send_message("❌ You lack the required permissions.", ephemeral=True)
            return
        await self.cog.show_activity_selection_for_update(interaction)

    @discord.ui.button(label="Wipe Reservations", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def clear_reservations(self, interaction: discord.Interaction, button: discord.ui.Button):
        is_admin, is_global_admin, _ = await self.cog.get_admin_permissions(interaction.user.id)
        if not is_global_admin:
            await interaction.response.send_message("❌ Global Admin access required.", ephemeral=True)
            return
        await self.cog.show_activity_selection_for_clear(interaction)

    @discord.ui.button(label="Reset Channels", style=discord.ButtonStyle.danger, emoji="📡", row=1)
    async def clear_channels(self, interaction: discord.Interaction, button: discord.ui.Button):
        is_admin, is_global_admin, _ = await self.cog.get_admin_permissions(interaction.user.id)
        if not is_global_admin:
            await interaction.response.send_message("❌ Global Admin access required.", ephemeral=True)
            return
        await self.cog.show_clear_channels_selection(interaction)

    @discord.ui.button(label="Unlink Server", style=discord.ButtonStyle.danger, emoji="🔓", row=1)
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        is_admin, is_global_admin, _ = await self.cog.get_admin_permissions(interaction.user.id)
        if not is_global_admin:
            await interaction.response.send_message("❌ Global Admin access required.", ephemeral=True)
            return
        try:
            svs_conn   = sqlite3.connect("db/svs.sqlite")
            svs_cursor = svs_conn.cursor()
            svs_cursor.execute("DELETE FROM reference WHERE context=?", ("minister guild id",))
            svs_conn.commit()
            svs_conn.close()
            await interaction.response.send_message("✅ Server record removed successfully.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to unlink server: {e}", ephemeral=True)

    @discord.ui.button(label="← Main Menu", style=discord.ButtonStyle.primary, emoji="🏠", row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.show_minister_channel_menu(interaction)


class MinisterChannelView(discord.ui.View):
    def __init__(self, bot, cog):
        super().__init__(timeout=None)
        self.bot = bot
        self.cog = cog

    @discord.ui.button(label="Construction Day", style=discord.ButtonStyle.primary, emoji="🏗️")
    async def construction_day(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_activity_selection(interaction, "Construction Day")

    @discord.ui.button(label="Research Day", style=discord.ButtonStyle.primary, emoji="🧪")
    async def research_day(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_activity_selection(interaction, "Research Day")

    @discord.ui.button(label="Troops Training Day", style=discord.ButtonStyle.primary, emoji="🛡️")
    async def troops_training_day(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_activity_selection(interaction, "Troops Training Day")

    @discord.ui.button(label="Channel Config", style=discord.ButtonStyle.success, emoji="📡", row=1)
    async def channel_setup(self, interaction: discord.Interaction, button: discord.ui.Button):
        is_admin, is_global_admin, _ = await self.cog.get_admin_permissions(interaction.user.id)
        if not is_global_admin:
            await interaction.response.send_message("❌ Global Admin access required.", ephemeral=True)
            return
        await self.cog.show_channel_setup_menu(interaction)

    @discord.ui.button(label="Admin Panel", style=discord.ButtonStyle.secondary, emoji="🔧", row=1)
    async def settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.show_settings_menu(interaction)

    @discord.ui.button(label="← Exit", style=discord.ButtonStyle.secondary, emoji="🚪", row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            other_features_cog = self.cog.bot.get_cog("OtherFeatures")
            if other_features_cog:
                await other_features_cog.show_other_features_menu(interaction)
            else:
                await interaction.response.send_message(
                    "❌ Other Features module not found.",
                    ephemeral=True
                )
        except Exception as e:
            await interaction.response.send_message(
                f"❌ Error returning to Other Features: {e}",
                ephemeral=True
            )

    async def _handle_activity_selection(self, interaction: discord.Interaction, activity_name: str):
        minister_schedule_cog = self.cog.bot.get_cog("MinisterSchedule")
        if not minister_schedule_cog:
            await interaction.response.send_message("❌ Minister Schedule module not found.", ephemeral=True)
            return

        log_guild = await minister_schedule_cog.get_log_guild(interaction.guild)
        if not log_guild:
            await interaction.response.send_message(
                "Could not find the minister log server. Make sure the bot is in that server.\n\nIf issue persists, run the `/settings` command → Other Features → Minister Scheduling → Delete Server ID and try again in the desired server",
                ephemeral=True
            )
            return

        if interaction.guild.id != log_guild.id:
            await interaction.response.send_message(
                f"This menu must be used in the configured server: `{log_guild}`.\n\n"
                "If you want to change the server, run `/settings` command → Other Features → Minister Scheduling → Delete Server ID and try again in the desired server",
                ephemeral=True
            )
            return

        await self.cog.show_filtered_user_select(interaction, activity_name)


class ChannelConfigurationView(discord.ui.View):
    def __init__(self, bot, cog):
        super().__init__(timeout=None)
        self.bot = bot
        self.cog = cog

    @discord.ui.button(label="Construction Channel", style=discord.ButtonStyle.secondary, emoji="🏗️")
    async def construction_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_channel_selection(interaction, "Construction Day channel", "Construction Day")

    @discord.ui.button(label="Research Channel", style=discord.ButtonStyle.secondary, emoji="🧪")
    async def research_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_channel_selection(interaction, "Research Day channel", "Research Day")

    @discord.ui.button(label="Training Channel", style=discord.ButtonStyle.secondary, emoji="🛡️")
    async def training_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_channel_selection(interaction, "Troops Training Day channel", "Troops Training Day")

    @discord.ui.button(label="Audit Log Channel", style=discord.ButtonStyle.secondary, emoji="📋")
    async def log_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_channel_selection(interaction, "minister log channel", "general logging")

    @discord.ui.button(label="← Back", style=discord.ButtonStyle.primary, emoji="↩️", row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.show_minister_channel_menu(interaction)

    async def _handle_channel_selection(self, interaction: discord.Interaction, channel_context: str, activity_name: str):
        minister_schedule_cog = self.cog.bot.get_cog("MinisterSchedule")
        if not minister_schedule_cog:
            await interaction.response.send_message("❌ Minister Schedule module not found.", ephemeral=True)
            return

        import sys
        minister_module = minister_schedule_cog.__class__.__module__
        ChannelSelect   = getattr(sys.modules[minister_module], 'ChannelSelect')

        class ChannelSelectWithBackView(discord.ui.View):
            def __init__(self, bot, context, cog):
                super().__init__(timeout=None)
                self.bot     = bot
                self.context = context
                self.cog     = cog
                self.add_item(ChannelSelect(bot, context))

            @discord.ui.button(label="← Back", style=discord.ButtonStyle.primary, emoji="↩️", row=1)
            async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                embed = discord.Embed(
                    title="📡  Channel Configuration",
                    description=(
                        "Map each event to its dedicated Discord channel.\n\n"
                        "**Available slots to configure:**\n\n"
                        "🏗️  **Construction Channel** — posts open Construction Day slots\n"
                        "🧪  **Research Channel** — posts open Research Day slots\n"
                        "🛡️  **Training Channel** — posts open Training Day slots\n"
                        "📋  **Audit Log Channel** — receives add/remove notifications\n\n"
                        "_Select a button below to begin:_"
                    ),
                    color=GOLD
                )
                import sys
                minister_menu_module       = self.cog.__class__.__module__
                ChannelConfigurationView_  = getattr(sys.modules[minister_menu_module], 'ChannelConfigurationView')
                view = ChannelConfigurationView_(self.bot, self.cog)
                await interaction.response.edit_message(content=None, embed=embed, view=view)

        await interaction.response.edit_message(
            content=f"Pick a channel to assign for **{activity_name}**:",
            view=ChannelSelectWithBackView(self.bot, channel_context, self.cog),
            embed=None
        )


class TimeSelectView(discord.ui.View):
    def __init__(self, bot, cog, activity_name, fid, available_times, current_time=None):
        super().__init__(timeout=300)
        self.bot             = bot
        self.cog             = cog
        self.activity_name   = activity_name
        self.fid             = fid
        self.available_times = available_times
        self.current_time    = current_time

        self.add_item(TimeSelect(available_times))

        if current_time:
            clear_button          = discord.ui.Button(label="Release Slot", style=discord.ButtonStyle.danger, emoji="🔓", row=1)
            clear_button.callback = self.clear_reservation_callback
            self.add_item(clear_button)

        back_button          = discord.ui.Button(label="← Back", style=discord.ButtonStyle.secondary, emoji="↩️", row=1)
        back_button.callback = self.back_button_callback
        self.add_item(back_button)

    async def back_button_callback(self, interaction: discord.Interaction):
        await self.cog.show_filtered_user_select(interaction, self.activity_name)

    async def clear_reservation_callback(self, interaction: discord.Interaction):
        await self.cog.clear_user_reservation(interaction, self.activity_name, self.fid, self.current_time)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


class TimeSelect(discord.ui.Select):
    def __init__(self, available_times):
        options = []
        for time_slot in available_times[:25]:
            options.append(discord.SelectOption(label=time_slot, value=time_slot))

        super().__init__(
            placeholder="Choose a time slot …",
            options=options,
            min_values=1,
            max_values=1
        )

    async def callback(self, interaction: discord.Interaction):
        selected_time = self.values[0]
        minister_cog  = self.view.cog
        await minister_cog.complete_booking(interaction, self.view.activity_name, self.view.fid, selected_time)


class MinisterMenu(commands.Cog):
    def __init__(self, bot):
        self.bot            = bot
        self.users_conn     = sqlite3.connect('db/users.sqlite')
        self.users_cursor   = self.users_conn.cursor()
        self.alliance_conn  = sqlite3.connect('db/alliance.sqlite')
        self.alliance_cursor = self.alliance_conn.cursor()
        self.svs_conn       = sqlite3.connect("db/svs.sqlite")
        self.svs_cursor     = self.svs_conn.cursor()
        self.original_interaction = None

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

    async def is_admin(self, user_id: int) -> bool:
        settings_conn   = sqlite3.connect('db/settings.sqlite')
        settings_cursor = settings_conn.cursor()
        if await is_bot_owner(self.bot, user_id):
            settings_conn.close()
            return True
        settings_cursor.execute("SELECT 1 FROM admin WHERE id=?", (user_id,))
        result = settings_cursor.fetchone() is not None
        settings_conn.close()
        return result

    async def show_minister_channel_menu(self, interaction: discord.Interaction):
        self.original_interaction = interaction

        embed = discord.Embed(
            title="⚔️  Alliance Event Scheduler",
            description=(
                "Manage which players lead your alliance events.\n\n"
                "**Select an event to manage:**\n\n"
                "🏗️  **Construction Day** — assign and review build slots\n"
                "🧪  **Research Day** — assign and review research slots\n"
                "🛡️  **Troops Training Day** — assign and review training slots\n\n"
                "**Admin tools:**\n\n"
                "📡  **Channel Config** — link events to Discord channels\n"
                "🔧  **Admin Panel** — sync names, wipe slots, manage server\n"
            ),
            color=GOLD
        )
        embed.set_footer(text="Only authorized staff can modify appointments.")

        view = MinisterChannelView(self.bot, self)
        try:
            if interaction.type == discord.InteractionType.application_command:
                await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            else:
                await interaction.response.edit_message(embed=embed, view=view)
        except discord.InteractionResponded:
            await interaction.edit_original_response(embed=embed, view=view)

    async def show_channel_setup_menu(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="📡  Channel Configuration",
            description=(
                "Map each event to its dedicated Discord channel.\n\n"
                "**Available slots to configure:**\n\n"
                "🏗️  **Construction Channel** — posts open Construction Day slots\n"
                "🧪  **Research Channel** — posts open Research Day slots\n"
                "🛡️  **Training Channel** — posts open Training Day slots\n"
                "📋  **Audit Log Channel** — receives add/remove notifications\n\n"
                "_Select a button below to begin:_"
            ),
            color=GOLD
        )
        view = ChannelConfigurationView(self.bot, self)
        try:
            await interaction.response.edit_message(embed=embed, view=view)
        except discord.InteractionResponded:
            await interaction.edit_original_response(embed=embed, view=view)

    async def get_admin_permissions(self, user_id: int):
        self.settings_cursor = sqlite3.connect('db/settings.sqlite').cursor()
        if await is_bot_owner(self.bot, user_id):
            return True, True, []

        self.settings_cursor.execute("SELECT is_initial FROM admin WHERE id=?", (user_id,))
        admin_result = self.settings_cursor.fetchone()
        if not admin_result:
            return False, False, []

        is_global_admin = admin_result[0] == 1
        if is_global_admin:
            return True, True, []

        self.settings_cursor.execute("SELECT alliances_id FROM adminserver WHERE admin=?", (user_id,))
        alliance_permissions = self.settings_cursor.fetchall()
        alliance_ids = [row[0] for row in alliance_permissions] if alliance_permissions else []
        return True, False, alliance_ids

    async def get_users_for_admin(self, user_id: int):
        is_admin, is_global_admin, alliance_ids = await self.get_admin_permissions(user_id)
        if not is_admin:
            return []

        if is_global_admin:
            self.users_cursor.execute("SELECT fid, nickname, alliance FROM users ORDER BY LOWER(nickname)")
            return self.users_cursor.fetchall()
        else:
            if not alliance_ids:
                return []
            placeholders = ','.join('?' for _ in alliance_ids)
            query = f"SELECT fid, nickname, alliance FROM users WHERE alliance IN ({placeholders}) ORDER BY LOWER(nickname)"
            self.users_cursor.execute(query, alliance_ids)
            return self.users_cursor.fetchall()

    async def show_filtered_user_select(self, interaction: discord.Interaction, activity_name: str):
        is_admin, is_global_admin, alliance_ids = await self.get_admin_permissions(interaction.user.id)
        if not is_admin:
            await interaction.response.send_message("❌ You lack permission to manage event slots.", ephemeral=True)
            return

        users = await self.get_users_for_admin(interaction.user.id)
        if not users:
            await interaction.response.send_message("❌ No players found in your allowed alliances.", ephemeral=True)
            return

        self.svs_cursor.execute("SELECT time, fid, alliance FROM appointments WHERE appointment_type=?", (activity_name,))
        booked_times = {row[0]: (row[1], row[2]) for row in self.svs_cursor.fetchall()}

        view = FilteredUserSelectView(self.bot, self, activity_name, users, booked_times)
        await view.update_embed(interaction)

    async def show_current_schedule_list(self, interaction: discord.Interaction, activity_name: str):
        await interaction.response.defer()

        self.svs_cursor.execute(
            "SELECT time, fid, alliance FROM appointments WHERE appointment_type=? ORDER BY time",
            (activity_name,)
        )
        bookings = self.svs_cursor.fetchall()

        if not bookings:
            embed = discord.Embed(
                title=f"📜  {activity_name} — Current Roster",
                description="No slots have been reserved yet.",
                color=SLATE
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        booking_lines = []
        for time_val, fid, alliance_id in bookings:
            self.users_cursor.execute("SELECT nickname FROM users WHERE fid=?", (fid,))
            user_result  = self.users_cursor.fetchone()
            nickname     = user_result[0] if user_result else f"Unknown ({fid})"

            self.alliance_cursor.execute("SELECT name FROM alliance_list WHERE alliance_id=?", (alliance_id,))
            alliance_result = self.alliance_cursor.fetchone()
            alliance_name   = alliance_result[0] if alliance_result else "Unknown"

            booking_lines.append(f"`{time_val}` — [{alliance_name}] {nickname} ({fid})")

        embed = discord.Embed(
            title=f"📜  {activity_name} — Current Roster",
            description="\n".join(booking_lines),
            color=SLATE
        )
        embed.set_footer(text=f"Total reservations: {len(bookings)}/48")
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def show_filtered_user_select_with_message(
        self, interaction: discord.Interaction, activity_name: str, message: str, is_error: bool = False
    ):
        users = await self.get_users_for_admin(interaction.user.id)
        if not users:
            await interaction.response.send_message("❌ No players found in your allowed alliances.", ephemeral=True)
            return

        self.svs_cursor.execute("SELECT time, fid, alliance FROM appointments WHERE appointment_type=?", (activity_name,))
        booked_times = {row[0]: (row[1], row[2]) for row in self.svs_cursor.fetchall()}

        view         = FilteredUserSelectView(self.bot, self, activity_name, users, booked_times)
        total_booked  = len({fid for _, (fid, _) in booked_times.items() if fid})
        available_slots = 48 - total_booked

        status_emoji = "❌" if is_error else "✅"
        lines = [
            f"{status_emoji} **{message}**\n",
            f"Pick a player to adjust their **{activity_name}** slot.\n",
        ]
        if view.filter_text:
            lines.append(f"**Active Search:** `{view.filter_text}`")
            lines.append(f"**Showing:** {len(view.filtered_users)} of {len(view.users)} players\n")

        lines += [
            "**Slot Overview**",
            f"◆  Filled: `{total_booked}/48`",
            f"◇  Open:   `{available_slots}/48`\n",
            "_🔖 = player already has a reservation_"
        ]

        embed = discord.Embed(
            title=f"🗓  {activity_name} — Player Roster",
            description="\n".join(lines),
            color=CRIMSON if is_error else EMERALD
        )

        try:
            await interaction.edit_original_response(embed=embed, view=view)
        except Exception:
            await interaction.followup.send(embed=embed, view=view)

    async def update_minister_names(self, interaction: discord.Interaction, activity_name: str):
        await interaction.response.defer()

        self.svs_cursor.execute("SELECT DISTINCT fid FROM appointments WHERE appointment_type=?", (activity_name,))
        fids = [row[0] for row in self.svs_cursor.fetchall()]

        if not fids:
            await interaction.followup.send("❌ No reservations to sync.", ephemeral=True)
            return

        updated_count = 0
        failed_count  = 0

        for fid in fids:
            try:
                data = await self.fetch_user_data(fid)
                if data and isinstance(data, dict) and "data" in data:
                    new_nickname = data["data"].get("nickname", "")
                    if new_nickname:
                        self.users_cursor.execute("UPDATE users SET nickname=? WHERE fid=?", (new_nickname, fid))
                        self.users_conn.commit()
                        updated_count += 1
                    else:
                        failed_count += 1
                else:
                    failed_count += 1
            except Exception as e:
                print(f"Error syncing nickname for FID {fid}: {e}")
                failed_count += 1

        result_msg = f"Synced {updated_count} name(s) for **{activity_name}**"
        if failed_count > 0:
            result_msg += f" ({failed_count} failed)"

        embed = discord.Embed(
            title="🔧  Admin Panel",
            description=(
                f"✅ **{result_msg}**\n\n"
                "**What would you like to do next?**\n\n"
                "◆ **Sync Names** — pull latest nicknames from the game API\n"
                "◆ **Wipe Reservations** — erase all slots for a given event\n"
                "◆ **Reset Channels** — unlink configured channels\n"
                "◆ **Unlink Server** — remove the registered server record\n"
            ),
            color=EMERALD
        )
        view = MinisterSettingsView(self.bot, self)
        await interaction.followup.send(embed=embed, view=view)

    async def show_clear_confirmation(self, interaction: discord.Interaction, activity_name: str):
        is_admin, is_global_admin, alliance_ids = await self.get_admin_permissions(interaction.user.id)

        if is_global_admin:
            self.svs_cursor.execute("SELECT COUNT(*) FROM appointments WHERE appointment_type=?", (activity_name,))
            count = self.svs_cursor.fetchone()[0]
            embed = discord.Embed(
                title="⚠️  Confirm: Wipe All Reservations",
                description=(
                    f"You are about to erase **all {count} reservation(s)** for **{activity_name}**.\n\n"
                    "This cannot be undone. Are you sure?"
                ),
                color=CRIMSON
            )
        else:
            if not alliance_ids:
                await interaction.response.send_message("❌ You don't have permission to wipe reservations.", ephemeral=True)
                return
            placeholders = ','.join('?' for _ in alliance_ids)
            query = f"SELECT COUNT(*) FROM appointments WHERE appointment_type=? AND alliance IN ({placeholders})"
            self.svs_cursor.execute(query, [activity_name] + alliance_ids)
            count = self.svs_cursor.fetchone()[0]
            embed = discord.Embed(
                title="⚠️  Confirm: Wipe Alliance Reservations",
                description=(
                    f"You are about to erase **{count} reservation(s)** for your alliance(s) in **{activity_name}**.\n\n"
                    "This cannot be undone. Are you sure?"
                ),
                color=CRIMSON
            )

        view = ClearConfirmationView(self.bot, self, activity_name, is_global_admin, alliance_ids)
        try:
            await interaction.response.edit_message(embed=embed, view=view)
        except discord.InteractionResponded:
            await interaction.edit_original_response(embed=embed, view=view)

    async def show_time_selection(self, interaction: discord.Interaction, activity_name: str, fid: str, current_time: str = None):
        self.svs_cursor.execute("SELECT time FROM appointments WHERE appointment_type=?", (activity_name,))
        booked_times = {row[0] for row in self.svs_cursor.fetchall()}

        available_times = []
        for hour in range(24):
            for minute in (0, 30):
                time_slot = f"{hour:02}:{minute:02}"
                if time_slot not in booked_times or time_slot == current_time:
                    available_times.append(time_slot)

        if not available_times:
            await interaction.response.send_message(
                f"❌ All time slots for {activity_name} are taken.",
                ephemeral=True
            )
            return

        self.users_cursor.execute("SELECT nickname FROM users WHERE fid=?", (fid,))
        user_data = self.users_cursor.fetchone()
        nickname  = user_data[0] if user_data else f"ID: {fid}"

        desc = f"Pick an available slot for **{nickname}** in **{activity_name}**:"
        if current_time:
            desc += f"\n\n**Current slot:** `{current_time}`"
            desc += "\n\nChoosing a new time will move the existing reservation."

        embed = discord.Embed(
            title=f"🕐  Assign Slot — {nickname}",
            description=desc,
            color=GOLD
        )
        view = TimeSelectView(self.bot, self, activity_name, fid, available_times, current_time)
        try:
            await interaction.response.edit_message(embed=embed, view=view)
        except discord.InteractionResponded:
            await interaction.edit_original_response(embed=embed, view=view)

    async def complete_booking(self, interaction: discord.Interaction, activity_name: str, fid: str, selected_time: str):
        try:
            if not interaction.response.is_done():
                await interaction.response.defer()

            self.svs_cursor.execute(
                "SELECT time FROM appointments WHERE fid=? AND appointment_type=?",
                (fid, activity_name)
            )
            existing_booking = self.svs_cursor.fetchone()

            if existing_booking:
                old_time = existing_booking[0]
                self.svs_cursor.execute("DELETE FROM appointments WHERE fid=? AND appointment_type=?", (fid, activity_name))

            self.svs_cursor.execute(
                "SELECT fid FROM appointments WHERE appointment_type=? AND time=?",
                (activity_name, selected_time)
            )
            conflicting_booking = self.svs_cursor.fetchone()
            if conflicting_booking:
                booked_fid = conflicting_booking[0]
                self.users_cursor.execute("SELECT nickname FROM users WHERE fid=?", (booked_fid,))
                booked_user     = self.users_cursor.fetchone()
                booked_nickname = booked_user[0] if booked_user else "Unknown"

                if existing_booking:
                    self.svs_cursor.execute("SELECT alliance FROM users WHERE fid=?", (fid,))
                    user_alliance = self.svs_cursor.fetchone()
                    if user_alliance:
                        self.svs_cursor.execute(
                            "INSERT INTO appointments (fid, appointment_type, time, alliance) VALUES (?, ?, ?, ?)",
                            (fid, activity_name, old_time, user_alliance[0])
                        )
                        self.svs_conn.commit()

                error_msg = f"Slot {selected_time} for {activity_name} is already held by {booked_nickname}"
                await self.show_filtered_user_select_with_message(interaction, activity_name, error_msg, is_error=True)
                return

            self.users_cursor.execute("SELECT alliance, nickname FROM users WHERE fid=?", (fid,))
            user_data = self.users_cursor.fetchone()
            if not user_data:
                await interaction.response.send_message(f"❌ Player {fid} is not registered.", ephemeral=True)
                return

            alliance_id, nickname = user_data

            self.alliance_cursor.execute("SELECT name FROM alliance_list WHERE alliance_id=?", (alliance_id,))
            alliance_result = self.alliance_cursor.fetchone()
            alliance_name   = alliance_result[0] if alliance_result else "Unknown"

            self.svs_cursor.execute(
                "INSERT INTO appointments (fid, appointment_type, time, alliance) VALUES (?, ?, ?, ?)",
                (fid, activity_name, selected_time, alliance_id)
            )
            self.svs_conn.commit()

            try:
                data = await self.fetch_user_data(fid)
                if isinstance(data, int) and data == 429:
                    avatar_image = "https://gof-formal-avatar.akamaized.net/avatar-dev/2023/07/17/1001.png"
                elif data and "data" in data and "avatar_image" in data["data"]:
                    avatar_image = data["data"]["avatar_image"]
                else:
                    avatar_image = "https://gof-formal-avatar.akamaized.net/avatar-dev/2023/07/17/1001.png"
            except Exception:
                avatar_image = "https://gof-formal-avatar.akamaized.net/avatar-dev/2023/07/17/1001.png"

            minister_schedule_cog = self.bot.get_cog("MinisterSchedule")
            if minister_schedule_cog:
                if existing_booking:
                    embed = discord.Embed(
                        title=f"Slot Moved — {activity_name}",
                        description=f"{nickname} ({fid}) from **{alliance_name}** moved from {old_time} → {selected_time}",
                        color=GOLD
                    )
                else:
                    embed = discord.Embed(
                        title=f"Slot Reserved — {activity_name}",
                        description=f"{nickname} ({fid}) from **{alliance_name}** assigned to {selected_time}",
                        color=EMERALD
                    )
                embed.set_thumbnail(url=avatar_image)
                embed.set_author(
                    name=f"Actioned by {interaction.user.display_name}",
                    icon_url=interaction.user.avatar.url if interaction.user.avatar else None
                )
                await minister_schedule_cog.send_embed_to_channel(embed)
                await self.update_channel_message(activity_name)

            if existing_booking:
                success_msg = f"Moved {nickname} from {old_time} → {selected_time}"
            else:
                success_msg = f"Reserved slot {selected_time} for {nickname} in {activity_name}"

            await self.show_filtered_user_select_with_message(interaction, activity_name, success_msg)

        except Exception as e:
            try:
                error_msg = f"❌ Booking error: {e}"
                await interaction.followup.send(error_msg, ephemeral=True)
            except Exception:
                print(f"Failed to show booking error: {e}")

    async def update_channel_message(self, activity_name: str):
        try:
            minister_schedule_cog = self.bot.get_cog("MinisterSchedule")
            if not minister_schedule_cog:
                return

            self.svs_cursor.execute(
                "SELECT time, fid, alliance FROM appointments WHERE appointment_type=?",
                (activity_name,)
            )
            booked_times    = {row[0]: (row[1], row[2]) for row in self.svs_cursor.fetchall()}
            time_list       = minister_schedule_cog.generate_available_time_list(booked_times)
            context         = f"{activity_name}"
            channel_context = f"{activity_name} channel"
            message_content = f"**{activity_name}** open slots:\n" + "\n".join(time_list)

            channel_id = await minister_schedule_cog.get_channel_id(channel_context)
            if channel_id:
                log_guild = await minister_schedule_cog.get_log_guild(None)
                if log_guild:
                    channel = log_guild.get_channel(channel_id)
                    if channel:
                        await minister_schedule_cog.get_or_create_message(context, message_content, channel)
        except Exception as e:
            print(f"Error updating channel message: {e}")

    async def clear_user_reservation(self, interaction: discord.Interaction, activity_name: str, fid: str, current_time: str):
        try:
            if not interaction.response.is_done():
                await interaction.response.defer()

            self.users_cursor.execute("SELECT nickname, alliance FROM users WHERE fid=?", (fid,))
            user_data = self.users_cursor.fetchone()
            if not user_data:
                await interaction.followup.send("❌ Player not found.", ephemeral=True)
                return

            nickname, alliance_id = user_data

            self.alliance_cursor.execute("SELECT name FROM alliance_list WHERE alliance_id=?", (alliance_id,))
            alliance_result = self.alliance_cursor.fetchone()
            alliance_name   = alliance_result[0] if alliance_result else "Unknown"

            self.svs_cursor.execute(
                "DELETE FROM appointments WHERE fid=? AND appointment_type=? AND time=?",
                (fid, activity_name, current_time)
            )
            self.svs_conn.commit()

            try:
                data = await self.fetch_user_data(fid)
                if isinstance(data, int) and data == 429:
                    avatar_image = "https://gof-formal-avatar.akamaized.net/avatar-dev/2023/07/17/1001.png"
                elif data and "data" in data and "avatar_image" in data["data"]:
                    avatar_image = data["data"]["avatar_image"]
                else:
                    avatar_image = "https://gof-formal-avatar.akamaized.net/avatar-dev/2023/07/17/1001.png"
            except Exception:
                avatar_image = "https://gof-formal-avatar.akamaized.net/avatar-dev/2023/07/17/1001.png"

            minister_schedule_cog = self.bot.get_cog("MinisterSchedule")
            if minister_schedule_cog:
                embed = discord.Embed(
                    title=f"Slot Released — {activity_name}",
                    description=f"{nickname} ({fid}) from **{alliance_name}** removed from slot {current_time}",
                    color=CRIMSON
                )
                embed.set_thumbnail(url=avatar_image)
                embed.set_author(
                    name=f"Actioned by {interaction.user.display_name}",
                    icon_url=interaction.user.avatar.url if interaction.user.avatar else None
                )
                await minister_schedule_cog.send_embed_to_channel(embed)
                await self.update_channel_message(activity_name)

            success_msg = f"Released {nickname}'s reservation at {current_time}"
            await self.show_filtered_user_select_with_message(interaction, activity_name, success_msg)

        except Exception as e:
            try:
                error_msg = f"❌ Error releasing slot: {e}"
                await interaction.followup.send(error_msg, ephemeral=True)
            except Exception:
                print(f"Failed to show slot release error: {e}")

    async def show_clear_channels_selection(self, interaction: discord.Interaction):
        class ClearChannelsConfirmView(discord.ui.View):
            def __init__(self, parent_cog):
                super().__init__(timeout=60)
                self.parent_cog = parent_cog

            @discord.ui.select(
                placeholder="Choose channels to reset …",
                options=[
                    discord.SelectOption(label="Construction Channel",    value="Construction Day",    emoji="🏗️"),
                    discord.SelectOption(label="Research Channel",        value="Research Day",        emoji="🧪"),
                    discord.SelectOption(label="Training Channel",        value="Troops Training Day", emoji="🛡️"),
                    discord.SelectOption(label="Audit Log Channel",       value="minister log",        emoji="📋"),
                    discord.SelectOption(label="Reset All Channels",      value="ALL",                 emoji="🔄", description="Unlink every configured channel"),
                ],
                min_values=1,
                max_values=5
            )
            async def select_channels(self, interaction: discord.Interaction, select: discord.ui.Select):
                try:
                    await interaction.response.defer()
                    cleared_channels = []
                    svs_conn   = sqlite3.connect("db/svs.sqlite")
                    svs_cursor = svs_conn.cursor()

                    for value in select.values:
                        if value == "ALL":
                            for activity in ["Construction Day", "Research Day", "Troops Training Day"]:
                                await self._clear_channel_config(svs_cursor, activity, interaction.guild)
                                cleared_channels.append(f"{activity} channel")
                            svs_cursor.execute("DELETE FROM reference WHERE context=?", ("minister log channel",))
                            cleared_channels.append("Audit Log channel")
                        else:
                            if value == "minister log":
                                svs_cursor.execute("DELETE FROM reference WHERE context=?", ("minister log channel",))
                                cleared_channels.append("Audit Log channel")
                            else:
                                await self._clear_channel_config(svs_cursor, value, interaction.guild)
                                cleared_channels.append(f"{value} channel")

                    svs_conn.commit()
                    svs_conn.close()

                    success_message = "Reset the following channel links:\n" + "\n".join([f"◆ {ch}" for ch in cleared_channels])

                    embed = discord.Embed(
                        title="🔧  Admin Panel",
                        description=(
                            f"✅ **{success_message}**\n\n"
                            "**What would you like to do next?**\n\n"
                            "◆ **Sync Names** — pull latest nicknames from the game API\n"
                            "◆ **Wipe Reservations** — erase all slots for a given event\n"
                            "◆ **Reset Channels** — unlink configured channels\n"
                            "◆ **Unlink Server** — remove the registered server record\n"
                        ),
                        color=EMERALD
                    )
                    view = MinisterSettingsView(self.parent_cog.bot, self.parent_cog)
                    await interaction.followup.edit_message(message_id=interaction.message.id, embed=embed, view=view)

                except Exception as e:
                    await interaction.followup.send(f"❌ Error resetting channels: {e}", ephemeral=True)

            async def _clear_channel_config(self, svs_cursor, activity_name, guild):
                channel_context = f"{activity_name} channel"
                svs_cursor.execute("SELECT context_id FROM reference WHERE context=?", (channel_context,))
                channel_row = svs_cursor.fetchone()
                if channel_row and guild:
                    channel_id = int(channel_row[0])
                    channel    = guild.get_channel(channel_id)
                    svs_cursor.execute("SELECT context_id FROM reference WHERE context=?", (activity_name,))
                    message_row = svs_cursor.fetchone()
                    if message_row and channel:
                        message_id = int(message_row[0])
                        try:
                            message = await channel.fetch_message(message_id)
                            await message.delete()
                        except Exception:
                            pass
                    svs_cursor.execute("DELETE FROM reference WHERE context=?", (activity_name,))
                svs_cursor.execute("DELETE FROM reference WHERE context=?", (channel_context,))

            @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="↩️")
            async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                await self.parent_cog.show_settings_menu(interaction)

        embed = discord.Embed(
            title="📡  Reset Channel Links",
            description=(
                "Select which channel bindings you want to remove.\n\n"
                "**Note:** This only unlinks the channel configuration.\n"
                "Appointment records will **not** be deleted."
            ),
            color=CRIMSON
        )
        await interaction.response.edit_message(embed=embed, view=ClearChannelsConfirmView(self))

    async def show_settings_menu(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🔧  Admin Panel",
            description=(
                "Manage advanced scheduling configuration.\n\n"
                "**Available actions:**\n\n"
                "◆ **Sync Names** — pull latest nicknames from the game API\n"
                "◆ **Wipe Reservations** — erase all slots for a given event\n"
                "◆ **Reset Channels** — unlink configured channels\n"
                "◆ **Unlink Server** — remove the registered server record\n"
            ),
            color=SLATE
        )
        view = MinisterSettingsView(self.bot, self)
        try:
            await interaction.response.edit_message(embed=embed, view=view)
        except discord.InteractionResponded:
            await interaction.edit_original_response(embed=embed, view=view)

    async def show_activity_selection_for_update(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🔄  Sync Player Names",
            description="Which event's roster would you like to sync with the game API?",
            color=GOLD
        )
        view = ActivitySelectView(self.bot, self, "update_names")
        try:
            await interaction.response.edit_message(embed=embed, view=view)
        except discord.InteractionResponded:
            await interaction.edit_original_response(embed=embed, view=view)

    async def show_activity_selection_for_clear(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🗑️  Wipe Event Reservations",
            description="Which event's reservations would you like to erase?",
            color=CRIMSON
        )
        view = ActivitySelectView(self.bot, self, "clear_reservations")
        try:
            await interaction.response.edit_message(embed=embed, view=view)
        except discord.InteractionResponded:
            await interaction.edit_original_response(embed=embed, view=view)


async def setup(bot):
    await bot.add_cog(MinisterMenu(bot))