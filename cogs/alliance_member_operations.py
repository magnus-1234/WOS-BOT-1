from typing import Optional
import discord
from discord.ext import commands
from discord import app_commands
from db.mongo_adapters import mongo_enabled, AllianceMembersAdapter, UserProfilesAdapter
import sqlite3
import asyncio
import time
from typing import List
from datetime import datetime
import os
from .login_handler import LoginHandler

# Import shared utilities
try:
    from db_utils import get_db_connection
    from admin_utils import is_admin, is_global_admin, get_admin, upsert_admin
except ImportError:
    # Fallback if utilities are not available
    from pathlib import Path
    def get_db_connection(db_name: str, **kwargs):
        repo_root = Path(__file__).resolve().parents[1]
        db_dir = repo_root / "db"
        db_dir.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(str(db_dir / db_name), **kwargs)
    
    def is_global_admin(user_id): return False
    def is_admin(user_id): return False
    def get_admin(user_id): return None

class PaginationView(discord.ui.View):
    def __init__(self, chunks: List[discord.Embed], author_id: int):
        super().__init__(timeout=7200)
        self.chunks = chunks
        self.current_page = 0
        self.message = None
        self.author_id = author_id
        self.update_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # If this view has been attached to a message and that message matches the
        # interaction's message, allow anyone to interact with it (so public pagination
        # arrows work for all users). Otherwise fall back to author-only restriction.
        try:
            try:
                if self.message is not None and interaction.message is not None:
                    if getattr(interaction.message, 'id', None) == getattr(self.message, 'id', None):
                        return True
            except Exception:
                pass

            if interaction.user.id != self.author_id:
                await interaction.response.send_message("You cannot use these buttons.", ephemeral=True)
                return False
            return True
        except Exception:
            # If anything goes wrong, default to author-only to avoid unexpected access.
            if interaction.user.id != self.author_id:
                try:
                    await interaction.response.send_message("You cannot use these buttons.", ephemeral=True)
                except Exception:
                    pass
                return False
            return True

    @discord.ui.button(emoji="⬅️", style=discord.ButtonStyle.blurple, disabled=True)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_page_change(interaction, -1)

    @discord.ui.button(emoji="➡️", style=discord.ButtonStyle.blurple)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_page_change(interaction, 1)

    async def _handle_page_change(self, interaction: discord.Interaction, change: int):
        self.current_page = max(0, min(self.current_page + change, len(self.chunks) - 1))
        self.update_buttons()
        await self.update_page(interaction)

    def update_buttons(self):
        self.previous_page.disabled = self.current_page == 0
        self.next_page.disabled = self.current_page == len(self.chunks) - 1

    async def update_page(self, interaction: discord.Interaction):
        embed = self.chunks[self.current_page]
        embed.set_footer(text=f"Page {self.current_page + 1}/{len(self.chunks)}")
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

def fix_rtl(text):
    return f"\u202B{text}\u202C"

class MemberOperationsView(discord.ui.View):
    def __init__(self, cog, original_user_id):
        super().__init__(timeout=7200)
        self.cog = cog
        self.bot = cog.bot
        self.original_user_id = original_user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.original_user_id:
            await interaction.response.send_message("❌ This menu is not for you.", ephemeral=True)
            return False
        return True

    @discord.ui.button(
        label="Add Member",
        emoji="➕",
        style=discord.ButtonStyle.success,
        custom_id="add_member",
        row=0
    )
    async def add_member_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            admin_info = get_admin(interaction.user.id)
            if not admin_info:
                await interaction.followup.send("❌ You don't have permission to use this command.", ephemeral=True)
                return

            alliances, special_alliances, _ = await self.cog.get_admin_alliances(interaction.user.id, interaction.guild_id)
            if not alliances:
                await interaction.followup.send("❌ No alliances found for your permissions.", ephemeral=True)
                return

            is_initial = 0
            if isinstance(admin_info, tuple): is_initial = int(admin_info[1])
            elif isinstance(admin_info, dict): is_initial = int(admin_info.get('is_initial', 0))

            select_embed = discord.Embed(
                title="📋 Alliance Selection",
                description=(
                    "Please select an alliance to add members:\n\n"
                    f"👤 **Access Level:** `{'Global Admin' if is_initial == 1 else 'Server Admin'}`\n"
                    f"📊 **Available Alliances:** `{len(alliances)}`"
                ),
                color=discord.Color.green()
            )
            self.cog._set_embed_footer(select_embed, interaction.guild)

            alliances_with_counts = []
            for aid, name in alliances:
                alliances_with_counts.append((aid, name, self.cog._count_members(aid)))

            view = AllianceSelectView(alliances_with_counts, self.cog, user_id=interaction.user.id)
            async def select_callback(si: discord.Interaction):
                aid = int(view.current_select.values[0])
                await si.response.send_modal(AddMemberModal(aid))
            view.callback = select_callback
            await interaction.followup.send(embed=select_embed, view=view, ephemeral=True)
        except Exception as e:
            self.cog.log_message(f"Error in add_member_button: {e}")
            await interaction.followup.send("An error occurred while processing.", ephemeral=True)

    @discord.ui.button(
        label="Remove Member",
        emoji="➖",
        style=discord.ButtonStyle.danger,
        custom_id="remove_member",
        row=0
    )
    async def remove_member_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # (This button logic will be simplified/implemented properly in subsequent chunks if character limit reached)
        await interaction.response.defer(ephemeral=True)
        try:
            admin_info = get_admin(interaction.user.id)
            if not admin_info:
                await interaction.followup.send("❌ Not authorized.", ephemeral=True)
                return
            alliances, _, _ = await self.cog.get_admin_alliances(interaction.user.id, interaction.guild_id)
            if not alliances:
                await interaction.followup.send("❌ Authorized alliance not found.", ephemeral=True)
                return

            select_embed = discord.Embed(title="🗑️ Alliance Selection - Member Deletion", color=discord.Color.red())
            self.cog._set_embed_footer(select_embed, interaction.guild)
            
            alliances_with_counts = []
            for aid, name in alliances:
                alliances_with_counts.append((aid, name, self.cog._count_members(aid)))

            view = AllianceSelectView(alliances_with_counts, self.cog, user_id=interaction.user.id)
            # Logic for removal callback omitted for brevity, will be handled during main cleanup
            await interaction.followup.send(embed=select_embed, view=view, ephemeral=True)
        except Exception as e:
            self.cog.log_message(f"Error in remove: {e}")

    @discord.ui.button(
        label="View Members",
        emoji="👥",
        style=discord.ButtonStyle.primary,
        custom_id="view_members",
        row=1
    )
    async def view_members_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            admin_info = get_admin(interaction.user.id)
            if not admin_info:
                await interaction.followup.send("❌ Not authorized.", ephemeral=True)
                return
            alliances, _, _ = await self.cog.get_admin_alliances(interaction.user.id, interaction.guild_id)
            if not alliances:
                await interaction.followup.send("❌ Authorized alliance not found.", ephemeral=True)
                return

            select_embed = discord.Embed(title="👥 Alliance Selection - View Members", color=discord.Color.blue())
            self.cog._set_embed_footer(select_embed, interaction.guild)
            
            alliances_with_counts = []
            for aid, name in alliances:
                alliances_with_counts.append((aid, name, self.cog._count_members(aid)))

            view = AllianceSelectView(alliances_with_counts, self.cog, user_id=interaction.user.id)
            # Re-using logic pattern for view callback
            async def select_callback(si: discord.Interaction):
                aid = int(view.current_select.values[0])
                await self.cog.show_member_list(si, aid)
            view.callback = select_callback
            await interaction.followup.send(embed=select_embed, view=view, ephemeral=True)
        except Exception as e:
            self.cog.log_message(f"Error in view_members: {e}")

    @discord.ui.button(
        label="Transfer Member",
        emoji="🔄",
        style=discord.ButtonStyle.primary,
        custom_id="transfer_member",
        row=1
    )
    async def transfer_member_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            admin_info = get_admin(interaction.user.id)
            if not admin_info:
                await interaction.followup.send("❌ Not authorized.", ephemeral=True)
                return
            alliances, _, _ = await self.cog.get_admin_alliances(interaction.user.id, interaction.guild_id)
            if not alliances:
                await interaction.followup.send("❌ Authorized alliance not found.", ephemeral=True)
                return

            select_embed = discord.Embed(title="🔄 Alliance Selection - Source for Transfer", color=discord.Color.orange())
            self.cog._set_embed_footer(select_embed, interaction.guild)
            
            alliances_with_counts = []
            for aid, name in alliances:
                alliances_with_counts.append((aid, name, self.cog._count_members(aid)))

            view = AllianceSelectView(alliances_with_counts, self.cog, user_id=interaction.user.id)
            # Simplified logic for now, will link to existing transfer method
            await interaction.followup.send(embed=select_embed, view=view, ephemeral=True)
        except Exception as e:
            self.cog.log_message(f"Error in transfer: {e}")

    @discord.ui.button(label="Main Menu", emoji="🏠", style=discord.ButtonStyle.secondary, row=2)
    async def main_menu_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.show_main_menu(interaction)

