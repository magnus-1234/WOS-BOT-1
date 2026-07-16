import discord
from discord.ext import commands
from discord import app_commands
import sqlite3
from admin_utils import is_bot_owner

class RemoteAccess(commands.Cog):
    def __init__(self, bot, conn):
        self.bot = bot
        self.conn = conn
        self.settings_db = sqlite3.connect('db/settings.sqlite', check_same_thread=False)
        self.settings_cursor = self.settings_db.cursor()

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if not interaction.type == discord.InteractionType.component:
            return

        custom_id = interaction.data.get("custom_id", "")
        
        if custom_id == "remote_access":
            try:
                # Check if user is global admin
                self.settings_cursor.execute("SELECT is_initial FROM admin WHERE id = ?", (interaction.user.id,))
                result = self.settings_cursor.fetchone()
                
                if (not result or result[0] != 1) and not await is_bot_owner(self.bot, interaction.user.id):
                    await interaction.response.send_message(
                        "❌ Only global administrators can use Remote Access.",
                        ephemeral=True
                    )
                    return

                # Get all servers where the bot is present
                guilds = self.bot.guilds
                
                if not guilds:
                    await interaction.response.send_message(
                        "❌ Bot is not in any servers.",
                        ephemeral=True
                    )
                    return

                embed = discord.Embed(
                    title="🌐 Remote Access Control Panel",
                    description=(
                        "**Manage Channels Across All Servers**\n"
                        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        f"📊 **Connected Servers:** `{len(guilds)}`\n"
                        f"👥 **Total Members:** `{sum(g.member_count for g in guilds)}`\n\n"
                        "**Available Actions:**\n"
                        "• 📋 View server channels\n"
                        "• ➕ Create new channels\n"
                        "• ✏️ Edit existing channels\n"
                        "• 🗑️ Delete channels\n"
                        "• 📨 Send messages to any channel\n"
                        "• 🛡️ Start alliance monitoring\n"
                        "• 🔒 Manage permissions\n\n"
                        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        "Select a server below to begin managing its channels."
                    ),
                    color=0x00D9FF
                )
                
                embed.set_footer(
                    text=f"Remote Access ΓÇó Requested by {interaction.user.display_name}",
                    icon_url=interaction.user.display_avatar.url
                )

                # Create server selection dropdown
                if len(guilds) <= 25:
                    # Single dropdown if 25 or fewer servers
                    options = [
                        discord.SelectOption(
                            label=f"{guild.name[:90]}",
                            value=str(guild.id),
                            description=f"Members: {guild.member_count} • Channels: {len(guild.channels)}",
                            emoji="🏰"
                        )
                        for guild in sorted(guilds, key=lambda g: g.name)
                    ]

                    select = discord.ui.Select(
                        placeholder="Select a server to manage...",
                        options=options,
                        custom_id="remote_access_server_select"
                    )

                    async def server_selected(select_interaction: discord.Interaction):
                        guild_id = int(select_interaction.data["values"][0])
                        guild = self.bot.get_guild(guild_id)
                        
                        if not guild:
                            await select_interaction.response.send_message(
                                "❌ Server not found.",
                                ephemeral=True
                            )
                            return
                        
                        await self.show_server_management(select_interaction, guild)

                    select.callback = server_selected

                    view = discord.ui.View(timeout=300)
                    view.add_item(select)
                    
                    # Add back button
                    back_button = discord.ui.Button(
                        label="◀ Back to Bot Operations",
                        emoji="🤖",
                        style=discord.ButtonStyle.secondary,
                        custom_id="bot_operations"
                    )
                    view.add_item(back_button)

                    await interaction.response.edit_message(embed=embed, view=view)
                
                else:
                    # Paginated server list for more than 25 servers
                    view = RemoteServerSelectionView(self.bot, guilds, self)
                    embed = view.create_embed()
                    await interaction.response.edit_message(embed=embed, view=view)

            except Exception as e:
                print(f"Remote Access error: {e}")
                import traceback
                traceback.print_exc()
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "❌ An error occurred while loading Remote Access.",
                        ephemeral=True
                    )

    async def show_server_management(self, interaction: discord.Interaction, guild: discord.Guild):
        """Show management options for a specific server"""
        try:
            # Check bot permissions in this guild
            bot_member = guild.get_member(self.bot.user.id)
            has_manage_channels = bot_member.guild_permissions.manage_channels if bot_member else False
            
            # Get channel counts
            text_channels = [c for c in guild.channels if isinstance(c, discord.TextChannel)]
            voice_channels = [c for c in guild.channels if isinstance(c, discord.VoiceChannel)]
            categories = [c for c in guild.channels if isinstance(c, discord.CategoryChannel)]
            
            embed = discord.Embed(
                title=f"🏰 Managing: {guild.name}",
                description=(
                    f"**Server Information**\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"👥 **Members:** `{guild.member_count}`\n"
                    f"📝 **Text Channels:** `{len(text_channels)}`\n"
                    f"🔊 **Voice Channels:** `{len(voice_channels)}`\n"
                    f"📁 **Categories:** `{len(categories)}`\n\n"
                    f"🔑 **Bot Permissions:**\n"
                    f"{'✅' if has_manage_channels else '❌'} Manage Channels\n"
                    f"{'✅' if bot_member.guild_permissions.administrator else '❌'} Administrator\n\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"Select an action below to manage channels in this server."
                ),
                color=0x5865F2
            )
            
            if guild.icon:
                embed.set_thumbnail(url=guild.icon.url)
            
            embed.set_footer(
                text=f"Server ID: {guild.id}",
                icon_url=interaction.user.display_avatar.url
            )

            view = discord.ui.View(timeout=300)
            
            # View Channels button
            view.add_item(discord.ui.Button(
                label="View Channels",
                emoji="📋",
                style=discord.ButtonStyle.primary,
                custom_id=f"remote_view_channels_{guild.id}",
                row=0
            ))
            
            # Create Channel button
            view.add_item(discord.ui.Button(
                label="Create Channel",
                emoji="➕",
                style=discord.ButtonStyle.success,
                custom_id=f"remote_create_channel_{guild.id}",
                disabled=not has_manage_channels,
                row=0
            ))
            
            # Edit Channel button
            view.add_item(discord.ui.Button(
                label="Edit Channel",
                emoji="✏️",
                style=discord.ButtonStyle.primary,
                custom_id=f"remote_edit_channel_{guild.id}",
                disabled=not has_manage_channels,
                row=0
            ))
            
            # Delete Channel button
            view.add_item(discord.ui.Button(
                label="Delete Channel",
                emoji="🗑️",
                style=discord.ButtonStyle.danger,
                custom_id=f"remote_delete_channel_{guild.id}",
                disabled=not has_manage_channels,
                row=1
            ))
            
            # Manage Permissions button
            view.add_item(discord.ui.Button(
                label="Manage Permissions",
                emoji="🔒",
                style=discord.ButtonStyle.primary,
                custom_id=f"remote_manage_perms_{guild.id}",
                disabled=not has_manage_channels,
                row=1
            ))
            
            
            # Send Message button
            view.add_item(discord.ui.Button(
                label="Send Message",
                emoji="📨",
                style=discord.ButtonStyle.success,
                custom_id=f"remote_send_message_{guild.id}",
                row=1
            ))

            # Delete Message button
            view.add_item(discord.ui.Button(
                label="Delete Message",
                emoji="🗑️",
                style=discord.ButtonStyle.danger,
                custom_id=f"remote_delete_message_{guild.id}",
                row=1
            ))
            
            

            
            # Remote Alliance Monitor button
            view.add_item(discord.ui.Button(
                label="Alliance Monitor",
                emoji="🛡️",
                style=discord.ButtonStyle.success,
                custom_id=f"remote_alliance_monitor_{guild.id}",
                row=2
            ))
            
            # Stop Alliance Monitor button
            view.add_item(discord.ui.Button(
                label="Stop Monitor",
                emoji="🛑",
                style=discord.ButtonStyle.danger,
                custom_id=f"remote_stop_alliance_monitor_{guild.id}",
                row=2
            ))

            # Kick User button
            bot_member = guild.get_member(self.bot.user.id)
            has_kick = bot_member.guild_permissions.kick_members if bot_member else False
            view.add_item(discord.ui.Button(
                label="Kick User",
                emoji="👢",
                style=discord.ButtonStyle.danger,
                custom_id=f"remote_kick_user_{guild.id}",
                disabled=not has_kick,
                row=2
            ))
            
            # Back button
            view.add_item(discord.ui.Button(
                label="◀ Back to Server List",
                emoji="🌐",
                style=discord.ButtonStyle.secondary,
                custom_id="remote_access",
                row=3
            ))

            # Set up callbacks for buttons
            for item in view.children:
                if isinstance(item, discord.ui.Button) and item.custom_id:
                    if item.custom_id.startswith("remote_view_channels_"):
                        item.callback = lambda i, g=guild: self.view_channels(i, g)
                    elif item.custom_id.startswith("remote_create_channel_"):
                        item.callback = lambda i, g=guild: self.create_channel(i, g)
                    elif item.custom_id.startswith("remote_edit_channel_"):
                        item.callback = lambda i, g=guild: self.edit_channel(i, g)
                    elif item.custom_id.startswith("remote_delete_channel_"):
                        item.callback = lambda i, g=guild: self.delete_channel(i, g)
                    elif item.custom_id.startswith("remote_manage_perms_"):
                        item.callback = lambda i, g=guild: self.manage_permissions(i, g)
                    elif item.custom_id.startswith("remote_send_message_"):
                        item.callback = lambda i, g=guild: self.send_message(i, g)
                    elif item.custom_id.startswith("remote_delete_message_"):
                        item.callback = lambda i, g=guild: self.delete_message_by_id(i, g)

                    elif item.custom_id.startswith("remote_alliance_monitor_"):
                        item.callback = lambda i, g=guild: self.start_alliance_monitor(i, g)
                    elif item.custom_id.startswith("remote_stop_alliance_monitor_"):
                        item.callback = lambda i, g=guild: self.stop_alliance_monitor(i, g)
                    elif item.custom_id.startswith("remote_kick_user_"):
                        item.callback = lambda i, g=guild: self.kick_user(i, g)

            await interaction.response.edit_message(embed=embed, view=view)
            
        except Exception as e:
            print(f"Show server management error: {e}")
            import traceback
            traceback.print_exc()
            await interaction.response.send_message(
                "❌ An error occurred while loading server management.",
                ephemeral=True
            )

    async def view_channels(self, interaction: discord.Interaction, guild: discord.Guild):
        """View all channels in a server"""
        try:
            await interaction.response.defer()
            
            # Organize channels by category
            categories_data = {}
            uncategorized_channels = []
            
            for channel in sorted(guild.channels, key=lambda c: c.position):
                if isinstance(channel, discord.CategoryChannel):
                    categories_data[channel.id] = {
                        'name': channel.name,
                        'channels': []
                    }
                elif channel.category:
                    if channel.category.id not in categories_data:
                        categories_data[channel.category.id] = {
                            'name': channel.category.name,
                            'channels': []
                        }
                    categories_data[channel.category.id]['channels'].append(channel)
                elif not isinstance(channel, discord.CategoryChannel):
                    uncategorized_channels.append(channel)
            
            # Build embed
            embed = discord.Embed(
                title=f"📋 Channels in {guild.name}",
                description=f"Total channels: {len([c for c in guild.channels if not isinstance(c, discord.CategoryChannel)])}",
                color=0x5865F2
            )
            
            # Add uncategorized channels first
            if uncategorized_channels:
                channel_list = ""
                for channel in uncategorized_channels[:10]:  # Limit to prevent embed size issues
                    emoji = "📝" if isinstance(channel, discord.TextChannel) else "🔊" if isinstance(channel, discord.VoiceChannel) else "📢"
                    channel_list += f"{emoji} {channel.mention if isinstance(channel, discord.TextChannel) else channel.name}\n"
                
                if channel_list:
                    embed.add_field(
                        name="📌 Uncategorized",
                        value=channel_list,
                        inline=False
                    )
            
            # Add categorized channels
            for cat_id, cat_data in list(categories_data.items())[:5]:  # Limit categories
                if cat_data['channels']:
                    channel_list = ""
                    for channel in cat_data['channels'][:10]:
                        emoji = "📝" if isinstance(channel, discord.TextChannel) else "🔊" if isinstance(channel, discord.VoiceChannel) else "📢"
                        channel_list += f"{emoji} {channel.mention if isinstance(channel, discord.TextChannel) else channel.name}\n"
                    
                    if channel_list:
                        embed.add_field(
                            name=f"📁 {cat_data['name']}",
                            value=channel_list,
                            inline=False
                        )
            
            embed.set_footer(text=f"Server ID: {guild.id}")
            
            # Back button
            view = discord.ui.View(timeout=300)
            back_button = discord.ui.Button(
                label="◀ Back",
                emoji="🏰",
                style=discord.ButtonStyle.secondary,
                custom_id=f"back_to_server_{guild.id}"
            )
            back_button.callback = lambda i: self.show_server_management(i, guild)
            view.add_item(back_button)
            
            await interaction.edit_original_response(embed=embed, view=view)
            
        except Exception as e:
            print(f"View channels error: {e}")
            import traceback
            traceback.print_exc()
            await interaction.followup.send(
                "❌ An error occurred while viewing channels.",
                ephemeral=True
            )

    async def create_channel(self, interaction: discord.Interaction, guild: discord.Guild):
        """Create a new channel in the server"""
        try:
            # Create modal for channel creation
            from discord.ui import Modal, TextInput
            
            class CreateChannelModal(Modal, title="Create New Channel"):
                channel_name = TextInput(
                    label="Channel Name",
                    placeholder="Enter channel name...",
                    required=True,
                    min_length=1,
                    max_length=100
                )
                
                channel_type = TextInput(
                    label="Channel Type (text/voice/category)",
                    placeholder="text",
                    required=True,
                    min_length=4,
                    max_length=8,
                    default="text"
                )
                
                channel_topic = TextInput(
                    label="Channel Topic (optional, text only)",
                    placeholder="Enter channel topic...",
                    required=False,
                    max_length=1024,
                    style=discord.TextStyle.paragraph
                )
                
                def __init__(self, parent_cog, target_guild):
                    super().__init__()
                    self.parent_cog = parent_cog
                    self.target_guild = target_guild
                
                async def on_submit(self, modal_interaction: discord.Interaction):
                    try:
                        channel_type_str = self.channel_type.value.lower().strip()
                        name = self.channel_name.value.strip()
                        topic = self.channel_topic.value.strip() if self.channel_topic.value else None
                        
                        # Validate channel type
                        if channel_type_str not in ['text', 'voice', 'category']:
                            await modal_interaction.response.send_message(
                                "❌ Invalid channel type. Use: text, voice, or category",
                                ephemeral=True
                            )
                            return
                        
                        await modal_interaction.response.defer(ephemeral=True)
                        
                        # Create the channel
                        created_channel = None
                        if channel_type_str == 'text':
                            created_channel = await self.target_guild.create_text_channel(
                                name=name,
                                topic=topic
                            )
                        elif channel_type_str == 'voice':
                            created_channel = await self.target_guild.create_voice_channel(
                                name=name
                            )
                        elif channel_type_str == 'category':
                            created_channel = await self.target_guild.create_category(
                                name=name
                            )
                        
                        if created_channel:
                            success_embed = discord.Embed(
                                title="✅ Channel Created",
                                description=(
                                    f"**Channel Name:** {created_channel.mention if hasattr(created_channel, 'mention') else created_channel.name}\n"
                                    f"**Type:** {channel_type_str.title()}\n"
                                    f"**Server:** {self.target_guild.name}\n"
                                    f"**Channel ID:** `{created_channel.id}`"
                                ),
                                color=0x57F287
                            )
                            
                            await modal_interaction.followup.send(embed=success_embed, ephemeral=True)
                        else:
                            await modal_interaction.followup.send(
                                "❌ Failed to create channel.",
                                ephemeral=True
                            )
                    
                    except discord.Forbidden:
                        await modal_interaction.followup.send(
                            "❌ I don't have permission to create channels in this server.",
                            ephemeral=True
                        )
                    except Exception as e:
                        print(f"Create channel modal error: {e}")
                        await modal_interaction.followup.send(
                            f"❌ An error occurred: {str(e)}",
                            ephemeral=True
                        )
            
            modal = CreateChannelModal(self, guild)
            await interaction.response.send_modal(modal)
            
        except Exception as e:
            print(f"Create channel error: {e}")
            import traceback
            traceback.print_exc()
            await interaction.response.send_message(
                "❌ An error occurred while creating the channel.",
                ephemeral=True
            )

    async def edit_channel(self, interaction: discord.Interaction, guild: discord.Guild):
        """Edit an existing channel in the server"""
        try:
            # Get all editable channels (text and voice, not categories)
            channels = [
                c for c in guild.channels 
                if isinstance(c, (discord.TextChannel, discord.VoiceChannel))
            ]
            
            if not channels:
                await interaction.response.send_message(
                    "❌ No editable channels found in this server.",
                    ephemeral=True
                )
                return
            
            # Create channel selection dropdown
            options = [
                discord.SelectOption(
                    label=f"{channel.name[:90]}",
                    value=str(channel.id),
                    description=f"{'Text' if isinstance(channel, discord.TextChannel) else 'Voice'} • Category: {channel.category.name if channel.category else 'None'}",
                    emoji="📝" if isinstance(channel, discord.TextChannel) else "🔊"
                )
                for channel in sorted(channels, key=lambda c: c.position)[:25]
            ]
            
            select = discord.ui.Select(
                placeholder="Select a channel to edit...",
                options=options,
                custom_id="select_channel_to_edit"
            )
            
            async def channel_selected(select_interaction: discord.Interaction):
                channel_id = int(select_interaction.data["values"][0])
                channel = guild.get_channel(channel_id)
                
                if not channel:
                    await select_interaction.response.send_message(
                        "❌ Channel not found.",
                        ephemeral=True
                    )
                    return
                
                # Create modal for editing
                from discord.ui import Modal, TextInput
                
                class EditChannelModal(Modal, title=f"Edit: {channel.name}"):
                    new_name = TextInput(
                        label="Channel Name",
                        placeholder="Enter new channel name...",
                        required=False,
                        min_length=1,
                        max_length=100,
                        default=channel.name
                    )
                    
                    new_topic = TextInput(
                        label="Channel Topic (text channels only)",
                        placeholder="Enter new topic...",
                        required=False,
                        max_length=1024,
                        style=discord.TextStyle.paragraph,
                        default=channel.topic if isinstance(channel, discord.TextChannel) and channel.topic else ""
                    )
                    
                    async def on_submit(self, modal_int: discord.Interaction):
                        try:
                            await modal_int.response.defer(ephemeral=True)
                            
                            changes = []
                            if self.new_name.value and self.new_name.value != channel.name:
                                await channel.edit(name=self.new_name.value)
                                changes.append(f"**Name:** `{channel.name}` → `{self.new_name.value}`")
                            
                            if isinstance(channel, discord.TextChannel) and self.new_topic.value != (channel.topic or ""):
                                await channel.edit(topic=self.new_topic.value if self.new_topic.value else None)
                                changes.append(f"**Topic:** Updated")
                            
                            if changes:
                                success_embed = discord.Embed(
                                    title="✅ Channel Updated",
                                    description=f"**Changes made to {channel.mention}:**\n" + "\n".join(changes),
                                    color=0x57F287
                                )
                                await modal_int.followup.send(embed=success_embed, ephemeral=True)
                            else:
                                await modal_int.followup.send(
                                    "ℹ️ No changes made.",
                                    ephemeral=True
                                )
                        
                        except discord.Forbidden:
                            await modal_int.followup.send(
                                "❌ I don't have permission to edit this channel.",
                                ephemeral=True
                            )
                        except Exception as e:
                            print(f"Edit channel error: {e}")
                            await modal_int.followup.send(
                                f"❌ An error occurred: {str(e)}",
                                ephemeral=True
                            )
                
                modal = EditChannelModal()
                await select_interaction.response.send_modal(modal)
            
            select.callback = channel_selected
            
            view = discord.ui.View(timeout=300)
            view.add_item(select)
            
            # Back button
            back_button = discord.ui.Button(
                label="◀ Back",
                emoji="🏰",
                style=discord.ButtonStyle.secondary
            )
            back_button.callback = lambda i: self.show_server_management(i, guild)
            view.add_item(back_button)
            
            embed = discord.Embed(
                title=f"✏️ Edit Channel in {guild.name}",
                description="Select a channel to edit from the dropdown below.",
                color=0x5865F2
            )
            
            await interaction.response.edit_message(embed=embed, view=view)
            
        except Exception as e:
            print(f"Edit channel error: {e}")
            import traceback
            traceback.print_exc()
            await interaction.response.send_message(
                "❌ An error occurred while editing the channel.",
                ephemeral=True
            )

    async def delete_channel(self, interaction: discord.Interaction, guild: discord.Guild):
        """Delete a channel from the server"""
        try:
            # Get all deletable channels
            channels = [
                c for c in guild.channels 
                if isinstance(c, (discord.TextChannel, discord.VoiceChannel, discord.CategoryChannel))
            ]
            
            if not channels:
                await interaction.response.send_message(
                    "❌ No channels found in this server.",
                    ephemeral=True
                )
                return
            
            # Create channel selection dropdown
            options = [
                discord.SelectOption(
                    label=f"{channel.name[:90]}",
                    value=str(channel.id),
                    description=f"{'Text' if isinstance(channel, discord.TextChannel) else 'Voice' if isinstance(channel, discord.VoiceChannel) else 'Category'}",
                    emoji="📝" if isinstance(channel, discord.TextChannel) else "🔊" if isinstance(channel, discord.VoiceChannel) else "📁"
                )
                for channel in sorted(channels, key=lambda c: c.position)[:25]
            ]
            
            select = discord.ui.Select(
                placeholder="Select a channel to delete...",
                options=options,
                custom_id="select_channel_to_delete"
            )
            
            async def channel_selected(select_interaction: discord.Interaction):
                channel_id = int(select_interaction.data["values"][0])
                channel = guild.get_channel(channel_id)
                
                if not channel:
                    await select_interaction.response.send_message(
                        "❌ Channel not found.",
                        ephemeral=True
                    )
                    return
                
                # Confirmation prompt
                confirm_embed = discord.Embed(
                    title="⚠️ Confirm Channel Deletion",
                    description=(
                        f"Are you sure you want to delete this channel?\n\n"
                        f"**Channel:** {channel.mention if hasattr(channel, 'mention') else channel.name}\n"
                        f"**Type:** {channel.__class__.__name__}\n"
                        f"**Server:** {guild.name}\n\n"
                        f"⚠️ **This action cannot be undone!**"
                    ),
                    color=0xED4245
                )
                
                confirm_view = discord.ui.View(timeout=60)
                
                async def confirm_delete(confirm_int: discord.Interaction):
                    try:
                        channel_name = channel.name
                        await channel.delete()
                        
                        success_embed = discord.Embed(
                            title="✅ Channel Deleted",
                            description=f"Successfully deleted **{channel_name}** from **{guild.name}**",
                            color=0x57F287
                        )
                        
                        await confirm_int.response.edit_message(embed=success_embed, view=None)
                    
                    except discord.Forbidden:
                        await confirm_int.response.send_message(
                            "❌ I don't have permission to delete this channel.",
                            ephemeral=True
                        )
                    except Exception as e:
                        print(f"Delete channel error: {e}")
                        await confirm_int.response.send_message(
                            f"❌ An error occurred: {str(e)}",
                            ephemeral=True
                        )
                
                async def cancel_delete(cancel_int: discord.Interaction):
                    cancel_embed = discord.Embed(
                        title="❌ Deletion Cancelled",
                        description="Channel deletion has been cancelled.",
                        color=0x5865F2
                    )
                    await cancel_int.response.edit_message(embed=cancel_embed, view=None)
                
                confirm_button = discord.ui.Button(
                    label="Confirm Delete",
                    style=discord.ButtonStyle.danger,
                    emoji="🗑️"
                )
                confirm_button.callback = confirm_delete
                
                cancel_button = discord.ui.Button(
                    label="Cancel",
                    style=discord.ButtonStyle.secondary,
                    emoji="❌"
                )
                cancel_button.callback = cancel_delete
                
                confirm_view.add_item(confirm_button)
                confirm_view.add_item(cancel_button)
                
                await select_interaction.response.edit_message(embed=confirm_embed, view=confirm_view)
            
            select.callback = channel_selected
            
            view = discord.ui.View(timeout=300)
            view.add_item(select)
            
            # Back button
            back_button = discord.ui.Button(
                label="◀ Back",
                emoji="🏰",
                style=discord.ButtonStyle.secondary
            )
            back_button.callback = lambda i: self.show_server_management(i, guild)
            view.add_item(back_button)
            
            embed = discord.Embed(
                title=f"🗑️ Delete Channel in {guild.name}",
                description="⚠️ Select a channel to delete. **This action cannot be undone!**",
                color=0xED4245
            )
            
            await interaction.response.edit_message(embed=embed, view=view)
            
        except Exception as e:
            print(f"Delete channel UI error: {e}")
            import traceback
            traceback.print_exc()
            await interaction.response.send_message(
                "❌ An error occurred while preparing channel deletion.",
                ephemeral=True
            )


    async def send_message(self, interaction: discord.Interaction, guild: discord.Guild):
        """Send a message to any channel in the server — paginated to bypass 25-option limit"""
        try:
            # Get all text channels where bot can send messages
            all_channels = sorted(
                [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages],
                key=lambda c: c.position
            )

            if not all_channels:
                await interaction.response.send_message(
                    "❌ No accessible text channels found in this server where I can send messages.",
                    ephemeral=True
                )
                return

            await self._show_channel_page(interaction, guild, all_channels, page=0)

        except Exception as e:
            print(f"Send message error: {e}")
            import traceback
            traceback.print_exc()
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ An error occurred while preparing to send message.",
                    ephemeral=True
                )

    async def _show_channel_page(
        self,
        interaction: discord.Interaction,
        guild: discord.Guild,
        all_channels: list,
        page: int = 0
    ):
        """Render one page of the paginated channel picker for Send Message."""
        PAGE_SIZE = 23  # leave room for prev/next/back buttons in the view
        total_pages = max(1, (len(all_channels) + PAGE_SIZE - 1) // PAGE_SIZE)
        page = max(0, min(page, total_pages - 1))

        page_channels = all_channels[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]

        options = [
            discord.SelectOption(
                label=channel.name[:90],
                value=str(channel.id),
                description=(
                    f"{'#' + channel.category.name if channel.category else 'No category'}"
                    + (f" • {channel.topic[:40]}" if channel.topic else "")
                )[:100],
                emoji="📝"
            )
            for channel in page_channels
        ]

        select = discord.ui.Select(
            placeholder=f"Select a channel… (page {page+1}/{total_pages})",
            options=options,
            custom_id="select_channel_for_message"
        )

        async def channel_selected(sel_int: discord.Interaction):
            channel_id = int(sel_int.data["values"][0])
            channel = guild.get_channel(channel_id)
            if not channel:
                await sel_int.response.send_message("❌ Channel not found.", ephemeral=True)
                return
            await self.show_message_type_selection(sel_int, channel, guild)

        select.callback = channel_selected

        view = discord.ui.View(timeout=300)
        view.add_item(select)

        # Prev page button
        prev_btn = discord.ui.Button(
            label="◀ Prev",
            style=discord.ButtonStyle.secondary,
            disabled=(page == 0),
            row=1
        )
        async def go_prev(btn_int: discord.Interaction):
            await self._show_channel_page(btn_int, guild, all_channels, page - 1)
        prev_btn.callback = go_prev
        view.add_item(prev_btn)

        # Page counter (disabled label button)
        counter_btn = discord.ui.Button(
            label=f"Page {page+1} / {total_pages}  ({len(all_channels)} channels)",
            style=discord.ButtonStyle.secondary,
            disabled=True,
            row=1
        )
        view.add_item(counter_btn)

        # Next page button
        next_btn = discord.ui.Button(
            label="Next ▶",
            style=discord.ButtonStyle.secondary,
            disabled=(page >= total_pages - 1),
            row=1
        )
        async def go_next(btn_int: discord.Interaction):
            await self._show_channel_page(btn_int, guild, all_channels, page + 1)
        next_btn.callback = go_next
        view.add_item(next_btn)

        # Back to server management
        back_btn = discord.ui.Button(
            label="◀ Back",
            emoji="🏰",
            style=discord.ButtonStyle.secondary,
            row=2
        )
        back_btn.callback = lambda i: self.show_server_management(i, guild)
        view.add_item(back_btn)

        embed = discord.Embed(
            title=f"📨 Send Message in {guild.name}",
            description=(
                "**Select a channel to send a message**\n\n"
                "You'll be able to:\n"
                "• Send plain text messages\n"
                "• Create rich embeds\n"
                "• Send messages with custom formatting\n\n"
                f"Showing **{len(page_channels)}** of **{len(all_channels)}** channels "
                f"(page {page+1}/{total_pages}).\n"
                "Use the ◀ Prev / Next ▶ buttons to browse all channels."
            ),
            color=0x57F287
        )

        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=view)
        else:
            await interaction.response.edit_message(embed=embed, view=view)

    async def show_message_type_selection(self, interaction: discord.Interaction, channel: discord.TextChannel, guild: discord.Guild):
        """Show message type selection (plain text or embed)"""
        try:
            embed = discord.Embed(
                title="📨 Choose Message Type",
                description=(
                    f"**Sending to:** {channel.mention}\n"
                    f"**Server:** {guild.name}\n\n"
                    "Select the type of message you want to send:\n\n"
                    "📝 **Plain Text** — raw message\n"
                    "🔔 **Tag User & Send** — pick a member → real @mention\n"
                    "📣 **Tag Role & Send** — pick a role → real role ping\n"
                    "🎨 **Embed** — rich embed card\n"
                    "📢 **Announcement** — @everyone / @here / role ping"
                ),
                color=0x5865F2
            )
            
            view = discord.ui.View(timeout=300)
            
            # Plain Text button
            plain_button = discord.ui.Button(
                label="Plain Text Message",
                emoji="📝",
                style=discord.ButtonStyle.primary,
                custom_id="send_plain_text"
            )
            
            async def send_plain_text(button_int: discord.Interaction):
                from discord.ui import Modal, TextInput
                
                class PlainMessageModal(Modal, title=f"Send to #{channel.name}"):
                    message_content = TextInput(
                        label="Message Content",
                        placeholder="Type your message here...",
                        required=True,
                        max_length=2000,
                        style=discord.TextStyle.paragraph
                    )
                    
                    async def on_submit(self, modal_int: discord.Interaction):
                        try:
                            await modal_int.response.defer(ephemeral=True)
                            
                            # Send the message
                            sent_message = await channel.send(self.message_content.value)
                            
                            success_embed = discord.Embed(
                                title="✅ Message Sent",
                                description=(
                                    f"**Channel:** {channel.mention}\n"
                                    f"**Server:** {guild.name}\n"
                                    f"**Message ID:** `{sent_message.id}`\n\n"
                                    f"**Content Preview:**\n{self.message_content.value[:500]}"
                                ),
                                color=0x57F287
                            )
                            
                            success_embed.add_field(
                                name="🔗 Jump to Message",
                                value=f"[Click here]({sent_message.jump_url})",
                                inline=False
                            )
                            
                            await modal_int.followup.send(embed=success_embed, ephemeral=True)
                            
                        except discord.Forbidden:
                            await modal_int.followup.send(
                                "❌ I don't have permission to send messages in this channel.",
                                ephemeral=True
                            )
                        except Exception as e:
                            print(f"Send message error: {e}")
                            await modal_int.followup.send(
                                f"❌ An error occurred: {str(e)}",
                                ephemeral=True
                            )
                
                modal = PlainMessageModal()
                await button_int.response.send_modal(modal)
            
            plain_button.callback = send_plain_text
            view.add_item(plain_button)

            # ── Tag User & Send ──────────────────────────────────────────────
            tag_user_button = discord.ui.Button(
                label="Tag User & Send",
                emoji="🔔",
                style=discord.ButtonStyle.success,
                custom_id="send_tag_user"
            )

            async def send_tag_user(button_int: discord.Interaction):
                """Step 1: pick a member from dropdown OR search by name/ID."""
                try:
                    all_members = sorted(
                        [m for m in guild.members if not m.bot],
                        key=lambda m: m.display_name.lower()
                    )
                    total_members = len(all_members)
                    top_members = all_members[:25]

                    # ── shared helper: open message modal for a resolved member ──
                    async def open_message_modal_for(resp_obj, target_member: discord.Member):
                        """Opens TagUserMessageModal. resp_obj must support send_modal."""
                        from discord.ui import Modal, TextInput

                        class TagUserMessageModal(Modal, title=f"Message to @{target_member.display_name}"[:45]):
                            message_content = TextInput(
                                label="Message Content",
                                placeholder=f"Message after tagging {target_member.display_name}...",
                                required=True,
                                max_length=1900,
                                style=discord.TextStyle.paragraph
                            )

                            async def on_submit(self, modal_int: discord.Interaction):
                                try:
                                    await modal_int.response.defer(ephemeral=True)
                                    full_content = f"{target_member.mention} {self.message_content.value}"
                                    sent_message = await channel.send(
                                        content=full_content,
                                        allowed_mentions=discord.AllowedMentions(users=True)
                                    )
                                    success_embed = discord.Embed(
                                        title="✅ Message Sent",
                                        description=(
                                            f"**Channel:** {channel.mention}\n"
                                            f"**Tagged:** {target_member.mention}\n"
                                            f"**Server:** {guild.name}\n"
                                            f"**Message ID:** `{sent_message.id}`"
                                        ),
                                        color=0x57F287
                                    )
                                    success_embed.add_field(
                                        name="🔗 Jump to Message",
                                        value=f"[Click here]({sent_message.jump_url})",
                                        inline=False
                                    )
                                    await modal_int.followup.send(embed=success_embed, ephemeral=True)
                                except discord.Forbidden:
                                    await modal_int.followup.send(
                                        "❌ I don't have permission to send messages in this channel.",
                                        ephemeral=True
                                    )
                                except Exception as e:
                                    print(f"Tag user send error: {e}")
                                    await modal_int.followup.send(
                                        f"❌ An error occurred: {str(e)}", ephemeral=True
                                    )

                        await resp_obj.send_modal(TagUserMessageModal())

                    # ── dropdown for top-25 ──
                    member_options = [
                        discord.SelectOption(
                            label=m.display_name[:90],
                            value=str(m.id),
                            description=f"@{m.name}" + (f"#{m.discriminator}" if m.discriminator != "0" else ""),
                            emoji="👤"
                        )
                        for m in top_members
                    ]

                    member_select = discord.ui.Select(
                        placeholder=f"Quick-pick (top 25 of {total_members} members)...",
                        options=member_options,
                        custom_id="select_member_to_tag"
                    )

                    async def member_chosen(sel_int: discord.Interaction):
                        member_id = int(sel_int.data["values"][0])
                        target_member = guild.get_member(member_id)
                        if not target_member:
                            await sel_int.response.send_message("❌ Member not found.", ephemeral=True)
                            return
                        await open_message_modal_for(sel_int.response, target_member)

                    member_select.callback = member_chosen

                    # ── search button for members NOT in top-25 ──
                    search_btn = discord.ui.Button(
                        label="Search by Name / User ID",
                        emoji="🔍",
                        style=discord.ButtonStyle.secondary,
                        custom_id="search_user_to_tag"
                    )

                    async def search_user(search_int: discord.Interaction):
                        from discord.ui import Modal, TextInput

                        class SearchAndSendModal(Modal, title="Search Member & Send Message"):
                            query = TextInput(
                                label="Name, Display Name, or User ID",
                                placeholder="e.g.  John   or   john#1234   or   123456789012345678",
                                required=True,
                                max_length=100
                            )
                            message_content = TextInput(
                                label="Message to send (after the tag)",
                                placeholder="Type your message here...",
                                required=True,
                                max_length=1900,
                                style=discord.TextStyle.paragraph
                            )

                            async def on_submit(self, modal_int: discord.Interaction):
                                raw = self.query.value.strip().lstrip("@")
                                msg_text = self.message_content.value

                                # helper: send the tagged message directly
                                async def send_to(target_member: discord.Member, resp):
                                    try:
                                        full_content = f"{target_member.mention} {msg_text}"
                                        sent = await channel.send(
                                            content=full_content,
                                            allowed_mentions=discord.AllowedMentions(users=True)
                                        )
                                        ok_embed = discord.Embed(
                                            title="✅ Message Sent",
                                            description=(
                                                f"**Channel:** {channel.mention}\n"
                                                f"**Tagged:** {target_member.mention}\n"
                                                f"**Server:** {guild.name}\n"
                                                f"**Message ID:** `{sent.id}`"
                                            ),
                                            color=0x57F287
                                        )
                                        ok_embed.add_field(
                                            name="🔗 Jump to Message",
                                            value=f"[Click here]({sent.jump_url})",
                                            inline=False
                                        )
                                        await resp.send(embed=ok_embed, ephemeral=True)
                                    except discord.Forbidden:
                                        await resp.send("❌ No permission to send in that channel.", ephemeral=True)
                                    except Exception as ex:
                                        await resp.send(f"❌ Error: {ex}", ephemeral=True)

                                # 1. Exact User ID
                                target = None
                                if raw.isdigit():
                                    target = guild.get_member(int(raw))

                                # 2. username#discriminator
                                if not target and "#" in raw:
                                    name_part, disc_part = raw.rsplit("#", 1)
                                    target = discord.utils.get(
                                        guild.members, name=name_part, discriminator=disc_part
                                    )

                                # 3. Partial display name / username (case-insensitive)
                                if not target:
                                    raw_lower = raw.lower()
                                    matches = [
                                        m for m in guild.members
                                        if not m.bot and (
                                            raw_lower in m.display_name.lower()
                                            or raw_lower in m.name.lower()
                                        )
                                    ]
                                    if len(matches) == 1:
                                        target = matches[0]
                                    elif len(matches) > 1:
                                        # Multiple matches → show a dropdown to pick, then send directly
                                        match_options = [
                                            discord.SelectOption(
                                                label=m.display_name[:90],
                                                value=str(m.id),
                                                description=f"@{m.name}",
                                                emoji="👤"
                                            )
                                            for m in matches[:25]
                                        ]
                                        match_select = discord.ui.Select(
                                            placeholder=f"{len(matches)} matches – pick the right one...",
                                            options=match_options,
                                            custom_id="select_searched_member"
                                        )

                                        async def pick_and_send(pick_int: discord.Interaction):
                                            picked = guild.get_member(int(pick_int.data["values"][0]))
                                            if not picked:
                                                await pick_int.response.send_message("❌ Member not found.", ephemeral=True)
                                                return
                                            await pick_int.response.defer(ephemeral=True)
                                            await send_to(picked, pick_int.followup)

                                        match_select.callback = pick_and_send
                                        result_view = discord.ui.View(timeout=120)
                                        result_view.add_item(match_select)
                                        result_embed = discord.Embed(
                                            title=f"🔍 {len(matches)} result(s) for \"{raw}\"",
                                            description=(
                                                f"Select the correct member below.\n"
                                                f"Your message will be sent immediately after selecting."
                                            ),
                                            color=0x5865F2
                                        )
                                        await modal_int.response.send_message(
                                            embed=result_embed, view=result_view, ephemeral=True
                                        )
                                        return

                                if not target:
                                    await modal_int.response.send_message(
                                        f"❌ No member found matching **{raw}**.\n"
                                        "Tips:\n"
                                        "• Paste the **User ID** (18-digit number) for a guaranteed match\n"
                                        "• Try a shorter part of the name\n"
                                        "• Check spelling",
                                        ephemeral=True
                                    )
                                    return

                                # Single match → send right away
                                await modal_int.response.defer(ephemeral=True)
                                await send_to(target, modal_int.followup)

                        await search_int.response.send_modal(SearchAndSendModal())

                    search_btn.callback = search_user

                    tag_view = discord.ui.View(timeout=120)
                    tag_view.add_item(member_select)
                    tag_view.add_item(search_btn)

                    tag_embed = discord.Embed(
                        title="🔔 Tag User & Send",
                        description=(
                            f"**Sending to:** {channel.mention}\n\n"
                            f"**{total_members} members** in this server.\n"
                            f"• Dropdown shows top **25** (alphabetical)\n"
                            f"• Use **🔍 Search** to find anyone by name or User ID"
                        ),
                        color=0x57F287
                    )

                    await button_int.response.edit_message(embed=tag_embed, view=tag_view)

                except Exception as e:
                    print(f"Tag user flow error: {e}")
                    import traceback; traceback.print_exc()
                    if not button_int.response.is_done():
                        await button_int.response.send_message(
                            f"❌ An error occurred: {str(e)}", ephemeral=True
                        )

            tag_user_button.callback = send_tag_user
            view.add_item(tag_user_button)

            # ── Tag Role & Send ──────────────────────────────────────────────
            tag_role_button = discord.ui.Button(
                label="Tag Role & Send",
                emoji="📣",
                style=discord.ButtonStyle.success,
                custom_id="send_tag_role"
            )

            async def send_tag_role(button_int: discord.Interaction):
                """Step 1: pick a role from the server, then type your message."""
                try:
                    # Get pingable roles (exclude @everyone and managed/bot roles), sorted
                    roles = sorted(
                        [r for r in guild.roles if not r.is_default() and not r.managed],
                        key=lambda r: -r.position
                    )[:25]

                    if not roles:
                        await button_int.response.send_message(
                            "❌ No pingable roles found in this server.",
                            ephemeral=True
                        )
                        return

                    role_options = [
                        discord.SelectOption(
                            label=r.name[:90],
                            value=str(r.id),
                            description=f"{len(r.members)} member(s)",
                            emoji="📣"
                        )
                        for r in roles
                    ]

                    role_select = discord.ui.Select(
                        placeholder="Select a role to ping...",
                        options=role_options,
                        custom_id="select_role_to_ping"
                    )

                    async def role_chosen(sel_int: discord.Interaction):
                        role_id = int(sel_int.data["values"][0])
                        target_role = guild.get_role(role_id)
                        if not target_role:
                            await sel_int.response.send_message("❌ Role not found.", ephemeral=True)
                            return

                        from discord.ui import Modal, TextInput

                        class TagRoleMessageModal(Modal, title=f"Message to @{target_role.name}"):
                            message_content = TextInput(
                                label="Message Content",
                                placeholder=f"Message to send after pinging {target_role.name}...",
                                required=True,
                                max_length=1900,
                                style=discord.TextStyle.paragraph
                            )

                            async def on_submit(self, modal_int: discord.Interaction):
                                try:
                                    await modal_int.response.defer(ephemeral=True)
                                    full_content = f"{target_role.mention} {self.message_content.value}"
                                    sent_message = await channel.send(
                                        content=full_content,
                                        allowed_mentions=discord.AllowedMentions(roles=True)
                                    )
                                    success_embed = discord.Embed(
                                        title="✅ Message Sent",
                                        description=(
                                            f"**Channel:** {channel.mention}\n"
                                            f"**Pinged Role:** {target_role.mention}\n"
                                            f"**Server:** {guild.name}\n"
                                            f"**Message ID:** `{sent_message.id}`"
                                        ),
                                        color=0x57F287
                                    )
                                    success_embed.add_field(
                                        name="🔗 Jump to Message",
                                        value=f"[Click here]({sent_message.jump_url})",
                                        inline=False
                                    )
                                    await modal_int.followup.send(embed=success_embed, ephemeral=True)
                                except discord.Forbidden:
                                    await modal_int.followup.send(
                                        "❌ I don't have permission to send messages / mention roles in this channel.",
                                        ephemeral=True
                                    )
                                except Exception as e:
                                    print(f"Tag role send error: {e}")
                                    await modal_int.followup.send(
                                        f"❌ An error occurred: {str(e)}", ephemeral=True
                                    )

                        modal = TagRoleMessageModal()
                        await sel_int.response.send_modal(modal)

                    role_select.callback = role_chosen

                    tag_role_view = discord.ui.View(timeout=120)
                    tag_role_view.add_item(role_select)

                    tag_role_embed = discord.Embed(
                        title="📣 Tag Role & Send",
                        description=(
                            f"**Sending to:** {channel.mention}\n\n"
                            "Select the role you want to ping in your message.\n"
                            "After selecting, you'll be able to type your message."
                        ),
                        color=0x57F287
                    )

                    await button_int.response.edit_message(embed=tag_role_embed, view=tag_role_view)

                except Exception as e:
                    print(f"Tag role flow error: {e}")
                    import traceback; traceback.print_exc()
                    if not button_int.response.is_done():
                        await button_int.response.send_message(
                            f"❌ An error occurred: {str(e)}", ephemeral=True
                        )

            tag_role_button.callback = send_tag_role
            view.add_item(tag_role_button)

            # Embed Message button
            embed_button = discord.ui.Button(
                label="Embed Message",
                emoji="🎨",
                style=discord.ButtonStyle.primary,
                custom_id="send_embed"
            )
            
            async def send_embed_message(button_int: discord.Interaction):
                from discord.ui import Modal, TextInput
                
                class EmbedMessageModal(Modal, title=f"Embed for #{channel.name}"):
                    embed_title = TextInput(
                        label="Embed Title",
                        placeholder="Enter embed title...",
                        required=True,
                        max_length=256
                    )
                    
                    embed_description = TextInput(
                        label="Embed Description",
                        placeholder="Enter embed description...",
                        required=True,
                        max_length=4000,
                        style=discord.TextStyle.paragraph
                    )
                    
                    embed_color = TextInput(
                        label="Embed Color (hex, e.g., 5865F2)",
                        placeholder="5865F2",
                        required=False,
                        max_length=6,
                        default="5865F2"
                    )
                    
                    footer_text = TextInput(
                        label="Footer Text (optional)",
                        placeholder="Enter footer text...",
                        required=False,
                        max_length=2048
                    )
                    
                    async def on_submit(self, modal_int: discord.Interaction):
                        try:
                            await modal_int.response.defer(ephemeral=True)
                            
                            # Parse color
                            try:
                                color_hex = self.embed_color.value.strip().lstrip('#')
                                color = int(color_hex, 16) if color_hex else 0x5865F2
                            except:
                                color = 0x5865F2
                            
                            # Create embed
                            message_embed = discord.Embed(
                                title=self.embed_title.value,
                                description=self.embed_description.value,
                                color=color
                            )
                            
                            if self.footer_text.value:
                                message_embed.set_footer(text=self.footer_text.value)
                            
                            # Send the embed
                            sent_message = await channel.send(embed=message_embed)
                            
                            success_embed = discord.Embed(
                                title="✅ Embed Sent",
                                description=(
                                    f"**Channel:** {channel.mention}\n"
                                    f"**Server:** {guild.name}\n"
                                    f"**Message ID:** `{sent_message.id}`\n\n"
                                    f"**Title:** {self.embed_title.value}"
                                ),
                                color=0x57F287
                            )
                            
                            success_embed.add_field(
                                name="🔗 Jump to Message",
                                value=f"[Click here]({sent_message.jump_url})",
                                inline=False
                            )
                            
                            await modal_int.followup.send(embed=success_embed, ephemeral=True)
                            
                        except discord.Forbidden:
                            await modal_int.followup.send(
                                "❌ I don't have permission to send messages in this channel.",
                                ephemeral=True
                            )
                        except Exception as e:
                            print(f"Send embed error: {e}")
                            await modal_int.followup.send(
                                f"❌ An error occurred: {str(e)}",
                                ephemeral=True
                            )
                
                modal = EmbedMessageModal()
                await button_int.response.send_modal(modal)
            
            embed_button.callback = send_embed_message
            view.add_item(embed_button)
            
            # Announcement button (with @everyone mention if bot has permission)
            announcement_button = discord.ui.Button(
                label="Announcement",
                emoji="📢",
                style=discord.ButtonStyle.danger,
                custom_id="send_announcement"
            )
            
            async def send_announcement(button_int: discord.Interaction):
                from discord.ui import Modal, TextInput
                
                class AnnouncementModal(Modal, title="Create Announcement"):
                    announcement_title = TextInput(
                        label="Announcement Title",
                        placeholder="IMPORTANT ANNOUNCEMENT",
                        required=True,
                        max_length=256
                    )
                    
                    announcement_content = TextInput(
                        label="Announcement Content",
                        placeholder="Enter your announcement message...",
                        required=True,
                        max_length=4000,
                        style=discord.TextStyle.paragraph
                    )
                    
                    mention_role = TextInput(
                        label="Mention (@everyone/@here/role name/empty)",
                        placeholder="@everyone",
                        required=False,
                        max_length=100,
                        default="@everyone"
                    )
                    
                    async def on_submit(self, modal_int: discord.Interaction):
                        try:
                            await modal_int.response.defer(ephemeral=True)
                            
                            # Create announcement embed
                            announcement_embed = discord.Embed(
                                title=f"📢 {self.announcement_title.value}",
                                description=self.announcement_content.value,
                                color=0xED4245
                            )
                            
                            announcement_embed.set_footer(
                                text=f"Sent by {modal_int.user.display_name}",
                                icon_url=modal_int.user.display_avatar.url
                            )
                            
                            # Prepare mention
                            mention_text = ""
                            if self.mention_role.value:
                                mention_value = self.mention_role.value.strip()
                                if mention_value.lower() == "@everyone":
                                    mention_text = "@everyone"
                                elif mention_value.lower() == "@here":
                                    mention_text = "@here"
                                else:
                                    # Try to find role
                                    role = discord.utils.get(guild.roles, name=mention_value)
                                    if role:
                                        mention_text = role.mention
                            
                            # Send the announcement
                            try:
                                if mention_text in ["@everyone", "@here"]:
                                    sent_message = await channel.send(
                                        content=mention_text,
                                        embed=announcement_embed,
                                        allowed_mentions=discord.AllowedMentions(everyone=True)
                                    )
                                else:
                                    content = mention_text if mention_text else None
                                    sent_message = await channel.send(
                                        content=content,
                                        embed=announcement_embed
                                    )
                                
                                success_embed = discord.Embed(
                                    title="✅ Announcement Posted",
                                    description=(
                                        f"**Channel:** {channel.mention}\n"
                                        f"**Server:** {guild.name}\n"
                                        f"**Mention:** {mention_text if mention_text else 'None'}\n"
                                        f"**Message ID:** `{sent_message.id}`"
                                    ),
                                    color=0x57F287
                                )
                                
                                success_embed.add_field(
                                    name="🔗 Jump to Announcement",
                                    value=f"[Click here]({sent_message.jump_url})",
                                    inline=False
                                )
                                
                                await modal_int.followup.send(embed=success_embed, ephemeral=True)
                                
                            except discord.Forbidden:
                                await modal_int.followup.send(
                                    "❌ I don't have permission to send announcements or mention everyone/here.",
                                    ephemeral=True
                                )
                            
                        except Exception as e:
                            print(f"Send announcement error: {e}")
                            await modal_int.followup.send(
                                f"❌ An error occurred: {str(e)}",
                                ephemeral=True
                            )
                
                modal = AnnouncementModal()
                await button_int.response.send_modal(modal)
            
            announcement_button.callback = send_announcement
            view.add_item(announcement_button)
            
            # Back button
            back_button = discord.ui.Button(
                label="◀ Back to Channel Selection",
                style=discord.ButtonStyle.secondary
            )
            back_button.callback = lambda i: self.send_message(i, guild)
            view.add_item(back_button)
            
            await interaction.response.edit_message(embed=embed, view=view)
            
        except Exception as e:
            print(f"Show message type error: {e}")
            import traceback
            traceback.print_exc()
            await interaction.response.send_message(
                "❌ An error occurred while loading message options.",
                ephemeral=True
            )


    async def start_alliance_monitor(self, interaction: discord.Interaction, guild: discord.Guild):
        """Start alliance monitoring for the selected server"""
        try:
            # Get the alliance cog
            alliance_cog = self.bot.get_cog('Alliance')
            
            if not alliance_cog:
                await interaction.response.send_message(
                    "❌ Alliance system is not loaded.",
                    ephemeral=True
                )
                return
            
            # Import ServerAllianceAdapter to get available alliances
            try:
                from db.mongo_adapters import ServerAllianceAdapter, mongo_enabled
            except:
                await interaction.response.send_message(
                    "❌ MongoDB not enabled. Alliance monitoring requires MongoDB.",
                    ephemeral=True
                )
                return
            
            # Get the current server's assigned alliance
            current_alliance = ServerAllianceAdapter.get_alliance(guild.id)
            
            # Get all available alliances from the alliance database
            available_alliances = []
            try:
                # Import the database connection function
                from db_utils import get_db_connection
                
                with get_db_connection('alliance.sqlite') as alliance_db:
                    cursor = alliance_db.cursor()
                    cursor.execute("SELECT alliance_id, name FROM alliance_list ORDER BY name")
                    available_alliances = cursor.fetchall()
            except Exception as db_error:
                print(f"Error fetching alliances: {db_error}")
            
            if not available_alliances:
                await interaction.response.send_message(
                    "❌ No alliances found in the database.\n\nPlease ensure alliances have been synced first.",
                    ephemeral=True
                )
                return
            
            # Create alliance selection dropdown
            alliance_options = [
                discord.SelectOption(
                    label=f"{name[:90]}",
                    value=str(alliance_id),
                    description=f"ID: {alliance_id}" + (" (Currently assigned)" if str(alliance_id) == str(current_alliance) else ""),
                    emoji="🏰"
                )
                for alliance_id, name in available_alliances[:25]
            ]
            
            alliance_select = discord.ui.Select(
                placeholder="Select an alliance to monitor...",
                options=alliance_options,
                custom_id="select_alliance_to_monitor"
            )
            
            async def alliance_selected(select_interaction: discord.Interaction):
                selected_alliance_id = select_interaction.data["values"][0]
                
                # Get alliance name
                alliance_name = "Unknown Alliance"
                try:
                    from db_utils import get_db_connection
                    with get_db_connection('alliance.sqlite') as alliance_db:
                        cursor = alliance_db.cursor()
                        cursor.execute("SELECT name FROM alliance_list WHERE alliance_id = ?", (selected_alliance_id,))
                        result = cursor.fetchone()
                        if result:
                            alliance_name = result[0]
                except:
                    pass
                
                # Now show channel selection for this alliance
                text_channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages]
                
                if not text_channels:
                    await select_interaction.response.send_message(
                        "❌ No accessible text channels found in this server.",
                        ephemeral=True
                    )
                    return
                
                # Create channel selection dropdown
                channel_options = [
                    discord.SelectOption(
                        label=f"{channel.name[:90]}",
                        value=str(channel.id),
                        description=f"Category: {channel.category.name if channel.category else 'None'}",
                        emoji="📝"
                    )
                    for channel in sorted(text_channels, key=lambda c: c.position)[:25]
                ]
                
                channel_select = discord.ui.Select(
                    placeholder="Select channel for monitoring updates...",
                    options=channel_options,
                    custom_id="select_monitor_channel"
                )
                
                async def channel_selected(channel_interaction: discord.Interaction):
                    channel_id = int(channel_interaction.data["values"][0])
                    monitor_channel = guild.get_channel(channel_id)
                    
                    if not monitor_channel:
                        await channel_interaction.response.send_message(
                            "❌ Channel not found.",
                            ephemeral=True
                        )
                        return
                    
                    # Save monitoring configuration
                    try:
                        await channel_interaction.response.defer(ephemeral=True)
                        
                        # Import database utilities
                        from db_utils import get_db_connection
                        
                        # Get member count for this alliance
                        members = []
                        if hasattr(alliance_cog, '_get_monitoring_members'):
                            members = await alliance_cog._get_monitoring_members(selected_alliance_id, guild_id=guild.id)
                        member_count = len(members) if members else 0
                        
                        # Save to database
                        with get_db_connection('settings.sqlite') as conn:
                            cursor = conn.cursor()
                            cursor.execute("""
                                INSERT OR REPLACE INTO alliance_monitoring 
                                (guild_id, alliance_id, channel_id, enabled, updated_at)
                                VALUES (?, ?, ?, 1, CURRENT_TIMESTAMP)
                            """, (guild.id, selected_alliance_id, channel_id))
                            conn.commit()
                        
                        # Save to MongoDB if enabled
                        if mongo_enabled():
                            try:
                                from db.mongo_adapters import AllianceMonitoringAdapter
                                AllianceMonitoringAdapter.upsert_monitor(guild.id, selected_alliance_id, channel_id, enabled=1)
                            except:
                                pass
                        
                        # Initialize member history if available
                        if members:
                            with get_db_connection('settings.sqlite') as conn:
                                cursor = conn.cursor()
                                for fid, nickname, furnace_lv, *_ in members:
                                    cursor.execute("""
                                        INSERT OR REPLACE INTO member_history 
                                        (fid, alliance_id, nickname, furnace_lv, last_checked)
                                        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                                    """, (str(fid), selected_alliance_id, nickname, furnace_lv))
                                conn.commit()
                        
                        # Create success embed
                        success_embed = discord.Embed(
                            title="✅ Alliance Monitoring Started",
                            description=(
                                f"**Alliance:** {alliance_name}\n"
                                f"**Alliance ID:** `{selected_alliance_id}`\n"
                                f"**Channel:** {monitor_channel.mention}\n"
                                f"**Server:** {guild.name}\n"
                                f"**Members Tracked:** {member_count}\n\n"
                                f"**Monitoring Active** ✅\n"
                                f"The system will check for changes every 4 minutes.\n\n"
                                f"**Tracked Changes:**\n"
                                f"• 👤 Name changes\n"
                                f"• 🔥 Furnace level changes\n"
                                f"• 🖼️ Avatar changes"
                            ),
                            color=0x57F287
                        )
                        
                        await channel_interaction.followup.send(embed=success_embed, ephemeral=True)
                        
                        # Send notification to the monitoring channel
                        try:
                            channel_embed = discord.Embed(
                                title="🛡️ Alliance Monitoring Started",
                                description=(
                                    f"Now monitoring **{alliance_name}** (ID: `{selected_alliance_id}`)\n\n"
                                    f"**Check Frequency:** Every 4 minutes\n"
                                    f"**Started by:** {channel_interaction.user.mention}\n\n"
                                    f"Updates will be posted here automatically."
                                ),
                                color=0x5865F2
                            )
                            await monitor_channel.send(embed=channel_embed)
                        except:
                            pass  # Not critical if this fails
                        
                        print(f"Remote alliance monitoring configured: Alliance {selected_alliance_id} in channel {channel_id} for guild {guild.id}")
                        
                    except Exception as save_error:
                        print(f"Error saving monitoring config: {save_error}")
                        import traceback
                        traceback.print_exc()
                        await channel_interaction.followup.send(
                            f"❌ Error saving monitoring configuration: {str(save_error)}",
                            ephemeral=True
                        )
                
                channel_select.callback = channel_selected
                
                channel_view = discord.ui.View(timeout=300)
                channel_view.add_item(channel_select)
                
                # Back button
                back_button = discord.ui.Button(
                    label="◀ Back",
                    emoji="🏰",
                    style=discord.ButtonStyle.secondary
                )
                back_button.callback = lambda i: self.show_server_management(i, guild)
                channel_view.add_item(back_button)
                
                channel_embed = discord.Embed(
                    title=f"🛡️ Monitor: {alliance_name}",
                    description=(
                        f"**Selected Alliance:** {alliance_name}\n"
                        f"**Alliance ID:** `{selected_alliance_id}`\n\n"
                        "Now select the channel where monitoring updates should be posted.\n\n"
                        "**What will be monitored:**\n"
                        "• 👤 Player name changes\n"
                        "• 🔥 Furnace level changes\n"
                        "• 🖼️ Avatar changes\n\n"
                        "Select a channel from the dropdown below."
                    ),
                    color=0x5865F2
                )
                
                await select_interaction.response.edit_message(embed=channel_embed, view=channel_view)
            
            alliance_select.callback = alliance_selected
            
            view = discord.ui.View(timeout=300)
            view.add_item(alliance_select)
            
            # Back button
            back_button = discord.ui.Button(
                label="◀ Back",
                emoji="🏰",
                style=discord.ButtonStyle.secondary
            )
            back_button.callback = lambda i: self.show_server_management(i, guild)
            view.add_item(back_button)
            
            embed = discord.Embed(
                title=f"🛡️ Alliance Monitor for {guild.name}",
                description=(
                    "**Remote Alliance Monitoring Setup**\n\n"
                    "**Step 1:** Select the alliance you want to monitor\n"
                    "**Step 2:** Choose the channel for updates (next screen)\n\n"
                    "**Features:**\n"
                    "• Monitor alliance member changes\n"
                    "• Track name, furnace, and avatar changes\n"
                    "• Automatic updates every 4 minutes\n"
                    "• Real-time notifications\n\n"
                    "Select an alliance from the dropdown below to continue."
                ),
                color=0x5865F2
            )
            
            if current_alliance:
                embed.add_field(
                    name="📌 Current Server Alliance",
                    value=f"This server is currently assigned to alliance `{current_alliance}`",
                    inline=False
                )
            
            await interaction.response.edit_message(embed=embed, view=view)
            
        except Exception as e:
            print(f"Start alliance monitor error: {e}")
            import traceback
            traceback.print_exc()
            await interaction.response.send_message(
                "❌ An error occurred while setting up alliance monitoring.",
                ephemeral=True
            )

    async def stop_alliance_monitor(self, interaction: discord.Interaction, guild: discord.Guild):
        """Stop alliance monitoring for the selected server"""
        try:
            # Get the alliance cog
            alliance_cog = self.bot.get_cog('Alliance')
            
            if not alliance_cog:
                await interaction.response.send_message(
                    "❌ Alliance system is not loaded.",
                    ephemeral=True
                )
                return
            
            # Import database utilities
            try:
                from db_utils import get_db_connection
                from db.mongo_adapters import mongo_enabled, AllianceMonitoringAdapter
            except:
                await interaction.response.send_message(
                    "❌ Database utilities not available.",
                    ephemeral=True
                )
                return
            
            # Get active monitors for this guild
            active_monitors = []
            try:
                with get_db_connection('settings.sqlite') as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT alliance_id, channel_id, enabled
                        FROM alliance_monitoring
                        WHERE guild_id = ? AND enabled = 1
                    """, (guild.id,))
                    active_monitors = cursor.fetchall()
            except Exception as db_error:
                print(f"Error fetching active monitors: {db_error}")
            
            if not active_monitors:
                await interaction.response.send_message(
                    "❌ No active alliance monitors found for this server.\n\nUse the **Alliance Monitor** button to start monitoring an alliance.",
                    ephemeral=True
                )
                return
            
            # Get alliance names for the monitors
            monitor_options = []
            for alliance_id, channel_id, enabled in active_monitors:
                alliance_name = "Unknown Alliance"
                channel_name = "Unknown Channel"
                
                # Get alliance name
                try:
                    with get_db_connection('alliance.sqlite') as alliance_db:
                        cursor = alliance_db.cursor()
                        cursor.execute("SELECT name FROM alliance_list WHERE alliance_id = ?", (alliance_id,))
                        result = cursor.fetchone()
                        if result:
                            alliance_name = result[0]
                except:
                    pass
                
                # Get channel name
                try:
                    channel = guild.get_channel(channel_id)
                    if channel:
                        channel_name = channel.name
                except:
                    pass
                
                monitor_options.append(
                    discord.SelectOption(
                        label=f"{alliance_name[:50]}",
                        value=f"{alliance_id}_{channel_id}",
                        description=f"Channel: #{channel_name[:40]} • ID: {alliance_id}",
                        emoji="🛡️"
                    )
                )
            
            # Create dropdown for monitor selection
            if not monitor_options:
                await interaction.response.send_message(
                    "❌ No valid monitors found to stop.",
                    ephemeral=True
                )
                return
            
            monitor_select = discord.ui.Select(
                placeholder="Select a monitor to stop...",
                options=monitor_options[:25],  # Discord limit
                custom_id="select_monitor_to_stop"
            )
            
            async def monitor_selected(select_interaction: discord.Interaction):
                selected_value = select_interaction.data["values"][0]
                alliance_id, channel_id = selected_value.split("_")
                alliance_id = int(alliance_id)
                channel_id = int(channel_id)
                
                # Get details for confirmation
                alliance_name = "Unknown Alliance"
                try:
                    with get_db_connection('alliance.sqlite') as alliance_db:
                        cursor = alliance_db.cursor()
                        cursor.execute("SELECT name FROM alliance_list WHERE alliance_id = ?", (alliance_id,))
                        result = cursor.fetchone()
                        if result:
                            alliance_name = result[0]
                except:
                    pass
                
                channel = guild.get_channel(channel_id)
                channel_mention = channel.mention if channel else f"<#{channel_id}>"
                
                # Create confirmation view
                confirm_view = discord.ui.View(timeout=60)
                
                async def confirm_stop(button_int: discord.Interaction):
                    try:
                        await button_int.response.defer(ephemeral=True)
                        
                        # Stop monitoring by setting enabled = 0
                        with get_db_connection('settings.sqlite') as conn:
                            cursor = conn.cursor()
                            cursor.execute("""
                                UPDATE alliance_monitoring 
                                SET enabled = 0, updated_at = CURRENT_TIMESTAMP
                                WHERE guild_id = ? AND alliance_id = ? AND channel_id = ?
                            """, (guild.id, alliance_id, channel_id))
                            conn.commit()
                        
                        # Update MongoDB if enabled
                        if mongo_enabled():
                            try:
                                AllianceMonitoringAdapter.upsert_monitor(guild.id, alliance_id, channel_id, enabled=0)
                            except:
                                pass
                        
                        # Create success embed
                        success_embed = discord.Embed(
                            title="✅ Alliance Monitoring Stopped",
                            description=(
                                f"**Alliance:** {alliance_name}\n"
                                f"**Alliance ID:** `{alliance_id}`\n"
                                f"**Channel:** {channel_mention}\n"
                                f"**Server:** {guild.name}\n\n"
                                f"**Status:** ❌ Monitoring Disabled\n\n"
                                f"You can restart monitoring at any time using the **Alliance Monitor** button."
                            ),
                            color=0xED4245  # Red color
                        )
                        
                        await button_int.followup.send(embed=success_embed, ephemeral=True)
                        
                        # Send notification to the channel
                        if channel:
                            try:
                                channel_embed = discord.Embed(
                                    title="🛑 Alliance Monitoring Stopped",
                                    description=(
                                        f"Monitoring for **{alliance_name}** (ID: `{alliance_id}`) has been stopped.\n\n"
                                        f"**Stopped by:** {button_int.user.mention}\n\n"
                                        f"To restart monitoring, use the `/settings` command or Remote Access."
                                    ),
                                    color=0xED4245
                                )
                                await channel.send(embed=channel_embed)
                            except:
                                pass  # Not critical if this fails
                        
                        print(f"Remote alliance monitoring stopped: Alliance {alliance_id} in channel {channel_id} for guild {guild.id}")
                        
                    except Exception as stop_error:
                        print(f"Error stopping monitor: {stop_error}")
                        import traceback
                        traceback.print_exc()
                        await button_int.followup.send(
                            f"❌ Error stopping monitor: {str(stop_error)}",
                            ephemeral=True
                        )
                
                async def cancel_stop(button_int: discord.Interaction):
                    cancel_embed = discord.Embed(
                        title="❌ Action Cancelled",
                        description="Alliance monitoring has not been stopped.",
                        color=0x95A5A6
                    )
                    await button_int.response.edit_message(embed=cancel_embed, view=None)
                
                # Add buttons to confirmation view
                confirm_button = discord.ui.Button(
                    label="Stop Monitoring",
                    emoji="🛑",
                    style=discord.ButtonStyle.danger
                )
                confirm_button.callback = confirm_stop
                
                cancel_button = discord.ui.Button(
                    label="Cancel",
                    emoji="❌",
                    style=discord.ButtonStyle.secondary
                )
                cancel_button.callback = cancel_stop
                
                confirm_view.add_item(confirm_button)
                confirm_view.add_item(cancel_button)
                
                # Create confirmation embed
                confirm_embed = discord.Embed(
                    title="⚠️ Confirm Stop Monitoring",
                    description=(
                        f"Are you sure you want to stop monitoring this alliance?\n\n"
                        f"**Alliance:** {alliance_name}\n"
                        f"**Alliance ID:** `{alliance_id}`\n"
                        f"**Channel:** {channel_mention}\n"
                        f"**Server:** {guild.name}\n\n"
                        f"**Note:** You can restart monitoring anytime."
                    ),
                    color=0xF39C12  # Orange warning color
                )
                
                await select_interaction.response.edit_message(embed=confirm_embed, view=confirm_view)
            
            monitor_select.callback = monitor_selected
            
            view = discord.ui.View(timeout=300)
            view.add_item(monitor_select)
            
            # Back button
            back_button = discord.ui.Button(
                label="◀ Back",
                emoji="🏰",
                style=discord.ButtonStyle.secondary
            )
            back_button.callback = lambda i: self.show_server_management(i, guild)
            view.add_item(back_button)
            
            embed = discord.Embed(
                title=f"🛑 Stop Alliance Monitor for {guild.name}",
                description=(
                    f"**Active Monitors:** {len(active_monitors)}\n\n"
                    "Select an alliance monitor to stop from the dropdown below.\n\n"
                    "**What happens when you stop:**\n"
                    "• Monitoring will be disabled\n"
                    "• No more change notifications will be sent\n"
                    "• You can restart monitoring anytime\n\n"
                    "Select a monitor to stop it."
                ),
                color=0xED4245
            )
            
            await interaction.response.edit_message(embed=embed, view=view)
            
        except Exception as e:
            print(f"Stop alliance monitor error: {e}")
            import traceback
            traceback.print_exc()
            await interaction.response.send_message(
                "❌ An error occurred while loading the stop monitor menu.",
                ephemeral=True
            )

    async def kick_user(self, interaction: discord.Interaction, guild: discord.Guild):
        """Remotely kick a member from the selected server"""
        try:
            bot_member = guild.get_member(self.bot.user.id)
            if not bot_member or not bot_member.guild_permissions.kick_members:
                await interaction.response.send_message(
                    "❌ I don't have **Kick Members** permission in this server.",
                    ephemeral=True
                )
                return

            # Collect kickable members (not bots, not above bot in hierarchy)
            members = [
                m for m in guild.members
                if not m.bot
                and m.id != self.bot.user.id
                and m.top_role < bot_member.top_role  # can't kick equal/higher roles
            ]

            if not members:
                await interaction.response.send_message(
                    "❌ No kickable members found in this server.\n"
                    "(The bot can only kick members whose highest role is below the bot's highest role.)",
                    ephemeral=True
                )
                return

            # Sort alphabetically, limit to 25 for the dropdown
            members_sorted = sorted(members, key=lambda m: m.display_name.lower())

            options = [
                discord.SelectOption(
                    label=f"{m.display_name[:80]}",
                    value=str(m.id),
                    description=f"@{str(m)[:40]} • Joined: {m.joined_at.strftime('%Y-%m-%d') if m.joined_at else 'Unknown'}",
                    emoji="👤"
                )
                for m in members_sorted[:25]
            ]

            select = discord.ui.Select(
                placeholder="Select a member to kick...",
                options=options,
                custom_id="select_member_to_kick"
            )

            async def member_selected(select_interaction: discord.Interaction):
                member_id = int(select_interaction.data["values"][0])
                target = guild.get_member(member_id)

                if not target:
                    await select_interaction.response.send_message(
                        "❌ Member not found. They may have already left the server.",
                        ephemeral=True
                    )
                    return

                # Show reason modal
                from discord.ui import Modal, TextInput

                class KickReasonModal(Modal, title=f"Kick {target.display_name}"):
                    reason = TextInput(
                        label="Kick Reason (optional)",
                        placeholder="Enter a reason for kicking this member...",
                        required=False,
                        max_length=512,
                        style=discord.TextStyle.paragraph
                    )

                    def __init__(self, parent_cog, member, g):
                        super().__init__()
                        self.parent_cog = parent_cog
                        self.member = member
                        self.guild = g

                    async def on_submit(self, modal_int: discord.Interaction):
                        try:
                            kick_reason = self.reason.value.strip() if self.reason.value else "No reason provided"

                            # Build confirmation embed
                            confirm_embed = discord.Embed(
                                title="⚠️ Confirm Member Kick",
                                description=(
                                    f"**Member:** {self.member.mention} (`{self.member}`)\_\n"
                                    f"**User ID:** `{self.member.id}`\n"
                                    f"**Server:** {self.guild.name}\n"
                                    f"**Top Role:** {self.member.top_role.name}\n"
                                    f"**Joined:** {f'<t:{int(self.member.joined_at.timestamp())}:R>' if self.member.joined_at else 'Unknown'}\n"
                                    f"**Account Created:** <t:{int(self.member.created_at.timestamp())}:R>\n\n"
                                    f"**Reason:** {kick_reason}\n\n"
                                    f"⚠️ **This will remove the member from the server.**\n"
                                    f"They can rejoin with a new invite unless banned."
                                ),
                                color=0xF39C12
                            )
                            confirm_embed.set_thumbnail(url=self.member.display_avatar.url)

                            confirm_view = discord.ui.View(timeout=60)

                            async def confirm_kick(confirm_int: discord.Interaction):
                                try:
                                    member_name = str(self.member)
                                    member_display = self.member.display_name
                                    await self.member.kick(reason=f"[Remote Access] {kick_reason} | By: {confirm_int.user}")

                                    success_embed = discord.Embed(
                                        title="✅ Member Kicked",
                                        description=(
                                            f"**Member:** `{member_name}` ({member_display})\_\n"
                                            f"**Server:** {self.guild.name}\n"
                                            f"**Reason:** {kick_reason}\n"
                                            f"**Kicked by:** {confirm_int.user.mention}\n\n"
                                            f"The member has been removed from the server."
                                        ),
                                        color=0x57F287
                                    )
                                    await confirm_int.response.edit_message(embed=success_embed, view=None)
                                    print(f"[REMOTE ACCESS] Kicked {member_name} from {self.guild.name} — Reason: {kick_reason} | By: {confirm_int.user}")

                                except discord.Forbidden:
                                    await confirm_int.response.send_message(
                                        "❌ I don't have permission to kick this member.\n"
                                        "Their role may be equal to or higher than mine.",
                                        ephemeral=True
                                    )
                                except discord.HTTPException as http_err:
                                    await confirm_int.response.send_message(
                                        f"❌ Discord API error: {str(http_err)}",
                                        ephemeral=True
                                    )
                                except Exception as kick_err:
                                    print(f"Kick error: {kick_err}")
                                    await confirm_int.response.send_message(
                                        f"❌ An error occurred: {str(kick_err)}",
                                        ephemeral=True
                                    )

                            async def cancel_kick(cancel_int: discord.Interaction):
                                cancel_embed = discord.Embed(
                                    title="❌ Kick Cancelled",
                                    description="The member was **not** kicked.",
                                    color=0x5865F2
                                )
                                await cancel_int.response.edit_message(embed=cancel_embed, view=None)

                            confirm_btn = discord.ui.Button(
                                label="Confirm Kick",
                                style=discord.ButtonStyle.danger,
                                emoji="👢"
                            )
                            confirm_btn.callback = confirm_kick

                            cancel_btn = discord.ui.Button(
                                label="Cancel",
                                style=discord.ButtonStyle.secondary,
                                emoji="❌"
                            )
                            cancel_btn.callback = cancel_kick

                            confirm_view.add_item(confirm_btn)
                            confirm_view.add_item(cancel_btn)

                            await modal_int.response.send_message(
                                embed=confirm_embed,
                                view=confirm_view,
                                ephemeral=True
                            )

                        except Exception as e:
                            print(f"KickReasonModal.on_submit error: {e}")
                            import traceback
                            traceback.print_exc()
                            if not modal_int.response.is_done():
                                await modal_int.response.send_message(
                                    f"❌ An error occurred: {str(e)}",
                                    ephemeral=True
                                )

                modal = KickReasonModal(self, target, guild)
                await select_interaction.response.send_modal(modal)

            select.callback = member_selected

            view = discord.ui.View(timeout=300)
            view.add_item(select)

            # Back button
            back_button = discord.ui.Button(
                label="◀ Back",
                emoji="🏰",
                style=discord.ButtonStyle.secondary
            )
            back_button.callback = lambda i: self.show_server_management(i, guild)
            view.add_item(back_button)

            total = len(members)
            shown = min(total, 25)
            embed = discord.Embed(
                title=f"👢 Kick Member from {guild.name}",
                description=(
                    f"**Server Members:** `{guild.member_count}`\n"
                    f"**Kickable Members:** `{total}` (showing first `{shown}`)\n\n"
                    "**Kick Flow:**\n"
                    "1️⃣ Select a member from the dropdown\n"
                    "2️⃣ Enter an optional kick reason\n"
                    "3️⃣ Confirm to execute the kick\n\n"
                    "⚠️ Only members whose highest role is **below** the bot's role are shown.\n"
                    "The kicked member can rejoin with a new invite unless banned."
                ),
                color=0xF39C12
            )
            if guild.icon:
                embed.set_thumbnail(url=guild.icon.url)

            await interaction.response.edit_message(embed=embed, view=view)

        except Exception as e:
            print(f"Kick user error: {e}")
            import traceback
            traceback.print_exc()
            await interaction.response.send_message(
                "❌ An error occurred while loading the kick menu.",
                ephemeral=True
            )

    async def delete_message_by_id(self, interaction: discord.Interaction, guild: discord.Guild):
        """Delete a specific message from a specific channel in the server by message ID"""
        try:
            # Get all text channels where bot has manage_messages permission
            channels = [
                c for c in guild.text_channels
                if c.permissions_for(guild.me).manage_messages or c.permissions_for(guild.me).administrator
            ]

            if not channels:
                await interaction.response.send_message(
                    "❌ I don't have **Manage Messages** permission in any text channel of this server.",
                    ephemeral=True
                )
                return

            # Step 1 – channel dropdown
            options = [
                discord.SelectOption(
                    label=f"{channel.name[:90]}",
                    value=str(channel.id),
                    description=f"Category: {channel.category.name if channel.category else 'None'} • {channel.topic[:40] if channel.topic else 'No topic'}",
                    emoji="📝"
                )
                for channel in sorted(channels, key=lambda c: c.position)[:25]
            ]

            select = discord.ui.Select(
                placeholder="Select the channel that contains the message...",
                options=options,
                custom_id="select_channel_for_delete_msg"
            )

            async def channel_selected(select_interaction: discord.Interaction):
                channel_id = int(select_interaction.data["values"][0])
                target_channel = guild.get_channel(channel_id)

                if not target_channel:
                    await select_interaction.response.send_message(
                        "❌ Channel not found.",
                        ephemeral=True
                    )
                    return

                # Step 2 – modal to enter the message ID
                from discord.ui import Modal, TextInput

                class DeleteMessageModal(Modal, title=f"Delete Message in #{target_channel.name}"):
                    message_id = TextInput(
                        label="Message ID",
                        placeholder="Enter the exact message ID to delete...",
                        required=True,
                        min_length=17,
                        max_length=20,
                        style=discord.TextStyle.short
                    )

                    def __init__(self, parent_cog, ch, g):
                        super().__init__()
                        self.parent_cog = parent_cog
                        self.channel = ch
                        self.guild = g

                    async def on_submit(self, modal_int: discord.Interaction):
                        try:
                            raw_id = self.message_id.value.strip()

                            # Validate that it is a number
                            if not raw_id.isdigit():
                                await modal_int.response.send_message(
                                    "❌ Invalid message ID. Please enter a numeric Discord message ID.",
                                    ephemeral=True
                                )
                                return

                            msg_id = int(raw_id)

                            # Try to fetch the message so we can show a preview
                            try:
                                target_msg = await self.channel.fetch_message(msg_id)
                            except discord.NotFound:
                                await modal_int.response.send_message(
                                    f"❌ Message `{msg_id}` was not found in {self.channel.mention}.\n"
                                    "Make sure the ID is correct and belongs to the selected channel.",
                                    ephemeral=True
                                )
                                return
                            except discord.Forbidden:
                                await modal_int.response.send_message(
                                    "❌ I don't have permission to read messages in that channel.",
                                    ephemeral=True
                                )
                                return

                            # Build content preview
                            content_preview = ""
                            if target_msg.content:
                                content_preview = target_msg.content[:300]
                                if len(target_msg.content) > 300:
                                    content_preview += "…"
                            elif target_msg.embeds:
                                embed_title = target_msg.embeds[0].title or "(embed)"
                                content_preview = f"*Embed: {embed_title}*"
                            else:
                                content_preview = "*(no text content)*"

                            # Step 3 – confirmation
                            confirm_embed = discord.Embed(
                                title="⚠️ Confirm Message Deletion",
                                description=(
                                    f"**Channel:** {self.channel.mention}\n"
                                    f"**Server:** {self.guild.name}\n"
                                    f"**Message ID:** `{msg_id}`\n"
                                    f"**Author:** {target_msg.author.mention} (`{target_msg.author}`)\n"
                                    f"**Sent:** <t:{int(target_msg.created_at.timestamp())}:R>\n\n"
                                    f"**Content Preview:**\n>>> {content_preview}\n\n"
                                    f"⚠️ **This action cannot be undone!**"
                                ),
                                color=0xED4245
                            )
                            if target_msg.author.display_avatar:
                                confirm_embed.set_thumbnail(url=target_msg.author.display_avatar.url)

                            confirm_view = discord.ui.View(timeout=60)

                            async def confirm_delete(confirm_int: discord.Interaction):
                                try:
                                    await target_msg.delete()
                                    success_embed = discord.Embed(
                                        title="✅ Message Deleted",
                                        description=(
                                            f"**Channel:** {self.channel.mention}\n"
                                            f"**Server:** {self.guild.name}\n"
                                            f"**Message ID:** `{msg_id}`\n"
                                            f"**Original Author:** `{target_msg.author}`\n\n"
                                            f"The message has been permanently deleted."
                                        ),
                                        color=0x57F287
                                    )
                                    await confirm_int.response.edit_message(embed=success_embed, view=None)
                                    print(f"[REMOTE ACCESS] Deleted message {msg_id} in #{self.channel.name} ({self.guild.name}) by {confirm_int.user}")

                                except discord.Forbidden:
                                    await confirm_int.response.send_message(
                                        "❌ I don't have permission to delete that message.",
                                        ephemeral=True
                                    )
                                except discord.NotFound:
                                    await confirm_int.response.send_message(
                                        "❌ The message was already deleted or could not be found.",
                                        ephemeral=True
                                    )
                                except Exception as del_err:
                                    print(f"Delete message error: {del_err}")
                                    await confirm_int.response.send_message(
                                        f"❌ An error occurred while deleting: {str(del_err)}",
                                        ephemeral=True
                                    )

                            async def cancel_delete(cancel_int: discord.Interaction):
                                cancel_embed = discord.Embed(
                                    title="❌ Deletion Cancelled",
                                    description="Message deletion has been cancelled. The message was not deleted.",
                                    color=0x5865F2
                                )
                                await cancel_int.response.edit_message(embed=cancel_embed, view=None)

                            confirm_btn = discord.ui.Button(
                                label="Confirm Delete",
                                style=discord.ButtonStyle.danger,
                                emoji="🗑️"
                            )
                            confirm_btn.callback = confirm_delete

                            cancel_btn = discord.ui.Button(
                                label="Cancel",
                                style=discord.ButtonStyle.secondary,
                                emoji="❌"
                            )
                            cancel_btn.callback = cancel_delete

                            confirm_view.add_item(confirm_btn)
                            confirm_view.add_item(cancel_btn)

                            await modal_int.response.send_message(
                                embed=confirm_embed,
                                view=confirm_view,
                                ephemeral=True
                            )

                        except Exception as e:
                            print(f"DeleteMessageModal.on_submit error: {e}")
                            import traceback
                            traceback.print_exc()
                            if not modal_int.response.is_done():
                                await modal_int.response.send_message(
                                    f"❌ An error occurred: {str(e)}",
                                    ephemeral=True
                                )

                modal = DeleteMessageModal(self, target_channel, guild)
                await select_interaction.response.send_modal(modal)

            select.callback = channel_selected

            view = discord.ui.View(timeout=300)
            view.add_item(select)

            # Back button
            back_button = discord.ui.Button(
                label="◀ Back",
                emoji="🏰",
                style=discord.ButtonStyle.secondary
            )
            back_button.callback = lambda i: self.show_server_management(i, guild)
            view.add_item(back_button)

            embed = discord.Embed(
                title=f"🗑️ Delete Message in {guild.name}",
                description=(
                    "**Delete a Specific Message by ID**\n\n"
                    "**How to get a Message ID:**\n"
                    "1. Enable **Developer Mode** in Discord settings\n"
                    "2. Right-click any message → **Copy Message ID**\n\n"
                    "**Steps:**\n"
                    "1️⃣ Select the channel that contains the message\n"
                    "2️⃣ Enter the Message ID\n"
                    "3️⃣ Confirm the deletion\n\n"
                    "⚠️ **This action is permanent and cannot be undone.**"
                ),
                color=0xED4245
            )

            await interaction.response.edit_message(embed=embed, view=view)

        except Exception as e:
            print(f"Delete message by ID error: {e}")
            import traceback
            traceback.print_exc()
            await interaction.response.send_message(
                "❌ An error occurred while setting up message deletion.",
                ephemeral=True
            )

    async def manage_permissions(self, interaction: discord.Interaction, guild: discord.Guild):
        """Manage permissions for channels"""
        await interaction.response.send_message(
            "🔒 **Manage Permissions**\n\nThis feature is coming soon! It will allow you to:\n"
            "• View channel permissions\n"
            "• Modify role permissions\n"
            "• Set user-specific overrides\n"
            "• Copy permissions between channels",
            ephemeral=True
        )

    async def show_paginated_servers(self, interaction: discord.Interaction, guilds: list):
        """Show paginated server list for servers > 25"""
        view = RemoteServerSelectionView(self.bot, guilds, self)
        embed = view.create_embed()
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.response.edit_message(embed=embed, view=view)


class RemoteServerSelect(discord.ui.Select):
    """Dropdown for selecting a server in Remote Access."""
    
    def __init__(self, guilds: list, page: int = 0):
        self.guilds = guilds
        self.page = page
        self.total_pages = max(1, (len(guilds) - 1) // 25 + 1)
        
        start_idx = page * 25
        end_idx = min(start_idx + 25, len(guilds))
        current_page_guilds = guilds[start_idx:end_idx]
        
        options = [
            discord.SelectOption(
                label=f"{guild.name[:90]}",
                value=str(guild.id),
                description=f"Members: {guild.member_count} ΓÇó Channels: {len(guild.channels)}",
                emoji="🏰"
            )
            for guild in current_page_guilds
        ]
        
        super().__init__(
            placeholder=f"ΓÅ│ Select a server... (Page {page + 1}/{self.total_pages})",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"remote_server_select_pg{page}"
        )

    async def callback(self, interaction: discord.Interaction):
        guild_id = int(self.values[0])
        guild = interaction.client.get_guild(guild_id)
        
        if not guild:
            await interaction.response.send_message("Γplain Server not found.", ephemeral=True)
            return
            
        # Call the parent cog's method to show server management
        cog = interaction.client.get_cog("RemoteAccess")
        if cog:
            await cog.show_server_management(interaction, guild)


class RemoteServerSelectionView(discord.ui.View):
    """View for server selection with pagination support in Remote Access."""
    
    def __init__(self, bot, guilds: list, cog, page: int = 0):
        super().__init__(timeout=300)
        self.bot = bot
        self.guilds = sorted(guilds, key=lambda g: g.name)
        self.cog = cog
        self.page = page
        self.total_pages = max(1, (len(guilds) - 1) // 25 + 1)
        
        # Add server select dropdown
        self.add_item(RemoteServerSelect(self.guilds, page=page))
        
        # Add pagination buttons if needed
        if self.total_pages > 1:
            # Previous button
            prev_button = discord.ui.Button(
                emoji="⬅️",
                style=discord.ButtonStyle.secondary,
                disabled=(page == 0),
                row=1
            )
            prev_button.callback = self.previous_page
            self.add_item(prev_button)
            
            # Page indicator
            page_button = discord.ui.Button(
                label=f"Page {page + 1}/{self.total_pages}",
                style=discord.ButtonStyle.secondary,
                disabled=True,
                row=1
            )
            self.add_item(page_button)
            
            # Next button
            next_button = discord.ui.Button(
                emoji="➡️",
                style=discord.ButtonStyle.secondary,
                disabled=(page == self.total_pages - 1),
                row=1
            )
            next_button.callback = self.next_page
            self.add_item(next_button)

        # Search button
        search_button = discord.ui.Button(
            label="Search",
            emoji="🔍",
            style=discord.ButtonStyle.primary,
            row=2
        )
        search_button.callback = self.search_callback
        self.add_item(search_button)

        # View All List button
        view_all_button = discord.ui.Button(
            label="View All List",
            emoji="📋",
            style=discord.ButtonStyle.secondary,
            row=2
        )
        view_all_button.callback = self.view_all_callback
        self.add_item(view_all_button)
    
    async def search_callback(self, interaction: discord.Interaction):
        """Open a search modal."""
        from discord.ui import Modal, TextInput
        
        class SearchModal(Modal, title="Search Server"):
            query = TextInput(label="Server Name", placeholder="Enter partial name...", required=True)
            
            def __init__(self, parent_view):
                super().__init__()
                self.parent_view = parent_view
                
            async def on_submit(self, modal_inter: discord.Interaction):
                search_query = self.query.value.lower()
                filtered_guilds = [g for g in self.parent_view.guilds if search_query in g.name.lower()]
                
                if not filtered_guilds:
                    await modal_inter.response.send_message(f"❌ No servers found matching '{self.query.value}'", ephemeral=True)
                    return
                
                # Show results using the same view class but with filtered list
                view = RemoteServerSelectionView(self.parent_view.bot, filtered_guilds, self.parent_view.cog)
                embed = view.create_embed()
                embed.title = f"🔍 Search Results: {self.query.value}"
                
                await modal_inter.response.send_message(embed=embed, view=view, ephemeral=True)
        
        await interaction.response.send_modal(SearchModal(self))

    async def view_all_callback(self, interaction: discord.Interaction):
        """Show a paginated list of all servers."""
        from cogs.message_extractor import MessageExtractor
        extractor = self.bot.get_cog("MessageExtractor")
        if extractor:
            await extractor.list_servers(interaction)
        else:
            await interaction.response.send_message("❌ MessageExtractor module not found.", ephemeral=True)

            
        # Back button to Bot Operations
        back_button = discord.ui.Button(
            label="ΓùÇ Back to Bot Operations",
            emoji="🤖",
            style=discord.ButtonStyle.secondary,
            custom_id="bot_operations",
            row=4 if self.total_pages > 1 else 1
        )
        self.add_item(back_button)

    def create_embed(self) -> discord.Embed:
        """Create the embed for the current page of servers."""
        start_idx = self.page * 25
        end_idx = min(start_idx + 25, len(self.guilds))
        current_guilds = self.guilds[start_idx:end_idx]
        
        embed = discord.Embed(
            title="Γplain Remote Access Control Panel",
            description=(
                f"**Select a server to manage channels and settings**\n"
                f"Page {self.page + 1} of {self.total_pages}\n\n"
            ),
            color=0x00D9FF
        )
        
        server_list = []
        for guild in current_guilds:
            server_list.append(f"Γ£╢ **{guild.name}** (`{guild.id}`)\n   Γò╖ Members: {guild.member_count} | Channels: {len(guild.channels)}")
        
        embed.description += "\n".join(server_list)
        embed.set_footer(text=f"Total Servers Connected: {len(self.guilds)}")
        return embed

    async def previous_page(self, interaction: discord.Interaction):
        """Navigate to the previous page."""
        self.page = max(0, self.page - 1)
        view = RemoteServerSelectionView(self.bot, self.guilds, self.cog, self.page)
        embed = view.create_embed()
        await interaction.response.edit_message(embed=embed, view=view)

    async def next_page(self, interaction: discord.Interaction):
        """Navigate to the next page."""
        self.page = min(self.total_pages - 1, self.page + 1)
        view = RemoteServerSelectionView(self.bot, self.guilds, self.cog, self.page)
        embed = view.create_embed()
        await interaction.response.edit_message(embed=embed, view=view)

async def setup(bot):
    await bot.add_cog(RemoteAccess(bot, None))