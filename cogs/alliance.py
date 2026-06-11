import discord
from discord import app_commands
from discord.ext import commands
import sqlite3  
import asyncio
from datetime import datetime
from discord.ext import tasks
from typing import List, Dict, Optional
import os
from .login_handler import LoginHandler
from command_animator import command_animation
from bot_activity import publish_bot_activity
from admin_utils import is_admin, is_global_admin, grant_admin_if_discord_admin, is_bot_owner, get_level_mapping, format_furnace_level
from .pagination_helper import ResultsPaginationView
TOPGG_REVIEW_URL = "https://top.gg/bot/1399025185046134866?s=0e3c921b2b5f8"

try:
    from db.mongo_adapters import mongo_enabled, AdminsAdapter, AlliancesAdapter, AllianceSettingsAdapter, AllianceMembersAdapter, FurnaceHistoryAdapter, AllianceMonitoringAdapter, ServerLimitsAdapter, AllianceEventsAdapter
except Exception as import_error:
    # Fallback: If MongoDB adapters fail to import, use SQLite exclusively
    print(f"[WARNING] MongoDB adapters import failed: {import_error}. Using SQLite fallback.")
    mongo_enabled = lambda: False
    
    # Provide dummy adapter classes that always return None/False
    class AdminsAdapter:
        @staticmethod
        def get(user_id): return None
        @staticmethod
        def upsert(user_id, is_initial): return False
        @staticmethod
        def count(): return 0
    
    class AlliancesAdapter:
        @staticmethod
        def get_all(): return []
        @staticmethod
        async def get_all_async(): return []
        @staticmethod
        def get(alliance_id): return None
    
    class AllianceSettingsAdapter:
        @staticmethod
        def get(alliance_id): return None
    
    class AllianceMembersAdapter:
        @staticmethod
        def get_all_members(): return []
        @staticmethod
        async def get_all_members_async(): return []
        @staticmethod
        def get_member(fid): return None
        @staticmethod
        async def get_member_async(fid): return None
        @staticmethod
        def upsert_member(fid, data): return False
        @staticmethod
        async def upsert_member_async(fid, data): return False

    class AllianceMonitoringAdapter:
        @staticmethod
        def get_all_monitors(): return []
        @staticmethod
        async def get_all_monitors_async(): return []

    class FurnaceHistoryAdapter:
        @staticmethod
        def insert(data): return False
        @staticmethod
        async def insert_async(data): return False

    class ServerLimitsAdapter:
        @staticmethod
        def get(guild_id): return None
        @staticmethod
        async def get_async(guild_id): return None
        @staticmethod
        def set(guild_id, data): return False
        @staticmethod
        async def set_async(guild_id, data): return False
        @staticmethod
        def get_all(): return []
        @staticmethod
        async def get_all_async(): return []
        @staticmethod
        def delete(guild_id): return False
        @staticmethod
        async def delete_async(guild_id): return False
        @staticmethod
        def get_max_redeem_members(guild_id): return -1
        @staticmethod
        async def get_max_redeem_members_async(guild_id): return -1
        @staticmethod
        def is_monitor_locked(guild_id): return False
        @staticmethod
        async def is_monitor_locked_async(guild_id): return False


# Import database utilities for consistent path handling
try:
    from db_utils import get_db_connection
except ImportError:
    # Fallback if db_utils is not available
    from pathlib import Path
    def get_db_connection(db_name: str, **kwargs):
        repo_root = Path(__file__).resolve().parents[1]
        db_dir = repo_root / "db"
        db_dir.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(str(db_dir / db_name), **kwargs)

