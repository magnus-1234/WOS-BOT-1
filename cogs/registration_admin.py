"""
Registration Admin Cog — Global Admin Commands for Server Registration Approval
Provides /reg-approve, /reg-deny, /reg-pending slash commands for the bot owner.
"""
import discord
from discord import app_commands
from discord.ext import commands
import logging
import os

logger = logging.getLogger(__name__)

BOT_OWNER_ID = int(os.getenv("BOT_OWNER_ID", "0"))


async def _is_global_admin(interaction: discord.Interaction) -> bool:
    """Check if the interaction user is the global admin (bot owner)."""
    return interaction.user.id == BOT_OWNER_ID


class ApproveView(discord.ui.View):
    """Interactive approve/deny buttons sent in admin DMs."""
    def __init__(self, guild_id: str, guild_name: str, alliance_name: str,
                 submitter_id: str, submitter_name: str, access_code: str):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.guild_name = guild_name
        self.alliance_name = alliance_name
        self.submitter_id = submitter_id
        self.submitter_name = submitter_name
        self.access_code = access_code

    @discord.ui.button(label="✅ Approve", style=discord.ButtonStyle.success,
                       custom_id="reg_approve_btn")
    async def approve_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != BOT_OWNER_ID:
            await interaction.response.send_message("❌ Only the global admin can do this.", ephemeral=True)
            return
        await interaction.response.defer()
        await _do_approve(interaction, self.guild_id, self.guild_name,
                          self.alliance_name, self.submitter_id, self.submitter_name)
        # Disable buttons
        for item in self.children:
            item.disabled = True
        await interaction.edit_original_response(view=self)

    @discord.ui.button(label="❌ Deny", style=discord.ButtonStyle.danger,
                       custom_id="reg_deny_btn")
    async def deny_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != BOT_OWNER_ID:
            await interaction.response.send_message("❌ Only the global admin can do this.", ephemeral=True)
            return
        await interaction.response.defer()
        await _do_deny(interaction, self.guild_id, self.guild_name,
                       self.submitter_id, self.submitter_name)
        for item in self.children:
            item.disabled = True
        await interaction.edit_original_response(view=self)


