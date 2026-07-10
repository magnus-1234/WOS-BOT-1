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
TOPGG_REVIEW_URL = "https://top.gg/bot/1399025185046134866?s=0e3c921b2b5f8"


async def _is_global_admin(interaction: discord.Interaction) -> bool:
    """Check if the interaction user is the global admin (bot owner)."""
    return interaction.user.id == BOT_OWNER_ID


def _approval_dm_message(guild_name: str, alliance_name: str) -> str:
    return (
        f"✅ **Your registration has been approved!**\n\n"
        f"**Server:** {guild_name}\n"
        f"**Alliance:** `{alliance_name}`\n\n"
        f"Your access code is now active. Visit the dashboard and use "
        f"`/manage` or click **Alliance Monitor / Gift Codes** — then enter "
        f"the code you set during registration.\n\n"
        f"If Whiteout Survival Bot helps your server, please share a quick "
        f"review or feedback on Top.gg:\n{TOPGG_REVIEW_URL}"
    )


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
    from src.bot.registration_utils import process_registration_approval
    success, msg = await process_registration_approval(
        bot=interaction.client,
        guild_id=int(guild_id),
        guild_name=guild_name,
        alliance_name=alliance_name,
        submitter_id=int(submitter_id),
        admin_id=interaction.user.id
    )
    await interaction.followup.send(msg, ephemeral=not success)


async def _do_deny(interaction, guild_id: str, guild_name: str,
                   submitter_id: str, submitter_name: str):
    from src.bot.registration_utils import process_registration_denial
    success, msg = await process_registration_denial(
        bot=interaction.client,
        guild_id=int(guild_id),
        guild_name=guild_name,
        submitter_id=int(submitter_id),
        admin_id=interaction.user.id
    )
    await interaction.followup.send(msg, ephemeral=not success)


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


    # ── /reg-repair ───────────────────────────────────────────────────────────
    @app_commands.command(name="reg-repair", description="[Admin] Repair auto-channels and DB for an already approved server")
    @app_commands.describe(guild_id="The Server ID to repair")
    async def reg_repair(self, interaction: discord.Interaction, guild_id: str):
        if not await _is_global_admin(interaction):
            await interaction.response.send_message("❌ Only the global admin can use this command.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=False)
        try:
            from db.mongo_adapters import PendingConfigAdapter, mongo_enabled
            if not mongo_enabled():
                await interaction.followup.send("❌ Database not available.")
                return

            # Fetch the doc even if it's already approved
            db = await PendingConfigAdapter._get_db_async()
            doc = await db[PendingConfigAdapter.COLL].find_one({'guild_id': str(guild_id)})
            
            if not doc:
                await interaction.followup.send(f"❌ No registration record found for guild `{guild_id}`.")
                return
                
            alliance_name = doc.get("alliance_name", "Unknown Alliance")
            submitter_id = int(doc.get("discord_user_id", 0))

            from src.bot.registration_utils import setup_approved_guild_channels
            success, msg = await setup_approved_guild_channels(
                bot=self.bot,
                guild_id=int(guild_id),
                alliance_name=alliance_name,
                submitter_id=submitter_id,
                admin_id=interaction.user.id
            )
            
            if success:
                await interaction.followup.send(f"✅ **Repaired {doc.get('guild_name', guild_id)}**: {msg}")
            else:
                await interaction.followup.send(f"⚠️ **Failed to repair {doc.get('guild_name', guild_id)}**: {msg}")

        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Error repairing guild {guild_id}: {e}")
            await interaction.followup.send(f"❌ Error: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(RegistrationAdmin(bot))
    import logging
    logging.getLogger(__name__).info("✅ RegistrationAdmin cog loaded")
