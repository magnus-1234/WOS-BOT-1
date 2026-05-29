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

            # Skip channels that have dedicated cogs handling FID input
            # (auto-redeem channels and ID-registration channels)
            if self._is_managed_channel(message):
                return
            
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
            thinking_msg = None
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
                                return
                except Exception as e:
                    self.logger.debug("playerinfo (message) network error for fid=%s: %s", fid, e)
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
                    await message.add_reaction("❌")
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
                        embed = discord.Embed(colour=discord.Colour.blurple(), description=f"Request error: {exc}")
                        self._set_embed_footer(embed, interaction)
                        await interaction.followup.send(embed=embed)
                        continue
                    # build embed from js (may be None if invalid json)
                    embed = build_embed_for(fid, js)
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

        # Fetch data
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
