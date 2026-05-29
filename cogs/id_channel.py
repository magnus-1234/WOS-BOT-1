import discord
from discord.ext import commands, tasks
import sqlite3
from datetime import datetime, timedelta
import os
import time
import hashlib
import aiohttp
import ssl

from db.mongo_adapters import mongo_enabled, AllianceMembersAdapter, IDChannelsAdapter
from admin_utils import get_level_mapping, format_furnace_level

SECRET = "tB87#kPtkxqOS2"

class IDChannel(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.setup_database()
        self.log_directory = 'log'
        if not os.path.exists(self.log_directory):
            os.makedirs(self.log_directory)
            
        self.level_mapping = get_level_mapping()

    def setup_database(self):
        """Initialize ID channel database"""
        if not os.path.exists('db'):
            os.makedirs('db')
            
        conn = sqlite3.connect('db/id_channel.sqlite')
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS id_channels
                     (guild_id INTEGER, 
                      alliance_id INTEGER,
                      channel_id INTEGER,
                      created_at TEXT,
                      created_by INTEGER,
                      UNIQUE(guild_id, channel_id))''')
        conn.commit()
        conn.close()

    async def log_action(self, action_type: str, user_id: int, guild_id: int, details: dict):
        """Log actions to file"""
        try:
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            log_file_path = os.path.join(self.log_directory, 'id_channel_log.txt')
            
            guild = self.bot.get_guild(guild_id)
            guild_name = guild.name if guild else "Unknown Server"
            
            user_name = f"User {user_id}"
            user = self.bot.get_user(user_id)
            if user:
                user_name = str(user)
            
            with open(log_file_path, 'a', encoding='utf-8') as log_file:
                log_file.write(f"\n[{timestamp}] {action_type}\n")
                log_file.write(f"User: {user_name} (ID: {user_id})\n")
                log_file.write(f"Server: {guild_name} (ID: {guild_id})\n")
                for key, value in details.items():
                    log_file.write(f"  {key}: {value}\n")
        except Exception as e:
            print(f"Logging error: {e}")

    def _log_debug(self, message):
        """Log debug messages"""
        try:
            with open('debug_id_channel.log', 'a', encoding='utf-8') as f:
                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                f.write(f"[{timestamp}] {message}\n")
        except Exception:
            print(message)

    def _upsert_member_from_api(self, fid: int, nickname: str, furnace_lv: int, kid, stove_lv_content, alliance_id: int, avatar_image=None) -> bool:
        """Save member data to database"""
        try:
            member_doc = {
                'fid': str(fid),
                'nickname': nickname,
                'furnace_lv': int(furnace_lv) if furnace_lv is not None else 0,
                'stove_lv': int(furnace_lv) if furnace_lv is not None else 0,
                'stove_lv_content': stove_lv_content,
                'kid': kid,
                'alliance': int(alliance_id),
                'alliance_id': int(alliance_id),
                'avatar_image': avatar_image,
            }
            
            # Try MongoDB first
            try:
                if mongo_enabled() and AllianceMembersAdapter is not None:
                    result = AllianceMembersAdapter.upsert_member(str(fid), member_doc)
                    if result:
                        return True
            except Exception as e:
                self._log_debug(f"Mongo upsert exception: {e}")
            
            # Fallback to SQLite
            try:
                with sqlite3.connect('db/users.sqlite') as users_db:
                    cursor = users_db.cursor()
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS users (
                            fid TEXT PRIMARY KEY,
                            nickname TEXT,
                            furnace_lv INTEGER,
                            stove_lv_content TEXT,
                            kid TEXT,
                            alliance INTEGER,
                            avatar_image TEXT
                        )
                    """)
                    cursor.execute("""
                        INSERT OR REPLACE INTO users 
                        (fid, nickname, furnace_lv, stove_lv_content, kid, alliance, avatar_image)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (
                        str(fid),
                        nickname,
                        furnace_lv,
                        stove_lv_content,
                        kid,
                        alliance_id,
                        avatar_image
                    ))
                    users_db.commit()
                    return True
            except Exception as e:
                self._log_debug(f"Failed to save member {fid} to SQLite: {e}")
                return False
                
        except Exception as e:
            self._log_debug(f"Error in _upsert_member_from_api: {e}")
            return False

    def format_furnace_level(self, furnace_lv):
        """Format furnace level display using shared utility"""
        return format_furnace_level(furnace_lv)

    async def process_fid(self, message: discord.Message, fid: int, alliance_id: int):
        """Process a FID from an ID channel"""
        try:
            # Check if already processed (has bot reaction)
            for reaction in message.reactions:
                async for user in reaction.users():
                    if user == self.bot.user:
                        return
            
            # Import LoginHandler
            from cogs.login_handler import LoginHandler
            login_handler = LoginHandler()
            
            # Fetch player data
            result = await login_handler.fetch_player_data(str(fid))
            
            if result['status'] == 'success' and result['data']:
                player_data = result['data']
                nickname = player_data.get('nickname', 'Unknown')
                furnace_lv = player_data.get('stove_lv', 0)
                kid = player_data.get('kid', '')
                stove_lv_content = player_data.get('stove_lv_content', '')
                avatar_image = player_data.get('avatar_image', '')
                
                # Cross-server Auto-Redeem Check
                from db.mongo_adapters import AutoRedeemMembersAdapter
                if mongo_enabled() and AutoRedeemMembersAdapter:
                    try:
                        existing_gid = await AutoRedeemMembersAdapter.find_member_anywhere_async(str(fid))
                        if existing_gid and existing_gid != message.guild.id:
                            try:
                                guild = self.bot.get_guild(existing_gid)
                                guild_name = guild.name if guild else f"Server {existing_gid}"
                            except:
                                guild_name = "another server"
                            
                            await message.add_reaction('⚠️')
                            embed = discord.Embed(
                                title="⚠️ Already Enrolled Elsewhere",
                                description=(
                                    f"❌ **{nickname}** (`{fid}`) is already enrolled for "
                                    f"automated gift codes in **{guild_name}**.\n\n"
                                    "A Player ID can only be active in one server at a time to prevent duplicates."
                                ),
                                color=0xED4245 # Red
                            )
                            if avatar_image and str(avatar_image).startswith('http'):
                                embed.set_thumbnail(url=avatar_image)
                            
                            server_name = message.guild.name if message.guild else "Whiteout Survival"
                            embed.set_footer(text=f"Whiteout Survival || {server_name} ❄️")
                            
                            await message.reply(embed=embed)
                            return
                    except Exception as e:
                        self._log_debug(f"Cross-server check error: {e}")

                # Save to database
                success = self._upsert_member_from_api(
                    fid, nickname, furnace_lv, kid, 
                    stove_lv_content, alliance_id, avatar_image
                )
                
                if success:
                    # Add success reaction
                    await message.add_reaction('✅')
                    
                    # Premium Embed Style as requested by User
                    formatted_fc = self.format_furnace_level(furnace_lv)
                    
                    success_embed = discord.Embed(
                        title="✨ Auto-Redeem Registered",
                        description=f"✅ **{nickname}** is now enrolled for automated gift codes.",
                        color=0x2ecc71 # Green
                    )
                    
                    success_embed.add_field(name="Player ID", value=f"`{fid}`", inline=True)
                    success_embed.add_field(name="Furnace", value=f"`{formatted_fc}`", inline=True)
                    
                    # This field indicates auto-redeem is starting (handled by ManageGiftCode cog if same channel)
                    success_embed.add_field(name="🚀 Auto-Processing", value="`Initializing...`", inline=False)

                    if avatar_image and str(avatar_image).startswith('http'):
                        success_embed.set_thumbnail(url=avatar_image)
                    
                    # Standardized footer branding
                    server_name = message.guild.name if message.guild else "Whiteout Survival"
                    success_embed.set_footer(
                        text=f"Whiteout Survival || {server_name} ❄️",
                        icon_url="https://cdn.discordapp.com/attachments/1435569370389807144/1436745053442805830/unnamed_5.png"
                    )

                    await message.reply(embed=success_embed)
                    
                    await self.log_action(
                        "ADD_MEMBER",
                        message.author.id,
                        message.guild.id,
                        {
                            "fid": fid,
                            "nickname": nickname,
                            "alliance_id": alliance_id,
                            "furnace_level": formatted_fc
                        }
                    )
                else:
                    await message.add_reaction('❌')
                    await message.reply("❌ Failed to register FID. Please try again.", delete_after=10)
            else:
                await message.add_reaction('❌')
                await message.reply(f"❌ Player with FID `{fid}` not found.", delete_after=10)
                
        except Exception as e:
            print(f"Error processing FID {fid}: {e}")
            await message.add_reaction('❌')
            await message.reply("❌ An error occurred during the process.", delete_after=10)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listen for FIDs in ID channels"""
        try:
            if message.author.bot or not message.guild:
                return
            
            content = message.content.strip()
            # Extract FID codes
            import re
            fid_pattern = r'\b\d{7,12}\b'
            fids = re.findall(fid_pattern, content)
            
            if not fids:
                return
            
            # Check if current channel is an ID channel
            is_id_channel = False
            alliance_id = 0
            
            # Check MongoDB first
            try:
                if mongo_enabled() and IDChannelsAdapter:
                    config = await IDChannelsAdapter.get_channel_async(message.guild.id)
                    if config and config.get('channel_id') == message.channel.id:
                        is_id_channel = True
                        alliance_id = config.get('alliance_id', 0)
            except Exception:
                pass

            if not is_id_channel:
                # Fallback to SQLite
                with sqlite3.connect('db/id_channel.sqlite') as db:
                    cursor = db.cursor()
                    cursor.execute(
                        "SELECT alliance_id FROM id_channels WHERE channel_id = ?",
                        (message.channel.id,)
                    )
                    row = cursor.fetchone()
                    if row:
                        is_id_channel = True
                        alliance_id = row[0]
            
            if is_id_channel:
                for fid in fids:
                    await self.process_fid(message, int(fid), alliance_id)
        
        except Exception as e:
            print(f"Error in on_message: {e}")

    @commands.Cog.listener()
    async def on_ready(self):
        """Initialize background tasks"""
        if not self.check_channels_loop.is_running():
            self.check_channels_loop.start()

    @tasks.loop(minutes=5)
    async def check_channels_loop(self):
        """Process missed messages in ID channels"""
        try:
            with sqlite3.connect('db/id_channel.sqlite') as db:
                cursor = db.cursor()
                cursor.execute("SELECT channel_id, alliance_id FROM id_channels")
                channels = cursor.fetchall()

            for channel_id, alliance_id in channels:
                channel = self.bot.get_channel(channel_id)
                if not channel:
                    continue

                async for message in channel.history(limit=50):
                    if message.author.bot:
                        continue

                    # Check if already processed
                    has_bot_reaction = False
                    for reaction in message.reactions:
                        async for user in reaction.users():
                            if user == self.bot.user:
                                has_bot_reaction = True
                                break
                        if has_bot_reaction:
                            break
                    if has_bot_reaction:
                        continue

                    content = message.content.strip()
                    if content.isdigit() and 7 <= len(content) <= 12:
                        await self.process_fid(message, int(content), alliance_id)
        except Exception as e:
            print(f"Error in check_channels_loop: {e}")

    async def show_id_channel_menu(self, interaction: discord.Interaction):
        """Management menu for ID channels"""
        try:
            # Check admin permissions
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("❌ Administrator permissions required.", ephemeral=True)
                return

            embed = discord.Embed(
                title="🆔 ID Channel Management",
                description=(
                    "Manage your alliance ID channels here:\n\n"
                    "**Available Operations**\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    "➕ Create new ID channel\n"
                    "🗑️ Delete existing ID channel\n"
                    "📋 View active ID channels\n"
                    "━━━━━━━━━━━━━━━━━━━━━━"
                ),
                color=discord.Color.blue()
            )
            
            view = IDChannelView(self)
            await interaction.response.edit_message(embed=embed, view=view)
        except Exception as e:
            print(f"Error in show_id_channel_menu: {e}")

class IDChannelView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="View Channels", emoji="📋", style=discord.ButtonStyle.secondary, custom_id="view_id_channels", row=1)
    async def view_channels_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            channels = []
            with sqlite3.connect('db/id_channel.sqlite') as db:
                cursor = db.cursor()
                cursor.execute("SELECT channel_id, alliance_id, created_at, created_by FROM id_channels WHERE guild_id = ?", (interaction.guild_id,))
                id_channels = cursor.fetchall()

            with sqlite3.connect('db/alliance.sqlite') as alliance_db:
                alliance_cursor = alliance_db.cursor()
                for channel_id, alliance_id, created_at, created_by in id_channels:
                    alliance_cursor.execute("SELECT name FROM alliance_list WHERE alliance_id = ?", (alliance_id,))
                    alliance_name = alliance_cursor.fetchone()
                    if alliance_name:
                        channels.append((channel_id, alliance_name[0], created_at, created_by))

            if not channels:
                await interaction.response.send_message("❌ No active ID channels found.", ephemeral=True)
                return

            embed = discord.Embed(title="📋 Active ID Channels", color=discord.Color.blue())
            for ch_id, name, created_at, created_by in channels:
                ch = interaction.guild.get_channel(ch_id)
                embed.add_field(
                    name=f"#{ch.name if ch else ch_id}",
                    value=f"**Alliance:** {name}\n**Created:** {created_at}\n**By:** <@{created_by}>",
                    inline=False
                )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as e:
            print(f"View channels error: {e}")

    @discord.ui.button(label="Delete Channel", emoji="🗑️", style=discord.ButtonStyle.danger, custom_id="delete_id_channel", row=0)
    async def delete_channel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            with sqlite3.connect('db/id_channel.sqlite') as db:
                cursor = db.cursor()
                cursor.execute("SELECT channel_id FROM id_channels WHERE guild_id = ?", (interaction.guild_id,))
                rows = cursor.fetchall()

            if not rows:
                await interaction.response.send_message("❌ No active ID channels found.", ephemeral=True)
                return

            options = []
            for row in rows:
                ch = interaction.guild.get_channel(row[0])
                if ch:
                    options.append(discord.SelectOption(label=f"#{ch.name}", value=str(row[0])))

            class ChannelSelect(discord.ui.Select):
                def __init__(self, cog):
                    super().__init__(placeholder="Select channel to delete", options=options)
                    self.cog = cog
                async def callback(self, select_interaction: discord.Interaction):
                    ch_id = int(self.values[0])
                    with sqlite3.connect('db/id_channel.sqlite') as db:
                        cursor = db.cursor()
                        cursor.execute("DELETE FROM id_channels WHERE channel_id = ?", (ch_id,))
                        db.commit()
                    await select_interaction.response.send_message(f"✅ ID Channel <#{ch_id}> deleted.", ephemeral=True)

            view = discord.ui.View()
            view.add_item(ChannelSelect(self.cog))
            await interaction.response.send_message("Select the channel to delete:", view=view, ephemeral=True)
        except Exception as e:
            print(f"Delete channel error: {e}")

    @discord.ui.button(label="Create Channel", emoji="➕", style=discord.ButtonStyle.success, custom_id="create_id_channel", row=0)
    async def create_channel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            with sqlite3.connect('db/alliance.sqlite') as alliance_db:
                cursor = alliance_db.cursor()
                cursor.execute("SELECT alliance_id, name FROM alliance_list")
                alliances = cursor.fetchall()

            if not alliances:
                await interaction.response.send_message("❌ No alliances found.", ephemeral=True)
                return

            options = [discord.SelectOption(label=name, value=str(aid)) for aid, name in alliances]

            class AllianceSelect(discord.ui.Select):
                def __init__(self, cog):
                    super().__init__(placeholder="Select alliance", options=options)
                    self.cog = cog
                async def callback(self, select_interaction: discord.Interaction):
                    aid = int(self.values[0])
                    class ChannelSelect(discord.ui.ChannelSelect):
                        def __init__(self, cog, alliance_id):
                            super().__init__(placeholder="Select text channel", channel_types=[discord.ChannelType.text])
                            self.cog = cog
                            self.aid = alliance_id
                        async def callback(self, ch_interaction: discord.Interaction):
                            ch = self.values[0]
                            with sqlite3.connect('db/id_channel.sqlite') as db:
                                cursor = db.cursor()
                                try:
                                    cursor.execute("INSERT INTO id_channels (guild_id, alliance_id, channel_id, created_at, created_by) VALUES (?, ?, ?, ?, ?)",
                                                 (ch_interaction.guild_id, self.aid, ch.id, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), ch_interaction.user.id))
                                    db.commit()
                                    await ch_interaction.response.send_message(f"✅ <#{ch.id}> is now an ID channel for alliance ID {self.aid}.", ephemeral=True)
                                except sqlite3.IntegrityError:
                                    await ch_interaction.response.send_message("❌ This channel is already an ID channel.", ephemeral=True)

                    view = discord.ui.View()
                    view.add_item(ChannelSelect(self.cog, aid))
                    await select_interaction.response.send_message("Select the channel:", view=view, ephemeral=True)

            view = discord.ui.View()
            view.add_item(AllianceSelect(self.cog))
            await interaction.response.send_message("Select the alliance:", view=view, ephemeral=True)
        except Exception as e:
            print(f"Create channel error: {e}")

    @discord.ui.button(label="Back", emoji="◀️", style=discord.ButtonStyle.secondary, custom_id="back_to_other_features", row=2)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            other_features_cog = self.cog.bot.get_cog("OtherFeatures")
            if other_features_cog:
                await other_features_cog.show_other_features_menu(interaction)
        except Exception as e:
            print(f"Back button error: {e}")

async def setup(bot):
    await bot.add_cog(IDChannel(bot))
