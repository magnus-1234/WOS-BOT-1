import discord
from discord.ext import commands
import re
from typing import List, Tuple
from db.mongo_adapters import mongo_enabled, ServerAllianceAdapter, AllianceMembersAdapter
from .login_handler import LoginHandler

class FIDCommands(commands.Cog):
    """Cog to handle !Add and !Remove commands for managing alliance members by FID"""
    
    def __init__(self, bot):
        self.bot = bot
        self.login_handler = LoginHandler()
    
    def _parse_fids(self, message_content: str) -> Tuple[str, List[str]]:
        """
        Parse FIDs from message content.
        Returns: (command, list of FIDs)
        """
        # Extract command (!Add or !Remove)
        parts = message_content.strip().split(maxsplit=1)
        if len(parts) < 2:
            return parts[0], []
        
        command = parts[0]
        fids_str = parts[1]
        
        # Split by comma and clean up
        fid_list = [fid.strip() for fid in fids_str.split(',')]
        
        # Validate FIDs (must be 8 or 9 digits)
        valid_fids = []
        for fid in fid_list:
            if re.match(r'^\d{8,9}$', fid):
                valid_fids.append(fid)
        
        return command, valid_fids
    
    async def _fetch_player_data(self, fid: str) -> dict:
        """Fetch player data from API using FID"""
        try:
            # Use the login handler to make API request
            result = await self.login_handler.fetch_player_data(fid)
            
            # Check if successful
            if result['status'] == 'success' and result['data']:
                return result['data']
            else:
                return None
        except Exception as e:
            print(f"Error fetching player data for FID {fid}: {e}")
            return None
    
    async def _add_member_to_alliance(self, fid: str, alliance_id: int) -> Tuple[bool, str]:
        """
        Add a member to an alliance by FID.
        Returns: (success, message)
        """
        try:
            # Fetch player data from API
            player_data = await self._fetch_player_data(fid)
            
            if not player_data:
                return False, f"❌ Could not fetch data for FID {fid}"
            
            # Prepare member data for MongoDB
            member_data = {
                'fid': str(fid),
                'nickname': player_data.get('nickname', 'Unknown'),
                'furnace_lv': int(player_data.get('stove_lv', 0)),
                'alliance': int(alliance_id),
                'alliance_id': int(alliance_id),
                'kid': player_data.get('kid', 0),
                'stove_lv_content': player_data.get('stove_lv_content', ''),
                'avatar_image': player_data.get('avatar_image', '')  # Add avatar
            }
            
            # Store in MongoDB
            if mongo_enabled() and AllianceMembersAdapter:
                success = AllianceMembersAdapter.upsert_member(str(fid), member_data)
                if success:
                    return True, f"✅ Added **{member_data['nickname']}** (FID: {fid})"
                else:
                    return False, f"❌ Failed to add FID {fid} to database"
            else:
                return False, "❌ MongoDB not enabled"
                
        except Exception as e:
            print(f"Error adding member {fid}: {e}")
            return False, f"❌ Error adding FID {fid}: {str(e)}"
    
    async def _remove_member_from_alliance(self, fid: str, alliance_id: int) -> Tuple[bool, str]:
        """
        Remove a member from an alliance by FID.
        Returns: (success, message)
        """
        try:
            # Check if member exists
            if mongo_enabled() and AllianceMembersAdapter:
                member = AllianceMembersAdapter.get_member(str(fid))
                
                if not member:
                    return False, f"❌ FID {fid} not found in alliance"
                
                # Verify member belongs to this alliance
                member_alliance = int(member.get('alliance', 0) or member.get('alliance_id', 0))
                if member_alliance != alliance_id:
                    return False, f"❌ FID {fid} belongs to a different alliance"
                
                nickname = member.get('nickname', 'Unknown')
                
                # Remove from MongoDB
                success = AllianceMembersAdapter.delete_member(str(fid))
                if success:
                    return True, f"✅ Removed **{nickname}** (FID: {fid})"
                else:
                    return False, f"❌ Failed to remove FID {fid} from database"
            else:
                return False, "❌ MongoDB not enabled"
                
        except Exception as e:
            print(f"Error removing member {fid}: {e}")
            return False, f"❌ Error removing FID {fid}: {str(e)}"
    
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listen for !Add and !Remove commands"""
        # Ignore bot messages
        if message.author.bot:
            return
        
        # Check if message starts with !Add or !Remove
        content = message.content.strip()
        if not (content.startswith('!Add') or content.startswith('!Remove') or content.startswith('!showlist')):
            return
        
        # Only work in guild channels
        if not message.guild:
            await message.channel.send("❌ This command can only be used in a server.")
            return
        
        # Check if server has an assigned alliance
        if not mongo_enabled() or not ServerAllianceAdapter:
            await message.channel.send("❌ MongoDB not enabled. Cannot process command.")
            return
        
        alliance_id = ServerAllianceAdapter.get_alliance(message.guild.id)
        if not alliance_id:
            await message.channel.send(
                "❌ No alliance assigned to this server.\n"
                "Please use `/settings` → Bot Operations → Assign Server Alliance to assign one."
            )
            return
        
        # Handle !showlist command
        if content.startswith('!showlist'):
            try:
                # Check if password is set
                stored_password = ServerAllianceAdapter.get_password(message.guild.id)
                if not stored_password:
                    error_embed = discord.Embed(
                        title="🔒 Access Denied",
                        description="No password configured for member list access.",
                        color=0x2B2D31
                    )
                    error_embed.add_field(
                        name="⚙️ Administrator Action Required",
                        value="Contact a server administrator to set up member list password via:\n`/settings` → **Bot Operations** → **Set Member List Password**",
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
                    await message.channel.send(embed=error_embed, view=view)
                    return

                # Create password modal
                class MagShieldModal(discord.ui.Modal, title="🛡️ MAG SHIELD • Security Verification"):
                    password_input = discord.ui.TextInput(
                        label="Enter Access Code",
                        placeholder="••••••••••••",
                        style=discord.TextStyle.short,
                        required=True,
                        max_length=50
                    )

                    def __init__(self, alliance_id: int, guild_id: int, channel):
                        super().__init__()
                        self.alliance_id = alliance_id
                        self.guild_id = guild_id
                        self.channel = channel

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
                                    name="🔐 Security Notice",
                                    value="Please verify your credentials and try again.",
                                    inline=False
                                )
                                await modal_interaction.response.send_message(
                                    embed=error_embed,
                                    ephemeral=True
                                )
                                return

                            # Password correct - show member list
                            await modal_interaction.response.defer(ephemeral=True)
                            
                            # Get members from MongoDB
                            if AllianceMembersAdapter:
                                all_members = AllianceMembersAdapter.get_all_members()
                                # Filter by alliance
                                members = [m for m in all_members if str(m.get('alliance') or m.get('alliance_id') or 0) == str(self.alliance_id)]
                            else:
                                members = []

                            if not members:
                                no_members_embed = discord.Embed(
                                    title="📋 Member List",
                                    description="No members found in the assigned alliance.",
                                    color=0x2B2D31
                                )
                                await modal_interaction.followup.send(
                                    embed=no_members_embed,
                                    ephemeral=True
                                )
                                return

                            # Get alliance name
                            try:
                                from db_utils import get_db_connection
                                with get_db_connection('alliance.sqlite') as conn:
                                    cursor = conn.cursor()
                                    cursor.execute("SELECT name FROM alliance_list WHERE alliance_id = ?", (self.alliance_id,))
                                    result = cursor.fetchone()
                                    alliance_name = result[0] if result else f"Alliance {self.alliance_id}"
                            except:
                                alliance_name = f"Alliance {self.alliance_id}"

                            # Sort members by furnace level (descending)
                            members.sort(key=lambda x: int(x.get('furnace_lv', 0) or 0), reverse=True)

                            # Create embed with technical styling
                            embed = discord.Embed(
                                title=f"👥 {alliance_name}",
                                description=f"```ansi\n\u001b[2;36m▸ Total Members: {len(members)}\n\u001b[2;37m▸ Sorted by: Furnace Level (Descending)\u001b[0m\n```",
                                color=0x5865F2
                            )
                            
                            embed.set_author(
                                name="MEMBER DATABASE • ACCESS GRANTED",
                                icon_url="https://cdn.discordapp.com/emojis/1234567890.png"  # Optional: Add shield icon
                            )


                            # Add members to embed (max 25 fields)
                            # Furnace level mapping
                            level_mapping = {
                                31: "30-1", 32: "30-2", 33: "30-3", 34: "30-4",
                                35: "FC 1", 36: "FC 1-1", 37: "FC 1-2", 38: "FC 1-3", 39: "FC 1-4",
                                40: "FC 2", 41: "FC 2-1", 42: "FC 2-2", 43: "FC 2-3", 44: "FC 2-4",
                                45: "FC 3", 46: "FC 3-1", 47: "FC 3-2", 48: "FC 3-3", 49: "FC 3-4",
                                50: "FC 4", 51: "FC 4-1", 52: "FC 4-2", 53: "FC 4-3", 54: "FC 4-4",
                                55: "FC 5", 56: "FC 5-1", 57: "FC 5-2", 58: "FC 5-3", 59: "FC 5-4",
                                60: "FC 6", 61: "FC 6-1", 62: "FC 6-2", 63: "FC 6-3", 64: "FC 6-4",
                                65: "FC 7", 66: "FC 7-1", 67: "FC 7-2", 68: "FC 7-3", 69: "FC 7-4",
                                70: "FC 8", 71: "FC 8-1", 72: "FC 8-2", 73: "FC 8-3", 74: "FC 8-4",
                                75: "FC 9", 76: "FC 9-1", 77: "FC 9-2", 78: "FC 9-3", 79: "FC 9-4",
                                80: "FC 10", 81: "FC 10-1", 82: "FC 10-2", 83: "FC 10-3", 84: "FC 10-4"
                            }
                            
                            for i, member in enumerate(members[:25], 1):
                                nickname = member.get('nickname', 'Unknown')
                                fid = member.get('fid', 'N/A')
                                furnace_lv = int(member.get('furnace_lv', 0) or 0)
                                level = level_mapping.get(furnace_lv, str(furnace_lv))
                                
                                # Create rank badge
                                if i == 1:
                                    rank_badge = "🥇"
                                elif i == 2:
                                    rank_badge = "🥈"
                                elif i == 3:
                                    rank_badge = "🥉"
                                else:
                                    rank_badge = f"#{i}"
                                
                                embed.add_field(
                                    name=f"{rank_badge} {nickname}",
                                    value=f"```yaml\nFID: {fid}\nFurnace: {level}\n```",
                                    inline=True
                                )

                            if len(members) > 25:
                                embed.set_footer(
                                    text=f"Displaying 25 of {len(members)} members ΓÇó Stored in MongoDB",
                                    icon_url="https://cdn.discordapp.com/attachments/1435569370389807144/1445459239131680859/images_7_1.png"
                                )
                            else:
                                embed.set_footer(
                                    text=f"Displaying 25 of {len(members)} members ΓÇó Stored in MongoDB",
                                    icon_url="https://cdn.discordapp.com/attachments/1435569370389807144/1445459239131680859/images_7_1.png"
                                )


                            # Send to channel (not ephemeral)
                            # Create view with pagination and filter buttons
                            class MemberListView(discord.ui.View):
                                def __init__(self, members_data, alliance_name, level_map):
                                    super().__init__(timeout=300)
                                    self.members = members_data
                                    self.alliance_name = alliance_name
                                    self.level_mapping = level_map
                                    self.current_page = 0
                                    self.members_per_page = 15
                                    self.sort_order = "desc"  # desc or asc
                                    self.message = None

                                def get_sorted_members(self):
                                    return sorted(
                                        self.members,
                                        key=lambda x: int(x.get('furnace_lv', 0) or 0),
                                        reverse=(self.sort_order == "desc")
                                    )

                                def get_total_pages(self):
                                    return (len(self.members) - 1) // self.members_per_page + 1

                                def create_embed(self):
                                    sorted_members = self.get_sorted_members()
                                    total_pages = self.get_total_pages()
                                    
                                    # Calculate statistics
                                    furnace_levels = [int(m.get('furnace_lv', 0) or 0) for m in self.members]
                                    max_fl = max(furnace_levels) if furnace_levels else 0
                                    avg_fl = sum(furnace_levels) / len(furnace_levels) if furnace_levels else 0

                                    # Create embed with alliance statistics
                                    embed = discord.Embed(
                                        title=f"👥 {self.alliance_name} - Member List",
                                        description=(
                                            "```ml\n"
                                            "Alliance Statistics\n"
                                            "══════════════════════════\n"
                                            f"📊 Total Members    : {len(self.members)}\n"
                                            f"⚔️ Highest Level    : {self.level_mapping.get(max_fl, str(max_fl))}\n"
                                            f"📈 Average Level    : {self.level_mapping.get(int(avg_fl), str(int(avg_fl)))}\n"
                                            "══════════════════════════\n"
                                            "```\n"
                                            "**Member List**\n"
                                            "━━━━━━━━━━━━━━━━━━━━━━\n"
                                        ),
                                        color=0x5865F2
                                    )

                                    # Get members for current page
                                    start_idx = self.current_page * self.members_per_page
                                    end_idx = start_idx + self.members_per_page
                                    page_members = sorted_members[start_idx:end_idx]

                                    # Add members to embed
                                    member_list = ""
                                    for idx, member in enumerate(page_members, start=start_idx + 1):
                                        nickname = member.get('nickname', 'Unknown')
                                        fid = member.get('fid', 'N/A')
                                        furnace_lv = int(member.get('furnace_lv', 0) or 0)
                                        level = self.level_mapping.get(furnace_lv, str(furnace_lv))
                                        
                                        member_list += f"**{idx:02d}.** 👤 {nickname}\n└ 🆔 `ID: {fid}` | ⚔️ `FC: {level}`\n\n"

                                    embed.description += member_list

                                    # Footer with page info and sort order
                                    if total_pages > 1:
                                        embed.set_footer(
                                            text=f"Page {self.current_page + 1}/{total_pages} ΓÇó Stored in MongoDB",
                                            icon_url="https://cdn.discordapp.com/attachments/1435569370389807144/1445459239131680859/images_7_1.png"
                                        )
                                    else:
                                        embed.set_footer(
                                            text=f"Page {self.current_page + 1}/{total_pages} ΓÇó Stored in MongoDB",
                                            icon_url="https://cdn.discordapp.com/attachments/1435569370389807144/1445459239131680859/images_7_1.png"
                                        )

                                    return embed

                                @discord.ui.button(emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="prev_page")
                                async def previous_page(self, button_interaction: discord.Interaction, button: discord.ui.Button):
                                    if self.current_page > 0:
                                        self.current_page -= 1
                                        embed = self.create_embed()
                                        await button_interaction.response.edit_message(embed=embed, view=self)
                                    else:
                                        await button_interaction.response.defer()

                                @discord.ui.button(emoji="➡️", style=discord.ButtonStyle.secondary, custom_id="next_page")
                                async def next_page(self, button_interaction: discord.Interaction, button: discord.ui.Button):
                                    if self.current_page < self.get_total_pages() - 1:
                                        self.current_page += 1
                                        embed = self.create_embed()
                                        await button_interaction.response.edit_message(embed=embed, view=self)
                                    else:
                                        await button_interaction.response.defer()

                                @discord.ui.button(label="Filter", emoji="🔽", style=discord.ButtonStyle.secondary, custom_id="filter_sort")
                                async def filter_sort(self, button_interaction: discord.Interaction, button: discord.ui.Button):
                                    # Toggle sort order
                                    self.sort_order = "asc" if self.sort_order == "desc" else "desc"
                                    self.current_page = 0  # Reset to first page
                                    
                                    # Update button emoji
                                    button.emoji = "🔼" if self.sort_order == "asc" else "🔽"
                                    
                                    embed = self.create_embed()
                                    await button_interaction.response.edit_message(embed=embed, view=self)

                                @discord.ui.button(label="Profile", emoji="👤", style=discord.ButtonStyle.secondary, custom_id="view_profile")
                                async def view_profile(self, button_interaction: discord.Interaction, button: discord.ui.Button):
                                    # Create a select menu for choosing a player
                                    class ProfileSelectView(discord.ui.View):
                                        def __init__(self, members_data, level_map):
                                            super().__init__(timeout=60)
                                            self.members = members_data
                                            self.level_mapping = level_map
                                            
                                            # Create select menu with member options (max 25)
                                            options = []
                                            for idx, member in enumerate(sorted(
                                                self.members,
                                                key=lambda x: int(x.get('furnace_lv', 0) or 0),
                                                reverse=True
                                            )[:25], 1):
                                                nickname = member.get('nickname', 'Unknown')
                                                fid = member.get('fid', 'N/A')
                                                furnace_lv = int(member.get('furnace_lv', 0) or 0)
                                                level = self.level_mapping.get(furnace_lv, str(furnace_lv))
                                                
                                                options.append(
                                                    discord.SelectOption(
                                                        label=f"{nickname}",
                                                        description=f"FID: {fid} | FC: {level}",
                                                        value=str(fid),
                                                        emoji="👤"
                                                    )
                                                )
                                            
                                            select = discord.ui.Select(
                                                placeholder="Select a player to view profile...",
                                                options=options,
                                                custom_id="player_select"
                                            )
                                            select.callback = self.select_callback
                                            self.add_item(select)
                                        
                                        async def select_callback(self, select_interaction: discord.Interaction):
                                            selected_fid = select_interaction.data['values'][0]
                                            
                                            # Find the member
                                            member = next((m for m in self.members if m.get('fid') == selected_fid), None)
                                            
                                            if not member:
                                                await select_interaction.response.send_message(
                                                    "❌ Member not found.",
                                                    ephemeral=True
                                                )
                                                return
                                            
                                            # Defer response for API call
                                            await select_interaction.response.defer(ephemeral=True)
                                            
                                            # Create profile embed
                                            nickname = member.get('nickname', 'Unknown')
                                            fid = member.get('fid', 'N/A')
                                            furnace_lv = int(member.get('furnace_lv', 0) or 0)
                                            level = self.level_mapping.get(furnace_lv, str(furnace_lv))
                                            avatar_url = member.get('avatar_image', '')
                                            
                                            # If no avatar in database, try to fetch from API
                                            if not avatar_url:
                                                try:
                                                    from .login_handler import LoginHandler
                                                    login_handler = LoginHandler()
                                                    result = await login_handler.fetch_player_data(fid)
                                                    
                                                    # Debug logging
                                                    print(f"DEBUG: API result for FID {fid}: {result}")
                                                    
                                                    if result['status'] == 'success' and result['data']:
                                                        avatar_url = result['data'].get('avatar_image', '')
                                                        print(f"DEBUG: Avatar URL from API: {avatar_url}")
                                                        
                                                        # Update member data in MongoDB with avatar
                                                        if avatar_url and AllianceMembersAdapter:
                                                            member['avatar_image'] = avatar_url
                                                            AllianceMembersAdapter.upsert_member(str(fid), member)
                                                            print(f"DEBUG: Updated member {fid} with avatar in MongoDB")
                                                except Exception as e:
                                                    print(f"Error fetching avatar for {fid}: {e}")
                                                    import traceback
                                                    traceback.print_exc()
                                            
                                            profile_embed = discord.Embed(
                                                title=f"👤 Player Profile",
                                                description=f"**{nickname}**",
                                                color=0x5865F2
                                            )
                                            
                                            profile_embed.add_field(
                                                name="🆔 FID",
                                                value=f"`{fid}`",
                                                inline=True
                                            )
                                            
                                            profile_embed.add_field(
                                                name="⚔️ Furnace Level",
                                                value=f"`{level}`",
                                                inline=True
                                            )
                                            
                                            # Set avatar as image - ALWAYS set it if available
                                            print(f"DEBUG: Setting avatar for {nickname} (FID: {fid})")
                                            print(f"DEBUG: Avatar URL: {avatar_url}")
                                            
                                            if avatar_url:
                                                try:
                                                    profile_embed.set_image(url=avatar_url)
                                                    profile_embed.set_footer(
                                                        text="Stored in MongoDB",
                                                        icon_url="https://cdn.discordapp.com/attachments/1435569370389807144/1445459239131680859/images_7_1.png"
                                                    )
                                                    print(f"DEBUG: Avatar set successfully for {fid}")
                                                except Exception as e:
                                                    print(f"DEBUG: Error setting avatar: {e}")
                                                    profile_embed.set_footer(
                                                        text="Stored in MongoDB",
                                                        icon_url="https://cdn.discordapp.com/attachments/1435569370389807144/1445459239131680859/images_7_1.png"
                                                    )
                                            else:
                                                profile_embed.set_footer(
                                                    text="Stored in MongoDB",
                                                    icon_url="https://cdn.discordapp.com/attachments/1435569370389807144/1445459239131680859/images_7_1.png"
                                                )
                                                print(f"DEBUG: No avatar URL available for {fid}")
                                            
                                            await select_interaction.followup.send(
                                                embed=profile_embed,
                                                ephemeral=True
                                            )
                                    
                                    # Show select menu
                                    select_view = ProfileSelectView(self.members, self.level_mapping)
                                    await button_interaction.response.send_message(
                                        "**Select a player to view their profile:**",
                                        view=select_view,
                                        ephemeral=True
                                    )

                            # Create view and send
                            view = MemberListView(members, alliance_name, level_mapping)
                            embed = view.create_embed()
                            msg = await self.channel.send(embed=embed, view=view)
                            view.message = msg
                            
                            # Send success confirmation
                            success_embed = discord.Embed(
                                title="✅ Access Granted",
                                description="Member list has been displayed in the channel.",
                                color=0x57F287
                            )
                            await modal_interaction.followup.send(embed=success_embed, ephemeral=True)

                        except Exception as e:
                            print(f"Mag Shield modal error: {e}")
                            error_embed = discord.Embed(
                                title="⚠️ System Error",
                                description="An error occurred while processing your request.",
                                color=0xFEE75C
                            )
                            await modal_interaction.followup.send(
                                embed=error_embed,
                                ephemeral=True
                            )

                # Create a view with a styled button
                class ShowListView(discord.ui.View):
                    def __init__(self, alliance_id: int, guild_id: int, channel):
                        super().__init__(timeout=60)
                        self.alliance_id = alliance_id
                        self.guild_id = guild_id
                        self.channel = channel

                    @discord.ui.button(label="Authenticate", emoji="🔐", style=discord.ButtonStyle.secondary, custom_id="mag_shield_auth")
                    async def enter_password(self, button_interaction: discord.Interaction, button: discord.ui.Button):
                        modal = MagShieldModal(self.alliance_id, self.guild_id, self.channel)
                        await button_interaction.response.send_modal(modal)

                # Create initial embed
                initial_embed = discord.Embed(
                    title=message.guild.name,
                    description="**Required Rank- R5/R4**\n\nAccess to alliance member database requires authentication.",
                    color=0x2B2D31
                )
                
                initial_embed.set_author(
                    name="SECURITY VERIFICATION REQUIRED",
                    icon_url="https://cdn.discordapp.com/attachments/1435569370389807144/1445470757844160543/unnamed_6_1.png"
                )
                
                initial_embed.add_field(
                    name="🔒 Protected Resource",
                    value="Alliance Member List",
                    inline=True
                )
                
                initial_embed.add_field(
                    name="🔑 Authentication Method",
                    value="Access Code",
                    inline=True
                )
                
                initial_embed.add_field(
                    name="⚡ Quick Actions",
                    value="Click the button below to proceed with authentication.",
                    inline=False
                )
                
                initial_embed.set_footer(
                    text="Secured by Discord Interaction Gateway",
                    icon_url="https://cdn.discordapp.com/attachments/1435569370389807144/1445660030815961209/discord-logo-png_seeklogo-481205_1.png?ex=69312752&is=692fd5d2&hm=5d6d7961ff5e1d3837308cbea9c5f0baa4a5cdf59af9009e49ba67b864963fe6"
                )

                # Send button to trigger modal
                view = ShowListView(alliance_id, message.guild.id, message.channel)
                await message.channel.send(embed=initial_embed, view=view)
                return

            except Exception as e:
                print(f"Showlist error: {e}")
                error_embed = discord.Embed(
                    title="⚠️ System Error",
                    description="An unexpected error occurred while processing the command.",
                    color=0xFEE75C
                )
                await message.channel.send(embed=error_embed)
                return
        
        # Parse FIDs from message
        command, fids = self._parse_fids(content)
        
        if not fids:
            await message.channel.send(
                "❌ Invalid format. Please use:\n"
                "`!Add 123456789` or `!Add 123456789,987654321`\n"
                "`!Remove 123456789` or `!Remove 123456789,987654321`\n"
                "FIDs must be 8 or 9 digits."
            )
            return
        
        # Process command
        results = []
        
        # Send initial processing message
        processing_embed = discord.Embed(
            title="⚙️ Processing Request",
            description=f"{'➕ Adding' if command == '!Add' else '➖ Removing'} **{len(fids)}** member(s)...\n\n```\nPlease wait while we process your request.\n```",
            color=0x5865F2
        )
        processing_embed.add_field(
            name="📊 Status",
            value=f"Processing 0/{len(fids)} members",
            inline=False
        )
        processing_msg = await message.channel.send(embed=processing_embed)
        
        if command == '!Add':
            # Add members
            for idx, fid in enumerate(fids, 1):
                # Update progress
                processing_embed.set_field_at(
                    0,
                    name="📊 Status",
                    value=f"Processing {idx}/{len(fids)} members\n`{'█' * idx}{'░' * (len(fids) - idx)}`",
                    inline=False
                )
                await processing_msg.edit(embed=processing_embed)
                
                success, msg = await self._add_member_to_alliance(fid, alliance_id)
                results.append(msg)
        
        elif command == '!Remove':
            # Remove members
            for idx, fid in enumerate(fids, 1):
                # Update progress
                processing_embed.set_field_at(
                    0,
                    name="📊 Status",
                    value=f"Processing {idx}/{len(fids)} members\n`{'█' * idx}{'░' * (len(fids) - idx)}`",
                    inline=False
                )
                await processing_msg.edit(embed=processing_embed)
                
                success, msg = await self._remove_member_from_alliance(fid, alliance_id)
                results.append(msg)
        
        # Delete processing message
        await processing_msg.delete()
        
        # Count successes and failures
        success_count = sum(1 for r in results if r.startswith('✅'))
        failure_count = len(results) - success_count
        
        # Send final results with enhanced UI
        result_embed = discord.Embed(
            title=f"{'➕ Add Members' if command == '!Add' else '➖ Remove Members'} - Complete",
            description=(
                "```ml\n"
                "Operation Summary\n"
                "══════════════════════════\n"
                f"✅ Successful    : {success_count}\n"
                f"❌ Failed        : {failure_count}\n"
                f"📊 Total         : {len(fids)}\n"
                "══════════════════════════\n"
                "```\n"
                "**Detailed Results**\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
            ),
            color=0x57F287 if failure_count == 0 else (0xFEE75C if success_count > 0 else 0xED4245)
        )
        
        # Add results
        for result in results:
            result_embed.description += f"{result}\n"
        
        # Get alliance name
        try:
            from db_utils import get_db_connection
            with get_db_connection('alliance.sqlite') as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM alliance_list WHERE alliance_id = ?", (alliance_id,))
                result = cursor.fetchone()
                alliance_name = result[0] if result else f"Alliance {alliance_id}"
        except:
            alliance_name = f"Alliance {alliance_id}"
        
        result_embed.set_footer(text=f"Alliance: {alliance_name} (ID: {alliance_id})")
        
        await message.channel.send(embed=result_embed)


async def setup(bot):
    await bot.add_cog(FIDCommands(bot))