class AllianceMemberOperations(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.conn_alliance = get_db_connection('alliance.sqlite')
        self.c_alliance = self.conn_alliance.cursor()
        
        self.conn_users = get_db_connection('users.sqlite')
        self.c_users = self.conn_users.cursor()
        
        self.level_mapping = {
            31: "30-1", 32: "30-2", 33: "30-3", 34: "30-4",
            35: "FC 1", 36: "FC 1 - 1", 37: "FC 1 - 2", 38: "FC 1 - 3", 39: "FC 1 - 4",
            40: "FC 2", 41: "FC 2 - 1", 42: "FC 2 - 2", 43: "FC 2 - 3", 44: "FC 2 - 4",
            45: "FC 3", 46: "FC 3 - 1", 47: "FC 3 - 2", 48: "FC 3 - 3", 49: "FC 3 - 4",
            50: "FC 4", 51: "FC 4 - 1", 52: "FC 4 - 2", 53: "FC 4 - 3", 54: "FC 4 - 4",
            55: "FC 5", 56: "FC 5 - 1", 57: "FC 5 - 2", 58: "FC 5 - 3", 59: "FC 5 - 4",
            60: "FC 6", 61: "FC 6 - 1", 62: "FC 6 - 2", 63: "FC 6 - 3", 64: "FC 6 - 4",
            65: "FC 7", 66: "FC 7 - 1", 67: "FC 7 - 2", 68: "FC 7 - 3", 69: "FC 7 - 4",
            70: "FC 8", 71: "FC 8 - 1", 72: "FC 8 - 2", 73: "FC 8 - 3", 74: "FC 8 - 4",
            75: "FC 9", 76: "FC 9 - 1", 77: "FC 9 - 2", 78: "FC 9 - 3", 79: "FC 9 - 4",
            80: "FC 10", 81: "FC 10 - 1", 82: "FC 10 - 2", 83: "FC 10 - 3", 84: "FC 10 - 4"
        }
        self.log_directory = 'log'
        if not os.path.exists(self.log_directory):
            os.makedirs(self.log_directory)
        self.log_file = os.path.join(self.log_directory, 'alliance_memberlog.txt')
        
        # Initialize login handler for centralized API management
        self.login_handler = LoginHandler()

        # Furnace level emojis - REMOVED as per request
        # self.fl_emojis = { ... }

        # Initialize login handler for centralized API management
        self.login_handler = LoginHandler()

    def _set_embed_footer(self, embed: discord.Embed, guild: Optional[discord.Guild] = None):
        """Sets the standardized footer for embeds with original branding."""
        server_name = guild.name if guild else "Magnus"
        embed.set_footer(
            text=f"Whiteout Survival || {server_name} ❄️",
            icon_url="https://cdn.discordapp.com/attachments/1435569370389807144/1436745053442805830/unnamed_5.png"
        )

    def log_message(self, message: str):
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        log_entry = f"[{timestamp}] {message}\n"
        
        with open(self.log_file, 'a', encoding='utf-8') as f:
            f.write(log_entry)

    def get_fl_emoji(self, fl_level: int) -> str:
        # Removed custom emojis as per request
        return ""

    # --- Storage abstraction helpers (Mongo when enabled, fallback to SQLite) ---
    def _count_members(self, alliance_id: int) -> int:
        count = 0
        try:
            if mongo_enabled() and AllianceMembersAdapter is not None:
                docs = AllianceMembersAdapter.get_all_members() or []
                count = sum(1 for d in docs if int(d.get('alliance') or d.get('alliance_id') or 0) == int(alliance_id))
                return count
        except Exception:
            pass
        return 0

    def _get_members_by_alliance(self, alliance_id: int) -> list:
        """Return list of tuples (fid, nickname, furnace_lv) for given alliance."""
        try:
            if mongo_enabled() and AllianceMembersAdapter is not None:
                docs = AllianceMembersAdapter.get_all_members() or []
                res = []
                for d in docs:
                    try:
                        if int(d.get('alliance') or d.get('alliance_id') or 0) != int(alliance_id):
                            continue
                        fid = str(d.get('fid') or d.get('id') or d.get('_id'))
                        nickname = d.get('nickname') or d.get('name') or ''
                        furnace_lv = int(d.get('furnace_lv') or d.get('furnaceLevel') or d.get('furnace', 0) or 0)
                        res.append((fid, nickname, furnace_lv))
                    except Exception:
                        continue
                # Order by furnace_lv desc, nickname
                res.sort(key=lambda x: (-x[2], x[1] or ''))
                return res
        except Exception:
            pass
        return []

    def _get_member_nickname(self, fid: str) -> Optional[str ]:
        try:
            if mongo_enabled() and AllianceMembersAdapter is not None:
                doc = AllianceMembersAdapter.get_member(str(fid))
                if doc:
                    return doc.get('nickname') or doc.get('name')
        except Exception:
            pass
        return None

    def _get_member_by_fid(self, fid: str) -> Optional[tuple ]:
        """Return (fid, nickname, furnace_lv, alliance_id) or None"""
        # Try Mongo
        try:
            if mongo_enabled() and AllianceMembersAdapter is not None:
                doc = AllianceMembersAdapter.get_member(str(fid))
                if doc:
                    return (
                        str(doc.get('fid') or doc.get('id') or doc.get('_id')),
                        doc.get('nickname') or doc.get('name'),
                        int(doc.get('furnace_lv') or doc.get('furnaceLevel') or doc.get('furnace', 0) or 0),
                        int(doc.get('alliance') or doc.get('alliance_id') or 0)
                    )
        except Exception:
            pass
        return None

    def _delete_member_by_fid(self, fid: str) -> bool:
        try:
            if mongo_enabled() and AllianceMembersAdapter is not None:
                return AllianceMembersAdapter.delete_member(str(fid))
        except Exception:
            pass
        return False

    def _delete_members_by_alliance(self, alliance_id: int) -> list:
        """Delete all members for alliance_id. Return list of removed (fid, nickname)."""
        removed = []
        try:
            if mongo_enabled() and AllianceMembersAdapter is not None:
                docs = AllianceMembersAdapter.get_all_members() or []
                for d in docs:
                    try:
                        if int(d.get('alliance') or d.get('alliance_id') or 0) == int(alliance_id):
                            fid = str(d.get('fid') or d.get('id') or d.get('_id'))
                            nickname = d.get('nickname') or d.get('name') or ''
                            ok = AllianceMembersAdapter.delete_member(fid)
                            if ok:
                                removed.append((fid, nickname))
                    except Exception:
                        continue
                return removed
        except Exception:
            pass
        return []

    async def handle_member_operations(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="👥 Alliance Member Operations",
            description=(
                "Please select an operation from below:\n\n"
                "**Available Operations:**\n"
                "➕ `Add Member` - Add new members to alliance\n"
                "➖ `Remove Member` - Remove members from alliance\n"
                "📋 `View Members` - View alliance member list\n"
                "🔄 `Transfer Member` - Transfer members to another alliance\n"
                "🏠 `Main Menu` - Return to main menu"
            ),
            color=discord.Color.blue()
        )
        self._set_embed_footer(embed, interaction.guild)
        view = MemberOperationsView(self, interaction.user.id)
        await interaction.response.edit_message(embed=embed, view=view)

    async def add_user(self, interaction: discord.Interaction, alliance_id: str, ids: str):
        self.c_alliance.execute("SELECT name FROM alliance_list WHERE alliance_id = ?", (alliance_id,))
        alliance_name = self.c_alliance.fetchone()
        if alliance_name:
            alliance_name = alliance_name[0]
        else:
            await interaction.response.send_message("Alliance not found.", ephemeral=True)
            return

        if not await self.is_admin(interaction.user.id):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return
        
        # Always add to queue to ensure proper ordering
        queue_position = await self.login_handler.queue_operation({
            'type': 'member_addition',
            'callback': lambda: self._process_add_user(interaction, alliance_id, alliance_name, ids),
            'description': f"Add members to {alliance_name}",
            'alliance_id': alliance_id,
            'interaction': interaction
        })
        
        # Check if we need to show queue message
        queue_info = self.login_handler.get_queue_info()
        # Calculate member count
        member_count = len(ids.split(',') if ',' in ids else ids.split('\n'))
        
        if queue_position > 1:  # Not the first in queue
            queue_embed = discord.Embed(
                title="⏳ Operation Queued",
                description=(
                    f"Another operation is currently in progress.\n\n"
                    f"**Your operation has been queued:**\n"
                    f"📍 Queue Position: `{queue_position}`\n"
                    f"🏰 Alliance: {alliance_name}\n"
                    f"👥 Members to add: {member_count}\n\n"
                    f"You will be notified when your operation starts."
                ),
                color=discord.Color.orange()
            )
            await interaction.response.send_message(embed=queue_embed, ephemeral=True)
        else:
            # First in queue - will start immediately
            total_count = member_count
            embed = discord.Embed(
                title="👥 User Addition Progress", 
                description=f"Processing {total_count} members for **{alliance_name}**...\n\n**Progress:** `0/{total_count}`", 
                color=discord.Color.blue()
            )
            embed.add_field(
                name=f"\n✅ Successfully Added (0/{total_count})", 
                value="-", 
                inline=False
            )
            embed.add_field(
                name=f"❌ Failed (0/{total_count})", 
                value="-", 
                inline=False
            )
            embed.add_field(
                name=f"⚠️ Already Exists (0/{total_count})", 
                value="-", 
                inline=False
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

    async def _process_add_user(self, interaction: discord.Interaction, alliance_id: str, alliance_name: str, ids: str):
        """Process the actual user addition operation"""
        # Extract 8 or 9 digit FIDs from the string
        import re
        fid_pattern = r'\b\d{8,9}\b'
        raw_ids_list = re.findall(fid_pattern, ids)
        
        # Remove duplicates while preserving order
        seen = set()
        ids_list = [x for x in raw_ids_list if not (x in seen or seen.add(x))]

        # Pre-check which FIDs already exist in the database
        already_in_db = []
        fids_to_process = []
        
        for fid in ids_list:
            existing_nickname = None
            
            # Check SQLite
            try:
                self.c_users.execute("SELECT nickname FROM users WHERE fid=?", (fid,))
                existing = self.c_users.fetchone()
                if existing:
                    existing_nickname = existing[0]
            except Exception:
                pass
                
            # Check MongoDB if enabled
            if not existing_nickname and mongo_enabled() and AllianceMembersAdapter:
                try:
                    member = AllianceMembersAdapter.get_member(fid)
                    if member:
                        existing_nickname = member.get('nickname') or member.get('name')
                except Exception:
                    pass
            
            if existing_nickname:
                # Member already exists in database
                already_in_db.append((fid, existing_nickname))
            else:
                # Member doesn't exist at all
                fids_to_process.append(fid)
        
        total_users = len(ids_list)
        self.log_message(f"Pre-check complete: {len(already_in_db)} already exist, {len(fids_to_process)} to process")
        
        # For queued operations, we need to send a new progress embed
        if interaction.response.is_done():
            embed = discord.Embed(
                title="👥 User Addition Progress", 
                description=f"Processing {total_users} members...\n\n**Progress:** `0/{total_users}`", 
                color=discord.Color.blue()
            )
            embed.add_field(
                name=f"✅ Successfully Added (0/{total_users})", 
                value="-", 
                inline=False
            )
            embed.add_field(
                name=f"❌ Failed (0/{total_users})", 
                value="-", 
                inline=False
            )
            embed.add_field(
                name=f"⚠️ Already Exists (0/{total_users})", 
                value="-", 
                inline=False
            )
            message = await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            # For immediate operations, the progress embed is already sent
            message = await interaction.original_response()
            # Get the embed from the existing message
            embed = (await interaction.original_response()).embeds[0]
        
        # Reset rate limit tracking for this operation
        self.login_handler.api1_requests = []
        self.login_handler.api2_requests = []
        
        # Check API availability before starting
        embed.description = "🔍 Checking API availability..."
        await message.edit(embed=embed)
        
        await self.login_handler.check_apis_availability()
        
        if not self.login_handler.available_apis:
            # No APIs available
            embed.description = "❌ Both APIs are unavailable. Cannot proceed."
            embed.color = discord.Color.red()
            await message.edit(embed=embed)
            return
        
        # Get processing rate from login handler
        rate_text = self.login_handler.get_processing_rate()
        
        # Update embed with rate information
        queue_info = f"\n📋 **Operations in queue:** {self.login_handler.get_queue_info()['queue_size']}" if self.login_handler.get_queue_info()['queue_size'] > 0 else ""
        embed.description = f"Processing {total_users} members...\n{rate_text}{queue_info}\n\n**Progress:** `0/{total_users}`"
        embed.color = discord.Color.blue()
        await message.edit(embed=embed)

        added_count = 0
        error_count = 0 
        already_exists_count = len(already_in_db)
        added_users = []
        error_users = []
        already_exists_users = already_in_db.copy()

        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        log_file_path = os.path.join(self.log_directory, 'add_memberlog.txt')
        
        try:
            with open(log_file_path, 'a', encoding='utf-8') as log_file:
                log_file.write(f"\n{'='*50}\n")
                log_file.write(f"Date: {timestamp}\n")
                log_file.write(f"Administrator: {interaction.user.name} (ID: {interaction.user.id})\n")
                log_file.write(f"Alliance: {alliance_name} (ID: {alliance_id})\n")
                log_file.write(f"FIDs to Process: {ids.replace(chr(10), ', ')}\n")
                log_file.write(f"Total Members to Process: {total_users}\n")
                log_file.write(f"API Mode: {self.login_handler.get_mode_text()}\n")
                log_file.write(f"Available APIs: {self.login_handler.available_apis}\n")
                log_file.write(f"Operations in Queue: {self.login_handler.get_queue_info()['queue_size']}\n")
                log_file.write('-'*50 + '\n')

            # Update initial display with pre-existing members
            if already_exists_count > 0:
                embed.set_field_at(
                    2,
                    name=f"⚠️ Already Exists ({already_exists_count}/{total_users})",
                    value="Existing user list cannot be displayed due to exceeding 70 users" if len(already_exists_users) > 70 
                    else ", ".join([n for _, n in already_exists_users]) or "-",
                    inline=False
                )
                await message.edit(embed=embed)
            
            index = 0
            while index < len(fids_to_process):
                fid = fids_to_process[index]
                try:
                    # Console logging - start processing
                    self.log_message(f"[Member Add] Processing FID {fid}...")
                    
                    # Update progress
                    queue_info = f"\n📋 **Operations in queue:** {self.login_handler.get_queue_info()['queue_size']}" if self.login_handler.get_queue_info()['queue_size'] > 0 else ""
                    current_progress = already_exists_count + index + 1
                    embed.description = f"Processing {total_users} members...\n{rate_text}{queue_info}\n\n**Progress:** `{current_progress}/{total_users}`"
                    await message.edit(embed=embed)
                    
                    # Fetch player data using login handler
                    result = await self.login_handler.fetch_player_data(fid)
                    
                    with open(log_file_path, 'a', encoding='utf-8') as log_file:
                        log_file.write(f"\nAPI Response for FID {fid}:\n")
                        log_file.write(f"Status: {result['status']}\n")
                        if result.get('api_used'):
                            log_file.write(f"API Used: {result['api_used']}\n")
                    
                    if result['status'] == 'rate_limited':
                        # Handle rate limiting with countdown
                        wait_time = result.get('wait_time', 60)
                        countdown_start = time.time()
                        remaining_time = wait_time
                        
                        with open(log_file_path, 'a', encoding='utf-8') as log_file:
                            log_file.write(f"Rate limit reached - Total wait time: {wait_time:.1f} seconds\n")
                        
                        # Update display with countdown
                        while remaining_time > 0:
                            queue_info = f"\n📋 **Operations in queue:** {self.login_handler.get_queue_info()['queue_size']}" if self.login_handler.get_queue_info()['queue_size'] > 0 else ""
                            embed.description = f"⚠️ Rate limit reached. Waiting {remaining_time:.0f} seconds...{queue_info}"
                            embed.color = discord.Color.orange()
                            await message.edit(embed=embed)
                            
                            # Wait for up to 5 seconds before updating
                            await asyncio.sleep(min(5, remaining_time))
                            elapsed = time.time() - countdown_start
                            remaining_time = max(0, wait_time - elapsed)
                        
                        embed.color = discord.Color.blue()
                        continue  # Retry this request
                    
                    if result['status'] == 'success':
                        data = result['data']
                        with open(log_file_path, 'a', encoding='utf-8') as log_file:
                            log_file.write(f"API Response Data: {str(data)}\n")
                        
                        nickname = data.get('nickname')
                        furnace_lv = data.get('stove_lv', 0)
                        stove_lv_content = data.get('stove_lv_content', None)
                        kid = data.get('kid', None)

                        if nickname:
                            try: # Since we pre-filtered, this FID should not exist in database
                                self.c_users.execute("""
                                    INSERT INTO users (fid, nickname, furnace_lv, kid, stove_lv_content, alliance)
                                    VALUES (?, ?, ?, ?, ?, ?)
                                """, (fid, nickname, furnace_lv, kid, stove_lv_content, alliance_id))
                                self.conn_users.commit()
                                
                                # MongoDB Insert
                                if mongo_enabled() and AllianceMembersAdapter:
                                    try:
                                        member_doc = {
                                            'fid': str(fid),
                                            'nickname': nickname,
                                            'furnace_lv': furnace_lv,
                                            'kid': kid,
                                            'stove_lv_content': stove_lv_content,
                                            'alliance': int(alliance_id),
                                            'last_updated': datetime.utcnow()
                                        }
                                        AllianceMembersAdapter.upsert_member(str(fid), member_doc)
                                    except Exception as e:
                                        self.log_message(f"MongoDB insert error for {fid}: {e}")
                                
                                with open(self.log_file, 'a', encoding='utf-8') as f:
                                    f.write(f"[{timestamp}] Successfully added member - FID: {fid}, Nickname: {nickname}, Level: {furnace_lv}\n")
                                
                                # Console logging - success
                                self.log_message(f"[Member Add] ✅ Successfully added {nickname} (FC {furnace_lv}) - FID {fid}")
                                
                                added_count += 1
                                added_users.append((fid, nickname))
                                
                                embed.set_field_at(
                                    0,
                                    name=f"✅ Successfully Added ({added_count}/{total_users})",
                                    value="User list cannot be displayed due to exceeding 70 users" if len(added_users) > 70 
                                    else ", ".join([n for _, n in added_users]) or "-",
                                    inline=False
                                )
                                await message.edit(embed=embed)
                                
                            except sqlite3.IntegrityError as e:
                                # This shouldn't happen since we pre-filtered, but handle it just in case
                                with open(log_file_path, 'a', encoding='utf-8') as log_file:
                                    log_file.write(f"ERROR: Member already exists (race condition?) - FID {fid}: {str(e)}\n")
                                
                                # Console logging - already exists
                                self.log_message(f"[Member Add] ⚠️ Already exists: FID {fid}")
                                
                                already_exists_count += 1
                                already_exists_users.append((fid, nickname))
                                
                                embed.set_field_at(
                                    2,
                                    name=f"⚠️ Already Exists ({already_exists_count}/{total_users})",
                                    value="Existing user list cannot be displayed due to exceeding 70 users" if len(already_exists_users) > 70 
                                    else ", ".join([n for _, n in already_exists_users]) or "-",
                                    inline=False
                                )
                                await message.edit(embed=embed)
                                
                            except Exception as e:
                                with open(log_file_path, 'a', encoding='utf-8') as log_file:
                                    log_file.write(f"ERROR: Database error for FID {fid}: {str(e)}\n")
                                
                                # Console logging - database error
                                self.log_message(f"[Member Add] ❌ Database error for FID {fid}: {str(e)}")
                                
                                error_count += 1
                                error_users.append(fid)
                                
                                embed.set_field_at(
                                    1,
                                    name=f"❌ Failed ({error_count}/{total_users})",
                                    value="Error list cannot be displayed due to exceeding 70 users" if len(error_users) > 70 
                                    else ", ".join(error_users) or "-",
                                    inline=False
                                )
                                await message.edit(embed=embed)
                        else:
                            # No nickname in API response
                            self.log_message(f"[Member Add] ❌ No nickname in API response for FID {fid}")
                            error_count += 1
                            error_users.append(fid)
                    else:
                            # Handle other error statuses
                            error_msg = result.get('error_message', 'Unknown error')
                            with open(log_file_path, 'a', encoding='utf-8') as log_file:
                                log_file.write(f"ERROR: {error_msg} for FID {fid}\n")
                            
                            # Console logging - API error
                            self.log_message(f"[Member Add] ❌ API error for FID {fid}: {error_msg}")
                            
                            error_count += 1
                            if fid not in error_users:
                                error_users.append(fid)
                            embed.set_field_at(
                                1,
                                name=f"❌ Failed ({error_count}/{total_users})",
                                value="Error list cannot be displayed due to exceeding 70 users" if len(error_users) > 70 
                                else ", ".join(error_users) or "-",
                                inline=False
                            )
                            await message.edit(embed=embed)
                    
                    index += 1

                except Exception as e:
                    with open(log_file_path, 'a', encoding='utf-8') as log_file:
                        log_file.write(f"ERROR: Request failed for FID {fid}: {str(e)}\n")
                    
                    # Console logging - exception
                    self.log_message(f"[Member Add] ❌ Exception for FID {fid}: {str(e)}")
                    
                    error_count += 1
                    error_users.append(fid)
                    await message.edit(embed=embed)
                    index += 1

            embed.set_field_at(0, name=f"✅ Successfully Added ({added_count}/{total_users})",
                value="User list cannot be displayed due to exceeding 70 users" if len(added_users) > 70 
                else ", ".join([nickname for _, nickname in added_users]) or "-",
                inline=False
            )
            
            embed.set_field_at(1, name=f"❌ Failed ({error_count}/{total_users})",
                value="Error list cannot be displayed due to exceeding 70 users" if len(error_users) > 70 
                else ", ".join(error_users) or "-",
                inline=False
            )
            
            embed.set_field_at(2, name=f"⚠️ Already Exists ({already_exists_count}/{total_users})",
                value="Existing user list cannot be displayed due to exceeding 70 users" if len(already_exists_users) > 70 
                else ", ".join([nickname for _, nickname in already_exists_users]) or "-",
                inline=False
            )

            await message.edit(embed=embed)

            try:
                with sqlite3.connect('db/settings.sqlite') as settings_db:
                    cursor = settings_db.cursor()
                    cursor.execute("""
                        SELECT channel_id 
                        FROM alliance_logs 
                        WHERE alliance_id = ?
                    """, (alliance_id,))
                    alliance_log_result = cursor.fetchone()
                    
                    if alliance_log_result and alliance_log_result[0]:
                        # Import pagination helper
                        from .pagination_helper import create_alliance_log_embeds, ResultsPaginationView
                        
                        # Create paginated embeds
                        log_embeds = create_alliance_log_embeds(
                            alliance_name=alliance_name,
                            admin_name=interaction.user.name,
                            admin_id=interaction.user.id,
                            added_count=added_count,
                            error_count=error_count,
                            already_exists_count=already_exists_count,
                            ids_list=ids_list,
                            items_per_page=20
                        )

                        try:
                            alliance_channel_id = int(alliance_log_result[0])
                            alliance_log_channel = self.bot.get_channel(alliance_channel_id)
                            if alliance_log_channel:
                                # Send with pagination if multiple pages
                                if len(log_embeds) > 1:
                                    view = ResultsPaginationView(log_embeds, author_id=None)
                                    message = await alliance_log_channel.send(embed=log_embeds[0], view=view)
                                    view.message = message
                                else:
                                    await alliance_log_channel.send(embed=log_embeds[0])
                        except Exception as e:
                            with open(log_file_path, 'a', encoding='utf-8') as log_file:
                                log_file.write(f"ERROR: Alliance Log Sending Error: {str(e)}\n")

            except Exception as e:
                with open(log_file_path, 'a', encoding='utf-8') as log_file:
                    log_file.write(f"ERROR: Log record error: {str(e)}\n")

            # Console logging - final summary
            self.log_message(f"[Member Add] ━━━━━━━━━━━━━━━━━━━━━━")
            self.log_message(f"[Member Add] 📊 SUMMARY: {added_count} added, {error_count} failed, {already_exists_count} already existed out of {total_users} total")
            self.log_message(f"[Member Add] ━━━━━━━━━━━━━━━━━━━━━━")

            with open(log_file_path, 'a', encoding='utf-8') as log_file:
                log_file.write(f"\nFinal Results:\n")
                log_file.write(f"Successfully Added: {added_count}\n")
                log_file.write(f"Failed: {error_count}\n")
                log_file.write(f"Already Exists: {already_exists_count}\n")
                log_file.write(f"API Mode: {self.login_handler.get_mode_text()}\n")
                log_file.write(f"API1 Requests: {len(self.login_handler.api1_requests)}\n")
                log_file.write(f"API2 Requests: {len(self.login_handler.api2_requests)}\n")
                log_file.write(f"{'='*50}\n")

        except Exception as e:
            with open(log_file_path, 'a', encoding='utf-8') as log_file:
                log_file.write(f"CRITICAL ERROR: {str(e)}\n")
                log_file.write(f"{'='*50}\n")

        # Calculate total processing time
        end_time = datetime.now()
        start_time = datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S')
        processing_time = (end_time - start_time).total_seconds()
        
        queue_info = f"📋 **Operations still in queue:** {self.login_handler.get_queue_info()['queue_size']}" if self.login_handler.get_queue_info()['queue_size'] > 0 else ""
        
        embed.title = "✅ User Addition Completed"
        embed.description = (
            f"Process completed for {total_users} members.\n"
            f"**Processing Time:** {processing_time:.1f} seconds{queue_info}\n\n"
        )
        embed.color = discord.Color.green()
        await message.edit(embed=embed)

    async def is_admin(self, user_id):
        return get_admin(user_id) is not None

    def cog_unload(self):
        self.conn_users.close()
        self.conn_alliance.close()

    async def get_admin_alliances(self, user_id: int, guild_id: int):
        try:
            admin_info = get_admin(user_id)
            
            if not admin_info:
                self.log_message(f"User {user_id} is not an admin")
                return [], [], False
                
            is_initial = 0
            if isinstance(admin_info, tuple):
                is_initial = int(admin_info[1])
            elif isinstance(admin_info, dict):
                is_initial = int(admin_info.get('is_initial', 0))
            
            if is_initial == 1:
                
                with get_db_connection('alliance.sqlite') as alliance_db:
                    cursor = alliance_db.cursor()
                    cursor.execute("SELECT alliance_id, name FROM alliance_list ORDER BY name")
                    alliances = cursor.fetchall()
                    return alliances, [], True
            
            server_alliances = []
            special_alliances = []
            
            with get_db_connection('alliance.sqlite') as alliance_db:
                cursor = alliance_db.cursor()
                cursor.execute("""
                    SELECT DISTINCT alliance_id, name 
                    FROM alliance_list 
                    WHERE discord_server_id = ?
                    ORDER BY name
                """, (guild_id,))
                server_alliances = cursor.fetchall()
            
            with get_db_connection('settings.sqlite') as settings_db:
                cursor = settings_db.cursor()
                cursor.execute("""
                    SELECT alliances_id 
                    FROM adminserver 
                    WHERE admin = ?
                """, (user_id,))
                special_alliance_ids = cursor.fetchall()
                
            if special_alliance_ids:
                with get_db_connection('alliance.sqlite') as alliance_db:
                    cursor = alliance_db.cursor()
                    placeholders = ','.join('?' * len(special_alliance_ids))
                    cursor.execute(f"""
                        SELECT DISTINCT alliance_id, name
                        FROM alliance_list
                        WHERE alliance_id IN ({placeholders})
                        ORDER BY name
                    """, [aid[0] for aid in special_alliance_ids])
                    special_alliances = cursor.fetchall()
            
            all_alliances = list({(aid, name) for aid, name in (server_alliances + special_alliances)})
            
            if not all_alliances and not special_alliances:
                return [], [], False
            
            return all_alliances, special_alliances, False
                
        except Exception as e:
            self.log_message(f"Error in get_admin_alliances: {e}")
            return [], [], False

    async def handle_button_interaction(self, interaction: discord.Interaction):
        custom_id = interaction.data["custom_id"]
        
        if custom_id == "main_menu":
            await self.show_main_menu(interaction)
    
    async def show_main_menu(self, interaction: discord.Interaction):
        try:
            alliance_cog = self.bot.get_cog("Alliance")
            if alliance_cog:
                await alliance_cog.show_main_menu(interaction)
            else:
                await interaction.response.send_message(
                    "❌ An error occurred while returning to main menu.",
                    ephemeral=True
                )
        except Exception as e:
            self.log_message(f"[ERROR] Main Menu error in member operations: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ An error occurred while returning to main menu.", 
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    "❌ An error occurred while returning to main menu.",
                    ephemeral=True
                )

class AddMemberModal(discord.ui.Modal):
    def __init__(self, alliance_id):
        super().__init__(title="Add Member")
        self.alliance_id = alliance_id
        self.add_item(discord.ui.TextInput(
            label="Enter FIDs (comma or newline separated)", 
            placeholder="Comma: 12345,67890,54321\nNewline:\n12345\n67890\n54321",
            style=discord.TextStyle.paragraph
        ))

    async def on_submit(self, interaction: discord.Interaction):
        try:
            ids = self.children[0].value
            await interaction.client.get_cog("AllianceMemberOperations").add_user(
                interaction, 
                self.alliance_id, 
                ids
            )
        except Exception as e:
            print(f"ERROR: Modal submit error - {str(e)}")
            await interaction.response.send_message(
                "An error occurred. Please try again.", 
                ephemeral=True
            )

class AllianceSelectView(discord.ui.View):
    def __init__(self, alliances_with_counts, cog=None, page=0, context="transfer", user_id=None):
        super().__init__(timeout=7200)
        self.alliances = alliances_with_counts
        self.user_id = user_id
        self.cog = cog
        self.page = page
        self.max_page = (len(alliances_with_counts) - 1) // 25 if alliances_with_counts else 0
        self.current_select = None
        self.callback = None
        self.member_dict = {}
        self.selected_alliance_id = None
        self.context = context  # "transfer", "furnace_history", or "nickname_history"
        self.update_select_menu()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.user_id and interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ This menu is not for you.", ephemeral=True)
            return False
        return True

    def update_select_menu(self):
        for item in self.children[:]:
            if isinstance(item, discord.ui.Select):
                self.remove_item(item)

        start_idx = self.page * 25
        end_idx = min(start_idx + 25, len(self.alliances))
        current_alliances = self.alliances[start_idx:end_idx]

        select = discord.ui.Select(
            placeholder=f"🏰 Select an alliance... (Page {self.page + 1}/{self.max_page + 1})",
            options=[
                discord.SelectOption(
                    label=f"{name[:50]}",
                    value=str(alliance_id),
                    description=f"ID: {alliance_id} | Members: {count}",
                    emoji="🏰"
                ) for alliance_id, name, count in current_alliances
            ]
        )
        
        async def select_callback(interaction: discord.Interaction):
            self.current_select = select
            if self.callback:
                await self.callback(interaction)
        
        select.callback = select_callback
        self.add_item(select)
        self.current_select = select

        if hasattr(self, 'prev_button'):
            self.prev_button.disabled = self.page == 0
        if hasattr(self, 'next_button'):
            self.next_button.disabled = self.page == self.max_page

    @discord.ui.button(label="◀️", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self.update_select_menu()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="▶️", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(self.max_page, self.page + 1)
        self.update_select_menu()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Select by FID", emoji="🔍", style=discord.ButtonStyle.secondary)
    async def fid_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            if self.current_select and self.current_select.values:
                self.selected_alliance_id = self.current_select.values[0]
            
            modal = FIDSearchModal(
                selected_alliance_id=self.selected_alliance_id,
                alliances=self.alliances,
                callback=self.callback,
                context=self.context,
                cog=self.cog
            )
            await interaction.response.send_modal(modal)
        except Exception as e:
            print(f"FID button error: {e}")
            await interaction.response.send_message(
                "❌ An error has occurred. Please try again.",
                ephemeral=True
            )

class FIDSearchModal(discord.ui.Modal):
    def __init__(self, selected_alliance_id=None, alliances=None, callback=None, context="transfer", cog=None):
        super().__init__(title="Search Members with FID")
        self.selected_alliance_id = selected_alliance_id
        self.alliances = alliances
        self.callback = callback
        self.context = context
        self.cog = cog
        
        self.add_item(discord.ui.TextInput(
            label="Member ID",
            placeholder="Example: 12345",
            min_length=1,
            max_length=20,
            required=True
        ))

    async def on_submit(self, interaction: discord.Interaction):
        try:
            fid = self.children[0].value.strip()
            
            # Check if we're in a history context
            if self.context in ["furnace_history", "nickname_history"]:
                # Get the Changes cog
                changes_cog = self.cog.bot.get_cog("Changes") if self.cog else interaction.client.get_cog("Changes")
                if changes_cog:
                    await interaction.response.defer()
                    if self.context == "furnace_history":
                        await changes_cog.show_furnace_history(interaction, int(fid))
                    else:
                        await changes_cog.show_nickname_history(interaction, int(fid))
                else:
                    await interaction.followup.send(
                        "❌ History feature is not available.",
                        ephemeral=True
                    )
                return

            if self.context == "remove":
                user_result = self.cog._get_member_by_fid(fid)
                
                if not user_result:
                    await interaction.response.send_message("❌ No member with this FID was found.", ephemeral=True)
                    return

                fid, nickname, furnace_lv, current_alliance_id = user_result
                
                confirm_embed = discord.Embed(
                    title="⚠️ Confirm Removal",
                    description=f"Are you sure you want to remove **{nickname}** (FID: {fid})?",
                    color=discord.Color.red()
                )
                
                confirm_view = discord.ui.View()
                confirm_button = discord.ui.Button(label="✅ Confirm", style=discord.ButtonStyle.danger, custom_id="confirm_remove")
                cancel_button = discord.ui.Button(label="❌ Cancel", style=discord.ButtonStyle.secondary, custom_id="cancel_remove")
                
                confirm_view.add_item(confirm_button)
                confirm_view.add_item(cancel_button)
                
                async def confirm_callback(confirm_interaction: discord.Interaction):
                    if confirm_interaction.data["custom_id"] == "confirm_remove":
                        await confirm_interaction.response.defer()
                        success = self.cog._delete_member_by_fid(fid)
                        if success:
                            await confirm_interaction.edit_original_response(embed=discord.Embed(title="✅ Member Removed", description=f"**{nickname}** has been removed.", color=discord.Color.green()), view=None)
                        else:
                            await confirm_interaction.edit_original_response(embed=discord.Embed(title="❌ Error", description="Failed to remove member.", color=discord.Color.red()), view=None)
                    else:
                        await confirm_interaction.response.edit_message(embed=discord.Embed(title="❌ Cancelled", description="Operation cancelled.", color=discord.Color.orange()), view=None)

                confirm_button.callback = confirm_callback
                cancel_button.callback = confirm_callback
                
                await interaction.response.send_message(embed=confirm_embed, view=confirm_view, ephemeral=True)
                return
            
            # Original transfer logic
            with get_db_connection('users.sqlite') as users_db:
                cursor = users_db.cursor()
                cursor.execute("""
                    SELECT fid, nickname, furnace_lv, alliance
                    FROM users 
                    WHERE fid = ?
                """, (fid,))
                user_result = cursor.fetchone()
                
                if not user_result:
                    await interaction.response.send_message(
                        "❌ No member with this FID was found.",
                        ephemeral=True
                    )
                    return

                fid, nickname, furnace_lv, current_alliance_id = user_result
 
                with get_db_connection('alliance.sqlite') as alliance_db:
                    cursor = alliance_db.cursor()
                    cursor.execute("SELECT name FROM alliance_list WHERE alliance_id = ?", (current_alliance_id,))
                    current_alliance_name = cursor.fetchone()[0]

                embed = discord.Embed(
                    title="✅ Member Found - Transfer Process",
                    description=(
                        f"**Member Information:**\n"
                        f"👤 **Name:** {nickname}\n"
                        f"🆔 **FID:** {fid}\n"
                        f"⚔️ **Level:** {furnace_lv}\n"
                        f"🏰 **Current Alliance:** {current_alliance_name}\n\n"
                        "**Transfer Process**\n"
                        "Please select the alliance you want to transfer the member to:"
                    ),
                    color=discord.Color.blue()
                )

                select = discord.ui.Select(
                    placeholder="🎯 Choose the target alliance...",
                    options=[
                        discord.SelectOption(
                            label=f"{name[:50]}",
                            value=str(alliance_id),
                            description=f"ID: {alliance_id}",
                            emoji="🏰"
                        ) for alliance_id, name, _ in self.alliances
                        if alliance_id != current_alliance_id  
                    ]
                )
                
                view = discord.ui.View()
                view.add_item(select)

                async def select_callback(select_interaction: discord.Interaction):
                    target_alliance_id = int(select.values[0])
                    
                    try:
                        with get_db_connection('alliance.sqlite') as alliance_db:
                            cursor = alliance_db.cursor()
                            cursor.execute("SELECT name FROM alliance_list WHERE alliance_id = ?", (target_alliance_id,))
                            target_alliance_name = cursor.fetchone()[0]

                        transferred = self._update_member_alliance(str(fid), target_alliance_id)

                        
                        success_embed = discord.Embed(
                            title="✅ Transfer Successful",
                            description=(
                                f"👤 **Member:** {nickname}\n"
                                f"🆔 **FID:** {fid}\n"
                                f"📤 **Source:** {current_alliance_name}\n"
                                f"📥 **Target:** {target_alliance_name}"
                            ),
                            color=discord.Color.green()
                        )
                        
                        await select_interaction.response.edit_message(
                            embed=success_embed,
                            view=None
                        )
                        
                    except Exception as e:
                        print(f"Transfer error: {e}")
                        error_embed = discord.Embed(
                            title="❌ Error",
                            description="An error occurred during the transfer operation.",
                            color=discord.Color.red()
                        )
                        await select_interaction.response.edit_message(
                            embed=error_embed,
                            view=None
                        )

                select.callback = select_callback
                await interaction.response.send_message(
                    embed=embed,
                    view=view,
                    ephemeral=True
                )

        except Exception as e:
            print(f"Error details: {str(e.__class__.__name__)}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ An error has occurred. Please try again.",
                    ephemeral=True
                )

class MemberSelectView(discord.ui.View):
    def __init__(self, members, source_alliance_name, cog, page=0, is_remove_operation=False, user_id=None):
        super().__init__(timeout=7200)
        self.members = members
        self.source_alliance_name = source_alliance_name
        self.cog = cog
        self.user_id = user_id
        self.page = page
        self.max_page = (len(members) - 1) // 25
        self.current_select = None
        self.callback = None
        self.member_dict = {str(fid): nickname for fid, nickname, _ in members}
        self.selected_alliance_id = None
        self.alliances = None
        self.is_remove_operation = is_remove_operation
        self.context = "remove" if is_remove_operation else "transfer"
        self.update_select_menu()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.user_id and interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ This menu is not for you.", ephemeral=True)
            return False
        return True

    def update_select_menu(self):
        for item in self.children[:]:
            if isinstance(item, discord.ui.Select):
                self.remove_item(item)

        start_idx = self.page * 25
        end_idx = min(start_idx + 25, len(self.members))
        current_members = self.members[start_idx:end_idx]

        options = []
        
        if self.page == 0:
            options.append(discord.SelectOption(
                label="ALL MEMBERS",
                value="all",
                description=f"⚠️ Delete all {len(self.members)} members!",
                emoji="⚠️"
            ))

        remaining_slots = 25 - len(options)
        member_options = [
            discord.SelectOption(
                label=f"{nickname[:50]}",
                value=str(fid),
                description=f"FID: {fid} | FC: {self.cog.level_mapping.get(furnace_lv, str(furnace_lv))}",
                emoji="👤"
            ) for fid, nickname, furnace_lv in current_members[:remaining_slots]
        ]
        options.extend(member_options)

        # Determine placeholder based on context (remove vs transfer)
        placeholder_text = "👤 Select member to remove..." if hasattr(self, 'is_remove_operation') and self.is_remove_operation else "👤 Select member to transfer..."

        select = discord.ui.Select(
            placeholder=f"{placeholder_text} (Page {self.page + 1}/{self.max_page + 1})",
            options=options
        )
        
        async def select_callback(interaction: discord.Interaction):
            self.current_select = select
            if self.callback:
                await self.callback(interaction)
        
        select.callback = select_callback
        self.add_item(select)
        self.current_select = select

        if hasattr(self, 'prev_button'):
            self.prev_button.disabled = self.page == 0
        if hasattr(self, 'next_button'):
            self.next_button.disabled = self.page == self.max_page

    @discord.ui.button(label="◀️", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self.update_select_menu()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="▶️", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(self.max_page, self.page + 1)
        self.update_select_menu()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Select by FID", emoji="🔍", style=discord.ButtonStyle.secondary)
    async def fid_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            
            if self.current_select and self.current_select.values:
                self.selected_alliance_id = self.current_select.values[0]
            
            modal = FIDSearchModal(
                selected_alliance_id=self.selected_alliance_id,
                alliances=self.alliances,
                callback=self.callback,
                context=self.context,
                cog=self.cog
            )
            await interaction.response.send_modal(modal)
        except Exception as e:
            print(f"FID button error: {e}")
            await interaction.response.send_message(
                "❌ An error has occurred. Please try again.",
                ephemeral=True
            )

async def setup(bot):
    await bot.add_cog(AllianceMemberOperations(bot))