class Alliance(commands.Cog):
    def __init__(self, bot, conn):
        self.bot = bot
        self.conn = conn
        self.c = self.conn.cursor()
        
        # Use centralized database connection utility for consistent paths
        self.conn_users = get_db_connection('users.sqlite')
        self.c_users = self.conn_users.cursor()
        
        self.conn_settings = get_db_connection('settings.sqlite')
        self.c_settings = self.conn_settings.cursor()
        
        self.conn_giftcode = get_db_connection('giftcode.sqlite')
        self.c_giftcode = self.conn_giftcode.cursor()

        self._create_table()
        self._check_and_add_column()

        # Alliance Monitoring Initialization
        self.login_handler = LoginHandler()
        
        # Check API availability and enable dual-API mode if both are available
        # This will be called asynchronously when the monitoring task starts
        self._api_check_done = False
        self._current_scanning_alliance = []   # list of dicts — one per concurrent scan
        self._last_cycle_stats = {"start": None, "end": None, "alliances": 0, "changes": 0}
        self._scan_semaphore = None             # created lazily inside the async task
        self._scan_stats_lock = None            # asyncio.Lock for safe stat mutation
        
        # Level mapping for furnace levels
        self.level_mapping = get_level_mapping()
        
        # Furnace level emojis
        # Furnace level emojis - REMOVED as per request
        # self.fl_emojis = { ... }
        
        # Logging
        self.log_directory = 'log'
        if not os.path.exists(self.log_directory):
            os.makedirs(self.log_directory)
        self.log_file = os.path.join(self.log_directory, 'alliance_monitoring.txt')
        
        # Initialize monitoring tables
        self._initialize_monitoring_tables()
        
        # Sync from MongoDB if enabled
        self._sync_from_mongo()
        
        # Start background monitoring task
        self.monitor_alliances.start()
    
    def cog_unload(self):
        """Clean up when cog is unloaded"""
        self.monitor_alliances.cancel()

    # Channel ID for bot-join notifications (owner's logging channel)
    NOTIFY_CHANNEL_ID = 1500004448393625601

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        """Auto-lock new servers when bot joins: lock /manage and alliance monitor by default."""
        try:
            print(f"\U0001f512 [AUTO-LOCK] Bot joined server: {guild.name} ({guild.id}). Applying default locks...")
            
            # 1. Ensure the bot is unlocked by default via server_locks table
            try:
                self.c_settings.execute(
                    "INSERT OR IGNORE INTO server_locks (guild_id, locked, feature_locked, locked_by, locked_at) VALUES (?, 0, 0, ?, CURRENT_TIMESTAMP)",
                    (guild.id, self.bot.user.id)
                )
                self.conn_settings.commit()
                print(f"   \u2705 Server unlocked by default for {guild.name}")
            except Exception as e:
                print(f"   \u274c Failed to unlock for {guild.name}: {e}")
            
            # 2. Lock alliance monitor + set default redeem limit (100) via ServerLimitsAdapter
            try:
                ServerLimitsAdapter.set(guild.id, {
                    'max_auto_redeem_members': 100,
                    'alliance_monitor_locked': True,
                    'updated_by': self.bot.user.id
                })
                print(f"   \u2705 Alliance monitor locked + redeem limit=100 for {guild.name}")
            except Exception as e:
                print(f"   \u274c Failed to set server limits for {guild.name}: {e}")
            
            print(f"\U0001f512 [AUTO-LOCK] Defaults applied for {guild.name}")

            # 3. Send Onboarding DM
            adder = None
            try:
                async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.bot_add):
                    if entry.target.id == self.bot.user.id:
                        adder = entry.user
                        break
            except discord.Forbidden:
                print(f"   \u26a0\ufe0f Lacking audit log permissions to find bot adder in {guild.name}")
            except Exception as e:
                print(f"   \u26a0\ufe0f Error finding bot adder: {e}")

            # 4. Send join notification to owner's logging channel
            try:
                notify_channel = self.bot.get_channel(self.NOTIFY_CHANNEL_ID)
                if notify_channel is None:
                    notify_channel = await self.bot.fetch_channel(self.NOTIFY_CHANNEL_ID)
                if notify_channel:
                    total_guilds = len(self.bot.guilds)
                    owner = guild.owner
                    owner_text = f"{owner} (`{owner.id}`)" if owner else "Unknown"
                    adder_text = f"{adder} (`{adder.id}`)" if adder else "Unknown (no audit log access)"
                    icon_url = guild.icon.url if guild.icon else None

                    notify_embed = discord.Embed(
                        title="🎉 Bot Added to a New Server!",
                        color=0x2ECC71,
                        timestamp=discord.utils.utcnow()
                    )
                    notify_embed.add_field(name="🏠 Server Name", value=f"**{guild.name}**", inline=True)
                    notify_embed.add_field(name="🆔 Server ID", value=f"`{guild.id}`", inline=True)
                    notify_embed.add_field(name="👥 Member Count", value=str(guild.member_count), inline=True)
                    notify_embed.add_field(name="👑 Server Owner", value=owner_text, inline=True)
                    notify_embed.add_field(name="➕ Added By", value=adder_text, inline=True)
                    notify_embed.add_field(name="📊 Total Servers", value=f"**{total_guilds}** servers", inline=True)
                    if icon_url:
                        notify_embed.set_thumbnail(url=icon_url)
                    notify_embed.set_footer(text="Whiteout Survival Bot • Server Join Log")

                    await notify_channel.send(embed=notify_embed)
                    print(f"   \u2705 Join notification sent for {guild.name}")
            except Exception as e:
                print(f"   \u26a0\ufe0f Failed to send join notification: {e}")

            if not adder:
                adder = guild.owner

            if adder:
                try:
                    avatar_url = self.bot.user.avatar.url if self.bot.user.avatar else self.bot.user.default_avatar.url
                    
                    class OnboardingView(discord.ui.View):
                        def __init__(self, guild_name):
                            super().__init__(timeout=None)
                            self.guild_name = guild_name
                            self.current_page = 0
                            self.pages = [self.get_page_1(), self.get_page_2(), self.get_page_3()]
                            self.update_buttons()

                        def get_page_1(self):
                            embed = discord.Embed(
                                title="🤖 Welcome to Whiteout Survival Bot!",
                                description=f"Thank you for adding me to **{self.guild_name}**!\n\nThis guide will help you understand my core features and how to set them up.",
                                color=0x06B6D4
                            )
                            embed.add_field(
                                name="🔒 Default Security Lock",
                                value="By default, core features like **`/manage`**, **Auto-Redeem**, and the **Alliance Monitor** are locked to prevent unauthorized access.",
                                inline=False
                            )
                            embed.add_field(
                                name="🔑 Getting Access",
                                value="To unlock these features for your server, please configure them using the **`/manage`** command or manage them from the **Web Dashboard**, then wait for administrator review.",
                                inline=False
                            )
                            embed.add_field(
                                name="⭐ Share Feedback",
                                value="After setup, please share a quick review or feedback on Top.gg if the bot helps your server.",
                                inline=False
                            )
                            embed.set_thumbnail(url=avatar_url)
                            embed.set_footer(text="Page 1 of 3 • Use the buttons below to navigate")
                            return embed

                        def get_page_2(self):
                            embed = discord.Embed(
                                title="✨ Core Features",
                                description="Once unlocked, you can configure these features via the `/manage` command or the Web Dashboard:",
                                color=0x06B6D4
                            )
                            embed.add_field(
                                name="🏰 Alliance Monitoring",
                                value="Track your alliance's statistics, name changes, and member growth.",
                                inline=False
                            )
                            embed.add_field(
                                name="🎁 Gift Code Management",
                                value="Automate gift code redemption for all your members!",
                                inline=False
                            )
                            embed.add_field(
                                name="👥 Player Records",
                                value="Keep detailed records of your members, including custom groups and notes.",
                                inline=False
                            )
                            embed.set_thumbnail(url=avatar_url)
                            embed.set_footer(text="Page 2 of 3 • Use the buttons below to navigate")
                            return embed

                        def get_page_3(self):
                            embed = discord.Embed(
                                title="🔮 Additional Features",
                                description="Here are some other awesome features available right now:",
                                color=0x06B6D4
                            )
                            embed.add_field(
                                name="🔍 Quick Player Info",
                                value="Simply type a player's **ID** in any channel, and I will automatically fetch and display their in-game information!",
                                inline=False
                            )
                            embed.add_field(
                                name="👋 Welcome Setup",
                                value="Create custom welcome messages for new members using `/welcome` or via the manage dashboard.",
                                inline=False
                            )
                            embed.add_field(
                                name="📅 Server Age",
                                value="Use `/server_age` to check your server's age and upcoming milestones.",
                                inline=False
                            )
                            embed.add_field(
                                name="🌐 Auto Translation",
                                value="Create auto-translating channels using `/autotranslatecreate`.",
                                inline=False
                            )
                            embed.add_field(
                                name="🎮 Tic Tac Toe",
                                value="Play Tic Tac Toe with your friends using the `/tictactoe` command.",
                                inline=False
                            )
                            embed.set_thumbnail(url=avatar_url)
                            embed.set_footer(text="Page 3 of 3 • Use /manage to request access")
                            return embed

                        def update_buttons(self):
                            self.clear_items()
                            
                            # Previous button
                            prev_btn = discord.ui.Button(
                                label="Previous",
                                style=discord.ButtonStyle.secondary,
                                disabled=self.current_page == 0,
                                custom_id="onboarding_prev"
                            )
                            prev_btn.callback = self.prev_page
                            self.add_item(prev_btn)

                            # Next button
                            if self.current_page < len(self.pages) - 1:
                                next_btn = discord.ui.Button(
                                    label="Next",
                                    style=discord.ButtonStyle.primary,
                                    custom_id="onboarding_next"
                                )
                                next_btn.callback = self.next_page
                                self.add_item(next_btn)
                            else:
                                # Understood button on last page
                                understood_btn = discord.ui.Button(
                                    label="Understood",
                                    style=discord.ButtonStyle.success,
                                    custom_id="onboarding_understood"
                                )
                                understood_btn.callback = self.understood
                                self.add_item(understood_btn)

                            # Support Server button
                            self.add_item(discord.ui.Button(
                                label="Support Server",
                                style=discord.ButtonStyle.link,
                                url="https://discord.gg/bP5JQFH2M5"
                            ))

                            # Web Dashboard button
                            self.add_item(discord.ui.Button(
                                label="Web Dashboard",
                                style=discord.ButtonStyle.link,
                                url="https://bot.whiteoutsurvival.dev/"
                            ))

                            # Top.gg Review button
                            self.add_item(discord.ui.Button(
                                label="Review on Top.gg",
                                style=discord.ButtonStyle.link,
                                url=TOPGG_REVIEW_URL
                            ))

                        async def prev_page(self, interaction: discord.Interaction):
                            if self.current_page > 0:
                                self.current_page -= 1
                                self.update_buttons()
                                await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)

                        async def next_page(self, interaction: discord.Interaction):
                            if self.current_page < len(self.pages) - 1:
                                self.current_page += 1
                                self.update_buttons()
                                await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)

                        async def understood(self, interaction: discord.Interaction):
                            for item in self.children:
                                if hasattr(item, "custom_id") and item.custom_id in ["onboarding_prev", "onboarding_understood", "onboarding_next"]:
                                    item.disabled = True
                            await interaction.response.edit_message(view=self)
                            await interaction.followup.send("✅ **You're all set!** Enjoy using Whiteout Survival Bot.", ephemeral=True)

                    view = OnboardingView(guild.name)
                    await adder.send(embed=view.pages[0], view=view)
                    print(f"   \u2705 Sent paginated onboarding DM to {adder.name}")
                except discord.Forbidden:
                    print(f"   \u274c Failed to send DM to {adder.name} (DMs disabled)")
                except Exception as e:
                    print(f"   \u274c Error sending DM: {e}")

        except Exception as e:
            print(f"\u274c [AUTO-LOCK] Error processing guild join for {guild}: {e}")

    def _create_table(self):
        # Core alliance list
        self.c.execute("""
            CREATE TABLE IF NOT EXISTS alliance_list (
                alliance_id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                discord_server_id INTEGER
            )
        """)

        # Settings for alliances (may be stored in SQLite for legacy/partial flows)
        try:
            self.c.execute("""
                CREATE TABLE IF NOT EXISTS alliancesettings (
                    alliance_id INTEGER PRIMARY KEY,
                    channel_id INTEGER,
                    interval INTEGER DEFAULT 0
                )
            """)
        except Exception:
            # Best-effort: if creating this table fails, other code will handle exceptions
            pass

        # Ensure legacy/local DB tables used elsewhere exist (best-effort).
        try:
            # giftcode DB tables
            self.c_giftcode.execute("""
                CREATE TABLE IF NOT EXISTS giftcodecontrol (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    alliance_id INTEGER,
                    status INTEGER
                )
            """)

            self.c_giftcode.execute("""
                CREATE TABLE IF NOT EXISTS giftcode_channel (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    alliance_id INTEGER,
                    channel_id INTEGER
                )
            """)
        except Exception:
            pass

        try:
            # users table (minimal shape to allow counts/queries)
            self.c_users.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fid TEXT,
                    nickname TEXT,
                    furnace_lv INTEGER DEFAULT 0,
                    kid INTEGER,
                    stove_lv_content TEXT,
                    alliance INTEGER
                )
            """)
        except Exception:
            pass

        try:
            # settings DB: admin + adminserver used by settings flow
            self.c_settings.execute("""
                CREATE TABLE IF NOT EXISTS admin (
                    id INTEGER PRIMARY KEY,
                    is_initial INTEGER DEFAULT 0
                )
            """)
            self.c_settings.execute("""
                CREATE TABLE IF NOT EXISTS adminserver (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    alliances_id INTEGER
                )
            """)
            # Server lock table - for locking bot on specific servers
            self.c_settings.execute("""
                CREATE TABLE IF NOT EXISTS server_locks (
                    guild_id INTEGER PRIMARY KEY,
                    locked INTEGER DEFAULT 0,
                    feature_locked INTEGER DEFAULT 0,
                    locked_by INTEGER,
                    locked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Migration: Add feature_locked column if it doesn't exist
            try:
                self.c_settings.execute("ALTER TABLE server_locks ADD COLUMN feature_locked INTEGER DEFAULT 0")
            except Exception:
                pass
                
        except Exception:
            pass

        # Commit all changes where possible
        try:
            self.conn.commit()
        except Exception:
            pass
        try:
            self.conn_giftcode.commit()
        except Exception:
            pass
        try:
            self.conn_users.commit()
        except Exception:
            pass
        try:
            self.conn_settings.commit()
        except Exception:
            pass

    def _check_and_add_column(self):
        self.c.execute("PRAGMA table_info(alliance_list)")
        columns = [info[1] for info in self.c.fetchall()]
        if "discord_server_id" not in columns:
            self.c.execute("ALTER TABLE alliance_list ADD COLUMN discord_server_id INTEGER")
            self.conn.commit()

    def _get_admin(self, user_id):
        """Get admin info with MongoDB fallback to SQLite"""
        try:
            if mongo_enabled():
                admin = AdminsAdapter.get(user_id)
                if admin is not None:
                    return admin
                # If MongoDB returns None, fall back to SQLite
        except Exception as e:
            print(f"[WARNING] MongoDB AdminsAdapter.get failed: {e}. Falling back to SQLite.")
        
        # SQLite fallback
        try:
            self.c_settings.execute("SELECT id, is_initial FROM admin WHERE id = ?", (user_id,))
            return self.c_settings.fetchone()
        except Exception as e:
            print(f"[ERROR] SQLite admin query failed: {e}")
            return None

    def _upsert_admin(self, user_id, is_initial=1):
        """Insert/update admin with MongoDB fallback to SQLite"""
        success = False
        try:
            if mongo_enabled():
                success = AdminsAdapter.upsert(user_id, is_initial)
                if success:
                    return True
                # If MongoDB fails, fall back to SQLite
                print(f"[WARNING] MongoDB AdminsAdapter.upsert returned False. Falling back to SQLite.")
        except Exception as e:
            print(f"[WARNING] MongoDB AdminsAdapter.upsert failed: {e}. Falling back to SQLite.")
        
        # SQLite fallback
        try:
            self.c_settings.execute(
                "INSERT OR REPLACE INTO admin (id, is_initial) VALUES (?, ?)",
                (user_id, is_initial)
            )
            self.conn_settings.commit()
            return True
        except Exception as e:
            print(f"[ERROR] SQLite admin upsert failed: {e}")
            return False

    def _count_admins(self):
        """Count admins with MongoDB fallback to SQLite"""
        try:
            if mongo_enabled():
                count = AdminsAdapter.count()
                if count is not None and count >= 0:
                    return count
                # If MongoDB returns None, fall back to SQLite
        except Exception as e:
            print(f"[WARNING] MongoDB AdminsAdapter.count failed: {e}. Falling back to SQLite.")
        
        # SQLite fallback
        try:
            self.c_settings.execute("SELECT COUNT(*) FROM admin")
            return self.c_settings.fetchone()[0]
        except Exception as e:
            print(f"[ERROR] SQLite admin count failed: {e}")
            return 0


    async def view_alliances(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            pass

        if interaction.guild is None:
            await interaction.followup.send("❌ This command must be used in a server, not in DMs.", ephemeral=True)
            return

        user_id = interaction.user.id
        if mongo_enabled():
            admin = AdminsAdapter.get(user_id)
        else:
            self.c_settings.execute("SELECT id, is_initial FROM admin WHERE id = ?", (user_id,))
            admin = self.c_settings.fetchone()

        if admin is None:
            await interaction.followup.send("You do not have permission to view alliances.", ephemeral=True)
            return

        is_initial = admin[1] if isinstance(admin, tuple) else int(admin.get('is_initial', 0))
        guild_id = interaction.guild.id

        try:
            if mongo_enabled():
                docs = AlliancesAdapter.get_all()
                if is_initial == 1:
                    alliances = [(d['alliance_id'], d['name'], (AllianceSettingsAdapter.get(d['alliance_id']) or {}).get('interval', 0)) for d in docs]
                else:
                    alliances = [(d['alliance_id'], d['name'], (AllianceSettingsAdapter.get(d['alliance_id']) or {}).get('interval', 0)) for d in docs if int(d.get('discord_server_id') or 0) == guild_id]
            else:
                if is_initial == 1:
                    query = """
                        SELECT a.alliance_id, a.name, COALESCE(s.interval, 0) as interval
                        FROM alliance_list a
                        LEFT JOIN alliancesettings s ON a.alliance_id = s.alliance_id
                        ORDER BY a.alliance_id ASC
                    """
                    self.c.execute(query)
                else:
                    query = """
                        SELECT a.alliance_id, a.name, COALESCE(s.interval, 0) as interval
                        FROM alliance_list a
                        LEFT JOIN alliancesettings s ON a.alliance_id = s.alliance_id
                        WHERE a.discord_server_id = ?
                        ORDER BY a.alliance_id ASC
                    """
                    self.c.execute(query, (guild_id,))
                alliances = self.c.fetchall()

            alliance_list = ""
            for alliance_id, name, interval in alliances:
                
                if mongo_enabled():
                    try:
                        members = AllianceMembersAdapter.get_all_members()
                        member_count = sum(1 for m in members if int(m.get('alliance', 0)) == alliance_id)
                    except Exception:
                        member_count = 0
                else:
                    self.c_users.execute("SELECT COUNT(*) FROM users WHERE alliance = ?", (alliance_id,))
                    member_count = self.c_users.fetchone()[0]
                
                interval_text = f"{interval} minutes" if interval > 0 else "No automatic control"
                alliance_list += f"🛡️ **{alliance_id}: {name}**\n👥 Members: {member_count}\n⏱️ Control Interval: {interval_text}\n\n"

            if not alliance_list:
                alliance_list = "No alliances found."

            embed = discord.Embed(
                title="🛡️ Alliance Directory",
                description=alliance_list,
                color=0x06B6D4
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            await interaction.followup.send(
                "An error occurred while fetching alliances.", 
                ephemeral=True
            )

    async def alliance_autocomplete(self, interaction: discord.Interaction, current: str):
        self.c.execute("SELECT alliance_id, name FROM alliance_list")
        alliances = self.c.fetchall()
        return [
            app_commands.Choice(name=f"{name} (ID: {alliance_id})", value=str(alliance_id))
            for alliance_id, name in alliances if current.lower() in name.lower()
        ][:25]

    async def show_main_menu(self, interaction: discord.Interaction):
        """Programmatic access to settings menu"""
        await self.settings.callback(self, interaction)

    @app_commands.command(name="settings", description="Open settings menu.")
    @command_animation
    async def settings(self, interaction: discord.Interaction):
        try:
            if interaction.guild is not None: # Check bot permissions only if in a guild
                perm_check = interaction.guild.get_member(interaction.client.user.id)
                if not perm_check.guild_permissions.administrator:
                    await interaction.response.send_message(
                        "Beeb boop 🤖 I need **Administrator** permissions to function. "
                        "Go to server settings --> Roles --> find my role --> scroll down and turn on Administrator", 
                        ephemeral=True
                    )
                    return
                
            # Use helper method with automatic fallback
            admin_count = self._count_admins()
            user_id = interaction.user.id

            if admin_count == 0:
                # First time setup - make this user the global admin
                self._upsert_admin(user_id, 1)

                first_use_embed = discord.Embed(
                    title="🎉 First Time Setup",
                    description=(
                        "This command has been used for the first time and no administrators were found.\n\n"
                        f"**{interaction.user.name}** has been added as the Global Administrator.\n\n"
                        "You can now access all administrative functions."
                    ),
                    color=discord.Color.green()
                )
                await interaction.followup.send(embed=first_use_embed, ephemeral=True)
                
                await asyncio.sleep(3)
                
            # Use helper method with automatic fallback
            admin = self._get_admin(user_id)

            # Check if user is global admin or bot owner
            from admin_utils import is_bot_owner
            is_owner = await is_bot_owner(self.bot, user_id)
            
            # Handle both tuple (SQLite) and dict (MongoDB) formats
            if admin:
                if isinstance(admin, tuple):
                    is_global_admin = admin[1] == 1
                elif isinstance(admin, dict):
                    is_global_admin = int(admin.get('is_initial', 0)) == 1
                else:
                    is_global_admin = False
            else:
                is_global_admin = False
            
            if not is_global_admin and not is_owner:
                # User is not a global admin - check if they have Discord admin permissions for first-time setup
                if admin_count == 0 and interaction.guild and (interaction.user.guild_permissions.administrator or interaction.guild.owner_id == interaction.user.id):
                    # First time setup - allow Discord admins to become global admin
                    pass
                else:
                    await interaction.followup.send(
                        "❌ Only **Magnus** can use this command.",
                        ephemeral=True
                    )
                    return

            if admin is None:
                # User is not in database - check if they have Discord admin permissions
                if interaction.guild and (interaction.user.guild_permissions.administrator or interaction.guild.owner_id == interaction.user.id):
                    # Grant admin rights automatically
                    self._upsert_admin(user_id, 0)
                    admin = self._get_admin(user_id)
                else:
                    await interaction.followup.send(
                        "You do not have permission to access this menu.", 
                        ephemeral=True
                    )
                    return


            embed = discord.Embed(
                title="⚙️ Settings Dashboard",
                description=(
                    "**Welcome to the Settings Control Center**\n"
                    "Select a category below to manage your bot configuration\n\n"
                    "╔═══════════════════════════════════╗\n"
                    "║  **📋 Available Categories**      ║\n"
                    "╚═══════════════════════════════════╝\n\n"
                    "🏰 **Alliance Operations**\n"
                    "   ▸ Create, edit, and manage alliances\n"
                    "   ▸ View alliance statistics and settings\n\n"
                    "👥 **Alliance Member Operations**\n"
                    "   ▸ Add, remove, and manage members\n"
                    "   ▸ Track member information\n\n"
                    "📁 **Records**\n"
                    "   ▸ Create custom player records\n"
                    "   ▸ Organize players in custom groups\n\n"
                    "🤖 **Bot Operations**\n"
                    "   ▸ Configure bot behavior and settings\n"
                    "   ▸ Manage bot permissions\n\n"
                    "🎁 **Gift Code Operations**\n"
                    "   ▸ Manage gift codes and rewards\n"
                    "   ▸ Track redemption status\n\n"
                    "📜 **Alliance History**\n"
                    "   ▸ View alliance changes and logs\n"
                    "   ▸ Track historical data\n\n"
                    "🆘 **Support Operations**\n"
                    "   ▸ Access help and support features\n"
                    "   ▸ Troubleshooting tools\n\n"
                    "🔧 **Other Features**\n"
                    "   ▸ Additional utility functions\n"
                    "   ▸ Advanced settings\n\n"
                    "⚡ **Server Limits**\n"
                    "   ▸ Set auto-redeem member caps\n"
                    "   ▸ Lock alliance monitor per server\n\n"
                    "🛡️ **Control Panel**\n"
                    "   ▸ Bird's-eye view across all servers\n"
                    "   ▸ Bulk limit management\n\n"
                    "🛑 **Stop Auto-Redeem**\n"
                    "   ▸ Stop all active auto redeem processes in this server\n\n"
                    "🚀 **Manual Trigger & Status**\n"
                    "   ▸ Force trigger code redemption globally\n\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                ),
                color=0x7B2CBF
            )

            
            view = discord.ui.View()
            view.add_item(discord.ui.Button(
                label="Alliance Operations",
                emoji="🏰",
                style=discord.ButtonStyle.primary,
                custom_id=f"alliance_operations:{user_id}",
                row=0
            ))
            view.add_item(discord.ui.Button(
                label="Member Operations",
                emoji="👥",
                style=discord.ButtonStyle.primary,
                custom_id=f"member_operations:{user_id}",
                row=0
            ))
            view.add_item(discord.ui.Button(
                label="Records",
                emoji="📁",
                style=discord.ButtonStyle.primary,
                custom_id=f"records_menu:{user_id}",
                row=0
            ))
            view.add_item(discord.ui.Button(
                label="Bot Operations",
                emoji="🤖",
                style=discord.ButtonStyle.primary,
                custom_id=f"bot_operations:{user_id}",
                row=1
            ))
            view.add_item(discord.ui.Button(
                label="Gift Code Operations",
                emoji="🎁",
                style=discord.ButtonStyle.primary,
                custom_id=f"gift_operations:{user_id}",
                row=1
            ))
            view.add_item(discord.ui.Button(
                label="Alliance History",
                emoji="📜",
                style=discord.ButtonStyle.primary,
                custom_id=f"alliance_history:{user_id}",
                row=2
            ))
            view.add_item(discord.ui.Button(
                label="Support Operations",
                emoji="🆘",
                style=discord.ButtonStyle.primary,
                custom_id=f"support_operations:{user_id}",
                row=2
            ))
            view.add_item(discord.ui.Button(
                label="Other Features",
                emoji="🔧",
                style=discord.ButtonStyle.primary,
                custom_id=f"other_features:{user_id}",
                row=3
            ))
            view.add_item(discord.ui.Button(
                label="System Status",
                emoji="📊",
                style=discord.ButtonStyle.secondary,
                custom_id=f"system_status:{user_id}",
                row=3
            ))
            view.add_item(discord.ui.Button(
                label="Server Limits",
                emoji="⚡",
                style=discord.ButtonStyle.secondary,
                custom_id=f"server_limits:{user_id}",
                row=3
            ))
            view.add_item(discord.ui.Button(
                label="Control Panel",
                emoji="🛡️",
                style=discord.ButtonStyle.secondary,
                custom_id=f"control_panel:{user_id}",
                row=3
            ))
            view.add_item(discord.ui.Button(
                label="Lock Bot",
                emoji="🔒",
                style=discord.ButtonStyle.danger,
                custom_id=f"lock_bot:{user_id}",
                row=4
            ))
            view.add_item(discord.ui.Button(
                label="Stop Auto-Redeem",
                emoji="🛑",
                style=discord.ButtonStyle.danger,
                custom_id=f"stop_auto_redeem:{user_id}",
                row=4
            ))
            view.add_item(discord.ui.Button(
                label="Manual Trigger",
                emoji="🚀",
                style=discord.ButtonStyle.primary,
                custom_id=f"auto_redeem_trigger_menu:{user_id}",
                row=4
            ))
            view.add_item(discord.ui.Button(
                label="Debug",
                emoji="🔍",
                style=discord.ButtonStyle.secondary,
                custom_id=f"debug_bot:{user_id}",
                row=4
            ))
            # Add logo to embed
            embed.set_thumbnail(url="attachment://logo.png")
            
            # Prepare logo file
            logo_file = discord.File("logo.png", filename="logo.png")

            if admin_count == 0:
                await interaction.edit_original_response(embed=embed, view=view, attachments=[logo_file])
            else:
                await interaction.followup.send(embed=embed, view=view, file=logo_file)

        except Exception as e:
            if not any(error_code in str(e) for error_code in ["10062", "40060"]):
                print(f"Settings command error: {e}")
            error_message = "An error occurred while processing your request."
            if not interaction.response.is_done():
                await interaction.response.send_message(error_message, ephemeral=True)
            else:
                await interaction.followup.send(error_message, ephemeral=True)





    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type == discord.InteractionType.component:
            custom_id = interaction.data.get("custom_id")
            user_id = interaction.user.id

            # Parse owner_id from custom_id if present
            owner_id = user_id
            if custom_id and ":" in custom_id:
                try:
                    real_id, owner_str = custom_id.rsplit(":", 1)
                    # Check if the suffix is a valid user ID (digits)
                    if owner_str.isdigit():
                        check_owner_id = int(owner_str)
                        if user_id != check_owner_id:
                            await interaction.response.send_message("❌ This menu is not for you.", ephemeral=True)
                            return
                        custom_id = real_id
                        owner_id = check_owner_id
                except ValueError:
                    pass

            # Skip interactions handled by BotOperations to avoid collisions
            BOT_OPERATIONS_IDS = {
                "records_menu", "manage_member_ops", "manage_alliance_monitor",
                "manage_other_features", "manage_welcome", "players_timezone",
                "set_player_timezone", "timezone_view_members", "return_to_manage",
                "giftcode_menu", "bot_operations",
            }
            if custom_id in BOT_OPERATIONS_IDS or (custom_id and (
                custom_id.startswith("record_") or
                custom_id.startswith("giftcode") or
                custom_id.startswith("manage_")
            )):
                return
            
            # Use helper method with automatic fallback
            admin = self._get_admin(user_id)
            is_admin = admin is not None
            is_initial = int(admin[1]) if (admin and isinstance(admin, tuple)) else (int(admin.get('is_initial', 0)) if admin else 0)

            # If user is not recognized as admin, attempt to grant if they have Discord admin rights
            if not is_admin:
                if interaction.guild and (interaction.user.guild_permissions.administrator or interaction.guild.owner_id == interaction.user.id):
                    # Grant admin rights in the DB using helper method
                    self._upsert_admin(user_id, 0)
                    is_initial = 0
                    # Refresh admin status after insertion
                    admin = self._get_admin(user_id)
                    is_admin = admin is not None
                else:
                    await interaction.response.send_message("You do not have permission to perform this action.", ephemeral=True)
                    return

            try:
                if custom_id == "alliance_operations":
                    embed = discord.Embed(
                        title="🏰 Alliance Operations Center",
                        description=(
                            "**Manage Your Alliances**\n"
                            "Comprehensive tools for alliance administration\n\n"
                            "╔═══════════════════════════════════╗\n"
                            "║  **⚡ Quick Actions**              ║\n"
                            "╚═══════════════════════════════════╝\n\n"
                            "➕ **Add Alliance**\n"
                            "   ▸ Create a new alliance entry\n"
                            "   ▸ Configure initial settings\n\n"
                            "✏️ **Edit Alliance**\n"
                            "   ▸ Modify alliance configuration\n"
                            "   ▸ Update control intervals\n\n"
                            "🗑️ **Delete Alliance**\n"
                            "   ▸ Remove alliance from database\n"
                            "   ▸ Permanent deletion\n\n"
                            "👀 **View Alliances**\n"
                            "   ▸ List all registered alliances\n"
                            "   ▸ View member counts and settings\n\n"
                            "🔍 **Check Alliance**\n"
                            "   ▸ Run control process manually\n"
                            "   ▸ Verify alliance status\n\n"
                            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                        ),
                        color=0x06B6D4
                    )
                    
                    view = discord.ui.View()
                    view.add_item(discord.ui.Button(
                        label="Add Alliance", 
                        emoji="➕",
                        style=discord.ButtonStyle.success, 
                        custom_id=f"add_alliance:{owner_id}", 
                        disabled=is_initial != 1
                    ))
                    view.add_item(discord.ui.Button(
                        label="Edit Alliance", 
                        emoji="✏️",
                        style=discord.ButtonStyle.primary, 
                        custom_id=f"edit_alliance:{owner_id}", 
                        disabled=is_initial != 1
                    ))
                    view.add_item(discord.ui.Button(
                        label="Delete Alliance", 
                        emoji="🗑️",
                        style=discord.ButtonStyle.danger, 
                        custom_id=f"delete_alliance:{owner_id}", 
                        disabled=is_initial != 1
                    ))
                    view.add_item(discord.ui.Button(
                        label="View Alliances", 
                        emoji="👀",
                        style=discord.ButtonStyle.primary, 
                        custom_id=f"view_alliances:{owner_id}"
                    ))
                    view.add_item(discord.ui.Button(
                        label="Check Alliance", 
                        emoji="🔍",
                        style=discord.ButtonStyle.primary, 
                        custom_id=f"check_alliance:{owner_id}"
                    ))
                    view.add_item(discord.ui.Button(
                        label="Main Menu", 
                        emoji="🏠",
                        style=discord.ButtonStyle.secondary, 
                        custom_id=f"main_menu:{owner_id}"
                    ))

                    await interaction.response.edit_message(embed=embed, view=view)

                elif custom_id == "edit_alliance":
                    if is_initial != 1:
                        await interaction.response.send_message("You do not have permission to perform this action.", ephemeral=True)
                        return
                    await self.edit_alliance(interaction)

                elif custom_id == "check_alliance":
                    self.c.execute("""
                        SELECT a.alliance_id, a.name, COALESCE(s.interval, 0) as interval
                        FROM alliance_list a
                        LEFT JOIN alliancesettings s ON a.alliance_id = s.alliance_id
                        ORDER BY a.name
                    """)
                    alliances = self.c.fetchall()

                    if not alliances:
                        await interaction.response.send_message("No alliances found to check.", ephemeral=True)
                        return

                    options = [
                        discord.SelectOption(
                            label="Check All Alliances",
                            value="all",
                            description="Start control process for all alliances",
                            emoji="🔄"
                        )
                    ]
                    
                    options.extend([
                        discord.SelectOption(
                            label=f"{name[:40]}",
                            value=str(alliance_id),
                            description=f"Control Interval: {interval} minutes"
                        ) for alliance_id, name, interval in alliances
                    ])

                    select = discord.ui.Select(
                        placeholder="Select an alliance to check",
                        options=options,
                        custom_id="alliance_check_select"
                    )

                    async def alliance_check_callback(select_interaction: discord.Interaction):
                        if select_interaction.user.id != owner_id:
                            await select_interaction.response.send_message("❌ This menu is not for you.", ephemeral=True)
                            return
                        try:
                            selected_value = select_interaction.data["values"][0]
                            control_cog = self.bot.get_cog('Control')
                            
                            if not control_cog:
                                await select_interaction.response.send_message("Control module not found.", ephemeral=True)
                                return
                            
                            # Ensure the centralized queue processor is running
                            await control_cog.login_handler.start_queue_processor()
                            
                            if selected_value == "all":
                                progress_embed = discord.Embed(
                                    title="🔄 Alliance Control Queue",
                                    description=(
                                        "**Control Queue Information**\n"
                                        "╔═══════════════════════════════════╗\n"
                                        "║  **📊 Queue Status**              ║\n"
                                        "╚═══════════════════════════════════╝\n\n"
                                        f"📊 **Total Alliances:** `{len(alliances)}`\n"
                                        "🔄 **Status:** `Adding to queue...`\n"
                                        "⏰ **Queue Start:** `Now`\n\n"
                                        "⚠️ **Processing Info**\n"
                                        "   ▸ Sequential processing\n"
                                        "   ▸ 1 minute between controls\n\n"
                                        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                        "⌛ Please wait while processing..."
                                    ),
                                    color=0x06B6D4
                                )
                                await select_interaction.response.send_message(embed=progress_embed)
                                msg = await select_interaction.original_response()
                                message_id = msg.id

                                # Queue all alliance operations at once
                                queued_alliances = []
                                for index, (alliance_id, name, _) in enumerate(alliances):
                                    try:
                                        self.c.execute("""
                                            SELECT channel_id FROM alliancesettings WHERE alliance_id = ?
                                        """, (alliance_id,))
                                        channel_data = self.c.fetchone()
                                        channel = self.bot.get_channel(channel_data[0]) if channel_data else select_interaction.channel
                                        
                                        await control_cog.login_handler.queue_operation({
                                            'type': 'alliance_control',
                                            'callback': lambda ch=channel, aid=alliance_id, inter=select_interaction: control_cog.check_agslist(ch, aid, interaction=inter),
                                            'description': f'Manual control check for alliance {name}',
                                            'alliance_id': alliance_id,
                                            'interaction': select_interaction
                                        })
                                        queued_alliances.append((alliance_id, name))
                                    
                                    except Exception as e:
                                        print(f"Error queuing alliance {name}: {e}")
                                        continue
                                
                                # Update status to show all alliances have been queued
                                queue_status_embed = discord.Embed(
                                    title="🔄 Alliance Control Queue",
                                    description=(
                                        "**Control Queue Information**\n"
                                        "╔═══════════════════════════════════╗\n"
                                        "║  **✅ Queue Ready**               ║\n"
                                        "╚═══════════════════════════════════╝\n\n"
                                        f"📊 **Total Alliances Queued:** `{len(queued_alliances)}`\n"
                                        f"⏰ **Queue Start:** <t:{int(datetime.now().timestamp())}:R>\n\n"
                                        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                        "⌛ All controls queued and will process in order..."
                                    ),
                                    color=0x06B6D4
                                )
                                channel = select_interaction.channel
                                msg = await channel.fetch_message(message_id)
                                await msg.edit(embed=queue_status_embed)
                                
                                # Monitor queue completion
                                start_time = datetime.now()
                                while True:
                                    queue_info = control_cog.login_handler.get_queue_info()
                                    
                                    # Check if all our operations are done
                                    if queue_info['queue_size'] == 0 and queue_info['current_operation'] is None:
                                        # Double-check by waiting a moment
                                        await asyncio.sleep(2)
                                        queue_info = control_cog.login_handler.get_queue_info()
                                        if queue_info['queue_size'] == 0 and queue_info['current_operation'] is None:
                                            break
                                    
                                    # Update status periodically
                                    if queue_info['current_operation'] and queue_info['current_operation'].get('type') == 'alliance_control':
                                        current_alliance_id = queue_info['current_operation'].get('alliance_id')
                                        current_name = next((name for aid, name in queued_alliances if aid == current_alliance_id), "Unknown")
                                        
                                        update_embed = discord.Embed(
                                            title="🔄 Alliance Control Queue",
                                            description=(
                                                "**Control Queue Information**\n"
                                                "╔═══════════════════════════════════╗\n"
                                                "║  **⚡ Processing**                ║\n"
                                                "╚═══════════════════════════════════╝\n\n"
                                                f"📊 **Total Alliances:** `{len(queued_alliances)}`\n"
                                                f"🔄 **Currently Processing:** `{current_name}`\n"
                                                f"📈 **Queue Remaining:** `{queue_info['queue_size']}`\n"
                                                f"⏰ **Started:** <t:{int(start_time.timestamp())}:R>\n\n"
                                                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                                "⌛ Processing controls..."
                                            ),
                                            color=0x06B6D4
                                        )
                                        await msg.edit(embed=update_embed)
                                    
                                    await asyncio.sleep(5)  # Check every 5 seconds
                                
                                # All operations complete
                                queue_complete_embed = discord.Embed(
                                    title="✅ Alliance Control Queue Complete",
                                    description=(
                                        "**Queue Status Information**\n"
                                        "╔═══════════════════════════════════╗\n"
                                        "║  **✅ All Complete**              ║\n"
                                        "╚═══════════════════════════════════╝\n\n"
                                        f"📊 **Total Alliances Processed:** `{len(queued_alliances)}`\n"
                                        "🔄 **Status:** `All controls completed`\n"
                                        f"⏰ **Completion Time:** <t:{int(datetime.now().timestamp())}:R>\n"
                                        f"⏱️ **Total Duration:** `{int((datetime.now() - start_time).total_seconds())} seconds`\n\n"
                                        "📝 **Note:** Control results shared in respective channels\n"
                                        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                                    ),
                                    color=0x10B981
                                )
                                await msg.edit(embed=queue_complete_embed)
                            
                            else:
                                alliance_id = int(selected_value)
                                self.c.execute("""
                                    SELECT a.name, s.channel_id 
                                    FROM alliance_list a
                                    LEFT JOIN alliancesettings s ON a.alliance_id = s.alliance_id
                                    WHERE a.alliance_id = ?
                                """, (alliance_id,))
                                alliance_data = self.c.fetchone()

                                if not alliance_data:
                                    await select_interaction.response.send_message("Alliance not found.", ephemeral=True)
                                    return

                                alliance_name, channel_id = alliance_data
                                channel = self.bot.get_channel(channel_id) if channel_id else select_interaction.channel
                                
                                status_embed = discord.Embed(
                                    title="🔍 Alliance Control",
                                    description=(
                                        "**Control Information**\n"
                                        "╔═══════════════════════════════════╗\n"
                                        "║  **⏳ Queued**                    ║\n"
                                        "╚═══════════════════════════════════╝\n\n"
                                        f"📊 **Alliance:** `{alliance_name}`\n"
                                        f"🔄 **Status:** `Queued`\n"
                                        f"⏰ **Queue Time:** `Now`\n"
                                        f"📢 **Results Channel:** `{channel.name if channel else 'Designated channel'}`\n\n"
                                        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                        "⏳ Alliance control will begin shortly..."
                                    ),
                                    color=0x06B6D4
                                )
                                await select_interaction.response.send_message(embed=status_embed)
                                
                                await control_cog.login_handler.queue_operation({
                                    'type': 'alliance_control',
                                    'callback': lambda ch=channel, aid=alliance_id: control_cog.check_agslist(ch, aid),
                                    'description': f'Manual control check for alliance {alliance_name}',
                                    'alliance_id': alliance_id
                                })

                        except Exception as e:
                            print(f"Alliance check error: {e}")
                            await select_interaction.response.send_message(
                                "An error occurred during the control process.", 
                                ephemeral=True
                            )

                    select.callback = alliance_check_callback
                    view = discord.ui.View()
                    view.add_item(select)

                    embed = discord.Embed(
                        title="🔍 Alliance Control Center",
                        description=(
                            "**Select Alliance to Control**\n"
                            "Choose an alliance to run the control process\n\n"
                            "╔═══════════════════════════════════╗\n"
                            "║  **ℹ️ Important Information**     ║\n"
                            "╚═══════════════════════════════════╝\n\n"
                            "🔄 **Check All Alliances**\n"
                            "   ▸ Process all registered alliances\n"
                            "   ▸ Sequential execution with 1-min intervals\n\n"
                            "⏱️ **Processing Time**\n"
                            "   ▸ May take several minutes to complete\n"
                            "   ▸ Progress updates will be shown\n\n"
                            "📢 **Results**\n"
                            "   ▸ Shared in designated alliance channels\n"
                            "   ▸ Other controls queued during process\n\n"
                            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                        ),
                        color=0x06B6D4
                    )
                    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

                elif custom_id == "member_operations":
                    await self.bot.get_cog("AllianceMemberOperations").handle_member_operations(interaction)

                elif custom_id == "bot_operations":
                    try:
                        bot_ops_cog = interaction.client.get_cog("BotOperations")
                        if bot_ops_cog:
                            await bot_ops_cog.show_bot_operations_menu(interaction)
                        else:
                            await interaction.response.send_message(
                                "❌ Bot Operations module not found.",
                                ephemeral=True
                            )
                    except Exception as e:
                        if not any(error_code in str(e) for error_code in ["10062", "40060"]):
                            print(f"Bot operations error: {e}")
                        if not interaction.response.is_done():
                            await interaction.response.send_message(
                                "An error occurred while loading Bot Operations.",
                                ephemeral=True
                            )
                        else:
                            await interaction.followup.send(
                                "An error occurred while loading Bot Operations.",
                                ephemeral=True
                            )

                elif custom_id == "gift_operations":
                    try:
                        gift_ops_cog = interaction.client.get_cog("GiftOperations")
                        if gift_ops_cog:
                            await gift_ops_cog.show_gift_menu(interaction)
                        else:
                            await interaction.response.send_message(
                                "❌ Gift Operations module not found.",
                                ephemeral=True
                            )
                    except Exception as e:
                        print(f"Gift operations error: {e}")
                        if not interaction.response.is_done():
                            await interaction.response.send_message(
                                "An error occurred while loading Gift Operations.",
                                ephemeral=True
                            )
                        else:
                            await interaction.followup.send(
                                "An error occurred while loading Gift Operations.",
                                ephemeral=True
                            )

                elif custom_id == "stop_auto_redeem":
                    manage_cog = interaction.client.get_cog("ManageGiftCode")
                    if manage_cog:
                        guild_id = interaction.guild_id
                        if guild_id:
                            # Set stop signal
                            manage_cog.stop_signals[guild_id] = True
                            
                            # Log and try to save to DB so it doesn't auto-restart
                            manage_cog.logger.info(f"Stop signal sent for guild {guild_id} by {interaction.user} via /settings button.")
                            try:
                                from db.mongo_adapters import mongo_enabled, AutoRedeemSettingsAdapter
                                if mongo_enabled() and AutoRedeemSettingsAdapter:
                                    AutoRedeemSettingsAdapter.set_enabled(guild_id, False, interaction.user.id)
                            except Exception:
                                pass
                            try:
                                manage_cog.cursor.execute("UPDATE auto_redeem_settings SET enabled = 0 WHERE guild_id = ?", (guild_id,))
                                manage_cog.giftcode_db.commit()
                            except Exception:
                                pass
                                
                            await interaction.response.send_message(
                                "🛑 **Auto-Redeem Stopped**\n\nSignal sent to stop redemption processes. They should halt shortly.", 
                                ephemeral=True
                            )
                        else:
                            await interaction.response.send_message("❌ This command must be used in a server.", ephemeral=True)
                    else:
                        await interaction.response.send_message("❌ ManageGiftCode module not found.", ephemeral=True)

                elif custom_id == "add_alliance":
                    if not is_admin:
                        await interaction.response.send_message("You do not have permission to perform this action.", ephemeral=True)
                        return
                    await self.add_alliance(interaction)

                elif custom_id == "delete_alliance":
                    if not is_admin:
                        await interaction.response.send_message("You do not have permission to perform this action.", ephemeral=True)
                        return
                    await self.delete_alliance(interaction)

                elif custom_id == "view_alliances":
                    await self.view_alliances(interaction)

                elif custom_id == "support_operations":
                    try:
                        support_ops_cog = interaction.client.get_cog("SupportOperations")
                        if support_ops_cog:
                            await support_ops_cog.show_support_menu(interaction)
                        else:
                            await interaction.response.send_message(
                                "❌ Support Operations module not found.",
                                ephemeral=True
                            )
                    except Exception as e:
                        if not any(error_code in str(e) for error_code in ["10062", "40060"]):
                            print(f"Support operations error: {e}")
                        if not interaction.response.is_done():
                            await interaction.response.send_message(
                                "An error occurred while loading Support Operations.", 
                                ephemeral=True
                            )
                        else:
                            await interaction.followup.send(
                                "An error occurred while loading Support Operations.",
                                ephemeral=True
                            )

                elif custom_id == "alliance_history":
                    try:
                        changes_cog = interaction.client.get_cog("Changes")
                        if changes_cog:
                            await changes_cog.show_alliance_history_menu(interaction)
                        else:
                            await interaction.response.send_message(
                                "❌ Alliance History module not found.",
                                ephemeral=True
                            )
                    except Exception as e:
                        print(f"Alliance history error: {e}")
                        if not interaction.response.is_done():
                            await interaction.response.send_message(
                                "An error occurred while loading Alliance History.",
                                ephemeral=True
                            )
                        else:
                            await interaction.followup.send(
                                "An error occurred while loading Alliance History.",
                                ephemeral=True
                            )

                elif custom_id == "server_limits":
                    # Server limits management - only global admins
                    if is_initial != 1:
                        await interaction.response.send_message(
                            "❌ Only Global Administrators can manage server limits.",
                            ephemeral=True
                        )
                        return
                    
                    try:
                        all_guilds = sorted(list(self.bot.guilds), key=lambda g: g.name.lower())
                        
                        if not all_guilds:
                            await interaction.response.send_message("❌ Bot is not in any servers.", ephemeral=True)
                            return
                        
                        # Get all existing limits from MongoDB
                        all_limits = {}
                        try:
                            limits_data = ServerLimitsAdapter.get_all()
                            for lim in limits_data:
                                gid = str(lim.get('guild_id', ''))
                                all_limits[gid] = lim
                        except Exception:
                            pass
                        
                        servers_per_page = 25
                        total_pages = (len(all_guilds) + servers_per_page - 1) // servers_per_page
                        
                        cog_ref = self
                        
                        class ServerLimitsView(discord.ui.View):
                            def __init__(self, guilds_list, current_page=0):
                                super().__init__(timeout=180)
                                self.guilds = guilds_list
                                self.current_page = current_page
                                self.total_pages = total_pages
                                
                                start_idx = current_page * servers_per_page
                                end_idx = min(start_idx + servers_per_page, len(guilds_list))
                                
                                server_options = []
                                for guild in guilds_list[start_idx:end_idx]:
                                    lim = all_limits.get(str(guild.id), {})
                                    max_members = lim.get('max_auto_redeem_members', -1)
                                    monitor_locked = lim.get('alliance_monitor_locked', False)
                                    
                                    limit_text = f"∞" if max_members == -1 else str(max_members)
                                    lock_emoji = "🔒" if monitor_locked else "🔓"
                                    
                                    server_options.append(
                                        discord.SelectOption(
                                            label=f"{guild.name[:85]}",
                                            value=str(guild.id),
                                            description=f"Redeem: {limit_text} | Monitor: {lock_emoji} {'Locked' if monitor_locked else 'Open'}",
                                            emoji="⚡"
                                        )
                                    )
                                
                                if server_options:
                                    server_select = discord.ui.Select(
                                        placeholder="Select a server to configure...",
                                        options=server_options,
                                        custom_id="server_limits_select",
                                        row=0
                                    )
                                    server_select.callback = self.server_selected
                                    self.add_item(server_select)
                                
                                if total_pages > 1:
                                    if current_page > 0:
                                        prev_btn = discord.ui.Button(label="◀ Previous", style=discord.ButtonStyle.secondary, custom_id="prev_limits_page", row=1)
                                        prev_btn.callback = self.previous_page
                                        self.add_item(prev_btn)
                                    if current_page < total_pages - 1:
                                        next_btn = discord.ui.Button(label="Next ▶", style=discord.ButtonStyle.secondary, custom_id="next_limits_page", row=1)
                                        next_btn.callback = self.next_page
                                        self.add_item(next_btn)
                                
                                back_btn = discord.ui.Button(label="◀ Main Menu", emoji="🏠", style=discord.ButtonStyle.secondary, custom_id="back_to_main_limits", row=2)
                                back_btn.callback = self.back_to_menu
                                self.add_item(back_btn)
                            
                            async def previous_page(self, btn_interaction: discord.Interaction):
                                new_page = max(0, self.current_page - 1)
                                new_view = ServerLimitsView(self.guilds, new_page)
                                embed = self.create_embed(new_page)
                                await btn_interaction.response.edit_message(embed=embed, view=new_view)
                            
                            async def next_page(self, btn_interaction: discord.Interaction):
                                new_page = min(self.total_pages - 1, self.current_page + 1)
                                new_view = ServerLimitsView(self.guilds, new_page)
                                embed = self.create_embed(new_page)
                                await btn_interaction.response.edit_message(embed=embed, view=new_view)
                            
                            async def back_to_menu(self, btn_interaction: discord.Interaction):
                                await btn_interaction.response.defer()
                                alliance_cog = btn_interaction.client.get_cog("Alliance")
                                if alliance_cog:
                                    await alliance_cog.show_main_menu(btn_interaction)
                            
                            async def server_selected(self, select_interaction: discord.Interaction):
                                selected_guild_id = int(select_interaction.data["values"][0])
                                target_guild = select_interaction.client.get_guild(selected_guild_id)
                                guild_name = target_guild.name if target_guild else f"Guild {selected_guild_id}"
                                member_count = target_guild.member_count if target_guild else "?"
                                
                                # Fetch current limits
                                current_limits = all_limits.get(str(selected_guild_id), {})
                                max_members = current_limits.get('max_auto_redeem_members', -1)
                                monitor_locked = current_limits.get('alliance_monitor_locked', False)
                                
                                limit_display = "♾️ Unlimited" if max_members == -1 else f"**{max_members}** members"
                                monitor_display = "🔒 **LOCKED**" if monitor_locked else "🔓 **Open**"
                                
                                config_embed = discord.Embed(
                                    title=f"⚡ Server Limits — {guild_name}",
                                    description=(
                                        f"**Server Info**\n"
                                        f"└ Members: `{member_count}`\n"
                                        f"└ ID: `{selected_guild_id}`\n\n"
                                        f"╔═══════════════════════════════════╗\n"
                                        f"║  **📊 Current Configuration**      ║\n"
                                        f"╚═══════════════════════════════════╝\n\n"
                                        f"🎁 **Auto-Redeem Limit:** {limit_display}\n"
                                        f"   ▸ Max members that can auto-redeem\n\n"
                                        f"🛡️ **Alliance Monitor:** {monitor_display}\n"
                                        f"   ▸ Controls whether monitoring runs\n\n"
                                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                                    ),
                                    color=0x06B6D4
                                )
                                
                                config_view = discord.ui.View(timeout=180)
                                
                                # Set Redeem Limit button
                                set_limit_btn = discord.ui.Button(
                                    label="Set Redeem Limit", emoji="📊",
                                    style=discord.ButtonStyle.primary,
                                    custom_id=f"set_redeem_limit_{selected_guild_id}", row=0
                                )
                                
                                async def set_limit_callback(btn_inter: discord.Interaction):
                                    class RedeemLimitModal(discord.ui.Modal, title="Set Auto-Redeem Member Limit"):
                                        limit_input = discord.ui.TextInput(
                                            label="Maximum Members (-1 for unlimited)",
                                            placeholder="Enter a number, e.g. 50, 100, 200 or -1",
                                            default=str(max_members),
                                            required=True,
                                            max_length=10
                                        )
                                        
                                        async def on_submit(modal_self, modal_interaction: discord.Interaction):
                                            try:
                                                new_limit = int(modal_self.limit_input.value.strip())
                                                if new_limit < -1:
                                                    new_limit = -1
                                                
                                                success = ServerLimitsAdapter.set(selected_guild_id, {
                                                    'max_auto_redeem_members': new_limit,
                                                    'alliance_monitor_locked': monitor_locked,
                                                    'updated_by': modal_interaction.user.id
                                                })
                                                
                                                # Update local cache
                                                all_limits[str(selected_guild_id)] = {
                                                    'guild_id': str(selected_guild_id),
                                                    'max_auto_redeem_members': new_limit,
                                                    'alliance_monitor_locked': monitor_locked
                                                }
                                                
                                                limit_text = "♾️ Unlimited" if new_limit == -1 else f"**{new_limit}** members"
                                                
                                                if success:
                                                    await modal_interaction.response.send_message(
                                                        f"✅ Auto-redeem limit for **{guild_name}** set to {limit_text}",
                                                        ephemeral=True
                                                    )
                                                else:
                                                    await modal_interaction.response.send_message(
                                                        "❌ Failed to save limit. Check MongoDB connection.",
                                                        ephemeral=True
                                                    )
                                            except ValueError:
                                                await modal_interaction.response.send_message(
                                                    "❌ Invalid number. Please enter a valid integer.",
                                                    ephemeral=True
                                                )
                                    
                                    await btn_inter.response.send_modal(RedeemLimitModal())
                                
                                set_limit_btn.callback = set_limit_callback
                                config_view.add_item(set_limit_btn)
                                
                                # Toggle Monitor Lock button
                                toggle_text = "Unlock Monitor" if monitor_locked else "Lock Monitor"
                                toggle_emoji = "🔓" if monitor_locked else "🔒"
                                toggle_style = discord.ButtonStyle.success if monitor_locked else discord.ButtonStyle.danger
                                
                                toggle_monitor_btn = discord.ui.Button(
                                    label=toggle_text, emoji=toggle_emoji,
                                    style=toggle_style,
                                    custom_id=f"toggle_monitor_{selected_guild_id}", row=0
                                )
                                
                                async def toggle_monitor_callback(btn_inter: discord.Interaction):
                                    new_locked = not monitor_locked
                                    # When unlocking, set default redeem limit to 100
                                    new_max = max_members
                                    if not new_locked and max_members == -1:
                                        new_max = 100  # Default limit when unlocking
                                    
                                    success = ServerLimitsAdapter.set(selected_guild_id, {
                                        'max_auto_redeem_members': new_max,
                                        'alliance_monitor_locked': new_locked,
                                        'updated_by': btn_inter.user.id
                                    })
                                    
                                    all_limits[str(selected_guild_id)] = {
                                        'guild_id': str(selected_guild_id),
                                        'max_auto_redeem_members': new_max,
                                        'alliance_monitor_locked': new_locked
                                    }
                                    
                                    status = "\U0001f512 **LOCKED**" if new_locked else "\U0001f513 **Unlocked**"
                                    extra = ""
                                    if not new_locked and max_members == -1:
                                        extra = "\n\u2514 Auto-redeem limit set to **100** (default)"
                                    if success:
                                        await btn_inter.response.send_message(
                                            f"\u2705 Alliance monitor for **{guild_name}** is now {status}{extra}",
                                            ephemeral=True
                                        )
                                    else:
                                        await btn_inter.response.send_message(
                                            "\u274c Failed to update monitor lock.",
                                            ephemeral=True
                                        )
                                
                                toggle_monitor_btn.callback = toggle_monitor_callback
                                config_view.add_item(toggle_monitor_btn)
                                
                                # Reset to Defaults button
                                reset_btn = discord.ui.Button(
                                    label="Reset Defaults", emoji="🗑️",
                                    style=discord.ButtonStyle.secondary,
                                    custom_id=f"reset_limits_{selected_guild_id}", row=1
                                )
                                
                                async def reset_callback(btn_inter: discord.Interaction):
                                    ServerLimitsAdapter.delete(selected_guild_id)
                                    all_limits.pop(str(selected_guild_id), None)
                                    await btn_inter.response.send_message(
                                        f"✅ Limits reset to defaults for **{guild_name}**\n"
                                        f"└ Auto-redeem: ♾️ Unlimited\n"
                                        f"└ Alliance monitor: 🔓 Open",
                                        ephemeral=True
                                    )
                                
                                reset_btn.callback = reset_callback
                                config_view.add_item(reset_btn)
                                
                                # Back button
                                back_btn = discord.ui.Button(
                                    label="◀ Server List", emoji="📋",
                                    style=discord.ButtonStyle.secondary,
                                    custom_id="back_to_server_list", row=1
                                )
                                
                                async def back_callback(btn_inter: discord.Interaction):
                                    # Refresh limits
                                    try:
                                        refreshed = ServerLimitsAdapter.get_all()
                                        all_limits.clear()
                                        for lim in refreshed:
                                            all_limits[str(lim.get('guild_id', ''))] = lim
                                    except Exception:
                                        pass
                                    new_view = ServerLimitsView(all_guilds, 0)
                                    embed = new_view.create_embed(0)
                                    await btn_inter.response.edit_message(embed=embed, view=new_view)
                                
                                back_btn.callback = back_callback
                                config_view.add_item(back_btn)
                                
                                await select_interaction.response.edit_message(embed=config_embed, view=config_view)
                            
                            def create_embed(self, page):
                                start_idx = page * servers_per_page
                                end_idx = min(start_idx + servers_per_page, len(self.guilds))
                                
                                server_list = ""
                                for idx, guild in enumerate(self.guilds[start_idx:end_idx], start=start_idx + 1):
                                    lim = all_limits.get(str(guild.id), {})
                                    max_m = lim.get('max_auto_redeem_members', -1)
                                    mon_locked = lim.get('alliance_monitor_locked', False)
                                    
                                    limit_str = "∞" if max_m == -1 else str(max_m)
                                    lock_str = "🔒" if mon_locked else "🔓"
                                    has_config = "⚡" if lim else "  "
                                    
                                    server_list += (
                                        f"{has_config} **{idx:02d}.** {guild.name}\n"
                                        f"    └ Redeem: `{limit_str}` | Monitor: {lock_str}\n\n"
                                    )
                                
                                embed = discord.Embed(
                                    title="⚡ Server Limits Management",
                                    description=(
                                        "```ansi\n"
                                        "\u001b[2;36m╔═══════════════════════════════════╗\n"
                                        "\u001b[2;36m║  \u001b[1;37mSERVER LIMITS CONTROL\u001b[0m\u001b[2;36m          ║\n"
                                        "\u001b[2;36m╚═══════════════════════════════════╝\u001b[0m\n"
                                        "```\n"
                                        f"**Total Servers:** `{len(self.guilds)}`  |  "
                                        f"**With Limits:** `{len(all_limits)}`\n"
                                        f"**Page {page + 1}/{self.total_pages}**\n\n"
                                        f"{server_list}"
                                        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                        "Select a server to configure limits"
                                    ),
                                    color=0x06B6D4
                                )
                                return embed
                        
                        view = ServerLimitsView(all_guilds, 0)
                        embed = view.create_embed(0)
                        await interaction.response.edit_message(embed=embed, view=view)
                    
                    except Exception as e:
                        print(f"Server limits error: {e}")
                        import traceback
                        traceback.print_exc()
                        if not interaction.response.is_done():
                            await interaction.response.send_message("❌ Error loading server limits.", ephemeral=True)
                        else:
                            await interaction.followup.send("❌ Error loading server limits.", ephemeral=True)

                elif custom_id == "control_panel":
                    # Control Panel - overview dashboard
                    if is_initial != 1:
                        await interaction.response.send_message(
                            "❌ Only Global Administrators can access the Control Panel.",
                            ephemeral=True
                        )
                        return
                    
                    await self.show_control_panel(interaction)

                elif custom_id == "debug_bot":
                    # Only global admins can use debug
                    if is_initial != 1:
                        await interaction.response.send_message(
                            "❌ Only Global Administrators can access debug tools.",
                            ephemeral=True
                        )
                        return
                        
                    await interaction.response.defer(ephemeral=True)
                    
                    import sys
                    import os
                    
                    def safe_mongo_enabled():
                        try:
                            from db.mongo_adapters import mongo_enabled
                            return mongo_enabled()
                        except Exception as e:
                            return f"Error importing mongo_enabled: {e}"
                    
                    def safe_get_db():
                        try:
                            from db.mongo_adapters import _get_db
                            return _get_db()
                        except Exception as e:
                            raise ImportError(f"Error importing _get_db: {e}")
                            
                    lines = []
                    lines.append(f"**🔧 Diagnostic Output**")
                    try:
                        lines.append(f"\n**System**")
                        lines.append(f"Python: {sys.version.split()[0]}")
                        
                        # Cogs and Extensions
                        cog_names = sorted(list(self.bot.cogs.keys())) if getattr(self.bot, 'cogs', None) else []
                        ext_names = sorted(list(self.bot.extensions.keys())) if getattr(self.bot, 'extensions', None) else []
                        lines.append(f"Loaded Cogs: {len(cog_names)}")
                        
                        # Commands
                        infos = []
                        try:
                            if hasattr(self.bot.tree, 'get_commands'):
                                iterable = self.bot.tree.get_commands()
                            else:
                                iterable = self.bot.tree.walk_commands()
                            infos = [c for c in iterable]
                        except Exception:
                            pass
                        lines.append(f"Registered Commands: {len(infos)}")
                        
                        lines.append(f"\n**Database Context**")
                        uri = os.getenv('MONGO_URI')
                        db_name = os.getenv('MONGO_DB_NAME')
                        lines.append(f"MONGO_URI present: {bool(uri)}")
                        lines.append(f"MONGO_DB_NAME: `{db_name}`")
                        
                        try:
                            import pymongo
                            lines.append(f"pymongo: {pymongo.__version__}")
                        except ImportError:
                            lines.append("pymongo: ❌ MISSING")
                            
                        is_enabled = safe_mongo_enabled()
                        lines.append(f"mongo_enabled(): `{is_enabled}`")
                        
                        if isinstance(is_enabled, bool) and is_enabled:
                            try:
                                db = safe_get_db()
                                lines.append(f"\n**Collections Status**")
                                cols = {
                                    'reminders': 'reminders', 
                                    'alliance_members': 'alliance_members',
                                    'gift_codes': 'gift_codes',
                                    'auto_redeem_settings': 'auto_redeem_settings',
                                    'users': 'users'
                                }
                                for name, col_name in cols.items():
                                    try:
                                        count = db[col_name].count_documents({})
                                        lines.append(f"▸ {name}: `{count}` docs")
                                    except Exception as e:
                                        lines.append(f"▸ {name}: Error {e}")
                            except Exception as e:
                                lines.append(f"**Connection Error**: {e}")
                                
                    except Exception as e:
                        lines.append(f"Fatal Debug Error: {e}")
                        
                    output = "\n".join(lines)
                    embed = discord.Embed(title="Bot Diagnostics", description=output, color=0x3498DB)
                    await interaction.followup.send(embed=embed, ephemeral=True)

                elif custom_id == "lock_bot":
                    # Only global admins can lock/unlock bot
                    if is_initial != 1:
                        await interaction.response.send_message(
                            "❌ Only Global Administrators can lock/unlock the bot.",
                            ephemeral=True
                        )
                        return
                    
                    try:
                        # Show server selection for locking
                        all_guilds = list(self.bot.guilds)
                        
                        if not all_guilds:
                            await interaction.response.send_message(
                                "❌ Bot is not in any servers.",
                                ephemeral=True
                            )
                            return
                        
                        # Get current lock status for all servers
                        self.c_settings.execute("SELECT guild_id, locked, feature_locked FROM server_locks")
                        lock_status = {row[0]: {"locked": row[1] == 1, "feature_locked": row[2] == 1 if len(row) > 2 else False} for row in self.c_settings.fetchall()}
                        
                        # Create paginated server view
                        servers_per_page = 25
                        total_pages = (len(all_guilds) + servers_per_page - 1) // servers_per_page
                        
                        class ServerLockView(discord.ui.View):
                            def __init__(self, guilds_list, current_page=0):
                                super().__init__(timeout=180)
                                self.guilds = guilds_list
                                self.current_page = current_page
                                self.total_pages = total_pages
                                
                                # Add server selection dropdown
                                start_idx = current_page * servers_per_page
                                end_idx = min(start_idx + servers_per_page, len(guilds_list))
                                
                                server_options = []
                                for guild in guilds_list[start_idx:end_idx]:
                                    l_status = lock_status.get(guild.id, {})
                                    is_locked = l_status.get("locked", False)
                                    is_feature_locked = l_status.get("feature_locked", False)
                                    
                                    if is_locked:
                                        lock_emoji = "🔒"
                                        status_text = "Locked"
                                    elif is_feature_locked:
                                        lock_emoji = "🔏"
                                        status_text = "Feature Locked"
                                    else:
                                        lock_emoji = "🔓"
                                        status_text = "Unlocked"
                                        
                                    server_options.append(
                                        discord.SelectOption(
                                            label=f"{guild.name[:90]}",
                                            value=str(guild.id),
                                            description=f"{lock_emoji} {status_text}",
                                            emoji=lock_emoji
                                        )
                                    )
                                
                                server_select = discord.ui.Select(
                                    placeholder="Select a server to lock/unlock...",
                                    options=server_options,
                                    custom_id="server_lock_select",
                                    row=0
                                )
                                server_select.callback = self.server_selected
                                self.add_item(server_select)
                                
                                # Add pagination buttons if needed
                                if total_pages > 1:
                                    if current_page > 0:
                                        prev_button = discord.ui.Button(
                                            label="◀ Previous",
                                            style=discord.ButtonStyle.secondary,
                                            custom_id="prev_page_lock",
                                            row=1
                                        )
                                        prev_button.callback = self.previous_page
                                        self.add_item(prev_button)
                                    
                                    if current_page < total_pages - 1:
                                        next_button = discord.ui.Button(
                                            label="Next ▶",
                                            style=discord.ButtonStyle.secondary,
                                            custom_id="next_page_lock",
                                            row=1
                                        )
                                        next_button.callback = self.next_page
                                        self.add_item(next_button)
                                
                                # Add back to main menu button
                                back_button = discord.ui.Button(
                                    label="◀ Main Menu",
                                    emoji="🏠",
                                    style=discord.ButtonStyle.secondary,
                                    custom_id="main_menu_lock",
                                    row=2
                                )
                                back_button.callback = self.back_to_menu
                                self.add_item(back_button)
                            
                            async def previous_page(self, button_interaction: discord.Interaction):
                                new_page = max(0, self.current_page - 1)
                                new_view = ServerLockView(self.guilds, new_page)
                                embed = self.create_embed(new_page)
                                await button_interaction.response.edit_message(embed=embed, view=new_view)
                            
                            async def next_page(self, button_interaction: discord.Interaction):
                                new_page = min(self.total_pages - 1, self.current_page + 1)
                                new_view = ServerLockView(self.guilds, new_page)
                                embed = self.create_embed(new_page)
                                await button_interaction.response.edit_message(embed=embed, view=new_view)
                            
                            async def back_to_menu(self, button_interaction: discord.Interaction):
                                await button_interaction.response.defer()
                                # Redirect to settings menu
                                from cogs.alliance import Alliance
                                alliance_cog = button_interaction.client.get_cog("Alliance")
                                if alliance_cog:
                                    await alliance_cog.show_main_menu(button_interaction)
                            
                            def create_embed(self, page):
                                start_idx = page * servers_per_page
                                end_idx = min(start_idx + servers_per_page, len(self.guilds))
                                
                                server_list = ""
                                for idx, guild in enumerate(self.guilds[start_idx:end_idx], start=start_idx + 1):
                                    l_status = lock_status.get(guild.id, {})
                                    is_locked = l_status.get("locked", False)
                                    is_feature_locked = l_status.get("feature_locked", False)
                                    
                                    if is_locked:
                                        lock_emoji = "🔒"
                                        status = "**LOCKED**"
                                    elif is_feature_locked:
                                        lock_emoji = "🔏"
                                        status = "**FEATURE LOCKED**"
                                    else:
                                        lock_emoji = "🔓"
                                        status = "Unlocked"
                                        
                                    server_list += f"**{idx:02d}.** {lock_emoji} {guild.name}\n└ Status: {status}\n\n"
                                
                                embed = discord.Embed(
                                    title="🔒 Server Lock Management",
                                    description=(
                                        "```ansi\n"
                                        "\u001b[2;31m╔═══════════════════════════════════╗\n"
                                        "\u001b[2;31m║  \u001b[1;37mSECURITY CONTROL\u001b[0m\u001b[2;31m              ║\n"
                                        "\u001b[2;31m╚═══════════════════════════════════╝\u001b[0m\n"
                                        "```\n"
                                        "**Select a server to lock or unlock the bot**\n\n"
                                        "🔒 **Locked**: Bot will not respond to commands\n"
                                        "🔏 **Feature Locked**: Locks Alliance Monitor\n"
                                        "🔓 **Unlocked**: Bot functions normally\n\n"
                                        f"{server_list}"
                                    ),
                                    color=0xED4245
                                )
                                
                                if self.total_pages > 1:
                                    embed.set_footer(text=f"Page {page + 1}/{self.total_pages} • {len(self.guilds)} total servers")
                                else:
                                    embed.set_footer(text=f"{len(self.guilds)} total servers")
                                
                                return embed
                            
                            async def server_selected(self, select_interaction: discord.Interaction):
                                guild_id = int(select_interaction.data["values"][0])
                                guild = discord.utils.get(self.guilds, id=guild_id)
                                
                                if not guild:
                                    await select_interaction.response.send_message(
                                        "❌ Server not found.",
                                        ephemeral=True
                                    )
                                    return
                                
                                # Get current lock status using local connection
                                import sqlite3
                                settings_db = sqlite3.connect('db/settings.sqlite')
                                cursor = settings_db.cursor()
                                cursor.execute(
                                    "SELECT locked, feature_locked FROM server_locks WHERE guild_id = ?",
                                    (guild_id,)
                                )
                                result = cursor.fetchone()
                                is_locked = result[0] == 1 if result else False
                                is_feature_locked = result[1] == 1 if result else False
                                settings_db.close()
                                
                                # Create lock/unlock confirmation view
                                confirm_view = discord.ui.View(timeout=60)
                                
                                # Unlock Button
                                unlock_button = discord.ui.Button(
                                    label="Unlock All",
                                    emoji="🔓",
                                    style=discord.ButtonStyle.success if (is_locked or is_feature_locked) else discord.ButtonStyle.secondary,
                                    custom_id="unlock_confirm",
                                    disabled=not (is_locked or is_feature_locked)
                                )
                                
                                async def unlock_callback(btn_interaction: discord.Interaction):
                                    import sqlite3
                                    settings_db = sqlite3.connect('db/settings.sqlite')
                                    cursor = settings_db.cursor()
                                    cursor.execute(
                                        "INSERT OR REPLACE INTO server_locks (guild_id, locked, feature_locked, locked_by, locked_at) VALUES (?, 0, 0, ?, CURRENT_TIMESTAMP)",
                                        (guild_id, btn_interaction.user.id)
                                    )
                                    settings_db.commit()
                                    settings_db.close()
                                    
                                    lock_status[guild_id] = {"locked": False, "feature_locked": False}
                                    
                                    success_embed = discord.Embed(
                                        title="✅ Bot Unlocked",
                                        description=(f"**Server:** {guild.name}\n**Status:** 🔓 Unlocked\n\nThe bot will now respond normally in this server."),
                                        color=0x57F287
                                    )
                                    success_embed.set_footer(text=f"Unlocked by {btn_interaction.user.display_name}", icon_url=btn_interaction.user.display_avatar.url)
                                    await btn_interaction.response.edit_message(embed=success_embed, view=None)
                                
                                unlock_button.callback = unlock_callback
                                confirm_view.add_item(unlock_button)

                                # Feature Lock Button
                                feature_lock_button = discord.ui.Button(
                                    label="Feature Lock",
                                    emoji="🔏",
                                    style=discord.ButtonStyle.primary if not is_feature_locked else discord.ButtonStyle.secondary,
                                    custom_id="feature_lock_confirm",
                                    disabled=is_feature_locked and not is_locked
                                )

                                async def feature_lock_callback(btn_interaction: discord.Interaction):
                                    import sqlite3
                                    settings_db = sqlite3.connect('db/settings.sqlite')
                                    cursor = settings_db.cursor()
                                    cursor.execute(
                                        "INSERT OR REPLACE INTO server_locks (guild_id, locked, feature_locked, locked_by, locked_at) VALUES (?, 0, 1, ?, CURRENT_TIMESTAMP)",
                                        (guild_id, btn_interaction.user.id)
                                    )
                                    settings_db.commit()
                                    settings_db.close()
                                    
                                    lock_status[guild_id] = {"locked": False, "feature_locked": True}
                                    
                                    success_embed = discord.Embed(
                                        title="🔏 Feature Locked",
                                        description=(f"**Server:** {guild.name}\n**Status:** 🔏 Feature Locked\n\nSpecific features like Alliance Monitor are now locked."),
                                        color=0xE67E22
                                    )
                                    success_embed.set_footer(text=f"Feature Locked by {btn_interaction.user.display_name}", icon_url=btn_interaction.user.display_avatar.url)
                                    await btn_interaction.response.edit_message(embed=success_embed, view=None)

                                feature_lock_button.callback = feature_lock_callback
                                confirm_view.add_item(feature_lock_button)

                                # Full Lock Button
                                full_lock_button = discord.ui.Button(
                                    label="Full Lock",
                                    emoji="🔒",
                                    style=discord.ButtonStyle.danger if not is_locked else discord.ButtonStyle.secondary,
                                    custom_id="full_lock_confirm",
                                    disabled=is_locked
                                )
                                
                                async def full_lock_callback(btn_interaction: discord.Interaction):
                                    import sqlite3
                                    settings_db = sqlite3.connect('db/settings.sqlite')
                                    cursor = settings_db.cursor()
                                    cursor.execute(
                                        "INSERT OR REPLACE INTO server_locks (guild_id, locked, feature_locked, locked_by, locked_at) VALUES (?, 1, 0, ?, CURRENT_TIMESTAMP)",
                                        (guild_id, btn_interaction.user.id)
                                    )
                                    settings_db.commit()
                                    settings_db.close()
                                    
                                    lock_status[guild_id] = {"locked": True, "feature_locked": False}
                                    
                                    success_embed = discord.Embed(
                                        title="🔒 Bot Locked",
                                        description=(f"**Server:** {guild.name}\n**Status:** 🔒 Locked\n\nThe bot will no longer respond to commands in this server."),
                                        color=0xED4245
                                    )
                                    success_embed.set_footer(text=f"Locked by {btn_interaction.user.display_name}", icon_url=btn_interaction.user.display_avatar.url)
                                    await btn_interaction.response.edit_message(embed=success_embed, view=None)
                                
                                full_lock_button.callback = full_lock_callback
                                confirm_view.add_item(full_lock_button)
                                
                                # Add cancel button
                                cancel_button = discord.ui.Button(
                                    label="Cancel",
                                    emoji="❌",
                                    style=discord.ButtonStyle.secondary,
                                    custom_id="cancel_lock"
                                )
                                
                                async def cancel_callback(btn_interaction: discord.Interaction):
                                    await btn_interaction.response.edit_message(
                                        content="❌ Operation cancelled.",
                                        embed=None,
                                        view=None
                                    )
                                
                                cancel_button.callback = cancel_callback
                                confirm_view.add_item(cancel_button)
                                
                                # Current Status
                                if is_locked:
                                    curr_status = "🔒 Locked"
                                    c_color = 0xED4245
                                elif is_feature_locked:
                                    curr_status = "🔏 Feature Locked"
                                    c_color = 0xE67E22
                                else:
                                    curr_status = "🔓 Unlocked"
                                    c_color = 0x57F287

                                # Show confirmation
                                confirm_embed = discord.Embed(
                                    title=f"Server Lock Management",
                                    description=(
                                        f"**Server:** {guild.name}\n"
                                        f"**Current Status:** {curr_status}\n\n"
                                        f"Select an action below to update the server lock status."
                                    ),
                                    color=c_color
                                )
                                
                                await select_interaction.response.send_message(
                                    embed=confirm_embed,
                                    view=confirm_view,
                                    ephemeral=True
                                )
                        
                        # Create and send initial view
                        view = ServerLockView(all_guilds, 0)
                        embed = view.create_embed(0)
                        
                        await interaction.response.send_message(
                            embed=embed,
                            view=view,
                            ephemeral=True
                        )
                        
                    except Exception as e:
                        print(f"Lock bot error: {e}")
                        import traceback
                        traceback.print_exc()
                        if not interaction.response.is_done():
                            await interaction.response.send_message(
                                "❌ An error occurred while loading the lock management interface.",
                                ephemeral=True
                            )
                        else:
                            await interaction.followup.send(
                                "❌ An error occurred while loading the lock management interface.",
                                ephemeral=True
                            )

                elif custom_id == "system_status":
                    await self.handle_system_status(interaction)

                elif custom_id == "other_features":
                    try:
                        other_features_cog = interaction.client.get_cog("OtherFeatures")
                        if other_features_cog:
                            await other_features_cog.show_other_features_menu(interaction)
                        else:
                            await interaction.response.send_message(
                                "❌ Other Features module not found.",
                                ephemeral=True
                            )
                    except Exception as e:
                        if not any(error_code in str(e) for error_code in ["10062", "40060"]):
                            print(f"Other features error: {e}")
                        if not interaction.response.is_done():
                            await interaction.response.send_message(
                                "An error occurred while loading Other Features menu.",
                                ephemeral=True
                            )
                        else:
                            await interaction.followup.send(
                                "An error occurred while loading Other Features menu.",
                                ephemeral=True
                            )

            except Exception as e:
                if not any(error_code in str(e) for error_code in ["10062", "40060"]):
                    print(f"Error processing interaction with custom_id '{custom_id}': {e}")
                
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "An error occurred while processing your request. Please try again.",
                        ephemeral=True
                    )
                else:
                    await interaction.followup.send(
                        "An error occurred while processing your request. Please try again.",
                        ephemeral=True
                    )

    async def handle_system_status(self, interaction: discord.Interaction):
        """Displays the system status dashboard"""
        try:
            status = self._get_system_status()
            
            embed = discord.Embed(
                title="📊 System Performance Dashboard",
                description=(
                    "**Technical Metrics & Bot Health**\n"
                    "╔═══════════════════════════════════╗\n"
                    "║  **🚀 Bot Status**                ║\n"
                    "╚═══════════════════════════════════╝\n\n"
                    f"⚡ **CPU Usage:** `{status['cpu']}%`\n"
                    f"🧠 **RAM Usage:** `{status['ram_used']:.2f}GB / {status['ram_total']:.2f}GB` ({status['ram_percent']}%)\n"
                    f"💾 **Disk Usage:** `{status['disk_used']:.2f}GB / {status['disk_total']:.2f}GB` ({status['disk_percent']}%)\n\n"
                    "╔═══════════════════════════════════╗\n"
                    "║  **⏱️ Performance**               ║\n"
                    "╚═══════════════════════════════════╝\n\n"
                    f"🕒 **Uptime:** `{status['uptime']}`\n"
                    f"📡 **Latency:** `{round(self.bot.latency * 1000)}ms`\n"
                    f"🖥️ **OS:** `{status['os']}`\n\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                ),
                color=0x06B6D4,
                timestamp=datetime.now()
            )
            
            view = discord.ui.View()
            view.add_item(discord.ui.Button(
                label="Refresh", 
                emoji="🔄",
                style=discord.ButtonStyle.primary,
                custom_id=f"system_status:{interaction.user.id}"
            ))
            view.add_item(discord.ui.Button(
                label="Main Menu",
                emoji="🏠",
                style=discord.ButtonStyle.secondary,
                custom_id=f"main_menu:{interaction.user.id}"
            ))
            
            if interaction.response.is_done():
                await interaction.edit_original_response(embed=embed, view=view)
            else:
                await interaction.response.edit_message(embed=embed, view=view)
        except Exception as e:
            print(f"Error showing system status: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Error fetching system metrics.", ephemeral=True)
            else:
                await interaction.followup.send("❌ Error fetching system metrics.", ephemeral=True)

    def _get_system_status(self):
        """Helper to fetch system metrics using psutil"""
        import psutil
        import platform
        
        # CPU
        cpu = psutil.cpu_percent(interval=None)
        
        # RAM
        mem = psutil.virtual_memory()
        ram_used = mem.used / (1024 ** 3)
        ram_total = mem.total / (1024 ** 3)
        ram_percent = mem.percent
        
        # Disk
        disk = psutil.disk_usage('/')
        disk_used = disk.used / (1024 ** 3)
        disk_total = disk.total / (1024 ** 3)
        disk_percent = disk.percent
        
        # Uptime
        boot_time = datetime.fromtimestamp(psutil.boot_time())
        uptime = datetime.now() - boot_time
        
        return {
            "cpu": cpu,
            "ram_used": ram_used,
            "ram_total": ram_total,
            "ram_percent": ram_percent,
            "disk_used": disk_used,
            "disk_total": disk_total,
            "disk_percent": disk_percent,
            "uptime": str(uptime).split('.')[0],
            "os": platform.system()
        }

    async def add_alliance(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("Please perform this action in a Discord channel.", ephemeral=True)
            return

        modal = AllianceModal(title="Add Alliance")
        await interaction.response.send_modal(modal)
        await modal.wait()

        try:
            alliance_name = modal.name.value.strip()
            interval = int(modal.interval.value.strip())

            embed = discord.Embed(
                title="📢 Channel Selection",
                description=(
                    "**Select Alliance Channel**\n"
                    "╔═══════════════════════════════════╗\n"
                    "║  **ℹ️ Instructions**              ║\n"
                    "╚═══════════════════════════════════╝\n\n"
                    "Please select a channel for the alliance\n\n"
                    f"**📊 Total Channels:** {len(interaction.guild.text_channels)}\n"
                    "**📄 Page:** 1/1"
                ),
                color=0x06B6D4
            )

            async def channel_select_callback(select_interaction: discord.Interaction):
                try:
                    self.c.execute("SELECT alliance_id FROM alliance_list WHERE name = ?", (alliance_name,))
                    existing_alliance = self.c.fetchone()
                    
                    if existing_alliance:
                        error_embed = discord.Embed(
                            title="Error",
                            description="An alliance with this name already exists.",
                            color=discord.Color.red()
                        )
                        await select_interaction.response.edit_message(embed=error_embed, view=None)
                        return

                    channel_id = int(select_interaction.data["values"][0])

                    self.c.execute("INSERT INTO alliance_list (name, discord_server_id) VALUES (?, ?)", 
                                 (alliance_name, interaction.guild.id))
                    alliance_id = self.c.lastrowid
                    self.c.execute("INSERT INTO alliancesettings (alliance_id, channel_id, interval) VALUES (?, ?, ?)", 
                                 (alliance_id, channel_id, interval))
                    self.conn.commit()
                    if mongo_enabled():
                        try:
                            AlliancesAdapter.upsert(alliance_id, alliance_name, interaction.guild.id)
                            AllianceSettingsAdapter.upsert(alliance_id, channel_id, interval, giftcodecontrol=1)
                        except Exception:
                            pass

                    self.c_giftcode.execute("""
                        INSERT INTO giftcodecontrol (alliance_id, status) 
                        VALUES (?, 1)
                    """, (alliance_id,))
                    self.conn_giftcode.commit()

                    result_embed = discord.Embed(
                        title="✅ Alliance Created Successfully",
                        description="The alliance has been created with the following details:",
                        color=0x10B981
                    )
                    
                    info_section = (
                        f"**🛡️ Alliance Name**\n{alliance_name}\n\n"
                        f"**🔢 Alliance ID**\n{alliance_id}\n\n"
                        f"**📢 Channel**\n<#{channel_id}>\n\n"
                        f"**⏱️ Control Interval**\n{interval} minutes"
                    )
                    result_embed.add_field(name="Alliance Details", value=info_section, inline=False)
                    
                    result_embed.set_footer(text="Alliance has been successfully created")
                    result_embed.timestamp = discord.utils.utcnow()
                    
                    await select_interaction.response.edit_message(embed=result_embed, view=None)

                except Exception as e:
                    error_embed = discord.Embed(
                        title="Error",
                        description=f"Error creating alliance: {str(e)}",
                        color=discord.Color.red()
                    )
                    await select_interaction.response.edit_message(embed=error_embed, view=None)

            channels = interaction.guild.text_channels
            view = PaginatedChannelView(channels, channel_select_callback)
            await modal.interaction.response.send_message(embed=embed, view=view, ephemeral=True)

        except ValueError:
            error_embed = discord.Embed(
                title="Error",
                description="Invalid interval value. Please enter a number.",
                color=discord.Color.red()
            )
            await modal.interaction.response.send_message(embed=error_embed, ephemeral=True)
        except Exception as e:
            error_embed = discord.Embed(
                title="Error",
                description=f"Error: {str(e)}",
                color=discord.Color.red()
            )
            await modal.interaction.response.send_message(embed=error_embed, ephemeral=True)

    async def edit_alliance(self, interaction: discord.Interaction):
        self.c.execute("""
            SELECT a.alliance_id, a.name, COALESCE(s.interval, 0) as interval, COALESCE(s.channel_id, 0) as channel_id 
            FROM alliance_list a 
            LEFT JOIN alliancesettings s ON a.alliance_id = s.alliance_id
            ORDER BY a.alliance_id ASC
        """)
        alliances = self.c.fetchall()
        
        if not alliances:
            no_alliance_embed = discord.Embed(
                title="❌ No Alliances Found",
                description=(
                    "There are no alliances registered in the database.\n"
                    "Please create an alliance first using the `/alliance create` command."
                ),
                color=discord.Color.red()
            )
            no_alliance_embed.set_footer(text="Use /alliance create to add a new alliance")
            return await interaction.response.send_message(embed=no_alliance_embed, ephemeral=True)

        alliance_options = [
            discord.SelectOption(
                label=f"{name} (ID: {alliance_id})",
                value=f"{alliance_id}",
                description=f"Interval: {interval} minutes"
            ) for alliance_id, name, interval, _ in alliances
        ]
        
        items_per_page = 25
        option_pages = [alliance_options[i:i + items_per_page] for i in range(0, len(alliance_options), items_per_page)]
        total_pages = len(option_pages)

        class PaginatedAllianceView(discord.ui.View):
            def __init__(self, pages, original_callback):
                super().__init__(timeout=7200)
                self.current_page = 0
                self.pages = pages
                self.original_callback = original_callback
                self.total_pages = len(pages)
                self.update_view()

            def update_view(self):
                self.clear_items()
                
                select = discord.ui.Select(
                    placeholder=f"Select alliance ({self.current_page + 1}/{self.total_pages})",
                    options=self.pages[self.current_page]
                )
                select.callback = self.original_callback
                self.add_item(select)
                
                previous_button = discord.ui.Button(
                    label="◀️",
                    style=discord.ButtonStyle.grey,
                    custom_id="previous",
                    disabled=(self.current_page == 0)
                )
                previous_button.callback = self.previous_callback
                self.add_item(previous_button)

                next_button = discord.ui.Button(
                    label="▶️",
                    style=discord.ButtonStyle.grey,
                    custom_id="next",
                    disabled=(self.current_page == len(self.pages) - 1)
                )
                next_button.callback = self.next_callback
                self.add_item(next_button)

            async def previous_callback(self, interaction: discord.Interaction):
                self.current_page = (self.current_page - 1) % len(self.pages)
                self.update_view()
                
                embed = interaction.message.embeds[0]
                embed.description = (
                    "**Instructions:**\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    "1️⃣ Select an alliance from the dropdown menu\n"
                    "2️⃣ Use ◀️ ▶️ buttons to navigate between pages\n\n"
                    f"**Current Page:** {self.current_page + 1}/{self.total_pages}\n"
                    f"**Total Alliances:** {sum(len(page) for page in self.pages)}\n"
                    "━━━━━━━━━━━━━━━━━━━━━━"
                )
                await interaction.response.edit_message(embed=embed, view=self)

            async def next_callback(self, interaction: discord.Interaction):
                self.current_page = (self.current_page + 1) % len(self.pages)
                self.update_view()
                
                embed = interaction.message.embeds[0]
                embed.description = (
                    "**Instructions:**\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    "1️⃣ Select an alliance from the dropdown menu\n"
                    "2️⃣ Use ◀️ ▶️ buttons to navigate between pages\n\n"
                    f"**Current Page:** {self.current_page + 1}/{self.total_pages}\n"
                    f"**Total Alliances:** {sum(len(page) for page in self.pages)}\n"
                    "━━━━━━━━━━━━━━━━━━━━━━"
                )
                await interaction.response.edit_message(embed=embed, view=self)

        async def select_callback(select_interaction: discord.Interaction):
            try:
                alliance_id = int(select_interaction.data["values"][0])
                alliance_data = next(a for a in alliances if a[0] == alliance_id)
                
                self.c.execute("""
                    SELECT interval, channel_id 
                    FROM alliancesettings 
                    WHERE alliance_id = ?
                """, (alliance_id,))
                settings_data = self.c.fetchone()
                
                modal = AllianceModal(
                    title="Edit Alliance",
                    default_name=alliance_data[1],
                    default_interval=str(settings_data[0] if settings_data else 0)
                )
                await select_interaction.response.send_modal(modal)
                await modal.wait()

                try:
                    alliance_name = modal.name.value.strip()
                    interval = int(modal.interval.value.strip())

                    embed = discord.Embed(
                        title="🔄 Channel Selection",
                        description=(
                            "**Current Channel Information**\n"
                            "━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"📢 Current channel: {f'<#{settings_data[1]}>' if settings_data else 'Not set'}\n"
                            "**Page:** 1/1\n"
                            f"**Total Channels:** {len(interaction.guild.text_channels)}\n"
                            "━━━━━━━━━━━━━━━━━━━━━━"
                        ),
                        color=discord.Color.blue()
                    )

                    async def channel_select_callback(channel_interaction: discord.Interaction):
                        try:
                            channel_id = int(channel_interaction.data["values"][0])

                            self.c.execute("UPDATE alliance_list SET name = ? WHERE alliance_id = ?", 
                                          (alliance_name, alliance_id))

                            if settings_data:
                                self.c.execute("""
                                    UPDATE alliancesettings 
                                    SET channel_id = ?, interval = ? 
                                    WHERE alliance_id = ?
                                """, (channel_id, interval, alliance_id))
                            else:
                                self.c.execute("""
                                    INSERT INTO alliancesettings (alliance_id, channel_id, interval)
                                    VALUES (?, ?, ?)
                                """, (alliance_id, channel_id, interval))
                            
                            self.conn.commit()
                            if mongo_enabled():
                                try:
                                    AlliancesAdapter.upsert(alliance_id, alliance_name, interaction.guild.id)
                                    AllianceSettingsAdapter.upsert(alliance_id, channel_id, interval)
                                except Exception:
                                    pass

                            result_embed = discord.Embed(
                                title="✅ Alliance Successfully Updated",
                                description="The alliance details have been updated as follows:",
                                color=discord.Color.green()
                            )
                            
                            info_section = (
                                f"**🛡️ Alliance Name**\n{alliance_name}\n\n"
                                f"**🔢 Alliance ID**\n{alliance_id}\n\n"
                                f"**📢 Channel**\n<#{channel_id}>\n\n"
                                f"**⏱️ Control Interval**\n{interval} minutes"
                            )
                            result_embed.add_field(name="Alliance Details", value=info_section, inline=False)
                            
                            result_embed.set_footer(text="Alliance settings have been successfully saved")
                            result_embed.timestamp = discord.utils.utcnow()
                            
                            await channel_interaction.response.edit_message(embed=result_embed, view=None)

                        except Exception as e:
                            error_embed = discord.Embed(
                                title="❌ Error",
                                description=f"An error occurred while updating the alliance: {str(e)}",
                                color=discord.Color.red()
                            )
                            await channel_interaction.response.edit_message(embed=error_embed, view=None)

                    channels = interaction.guild.text_channels
                    view = PaginatedChannelView(channels, channel_select_callback)
                    await modal.interaction.response.send_message(embed=embed, view=view, ephemeral=True)

                except ValueError:
                    error_embed = discord.Embed(
                        title="Error",
                        description="Invalid interval value. Please enter a number.",
                        color=discord.Color.red()
                    )
                    await modal.interaction.response.send_message(embed=error_embed, ephemeral=True)
                except Exception as e:
                    error_embed = discord.Embed(
                        title="Error",
                        description=f"Error: {str(e)}",
                        color=discord.Color.red()
                    )
                    await modal.interaction.response.send_message(embed=error_embed, ephemeral=True)

            except Exception as e:
                error_embed = discord.Embed(
                    title="❌ Error",
                    description=f"An error occurred: {str(e)}",
                    color=discord.Color.red()
                )
                if not select_interaction.response.is_done():
                    await select_interaction.response.send_message(embed=error_embed, ephemeral=True)
                else:
                    await select_interaction.followup.send(embed=error_embed, ephemeral=True)

        view = PaginatedAllianceView(option_pages, select_callback)
        embed = discord.Embed(
            title="🛡️ Alliance Edit Menu",
            description=(
                "**Instructions:**\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "1️⃣ Select an alliance from the dropdown menu\n"
                "2️⃣ Use ◀️ ▶️ buttons to navigate between pages\n\n"
                f"**Current Page:** {1}/{total_pages}\n"
                f"**Total Alliances:** {len(alliances)}\n"
                "━━━━━━━━━━━━━━━━━━━━━━"
            ),
            color=discord.Color.blue()
        )
        embed.set_footer(text="Select an alliance to edit its settings")
        embed.timestamp = discord.utils.utcnow()
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def delete_alliance(self, interaction: discord.Interaction):
        try:
            self.c.execute("SELECT alliance_id, name FROM alliance_list ORDER BY name")
            alliances = self.c.fetchall()
            
            if not alliances:
                no_alliance_embed = discord.Embed(
                    title="❌ No Alliances Found",
                    description="There are no alliances to delete.",
                    color=discord.Color.red()
                )
                await interaction.response.send_message(embed=no_alliance_embed, ephemeral=True)
                return

            alliance_members = {}
            for alliance_id, _ in alliances:
                self.c_users.execute("SELECT COUNT(*) FROM users WHERE alliance = ?", (alliance_id,))
                member_count = self.c_users.fetchone()[0]
                alliance_members[alliance_id] = member_count

            items_per_page = 25
            all_options = [
                discord.SelectOption(
                    label=f"{name[:40]} (ID: {alliance_id})",
                    value=f"{alliance_id}",
                    description=f"👥 Members: {alliance_members[alliance_id]} | Click to delete",
                    emoji="🗑️"
                ) for alliance_id, name in alliances
            ]
            
            option_pages = [all_options[i:i + items_per_page] for i in range(0, len(all_options), items_per_page)]
            
            embed = discord.Embed(
                title="🗑️ Delete Alliance",
                description=(
                    "**⚠️ Warning: This action cannot be undone!**\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    "1️⃣ Select an alliance from the dropdown menu\n"
                    "2️⃣ Use ◀️ ▶️ buttons to navigate between pages\n\n"
                    f"**Current Page:** 1/{len(option_pages)}\n"
                    f"**Total Alliances:** {len(alliances)}\n"
                    "━━━━━━━━━━━━━━━━━━━━━━"
                ),
                color=discord.Color.red()
            )
            embed.set_footer(text="Select an alliance to delete")
            embed.timestamp = discord.utils.utcnow()

            view = PaginatedDeleteView(option_pages, self.alliance_delete_callback)
            
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

        except Exception as e:
            print(f"Error in delete_alliance: {e}")
            error_embed = discord.Embed(
                title="❌ Error",
                description="An error occurred while loading the delete menu.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=error_embed, ephemeral=True)

    async def alliance_delete_callback(self, interaction: discord.Interaction):
        try:
            alliance_id = int(interaction.data["values"][0])
            
            self.c.execute("SELECT name FROM alliance_list WHERE alliance_id = ?", (alliance_id,))
            alliance_data = self.c.fetchone()
            
            if not alliance_data:
                await interaction.response.send_message("Alliance not found.", ephemeral=True)
                return
            
            alliance_name = alliance_data[0]

            self.c.execute("SELECT COUNT(*) FROM alliancesettings WHERE alliance_id = ?", (alliance_id,))
            settings_count = self.c.fetchone()[0]

            self.c_users.execute("SELECT COUNT(*) FROM users WHERE alliance = ?", (alliance_id,))
            users_count = self.c_users.fetchone()[0]

            self.c_settings.execute("SELECT COUNT(*) FROM adminserver WHERE alliances_id = ?", (alliance_id,))
            admin_server_count = self.c_settings.fetchone()[0]

            self.c_giftcode.execute("SELECT COUNT(*) FROM giftcode_channel WHERE alliance_id = ?", (alliance_id,))
            gift_channels_count = self.c_giftcode.fetchone()[0]

            self.c_giftcode.execute("SELECT COUNT(*) FROM giftcodecontrol WHERE alliance_id = ?", (alliance_id,))
            gift_code_control_count = self.c_giftcode.fetchone()[0]

            confirm_embed = discord.Embed(
                title="⚠️ Confirm Alliance Deletion",
                description=(
                    f"Are you sure you want to delete this alliance?\n\n"
                    f"**Alliance Details:**\n"
                    f"🛡️ **Name:** {alliance_name}\n"
                    f"🔢 **ID:** {alliance_id}\n"
                    f"👥 **Members:** {users_count}\n\n"
                    f"**Data to be Deleted:**\n"
                    f"⚙️ Alliance Settings: {settings_count}\n"
                    f"👥 User Records: {users_count}\n"
                    f"🏰 Admin Server Records: {admin_server_count}\n"
                    f"📢 Gift Channels: {gift_channels_count}\n"
                    f"📊 Gift Code Controls: {gift_code_control_count}\n\n"
                    "**⚠️ WARNING: This action cannot be undone!**"
                ),
                color=discord.Color.red()
            )
            
            confirm_view = discord.ui.View(timeout=60)
            
            async def confirm_callback(button_interaction: discord.Interaction):
                try:
                    self.c.execute("DELETE FROM alliance_list WHERE alliance_id = ?", (alliance_id,))
                    alliance_count = self.c.rowcount
                    
                    self.c.execute("DELETE FROM alliancesettings WHERE alliance_id = ?", (alliance_id,))
                    admin_settings_count = self.c.rowcount
                    
                    self.conn.commit()

                    self.c_users.execute("DELETE FROM users WHERE alliance = ?", (alliance_id,))
                    users_count_deleted = self.c_users.rowcount
                    self.conn_users.commit()

                    self.c_settings.execute("DELETE FROM adminserver WHERE alliances_id = ?", (alliance_id,))
                    admin_server_count = self.c_settings.rowcount
                    self.conn_settings.commit()

                    self.c_giftcode.execute("DELETE FROM giftcode_channel WHERE alliance_id = ?", (alliance_id,))
                    gift_channels_count = self.c_giftcode.rowcount

                    self.c_giftcode.execute("DELETE FROM giftcodecontrol WHERE alliance_id = ?", (alliance_id,))
                    gift_code_control_count = self.c_giftcode.rowcount
                    
                    self.conn_giftcode.commit()
                    if mongo_enabled():
                        try:
                            AlliancesAdapter.delete(alliance_id)
                            AllianceSettingsAdapter.delete(alliance_id)
                        except Exception:
                            pass

                    cleanup_embed = discord.Embed(
                        title="✅ Alliance Successfully Deleted",
                        description=(
                            f"Alliance **{alliance_name}** has been deleted.\n\n"
                            "**Cleaned Up Data:**\n"
                            f"🛡️ Alliance Records: {alliance_count}\n"
                            f"👥 Users Removed: {users_count_deleted}\n"
                            f"⚙️ Alliance Settings: {admin_settings_count}\n"
                            f"🏰 Admin Server Records: {admin_server_count}\n"
                            f"📢 Gift Channels: {gift_channels_count}\n"
                            f"📊 Gift Code Controls: {gift_code_control_count}"
                        ),
                        color=discord.Color.green()
                    )
                    cleanup_embed.set_footer(text="All related data has been successfully removed")
                    cleanup_embed.timestamp = discord.utils.utcnow()
                    
                    await button_interaction.response.edit_message(embed=cleanup_embed, view=None)
                    
                except Exception as e:
                    error_embed = discord.Embed(
                        title="❌ Error",
                        description=f"An error occurred while deleting the alliance: {str(e)}",
                        color=discord.Color.red()
                    )
                    await button_interaction.response.edit_message(embed=error_embed, view=None)

            async def cancel_callback(button_interaction: discord.Interaction):
                cancel_embed = discord.Embed(
                    title="❌ Deletion Cancelled",
                    description="Alliance deletion has been cancelled.",
                    color=discord.Color.grey()
                )
                await button_interaction.response.edit_message(embed=cancel_embed, view=None)

            confirm_button = discord.ui.Button(label="Confirm", style=discord.ButtonStyle.danger)
            cancel_button = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.grey)
            confirm_button.callback = confirm_callback
            cancel_button.callback = cancel_callback
            confirm_view.add_item(confirm_button)
            confirm_view.add_item(cancel_button)

            await interaction.response.edit_message(embed=confirm_embed, view=confirm_view)

        except Exception as e:
            print(f"Error in alliance_delete_callback: {e}")
            error_embed = discord.Embed(
                title="❌ Error",
                description="An error occurred while processing the deletion.",
                color=discord.Color.red()
            )
            if not interaction.response.is_done():
                await interaction.response.send_message(embed=error_embed, ephemeral=True)
            else:
                await interaction.followup.send(embed=error_embed, ephemeral=True)

    async def handle_button_interaction(self, interaction: discord.Interaction):
        custom_id = interaction.data["custom_id"]
        
        if custom_id == "main_menu":
            embed = discord.Embed(
                title="⚙️ Settings Menu",
                description=(
                    "Please select a category:\n\n"
                    "**Menu Categories**\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    "🏰 **Alliance Operations**\n"
                    "└ Manage alliances and settings\n\n"
                    "👥 **Alliance Member Operations**\n"
                    "└ Add, remove, and view members\n\n"
                    "🤖 **Bot Operations**\n"
                    "└ Configure bot settings\n\n"
                    "🎁 **Gift Code Operations**\n"
                    "└ Manage gift codes and rewards\n\n"
                    "📜 **Alliance History**\n"
                    "└ View alliance changes and history\n\n"
                    "📂 **Records Management**\n"
                    "└ Track custom player groups\n\n"
                    "🆘 **Support Operations**\n"
                    "└ Access support features\n"
                    "━━━━━━━━━━━━━━━━━━━━━━"
                ),
                color=discord.Color.blue()
            )
            
            view = discord.ui.View()
            view.add_item(discord.ui.Button(
                label="Alliance Operations",
                emoji="🏰",
                style=discord.ButtonStyle.primary,
                custom_id="alliance_operations",
                row=0
            ))
            view.add_item(discord.ui.Button(
                label="Member Operations",
                emoji="👥",
                style=discord.ButtonStyle.primary,
                custom_id="member_operations",
                row=0
            ))
            view.add_item(discord.ui.Button(
                label="Bot Operations",
                emoji="🤖",
                style=discord.ButtonStyle.primary,
                custom_id="bot_operations",
                row=1
            ))
            view.add_item(discord.ui.Button(
                label="Gift Operations",
                emoji="🎁",
                style=discord.ButtonStyle.primary,
                custom_id="gift_code_operations",
                row=1
            ))
            view.add_item(discord.ui.Button(
                label="Alliance History",
                emoji="📜",
                style=discord.ButtonStyle.primary,
                custom_id="alliance_history",
                row=2
            ))
            view.add_item(discord.ui.Button(
                label="Support Operations",
                emoji="🆘",
                style=discord.ButtonStyle.primary,
                custom_id="support_operations",
                row=2
            ))
            view.add_item(discord.ui.Button(
                label="Records",
                emoji="📂",
                style=discord.ButtonStyle.primary,
                custom_id="records_menu",
                row=3
            ))
            view.add_item(discord.ui.Button(
                label="Other Features",
                emoji="🔧",
                style=discord.ButtonStyle.primary,
                custom_id="other_features",
                row=3
            ))


            await interaction.response.edit_message(embed=embed, view=view)

        elif custom_id == "other_features":
            try:
                other_features_cog = interaction.client.get_cog("OtherFeatures")
                if other_features_cog:
                    await other_features_cog.show_other_features_menu(interaction)
                else:
                    await interaction.response.send_message(
                        "❌ Other Features module not found.",
                        ephemeral=True
                    )
            except Exception as e:
                if not any(error_code in str(e) for error_code in ["10062", "40060"]):
                    print(f"Other features error: {e}")
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "An error occurred while loading Other Features menu.",
                        ephemeral=True
                    )
                else:
                    await interaction.followup.send(
                        "An error occurred while loading Other Features menu.",
                        ephemeral=True
                    )

        elif custom_id == "records_menu":
            try:
                bot_ops_cog = interaction.client.get_cog("BotOperations")
                if bot_ops_cog:
                    await bot_ops_cog.records_menu(interaction)
                else:
                    await interaction.response.send_message(
                        "❌ Bot Operations module not found (Records inaccessible).",
                        ephemeral=True
                    )
            except Exception as e:
                print(f"Records menu error in alliance handle: {e}")
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "An error occurred while loading Records menu.",
                        ephemeral=True
                    )

    async def show_main_menu(self, interaction: discord.Interaction):
        try:
            embed = discord.Embed(
                title="⚙️ Settings Menu",
                description=(
                    "Please select a category:\n\n"
                    "**Menu Categories**\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    "🏰 **Alliance Operations**\n"
                    "└ Manage alliances and settings\n\n"
                    "👥 **Alliance Member Operations**\n"
                    "└ Add, remove, and view members\n\n"
                    "🤖 **Bot Operations**\n"
                    "└ Configure bot settings\n\n"
                    "🎁 **Gift Code Operations**\n"
                    "└ Manage gift codes and rewards\n\n"
                    "📜 **Alliance History**\n"
                    "└ View alliance changes and history\n\n"
                    "📂 **Records Management**\n"
                    "└ Track custom player groups\n\n"
                    "🆘 **Support Operations**\n"
                    "└ Access support features\n\n"
                    "🔧 **Other Features**\n"
                    "└ Access other features\n"
                    "━━━━━━━━━━━━━━━━━━━━━━"
                ),
                color=discord.Color.blue()
            )
            
            view = discord.ui.View()
            view.add_item(discord.ui.Button(
                label="Alliance Operations",
                emoji="🏰",
                style=discord.ButtonStyle.primary,
                custom_id="alliance_operations",
                row=0
            ))
            view.add_item(discord.ui.Button(
                label="Member Operations",
                emoji="👥",
                style=discord.ButtonStyle.primary,
                custom_id="member_operations",
                row=0
            ))
            view.add_item(discord.ui.Button(
                label="Bot Operations",
                emoji="🤖",
                style=discord.ButtonStyle.primary,
                custom_id="bot_operations",
                row=1
            ))
            view.add_item(discord.ui.Button(
                label="Gift Operations",
                emoji="🎁",
                style=discord.ButtonStyle.primary,
                custom_id="gift_code_operations",
                row=1
            ))
            view.add_item(discord.ui.Button(
                label="Alliance History",
                emoji="📜",
                style=discord.ButtonStyle.primary,
                custom_id="alliance_history",
                row=2
            ))
            view.add_item(discord.ui.Button(
                label="Support Operations",
                emoji="🆘",
                style=discord.ButtonStyle.primary,
                custom_id="support_operations",
                row=2
            ))
            view.add_item(discord.ui.Button(
                label="Records",
                emoji="📂",
                style=discord.ButtonStyle.primary,
                custom_id="records_menu",
                row=3
            ))
            view.add_item(discord.ui.Button(
                label="Other Features",
                emoji="🔧",
                style=discord.ButtonStyle.primary,
                custom_id="other_features",
                row=3
            ))

            try:
                await interaction.response.edit_message(embed=embed, view=view)
            except discord.InteractionResponded:
                pass
                
        except Exception as e:
            pass

    @discord.ui.button(label="Bot Operations", emoji="🤖", style=discord.ButtonStyle.primary, custom_id="bot_operations", row=1)
    async def bot_operations_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            bot_ops_cog = interaction.client.get_cog("BotOperations")
            if bot_ops_cog:
                await bot_ops_cog.show_bot_operations_menu(interaction)
            else:
                await interaction.response.send_message(
                    "❌ Bot Operations module not found.",
                    ephemeral=True
                )
        except Exception as e:
            print(f"Bot operations button error: {e}")
            await interaction.response.send_message(
                "❌ An error occurred. Please try again.",
                ephemeral=True
            )

    # =========================================================================
    # ALLIANCE MONITORING METHODS
    # =========================================================================

    def log_message(self, message: str):
        """Log a message with timestamp"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        log_entry = f"[{timestamp}] {message}\n"
        
        with open(self.log_file, 'a', encoding='utf-8') as f:
            f.write(log_entry)

    def _set_embed_footer(self, embed: discord.Embed, guild: Optional[discord.Guild] = None):
        """Set the standard footer for alliance monitoring embeds"""
        server_name = guild.name if guild else "ICE"
        embed.set_footer(
            text=f"Whiteout Survival || {server_name} ❄️",
            icon_url="https://cdn.discordapp.com/attachments/1435569370389807144/1436745053442805830/unnamed_5.png"
        )
    
    def _initialize_monitoring_tables(self):
        """Create necessary database tables if they don't exist"""
        try:
            with get_db_connection('settings.sqlite') as conn:
                cursor = conn.cursor()
                
                # Alliance monitoring configuration table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS alliance_monitoring (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        guild_id INTEGER NOT NULL,
                        alliance_id INTEGER NOT NULL,
                        channel_id INTEGER NOT NULL,
                        enabled INTEGER DEFAULT 1,
                        check_interval INTEGER DEFAULT 240,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(guild_id, alliance_id)
                    )
                """)
                
                # Member history table for change detection
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS member_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        fid TEXT NOT NULL,
                        alliance_id INTEGER NOT NULL,
                        nickname TEXT NOT NULL,
                        furnace_lv INTEGER NOT NULL,
                        state_id TEXT,
                        last_checked TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(fid, alliance_id)
                    )
                """)

                # New table for tracking furnace history over time
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS furnace_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        fid TEXT NOT NULL,
                        nickname TEXT,
                        alliance_id INTEGER,
                        old_level INTEGER,
                        new_level INTEGER,
                        change_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                conn.commit()
                self.log_message("Database tables initialized successfully")
                
                # Check if avatar_image column exists in member_history
                try:
                    cursor.execute("SELECT avatar_image FROM member_history LIMIT 1")
                except Exception:
                    try:
                        cursor.execute("ALTER TABLE member_history ADD COLUMN avatar_image TEXT")
                        conn.commit()
                        self.log_message("Added avatar_image column to member_history")
                    except Exception as e:
                        self.log_message(f"Error adding avatar_image column: {e}")
                
                # Check if state_id column exists
                try:
                    cursor.execute("SELECT state_id FROM member_history LIMIT 1")
                except Exception:
                    try:
                        cursor.execute("ALTER TABLE member_history ADD COLUMN state_id TEXT")
                        conn.commit()
                        self.log_message("Added state_id column to member_history")
                    except Exception as e:
                        self.log_message(f"Error adding state_id column: {e}")
                        
        except Exception as e:
            self.log_message(f"Error initializing database: {e}")
        except Exception as e:
            self.log_message(f"Error initializing database: {e}")

    def _sync_from_mongo(self):
        """Sync data from MongoDB to local SQLite on startup"""
        if not mongo_enabled():
            return

        self.log_message("Syncing data from MongoDB to local SQLite...")
        
        try:
            # Sync Alliance List
            alliances = AlliancesAdapter.get_all()
            for a in alliances:
                self.c.execute("INSERT OR REPLACE INTO alliance_list (alliance_id, name, discord_server_id) VALUES (?, ?, ?)",
                             (a['alliance_id'], a['name'], a['discord_server_id']))
            self.conn.commit()
            
            # Sync Alliance Settings
            settings = AllianceSettingsAdapter.get_all()
            for s in settings:
                self.c.execute("INSERT OR REPLACE INTO alliancesettings (alliance_id, channel_id, interval) VALUES (?, ?, ?)",
                             (s['alliance_id'], s['channel_id'], s['interval']))
            self.conn.commit()
            
            # Sync Alliance Monitoring
            monitors = AllianceMonitoringAdapter.get_all_monitors()
            with get_db_connection('settings.sqlite') as conn:
                cursor = conn.cursor()
                for m in monitors:
                    cursor.execute("""
                        INSERT OR REPLACE INTO alliance_monitoring 
                        (guild_id, alliance_id, channel_id, enabled, updated_at)
                        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """, (m['guild_id'], m['alliance_id'], m['channel_id'], m['enabled']))
                conn.commit()
                
            self.log_message(f"Synced {len(alliances)} alliances, {len(settings)} settings, {len(monitors)} monitors.")
            
        except Exception as e:
            self.log_message(f"Error syncing from MongoDB: {e}")

    def get_fl_emoji(self, fl_level: int) -> str:
        """Get emoji for furnace level"""
        # Removed custom emojis as per request
        return ""
    
    async def _get_monitoring_members(self, alliance_id: int) -> list:
        """Get all members of an alliance from database.
        
        Uses a targeted MongoDB query (not full-collection scan) and merges with
        SQLite so members added/removed via bot commands are auto-reflected on the
        next monitoring cycle without any manual refresh.
        """
        mongo_fids: set = set()
        res: list = []

        # --- MongoDB: targeted query by alliance_id ---
        if mongo_enabled() and AllianceMembersAdapter is not None:
            try:
                docs = await AllianceMembersAdapter.get_members_by_alliance_async(alliance_id)
                for d in docs:
                    try:
                        fid = str(d.get('fid') or d.get('id') or d.get('_id') or '').strip()
                        if not fid:
                            continue
                        nickname   = d.get('nickname') or d.get('name') or ''
                        furnace_lv = int(d.get('furnace_lv') or d.get('furnaceLevel') or d.get('furnace', 0) or 0)
                        state_id   = str(d.get('state_id') or d.get('kid') or '')
                        res.append((fid, nickname, furnace_lv, state_id))
                        mongo_fids.add(fid)
                    except Exception:
                        continue
            except Exception as e:
                self.log_message(f"[Monitor] Error querying MongoDB members for alliance {alliance_id}: {e}")

        # --- SQLite: always check to pick up newly added members not yet in MongoDB ---
        try:
            with get_db_connection('users.sqlite') as users_db:
                cursor = users_db.cursor()
                cursor.execute(
                    "SELECT fid, nickname, furnace_lv, kid FROM users WHERE alliance = ?",
                    (alliance_id,)
                )
                for row in cursor.fetchall():
                    fid = str(row[0]).strip()
                    if fid and fid not in mongo_fids:
                        res.append((fid, row[1] or '', int(row[2] or 0), str(row[3] or '')))
        except Exception as e:
            self.log_message(f"[Monitor] Error querying SQLite members for alliance {alliance_id}: {e}")

        return res
    
    async def _get_monitored_alliances(self) -> List[Dict]:
        """Get all alliances that are being monitored"""
        try:
            if mongo_enabled():
                return await AllianceMonitoringAdapter.get_all_monitors_async()
            
            with get_db_connection('settings.sqlite') as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, guild_id, alliance_id, channel_id, enabled, check_interval
                    FROM alliance_monitoring
                    WHERE enabled = 1
                """)
                
                results = []
                for row in cursor.fetchall():
                    results.append({
                        'id': row[0],
                        'guild_id': row[1],
                        'alliance_id': row[2],
                        'channel_id': row[3],
                        'enabled': row[4],
                        'check_interval': row[5]
                    })
                return results
        except Exception as e:
            self.log_message(f"Error getting monitored alliances: {e}")
            return []
    
    async def _check_alliance_changes(self, alliance_id: int, channel_id: int, guild_id: int):
        """Check for changes in an alliance and post notifications"""
        try:
            # Get guild object for footer
            guild = self.bot.get_guild(guild_id)
            
            # Get alliance name
            alliance_name = "Unknown Alliance"
            try:
                with get_db_connection('alliance.sqlite') as alliance_db:
                    cursor = alliance_db.cursor()
                    cursor.execute("SELECT name FROM alliance_list WHERE alliance_id = ?", (alliance_id,))
                    result = cursor.fetchone()
                    if result:
                        alliance_name = result[0]
            except Exception as e:
                self.log_message(f"Error getting alliance name: {e}")
            
            # Get current members from database
            current_members = await self._get_monitoring_members(alliance_id)
            
            if not current_members:
                self.log_message(f"No members found for alliance {alliance_id}")
                return
            
            # Get channel
            channel = self.bot.get_channel(channel_id)
            if not channel:
                self.log_message(f"Channel {channel_id} not found")
                return
            
            # Extract FIDs and current states for tracking
            fids = [str(fid) for fid, _, _, *rest in current_members]
            member_map = {}
            for m in current_members:
                fid = str(m[0])
                nickname = m[1]
                furnace_lv = m[2]
                state_id = m[3] if len(m) > 3 else ''
                member_map[fid] = (nickname, furnace_lv, state_id)
            
            self.log_message(f"Fetching data for {len(fids)} members using {'dual-API' if self.login_handler.dual_api_mode else 'single-API'} mode...")
            
            # Fetch all member data concurrently using batch processing
            api_results = await self.login_handler.fetch_player_batch(
                fids,
                alliance_id=str(alliance_id)
            )
            
            # Process results and detect changes
            changes_detected = []
            successful_fetches = 0
            failed_fetches = 0
            
            for i, api_result in enumerate(api_results):
                fid = fids[i]
                current_nickname, current_furnace_lv, current_state_id = member_map[fid]
                
                if api_result['status'] == 'success':
                    successful_fetches += 1
                    api_data = api_result['data']
                    api_nickname = api_data.get('nickname', current_nickname)
                    api_furnace_lv = api_data.get('stove_lv', current_furnace_lv)
                    api_state_id = str(api_data.get('kid', current_state_id))
                    
                    # Get historical data
                    if mongo_enabled() and AllianceMembersAdapter is not None:
                        # MongoDB Logic
                        try:
                            doc = await AllianceMembersAdapter.get_member_async(str(fid)) or {}
                            
                            old_nickname = doc.get('nickname') or doc.get('name')
                            old_furnace_lv = int(doc.get('furnace_lv') or doc.get('furnaceLevel') or doc.get('furnace', 0) or 0)
                            old_avatar = doc.get('avatar_image', '')
                            
                            # Check for name change
                            if old_nickname and api_nickname != old_nickname:
                                changes_detected.append({
                                    'type': 'name_change',
                                    'fid': fid,
                                    'old_value': old_nickname,
                                    'new_value': api_nickname,
                                    'furnace_lv': api_furnace_lv,
                                    'state_id': api_state_id,
                                    'alliance_name': alliance_name,
                                    'avatar_image': api_data.get('avatar_image', '')
                                })
                            
                            # Check for avatar change
                            api_avatar = api_data.get('avatar_image', '')
                            if api_avatar and old_avatar and api_avatar != old_avatar:
                                changes_detected.append({
                                    'type': 'avatar_change',
                                    'fid': fid,
                                    'nickname': api_nickname,
                                    'old_value': old_avatar,
                                    'new_value': api_avatar,
                                    'furnace_lv': api_furnace_lv,
                                    'state_id': api_state_id,
                                    'alliance_name': alliance_name
                                })
                            
                            # Check for furnace level change
                            if old_furnace_lv > 0 and api_furnace_lv != old_furnace_lv:
                                changes_detected.append({
                                    'type': 'furnace_change',
                                    'fid': fid,
                                    'nickname': api_nickname,
                                    'old_value': old_furnace_lv,
                                    'new_value': api_furnace_lv,
                                    'state_id': api_state_id,
                                    'alliance_name': alliance_name,
                                    'avatar_image': api_data.get('avatar_image', '')
                                })
                            
                            # Check for state change (Transfer)
                            old_state_id = str(doc.get('state_id') or doc.get('kid') or '')
                            if old_state_id and api_state_id != old_state_id:
                                changes_detected.append({
                                    'type': 'state_change',
                                    'fid': fid,
                                    'nickname': api_nickname,
                                    'old_value': old_state_id,
                                    'new_value': api_state_id,
                                    'furnace_lv': api_furnace_lv,
                                    'state_id': api_state_id,
                                    'alliance_name': alliance_name,
                                    'avatar_image': api_data.get('avatar_image', '')
                                })
                            
                            # Update MongoDB document
                            doc['fid'] = str(fid)
                            doc['alliance'] = alliance_id
                            doc['nickname'] = api_nickname
                            doc['furnace_lv'] = api_furnace_lv
                            doc['state_id'] = api_state_id
                            doc['avatar_image'] = api_data.get('avatar_image', '')
                            doc['last_checked'] = datetime.utcnow()
                            
                            await AllianceMembersAdapter.upsert_member_async(str(fid), doc)
                            
                        except Exception as e:
                            self.log_message(f"Error processing MongoDB member update for {fid}: {e}")

                    else:
                        # SQLite Logic (Fallback)
                        with get_db_connection('settings.sqlite') as conn:
                            cursor = conn.cursor()
                            cursor.execute("""
                                SELECT nickname, furnace_lv, avatar_image, state_id
                                FROM member_history 
                                WHERE fid = ? AND alliance_id = ?
                            """, (str(fid), alliance_id))
                            
                            history = cursor.fetchone()
                            
                            if history:
                                old_nickname = history[0]
                                old_furnace_lv = history[1]
                                
                                # Check for name change
                                if api_nickname != old_nickname:
                                    changes_detected.append({
                                        'type': 'name_change',
                                        'fid': fid,
                                        'old_value': old_nickname,
                                        'new_value': api_nickname,
                                        'furnace_lv': api_furnace_lv,
                                        'state_id': api_state_id,
                                        'alliance_name': alliance_name,
                                        'avatar_image': api_data.get('avatar_image', '')
                                    })
                                
                                # Check for avatar change
                                api_avatar = api_data.get('avatar_image', '')
                                old_avatar = history[2] if len(history) > 2 else ''
                                
                                if api_avatar and old_avatar and api_avatar != old_avatar:
                                    changes_detected.append({
                                        'type': 'avatar_change',
                                        'fid': fid,
                                        'nickname': api_nickname,
                                        'old_value': old_avatar,
                                        'new_value': api_avatar,
                                        'furnace_lv': api_furnace_lv,
                                        'state_id': api_state_id,
                                        'alliance_name': alliance_name
                                    })
                                
                                # Check for furnace level change
                                if api_furnace_lv != old_furnace_lv:
                                    changes_detected.append({
                                        'type': 'furnace_change',
                                        'fid': fid,
                                        'nickname': api_nickname,
                                        'old_value': old_furnace_lv,
                                        'new_value': api_furnace_lv,
                                        'state_id': api_state_id,
                                        'alliance_name': alliance_name,
                                        'avatar_image': api_data.get('avatar_image', '')
                                    })
                                
                                # Check for state change
                                old_state_id = history[3] if len(history) > 3 and history[3] else ''
                                if old_state_id and api_state_id != old_state_id:
                                    changes_detected.append({
                                        'type': 'state_change',
                                        'fid': fid,
                                        'nickname': api_nickname,
                                        'old_value': old_state_id,
                                        'new_value': api_state_id,
                                        'furnace_lv': api_furnace_lv,
                                        'state_id': api_state_id,
                                        'alliance_name': alliance_name,
                                        'avatar_image': api_data.get('avatar_image', '')
                                    })
                            
                            # Update or insert history
                            api_avatar = api_data.get('avatar_image', '')
                            cursor.execute("""
                                INSERT OR REPLACE INTO member_history 
                                (fid, alliance_id, nickname, furnace_lv, state_id, avatar_image, last_checked)
                                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                            """, (str(fid), alliance_id, api_nickname, api_furnace_lv, api_state_id, api_avatar))
                            
                            conn.commit()
                else:
                    failed_fetches += 1
                    if api_result['status'] != 'not_found':
                        self.log_message(f"Failed to fetch data for FID {fid}: {api_result.get('error_message', 'Unknown error')}")
            
            # Log batch processing results
            self.log_message(f"Batch processing complete: {successful_fetches} successful, {failed_fetches} failed out of {len(fids)} members")
            
            # Post change notifications
            for change in changes_detected:
                change_type = change.get('type')
                nickname = (
                    change.get('nickname')
                    or (change.get('new_value') if change_type == 'name_change' else None)
                    or "Unknown"
                )
                old_value = change.get('old_value')
                new_value = change.get('new_value')
                if change_type == 'furnace_change':
                    event_type = "furnace_changed"
                    status = "changed"
                    message = f"{nickname} moved from {format_furnace_level(old_value)} to {format_furnace_level(new_value)}"
                elif change_type == 'name_change':
                    event_type = "nickname_changed"
                    status = "changed"
                    message = f"{old_value} is now {new_value}"
                elif change_type == 'avatar_change':
                    event_type = "avatar_changed"
                    status = "changed"
                    message = f"{nickname} changed profile avatar"
                elif change_type == 'state_change':
                    event_type = "state_changed"
                    status = "changed"
                    message = f"{nickname} moved from State {old_value} to State {new_value}"
                else:
                    event_type = "profile_changed"
                    status = "changed"
                    message = f"{nickname} profile changed"

                await publish_bot_activity(
                    workflow="alliance_monitor",
                    event_type=event_type,
                    status=status,
                    message=message,
                    guild_id=getattr(guild, "id", None),
                    guild_name=getattr(guild, "name", None),
                    alliance_id=alliance_id,
                    alliance_name=change.get('alliance_name') or alliance_name,
                    fid=str(change.get('fid')),
                    nickname=nickname,
                    state_id=change.get('state_id'),
                    old_value=old_value,
                    new_value=new_value,
                    details={
                        "furnace_lv": change.get('furnace_lv'),
                        "avatar_image": change.get('avatar_image', ''),
                    },
                )

                # Consolidated event logging for dashboard
                if mongo_enabled():
                    try:
                        await AllianceEventsAdapter.log_event_async(
                            event_type=change_type,
                            fid=str(change['fid']),
                            nickname=nickname,
                            alliance_id=alliance_id,
                            old_val=change.get('old_value'),
                            new_val=change.get('new_value'),
                            extra={'avatar_image': change.get('avatar_image', '')}
                        )
                    except Exception as e:
                        self.log_message(f"Error logging consolidated event: {e}")

                # Log furnace changes to history table
                if change['type'] == 'furnace_change':
                    try:
                        if mongo_enabled():
                            await FurnaceHistoryAdapter.insert_async({
                                'fid': str(change['fid']),
                                'nickname': change['nickname'],
                                'alliance_id': alliance_id,
                                'old_level': change['old_value'],
                                'new_level': change['new_value']
                            })
                        else:
                            with get_db_connection('settings.sqlite') as conn:
                                cursor = conn.cursor()
                                cursor.execute("""
                                    INSERT INTO furnace_history (fid, nickname, alliance_id, old_level, new_level)
                                    VALUES (?, ?, ?, ?, ?)
                                """, (str(change['fid']), change['nickname'], alliance_id, change['old_value'], change['new_value']))
                                conn.commit()
                    except Exception as e:
                        self.log_message(f"Error logging furnace history: {e}")

                embed = self._create_change_embed(change, guild)
                await channel.send(embed=embed)
                self.log_message(f"Posted {change['type']} notification for ID {change['fid']}")
            
            if changes_detected:
                try:
                    async with self._scan_stats_lock:
                        self._last_cycle_stats["changes"] = int(self._last_cycle_stats.get("changes") or 0) + len(changes_detected)
                except Exception:
                    self._last_cycle_stats["changes"] = int(self._last_cycle_stats.get("changes") or 0) + len(changes_detected)
                self.log_message(f"Detected {len(changes_detected)} changes for alliance {alliance_id}")
            
        except Exception as e:
            self.log_message(f"Error checking alliance {alliance_id}: {e}")
    
    def _create_change_embed(self, change: Dict, guild: Optional[discord.Guild] = None) -> discord.Embed:
        """Create an attractive embed for a detected change"""
        timestamp = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
        
        if change['type'] == 'name_change':
            embed = discord.Embed(
                title="👤 Name Change Detected",
                color=discord.Color.blue()
            )
            
            furnace_level_str = format_furnace_level(change['furnace_lv'])
            fl_emoji = self.get_fl_emoji(change['furnace_lv'])
            
            embed.add_field(name="Player 🆔 ", value=f"`{change['fid']}`", inline=False)
            embed.add_field(name="📝 Old Name", value=f"~~`{change['old_value']}`~~", inline=True)
            embed.add_field(name="✨ New Name", value=f"**`{change['new_value']}`**", inline=True)
            embed.add_field(name="⚔️ Furnace Level", value=f"{fl_emoji} `{furnace_level_str}`", inline=False)
            embed.add_field(name="🏰 Alliance", value=f"`{change['alliance_name']}`", inline=True)
            embed.add_field(name="🕐 Time", value=f"`{timestamp}`", inline=True)
            
            if change.get('avatar_image'):
                embed.set_thumbnail(url=change['avatar_image'])
            
        elif change['type'] == 'avatar_change':
            embed = discord.Embed(
                title="<a:profile:1454933848516464891> Avatar Change Detected",
                color=discord.Color.purple()
            )
            
            furnace_level_str = format_furnace_level(change['furnace_lv'])
            fl_emoji = self.get_fl_emoji(change['furnace_lv'])
            
            embed.add_field(name="🆔 Player ID", value=f"`{change['fid']}`", inline=False)
            embed.add_field(name="👤 Player Name", value=f"`{change['nickname']}`", inline=False)
            embed.add_field(name="⚔️ Furnace Level", value=f"{fl_emoji} `{furnace_level_str}`", inline=False)
            embed.add_field(name="🏰 Alliance", value=f"`{change['alliance_name']}`", inline=True)
            embed.add_field(name="🕐 Time", value=f"`{timestamp}`", inline=True)
            embed.add_field(name="Previous Profile ↗️", value="*(See Thumbnail)*", inline=True)
            
            embed.add_field(name="New Profile ⬇️", value="*(See Image Below)*", inline=False)
            
            # Set old avatar as thumbnail and new avatar as image
            if change['old_value']:
                embed.set_thumbnail(url=change['old_value'])
            
            if change['new_value']:
                embed.set_image(url=change['new_value'])
            
        elif change['type'] == 'furnace_change':
            # Determine if it's an upgrade or downgrade
            is_upgrade = change['new_value'] > change['old_value']
            title = "<a:furnace:1454930497623953591> Furnace Level Up 📈" if is_upgrade else "📉 Furnace Level Change"
            color = discord.Color.green() if is_upgrade else discord.Color.orange()
            
            embed = discord.Embed(
                title=title,
                color=color
            )
            
            old_level_str = format_furnace_level(change['old_value'])
            new_level_str = format_furnace_level(change['new_value'])
            old_emoji = self.get_fl_emoji(change['old_value'])
            new_emoji = self.get_fl_emoji(change['new_value'])
            
            embed.add_field(name="Player 🆔", value=f"`{change['fid']}`", inline=False)
            embed.add_field(name="👤 Player Name", value=f"`{change['nickname']}`", inline=False)
            embed.add_field(name="📊 Previous Level", value=f"{old_emoji} `{old_level_str}`", inline=True)
            embed.add_field(name="🎉 New Level", value=f"{new_emoji} `{new_level_str}`", inline=True)
            embed.add_field(name="🏰 Alliance", value=f"`{change['alliance_name']}`", inline=True)
            embed.add_field(name="🕐 Time", value=f"`{timestamp}`", inline=True)
            
            if change.get('avatar_image'):
                embed.set_thumbnail(url=change['avatar_image'])
        
        elif change['type'] == 'state_change':
            embed = discord.Embed(
                title="✈️ State Transfer Detected",
                description=f"**{change['nickname']}** has transferred to a different state!",
                color=discord.Color.gold()
            )
            
            furnace_level_str = self.level_mapping.get(change['furnace_lv'], str(change['furnace_lv']))
            fl_emoji = self.get_fl_emoji(change['furnace_lv'])
            
            embed.add_field(name="Player 🆔", value=f"`{change['fid']}`", inline=False)
            embed.add_field(name="👤 Player Name", value=f"`{change['nickname']}`", inline=False)
            embed.add_field(name="🌍 Old State", value=f"`#{change['old_value']}`", inline=True)
            embed.add_field(name="🚀 New State", value=f"**`#{change['new_value']}`**", inline=True)
            embed.add_field(name="⚔️ Furnace Level", value=f"{fl_emoji} `{furnace_level_str}`", inline=False)
            embed.add_field(name="🏰 Alliance", value=f"`{change['alliance_name']}`", inline=True)
            embed.add_field(name="🕐 Time", value=f"`{timestamp}`", inline=True)
            
            if change.get('avatar_image'):
                embed.set_thumbnail(url=change['avatar_image'])
        
        self._set_embed_footer(embed, guild)
        return embed
    
    @tasks.loop(minutes=4)
    async def monitor_alliances(self):
        """Background task that monitors alliances for changes.

        Alliances are scanned CONCURRENTLY (up to 10 at a time) instead of
        sequentially so a large number of servers doesn't cause delay build-up.
        A semaphore keeps API pressure reasonable; each coroutine has its own
        per-alliance sleep buffer to stay polite to the upstream game API.
        """
        # Lazily create async primitives (must happen inside a running loop)
        if self._scan_semaphore is None:
            self._scan_semaphore = asyncio.Semaphore(10)   # max 10 parallel scans
        if self._scan_stats_lock is None:
            self._scan_stats_lock = asyncio.Lock()

        try:
            self.log_message("Starting alliance monitoring cycle")

            monitored = await self._get_monitored_alliances()

            if not monitored:
                self.log_message("No alliances being monitored")
                return

            self.log_message(f"Monitoring {len(monitored)} alliance(s) concurrently (batch size ≤10)")

            # Reset cycle stats
            from bot_activity import publish_bot_activity
            self._last_cycle_stats["start"] = datetime.utcnow()
            self._last_cycle_stats["alliances"] = len(monitored)
            self._last_cycle_stats["changes"] = 0
            self._current_scanning_alliance = []   # reset active-scan list

            await publish_bot_activity(
                workflow="alliance_monitor",
                event_type="cycle_started",
                status="processing",
                message=f"Starting concurrent scan of {len(monitored)} alliance(s)"
            )

            async def _scan_one(config: dict) -> None:
                """Scan a single alliance, guarded by the shared semaphore."""
                guild_id = config['guild_id']

                # Skip locked guilds
                try:
                    if await ServerLimitsAdapter.is_monitor_locked_async(guild_id):
                        self.log_message(f"🔒 Skipping guild {guild_id} (monitor locked)")
                        return
                except Exception:
                    pass  # fail-open

                # Resolve alliance name for the activity feed
                alliance_name = "Unknown Alliance"
                try:
                    with get_db_connection('alliance.sqlite') as alliance_db:
                        cur = alliance_db.cursor()
                        cur.execute(
                            "SELECT name FROM alliance_list WHERE alliance_id = ?",
                            (config['alliance_id'],)
                        )
                        row = cur.fetchone()
                        if row:
                            alliance_name = row[0]
                except Exception:
                    pass

                scan_entry = {
                    "id": config['alliance_id'],
                    "name": alliance_name,
                    "guild_id": guild_id,
                    "started_at": datetime.utcnow()
                }

                # Register this scan in the active-scan list (thread-safe via lock)
                async with self._scan_stats_lock:
                    self._current_scanning_alliance.append(scan_entry)

                try:
                    await publish_bot_activity(
                        workflow="alliance_monitor",
                        event_type="scan_started",
                        status="processing",
                        message=f"Scanning alliance: {alliance_name}",
                        guild_id=guild_id,
                        alliance_id=config['alliance_id'],
                        alliance_name=alliance_name
                    )

                    async with self._scan_semaphore:
                        await self._check_alliance_changes(
                            config['alliance_id'],
                            config['channel_id'],
                            config['guild_id']
                        )
                        # Short per-scan delay — keeps API polite without blocking others
                        await asyncio.sleep(1)

                except Exception as scan_err:
                    self.log_message(
                        f"Error scanning alliance {config['alliance_id']} "
                        f"({alliance_name}): {scan_err}"
                    )
                finally:
                    # Always remove from active list when done
                    async with self._scan_stats_lock:
                        try:
                            self._current_scanning_alliance.remove(scan_entry)
                        except ValueError:
                            pass

            # Launch all scans concurrently; gather waits for all to finish
            await asyncio.gather(*[_scan_one(cfg) for cfg in monitored])

            self._current_scanning_alliance = []
            self._last_cycle_stats["end"] = datetime.utcnow()
            self.log_message("Alliance monitoring cycle completed")
            await publish_bot_activity(
                workflow="alliance_monitor",
                event_type="cycle_completed",
                status="success",
                message=(
                    f"Completed concurrent scan of {len(monitored)} alliance(s); "
                    f"{int(self._last_cycle_stats.get('changes') or 0)} change(s) detected"
                ),
                details={"changes": int(self._last_cycle_stats.get("changes") or 0)}
            )

        except Exception as e:
            self.log_message(f"Error in monitoring task: {e}")
            try:
                from bot_activity import publish_bot_activity
                await publish_bot_activity(
                    workflow="alliance_monitor",
                    event_type="cycle_failed",
                    status="failed",
                    message=f"Monitoring cycle error: {str(e)[:100]}"
                )
            except Exception:
                pass
    
    @monitor_alliances.before_loop
    async def before_monitor_alliances(self):
        """Wait for bot to be ready before starting monitoring"""
        await self.bot.wait_until_ready()
        
        # Check API availability and enable dual-API mode if not already done
        if not self._api_check_done:
            try:
                self.log_message("Checking API availability for dual-API mode...")
                api_status = await self.login_handler.check_apis_availability()
                
                if self.login_handler.dual_api_mode:
                    self.log_message(f"✓ Dual API mode enabled with APIs {self.login_handler.available_apis}")
                    self.log_message(f"  API 1: {api_status['api1_url']} - {'Available' if api_status['api1_available'] else 'Unavailable'}")
                    self.log_message(f"  API 2: {api_status['api2_url']} - {'Available' if api_status['api2_available'] else 'Unavailable'}")
                    self.log_message(f"  Request delay: {self.login_handler.request_delay}s (concurrent processing enabled)")
                else:
                    self.log_message(f"Single API mode - using API {self.login_handler.available_apis[0] if self.login_handler.available_apis else 'None'}")
                    self.log_message(f"  Request delay: {self.login_handler.request_delay}s")
                
                self._api_check_done = True
            except Exception as e:
                self.log_message(f"Error checking API availability: {e}")
        
        self.log_message("Alliance monitoring task ready")
    
    # /setalliancelogchannel command removed - now available via /alliancemonitor dashboard
    
    # /selectalliance command removed - now available via /alliancemonitor dashboard
    
    # /alliancemonitoringstatus command removed - now available via /alliancemonitor dashboard
    
    @app_commands.command(name="alliancemonitor", description="Alliance monitoring dashboard with quick access to all monitoring features")
    @command_animation
    async def alliance_monitor(self, interaction: discord.Interaction):
        """Display alliance monitoring dashboard with authentication"""
        try:
            # Import authentication adapters
            from db.mongo_adapters import mongo_enabled, ServerAllianceAdapter, AuthSessionsAdapter
            
            # Check if MongoDB is enabled
            if not mongo_enabled() or not ServerAllianceAdapter:
                await interaction.followup.send(
                    "❌ MongoDB not enabled. Cannot access Alliance Monitor.",
                    ephemeral=True
                )
                return
            
            # Check if password is set
            stored_password = ServerAllianceAdapter.get_password(interaction.guild.id)
            if not stored_password:
                error_embed = discord.Embed(
                    title="🔒 Access Denied",
                    description="No password configured for Alliance Monitor access.",
                    color=0x2B2D31
                )
                error_embed.add_field(
                    name="⚙️ Administrator Action Required",
                    value="Contact a server administrator to set up password via:\n`/settings` → **Bot Operations** → **Set Member List Password**",
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
                        # Add link button to contact global admin
                        self.add_item(discord.ui.Button(
                            label="Contact Global Admin",
                            emoji="👤",
                            style=discord.ButtonStyle.link,
                            url="https://discord.com/users/850786361572720661"
                        ))
                
                view = ContactAdminView()
                await interaction.followup.send(embed=error_embed, view=view, ephemeral=True)
                return
            
            # Check if user has a valid authentication session
            if AuthSessionsAdapter and AuthSessionsAdapter.is_session_valid(
                interaction.guild.id,
                interaction.user.id,
                stored_password
            ):
                # User has valid session, show Alliance Monitor dashboard directly
                view = AllianceMonitorView(self, interaction.guild.id)
                
                embed = discord.Embed(
                    title="🏰 Alliance Monitoring Dashboard",
                    description=(
                        "Centralized control panel for alliance monitoring operations.\n\n"
                        "**Available Features:**\n"
                        "• 👤 Track name changes\n"
                        "• 🔥 Monitor furnace level changes\n"
                        "• 🖼️ Detect avatar changes\n\n"
                        "Use the buttons below to manage your monitoring settings."
                    ),
                    color=discord.Color.blue()
                )
                await interaction.followup.send(
                    content="✅ **Access Granted** (Session Active)",
                    embed=embed,
                    view=view,
                    ephemeral=True
                )
                return
            
            # No valid session - show authentication modal
            class AllianceAuthModal(discord.ui.Modal, title="🛡️ Security Verification"):
                password_input = discord.ui.TextInput(
                    label="Enter Access Code",
                    placeholder="••••••••••••",
                    style=discord.TextStyle.short,
                    required=True,
                    max_length=50
                )
                
                def __init__(self, guild_id: int, guild_name: str, cog_instance):
                    super().__init__()
                    self.guild_id = guild_id
                    self.guild_name = guild_name
                    self.cog = cog_instance
                
                async def on_submit(self, modal_interaction: discord.Interaction):
                    try:
                        entered_password = self.password_input.value.strip()
                        
                        # Verify password
                        if not ServerAllianceAdapter.verify_password(self.guild_id, entered_password):
                            error_embed = discord.Embed(
                                title="❌ Authentication Failed",
                                description="The access code you entered is incorrect.",
                                color=0xED4245
                            )
                            error_embed.add_field(
                                name="🔄 Try Again",
                                value="Use `/alliancemonitor` command again to retry.",
                                inline=False
                            )
                            await modal_interaction.response.send_message(embed=error_embed, ephemeral=True)
                            return
                        
                        # Authentication successful - create session
                        if AuthSessionsAdapter:
                            try:
                                AuthSessionsAdapter.create_session(
                                    self.guild_id,
                                    modal_interaction.user.id,
                                    entered_password
                                )
                            except Exception as session_error:
                                print(f"Failed to create auth session: {session_error}")
                        
                        # Show Alliance Monitor dashboard directly
                        view = AllianceMonitorView(self.cog, modal_interaction.guild.id)
                        
                        embed = discord.Embed(
                            title="🏰 Alliance Monitoring Dashboard",
                            description=(
                                "Centralized control panel for alliance monitoring operations.\n\n"
                                "**Available Features:**\n"
                                "• 👤 Track name changes\n"
                                "• 🔥 Monitor furnace level changes\n"
                                "• 🖼️ Detect avatar changes\n\n"
                                "Use the buttons below to manage your monitoring settings."
                            ),
                            color=discord.Color.blue()
                        )
                        await modal_interaction.response.send_message(
                            content="✅ **Access Granted**",
                            embed=embed,
                            view=view,
                            ephemeral=True
                        )
                    
                    except Exception as e:
                        print(f"Error in alliance auth modal: {e}")
                        import traceback
                        traceback.print_exc()
                        await modal_interaction.response.send_message(
                            "❌ An error occurred during authentication.",
                            ephemeral=True
                        )
            
            # Create authentication view with button
            class AllianceAuthView(discord.ui.View):
                def __init__(self, guild_id: int, guild_name: str, cog_instance):
                    super().__init__(timeout=60)
                    self.guild_id = guild_id
                    self.guild_name = guild_name
                    self.cog = cog_instance
                
                @discord.ui.button(label="Authenticate", emoji="🔐", style=discord.ButtonStyle.secondary, custom_id="alliance_auth_cmd")
                async def authenticate(self, button_interaction: discord.Interaction, button: discord.ui.Button):
                    modal = AllianceAuthModal(self.guild_id, self.guild_name, self.cog)
                    await button_interaction.response.send_modal(modal)
            
            # Create authentication embed
            auth_embed = discord.Embed(
                title=interaction.guild.name,
                description="**Alliance Monitor Access**\n\nAuthentication required to access alliance monitoring features.",
                color=0x2B2D31
            )
            
            auth_embed.set_author(
                name="SECURITY VERIFICATION REQUIRED",
                icon_url="https://cdn.discordapp.com/attachments/1435569370389807144/1445470757844160543/unnamed_6_1.png"
            )
            
            auth_embed.add_field(
                name="🔒 Protected Resource",
                value="Alliance Monitoring Dashboard",
                inline=True
            )
            
            auth_embed.add_field(
                name="🔑 Authentication Method",
                value="Access Code",
                inline=True
            )
            
            auth_embed.add_field(
                name="⚡ Quick Actions",
                value="Click the button below to proceed with authentication.",
                inline=False
            )
            
            auth_embed.set_footer(
                text="Secured by Discord Interaction Gateway"
            )
            
            # Send authentication embed with button
            view = AllianceAuthView(interaction.guild.id, interaction.guild.name, self)
            await interaction.followup.send(embed=auth_embed, view=view, ephemeral=True)
            
        except Exception as e:
            self.log_message(f"Error in alliance_monitor: {e}")
            import traceback
            traceback.print_exc()
            await interaction.followup.send(
                "❌ An error occurred while opening the monitoring dashboard.",
                ephemeral=True
            )
    

    # /stopalliancemonitoring command removed - now available via /alliancemonitor dashboard


class AllianceMonitorView(discord.ui.View):
    """Interactive view for alliance monitoring dashboard"""
    def __init__(self, cog, guild_id=None):
        super().__init__(timeout=300)  # 5 minute timeout
        self.cog = cog
        self.guild_id = guild_id
        
        # Dynamically update the toggle button based on monitoring status
        if guild_id:
            self._update_toggle_button()
    
    def _update_toggle_button(self):
        """Update the toggle button based on current monitoring status"""
        try:
            # Import ServerAllianceAdapter
            from db.mongo_adapters import ServerAllianceAdapter
            
            # Get server's assigned alliance
            alliance_id = ServerAllianceAdapter.get_alliance(self.guild_id)
            
            if alliance_id:
                # Check if monitoring is enabled
                with get_db_connection('settings.sqlite') as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT enabled
                        FROM alliance_monitoring
                        WHERE guild_id = ? AND alliance_id = ?
                    """, (self.guild_id, alliance_id))
                    
                    result = cursor.fetchone()
                    is_enabled = result[0] if result else False
                
                # Find and update the toggle button
                for item in self.children:
                    if isinstance(item, discord.ui.Button) and item.custom_id == "toggle_monitoring":
                        if is_enabled:
                            item.label = "Stop Monitoring"
                            item.emoji = "🛑"
                            item.style = discord.ButtonStyle.danger
                        else:
                            item.label = "Enable Monitoring"
                            item.emoji = "✅"
                            item.style = discord.ButtonStyle.success
                        break
        except Exception as e:
            self.cog.log_message(f"Error updating toggle button: {e}")
    
    @discord.ui.button(
        label="Set Log Channel",
        emoji="📝",
        style=discord.ButtonStyle.primary,
        custom_id="set_log_channel"
    )
    async def set_log_channel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Button to set alliance log channel - automatically uses server's assigned alliance"""
        try:
            # Import ServerAllianceAdapter
            try:
                from db.mongo_adapters import ServerAllianceAdapter
            except:
                await interaction.response.send_message(
                    "❌ MongoDB not enabled. Alliance monitoring requires MongoDB.",
                    ephemeral=True
                )
                return
            
            # Get server's assigned alliance
            alliance_id = ServerAllianceAdapter.get_alliance(interaction.guild_id)
            
            if not alliance_id:
                await interaction.response.send_message(
                    "❌ **No Alliance Assigned**\n\n"
                    "This server doesn't have an assigned alliance yet.\n\n"
                    "**To assign an alliance:**\n"
                    "1. Use `/manage` command\n"
                    "2. Click **Assign Server Alliance**\n"
                    "3. Select your alliance\n\n"
                    "Then return here to set up monitoring.",
                    ephemeral=True
                )
                return
            
            # Get alliance name
            alliance_name = "Unknown Alliance"
            try:
                with get_db_connection('alliance.sqlite') as alliance_db:
                    cursor = alliance_db.cursor()
                    cursor.execute("SELECT name FROM alliance_list WHERE alliance_id = ?", (alliance_id,))
                    result = cursor.fetchone()
                    if result:
                        alliance_name = result[0]
            except Exception:
                pass
            
            # Check if monitoring is already configured
            current_channel_id = None
            is_enabled = False
            try:
                with get_db_connection('settings.sqlite') as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT channel_id, enabled 
                        FROM alliance_monitoring 
                        WHERE guild_id = ? AND alliance_id = ?
                    """, (interaction.guild_id, alliance_id))
                    result = cursor.fetchone()
                    if result:
                        current_channel_id = result[0]
                        is_enabled = result[1]
            except Exception:
                pass
            
            # Create channel select menu
            channel_select = discord.ui.ChannelSelect(
                placeholder="Select a channel for alliance logs...",
                channel_types=[discord.ChannelType.text],
                min_values=1,
                max_values=1
            )
            
            async def channel_callback(select_interaction: discord.Interaction):
                channel = select_interaction.data['values'][0]
                channel_id = int(channel)
                
                # Save monitoring configuration immediately
                try:
                    # Get member count
                    members = await self.cog._get_monitoring_members(alliance_id)
                    member_count = len(members) if members else 0
                    
                    # Save to database
                    with get_db_connection('settings.sqlite') as conn:
                        cursor = conn.cursor()
                        cursor.execute("""
                            INSERT OR REPLACE INTO alliance_monitoring 
                            (guild_id, alliance_id, channel_id, enabled, updated_at)
                            VALUES (?, ?, ?, 1, CURRENT_TIMESTAMP)
                        """, (interaction.guild_id, alliance_id, channel_id))
                        conn.commit()
                    
                    # Save to MongoDB
                    if mongo_enabled():
                        AllianceMonitoringAdapter.upsert_monitor(interaction.guild_id, alliance_id, channel_id, enabled=1)
                        # ✅ Auto-unlock the alliance monitor for this guild.
                        # By default, on_guild_join locks it for all new servers.
                        # When the user explicitly configures monitoring, they intend
                        # to use it — so unlock it now.
                        try:
                            ServerLimitsAdapter.set(interaction.guild_id, {
                                'alliance_monitor_locked': False,
                                'updated_by': 0  # 0 = system auto-unlock on user setup
                            })
                            self.cog.log_message(
                                f"[Monitor] Auto-unlocked alliance monitor for guild {interaction.guild_id}"
                            )
                        except Exception as unlock_err:
                            self.cog.log_message(
                                f"[Monitor] Warning: Could not auto-unlock monitor for guild "
                                f"{interaction.guild_id}: {unlock_err}"
                            )
                    
                    # Initialize member history
                    if members:
                        with get_db_connection('settings.sqlite') as conn:
                            cursor = conn.cursor()
                            for fid, nickname, furnace_lv, *_ in members:
                                cursor.execute("""
                                    INSERT OR REPLACE INTO member_history 
                                    (fid, alliance_id, nickname, furnace_lv, last_checked)
                                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                                """, (str(fid), alliance_id, nickname, furnace_lv))
                            conn.commit()
                    
                    # Create success embed
                    success_embed = discord.Embed(
                        title="✅ Alliance Monitoring Configured",
                        description=(
                            f"**Alliance:** {alliance_name}\n"
                            f"**Alliance ID:** {alliance_id}\n"
                            f"**Log Channel:** <#{channel_id}>\n"
                            f"**Members Tracked:** {member_count}\n\n"
                            f"**Monitoring Active** ✅\n"
                            f"The system will check for changes every 4 minutes.\n\n"
                            f"**Tracked Changes:**\n"
                            f"• 👤 Name changes\n"
                            f"• 🔥 Furnace level changes\n"
                            f"• 🖼️ Avatar changes"
                        ),
                        color=discord.Color.green()
                    )
                    
                    self.cog._set_embed_footer(success_embed)
                    
                    await select_interaction.response.edit_message(
                        content=None,
                        embed=success_embed,
                        view=None
                    )
                    
                    self.cog.log_message(f"Monitoring configured for alliance {alliance_id} ({alliance_name}) in channel {channel_id}")
                    
                except Exception as e:
                    self.cog.log_message(f"Error saving monitoring config: {e}")
                    await select_interaction.response.edit_message(
                        content="❌ Error saving monitoring configuration.",
                        embed=None,
                        view=None
                    )
            
            channel_select.callback = channel_callback
            view = discord.ui.View()
            view.add_item(channel_select)
            
            # Create message with current status
            status_msg = f"**Alliance:** {alliance_name} (ID: {alliance_id})\n\n"
            
            if current_channel_id:
                status_emoji = "✅" if is_enabled else "⚠️"
                status_text = "Active" if is_enabled else "Disabled"
                status_msg += (
                    f"**Current Status:** {status_emoji} {status_text}\n"
                    f"**Current Channel:** <#{current_channel_id}>\n\n"
                    f"Select a new channel below to change the monitoring channel:"
                )
            else:
                status_msg += "Select a channel below to start monitoring this alliance:"
            
            await interaction.response.send_message(
                status_msg,
                view=view,
                ephemeral=True
            )
            
        except Exception as e:
            self.cog.log_message(f"Error in set_log_channel_button: {e}")
            await interaction.response.send_message(
                "❌ An error occurred while setting the log channel.",
                ephemeral=True
            )
    
    @discord.ui.button(
        label="View Status",
        emoji="📊",
        style=discord.ButtonStyle.secondary,
        custom_id="view_status"
    )
    async def view_status_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Button to view monitoring status for server's assigned alliance"""
        try:
            await interaction.response.defer(ephemeral=True)
            
            # Import ServerAllianceAdapter
            try:
                from db.mongo_adapters import ServerAllianceAdapter
            except:
                await interaction.followup.send(
                    "❌ MongoDB not enabled. Alliance monitoring requires MongoDB.",
                    ephemeral=True
                )
                return
            
            # Get server's assigned alliance
            alliance_id = ServerAllianceAdapter.get_alliance(interaction.guild_id)
            
            if not alliance_id:
                await interaction.followup.send(
                    "ℹ️ **No Alliance Assigned**\n\n"
                    "This server doesn't have an assigned alliance yet.\n\n"
                    "Use `/manage` → **Assign Server Alliance** to assign one.",
                    ephemeral=True
                )
                return
            
            # Get monitoring configuration for the assigned alliance
            with get_db_connection('settings.sqlite') as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT channel_id, enabled, created_at, updated_at
                    FROM alliance_monitoring
                    WHERE guild_id = ? AND alliance_id = ?
                """, (interaction.guild_id, alliance_id))
                
                config = cursor.fetchone()
            
            # Get alliance name
            alliance_name = "Unknown Alliance"
            member_count = 0
            try:
                with get_db_connection('alliance.sqlite') as alliance_db:
                    cursor = alliance_db.cursor()
                    cursor.execute("SELECT name FROM alliance_list WHERE alliance_id = ?", (alliance_id,))
                    result = cursor.fetchone()
                    if result:
                        alliance_name = result[0]
                
                # Get live member count (merges MongoDB + SQLite so it's always accurate)
                live_members = await self.cog._get_monitoring_members(alliance_id)
                member_count = len(live_members)
            except Exception:
                pass
            
            # Create status embed
            if not config:
                embed = discord.Embed(
                    title="📊 Alliance Monitoring Status",
                    description=(
                        f"**Server Alliance:** {alliance_name}\n"
                        f"**Alliance ID:** {alliance_id}\n\n"
                        f"**Status:** ⚠️ Not Configured\n\n"
                        f"Use the **Set Log Channel** button to start monitoring this alliance."
                    ),
                    color=discord.Color.orange()
                )
            else:
                channel_id, enabled, created_at, updated_at = config
                status_emoji = "✅" if enabled else "❌"
                status_text = "Active" if enabled else "Disabled"
                
                embed = discord.Embed(
                    title="📊 Alliance Monitoring Status",
                    description=(
                        f"**Server Alliance:** {alliance_name}\n"
                        f"**Alliance ID:** {alliance_id}\n\n"
                        f"**Status:** {status_emoji} {status_text}\n"
                        f"**Log Channel:** <#{channel_id}>\n"
                        f"**Members Tracked:** {member_count}\n\n"
                        f"**Monitored Changes:**\n"
                        f"• 👤 Name changes\n"
                        f"• 🔥 Furnace level changes\n"
                        f"• 🖼️ Avatar changes\n\n"
                        f"The system checks for changes every 4 minutes."
                    ),
                    color=discord.Color.green() if enabled else discord.Color.red()
                )
            
            self.cog._set_embed_footer(embed, interaction.guild)
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            self.cog.log_message(f"Error in view_status_button: {e}")
            await interaction.followup.send(
                "❌ An error occurred while retrieving monitoring status.",
                ephemeral=True
            )
    
    @discord.ui.button(
        label="Stop Monitoring",
        emoji="🛑",
        style=discord.ButtonStyle.danger,
        custom_id="toggle_monitoring"
    )
    async def toggle_monitoring_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Button to toggle monitoring (enable/disable) for the server's assigned alliance"""
        try:
            await interaction.response.defer(ephemeral=True)
            
            # Import ServerAllianceAdapter
            try:
                from db.mongo_adapters import ServerAllianceAdapter
            except:
                await interaction.followup.send(
                    "❌ MongoDB not enabled. Alliance monitoring requires MongoDB.",
                    ephemeral=True
                )
                return
            
            # Get server's assigned alliance
            alliance_id = ServerAllianceAdapter.get_alliance(interaction.guild_id)
            
            if not alliance_id:
                await interaction.followup.send(
                    "ℹ️ **No Alliance Assigned**\n\n"
                    "This server doesn't have an assigned alliance.",
                    ephemeral=True
                )
                return
            
            # Check current monitoring status
            with get_db_connection('settings.sqlite') as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT channel_id, enabled
                    FROM alliance_monitoring
                    WHERE guild_id = ? AND alliance_id = ?
                """, (interaction.guild_id, alliance_id))
                
                result = cursor.fetchone()
            
            if not result:
                await interaction.followup.send(
                    "ℹ️ **Monitoring Not Configured**\n\n"
                    "Please use **Set Log Channel** first to configure monitoring.",
                    ephemeral=True
                )
                return
            
            channel_id, is_enabled = result
            
            # Get alliance name
            alliance_name = "Unknown Alliance"
            try:
                with get_db_connection('alliance.sqlite') as alliance_db:
                    cursor = alliance_db.cursor()
                    cursor.execute("SELECT name FROM alliance_list WHERE alliance_id = ?", (alliance_id,))
                    result = cursor.fetchone()
                    if result:
                        alliance_name = result[0]
            except Exception:
                pass
            
            # Toggle monitoring status
            new_status = 0 if is_enabled else 1
            
            try:
                with get_db_connection('settings.sqlite') as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        UPDATE alliance_monitoring 
                        SET enabled = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE guild_id = ? AND alliance_id = ?
                    """, (new_status, interaction.guild_id, alliance_id))
                    conn.commit()

                if mongo_enabled():
                    AllianceMonitoringAdapter.upsert_monitor(interaction.guild_id, alliance_id, channel_id, enabled=new_status)
                    # Auto-unlock the monitor lock whenever the user re-enables monitoring
                    if new_status == 1:
                        try:
                            ServerLimitsAdapter.set(interaction.guild_id, {
                                'alliance_monitor_locked': False,
                                'updated_by': 0
                            })
                        except Exception:
                            pass
                
                # Create appropriate embed based on new status
                if new_status == 1:
                    # Get member count
                    members = await self.cog._get_monitoring_members(alliance_id)
                    member_count = len(members) if members else 0
                    
                    success_embed = discord.Embed(
                        title="✅ Alliance Monitoring Enabled",
                        description=(
                            f"**Alliance:** {alliance_name}\n"
                            f"**Alliance ID:** {alliance_id}\n"
                            f"**Log Channel:** <#{channel_id}>\n"
                            f"**Members Tracked:** {member_count}\n\n"
                            f"**Monitoring Active** ✅\n"
                            f"The system will check for changes every 4 minutes.\n\n"
                            f"**Tracked Changes:**\n"
                            f"• 👤 Name changes\n"
                            f"• 🔥 Furnace level changes\n"
                            f"• 🖼️ Avatar changes"
                        ),
                        color=discord.Color.green()
                    )
                    action_msg = "enabled"
                else:
                    success_embed = discord.Embed(
                        title="🛑 Alliance Monitoring Stopped",
                        description=(
                            f"**Alliance:** {alliance_name}\n"
                            f"**Alliance ID:** {alliance_id}\n\n"
                            f"Monitoring has been disabled.\n"
                            f"Member history has been preserved.\n\n"
                            f"Click **Enable Monitoring** to re-enable monitoring."
                        ),
                        color=discord.Color.red()
                    )
                    action_msg = "stopped"
                
                self.cog._set_embed_footer(success_embed)
                
                # Update the button in the view
                if new_status == 1:
                    button.label = "Stop Monitoring"
                    button.emoji = "🛑"
                    button.style = discord.ButtonStyle.danger
                else:
                    button.label = "Enable Monitoring"
                    button.emoji = "✅"
                    button.style = discord.ButtonStyle.success
                
                # Send the response with updated view
                await interaction.followup.send(embed=success_embed, ephemeral=True)
                
                # Update the original message with the new button state
                try:
                    # Get the original message from the interaction
                    original_message = await interaction.original_response()
                    if original_message:
                        # Update the view with new button state
                        await original_message.edit(view=self)
                except:
                    pass
                
                self.cog.log_message(f"Monitoring {action_msg} for alliance {alliance_id} ({alliance_name})")
                
            except Exception as e:
                self.cog.log_message(f"Error toggling monitoring: {e}")
                await interaction.followup.send(
                    "❌ An error occurred while toggling monitoring.",
                    ephemeral=True
                )
            
        except Exception as e:
            self.cog.log_message(f"Error in toggle_monitoring_button: {e}")
            await interaction.followup.send(
                "❌ An error occurred.",
                ephemeral=True
            )
    
    @discord.ui.button(
        label="Back",
        emoji="◀️",
        style=discord.ButtonStyle.secondary,
        custom_id="alliance_monitor_back",
        row=2
    )
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Return to previous menu"""
        try:
            await interaction.response.edit_message(
                content="✅ Closed Alliance Monitor",
                embed=None,
                view=None
            )
        except Exception as e:
            self.cog.log_message(f"Error in back_button: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ An error occurred.",
                    ephemeral=True
                )

class AllianceModal(discord.ui.Modal):
    def __init__(self, title: str, default_name: str = "", default_interval: str = "0"):
        super().__init__(title=title)
        
        self.name = discord.ui.TextInput(
            label="Alliance Name",
            placeholder="Enter alliance name",
            default=default_name,
            required=True
        )
        self.add_item(self.name)
        
        self.interval = discord.ui.TextInput(
            label="Control Interval (minutes)",
            placeholder="Enter interval (0 to disable)",
            default=default_interval,
            required=True
        )
        self.add_item(self.interval)

    async def on_submit(self, interaction: discord.Interaction):
        self.interaction = interaction

class AllianceView(discord.ui.View):
    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    @discord.ui.button(
        label="Main Menu",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="main_menu"
    )
    async def main_menu_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.show_main_menu(interaction)

class MemberOperationsView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    async def get_admin_alliances(self, user_id, guild_id):
        self.cog.c_settings.execute("SELECT id, is_initial FROM admin WHERE id = ?", (user_id,))
        admin = self.cog.c_settings.fetchone()
        
        if admin is None:
            return []
            
        is_initial = admin[1]
        
        if is_initial == 1:
            self.cog.c.execute("SELECT alliance_id, name FROM alliance_list ORDER BY name")
        else:
            self.cog.c.execute("""
                SELECT alliance_id, name 
                FROM alliance_list 
                WHERE discord_server_id = ? 
                ORDER BY name
            """, (guild_id,))
            
        return self.cog.c.fetchall()

    @discord.ui.button(label="Add Member", emoji="➕", style=discord.ButtonStyle.primary, custom_id="add_member")
    async def add_member_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            alliances = await self.get_admin_alliances(interaction.user.id, interaction.guild.id)
            if not alliances:
                await interaction.response.send_message("İttifak üyesi ekleme yetkiniz yok.", ephemeral=True)
                return

            options = [
                discord.SelectOption(
                    label=f"{name}",
                    value=str(alliance_id),
                    description=f"İttifak ID: {alliance_id}"
                ) for alliance_id, name in alliances
            ]

            select = discord.ui.Select(
                placeholder="Bir ittifak seçin",
                options=options,
                custom_id="alliance_select"
            )

            view = discord.ui.View()
            view.add_item(select)

            await interaction.response.send_message(
                "Üye eklemek istediğiniz ittifakı seçin:",
                view=view,
                ephemeral=True
            )

        except Exception as e:
            print(f"Error in add_member_button: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "An error occurred during the process of adding a member.",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    "An error occurred during the process of adding a member.",
                    ephemeral=True
                )

    @discord.ui.button(label="Remove Member", emoji="➖", style=discord.ButtonStyle.danger, custom_id="remove_member")
    async def remove_member_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            alliances = await self.get_admin_alliances(interaction.user.id, interaction.guild.id)
            if not alliances:
                await interaction.response.send_message("You are not authorized to delete alliance members.", ephemeral=True)
                return

            options = [
                discord.SelectOption(
                    label=f"{name}",
                    value=str(alliance_id),
                    description=f"Alliance ID: {alliance_id}"
                ) for alliance_id, name in alliances
            ]

            select = discord.ui.Select(
                placeholder="Choose an alliance",
                options=options,
                custom_id="alliance_select_remove"
            )

            view = discord.ui.View()
            view.add_item(select)

            await interaction.response.send_message(
                "Select the alliance you want to delete members from:",
                view=view,
                ephemeral=True
            )

        except Exception as e:
            print(f"Error in remove_member_button: {e}")
            await interaction.response.send_message(
                "An error occurred during the member deletion process.",
                ephemeral=True
            )

    @discord.ui.button(label="View Members", emoji="👥", style=discord.ButtonStyle.primary, custom_id="view_members")
    async def view_members_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            alliances = await self.get_admin_alliances(interaction.user.id, interaction.guild.id)
            if not alliances:
                await interaction.response.send_message("You are not authorized to screen alliance members.", ephemeral=True)
                return

            options = [
                discord.SelectOption(
                    label=f"{name}",
                    value=str(alliance_id),
                    description=f"Alliance ID: {alliance_id}"
                ) for alliance_id, name in alliances
            ]

            select = discord.ui.Select(
                placeholder="Choose an alliance",
                options=options,
                custom_id="alliance_select_view"
            )

            view = discord.ui.View()
            view.add_item(select)

            await interaction.response.send_message(
                "Select the alliance whose members you want to view:",
                view=view,
                ephemeral=True
            )

        except Exception as e:
            print(f"Error in view_members_button: {e}")
            await interaction.response.send_message(
                "An error occurred while viewing the member list.",
                ephemeral=True
            )

    @discord.ui.button(label="Main Menu", emoji="🏠", style=discord.ButtonStyle.secondary, custom_id="main_menu")
    async def main_menu_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await self.cog.show_main_menu(interaction)
        except Exception as e:
            print(f"Error in main_menu_button: {e}")
            await interaction.response.send_message(
                "An error occurred during return to the main menu.",
                ephemeral=True
            )

class PaginatedDeleteView(discord.ui.View):
    def __init__(self, pages, original_callback):
        super().__init__(timeout=7200)
        self.current_page = 0
        self.pages = pages
        self.original_callback = original_callback
        self.total_pages = len(pages)
        self.update_view()

    def update_view(self):
        self.clear_items()
        
        select = discord.ui.Select(
            placeholder=f"Select alliance to delete ({self.current_page + 1}/{self.total_pages})",
            options=self.pages[self.current_page]
        )
        select.callback = self.original_callback
        self.add_item(select)
        
        previous_button = discord.ui.Button(
            label="◀️",
            style=discord.ButtonStyle.grey,
            custom_id="previous",
            disabled=(self.current_page == 0)
        )
        previous_button.callback = self.previous_callback
        self.add_item(previous_button)

        next_button = discord.ui.Button(
            label="▶️",
            style=discord.ButtonStyle.grey,
            custom_id="next",
            disabled=(self.current_page == len(self.pages) - 1)
        )
        next_button.callback = self.next_callback
        self.add_item(next_button)

    async def previous_callback(self, interaction: discord.Interaction):
        self.current_page = (self.current_page - 1) % len(self.pages)
        self.update_view()
        
        embed = discord.Embed(
            title="🗑️ Delete Alliance",
            description=(
                "**⚠️ Warning: This action cannot be undone!**\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "1️⃣ Select an alliance from the dropdown menu\n"
                "2️⃣ Use ◀️ ▶️ buttons to navigate between pages\n\n"
                f"**Current Page:** {self.current_page + 1}/{self.total_pages}\n"
                f"**Total Alliances:** {sum(len(page) for page in self.pages)}\n"
                "━━━━━━━━━━━━━━━━━━━━━━"
            ),
            color=discord.Color.red()
        )
        embed.set_footer(text="⚠️ Warning: Deleting an alliance will remove all its data!")
        embed.timestamp = discord.utils.utcnow()
        
        await interaction.response.edit_message(embed=embed, view=self)

    async def next_callback(self, interaction: discord.Interaction):
        self.current_page = (self.current_page + 1) % len(self.pages)
        self.update_view()
        
        embed = discord.Embed(
            title="🗑️ Delete Alliance",
            description=(
                "**⚠️ Warning: This action cannot be undone!**\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "1️⃣ Select an alliance from the dropdown menu\n"
                "2️⃣ Use ◀️ ▶️ buttons to navigate between pages\n\n"
                f"**Current Page:** {self.current_page + 1}/{self.total_pages}\n"
                f"**Total Alliances:** {sum(len(page) for page in self.pages)}\n"
                "━━━━━━━━━━━━━━━━━━━━━━"
            ),
            color=discord.Color.red()
        )
        embed.set_footer(text="⚠️ Warning: Deleting an alliance will remove all its data!")
        embed.timestamp = discord.utils.utcnow()
        
        await interaction.response.edit_message(embed=embed, view=self)

class PaginatedChannelView(discord.ui.View):
    def __init__(self, channels, original_callback):
        super().__init__(timeout=7200)
        self.current_page = 0
        self.channels = channels
        self.original_callback = original_callback
        self.items_per_page = 25
        self.pages = [channels[i:i + self.items_per_page] for i in range(0, len(channels), self.items_per_page)]
        self.total_pages = len(self.pages)
        self.update_view()

    def update_view(self):
        self.clear_items()
        
        current_channels = self.pages[self.current_page]
        channel_options = [
            discord.SelectOption(
                label=f"#{channel.name}"[:100],
                value=str(channel.id),
                description=f"Channel ID: {channel.id}" if len(f"#{channel.name}") > 40 else None,
                emoji="📢"
            ) for channel in current_channels
        ]
        
        select = discord.ui.Select(
            placeholder=f"Select channel ({self.current_page + 1}/{self.total_pages})",
            options=channel_options
        )
        select.callback = self.original_callback
        self.add_item(select)
        
        if self.total_pages > 1:
            previous_button = discord.ui.Button(
                label="◀️",
                style=discord.ButtonStyle.grey,
                custom_id="previous",
                disabled=(self.current_page == 0)
            )
            previous_button.callback = self.previous_callback
            self.add_item(previous_button)

            next_button = discord.ui.Button(
                label="▶️",
                style=discord.ButtonStyle.grey,
                custom_id="next",
                disabled=(self.current_page == len(self.pages) - 1)
            )
            next_button.callback = self.next_callback
            self.add_item(next_button)

    async def previous_callback(self, interaction: discord.Interaction):
        self.current_page = (self.current_page - 1) % len(self.pages)
        self.update_view()
        
        embed = interaction.message.embeds[0]
        embed.description = (
            f"**Page:** {self.current_page + 1}/{self.total_pages}\n"
            f"**Total Channels:** {len(self.channels)}\n\n"
            "Please select a channel from the menu below."
        )
        
        await interaction.response.edit_message(embed=embed, view=self)

    async def next_callback(self, interaction: discord.Interaction):
        self.current_page = (self.current_page + 1) % len(self.pages)
        self.update_view()
        
        embed = interaction.message.embeds[0]
        embed.description = (
            f"**Page:** {self.current_page + 1}/{self.total_pages}\n"
            f"**Total Channels:** {len(self.channels)}\n\n"
            "Please select a channel from the menu below."
        )
        
        await interaction.response.edit_message(embed=embed, view=self)


    async def show_control_panel(self, interaction: discord.Interaction):
        """Show the paginated control panel overview."""
        try:
            total_servers = len(self.bot.guilds)
            total_members = sum(g.member_count or 0 for g in self.bot.guilds)
            
            # Get all limits
            all_limits_data = ServerLimitsAdapter.get_all()
            limits_map = {str(l.get('guild_id', '')): l for l in all_limits_data}
            
            servers_with_limits = sum(1 for l in all_limits_data if l.get('max_auto_redeem_members', -1) != -1)
            servers_with_lock = sum(1 for l in all_limits_data if l.get('alliance_monitor_locked', False))
            
            # Sort all guilds by name
            all_guilds = sorted(list(self.bot.guilds), key=lambda g: g.name.lower())
            
            # Create pages (20 per page)
            items_per_page = 20
            total_pages = (len(all_guilds) - 1) // items_per_page + 1
            embeds = []
            
            for page_num in range(total_pages):
                start_idx = page_num * items_per_page
                end_idx = min(start_idx + items_per_page, len(all_guilds))
                page_guilds = all_guilds[start_idx:end_idx]
                
                configured_list = ""
                for guild in page_guilds:
                    gid = str(guild.id)
                    lim = limits_map.get(gid, {})
                    
                    max_m = lim.get('max_auto_redeem_members', -1)
                    mon_locked = lim.get('alliance_monitor_locked', False)
                    
                    limit_str = "∞" if max_m == -1 else str(max_m)
                    lock_str = "🔒" if mon_locked else "🔓"
                    configured_list += f"• {guild.name[:30]} — Redeem: `{limit_str}` {lock_str}\n"
                
                panel_embed = discord.Embed(
                    title="🛡️ Control Panel — Overview",
                    description=(
                        "```ansi\n"
                        "\u001b[2;33m╔═══════════════════════════════════╗\n"
                        "\u001b[2;33m║  \u001b[1;37mSCALING CONTROL CENTER\u001b[0m\u001b[2;33m         ║\n"
                        "\u001b[2;33m╚═══════════════════════════════════╝\u001b[0m\n"
                        "```\n"
                        f"📊 **Bot Statistics**\n"
                        f"   ▸ Total Servers: `{total_servers}`\n"
                        f"   ▸ Total Users: `{total_members:,}`\n\n"
                        f"⚡ **Limits Overview**\n"
                        f"   ▸ Servers with Redeem Limits: `{servers_with_limits}`\n"
                        f"   ▸ Servers with Monitor Locked: `{servers_with_lock}`\n\n"
                        f"📋 **Server List (Page {page_num + 1}/{total_pages})**\n{configured_list}\n\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                    ),
                    color=0xF59E0B
                )
                panel_embed.set_footer(text=f"Total Servers: {total_servers} | Page {page_num+1}/{total_pages}")
                embeds.append(panel_embed)
            
            view = ControlPanelView(embeds, self, interaction.user.id, all_limits_data, limits_map)
            
            if interaction.response.is_done():
                await interaction.followup.send(embed=embeds[0], view=view, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embeds[0], view=view, ephemeral=True)
                
        except Exception as e:
            print(f"Control panel error: {e}")
            import traceback
            traceback.print_exc()
            if not interaction.response.is_done():
                await interaction.response.send_message(f"❌ Error loading Control Panel: {e}", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ Error loading Control Panel: {e}", ephemeral=True)

class ControlPanelView(ResultsPaginationView):
    """Custom pagination view for the Control Panel with global actions."""
    def __init__(self, embeds, cog, author_id, all_limits_data, limits_map):
        super().__init__(embeds, author_id)
        self.cog = cog
        self.all_limits_data = all_limits_data
        self.limits_map = limits_map
        
        # Add Global Action Buttons
        self.add_admin_buttons()

    def add_admin_buttons(self):
        # Row 1: Unlock All and Set Global
        unlock_all_btn = discord.ui.Button(
            label="Unlock All Monitors", emoji="🔓",
            style=discord.ButtonStyle.success,
            row=1
        )
        unlock_all_btn.callback = self.unlock_all_callback
        self.add_item(unlock_all_btn)
        
        set_all_btn = discord.ui.Button(
            label="Set Global Limit", emoji="📊",
            style=discord.ButtonStyle.primary,
            row=1
        )
        set_all_btn.callback = self.set_all_callback
        self.add_item(set_all_btn)
        
        # Row 2: Reset All and Back to Menu
        reset_all_btn = discord.ui.Button(
            label="Reset All Limits", emoji="🗑️",
            style=discord.ButtonStyle.danger,
            row=2
        )
        reset_all_btn.callback = self.reset_all_callback
        self.add_item(reset_all_btn)
        
        menu_btn = discord.ui.Button(
            label="Main Menu", emoji="🏠",
            style=discord.ButtonStyle.secondary,
            row=2
        )
        menu_btn.callback = lambda i: self.cog.show_main_menu(i)
        self.add_item(menu_btn)

    async def unlock_all_callback(self, interaction: discord.Interaction):
        unlocked = 0
        for lim in self.all_limits_data:
            if lim.get('alliance_monitor_locked', False):
                gid = lim.get('guild_id', '')
                ServerLimitsAdapter.set(int(gid), {
                    'max_auto_redeem_members': lim.get('max_auto_redeem_members', -1),
                    'alliance_monitor_locked': False,
                    'updated_by': interaction.user.id
                })
                unlocked += 1
        await interaction.response.send_message(
            f"✅ Unlocked alliance monitors for **{unlocked}** server(s).",
            ephemeral=True
        )

    async def set_all_callback(self, interaction: discord.Interaction):
        class GlobalLimitModal(discord.ui.Modal, title="Set Default Limit for ALL Servers"):
            limit_input = discord.ui.TextInput(
                label="Max Auto-Redeem Members (-1 = unlimited)",
                placeholder="e.g. 50, 100, -1",
                required=True,
                max_length=10
            )
            
            def __init__(self, view_self):
                super().__init__()
                self.view_self = view_self
            
            async def on_submit(self, modal_interaction: discord.Interaction):
                try:
                    new_limit = int(self.limit_input.value.strip())
                    if new_limit < -1: new_limit = -1
                    
                    applied = 0
                    for guild in self.view_self.cog.bot.guilds:
                        existing = self.view_self.limits_map.get(str(guild.id), {})
                        ServerLimitsAdapter.set(guild.id, {
                            'max_auto_redeem_members': new_limit,
                            'alliance_monitor_locked': existing.get('alliance_monitor_locked', False),
                            'updated_by': modal_interaction.user.id
                        })
                        applied += 1
                    
                    limit_text = "♾️ Unlimited" if new_limit == -1 else f"**{new_limit}**"
                    await modal_interaction.response.send_message(
                        f"✅ Set auto-redeem limit to {limit_text} for **{applied}** server(s).",
                        ephemeral=True
                    )
                except ValueError:
                    await modal_interaction.response.send_message("❌ Invalid number.", ephemeral=True)
        
        await interaction.response.send_modal(GlobalLimitModal(self))

    async def reset_all_callback(self, interaction: discord.Interaction):
        class ConfirmResetModal(discord.ui.Modal, title="CONFIRM RESET ALL LIMITS"):
            confirm = discord.ui.TextInput(
                label='Type "RESET ALL" to confirm',
                placeholder="RESET ALL",
                required=True
            )
            
            async def on_submit(self, modal_interaction: discord.Interaction):
                if self.confirm.value.strip().upper() == "RESET ALL":
                    try:
                        deleted = ServerLimitsAdapter.delete_all()
                    except AttributeError:
                        all_limits = ServerLimitsAdapter.get_all()
                        deleted = 0
                        for lim in all_limits:
                            gid = lim.get('guild_id')
                            if gid:
                                ServerLimitsAdapter.delete(int(gid))
                                deleted += 1
                    await modal_interaction.response.send_message(
                        f"✅ Successfully cleared custom limits for **{deleted}** server(s). All servers returned to default settings.",
                        ephemeral=True
                    )
                else:
                    await modal_interaction.response.send_message("❌ Confirmation failed. No changes made.", ephemeral=True)
        
        await interaction.response.send_modal(ConfirmResetModal())

async def setup(bot):
    try:
        # Prefer using a shared connection created in main.py (attached to bot)
        conn = None
        if hasattr(bot, "_connections") and isinstance(bot._connections, dict):
            conn = bot._connections.get("conn_alliance")

        if conn is None:
            # Fallback: ensure the repository `db` folder exists and open local DB
            from pathlib import Path

            repo_root = Path(__file__).resolve().parents[1]
            db_dir = repo_root / "db"
            try:
                db_dir.mkdir(parents=True, exist_ok=True)
            except Exception as mkdir_exc:
                pass

            db_path = db_dir / "alliance.sqlite"
            conn = sqlite3.connect(str(db_path))

        cog = Alliance(bot, conn)
        await bot.add_cog(cog)
        print(f"✓ Alliance cog loaded successfully")
    except Exception as e:
        print(f"✗ Failed to setup Alliance cog: {e}")
        raise
