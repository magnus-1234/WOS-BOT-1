import discord
from discord.ext import commands
from discord import app_commands
import logging
from command_animator import command_animation

logger = logging.getLogger(__name__)

class StartView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Alliance", style=discord.ButtonStyle.primary, emoji="🛡️", custom_id="start_alliance")
    async def alliance_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            # Import authentication adapters
            from db.mongo_adapters import mongo_enabled, ServerAllianceAdapter, AuthSessionsAdapter
            
            # Check if MongoDB is enabled
            if not mongo_enabled() or not ServerAllianceAdapter:
                await interaction.response.send_message(
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
                await interaction.response.send_message(embed=error_embed, view=view, ephemeral=True)
                return
            
            # Check if user has a valid authentication session
            if AuthSessionsAdapter and AuthSessionsAdapter.is_session_valid(
                interaction.guild.id,
                interaction.user.id,
                stored_password
            ):
                # User has valid session, show Alliance Monitor dashboard directly
                alliance_cog = self.bot.get_cog("Alliance")
                if alliance_cog:
                    from cogs.alliance import AllianceMonitorView
                    view = AllianceMonitorView(alliance_cog, interaction.guild.id)
                    
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
                    await interaction.response.send_message(
                        content="✅ **Access Granted** (Session Active)",
                        embed=embed,
                        view=view,
                        ephemeral=True
                    )
                else:
                    await interaction.response.send_message("Alliance module is not loaded.", ephemeral=True)
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
                
                def __init__(self, guild_id: int, guild_name: str, bot_instance):
                    super().__init__()
                    self.guild_id = guild_id
                    self.guild_name = guild_name
                    self.bot = bot_instance
                
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
                                value="Click the **Alliance** button again to retry.",
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
                        alliance_cog = self.bot.get_cog("Alliance")
                        if alliance_cog:
                            from cogs.alliance import AllianceMonitorView
                            view = AllianceMonitorView(alliance_cog, interaction.guild.id)
                            
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
                        else:
                            await modal_interaction.response.send_message(
                                "❌ Alliance module is not loaded.",
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
                def __init__(self, guild_id: int, guild_name: str, bot_instance):
                    super().__init__(timeout=60)
                    self.guild_id = guild_id
                    self.guild_name = guild_name
                    self.bot = bot_instance
                
                @discord.ui.button(label="Authenticate", emoji="🔐", style=discord.ButtonStyle.secondary, custom_id="alliance_auth")
                async def authenticate(self, button_interaction: discord.Interaction, button: discord.ui.Button):
                    modal = AllianceAuthModal(self.guild_id, self.guild_name, self.bot)
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
                text="Secured by Discord Interaction Gateway",
                icon_url="https://cdn.discordapp.com/attachments/1435569370389807144/1445660030815961209/discord-logo-png_seeklogo-481205_1.png?ex=69312752&is=692fd5d2&hm=5d6d7961ff5e1d3837308cbea9c5f0baa4a5cdf59af9009e49ba67b864963fe6"
            )
            
            # Send authentication embed with button
            view = AllianceAuthView(interaction.guild.id, interaction.guild.name, self.bot)
            await interaction.response.send_message(embed=auth_embed, view=view, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Error in alliance button: {e}")
            import traceback
            traceback.print_exc()
            await interaction.response.send_message("Failed to open Alliance menu.", ephemeral=True)

    @discord.ui.button(label="Gift Codes", style=discord.ButtonStyle.success, emoji="🎁", custom_id="start_giftcodes")
    async def giftcodes_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            # Import authentication adapters
            from db.mongo_adapters import mongo_enabled, ServerAllianceAdapter, AuthSessionsAdapter
            
            # Check if MongoDB is enabled
            if not mongo_enabled() or not ServerAllianceAdapter:
                await interaction.response.send_message(
                    "❌ MongoDB not enabled. Cannot access Gift Code Settings.",
                    ephemeral=True
                )
                return
            
            # Check if password is set
            stored_password = ServerAllianceAdapter.get_password(interaction.guild.id)
            if not stored_password:
                error_embed = discord.Embed(
                    title="🔒 Access Denied",
                    description="No password configured for management access.",
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
                await interaction.response.send_message(embed=error_embed, view=view, ephemeral=True)
                return
            
            # Check if user has a valid authentication session
            if AuthSessionsAdapter and AuthSessionsAdapter.is_session_valid(
                interaction.guild.id,
                interaction.user.id,
                stored_password
            ):
                # User has valid session, show gift code settings directly
                giftcodesettings_cmd = self.bot.tree.get_command("giftcodesettings")
                if giftcodesettings_cmd:
                    await giftcodesettings_cmd.callback(interaction)
                else:
                    await interaction.response.send_message("Gift code settings command is not available.", ephemeral=True)
                return
            
            # No valid session - show authentication modal
            class GiftCodeAuthModal(discord.ui.Modal, title="🔐 Security Verification"):
                password_input = discord.ui.TextInput(
                    label="Enter Access Code",
                    placeholder="••••••••••••",
                    style=discord.TextStyle.short,
                    required=True,
                    max_length=50
                )
                
                def __init__(self, guild_id: int, guild_name: str, bot_instance):
                    super().__init__()
                    self.guild_id = guild_id
                    self.guild_name = guild_name
                    self.bot = bot_instance
                
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
                                value="Click the **Gift Codes** button again to retry.",
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
                        
                        # Show gift code settings
                        giftcodesettings_cmd = self.bot.tree.get_command("giftcodesettings")
                        if giftcodesettings_cmd:
                            await giftcodesettings_cmd.callback(modal_interaction)
                        else:
                            await modal_interaction.response.send_message(
                                "❌ Gift code settings command is not available.",
                                ephemeral=True
                            )
                    
                    except Exception as e:
                        print(f"Error in gift code auth modal: {e}")
                        import traceback
                        traceback.print_exc()
                        await modal_interaction.response.send_message(
                            "❌ An error occurred during authentication.",
                            ephemeral=True
                        )
            
            # Create authentication view with button
            class GiftCodeAuthView(discord.ui.View):
                def __init__(self, guild_id: int, guild_name: str, bot_instance):
                    super().__init__(timeout=60)
                    self.guild_id = guild_id
                    self.guild_name = guild_name
                    self.bot = bot_instance
                
                @discord.ui.button(label="Authenticate", emoji="🔐", style=discord.ButtonStyle.secondary, custom_id="giftcode_auth")
                async def authenticate(self, button_interaction: discord.Interaction, button: discord.ui.Button):
                    modal = GiftCodeAuthModal(self.guild_id, self.guild_name, self.bot)
                    await button_interaction.response.send_modal(modal)
            
            # Create authentication embed
            auth_embed = discord.Embed(
                title=interaction.guild.name,
                description="**Gift Code Settings Access**\n\nAuthentication required to access gift code management features.",
                color=0x2B2D31
            )
            
            auth_embed.set_author(
                name="SECURITY VERIFICATION REQUIRED",
                icon_url="https://cdn.discordapp.com/attachments/1435569370389807144/1445470757844160543/unnamed_6_1.png"
            )
            
            auth_embed.add_field(
                name="🔒 Protected Resource",
                value="Gift Code Settings Dashboard",
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
                text="Secured by Discord Interaction Gateway",
                icon_url="https://cdn.discordapp.com/attachments/1435569370389807144/1445660030815961209/discord-logo-png_seeklogo-481205_1.png?ex=69312752&is=692fd5d2&hm=5d6d7961ff5e1d3837308cbea9c5f0baa4a5cdf59af9009e49ba67b864963fe6"
            )
            
            # Send authentication embed with button
            view = GiftCodeAuthView(interaction.guild.id, interaction.guild.name, self.bot)
            await interaction.response.send_message(embed=auth_embed, view=view, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Error in giftcodes button: {e}")
            import traceback
            traceback.print_exc()
            await interaction.response.send_message("Failed to open Gift Code Settings.", ephemeral=True)

    @discord.ui.button(label="Events", style=discord.ButtonStyle.secondary, emoji="📅", custom_id="start_events")
    async def events_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            # Import event data
            from event_tips import EVENT_TIPS, get_event_info, get_difficulty_color, get_category_emoji
            
            # Create event selection view
            class EventSelectionView(discord.ui.View):
                def __init__(self):
                    super().__init__(timeout=180)
                    
                    # Create event options from EVENT_TIPS
                    event_options = [
                        discord.SelectOption(
                            label=event_info['name'],
                            value=event_key,
                            emoji=get_category_emoji(event_info.get('category', ''))
                        )
                        for event_key, event_info in EVENT_TIPS.items()
                    ]
                    
                    event_select = discord.ui.Select(
                        placeholder="Select an event to view details...",
                        options=event_options[:25],  # Discord limit
                        custom_id="event_select"
                    )
                    event_select.callback = self.event_selected
                    self.add_item(event_select)
                
                async def event_selected(self, select_interaction: discord.Interaction):
                    try:
                        selected_event = select_interaction.data['values'][0]
                        event_info = get_event_info(selected_event.lower())
                        
                        if not event_info:
                            await select_interaction.response.send_message(
                                "❌ Event information not found.",
                                ephemeral=True
                            )
                            return
                        
                        # Build event description
                        description_lines = []
                        
                        if event_info.get('guide'):
                            description_lines.append(f"📚 Guide: [Click here]({event_info['guide']})")
                        
                        if event_info.get('video'):
                            description_lines.append(f"🎥 Video Guide: [Watch here]({event_info['video']})")
                        
                        if event_info.get('tips'):
                            description_lines.append(f"💡 Tips: {event_info['tips']}")
                        else:
                            description_lines.append("💡 Tips: please wait🙏- working on it.....")
                        
                        description = "\n".join(description_lines) if description_lines else "No information available."
                        
                        embed = discord.Embed(
                            title=f"{get_category_emoji(event_info['category'])} {event_info['name']}",
                            description=description,
                            color=get_difficulty_color(event_info['difficulty']),
                        )
                        
                        if 'image' in event_info:
                            embed.set_thumbnail(url=event_info['image'])
                        
                        await select_interaction.response.send_message(embed=embed, ephemeral=True)
                    
                    except Exception as e:
                        logger.error(f"Error in event selection: {e}")
                        if not select_interaction.response.is_done():
                            await select_interaction.response.send_message(
                                "❌ An error occurred while loading event information.",
                                ephemeral=True
                            )
            
            # Create and send event selection embed
            embed = discord.Embed(
                title="📅 Whiteout Survival Events",
                description="Select an event from the dropdown below to view detailed information, guides, and tips.",
                color=discord.Color.blue()
            )
            
            view = EventSelectionView()
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Error in events button: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message("Failed to open Events menu.", ephemeral=True)

    @discord.ui.button(label="Help", style=discord.ButtonStyle.secondary, emoji="❓", custom_id="start_help")
    async def help_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            import cogs.shared_views as sv
            # Show the same interactive help menu as /help command
            embed = discord.Embed(
                title="⚡ Whiteout Survival Bot",
                description=(
                    "Access all bot functions through categorized command modules.\n"
                    "Use the dropdown below to explore each category.\n\n"
                    "**📋 Available Modules**\n\n"
                    "🎮 **Fun & Games** — 3 commands\n"
                    "🎁 **Gift Codes & Rewards** — 3 commands\n"
                    "🎵 **Music Player** — 15 commands\n"
                    "⏰ **Reminders & Time** — 2 commands\n"
                    "👥 **Community & Stats** — 4 commands\n"
                    "🛡️ **Alliance Management** — 4 commands\n"
                    "🌐 **Auto-Translate** — 5 commands\n"
                    "⚙️ **Server Configuration** — 4 commands\n"
                    "🔧 **Utility & Tools** — 2 commands"
                ),
                color=0x00d9ff
            )
            embed.set_thumbnail(url="https://i.postimg.cc/Fzq03CJf/a463d7c7-7fc7-47fc-b24d-1324383ee2ff-removebg-preview.png")
            embed.set_footer(text="Select a category to view detailed commands")
            
            view = sv.InteractiveHelpView()
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        except Exception as e:
            logger.error(f"Error in help button: {e}")
            await interaction.response.send_message("Failed to open Help.", ephemeral=True)

    @discord.ui.button(label="Reminder", style=discord.ButtonStyle.primary, emoji="⏰", custom_id="start_reminder")
    async def reminder_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            # Get the reminder command and invoke it directly - this will show all options
            reminder_cmd = self.bot.tree.get_command("reminder")
            if reminder_cmd:
                # Since we can't directly invoke a slash command with a button,
                # we'll create an interactive view that mimics the full /reminder interface
                from cogs.reminder_system import REMINDER_IMAGES
                
                # Create a comprehensive reminder setup view
                class ReminderSetupView(discord.ui.View):
                    def __init__(self, bot_instance):
                        super().__init__(timeout=300)
                        self.bot = bot_instance
                        self.time_str = None
                        self.message = None
                        self.channel = None
                        self.body = None
                        self.thumbnail_preset = None
                        self.image_url = None
                        self.thumbnail_url = None
                        self.footer_text = None
                        self.footer_icon_url = None
                        self.author_url = None
                        
                        # Add channel select
                        channel_select = discord.ui.ChannelSelect(
                            placeholder="Select channel for reminder",
                            min_values=1,
                            max_values=1,
                            channel_types=[discord.ChannelType.text]
                        )
                        channel_select.callback = self.channel_selected
                        self.add_item(channel_select)
                        
                        # Add thumbnail preset select
                        preset_options = [
                            discord.SelectOption(label=k, value=k) 
                            for k in REMINDER_IMAGES.keys()
                        ]
                        preset_select = discord.ui.Select(
                            placeholder="Choose thumbnail preset (optional)",
                            options=preset_options,
                            min_values=0,
                            max_values=1,
                            row=1
                        )
                        preset_select.callback = self.preset_selected
                        self.add_item(preset_select)
                    
                    async def channel_selected(self, select_interaction: discord.Interaction):
                        self.channel = select_interaction.data['values'][0]
                        await select_interaction.response.send_message(
                            f"✅ Channel selected: <#{self.channel}>",
                            ephemeral=True
                        )
                    
                    async def preset_selected(self, select_interaction: discord.Interaction):
                        if select_interaction.data['values']:
                            self.thumbnail_preset = select_interaction.data['values'][0]
                            await select_interaction.response.send_message(
                                f"✅ Thumbnail preset selected: {self.thumbnail_preset}",
                                ephemeral=True
                            )
                    
                    @discord.ui.button(label="Set Basic Info", style=discord.ButtonStyle.primary, emoji="📝", row=2)
                    async def set_basic_info(self, button_interaction: discord.Interaction, button: discord.ui.Button):
                        # Modal for basic info
                        class BasicInfoModal(discord.ui.Modal, title="⏰ Basic Reminder Info"):
                            time_input = discord.ui.TextInput(
                                label="When to remind you",
                                placeholder="e.g., '5 minutes', 'tomorrow 3pm IST', 'daily at 9am'",
                                style=discord.TextStyle.short,
                                required=True,
                                max_length=100
                            )
                            
                            message_input = discord.ui.TextInput(
                                label="Reminder Title/Message",
                                placeholder="Enter the reminder message...",
                                style=discord.TextStyle.short,
                                required=True,
                                max_length=200
                            )
                            
                            body_input = discord.ui.TextInput(
                                label="Detailed Description (Optional)",
                                placeholder="Enter detailed description...",
                                style=discord.TextStyle.paragraph,
                                required=False,
                                max_length=1000
                            )
                            
                            def __init__(self, parent_view):
                                super().__init__()
                                self.parent_view = parent_view
                            
                            async def on_submit(self, modal_interaction: discord.Interaction):
                                self.parent_view.time_str = self.time_input.value.strip()
                                self.parent_view.message = self.message_input.value.strip()
                                self.parent_view.body = self.body_input.value.strip() if self.body_input.value else None
                                await modal_interaction.response.send_message(
                                    "✅ Basic info saved! You can now add custom URLs or create the reminder.",
                                    ephemeral=True
                                )
                        
                        modal = BasicInfoModal(self)
                        await button_interaction.response.send_modal(modal)
                    
                    @discord.ui.button(label="Add Custom URLs", style=discord.ButtonStyle.secondary, emoji="🔗", row=2)
                    async def add_custom_urls(self, button_interaction: discord.Interaction, button: discord.ui.Button):
                        # Modal for custom URLs
                        class CustomURLModal(discord.ui.Modal, title="🔗 Custom URLs"):
                            image_url_input = discord.ui.TextInput(
                                label="Image URL (Optional)",
                                placeholder="https://example.com/image.png",
                                style=discord.TextStyle.short,
                                required=False,
                                max_length=500
                            )
                            
                            thumbnail_url_input = discord.ui.TextInput(
                                label="Thumbnail URL (Optional)",
                                placeholder="https://example.com/thumbnail.png",
                                style=discord.TextStyle.short,
                                required=False,
                                max_length=500
                            )
                            
                            footer_text_input = discord.ui.TextInput(
                                label="Footer Text (Optional)",
                                placeholder="Footer text...",
                                style=discord.TextStyle.short,
                                required=False,
                                max_length=200
                            )
                            
                            footer_icon_input = discord.ui.TextInput(
                                label="Footer Icon URL (Optional)",
                                placeholder="https://example.com/icon.png",
                                style=discord.TextStyle.short,
                                required=False,
                                max_length=500
                            )
                            
                            author_url_input = discord.ui.TextInput(
                                label="Author URL (Optional)",
                                placeholder="https://example.com",
                                style=discord.TextStyle.short,
                                required=False,
                                max_length=500
                            )
                            
                            def __init__(self, parent_view):
                                super().__init__()
                                self.parent_view = parent_view
                            
                            async def on_submit(self, modal_interaction: discord.Interaction):
                                self.parent_view.image_url = self.image_url_input.value.strip() if self.image_url_input.value else None
                                self.parent_view.thumbnail_url = self.thumbnail_url_input.value.strip() if self.thumbnail_url_input.value else None
                                self.parent_view.footer_text = self.footer_text_input.value.strip() if self.footer_text_input.value else None
                                self.parent_view.footer_icon_url = self.footer_icon_input.value.strip() if self.footer_icon_input.value else None
                                self.parent_view.author_url = self.author_url_input.value.strip() if self.author_url_input.value else None
                                await modal_interaction.response.send_message(
                                    "✅ Custom URLs saved!",
                                    ephemeral=True
                                )
                        
                        modal = CustomURLModal(self)
                        await button_interaction.response.send_modal(modal)
                    
                    @discord.ui.button(label="Create Reminder", style=discord.ButtonStyle.success, emoji="✅", row=3)
                    async def create_reminder(self, button_interaction: discord.Interaction, button: discord.ui.Button):
                        # Validate required fields
                        if not self.time_str or not self.message:
                            await button_interaction.response.send_message(
                                "❌ Please set basic info first (time and message are required)!",
                                ephemeral=True
                            )
                            return
                        
                        # Get channel - use selected or current
                        if self.channel:
                            target_channel = button_interaction.guild.get_channel(int(self.channel))
                        else:
                            target_channel = button_interaction.channel
                        
                        # Get the reminder command
                        reminder_cmd = self.bot.tree.get_command("reminder")
                        if reminder_cmd:
                            try:
                                # Call the reminder command with all parameters
                                await reminder_cmd.callback(
                                    button_interaction,
                                    time=self.time_str,
                                    message=self.message,
                                    channel=target_channel,
                                    body=self.body,
                                    thumbnailimage_preset=self.thumbnail_preset,
                                    image_url=self.image_url,
                                    thumbnail_url=self.thumbnail_url,
                                    footer_text=self.footer_text,
                                    footer_icon_url=self.footer_icon_url,
                                    author_url=self.author_url
                                )
                            except Exception as e:
                                logger.error(f"Error creating reminder: {e}")
                                import traceback
                                traceback.print_exc()
                                if not button_interaction.response.is_done():
                                    await button_interaction.response.send_message(
                                        f"❌ Error creating reminder: {str(e)}",
                                        ephemeral=True
                                    )
                        else:
                            await button_interaction.response.send_message(
                                "❌ Reminder command is not available.",
                                ephemeral=True
                            )
                
                # Create and send the setup view
                view = ReminderSetupView(self.bot)
                embed = discord.Embed(
                    title="⏰ Create Reminder",
                    description=(
                        "**Set up your reminder with all available options:**\n\n"
                        "1️⃣ **Select Channel** - Choose where to send the reminder\n"
                        "2️⃣ **Choose Thumbnail Preset** - Optional preset image\n"
                        "3️⃣ **Set Basic Info** - Time, message, and description\n"
                        "4️⃣ **Add Custom URLs** - Images, footer, author link (optional)\n"
                        "5️⃣ **Create Reminder** - Finalize and create\n\n"
                        "💡 Only time and message are required!"
                    ),
                    color=discord.Color.blue()
                )
                embed.set_thumbnail(url="https://i.postimg.cc/Fzq03CJf/a463d7c7-7fc7-47fc-b24d-1324383ee2ff-removebg-preview.png")
                
                await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            else:
                await interaction.response.send_message("Reminder command is not available.", ephemeral=True)
                
        except Exception as e:
            logger.error(f"Error in reminder button: {e}")
            import traceback
            traceback.print_exc()
            if not interaction.response.is_done():
                await interaction.response.send_message("Failed to open Reminder creator.", ephemeral=True)

    @discord.ui.button(label="Music", style=discord.ButtonStyle.success, emoji="🎵", custom_id="start_music")
    async def music_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.send_message(
                "🎵 Music now runs on the separate music bot. Use `/play` from that bot in a voice channel.",
                ephemeral=True,
            )
            
        except Exception as e:
            logger.error(f"Error in music button: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message("Failed to open Music player.", ephemeral=True)

    @discord.ui.button(label="Auto Translate", style=discord.ButtonStyle.primary, emoji="🌐", custom_id="start_autotranslate")
    async def autotranslate_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            # Show auto-translate menu with sub-buttons
            view = AutoTranslateMenuView(self.bot)
            embed = discord.Embed(
                title="🌐 Auto Translate Menu",
                description="Manage automatic translation configurations between channels.\n\nSelect an action below:",
                color=discord.Color.blue()
            )
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        except Exception as e:
            logger.error(f"Error in autotranslate button: {e}")
            await interaction.response.send_message("Failed to open Auto Translate menu.", ephemeral=True)

    @discord.ui.button(label="Settings", style=discord.ButtonStyle.secondary, emoji="⚙️", custom_id="start_settings")
    async def settings_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            alliance_cog = self.bot.get_cog("Alliance")
            if alliance_cog and hasattr(alliance_cog, 'settings'):
                # Call the settings command from the Alliance cog
                await alliance_cog.settings.callback(alliance_cog, interaction)
            else:
                await interaction.response.send_message("Settings command is not available.", ephemeral=True)
        except Exception as e:
            logger.error(f"Error in settings button: {e}")
            await interaction.response.send_message("Failed to open Settings menu.", ephemeral=True)

    @discord.ui.button(label="Games", style=discord.ButtonStyle.success, emoji="🎮", custom_id="start_games")
    async def games_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            view = GamesView(self.bot)
            embed = discord.Embed(
                title="🎮 Games Menu",
                description=(
                    "Choose a game to play:\n\n"
                    "🎲 **Dice** - Roll the dice and test your luck!\n"
                    "⭕ **Tic-Tac-Toe** - Challenge a friend to an epic battle!\n\n"
                    "Select a game below to get started!"
                ),
                color=discord.Color.green()
            )
            embed.set_footer(text="≡ƒÄ» Have fun playing!")
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        except Exception as e:
            logger.error(f"Error in games button: {e}")
            await interaction.response.send_message("Failed to open Games menu.", ephemeral=True)

    @discord.ui.button(label="Birthday", style=discord.ButtonStyle.primary, emoji="🎂", custom_id="start_birthday")
    async def birthday_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            import cogs.shared_views as sv
            birthday_cog = self.bot.get_cog("BirthdaySystem")
            if birthday_cog:
                view = sv.BirthdayDashboardView(birthday_cog)
                embed = discord.Embed(
                    title="🎂 Birthday Dashboard",
                    description="Manage your birthday and view upcoming celebrations!",
                    color=discord.Color.from_rgb(255, 105, 180)  # Hot pink
                )
                await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            else:
                await interaction.response.send_message("Birthday system is not active.", ephemeral=True)
        except Exception as e:
            logger.error(f"Error in birthday button: {e}")
            await interaction.response.send_message("Failed to open Birthday menu.", ephemeral=True)

    @discord.ui.button(label="Welcome", style=discord.ButtonStyle.success, emoji="👋", custom_id="start_welcome")
    async def welcome_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            welcome_cog = self.bot.get_cog("WelcomeChannel")
            if welcome_cog:
                # Call the welcome command directly
                await welcome_cog.welcome.callback(welcome_cog, interaction)
            else:
                await interaction.response.send_message("Welcome system is not active.", ephemeral=True)
        except Exception as e:
            logger.error(f"Error in welcome button: {e}")
            await interaction.response.send_message("Failed to open Welcome menu.", ephemeral=True)

    @discord.ui.button(label="Manage", style=discord.ButtonStyle.primary, emoji="📋", custom_id="start_manage")
    async def manage_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            # Get the BotOperations cog and call the manage command
            bot_operations_cog = self.bot.get_cog("BotOperations")
            if bot_operations_cog and hasattr(bot_operations_cog, 'manage'):
                await bot_operations_cog.manage.callback(bot_operations_cog, interaction)
            else:
                await interaction.response.send_message("Manage command is not available.", ephemeral=True)
        except Exception as e:
            logger.error(f"Error in manage button: {e}")
            await interaction.response.send_message("Failed to open Manage menu.", ephemeral=True)


class AutoTranslateMenuView(discord.ui.View):
    """Sub-menu for auto-translate commands"""
    def __init__(self, bot):
        super().__init__(timeout=180)
        self.bot = bot

    @discord.ui.button(label="Create", style=discord.ButtonStyle.success, emoji="➕", custom_id="autotranslate_create")
    async def create_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            autotranslate_cog = self.bot.get_cog("AutoTranslate")
            if autotranslate_cog and hasattr(autotranslate_cog, 'autotranslatecreate'):
                await autotranslate_cog.autotranslatecreate.callback(autotranslate_cog, interaction)
            else:
                await interaction.response.send_message("Auto-translate system is not available.", ephemeral=True)
        except Exception as e:
            logger.error(f"Error in autotranslate create button: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message("Failed to create auto-translate configuration.", ephemeral=True)

    @discord.ui.button(label="List", style=discord.ButtonStyle.primary, emoji="📋", custom_id="autotranslate_list")
    async def list_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            autotranslate_cog = self.bot.get_cog("AutoTranslate")
            if autotranslate_cog and hasattr(autotranslate_cog, 'autotranslatelist'):
                await autotranslate_cog.autotranslatelist.callback(autotranslate_cog, interaction)
            else:
                await interaction.response.send_message("Auto-translate system is not available.", ephemeral=True)
        except Exception as e:
            logger.error(f"Error in autotranslate list button: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message("Failed to list auto-translate configurations.", ephemeral=True)

    @discord.ui.button(label="Edit", style=discord.ButtonStyle.secondary, emoji="✏️", custom_id="autotranslate_edit")
    async def edit_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            autotranslate_cog = self.bot.get_cog("AutoTranslate")
            if autotranslate_cog and hasattr(autotranslate_cog, 'autotranslateedit'):
                await autotranslate_cog.autotranslateedit.callback(autotranslate_cog, interaction)
            else:
                await interaction.response.send_message("Auto-translate system is not available.", ephemeral=True)
        except Exception as e:
            logger.error(f"Error in autotranslate edit button: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message("Failed to edit auto-translate configuration.", ephemeral=True)

    @discord.ui.button(label="Toggle", style=discord.ButtonStyle.secondary, emoji="🔄", custom_id="autotranslate_toggle")
    async def toggle_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            autotranslate_cog = self.bot.get_cog("AutoTranslate")
            if autotranslate_cog and hasattr(autotranslate_cog, 'autotranslatetoggle'):
                await autotranslate_cog.autotranslatetoggle.callback(autotranslate_cog, interaction)
            else:
                await interaction.response.send_message("Auto-translate system is not available.", ephemeral=True)
        except Exception as e:
            logger.error(f"Error in autotranslate toggle button: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message("Failed to toggle auto-translate configuration.", ephemeral=True)

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, emoji="🗑️", custom_id="autotranslate_delete")
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            autotranslate_cog = self.bot.get_cog("AutoTranslate")
            if autotranslate_cog and hasattr(autotranslate_cog, 'autotranslatedelete'):
                await autotranslate_cog.autotranslatedelete.callback(autotranslate_cog, interaction)
            else:
                await interaction.response.send_message("Auto-translate system is not available.", ephemeral=True)
        except Exception as e:
            logger.error(f"Error in autotranslate delete button: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message("Failed to delete auto-translate configuration.", ephemeral=True)


class GamesView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Dice", style=discord.ButtonStyle.primary, emoji="🎲", custom_id="game_dice")
    async def dice_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            # Get the dice command from the bot's tree
            dice_command = self.bot.tree.get_command("dice")
            if dice_command:
                await dice_command.callback(interaction)
            else:
                await interaction.response.send_message("Dice command is not available.", ephemeral=True)
        except Exception as e:
            logger.error(f"Error in dice button: {e}")
            await interaction.response.send_message("Failed to roll dice.", ephemeral=True)

    @discord.ui.button(label="Tic-Tac-Toe", style=discord.ButtonStyle.success, emoji="⭕", custom_id="game_tictactoe")
    async def tictactoe_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            # Create a modal to select opponent
            class TicTacToeOpponentModal(discord.ui.Modal, title="🎮 Start Tic-Tac-Toe"):
                opponent_input = discord.ui.TextInput(
                    label="Opponent's Username or ID",
                    placeholder="Enter the username or ID of your opponent...",
                    style=discord.TextStyle.short,
                    required=True,
                    max_length=50
                )
                
                def __init__(self, bot_instance):
                    super().__init__()
                    self.bot = bot_instance
                
                async def on_submit(self, modal_interaction: discord.Interaction):
                    try:
                        opponent_input = self.opponent_input.value.strip()
                        
                        # Try to find the opponent
                        opponent = None
                        
                        # Try to get by ID first
                        if opponent_input.isdigit():
                            opponent = await modal_interaction.guild.fetch_member(int(opponent_input))
                        
                        # If not found, try by username or display name
                        if not opponent:
                            for member in modal_interaction.guild.members:
                                if (member.name.lower() == opponent_input.lower() or 
                                    member.display_name.lower() == opponent_input.lower() or
                                    str(member) == opponent_input):
                                    opponent = member
                                    break
                        
                        if not opponent:
                            error_embed = discord.Embed(
                                title="❌ User Not Found",
                                description=f"Could not find a user matching: **{opponent_input}**\n\nTry using their exact username or Discord ID.",
                                color=0xFF0000
                            )
                            await modal_interaction.response.send_message(embed=error_embed, ephemeral=True)
                            return
                        
                        # Get the TicTacToe cog and call the command
                        tictactoe_cog = self.bot.get_cog("TicTacToe")
                        if tictactoe_cog and hasattr(tictactoe_cog, 'tictactoe'):
                            await tictactoe_cog.tictactoe.callback(tictactoe_cog, modal_interaction, opponent)
                        else:
                            await modal_interaction.response.send_message(
                                "❌ Tic-Tac-Toe game is not available.",
                                ephemeral=True
                            )
                    
                    except discord.NotFound:
                        error_embed = discord.Embed(
                            title="❌ User Not Found",
                            description=f"Could not find a user with ID or name: **{opponent_input}**",
                            color=0xFF0000
                        )
                        await modal_interaction.response.send_message(embed=error_embed, ephemeral=True)
                    except Exception as e:
                        logger.error(f"Error finding opponent: {e}")
                        import traceback
                        traceback.print_exc()
                        if not modal_interaction.response.is_done():
                            await modal_interaction.response.send_message(
                                f"❌ An error occurred: {str(e)}",
                                ephemeral=True
                            )
            
            # Show the opponent selection modal
            modal = TicTacToeOpponentModal(self.bot)
            await interaction.response.send_modal(modal)
            
        except Exception as e:
            logger.error(f"Error in tictactoe button: {e}")
            import traceback
            traceback.print_exc()
            if not interaction.response.is_done():
                await interaction.response.send_message("Failed to start Tic-Tac-Toe.", ephemeral=True)



class StartMenu(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="start", description="Show the main menu")
    @command_animation
    async def start(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🤖 Bot Main Menu",
            description="Welcome! Please select a feature below:",
            color=discord.Color.blue()
        )
        embed.set_thumbnail(url="attachment://logo.png")
        view = StartView(self.bot)
        
        # Send with the logo file
        logo_file = discord.File("logo.png", filename="logo.png")
        
        # Check if interaction was already responded to (deferred)
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, view=view, file=logo_file)
        else:
            # Interaction wasn't deferred (expired before defer), respond directly
            await interaction.response.send_message(embed=embed, view=view, file=logo_file)


async def setup(bot):
    await bot.add_cog(StartMenu(bot))
