"""
Script to replace the import from alliance handler in manage_giftcode.py
"""

# Read the file
with open(r'f:\STARK-whiteout survival bot\DISCORD BOT\cogs\manage_giftcode.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Find the start and end of the section to replace
start_marker = '        # Handle import from alliance button\n        if custom_id == "auto_redeem_import_alliance":'
end_marker = '            return\n        \n        # Handle import from channel button'

start_idx = content.find(start_marker)
end_idx = content.find(end_marker)

if start_idx == -1 or end_idx == -1:
    print("ERROR: Could not find markers")
    print(f"Start found: {start_idx != -1}")
    print(f"End found: {end_idx != -1}")
    exit(1)

# The new implementation
new_implementation = '''        # Handle import from alliance button - WITH MULTI-SELECT
        if custom_id == "auto_redeem_import_alliance":
            if not await self.check_admin_permission(interaction.user.id):
                await interaction.response.send_message(
                    "❌ Only administrators can import members.",
                    ephemeral=True
                )
                return
            
            await interaction.response.defer(ephemeral=True)
            
            try:
                # Get alliance members from MongoDB
                from db.mongo_adapters import AllianceMembersAdapter
                alliance_data = AllianceMembersAdapter.load_all()
                
                # Find alliance for this guild
                guild_alliance = None
                for alliance_id, data in alliance_data.items():
                    if data.get('guild_id') == interaction.guild.id:
                        guild_alliance = data
                        break
                
                if not guild_alliance or not guild_alliance.get('members'):
                    await interaction.followup.send(
                        "❌ No alliance found for this server.\\n\\nPlease assign an alliance first using `/alliance` command.",
                        ephemeral=True
                    )
                    return
                
                members = guild_alliance['members']
                
                # Create multi-select view
                class AllianceMemberSelectView(discord.ui.View):
                    def __init__(self, members_data, cog_instance, guild_id):
                        super().__init__(timeout=300)
                        self.members = members_data
                        self.cog = cog_instance
                        self.guild_id = guild_id
                        self.selected_fids = set()
                        self.current_page = 0
                        self.members_per_page = 20
                        self.update_components()
                    
                    def get_total_pages(self):
                        return (len(self.members) - 1) // self.members_per_page + 1
                    
                    def update_components(self):
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
                            
                            # Check if already in auto-redeem list
                            already_added = self.cog.AutoRedeemDB.member_exists(self.cog, self.guild_id, fid)
                            
                            options.append(
                                discord.SelectOption(
                                    label=f"{nickname} (FC {furnace_lv})",
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
                        # Toggle selected FIDs
                        selected_values = set(select_interaction.data['values'])
                        
                        # Get all FIDs on current page
                        start_idx = self.current_page * self.members_per_page
                        end_idx = start_idx + self.members_per_page
                        page_fids = {m.get('fid') for m in self.members[start_idx:end_idx]}
                        
                        # Remove deselected from current page
                        self.selected_fids = (self.selected_fids - page_fids) | selected_values
                        
                        self.update_components()
                        await select_interaction.response.edit_message(
                            content=f"**Selected {len(self.selected_fids)} member(s)** - Choose more or click 'Add Selected'",
                            view=self
                        )
                    
                    async def previous_page(self, btn_interaction: discord.Interaction):
                        if self.current_page > 0:
                            self.current_page -= 1
                            self.update_components()
                            await btn_interaction.response.edit_message(
                                content=f"**Selected {len(self.selected_fids)} member(s)** - Choose more or click 'Add Selected'",
                                view=self
                            )
                    
                    async def next_page(self, btn_interaction: discord.Interaction):
                        if self.current_page < self.get_total_pages() - 1:
                            self.current_page += 1
                            self.update_components()
                            await btn_interaction.response.edit_message(
                                content=f"**Selected {len(self.selected_fids)} member(s)** - Choose more or click 'Add Selected'",
                                view=self
                            )
                    
                    async def add_selected_members(self, add_interaction: discord.Interaction):
                        if not self.selected_fids:
                            await add_interaction.response.send_message("❌ No members selected.", ephemeral=True)
                            return
                        
                        # Processing animation
                        processing_embed = discord.Embed(
                            title="➕ Adding Members to Auto-Redeem",
                            description=f"Adding **{len(self.selected_fids)}** member(s)...\\n\\n```\\nPlease wait...\\n```",
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
                                
                                success = self.cog.AutoRedeemDB.add_member(
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
                        
                        # Final result
                        result_embed = discord.Embed(
                            title="➕ Import from Alliance - Complete",
                            description=f"**Results:** {success_count} added, {fail_count} failed\\n━━━━━━━━━━━━━━━━━━━━━━",
                            color=0x57F287 if success_count > 0 else 0xED4245
                        )
                        
                        results_text = "\\n".join(results[:20])
                        if results_text:
                            result_embed.add_field(name="📋 Details", value=results_text, inline=False)
                        
                        if len(results) > 20:
                            result_embed.set_footer(text=f"Showing 20 of {len(results)} results")
                        
                        await add_interaction.edit_original_response(embed=result_embed)
                
                # Show member selection
                member_view = AllianceMemberSelectView(members, self, interaction.guild.id)
                await interaction.followup.send(
                    f"**Select members to add to auto-redeem list:**\\n\\nTotal alliance members: {len(members)}",
                    view=member_view,
                    ephemeral=True
                )
                
            except Exception as e:
                self.logger.exception(f"Error in import from alliance: {e}")
                await interaction.followup.send(
                    f"❌ An error occurred: {str(e)}",
                    ephemeral=True
                )
            return
        '''

# Replace the section
new_content = content[:start_idx] + new_implementation + content[end_idx:]

# Write back
with open(r'f:\STARK-whiteout survival bot\DISCORD BOT\cogs\manage_giftcode.py', 'w', encoding='utf-8') as f:
    f.write(new_content)

print("✅ Successfully replaced import from alliance handler!")
print(f"Old section length: {end_idx - start_idx} characters")
print(f"New section length: {len(new_implementation)} characters")
