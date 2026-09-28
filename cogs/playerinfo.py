from typing import Optional
"""/playerinfo cog - single clean implementation with logging.

This module intentionally keeps the request pattern aligned with other
working cogs: millisecond timestamp, MD5(form + SECRET), x-www-form-urlencoded
payload, and Origin header. It also logs invocation, payloads, API responses
and exceptions to the bot logger configured in `main.py` so you can inspect
`bot/log/log.txt` for issues.
"""

import re
import time
import hashlib
import aiohttp
import ssl
import asyncio
from typing import Optional, Union
import sqlite3
from datetime import datetime
import os
import logging
import discord
from discord import app_commands
from discord.ext import commands
import urllib.parse
from thinking_animation import ThinkingAnimation
from command_animator import command_animation
from db.mongo_adapters import mongo_enabled, AllianceMembersAdapter, AutoRedeemChannelsAdapter
from src.api.wosoracle import fetch_player_info as fetch_oracle_player_info, WoSOracleError
from src.api.wosatlas import fetch_player_profile as fetch_atlas_player_profile

# Player API endpoint and secret (keep this in sync with your other code)
API_URL = "https://wos-giftcode-api.centurygame.com/api/player"
SECRET = "tB87#kPtkxqOS2"

# Development guild for quick command registration (prefer env var; fall back
# to the historically used hard-coded value). The user may set DEV_GUILD_ID
# in the environment for quick per-guild registration.
try:
    _env_dev_gid = os.getenv('DEV_GUILD_ID')
    DEV_GUILD_ID = int(_env_dev_gid) if _env_dev_gid else 850787279664185434
except Exception:
    DEV_GUILD_ID = 850787279664185434
# Watermark image (user-provided). This may be a page URL; Discord requires
# an actual image URL for icon fields. We attempt to set it and will quietly
# fall back if Discord rejects it.
WATERMARK_URL = "https://cdn.discordapp.com/attachments/1435569370389807144/1436437186424606741/unnamed_4.png?ex=690f99e0&is=690e4860&hm=2262bc4ceea28787c91c5bfcb2d6e7fac28cda152c4963a9b4375eac4913b063"
ORACLE_TEST_GUILD_ID = 1394263768501846068