async def _do_approve(interaction, guild_id: str, guild_name: str,
                      alliance_name: str, submitter_id: str, submitter_name: str):
    try:
        from db.mongo_adapters import PendingConfigAdapter, mongo_enabled
        if not mongo_enabled():
            await interaction.followup.send("❌ Database not available.", ephemeral=True)
            return
        ok = await PendingConfigAdapter.approve_async(int(guild_id), interaction.user.id)
        if ok:
            # Create auto-channels
            try:
                guild = interaction.client.get_guild(int(guild_id))
                if guild:
                    # player-ids channel
                    player_ids_channel = discord.utils.get(guild.text_channels, name="🆔┃player-ids")
                    if not player_ids_channel:
                        player_ids_channel = await guild.create_text_channel("🆔┃player-ids")
                        
                    # giftcode channel
                    giftcode_channel = discord.utils.get(guild.text_channels, name="🎁┃𝐠𝐢𝐟𝐭𝐜𝐨𝐝𝐞")
                    if not giftcode_channel:
                        giftcode_channel = await guild.create_text_channel("🎁┃𝐠𝐢𝐟𝐭𝐜𝐨𝐝𝐞")
                        
                    # welcome channel
                    welcome_channel = discord.utils.get(guild.text_channels, name="🎉┃welcome")
                    if not welcome_channel:
                        welcome_channel = await guild.create_text_channel("🎉┃welcome")
                    
                    import sqlite3
                    from datetime import datetime
                    
                    # 1. Player IDs config
                    try:
                        alliance_id = 0
                        try:
                            with sqlite3.connect('db/alliance.sqlite', timeout=10) as alliance_db:
                                ac_cursor = alliance_db.cursor()
                                ac_cursor.execute("SELECT alliance_id FROM alliance_list WHERE name = ?", (alliance_name,))
                                row = ac_cursor.fetchone()
                                if row:
                                    alliance_id = row[0]
                                else:
                                    ac_cursor.execute("INSERT INTO alliance_list (name, discord_server_id) VALUES (?, ?)", (alliance_name, int(guild_id)))
                                    alliance_id = ac_cursor.lastrowid
                                    alliance_db.commit()
                                    try:
                                        from db.mongo_adapters import AlliancesAdapter, mongo_enabled
                                        if mongo_enabled() and hasattr(AlliancesAdapter, 'upsert_async'):
                                            await AlliancesAdapter.upsert_async(alliance_id, alliance_name, int(guild_id))
                                        elif mongo_enabled() and hasattr(AlliancesAdapter, 'upsert'):
                                            AlliancesAdapter.upsert(alliance_id, alliance_name, int(guild_id))
                                    except Exception as mongo_err:
                                        logger.error(f"Error saving new alliance to mongo: {mongo_err}")
                        except Exception as e:
                            logger.error(f"Error finding/creating alliance_id for {alliance_name}: {e}")
                            
                        if alliance_id:
                            try:
                                from db.mongo_adapters import ServerAllianceAdapter
                                if mongo_enabled():
                                    if hasattr(ServerAllianceAdapter, 'set_alliance_async'):
                                        await ServerAllianceAdapter.set_alliance_async(int(guild_id), alliance_id, int(submitter_id))
                                    else:
                                        ServerAllianceAdapter.set_alliance(int(guild_id), alliance_id, int(submitter_id))
                            except Exception as e:
                                logger.error(f"Failed to set server alliance for {guild_id}: {e}")

                            try:
                                with sqlite3.connect('db/settings.sqlite', timeout=10) as sdb:
                                    sc = sdb.cursor()
                                    sc.execute("INSERT OR IGNORE INTO admin (id, is_initial) VALUES (?, 0)", (int(submitter_id),))
                                    sc.execute("INSERT OR IGNORE INTO adminserver (admin, alliances_id) VALUES (?, ?)", (int(submitter_id), alliance_id))
                                    sdb.commit()
                            except Exception as e:
                                logger.error(f"Failed to update sqlite adminserver for {submitter_id}: {e}")

                        with sqlite3.connect('db/id_channel.sqlite', timeout=10) as db:
                            cursor = db.cursor()
                            cursor.execute('''CREATE TABLE IF NOT EXISTS id_channels
                                         (guild_id INTEGER, alliance_id INTEGER, channel_id INTEGER, created_at TEXT, created_by INTEGER, UNIQUE(guild_id, channel_id))''')
                            cursor.execute("INSERT OR REPLACE INTO id_channels (guild_id, alliance_id, channel_id, created_at, created_by) VALUES (?, ?, ?, ?, ?)",
                                         (int(guild_id), alliance_id, player_ids_channel.id, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), interaction.user.id))
                            db.commit()
                            
                        try:
                            from db.mongo_adapters import IDChannelsAdapter
                            if mongo_enabled() and hasattr(IDChannelsAdapter, 'set_channel_async'):
                                await IDChannelsAdapter.set_channel_async(int(guild_id), player_ids_channel.id, alliance_id)
                        except Exception:
                            pass
                            
                        await player_ids_channel.send(
                            "This channel has been configured for auto redeem, alliance members can write their player id here to get registered for fastest redeem."
                        )
                    except Exception as e:
                        logger.error(f"Failed to setup player_ids channel: {e}")

                    # 2. Giftcode Channel config
                    try:
                        try:
                            from db.mongo_adapters import AutoRedeemChannelsAdapter, AutoRedeemSettingsAdapter
                            if mongo_enabled():
                                if hasattr(AutoRedeemChannelsAdapter, 'set_channel_async'):
                                    await AutoRedeemChannelsAdapter.set_channel_async(int(guild_id), giftcode_channel.id, 0)
                                elif hasattr(AutoRedeemChannelsAdapter, 'set_channel'):
                                    AutoRedeemChannelsAdapter.set_channel(int(guild_id), giftcode_channel.id, 0)
                                    
                                if hasattr(AutoRedeemSettingsAdapter, 'update_settings_async'):
                                    await AutoRedeemSettingsAdapter.update_settings_async(int(guild_id), {'enabled': True})
                        except Exception:
                            pass
                        
                        with sqlite3.connect('db/giftcode.sqlite', timeout=10) as gcdb:
                            g_cursor = gcdb.cursor()
                            g_cursor.execute("CREATE TABLE IF NOT EXISTS auto_redeem_channels (guild_id INTEGER, channel_id INTEGER, role_id INTEGER, PRIMARY KEY (guild_id))")
                            g_cursor.execute("INSERT OR REPLACE INTO auto_redeem_channels (guild_id, channel_id, role_id) VALUES (?, ?, ?)", (int(guild_id), giftcode_channel.id, 0))
                            
                            g_cursor.execute("CREATE TABLE IF NOT EXISTS auto_redeem_settings (guild_id INTEGER PRIMARY KEY, enabled INTEGER DEFAULT 0, priority INTEGER DEFAULT 999, updated_by INTEGER, updated_at TEXT)")
                            g_cursor.execute("""
                                INSERT INTO auto_redeem_settings (guild_id, enabled, updated_by, updated_at)
                                VALUES (?, 1, ?, ?)
                                ON CONFLICT(guild_id) DO UPDATE SET
                                    enabled = 1,
                                    updated_by = excluded.updated_by,
                                    updated_at = excluded.updated_at
                            """, (int(guild_id), interaction.user.id, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
                            gcdb.commit()
                    except Exception as e:
                        logger.error(f"Failed to setup giftcode channel: {e}")
                        
                    # 3. Welcome Channel config
                    try:
                        try:
                            from db.mongo_adapters import WelcomeChannelAdapter
                            if mongo_enabled():
                                if hasattr(WelcomeChannelAdapter, 'set_async'):
                                    await WelcomeChannelAdapter.set_async(int(guild_id), welcome_channel.id, enabled=True)
                                elif hasattr(WelcomeChannelAdapter, 'set'):
                                    WelcomeChannelAdapter.set(int(guild_id), welcome_channel.id, enabled=True)
                        except Exception:
                            pass
                    except Exception as e:
                        logger.error(f"Failed to setup welcome channel: {e}")
            except Exception as e:
                logger.error(f"Error creating auto-channels for {guild_id}: {e}")

            # Notify submitter
            try:
                user = await interaction.client.fetch_user(int(submitter_id))
                await user.send(
                    f"✅ **Your registration has been approved!**\n\n"
                    f"**Server:** {guild_name}\n"
                    f"**Alliance:** `{alliance_name}`\n\n"
                    f"Your access code is now active. Visit the dashboard and use "
                    f"`/manage` or click **Alliance Monitor / Gift Codes** — then enter "
                    f"the code you set during registration."
                )
            except Exception as dm_err:
                logger.warning(f"Could not DM submitter {submitter_id}: {dm_err}")
            await interaction.followup.send(
                f"✅ **Approved!** Registration for **{guild_name}** is now active.\n"
                f"Alliance `{alliance_name}` has been configured.",
                ephemeral=False
            )
        else:
            await interaction.followup.send(
                f"⚠️ Could not find a pending request for guild `{guild_id}`. "
                f"It may have already been processed.", ephemeral=True
            )
    except Exception as e:
        logger.error(f"Error approving registration for guild {guild_id}: {e}")
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)


async def _do_deny(interaction, guild_id: str, guild_name: str,
                   submitter_id: str, submitter_name: str):
    try:
        from db.mongo_adapters import PendingConfigAdapter, mongo_enabled
        if not mongo_enabled():
            await interaction.followup.send("❌ Database not available.", ephemeral=True)
            return
        ok = await PendingConfigAdapter.deny_async(int(guild_id), interaction.user.id)
        if ok:
            # Notify submitter
            try:
                user = await interaction.client.fetch_user(int(submitter_id))
                await user.send(
                    f"❌ **Your registration request was denied.**\n\n"
                    f"**Server:** {guild_name}\n\n"
                    f"Please contact the bot administrator for more details.\n"
                    f"You may submit a new registration request when ready."
                )
            except Exception as dm_err:
                logger.warning(f"Could not DM submitter {submitter_id}: {dm_err}")
            await interaction.followup.send(
                f"❌ **Denied.** Registration for **{guild_name}** has been rejected.",
                ephemeral=False
            )
        else:
            await interaction.followup.send(
                f"⚠️ Could not find a pending request for guild `{guild_id}`.", ephemeral=True
            )
    except Exception as e:
        logger.error(f"Error denying registration for guild {guild_id}: {e}")
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)


