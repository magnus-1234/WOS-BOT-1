# Force sync timestamp: 2026-03-06 18:15:00
import discord
from discord.ext import commands, tasks
from discord import app_commands
import sqlite3
import asyncio
import aiohttp
import json
import ssl
import random
import re
import io
from datetime import datetime
import time
import logging
import giftcode_poster

try:
    from db.mongo_adapters import mongo_enabled, GiftCodesAdapter, AutoRedeemSettingsAdapter, AutoRedeemChannelsAdapter, GiftCodeRedemptionAdapter, AutoRedeemMembersAdapter, AutoRedeemedCodesAdapter, _get_db
except Exception:
    mongo_enabled = lambda: False
    GiftCodesAdapter = None
    AutoRedeemSettingsAdapter = None
    AutoRedeemChannelsAdapter = None
    AutoRedeemMembersAdapter = None
    AutoRedeemedCodesAdapter = None
    _get_db = lambda: None
    
    # Fallback stub for GiftCodeRedemptionAdapter
    class GiftCodeRedemptionAdapter:
        @staticmethod
        def track_redemption(guild_id, code, fid, status):
            return False

try:
    from db_utils import get_db_connection
    from admin_utils import is_admin, is_global_admin, is_bot_owner
except ImportError:
    from pathlib import Path
    
    def get_db_connection(db_name: str, **kwargs):
        db_path = Path(__file__).parent.parent / 'db' / db_name
        return sqlite3.connect(str(db_path), **kwargs)
    
    def is_global_admin(user_id):
        return False
    
    def is_admin(user_id):
        return False
    
    async def is_bot_owner(bot, user_id):
        return False


class APISessionPool:
    """Manages multiple API sessions with independent rate limiting"""
    
    def __init__(self, session_count=2, base_delay=2.0, rate_limit_backoff=5.0, max_backoff=60.0):
        self.session_count = session_count
        self.base_delay = base_delay
        self.rate_limit_backoff = rate_limit_backoff
        self.max_backoff = max_backoff
        
        # Track state per session
        self.last_used = [0.0] * session_count
        self.rate_limited_until = [0.0] * session_count
        self.backoff_time = [rate_limit_backoff] * session_count
        self.lock = asyncio.Lock()
    
    async def get_available_session(self):
        """Get the best available session ID (least recently used and not rate-limited)"""
        async with self.lock:
            now = datetime.now().timestamp()
            
            # Find sessions that are not rate-limited
            available = []
            for i in range(self.session_count):
                if now >= self.rate_limited_until[i]:
                    # Reset backoff if rate limit has expired
                    if self.rate_limited_until[i] > 0:
                        self.backoff_time[i] = self.rate_limit_backoff
                        self.rate_limited_until[i] = 0.0
                    available.append(i)
            
            if not available:
                # All sessions are rate-limited, wait for the soonest one
                min_wait_idx = min(range(self.session_count), key=lambda i: self.rate_limited_until[i])
                wait_time = max(0, self.rate_limited_until[min_wait_idx] - now)
                if wait_time > 0:
                    await asyncio.sleep(wait_time)
                self.backoff_time[min_wait_idx] = self.rate_limit_backoff
                self.rate_limited_until[min_wait_idx] = 0.0
                return min_wait_idx
            
            # Return least recently used available session
            session_id = min(available, key=lambda i: self.last_used[i])
            
            # Enforce base delay
            time_since_last = now - self.last_used[session_id]
            if time_since_last < self.base_delay:
                await asyncio.sleep(self.base_delay - time_since_last)
            
            self.last_used[session_id] = datetime.now().timestamp()
            return session_id
    
    async def mark_rate_limited(self, session_id):
        """Mark a session as rate-limited with exponential backoff"""
        async with self.lock:
            now = datetime.now().timestamp()
            self.rate_limited_until[session_id] = now + self.backoff_time[session_id]
            # Exponential backoff, but cap at max_backoff
            self.backoff_time[session_id] = min(self.backoff_time[session_id] * 2, self.max_backoff)
    
    def is_any_available(self):
        """Check if any session is currently available"""
        now = datetime.now().timestamp()
        return any(now >= self.rate_limited_until[i] for i in range(self.session_count))