def map_furnace(lv: int) -> Optional[str]:
    """Map numeric furnace level to FC labels per user rules.

    Rules implemented:
    - 31-39 -> FC1
    - 40-44 -> FC2
    - 45-49 -> FC3
    - 50-54 -> FC4
    - 55-59 -> FC5, etc (every 5 levels after 40 increments FC index)
    """
    if lv is None:
        return None
    try:
        lv = int(lv)
    except Exception:
        return None

    if 31 <= lv <= 39:
        return "FC1"
    if lv >= 40:
        fc_index = ((lv - 40) // 5) + 2
        return f"FC{fc_index}"
    return None


class PlayerInfoCog(commands.Cog):
    """Cog that adds a /playerinfo slash command.

    The command accepts an 8- or 9-digit player id (fid) and returns a rich embed
    containing: nickname, fid, kid, furnace level (and FC mapping), small
    furnace icon and the avatar as the embed thumbnail.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = logging.getLogger('bot.playerinfo')
        # Semaphore to limit concurrent external requests from message triggers
        self._sem = asyncio.Semaphore(6)
        # Thinking animation instance for message-based lookups
        self._thinking_animation = ThinkingAnimation()

    async def _add_atlas_coordinates(
        self, embed: discord.Embed, fid: str, profile: dict | None = None
    ) -> None:
        """Add coordinates near the top of a player lookup when Atlas has them."""
        try:
            if profile is None:
                profile = await fetch_atlas_player_profile(fid, timeout=12)
            coordinates = profile.get("coordinates") if profile else None
            if coordinates:
                embed.insert_field_at(
                    min(3, len(embed.fields)),
                    name="📍 Coordinates",
                    value=f"`{coordinates['x']}, {coordinates['y']}` (X, Y)",
                    inline=True,
                )
        except Exception as exc:
            self.logger.debug("Atlas coordinates unavailable for fid=%s: %s", fid, exc)

    @staticmethod
    def _merge_player_profiles(oracle_profile, atlas_profile) -> dict | None:
        """Keep Oracle's values and fill missing profile details from Atlas."""
        if isinstance(oracle_profile, dict):
            player = oracle_profile.get("player") or oracle_profile.get("data") or oracle_profile
            merged = dict(player) if isinstance(player, dict) else {}
        else:
            merged = {}
        if isinstance(atlas_profile, dict):
            atlas_fields = {
                "username": atlas_profile.get("nickname"),
                "state_id": atlas_profile.get("state_id"),
                "town_hall_level": atlas_profile.get("furnace_lv"),
                "alliance_abbr": atlas_profile.get("alliance_abbr"),
                "alliance_id": atlas_profile.get("alliance_id"),
                "power": atlas_profile.get("power"),
            }
            for key, value in atlas_fields.items():
                if merged.get(key) in (None, "", [], {}) and value not in (None, "", [], {}):
                    merged[key] = value
            if not merged.get("alliance") and atlas_profile.get("alliance_abbr"):
                merged["alliance"] = atlas_profile["alliance_abbr"]
            if not merged.get("coordinates") and atlas_profile.get("coordinates"):
                merged["coordinates"] = atlas_profile["coordinates"]
        return merged or None

    def _is_managed_channel(self, message: discord.Message) -> bool:
        """Return True if this channel is managed by another cog (auto-redeem or ID channel).
        
        playerinfo should NOT intercept 8- or 9-digit FIDs in these channels because a
        dedicated cog already handles them and will produce its own response.
        """
        if not message.guild:
            return False
        try:
            import sqlite3
            guild_id = message.guild.id
            channel_id = message.channel.id

            # ── Check manage_giftcode auto-redeem channel ────────────────────
            # MongoDB is the primary store; SQLite is the fallback.
            # We must mirror the exact lookup order that manage_giftcode.py
            # uses in its own on_message handler so we never miss a hit.
            try:
                if mongo_enabled() and AutoRedeemChannelsAdapter:
                    channel_config = AutoRedeemChannelsAdapter.get_channel(guild_id)
                    if channel_config and channel_config.get('channel_id') == channel_id:
                        return True
            except Exception:
                pass

            # SQLite fallback (channel may not yet be synced to Mongo)
            try:
                with sqlite3.connect('db/giftcode.sqlite') as db:
                    cur = db.cursor()
                    cur.execute(
                        "SELECT 1 FROM auto_redeem_channels WHERE guild_id = ? AND channel_id = ?",
                        (guild_id, channel_id)
                    )
                    if cur.fetchone():
                        return True
            except Exception:
                pass

            # ── Check id_channel registration channels ───────────────────────
            try:
                with sqlite3.connect('db/id_channel.sqlite') as db:
                    cur = db.cursor()
                    cur.execute(
                        "SELECT 1 FROM id_channels WHERE guild_id = ? AND channel_id = ?",
                        (guild_id, channel_id)
                    )
                    if cur.fetchone():
                        return True
            except Exception:
                pass

        except Exception:
            pass
        return False

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listen for messages that contain a standalone 8- or 9-digit number and show player info inline.

        If the message contains a standalone 8- or 9-digit number (anywhere in the text)
        and the API returns a valid player, reply with the same embed used by the
        slash command. If the API doesn't return data or an error occurs, react
        to the message with ❌ to indicate the lookup failed.
        """
        try:
            if message.author.bot:
                return

            content = (message.content or "")
            
            # Skip if message starts with !Add or !Remove (FID commands)
            if content.strip().startswith(('!Add', '!Remove')):
                return

            # Player details are useful in registration channels too. The
            # registration cogs may reject enrollment, but should not suppress
            # this independent lookup response.
            m = re.search(r"\b(\d{8,9})\b", content)
            if not m:
                return

            fid = m.group(1)
            # Delegate handling to shared handler so other code (like app.py) can reuse it
            await self.handle_fid_message(message, fid)
        except Exception as outer_e:
            self.logger.exception("Unexpected error in playerinfo on_message: %s", outer_e)

    async def handle_fid_message(self, message: discord.Message, fid: str):
        """Shared handler to perform the API lookup and reply with embed.

        This is separated so external code (like app.py's on_message)
        can invoke it directly when they detect a raw 8- or 9-digit message.
        """
        thinking_msg = None
        try:
            # Avoid running twice on the same message (app.py may delegate and
            # the cog may also receive the event). Mark message when handled.
            if getattr(message, '_playerinfo_handled', False):
                return
            try:
                message._playerinfo_handled = True
            except Exception:
                pass

            # Log detection so we can trace when message-based lookups run
            channel_type = 'DM' if isinstance(message.channel, discord.DMChannel) else f'GUILD:{getattr(message.guild, "id", "unknown")}'
            self.logger.info("playerinfo (message) detected fid=%s from user=%s in %s", fid, getattr(message.author, 'id', 'unknown'), channel_type)

            # Show thinking animation
            try:
                # Create a simple thinking embed
                thinking_embed = discord.Embed(
                    title="🤖 Processing...",
                    description=f"```\n{self._thinking_animation.generate_binary_frame(24)}\n```\n*{self._thinking_animation.generate_status_text()}*",
                    color=0x9b59b6
                )
                self._set_embed_footer(thinking_embed, message)
                thinking_embed.set_thumbnail(url="https://i.postimg.cc/fLLWWSKq/ezgif-278f9fa56d75db.gif")
                thinking_msg = await message.reply(embed=thinking_embed, mention_author=False)
            except Exception as e:
                self.logger.debug(f"Failed to show thinking animation: {e}")

            # Query both sources independently: Oracle is authoritative for the
            # player profile, while Atlas supplies coordinates and can provide
            # a useful profile when Oracle is unavailable.
            oracle_profile, atlas_profile = await asyncio.gather(
                fetch_oracle_player_info(fid, timeout=12),
                fetch_atlas_player_profile(fid, timeout=12),
                return_exceptions=True,
            )
            if isinstance(oracle_profile, Exception):
                self.logger.warning("WoS Oracle lookup failed for fid=%s: %s", fid, oracle_profile)
                oracle_profile = None
            if isinstance(atlas_profile, Exception):
                self.logger.warning("WoS Atlas lookup failed for fid=%s: %s", fid, atlas_profile)
                atlas_profile = None

            player_profile = self._merge_player_profiles(oracle_profile, atlas_profile)
            if isinstance(player_profile, dict):
                player_embed = self._build_oracle_embed(fid, player_profile, message)
                await self._add_atlas_coordinates(player_embed, fid, atlas_profile)
                if thinking_msg:
                    try:
                        await thinking_msg.delete()
                    except Exception:
                        pass
                await message.reply(embed=player_embed, mention_author=False)
                return

            # prepare request pieces
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": "https://wos-giftcode-api.centurygame.com",
            }

            async with self._sem:
                current_time = int(time.time() * 1000)
                form = f"fid={fid}&time={current_time}"
                sign = hashlib.md5((form + SECRET).encode("utf-8")).hexdigest()
                payload = f"sign={sign}&{form}"

                try:
                    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_context)) as session:
                        async with session.post(API_URL, data=payload, headers=headers, timeout=20) as resp:
                            text = await resp.text()
                            try:
                                js = await resp.json()
                            except Exception:
                                self.logger.debug("playerinfo (message) invalid json for fid=%s: %s", fid, text)
                                if thinking_msg:
                                    try:
                                        await thinking_msg.delete()
                                    except Exception:
                                        pass
                                return
                except Exception as e:
                    self.logger.debug("playerinfo (message) network error for fid=%s: %s", fid, e)
                    if thinking_msg:
                        try:
                            await thinking_msg.delete()
                        except Exception:
                            pass
                    return

            # Log API result for debugging (don't include full payload)
            try:
                code = js.get('code') if isinstance(js, dict) else None
                nick = js.get('data', {}).get('nickname') if isinstance(js, dict) else None
                self.logger.info("playerinfo (message) api result for fid=%s: code=%s nickname=%s", fid, code, nick)
            except Exception:
                self.logger.debug("playerinfo (message) unable to parse api result for fid=%s", fid)

            # If API did not return code 0, react with ❌ and stop
            if not js or js.get("code") != 0:
                try:
                    # Delete thinking message and add reaction
                    if thinking_msg:
                        await thinking_msg.delete()
                except Exception:
                    pass
                return

            # Build embed similarly to the slash command
            data = js.get('data', {})
            nickname = data.get('nickname', 'Unknown')
            kid = data.get('kid', 'N/A')
            stove_lv = data.get('stove_lv')
            stove_icon = data.get('stove_lv_content')
            avatar = data.get('avatar_image')

            try:
                lv_int = int(stove_lv) if stove_lv is not None else None
            except Exception:
                lv_int = None
            fc = map_furnace(lv_int)

            embed = discord.Embed(colour=discord.Colour.blurple())
            # author
            try:
                author_name = f"{nickname}"
                if stove_icon:
                    p = urllib.parse.urlparse(stove_icon)
                    if p.scheme in ("http", "https") and p.netloc:
                        embed.set_author(name=author_name, icon_url=stove_icon)
                    else:
                        embed.set_author(name=author_name)
                else:
                    embed.set_author(name=author_name)
            except Exception:
                embed.set_author(name=nickname)

            # thumbnail
            try:
                if avatar:
                    p2 = urllib.parse.urlparse(avatar)
                    if p2.scheme in ("http", "https") and p2.netloc:
                        embed.set_thumbnail(url=avatar)
            except Exception:
                pass

            if lv_int is None:
                furnace_display = f"```{stove_lv or 'N/A'}```"
            else:
                furnace_display = f"```{fc or lv_int}```"

            pid_display = f"```{fid}```"
            raw_state = str(kid or "N/A")
            if raw_state.startswith("#"):
                state_val = f"```{raw_state}```"
            else:
                state_val = f"```#{raw_state}```"

            embed.add_field(name="🪪 Player ID", value=pid_display, inline=True)
            embed.add_field(name="🏠 STATE", value=state_val, inline=True)
            embed.add_field(name="Furnace Level", value=furnace_display, inline=True)

            # If this fid exists in our local users DB and is linked to an alliance,
            # include the alliance name in the embed. This mirrors the message
            # handler behavior and ensures the slash command shows the same info.
            try:
                alliance_name = None
                alliance_val = None

                # Try Mongo first
                if mongo_enabled() and AllianceMembersAdapter:
                    try:
                        member = AllianceMembersAdapter.get_member(str(fid))
                        if member:
                            alliance_val = member.get('alliance') or member.get('alliance_id')
                            if alliance_val:
                                alliance_val = str(alliance_val)
                    except Exception:
                        pass
                
                # Fallback to SQLite if not found in Mongo
                if not alliance_val:
                    with sqlite3.connect('db/users.sqlite') as users_db:
                        cur = users_db.cursor()
                        cur.execute('SELECT alliance FROM users WHERE fid = ?', (fid,))
                        row = cur.fetchone()
                        if row and row[0] is not None:
                            alliance_val = str(row[0])

                if alliance_val:
                    # If alliance_val looks like an integer id, try to resolve name using SQLite (names are static mostly)
                    if alliance_val.isdigit():
                        try:
                            with sqlite3.connect('db/alliance.sqlite') as a_db:
                                ac = a_db.cursor()
                                ac.execute('SELECT name FROM alliance_list WHERE alliance_id = ?', (int(alliance_val),))
                                arow = ac.fetchone()
                                if arow:
                                    alliance_name = arow[0]
                                else:
                                    alliance_name = alliance_val
                        except Exception:
                            alliance_name = alliance_val
                    else:
                        alliance_name = alliance_val

                if alliance_name:
                    embed.add_field(name="🏰 Alliance", value=f"```{alliance_name}```", inline=True)
            except Exception:
                # non-critical; ignore DB lookup failures
                pass
            
            await self._add_atlas_coordinates(embed, fid)
            self._set_embed_footer(embed, message)

            try:
                # Delete thinking message and send actual player info
                if thinking_msg:
                    await thinking_msg.delete()
                await message.reply(embed=embed, mention_author=False)
            except Exception as send_err:
                self.logger.debug("Failed to send playerinfo reply: %s", send_err)
        except Exception as outer_e:
            self.logger.exception("Unexpected error in playerinfo handler: %s", outer_e)
            if thinking_msg:
                try:
                    failure_embed = discord.Embed(
                        title="Player lookup unavailable",
                        description="WoS Oracle and WoS Atlas could not return player details right now. Please try again shortly.",
                        color=discord.Color.orange(),
                    )
                    await thinking_msg.edit(embed=failure_embed)
                except Exception:
                    pass

    def _build_oracle_embed(self, fid: str, profile: dict, context) -> discord.Embed:
        """Format a WoSOracle player profile for the channel lookup response."""
        player = profile.get("player") or profile.get("data") or profile
        if not isinstance(player, dict):
            raise WoSOracleError("The service returned an unexpected player profile")

        def pick(*keys):
            for key in keys:
                value = player.get(key)
                if value not in (None, "", [], {}):
                    return value
            return None

        def label(value):
            if isinstance(value, dict):
                return value.get("name") or value.get("username") or value.get("id") or value.get("number")
            return value

        nickname = str(pick("username", "name", "nickname", "chief_name") or f"Player {fid}")
        state = label(pick("state", "state_id", "kid", "kingdom", "kingdom_id"))
        state = str(state) if state is not None else "Unknown"
        if state.isdigit():
            state = f"#{state}"

        alliance = pick("alliance", "alliance_name", "alliance_abbr", "alliance_tag")
        alliance_name = label(alliance)
        alliance_abbr = alliance.get("abbr") or alliance.get("tag") if isinstance(alliance, dict) else pick("alliance_abbr", "alliance_tag")
        alliance_rank = pick("alliance_rank")
        alliance_role = pick("alliance_role", "role")
        furnace = pick("furnace_level", "furnace_lv", "furnace", "stove_level", "stove_lv", "town_hall_level")
        power = pick("power", "total_power", "player_power")
        vip = pick("vip", "vip_level", "vip_lv", "vipLevel")
        kills = pick("kills", "kill_count", "total_kills", "totalKills")
        avatar = pick("avatar_url", "avatarUrl", "avatar_image", "avatar", "portrait", "profile_image", "icon_url")
        stove_icon = pick("stove_lv_content", "furnace_icon", "furnace_icon_url", "furnace_image")

        def image_url(value):
            if isinstance(value, dict):
                value = value.get("url") or value.get("src") or value.get("image") or value.get("path")
            if not isinstance(value, str) or not value.strip():
                return None
            value = value.strip()
            if value.startswith("//"):
                value = "https:" + value
            return urllib.parse.urljoin("https://wosoracle.com/", value)

        embed = discord.Embed(
            colour=discord.Colour.blurple(),
            url=f"https://wosoracle.com/player/{fid}",
        )
        author_icon = image_url(stove_icon)
        embed.set_author(name=nickname, **({"icon_url": author_icon} if author_icon else {}))
        avatar_url = image_url(avatar)
        if avatar_url and avatar_url.startswith(("https://", "http://")):
            embed.set_thumbnail(url=avatar_url)

        alliance_text = str(alliance_name or "Unknown")
        if alliance_abbr:
            alliance_text = f"[{alliance_abbr}] {alliance_text}"
        if alliance_role:
            alliance_text += f" · {alliance_role}"
        if alliance_rank is not None:
            rank_match = re.fullmatch(r"R?([1-5])", str(alliance_rank).strip(), re.IGNORECASE)
            if rank_match:
                alliance_text += f" · R{rank_match.group(1)}"

        def pretty(value):
            if isinstance(value, int):
                return f"{value:,}"
            if isinstance(value, float):
                return f"{value:,.0f}"
            return str(label(value))

        furnace_display = pretty(furnace) if furnace is not None else None
        if furnace is not None:
            try:
                furnace_level = int(furnace)
                furnace_display = map_furnace(furnace_level) or str(furnace_level)
            except (TypeError, ValueError):
                pass

        embed.add_field(name="🪪 Player ID", value=f"```{fid}```", inline=True)
        embed.add_field(name="🏠 STATE", value=f"```{state}```", inline=True)
        if furnace is not None:
            embed.add_field(name="Furnace Level", value=f"```{furnace_display}```", inline=True)
        embed.add_field(name="🏰 Alliance", value=f"```{alliance_text[:1000]}```", inline=True)
        if power is not None:
            embed.add_field(name="⚡ Power", value=f"```{pretty(power)}```", inline=True)
        if vip is not None:
            embed.add_field(name="💎 VIP lvl", value=f"```{pretty(vip)}```", inline=True)
        if kills is not None:
            embed.add_field(name="⚔️ Kills", value=f"```{pretty(kills)}```", inline=True)
        self._set_embed_footer(embed, context)
        return embed

    def _set_embed_footer(self, embed: discord.Embed, context):
        """Set the personalized footer for the bot embeds."""
        guild_name = getattr(context, 'guild', None)
        guild_name = guild_name.name if guild_name else "Whiteout Survival"
        footer_text = f"Whiteout Survival || {guild_name} ❄️"
        icon_url = "https://cdn.discordapp.com/attachments/1435569370389807144/1436745053442805830/unnamed_5.png"
        embed.set_footer(text=footer_text, icon_url=icon_url)

    @discord.app_commands.command(
        name="playerinfo",
        description="Get player info by 8- or 9-digit player id. Accepts comma-separated list (max 30).",
    )
    @app_commands.describe(player_id="Single 8- or 9-digit id or comma-separated list of ids (max 30)")
    @command_animation
    async def playerinfo(self, interaction: discord.Interaction, player_id: str):
        # log invocation
        user_id = getattr(interaction.user, 'id', 'unknown')
        self.logger.info("/playerinfo invoked by user %s for player_id=%s", user_id, player_id)

        # Split comma-separated list, trim spaces, enforce limits
        ids = [p.strip() for p in str(player_id).split(',') if p.strip()]
        if not ids:
            await interaction.response.send_message("No player ids provided.", ephemeral=True)
            return
        if len(ids) > 30:
            if interaction.response.is_done():
                await interaction.followup.send("Too many ids — max 30 at a time.", ephemeral=True)
            else:
                await interaction.response.send_message("Too many ids — max 30 at a time.", ephemeral=True)
            return

        # Validate each id individually
        invalid = [p for p in ids if not re.fullmatch(r"\d{8,9}", p)]
        if invalid:
            if interaction.response.is_done():
                await interaction.followup.send(
                    f"The following ids are invalid (must be 8 or 9 digits): {', '.join(invalid)}",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    f"The following ids are invalid (must be 8 or 9 digits): {', '.join(invalid)}",
                    ephemeral=True,
                )
            return

        # Defer before external lookups so even slow bridge responses fit Discord's
        # interaction deadline. Results are delivered as follow-up embeds.
        if not interaction.response.is_done():
            await interaction.response.defer()

        # Prefer the Cloudflare-backed WoS Oracle session for every slash lookup.
        # IDs unavailable from Oracle continue through the legacy Century Games path.
        oracle_sem = asyncio.Semaphore(3)

        async def fetch_oracle(fid: str):
            async with oracle_sem:
                try:
                    oracle_profile, atlas_profile = await asyncio.gather(
                        fetch_oracle_player_info(fid, timeout=12),
                        fetch_atlas_player_profile(fid, timeout=12),
                        return_exceptions=True,
                    )
                    if isinstance(oracle_profile, Exception):
                        oracle_profile = None
                    if isinstance(atlas_profile, Exception):
                        atlas_profile = None
                    profile = self._merge_player_profiles(oracle_profile, atlas_profile)
                    if profile is None:
                        raise WoSOracleError("Neither player profile service returned data")
                    embed = self._build_oracle_embed(fid, profile, interaction)
                    await self._add_atlas_coordinates(embed, fid, atlas_profile)
                    return fid, embed, None
                except Exception as exc:
                    return fid, None, exc

        oracle_results = await asyncio.gather(*(fetch_oracle(fid) for fid in ids))
        fallback_ids = []
        for fid, embed, error in oracle_results:
            if embed is not None:
                await interaction.followup.send(embed=embed)
            else:
                self.logger.info("Player lookup unavailable for fid=%s: %s", fid, error)
                fallback_ids.append(fid)
        if not fallback_ids:
            return
        ids = fallback_ids

        # prepare shared SSL/context and headers
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": "https://wos-giftcode-api.centurygame.com",
        }

        # URL validator used by the embed builder
        def _is_valid_url(u: str) -> bool:
            if not u:
                return False
            try:
                p = urllib.parse.urlparse(u)
                return p.scheme in ("http", "https") and bool(p.netloc)
            except Exception:
                return False

        # Concurrency limiter to avoid hammering the API
        sem = asyncio.Semaphore(10)

        async def fetch_one(session: aiohttp.ClientSession, fid: str) -> tuple[str, Optional[dict], Optional[Exception]]:
            """Fetch player info for a single fid. Returns (fid, json, exception).
            json is None if request/json parsing failed; exception is set on network errors.
            """
            async with sem:
                try:
                    current_time = int(time.time() * 1000)
                    form = f"fid={fid}&time={current_time}"
                    sign = hashlib.md5((form + SECRET).encode("utf-8")).hexdigest()
                    payload = f"sign={sign}&{form}"
                    # Redact the signed payload from logs to avoid exposing SECRET.
                    self.logger.debug("playerinfo request for %s time=%s", fid, current_time)
                    async with session.post(API_URL, data=payload, headers=headers, timeout=20) as resp:
                        text = await resp.text()
                        try:
                            js = await resp.json()
                        except Exception:
                            self.logger.warning("Invalid JSON response for fid=%s: %s", fid, text)
                            return fid, None, None
                        return fid, js, None
                except Exception as e:
                    self.logger.exception("Request error for fid=%s", fid)
                    return fid, None, e

        # helper to build embed from API data (or from error cases)
        def build_embed_for(fid: str, js: Optional[dict ]) -> discord.Embed:
            # default empty embed in case of network/json error
            embed = discord.Embed(colour=discord.Colour.blurple())
            if js is None:
                embed.description = "No valid response from API."
                self._set_embed_footer(embed, interaction)
                return embed

            if js.get("code") != 0:
                api_msg_raw = js.get('msg') or ''
                api_msg = str(api_msg_raw).lower().replace('_', ' ')
                if ('role' in api_msg and ('not' in api_msg and ('exist' in api_msg or 'found' in api_msg))) \
                   or (('not' in api_msg) and ('exist' in api_msg or 'found' in api_msg)):
                    embed.description = "Player not found — check the 8- or 9-digit player ID and try again."
                    self._set_embed_footer(embed, interaction)
                    return embed
                else:
                    embed.description = f"API error: {api_msg_raw}"
                    self._set_embed_footer(embed, interaction)
                    return embed

            data = js.get('data', {})
            nickname = data.get('nickname', 'Unknown')
            kid = data.get('kid', 'N/A')
            stove_lv = data.get('stove_lv')
            stove_icon = data.get('stove_lv_content')
            avatar = data.get('avatar_image')

            # compute furnace label
            try:
                lv_int = int(stove_lv) if stove_lv is not None else None
            except Exception:
                lv_int = None
            fc = map_furnace(lv_int)

            # Build embed
            embed = discord.Embed(colour=discord.Colour.blurple())

            # Set author to nickname with stove icon if valid
            try:
                author_name = f"{nickname}"
                if stove_icon and _is_valid_url(stove_icon):
                    embed.set_author(name=author_name, icon_url=stove_icon)
                else:
                    embed.set_author(name=author_name)
            except Exception:
                embed.set_author(name=author_name)

            # Thumbnail
            if avatar and _is_valid_url(avatar):
                try:
                    embed.set_thumbnail(url=avatar)
                except Exception:
                    pass

            # Furnace display rules: only FC label when present, else numeric.
            if lv_int is None:
                furnace_display = f"```{stove_lv or 'N/A'}```"
            else:
                if fc:
                    furnace_display = f"```{fc}```"
                else:
                    furnace_display = f"```{lv_int}```"

            pid_display = f"```{fid}```"
            raw_state = str(kid or "N/A")
            if raw_state.startswith("#"):
                state_val = f"```{raw_state}```"
            else:
                state_val = f"```#{raw_state}```"

            embed.add_field(name="🪪 Player ID", value=pid_display, inline=True)
            embed.add_field(name="🏠 STATE", value=state_val, inline=True)
            embed.add_field(name="Furnace Level", value=furnace_display, inline=True)

            self._set_embed_footer(embed, interaction)

            return embed

        # perform requests reusing a single session
        results = []
        try:
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_context)) as session:
                tasks = [asyncio.create_task(fetch_one(session, fid)) for fid in ids]
                for coro in asyncio.as_completed(tasks):
                    fid, js, exc = await coro
                    if exc:
                        self.logger.warning("Network error for fid=%s: %s", fid, exc)
                        await interaction.followup.send(
                            f"Player lookup is temporarily unavailable for {fid}. Please try again shortly.",
                            ephemeral=True,
                        )
                        continue
                    # build embed from js (may be None if invalid json)
                    embed = build_embed_for(fid, js)
                    if js and js.get("code") == 0:
                        oracle_legacy, atlas_profile = await asyncio.gather(
                            fetch_oracle_player_info(fid, timeout=12),
                            fetch_atlas_player_profile(fid, timeout=12),
                            return_exceptions=True,
                        )
                        if isinstance(oracle_legacy, Exception):
                            oracle_legacy = None
                        if isinstance(atlas_profile, Exception):
                            atlas_profile = None
                        merged_profile = self._merge_player_profiles(oracle_legacy, atlas_profile)
                        if merged_profile:
                            embed = self._build_oracle_embed(fid, merged_profile, interaction)
                        await self._add_atlas_coordinates(embed, fid, atlas_profile)
                    await interaction.followup.send(embed=embed)
        except Exception as e:
            self.logger.exception("Unexpected error during batch fetch")
            if interaction.response.is_done():
                await interaction.followup.send(f"Unexpected error: {e}", ephemeral=True)
            else:
                await interaction.response.send_message(f"Unexpected error: {e}", ephemeral=True)
            return

    @discord.app_commands.command(
        name="editplayerinfo",
        description="Update an existing playerinfo message with new player data.",
    )
    @app_commands.describe(
        message_id="The ID of the message to edit (must be in the current channel)",
        player_id="The new 8- or 9-digit player ID to fetch info for"
    )
    async def editplayerinfo(self, interaction: discord.Interaction, message_id: str, player_id: str):
        """Edit an existing message with new player info."""
        user_id = getattr(interaction.user, 'id', 'unknown')
        self.logger.info("/editplayerinfo invoked by user %s for msg=%s player_id=%s", user_id, message_id, player_id)

        # Validate player_id
        if not re.fullmatch(r"\d{8,9}", player_id):
            await interaction.response.send_message("Invalid player ID. Must be 8 or 9 digits.", ephemeral=True)
            return

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        # Fetch message
        try:
            m_id = int(message_id)
            msg = await interaction.channel.fetch_message(m_id)
        except discord.NotFound:
            await interaction.followup.send("Message not found in this channel.", ephemeral=True)
            return
        except (ValueError, discord.HTTPException) as e:
            await interaction.followup.send(f"Error fetching message: {e}", ephemeral=True)
            return

        if msg.author.id != self.bot.user.id:
            await interaction.followup.send("I can only edit my own messages.", ephemeral=True)
            return

        # Combine the Cloudflare-backed Oracle session and Atlas profile.
        try:
            oracle_profile, atlas_profile = await asyncio.gather(
                fetch_oracle_player_info(player_id, timeout=12),
                fetch_atlas_player_profile(player_id, timeout=12),
                return_exceptions=True,
            )
            if isinstance(oracle_profile, Exception):
                oracle_profile = None
            if isinstance(atlas_profile, Exception):
                atlas_profile = None
            profile = self._merge_player_profiles(oracle_profile, atlas_profile)
            if profile is None:
                raise WoSOracleError("Neither player profile service returned data")
            embed = self._build_oracle_embed(player_id, profile, interaction)
            await self._add_atlas_coordinates(embed, player_id, atlas_profile)
            await msg.edit(embed=embed)
            await interaction.followup.send(f"Updated message {message_id}.", ephemeral=True)
            return
        except WoSOracleError as e:
            self.logger.info("Player lookup unavailable for fid=%s; using fallback: %s", player_id, e)

        # Fallback to the existing Century Games lookup when Oracle is unavailable.
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": "https://wos-giftcode-api.centurygame.com",
        }

        js = None
        try:
            current_time = int(time.time() * 1000)
            form = f"fid={player_id}&time={current_time}"
            sign = hashlib.md5((form + SECRET).encode("utf-8")).hexdigest()
            payload = f"sign={sign}&{form}"
            
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_context)) as session:
                async with session.post(API_URL, data=payload, headers=headers, timeout=20) as resp:
                    text = await resp.text()
                    try:
                        js = await resp.json()
                    except Exception:
                        self.logger.warning("Invalid JSON response for fid=%s: %s", player_id, text)
        except Exception as e:
            self.logger.exception("Request error for fid=%s", player_id)
            await interaction.followup.send(f"Network error fetching player info: {e}", ephemeral=True)
            return

        # Helper for URL validation
        def _is_valid_url(u: str) -> bool:
            if not u:
                return False
            try:
                p = urllib.parse.urlparse(u)
                return p.scheme in ("http", "https") and bool(p.netloc)
            except Exception:
                return False

        # Build embed
        embed = discord.Embed(colour=discord.Colour.blurple())
        
        if js is None:
            embed.description = "No valid response from API."
            self._set_embed_footer(embed, interaction)
        elif js.get("code") != 0:
            api_msg_raw = js.get('msg') or ''
            api_msg = str(api_msg_raw).lower().replace('_', ' ')
            if ('role' in api_msg and ('not' in api_msg and ('exist' in api_msg or 'found' in api_msg))) \
               or (('not' in api_msg) and ('exist' in api_msg or 'found' in api_msg)):
                embed.description = "Player not found — check the 8- or 9-digit player ID and try again."
            else:
                embed.description = f"API error: {api_msg_raw}"
            self._set_embed_footer(embed, interaction)
        else:
            # Success
            data = js.get('data', {})
            nickname = data.get('nickname', 'Unknown')
            kid = data.get('kid', 'N/A')
            stove_lv = data.get('stove_lv')
            stove_icon = data.get('stove_lv_content')
            avatar = data.get('avatar_image')

            # compute furnace label
            try:
                lv_int = int(stove_lv) if stove_lv is not None else None
            except Exception:
                lv_int = None
            fc = map_furnace(lv_int)

            # Set author
            try:
                author_name = f"{nickname}"
                if stove_icon and _is_valid_url(stove_icon):
                    embed.set_author(name=author_name, icon_url=stove_icon)
                else:
                    embed.set_author(name=author_name)
            except Exception:
                embed.set_author(name=author_name)

            # Thumbnail
            if avatar and _is_valid_url(avatar):
                try:
                    embed.set_thumbnail(url=avatar)
                except Exception:
                    pass

            # Furnace display
            if lv_int is None:
                furnace_display = f"```{stove_lv or 'N/A'}```"
            else:
                if fc:
                    furnace_display = f"```{fc}```"
                else:
                    furnace_display = f"```{lv_int}```"

            pid_display = f"```{player_id}```"
            raw_state = str(kid or "N/A")
            if raw_state.startswith("#"):
                state_val = f"```{raw_state}```"
            else:
                state_val = f"```#{raw_state}```"

            embed.add_field(name="🪪 Player ID", value=pid_display, inline=True)
            embed.add_field(name="🏠 STATE", value=state_val, inline=True)
            embed.add_field(name="Furnace Level", value=furnace_display, inline=True)

            # Alliance lookup
            try:
                alliance_name = None
                alliance_val = None

                # Try Mongo first
                if mongo_enabled() and AllianceMembersAdapter:
                    try:
                        member = AllianceMembersAdapter.get_member(str(player_id))
                        if member:
                            alliance_val = member.get('alliance') or member.get('alliance_id')
                            if alliance_val:
                                alliance_val = str(alliance_val)
                    except Exception:
                        pass

                # Fallback to SQLite
                if alliance_val is None:
                    with sqlite3.connect('db/users.sqlite') as users_db:
                        cur = users_db.cursor()
                        cur.execute('SELECT alliance FROM users WHERE fid = ?', (player_id,))
                        row = cur.fetchone()
                        if row and row[0] is not None:
                            alliance_val = str(row[0])

                if alliance_val:
                    if alliance_val.isdigit():
                        try:
                            with sqlite3.connect('db/alliance.sqlite') as a_db:
                                ac = a_db.cursor()
                                ac.execute('SELECT name FROM alliance_list WHERE alliance_id = ?', (int(alliance_val),))
                                arow = ac.fetchone()
                                if arow:
                                    alliance_name = arow[0]
                                else:
                                    alliance_name = alliance_val
                        except Exception:
                            alliance_name = alliance_val
                    else:
                        alliance_name = alliance_val

                if alliance_name:
                    embed.add_field(name="🏰 Alliance", value=f"```{alliance_name}```", inline=True)
            except Exception:
                pass

            await self._add_atlas_coordinates(embed, player_id)

            # Footer
            self._set_embed_footer(embed, interaction)

        # Update message
        try:
            await msg.edit(embed=embed)
            await interaction.followup.send(f"Updated message {message_id}.", ephemeral=True)
        except Exception as e:
            self.logger.error("Failed to edit message %s: %s", message_id, e)
            await interaction.followup.send(f"Failed to edit message: {e}", ephemeral=True)


async def setup(bot: commands.Bot):
    """Add the cog to the bot.

    Command syncing is handled centrally by the bot process (see `app.py`),
    matching how other cogs in this project register their commands. This
    keeps command registration consistent and avoids per-cog side-effects.
    """
    await bot.add_cog(PlayerInfoCog(bot))