class RegistrationAdmin(commands.Cog):
    """Slash commands for the global admin to approve or deny server registrations."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── /reg-pending ──────────────────────────────────────────────────────────
    @app_commands.command(
        name="reg-pending",
        description="[Admin] View all pending server registration requests"
    )
    async def reg_pending(self, interaction: discord.Interaction):
        if not await _is_global_admin(interaction):
            await interaction.response.send_message(
                "❌ This command is restricted to the global administrator.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            from db.mongo_adapters import PendingConfigAdapter, mongo_enabled
            if not mongo_enabled():
                await interaction.followup.send("❌ Database not available.", ephemeral=True)
                return

            docs = await PendingConfigAdapter.get_all_pending_async()
            if not docs:
                await interaction.followup.send(
                    "✅ No pending registration requests.", ephemeral=True
                )
                return

            embed = discord.Embed(
                title="📋 Pending Server Registrations",
                description=f"**{len(docs)}** request(s) awaiting review.",
                color=0xf59e0b
            )
            for doc in docs[:10]:  # Show up to 10
                state_text = f"`{doc.get('state')}`" if doc.get("state") is not None else "`not provided`"
                embed.add_field(
                    name=f"🏰 {doc.get('guild_name', 'Unknown Server')}",
                    value=(
                        f"**Guild ID:** `{doc.get('guild_id')}`\n"
                        f"**Alliance:** `{doc.get('alliance_name')}`\n"
                        f"**State:** {state_text}\n"
                        f"**By:** {doc.get('discord_username')} (`{doc.get('discord_user_id')}`)\n"
                        f"**Submitted:** {doc.get('submitted_at', 'N/A')[:10]}\n"
                        f"Use `/reg-approve {doc.get('guild_id')}` or `/reg-deny {doc.get('guild_id')}`"
                    ),
                    inline=False
                )
            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Error fetching pending registrations: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    # ── /reg-approve ──────────────────────────────────────────────────────────
    @app_commands.command(
        name="reg-approve",
        description="[Admin] Approve a pending server registration request"
    )
    @app_commands.describe(guild_id="The Discord server (guild) ID to approve")
    async def reg_approve(self, interaction: discord.Interaction, guild_id: str):
        if not await _is_global_admin(interaction):
            await interaction.response.send_message(
                "❌ This command is restricted to the global administrator.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            from db.mongo_adapters import PendingConfigAdapter, mongo_enabled
            if not mongo_enabled():
                await interaction.followup.send("❌ Database not available.", ephemeral=True)
                return

            raw_guild_id = guild_id.replace("/reg-approve ", "").replace("/reg-deny ", "").strip()
            
            doc = await PendingConfigAdapter.get_by_guild_async(int(raw_guild_id))
            if not doc or doc.get("status") != "pending":
                await interaction.followup.send(
                    f"⚠️ No pending registration found for guild `{raw_guild_id}`.", ephemeral=True
                )
                return

            await _do_approve(
                interaction, raw_guild_id,
                doc.get("guild_name", raw_guild_id),
                doc.get("alliance_name", ""),
                doc.get("discord_user_id", "0"),
                doc.get("discord_username", "Unknown")
            )
        except Exception as e:
            logger.error(f"Error in reg-approve: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    # ── /reg-deny ─────────────────────────────────────────────────────────────
    @app_commands.command(
        name="reg-deny",
        description="[Admin] Deny a pending server registration request"
    )
    @app_commands.describe(guild_id="The Discord server (guild) ID to deny")
    async def reg_deny(self, interaction: discord.Interaction, guild_id: str):
        if not await _is_global_admin(interaction):
            await interaction.response.send_message(
                "❌ This command is restricted to the global administrator.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            from db.mongo_adapters import PendingConfigAdapter, mongo_enabled
            if not mongo_enabled():
                await interaction.followup.send("❌ Database not available.", ephemeral=True)
                return

            raw_guild_id = guild_id.replace("/reg-approve ", "").replace("/reg-deny ", "").strip()
            
            doc = await PendingConfigAdapter.get_by_guild_async(int(raw_guild_id))
            if not doc or doc.get("status") != "pending":
                await interaction.followup.send(
                    f"⚠️ No pending registration found for guild `{raw_guild_id}`.", ephemeral=True
                )
                return

            await _do_deny(
                interaction, raw_guild_id,
                doc.get("guild_name", raw_guild_id),
                doc.get("discord_user_id", "0"),
                doc.get("discord_username", "Unknown")
            )
        except Exception as e:
            logger.error(f"Error in reg-deny: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(RegistrationAdmin(bot))
    logger.info("✅ RegistrationAdmin cog loaded")