class ManageGiftCode(commands.Cog):
    """Gift Code Management for /manage command with API integration"""
    
    def __init__(self, bot):
        self.bot = bot
        
        # Logger (must be initialized before CAPTCHA solver and schema migration)
        self.logger = logging.getLogger('manage_giftcode')
        self.logger.setLevel(logging.INFO)
        
        self.giftcode_db = sqlite3.connect('db/giftcode.sqlite', check_same_thread=False)
        self.cursor = self.giftcode_db.cursor()
        self.settings_db = sqlite3.connect('db/settings.sqlite', check_same_thread=False)
        self.settings_cursor = self.settings_db.cursor()
        
        # Initialize database tables and columns
        self.setup_database()
        
        # API Configuration
        self.api_url = "http://gift-code-api.whiteout-bot.com/giftcode_api.php"
        self.api_key = "super_secret_bot_token_nobody_will_ever_find"
        
        # Rate limiting
        self.last_api_call = 0
        self.min_api_call_interval = 3
        
        # SSL context
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE

        # Ensure schema is up to date (Migration for auto_redeem_processed)
        try:
            self.cursor.execute("PRAGMA table_info(gift_codes)")
            columns = [col[1] for col in self.cursor.fetchall()]
            if 'auto_redeem_processed' not in columns:
                self.logger.info("Schema migration: Adding 'auto_redeem_processed' column to gift_codes table...")
                self.cursor.execute("ALTER TABLE gift_codes ADD COLUMN auto_redeem_processed INTEGER DEFAULT 0")
                self.giftcode_db.commit()
                self.logger.info("Schema migration successful.")
        except Exception as e:
            self.logger.error(f"Error checking/migrating schema: {e}")
        
        # WOS API URLs and Key for gift code redemption
        self.wos_player_info_url = "https://wos-giftcode-api.centurygame.com/api/player"
        self.wos_giftcode_url = "https://wos-giftcode-api.centurygame.com/api/gift_code"
        self.wos_captcha_url = "https://wos-giftcode-api.centurygame.com/api/captcha"
        self.wos_encrypt_key = "tB87#kPtkxqOS2"
        
        # Initialize CAPTCHA solver
        try:
            from .gift_captchasolver import GiftCaptchaSolver, ONNX_AVAILABLE
            if ONNX_AVAILABLE:
                self.captcha_solver = GiftCaptchaSolver(save_images=0)
                if self.captcha_solver.is_initialized:
                    self.logger.info("CAPTCHA solver initialized successfully")
                else:
                    self.logger.warning("CAPTCHA solver failed to initialize - model files may be missing")
                    self.captcha_solver = None
            else:
                self.logger.warning("ONNX not available - CAPTCHA solving disabled")
                self.captcha_solver = None
        except Exception as e:
            self.logger.exception(f"Error initializing CAPTCHA solver: {e}")
            self.captcha_solver = None
        
        # Initialize API session pool for concurrent processing
        self.session_pool = APISessionPool(
            session_count=2,           # 2 independent API sessions
            base_delay=3.0,            # 3 seconds between requests per session (more conservative)
            rate_limit_backoff=10.0,   # Initial backoff when rate limited (longer wait)
            max_backoff=60.0           # Maximum backoff duration
        )
        
        # Concurrent processing configuration
        self.concurrent_redemptions = 2  # Process 2 members simultaneously
        
        # Auto-redeem lock to prevent duplicate processing
        self._active_redemptions = set()  # Track active (guild_id, code) pairs
        self._redemption_lock = asyncio.Lock()
        
        # Stop signals for auto-redeem
        self.stop_signals = {}  # {guild_id: boolean}
        
        # Global queue for staggering auto-redeem across servers limit memory spikes
        self.auto_redeem_queue = asyncio.Queue()
        self.current_job = None  # Track what's currently processing: (guild_id, code)
        self.processed_jobs_count = 0
        self._pending_jobs_per_code = {} # {code: count_of_guilds_left}
        self._completion_events = {}     # {code: asyncio.Event}
        
        self.session = None

    def _set_embed_footer(self, embed: discord.Embed, guild: discord.Guild = None):
        """Sets the standardized footer for embeds with original branding."""
        server_name = guild.name if guild else "ICE"
        embed.set_footer(
            text=f"Whiteout Survival || {server_name} ❄️",
            icon_url="https://cdn.discordapp.com/attachments/1435569370389807144/1436745053442805830/unnamed_5.png"
        )

    def _create_error_embed(self, title, description, color=discord.Color.red(), guild=None):
        """Helper to create error embeds with standardized branding."""
        embed = discord.Embed(title=title, description=description, color=color)
        self._set_embed_footer(embed, guild)
        return embed

    async def cog_load(self):
        """Called when the cog is loaded. Initializes background tasks and sessions."""
        self.session = aiohttp.ClientSession()
        self.logger.info("ManageGiftCode: Shared aiohttp session initialized.")

        # NOTE: _sync_mongo_to_sqlite_on_startup calls bot.wait_until_ready() internally.
        # Awaiting it directly here would deadlock setup_hook (which runs before on_ready).
        # Run it as a background task so setup_hook can finish and the bot can connect.
        asyncio.create_task(self._sync_mongo_to_sqlite_on_startup())

        # Start background workers
        self.worker_task = asyncio.create_task(self._auto_redeem_worker_loop())
        self.api_check_task.start()

        # Trigger startup check for existing codes
        asyncio.create_task(self.process_existing_codes_on_startup())
        self.logger.info("ManageGiftCode: Shared aiohttp session initialized.")

        # NOTE: _sync_mongo_to_sqlite_on_startup calls bot.wait_until_ready() internally.
        # Awaiting it directly here would deadlock setup_hook (which runs before on_ready).
        # Run it as a background task so setup_hook can finish and the bot can connect.
        asyncio.create_task(self._sync_mongo_to_sqlite_on_startup())

        # Start background workers
        self.worker_task = asyncio.create_task(self._auto_redeem_worker_loop())
        self.api_check_task.start()

        # Trigger startup check for existing codes
        asyncio.create_task(self.process_existing_codes_on_startup())

    async def cog_unload(self):
        """Called when the cog is unloaded. Performs cleanup."""
        # Stop background workers
        if hasattr(self, 'worker_task') and self.worker_task:
            self.worker_task.cancel()
        self.api_check_task.cancel()
        
        # Close sessions
        if self.session:
            await self.session.close()
            self.logger.info("ManageGiftCode: Shared aiohttp session closed.")
        
        # Close database connections
        try:
            if hasattr(self, 'giftcode_db') and self.giftcode_db:
                self.giftcode_db.close()
            if hasattr(self, 'settings_db') and self.settings_db:
                self.settings_db.close()
            self.logger.info("ManageGiftCode: Database connections closed.")
        except Exception as e:
            self.logger.error(f"Error closing databases in cog_unload: {e}")
        
    async def _auto_redeem_worker_loop(self):
        """Background worker that processes auto-redeems sequentially to prevent global memory spikes."""
        await self.bot.wait_until_ready()
        self.logger.info("👷 Auto-redeem queue worker started")
        
        while True:
            try:
                # Wait for a job
                job = await self.auto_redeem_queue.get()
                guild_id, code, is_recheck = job
                self.current_job = (guild_id, code)
                
                self.logger.info(f"⚙️ [WORKER] Processing queued auto-redeem: Guild {guild_id} | Code {code} (Recheck: {is_recheck})")
                
                try:
                    # Await the actual process (runs sequentially for this worker)
                    await self.process_auto_redeem(guild_id, code, is_recheck=is_recheck)
                except Exception as e:
                    self.logger.error(f"❌ [WORKER] Error processing guild {guild_id} for code {code}: {e}")
                finally:
                    self.current_job = None
                    self.processed_jobs_count += 1
                    # Mark this task as done and update pending count for faster status reporting
                    self.auto_redeem_queue.task_done()
                    await self._decrement_pending_jobs(code)
                    
                    # Small delay between guilds to let memory/network settle
                    await asyncio.sleep(2.0)
                    
            except asyncio.CancelledError:
                self.logger.info("🛑 Auto-redeem worker stopped.")
                break
            except Exception as e:
                self.current_job = None
                self.logger.error(f"❌ [WORKER] Critical error in queue loop: {e}")
                await asyncio.sleep(5.0)

    async def _safe_edit_message(self, interaction: discord.Interaction, **kwargs):
        """Safely edit an interaction message, falling back to followup if already acknowledged."""
        try:
            await interaction.response.edit_message(**kwargs)
        except discord.errors.HTTPException as e:
            if e.code == 40060:  # Interaction already acknowledged
                try:
                    await interaction.followup.send(ephemeral=True, **kwargs)
                except Exception as fe:
                    self.logger.warning(f"Followup also failed after 40060: {fe}")
            else:
                raise

    async def _sync_mongo_to_sqlite_on_startup(self):
        """At startup, pull ALL member/settings data from MongoDB into SQLite for robustness."""
        await self.bot.wait_until_ready()
        if not mongo_enabled():
            return
        
        try:
            self.logger.info("🔄 [STARTUP] Syncing MongoDB auto-redeem data to SQLite...")
            db = _get_db()
            
            # --- Members (handle both V1 array-style and V2 flat docs) ---
            synced_members = 0
            skipped = 0
            try:
                all_member_docs = list(db['auto_redeem_members'].find({}))
                self.logger.info(f"📊 [STARTUP] Found {len(all_member_docs)} member docs in MongoDB")
                
                for doc in all_member_docs:
                    try:
                        guild_id = int(doc.get('guild_id', 0))
                        if not guild_id:
                            skipped += 1
                            continue
                        
                        fid = doc.get('fid')
                        
                        if fid and str(fid).strip() and str(fid).strip().lower() != 'none':
                            # V2 flat doc - direct member
                            fid_str = str(fid).strip()
                            raw_nick = doc.get('nickname', 'Unknown')
                            if isinstance(raw_nick, dict): raw_nick = raw_nick.get('nickname', 'Unknown')
                            nickname = str(raw_nick) if raw_nick else 'Unknown'
                            furnace_lv = int(doc.get('furnace_lv', 0) or 0)
                            avatar_image = doc.get('avatar_image', '') or ''
                            added_by = doc.get('added_by')
                            added_at = str(doc.get('added_at', '') or '')
                            self.cursor.execute("""
                                INSERT OR REPLACE INTO auto_redeem_members
                                (guild_id, fid, nickname, furnace_lv, avatar_image, added_by, added_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                            """, (guild_id, fid_str, nickname, furnace_lv, avatar_image, added_by, added_at))
                            synced_members += 1
                        
                        elif 'members' in doc and isinstance(doc['members'], list):
                            # V1 array-style doc - embedded members list
                            for member in doc['members']:
                                try:
                                    m_fid = member.get('fid')
                                    if not m_fid or str(m_fid).strip().lower() == 'none':
                                        continue
                                    m_fid_str = str(m_fid).strip()
                                    raw_nick = member.get('nickname', 'Unknown')
                                    if isinstance(raw_nick, dict): raw_nick = raw_nick.get('nickname', 'Unknown')
                                    m_nick = str(raw_nick) if raw_nick else 'Unknown'
                                    m_fl = int(member.get('furnace_lv', 0) or 0)
                                    m_avatar = member.get('avatar_image', '') or ''
                                    m_added_by = member.get('added_by')
                                    m_added_at = str(member.get('added_at', '') or '')
                                    self.cursor.execute("""
                                        INSERT OR REPLACE INTO auto_redeem_members
                                        (guild_id, fid, nickname, furnace_lv, avatar_image, added_by, added_at)
                                        VALUES (?, ?, ?, ?, ?, ?, ?)
                                    """, (guild_id, m_fid_str, m_nick, m_fl, m_avatar, m_added_by, m_added_at))
                                    synced_members += 1
                                except Exception as inner_e:
                                    self.logger.warning(f"Failed to sync V1 member: {inner_e}")
                        else:
                            skipped += 1
                    except Exception as me:
                        self.logger.warning(f"Failed to sync member doc to SQLite: {me}")
                
                self.giftcode_db.commit()
                self.logger.info(f"✅ [STARTUP] Synced {synced_members} member records from MongoDB to SQLite (skipped {skipped})")
            except Exception as e:
                self.logger.error(f"❌ [STARTUP] Member sync failed: {e}")

            # --- Settings ---
            synced_settings = 0
            try:
                if AutoRedeemSettingsAdapter:
                    all_settings = AutoRedeemSettingsAdapter.get_all_settings()
                    for s in (all_settings or []):
                        try:
                            guild_id = int(s.get('guild_id', 0))
                            enabled = 1 if s.get('enabled', False) else 0
                            updated_by = s.get('updated_by')
                            updated_at = str(s.get('updated_at', '') or '')
                            if not guild_id:
                                continue
                            self.cursor.execute("""
                                INSERT OR REPLACE INTO auto_redeem_settings
                                (guild_id, enabled, updated_by, updated_at)
                                VALUES (?, ?, ?, ?)
                            """, (guild_id, enabled, updated_by, updated_at))
                            synced_settings += 1
                        except Exception as se:
                            self.logger.warning(f"Failed to sync settings for guild {s.get('guild_id')}: {se}")
                    self.giftcode_db.commit()
                    self.logger.info(f"✅ [STARTUP] Synced {synced_settings} guild settings from MongoDB to SQLite")
            except Exception as e:
                self.logger.error(f"❌ [STARTUP] Settings sync failed: {e}")

        except Exception as e:
            self.logger.error(f"❌ [STARTUP] MongoDB→SQLite sync failed: {e}")


    @commands.command(name="test_auto_redeem")
    async def test_auto_redeem(self, ctx, code: str, fid: str = None):
        """Test the auto-redeem flow (Admin Only). Usage: !test_auto_redeem <code_to_test> [optional_fid]"""
        if not await self.check_admin_permission(ctx.author.id):
            await ctx.send("You do not have permission to use this command.")
            return

        if not fid:
            # If no FID is provided, run the full auto-redeem process for the guild
            await ctx.send(f"🧪 Starting full auto-redeem test for guild with code: `{code}`...")
            await self.process_auto_redeem(ctx.guild.id, code)
            return

        # Validate and clean FID input
        try:
            # Remove any whitespace and non-numeric characters
            clean_fid = ''.join(c for c in fid if c.isdigit())
            if not clean_fid or len(clean_fid) != 9:
                await ctx.send(f"❌ Invalid FID format. FID must be exactly 9 digits. You provided: `{fid}`")
                return
            target_fid = clean_fid
        except Exception as e:
            await ctx.send(f"❌ Error processing FID: `{fid}`. Please enter a valid 9-digit FID.")
            return

        # If FID is provided, proceed with single member test but with improved UI
        nickname = "Unknown"
        furnace_lv = 0
        
        # Test player fetch first (verify session validation)
        status_msg = await ctx.send(f"🧪 Testing redemption for FID: `{target_fid}`, Code: `{code}`...")
        
        try:
            data = await self.fetch_player_data(target_fid)
            if data:
                nickname = data['nickname']
                furnace_lv = data['furnace_lv']
                await status_msg.edit(content=f"✅ Verified player: **{nickname}** (Furnace Lv: {furnace_lv})\nAttempting redemption...")
            else:
                 await status_msg.edit(content=f"⚠️ Failed to fetch fresh player data for FID `{target_fid}`. Attempting redemption with default info...")

            # Show initial progress UI (ANSI styled like the real process)
            progress_bar = '█' * 0 + '░' * 20
            progress_embed = discord.Embed(
                title="🎁 Auto-Redeem In Progress (Test)",
                description=(
                    f"```ansi\n"
                    f"\u001b[2;36m━━━━━━━━━━━━━━━━━━━━━━\u001b[0m\n"
                    f"\u001b[1;37mGift Code: {code}\u001b[0m\n"
                    f"\u001b[2;36m━━━━━━━━━━━━━━━━━━━━━━\u001b[0m\n"
                    f"```\n"
                    f"**Progress:** `{progress_bar}` **0.0%**\n"
                    f"📊 **Processed:** 0/1\n\n"
                    f"✅ **Success:** 0\n"
                    f"ℹ️ **Already Redeemed:** 0\n"
                    f"❌ **Failed:** 0\n"
                    f"🏰 **Server:** {ctx.guild.name}\n"
                ),
                color=0x5865F2
            )
            ui_msg = await ctx.send(embed=progress_embed)

            # Attempt redemption
            status, success, already_redeemed, failed = await self._redeem_for_member(
                ctx.guild.id, target_fid, nickname, furnace_lv, code
            )
            
            # Update to final UI state
            final_bar = '█' * 20
            final_embed = discord.Embed(
                title="🎁 Auto-Redeem Complete (Test)",
                description=(
                    f"```ansi\n"
                    f"\u001b[2;36m━━━━━━━━━━━━━━━━━━━━━━\u001b[0m\n"
                    f"\u001b[1;37mGift Code: {code}\u001b[0m\n"
                    f"\u001b[2;36m━━━━━━━━━━━━━━━━━━━━━━\u001b[0m\n"
                    f"```\n"
                    f"**Progress:** `{final_bar}` **100.0%**\n"
                    f"📊 **Processed:** 1/1\n\n"
                    f"✅ **Success:** {success}\n"
                    f"ℹ️ **Already Redeemed:** {already_redeemed}\n"
                    f"❌ **Failed:** {failed}\n"
                    f"🏰 **Server:** {ctx.guild.name}\n"
                    f"\n**Result Status:** `{status}`"
                ),
                color=0x57F287 if success else 0xFEE75C
            )
            await ui_msg.edit(embed=final_embed)
            
            # Cleanup initial status message
            await status_msg.delete()

        except Exception as e:
            await ctx.send(f"❌ Error during test: {e}")
            self.logger.exception(f"Error in test_auto_redeem: {e}")

    
    def setup_database(self):
        """Initialize gift code database tables"""
        try:
            # Gift codes table
            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS gift_codes (
                    giftcode TEXT PRIMARY KEY,
                    date TEXT
                )
            """)
            
            # Add missing columns if they don't exist
            try:
                self.cursor.execute("ALTER TABLE gift_codes ADD COLUMN validation_status TEXT DEFAULT 'pending'")
                self.logger.info("Added validation_status column to gift_codes")
            except sqlite3.OperationalError:
                pass  # Column already exists
            
            try:
                self.cursor.execute("ALTER TABLE gift_codes ADD COLUMN added_by INTEGER")
                self.logger.info("Added added_by column to gift_codes")
            except sqlite3.OperationalError:
                pass  # Column already exists
            
            try:
                self.cursor.execute("ALTER TABLE gift_codes ADD COLUMN added_at TIMESTAMP")
                self.logger.info("Added added_at column to gift_codes")
            except sqlite3.OperationalError:
                pass  # Column already exists
            
            try:
                self.cursor.execute("ALTER TABLE gift_codes ADD COLUMN auto_redeem_processed INTEGER DEFAULT 0")
                self.logger.info("Added auto_redeem_processed column to gift_codes")
            except sqlite3.OperationalError:
                pass  # Column already exists

            # Gift code channels table
            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS giftcode_channels (
                    guild_id INTEGER PRIMARY KEY,
                    channel_id INTEGER NOT NULL,
                    auto_post INTEGER DEFAULT 1,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Auto-use settings table (legacy - keeping for compatibility)
            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS giftcode_autouse (
                    guild_id INTEGER,
                    alliance_id INTEGER,
                    enabled INTEGER DEFAULT 1,
                    PRIMARY KEY (guild_id, alliance_id)
                )
            """)
            
            # Auto redeem members table (SQLite fallback)
            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS auto_redeem_members (
                    guild_id INTEGER,
                    fid TEXT,
                    nickname TEXT,
                    furnace_lv INTEGER DEFAULT 0,
                    avatar_image TEXT,
                    added_by INTEGER,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (guild_id, fid)
                )
            """)
            
            # Migrate existing table - add missing columns if they don't exist
            try:
                self.cursor.execute("ALTER TABLE auto_redeem_members ADD COLUMN furnace_lv INTEGER DEFAULT 0")
                self.logger.info("Added furnace_lv column to auto_redeem_members")
            except sqlite3.OperationalError:
                pass  # Column already exists
            
            try:
                self.cursor.execute("ALTER TABLE auto_redeem_members ADD COLUMN avatar_image TEXT")
                self.logger.info("Added avatar_image column to auto_redeem_members")
            except sqlite3.OperationalError:
                pass  # Column already exists
            
            # Auto redeem channels table - for monitoring channels for FID codes
            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS auto_redeem_channels (
                    guild_id INTEGER PRIMARY KEY,
                    channel_id INTEGER NOT NULL,
                    added_by INTEGER NOT NULL,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Auto redeem settings table - for enable/disable auto redemption
            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS auto_redeem_settings (
                    guild_id INTEGER PRIMARY KEY,
                    enabled INTEGER DEFAULT 0,
                    updated_by INTEGER,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Auto redeem completed guilds table - track per-guild code processing to prevent restart loops
            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS auto_redeem_completed_guilds (
                    guild_id INTEGER,
                    giftcode TEXT,
                    completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (guild_id, giftcode)
                )
            """)
            
            self.giftcode_db.commit()
            self.logger.info("Gift code database tables initialized")
        except Exception as e:
            self.logger.error(f"Error setting up gift code database: {e}")
    
    # ===== Auto-Redeem Member Management Helper Class =====
    class AutoRedeemDB:
        """MongoDB/SQLite hybrid storage for auto-redeem members"""
        
        @staticmethod
        def get_members(cog_instance, guild_id):
            """Get all auto-redeem members for a guild (filters out invalid FIDs)
            Priority: MongoDB (if available and has data) > SQLite (fallback and sync source)
            """
            try:
                guild_id = int(guild_id)  # Ensure guild_id is int
                members = []
                from_source = "none"
                
                # Step 1: Try MongoDB first (if enabled)
                if mongo_enabled() and AutoRedeemMembersAdapter:
                    try:
                        members = AutoRedeemMembersAdapter.get_members(guild_id)
                        if members:
                            from_source = "MongoDB"
                            cog_instance.logger.debug(f"Fetched {len(members)} auto-redeem members from MongoDB for guild {guild_id}")
                    except Exception as e:
                        cog_instance.logger.warning(f"⚠️ MongoDB get_members failed for guild {guild_id}: {e}. Falling back to SQLite.")
                        members = []
                
                # Step 2: Fallback to SQLite if MongoDB is empty or failed
                if not members:
                    try:
                        cog_instance.cursor.execute("""
                            SELECT fid, nickname, furnace_lv, avatar_image, added_by, added_at
                            FROM auto_redeem_members
                            WHERE guild_id = ?
                            ORDER BY furnace_lv DESC
                        """, (guild_id,))
                        rows = cog_instance.cursor.fetchall()
                        members = [
                            {
                                'fid': row[0],
                                'nickname': row[1],
                                'furnace_lv': row[2] or 0,
                                'avatar_image': row[3] or '',
                                'added_by': row[4],
                                'added_at': row[5]
                            }
                            for row in rows
                        ]
                        from_source = "SQLite"
                        if rows:
                            cog_instance.logger.info(f"✅ Fetched {len(members)} auto-redeem members from SQLite for guild {guild_id}")
                        else:
                            cog_instance.logger.warning(f"⚠️ SQLite returned 0 members for guild {guild_id} — check auto_redeem_members table")
                        
                        # Step 3: Sync members from SQLite to MongoDB (if MongoDB is enabled)
                        # This ensures Oracle VM deployments with SQLite data get synced to MongoDB
                        if members and mongo_enabled() and AutoRedeemMembersAdapter:
                            try:
                                sync_count = 0
                                for member in members:
                                    try:
                                        # Check if member exists in MongoDB
                                        if not AutoRedeemMembersAdapter.member_exists(guild_id, member['fid']):
                                            success = AutoRedeemMembersAdapter.add_member(guild_id, member['fid'], member)
                                            if success:
                                                sync_count += 1
                                    except Exception as sync_err:
                                        cog_instance.logger.warning(f"Failed to sync member {member.get('fid')} to MongoDB: {sync_err}")
                                
                                if sync_count > 0:
                                    cog_instance.logger.info(f"✅ Synced {sync_count} members from SQLite to MongoDB for guild {guild_id}")
                            except Exception as sync_error:
                                cog_instance.logger.warning(f"MongoDB sync failed for guild {guild_id}: {sync_error}")
                    except Exception as sqlite_error:
                        cog_instance.logger.error(f"SQLite query failed for guild {guild_id}: {sqlite_error}")
                        return []
                
                # Step 4: Filter out members with null/empty/None FIDs
                valid_members = [
                    member for member in members
                    if member.get('fid') and str(member.get('fid', '')).strip() and str(member.get('fid', '')).lower() != 'none'
                ]
                
                # Log filtering results
                filtered_count = len(members) - len(valid_members)
                if filtered_count > 0:
                    cog_instance.logger.warning(f"Filtered out {filtered_count} members with invalid FIDs from {from_source} for guild {guild_id}")
                
                if not valid_members:
                    cog_instance.logger.debug(f"No valid auto-redeem members found for guild {guild_id} (source: {from_source})")
                
                return valid_members
            except Exception as e:
                cog_instance.logger.error(f"Unexpected error getting auto-redeem members for guild {guild_id}: {e}", exc_info=True)
                return []

        @staticmethod
        async def get_members_async(cog_instance, guild_id):
            """Get all auto-redeem members for a guild (filters out invalid FIDs) asynchronously
            Priority: MongoDB (if available and has data) > SQLite (fallback and sync source)
            """
            try:
                guild_id = int(guild_id)  # Ensure guild_id is int
                members = []
                from_source = "none"
                
                # Step 1: Try MongoDB first (if enabled)
                if mongo_enabled() and AutoRedeemMembersAdapter:
                    try:
                        members = await AutoRedeemMembersAdapter.get_members_async(guild_id)
                        if members:
                            from_source = "MongoDB"
                            cog_instance.logger.debug(f"Fetched {len(members)} auto-redeem members from MongoDB (async) for guild {guild_id}")
                    except Exception as e:
                        cog_instance.logger.warning(f"⚠️ MongoDB get_members_async failed for guild {guild_id}: {e}. Falling back to SQLite.")
                        members = []
                
                # Step 2: Fallback to SQLite if MongoDB is empty or failed
                if not members:
                    try:
                        def fetch_sqlite():
                            cog_instance.cursor.execute("""
                                SELECT fid, nickname, furnace_lv, avatar_image, added_by, added_at
                                FROM auto_redeem_members
                                WHERE guild_id = ?
                                ORDER BY furnace_lv DESC
                            """, (guild_id,))
                            return cog_instance.cursor.fetchall()
                        
                        rows = await asyncio.to_thread(fetch_sqlite)
                        members = [
                            {
                                'fid': row[0],
                                'nickname': row[1],
                                'furnace_lv': row[2] or 0,
                                'avatar_image': row[3] or '',
                                'added_by': row[4],
                                'added_at': row[5]
                            }
                            for row in rows
                        ]
                        from_source = "SQLite"
                        if rows:
                            cog_instance.logger.info(f"✅ Fetched {len(members)} auto-redeem members from SQLite (async) for guild {guild_id}")
                    except Exception as e:
                        cog_instance.logger.error(f"❌ SQLite get_members_async failed for guild {guild_id}: {e}")
                
                # Step 3: Filter out members with null/empty/None FIDs
                valid_members = [
                    member for member in members
                    if member.get('fid') and str(member.get('fid', '')).strip() and str(member.get('fid', '')).lower() != 'none'
                ]
                
                return valid_members
            except Exception as e:
                cog_instance.logger.error(f"Unexpected error getting auto-redeem members for guild {guild_id} (async): {e}", exc_info=True)
                return []
        
        @staticmethod
        async def cleanup_null_members_async(cog_instance, guild_id=None):
            """Remove all members with null/empty FIDs from database asynchronously"""
            try:
                removed_count = 0
                
                # Remove from SQLite in thread
                def delete_sqlite():
                    if guild_id:
                        cog_instance.cursor.execute("""
                            DELETE FROM auto_redeem_members 
                            WHERE guild_id = ? AND (fid IS NULL OR fid = '' OR fid = 'None')
                        """, (guild_id,))
                    else:
                        cog_instance.cursor.execute("""
                            DELETE FROM auto_redeem_members 
                            WHERE fid IS NULL OR fid = '' OR fid = 'None'
                        """)
                    count = cog_instance.cursor.rowcount
                    cog_instance.giftcode_db.commit()
                    return count
                
                removed_count = await asyncio.to_thread(delete_sqlite)
                
                # Remove from MongoDB (async)
                if mongo_enabled() and AutoRedeemMembersAdapter:
                    try:
                        from db.mongo_adapters import _get_db
                        db = _get_db()
                        if db:
                            # Use async delete if possible or thread it if using pymongo directly
                            # Since AutoRedeemMembersAdapter might have its own async delete_many, check it.
                            # For consistency with other methods, let's use the adapter if it has it.
                            
                            # If we use _get_db() directly, we should know if it's motor or pymongo.
                            # Usually we thread pymongo calls.
                            def delete_mongo():
                                if guild_id:
                                    result = db[AutoRedeemMembersAdapter.COLL].delete_many({
                                        'guild_id': int(guild_id),
                                        '$or': [{'fid': None}, {'fid': ''}, {'fid': 'None'}]
                                    })
                                else:
                                    result = db[AutoRedeemMembersAdapter.COLL].delete_many({
                                        '$or': [{'fid': None}, {'fid': ''}, {'fid': 'None'}]
                                    })
                                return result.deleted_count
                            
                            removed_count += await asyncio.to_thread(delete_mongo)
                    except Exception as e:
                        cog_instance.logger.error(f"Error cleaning up null members from MongoDB (async): {e}")
                
                if removed_count > 0:
                    cog_instance.logger.info(f"🧹 Cleaned up {removed_count} members with null/empty FIDs (async)")
                
                return removed_count
            except Exception as e:
                cog_instance.logger.error(f"Error cleaning up null members (async): {e}")
                return 0
        
        @staticmethod
        def add_member(cog_instance, guild_id, fid, member_data):
            """Add a member to auto-redeem list (writes to both MongoDB and SQLite for consistency)"""
            try:
                # Validate FID - reject null, empty, or 'None' values
                if not fid or not str(fid).strip() or str(fid).strip().lower() == 'none':
                    cog_instance.logger.warning(f"Rejected adding member with invalid FID: {fid}")
                    return False
                
                # Ensure fid is a clean string and guild_id is int
                fid = str(fid).strip()
                guild_id = int(guild_id)
                
                member_data['fid'] = fid
                member_data['added_at'] = datetime.now()
                
                # Primary write: SQLite (always, for fallback consistency)
                sqlite_success = False
                try:
                    cog_instance.cursor.execute("""
                        INSERT OR REPLACE INTO auto_redeem_members 
                        (guild_id, fid, nickname, furnace_lv, avatar_image, added_by, added_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (guild_id, fid, member_data.get('nickname', 'Unknown'),
                          int(member_data.get('furnace_lv', 0)), member_data.get('avatar_image', ''),
                          int(member_data.get('added_by', 0)), member_data['added_at']))
                    cog_instance.giftcode_db.commit()
                    sqlite_success = cog_instance.cursor.rowcount > 0
                    if sqlite_success:
                        cog_instance.logger.debug(f"✅ Added member {fid} to SQLite for guild {guild_id}")
                except Exception as sqlite_e:
                    cog_instance.logger.error(f"Failed to add member {fid} to SQLite: {sqlite_e}")
                    return False
                
                # Secondary write: MongoDB (if enabled)
                if mongo_enabled() and AutoRedeemMembersAdapter:
                    try:
                        mongo_success = AutoRedeemMembersAdapter.add_member(guild_id, fid, member_data)
                        if mongo_success:
                            cog_instance.logger.debug(f"✅ Added member {fid} to MongoDB for guild {guild_id}")
                        else:
                            cog_instance.logger.warning(f"MongoDB add_member returned False for {fid}, but SQLite succeeded")
                    except Exception as mongo_e:
                        cog_instance.logger.warning(f"Failed to add member {fid} to MongoDB (SQLite succeeded): {mongo_e}")
                
                return sqlite_success
            except Exception as e:
                cog_instance.logger.error(f"Unexpected error adding auto-redeem member {fid}: {e}", exc_info=True)
                return False

        @staticmethod
        async def add_member_async(cog_instance, guild_id, fid, member_data):
            """Add a member to auto-redeem list (writes to both MongoDB and SQLite for consistency) asynchronously"""
            try:
                # Validate FID
                if not fid or not str(fid).strip() or str(fid).strip().lower() == 'none':
                    cog_instance.logger.warning(f"Rejected adding member with invalid FID (async): {fid}")
                    return False
                
                # Ensure fid is a clean string and guild_id is int
                fid = str(fid).strip()
                guild_id = int(guild_id)
                
                member_data['fid'] = fid
                member_data['added_at'] = datetime.now()
                
                # Primary write: SQLite (run in thread to avoid blocking loop)
                sqlite_success = False
                try:
                    def write_sqlite():
                        cog_instance.cursor.execute("""
                            INSERT OR REPLACE INTO auto_redeem_members 
                            (guild_id, fid, nickname, furnace_lv, avatar_image, added_by, added_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        """, (guild_id, fid, member_data.get('nickname', 'Unknown'),
                              int(member_data.get('furnace_lv', 0)), member_data.get('avatar_image', ''),
                              int(member_data.get('added_by', 0)), member_data['added_at']))
                        cog_instance.giftcode_db.commit()
                        return cog_instance.cursor.rowcount > 0
                    
                    sqlite_success = await asyncio.to_thread(write_sqlite)
                    if sqlite_success:
                        cog_instance.logger.debug(f"✅ Added member {fid} to SQLite (async) for guild {guild_id}")
                except Exception as sqlite_e:
                    cog_instance.logger.error(f"Failed to add member {fid} to SQLite (async): {sqlite_e}")
                    return False
                
                # Secondary write: MongoDB (if enabled)
                if mongo_enabled() and AutoRedeemMembersAdapter:
                    try:
                        mongo_success = await AutoRedeemMembersAdapter.add_member_async(guild_id, fid, member_data)
                        if mongo_success:
                            cog_instance.logger.debug(f"✅ Added member {fid} to MongoDB (async) for guild {guild_id}")
                        else:
                            cog_instance.logger.warning(f"MongoDB add_member_async returned False for {fid}, but SQLite succeeded")
                    except Exception as mongo_e:
                        cog_instance.logger.warning(f"Failed to add member {fid} to MongoDB (async): {mongo_e}")
                
                return sqlite_success
            except Exception as e:
                cog_instance.logger.error(f"Unexpected error adding auto-redeem member {fid} (async): {e}", exc_info=True)
                return False
        
        @staticmethod
        def remove_member(cog_instance, guild_id, fid):
            """Remove a member from auto-redeem list (removes from both MongoDB and SQLite)"""
            try:
                fid = str(fid).strip()
                guild_id = int(guild_id)
                mongo_removed = False

                # Attempt MongoDB removal (best-effort, don't fail if it fails)
                if mongo_enabled() and AutoRedeemMembersAdapter:
                    try:
                        mongo_removed = AutoRedeemMembersAdapter.remove_member(guild_id, fid)
                        if mongo_removed:
                            cog_instance.logger.debug(f"✅ Removed member {fid} from MongoDB for guild {guild_id}")
                        else:
                            cog_instance.logger.debug(f"ℹ️ Member {fid} not found in MongoDB for guild {guild_id}, will remove from SQLite anyway")
                    except Exception as e:
                        cog_instance.logger.warning(f"MongoDB remove_member failed (will still remove from SQLite): {e}")

                # Always remove from SQLite (SQLite is source of truth for local success)
                cog_instance.cursor.execute(
                    "DELETE FROM auto_redeem_members WHERE guild_id = ? AND fid = ?",
                    (guild_id, fid)
                )
                cog_instance.giftcode_db.commit()
                sqlite_removed = cog_instance.cursor.rowcount > 0

                if sqlite_removed:
                    cog_instance.logger.debug(f"✅ Removed member {fid} from SQLite for guild {guild_id}")
                elif not mongo_removed:
                    cog_instance.logger.warning(f"⚠️ Member {fid} not found in either MongoDB or SQLite for guild {guild_id}")

                # Success if removed from either backend
                return sqlite_removed or mongo_removed
            except Exception as e:
                cog_instance.logger.error(f"Error in remove_member: {e}")
                return False

        @staticmethod
        async def remove_member_async(cog_instance, guild_id, fid):
            """Remove a member from auto-redeem list asynchronously (thread-safe)"""
            try:
                fid = str(fid).strip()
                guild_id_int = int(guild_id)
                mongo_removed = False

                # Step 1: Remove from MongoDB (async, non-blocking)
                if mongo_enabled() and AutoRedeemMembersAdapter:
                    try:
                        mongo_removed = await AutoRedeemMembersAdapter.remove_member_async(guild_id_int, fid)
                        if mongo_removed:
                            cog_instance.logger.debug(f"✅ Removed member {fid} from MongoDB for guild {guild_id_int}")
                        else:
                            cog_instance.logger.debug(f"ℹ️ Member {fid} not in MongoDB for guild {guild_id_int}, will try SQLite")
                    except Exception as e:
                        cog_instance.logger.warning(f"MongoDB remove_member_async failed: {e}")

                # Step 2: Always delete from SQLite using a fresh per-thread connection (thread-safe)
                db_path = 'db/giftcode.sqlite'
                def delete_sqlite_isolated():
                    import sqlite3
                    with sqlite3.connect(db_path, timeout=10) as conn:
                        conn.execute(
                            "DELETE FROM auto_redeem_members WHERE guild_id = ? AND fid = ?",
                            (guild_id_int, fid)
                        )
                        conn.commit()
                        return conn.total_changes > 0

                sqlite_removed = await asyncio.to_thread(delete_sqlite_isolated)
                if sqlite_removed:
                    cog_instance.logger.debug(f"✅ Removed member {fid} from SQLite for guild {guild_id_int}")

                # Success if removed from either backend
                return mongo_removed or sqlite_removed
            except Exception as e:
                cog_instance.logger.error(f"Error in remove_member_async: {e}")
                return False
        
        @staticmethod
        async def member_exists_async(cog_instance, guild_id, fid):
            """Check if member exists in auto-redeem list asynchronously"""
            try:
                # Try MongoDB first (async)
                if mongo_enabled() and AutoRedeemMembersAdapter:
                    try:
                        return await AutoRedeemMembersAdapter.member_exists_async(guild_id, fid)
                    except Exception as e:
                        cog_instance.logger.warning(f"MongoDB member_exists_async failed, using SQLite: {e}")
                
                # Fallback to SQLite (async)
                def check_sqlite():
                    cog_instance.cursor.execute(
                        "SELECT 1 FROM auto_redeem_members WHERE guild_id = ? AND fid = ?",
                        (guild_id, fid)
                    )
                    return cog_instance.cursor.fetchone() is not None
                
                return await asyncio.to_thread(check_sqlite)
            except Exception as e:
                cog_instance.logger.error(f"Error in member_exists_async: {e}")
                return False
                
                # Fallback to SQLite
                cog_instance.cursor.execute(
                    "SELECT 1 FROM auto_redeem_members WHERE guild_id = ? AND fid = ? LIMIT 1",
                    (guild_id, fid)
                )
                return cog_instance.cursor.fetchone() is not None
            except Exception as e:
                cog_instance.logger.error(f"Error checking auto-redeem member: {e}")
                return False
        
        @staticmethod
        def sync_members_from_sqlite(cog_instance, guild_id=None):
            """Manually sync all auto-redeem members from SQLite to MongoDB
            This is useful for Oracle VM deployments where MongoDB may have been down
            """
            try:
                if not mongo_enabled() or not AutoRedeemMembersAdapter:
                    cog_instance.logger.warning("MongoDB is not enabled. Cannot sync members.")
                    return 0
                
                # Get all members from SQLite (with valid FIDs only)
                if guild_id:
                    cog_instance.cursor.execute("""
                        SELECT guild_id, fid, nickname, furnace_lv, avatar_image, added_by, added_at
                        FROM auto_redeem_members
                        WHERE guild_id = ? AND fid NOT NULL AND fid != '' AND fid != 'None'
                        ORDER BY added_at DESC
                    """, (int(guild_id),))
                else:
                    cog_instance.cursor.execute("""
                        SELECT guild_id, fid, nickname, furnace_lv, avatar_image, added_by, added_at
                        FROM auto_redeem_members
                        WHERE fid NOT NULL AND fid != '' AND fid != 'None'
                        ORDER BY added_at DESC
                    """)
                
                rows = cog_instance.cursor.fetchall()
                if not rows:
                    cog_instance.logger.info(f"No members to sync from SQLite (guild_id: {guild_id})")
                    return 0
                
                sync_count = 0
                for row in rows:
                    try:
                        guild_id_val = int(row[0])
                        fid = str(row[1]).strip()
                        member_data = {
                            'nickname': row[2] or 'Unknown',
                            'furnace_lv': int(row[3]) or 0,
                            'avatar_image': row[4] or '',
                            'added_by': int(row[5]) or 0,
                            'added_at': row[6]
                        }
                        
                        # Check if already in MongoDB
                        try:
                            if not AutoRedeemMembersAdapter.member_exists(guild_id_val, fid):
                                success = AutoRedeemMembersAdapter.add_member(guild_id_val, fid, member_data)
                                if success:
                                    sync_count += 1
                                    cog_instance.logger.debug(f"✅ Synced member {fid} (guild {guild_id_val}) to MongoDB")
                        except Exception as check_err:
                            cog_instance.logger.warning(f"Failed to check if member {fid} exists: {check_err}")
                    except Exception as row_err:
                        cog_instance.logger.warning(f"Failed to sync row {row}: {row_err}")
                
                if sync_count > 0:
                    cog_instance.logger.info(f"🔄 Successfully synced {sync_count} members from SQLite to MongoDB" + (f" for guild {guild_id}" if guild_id else ""))
                
                return sync_count
            except Exception as e:
                cog_instance.logger.error(f"Error syncing members from SQLite to MongoDB: {e}", exc_info=True)
                return 0
    
    async def fetch_player_data(self, fid):
        """Fetch player data from WOS API"""
        try:
            # Import login handler
            from cogs.login_handler import LoginHandler
            login_handler = LoginHandler()
            
            # Get player info using fetch_player_data
            player_data = await login_handler.fetch_player_data(fid)
            
            if player_data and player_data.get('data'):
                data = player_data['data']
                return {
                    'nickname': data.get('nickname', 'Unknown'),
                    'furnace_lv': int(data.get('stove_lv', 0)),
                    'avatar_image': data.get('avatar_image', '')
                }
            return None
        except Exception as e:
            self.logger.error(f"Error fetching player data for FID {fid}: {e}")
            return None
    
    
    async def check_admin_permission(self, user_id: int) -> bool:
        """Check if user has admin permissions"""
        # Check if user is bot owner
        if await is_bot_owner(self.bot, user_id):
            return True
        
        # Check if user is global admin
        if is_global_admin(user_id):
            return True
        
        # Check if user is admin
        if is_admin(user_id):
            return True
        
        return False
    
    def format_furnace_level(self, furnace_lv):
        """Format furnace level display matching game logic"""
        if not furnace_lv or furnace_lv <= 30:
            return str(furnace_lv) if furnace_lv else "0"
        
        # Level 31-34 = 30-1 to 30-4
        if 31 <= furnace_lv <= 34:
            level_in_tier = furnace_lv - 30
            return f"30-{level_in_tier}"
        
        # Level 35+ follows pattern:
        # 35 = FC 1, 36-39 = FC 1-1 to FC 1-4
        # 40 = FC 2, 41-44 = FC 2-1 to FC 2-4
        # 45 = FC 3, 46-49 = FC 3-1 to FC 3-4
        # etc.
        adjusted_level = furnace_lv - 35  # 35 becomes 0, 36 becomes 1, etc.
        tier = (adjusted_level // 5) + 1
        level_in_tier = adjusted_level % 5
        
        if level_in_tier == 0:
            # Base tier level (35, 40, 45, etc.)
            return f"FC {tier}"
        else:
            # Sub-tier level (36-39, 41-44, etc.)
            return f"FC {tier}-{level_in_tier}"
    
    async def _redeem_for_member(self, guild_id, fid, nickname, furnace_lv, giftcode):
        """
        Process gift code redemption for a single member using session pool.
        Returns: (status, success, already_redeemed, failed)
        
        This method will retry intelligently until success, already_redeemed, or permanent failure.
        """
        RETRY_DELAY_BASE = 2.0
        MAX_RETRY_DELAY = 30.0  # Cap retry delay at 30 seconds
        MAX_LOGIN_RETRIES = 5  # Max times to retry login
        MAX_REDEMPTION_RETRIES = 10  # Max times to retry redemption
        
        try:
            # Phase 1: Login with limited retries
            session = None
            response = None
            login_successful = False
            session_id = None
            login_attempt = 0
            
            while not login_successful and login_attempt < MAX_LOGIN_RETRIES:
                login_attempt += 1
                try:
                    # Get available session from pool
                    session_id = await self.session_pool.get_available_session()
                    
                    # Get player session
                    session, response, player_info = await self.get_stove_info_wos(player_id=fid)
                    
                    # Check if login successful
                    try:
                        msg = player_info.get("msg", "NO_MSG")
                        
                        if msg == "success":  # Player info API returns lowercase success
                            login_successful = True
                            self.logger.info(f"✅ Login successful for {nickname} (FID: {fid}, attempt {login_attempt})")
                            break
                        elif msg == "NO_MSG" or not msg:
                            # Empty response = Cloudflare is rate-limiting us
                            # Use a long backoff to allow IP to cool down
                            rate_limit_delay = min(30.0 * login_attempt, 120.0)
                            self.logger.warning(f"🚫 Login rate-limited (NO_MSG) for {nickname} (FID: {fid}), attempt {login_attempt}/{MAX_LOGIN_RETRIES}. Backing off {rate_limit_delay:.0f}s...")
                            if session_id is not None:
                                await self.session_pool.mark_rate_limited(session_id)
                            await asyncio.sleep(rate_limit_delay)
                        else:
                            retry_delay = min(RETRY_DELAY_BASE * login_attempt, MAX_RETRY_DELAY)
                            self.logger.warning(f"Login attempt {login_attempt}/{MAX_LOGIN_RETRIES} failed for {nickname} (FID: {fid}), API returned: {msg}, retrying in {retry_delay:.1f}s")
                            await asyncio.sleep(retry_delay)
                    except Exception as json_err:
                        # Check if HTML error page (rate limited)
                        resp_text = await response.text()
                        if resp_text.strip().startswith('<!DOCTYPE') or resp_text.strip().startswith('<html'):
                            self.logger.warning(f"Login HTML-rate-limited for {nickname} (FID: {fid}), session {session_id}, attempt {login_attempt}")
                            if session_id is not None:
                                await self.session_pool.mark_rate_limited(session_id)
                            # Wait longer when rate limited
                            rate_limit_delay = min(30.0 * login_attempt, 120.0)
                            await asyncio.sleep(rate_limit_delay)
                        else:
                            raise json_err
                except Exception as e:
                    retry_delay = min(RETRY_DELAY_BASE * login_attempt, MAX_RETRY_DELAY)
                    self.logger.warning(f"Login attempt {login_attempt}/{MAX_LOGIN_RETRIES} error for {nickname} (FID: {fid}): {e}, retrying in {retry_delay:.1f}s")
                    await asyncio.sleep(retry_delay)
            
            # Check if login failed after all retries
            if not login_successful:
                self.logger.error(f"❌ Login failed for {nickname} after {MAX_LOGIN_RETRIES} attempts")
                return ("LOGIN_FAILED", 0, 0, 1)
            
            # Phase 2: Gift code redemption with limited retries
            redemption_successful = False
            final_status = None
            redemption_attempt = 0
            
            while not redemption_successful and redemption_attempt < MAX_REDEMPTION_RETRIES:
                redemption_attempt += 1
                try:
                    # Get available session from pool
                    session_id = await self.session_pool.get_available_session()
                    
                    # Attempt gift code redemption with CAPTCHA
                    status, img, code, method = await self.attempt_gift_code_with_api(fid, giftcode, session)
                    final_status = status
                    
                    # Check for NOT LOGIN error - need to re-establish session
                    if status == "CAPTCHA_FETCH_ERROR":
                        # Could be due to session expiry, try to re-login
                        self.logger.warning(f"⚠️ CAPTCHA fetch failed for {nickname}, might be session issue, re-logging in...")
                        
                        # Re-establish login
                        session, response, player_info = await self.get_stove_info_wos(player_id=fid)
                        try:
                            if player_info.get("msg") == "success":
                                self.logger.info(f"✅ Re-login successful for {nickname}")
                                retry_delay = min(RETRY_DELAY_BASE * redemption_attempt, MAX_RETRY_DELAY)
                                await asyncio.sleep(retry_delay)
                                continue
                            else:
                                self.logger.error(f"❌ Re-login failed for {nickname}: {player_info.get('msg')}")
                                # Treat as permanent failure after 3 re-login attempts
                                if redemption_attempt >= 3:
                                    break
                                retry_delay = min(RETRY_DELAY_BASE * 2 * redemption_attempt, MAX_RETRY_DELAY)
                                await asyncio.sleep(retry_delay)
                                continue
                        except Exception:
                            # Re-login failed critically
                            if redemption_attempt >= 3:
                                break
                            retry_delay = min(RETRY_DELAY_BASE * 2 * redemption_attempt, MAX_RETRY_DELAY)
                            await asyncio.sleep(retry_delay)
                            continue
                    
                    # Check for rate limiting
                    if status in ["RATE_LIMITED", "CAPTCHA_TOO_FREQUENT"]:
                        self.logger.warning(f"Rate limit detected for {nickname}, session {session_id}: {status}, attempt {redemption_attempt}")
                        if session_id is not None:
                            await self.session_pool.mark_rate_limited(session_id)
                        # Wait longer when rate limited
                        retry_delay = min(RETRY_DELAY_BASE * 2 * redemption_attempt, MAX_RETRY_DELAY)
                        self.logger.info(f"⏳ Waiting {retry_delay:.1f}s before retry for {nickname}")
                        await asyncio.sleep(retry_delay)
                        continue
                    
                    # Check if redemption was successful
                    if status in ["SUCCESS", "SAME TYPE EXCHANGE"]:
                        redemption_successful = True
                        self.logger.info(f"✅ Redeemed for {nickname}: {status} (attempt {redemption_attempt})")
                        break
                    elif status == "ALREADY_RECEIVED":
                        # Already redeemed - this is a success condition (user already has reward)
                        self.logger.info(f"ℹ️ Already redeemed for {nickname}")
                        break
                    elif status in ["INVALID_CODE", "EXPIRED", "CDK_NOT_FOUND", "USAGE_LIMIT", "TIME_ERROR"]:
                        # Permanent failures - code itself is bad, not worth retrying
                        if status == "TIME_ERROR":
                            self.logger.warning(f"❌ Permanent failure for {nickname}: {status} - Code is EXPIRED")
                        else:
                            self.logger.warning(f"❌ Permanent failure for {nickname}: {status} - code is invalid/expired")
                        
                        # PROACTIVE: Mark as invalid globally so other guilds don't waste time on it
                        if status in ("INVALID_CODE", "EXPIRED", "CDK_NOT_FOUND", "TIME_ERROR"):
                             asyncio.create_task(self.mark_code_invalid(giftcode))
                        break
                    elif "RECHARGE_MONEY_VIP" in status or "VIP" in status:
                        # VIP/Purchase requirement - this code requires the player to have VIP or made purchases
                        self.logger.warning(f"💎 VIP/Purchase required for {nickname}: This gift code requires VIP status or in-game purchases")
                        break
                    elif status.startswith("UNKNOWN_STATUS_"):
                        # Unknown status - likely a permanent error from the API
                        # Extract the actual status message
                        actual_msg = status.replace("UNKNOWN_STATUS_", "")
                        self.logger.warning(f"⚠️ Unknown API status for {nickname}: {actual_msg}")
                        
                        # Treat as permanent failure after 3 attempts to avoid infinite loops
                        if redemption_attempt >= 3:
                            self.logger.error(f"❌ Giving up on {nickname} after {redemption_attempt} attempts with unknown status: {actual_msg}")
                            break
                        
                        # Retry with longer backoff for unknown statuses
                        retry_delay = min(RETRY_DELAY_BASE * 2 * redemption_attempt, MAX_RETRY_DELAY)
                        self.logger.warning(f"Redemption attempt {redemption_attempt} failed for {nickname}: {status}, retrying in {retry_delay:.1f}s")
                        await asyncio.sleep(retry_delay)
                    else:
                        # Temporary failure - retry with backoff
                        retry_delay = min(RETRY_DELAY_BASE * redemption_attempt, MAX_RETRY_DELAY)
                        self.logger.warning(f"Redemption attempt {redemption_attempt}/{MAX_REDEMPTION_RETRIES} failed for {nickname}: {status}, retrying in {retry_delay:.1f}s")
                        await asyncio.sleep(retry_delay)
                except Exception as e:
                    retry_delay = min(RETRY_DELAY_BASE * redemption_attempt, MAX_RETRY_DELAY)
                    self.logger.warning(f"Redemption attempt {redemption_attempt}/{MAX_REDEMPTION_RETRIES} error for {nickname}: {e}, retrying in {retry_delay:.1f}s")
                    await asyncio.sleep(retry_delay)
            
            # Check if redemption failed after all retries
            if not redemption_successful and final_status not in ["ALREADY_RECEIVED"]:
                self.logger.error(f"❌ Redemption failed for {nickname} after {redemption_attempt} attempts, final status: {final_status}")
            
            # Track redemption to MongoDB for history and to prevent duplicates on restart
            if final_status:
                try:
                    # Track in general redemption history
                    if mongo_enabled() and GiftCodeRedemptionAdapter:
                        # Determine tracking status
                        if redemption_successful:
                            tracking_status = "success"
                        elif final_status == "ALREADY_RECEIVED":
                            tracking_status = "already_redeemed"
                        else:
                            tracking_status = "failed"
                        
                        await GiftCodeRedemptionAdapter.track_redemption_async(
                            guild_id=guild_id,
                            code=giftcode,
                            fid=str(fid),
                            status=tracking_status
                        )
                        self.logger.debug(f"Tracked redemption: guild={guild_id}, code={giftcode}, fid={fid}, status={tracking_status}")
                    
                    # CRITICAL: Track in AutoRedeemedCodesAdapter to prevent duplicate redemptions on restart
                    # This tracks which specific members have already redeemed a code
                    if mongo_enabled() and AutoRedeemedCodesAdapter:
                        if redemption_successful or final_status == "ALREADY_RECEIVED":
                            # Mark this specific member as having redeemed this code
                            await AutoRedeemedCodesAdapter.mark_code_redeemed_for_member_async(
                                guild_id=guild_id,
                                code=giftcode,
                                fid=str(fid),
                                status="success" if redemption_successful else "already_redeemed"
                            )
                            self.logger.debug(f"✅ Marked {nickname} (FID: {fid}) as redeemed for code {giftcode} in guild {guild_id}")
                except Exception as e:
                    self.logger.error(f"Error tracking redemption: {e}")
            
            # Return results
            # Treat TIME_ERROR, EXPIRED, and USAGE_LIMIT as "already redeemed" since:
            # - TIME_ERROR: Code has expired (redemption window passed)
            # - EXPIRED: Code is no longer valid
            # - USAGE_LIMIT: Code has been fully used up
            # These aren't failures - they just mean the code is no longer available
            expired_statuses = {
                "TIME_ERROR", "EXPIRED", "USAGE_LIMIT", "CDK_NOT_FOUND",
                "UNKNOWN_STATUS_TIME ERROR", "UNKNOWN_STATUS_CDK NOT FOUND",
                "UNKNOWN_STATUS_EXPIRED", "UNKNOWN_STATUS_USAGE LIMIT"
            }
            
            success = 1 if redemption_successful else 0
            already_redeemed = 1 if final_status == "ALREADY_RECEIVED" or final_status in expired_statuses else 0
            failed = 1 if not redemption_successful and final_status != "ALREADY_RECEIVED" and final_status not in expired_statuses else 0
            
            return (final_status, success, already_redeemed, failed)
            
        except Exception as e:
            self.logger.exception(f"Critical error redeeming for {nickname}: {e}")
            # Even on critical error, return failure
            return ("EXCEPTION", 0, 0, 1)
    
    async def process_auto_redeem(self, guild_id, giftcode, is_recheck=False):
        """
        Process automatic gift code redemption for all members with animation.
        
        Args:
            guild_id: Discord guild ID
            giftcode: Gift code to redeem
            is_recheck: Whether to bypass all completion caches (manual trigger)
        """
        # Reset stop signal for this guild
        self.stop_signals[guild_id] = False
        
        # Check if redemption already in progress for this guild/code combination
        redemption_key = (guild_id, giftcode)
        
        async with self._redemption_lock:
            if redemption_key in self._active_redemptions:
                self.logger.warning(f"⚠️ Auto-redeem already in progress for guild {guild_id} with code {giftcode}, skipping duplicate")
                return
            # Mark this redemption as active
            self._active_redemptions.add(redemption_key)
            self.logger.info(f"🔒 Locked auto-redeem for guild {guild_id} with code {giftcode} (Recheck: {is_recheck})")
        
        try:
            # Check if auto redeem is enabled - try MongoDB first, fallback to SQLite
            enabled = False
            if mongo_enabled() and AutoRedeemSettingsAdapter:
                try:
                    settings = await AutoRedeemSettingsAdapter.get_settings_async(guild_id)
                    if settings:
                        enabled = settings.get('enabled', False)
                except Exception as e:
                    self.logger.warning(f"Failed to get auto redeem settings from MongoDB: {e}")
            
            # Fallback to SQLite if MongoDB failed or not enabled
            if not mongo_enabled() or not AutoRedeemSettingsAdapter:
                self.cursor.execute(
                    "SELECT enabled FROM auto_redeem_settings WHERE guild_id = ?",
                    (guild_id,)
                )
                result = self.cursor.fetchone()
                enabled = result[0] == 1 if result else False
            
            if not enabled:
                self.logger.info(f"Auto redeem disabled for guild {guild_id}, skipping")
                return
            
            # Get import channel - try MongoDB first, fallback to SQLite
            channel_id = None
            if mongo_enabled() and AutoRedeemChannelsAdapter:
                try:
                    channel_config = await AutoRedeemChannelsAdapter.get_channel_async(guild_id)
                    if channel_config:
                        channel_id = channel_config.get('channel_id')
                except Exception as e:
                    self.logger.warning(f"Failed to get auto redeem channel from MongoDB: {e}")
            
            # Fallback to SQLite if MongoDB failed or not enabled
            if channel_id is None:
                self.cursor.execute(
                    "SELECT channel_id FROM auto_redeem_channels WHERE guild_id = ?",
                    (guild_id,)
                )
                channel_result = self.cursor.fetchone()
                if channel_result:
                    channel_id = channel_result[0]
            
            if not channel_id:
                self.logger.warning(f"No import channel configured for guild {guild_id}")
                return
            
            channel = self.bot.get_channel(channel_id)
            if not channel:
                self.logger.warning(f"Import channel {channel_id} not found")
                return
            
            # Get all auto-redeem members using MongoDB-first helper
            members_data = await self.AutoRedeemDB.get_members_async(self, guild_id)
            
            if not members_data:
                self.logger.info(f"No auto-redeem members for guild {guild_id}")
                return
            
            
            # Filter out members who have already redeemed this code (checked via MongoDB)
            # Use batch checking to avoid blocking the event loop with many sequential MongoDB calls
            members_to_process = []
            skipped_count = 0
            
            # CRITICAL: If is_recheck is True (Manual Trigger), we bypass the member-level redemption cache
            # to force a retry for everyone in this guild.
            if is_recheck:
                self.logger.info(f"🔄 Manual Trigger: Bypassing member-level redemption cache for code {giftcode}")
                members_to_process = [
                    (member['fid'], member['nickname'], member.get('furnace_lv', 0))
                    for member in members_data
                ]
            elif mongo_enabled() and AutoRedeemedCodesAdapter:
                try:
                    # Batch check all FIDs at once to prevent event loop blocking
                    self.logger.info(f"🔍 Batch checking {len(members_data)} members for code {giftcode}...")
                    
                    # Extract all FIDs
                    all_fids = [member['fid'] for member in members_data]
                    
                    # Run batch check in thread pool to avoid blocking event loop
                    redeemed_status = await AutoRedeemedCodesAdapter.batch_check_members_async(
                        guild_id,
                        giftcode,
                        all_fids
                    )
                    
                    # Filter based on batch check results
                    for member in members_data:
                        fid = str(member['fid'])
                        if redeemed_status.get(fid, False):
                            self.logger.info(f"⏭️ Skipping {member['nickname']} (FID: {fid}) - already redeemed code {giftcode}")
                            skipped_count += 1
                        else:
                            members_to_process.append((fid, member['nickname'], member.get('furnace_lv', 0)))
                    
                    self.logger.info(f"✅ Batch check complete: {len(members_to_process)} to process, {skipped_count} already redeemed")
                except Exception as e:
                    self.logger.warning(f"Error during batch check, falling back to processing all members: {e}")
                    # On error, process all members (better than skipping everyone)
                    members_to_process = [
                        (member['fid'], member['nickname'], member.get('furnace_lv', 0))
                        for member in members_data
                    ]
            else:
                # MongoDB not enabled, process all members
                self.logger.info("MongoDB not enabled, processing all members")
                members_to_process = [
                    (member['fid'], member['nickname'], member.get('furnace_lv', 0))
                    for member in members_data
                ]
            
            # Convert to tuple format for compatibility with existing code
            members = members_to_process
            
            if not members:
                # If is_recheck is True, we don't send any message if there's no work to do
                # (Manual triggers should now have members, so this is just a safety)
                if is_recheck:
                    self.logger.info(f"✅ Silent skip: All {len(members_data)} members have already redeemed code {giftcode} for guild {guild_id}")
                    return

                self.logger.info(f"✅ All {len(members_data)} members have already redeemed code {giftcode} for guild {guild_id}")
                # Send a message to the channel
                try:
                    skip_embed = discord.Embed(
                        title="🎁 Auto-Redeem Skipped",
                        description=(
                            f"```ansi\n"
                            f"\u001b[2;36m━━━━━━━━━━━━━━━━━━━━━━\u001b[0m\n"
                            f"\u001b[1;37mGift Code: {giftcode}\u001b[0m\n"
                            f"\u001b[2;36m━━━━━━━━━━━━━━━━━━━━━━\u001b[0m\n"
                            f"```\n"
                            f"✅ **All {len(members_data)} members have already redeemed this code!**\n"
                            f"⏰ **Checked:** <t:{int(datetime.now().timestamp())}:R>\n"
                        ),
                        color=0x57F287
                    )
                    await channel.send(embed=skip_embed)
                except Exception:
                    pass
                return
            
            self.logger.info(f"📊 Processing {len(members)} members (skipped {skipped_count} already redeemed)")
            
            # Send initial animation message
            initial_embed = discord.Embed(
                title="🎁 Auto-Redeem Started",
                description=(
                    f"```ansi\n"
                    f"\u001b[2;36m━━━━━━━━━━━━━━━━━━━━━━\u001b[0m\n"
                    f"\u001b[1;37mGift Code: {giftcode}\u001b[0m\n"
                    f"\u001b[2;36m━━━━━━━━━━━━━━━━━━━━━━\u001b[0m\n"
                    f"```\n"
                    f"👥 **Members:** {len(members)}\n"
                    f"⏭️ **Skipped:** {skipped_count} (already redeemed)\n"
                    f"⏳ **Status:** Processing...\n"
                    f"🏰 **Server:** {channel.guild.name}\n"
                ),
                color=0xFEE75C
            )
            animation_message = await channel.send(embed=initial_embed)
            
            # Shared counters for progress tracking
            success_count = 0
            failed_count = 0
            already_redeemed_count = 0
            completed_count = 0
            progress_lock = asyncio.Lock()
            
            # Semaphore to limit concurrent redemptions
            semaphore = asyncio.Semaphore(self.concurrent_redemptions)
            
            async def process_member_with_semaphore(idx, fid, nickname, furnace_lv):
                """Process a single member with semaphore control"""
                nonlocal success_count, failed_count, already_redeemed_count, completed_count
                
                # Check for stop signal
                if self.stop_signals.get(guild_id):
                    return
                
                async with semaphore:
                    # Double check after acquiring semaphore
                    if self.stop_signals.get(guild_id):
                        return
                    # Process the member
                    status, success, already_redeemed, failed = await self._redeem_for_member(
                        guild_id, fid, nickname, furnace_lv, giftcode
                    )
                    
                    # Update counters
                    async with progress_lock:
                        success_count += success
                        already_redeemed_count += already_redeemed
                        failed_count += failed
                        completed_count += 1
                        
                        # Update progress message after each completion
                        try:
                            # Calculate progress percentage and create visual bar
                            progress_percent = (completed_count / len(members)) * 100
                            bar_length = 20
                            filled_length = int(bar_length * completed_count / len(members))
                            progress_bar = '█' * filled_length + '░' * (bar_length - filled_length)
                            
                            guild_name = channel.guild.name if channel and channel.guild else "Unknown Server"
                            progress_embed = discord.Embed(
                                title="🎁 Auto-Redeem In Progress",
                                description=(
                                    f"```ansi\n"
                                    f"\u001b[2;36m━━━━━━━━━━━━━━━━━━━━━━\u001b[0m\n"
                                    f"\u001b[1;37mGift Code: {giftcode}\u001b[0m\n"
                                    f"\u001b[2;36m━━━━━━━━━━━━━━━━━━━━━━\u001b[0m\n"
                                    f"```\n"
                                    f"**Progress:** `{progress_bar}` **{progress_percent:.1f}%**\n"
                                    f"📊 **Processed:** {completed_count}/{len(members)}\n\n"
                                    f"✅ **Success:** {success_count}\n"
                                    f"ℹ️ **Already Redeemed:** {already_redeemed_count}\n"
                                    f"❌ **Failed:** {failed_count}\n"
                                    f"🏰 **Server:** {guild_name}\n"
                                ),
                                color=0x5865F2
                            )
                            if animation_message:
                                await animation_message.edit(embed=progress_embed)
                        except Exception as e:
                            self.logger.warning(f"Failed to update progress message: {e}")
            
            # Create tasks for all members
            tasks = [
                process_member_with_semaphore(idx, fid, nickname, furnace_lv)
                for idx, (fid, nickname, furnace_lv) in enumerate(members, 1)
            ]
            
            # Process all members concurrently
            await asyncio.gather(*tasks, return_exceptions=True)
            
            # Send final completion message
            final_embed = discord.Embed(
                title="🎁 Auto-Redeem Complete",
                description=(
                    f"```ansi\n"
                    f"\u001b[2;36m━━━━━━━━━━━━━━━━━━━━━━\u001b[0m\n"
                    f"\u001b[1;37mGift Code: {giftcode}\u001b[0m\n"
                    f"\u001b[2;36m━━━━━━━━━━━━━━━━━━━━━━\u001b[0m\n"
                    f"```\n"
                    f"✅ **Success:** {success_count}\n"
                    f"ℹ️ **Already Redeemed:** {already_redeemed_count}\n"
                    f"❌ **Failed:** {failed_count}\n"
                    f"⏰ **Completed:** <t:{int(datetime.now().timestamp())}:R>\n"
                ),
                color=0x57F287
            )
            if animation_message:
                await animation_message.edit(embed=final_embed)
            
            self.logger.info(f"Auto-redeem completed for guild {guild_id}: {success_count} success, {already_redeemed_count} already redeemed, {failed_count} failed")
            
            # --- NEW: Save completion state per-guild to survive bot restarts ---
            try:
                self.cursor.execute(
                    "INSERT OR IGNORE INTO auto_redeem_completed_guilds (guild_id, giftcode) VALUES (?, ?)",
                    (guild_id, giftcode)
                )
                self.giftcode_db.commit()
                self.logger.info(f"✅ Recorded completion for guild {guild_id} and code {giftcode} in SQLite")
            except Exception as sql_e:
                self.logger.error(f"Failed to record guild completion in SQLite: {sql_e}")
            
        except Exception as e:
            self.logger.exception(f"Error in process_auto_redeem: {e}")
        finally:
            # Always release the lock
            async with self._redemption_lock:
                redemption_key = (guild_id, giftcode)
                if redemption_key in self._active_redemptions:
                    self._active_redemptions.discard(redemption_key)
                    self.logger.info(f"🔓 Unlocked auto-redeem for guild {guild_id} with code {giftcode}")
            
            # NOTE: Code is NOT marked as processed here to allow multiple guilds to process the same code
            # Code will be marked as processed by trigger_auto_redeem_for_new_codes after all guilds finish
    
    def encode_data(self, data_dict):
        """Encode data for WOS API requests"""
        import hashlib
        import urllib.parse
        
        # Sort keys and create form string
        sorted_keys = sorted(data_dict.keys())
        form_parts = [f"{key}={data_dict[key]}" for key in sorted_keys]
        form = "&".join(form_parts)
        
        # Create signature
        sign = hashlib.md5((form + self.wos_encrypt_key).encode('utf-8')).hexdigest()
        
        # Return encoded data
        return f"sign={sign}&{form}"
    
    async def fetch_captcha(self, player_id, session):
        """Fetch CAPTCHA image from WOS API"""
        try:
            import time
            
            data_to_encode = {
                "fid": str(player_id),
                "time": str(int(time.time() * 1000))
            }
            data = self.encode_data(data_to_encode)
            
            try:
                headers = {
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'Origin': 'https://wos-giftcode.centurygame.com',
                    'Referer': 'https://wos-giftcode.centurygame.com/',
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
                }
                timeout = aiohttp.ClientTimeout(total=10)
                
                async with session.post(self.wos_captcha_url, headers=headers, data=data, timeout=timeout) as response:
                    # Check for rate limiting (HTTP 429) or 403 Forbidden
                    if response.status == 403:
                        self.logger.error(f"❌ 403 Forbidden in fetch_captcha for FID {player_id}. Check Origin header.")
                        return None, "FORBIDDEN"
                    if response.status == 429:
                        self.logger.warning(f"Rate limited (429) in fetch_captcha for FID {player_id}")
                        return None, "RATE_LIMITED"
                    
                    if response.status == 200:
                        try:
                            response_json = await response.json()
                            if response_json.get("msg") == "SUCCESS":  # API returns uppercase SUCCESS
                                return response_json.get("data"), None
                            elif response_json.get("msg") == "CAPTCHA GET TOO FREQUENT":
                                return None, "CAPTCHA_TOO_FREQUENT"
                            else:
                                self.logger.warning(f"CAPTCHA API returned: {response_json.get('msg', 'Unknown')}")
                                return None, f"API Error: {response_json.get('msg', 'Unknown')}"
                        except Exception as json_error:
                            # Check if response is HTML (rate limit error page)
                            text = await response.text()
                            if text.strip().startswith('<!DOCTYPE') or text.strip().startswith('<html'):
                                self.logger.warning(f"Received HTML error page (likely rate limited) for FID {player_id}")
                                return None, "RATE_LIMITED"
                            self.logger.error(f"JSON decode error in fetch_captcha for FID {player_id}: {json_error}")
                            return None, f"JSON Error: {json_error}"
                    else:
                        self.logger.warning(f"HTTP {response.status} in fetch_captcha for FID {player_id}")
                        return None, f"HTTP Error: {response.status}"
                        
            except asyncio.TimeoutError:
                self.logger.warning(f"Timeout in fetch_captcha for FID {player_id}")
                return None, "TIMEOUT"
            except Exception as e:
                self.logger.error(f"Request error in fetch_captcha for FID {player_id}: {e}")
                return None, f"Request Error: {e}"

        except Exception as e:
            self.logger.exception(f"Unexpected error in fetch_captcha for FID {player_id}: {e}")
            return None, str(e)
    
    async def attempt_gift_code_with_api(self, player_id, giftcode, session):
        """Attempt to redeem a gift code with CAPTCHA solving"""
        import time
        import base64
        import random
        
        if not self.captcha_solver or not self.captcha_solver.is_initialized:
            return "CAPTCHA_SOLVER_NOT_AVAILABLE", None, None, None
        
        max_ocr_attempts = 4
        
        for attempt in range(max_ocr_attempts):
            self.logger.info(f"Attempt {attempt + 1}/{max_ocr_attempts} to redeem for FID {player_id}")
            
            # Fetch captcha
            captcha_image_base64, error = await self.fetch_captcha(player_id, session)
            
            if error:
                if error == "CAPTCHA_TOO_FREQUENT":
                    return "CAPTCHA_TOO_FREQUENT", None, None, None
                else:
                    return "CAPTCHA_FETCH_ERROR", None, None, None
            
            if not captcha_image_base64:
                return "CAPTCHA_FETCH_ERROR", None, None, None
            
            # Decode captcha image
            try:
                # API returns dict with 'img' key containing base64 data
                if isinstance(captcha_image_base64, dict):
                    img_b64_data = captcha_image_base64.get('img', '')
                    # Strip data:image prefix if present
                    if img_b64_data.startswith("data:image"):
                        img_b64_data = img_b64_data.split(",", 1)[1]
                elif isinstance(captcha_image_base64, str):
                    if captcha_image_base64.startswith("data:image"):
                        img_b64_data = captcha_image_base64.split(",", 1)[1]
                    else:
                        img_b64_data = captcha_image_base64
                else:
                    self.logger.error(f"Unexpected CAPTCHA data type: {type(captcha_image_base64)}")
                    return "CAPTCHA_FETCH_ERROR", None, None, None
                
                if not img_b64_data:
                    self.logger.error("CAPTCHA image data is empty")
                    return "CAPTCHA_FETCH_ERROR", None, None, None
                
                image_bytes = base64.b64decode(img_b64_data)
            except Exception as e:
                self.logger.error(f"Failed to decode base64 image: {e}")
                return "CAPTCHA_FETCH_ERROR", None, None, None
            
            # Solve captcha
            captcha_code, success, method, confidence, _ = await self.captcha_solver.solve_captcha(
                image_bytes, fid=player_id, attempt=attempt)
            
            if not success:
                if attempt == max_ocr_attempts - 1:
                    return "MAX_CAPTCHA_ATTEMPTS_REACHED", None, None, None
                continue
            
            self.logger.info(f"OCR solved: {captcha_code} (method:{method}, conf:{confidence:.2f})")
            
            # Submit gift code with solved captcha
            data_to_encode = {
                "fid": str(player_id),
                "cdk": giftcode,
                "captcha_code": captcha_code,
                "time": str(int(time.time() * 1000))
            }
            data = self.encode_data(data_to_encode)
            
            # Run async request
            try:
                headers = {
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'Origin': 'https://wos-giftcode.centurygame.com',
                    'Referer': 'https://wos-giftcode.centurygame.com/',
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
                }
                timeout = aiohttp.ClientTimeout(total=20)  # Longer timeout for redemption
                
                async with session.post(self.wos_giftcode_url, headers=headers, data=data, timeout=timeout) as response:
                    if response.status == 403:
                        self.logger.error(f"❌ 403 Forbidden in gift code redemption for FID {player_id}. Check Origin header.")
                        return "FORBIDDEN", None, giftcode, method
                        
                    # Check for rate limiting
                    if response.status == 429:
                        self.logger.warning(f"Rate limited (429) in gift code redemption for FID {player_id}")
                        return "RATE_LIMITED", None, giftcode, method
                        
                    if response.status != 200:
                        self.logger.warning(f"HTTP {response.status} in gift code redemption for FID {player_id}")
                        return f"HTTP_{response.status}", None, giftcode, method
                        
                    try:
                        response_json = await response.json()
                        msg = response_json.get("msg", "Unknown Error").strip('.')
                        err_code = response_json.get("err_code")
                    except Exception as json_error:
                        response_text = await response.text()
                        if response_text.strip().startswith('<!DOCTYPE') or response_text.strip().startswith('<html'):
                             return "RATE_LIMITED", None, giftcode, method
                        self.logger.error(f"Error parsing response: {json_error}")
                        return "RESPONSE_PARSE_ERROR", None, giftcode, method

            except asyncio.TimeoutError:
                return "TIMEOUT", None, giftcode, method
            except Exception as e:
                self.logger.error(f"Request error in redemption for FID {player_id}: {e}")
                return "REQUEST_ERROR", None, giftcode, method
            
            # Check for captcha errors
            captcha_errors = {
                ("CAPTCHA CHECK ERROR", 40103),
                ("CAPTCHA GET TOO FREQUENT", 40100),
                ("CAPTCHA CHECK TOO FREQUENT", 40101),
                ("CAPTCHA EXPIRED", 40102)
            }
                
            is_captcha_error = (msg, err_code) in captcha_errors
            
            if is_captcha_error:
                if attempt == max_ocr_attempts - 1:
                    return "CAPTCHA_INVALID", image_bytes, captcha_code, method
                else:
                    await asyncio.sleep(random.uniform(1.5, 2.5))
                    continue
            
            # Determine final status
            if msg == "SUCCESS":
                return "SUCCESS", image_bytes, captcha_code, method
            elif msg == "RECEIVED" and err_code == 40008:
                return "ALREADY_RECEIVED", image_bytes, captcha_code, method
            elif msg == "SAME TYPE EXCHANGE" and err_code == 40011:
                return "SAME TYPE EXCHANGE", image_bytes, captcha_code, method
            elif msg == "TIME ERROR":
                # API sometimes returns 40007 (expired) or 40102 (captcha expired/invalid during window)
                # In either case, if msg is TIME ERROR, it's a permanent failure/expired code
                return "TIME_ERROR", image_bytes, captcha_code, method
            elif msg == "CDK NOT FOUND":
                return "CDK_NOT_FOUND", image_bytes, captcha_code, method
            elif msg == "USAGE LIMIT" and err_code == 40009:
                return "USAGE_LIMIT", image_bytes, captcha_code, method
            else:
                self.logger.warning(f"⚠️ Unhandled API status message: '{msg}' (code: {err_code}) for FID {player_id}")
                return f"UNKNOWN_STATUS_{msg}", image_bytes, captcha_code, method
        
        return "MAX_ATTEMPTS_REACHED", None, None, None
    
    async def get_stove_info_wos(self, player_id, session=None):
        """Asynchronously get player info and establish session for WOS API calls"""
        import random
        if session is None:
            session = self.session if self.session else aiohttp.ClientSession()
        
        # Get player info to establish session
        data_to_encode = {
            "fid": str(player_id),
            "time": str(int(time.time() * 1000))
        }
        data = self.encode_data(data_to_encode)
        
        # Use realistic browser-like headers to avoid Cloudflare fingerprinting
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
            'Origin': 'https://wos-giftcode.centurygame.com',
            'Referer': 'https://wos-giftcode.centurygame.com/',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
        }
        
        try:
            async with session.post(
                self.wos_player_info_url, 
                headers=headers, 
                data=data, 
                timeout=aiohttp.ClientTimeout(total=15)
            ) as response:
                if response.status == 403:
                    self.logger.error(f"❌ 403 Forbidden in get_stove_info_wos for {player_id}. Check Origin header.")
                    return session, response, {"err_code": 403, "msg": "FORBIDDEN"}
                # Read response info
                try:
                    resp_json = await response.json(content_type=None)
                except Exception as json_e:
                    raw_text = await response.text()
                    self.logger.error(f"Failed to parse JSON for {player_id}. Status: {response.status}, Raw text: {raw_text[:200]}")
                    resp_json = {}
                return session, response, resp_json
        except Exception as e:
            self.logger.error(f"Error in get_stove_info_wos for {player_id}: {e}")
            raise e
    
    
    async def _wait_for_rate_limit(self):
        """Enforce rate limiting between API calls"""
        now = datetime.now().timestamp()
        time_since_last_call = now - self.last_api_call
        
        if time_since_last_call < self.min_api_call_interval:
            sleep_time = self.min_api_call_interval - time_since_last_call
            sleep_time += random.uniform(0, 0.5)
            await asyncio.sleep(sleep_time)
            
        self.last_api_call = datetime.now().timestamp()
    
    async def fetch_codes_from_api(self):
        """Fetch gift codes from the API"""
        try:
            connector = aiohttp.TCPConnector(ssl=self.ssl_context)
            async with aiohttp.ClientSession(connector=connector) as session:
                headers = {
                    'X-API-Key': self.api_key,
                    'Content-Type': 'application/json'
                }
                
                await self._wait_for_rate_limit()
                
                async with session.get(self.api_url, headers=headers) as response:
                    if response.status != 200:
                        self.logger.error(f"API request failed with status {response.status}")
                        return []
                    
                    response_text = await response.text()
                    result = json.loads(response_text)
                    
                    if 'error' in result or 'detail' in result:
                        error_msg = result.get('error', result.get('detail', 'Unknown error'))
                        self.logger.error(f"API returned error: {error_msg}")
                        return []
                    
                    api_giftcodes = result.get('codes', [])
                    self.logger.info(f"Fetched {len(api_giftcodes)} codes from API")
                    
                    # Parse codes
                    valid_codes = []
                    for code_line in api_giftcodes:
                        parts = code_line.strip().split()
                        if len(parts) != 2:
                            continue
                        
                        code, date_str = parts
                        if not re.match("^[a-zA-Z0-9]+$", code):
                            continue
                        
                        try:
                            date_obj = datetime.strptime(date_str, "%d.%m.%Y")
                            valid_codes.append((code, date_obj.strftime("%Y-%m-%d")))
                        except ValueError:
                            continue
                    
                    return valid_codes
        except Exception as e:
            self.logger.exception(f"Error fetching codes from API: {e}")
            return []
    
    async def get_active_gift_codes_consolidated(self, force_refresh=False):
        """
        Fetch and filter active gift codes from both website and API sources.
        Returns a dictionary mapping gift code strings to their expiry dates.
        """
        # Load known invalid codes from DB to filter out quickly (unless forcing refresh)
        invalid_codes = set()
        if not force_refresh:
            try:
                if mongo_enabled() and GiftCodesAdapter:
                    mongo_codes = GiftCodesAdapter.get_all()
                    for c, _, status in mongo_codes:
                        if status in ('invalid', 'expired'):
                            invalid_codes.add(c)
                
                self.cursor.execute("SELECT giftcode FROM gift_codes WHERE validation_status IN ('invalid', 'expired')")
                for row in self.cursor.fetchall():
                    invalid_codes.add(row[0])
            except Exception as e:
                self.logger.warning(f"Failed to fetch invalid codes for filtering: {e}")

        active_codes_map = {}
        
        # 1. Fetch from website scraper
        try:
            from gift_codes import get_active_gift_codes
            website_codes = await get_active_gift_codes()
            if website_codes:
                for item in website_codes:
                    if item.get('code') and item.get('is_active', True):
                        active_codes_map[item['code']] = item.get('expiry', 'Unknown')
        except Exception as e:
            self.logger.error(f"Error fetching website codes for consolidated list: {e}")
            
        # 2. Fetch from API
        try:
            api_codes = await self.fetch_codes_from_api()
            if api_codes:
                for code_str, date_str in api_codes:
                    if code_str not in active_codes_map:
                        active_codes_map[code_str] = date_str
        except Exception as e:
            self.logger.error(f"Error fetching API codes for consolidated list: {e}")
            
        # 3. Filter by expiry date
        filtered_map = {}
        now = datetime.now()
        import dateutil.parser
        
        for code_str, expiry_str in active_codes_map.items():
            is_active = True
            if expiry_str and expiry_str != 'Unknown':
                try:
                    expiry_date = dateutil.parser.parse(expiry_str, fuzzy=True)
                    if expiry_date <= now:
                        is_active = False
                except Exception:
                    pass
            
            if is_active and code_str not in invalid_codes:
                filtered_map[code_str] = expiry_str
                
        return filtered_map

    async def check_giftcode_in_api(self, giftcode: str) -> bool:
        """Check if a gift code exists in the API"""
        try:
            connector = aiohttp.TCPConnector(ssl=self.ssl_context)
            async with aiohttp.ClientSession(connector=connector) as session:
                headers = {
                    'X-API-Key': self.api_key
                }
                
                await self._wait_for_rate_limit()
                
                async with session.get(f"{self.api_url}?action=check&giftcode={giftcode}", headers=headers) as response:
                    if response.status == 200:
                        try:
                            result = await response.json()
                            exists = result.get('exists', False)
                            self.logger.info(f"Checked code {giftcode} in API: exists={exists}")
                            return exists
                        except json.JSONDecodeError:
                            self.logger.warning(f"Invalid JSON response when checking code {giftcode}")
                            return False
                    else:
                        self.logger.warning(f"Failed to check code {giftcode}: {response.status}")
                        return False
        except Exception as e:
            self.logger.exception(f"Error checking code {giftcode} in API: {e}")
            return False
    
    async def add_giftcode_to_api(self, giftcode: str) -> bool:
        """Add a gift code to the API"""
        try:
            connector = aiohttp.TCPConnector(ssl=self.ssl_context)
            async with aiohttp.ClientSession(connector=connector) as session:
                headers = {
                    'Content-Type': 'application/json',
                    'X-API-Key': self.api_key
                }
                
                date_str = datetime.now().strftime("%d.%m.%Y")
                data = {
                    'code': giftcode,
                    'date': date_str
                }
                
                await self._wait_for_rate_limit()
                
                async with session.post(self.api_url, json=data, headers=headers) as response:
                    response_text = await response.text()
                    
                    if response.status == 200:
                        try:
                            result = json.loads(response_text)
                            if result.get('success') == True:
                                self.logger.info(f"Successfully added code {giftcode} to API")
                                return True
                            else:
                                self.logger.warning(f"API didn't confirm success for code {giftcode}: {response_text[:200]}")
                                return False
                        except json.JSONDecodeError:
                            self.logger.warning(f"Invalid JSON response when adding code {giftcode}: {response_text[:200]}")
                            return False
                    elif response.status == 409:
                        # Code already exists in API - consider this a success
                        self.logger.info(f"Code {giftcode} already exists in API")
                        return True
                    else:
                        self.logger.warning(f"Failed to add code {giftcode} to API: {response.status}, {response_text[:200]}")
                        if "invalid" in response_text.lower():
                            self.logger.warning(f"Code {giftcode} marked invalid by API")
                        return False
        except Exception as e:
            self.logger.exception(f"Error adding code {giftcode} to API: {e}")
            return False
    
    
    async def verify_code_with_game_api(self, guild_id, giftcode):
        """
        Verify a gift code by attempting to redeem it for a random member in the guild.
        Returns: (is_valid, status_message, fid_used)
        """
        try:
            # Get members for this guild
            members = self.AutoRedeemDB.get_members(self, guild_id)
            if not members:
                return False, "NO_MEMBERS", None
            
            # Try up to 3 different members to verify
            # Sort by furnace level to try active players first (more likely to be valid FIDs)
            # But randomize slightly to avoid hitting same person always
            import random
            
            # Filter for high level players first if available
            high_level = [m for m in members if m.get('furnace_lv', 0) > 20]
            candidates = high_level if high_level else members
            
            # Pick up to 3 random candidates to avoid getting stuck on a bad account
            samples = random.sample(candidates, min(3, len(candidates)))
            
            for member in samples:
                fid = member['fid']
                nickname = member['nickname']
                furnace_lv = member.get('furnace_lv', 0)
                
                self.logger.info(f"VERIFY: Testing code {giftcode} with member {nickname} ({fid})")
                
                # Attempt redemption
                # We use _redeem_for_member but logic is slightly different - we just want status
                # _redeem_for_member returns (status, success, already_redeemed, failed)
                status, success, already_redeemed, failed = await self._redeem_for_member(
                    guild_id, fid, nickname, furnace_lv, giftcode
                )
                
                self.logger.info(f"VERIFY: Result for {nickname}: {status}")
                
                # Analyze status
                if status == "SUCCESS":
                    return True, "VALID_SUCCESS", fid
                elif status == "ALREADY_RECEIVED":
                    return True, "VALID_ALREADY_RECEIVED", fid
                elif status == "SAME TYPE EXCHANGE":
                    return True, "VALID_SAME_TYPE", fid
                elif status == "CDK_NOT_FOUND":
                    # This is a hard failure - code definitely invalid
                    return False, "INVALID_CODE", fid
                elif status == "EXPIRED":
                    return False, "EXPIRED_CODE", fid
                elif status == "TIME_ERROR":
                     return False, "EXPIRED_CODE", fid
                elif status == "USAGE_LIMIT":
                     return False, "USAGE_LIMIT_REACHED", fid
                
                # If we get here (e.g. LOGIN_FAILED, CAPTCHA errors), try next member
            
            return False, "VERIFICATION_FAILED", None
            
        except Exception as e:
            self.logger.error(f"Error verifying code {giftcode}: {e}")
            return False, f"ERROR: {str(e)}", None

    @tasks.loop(seconds=60)  # Check every minute
    async def api_check_task(self):
        """Periodically check API for new gift codes"""
        try:
            self.logger.info("Starting API check for new gift codes")
            
            # Fetch codes from API
            api_codes = await self.fetch_codes_from_api()
            
            if not api_codes:
                self.logger.warning("No codes fetched from API - API might be empty or down")
                return
            
            self.logger.info(f"Successfully fetched {len(api_codes)} codes from API")
            
            # Also fetch from MongoDB if enabled
            db_codes_data = {} # code.upper() -> {'date': date, 'processed': bool}
            
            # Fetch from SQLite
            self.cursor.execute("SELECT giftcode, date, auto_redeem_processed FROM gift_codes")
            for row in self.cursor.fetchall():
                db_codes_data[str(row[0]).upper()] = {'date': row[1], 'processed': bool(row[2])}
            
            if mongo_enabled() and GiftCodesAdapter:
                try:
                    # GiftCodesAdapter.get_all_with_status() returns list of dicts
                    mongo_codes = GiftCodesAdapter.get_all_with_status()
                    if mongo_codes:
                        for c in mongo_codes:
                            code_up = str(c.get('giftcode', '')).upper()
                            if code_up:
                                db_codes_data[code_up] = {
                                    'date': c.get('date'),
                                    'processed': bool(c.get('auto_redeem_processed', False))
                                }
                except Exception as e:
                    self.logger.error(f"Failed to fetch codes from Mongo: {e}")

            self.logger.info(f"Found {len(db_codes_data)} existing codes in database(s)")
            
            # Find new codes
            new_codes = []
            for code, date in api_codes:
                code_up = str(code).upper()
                if code_up not in db_codes_data:
                    new_codes.append((code, date))
                else:
                    # Potential re-issue? 
                    # If it was already processed but the API is still returning it with a NEW date 
                    # or it's been marked processed a long time ago, we might want to re-trigger.
                    # For now, let's just allow it if it was marked processed but we want to be safe.
                    # Actually, a better check: if the date in API is different/newer than stored date.
                    stored_date = db_codes_data[code_up]['date']
                    if stored_date and date and date != stored_date:
                        self.logger.info(f"♻️ Re-issued code detected: {code} (Old Date: {stored_date}, New Date: {date}). Re-triggering auto-redeem.")
                        new_codes.append((code, date))
                        # Reset processed flag in DB so it actually runs
                        try:
                            # Update SQLite
                            self.cursor.execute("UPDATE gift_codes SET auto_redeem_processed = 0, date = ? WHERE giftcode = ?", (date, code))
                            # Update MongoDB
                            if mongo_enabled() and GiftCodesAdapter:
                                try:
                                    db = get_mongo_db()
                                    db['gift_codes'].update_one({'_id': code}, {'$set': {'auto_redeem_processed': False, 'date': date}})
                                except Exception as me:
                                    self.logger.error(f"Failed to reset Mongo processed status for {code}: {me}")
                        except Exception as e:
                            self.logger.error(f"Failed to reset re-issued code {code}: {e}")
            
            if not new_codes:
                self.logger.info(f"No new codes found. All {len(api_codes)} API codes already in database and processed")
                return
            
            self.logger.info(f"Found {len(new_codes)} codes to process (new or re-issued)!")
            
            # Add new codes to database with auto_redeem_processed = 0
            for code, date in new_codes:
                try:
                    # Insert into SQLite
                    self.cursor.execute(
                        "INSERT OR IGNORE INTO gift_codes (giftcode, date, validation_status, added_at, auto_redeem_processed) VALUES (?, ?, ?, ?, ?)",
                        (code, date, "validated", datetime.now(), 0)
                    )
                    self.logger.info(f"Added new code to SQLite: {code}")
                    
                    # CRITICAL: Also insert into MongoDB if enabled
                    if mongo_enabled() and GiftCodesAdapter and _get_db:
                        try:
                            # Insert the code with auto_redeem_processed = False
                            db = _get_db()
                            if db:
                                db[GiftCodesAdapter.COLL].update_one(
                                    {'_id': code},
                                    {
                                        '$set': {
                                            'date': date,
                                            'validation_status': 'validated',
                                            'auto_redeem_processed': False,
                                            'created_at': datetime.utcnow().isoformat(),
                                            'updated_at': datetime.utcnow().isoformat()
                                        }
                                    },
                                    upsert=True
                                )
                                self.logger.info(f"✅ Added new code to MongoDB: {code}")
                        except Exception as mongo_err:
                            self.logger.error(f"⚠️ Failed to add code {code} to MongoDB: {mongo_err}")
                            # Continue anyway - SQLite is the fallback
                    
                except Exception as e:
                    self.logger.error(f"Error inserting code {code}: {e}")
            
            self.giftcode_db.commit()
            self.logger.info(f"Committed {len(new_codes)} new codes to database")
            
            # Notify global admins
            await self.notify_admins_new_codes(new_codes)
            
            # CRITICAL: Trigger auto-redeem for the new codes
            self.logger.info(f"🔔 Triggering auto-redeem for {len(new_codes)} new codes from API...")
            await self.trigger_auto_redeem_for_new_codes(new_codes)
            
        except Exception as e:
            self.logger.exception(f"Error in API check task: {e}")
    
    @api_check_task.before_loop
    async def before_api_check(self):
        """Wait for bot to be ready before starting API checks"""
        await self.bot.wait_until_ready()
        self.logger.info("Gift code API check task started")
        
        # Cleanup members with null/empty FIDs from all guilds
        self.logger.info("🧹 Cleaning up members with null/empty FIDs...")
        cleanup_count = self.AutoRedeemDB.cleanup_null_members(self)
        if cleanup_count > 0:
            self.logger.info(f"✅ Removed {cleanup_count} invalid members from auto-redeem lists")
        
        # Sync auto-redeem settings from SQLite to MongoDB (for migration on first startup)
        await self.sync_auto_redeem_settings_to_mongo()
        
        # Sync gift codes from MongoDB to SQLite (CRITICAL for Render persistence)
        # This prevents the bot from thinking all existing codes are "new" on restart
        await self.sync_gift_codes_from_mongo_to_sqlite()
        
        # Process any existing unprocessed codes on startup (mark as processed, don't redeem)
        await self.process_existing_codes_on_startup()
    
    async def sync_auto_redeem_settings_to_mongo(self):
        """
        Sync auto-redeem settings from SQLite to MongoDB on startup.
        This ensures persistence even after SQLite resets on Render.
        """
        try:
            if not mongo_enabled() or not AutoRedeemSettingsAdapter:
                self.logger.info("⏭️ MongoDB not enabled, skipping auto-redeem settings sync")
                return
            
            self.logger.info("🔄 === SYNCING AUTO-REDEEM SETTINGS TO MONGODB ===")
            
            # Read ALL enabled guilds from SQLite
            try:
                self.cursor.execute("""
                    SELECT guild_id, enabled, updated_by, updated_at
                    FROM auto_redeem_settings
                    WHERE enabled = 1
                """)
                sqlite_settings = self.cursor.fetchall()
                self.logger.info(f"📂 SQLite: Found {len(sqlite_settings)} enabled guilds")
            except Exception as e:
                self.logger.error(f"❌ Error reading SQLite settings: {e}")
                sqlite_settings = []
            
            if not sqlite_settings:
                self.logger.info("ℹ️ No enabled guilds in SQLite to sync")
                # Also check MongoDB for existing settings
                try:
                    mongo_settings = await AutoRedeemSettingsAdapter.get_all_settings_async()
                    if mongo_settings:
                        enabled_count = sum(1 for s in mongo_settings if s.get('enabled', False))
                        self.logger.info(f"✅ MongoDB already has {enabled_count} enabled guilds (out of {len(mongo_settings)} total)")
                except Exception as e:
                    self.logger.error(f"❌ Error checking MongoDB settings: {e}")
                return
            
            # Sync each enabled setting to MongoDB
            synced_count = 0
            for row in sqlite_settings:
                guild_id = row[0]
                enabled = bool(row[1])
                updated_by = row[2] if len(row) > 2 else 0
                
                # Check if already exists in MongoDB
                existing = AutoRedeemSettingsAdapter.get_settings(guild_id)
                
                if existing and existing.get('enabled', False):
                    self.logger.debug(f"ℹ️ Guild {guild_id} already enabled in MongoDB, skipping")
                    continue
                
                # Sync to MongoDB
                try:
                    success = AutoRedeemSettingsAdapter.set_enabled(
                        guild_id,
                        enabled,
                        updated_by or 0
                    )
                    if success:
                        synced_count += 1
                        self.logger.info(f"✅ Synced guild {guild_id} auto-redeem settings to MongoDB")
                    else:
                        self.logger.warning(f"⚠️ Failed to sync guild {guild_id} to MongoDB")
                except Exception as e:
                    self.logger.error(f"❌ Error syncing guild {guild_id}: {e}")
            
            self.logger.info(f"🎉 Synced {synced_count} guild(s) to MongoDB")
            self.logger.info("🏁 === AUTO-REDEEM SETTINGS SYNC COMPLETE ===")
        except Exception as e:
            self.logger.exception(f"❌ Error syncing auto-redeem settings: {e}")

    async def sync_gift_codes_from_mongo_to_sqlite(self):
        """
        Sync gift codes from MongoDB to SQLite on startup.
        This ensures that when the bot restarts on a platform like Render (where SQLite is ephemeral),
        it doesn't treat all old codes as 'new' and incorrectly trigger auto-redeem.
        """
        try:
            if not mongo_enabled() or not GiftCodesAdapter:
                self.logger.info("⏭️ MongoDB not enabled or GiftCodesAdapter missing, skipping code sync")
                return

            self.logger.info("🔄 === SYNCING GIFT CODES FROM MONGODB TO SQLITE ===")
            
            # Fetch all codes from MongoDB
            mongo_codes = GiftCodesAdapter.get_all_with_status()
            if not mongo_codes:
                self.logger.info("ℹ️ MongoDB has no gift codes to sync")
                return
            
            self.logger.info(f"📊 Found {len(mongo_codes)} gift codes in MongoDB")
            
            # Get existing codes in SQLite to avoid duplicates/unnecessary writes
            self.cursor.execute("SELECT giftcode FROM gift_codes")
            sqlite_codes = {row[0] for row in self.cursor.fetchall()}
            
            synced_count = 0
            new_count = 0
            
            for code_data in mongo_codes:
                code = code_data.get('giftcode')
                date = code_data.get('date', '')
                validation_status = code_data.get('validation_status', 'validated')
                auto_redeem_processed = code_data.get('auto_redeem_processed', False)
                
                # Convert boolean to integer for SQLite (0/1)
                processed_int = 1 if auto_redeem_processed else 0
                
                if code in sqlite_codes:
                    # Update status of existing code if needed (e.g., if it was processed in Mongo but not SQLite)
                    self.cursor.execute(
                        "UPDATE gift_codes SET auto_redeem_processed = ? WHERE giftcode = ? AND auto_redeem_processed != ?",
                        (processed_int, str(code), processed_int)
                    )
                    if self.cursor.rowcount > 0:
                        synced_count += 1
                else:
                    # Insert new code into SQLite
                    try:
                        added_at = code_data.get('created_at') or datetime.now()
                        self.cursor.execute(
                            "INSERT INTO gift_codes (giftcode, date, validation_status, added_at, auto_redeem_processed) VALUES (?, ?, ?, ?, ?)",
                            (str(code), str(date), str(validation_status), str(added_at), processed_int)
                        )
                        new_count += 1
                    except Exception as e:
                        self.logger.error(f"❌ Failed to insert code {code} into SQLite: {e}")
            
            self.giftcode_db.commit()
            
            if new_count > 0 or synced_count > 0:
                self.logger.info(f"✅ Imported {new_count} new codes and updated {synced_count} statuses from MongoDB")
            else:
                self.logger.info("✅ SQLite already in sync with MongoDB")
                
            self.logger.info("🏁 === CODE SYNC COMPLETE ===")
            
        except Exception as e:
            self.logger.exception(f"❌ CRITICAL ERROR syncing codes from MongoDB: {e}")
    
    async def process_existing_codes_on_startup(self):
        """
        Check for existing codes that haven't been marked as processed.
        - Fetches from BOTH MongoDB (if enabled) and SQLite to ensure no codes are missed.
        - If a code is RECENT (e.g. < 24 hours), we TRIGGER auto-redeem.
        - If a code is OLD, we just MARK it as processed to avoid spam.
        """
        try:
            # Wait for bot to be ready
            await self.bot.wait_until_ready()
            
            self.logger.info("🚀 === STARTUP CODE PROCESSING ===")
            self.logger.info("Checking for unprocessed gift codes in ALL databases...")
            
            unprocessed_codes = {} # Use dict for deduplication: code -> (date, created_at)
            
            # 1. Fetch from MongoDB
            if mongo_enabled() and GiftCodesAdapter:
                try:
                    self.logger.info("📊 Fetching from MongoDB...")
                    all_codes = GiftCodesAdapter.get_all_with_status()
                    count_mongo = 0
                    if all_codes:
                        for code in all_codes:
                            if not code.get('auto_redeem_processed', False):
                                giftcode = code['giftcode']
                                unprocessed_codes[giftcode] = (code.get('date', ''), code.get('created_at'))
                                count_mongo += 1
                    self.logger.info(f"✅ MongoDB: Found {count_mongo} unprocessed codes")
                except Exception as e:
                    self.logger.warning(f"⚠️ MongoDB fetch failed: {e}")
            
            # 2. Fetch from SQLite (ALWAYS check SQLite as backup/primary source)
            try:
                self.logger.info("📂 Fetching from SQLite...")
                self.cursor.execute("""
                    SELECT giftcode, date
                    FROM gift_codes 
                    WHERE auto_redeem_processed = 0 OR auto_redeem_processed IS NULL
                    ORDER BY date DESC
                """)
                sqlite_rows = self.cursor.fetchall()
                count_sqlite = 0
                for row in sqlite_rows:
                    giftcode = row[0]
                    if giftcode not in unprocessed_codes:
                        # Use date as proxy for created_at if calling code expects 3 items
                        # But wait, we store (date_str, created_at) in unprocessed_codes dict
                        unprocessed_codes[giftcode] = (row[1], None) # None for created_at
                        count_sqlite += 1
                self.logger.info(f"✅ SQLite: Found {count_sqlite} unique unprocessed codes (not in Mongo)")
            except Exception as e:
                self.logger.error(f"❌ SQLite fetch failed: {e}")
            
            if not unprocessed_codes:
                self.logger.info("✅ No unprocessed codes found in any database.")
                self.logger.info("🏁 === STARTUP CODE PROCESSING COMPLETE ===")
                return
            
            self.logger.info(f"📋 TOTAL UNPROCESSED CODES FOUND: {len(unprocessed_codes)}")
            
            recent_codes = []
            old_codes = []
            
            now = datetime.now()
            
            for code, (date_str, created_at) in unprocessed_codes.items():
                is_recent = False
                
                # Check created_at (from MongoDB)
                if created_at:
                    created_at_dt = datetime.min
                    if isinstance(created_at, str):
                        try:
                            created_at_dt = datetime.fromisoformat(created_at)
                        except ValueError:
                            try:
                                created_at_dt = datetime.strptime(created_at, '%Y-%m-%d %H:%M:%S.%f')
                            except ValueError:
                                try:
                                    created_at_dt = datetime.strptime(created_at, '%Y-%m-%d %H:%M:%S')
                                except:
                                    pass
                    elif isinstance(created_at, datetime):
                        created_at_dt = created_at
                    
                    if created_at_dt != datetime.min:
                         age = (now - created_at_dt).total_seconds()
                         if age < 86400:
                             is_recent = True
                
                # Fallback: Check date_str (from SQLite or Mongo)
                if not is_recent and date_str:
                    try:
                        code_date = datetime.strptime(date_str, '%Y-%m-%d')
                        # If date is today or yesterday, consider it recent enough to check
                        if (now - code_date).days < 2:
                            is_recent = True
                    except:
                        pass

                if is_recent:
                    recent_codes.append((code, date_str))
                else:
                    old_codes.append((code, date_str))
            
            # 1. Process RECENT codes (Trigger Auto-Redeem)
            if recent_codes:
                self.logger.info(f"🚀 Found {len(recent_codes)} RECENT codes (within 24h) - Triggering Auto-Redeem!")
                self.logger.info(f"Codes: {[c[0] for c in recent_codes]}")
                # We do NOT mark them as processed here; trigger_auto_redeem_for_new_codes will do that
                # after dispatching tasks to all guilds.
                asyncio.create_task(self.trigger_auto_redeem_for_new_codes(recent_codes))
            
            # 2. Process OLD codes (Mark as processed only)
            if old_codes:
                self.logger.info(f"🕰️ Found {len(old_codes)} OLD codes (>24h) - Marking as processed WITHOUT redeeming.")
                self.logger.info(f"Codes: {[c[0] for c in old_codes]}")
                
                for code, _ in old_codes:
                    try:
                        # Mark in MongoDB
                        if mongo_enabled() and GiftCodesAdapter:
                            try:
                                GiftCodesAdapter.mark_code_processed(code)
                            except:
                                pass
                        
                        # Mark in SQLite
                        try:
                            self.cursor.execute(
                                "UPDATE gift_codes SET auto_redeem_processed = 1 WHERE giftcode = ?",
                                (code,)
                            )
                            self.giftcode_db.commit()
                        except:
                            pass
                    except Exception as e:
                        self.logger.error(f"Error marking old code {code}: {e}")
            
            self.logger.info("🏁 === STARTUP CODE PROCESSING COMPLETE ===")
            
        except Exception as e:
            self.logger.exception(f"❌ CRITICAL ERROR in startup auto-redeem check: {e}")

    @commands.command(name="trigger_auto_redeem")
    @commands.has_permissions(administrator=True)
    async def trigger_auto_redeem(self, ctx, code: str):
        """Manually trigger auto-redeem for a specific gift code."""
        try:
            await ctx.send(f"⏳ Manually triggering auto-redeem for code: **{code}**...")
            self.logger.info(f"🔧 Manual trigger of auto-redeem for code {code} by {ctx.author}")
            
            # Format as list of tuples [(code, date)]
            # We don't have the date handy, so just pass empty string or 'Manual'
            codes_list = [(code, "Manual Trigger")]
            
            # Call the existing method
            await self.trigger_auto_redeem_for_new_codes(codes_list)
            
            await ctx.send(f"✅ Auto-redeem process initiated for **{code}**.")
            
        except Exception as e:
            self.logger.exception(f"Error in manual trigger: {e}")
            await ctx.send(f"❌ Error triggering auto-redeem: {e}")
    
    async def notify_admins_new_codes(self, new_codes):
        """Notify global administrators about new gift codes"""
        try:
            # Get global admin IDs
            self.settings_cursor.execute("SELECT id FROM admin WHERE is_initial = 1")
            admin_ids = self.settings_cursor.fetchall()
            
            if not admin_ids:
                self.logger.info("No global admins found to notify")
                return
            
            for code, date in new_codes:
                embed = discord.Embed(
                    title="🎁 New Gift Code Detected!",
                    description=(
                        f"**Gift Code Details**\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🎁 **Code:** `{code}`\n"
                        f"📅 **Date:** `{date}`\n"
                        f"📝 **Source:** `Bot API`\n"
                        f"⏰ **Detected:** <t:{int(datetime.now().timestamp())}:R>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"\n🤖 **Auto-redeem is now active for enabled servers!**"
                    ),
                    color=discord.Color.green()
                )
                
                for admin_id in admin_ids:
                    try:
                        admin_user = await self.bot.fetch_user(admin_id[0])
                        if admin_user:
                            await admin_user.send(embed=embed)
                            self.logger.info(f"Notified admin {admin_id[0]} about new code {code}")
                    except Exception as e:
                        self.logger.error(f"Error notifying admin {admin_id[0]}: {e}")
            
            # Trigger auto-redeem for all guilds that have it enabled
            await self.trigger_auto_redeem_for_new_codes(new_codes)

            # Trigger immediate auto-posting to configured channels
            try:
                await giftcode_poster.run_now_and_report(self.bot)
                self.logger.info("🚀 Triggered immediate gift code auto-posting")
            except Exception as e:
                self.logger.error(f"Error triggering immediate auto-posting: {e}")
        
        except Exception as e:
            self.logger.exception(f"Error notifying admins: {e}")
    
    async def trigger_auto_redeem_for_new_codes(self, new_codes, is_recheck=False):
        """Trigger auto-redeem for all guilds with auto-redeem enabled"""
        try:
            self.logger.info("🔔 === TRIGGER AUTO-REDEEM ===")
            self.logger.info(f"📥 Received {len(new_codes)} codes to process: {[c[0] for c in new_codes]}")
            
            enabled_guilds = await self._get_enabled_guilds()
            if not enabled_guilds:
                self.logger.error("❌ CRITICAL: No guilds have auto-redeem enabled!")
                return

            self.logger.info(f"Triggering auto-redeem for {len(enabled_guilds)} guilds with {len(new_codes)} new codes")

            redemption_tasks = []
            for code, date in new_codes:
                task = asyncio.create_task(self._process_single_code(code, date, enabled_guilds, is_recheck))
                redemption_tasks.append(task)
            
            await asyncio.gather(*redemption_tasks, return_exceptions=True)

            self.logger.info("🏁 === TRIGGER AUTO-REDEEM COMPLETE ===")
        
        except Exception as e:
            self.logger.exception(f"Error in trigger_auto_redeem_for_new_codes: {e}")

    async def _get_enabled_guilds(self):
        """Fetch all guilds with auto-redeem enabled from BOTH MongoDB and SQLite, then deduplicate."""
        enabled_guilds_set = set()
        
        # 1. Try MongoDB
        if mongo_enabled() and AutoRedeemSettingsAdapter and hasattr(AutoRedeemSettingsAdapter, 'get_all_settings'):
            try:
                self.logger.info("📊 Checking MongoDB for enabled guilds...")
                all_settings = AutoRedeemSettingsAdapter.get_all_settings()
                if all_settings:
                    mongo_enabled_guilds = [int(s['guild_id']) for s in all_settings if s.get('enabled', False)]
                    self.logger.info(f"✅ MongoDB: Found {len(mongo_enabled_guilds)} enabled guilds")
                    for gid in mongo_enabled_guilds:
                        enabled_guilds_set.add(gid)
            except Exception as e:
                self.logger.error(f"❌ MongoDB get_all_settings failed: {e}")
        
        # 2. Try SQLite (Always check SQLite as backup/source)
        try:
            self.logger.info("📂 Checking SQLite for enabled guilds...")
            self.cursor.execute("SELECT guild_id FROM auto_redeem_settings WHERE enabled = 1")
            sqlite_rows = self.cursor.fetchall()
            self.logger.info(f"✅ SQLite: Found {len(sqlite_rows)} enabled guilds")
            for row in sqlite_rows:
                enabled_guilds_set.add(int(row[0]))
        except Exception as e:
            self.logger.error(f"❌ SQLite query failed: {e}")
        
        # Convert back to expected format: list of tuples [(gid,), ...]
        final_list = [(gid,) for gid in enabled_guilds_set]
        self.logger.info(f"📊 TOTAL UNIQUE ENABLED GUILDS: {len(final_list)}")
        return final_list

    async def _process_single_code(self, code, date, enabled_guilds, is_recheck):
        """Process a single gift code for all enabled guilds."""
        try:
            self.logger.info(f"--- START PROCESS SINGLE CODE: {code} ---")
            
            already_processed = await self._is_code_already_processed(code)
            if already_processed and not is_recheck:
                self.logger.info(f"⏭️ Skipping code {code} - already fully processed")
                return

            if is_recheck:
                self.logger.info(f"🔄 Manual Trigger: Bypassing completion status for code {code}")
                # For manual trigger, we process ALL enabled guilds, bypassing the 'completed' cache
                pending_guilds = enabled_guilds
            else:
                completed_guilds = await self._get_completed_guilds(code)
                pending_guilds = [g for g in enabled_guilds if g[0] not in completed_guilds]

            if not pending_guilds:
                self.logger.info(f"⏭️ Skipping code {code} - no pending guilds found among {len(enabled_guilds)} enabled guilds!")
                # Force mark done if it's already done by all servers
                await self._mark_code_done(code)
                return

            self.logger.info(f"🎯 Processing code {code} for {len(pending_guilds)} guilds... (Total Enabled: {len(enabled_guilds)})")
            
            for (guild_id,) in pending_guilds:
                self.auto_redeem_queue.put_nowait((guild_id, code, is_recheck))
            
            # Wait for all jobs for THIS code to complete
            await self.wait_for_code_completion(code, len(pending_guilds))

        except Exception as e:
            self.logger.exception(f"Error processing single code {code}: {e}")

    async def _is_code_already_processed(self, code):
        """Check if a code is marked as fully processed in MongoDB or SQLite."""
        if mongo_enabled() and GiftCodesAdapter:
            try:
                code_data = GiftCodesAdapter.get_code(code)
                if code_data and code_data.get('auto_redeem_processed', False):
                    return True
            except Exception as e:
                self.logger.warning(f"Failed to check code status in MongoDB: {e}")
        
        try:
            self.cursor.execute("SELECT auto_redeem_processed FROM gift_codes WHERE giftcode = ?", (code,))
            result = self.cursor.fetchone()
            return result and result[0]
        except Exception as e:
            self.logger.error(f"Failed to check code status in SQLite: {e}")
            return False

    async def _get_completed_guilds(self, code):
        """Get a set of guild IDs that have already completed redemption for a code."""
        try:
            self.cursor.execute("SELECT guild_id FROM auto_redeem_completed_guilds WHERE giftcode = ?", (code,))
            return {row[0] for row in self.cursor.fetchall()}
        except Exception as e:
            self.logger.error(f"Failed to fetch completed guilds for code {code}: {e}")
            return set()

    async def wait_for_code_completion(self, code, expected_jobs):
        """Wait until the number of completed jobs for a code reaches the expected count."""
        if expected_jobs <= 0:
            self.logger.info(f"No jobs for code {code} to wait for.")
            return

        self._pending_jobs_per_code[code] = expected_jobs
        self._completion_events[code] = asyncio.Event()

        try:
            self.logger.info(f"⌛ Waiting for {expected_jobs} jobs to complete for code {code}...")
            await asyncio.wait_for(self._completion_events[code].wait(), timeout=3600) # 1hr timeout
            self.logger.info(f"🏁 All {expected_jobs} jobs for code {code} completed. Marking as fully processed.")
            await self._mark_code_done(code)
        except asyncio.TimeoutError:
            self.logger.error(f"⌛️ Timeout waiting for code {code} to complete. It may be stuck.")
        finally:
            self._pending_jobs_per_code.pop(code, None)
            self._completion_events.pop(code, None)

    async def _decrement_pending_jobs(self, code):
        """Decrement pending jobs and set event if all jobs for a code are done."""
        if code in self._pending_jobs_per_code:
            self._pending_jobs_per_code[code] -= 1
            if self._pending_jobs_per_code[code] <= 0:
                if code in self._completion_events:
                    self._completion_events[code].set()

    async def _mark_code_done(self, code):
        """Internal helper to mark code as processed in DBs without waiting for queue."""
        # Mark in MongoDB if available
        mongo_marked = False
        if mongo_enabled() and GiftCodesAdapter:
            try:
                GiftCodesAdapter.mark_code_processed(code)
                mongo_marked = True
                self.logger.info(f"✅ Marked {code} as processed in MongoDB")
            except Exception as e:
                self.logger.error(f"❌ Failed to mark code in MongoDB: {e}")
        
        # Mark in SQLite for consistency
        sqlite_marked = False
        try:
            self.cursor.execute(
                "UPDATE gift_codes SET auto_redeem_processed = 1 WHERE giftcode = ?",
                (code,)
            )
            self.giftcode_db.commit()
            sqlite_marked = True
            self.logger.info(f"✅ Marked {code} as processed in SQLite")
        except Exception as e:
            self.logger.error(f"❌ Failed to mark code in SQLite: {e}")

    async def mark_code_invalid(self, code):
        """Mark a code as invalid/expired globally and stop processing it."""
        self.logger.warning(f"🚫 Marking code {code} as INVALID/EXPIRED globally")
        
        # Mark in MongoDB
        if mongo_enabled() and GiftCodesAdapter:
            try:
                # Use our new method!
                if hasattr(GiftCodesAdapter, 'mark_code_invalid'):
                    GiftCodesAdapter.mark_code_invalid(code)
                else:
                    GiftCodesAdapter.update_status(code, 'invalid')
                self.logger.info(f"✅ Marked {code} as invalid in MongoDB")
            except Exception as e:
                self.logger.error(f"❌ Failed to mark code invalid in MongoDB: {e}")
                
        # Mark in SQLite
        try:
            self.cursor.execute(
                "UPDATE gift_codes SET validation_status = 'invalid', auto_redeem_processed = 1 WHERE giftcode = ?",
                (code,)
            )
            self.giftcode_db.commit()
            self.logger.info(f"✅ Marked {code} as invalid in SQLite")
        except Exception as e:
            self.logger.error(f"❌ Failed to mark code invalid in SQLite: {e}")
            
        # Clear from pending jobs
        self._pending_jobs_per_code.pop(code, None)

    async def _mark_code_when_queue_empty(self, code):
        """DEPRECATED: Use _decrement_pending_jobs instead for faster updates."""
        # Keeping for compatibility but making it a no-op
        pass
        try:
            self.logger.info(f"⏳ Waiting for auto-redeem queue to finish before marking {code} as processed...")
            await self.auto_redeem_queue.join()
            
            self.logger.info(f"🏁 Queue empty! Marking code {code} as 100% processed globally.")
            
            # Mark in MongoDB if available
            mongo_marked = False
            if mongo_enabled() and GiftCodesAdapter:
                try:
                    GiftCodesAdapter.mark_code_processed(code)
                    mongo_marked = True
                    self.logger.info(f"✅ Marked {code} as processed in MongoDB")
                except Exception as e:
                    self.logger.error(f"❌ Failed to mark code in MongoDB: {e}")
            
            # Mark in SQLite for consistency
            sqlite_marked = False
            try:
                self.cursor.execute(
                    "UPDATE gift_codes SET auto_redeem_processed = 1 WHERE giftcode = ?",
                    (code,)
                )
                self.giftcode_db.commit()
                sqlite_marked = True
                self.logger.info(f"✅ Marked {code} as processed in SQLite")
            except Exception as e:
                self.logger.error(f"❌ Failed to mark code in SQLite: {e}")
                
            if not mongo_marked and not sqlite_marked:
                self.logger.error(f"❌ CRITICAL: Failed to mark {code} as processed in ANY database!")
            else:
                self.logger.info(f"🎉 Successfully marked {code} as processed (MongoDB: {mongo_marked}, SQLite: {sqlite_marked})")
                
        except Exception as e:
            self.logger.error(f"❌ Error waiting for queue to mark {code}: {e}")
    
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        """Handle gift code management interactions"""
        if not interaction.type == discord.InteractionType.component:
            return
        
        # Early exit if interaction already handled by another cog listener
        if interaction.response.is_done():
            return
        
        custom_id = interaction.data.get("custom_id", "")
        
        # Only handle custom_ids that belong to this cog.
        # All managed IDs are namespaced under 'giftcode_' or 'auto_redeem_'.
        # Do NOT add other prefixes here unless this cog's on_interaction handles them.
        check_id = custom_id.rsplit(":", 1)[0] if ":" in custom_id else custom_id
        if not (check_id.startswith("giftcode_") or check_id.startswith("auto_redeem_")):
            return
        
        # Security: Parse user_id from custom_id if present
        if custom_id and ":" in custom_id:
            try:
                real_id, user_str = custom_id.rsplit(":", 1)
                if user_str.isdigit():
                    check_user_id = int(user_str)
                    if interaction.user.id != check_user_id:
                        try:
                            await interaction.response.send_message("❌ This menu is not for you.", ephemeral=True)
                        except discord.errors.InteractionResponded:
                            pass
                        return
                    custom_id = real_id
            except ValueError:
                pass
        
        # --- NEW: Manual Trigger Catch-alls ---
        if custom_id and custom_id.startswith("auto_redeem_manual_status_"):
            if not await self.check_admin_permission(interaction.user.id):
                await interaction.response.send_message("❌ Admin only.", ephemeral=True)
                return
            code = custom_id.replace("auto_redeem_manual_status_", "")
            queue_size = self.auto_redeem_queue.qsize()
            
            # Get current job info
            current_status = "💤 Idle"
            if self.current_job:
                g_id, c_code = self.current_job
                guild = self.bot.get_guild(g_id)
                guild_name = guild.name if guild else f"ID: {g_id}"
                current_status = f"⚙️ Processing: **{guild_name}** for code `{c_code}`"
            
            embed = discord.Embed(
                title="🚀 Auto-Redeem Global Status",
                description=(
                    f"**Queued Codes:** `{code}`\n\n"
                    f"**Current Activity:** {current_status}\n"
                    f"**Servers Pending in Queue:** `{queue_size}`\n"
                    f"**Total Jobs Processed:** `{self.processed_jobs_count}`\n\n"
                    "Click 'Trigger' to force all enabled servers to process this code.\n"
                    "Click 'Refresh' to see the latest processing server."
                ),
                color=0xFF5733
            )
            self._set_embed_footer(embed, interaction.guild)
            
            view = discord.ui.View()
            view.add_item(discord.ui.Button(label="Trigger For All Enabled Servers", emoji="▶️", style=discord.ButtonStyle.danger, custom_id=f"auto_redeem_manual_trigger_{code}"))
            view.add_item(discord.ui.Button(label="Refresh Status", emoji="🔄", style=discord.ButtonStyle.secondary, custom_id=f"auto_redeem_manual_status_{code}"))
            
            try:
                await self._safe_edit_message(interaction, embed=embed, view=view)
            except discord.errors.HTTPException as e:
                if e.code == 40060: # Already acknowledged
                    await interaction.followup.edit_message(message_id=interaction.message.id, embed=embed, view=view)
                else:
                    raise
            return

        if custom_id and custom_id.startswith("auto_redeem_manual_trigger_"):
            if not await self.check_admin_permission(interaction.user.id):
                await interaction.response.send_message("❌ Admin only.", ephemeral=True)
                return
            
            try:
                await interaction.response.defer(ephemeral=True)
            except discord.errors.HTTPException as e:
                if e.code != 40060:
                    raise
            
            code = custom_id.replace("auto_redeem_manual_trigger_", "")
            
            embed = discord.Embed(
                title="🚀 Trigger Initiated",
                description=(
                    f"Triggering code `{code}` across all enabled servers.\n"
                    "This operates securely using a background queue.\n"
                    "Check the 'Refresh Status' button to monitor progress.\n"
                ),
                color=0x57F287
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            
            # trigger the background logic!
            try:
                await self.trigger_auto_redeem_for_new_codes([(code, "Manual Trigger")], is_recheck=True)
            except Exception as e:
                self.logger.exception(f"Error during manual trigger of code {code}: {e}")
            return
        # --------------------------------------

        # Handle gift code menu button
        if custom_id == "giftcode_menu":
            # Check admin permissions
            if not await self.check_admin_permission(interaction.user.id):
                await interaction.response.send_message(
                    "❌ Only administrators can access Gift Code Management.",
                    ephemeral=True
                )
                return
            
            # Create Gift Code Management submenu
            embed = discord.Embed(
                title="🎁 Gift Code Management",
                description=(
                    "```ansi\n"
                    "\u001b[2;36m╔═══════════════════════════════════╗\n"
                    "\u001b[2;36m║  \u001b[1;37mGIFT CODE OPERATIONS\u001b[0m\u001b[2;36m          ║\n"
                    "\u001b[2;36m╚═══════════════════════════════════╝\u001b[0m\n"
                    "```\n"
                    "**Manage gift codes for your server**\n\n"
                    "🔄 **Auto-Fetch:** Checking API every minute\n"
                    "📡 **API Status:** Connected\n\n"
                    "Available operations:\n"
                    "• View active gift codes\n"
                    "• Add new gift codes\n"
                    "• View gift code history\n"
                    "• Configure auto-redeem members\n"
                ),
                color=0x2B2D31
            )
            self._set_embed_footer(embed, interaction.guild)
            
            view = discord.ui.View()
            view.add_item(discord.ui.Button(
                label="View Codes",
                emoji="👁️",
                style=discord.ButtonStyle.secondary,
                custom_id="giftcode_view_codes",
                row=0
            ))
            view.add_item(discord.ui.Button(
                label="Add Code",
                emoji="➕",
                style=discord.ButtonStyle.secondary,
                custom_id="giftcode_add_code",
                row=0
            ))
            view.add_item(discord.ui.Button(
                label="Auto Redeem",
                emoji="🤖",
                style=discord.ButtonStyle.primary,
                custom_id="giftcode_auto_redeem",
                row=0
            ))
            view.add_item(discord.ui.Button(
                label="History",
                emoji="📜",
                style=discord.ButtonStyle.secondary,
                custom_id="giftcode_history",
                row=1
            ))
            view.add_item(discord.ui.Button(
                label="Refresh API",
                emoji="🔄",
                style=discord.ButtonStyle.primary,
                custom_id="giftcode_refresh_api",
                row=1
            ))
            view.add_item(discord.ui.Button(
                label="◀ Back",
                emoji="🏠",
                style=discord.ButtonStyle.secondary,
                custom_id="return_to_manage",
                row=2
            ))
            
            await self._safe_edit_message(interaction, embed=embed, view=view)
            return
        
        # Handle view codes button
        if custom_id == "giftcode_view_codes":
            if not await self.check_admin_permission(interaction.user.id):
                await interaction.response.send_message(
                    "❌ Only administrators can view gift codes.",
                    ephemeral=True
                )
                return
            
            # Get active gift codes
            try:
                # Try with all columns first
                self.cursor.execute("""
                    SELECT giftcode, 
                           COALESCE(date, 'Unknown') as date, 
                           COALESCE(validation_status, 'pending') as validation_status,
                           COALESCE(added_at, date) as added_at
                    FROM gift_codes
                    WHERE COALESCE(validation_status, 'pending') != 'invalid'
                    ORDER BY COALESCE(added_at, date, giftcode) DESC
                    LIMIT 25
                """)
            except sqlite3.OperationalError:
                # Fallback to basic query if columns don't exist
                self.cursor.execute("""
                    SELECT giftcode, 
                           COALESCE(date, 'Unknown') as date,
                           'validated' as validation_status,
                           COALESCE(date, 'Unknown') as added_at
                    FROM gift_codes
                    LIMIT 25
                """)
            codes = self.cursor.fetchall()
            
            if not codes:
                await interaction.response.send_message(
                    "📋 No active gift codes found. The API check runs every minute.",
                    ephemeral=True
                )
                return
            
            embed = discord.Embed(
                title="🎁 Active Gift Codes",
                description=f"Total Active Codes: **{len(codes)}**\n━━━━━━━━━━━━━━━━━━━━━━",
                color=0x5865F2
            )
            
            for code, date, status, added_at in codes:
                status_emoji = "✅" if status == "validated" else "⚠️"
                embed.add_field(
                    name=f"{status_emoji} {code}",
                    value=f"📅 Date: `{date[:10] if date else 'Unknown'}`\n📊 Status: `{status}`",
                    inline=True
                )
            
            if len(codes) >= 25:
                self._set_embed_footer(embed, interaction.guild)
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        # Handle history button
        if custom_id == "giftcode_history":
            if not await self.check_admin_permission(interaction.user.id):
                await interaction.response.send_message(
                    "❌ Only administrators can view gift code history.",
                    ephemeral=True
                )
                return
            
            try:
                from db.mongo_adapters import mongo_enabled, GiftCodeRedemptionAdapter
                
                if not mongo_enabled():
                    await interaction.response.send_message(
                        "📊 **Usage Tracking Unavailable**\n\n"
                        "Gift code usage tracking requires MongoDB to be enabled.\n"
                        "Contact the bot administrator to enable this feature.",
                        ephemeral=True
                    )
                    return
                
                # Get usage statistics for this server
                stats = GiftCodeRedemptionAdapter.get_all_stats(interaction.guild.id)
                
                if not stats:
                    await interaction.response.send_message(
                        "📊 **No Usage Data Yet**\n\n"
                        "No gift code redemptions have been tracked for this server yet.\n"
                        "Usage statistics will appear here after codes are redeemed via auto-redeem.",
                        ephemeral=True
                    )
                    return
                # Build embed with statistics
                embed = discord.Embed(
                    title="📊 Gift Code Usage History",
                    description=f"Usage statistics for **{interaction.guild.name}**\n━━━━━━━━━━━━━━━━━━━━━━",
                    color=0x5865F2
                )
                
                # Show top 15 codes
                for idx, code_stats in enumerate(stats[:15], 1):
                    code = code_stats['code']
                    unique_users = code_stats['unique_users']
                    success = code_stats['success']
                    failed = code_stats['failed']
                    total = code_stats['total_attempts']
                    
                    # Calculate success rate
                    success_rate = (success / total * 100) if total > 0 else 0
                    
                    value_text = (
                        f"👥 **{unique_users}** players redeemed\n"
                        f"✅ Success: {success} | ❌ Failed: {failed}\n"
                        f"📊 Success Rate: {success_rate:.1f}%"
                    )
                    
                    embed.add_field(
                        name=f"{idx}. 🎁 {code}",
                        value=value_text,
                        inline=True
                    )
                
                if len(stats) > 15:
                    self._set_embed_footer(embed, interaction.guild)
                else:
                    self._set_embed_footer(embed, interaction.guild)
                
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
                
            except Exception as e:
                self.logger.error(f"Error displaying gift code history: {e}")
                import traceback
                traceback.print_exc()
                await interaction.response.send_message(
                    "❌ An error occurred while loading gift code history.",
                    ephemeral=True
                )
                return
        
        # Handle add code button
        if custom_id == "giftcode_add_code":
            if not await self.check_admin_permission(interaction.user.id):
                await interaction.response.send_message(
                    "❌ Only administrators can add gift codes.",
                    ephemeral=True
                )
                return
            
            class AddCodeModal(discord.ui.Modal, title="Add Gift Code"):
                code = discord.ui.TextInput(
                    label="Gift Code",
                    placeholder="Enter the gift code",
                    required=True,
                    max_length=50
                )
                
                def __init__(self, cog):
                    super().__init__()
                    self.cog = cog
                
                async def on_submit(self, modal_interaction: discord.Interaction):
                    try:
                        gift_code = self.code.value.strip().upper()
                        
                        # Send initial processing message
                        processing_embed = discord.Embed(
                            title="🔄 Validating Gift Code",
                            description=f"Checking `{gift_code}` with API...",
                            color=discord.Color.blue()
                        )
                        await modal_interaction.response.send_message(embed=processing_embed, ephemeral=True)
                        
                        # Check if code already exists in database
                        self.cog.cursor.execute("SELECT validation_status FROM gift_codes WHERE giftcode = ?", (gift_code,))
                        existing = self.cog.cursor.fetchone()
                        
                        if existing:
                            status = existing[0] if existing[0] else 'unknown'
                            error_embed = discord.Embed(
                                title="ℹ️ Code Already Exists",
                                description=f"Gift code `{gift_code}` is already in the database.\n\n**Status:** `{status}`",
                                color=discord.Color.orange()
                            )
                            await modal_interaction.edit_original_response(embed=error_embed)
                            return
                        
                        # Check if code exists in API
                        exists_in_api = await self.cog.check_giftcode_in_api(gift_code)
                        
                        if exists_in_api:
                            # Code exists in API, add it to our database
                            self.cog.cursor.execute("""
                                INSERT INTO gift_codes (giftcode, date, validation_status, added_by, added_at)
                                VALUES (?, ?, ?, ?, ?)
                            """, (gift_code, datetime.now().strftime("%Y-%m-%d"), "validated", modal_interaction.user.id, datetime.now()))
                            self.cog.giftcode_db.commit()
                            
                            success_embed = discord.Embed(
                                title="✅ Gift Code Added",
                                description=(
                                    f"Successfully added gift code: `{gift_code}`\n\n"
                                    f"**Status:** Validated via API\n"
                                    f"**Added by:** {modal_interaction.user.mention}\n"
                                    f"**Date:** {datetime.now().strftime('%Y-%m-%d')}\n\n"
                                    f"🚀 **Auto-redeem initiated globally!**"
                                ),
                                color=discord.Color.green()
                            )
                            await modal_interaction.edit_original_response(embed=success_embed)
                            # Trigger auto-redeem
                            asyncio.create_task(self.cog.trigger_auto_redeem_for_new_codes([(gift_code, datetime.now().strftime("%Y-%m-%d"))]))
                        else:
                            # Code doesn't exist in API, try to add it
                            success = await self.cog.add_giftcode_to_api(gift_code)
                            
                            if success:
                                # Successfully added to API, now add to database
                                self.cog.cursor.execute("""
                                    INSERT INTO gift_codes (giftcode, date, validation_status, added_by, added_at)
                                    VALUES (?, ?, ?, ?, ?)
                                """, (gift_code, datetime.now().strftime("%Y-%m-%d"), "validated", modal_interaction.user.id, datetime.now()))
                                self.cog.giftcode_db.commit()
                                
                                success_embed = discord.Embed(
                                    title="✅ Gift Code Added",
                                    description=(
                                        f"Successfully added new gift code: `{gift_code}`\n\n"
                                        f"**Status:** Added to API and Database\n"
                                        f"**Added by:** {modal_interaction.user.mention}\n"
                                        f"**Date:** {datetime.now().strftime('%Y-%m-%d')}\n\n"
                                        f"🚀 **Auto-redeem initiated globally!**"
                                    ),
                                    color=discord.Color.green()
                                )
                                await modal_interaction.edit_original_response(embed=success_embed)
                                # Trigger auto-redeem
                                asyncio.create_task(self.cog.trigger_auto_redeem_for_new_codes([(gift_code, datetime.now().strftime("%Y-%m-%d"))]))
                            else:
                                # API rejected it. Try GAME API verification
                                try:
                                    await modal_interaction.edit_original_response(
                                        embed=discord.Embed(
                                            title="🔄 Verifying with Game API",
                                            description=f"External API check failed. Attempting to verify `{gift_code}` directly with Game API...",
                                            color=discord.Color.blue()
                                        )
                                    )
                                    
                                    is_valid, status, fid_used = await self.cog.verify_code_with_game_api(modal_interaction.guild.id, gift_code)
                                    
                                    if is_valid:
                                        self.cog.logger.info(f"Game API verified code {gift_code} as valid ({status})")
                                        
                                        # Add to Database as validated
                                        self.cog.cursor.execute("""
                                            INSERT INTO gift_codes (giftcode, date, validation_status, added_by, added_at)
                                            VALUES (?, ?, ?, ?, ?)
                                        """, (gift_code, datetime.now().strftime("%Y-%m-%d"), "validated", modal_interaction.user.id, datetime.now()))
                                        self.cog.giftcode_db.commit()
                                        
                                        success_embed = discord.Embed(
                                            title="✅ Gift Code Verified & Added",
                                            description=(
                                                f"Successfully verified and added: `{gift_code}`\n\n"
                                                f"**Status:** Validated via Game API ({status})\n"
                                                f"**Verified with:** FID {fid_used}\n"
                                                f"**Added by:** {modal_interaction.user.mention}\n"
                                                f"**Date:** {datetime.now().strftime('%Y-%m-%d')}\n\n"
                                                f"🚀 **Auto-redeem initiated globally!**"
                                            ),
                                            color=discord.Color.green()
                                        )
                                        await modal_interaction.edit_original_response(embed=success_embed)
                                        # Trigger auto-redeem
                                        asyncio.create_task(self.cog.trigger_auto_redeem_for_new_codes([(gift_code, datetime.now().strftime("%Y-%m-%d"))]))
                                        
                                    elif status in ["INVALID_CODE", "EXPIRED", "USAGE_LIMIT_REACHED", "EXPIRED_CODE"]:
                                        # Definitively invalid
                                        error_embed = discord.Embed(
                                            title="❌ Invalid Gift Code",
                                            description=(
                                                f"Gift code `{gift_code}` was rejected by the Game API.\n\n"
                                                f"**Reason:** {status}\n"
                                                f"**Verified with:** FID {fid_used if fid_used else 'N/A'}"
                                            ),
                                            color=discord.Color.red()
                                        )
                                        await modal_interaction.edit_original_response(embed=error_embed)
                                        
                                    else:
                                        # Verification failed or unknown error - Fallback to Force Add
                                        self.cog.logger.warning(f"Force adding code {gift_code} despite API rejection (Verification status: {status})")
                                        
                                        # Add to Database
                                        self.cog.cursor.execute("""
                                            INSERT INTO gift_codes (giftcode, date, validation_status, added_by, added_at)
                                            VALUES (?, ?, ?, ?, ?)
                                        """, (gift_code, datetime.now().strftime("%Y-%m-%d"), "forced", modal_interaction.user.id, datetime.now()))
                                        self.cog.giftcode_db.commit()
                                        
                                        success_embed = discord.Embed(
                                            title="⚠️ Gift Code Force Added",
                                            description=(
                                                f"Added gift code: `{gift_code}`\n\n"
                                                f"**Status:** Forced\n"
                                                f"**Game Verification:** Failed ({status})\n"
                                                f"**Added by:** {modal_interaction.user.mention}\n"
                                                f"**Date:** {datetime.now().strftime('%Y-%m-%d')}\n"
                                                f"**Note:** API rejected it and Game Verification failed, but added anyway.\n\n"
                                                f"🚀 **Auto-redeem initiated globally!**"
                                            ),
                                            color=discord.Color.orange()
                                        )
                                        await modal_interaction.edit_original_response(embed=success_embed)
                                        # Trigger auto-redeem
                                        asyncio.create_task(self.cog.trigger_auto_redeem_for_new_codes([(gift_code, datetime.now().strftime("%Y-%m-%d"))]))
                                except Exception as e:
                                    self.cog.logger.error(f"Error during verification/force add: {e}")
                                    # Fallback to force add on error
                                    self.cog.cursor.execute("""
                                        INSERT INTO gift_codes (giftcode, date, validation_status, added_by, added_at)
                                        VALUES (?, ?, ?, ?, ?)
                                    """, (gift_code, datetime.now().strftime("%Y-%m-%d"), "forced", modal_interaction.user.id, datetime.now()))
                                    self.cog.giftcode_db.commit()
                                    
                                    success_embed = discord.Embed(
                                        title="⚠️ Gift Code Force Added (Error)",
                                        description=(
                                            f"Added gift code: `{gift_code}`\n\n"
                                            f"**Status:** Forced (Error during verification)\n"
                                            f"**Error:** {str(e)}\n"
                                            f"**Added by:** {modal_interaction.user.mention}\n\n"
                                            f"🚀 **Auto-redeem initiated globally!**"
                                        ),
                                        color=discord.Color.orange()
                                    )
                                    await modal_interaction.edit_original_response(embed=success_embed)
                                    # Trigger auto-redeem
                                    asyncio.create_task(self.cog.trigger_auto_redeem_for_new_codes([(gift_code, datetime.now().strftime("%Y-%m-%d"))]))
                        
                    except Exception as e:
                        self.cog.logger.exception(f"Error adding gift code: {e}")
                        error_embed = discord.Embed(
                            title="❌ Error",
                            description=f"An error occurred while adding the gift code:\n```{str(e)}```",
                            color=discord.Color.red()
                        )
                        try:
                            await modal_interaction.edit_original_response(embed=error_embed)
                        except:
                            await modal_interaction.followup.send(embed=error_embed, ephemeral=True)
            
            await interaction.response.send_modal(AddCodeModal(self))
            return
        
        # Handle history button
        if custom_id == "giftcode_history":
            if not await self.check_admin_permission(interaction.user.id):
                await interaction.response.send_message(
                    "❌ Only administrators can view gift code history.",
                    ephemeral=True
                )
                return
            
            # Get all gift codes
            try:
                self.cursor.execute("""
                    SELECT giftcode, 
                           COALESCE(date, 'Unknown') as date, 
                           COALESCE(validation_status, 'pending') as validation_status,
                           COALESCE(added_at, date) as added_at
                    FROM gift_codes
                    ORDER BY COALESCE(added_at, date, giftcode) DESC
                    LIMIT 25
                """)
            except sqlite3.OperationalError:
                # Fallback query
                self.cursor.execute("""
                    SELECT giftcode, 
                           COALESCE(date, 'Unknown') as date,
                           'validated' as validation_status,
                           COALESCE(date, 'Unknown') as added_at
                    FROM gift_codes
                    LIMIT 25
                """)
            codes = self.cursor.fetchall()
            
            if not codes:
                await interaction.response.send_message(
                    "📋 No gift code history found.",
                    ephemeral=True
                )
                return
            
            embed = discord.Embed(
                title="📜 Gift Code History",
                description=f"Showing last **{len(codes)}** gift codes\n━━━━━━━━━━━━━━━━━━━━━━",
                color=0x5865F2
            )
            
            for code, date, status, added_at in codes:
                status_emoji = "✅" if status == "validated" else "❌" if status == "invalid" else "⚠️"
                embed.add_field(
                    name=f"{status_emoji} {code}",
                    value=f"📅 Date: `{date[:10] if date else 'Unknown'}`\n📊 Status: `{status}`",
                    inline=True
                )
            
            if len(codes) >= 25:
                self._set_embed_footer(embed, interaction.guild)
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        # Handle refresh API button
        if custom_id == "giftcode_refresh_api":
            if not await self.check_admin_permission(interaction.user.id):
                await interaction.response.send_message(
                    "❌ Only administrators can refresh the API.",
                    ephemeral=True
                )
                return
            
            await interaction.response.defer(ephemeral=True)
            
            # Manually trigger API check
            try:
                api_codes = await self.fetch_codes_from_api()
                
                if not api_codes:
                    await interaction.followup.send(
                        "⚠️ No codes fetched from API. The API might be down or empty.",
                        ephemeral=True
                    )
                    return
                
                # Get existing codes
                self.cursor.execute("SELECT giftcode FROM gift_codes")
                db_codes = {row[0] for row in self.cursor.fetchall()}
                
                # Find new codes
                new_codes = [code for code, date in api_codes if code not in db_codes]
                
                embed = discord.Embed(
                    title="🔄 API Refresh Complete",
                    description=(
                        f"**Results:**\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"📡 **Total API Codes:** `{len(api_codes)}`\n"
                        f"💾 **Already in DB:** `{len(api_codes) - len(new_codes)}`\n"
                        f"✨ **New Codes Found:** `{len(new_codes)}`\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━"
                    ),
                    color=0x57F287 if new_codes else 0x5865F2
                )
                
                if new_codes:
                    embed.add_field(
                        name="🎁 New Codes",
                        value="\n".join([f"`{code}`" for code in new_codes[:10]]),
                        inline=False
                    )
                    if len(new_codes) > 10:
                        self._set_embed_footer(embed, interaction.guild)
                
                await interaction.followup.send(embed=embed, ephemeral=True)
                
            except Exception as e:
                await interaction.followup.send(
                    f"❌ Error refreshing API: {str(e)}",
                    ephemeral=True
                )
            return
        
        # Handle auto redeem button
        if custom_id == "giftcode_auto_redeem":
            if not await self.check_admin_permission(interaction.user.id):
                await interaction.response.send_message(
                    "❌ Only administrators can manage auto-redeem settings.",
                    ephemeral=True
                )
                return
            
            # Get current member count
            members = self.AutoRedeemDB.get_members(self, interaction.guild.id)
            member_count = len(members)
            
            embed = discord.Embed(
                title="🤖 Auto Redeem Management",
                description=(
                    "```ansi\n"
                    "\u001b[2;36m╔═══════════════════════════════════╗\n"
                    "\u001b[2;36m║  \u001b[1;37mAUTO REDEEM MEMBERS\u001b[0m\u001b[2;36m            ║\n"
                    "\u001b[2;36m╚═══════════════════════════════════╝\u001b[0m\n"
                    "```\n"
                    "**Configure automatic gift code redemption**\n\n"
                    f"👥 **Current Members:** `{member_count}`\n"
                    "🔄 **Status:** Active\n\n"
                    "**How it works:**\n"
                    "• Add members by FID or import from alliance\n"
                    "• New gift codes auto-redeem for all members\n"
                    "• Import from FID channel\n"
                ),
                color=0x2B2D31
            )
            self._set_embed_footer(embed, interaction.guild)
            
            view = discord.ui.View()
            view.add_item(discord.ui.Button(
                label="Add Member",
                emoji="➕",
                style=discord.ButtonStyle.success,
                custom_id="auto_redeem_add_member",
                row=0
            ))
            view.add_item(discord.ui.Button(
                label="Remove Member",
                emoji="➖",
                style=discord.ButtonStyle.danger,
                custom_id="auto_redeem_remove_member",
                row=0
            ))
            view.add_item(discord.ui.Button(
                label="View Members",
                emoji="👁️",
                style=discord.ButtonStyle.secondary,
                custom_id="auto_redeem_view_members",
                row=0
            ))
            view.add_item(discord.ui.Button(
                label="Configure Auto Redeem",
                emoji="⚙️",
                style=discord.ButtonStyle.secondary,
                custom_id="giftcode_configure_auto_redeem",
                row=1
            ))
            view.add_item(discord.ui.Button(
                label="Manual Trigger & Status",
                emoji="🚀",
                style=discord.ButtonStyle.primary,
                custom_id="auto_redeem_trigger_menu",
                row=1
            ))
            view.add_item(discord.ui.Button(
                label="◀ Back",
                emoji="🏠",
                style=discord.ButtonStyle.secondary,
                custom_id="giftcode_menu",
                row=2
            ))
            
            await self._safe_edit_message(interaction, embed=embed, view=view)
            return

        # Handle manual trigger menu
        if custom_id == "auto_redeem_trigger_menu":
            if not await self.check_admin_permission(interaction.user.id):
                await interaction.response.send_message(
                    "❌ Only administrators can trigger auto-redeem.",
                    ephemeral=True
                )
                return
            
            await interaction.response.defer(ephemeral=True)
            
            try:
                # Fetch active codes using the unified logic
                active_codes_map = await self.get_active_gift_codes_consolidated()
                valid_active_codes = list(active_codes_map.keys())
                
                all_codes = []
                if valid_active_codes:
                    # Get processed status from database
                    _mongo_enabled = globals().get('mongo_enabled', lambda: False)
                    db_status_map = {}
                    
                    if _mongo_enabled() and GiftCodesAdapter:
                        try:
                            mongo_codes = GiftCodesAdapter.get_all_with_status()
                            if mongo_codes:
                                for code_data in mongo_codes:
                                    c = str(code_data.get('giftcode', ''))
                                    proc = code_data.get('auto_redeem_processed', False)
                                    date_added = code_data.get('date', '')
                                    db_status_map[c] = (proc, date_added)
                        except Exception as e:
                            self.logger.warning(f"Failed to fetch from Mongo for trigger menu: {e}")
                    
                    if not db_status_map:
                        try:
                            self.cursor.execute("SELECT giftcode, auto_redeem_processed, date FROM gift_codes")
                            for row in self.cursor.fetchall():
                                db_status_map[row[0]] = (bool(row[1]), str(row[2]))
                        except Exception as e:
                            self.logger.error(f"SQLite fetch failed for trigger menu: {e}")
                    
                    # Build final list
                    for code_str in valid_active_codes:
                        processed = False
                        date_str = active_codes_map[code_str]
                        if code_str in db_status_map:
                            processed, db_date = db_status_map[code_str]
                            if db_date and db_date.strip() and date_str == 'Unknown':
                                date_str = db_date
                        
                        all_codes.append((code_str, date_str, processed))
                
                if not all_codes:
                    await interaction.followup.send("📋 No active gift codes found at the moment.", ephemeral=True)
                    return
                
                recent_codes = all_codes[:25]
                
                class CodeTriggerSelectView(discord.ui.View):
                    def __init__(self, codes_list, cog_instance):
                        super().__init__(timeout=300)
                        self.codes = codes_list
                        self.cog = cog_instance
                        
                        options = []
                        for code, date, processed in self.codes:
                            status_emoji = "✅" if processed else "⏳"
                            options.append(
                                discord.SelectOption(
                                    label=f"{code[:50]}",
                                    description=f"{'Processed' if processed else 'Unprocessed'} • {date if date else 'No date'}",
                                    value=f"trigger_select_{code}",
                                    emoji=status_emoji
                                )
                            )
                        
                        select = discord.ui.Select(
                            placeholder="Select a code to view or trigger...",
                            options=options,
                            custom_id="code_to_trigger_select"
                        )
                        select.callback = self.select_code
                        self.add_item(select)
                        
                        # Add Refresh button
                        refresh_api_btn = discord.ui.Button(
                            label="Refresh Codes (Bypass Cache)",
                            emoji="🔄",
                            style=discord.ButtonStyle.primary,
                            custom_id="auto_redeem_refresh_api"
                        )
                        self.add_item(refresh_api_btn)
                    
                    async def select_code(self, select_interaction: discord.Interaction):
                        await select_interaction.response.defer(ephemeral=True)
                        selected_code = select_interaction.data["values"][0].replace("trigger_select_", "")
                        
                        # Calculate queue size accurately
                        queue_size = self.cog.auto_redeem_queue.qsize()
                        
                        embed = discord.Embed(
                            title="🚀 Manual Auto-Redeem Trigger",
                            description=(
                                f"**Code:** `{selected_code}`\n\n"
                                f"**Global Queue Size:** `{queue_size}` servers pending\n\n"
                                "Click the button below to force all enabled servers to process this code right now.\n"
                                "Members who already redeemed it successfully will be safely skipped."
                            ),
                            color=0xFF5733
                        )
                        self._set_embed_footer(embed, interaction.guild)
                        
                        view = discord.ui.View()
                        
                        trigger_btn = discord.ui.Button(
                            label="Trigger For All Enabled Servers",
                            emoji="▶️",
                            style=discord.ButtonStyle.danger,
                            custom_id=f"auto_redeem_manual_trigger_{selected_code}"
                        )
                        view.add_item(trigger_btn)
                        
                        refresh_btn = discord.ui.Button(
                            label="Refresh Status",
                            emoji="🔄",
                            style=discord.ButtonStyle.secondary,
                            custom_id=f"auto_redeem_manual_status_{selected_code}"
                        )
                        view.add_item(refresh_btn)
                        
                        await select_interaction.followup.send(embed=embed, view=view, ephemeral=True)
                
                view = CodeTriggerSelectView(recent_codes, self)
                
                embed = discord.Embed(
                    title="🚀 Manual Code Trigger",
                    description=(
                        f"**Showing {len(recent_codes)} most recent codes.**\n\n"
                        "Select a code below to view its status and forcefully trigger auto-redeem across all servers.\n\n"
                        "*Note: Manually triggering is safe. It will skip users who have already redeemed the code successfully.*"
                    ),
                    color=0xFF5733
                )
                
                await interaction.followup.send(embed=embed, view=view, ephemeral=True)
                
            except Exception as e:
                self.logger.exception(f"Error in manual trigger menu: {e}")
                await interaction.followup.send(f"❌ An error occurred: {str(e)}", ephemeral=True)
            return

        # Handle Refresh API button in trigger menu
        if custom_id == "auto_redeem_refresh_api":
            if not await self.check_admin_permission(interaction.user.id):
                await interaction.response.send_message("❌ Only administrators can refresh codes.", ephemeral=True)
                return
            
            await interaction.response.defer(ephemeral=True)
            try:
                # Force refresh from API
                await self.get_active_gift_codes_consolidated(force_refresh=True)
                # Re-trigger the menu handler to show fresh data
                interaction.data['custom_id'] = "auto_redeem_trigger_menu"
                # Re-call this same callback with modified custom_id? 
                # Better: just re-run the menu logic by calling the handler again if possible, 
                # or just send a success message.
                await interaction.followup.send("✅ Code list refreshed from API sources!", ephemeral=True)
                # Actually, let's just trigger the menu again to show it
                # We can't easily re-invoke the complex logic without duplicating or refactoring.
                # For now, a success message and asking them to reopen is simplest, 
                # OR I can just duplicate the menu logic here.
            except Exception as e:
                self.logger.error(f"Error refreshing API: {e}")
                await interaction.followup.send(f"❌ Failed to refresh API: {e}", ephemeral=True)
            return

        # Handle add member button - show submenu
        if custom_id == "auto_redeem_add_member":
            if not await self.check_admin_permission(interaction.user.id):
                await interaction.response.send_message(
                    "❌ Only administrators can add members.",
                    ephemeral=True
                )
                return
            
            # Get current member count
            members = self.AutoRedeemDB.get_members(self, interaction.guild.id)
            member_count = len(members)
            
            embed = discord.Embed(
                title="➕ Add Member to Auto-Redeem",
                description=(
                    "```ansi\n"
                    "\u001b[2;36m╔═══════════════════════════════════╗\n"
                    "\u001b[2;36m║  \u001b[1;37mADD MEMBER OPTIONS\u001b[0m\u001b[2;36m             ║\n"
                    "\u001b[2;36m╚═══════════════════════════════════╝\u001b[0m\n"
                    "```\n"
                    "**Choose how to add members:**\n\n"
                    f"👥 **Current Members:** `{member_count}`\n\n"
                    "**📝 Add via FID**\n"
                    "   ▸ Manually enter player FID and nickname\n"
                    "   ▸ Add one member at a time\n\n"
                    "**🤖 Import from Alliance**\n"
                    "   ▸ Import members from alliance list\n"
                    "   ▸ Multi-select interface\n\n"
                    "**📺 Auto Register**\n"
                    "   ▸ Monitor a channel for FID codes\n"
                    "   ▸ Auto-add players who post their FID\n"
                ),
                color=0x2B2D31
            )
            self._set_embed_footer(embed, interaction.guild)
            
            view = discord.ui.View()
            view.add_item(discord.ui.Button(
                label="Add via FID",
                emoji="📝",
                style=discord.ButtonStyle.success,
                custom_id="auto_redeem_add_via_fid",
                row=0
            ))
            view.add_item(discord.ui.Button(
                label="Import from Alliance",
                emoji="🤖",
                style=discord.ButtonStyle.primary,
                custom_id="auto_redeem_auto_register",
                row=0
            ))
            view.add_item(discord.ui.Button(
                label="Auto Register",
                emoji="📺",
                style=discord.ButtonStyle.primary,
                custom_id="auto_redeem_import_from_channel",
                row=0
            ))
            view.add_item(discord.ui.Button(
                label="◀ Back",
                emoji="🏠",
                style=discord.ButtonStyle.secondary,
                custom_id="giftcode_auto_redeem",
                row=1
            ))
            
            await self._safe_edit_message(interaction, embed=embed, view=view)
            return
        
        # Handle add via FID button - show modal with WOS API integration
        if custom_id == "auto_redeem_add_via_fid":
            if not await self.check_admin_permission(interaction.user.id):
                await interaction.response.send_message(
                    "❌ Only administrators can add members.",
                    ephemeral=True
                )
                return
            
            class AddMemberModal(discord.ui.Modal, title="Add Member to Auto-Redeem"):
                fids_input = discord.ui.TextInput(
                    label="Player ID(s)",
                    placeholder="123456789 or 123456789, 987654321 or 123456789 PlayerName",
                    required=True,
                    style=discord.TextStyle.paragraph,
                    max_length=1000
                )
                
                def __init__(self, cog):
                    super().__init__()
                    self.cog = cog
                
                async def on_submit(self, modal_interaction: discord.Interaction):
                    try:
                        input_text = self.fids_input.value.strip()
                        
                        # Parse FIDs (support multiple formats)
                        fid_entries = []
                        for line in input_text.replace(',', '\n').split('\n'):
                            line = line.strip()
                            if not line:
                                continue
                            
                            parts = line.split(None, 1)  # Split on first whitespace
                            fid = parts[0]
                            nickname = parts[1] if len(parts) > 1 else None
                            
                            if fid.isdigit():
                                fid_entries.append((fid, nickname))
                        
                        if not fid_entries:
                            await modal_interaction.response.send_message(
                                "❌ No valid FIDs found. Please enter numeric FIDs.",
                                ephemeral=True
                            )
                            return
                        
                        # Processing animation
                        processing_embed = discord.Embed(
                            title="➕ Adding Members to Auto-Redeem",
                            description=f"Processing **{len(fid_entries)}** member(s)...\n\n```\nFetching player data from WOS API...\n```",
                            color=0x5865F2
                        )
                        await modal_interaction.response.send_message(embed=processing_embed, ephemeral=True)
                        
                        # Process each FID
                        results = []
                        success_count = 0
                        fail_count = 0
                        
                        for fid, custom_nickname in fid_entries:
                            # Check if already exists
                            if self.cog.AutoRedeemDB.member_exists(self.cog, modal_interaction.guild.id, fid):
                                results.append(f"❌ Already exists: `{fid}`")
                                fail_count += 1
                                continue
                            
                            # Fetch player data from WOS API
                            player_data = await self.cog.fetch_player_data(fid)
                            
                            if player_data:
                                # Use custom nickname if provided, otherwise use API nickname
                                if custom_nickname:
                                    player_data['nickname'] = custom_nickname
                                
                                player_data['added_by'] = modal_interaction.user.id
                                
                                # Add to database
                                success = self.cog.AutoRedeemDB.add_member(
                                    self.cog,
                                    modal_interaction.guild.id,
                                    fid,
                                    player_data
                                )
                                
                                if success:
                                    furnace_lv = player_data.get('furnace_lv', 0)
                                    results.append(f"✅ **{player_data['nickname']}** (FC {furnace_lv}) - `{fid}`")
                                    success_count += 1
                                else:
                                    results.append(f"❌ Failed to add: `{fid}`")
                                    fail_count += 1
                            else:
                                results.append(f"❌ Invalid FID or API error: `{fid}`")
                                fail_count += 1
                        
                        # Final result
                        result_embed = discord.Embed(
                            title="➕ Add Members - Complete",
                            description=f"**Results:** {success_count} added, {fail_count} failed\n━━━━━━━━━━━━━━━━━━━━━━",
                            color=0x57F287 if success_count > 0 else 0xED4245
                        )
                        
                        results_text = "\n".join(results[:20])
                        if results_text:
                            result_embed.add_field(name="📋 Details", value=results_text, inline=False)
                        
                        if len(results) > 20:
                            result_self._set_embed_footer(embed, interaction.guild)
                        
                        await modal_interaction.edit_original_response(embed=result_embed)
                    
                    except Exception as e:
                        self.cog.logger.exception(f"Error adding members: {e}")
                        error_embed = discord.Embed(
                            title="❌ Error",
                            description=f"An error occurred while adding members:\n```{str(e)}```",
                            color=discord.Color.red()
                        )
                        try:
                            await modal_interaction.edit_original_response(embed=error_embed)
                        except:
                            await modal_interaction.followup.send(embed=error_embed, ephemeral=True)
            
            await interaction.response.send_modal(AddMemberModal(self))
            return
        
        # Handle remove member button - show submenu
        if custom_id == "auto_redeem_remove_member":
            if not await self.check_admin_permission(interaction.user.id):
                await interaction.response.send_message(
                    "❌ Only administrators can remove members.",
                    ephemeral=True
                )
                return
            
            # Get current member count
            members = self.AutoRedeemDB.get_members(self, interaction.guild.id)
            member_count = len(members)
            
            embed = discord.Embed(
                title="➖ Remove Member from Auto-Redeem",
                description=(
                    "```ansi\n"
                    "\u001b[2;36m╔═══════════════════════════════════╗\n"
                    "\u001b[2;36m║  \u001b[1;37mREMOVE MEMBER OPTIONS\u001b[0m\u001b[2;36m          ║\n"
                    "\u001b[2;36m╚═══════════════════════════════════╝\u001b[0m\n"
                    "```\n"
                    "**Choose how to remove members:**\n\n"
                    f"👥 **Current Members:** `{member_count}`\n\n"
                    "**📝 Remove via FID**\n"
                    "   ▸ Manually enter player FID(s) to remove\n"
                    "   ▸ Support single or multiple FIDs\n\n"
                    "**🗑️ Bulk Remove from List**\n"
                    "   ▸ Select members from current auto-redeem list\n"
                    "   ▸ Multi-select interface for bulk removal\n"
                ),
                color=0x2B2D31
            )
            self._set_embed_footer(embed, interaction.guild)
            
            view = discord.ui.View()
            view.add_item(discord.ui.Button(
                label="Remove via FID",
                emoji="📝",
                style=discord.ButtonStyle.success,
                custom_id="auto_redeem_remove_via_fid",
                row=0
            ))
            view.add_item(discord.ui.Button(
                label="Bulk Remove from List",
                emoji="🗑️",
                style=discord.ButtonStyle.primary,
                custom_id="auto_redeem_bulk_remove",
                row=0
            ))
            view.add_item(discord.ui.Button(
                label="◀ Back",
                emoji="🏠",
                style=discord.ButtonStyle.secondary,
                custom_id="giftcode_auto_redeem",
                row=1
            ))
            
            await self._safe_edit_message(interaction, embed=embed, view=view)
            return
        
        # Handle remove via FID button
        if custom_id == "auto_redeem_remove_via_fid":
            if not await self.check_admin_permission(interaction.user.id):
                await interaction.response.send_message(
                    "❌ Only administrators can remove members.",
                    ephemeral=True
                )
                return
            
            class RemoveMemberModal(discord.ui.Modal, title="Remove Members"):
                fids_input = discord.ui.TextInput(
                    label="Player ID(s)",
                    placeholder="123456789 or 123456789, 987654321",
                    required=True,
                    style=discord.TextStyle.paragraph,
                    max_length=1000
                )
                
                def __init__(self, cog):
                    super().__init__()
                    self.cog = cog
                
                async def on_submit(self, modal_interaction: discord.Interaction):
                    try:
                        input_text = self.fids_input.value.strip()
                        
                        # Parse FIDs
                        fids = []
                        for line in input_text.replace(',', '\n').split('\n'):
                            fid = line.strip().split()[0] if line.strip() else ""
                            if fid.isdigit():
                                fids.append(fid)
                        
                        if not fids:
                            await modal_interaction.response.send_message(
                                "❌ No valid FIDs found.",
                                ephemeral=True
                            )
                            return
                        
                        # Processing animation
                        processing_embed = discord.Embed(
                            title="➖ Removing Members",
                            description=f"Processing **{len(fids)}** member(s)...\\n\\n```\\nPlease wait...\\n```",
                            color=0xED4245
                        )
                        await modal_interaction.response.send_message(embed=processing_embed, ephemeral=True)
                        
                        results = []
                        success_count = 0
                        fail_count = 0
                        
                        for fid in fids:
                            # Get member data
                            members = await self.cog.AutoRedeemDB.get_members_async(self.cog, modal_interaction.guild.id)
                            member = next((m for m in members if m.get('fid') == fid), None)
                            
                            if not member:
                                results.append(f"❌ Not found: `{fid}`")
                                fail_count += 1
                                continue
                                
                            success = await self.cog.AutoRedeemDB.remove_member_async(
                                self.cog,
                                modal_interaction.guild.id,
                                fid
                            )
                            
                            if success:
                                results.append(f"✅ Removed: **{member.get('nickname', 'Unknown')}** (`{fid}`)")
                                success_count += 1
                            else:
                                results.append(f"❌ Failed to remove: `{fid}`")
                                fail_count += 1
                        
                        # Final result
                        result_embed = discord.Embed(
                            title="➖ Remove Members - Complete",
                            description=f"**Results:** {success_count} removed, {fail_count} failed\\n━━━━━━━━━━━━━━━━━━━━━━",
                            color=0x57F287 if success_count > 0 else 0xED4245
                        )
                        
                        results_text = "\\n".join(results[:20])
                        if results_text:
                            result_embed.add_field(name="📋 Details", value=results_text, inline=False)
                        
                        if len(results) > 20:
                            result_self._set_embed_footer(embed, interaction.guild)
                        
                        await modal_interaction.edit_original_response(embed=result_embed)
                        
                    except Exception as e:
                        self.cog.logger.exception(f"Error removing members: {e}")
                        await modal_interaction.edit_original_response(content=f"❌ Error: {str(e)}")

            await interaction.response.send_modal(RemoveMemberModal(self))
            return
        
        # Handle view members button
        if custom_id == "auto_redeem_view_members":
            if not await self.check_admin_permission(interaction.user.id):
                await interaction.response.send_message(
                    "❌ Only administrators can view auto-redeem members.",
                    ephemeral=True
                )
                return
            
            # Get all auto-redeem members for this guild
            members = await self.AutoRedeemDB.get_members_async(self, interaction.guild.id)
            
            if not members:
                await interaction.response.send_message(
                    "📋 **No Members in Auto-Redeem List**\n\n"
                    "Add members using the **Add Member** button to get started!",
                    ephemeral=True
                )
                return
            
            # Pagination settings
            members_per_page = 15
            total_pages = (len(members) + members_per_page - 1) // members_per_page
            
            # Create paginated view
            class MemberPaginationView(discord.ui.View):
                def __init__(self, members_list, cog_instance):
                    super().__init__(timeout=180)
                    self.members = members_list
                    self.cog = cog_instance
                    self.current_page = 0
                    self.update_buttons()
                
                def get_embed(self):
                    start_idx = self.current_page * members_per_page
                    end_idx = min(start_idx + members_per_page, len(self.members))
                    page_members = self.members[start_idx:end_idx]
                    
                    embed = discord.Embed(
                        title="👥 Auto-Redeem Members",
                        description=f"**Total Members:** `{len(self.members)}`\n**Page {self.current_page + 1} of {total_pages}**\n━━━━━━━━━━━━━━━━━━━━━━",
                        color=0x5865F2
                    )
                    
                    for idx, member in enumerate(page_members, start=start_idx + 1):
                        fid = member.get('fid', 'Unknown')
                        nickname = member.get('nickname', 'Unknown')
                        furnace_lv = member.get('furnace_lv', 0)
                        formatted_fc = self.cog.format_furnace_level(furnace_lv)
                        safe_nick = str(nickname)[:50] if nickname else "Unknown"
                        
                        embed.add_field(
                            name=f"{idx}. {safe_nick}",

                            value=f"🆔 `{fid}`\n⚔️ {formatted_fc}",
                            inline=True
                        )
                    
                    return embed
                
                def update_buttons(self):
                    self.clear_items()
                    
                    # Previous button
                    prev_btn = discord.ui.Button(
                        label="◀ Previous",
                        style=discord.ButtonStyle.secondary,
                        disabled=(self.current_page == 0)
                    )
                    prev_btn.callback = self.previous_page
                    self.add_item(prev_btn)
                    
                    # Page indicator
                    page_btn = discord.ui.Button(
                        label=f"Page {self.current_page + 1}/{total_pages}",
                        style=discord.ButtonStyle.primary,
                        disabled=True
                    )
                    self.add_item(page_btn)
                    
                    # Next button
                    next_btn = discord.ui.Button(
                        label="Next ▶",
                        style=discord.ButtonStyle.secondary,
                        disabled=(self.current_page >= total_pages - 1)
                    )
                    next_btn.callback = self.next_page
                    self.add_item(next_btn)
                
                async def previous_page(self, button_interaction: discord.Interaction):
                    if self.current_page > 0:
                        self.current_page -= 1
                        self.update_buttons()
                        await button_interaction.response.edit_message(embed=self.get_embed(), view=self)
                
                async def next_page(self, button_interaction: discord.Interaction):
                    if self.current_page < total_pages - 1:
                        self.current_page += 1
                        self.update_buttons()
                        await button_interaction.response.edit_message(embed=self.get_embed(), view=self)
            
            view = MemberPaginationView(members, self)
            await interaction.response.send_message(embed=view.get_embed(), view=view, ephemeral=True)
            return
        
        # Handle configure auto redeem button
        if custom_id == "giftcode_configure_auto_redeem":
            if not await self.check_admin_permission(interaction.user.id):
                await interaction.response.send_message(
                    "❌ Only administrators can configure auto-redeem settings.",
                    ephemeral=True
                )
                return
            
            # Get current settings - MongoDB first, SQLite fallback
            enabled = 0
            
            # Try MongoDB first
            try:
                use_mongo = mongo_enabled() and AutoRedeemSettingsAdapter
            except (NameError, UnboundLocalError):
                use_mongo = False
            
            if use_mongo:
                try:
                    mongo_settings = await AutoRedeemSettingsAdapter.get_settings_async(interaction.guild.id)
                    if mongo_settings:
                        enabled = 1 if mongo_settings.get('enabled', False) else 0
                    else:
                        # No MongoDB settings, check SQLite
                        self.cursor.execute("""
                            SELECT enabled
                            FROM auto_redeem_settings 
                            WHERE guild_id = ?
                        """, (interaction.guild.id,))
                        settings = self.cursor.fetchone()
                        if settings:
                            enabled = settings[0]
                except Exception as e:
                    self.logger.error(f"Failed to load auto redeem settings from MongoDB: {e}")
                    # Fall back to SQLite
                    self.cursor.execute("""
                        SELECT enabled
                        FROM auto_redeem_settings 
                        WHERE guild_id = ?
                    """, (interaction.guild.id,))
                    settings = self.cursor.fetchone()
                    if settings:
                        enabled = settings[0]
            else:
                # MongoDB not available, use SQLite
                self.cursor.execute("""
                    SELECT enabled
                    FROM auto_redeem_settings 
                    WHERE guild_id = ?
                """, (interaction.guild.id,))
                settings = self.cursor.fetchone()
                if settings:
                    enabled = settings[0]
            
            # Get member count
            members = await self.AutoRedeemDB.get_members_async(self, interaction.guild.id)
            member_count = len(members)
            
            # Get configured channel for FID monitoring - MongoDB first, SQLite fallback
            channel_name = "Not configured"
            channel_id = None
            
            # Try MongoDB first
            try:
                use_mongo = mongo_enabled() and AutoRedeemChannelsAdapter
            except (NameError, UnboundLocalError):
                use_mongo = False
            
            if use_mongo:
                try:
                    mongo_channel = await AutoRedeemChannelsAdapter.get_channel_async(interaction.guild.id)
                    if mongo_channel:
                        channel_id = mongo_channel.get('channel_id')
                    else:
                        # No MongoDB channel, check SQLite
                        self.cursor.execute("""
                            SELECT channel_id 
                            FROM auto_redeem_channels 
                            WHERE guild_id = ?
                        """, (interaction.guild.id,))
                        channel_result = self.cursor.fetchone()
                        if channel_result:
                            channel_id = channel_result[0]
                except Exception as e:
                    self.logger.error(f"Failed to load auto redeem channel from MongoDB: {e}")
                    # Fall back to SQLite
                    self.cursor.execute("""
                        SELECT channel_id 
                        FROM auto_redeem_channels 
                        WHERE guild_id = ?
                    """, (interaction.guild.id,))
                    channel_result = self.cursor.fetchone()
                    if channel_result:
                        channel_id = channel_result[0]
            else:
                # MongoDB not available, use SQLite
                self.cursor.execute("""
                    SELECT channel_id 
                    FROM auto_redeem_channels 
                    WHERE guild_id = ?
                """, (interaction.guild.id,))
                channel_result = self.cursor.fetchone()
                if channel_result:
                    channel_id = channel_result[0]
            
            # Get channel name if we have an ID
            if channel_id:
                channel = interaction.guild.get_channel(channel_id)
                if channel:
                    channel_name = f"#{channel.name}"
            
            embed = discord.Embed(
                title="⚙️ Auto-Redeem Configuration",
                description=(
                    "```ansi\n"
                    "\u001b[2;36m╔═══════════════════════════════════╗\n"
                    "\u001b[2;36m║  \u001b[1;37mAUTO-REDEEM SETTINGS\u001b[0m\u001b[2;36m           ║\n"
                    "\u001b[2;36m╚═══════════════════════════════════╝\u001b[0m\n"
                    "```\n"
                    "**Current Configuration:**\n\n"
                    f"🔘 **Status:** {'🟢 Enabled' if enabled else '🔴 Disabled'}\n"
                    f"👥 **Members:** `{member_count}`\n"
                    f"📢 **FID Monitor Channel:** {channel_name}\n\n"
                    "**Features:**\n"
                    "• Automatically redeem new gift codes\n"
                    "• Monitor channel for FID codes\n"
                    "• Track redemption success/failure\n"
                ),
                color=0x57F287 if enabled else 0xED4245
            )
            self._set_embed_footer(embed, interaction.guild)
            
            view = discord.ui.View()
            
            # Toggle enable/disable button
            if enabled:
                view.add_item(discord.ui.Button(
                    label="Disable Auto-Redeem",
                    emoji="🔴",
                    style=discord.ButtonStyle.danger,
                    custom_id="auto_redeem_disable",
                    row=0
                ))
            else:
                view.add_item(discord.ui.Button(
                    label="Enable Auto-Redeem",
                    emoji="🟢",
                    style=discord.ButtonStyle.success,
                    custom_id="auto_redeem_enable",
                    row=0
                ))
            
            # Set FID monitor channel button
            view.add_item(discord.ui.Button(
                label="Set FID Monitor Channel",
                emoji="📢",
                style=discord.ButtonStyle.primary,
                custom_id="auto_redeem_import_from_channel",
                row=0
            ))
            
            # Reset code status button (for testing)
            view.add_item(discord.ui.Button(
                label="Reset Code Status",
                emoji="🔄",
                style=discord.ButtonStyle.secondary,
                custom_id="auto_redeem_reset_code",
                row=1
            ))
            
            # Delete code button
            view.add_item(discord.ui.Button(
                label="Delete Code",
                emoji="🗑️",
                style=discord.ButtonStyle.danger,
                custom_id="auto_redeem_delete_code",
                row=1
            ))
            
            # Back button
            view.add_item(discord.ui.Button(
                label="◀ Back",
                emoji="🏠",
                style=discord.ButtonStyle.secondary,
                custom_id="giftcode_auto_redeem",
                row=2
            ))
            
            await self._safe_edit_message(interaction, embed=embed, view=view)
            return

        # Handle bulk remove button
        if custom_id == "auto_redeem_bulk_remove":
            if not await self.check_admin_permission(interaction.user.id):
                await interaction.response.send_message(
                    "❌ Only administrators can remove members.",
                    ephemeral=True
                )
                return
            
            await interaction.response.defer(ephemeral=True)
            
            try:
                # Get all members
                members = await self.AutoRedeemDB.get_members_async(self, interaction.guild.id)
                
                if not members:
                    await interaction.followup.send(
                        "📋 No members in auto-redeem list to remove.",
                        ephemeral=True
                    )
                    return
                
                # Create multi-select view (similar to AllianceMemberSelectView but for removal)
                class BulkRemoveSelectView(discord.ui.View):
                    def __init__(self, members_data, cog_instance, guild_id):
                        super().__init__(timeout=None)
                        self.members = members_data
                        self.cog = cog_instance
                        self.guild_id = guild_id
                        self.selected_fids = set()
                        self.current_page = 0
                        self.members_per_page = 20
                        # update_components() removed from init as it's now async
                    
                    def get_total_pages(self):
                        return (len(self.members) - 1) // self.members_per_page + 1
                    
                    async def update_components(self):
                        self.clear_items()
                        
                        # Get members for current page
                        start_idx = self.current_page * self.members_per_page
                        end_idx = start_idx + self.members_per_page
                        page_members = self.members[start_idx:end_idx]
                        
                        # Create select menu
                        options = []
                        for member in page_members:
                            fid = member.get('fid', '')
                            nickname = member.get('nickname', 'Unknown')
                            furnace_lv = member.get('furnace_lv', 0)
                            formatted_fc = self.cog.format_furnace_level(furnace_lv)
                            safe_nick = str(nickname)[:40] if nickname else "Unknown"
                            
                            options.append(
                                discord.SelectOption(
                                    label=f"{safe_nick} (FC {formatted_fc})",
                                    description=f"FID: {fid}",
                                    value=fid,
                                    emoji="✅" if fid in self.selected_fids else "🗑️",
                                    default=(fid in self.selected_fids)
                                )
                            )
                        
                        if options:
                            select = discord.ui.Select(
                                placeholder=f"Select members to remove ({len(self.selected_fids)} selected)",
                                options=options,
                                min_values=0,
                                max_values=len(options)
                            )
                            select.callback = self.member_select
                            self.add_item(select)
                        
                        # Select All / Deselect All buttons
                        if len(self.selected_fids) < len(self.members):
                            select_all_btn = discord.ui.Button(
                                label="Select All",
                                style=discord.ButtonStyle.primary,
                                emoji="☑️"
                            )
                            select_all_btn.callback = self.select_all_callback
                            self.add_item(select_all_btn)
                        
                        if self.selected_fids:
                            deselect_all_btn = discord.ui.Button(
                                label="Deselect All",
                                style=discord.ButtonStyle.secondary,
                                emoji="⬜"
                            )
                            deselect_all_btn.callback = self.deselect_all_callback
                            self.add_item(deselect_all_btn)
                        
                        # Pagination buttons
                        if self.current_page > 0:
                            prev_btn = discord.ui.Button(label="◀ Previous", style=discord.ButtonStyle.secondary)
                            prev_btn.callback = self.previous_page
                            self.add_item(prev_btn)
                        
                        if self.current_page < self.get_total_pages() - 1:
                            next_btn = discord.ui.Button(label="Next ▶", style=discord.ButtonStyle.secondary)
                            next_btn.callback = self.next_page
                            self.add_item(next_btn)
                        
                        # Remove selected button
                        if self.selected_fids:
                            remove_btn = discord.ui.Button(
                                label=f"Remove Selected ({len(self.selected_fids)})",
                                style=discord.ButtonStyle.danger,
                                emoji="🗑️"
                            )
                            remove_btn.callback = self.remove_selected_members
                            self.add_item(remove_btn)
                    
                    async def member_select(self, select_interaction: discord.Interaction):
                        selected_values = set(select_interaction.data['values'])
                        
                        # Get all FIDs on current page
                        start_idx = self.current_page * self.members_per_page
                        end_idx = start_idx + self.members_per_page
                        page_fids = {m.get('fid') for m in self.members[start_idx:end_idx]}
                        
                        # Remove deselected from current page
                        self.selected_fids = (self.selected_fids - page_fids) | selected_values
                        
                        await self.update_components()
                        await select_interaction.response.edit_message(
                            content=f"**Selected {len(self.selected_fids)} member(s)** - Choose more or click 'Remove Selected'",
                            view=self
                        )
                    
                    async def previous_page(self, btn_interaction: discord.Interaction):
                        if self.current_page > 0:
                            self.current_page -= 1
                            await self.update_components()
                            await btn_interaction.response.edit_message(view=self)
                    
                    async def next_page(self, btn_interaction: discord.Interaction):
                        if self.current_page < self.get_total_pages() - 1:
                            self.current_page += 1
                            await self.update_components()
                            await btn_interaction.response.edit_message(view=self)
                    
                    async def select_all_callback(self, btn_interaction: discord.Interaction):
                        """Select all members for removal"""
                        for member in self.members:
                            fid = member.get('fid')
                            if fid:
                                self.selected_fids.add(fid)
                        
                        await self.update_components()
                        await btn_interaction.response.edit_message(
                            content=f"**✅ Selected all {len(self.selected_fids)} member(s)** - Click 'Remove Selected' to remove them",
                            view=self
                        )
                    
                    async def deselect_all_callback(self, btn_interaction: discord.Interaction):
                        """Deselect all members"""
                        self.selected_fids.clear()
                        await self.update_components()
                        await btn_interaction.response.edit_message(
                            content=f"**Deselected all members** - Select members to remove from auto-redeem list",
                            view=self
                        )
                    
                    async def remove_selected_members(self, remove_interaction: discord.Interaction):
                        if not self.selected_fids:
                            await remove_interaction.response.send_message("❌ No members selected.", ephemeral=True)
                            return
                        
                        # Processing animation
                        processing_embed = discord.Embed(
                            title="➖ Removing Members",
                            description=f"Removing **{len(self.selected_fids)}** member(s)...\n\n```\nPlease wait...\n```",
                            color=0xED4245
                        )
                        await remove_interaction.response.send_message(embed=processing_embed, ephemeral=True)
                        
                        results = []
                        success_count = 0
                        fail_count = 0
                        
                        for fid in self.selected_fids:
                            member = next((m for m in self.members if m.get('fid') == fid), None)
                            nickname = member.get('nickname', 'Unknown') if member else "Unknown"
                            
                            success = await self.cog.AutoRedeemDB.remove_member_async(
                                self.cog,
                                self.guild_id,
                                fid
                            )
                            
                            if success:
                                results.append(f"✅ Removed: **{nickname}** (`{fid}`)")
                                success_count += 1
                            else:
                                results.append(f"❌ Failed: `{fid}`")
                                fail_count += 1
                        
                        # Final result
                        result_embed = discord.Embed(
                            title="➖ Bulk Remove - Complete",
                            description=f"**Results:** {success_count} removed, {fail_count} failed\n━━━━━━━━━━━━━━━━━━━━━━",
                            color=0x57F287 if success_count > 0 else 0xED4245
                        )
                        
                        results_text = "\n".join(results[:20])
                        if results_text:
                            result_embed.add_field(name="📋 Details", value=results_text, inline=False)
                        
                        if len(results) > 20:
                            result_self._set_embed_footer(embed, interaction.guild)
                        
                        await remove_interaction.edit_original_response(embed=result_embed)

                view = BulkRemoveSelectView(members, self, interaction.guild.id)
                await view.update_components()
                await interaction.followup.send(
                    f"**Select members to remove from auto-redeem list:**\n\nTotal members: {len(members)}",
                    view=view,
                    ephemeral=True
                )
                
            except Exception as e:
                self.logger.exception(f"Error in bulk remove: {e}")
                await interaction.followup.send(
                    f"❌ An error occurred: {str(e)}",
                    ephemeral=True
                )
            return
        
        # Handle view members button
        if custom_id == "auto_redeem_view_members":
            if not await self.check_admin_permission(interaction.user.id):
                await interaction.response.send_message(
                    "❌ Only administrators can view members.",
                    ephemeral=True
                )
                return
            
            # Get all members using MongoDB-first helper
            members_data = await self.AutoRedeemDB.get_members_async(self, interaction.guild.id)
            
            # Convert to tuple format for compatibility with existing view code
            members = [
                (m['fid'], m['nickname'], m.get('added_at'), m.get('furnace_lv', 0))
                for m in members_data
            ]
            
            if not members:
                await interaction.response.send_message(
                    "📋 No members in auto-redeem list.\n\nUse **Add Member** to add players.",
                    ephemeral=True
                )
                return
            
            # Create paginated view
            class MemberListView(discord.ui.View):
                def __init__(self, members_data, cog_instance):
                    super().__init__(timeout=None)
                    self.members = members_data
                    self.cog = cog_instance
                    self.current_page = 0
                    self.members_per_page = 15
                    self.update_buttons()
                
                def get_total_pages(self):
                    return (len(self.members) - 1) // self.members_per_page + 1
                
                def get_embed(self):
                    start_idx = self.current_page * self.members_per_page
                    end_idx = start_idx + self.members_per_page
                    page_members = self.members[start_idx:end_idx]
                    
                    embed = discord.Embed(
                        title="👥 Auto-Redeem Members",
                        description=f"Total Members: **{len(self.members)}** | Page {self.current_page + 1}/{self.get_total_pages()}\n━━━━━━━━━━━━━━━━━━━━━━",
                        color=0x5865F2
                    )
                    
                    for fid, nickname, added_at, furnace_lv in page_members:
                        formatted_fc = self.cog.format_furnace_level(furnace_lv)
                        furnace_text = f" (FC {formatted_fc})" if furnace_lv else ""
                        safe_nick = str(nickname)[:50] if nickname else "Unknown"
                        embed.add_field(
                            name=f"👤 {safe_nick}{furnace_text}",
                            value=f"**FID:** `{fid}`\n**Added:** <t:{int(datetime.fromisoformat(str(added_at)).timestamp())}:R>",
                            inline=True
                        )
                    
                    return embed
                
                def update_buttons(self):
                    self.clear_items()
                    
                    # Previous button
                    prev_btn = discord.ui.Button(
                        label="",
                        emoji="⬅️",
                        style=discord.ButtonStyle.primary,
                        disabled=(self.current_page == 0)
                    )
                    prev_btn.callback = self.previous_page
                    self.add_item(prev_btn)
                    
                    # Next button
                    next_btn = discord.ui.Button(
                        label="",
                        emoji="➡️",
                        style=discord.ButtonStyle.primary,
                        disabled=(self.current_page >= self.get_total_pages() - 1)
                    )
                    next_btn.callback = self.next_page
                    self.add_item(next_btn)
                
                async def previous_page(self, button_interaction: discord.Interaction):
                    if self.current_page > 0:
                        self.current_page -= 1
                        self.update_buttons()
                        await button_interaction.response.edit_message(embed=self.get_embed(), view=self)
                
                async def next_page(self, button_interaction: discord.Interaction):
                    if self.current_page < self.get_total_pages() - 1:
                        self.current_page += 1
                        self.update_buttons()
                        await button_interaction.response.edit_message(embed=self.get_embed(), view=self)
            
            view = MemberListView(members, self)
            await interaction.response.send_message(embed=view.get_embed(), view=view, ephemeral=True)
            return
        
        # Handle auto register button - WITH MULTI-SELECT
        if custom_id == "auto_redeem_auto_register":
            if not await self.check_admin_permission(interaction.user.id):
                await interaction.response.send_message(
                    "❌ Only administrators can import members.",
                    ephemeral=True
                )
                return
            
            try:
                # Defer the interaction to prevent timeout
                await interaction.response.defer(ephemeral=True)
            except discord.errors.NotFound:
                self.logger.error("Interaction expired before defer")
                return
            except Exception as e:
                self.logger.error(f"Error deferring interaction: {e}")
                return
            
            try:
                # Get alliance members from MongoDB - filtered by server's assigned alliance
                from db.mongo_adapters import AllianceMembersAdapter, ServerAllianceAdapter
                
                # Get server's assigned alliance
                alliance_id = await asyncio.to_thread(ServerAllianceAdapter.get_alliance, interaction.guild.id)
                
                if not alliance_id:
                    await interaction.followup.send(
                        "❌ No alliance assigned to this server.\n\nPlease assign an alliance first using the alliance management features.",
                        ephemeral=True
                    )
                    return
                
                # Get all members and filter by assigned alliance
                all_members = await AllianceMembersAdapter.get_all_members_async()
                members = [m for m in all_members if int(m.get('alliance', 0) or m.get('alliance_id', 0)) == alliance_id]
                
                if not members:
                    await interaction.followup.send(
                        "❌ No members found in your assigned alliance.\n\nPlease ensure alliance monitoring is active and members have been synced.",
                        ephemeral=True
                    )
                    return
                
                # Create multi-select view
                class AllianceMemberSelectView(discord.ui.View):
                    def __init__(self, members_data, cog_instance, guild_id):
                        super().__init__(timeout=300)  # 5 minute timeout
                        self.members = members_data
                        self.cog = cog_instance
                        self.guild_id = guild_id
                        self.selected_fids = set()
                        self.current_page = 0
                        self.members_per_page = 20
                        # update_components() removed as it is now async
                    
                    async def on_timeout(self):
                        """Handle view timeout"""
                        try:
                            for item in self.children:
                                item.disabled = True
                            # Note: We can't edit the message here as we don't have the message reference
                        except Exception as e:
                            self.cog.logger.error(f"Error in view timeout: {e}")
                    
                    def get_total_pages(self):
                        return (len(self.members) - 1) // self.members_per_page + 1
                    
                    async def update_components(self):
                        self.clear_items()
                        
                        # Get members for current page
                        start_idx = self.current_page * self.members_per_page
                        end_idx = start_idx + self.members_per_page
                        page_members = self.members[start_idx:end_idx]
                        
                        # BATCH CHECK: Get all FIDs and check existence in one query
                        from db.mongo_adapters import AutoRedeemMembersAdapter
                        all_fids = [m.get('fid') for m in self.members if m.get('fid')]
                        existing_fids_map = await AutoRedeemMembersAdapter.batch_member_exists_async(self.guild_id, all_fids)
                        
                        # Create select menu
                        options = []
                        for member in page_members:
                            fid = member.get('fid', '')
                            nickname = member.get('nickname', 'Unknown')
                            furnace_lv = member.get('furnace_lv', 0)
                            formatted_fc = self.cog.format_furnace_level(furnace_lv)
                            
                            # Check if already in auto-redeem list (from batch result)
                            already_added = existing_fids_map.get(fid, False)
                            
                            options.append(
                                discord.SelectOption(
                                    label=f"{nickname} (FC {formatted_fc})",
                                    description=f"FID: {fid}" + (" - Already added" if already_added else ""),
                                    value=fid,
                                    emoji="✅" if fid in self.selected_fids else "👤",
                                    default=(fid in self.selected_fids)
                                )
                            )
                        
                        if options:
                            select = discord.ui.Select(
                                placeholder=f"Select members to add ({len(self.selected_fids)} selected)",
                                options=options,
                                min_values=0,
                                max_values=len(options)
                            )
                            select.callback = self.member_select
                            self.add_item(select)
                        
                        # Select All / Deselect All buttons
                        # Count total members with valid FIDs (including already added)
                        total_selectable = sum(1 for m in self.members if m.get('fid'))
                        
                        if len(self.selected_fids) < total_selectable:
                            select_all_btn = discord.ui.Button(
                                label="Select All",
                                style=discord.ButtonStyle.primary,
                                emoji="☑️"
                            )
                            select_all_btn.callback = self.select_all_callback
                            self.add_item(select_all_btn)
                        
                        if self.selected_fids:
                            deselect_all_btn = discord.ui.Button(
                                label="Deselect All",
                                style=discord.ButtonStyle.secondary,
                                emoji="⬜"
                            )
                            deselect_all_btn.callback = self.deselect_all_callback
                            self.add_item(deselect_all_btn)
                        
                        # Pagination buttons
                        if self.current_page > 0:
                            prev_btn = discord.ui.Button(label="◀ Previous", style=discord.ButtonStyle.secondary)
                            prev_btn.callback = self.previous_page
                            self.add_item(prev_btn)
                        
                        if self.current_page < self.get_total_pages() - 1:
                            next_btn = discord.ui.Button(label="Next ▶", style=discord.ButtonStyle.secondary)
                            next_btn.callback = self.next_page
                            self.add_item(next_btn)
                        
                        # Add selected button
                        if self.selected_fids:
                            add_btn = discord.ui.Button(
                                label=f"Add Selected ({len(self.selected_fids)})",
                                style=discord.ButtonStyle.success,
                                emoji="➕"
                            )
                            add_btn.callback = self.add_selected_members
                            self.add_item(add_btn)
                    
                    async def member_select(self, select_interaction: discord.Interaction):
                        try:
                            # Toggle selected FIDs
                            selected_values = set(select_interaction.data['values'])
                            
                            # Get all FIDs on current page
                            start_idx = self.current_page * self.members_per_page
                            end_idx = start_idx + self.members_per_page
                            page_fids = {m.get('fid') for m in self.members[start_idx:end_idx]}
                            
                            # Remove deselected from current page
                            self.selected_fids = (self.selected_fids - page_fids) | selected_values
                            
                            await self.update_components()
                            await select_interaction.response.edit_message(
                                content=f"**Selected {len(self.selected_fids)} member(s)** - Choose more or click 'Add Selected'",
                                view=self
                            )
                        except Exception as e:
                            self.cog.logger.exception(f"Error in member_select: {e}")
                            try:
                                await select_interaction.response.send_message(
                                    f"❌ An error occurred: {str(e)}",
                                    ephemeral=True
                                )
                            except:
                                pass
                    
                    async def previous_page(self, btn_interaction: discord.Interaction):
                        try:
                            if self.current_page > 0:
                                self.current_page -= 1
                                await self.update_components()
                                await btn_interaction.response.edit_message(
                                    content=f"**Selected {len(self.selected_fids)} member(s)** - Choose more or click 'Add Selected'",
                                    view=self
                                )
                        except Exception as e:
                            self.cog.logger.exception(f"Error in previous_page: {e}")
                    
                    async def next_page(self, btn_interaction: discord.Interaction):
                        try:
                            if self.current_page < self.get_total_pages() - 1:
                                self.current_page += 1
                                await self.update_components()
                                await btn_interaction.response.edit_message(
                                    content=f"**Selected {len(self.selected_fids)} member(s)** - Choose more or click 'Add Selected'",
                                    view=self
                                )
                        except Exception as e:
                            self.cog.logger.exception(f"Error in next_page: {e}")
                    
                    async def select_all_callback(self, btn_interaction: discord.Interaction):
                        """Select all members (including those already in auto-redeem list)"""
                        try:
                            # Select ALL members with valid FIDs
                            for member in self.members:
                                fid = member.get('fid')
                                if fid:  # Only add if FID is not None/empty
                                    self.selected_fids.add(fid)
                            
                            await self.update_components()
                            await btn_interaction.response.edit_message(
                                content=f"**✅ Selected all {len(self.selected_fids)} member(s)** - Click 'Add Selected' to import (existing members will be skipped)",
                                view=self
                            )
                        except Exception as e:
                            self.cog.logger.exception(f"Error in select_all_callback: {e}")
                    
                    async def deselect_all_callback(self, btn_interaction: discord.Interaction):
                        """Deselect all members"""
                        try:
                            self.selected_fids.clear()
                            await self.update_components()
                            await btn_interaction.response.edit_message(
                                content=f"**Deselected all members** - Select members to add to auto-redeem list",
                                view=self
                            )
                        except Exception as e:
                            self.cog.logger.exception(f"Error in deselect_all_callback: {e}")
                    
                    async def add_selected_members(self, add_interaction: discord.Interaction):
                        try:
                            if not self.selected_fids:
                                await add_interaction.response.send_message("❌ No members selected.", ephemeral=True)
                                return
                            
                            # Processing animation
                            processing_embed = discord.Embed(
                                title="➕ Adding Members to Auto-Redeem",
                                description=f"Adding **{len(self.selected_fids)}** member(s)...\n\n```\nPlease wait...\n```",
                                color=0x5865F2
                            )
                            await add_interaction.response.send_message(embed=processing_embed, ephemeral=True)
                            
                            results = []
                            success_count = 0
                            fail_count = 0
                            
                            for fid in self.selected_fids:
                                # Find member data
                                member = next((m for m in self.members if m.get('fid') == fid), None)
                                if member:
                                    member_data = {
                                        'nickname': member.get('nickname', 'Unknown'),
                                        'furnace_lv': int(member.get('furnace_lv', 0) or 0),
                                        'avatar_image': member.get('avatar_image', ''),
                                        'added_by': add_interaction.user.id
                                    }
                                    
                                    success = await self.cog.AutoRedeemDB.add_member_async(
                                        self.cog,
                                        self.guild_id,
                                        fid,
                                        member_data
                                    )
                                    
                                    if success:
                                        results.append(f"✅ **{member_data['nickname']}** (`{fid}`)")
                                        success_count += 1
                                    else:
                                        results.append(f"❌ Already exists: `{fid}`")
                                        fail_count += 1
                            
                            # Create paginated result view
                            class ResultPaginationView(discord.ui.View):
                                def __init__(self, results_list, success_cnt, fail_cnt):
                                    super().__init__(timeout=180)
                                    self.results = results_list
                                    self.success_count = success_cnt
                                    self.fail_count = fail_cnt
                                    self.current_page = 0
                                    self.items_per_page = 20
                                    self.update_buttons()
                                
                                def get_total_pages(self):
                                    return (len(self.results) - 1) // self.items_per_page + 1 if self.results else 1
                                
                                def get_embed(self):
                                    start_idx = self.current_page * self.items_per_page
                                    end_idx = start_idx + self.items_per_page
                                    page_results = self.results[start_idx:end_idx]
                                    
                                    embed = discord.Embed(
                                        title="➕ Import from Alliance - Complete",
                                        description=f"**Results:** {self.success_count} added, {self.fail_count} failed\n━━━━━━━━━━━━━━━━━━━━━━",
                                        color=0x57F287 if self.success_count > 0 else 0xED4245
                                    )
                                    
                                    if page_results:
                                        results_text = "\n".join(page_results)
                                        embed.add_field(name="📋 Details", value=results_text, inline=False)
                                    
                                    if len(self.results) > self.items_per_page:
                                        self._set_embed_footer(embed, interaction.guild)
                                    
                                    return embed
                                
                                def update_buttons(self):
                                    self.clear_items()
                                    
                                    if self.get_total_pages() > 1:
                                        # Previous button
                                        prev_btn = discord.ui.Button(
                                            label="",
                                            emoji="⬅️",
                                            style=discord.ButtonStyle.primary,
                                            disabled=(self.current_page == 0)
                                        )
                                        prev_btn.callback = self.previous_page
                                        self.add_item(prev_btn)
                                        
                                        # Next button
                                        next_btn = discord.ui.Button(
                                            label="",
                                            emoji="➡️",
                                            style=discord.ButtonStyle.primary,
                                            disabled=(self.current_page >= self.get_total_pages() - 1)
                                        )
                                        next_btn.callback = self.next_page
                                        self.add_item(next_btn)
                                
                                async def previous_page(self, button_interaction: discord.Interaction):
                                    if self.current_page > 0:
                                        self.current_page -= 1
                                        self.update_buttons()
                                        await button_interaction.response.edit_message(embed=self.get_embed(), view=self)
                                
                                async def next_page(self, button_interaction: discord.Interaction):
                                    if self.current_page < self.get_total_pages() - 1:
                                        self.current_page += 1
                                        self.update_buttons()
                                        await button_interaction.response.edit_message(embed=self.get_embed(), view=self)
                            
                            # Show paginated results
                            result_view = ResultPaginationView(results, success_count, fail_count)
                            await add_interaction.edit_original_response(embed=result_view.get_embed(), view=result_view)
                        except Exception as e:
                            self.cog.logger.exception(f"Error in add_selected_members: {e}")
                            try:
                                await add_interaction.response.send_message(
                                    f"❌ An error occurred while adding members: {str(e)}",
                                    ephemeral=True
                                )
                            except:
                                try:
                                    await add_interaction.followup.send(
                                        f"❌ An error occurred while adding members: {str(e)}",
                                        ephemeral=True
                                    )
                                except:
                                    pass
                
                # Show member selection
                member_view = AllianceMemberSelectView(members, self, interaction.guild.id)
                await member_view.update_components()
                await interaction.followup.send(
                    f"**Select members to add to auto-redeem list:**\n\nTotal alliance members: {len(members)}",
                    view=member_view,
                    ephemeral=True
                )
                
            except discord.errors.NotFound:
                self.logger.error("Interaction expired during auto register")
            except Exception as e:
                self.logger.exception(f"Error in auto register: {e}")
                try:
                    await interaction.followup.send(
                        f"❌ An error occurred: {str(e)}\n\nPlease try again or contact an administrator if the issue persists.",
                        ephemeral=True
                    )
                except:
                    self.logger.error("Could not send error message to user")
            return
        
        # Handle import from channel button
        if custom_id == "auto_redeem_import_from_channel":
            # Import at method level to avoid scope issues
            from db.mongo_adapters import mongo_enabled, AutoRedeemChannelsAdapter
            
            if not await self.check_admin_permission(interaction.user.id):
                await interaction.response.send_message(
                    "❌ Only administrators can configure channel monitoring.",
                    ephemeral=True
                )
                return
            
            # Check if channel is already configured - try MongoDB first
            existing_channel_id = None
            
            if mongo_enabled() and AutoRedeemChannelsAdapter:
                try:
                    channel_config = AutoRedeemChannelsAdapter.get_channel(interaction.guild.id)
                    if channel_config:
                        existing_channel_id = channel_config.get('channel_id')
                except Exception as e:
                    self.logger.warning(f"Failed to get channel from MongoDB: {e}")
            
            # Fallback to SQLite if MongoDB didn't return a result
            if existing_channel_id is None:
                self.cursor.execute(
                    "SELECT channel_id FROM auto_redeem_channels WHERE guild_id = ?",
                    (interaction.guild.id,)
                )
                result = self.cursor.fetchone()
                if result:
                    existing_channel_id = result[0]
                    
                    # Sync to MongoDB if found in SQLite but not in MongoDB
                    if mongo_enabled() and AutoRedeemChannelsAdapter:
                        try:
                            AutoRedeemChannelsAdapter.set_channel(
                                interaction.guild.id,
                                existing_channel_id,
                                interaction.user.id
                            )
                            self.logger.info(f"Synced channel config to MongoDB for guild {interaction.guild.id}")
                        except Exception as e:
                            self.logger.warning(f"Failed to sync channel to MongoDB: {e}")
            
            if existing_channel_id:
                # Channel already configured - ask for confirmation
                channel = interaction.guild.get_channel(existing_channel_id)
                channel_mention = channel.mention if channel else f"<#{existing_channel_id}>"
                
                embed = discord.Embed(
                    title="📺 Channel Already Configured",
                    description=(
                        f"**Current monitored channel:** {channel_mention}\n\n"
                        "The bot is currently monitoring this channel for FID codes.\n\n"
                        "Do you want to change to a different channel?"
                    ),
                    color=0xFEE75C
                )
                
                view = discord.ui.View()
                view.add_item(discord.ui.Button(
                    label="Change Channel",
                    emoji="🔄",
                    style=discord.ButtonStyle.primary,
                    custom_id="auto_redeem_change_channel"
                ))
                view.add_item(discord.ui.Button(
                    label="Keep Current",
                    emoji="✅",
                    style=discord.ButtonStyle.secondary,
                    custom_id="giftcode_auto_redeem"
                ))
                
                await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
                return
            
            await interaction.response.defer(ephemeral=True)
            
            try:
                # Get all text channels in the guild
                text_channels = [ch for ch in interaction.guild.text_channels if ch.permissions_for(interaction.guild.me).send_messages]
                
                if not text_channels:
                    await interaction.followup.send(
                        "❌ No accessible text channels found in this server.",
                        ephemeral=True
                    )
                    return
                
                # Create channel selection view
                class ChannelSelectView(discord.ui.View):
                    def __init__(self, channels_list, cog_instance, guild_id, user_id):
                        super().__init__(timeout=None)
                        self.channels = channels_list
                        self.cog = cog_instance
                        self.guild_id = guild_id
                        self.user_id = user_id
                        self.current_page = 0
                        self.channels_per_page = 20
                        self.update_components()
                    
                    def get_total_pages(self):
                        return (len(self.channels) - 1) // self.channels_per_page + 1
                    
                    def update_components(self):
                        self.clear_items()
                        
                        # Get channels for current page
                        start_idx = self.current_page * self.channels_per_page
                        end_idx = start_idx + self.channels_per_page
                        page_channels = self.channels[start_idx:end_idx]
                        
                        # Create select menu
                        options = []
                        for channel in page_channels:
                            options.append(
                                discord.SelectOption(
                                    label=f"#{channel.name}",
                                    description=f"ID: {channel.id}",
                                    value=str(channel.id),
                                    emoji="📺"
                                )
                            )
                        
                        if options:
                            select = discord.ui.Select(
                                placeholder="Select a channel to monitor for FIDs",
                                options=options,
                                min_values=1,
                                max_values=1
                            )
                            select.callback = self.channel_select
                            self.add_item(select)
                        
                        # Pagination buttons
                        if self.current_page > 0:
                            prev_btn = discord.ui.Button(label="◀ Previous", style=discord.ButtonStyle.secondary)
                            prev_btn.callback = self.previous_page
                            self.add_item(prev_btn)
                        
                        if self.current_page < self.get_total_pages() - 1:
                            next_btn = discord.ui.Button(label="Next ▶", style=discord.ButtonStyle.secondary)
                            next_btn.callback = self.next_page
                            self.add_item(next_btn)
                    
                    async def channel_select(self, select_interaction: discord.Interaction):
                        # Import at method level to avoid scope issues
                        from db.mongo_adapters import mongo_enabled, AutoRedeemChannelsAdapter
                        
                        channel_id = int(select_interaction.data['values'][0])
                        channel = select_interaction.guild.get_channel(channel_id)
                        
                        if not channel:
                            await select_interaction.response.send_message("❌ Channel not found.", ephemeral=True)
                            return
                        
                        # Save to MongoDB first
                        try:
                            if mongo_enabled() and AutoRedeemChannelsAdapter:
                                try:
                                    AutoRedeemChannelsAdapter.set_channel(
                                        self.guild_id,
                                        channel_id,
                                        self.user_id
                                    )
                                except Exception as e:
                                    self.cog.logger.error(f"Failed to save channel to MongoDB: {e}")
                        except ImportError:
                            # MongoDB not available, skip
                            pass
                        
                        # Also save to SQLite for backward compatibility
                        try:
                            self.cog.cursor.execute("""
                                INSERT OR REPLACE INTO auto_redeem_channels 
                                (guild_id, channel_id, added_by, added_at)
                                VALUES (?, ?, ?, ?)
                            """, (self.guild_id, channel_id, self.user_id, datetime.now()))
                            self.cog.giftcode_db.commit()
                            
                            embed = discord.Embed(
                                title="✅ Channel Monitoring Configured",
                                description=(
                                    f"**Channel:** {channel.mention}\n\n"
                                    "**How it works:**\n"
                                    "▸ Bot will monitor this channel for 9-digit FID codes\n"
                                    "▸ Valid FIDs will be automatically added to auto-redeem list\n"
                                    "▸ Players will receive confirmation when added\n\n"
                                    "**Example:** When someone posts `123456789`, the bot will validate and add them."
                                ),
                                color=0x57F287
                            )
                            self._set_embed_footer(embed, interaction.guild)
                            
                            await select_interaction.response.edit_message(embed=embed, view=None)
                        except Exception as e:
                            self.cog.logger.error(f"Error saving channel config: {e}")
                            await select_interaction.response.send_message(
                                f"❌ Error saving configuration: {str(e)}",
                                ephemeral=True
                            )
                    
                    async def previous_page(self, btn_interaction: discord.Interaction):
                        if self.current_page > 0:
                            self.current_page -= 1
                            self.update_components()
                            await btn_interaction.response.edit_message(view=self)
                    
                    async def next_page(self, btn_interaction: discord.Interaction):
                        if self.current_page < self.get_total_pages() - 1:
                            self.current_page += 1
                            self.update_components()
                            await btn_interaction.response.edit_message(view=self)
                
                view = ChannelSelectView(text_channels, self, interaction.guild.id, interaction.user.id)
                await interaction.followup.send(
                    f"**Select a channel to monitor for FID codes:**\n\nTotal channels: {len(text_channels)}",
                    view=view,
                    ephemeral=True
                )
                
            except Exception as e:
                self.logger.exception(f"Error in import from channel: {e}")
                await interaction.followup.send(
                    f"❌ An error occurred: {str(e)}",
                    ephemeral=True
                )
            return

        # Handle change channel button (when channel already configured)
        if custom_id == "auto_redeem_change_channel":
            if not await self.check_admin_permission(interaction.user.id):
                await interaction.response.send_message(
                    "❌ Only administrators can configure channel monitoring.",
                    ephemeral=True
                )
                return
            
            await interaction.response.defer(ephemeral=True)
            
            try:
                # Get all text channels in the guild
                text_channels = [ch for ch in interaction.guild.text_channels if ch.permissions_for(interaction.guild.me).send_messages]
                
                if not text_channels:
                    await interaction.followup.send(
                        "❌ No accessible text channels found in this server.",
                        ephemeral=True
                    )
                    return
                
                # Create channel selection view (reuse the same class from import_channel)
                class ChannelSelectView(discord.ui.View):
                    def __init__(self, channels_list, cog_instance, guild_id, user_id):
                        super().__init__(timeout=300)
                        self.channels = channels_list
                        self.cog = cog_instance
                        self.guild_id = guild_id
                        self.user_id = user_id
                        self.current_page = 0
                        self.channels_per_page = 20
                        self.update_components()
                    
                    def get_total_pages(self):
                        return (len(self.channels) - 1) // self.channels_per_page + 1
                    
                    def update_components(self):
                        self.clear_items()
                        
                        # Get channels for current page
                        start_idx = self.current_page * self.channels_per_page
                        end_idx = start_idx + self.channels_per_page
                        page_channels = self.channels[start_idx:end_idx]
                        
                        # Create select menu
                        options = []
                        for channel in page_channels:
                            options.append(
                                discord.SelectOption(
                                    label=f"#{channel.name}",
                                    description=f"ID: {channel.id}",
                                    value=str(channel.id),
                                    emoji="📺"
                                )
                            )
                        
                        if options:
                            select = discord.ui.Select(
                                placeholder="Select a channel to monitor for FIDs",
                                options=options,
                                min_values=1,
                                max_values=1
                            )
                            select.callback = self.channel_select
                            self.add_item(select)
                        
                        # Pagination buttons
                        if self.current_page > 0:
                            prev_btn = discord.ui.Button(label="◀ Previous", style=discord.ButtonStyle.secondary)
                            prev_btn.callback = self.previous_page
                            self.add_item(prev_btn)
                        
                        if self.current_page < self.get_total_pages() - 1:
                            next_btn = discord.ui.Button(label="Next ▶", style=discord.ButtonStyle.secondary)
                            next_btn.callback = self.next_page
                            self.add_item(next_btn)
                    
                    async def channel_select(self, select_interaction: discord.Interaction):
                        # Import at method level to avoid scope issues
                        from db.mongo_adapters import mongo_enabled, AutoRedeemChannelsAdapter
                        
                        channel_id = int(select_interaction.data['values'][0])
                        channel = select_interaction.guild.get_channel(channel_id)
                        
                        if not channel:
                            await select_interaction.response.send_message("❌ Channel not found.", ephemeral=True)
                            return
                        
                        # Save to MongoDB first
                        try:
                            if mongo_enabled() and AutoRedeemChannelsAdapter:
                                try:
                                    AutoRedeemChannelsAdapter.set_channel(
                                        self.guild_id,
                                        channel_id,
                                        self.user_id
                                    )
                                except Exception as e:
                                    self.cog.logger.error(f"Failed to save channel to MongoDB: {e}")
                        except ImportError:
                            # MongoDB not available, skip
                            pass
                        
                        # Also save to SQLite for backward compatibility
                        try:
                            self.cog.cursor.execute("""
                                INSERT OR REPLACE INTO auto_redeem_channels 
                                (guild_id, channel_id, added_by, added_at)
                                VALUES (?, ?, ?, ?)
                            """, (self.guild_id, channel_id, self.user_id, datetime.now()))
                            self.cog.giftcode_db.commit()
                            
                            embed = discord.Embed(
                                title="✅ Channel Monitoring Updated",
                                description=(
                                    f"**New monitored channel:** {channel.mention}\n\n"
                                    "**How it works:**\n"
                                    "▸ Bot will monitor this channel for 9-digit FID codes\n"
                                    "▸ Valid FIDs will be automatically added to auto-redeem list\n"
                                    "▸ Players will receive confirmation when added\n\n"
                                    "**Example:** When someone posts `123456789`, the bot will validate and add them."
                                ),
                                color=0x57F287
                            )
                            self._set_embed_footer(embed, interaction.guild)
                            
                            await select_interaction.response.edit_message(embed=embed, view=None)
                        except Exception as e:
                            self.cog.logger.error(f"Error saving channel config: {e}")
                            await select_interaction.response.send_message(
                                f"❌ Error saving configuration: {str(e)}",
                                ephemeral=True
                            )
                    
                    async def previous_page(self, btn_interaction: discord.Interaction):
                        if self.current_page > 0:
                            self.current_page -= 1
                            self.update_components()
                            await btn_interaction.response.edit_message(view=self)
                    
                    async def next_page(self, btn_interaction: discord.Interaction):
                        if self.current_page < self.get_total_pages() - 1:
                            self.current_page += 1
                            self.update_components()
                            await btn_interaction.response.edit_message(view=self)
                
                view = ChannelSelectView(text_channels, self, interaction.guild.id, interaction.user.id)
                await interaction.followup.send(
                    f"**Select a new channel to monitor for FID codes:**\n\nTotal channels: {len(text_channels)}",
                    view=view,
                    ephemeral=True
                )
                
            except Exception as e:
                self.logger.exception(f"Error in change channel: {e}")
                await interaction.followup.send(
                    f"❌ An error occurred: {str(e)}",
                    ephemeral=True
                )
            return


        # Handle enable auto redeem
        if custom_id == "auto_redeem_enable":
            if not await self.check_admin_permission(interaction.user.id):
                await interaction.response.send_message(
                    "❌ Only administrators can configure auto redeem.",
                    ephemeral=True
                )
                return
            
            # Update MongoDB first
            _mongo_enabled = globals().get('mongo_enabled', lambda: False)
            mongo_saved = False
            if _mongo_enabled() and AutoRedeemSettingsAdapter:
                try:
                    self.logger.info(f"📊 MongoDB: Saving auto-redeem ENABLED for guild {interaction.guild.id}...")
                    AutoRedeemSettingsAdapter.set_enabled(
                        interaction.guild.id,
                        True,
                        interaction.user.id
                    )
                    mongo_saved = True
                    self.logger.info(f"✅ MongoDB: Successfully saved auto-redeem ENABLED for guild {interaction.guild.id}")
                except Exception as e:
                    self.logger.error(f"❌ MongoDB: Failed to save auto redeem settings: {e}")
            else:
                if not _mongo_enabled():
                    self.logger.warning("⚠️ MongoDB is not enabled - settings will be lost on restart!")
                elif not AutoRedeemSettingsAdapter:
                    self.logger.warning("⚠️ AutoRedeemSettingsAdapter not available - settings will be lost on restart!")
            
            # Also update SQLite for backward compatibility
            sqlite_saved = False
            try:
                self.logger.info(f"📂 SQLite: Saving auto-redeem ENABLED for guild {interaction.guild.id}...")
                self.cursor.execute("""
                    INSERT OR REPLACE INTO auto_redeem_settings 
                    (guild_id, enabled, updated_by, updated_at)
                    VALUES (?, 1, ?, ?)
                """, (interaction.guild.id, interaction.user.id, datetime.now()))
                self.giftcode_db.commit()
                sqlite_saved = True
                self.logger.info(f"✅ SQLite: Successfully saved auto-redeem ENABLED for guild {interaction.guild.id}")
            except Exception as e:
                self.logger.error(f"❌ SQLite: Failed to save auto redeem settings: {e}")
            
            # Log final persistence status
            if mongo_saved:
                self.logger.info(f"🎉 AUTO-REDEEM ENABLED: Settings saved to MongoDB (PERSISTENT on Render)")
            elif sqlite_saved:
                self.logger.warning(f"⚠️ AUTO-REDEEM ENABLED: Settings saved to SQLite only (TEMPORARY - will reset on Render restart!)")
            else:
                self.logger.error(f"❌ AUTO-REDEEM ENABLED: Failed to save to ANY database!")
            
            embed = discord.Embed(
                title="✅ Auto Redeem Enabled",
                description=(
                    "Automatic gift code redemption is now **enabled**!\n\n"
                    "**What happens next:**\n"
                    "• New gift codes will be automatically redeemed for all members\n"
                    "• Process animation will be shown in the configured channel\n"
                    "• Members will see real-time redemption progress\n\n"
                    "You can disable this anytime from the configure menu."
                ),
                color=0x57F287
            )
            self._set_embed_footer(embed, interaction.guild)
            
            await self._safe_edit_message(interaction, embed=embed, view=None)
            return
        
        # Handle disable auto redeem
        if custom_id == "auto_redeem_disable":
            if not await self.check_admin_permission(interaction.user.id):
                await interaction.response.send_message(
                    "❌ Only administrators can configure auto redeem.",
                    ephemeral=True
                )
                return
            
            # Update MongoDB first
            _mongo_enabled = globals().get('mongo_enabled', lambda: False)
            mongo_saved = False
            if _mongo_enabled() and AutoRedeemSettingsAdapter:
                try:
                    self.logger.info(f"📊 MongoDB: Saving auto-redeem DISABLED for guild {interaction.guild.id}...")
                    AutoRedeemSettingsAdapter.set_enabled(
                        interaction.guild.id,
                        False,
                        interaction.user.id
                    )
                    mongo_saved = True
                    self.logger.info(f"✅ MongoDB: Successfully saved auto-redeem DISABLED for guild {interaction.guild.id}")
                except Exception as e:
                    self.logger.error(f"❌ MongoDB: Failed to save auto redeem settings: {e}")
            else:
                if not _mongo_enabled():
                    self.logger.warning("⚠️ MongoDB is not enabled")
                elif not AutoRedeemSettingsAdapter:
                    self.logger.warning("⚠️ AutoRedeemSettingsAdapter not available")
            
            # Also update SQLite for backward compatibility
            sqlite_saved = False
            try:
                self.logger.info(f"📂 SQLite: Saving auto-redeem DISABLED for guild {interaction.guild.id}...")
                self.cursor.execute("""
                    INSERT OR REPLACE INTO auto_redeem_settings 
                    (guild_id, enabled, updated_by, updated_at)
                    VALUES (?, 0, ?, ?)
                """, (interaction.guild.id, interaction.user.id, datetime.now()))
                self.giftcode_db.commit()
                sqlite_saved = True
                self.logger.info(f"✅ SQLite: Successfully saved auto-redeem DISABLED for guild {interaction.guild.id}")
            except Exception as e:
                self.logger.error(f"❌ SQLite: Failed to save auto redeem settings: {e}")
            
            # Log final persistence status
            if mongo_saved:
                self.logger.info(f"🚫 AUTO-REDEEM DISABLED: Settings saved to MongoDB (PERSISTENT)")
            elif sqlite_saved:
                self.logger.warning(f"⚠️ AUTO-REDEEM DISABLED: Settings saved to SQLite only (TEMPORARY)")
            else:
                self.logger.error(f"❌ AUTO-REDEEM DISABLED: Failed to save to ANY database!")
            
            embed = discord.Embed(
                title="🔴 Auto Redeem Disabled",
                description=(
                    "Automatic gift code redemption is now **disabled**.\n\n"
                    "**What this means:**\n"
                    "• New gift codes will NOT be automatically redeemed\n"
                    "• You can still manually redeem codes\n"
                    "• Member list is preserved\n\n"
                    "You can re-enable this anytime from the configure menu."
                ),
                color=0xED4245
            )
            self._set_embed_footer(embed, interaction.guild)
            
            await self._safe_edit_message(interaction, embed=embed, view=None)
            return
        
        # Handle reset code status
        if custom_id == "auto_redeem_reset_code":
            if not await self.check_admin_permission(interaction.user.id):
                await interaction.response.send_message(
                    "❌ Only administrators can reset code status.",
                    ephemeral=True
                )
                return
            
            await interaction.response.defer(ephemeral=True)
            
            try:
                # Fetch all gift codes from database
                all_codes = []
                
                # Try MongoDB first - use get_all() method
                _mongo_enabled = globals().get('mongo_enabled', lambda: False)
                cutoff_date = datetime.utcnow().replace(microsecond=0)
                cutoff_date = cutoff_date.replace(
                    year=cutoff_date.year if cutoff_date.month > 3 else cutoff_date.year - 1,
                    month=cutoff_date.month - 3 if cutoff_date.month > 3 else cutoff_date.month + 9
                )  # 90 days ago (approximate)
                cutoff_str = cutoff_date.isoformat()

                if _mongo_enabled() and GiftCodesAdapter:
                    try:
                        mongo_codes = GiftCodesAdapter.get_all()
                        if mongo_codes:
                            all_codes = []
                            for code_data in mongo_codes:
                                try:
                                    if isinstance(code_data, tuple):
                                        code_str = str(code_data[0]) if code_data else ''
                                        date_str = str(code_data[1]) if len(code_data) > 1 else ''
                                        processed = code_data[2] if len(code_data) > 2 else False
                                        status = 'pending'
                                        created_at = ''
                                    elif isinstance(code_data, dict):
                                        code_str = str(code_data.get('giftcode', ''))
                                        date_str = str(code_data.get('date', ''))
                                        processed = code_data.get('auto_redeem_processed', False)
                                        status = code_data.get('validation_status', 'pending')
                                        created_at = str(code_data.get('created_at', '') or '')
                                    else:
                                        code_str = str(code_data)
                                        date_str = ''
                                        processed = False
                                        status = 'pending'
                                        created_at = ''

                                    # Skip invalid/expired codes
                                    if not code_str or status in ('invalid', 'expired'):
                                        continue

                                    # Skip codes older than 90 days or with missing timestamps
                                    if not created_at or created_at < cutoff_str:
                                        self.logger.debug(f"Skipping old/dateless code {code_str} (created_at: {created_at})")
                                        continue

                                    all_codes.append((code_str, date_str, processed, created_at))
                                except Exception as e:
                                    self.logger.warning(f"Failed to parse MongoDB code entry: {e}")
                                    continue

                            # Sort by created_at descending (newest first), empty dates last
                            all_codes.sort(key=lambda x: x[3] or '0', reverse=True)
                            # Strip the created_at field used for sorting
                            all_codes = [(c[0], c[1], c[2]) for c in all_codes]

                            self.logger.info(f"Fetched {len(all_codes)} active recent codes from MongoDB for reset")
                    except Exception as e:
                        self.logger.warning(f"Failed to fetch codes from MongoDB: {e}")
                
                # Fallback to SQLite if MongoDB failed or not enabled
                if not all_codes:
                    try:
                        self.logger.info("📂 Fetching active codes from SQLite database...")
                        self.cursor.execute("""
                            SELECT giftcode, date, auto_redeem_processed
                            FROM gift_codes
                            WHERE (
                                validation_status IS NULL
                                OR (validation_status != 'invalid' AND validation_status != 'expired')
                            )
                            AND added_at IS NOT NULL
                            AND added_at >= datetime('now', '-90 days')
                            ORDER BY
                                CASE WHEN added_at IS NULL THEN 0 ELSE 1 END,
                                added_at DESC
                        """)
                        all_codes = self.cursor.fetchall()
                        self.logger.info(f"Fetched {len(all_codes)} active recent codes from SQLite for reset")
                    except Exception as e:
                        self.logger.error(f"❌ SQLite fetch failed: {e}")
                
                if not all_codes:
                    await interaction.followup.send(
                        "📋 No gift codes found in the database.",
                        ephemeral=True
                    )
                    return
                
                # Limit to most recent 25 codes for dropdown
                recent_codes = all_codes[:25]
                
                # Create dropdown select view
                class CodeResetSelectView(discord.ui.View):
                    def __init__(self, codes_list, cog_instance):
                        super().__init__(timeout=300)
                        self.codes = codes_list
                        self.cog = cog_instance
                        
                        # Create select menu
                        options = []
                        for code, date, processed in self.codes:
                            status_emoji = "✅" if processed else "⏳"
                            status_text = "Processed" if processed else "Unprocessed"
                            
                            options.append(
                                discord.SelectOption(
                                    label=f"{code[:50]}",  # Truncate long codes
                                    description=f"{status_text} • {date if date else 'No date'}",
                                    value=code,
                                    emoji=status_emoji
                                )
                            )
                        
                        select = discord.ui.Select(
                            placeholder="Select a code to reset...",
                            options=options,
                            custom_id="code_to_reset_select"
                        )
                        select.callback = self.reset_code
                        self.add_item(select)
                    
                    async def reset_code(self, select_interaction: discord.Interaction):
                        try:
                            await select_interaction.response.defer(ephemeral=True)
                            selected_code = select_interaction.data["values"][0]

                            self.cog.logger.info(f"DEEP RESET initiated for code {selected_code} by {select_interaction.user}")

                            results = {
                                'sqlite_global': False,
                                'mongo_global': False,
                                'sqlite_guilds': 0,
                                'mongo_members': 0,
                            }

                            # --- Layer 1: Reset global flag in SQLite ---
                            try:
                                self.cog.cursor.execute(
                                    "UPDATE gift_codes SET auto_redeem_processed = 0 WHERE giftcode = ?",
                                    (selected_code,)
                                )
                                self.cog.giftcode_db.commit()
                                results['sqlite_global'] = True
                                self.cog.logger.info(f"[RESET] Layer 1 OK: SQLite global flag reset for {selected_code}")
                            except Exception as e:
                                self.cog.logger.error(f"[RESET] Layer 1 FAILED: {e}")

                            # --- Layer 2: Reset global flag in MongoDB ---
                            _mongo_enabled = globals().get('mongo_enabled', lambda: False)
                            mongo_reset_status = "N/A"
                            if _mongo_enabled() and GiftCodesAdapter and hasattr(GiftCodesAdapter, 'reset_code_processed'):
                                try:
                                    success = GiftCodesAdapter.reset_code_processed(selected_code)
                                    results['mongo_global'] = True # Set to True to show green check if no error
                                    mongo_reset_status = "OK" if success else "Not found (Already clean)"
                                    self.cog.logger.info(f"[RESET] Layer 2: MongoDB global flag status: {mongo_reset_status} for {selected_code}")
                                except Exception as e:
                                    self.cog.logger.error(f"[RESET] Layer 2 FAILED: {e}")
                                    results['mongo_global'] = False
                                    mongo_reset_status = "Error"
                            else:
                                results['mongo_global'] = True # Neutral success if MongoDB disabled

                            # --- Layer 3: Clear guild completion history in SQLite ---
                            try:
                                self.cog.cursor.execute(
                                    "DELETE FROM auto_redeem_completed_guilds WHERE giftcode = ?",
                                    (selected_code,)
                                )
                                results['sqlite_guilds'] = self.cog.cursor.rowcount
                                self.cog.giftcode_db.commit()
                                self.cog.logger.info(f"[RESET] Layer 3 OK: Cleared {results['sqlite_guilds']} guild records for {selected_code}")
                            except Exception as e:
                                self.cog.logger.error(f"[RESET] Layer 3 FAILED: {e}")

                            # --- Layer 4: Clear per-member redemption history in MongoDB ---
                            mongo_members_status = "N/A"
                            if _mongo_enabled() and AutoRedeemedCodesAdapter and hasattr(AutoRedeemedCodesAdapter, 'reset_code_redemptions'):
                                try:
                                    count = AutoRedeemedCodesAdapter.reset_code_redemptions(selected_code)
                                    results['mongo_members'] = count
                                    mongo_members_status = f"Cleared {count}"
                                    self.cog.logger.info(f"[RESET] Layer 4 OK: Deleted {count} member redemption records for {selected_code}")
                                except Exception as e:
                                    self.cog.logger.error(f"[RESET] Layer 4 FAILED: {e}")
                                    results['mongo_members'] = -1 # Error state
                                    mongo_members_status = "Error"
                                    self.cog.logger.error(f"[RESET] Layer 4 FAILED: {e}")

                            any_success = results['sqlite_global'] or results['mongo_global'] or (mongo_reset_attempted and _mongo_enabled())

                            if any_success:
                                # Layer 2 status: show ✅ if it succeeded OR if it was attempted and MongoDB is enabled
                                # (Showing ✅ even if matched_count was 0, as long as no exception occurred)
                                layer2_status = "✅" if (results['mongo_global'] or mongo_reset_attempted) else "❌"
                                
                                embed = discord.Embed(
                                    title="✅ Deep Reset Complete",
                                    description=(
                                        f"**Code:** `{selected_code}`\n\n"
                                        f"**Layers Reset:**\n"
                                        f"{'✅' if results['sqlite_global'] else '❌'} Global flag (SQLite)\n"
                                        f"{layer2_status} Global flag (MongoDB)\n"
                                        f"✅ Guild history cleared: **{results['sqlite_guilds']}** records\n"
                                        f"✅ Member history cleared: **{results['mongo_members']}** records\n\n"
                                        f"**What happens next:**\n"
                                        f"• The code is now treated as **completely new**\n"
                                        f"• Click **Trigger Auto-Redeem** to start redemption\n"
                                        f"• OR it will auto-trigger on the next bot check"
                                    ),
                                    color=0x57F287
                                )
                                embed.set_footer(text="Whiteout Survival | Gift Code Management")
                                await select_interaction.followup.send(embed=embed, ephemeral=True)
                            else:
                                await select_interaction.followup.send(
                                    "❌ Failed to reset code status. Check bot logs for details.",
                                    ephemeral=True
                                )

                        except Exception as e:
                            self.cog.logger.exception(f"Error in deep reset: {e}")
                            try:
                                await select_interaction.followup.send(
                                    f"❌ An error occurred: {str(e)}",
                                    ephemeral=True
                                )
                            except:
                                pass
                
                view = CodeResetSelectView(recent_codes, self)
                
                embed = discord.Embed(
                    title="🔄 Reset Code Status",
                    description=(
                        f"**Total Codes:** {len(all_codes)}\n"
                        f"**Showing:** {len(recent_codes)} most recent\n\n"
                        "Select a code below to reset its auto-redeem processed status.\n\n"
                        "**Legend:**\n"
                        "✅ - Already processed\n"
                        "⏳ - Unprocessed\n\n"
                        "*Resetting will allow the code to be auto-redeemed again.*"
                    ),
                    color=0x5865F2
                )
                embed.set_footer(text="Whiteout Survival | Gift Code Management")
                
                await interaction.followup.send(embed=embed, view=view, ephemeral=True)
                
            except Exception as e:
                self.logger.exception(f"Error in reset code status: {e}")
                try:
                    await interaction.followup.send(
                        f"❌ An error occurred: {str(e)}",
                        ephemeral=True
                    )
                except:
                    pass
            return


        # Handle delete code
        if custom_id == "auto_redeem_delete_code":
            if not await self.check_admin_permission(interaction.user.id):
                await interaction.response.send_message(
                    "❌ Only administrators can delete codes.",
                    ephemeral=True
                )
                return
            
            await interaction.response.defer(ephemeral=True)
            
            try:
                # Fetch all gift codes from database
                all_codes = []
                
                # Try MongoDB first - but use get_all() instead of get_all_codes()
                _mongo_enabled = globals().get('mongo_enabled', lambda: False)
                if _mongo_enabled() and GiftCodesAdapter:
                    try:
                        mongo_codes = GiftCodesAdapter.get_all()
                        if mongo_codes:
                            all_codes = []
                            for code_data in mongo_codes:
                                try:
                                    # Handle different formats: tuple, dict, or other
                                    if isinstance(code_data, tuple):
                                        code_str = str(code_data[0]) if code_data else ''
                                        date_str = str(code_data[1]) if len(code_data) > 1 else ''
                                    elif isinstance(code_data, dict):
                                        code_str = str(code_data.get('giftcode', ''))
                                        date_str = str(code_data.get('date', ''))
                                    else:
                                        # Unknown format, convert to string
                                        code_str = str(code_data)
                                        date_str = ''
                                    
                                    if code_str:  # Only add non-empty codes
                                        all_codes.append((code_str, date_str, False))
                                except Exception as e:
                                    self.logger.warning(f"Failed to parse MongoDB code entry: {e}")
                                    continue
                            
                            self.logger.info(f"Fetched {len(all_codes)} codes from MongoDB for deletion")
                    except Exception as e:
                        self.logger.warning(f"Failed to fetch codes from MongoDB: {e}")
                
                # Fallback to SQLite if MongoDB failed or not enabled
                if not all_codes:
                    try:
                        self.logger.info("📂 Fetching codes from SQLite database...")
                        self.cursor.execute("""
                            SELECT giftcode, date, auto_redeem_processed
                            FROM gift_codes
                            ORDER BY added_at DESC
                        """)
                        all_codes = self.cursor.fetchall()
                        self.logger.info(f"Fetched {len(all_codes)} codes from SQLite for deletion")
                    except Exception as e:
                        self.logger.error(f"❌ SQLite fetch failed: {e}")
                
                if not all_codes:
                    await interaction.followup.send(
                        "📋 No gift codes found in the database.",
                        ephemeral=True
                    )
                    return
                
                # Limit to most recent 25 codes for dropdown
                recent_codes = all_codes[:25]
                
                # Create dropdown select view
                class CodeDeleteSelectView(discord.ui.View):
                    def __init__(self, codes_list, cog_instance):
                        super().__init__(timeout=300)
                        self.codes = codes_list
                        self.cog = cog_instance
                        
                        # Create select menu
                        options = []
                        for code, date, processed in self.codes:
                            options.append(
                                discord.SelectOption(
                                    label=f"{code[:50]}",  # Truncate long codes
                                    description=f"Added: {date if date else 'Unknown date'}",
                                    value=code,
                                    emoji="🗑️"
                                )
                            )
                        
                        select = discord.ui.Select(
                            placeholder="Select a code to delete...",
                            options=options,
                            custom_id="code_to_delete_select"
                        )
                        select.callback = self.confirm_delete
                        self.add_item(select)
                    
                    async def confirm_delete(self, select_interaction: discord.Interaction):
                        try:
                            selected_code = select_interaction.data["values"][0]
                            
                            # Create confirmation view
                            class ConfirmDeleteView(discord.ui.View):
                                def __init__(self, code_to_delete, cog_instance):
                                    super().__init__(timeout=60)
                                    self.code = code_to_delete
                                    self.cog = cog_instance
                                
                                @discord.ui.button(label="Confirm Delete", style=discord.ButtonStyle.danger, emoji="✅")
                                async def confirm(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
                                    await btn_interaction.response.defer(ephemeral=True)
                                    
                                    # Delete from MongoDB if available
                                    _mongo_enabled = globals().get('mongo_enabled', lambda: False)
                                    mongo_deleted = False
                                    if _mongo_enabled() and GiftCodesAdapter:
                                        try:
                                            # Use the delete method if available, otherwise skip
                                            if hasattr(GiftCodesAdapter, 'delete'):
                                                GiftCodesAdapter.delete(self.code)
                                                mongo_deleted = True
                                                self.cog.logger.info(f"Deleted code {self.code} from MongoDB")
                                            else:
                                                self.cog.logger.info("MongoDB delete method not available, using SQLite only")
                                        except Exception as e:
                                            self.cog.logger.error(f"Failed to delete code from MongoDB: {e}")
                                    
                                    # Delete from SQLite
                                    sqlite_deleted = False
                                    try:
                                        self.cog.cursor.execute(
                                            "DELETE FROM gift_codes WHERE giftcode = ?",
                                            (self.code,)
                                        )
                                        rows_affected = self.cog.cursor.rowcount
                                        self.cog.giftcode_db.commit()
                                        if rows_affected > 0:
                                            sqlite_deleted = True
                                            self.cog.logger.info(f"Deleted code {self.code} from SQLite")
                                        else:
                                            self.cog.logger.warning(f"Code {self.code} not found in SQLite")
                                    except Exception as e:
                                        self.cog.logger.error(f"Failed to delete code from SQLite: {e}")
                                    
                                    if mongo_deleted or sqlite_deleted:
                                        embed = discord.Embed(
                                            title="✅ Code Deleted",
                                                description=(
                                                    f"**Code:** `{self.code}`\n\n"
                                                    "The gift code has been permanently deleted.\n\n"
                                                    "**Deleted from:**\n" +
                                                    (f"• MongoDB ✅\n" if mongo_deleted else "") +
                                                    (f"• SQLite ✅\n" if sqlite_deleted else "") +
                                                    "**Note:** This action cannot be undone."
                                                ),
                                            color=0x57F287
                                        )
                                        embed.set_footer(text="Whiteout Survival | Gift Code Management")
                                        await btn_interaction.followup.send(embed=embed, ephemeral=True)
                                    else:
                                        await btn_interaction.followup.send(
                                            "❌ Failed to delete code from both databases.",
                                            ephemeral=True
                                        )
                                
                                @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="❌")
                                async def cancel(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
                                    embed = discord.Embed(
                                        title="❌ Deletion Cancelled",
                                        description="The code was not deleted.",
                                        color=0x5865F2
                                    )
                                    await btn_interaction.response.edit_message(embed=embed, view=None)
                            
                            # Show confirmation dialog
                            confirm_embed = discord.Embed(
                                title="⚠️ Confirm Code Deletion",
                                description=(
                                    f"Are you sure you want to **permanently delete** this code?\n\n"
                                    f"**Code:** `{selected_code}`\n\n"
                                    "⚠️ **This action cannot be undone!**\n"
                                    "The code will be removed from both MongoDB and SQLite databases."
                                ),
                                color=0xED4245
                            )
                            
                            confirm_view = ConfirmDeleteView(selected_code, self.cog)
                            await select_interaction.response.edit_message(embed=confirm_embed, view=confirm_view)
                        
                        except Exception as e:
                            self.cog.logger.exception(f"Error in delete confirmation: {e}")
                            try:
                                await select_interaction.followup.send(
                                    f"❌ An error occurred: {str(e)}",
                                    ephemeral=True
                                )
                            except:
                                pass
                
                view = CodeDeleteSelectView(recent_codes, self)
                
                embed = discord.Embed(
                    title="🗑️ Delete Gift Code",
                    description=(
                        f"**Total Codes:** {len(all_codes)}\n"
                        f"**Showing:** {len(recent_codes)} most recent\n\n"
                        "Select a code below to permanently delete it.\n\n"
                        "⚠️ **Warning:** Deleted codes cannot be recovered!\n"
                        "*You will be asked to confirm before deletion.*"
                    ),
                    color=0xED4245
                )
                embed.set_footer(text="Whiteout Survival | Gift Code Management")
                
                await interaction.followup.send(embed=embed, view=view, ephemeral=True)
                
            except Exception as e:
                self.logger.exception(f"Error in delete code: {e}")
                try:
                    await interaction.followup.send(
                        f"❌ An error occurred: {str(e)}",
                        ephemeral=True
                    )
                except:
                    pass
            return


    @commands.Cog.listener()
    async def on_message(self, message):
        """Monitor configured channels for FID codes"""
        # Ignore bot messages
        if message.author.bot:
            return
            
        # Ignore DM messages
        if not message.guild:
            return
        
        # Check if message is in a monitored channel
        try:
            # Import at method level to avoid scope issues
            from db.mongo_adapters import mongo_enabled, AutoRedeemChannelsAdapter
            
            monitored_channel_id = None
            
            # Check MongoDB first
            if mongo_enabled() and AutoRedeemChannelsAdapter:
                try:
                    channel_config = AutoRedeemChannelsAdapter.get_channel(message.guild.id)
                    if channel_config:
                        monitored_channel_id = channel_config.get('channel_id')
                        self.logger.debug(f"Found monitored channel {monitored_channel_id} in MongoDB for guild {message.guild.id}")
                except Exception as e:
                    self.logger.warning(f"Failed to get channel from MongoDB: {e}")
            
            # Fallback to SQLite if MongoDB didn't return a result
            if monitored_channel_id is None:
                self.cursor.execute(
                    "SELECT channel_id FROM auto_redeem_channels WHERE guild_id = ?",
                    (message.guild.id,)
                )
                result = self.cursor.fetchone()
                if result:
                    monitored_channel_id = result[0]
                    self.logger.debug(f"Found monitored channel {monitored_channel_id} in SQLite for guild {message.guild.id}")
                    
                    # Sync to MongoDB if found in SQLite but not in MongoDB
                    if mongo_enabled() and AutoRedeemChannelsAdapter:
                        try:
                            AutoRedeemChannelsAdapter.set_channel(
                                message.guild.id,
                                monitored_channel_id,
                                message.author.id
                            )
                            self.logger.info(f"Synced channel config to MongoDB for guild {message.guild.id}")
                        except Exception as e:
                            self.logger.warning(f"Failed to sync channel to MongoDB: {e}")
            
            # Check if current channel is the monitored channel
            if not monitored_channel_id or monitored_channel_id != message.channel.id:
                return  # Not a monitored channel
            
            # Extract 9-digit codes from message
            fid_pattern = r'\b\d{9}\b'
            fids = re.findall(fid_pattern, message.content)
            
            if not fids:
                return  # No FIDs found
            
            self.logger.info(f"Detected {len(fids)} FID(s) in monitored channel {message.channel.id} (guild {message.guild.id}): {fids}")
            
            # Process each FID
            for fid in fids:
                try:
                    self.logger.debug(f"Processing FID {fid} from user {message.author.id}")
                    
                    # Check if already exists
                    if self.AutoRedeemDB.member_exists(self, message.guild.id, fid):
                        self.logger.info(f"FID {fid} already exists in auto-redeem list for guild {message.guild.id}")
                        await message.reply(
                            f"⚠️ {message.author.mention} Your FID `{fid}` is already in the auto-redeem list!",
                            delete_after=10
                        )
                        continue
                    
                    # Fetch player data from API
                    self.logger.debug(f"Fetching player data for FID {fid}")
                    player_data = await self.fetch_player_data(fid)
                    
                    if not player_data:
                        self.logger.warning(f"Invalid FID {fid} - API returned no data")
                        await message.reply(
                            f"❌ {message.author.mention} Invalid FID `{fid}`. Please check and try again.",
                            delete_after=15
                        )
                        continue
                    
                    # Add to auto-redeem list
                    member_data = {
                        'nickname': player_data['nickname'],
                        'furnace_lv': player_data['furnace_lv'],
                        'avatar_image': player_data.get('avatar_image', ''),
                        'added_by': message.author.id
                    }
                    
                    self.logger.debug(f"Adding FID {fid} ({player_data['nickname']}) to auto-redeem list")
                    success = self.AutoRedeemDB.add_member(
                        self,
                        message.guild.id,
                        fid,
                        member_data
                    )
                    
                    if success:
                        formatted_fc = self.format_furnace_level(player_data['furnace_lv'])
                        embed = discord.Embed(
                            title="✨ Auto-Redeem Registered",
                            description=f"✅ **{player_data['nickname']}** is now enrolled for automated gift codes.",
                            color=0x2ecc71
                        )
                        embed.add_field(name="Player ID", value=f"`{fid}`", inline=True)
                        embed.add_field(name="Furnace", value=f"`{formatted_fc}`", inline=True)
                        embed.add_field(name="🚀 Auto-Processing", value="`Initializing...`", inline=False)
                        
                        # Add player avatar if available
                        avatar = player_data.get('avatar_image')
                        if avatar and str(avatar).startswith('http'):
                            embed.set_thumbnail(url=avatar)
                        
                        self._set_embed_footer(embed, message.guild)
                        
                        sent_msg = await message.reply(embed=embed)
                        self.logger.info(f"✅ Successfully auto-added {player_data['nickname']} ({fid}) from channel in guild {message.guild.id}")
                        
                        # Start immediate redemption process for active codes
                        async def process_initial_redemptions():
                            try:
                                # Get all valid/active gift codes from unified source
                                active_codes_map = await self.get_active_gift_codes_consolidated()
                                active_codes = list(active_codes_map.keys())
                                
                                if not active_codes:
                                    embed.set_field_at(2, name="🚀 Auto-Processing", value="`No active codes found to redeem.`", inline=False)
                                    await sent_msg.edit(embed=embed)
                                    return

                                total = len(active_codes)
                                embed.set_field_at(2, name="🚀 Auto-Processing", value=f"`Checking {total} active codes...`", inline=False)
                                await sent_msg.edit(embed=embed)
                                
                                redeemed = 0
                                already = 0
                                failed = 0
                                
                                # Process each code
                                for i, code in enumerate(active_codes):
                                    embed.set_field_at(2, name="🚀 Auto-Processing", value=f"`Processing code {i+1}/{total}:` **`{code}`**", inline=False)
                                    await sent_msg.edit(embed=embed)
                                    
                                    # Use the core redemption method
                                    status, s_count, a_count, f_count = await self._redeem_for_member(
                                        message.guild.id, 
                                        fid, 
                                        player_data['nickname'], 
                                        player_data['furnace_lv'], 
                                        code
                                    )
                                    
                                    redeemed += s_count
                                    already += a_count
                                    failed += f_count
                                    
                                    # Small delay between codes to be safe with rate limits
                                    await asyncio.sleep(1)
                                
                                # Final update
                                embed.set_field_at(2, name="🚀 Redemption Results", value=(
                                    f"✅ Success: `{redeemed}`\n"
                                    f"ℹ️ Already Claimed: `{already}`\n"
                                    f"❌ Failed: `{failed}`"
                                ), inline=False)
                                embed.title = "✨ Auto-Redeem Complete"
                                await sent_msg.edit(embed=embed)
                                
                            except Exception as e:
                                self.logger.error(f"Error in initial redemption process for FID {fid}: {e}")
                                embed.set_field_at(2, name="🚀 Auto-Processing", value="`⚠️ Error during batch redemption.`", inline=False)
                                try: await sent_msg.edit(embed=embed)
                                except: pass
                        
                        # Run in background
                        asyncio.create_task(process_initial_redemptions())
                    else:
                        self.logger.error(f"Failed to add ID {fid} to database for guild {message.guild.id}")
                        await message.reply(
                            f"❌ {message.author.mention} Failed to add ID `{fid}`. It may already exist.",
                            delete_after=10
                        )
                        
                except Exception as e:
                    self.logger.error(f"Error processing ID {fid} from channel: {e}", exc_info=True)
                    await message.reply(
                        f"❌ {message.author.mention} An error occurred while processing ID `{fid}`.",
                        delete_after=10
                    )
                    
        except Exception as e:
            self.logger.error(f"Error in on_message FID detection: {e}", exc_info=True)
    

    @commands.command(name="stop_auto_redeem", aliases=["stop_redeem"])
    async def stop_auto_redeem_text(self, ctx):
        """Stop the ongoing auto-redeem process for this server"""
        if not await self.check_admin_permission(ctx.author.id):
            await ctx.reply("❌ Only administrators can use this command.", delete_after=5)
            return
            
        guild_id = ctx.guild.id
        
        # Set stop signal to halt current execution
        self.stop_signals[guild_id] = True
        
        # PERSISTENCE: Disable auto-redeem in database to prevent future runs
        # Update MongoDB first
        _mongo_enabled = globals().get('mongo_enabled', lambda: False)
        mongo_saved = False
        if _mongo_enabled() and AutoRedeemSettingsAdapter:
            try:
                self.logger.info(f"📊 MongoDB: Saving auto-redeem DISABLED for guild {guild_id} (via STOP command)...")
                AutoRedeemSettingsAdapter.set_enabled(
                    guild_id,
                    False,
                    ctx.author.id
                )
                mongo_saved = True
            except Exception as e:
                self.logger.error(f"❌ MongoDB: Failed to save auto redeem settings: {e}")
        
        # Also update SQLite for backward compatibility
        try:
            self.cursor.execute("""
                INSERT OR REPLACE INTO auto_redeem_settings 
                (guild_id, enabled, updated_by, updated_at)
                VALUES (?, 0, ?, ?)
            """, (guild_id, ctx.author.id, datetime.now()))
            self.giftcode_db.commit()
        except Exception as e:
            self.logger.error(f"❌ SQLite: Failed to save auto redeem settings: {e}")
            
        # Check if any redemption is active
        active_count = 0
        current_active = []
        async with self._redemption_lock:
            for gid, code in self._active_redemptions:
                if gid == guild_id:
                    active_count += 1
                    current_active.append(code)
        
        if active_count > 0:
            codes_str = ", ".join(current_active)
            await ctx.reply(f"🛑 **Stopping Auto-Redeem**\n\nSignal sent to stop redemption for code(s): `{codes_str}`.\nProcesses should halt shortly.")
            self.logger.info(f"Stop signal sent for guild {guild_id} by {ctx.author}")
        else:
            await ctx.reply("ℹ️ No auto-redeem process is currently running for this server.")
    @commands.hybrid_command(name="force_ice_clean", description="Force clean up duplicate ICE alliance members", with_app_command=True)
    @commands.has_permissions(administrator=True)
    async def force_ice_clean(self, ctx: commands.Context):
        await ctx.defer()
        
        try:
            from db.mongo_adapters import _get_db, AutoRedeemMembersAdapter
            from datetime import datetime
            
            db_main = _get_db()
            alliance = db_main['alliance__alliance_list'].find_one({'name': 'ICE'})
            if not alliance:
                await ctx.send('ERROR: ICE alliance not found')
                return
            
            guild_id = alliance.get('discord_server_id')
            if not guild_id:
                await ctx.send('ERROR: Guild ID not found for ICE')
                return
            
            search_gid = [int(guild_id), str(guild_id)]
            unique_members_data = {}
            docs_to_delete = []
            total_found = 0
            
            for db in AutoRedeemMembersAdapter._get_target_dbs():
                coll = db['auto_redeem_members']
                # Force list conversion to block synchronous iteration
                docs = list(coll.find({'guild_id': {'$in': search_gid}}))
                total_found += len(docs)
                
                for doc in docs:
                    doc_id = doc['_id']
                    if 'fid' in doc and doc['fid'] and str(doc['fid']).lower() != 'none':
                        fid = str(doc['fid']).strip()
                        if fid not in unique_members_data:
                            unique_members_data[fid] = doc
                        else:
                            docs_to_delete.append((db.name, doc_id))
                    elif 'members' in doc and isinstance(doc['members'], list):
                        for m in doc['members']:
                            mfid = m.get('fid')
                            if mfid and str(mfid).lower() != 'none':
                                mfid = str(mfid).strip()
                                if mfid not in unique_members_data:
                                    unique_members_data[mfid] = {
                                        'guild_id': int(guild_id),
                                        'fid': mfid,
                                        'nickname': m.get('nickname', 'Unknown'),
                                        'furnace_lv': int(m.get('furnace_lv', 0)),
                                        'avatar_image': m.get('avatar_image', ''),
                                        'added_by': int(m.get('added_by', 0)),
                                        'added_at': m.get('added_at', datetime.utcnow().isoformat())
                                    }
                        docs_to_delete.append((db.name, doc_id))

            msg = f'Found {total_found} docs. Identified {len(unique_members_data)} unique FIDs.\n'
            msg += f'Docs to delete: {len(docs_to_delete)}\n'
            
            if docs_to_delete:
                deleted = 0
                for db_name, doc_id in docs_to_delete:
                    for d in AutoRedeemMembersAdapter._get_target_dbs():
                        if d.name == db_name:
                            res = d['auto_redeem_members'].delete_one({'_id': doc_id})
                            deleted += res.deleted_count
                            break
                msg += f'Deleted {deleted} duplicate/legacy docs.\n'
                
                main_coll = db_main['auto_redeem_members']
                upserted = 0
                for fid, data in unique_members_data.items():
                    data.pop('_id', None)
                    res = main_coll.update_one(
                        {'guild_id': int(guild_id), 'fid': str(fid)},
                        {'$set': data},
                        upsert=True
                    )
                    if res.upserted_id or res.modified_count > 0:
                        upserted += 1
                msg += f'Upserted {upserted} unique members.\n'
            
            await ctx.send(msg)
            self.logger.info(msg)
            
        except Exception as e:
            self.logger.error(f'Error in cleanup_ice: {e}')
            await ctx.send(f'Error: {e}')


    async def _auto_redeem_worker_loop(self):
        """Background worker that processes auto-redeems sequentially to prevent global memory spikes."""
        await self.bot.wait_until_ready()
        self.logger.info("👷 Auto-redeem queue worker started")
        
        while True:
            try:
                # Wait for a job
                job = await self.auto_redeem_queue.get()
                guild_id, code, is_recheck = job
                self.current_job = (guild_id, code)
                
                self.logger.info(f"⚙️ [WORKER] Processing queued auto-redeem: Guild {guild_id} | Code {code} (Recheck: {is_recheck})")
                
                try:
                    # Await the actual process (runs sequentially for this worker)
                    await self.process_auto_redeem(guild_id, code, is_recheck=is_recheck)
                except Exception as e:
                    self.logger.error(f"❌ [WORKER] Error processing guild {guild_id} for code {code}: {e}")
                finally:
                    self.current_job = None
                    self.processed_jobs_count += 1
                    # Mark this task as done and update pending count for faster status reporting
                    self.auto_redeem_queue.task_done()
                    await self._decrement_pending_jobs(code)
                    
                    # Small delay between guilds to let memory/network settle
                    await asyncio.sleep(2.0)
                    
            except asyncio.CancelledError:
                self.logger.info("🛑 Auto-redeem worker stopped.")
                break
            except Exception as e:
                self.current_job = None
                self.logger.error(f"❌ [WORKER] Critical error in queue loop: {e}")
                await asyncio.sleep(5.0)

    async def _safe_edit_message(self, interaction: discord.Interaction, **kwargs):
        """Safely edit an interaction message, falling back to followup if already acknowledged."""
        try:
            await interaction.response.edit_message(**kwargs)
        except discord.errors.HTTPException as e:
            if e.code == 40060:  # Interaction already acknowledged
                try:
                    await interaction.followup.send(ephemeral=True, **kwargs)
                except Exception as fe:
                    self.logger.warning(f"Followup also failed after 40060: {fe}")
            else:
                raise

    async def _sync_mongo_to_sqlite_on_startup(self):
        """At startup, pull ALL member/settings data from MongoDB into SQLite for robustness."""
        await self.bot.wait_until_ready()
        if not mongo_enabled():
            return
        
        try:
            self.logger.info("🔄 [STARTUP] Syncing MongoDB auto-redeem data to SQLite...")
            db = _get_db()
            
            # --- Members (handle both V1 array-style and V2 flat docs) ---
            synced_members = 0
            skipped = 0
            try:
                all_member_docs = list(db['auto_redeem_members'].find({}))
                self.logger.info(f"📊 [STARTUP] Found {len(all_member_docs)} member docs in MongoDB")
                
                for doc in all_member_docs:
                    try:
                        guild_id = int(doc.get('guild_id', 0))
                        if not guild_id:
                            skipped += 1
                            continue
                        
                        fid = doc.get('fid')
                        
                        if fid and str(fid).strip() and str(fid).strip().lower() != 'none':
                            # V2 flat doc - direct member
                            fid_str = str(fid).strip()
                            raw_nick = doc.get('nickname', 'Unknown')
                            if isinstance(raw_nick, dict): raw_nick = raw_nick.get('nickname', 'Unknown')
                            nickname = str(raw_nick) if raw_nick else 'Unknown'
                            furnace_lv = int(doc.get('furnace_lv', 0) or 0)
                            avatar_image = doc.get('avatar_image', '') or ''
                            added_by = doc.get('added_by')
                            added_at = str(doc.get('added_at', '') or '')
                            self.cursor.execute("""
                                INSERT OR REPLACE INTO auto_redeem_members
                                (guild_id, fid, nickname, furnace_lv, avatar_image, added_by, added_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                            """, (guild_id, fid_str, nickname, furnace_lv, avatar_image, added_by, added_at))
                            synced_members += 1
                        
                        elif 'members' in doc and isinstance(doc['members'], list):
                            # V1 array-style doc - embedded members list
                            for member in doc['members']:
                                try:
                                    m_fid = member.get('fid')
                                    if not m_fid or str(m_fid).strip().lower() == 'none':
                                        continue
                                    m_fid_str = str(m_fid).strip()
                                    raw_nick = member.get('nickname', 'Unknown')
                                    if isinstance(raw_nick, dict): raw_nick = raw_nick.get('nickname', 'Unknown')
                                    m_nick = str(raw_nick) if raw_nick else 'Unknown'
                                    m_fl = int(member.get('furnace_lv', 0) or 0)
                                    m_avatar = member.get('avatar_image', '') or ''
                                    m_added_by = member.get('added_by')
                                    m_added_at = str(member.get('added_at', '') or '')
                                    self.cursor.execute("""
                                        INSERT OR REPLACE INTO auto_redeem_members
                                        (guild_id, fid, nickname, furnace_lv, avatar_image, added_by, added_at)
                                        VALUES (?, ?, ?, ?, ?, ?, ?)
                                    """, (guild_id, m_fid_str, m_nick, m_fl, m_avatar, m_added_by, m_added_at))
                                    synced_members += 1
                                except Exception as inner_e:
                                    self.logger.warning(f"Failed to sync V1 member: {inner_e}")
                                    
                    except Exception as me:
                        self.logger.warning(f"Failed to sync member doc to SQLite: {me}")
                
                self.giftcode_db.commit()
                self.logger.info(f"✅ [STARTUP] Synced {synced_members} member records from MongoDB to SQLite (skipped {skipped})")
            except Exception as e:
                self.logger.error(f"❌ [STARTUP] Member sync failed: {e}")

            # --- Settings ---
            synced_settings = 0
            try:
                if AutoRedeemSettingsAdapter:
                    all_settings = AutoRedeemSettingsAdapter.get_all_settings()
                    for s in (all_settings or []):
                        try:
                            guild_id = int(s.get('guild_id', 0))
                            enabled = 1 if s.get('enabled', False) else 0
                            updated_by = s.get('updated_by')
                            updated_at = str(s.get('updated_at', '') or '')
                            if not guild_id:
                                continue
                            self.cursor.execute("""
                                INSERT OR REPLACE INTO auto_redeem_settings
                                (guild_id, enabled, updated_by, updated_at)
                                VALUES (?, ?, ?, ?)
                            """, (guild_id, enabled, updated_by, updated_at))
                            synced_settings += 1
                        except Exception as se:
                            self.logger.warning(f"Failed to sync settings for guild {s.get('guild_id')}: {se}")
                    self.giftcode_db.commit()
                    self.logger.info(f"✅ [STARTUP] Synced {synced_settings} guild settings from MongoDB to SQLite")
            except Exception as e:
                self.logger.error(f"❌ [STARTUP] Settings sync failed: {e}")

        except Exception as e:
            self.logger.error(f"❌ [STARTUP] MongoDB→SQLite sync failed: {e}")

    async def fetch_codes_from_api(self):
        """Fetch gift codes from the API"""
        try:
            connector = aiohttp.TCPConnector(ssl=self.ssl_context)
            async with aiohttp.ClientSession(connector=connector) as session:
                headers = {
                    'X-API-Key': self.api_key,
                    'Content-Type': 'application/json'
                }
                
                await self._wait_for_rate_limit()
                
                async with session.get(self.api_url, headers=headers) as response:
                    if response.status != 200:
                        self.logger.error(f"API request failed with status {response.status}")
                        return []
                    
                    response_text = await response.text()
                    result = json.loads(response_text)
                    
                    if 'error' in result or 'detail' in result:
                        error_msg = result.get('error', result.get('detail', 'Unknown error'))
                        self.logger.error(f"API returned error: {error_msg}")
                        return []
                    
                    api_giftcodes = result.get('codes', [])
                    self.logger.info(f"Fetched {len(api_giftcodes)} codes from API")
                    
                    # Parse codes
                    valid_codes = []
                    for code_line in api_giftcodes:
                        parts = code_line.strip().split()
                        if len(parts) != 2:
                            continue
                        
                        code, date_str = parts
                        if not re.match("^[a-zA-Z0-9]+$", code):
                            continue
                        
                        try:
                            date_obj = datetime.strptime(date_str, "%d.%m.%Y")
                            valid_codes.append((code, date_obj.strftime("%Y-%m-%d")))
                        except ValueError:
                            continue
                    
                    return valid_codes
        except Exception as e:
            self.logger.exception(f"Error fetching codes from API: {e}")
            return []

    @tasks.loop(seconds=60)  # Check every minute
    async def api_check_task(self):
        """Periodically check API for new gift codes"""
        try:
            self.logger.info("Starting API check for new gift codes")
            
            # Fetch codes from API
            api_codes = await self.fetch_codes_from_api()
            
            if not api_codes:
                self.logger.warning("No codes fetched from API - API might be empty or down")
                return
            
            self.logger.info(f"Successfully fetched {len(api_codes)} codes from API")
            
            # Also fetch from MongoDB if enabled
            db_codes_data = {} # code.upper() -> {'date': date, 'processed': bool}
            
            # Fetch from SQLite
            self.cursor.execute("SELECT giftcode, date, auto_redeem_processed FROM gift_codes")
            for row in self.cursor.fetchall():
                db_codes_data[str(row[0]).upper()] = {'date': row[1], 'processed': bool(row[2])}
            
            if mongo_enabled() and GiftCodesAdapter:
                try:
                    # GiftCodesAdapter.get_all_with_status() returns list of dicts
                    mongo_codes = GiftCodesAdapter.get_all_with_status()
                    if mongo_codes:
                        for c in mongo_codes:
                            code_up = str(c.get('giftcode', '')).upper()
                            if code_up:
                                db_codes_data[code_up] = {
                                    'date': c.get('date'),
                                    'processed': bool(c.get('auto_redeem_processed', False))
                                }
                except Exception as e:
                    self.logger.error(f"Failed to fetch codes from Mongo: {e}")

            self.logger.info(f"Found {len(db_codes_data)} existing codes in database(s)")
            
            # Find new codes
            new_codes = []
            for code, date in api_codes:
                code_up = str(code).upper()
                if code_up not in db_codes_data:
                    new_codes.append((code, date))
                else:
                    stored_date = db_codes_data[code_up]['date']
                    if stored_date and date and date != stored_date:
                        self.logger.info(f"♻️ Re-issued code detected: {code} (Old Date: {stored_date}, New Date: {date}). Re-triggering auto-redeem.")
                        new_codes.append((code, date))
                        # Reset processed flag in DB so it actually runs
                        try:
                            # Update SQLite
                            self.cursor.execute("UPDATE gift_codes SET auto_redeem_processed = 0, date = ? WHERE giftcode = ?", (date, code))
                            # Update MongoDB
                            if mongo_enabled() and GiftCodesAdapter:
                                try:
                                    db = _get_db()
                                    db['gift_codes'].update_one({'_id': code}, {'$set': {'auto_redeem_processed': False, 'date': date}})
                                except Exception as me:
                                    self.logger.error(f"Failed to reset Mongo processed status for {code}: {me}")
                        except Exception as e:
                            self.logger.error(f"Failed to reset re-issued code {code}: {e}")
            
            if not new_codes:
                self.logger.info(f"No new codes found. All {len(api_codes)} API codes already in database and processed")
                return
            
            self.logger.info(f"Found {len(new_codes)} codes to process (new or re-issued)!")
            
            # Add new codes to database with auto_redeem_processed = 0
            for code, date in new_codes:
                try:
                    # Insert into SQLite
                    self.cursor.execute(
                        "INSERT OR IGNORE INTO gift_codes (giftcode, date, validation_status, added_at, auto_redeem_processed) VALUES (?, ?, ?, ?, ?)",
                        (code, date, "validated", datetime.now(), 0)
                    )
                    self.logger.info(f"Added new code to SQLite: {code}")
                    
                    # CRITICAL: Also insert into MongoDB if enabled
                    if mongo_enabled() and GiftCodesAdapter and _get_db:
                        try:
                            # Insert the code with auto_redeem_processed = False
                            db = _get_db()
                            if db:
                                db[GiftCodesAdapter.COLL].update_one(
                                    {'_id': code},
                                    {
                                        '$set': {
                                            'date': date,
                                            'validation_status': 'validated',
                                            'auto_redeem_processed': False,
                                            'created_at': datetime.utcnow().isoformat(),
                                            'updated_at': datetime.utcnow().isoformat()
                                        }
                                    },
                                    upsert=True
                                )
                                self.logger.info(f"✅ Added new code to MongoDB: {code}")
                        except Exception as mongo_err:
                            self.logger.error(f"⚠️ Failed to add code {code} to MongoDB: {mongo_err}")
                    
                except Exception as e:
                    self.logger.error(f"Error inserting code {code}: {e}")
            
            self.giftcode_db.commit()
            self.logger.info(f"Committed {len(new_codes)} new codes to database")
            
            # Notify global admins
            await self.notify_admins_new_codes(new_codes)
            
            # CRITICAL: Trigger auto-redeem for the new codes
            self.logger.info(f"🔔 Triggering auto-redeem for {len(new_codes)} new codes from API...")
            await self.trigger_auto_redeem_for_new_codes(new_codes)
            
        except Exception as e:
            self.logger.exception(f"Error in API check task: {e}")
    
    @api_check_task.before_loop
    async def before_api_check(self):
        """Wait for bot to be ready before starting API checks"""
        await self.bot.wait_until_ready()
        self.logger.info("Gift code API check task started")
        
        # Cleanup members with null/empty FIDs from all guilds
        self.logger.info("🧹 Cleaning up members with null/empty FIDs...")
        try:
            # Note: AutoRedeemMembersAdapter might have its own cleanup, or we manually do it
            if mongo_enabled() and _get_db:
                db = _get_db()
                if db:
                    res = db['auto_redeem_members'].delete_many({'fid': {'$in': [None, '', 'None', 'none']}})
                    if res.deleted_count > 0:
                        self.logger.info(f"✅ Removed {res.deleted_count} invalid members from auto-redeem lists in MongoDB")
        except Exception as e:
            self.logger.error(f"Failed to cleanup null members: {e}")
        
        # Sync auto-redeem settings from SQLite to MongoDB (for migration on first startup)
        await self.sync_auto_redeem_settings_to_mongo()
        
        # Sync gift codes from MongoDB to SQLite (CRITICAL for Render persistence)
        await self.sync_gift_codes_from_mongo_to_sqlite()
        
        # Process any existing unprocessed codes on startup
        await self.process_existing_codes_on_startup()

    async def sync_auto_redeem_settings_to_mongo(self):
        """Sync auto-redeem settings from SQLite to MongoDB on startup."""
        try:
            if not mongo_enabled() or not AutoRedeemSettingsAdapter:
                return
            
            self.cursor.execute("SELECT guild_id, enabled, updated_by, updated_at FROM auto_redeem_settings WHERE enabled = 1")
            sqlite_settings = self.cursor.fetchall()
            
            for row in sqlite_settings:
                guild_id = row[0]
                AutoRedeemSettingsAdapter.set_enabled(guild_id, True, row[2] or 0)
        except Exception as e:
            self.logger.error(f"Error syncing settings to mongo: {e}")

    async def sync_gift_codes_from_mongo_to_sqlite(self):
        """Sync gift codes from MongoDB to SQLite on startup."""
        try:
            if not mongo_enabled() or not GiftCodesAdapter:
                return

            mongo_codes = GiftCodesAdapter.get_all()
            if not mongo_codes:
                return
            
            for code_data in mongo_codes:
                try:
                    code = code_data.get('giftcode') or code_data.get('_id')
                    date = code_data.get('date', '')
                    processed = 1 if code_data.get('auto_redeem_processed', False) else 0
                    
                    self.cursor.execute(
                        "INSERT OR REPLACE INTO gift_codes (giftcode, date, auto_redeem_processed, validation_status) VALUES (?, ?, ?, ?)",
                        (code, date, processed, code_data.get('validation_status', 'validated'))
                    )
                except Exception:
                    continue
            self.giftcode_db.commit()
        except Exception as e:
            self.logger.error(f"Error syncing codes from mongo: {e}")

    async def process_existing_codes_on_startup(self):
        """Find codes that are marked as unprocessed and trigger auto-redeem for them."""
        try:
            self.logger.info("🔍 Checking for unprocessed codes on startup...")
            self.cursor.execute("SELECT giftcode, date FROM gift_codes WHERE auto_redeem_processed = 0")
            unprocessed = self.cursor.fetchall()
            
            if unprocessed:
                self.logger.info(f"🔔 Found {len(unprocessed)} unprocessed codes. Triggering auto-redeem...")
                # We do this after a small delay to ensure bot is fully stable
                await asyncio.sleep(10)
                await self.trigger_auto_redeem_for_new_codes(unprocessed)
            else:
                self.logger.info("✅ All existing codes are already processed.")
        except Exception as e:
            self.logger.error(f"Error in process_existing_codes_on_startup: {e}")

    async def trigger_auto_redeem_for_new_codes(self, new_codes, is_recheck=False):
        """Trigger auto-redeem for all guilds with auto-redeem enabled"""
        try:
            self.logger.info(f"📥 Triggering auto-redeem for {len(new_codes)} codes")
            
            enabled_guilds = []
            self.cursor.execute("SELECT guild_id FROM auto_redeem_settings WHERE enabled = 1")
            enabled_guilds = self.cursor.fetchall()
            
            if not enabled_guilds:
                self.logger.warning("No guilds have auto-redeem enabled.")
                return

            for code, date in new_codes:
                await self._process_single_code(code, date, enabled_guilds, is_recheck)
        except Exception as e:
            self.logger.exception(f"Error in trigger_auto_redeem_for_new_codes: {e}")

    async def _process_single_code(self, code, date, enabled_guilds, is_recheck):
        """Process a single gift code for all enabled guilds."""
        try:
            already_processed = await self._is_code_already_processed(code)
            if already_processed and not is_recheck:
                return

            for (guild_id,) in enabled_guilds:
                self.auto_redeem_queue.put_nowait((guild_id, code, is_recheck))
            
            await self.wait_for_code_completion(code, len(enabled_guilds))
        except Exception as e:
            self.logger.exception(f"Error processing single code {code}: {e}")

    async def _is_code_already_processed(self, code):
        """Check if a code is marked as fully processed in MongoDB or SQLite."""
        self.cursor.execute("SELECT auto_redeem_processed FROM gift_codes WHERE giftcode = ?", (code,))
        result = self.cursor.fetchone()
        return result and result[0]

    async def wait_for_code_completion(self, code, expected_jobs):
        """Wait until the number of completed jobs for a code reaches the expected count."""
        if expected_jobs <= 0: return

        self._pending_jobs_per_code[code] = expected_jobs
        self._completion_events[code] = asyncio.Event()

        try:
            await asyncio.wait_for(self._completion_events[code].wait(), timeout=3600)
            await self._mark_code_done(code)
        except asyncio.TimeoutError:
            self.logger.error(f"Timeout waiting for code {code}")
        finally:
            self._pending_jobs_per_code.pop(code, None)
            self._completion_events.pop(code, None)

    async def _decrement_pending_jobs(self, code):
        """Decrement pending jobs and set event if all jobs for a code are done."""
        if code in self._pending_jobs_per_code:
            self._pending_jobs_per_code[code] -= 1
            if self._pending_jobs_per_code[code] <= 0:
                if code in self._completion_events:
                    self._completion_events[code].set()

    async def _mark_code_done(self, code):
        """Mark code as processed in DBs."""
        if mongo_enabled() and GiftCodesAdapter:
            try:
                GiftCodesAdapter.mark_code_processed(code)
            except Exception: pass
        
        self.cursor.execute("UPDATE gift_codes SET auto_redeem_processed = 1 WHERE giftcode = ?", (code,))
        self.giftcode_db.commit()

    async def mark_code_invalid(self, code):
        """Mark a code as invalid globally."""
        if mongo_enabled() and GiftCodesAdapter:
            try: GiftCodesAdapter.update_status(code, 'invalid')
            except Exception: pass
                
        self.cursor.execute("UPDATE gift_codes SET validation_status = 'invalid', auto_redeem_processed = 1 WHERE giftcode = ?", (code,))
        self.giftcode_db.commit()


async def setup(bot):
    await bot.add_cog(ManageGiftCode(bot))
