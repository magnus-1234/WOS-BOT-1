import logging
from typing import Optional

import discord
from discord.ext import commands

from db.roles_adapters import AutoRolesAdapter, ReactionRolesAdapter

logger = logging.getLogger(__name__)


class RoleMenu(commands.Cog):
    """Applies dashboard-configured reaction roles and auto roles."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @staticmethod
    def _payload_emoji(payload: discord.RawReactionActionEvent) -> str:
        return str(payload.emoji)

    @staticmethod
    async def _resolve_role(guild: discord.Guild, role_id: int) -> Optional[discord.Role]:
        role = guild.get_role(int(role_id))
        if role:
            return role
        try:
            roles = await guild.fetch_roles()
            return next((item for item in roles if item.id == int(role_id)), None)
        except Exception:
            return None

    async def _get_reaction_role(
        self,
        guild_id: int,
        message_id: int,
        emoji: str,
    ) -> Optional[dict]:
        reaction_roles = await ReactionRolesAdapter.get_reaction_roles(guild_id)
        for reaction_role in reaction_roles:
            if int(reaction_role.get("message_id", 0)) == int(message_id) and str(reaction_role.get("emoji")) == emoji:
                return reaction_role
        return None

    async def _apply_reaction_role(self, payload: discord.RawReactionActionEvent, add: bool) -> None:
        if payload.guild_id is None or payload.user_id == self.bot.user.id:
            return

        emoji = self._payload_emoji(payload)
        reaction_role = await self._get_reaction_role(payload.guild_id, payload.message_id, emoji)
        if not reaction_role:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        role = await self._resolve_role(guild, int(reaction_role["role_id"]))
        if not role:
            logger.warning("Reaction role %s no longer exists in guild %s", reaction_role["role_id"], guild.id)
            return

        member = guild.get_member(payload.user_id)
        if not member:
            try:
                member = await guild.fetch_member(payload.user_id)
            except discord.NotFound:
                return

        try:
            reason = "Dashboard reaction role"
            if add:
                await member.add_roles(role, reason=reason)
            else:
                await member.remove_roles(role, reason=reason)
        except discord.Forbidden:
            logger.warning("Missing permission or hierarchy to update role %s in guild %s", role.id, guild.id)
        except Exception as e:
            logger.error("Failed to update reaction role %s for user %s: %s", role.id, payload.user_id, e)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        await self._apply_reaction_role(payload, add=True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        await self._apply_reaction_role(payload, add=False)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        role_ids = await AutoRolesAdapter.get_auto_roles(member.guild.id)
        if not role_ids:
            return

        roles = []
        for role_id in role_ids:
            role = await self._resolve_role(member.guild, int(role_id))
            if role:
                roles.append(role)

        if not roles:
            return

        try:
            await member.add_roles(*roles, reason="Dashboard auto roles")
        except discord.Forbidden:
            logger.warning("Missing permission or hierarchy to apply auto roles in guild %s", member.guild.id)
        except Exception as e:
            logger.error("Failed to apply auto roles in guild %s to user %s: %s", member.guild.id, member.id, e)


async def setup(bot: commands.Bot):
    await bot.add_cog(RoleMenu(bot))
