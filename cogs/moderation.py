import logging
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import discord
from discord.ext import commands

from db.moderation_adapters import (
    BlacklistAdapter,
    ModerationActionsAdapter,
    ModerationSettingsAdapter,
)

logger = logging.getLogger(__name__)


INVITE_RE = re.compile(r"(?:discord\.gg|discord(?:app)?\.com/invite)/[A-Za-z0-9-]+", re.I)
URL_RE = re.compile(r"https?://|www\.", re.I)


class DashboardModeration(commands.Cog):
    """Enforces dashboard-configured moderation rules."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._message_windows = defaultdict(lambda: deque(maxlen=40))

    @staticmethod
    def _get_automod(settings: Dict[str, Any]) -> Dict[str, Any]:
        return settings.get("automod") or {}

    @staticmethod
    def _get_logging(settings: Dict[str, Any]) -> Dict[str, Any]:
        return settings.get("logging") or {}

    @staticmethod
    def _has_bypass(member: discord.Member, automod: Dict[str, Any]) -> bool:
        bypass_roles = {int(role_id) for role_id in automod.get("bypass_roles", []) if role_id}
        return bool(bypass_roles.intersection(role.id for role in member.roles))

    async def _log_dashboard_action(
        self,
        message: discord.Message,
        action: str,
        reason: str,
        duration_minutes: Optional[int] = None,
        settings: Optional[Dict[str, Any]] = None,
    ) -> None:
        await ModerationActionsAdapter.add_action(
            guild_id=message.guild.id,
            user_id=message.author.id,
            moderator_id=self.bot.user.id if self.bot.user else 0,
            action_type=action,
            reason=reason,
            duration=duration_minutes,
        )

        logging_settings = self._get_logging(settings or {})
        if not logging_settings.get("enabled"):
            return

        events = {str(event).lower() for event in logging_settings.get("events", [])}
        if events and action not in events and "automod" not in events:
            return

        channel_id = logging_settings.get("channel_id")
        channel = message.guild.get_channel(int(channel_id)) if channel_id else None
        if not channel:
            return

        try:
            embed = discord.Embed(
                title="AutoMod Action",
                description=reason,
                color=discord.Color.orange(),
                timestamp=datetime.now(timezone.utc),
            )
            embed.add_field(name="Action", value=action, inline=True)
            embed.add_field(name="Member", value=f"{message.author} (`{message.author.id}`)", inline=False)
            embed.add_field(name="Channel", value=message.channel.mention, inline=True)
            if message.content:
                embed.add_field(name="Message", value=message.content[:1000], inline=False)
            await channel.send(embed=embed)
        except Exception as exc:
            logger.warning("Failed to send moderation log in guild %s: %s", message.guild.id, exc)

    async def _safe_delete(self, message: discord.Message) -> None:
        try:
            await message.delete()
        except discord.NotFound:
            pass
        except discord.Forbidden:
            logger.warning("Missing permission to delete message in guild %s", message.guild.id)

    async def _enforce(
        self,
        message: discord.Message,
        action: str,
        reason: str,
        settings: Dict[str, Any],
        duration_seconds: Optional[int] = None,
    ) -> None:
        action = (action or "delete").lower()
        duration_minutes = max(1, int((duration_seconds or 600) / 60))

        if action in {"delete", "warn", "mute", "kick", "ban"}:
            await self._safe_delete(message)

        try:
            if action == "warn":
                await message.channel.send(f"{message.author.mention} {reason}", delete_after=10)
            elif action == "mute":
                until = datetime.now(timezone.utc) + timedelta(minutes=duration_minutes)
                await message.author.timeout(until, reason=reason)
            elif action == "kick":
                await message.author.kick(reason=reason)
            elif action == "ban":
                await message.guild.ban(message.author, reason=reason, delete_message_seconds=0)
        except discord.Forbidden:
            logger.warning("Missing permission or hierarchy for %s in guild %s", action, message.guild.id)
            await self._log_dashboard_action(message, "automod", f"{reason} (permission failed)", None, settings)
            return

        await self._log_dashboard_action(message, action if action != "delete" else "automod", reason, duration_minutes, settings)

    def _spam_violation(self, message: discord.Message, automod: Dict[str, Any]) -> bool:
        threshold = max(2, int(automod.get("spam_threshold") or 5))
        window_key = (message.guild.id, message.channel.id, message.author.id)
        now = time.monotonic()
        window = self._message_windows[window_key]
        window.append(now)
        while window and now - window[0] > 5:
            window.popleft()
        return len(window) >= threshold

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot or not isinstance(message.author, discord.Member):
            return

        settings = await ModerationSettingsAdapter.get_settings(message.guild.id)
        automod = self._get_automod(settings)
        if not automod.get("enabled") or self._has_bypass(message.author, automod):
            return

        content = message.content or ""
        content_lower = content.lower()

        if automod.get("anti_spam") and self._spam_violation(message, automod):
            await self._enforce(
                message,
                automod.get("spam_action", "warn"),
                "AutoMod: message spam detected.",
                settings,
                int(automod.get("spam_duration") or 600),
            )
            return

        max_mentions = int(automod.get("max_mentions") or 0)
        if automod.get("mention_enabled") and max_mentions and len(message.mentions) > max_mentions:
            await self._enforce(
                message,
                automod.get("mention_action", "warn"),
                "AutoMod: too many mentions in one message.",
                settings,
            )
            return

        if automod.get("anti_link") or automod.get("anti_invites"):
            has_invite = bool(INVITE_RE.search(content))
            has_link = bool(URL_RE.search(content))
            block_type = automod.get("link_type") or "external"
            should_block = (
                (automod.get("anti_invites") and has_invite)
                or block_type == "all" and has_link
                or block_type == "external" and has_link
                or block_type == "invites" and has_invite
            )
            if should_block:
                await self._enforce(
                    message,
                    automod.get("link_action", "delete"),
                    "AutoMod: blocked link or Discord invite.",
                    settings,
                )
                return

        if automod.get("caps_enabled") and len(content) >= 12:
            letters = [char for char in content if char.isalpha()]
            if letters:
                caps_percent = sum(1 for char in letters if char.isupper()) * 100 / len(letters)
                if caps_percent >= int(automod.get("caps_threshold") or 70):
                    await self._enforce(
                        message,
                        automod.get("caps_action", "delete"),
                        "AutoMod: excessive uppercase text.",
                        settings,
                    )
                    return

        blacklist = await BlacklistAdapter.get_blacklist(message.guild.id)
        for word in blacklist:
            term = str(word).strip().lower()
            if term and term in content_lower:
                await self._enforce(
                    message,
                    automod.get("profanity_action", "delete"),
                    "AutoMod: blacklisted word or phrase detected.",
                    settings,
                )
                return


async def setup(bot: commands.Bot):
    await bot.add_cog(DashboardModeration(bot))
