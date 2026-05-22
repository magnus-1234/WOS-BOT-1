from typing import Union
import discord
from discord.ext import commands
from discord import app_commands
import os
import sqlite3
import asyncio
import requests
from .alliance_member_operations import AllianceSelectView
from admin_utils import is_bot_owner
try:
    from db.mongo_adapters import mongo_enabled, AdminsAdapter, AlliancesAdapter, ServerAllianceAdapter, AllianceMembersAdapter, RecordsAdapter, AuthSessionsAdapter, UserTimezonesAdapter as PlayerTimezonesAdapter
except Exception:
    mongo_enabled = lambda: False
    AdminsAdapter = None
    AlliancesAdapter = None
    ServerAllianceAdapter = None
    AllianceMembersAdapter = None
    RecordsAdapter = None
    AuthSessionsAdapter = None
    PlayerTimezonesAdapter = None

class BotOperations(commands.Cog):
    def __init__(self, bot, conn):
        self.bot = bot
        self.conn = conn
        self.settings_db = sqlite3.connect('db/settings.sqlite', check_same_thread=False)
        self.settings_cursor = self.settings_db.cursor()
        self.alliance_db = sqlite3.connect('db/alliance.sqlite', check_same_thread=False)
        self.c_alliance = self.alliance_db.cursor()
        self.setup_database()

    def get_current_version(self):
        """Get current version from version file"""
        try:
            if os.path.exists("version"):
                with open("version", "r") as f:
                    return f.read().strip()
            return "v0.0.0"
        except Exception:
            return "v0.0.0"
        
    def setup_database(self):
        try:
            self.settings_cursor.execute("""
                CREATE TABLE IF NOT EXISTS admin (
                    id INTEGER PRIMARY KEY,
                    is_initial INTEGER DEFAULT 0
                )
            """)
            
            self.settings_cursor.execute("""
                CREATE TABLE IF NOT EXISTS adminserver (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    admin INTEGER NOT NULL,
                    alliances_id INTEGER NOT NULL,
                    FOREIGN KEY (admin) REFERENCES admin(id),
                    UNIQUE(admin, alliances_id)
                )
            """)
            
            self.settings_db.commit()
                
        except Exception as e:
            pass

    def __del__(self):
        try:
            self.settings_db.close()
            self.alliance_db.close()
        except:
            pass

    @commands.command(name="sync")
    async def sync_tree(self, ctx, scope: str = "global"):
        """Syncs the command tree. usage: !sync [Union[global, guild]]"""
        # Check if user is global admin
        self.settings_cursor.execute("SELECT is_initial FROM admin WHERE id = ?", (ctx.author.id,))
        result = self.settings_cursor.fetchone()
        
        if (not result or result[0] != 1) and not await is_bot_owner(self.bot, ctx.author.id):
            await ctx.send("You do not have permission to use this command.")
            return

        try:
            if scope.lower() == "guild":
                self.bot.tree.copy_global_to(guild=ctx.guild)
                fmt = await self.bot.tree.sync(guild=ctx.guild)
                await ctx.send(f"Synced {len(fmt)} commands to this guild.")
            else:
                fmt = await self.bot.tree.sync()
                await ctx.send(f"Synced {len(fmt)} commands globally (may take up to 1 hour).")
        except Exception as e:
            await ctx.send(f"Failed to sync: {e}")

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if not interaction.type == discord.InteractionType.component:
            return

        custom_id = interaction.data.get("custom_id", "")

        # Security: Parse user_id from custom_id if present
        # This handles format: "original_id:user_id"
        if custom_id and ":" in custom_id:
            try:
                real_id, user_str = custom_id.rsplit(":", 1)
                # Check if the suffix is a valid user ID (digits)
                if user_str.isdigit():
                    check_user_id = int(user_str)
                    
                    # Verify user
                    if interaction.user.id != check_user_id:
                        await interaction.response.send_message("❌ This menu is not for you.", ephemeral=True)
                        return
                    
                    # Update custom_id to strip the suffix so downstream logic works
                    custom_id = real_id
            except ValueError:
                pass
        
        # Let ManageGiftCode cog handle all giftcode interactions
        if custom_id.startswith("giftcode"):
            return
        
        if custom_id == "bot_operations":
            return
        
        # Handle Other Features button
        if custom_id == "manage_other_features":
            try:
                # Create Other Features submenu
                embed = discord.Embed(
                    title="🔮 Other Features",
                    description=(
                        "```ansi\n"
                        "\u001b[2;36m╔═══════════════════════════════════╗\n"
                        "\u001b[2;36m║  \u001b[1;37mADDITIONAL FEATURES\u001b[0m\u001b[2;36m           ║\n"
                        "\u001b[2;36m╚═══════════════════════════════════╝\u001b[0m\n"
                        "```\n"
                        "**Explore additional bot features**\n\n"
                        "🌍 **Players Timezone**\n"
                        "   ▸ View member timezones\n"
                        "   ▸ Set your timezone\n\n"
                        "👋 **Welcome Messages**\n"
                        "   ▸ Configure welcome channel\n"
                        "   ▸ Customize welcome images\n"
                    ),
                    color=0x2B2D31
                )
                embed.set_footer(
                    text=f"{interaction.guild.name} x Magnus🚀",
                    icon_url="https://cdn.discordapp.com/attachments/1435569370389807144/1436745053442805830/unnamed_5.png"
                )

                view = discord.ui.View()
                view.add_item(discord.ui.Button(
                    label="Players Timezone",
                    emoji="🌍",
                    style=discord.ButtonStyle.secondary,
                    custom_id="players_timezone",
                    row=0
                ))
                view.add_item(discord.ui.Button(
                    label="Welcome Messages",
                    emoji="👋",
                    style=discord.ButtonStyle.secondary,
                    custom_id="manage_welcome",
                    row=0
                ))
                view.add_item(discord.ui.Button(
                    label="◀ Back",
                    emoji="🏠",
                    style=discord.ButtonStyle.secondary,
                    custom_id="return_to_manage",
                    row=1
                ))

                await interaction.response.edit_message(embed=embed, view=view)
            except discord.errors.NotFound:
                # Interaction expired, send a new message
                await interaction.followup.send(
                    "❌ Interaction expired. Please run `/manage` again.",
                    ephemeral=True
                )
            except Exception as e:
                print(f"Other Features error: {e}")
                import traceback
                traceback.print_exc()
                try:
                    await interaction.response.send_message(
                        "❌ An error occurred while loading Other Features.",
                        ephemeral=True
                    )
                except:
                    await interaction.followup.send(
                        "❌ An error occurred while loading Other Features.",
                        ephemeral=True
                    )
            return
        
        # Handle Alliance Monitor button
        if custom_id == "manage_alliance_monitor":
            try:
                alliance_cog = self.bot.get_cog("Alliance")
                if alliance_cog:
                    from cogs.alliance import AllianceMonitorView
                    view = AllianceMonitorView(alliance_cog, interaction.guild_id)
                    
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
                    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
                else:
                    await interaction.response.send_message("Alliance module is not loaded.", ephemeral=True)
            except Exception as e:
                print(f"Alliance Monitor error: {e}")
                import traceback
                traceback.print_exc()
                await interaction.response.send_message(
                    "❌ An error occurred while loading Alliance Monitor.",
                    ephemeral=True
                )
            return
        
        # Handle Welcome Messages button
        if custom_id == "manage_welcome":
            try:
                welcome_cog = self.bot.get_cog("WelcomeChannel")
                if welcome_cog:
                    # Call the welcome command directly
                    await welcome_cog.welcome.callback(welcome_cog, interaction)
                else:
                    await interaction.response.send_message(
                        "❌ Welcome system is not loaded.",
                        ephemeral=True
                    )
            except Exception as e:
                print(f"Welcome Messages error: {e}")
                import traceback
                traceback.print_exc()
                try:
                    await interaction.response.send_message(
                        "❌ An error occurred while loading Welcome Messages.",
                        ephemeral=True
                    )
                except:
                    await interaction.followup.send(
                        "❌ An error occurred while loading Welcome Messages.",
                        ephemeral=True
                    )
            return
        
        # Handle Players Timezone button
        if custom_id == "players_timezone":
            try:
                # Create Players Timezone submenu
                embed = discord.Embed(
                    title="🌍 Players Timezone",
                    description=(
                        "```ansi\n"
                        "\u001b[2;36m╔═══════════════════════════════════╗\n"
                        "\u001b[2;36m║  \u001b[1;37mTIMEZONE MANAGEMENT\u001b[0m\u001b[2;36m         ║\n"
                        "\u001b[2;36m╚═══════════════════════════════════╝\u001b[0m\n"
                        "```\n"
                        "**Manage player timezone settings**\n\n"
                        "👥 **View Members**\n"
                        "   ▸ See all member timezones\n"
                        "   ▸ Check availability by timezone\n\n"
                        "⏰ **Set Player Timezone**\n"
                        "   ▸ Configure your timezone\n"
                        "   ▸ Update preferences\n"
                    ),
                    color=0x2B2D31
                )
                embed.set_footer(
                    text=f"{interaction.guild.name} x Magnus🚀",
                    icon_url="https://cdn.discordapp.com/attachments/1435569370389807144/1436745053442805830/unnamed_5.png"
                )

                view = discord.ui.View()
                view.add_item(discord.ui.Button(
                    label="View Members",
                    emoji="👥",
                    style=discord.ButtonStyle.secondary,
                    custom_id="timezone_view_members",
                    row=0
                ))
                view.add_item(discord.ui.Button(
                    label="Set Player Timezone",
                    emoji="⏰",
                    style=discord.ButtonStyle.secondary,
                    custom_id="set_player_timezone",
                    row=0
                ))
                view.add_item(discord.ui.Button(
                    label="◀ Back",
                    emoji="🔮",
                    style=discord.ButtonStyle.secondary,
                    custom_id="manage_other_features",
                    row=1
                ))

                await interaction.response.edit_message(embed=embed, view=view)
            except discord.errors.NotFound:
                # Interaction expired, send a new message
                await interaction.followup.send(
                    "❌ Interaction expired. Please run `/manage` again.",
                    ephemeral=True
                )
            except Exception as e:
                print(f"Players Timezone error: {e}")
                import traceback
                traceback.print_exc()
                try:
                    await interaction.response.send_message(
                        "❌ An error occurred while loading Players Timezone.",
                        ephemeral=True
                    )
                except:
                    await interaction.followup.send(
                        "❌ An error occurred while loading Players Timezone.",
                        ephemeral=True
                    )
            return
        
        # Handle Set Player Timezone button
        if custom_id == "set_player_timezone":
            try:
                await interaction.response.defer(ephemeral=True)
                
                # Import adapters
                from db import mongo_adapters
                
                # Check MongoDB is enabled
                if not mongo_adapters.mongo_enabled() or not mongo_adapters.ServerAllianceAdapter or not mongo_adapters.AllianceMembersAdapter:
                    await interaction.followup.send(
                        "❌ MongoDB not enabled or required adapters not available.",
                        ephemeral=True
                    )
                    return
                
                # Get server's assigned alliance
                alliance_id = mongo_adapters.ServerAllianceAdapter.get_alliance(interaction.guild.id)
                
                if not alliance_id:
                    await interaction.followup.send(
                        "❌ No alliance assigned to this server. Please assign an alliance first using `/manage`.",
                        ephemeral=True
                    )
                    return
                
                # Get all alliance members
                all_members = mongo_adapters.AllianceMembersAdapter.get_all_members()
                members = [m for m in all_members if int(m.get('alliance', 0) or m.get('alliance_id', 0)) == alliance_id]
                
                if not members:
                    await interaction.followup.send(
                        "❌ No members found in this alliance.",
                        ephemeral=True
                    )
                    return
                
                # Sort members by nickname
                members.sort(key=lambda x: x.get('nickname', 'Unknown').lower())
                
                # Level mapping for furnace levels
                level_mapping = {
                    31: "30-1", 32: "30-2", 33: "30-3", 34: "30-4",
                    35: "1", 36: "1-(1)", 37: "1-(2)", 38: "1-(3)", 39: "1-(4)",
                    40: "2", 41: "2-(1)", 42: "2-(2)", 43: "2-(3)", 44: "2-(4)",
                    45: "3", 46: "3-(1)", 47: "3-(2)", 48: "3-(3)", 49: "3-(4)",
                    50: "4", 51: "4-(1)", 52: "4-(2)", 53: "4-(3)", 54: "4-(4)",
                    55: "5", 56: "5-(1)", 57: "5-(2)", 58: "5-(3)", 59: "5-(4)",
                    60: "6", 61: "6-(1)", 62: "6-(2)", 63: "6-(3)", 64: "6-(4)",
                    65: "7", 66: "7-(1)", 67: "7-(2)", 68: "7-(3)", 69: "7-(4)",
                    70: "8", 71: "8-(1)", 72: "8-(2)", 73: "8-(3)", 74: "8-(4)",
                    75: "9", 76: "9-(1)", 77: "9-(2)", 78: "9-(3)", 79: "9-(4)",
                    80: "10", 81: "10-(1)", 82: "10-(2)", 83: "10-(3)", 84: "10-(4)"
                }
                
                # Create paginated player list with buttons
                players_per_page = 10
                total_pages = (len(members) + players_per_page - 1) // players_per_page
                
                class PlayerSelectionView(discord.ui.View):
                    def __init__(self, members_list, current_page=0):
                        super().__init__(timeout=180)
                        self.members = members_list
                        self.current_page = current_page
                        self.total_pages = total_pages
                        
                        # Add player buttons for current page
                        start_idx = current_page * players_per_page
                        end_idx = min(start_idx + players_per_page, len(members_list))
                        
                        for idx in range(start_idx, end_idx):
                            member = members_list[idx]
                            nickname = member.get('nickname', 'Unknown')
                            fid = member.get('fid', 'N/A')
                            
                            button = discord.ui.Button(
                                label=f"{nickname[:40]}",
                                style=discord.ButtonStyle.secondary,
                                custom_id=f"select_player_{fid}",
                                row=(idx - start_idx) // 5  # 5 buttons per row
                            )
                            button.callback = lambda i, f=fid, n=nickname: self.player_selected(i, f, n)
                            self.add_item(button)
                        
                        # Add search button
                        search_button = discord.ui.Button(
                            label="🔍 Search Player",
                            style=discord.ButtonStyle.primary,
                            custom_id="search_player_tz",
                            row=3
                        )
                        search_button.callback = self.search_player
                        self.add_item(search_button)
                        
                        # Add navigation buttons if needed
                        if total_pages > 1:
                            if current_page > 0:
                                prev_button = discord.ui.Button(
                                    label="◀ Previous",
                                    style=discord.ButtonStyle.primary,
                                    custom_id="prev_page",
                                    row=4
                                )
                                prev_button.callback = self.previous_page
                                self.add_item(prev_button)
                            
                            if current_page < total_pages - 1:
                                next_button = discord.ui.Button(
                                    label="Next ▶",
                                    style=discord.ButtonStyle.primary,
                                    custom_id="next_page",
                                    row=4
                                )
                                next_button.callback = self.next_page
                                self.add_item(next_button)
                    
                    async def previous_page(self, button_interaction: discord.Interaction):
                        new_page = max(0, self.current_page - 1)
                        new_view = PlayerSelectionView(self.members, new_page)
                        embed = self.create_embed(new_page)
                        await button_interaction.response.edit_message(embed=embed, view=new_view)
                    
                    async def next_page(self, button_interaction: discord.Interaction):
                        new_page = min(self.total_pages - 1, self.current_page + 1)
                        new_view = PlayerSelectionView(self.members, new_page)
                        embed = self.create_embed(new_page)
                        await button_interaction.response.edit_message(embed=embed, view=new_view)
                    
                    def create_embed(self, page):
                        start_idx = page * players_per_page
                        end_idx = min(start_idx + players_per_page, len(self.members))
                        
                        embed = discord.Embed(
                            title="⏰ Set Player Timezone",
                            description=(
                                "```ansi\n"
                                "\u001b[2;36m╔═══════════════════════════════════╗\n"
                                "\u001b[2;36m║  \u001b[1;37mSELECT A PLAYER\u001b[0m\u001b[2;36m              ║\n"
                                "\u001b[2;36m╚═══════════════════════════════════╝\u001b[0m\n"
                                "```\n"
                                "**Click a button below to select a player:**\n\n"
                            ),
                            color=0x2B2D31
                        )
                        
                        # Add player list to embed
                        player_list = ""
                        for idx in range(start_idx, end_idx):
                            member = self.members[idx]
                            nickname = member.get('nickname', 'Unknown')
                            fid = member.get('fid', 'N/A')
                            furnace_lv = int(member.get('furnace_lv', 0) or 0)
                            level = level_mapping.get(furnace_lv, str(furnace_lv))
                            
                            player_list += f"**{idx + 1:02d}.** 👤 {nickname}\n└ 🆔 `{fid}` | ⚔️ `FC: {level}`\n\n"
                        
                        embed.description += player_list
                        
                        if self.total_pages > 1:
                            embed.set_footer(text=f"Page {page + 1}/{self.total_pages}")
                        
                        return embed
                    
                    async def search_player(self, search_interaction: discord.Interaction):
                        """Show search modal for finding players by name"""
                        class PlayerSearchModal(discord.ui.Modal, title="🔍 Search Player"):
                            search_input = discord.ui.TextInput(
                                label="Player Name",
                                placeholder="Enter player name to search...",
                                style=discord.TextStyle.short,
                                required=True,
                                min_length=1,
                                max_length=50
                            )
                            
                            def __init__(self, members_list, parent_view):
                                super().__init__()
                                self.members_list = members_list
                                self.parent_view = parent_view
                            
                            async def on_submit(self, modal_interaction: discord.Interaction):
                                search_term = self.search_input.value.lower()
                                
                                # Search for matching players
                                matches = [
                                    m for m in self.members_list 
                                    if search_term in m.get('nickname', '').lower()
                                ]
                                
                                if not matches:
                                    await modal_interaction.response.send_message(
                                        f"❌ No players found matching '{self.search_input.value}'",
                                        ephemeral=True
                                    )
                                    return
                                
                                if len(matches) == 1:
                                    # Only one match, show timezone selection directly
                                    player = matches[0]
                                    fid = player.get('fid', 'N/A')
                                    player_name = player.get('nickname', 'Unknown')
                                    
                                    # Create timezone selection view (max 25 options)
                                    # Note: Each option value must be unique
                                    timezone_options = [
                                        discord.SelectOption(label="UTC-12:00 (Baker Island)", value="UTC-12:00", emoji="🌍"),
                                        discord.SelectOption(label="UTC-11:00 (American Samoa)", value="UTC-11:00", emoji="🌍"),
                                        discord.SelectOption(label="UTC-10:00 (Hawaii)", value="UTC-10:00", emoji="🌺"),
                                        discord.SelectOption(label="UTC-09:00 (Alaska)", value="UTC-09:00", emoji="❄️"),
                                        discord.SelectOption(label="UTC-08:00 (PST - Los Angeles)", value="UTC-08:00", emoji="🌴"),
                                        discord.SelectOption(label="UTC-07:00 (MST - Denver)", value="UTC-07:00", emoji="🏔️"),
                                        discord.SelectOption(label="UTC-06:00 (CST - Chicago)", value="UTC-06:00", emoji="🌆"),
                                        discord.SelectOption(label="UTC-05:00 (EST - New York)", value="UTC-05:00", emoji="🗽"),
                                        discord.SelectOption(label="UTC-04:00 (Atlantic)", value="UTC-04:00", emoji="🌊"),
                                        discord.SelectOption(label="UTC-03:00 (Brazil)", value="UTC-03:00", emoji="🇧🇷"),
                                        discord.SelectOption(label="UTC-02:00 (Mid-Atlantic)", value="UTC-02:00", emoji="🌊"),
                                        discord.SelectOption(label="UTC-01:00 (Azores/Portugal)", value="UTC-01:00", emoji="🇵🇹"),
                                        discord.SelectOption(label="UTC+00:00 (GMT - UK/Ireland)", value="UTC+00:00", emoji="🇬🇧"),
                                        discord.SelectOption(label="UTC+01:00 (CET - France/Spain)", value="UTC+01:00", emoji="🇫🇷"),
                                        discord.SelectOption(label="UTC+01:00 (Germany/Italy)", value="UTC+01:00_DE", emoji="🇩🇪"),
                                        discord.SelectOption(label="UTC+01:00 (Netherlands/Belgium)", value="UTC+01:00_NL", emoji="🇳🇱"),
                                        discord.SelectOption(label="UTC+01:00 (Poland/Czech)", value="UTC+01:00_PL", emoji="🇵🇱"),
                                        discord.SelectOption(label="UTC+01:00 (Sweden/Norway)", value="UTC+01:00_SE", emoji="🇸🇪"),
                                        discord.SelectOption(label="UTC+01:00 (Switzerland/Austria)", value="UTC+01:00_CH", emoji="🇨🇭"),
                                        discord.SelectOption(label="UTC+02:00 (EET - Greece/Romania)", value="UTC+02:00", emoji="🇬🇷"),
                                        discord.SelectOption(label="UTC+02:00 (Finland/Estonia)", value="UTC+02:00_FI", emoji="🇫🇮"),
                                        discord.SelectOption(label="UTC+02:00 (Bulgaria/Ukraine)", value="UTC+02:00_BG", emoji="🇧🇬"),
                                        discord.SelectOption(label="UTC+03:00 (Moscow/Turkey)", value="UTC+03:00", emoji="🇷🇺"),
                                        discord.SelectOption(label="UTC+03:30 (Tehran)", value="UTC+03:30", emoji="🇮🇷"),
                                        discord.SelectOption(label="UTC+04:00 (Dubai/UAE)", value="UTC+04:00", emoji="🇦🇪"),
                                    ]
                                    
                                    timezone_select = discord.ui.Select(
                                        placeholder=f"Select timezone for {player_name}...",
                                        options=timezone_options,
                                        custom_id="timezone_select_search"
                                    )
                                    
                                    async def timezone_selected(tz_interaction: discord.Interaction):
                                        selected_timezone = tz_interaction.data['values'][0]
                                        
                                        # Set the timezone (keep country code for proper flag display)
                                        from db import mongo_adapters
                                        success = mongo_adapters.PlayerTimezonesAdapter.set(
                                            fid,
                                            selected_timezone,
                                            tz_interaction.user.id
                                        )
                                        
                                        if success:
                                            embed = discord.Embed(
                                                title="✅ Timezone Set Successfully",
                                                description=(
                                                    f"**Player:** {player_name}\n"
                                                    f"**FID:** `{fid}`\n"
                                                    f"**Timezone:** `{selected_timezone}`"
                                                ),
                                                color=0x57F287
                                            )
                                            embed.set_footer(
                                                text=f"Set by {tz_interaction.user.display_name}",
                                                icon_url=tz_interaction.user.display_avatar.url
                                            )
                                            await tz_interaction.response.edit_message(
                                                embed=embed,
                                                view=None
                                            )
                                        else:
                                            await tz_interaction.response.edit_message(
                                                content="❌ Failed to set timezone. Please try again.",
                                                view=None
                                            )
                                    
                                    timezone_select.callback = timezone_selected
                                    
                                    # Create view with timezone selection
                                    tz_view = discord.ui.View(timeout=180)
                                    tz_view.add_item(timezone_select)
                                    
                                    await modal_interaction.response.send_message(
                                        content=f"**Selected Player:** {player_name} (`{fid}`)\n\nSelect a timezone:",
                                        view=tz_view,
                                        ephemeral=True
                                    )
                                else:
                                    # Multiple matches, show selection
                                    embed = discord.Embed(
                                        title=f"🔍 Search Results for '{self.search_input.value}'",
                                        description=f"Found {len(matches)} matching players. Click a button to select:",
                                        color=0x2B2D31
                                    )
                                    
                                    view = discord.ui.View(timeout=180)
                                    for idx, member in enumerate(matches[:10]):  # Limit to 10 results
                                        nickname = member.get('nickname', 'Unknown')
                                        fid = member.get('fid', 'N/A')
                                        furnace_lv = int(member.get('furnace_lv', 0) or 0)
                                        level = level_mapping.get(furnace_lv, str(furnace_lv))
                                        
                                        button = discord.ui.Button(
                                            label=f"{nickname[:40]}",
                                            style=discord.ButtonStyle.secondary,
                                            row=idx // 5
                                        )
                                        button.callback = lambda i, f=fid, n=nickname: self.parent_view.player_selected(i, f, n)
                                        view.add_item(button)
                                    
                                    await modal_interaction.response.send_message(
                                        embed=embed,
                                        view=view,
                                        ephemeral=True
                                    )
                        
                        modal = PlayerSearchModal(self.members, self)
                        await search_interaction.response.send_modal(modal)
                    
                    async def player_selected(self, select_interaction: discord.Interaction, fid: str, player_name: str):
                        # Show timezone selection with comprehensive European options (max 25)
                        # Note: Each option value must be unique, so we use the actual timezone as value
                        timezone_options = [
                            discord.SelectOption(label="UTC-12:00 (Baker Island)", value="UTC-12:00", emoji="🌍"),
                            discord.SelectOption(label="UTC-11:00 (American Samoa)", value="UTC-11:00", emoji="🌍"),
                            discord.SelectOption(label="UTC-10:00 (Hawaii)", value="UTC-10:00", emoji="🌺"),
                            discord.SelectOption(label="UTC-09:00 (Alaska)", value="UTC-09:00", emoji="❄️"),
                            discord.SelectOption(label="UTC-08:00 (PST - Los Angeles)", value="UTC-08:00", emoji="🌴"),
                            discord.SelectOption(label="UTC-07:00 (MST - Denver)", value="UTC-07:00", emoji="🏔️"),
                            discord.SelectOption(label="UTC-06:00 (CST - Chicago)", value="UTC-06:00", emoji="🌆"),
                            discord.SelectOption(label="UTC-05:00 (EST - New York)", value="UTC-05:00", emoji="🗽"),
                            discord.SelectOption(label="UTC-04:00 (Atlantic)", value="UTC-04:00", emoji="🌊"),
                            discord.SelectOption(label="UTC-03:00 (Brazil)", value="UTC-03:00", emoji="🇧🇷"),
                            discord.SelectOption(label="UTC-02:00 (Mid-Atlantic)", value="UTC-02:00", emoji="🌊"),
                            discord.SelectOption(label="UTC-01:00 (Azores/Portugal)", value="UTC-01:00", emoji="🇵🇹"),
                            discord.SelectOption(label="UTC+00:00 (GMT - UK/Ireland)", value="UTC+00:00", emoji="🇬🇧"),
                            discord.SelectOption(label="UTC+01:00 (CET - France/Spain)", value="UTC+01:00", emoji="🇫🇷"),
                            discord.SelectOption(label="UTC+01:00 (Germany/Italy)", value="UTC+01:00_DE", emoji="🇩🇪"),
                            discord.SelectOption(label="UTC+01:00 (Netherlands/Belgium)", value="UTC+01:00_NL", emoji="🇳🇱"),
                            discord.SelectOption(label="UTC+01:00 (Poland/Czech)", value="UTC+01:00_PL", emoji="🇵🇱"),
                            discord.SelectOption(label="UTC+01:00 (Sweden/Norway)", value="UTC+01:00_SE", emoji="🇸🇪"),
                            discord.SelectOption(label="UTC+01:00 (Switzerland/Austria)", value="UTC+01:00_CH", emoji="🇨🇭"),
                            discord.SelectOption(label="UTC+02:00 (EET - Greece/Romania)", value="UTC+02:00", emoji="🇬🇷"),
                            discord.SelectOption(label="UTC+02:00 (Finland/Estonia)", value="UTC+02:00_FI", emoji="🇫🇮"),
                            discord.SelectOption(label="UTC+02:00 (Bulgaria/Ukraine)", value="UTC+02:00_BG", emoji="🇧🇬"),
                            discord.SelectOption(label="UTC+03:00 (Moscow/Turkey)", value="UTC+03:00", emoji="🇷🇺"),
                            discord.SelectOption(label="UTC+03:30 (Tehran)", value="UTC+03:30", emoji="🇮🇷"),
                            discord.SelectOption(label="UTC+04:00 (Dubai/UAE)", value="UTC+04:00", emoji="🇦🇪"),
                        ]
                        
                        timezone_select = discord.ui.Select(
                            placeholder=f"Select timezone for {player_name}...",
                            options=timezone_options,
                            custom_id="timezone_select"
                        )
                        
                        async def timezone_selected(tz_interaction: discord.Interaction):
                            selected_timezone = tz_interaction.data['values'][0]
                            
                            # Set the timezone (keep country code for proper flag display)
                            from db import mongo_adapters
                            success = mongo_adapters.PlayerTimezonesAdapter.set(
                                fid,
                                selected_timezone,
                                tz_interaction.user.id
                            )
                            
                            if success:
                                embed = discord.Embed(
                                    title="✅ Timezone Set Successfully",
                                    description=(
                                        f"**Player:** {player_name}\n"
                                        f"**FID:** `{fid}`\n"
                                        f"**Timezone:** `{selected_timezone}`"
                                    ),
                                    color=0x57F287
                                )
                                embed.set_footer(
                                    text=f"Set by {tz_interaction.user.display_name}",
                                    icon_url=tz_interaction.user.display_avatar.url
                                )
                                await tz_interaction.response.edit_message(
                                    embed=embed,
                                    view=None
                                )
                            else:
                                await tz_interaction.response.edit_message(
                                    content="❌ Failed to set timezone. Please try again.",
                                    view=None
                                )
                        
                        timezone_select.callback = timezone_selected
                        
                        # Create new view with timezone selection
                        new_view = discord.ui.View(timeout=180)
                        new_view.add_item(timezone_select)
                        
                        # Add "More Timezones" button for additional options
                        more_tz_button = discord.ui.Button(
                            label="More Timezones",
                            emoji="🌏",
                            style=discord.ButtonStyle.secondary,
                            custom_id="more_timezones"
                        )
                        
                        async def show_more_timezones(button_interaction: discord.Interaction):
                            # Additional timezone options (Asia, Pacific, Oceania)
                            more_timezone_options = [
                                discord.SelectOption(label="UTC+04:30 (Kabul)", value="UTC+04:30", emoji="🇦🇫"),
                                discord.SelectOption(label="UTC+05:00 (Pakistan)", value="UTC+05:00", emoji="🇵🇰"),
                                discord.SelectOption(label="UTC+05:30 (India/Sri Lanka)", value="UTC+05:30", emoji="🇮🇳"),
                                discord.SelectOption(label="UTC+05:45 (Nepal)", value="UTC+05:45", emoji="🇳🇵"),
                                discord.SelectOption(label="UTC+06:00 (Bangladesh)", value="UTC+06:00", emoji="🇧🇩"),
                                discord.SelectOption(label="UTC+06:30 (Myanmar)", value="UTC+06:30", emoji="🇲🇲"),
                                discord.SelectOption(label="UTC+07:00 (Thailand/Vietnam)", value="UTC+07:00", emoji="🇹🇭"),
                                discord.SelectOption(label="UTC+08:00 (China/Singapore)", value="UTC+08:00", emoji="🇨🇳"),
                                discord.SelectOption(label="UTC+08:45 (Eucla)", value="UTC+08:45", emoji="🇦🇺"),
                                discord.SelectOption(label="UTC+09:00 (Japan/Korea)", value="UTC+09:00", emoji="🇯🇵"),
                                discord.SelectOption(label="UTC+09:30 (Adelaide)", value="UTC+09:30", emoji="🇦🇺"),
                                discord.SelectOption(label="UTC+10:00 (Sydney/Brisbane)", value="UTC+10:00", emoji="🇦🇺"),
                                discord.SelectOption(label="UTC+10:30 (Lord Howe)", value="UTC+10:30", emoji="🏝️"),
                                discord.SelectOption(label="UTC+11:00 (Solomon Islands)", value="UTC+11:00", emoji="🏝️"),
                                discord.SelectOption(label="UTC+12:00 (New Zealand)", value="UTC+12:00", emoji="🇳🇿"),
                                discord.SelectOption(label="UTC+12:45 (Chatham Islands)", value="UTC+12:45", emoji="🏝️"),
                                discord.SelectOption(label="UTC+13:00 (Tonga)", value="UTC+13:00", emoji="🇹🇴"),
                                discord.SelectOption(label="UTC+14:00 (Kiribati)", value="UTC+14:00", emoji="🏝️"),
                                discord.SelectOption(label="🚀 MARS TIME (Sol +39m 35s)", value="MARS", emoji="🔴"),
                            ]
                            
                            tz_select = discord.ui.Select(
                                placeholder=f"Select timezone for {player_name}...",
                                options=more_timezone_options,
                                custom_id="timezone_select_more"
                            )
                            tz_select.callback = timezone_selected
                            
                            more_view = discord.ui.View(timeout=180)
                            more_view.add_item(tz_select)
                            
                            # Add back button
                            back_button = discord.ui.Button(
                                label="◀ Back",
                                style=discord.ButtonStyle.secondary,
                                custom_id="back_to_main_tz"
                            )
                            
                            async def go_back(back_interaction: discord.Interaction):
                                await back_interaction.response.edit_message(
                                    content=f"**Selected Player:** {player_name} (`{fid}`)\n\nSelect a timezone:",
                                    view=new_view
                                )
                            
                            back_button.callback = go_back
                            more_view.add_item(back_button)
                            
                            await button_interaction.response.edit_message(
                                content=f"**Selected Player:** {player_name} (`{fid}`)\n\nSelect a timezone (More options):",
                                view=more_view
                            )
                        
                        more_tz_button.callback = show_more_timezones
                        new_view.add_item(more_tz_button)
                        
                        await select_interaction.response.edit_message(
                            content=f"**Selected Player:** {player_name} (`{fid}`)\n\nSelect a timezone:",
                            view=new_view
                        )
                
                # Create and send the initial player selection view
                view = PlayerSelectionView(members, 0)
                embed = view.create_embed(0)
                
                await interaction.followup.send(
                    embed=embed,
                    view=view,
                    ephemeral=True
                )
                
            except Exception as e:
                print(f"Set Player Timezone error: {e}")
                import traceback
                traceback.print_exc()
                try:
                    await interaction.followup.send(
                        "❌ An error occurred while setting up timezone selection.",
                        ephemeral=True
                    )
                except:
                    pass
            return
        
        # Handle View Members (Timezone) button
        if custom_id == "timezone_view_members":
            try:
                await interaction.response.defer(ephemeral=True)
                
                # Import adapters to avoid scope issues with local imports elsewhere
                from db import mongo_adapters
                
                # Check MongoDB is enabled
                if not mongo_adapters.mongo_enabled() or not mongo_adapters.ServerAllianceAdapter or not mongo_adapters.AllianceMembersAdapter or not mongo_adapters.PlayerTimezonesAdapter:
                    await interaction.followup.send(
                        "❌ MongoDB not enabled or required adapters not available.",
                        ephemeral=True
                    )
                    return
                
                # Get server's assigned alliance
                alliance_id = mongo_adapters.ServerAllianceAdapter.get_alliance(interaction.guild.id)
                
                if not alliance_id:
                    await interaction.followup.send(
                        "❌ No alliance assigned to this server. Please assign an alliance first using `/manage`.",
                        ephemeral=True
                    )
                    return
                
                # Get all alliance members
                all_members = mongo_adapters.AllianceMembersAdapter.get_all_members()
                members = [m for m in all_members if int(m.get('alliance', 0) or m.get('alliance_id', 0)) == alliance_id]
                
                if not members:
                    await interaction.followup.send(
                        "❌ No members found in this alliance.",
                        ephemeral=True
                    )
                    return
                
                # Get all player timezones
                player_timezones = mongo_adapters.PlayerTimezonesAdapter.get_all()
                
                # Level mapping for furnace levels
                level_mapping = {
                    31: "30-1", 32: "30-2", 33: "30-3", 34: "30-4",
                    35: "1", 36: "1-(1)", 37: "1-(2)", 38: "1-(3)", 39: "1-(4)",
                    40: "2", 41: "2-(1)", 42: "2-(2)", 43: "2-(3)", 44: "2-(4)",
                    45: "3", 46: "3-(1)", 47: "3-(2)", 48: "3-(3)", 49: "3-(4)",
                    50: "4", 51: "4-(1)", 52: "4-(2)", 53: "4-(3)", 54: "4-(4)",
                    55: "5", 56: "5-(1)", 57: "5-(2)", 58: "5-(3)", 59: "5-(4)",
                    60: "6", 61: "6-(1)", 62: "6-(2)", 63: "6-(3)", 64: "6-(4)",
                    65: "7", 66: "7-(1)", 67: "7-(2)", 68: "7-(3)", 69: "7-(4)",
                    70: "8", 71: "8-(1)", 72: "8-(2)", 73: "8-(3)", 74: "8-(4)",
                    75: "9", 76: "9-(1)", 77: "9-(2)", 78: "9-(3)", 79: "9-(4)",
                    80: "10", 81: "10-(1)", 82: "10-(2)", 83: "10-(3)", 84: "10-(4)"
                }
                
                # Sort members by furnace level (descending)
                members.sort(key=lambda x: int(x.get('furnace_lv', 0) or 0), reverse=True)
                
                # Calculate statistics
                max_fl = max(int(m.get('furnace_lv', 0) or 0) for m in members)
                avg_fl = sum(int(m.get('furnace_lv', 0) or 0) for m in members) / len(members)
                
                
                # Get alliance name
                alliance_name = "Alliance"  # Default
                try:
                    from db import mongo_adapters
                    alliance_doc = mongo_adapters.AlliancesAdapter.get_all()
                    for ally in alliance_doc:
                        if int(ally.get('alliance_id', 0)) == alliance_id:
                            alliance_name = ally.get('name', 'Alliance')
                            break
                except:
                    pass
                
                # Get current UTC time
                from datetime import datetime, timezone
                utc_now = datetime.now(timezone.utc)
                utc_time_str = utc_now.strftime("%H:%M:%S")
                utc_date_str = utc_now.strftime("%Y-%m-%d")
                utc_day_str = utc_now.strftime("%A")
                
                # Create base embed with real-time UTC clock
                base_embed = discord.Embed(
                    title=f"🌍 {alliance_name} - Member Timezones",
                    description=(
                        "```ml\n"
                        "Current UTC Time\n"
                        "══════════════════════════\n"
                        f"🕐 Time    : {utc_time_str} UTC\n"
                        f"📅 Date    : {utc_date_str}\n"
                        f"📆 Day     : {utc_day_str}\n"
                        "══════════════════════════\n"
                        "```\n"
                        "**Member List with Timezones**\n"
                        "━━━━━━━━━━━━━━━━━━━━━━\n"
                    ),
                    color=discord.Color.blue()
                )
                
                # Set author with custom icon
                base_embed.set_author(
                    name="World Clock",
                    icon_url="https://cdn.discordapp.com/attachments/1435569370389807144/1447792789843607724/dENyFQfxU7gSyQ36Y9Nf--1--ncrgz-2.jpg"
                )
                
                # Create member list
                members_per_page = 15
                member_chunks = [members[i:i + members_per_page] for i in range(0, len(members), members_per_page)]
                embeds = []
                
                # Thumbnail URLs
                map_thumbnail = "https://cdn.discordapp.com/attachments/1435569370389807144/1447677334961393785/map.1762081200.png"
                clock_thumbnail = "https://cdn.discordapp.com/attachments/1435569370389807144/1447677682157617202/wired-flat-45-clock-time.gif"
                
                # Timezone to flag emoji mapping
                timezone_flags = {
                    'UTC-12:00': '🏝️', 'UTC-11:00': '🇦🇸', 'UTC-10:00': '🇺🇸',
                    'UTC-09:00': '🇺🇸', 'UTC-08:00': '🇺🇸', 'UTC-07:00': '🇺🇸',
                    'UTC-06:00': '🇺🇸', 'UTC-05:00': '🇺🇸', 'UTC-04:00': '🇨🇦',
                    'UTC-03:00': '🇧🇷', 'UTC-02:00': '🌊', 'UTC-01:00': '🇵🇹',
                    'UTC+00:00': '🇬🇧', 
                    'UTC+01:00': '🇫🇷',  # France/Spain (default for UTC+01:00)
                    'UTC+01:00_DE': '🇩🇪',  # Germany/Italy
                    'UTC+01:00_NL': '🇳🇱',  # Netherlands/Belgium
                    'UTC+01:00_PL': '🇵🇱',  # Poland/Czech
                    'UTC+01:00_SE': '🇸🇪',  # Sweden/Norway
                    'UTC+01:00_CH': '🇨🇭',  # Switzerland/Austria
                    'UTC+02:00': '🇪🇬',  # Greece/Romania (default for UTC+02:00)
                    'UTC+02:00_FI': '🇫🇮',  # Finland/Estonia
                    'UTC+02:00_BG': '🇧🇬',  # Bulgaria/Ukraine
                    'UTC+03:00': '🇷🇺', 'UTC+03:30': '🇮🇷', 'UTC+04:00': '🇦🇪',
                    'UTC+04:30': '🇦🇫', 'UTC+05:00': '🇵🇰', 'UTC+05:30': '🇮🇳',
                    'UTC+05:45': '🇳🇵', 'UTC+06:00': '🇧🇩', 'UTC+06:30': '🇲🇲',
                    'UTC+07:00': '🇹🇭', 'UTC+08:00': '🇨🇳', 'UTC+08:45': '🇦🇺',
                    'UTC+09:00': '🇯🇵', 'UTC+09:30': '🇦🇺', 'UTC+10:00': '🇦🇺',
                    'UTC+10:30': '🇦🇺', 'UTC+11:00': '🏝️', 'UTC+12:00': '🇳🇿',
                    'UTC+12:45': '🇳🇿', 'UTC+13:00': '🇹🇴', 'UTC+14:00': '🏝️',
                    'MARS': '🔴'  # Mars timezone
                }
                
                for page, chunk in enumerate(member_chunks):
                    embed = base_embed.copy()
                    
                    member_list = ""
                    for idx, member in enumerate(chunk, start=page * members_per_page + 1):
                        nickname = member.get('nickname', 'Unknown')
                        fid = member.get('fid', 'N/A')
                        furnace_lv = int(member.get('furnace_lv', 0) or 0)
                        level = level_mapping.get(furnace_lv, str(furnace_lv))
                        
                        # Get timezone for this player
                        timezone = player_timezones.get(fid, 'Unknown')
                        
                        # Get flag emoji for timezone
                        tz_flag = timezone_flags.get(timezone, '🌍')
                        
                        member_list += f"**{idx:02d}.** 👤 {nickname}\n└ 🆔 `ID: {fid}` | ⚔️ `FC: {level}` | {tz_flag} `{timezone}`\n\n"
                    
                    embed.description += member_list
                    
                    # Add thumbnails (alternate between map and clock)
                    if page % 2 == 0:
                        embed.set_thumbnail(url=map_thumbnail)
                    else:
                        embed.set_thumbnail(url=clock_thumbnail)
                    
                    if len(member_chunks) > 1:
                        embed.set_footer(text=f"Page {page + 1}/{len(member_chunks)}")
                    
                    embeds.append(embed)
                
                
                
                # Create a persistent view with stop/start button for the live clock
                class LiveClockView(discord.ui.View):
                    def __init__(self, message=None, embeds=None, member_chunks=None):
                        super().__init__(timeout=None)
                        self.is_running = True
                        self.message = message
                        self.embeds = embeds
                        self.member_chunks = member_chunks
                        self.update_task = None
                    
                    @discord.ui.button(label="⏸️ Stop Clock", style=discord.ButtonStyle.danger, custom_id="timezone_stop_live_clock")
                    async def stop_clock(self, button_interaction: discord.Interaction, button: discord.ui.Button):
                        self.is_running = False
                        
                        # Update button to show resume option
                        button.label = "▶️ Start Clock"
                        button.style = discord.ButtonStyle.success
                        button.disabled = False
                        
                        # Change custom_id to start button
                        for item in self.children:
                            if isinstance(item, discord.ui.Button) and item.custom_id == "timezone_stop_live_clock":
                                self.remove_item(item)
                                break
                        
                        # Add start button
                        start_button = discord.ui.Button(
                            label="▶️ Start Clock",
                            style=discord.ButtonStyle.success,
                            custom_id="timezone_start_live_clock"
                        )
                        start_button.callback = self.start_clock
                        self.add_item(start_button)
                        
                        await button_interaction.response.edit_message(view=self)
                    
                    async def start_clock(self, button_interaction: discord.Interaction):
                        self.is_running = True
                        
                        # Remove start button and add stop button
                        for item in self.children:
                            if isinstance(item, discord.ui.Button) and item.custom_id == "timezone_start_live_clock":
                                self.remove_item(item)
                                break
                        
                        stop_button = discord.ui.Button(
                            label="⏸️ Stop Clock",
                            style=discord.ButtonStyle.danger,
                            custom_id="timezone_stop_live_clock"
                        )
                        stop_button.callback = self.stop_clock
                        self.add_item(stop_button)
                        
                        await button_interaction.response.edit_message(view=self)
                        
                        # Restart the clock update task
                        if self.message and self.embeds and self.member_chunks:
                            self.update_task = button_interaction.client.loop.create_task(
                                update_clock_embed(self.message, self.embeds, self, self.member_chunks, pagination_view=None)
                            )
                
                async def update_clock_embed(message, embeds, view, member_chunks, pagination_view=None):
                    """Update the clock embed every second"""
                    import asyncio
                    from datetime import datetime, timezone
                    
                    while view.is_running:
                        try:
                            # Get current page from pagination view if available
                            current_page = 0
                            if pagination_view and hasattr(pagination_view, 'current_page'):
                                current_page = pagination_view.current_page
                            
                            # Get current UTC time
                            utc_now = datetime.now(timezone.utc)
                            utc_time_str = utc_now.strftime("%H:%M:%S")
                            utc_date_str = utc_now.strftime("%Y-%m-%d")
                            utc_day_str = utc_now.strftime("%A")
                            
                            # Update the embed description with new time
                            for page, chunk in enumerate(member_chunks):
                                embed = embeds[page]
                                
                                # Rebuild description with updated time
                                member_list = ""
                                for idx, member in enumerate(chunk, start=page * members_per_page + 1):
                                    nickname = member.get('nickname', 'Unknown')
                                    fid = member.get('fid', 'N/A')
                                    furnace_lv = int(member.get('furnace_lv', 0) or 0)
                                    level = level_mapping.get(furnace_lv, str(furnace_lv))
                                    
                                    # Get timezone for this player
                                    timezone_val = player_timezones.get(fid, 'Unknown')
                                    
                                    # Get flag emoji for timezone
                                    tz_flag = timezone_flags.get(timezone_val, '🌍')
                                    
                                    member_list += f"**{idx:02d}.** 👤 {nickname}\n└ 🆔 `ID: {fid}` | ⚔️ `FC: {level}` | {tz_flag} `{timezone_val}`\n\n"
                                
                                embed.description = (
                                    "```ml\n"
                                    "Current UTC Time\n"
                                    "══════════════════════════\n"
                                    f"🕐 Time    : {utc_time_str} UTC\n"
                                    f"📅 Date    : {utc_date_str}\n"
                                    f"📆 Day     : {utc_day_str}\n"
                                    "══════════════════════════\n"
                                    "```\n"
                                    "**Member List with Timezones**\n"
                                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                                    + member_list
                                )
                            
                            # Update only the current page
                            await message.edit(embed=embeds[current_page])
                            
                            # Wait 10 seconds before next update (prevents Discord rate limiting)
                            # Discord allows max 5 requests per 5 seconds, so 10 seconds is safe
                            await asyncio.sleep(10)
                            
                        except discord.errors.NotFound:
                            # Message was deleted, stop updating
                            view.is_running = False
                            break
                        except discord.errors.HTTPException as e:
                            if e.status == 429:  # Rate limited
                                print(f"Clock update rate limited. Stopping clock to prevent further issues.")
                                view.is_running = False
                                break
                            print(f"Clock update HTTP error: {e}")
                            await asyncio.sleep(10)
                        except Exception as e:
                            print(f"Clock update error: {e}")
                            await asyncio.sleep(10)
                
                # Send without profile button - just use pagination if needed
                # Post publicly to channel instead of ephemeral
                if len(embeds) > 1:
                    from cogs.pagination_helper import ResultsPaginationView
                    
                    # Create custom pagination view that works with live clock
                    class LiveClockPaginationView(ResultsPaginationView):
                        def __init__(self, embeds, author_id, live_view):
                            super().__init__(embeds, author_id, timeout=None)
                            self.live_view = live_view
                            # Add stop button
                            self.add_item(live_view.children[0])
                    
                    # Acknowledge the interaction first
                    await interaction.followup.send(
                        "✅ Member timezone list with live UTC clock has been generated and posted below.",
                        ephemeral=True
                    )
                    
                    # Post publicly to channel with temporary view
                    temp_view = discord.ui.View(timeout=None)
                    message = await interaction.channel.send(embed=embeds[0], view=temp_view)
                    
                    # Now create live view with message reference
                    live_view = LiveClockView(message, embeds, member_chunks)
                    view = LiveClockPaginationView(embeds, author_id=interaction.user.id, live_view=live_view)
                    view.message = message
                    
                    # Update message with proper view
                    await message.edit(view=view)
                    
                    # Start the clock update task with pagination view reference
                    self.bot.loop.create_task(update_clock_embed(message, embeds, live_view, member_chunks, pagination_view=view))
                else:
                    # Acknowledge the interaction first
                    await interaction.followup.send(
                        "✅ Member timezone list with live UTC clock has been generated and posted below.",
                        ephemeral=True
                    )
                    
                    # Post publicly to channel with temporary view
                    temp_view = discord.ui.View(timeout=None)
                    message = await interaction.channel.send(embed=embeds[0], view=temp_view)
                    
                    # Now create live view with message reference
                    live_view = LiveClockView(message, embeds, member_chunks)
                    
                    # Update message with proper view
                    await message.edit(view=live_view)
                    
                    # Start the clock update task (no pagination for single page)
                    self.bot.loop.create_task(update_clock_embed(message, embeds, live_view, member_chunks, pagination_view=None))
                    
            except Exception as e:
                print(f"View Members Timezone error: {e}")
                import traceback
                traceback.print_exc()
                try:
                    await interaction.followup.send(
                        "❌ An error occurred while loading member timezones.",
                        ephemeral=True
                    )
                except:
                    pass
            return
        
        if custom_id == "records_menu":
            # Check if user has valid authentication session
            try:
                if not mongo_enabled() or not ServerAllianceAdapter or not AuthSessionsAdapter:
                    await interaction.response.send_message(
                        "❌ MongoDB not enabled. Cannot access Records.",
                        ephemeral=True
                    )
                    return
                
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
                
                # Check if user has valid session
                if not AuthSessionsAdapter.is_session_valid(
                    interaction.guild.id,
                    interaction.user.id,
                    stored_password
                ):
                    await interaction.response.send_message(
                        "❌ Authentication required. Please use `/manage` to authenticate first.",
                        ephemeral=True
                    )
                    return

                # Create Records submenu
                embed = discord.Embed(
                    title="📁 Records Management",
                    description=(
                        "```ansi\n"
                        "\u001b[2;36m╔═══════════════════════════════════╗\n"
                        "\u001b[2;36m║  \u001b[1;37mCUSTOM PLAYER RECORDS\u001b[0m\u001b[2;36m          ║\n"
                        "\u001b[2;36m╚═══════════════════════════════════╝\u001b[0m\n"
                        "```\n"
                        "**Organize players into custom groups**\n\n"
                        "Perfect for tracking:\n"
                        "• Players who violate rules\n"
                        "• Farm Accounts\n"
                        "• Bear Trap teams\n"
                        "• Special Operations\n"
                    ),
                    color=0x2B2D31
                )
                embed.set_footer(
                    text=f"{interaction.guild.name} x Magnus🚀",
                    icon_url="https://cdn.discordapp.com/attachments/1435569370389807144/1436745053442805830/unnamed_5.png"
                )

                view = discord.ui.View()
                view.add_item(discord.ui.Button(
                    label="Create Record",
                    emoji="📝",
                    style=discord.ButtonStyle.secondary,
                    custom_id="record_create",
                    row=0
                ))
                view.add_item(discord.ui.Button(
                    label="Add Members",
                    emoji="➕",
                    style=discord.ButtonStyle.secondary,
                    custom_id="record_add",
                    row=0
                ))
                view.add_item(discord.ui.Button(
                    label="Remove Members",
                    emoji="➖",
                    style=discord.ButtonStyle.secondary,
                    custom_id="record_remove",
                    row=0
                ))
                view.add_item(discord.ui.Button(
                    label="View Record",
                    emoji="👁️",
                    style=discord.ButtonStyle.secondary,
                    custom_id="record_view",
                    row=1
                ))
                view.add_item(discord.ui.Button(
                    label="List Records",
                    emoji="📋",
                    style=discord.ButtonStyle.secondary,
                    custom_id="record_list",
                    row=1
                ))
                view.add_item(discord.ui.Button(
                    label="Rename Record",
                    emoji="✏️",
                    style=discord.ButtonStyle.secondary,
                    custom_id="record_rename",
                    row=1
                ))
                view.add_item(discord.ui.Button(
                    label="Delete Record",
                    emoji="🗑️",
                    style=discord.ButtonStyle.secondary,
                    custom_id="record_delete",
                    row=2
                ))
                view.add_item(discord.ui.Button(
                    label="Custom Columns",
                    emoji="⚙️",
                    style=discord.ButtonStyle.secondary,
                    custom_id="record_columns",
                    row=2
                ))
                view.add_item(discord.ui.Button(
                    label="◀ Back",
                    emoji="🏠",
                    style=discord.ButtonStyle.secondary,
                    custom_id="return_to_manage",
                    row=2
                ))

                await interaction.response.edit_message(embed=embed, view=view)
                return
            except Exception as e:
                print(f"Records menu error: {e}")
                await interaction.response.send_message(
                    "❌ An error occurred while opening Records menu.",
                    ephemeral=True
                )
                return
        
        # Handle back buttons to return to management dashboard
        if custom_id in ["back_to_manage", "return_to_manage"]:
            try:
                # Truncate username if too long for the ANSI box
                user_display = interaction.user.display_name[:15] if len(interaction.user.display_name) > 15 else interaction.user.display_name
                user_id_str = str(interaction.user.id)
                
                # Recreate the management dashboard
                embed = discord.Embed(
                    title="⚙️ Dashboard",
                    description=(
                        "```ansi\n"
                        "\u001b[0;33m    ✦\u001b[0m      \u001b[2;37m·\u001b[0m    \u001b[0;33m✧\u001b[0m     \u001b[2;37m·\u001b[0m\n"
                        "\u001b[0;36m  ╔═══════════════════════╗\n"
                        "\u001b[0;36m  ║ \u001b[1;37m◢◣ CONTROL PANEL ◢◣\u001b[0m\u001b[0;36m ║\n"
                        "\u001b[0;36m  ╠═══════════════════════╣\n"
                        f"\u001b[0;36m  ║ \u001b[1;35m👤\u001b[0m USER: \u001b[1;37m{user_display:<15}\u001b[0m\u001b[0;36m║\n"
                        f"\u001b[0;36m  ║ \u001b[1;35m🆔\u001b[0m ID: \u001b[0;37m{user_id_str:<17}\u001b[0m\u001b[0;36m║\n"
                        "\u001b[0;36m  ╠═══════════════════════╣\n"
                        "\u001b[0;36m  ║ \u001b[1;32m▸\u001b[0m STATUS: \u001b[1;32mONLINE\u001b[0m     \u001b[0;36m ║\n"
                        "\u001b[0;36m  ╚═══════════════════════╝\n"
                        "\u001b[2;37m    ·\u001b[0m   \u001b[0;33m✦\u001b[0m      \u001b[2;37m·\u001b[0m   \u001b[0;33m✧\u001b[0m\u001b[0m\n"
                        "```\n"
                        "**Select an operation to continue:**\n\n"
                        "👥 **Member Operations**\n"
                        "   ▸ Manage alliance members\n"
                        "   ▸  Update Alliance log\n\n"
                        "📁 **Records Management**\n"
                        "   ▸ Keep track of Players\n"
                        "   ▸ Create and manage groups\n\n"
                        "🎁 **Gift Code Management**\n"
                        "   ▸ Configure gift code channels\n"
                        "   ▸ Add and manage gift codes\n\n"
                        "🏰 **Alliance Monitor**\n"
                        "   ▸ Track member changes\n"
                        "   ▸ Monitor furnace levels\n\n"
                        "🔮 **Other Features**\n"
                        "   ▸ More features coming soon\n"
                    ),
                    color=0x2B2D31
                )
                embed.set_thumbnail(url=interaction.user.display_avatar.url)
                embed.add_field(
                    name="🎮 Current Operator",
                    value=f"{interaction.user.mention}",
                    inline=True
                )
                embed.set_footer(
                    text=f"{interaction.guild.name} x Magnus🚀",
                    icon_url="https://cdn.discordapp.com/attachments/1435569370389807144/1436745053442805830/unnamed_5.png"
                )

                view = discord.ui.View()
                view.add_item(discord.ui.Button(
                    label="Member Operations",
                    emoji="👥",
                    style=discord.ButtonStyle.secondary,
                    custom_id="manage_member_ops",
                    row=0
                ))
                view.add_item(discord.ui.Button(
                    label="Records",
                    emoji="📁",
                    style=discord.ButtonStyle.secondary,
                    custom_id="records_menu",
                    row=0
                ))
                view.add_item(discord.ui.Button(
                    label="Gift Codes",
                    emoji="🎁",
                    style=discord.ButtonStyle.secondary,
                    custom_id="giftcode_menu",
                    row=0
                ))
                view.add_item(discord.ui.Button(
                    label="Alliance Monitor",
                    emoji="🏰",
                    style=discord.ButtonStyle.secondary,
                    custom_id="manage_alliance_monitor",
                    row=1
                ))
                view.add_item(discord.ui.Button(
                    label="Other Features",
                    emoji="🔮",
                    style=discord.ButtonStyle.secondary,
                    custom_id="manage_other_features",
                    row=1
                ))

                await interaction.response.edit_message(embed=embed, view=view)
                return
            except Exception as e:
                print(f"Back button error: {e}")
                await interaction.response.send_message(
                    "❌ An error occurred.",
                    ephemeral=True
                )
                return
        
        # Record operation handlers
        if custom_id in ["record_create", "record_add", "record_remove", "record_view", "record_list", "record_rename", "record_delete", "record_columns"]:
            # Import LoginHandler
            try:
                from cogs.login_handler import LoginHandler
            except Exception as e:
                await interaction.response.send_message(
                    f"❌ Error loading required modules: {e}",
                    ephemeral=True
                )
                return
            
            # Check if user has valid authentication session
            if not mongo_enabled() or not ServerAllianceAdapter or not AuthSessionsAdapter:
                await interaction.response.send_message(
                    "❌ MongoDB not enabled. Cannot access Records.",
                    ephemeral=True
                )
                return
            
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
            
            # Check if user has valid session
            if not AuthSessionsAdapter.is_session_valid(
                interaction.guild.id,
                interaction.user.id,
                stored_password
            ):
                await interaction.response.send_message(
                    "❌ Authentication required. Please use `/manage` to authenticate first.",
                    ephemeral=True
                )
                return
            
            # Handle record_list (no modal needed)
            if custom_id == "record_list":
                records = RecordsAdapter.get_all_records(interaction.guild.id)
                
                if not records:
                    await interaction.response.send_message(
                        "📋 No records found. Use **Create Record** to create one!",
                        ephemeral=True
                    )
                    return
                
                embed = discord.Embed(
                    title="📋 Custom Records",
                    description=f"Total Records: **{len(records)}**\n━━━━━━━━━━━━━━━━━━━━━━",
                    color=0x5865F2
                )
                
                for record in records[:25]:
                    member_count = record.get('member_count', 0)
                    created_at = record.get('created_at', 'Unknown')[:10]
                    
                    embed.add_field(
                        name=f"📁 {record['record_name']}",
                        value=f"👥 Members: `{member_count}`\n📅 Created: `{created_at}`",
                        inline=True
                    )
                
                if len(records) > 25:
                    embed.set_footer(text=f"Showing 25 of {len(records)} records")
                
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
            
            # Handle record_create
            if custom_id == "record_create":
                class CreateRecordModal(discord.ui.Modal, title="Create New Record"):
                    record_name = discord.ui.TextInput(
                        label="Record Name",
                        placeholder="e.g., KE Team, Farm Accounts",
                        required=True,
                        max_length=50
                    )
                    
                    async def on_submit(self, modal_interaction: discord.Interaction):
                        name = self.record_name.value.strip()
                        
                        success = RecordsAdapter.create_record(
                            guild_id=modal_interaction.guild.id,
                            record_name=name,
                            created_by=modal_interaction.user.id
                        )
                        
                        if success:
                            embed = discord.Embed(
                                title="✅ Record Created",
                                description=f"Successfully created record: **{name}**",
                                color=0x57F287
                            )
                            await modal_interaction.response.send_message(embed=embed, ephemeral=True)
                        else:
                            await modal_interaction.response.send_message(
                                f"❌ Failed to create record. **{name}** may already exist.",
                                ephemeral=True
                            )
                
                await interaction.response.send_modal(CreateRecordModal())
                return
            
            # Handle record_add - Show select menu of records
            if custom_id == "record_add":
                # Get all records for this guild
                records = RecordsAdapter.get_all_records(interaction.guild.id)
                
                if not records:
                    await interaction.response.send_message(
                        "📋 No records found. Use **Create Record** to create one first!",
                        ephemeral=True
                    )
                    return
                
                # Create select menu with records
                options = []
                for record in records[:25]:  # Discord limit
                    member_count = record.get('member_count', 0)
                    options.append(
                        discord.SelectOption(
                            label=record['record_name'],
                            description=f"👥 {member_count} members",
                            value=record['record_name'],
                            emoji="📁"
                        )
                    )
                
                class RecordSelectView(discord.ui.View):
                    def __init__(self):
                        super().__init__(timeout=60)
                        
                        select = discord.ui.Select(
                            placeholder="Select a record to add members to...",
                            options=options,
                            custom_id="record_select_add"
                        )
                        select.callback = self.select_callback
                        self.add_item(select)
                    
                    async def select_callback(self, select_interaction: discord.Interaction):
                        selected_record = select_interaction.data['values'][0]
                        
                        # Show two options: Add via FID or Add from Alliance
                        class AddMethodView(discord.ui.View):
                            def __init__(self, record_name):
                                super().__init__(timeout=60)
                                self.record_name = record_name
                            
                            @discord.ui.button(label="Add via FID", emoji="🔢", style=discord.ButtonStyle.primary, row=0)
                            async def add_via_fid(self, button_interaction: discord.Interaction, button: discord.ui.Button):
                                # Show FID input modal
                                class AddMembersModal(discord.ui.Modal, title=f"Add to {self.record_name[:30]}"):
                                    fids = discord.ui.TextInput(
                                        label="Player IDs",
                                        placeholder="Comma or newline separated: 123456789,987654321 or one per line",
                                        style=discord.TextStyle.paragraph,
                                        required=True
                                    )
                                    
                                    def __init__(self, record_name):
                                        super().__init__()
                                        self.record_name = record_name
                                    
                                    async def on_submit(self, modal_interaction: discord.Interaction):
                                        # Parse FIDs - support both comma and newline separated
                                        fid_text = self.fids.value.replace(',', '\n')
                                        fid_list = [f.strip() for f in fid_text.split('\n')]
                                        fid_list = [f for f in fid_list if f and len(f) == 9 and f.isdigit()]
                                        
                                        if not fid_list:
                                            await modal_interaction.response.send_message(
                                                "❌ No valid FIDs provided.",
                                                ephemeral=True
                                            )
                                            return
                                        
                                        # Send initial processing message
                                        processing_embed = discord.Embed(
                                            title="➕ Adding Members to Record",
                                            description=f"Adding **{len(fid_list)}** member(s) to **{self.record_name}**...\n\n```\nPlease wait while we process your request.\n```",
                                            color=0x5865F2
                                        )
                                        processing_embed.set_footer(text=f"Processing 0/{len(fid_list)} FIDs...")
                                        
                                        await modal_interaction.response.send_message(embed=processing_embed, ephemeral=True)
                                        
                                        # Process FIDs with status updates
                                        login_handler = LoginHandler()
                                        results = []
                                        success_count = 0
                                        fail_count = 0
                                        
                                        for idx, fid in enumerate(fid_list, 1):
                                            try:
                                                player_data_result = await login_handler.fetch_player_data(fid)
                                                
                                                if player_data_result['status'] == 'success' and player_data_result['data']:
                                                    player_data = player_data_result['data']
                                                    
                                                    member_data = {
                                                        'nickname': player_data.get('nickname', 'Unknown'),
                                                        'furnace_lv': int(player_data.get('stove_lv', 0)),
                                                        'avatar_image': player_data.get('avatar_image', ''),
                                                        'added_by': modal_interaction.user.id
                                                    }
                                                    
                                                    success = RecordsAdapter.add_member_to_record(
                                                        guild_id=modal_interaction.guild.id,
                                                        record_name=self.record_name,
                                                        fid=fid,
                                                        member_data=member_data
                                                    )
                                                    
                                                    if success:
                                                        results.append(f"✅ **{member_data['nickname']}** (`{fid}`)")
                                                        success_count += 1
                                                    else:
                                                        results.append(f"❌ Failed: `{fid}`")
                                                        fail_count += 1
                                                else:
                                                    results.append(f"❌ Not found: `{fid}`")
                                                    fail_count += 1
                                            except Exception as e:
                                                results.append(f"❌ Error: `{fid}`")
                                                fail_count += 1
                                            
                                            # Update progress every 3 FIDs or on last FID
                                            if idx % 3 == 0 or idx == len(fid_list):
                                                progress_bar = "█" * int((idx / len(fid_list)) * 20)
                                                progress_bar += "░" * (20 - len(progress_bar))
                                                
                                                progress_embed = discord.Embed(
                                                    title="➕ Adding Members to Record",
                                                    description=f"**{self.record_name}**\n\n```\n[{progress_bar}] {int((idx/len(fid_list))*100)}%\n```\n✅ Success: {success_count} | ❌ Failed: {fail_count}",
                                                    color=0x5865F2
                                                )
                                                progress_embed.set_footer(text=f"Processing {idx}/{len(fid_list)} FIDs...")
                                                
                                                await modal_interaction.edit_original_response(embed=progress_embed)
                                        
                                        # Create final result embed with pagination
                                        from cogs.pagination_helper import create_paginated_embeds, ResultsPaginationView
                                        
                                        description = f"**Record:** {self.record_name}\n\n**Results:** {success_count} added, {fail_count} failed\n━━━━━━━━━━━━━━━━━━━━━━"
                                        result_embeds = create_paginated_embeds(
                                            title="➕ Add Members - Complete",
                                            description=description,
                                            results=results,
                                            items_per_page=20,
                                            color=0x57F287 if success_count > 0 else 0xED4245,
                                            field_name="📋 Details"
                                        )
                                        
                                        if len(result_embeds) > 1:
                                            view = ResultsPaginationView(result_embeds, author_id=modal_interaction.user.id)
                                            await modal_interaction.edit_original_response(embed=result_embeds[0], view=view)
                                        else:
                                            await modal_interaction.edit_original_response(embed=result_embeds[0])
                                
                                await button_interaction.response.send_modal(AddMembersModal(self.record_name))
                            
                            @discord.ui.button(label="Add from Alliance", emoji="👥", style=discord.ButtonStyle.primary, row=0)
                            async def add_from_alliance(self, button_interaction: discord.Interaction, button: discord.ui.Button):
                                await button_interaction.response.defer(ephemeral=True)
                                
                                # Get server's assigned alliance
                                try:
                                    # Import adapters locally to avoid scope issues
                                    from db.mongo_adapters import AllianceMembersAdapter as MembersAdapter
                                    from db.mongo_adapters import RecordsAdapter as RecAdapter
                                    
                                    if not mongo_enabled() or not ServerAllianceAdapter:
                                        await button_interaction.followup.send(
                                            "❌ MongoDB is not enabled or alliance system is not available.",
                                            ephemeral=True
                                        )
                                        return
                                    
                                    alliance_id = ServerAllianceAdapter.get_alliance(button_interaction.guild.id)
                                    
                                    if not alliance_id:
                                        await button_interaction.followup.send(
                                            "❌ No alliance assigned to this server. Please assign an alliance first using `/manage`.",
                                            ephemeral=True
                                        )
                                        return
                                    
                                    # Get members from the alliance
                                    all_members = MembersAdapter.get_all_members()
                                    members = [m for m in all_members if int(m.get('alliance', 0) or m.get('alliance_id', 0)) == alliance_id]
                                    
                                    if not members:
                                        await button_interaction.followup.send(
                                            "❌ No members found in this alliance.",
                                            ephemeral=True
                                        )
                                        return
                                    
                                    # Show member selection with pagination
                                    class MemberSelectView(discord.ui.View):
                                        def __init__(self, members_list, record_name):
                                            super().__init__(timeout=120)
                                            self.members = sorted(members_list, key=lambda x: int(x.get('furnace_lv', 0) or 0), reverse=True)
                                            self.record_name = record_name
                                            self.current_page = 0
                                            self.members_per_page = 25
                                            self.selected_fids = set()
                                            
                                            # Level mapping for furnace levels
                                            self.level_mapping = {
                                                31: "30-1", 32: "30-2", 33: "30-3", 34: "30-4",
                                                35: "1", 36: "1-(1)", 37: "1-(2)", 38: "1-(3)", 39: "1-(4)",
                                                40: "2", 41: "2-(1)", 42: "2-(2)", 43: "2-(3)", 44: "2-(4)",
                                                45: "3", 46: "3-(1)", 47: "3-(2)", 48: "3-(3)", 49: "3-(4)",
                                                50: "4", 51: "4-(1)", 52: "4-(2)", 53: "4-(3)", 54: "4-(4)",
                                                55: "5", 56: "5-(1)", 57: "5-(2)", 58: "5-(3)", 59: "5-(4)",
                                                60: "6", 61: "6-(1)", 62: "6-(2)", 63: "6-(3)", 64: "6-(4)",
                                                65: "7", 66: "7-(1)", 67: "7-(2)", 68: "7-(3)", 69: "7-(4)",
                                                70: "8", 71: "8-(1)", 72: "8-(2)", 73: "8-(3)", 74: "8-(4)",
                                                75: "9", 76: "9-(1)", 77: "9-(2)", 78: "9-(3)", 79: "9-(4)",
                                                80: "10", 81: "10-(1)", 82: "10-(2)", 83: "10-(3)", 84: "10-(4)"
                                            }
                                            
                                            self.update_components()
                                        
                                        def get_total_pages(self):
                                            return (len(self.members) - 1) // self.members_per_page + 1
                                        
                                        def update_components(self):
                                            self.clear_items()
                                            
                                            # Get members for current page
                                            start_idx = self.current_page * self.members_per_page
                                            end_idx = start_idx + self.members_per_page
                                            page_members = self.members[start_idx:end_idx]
                                            
                                            # Create select menu (max 25 options)
                                            options = []
                                            for member in page_members:
                                                nickname = member.get('nickname', 'Unknown')
                                                fid = member.get('fid', 'N/A')
                                                furnace_lv = int(member.get('furnace_lv', 0) or 0)
                                                
                                                # Format furnace level
                                                level_display = self.level_mapping.get(furnace_lv, str(furnace_lv))
                                                
                                                # Check if already selected
                                                is_selected = fid in self.selected_fids
                                                label_prefix = "✅ " if is_selected else ""
                                                
                                                options.append(
                                                    discord.SelectOption(
                                                        label=f"{label_prefix}{nickname[:80]}",
                                                        description=f"FID: {fid} | FC: {level_display}",
                                                        value=fid,
                                                        emoji="✅" if is_selected else "👤"
                                                    )
                                                )
                                            
                                            select = discord.ui.Select(
                                                placeholder=f"Select members to add (Page {self.current_page + 1}/{self.get_total_pages()})...",
                                                options=options,
                                                max_values=min(len(options), 25),
                                                custom_id="member_select"
                                            )
                                            select.callback = self.members_selected
                                            self.add_item(select)
                                            
                                            # Pagination buttons
                                            if self.get_total_pages() > 1:
                                                prev_btn = discord.ui.Button(emoji="⬅️", style=discord.ButtonStyle.secondary, disabled=(self.current_page == 0), row=1)
                                                prev_btn.callback = self.previous_page
                                                self.add_item(prev_btn)
                                                
                                                next_btn = discord.ui.Button(emoji="➡️", style=discord.ButtonStyle.secondary, disabled=(self.current_page >= self.get_total_pages() - 1), row=1)
                                                next_btn.callback = self.next_page
                                                self.add_item(next_btn)
                                            
                                            # Add selected members button
                                            add_btn = discord.ui.Button(
                                                label=f"Add {len(self.selected_fids)} Members",
                                                emoji="➕",
                                                style=discord.ButtonStyle.success,
                                                disabled=(len(self.selected_fids) == 0),
                                                row=2
                                            )
                                            add_btn.callback = self.add_selected_members
                                            self.add_item(add_btn)
                                        
                                        async def members_selected(self, select_interaction: discord.Interaction):
                                            # Toggle selection
                                            for fid in select_interaction.data['values']:
                                                if fid in self.selected_fids:
                                                    self.selected_fids.remove(fid)
                                                else:
                                                    self.selected_fids.add(fid)
                                            
                                            self.update_components()
                                            await select_interaction.response.edit_message(
                                                content=f"**Selected {len(self.selected_fids)} member(s)** - Choose more or click 'Add Members'",
                                                view=self
                                            )
                                        
                                        async def previous_page(self, btn_interaction: discord.Interaction):
                                            if self.current_page > 0:
                                                self.current_page -= 1
                                                self.update_components()
                                                await btn_interaction.response.edit_message(
                                                    content=f"**Selected {len(self.selected_fids)} member(s)** - Choose more or click 'Add Members'",
                                                    view=self
                                                )
                                        
                                        async def next_page(self, btn_interaction: discord.Interaction):
                                            if self.current_page < self.get_total_pages() - 1:
                                                self.current_page += 1
                                                self.update_components()
                                                await btn_interaction.response.edit_message(
                                                    content=f"**Selected {len(self.selected_fids)} member(s)** - Choose more or click 'Add Members'",
                                                    view=self
                                                )
                                        
                                        async def add_selected_members(self, add_interaction: discord.Interaction):
                                            if not self.selected_fids:
                                                await add_interaction.response.send_message("❌ No members selected.", ephemeral=True)
                                                return
                                            
                                            # Process selected members
                                            processing_embed = discord.Embed(
                                                title="➕ Adding Members to Record",
                                                description=f"Adding **{len(self.selected_fids)}** member(s) to **{self.record_name}**...\n\n```\nPlease wait...\n```",
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
                                                    
                                                    success = RecAdapter.add_member_to_record(
                                                        guild_id=add_interaction.guild.id,
                                                        record_name=self.record_name,
                                                        fid=fid,
                                                        member_data=member_data
                                                    )
                                                    
                                                    if success:
                                                        results.append(f"✅ **{member_data['nickname']}** (`{fid}`)")
                                                        success_count += 1
                                                    else:
                                                        results.append(f"❌ Already exists: `{fid}`")
                                                        fail_count += 1
                                            
                                            # Final result with pagination
                                            from cogs.pagination_helper import create_paginated_embeds, ResultsPaginationView
                                            
                                            description = f"**Record:** {self.record_name}\n\n**Results:** {success_count} added, {fail_count} failed\n━━━━━━━━━━━━━━━━━━━━━━"
                                            result_embeds = create_paginated_embeds(
                                                title="➕ Add Members - Complete",
                                                description=description,
                                                results=results,
                                                items_per_page=20,
                                                color=0x57F287 if success_count > 0 else 0xED4245,
                                                field_name="📋 Details"
                                            )
                                            
                                            if len(result_embeds) > 1:
                                                view = ResultsPaginationView(result_embeds, author_id=add_interaction.user.id)
                                                await add_interaction.edit_original_response(embed=result_embeds[0], view=view)
                                            else:
                                                await add_interaction.edit_original_response(embed=result_embeds[0])
                                    
                                    # Show member selection
                                    member_view = MemberSelectView(members, self.record_name)
                                    await button_interaction.followup.send(
                                        f"**Select members to add to {self.record_name}:**",
                                        view=member_view,
                                        ephemeral=True
                                    )
                                    
                                except Exception as e:
                                    print(f"Error in add_from_alliance: {e}")
                                    import traceback
                                    traceback.print_exc()
                                    await button_interaction.followup.send(
                                        f"❌ An error occurred: {str(e)}",
                                        ephemeral=True
                                    )
                        
                        # Show method selection
                        method_embed = discord.Embed(
                            title=f"➕ Add Members to: {selected_record}",
                            description="Choose how you want to add members:",
                            color=0x5865F2
                        )
                        method_embed.add_field(
                            name="🔢 Add via FID",
                            value="Enter player FIDs manually",
                            inline=True
                        )
                        method_embed.add_field(
                            name="👥 Add from Alliance",
                            value="Select from your alliance members",
                            inline=True
                        )
                        
                        await select_interaction.response.send_message(
                            embed=method_embed,
                            view=AddMethodView(selected_record),
                            ephemeral=True
                        )
                
                embed = discord.Embed(
                    title="➕ Add Members to Record",
                    description="Select a record to add members to:",
                    color=0x5865F2
                )
                
                await interaction.response.send_message(embed=embed, view=RecordSelectView(), ephemeral=True)
                return
            
            # Handle record_remove - Show select menu of records
            if custom_id == "record_remove":
                # Get all records for this guild
                records = RecordsAdapter.get_all_records(interaction.guild.id)
                
                if not records:
                    await interaction.response.send_message(
                        "📋 No records found. Use **Create Record** to create one first!",
                        ephemeral=True
                    )
                    return
                
                # Create select menu with records
                options = []
                for record in records[:25]:  # Discord limit
                    member_count = record.get('member_count', 0)
                    options.append(
                        discord.SelectOption(
                            label=record['record_name'],
                            description=f"👥 {member_count} members",
                            value=record['record_name'],
                            emoji="📁"
                        )
                    )
                
                class RecordSelectView(discord.ui.View):
                    def __init__(self):
                        super().__init__(timeout=60)
                        
                        select = discord.ui.Select(
                            placeholder="Select a record to remove members from...",
                            options=options,
                            custom_id="record_select_remove"
                        )
                        select.callback = self.select_callback
                        self.add_item(select)
                        
                        # Add "Select All" button
                        select_all_button = discord.ui.Button(
                            label="Select All",
                            emoji="☑️",
                            style=discord.ButtonStyle.primary,
                            custom_id="select_all_remove",
                            row=1
                        )
                        select_all_button.callback = self.select_all_callback
                        self.add_item(select_all_button)
                    
                    async def select_all_callback(self, button_interaction: discord.Interaction):
                        # First, we need to show a dropdown to select which record to remove all members from
                        class SelectRecordForAllView(discord.ui.View):
                            def __init__(self, parent_options):
                                super().__init__(timeout=60)
                                
                                select = discord.ui.Select(
                                    placeholder="Select a record to remove ALL members from...",
                                    options=parent_options,
                                    custom_id="record_select_all"
                                )
                                select.callback = self.record_selected
                                self.add_item(select)
                            
                            async def record_selected(self, select_interaction: discord.Interaction):
                                selected_record = select_interaction.data['values'][0]
                                
                                # Get all members from the record
                                members = RecordsAdapter.get_record_members(select_interaction.guild.id, selected_record)
                                
                                if not members:
                                    await select_interaction.response.send_message(
                                        f"❌ No members found in **{selected_record}**.",
                                        ephemeral=True
                                    )
                                    return
                                
                                # Extract all FIDs
                                all_fids = [member.get('fid') for member in members if member.get('fid')]
                                fids_text = ','.join(all_fids)
                                
                                # Show modal with all FIDs pre-filled
                                class RemoveMembersModal(discord.ui.Modal, title=f"Remove All from {selected_record[:25]}"):
                                    fids = discord.ui.TextInput(
                                        label=f"Remove {len(all_fids)} Members",
                                        placeholder="All FIDs are pre-filled. Click Submit to confirm.",
                                        style=discord.TextStyle.paragraph,
                                        required=True,
                                        default=fids_text,
                                        max_length=4000
                                    )
                                    
                                    def __init__(self, record_name):
                                        super().__init__()
                                        self.record_name = record_name
                                    
                                    async def on_submit(self, modal_interaction: discord.Interaction):
                                        # Parse FIDs - support both comma and newline separated
                                        fid_text = self.fids.value.replace(',', '\n')
                                        fid_list = [f.strip() for f in fid_text.split('\n')]
                                        fid_list = [f for f in fid_list if f and len(f) == 9 and f.isdigit()]
                                        
                                        if not fid_list:
                                            await modal_interaction.response.send_message(
                                                "❌ No valid FIDs provided.",
                                                ephemeral=True
                                            )
                                            return
                                        
                                        # Send initial processing message
                                        processing_embed = discord.Embed(
                                            title="➖ Removing All Members from Record",
                                            description=f"Removing **{len(fid_list)}** member(s) from **{self.record_name}**...\n\n```\nPlease wait while we process your request.\n```",
                                            color=0x5865F2
                                        )
                                        processing_embed.set_footer(text=f"Processing 0/{len(fid_list)} FIDs...")
                                        
                                        await modal_interaction.response.send_message(embed=processing_embed, ephemeral=True)
                                        
                                        # Process FIDs with status updates
                                        results = []
                                        success_count = 0
                                        fail_count = 0
                                        
                                        for idx, fid in enumerate(fid_list, 1):
                                            success = RecordsAdapter.remove_member_from_record(
                                                guild_id=modal_interaction.guild.id,
                                                record_name=self.record_name,
                                                fid=fid
                                            )
                                            
                                            if success:
                                                results.append(f"✅ Removed: `{fid}`")
                                                success_count += 1
                                            else:
                                                results.append(f"❌ Failed: `{fid}`")
                                                fail_count += 1
                                            
                                            # Update progress every 3 FIDs or on last FID
                                            if idx % 3 == 0 or idx == len(fid_list):
                                                progress_bar = "█" * int((idx / len(fid_list)) * 20)
                                                progress_bar += "░" * (20 - len(progress_bar))
                                                
                                                progress_embed = discord.Embed(
                                                    title="➖ Removing All Members from Record",
                                                    description=f"**{self.record_name}**\n\n```\n[{progress_bar}] {int((idx/len(fid_list))*100)}%\n```\n✅ Success: {success_count} | ❌ Failed: {fail_count}",
                                                    color=0x5865F2
                                                )
                                                progress_embed.set_footer(text=f"Processing {idx}/{len(fid_list)} FIDs...")
                                                
                                                await modal_interaction.edit_original_response(embed=progress_embed)
                                        
                                        # Create final result embed
                                        result_embed = discord.Embed(
                                            title="➖ Remove All Members - Complete",
                                            description=f"**Record:** {self.record_name}\n\n**Results:** {success_count} removed, {fail_count} failed\n━━━━━━━━━━━━━━━━━━━━━━",
                                            color=0x57F287 if success_count > 0 else 0xED4245
                                        )
                                        
                                        # Add results
                                        results_text = "\n".join(results[:20])  # Limit to 20 for embed
                                        if results_text:
                                            result_embed.add_field(
                                                name="📋 Details",
                                                value=results_text,
                                                inline=False
                                            )
                                        
                                        if len(results) > 20:
                                            result_embed.set_footer(text=f"Showing 20 of {len(results)} results")
                                        
                                        await modal_interaction.edit_original_response(embed=result_embed)
                                
                                await select_interaction.response.send_modal(RemoveMembersModal(selected_record))
                        
                        # Show the record selection view for "Select All"
                        select_all_embed = discord.Embed(
                            title="☑️ Select All - Remove All Members",
                            description="Select a record to remove **ALL** members from:",
                            color=0xED4245
                        )
                        await button_interaction.response.send_message(
                            embed=select_all_embed,
                            view=SelectRecordForAllView(options),
                            ephemeral=True
                        )
                    
                    async def select_callback(self, select_interaction: discord.Interaction):
                        selected_record = select_interaction.data['values'][0]
                        
                        # Show FID input modal
                        class RemoveMembersModal(discord.ui.Modal, title=f"Remove from {selected_record[:30]}"):
                            fids = discord.ui.TextInput(
                                label="Player IDs to Remove",
                                placeholder="Comma or newline separated: 123456789,987654321 or one per line",
                                style=discord.TextStyle.paragraph,
                                required=True
                            )
                            
                            def __init__(self, record_name):
                                super().__init__()
                                self.record_name = record_name
                            
                            async def on_submit(self, modal_interaction: discord.Interaction):
                                # Parse FIDs - support both comma and newline separated
                                fid_text = self.fids.value.replace(',', '\n')
                                fid_list = [f.strip() for f in fid_text.split('\n')]
                                fid_list = [f for f in fid_list if f and len(f) == 9 and f.isdigit()]
                                
                                if not fid_list:
                                    await modal_interaction.response.send_message(
                                        "❌ No valid FIDs provided.",
                                        ephemeral=True
                                    )
                                    return
                                
                                # Send initial processing message
                                processing_embed = discord.Embed(
                                    title="➖ Removing Members from Record",
                                    description=f"Removing **{len(fid_list)}** member(s) from **{self.record_name}**...\n\n```\nPlease wait while we process your request.\n```",
                                    color=0x5865F2
                                )
                                        processing_embed.set_footer(text=f"Processing 0/{len(fid_list)} FIDs...")
                                
                                await modal_interaction.response.send_message(embed=processing_embed, ephemeral=True)
                                
                                # Process FIDs with status updates
                                results = []
                                success_count = 0
                                fail_count = 0
                                
                                for idx, fid in enumerate(fid_list, 1):
                                    success = RecordsAdapter.remove_member_from_record(
                                        guild_id=modal_interaction.guild.id,
                                        record_name=self.record_name,
                                        fid=fid
                                    )
                                    
                                    if success:
                                        results.append(f"✅ Removed: `{fid}`")
                                        success_count += 1
                                    else:
                                        results.append(f"❌ Failed: `{fid}`")
                                        fail_count += 1
                                    
                                    # Update progress every 3 FIDs or on last FID
                                    if idx % 3 == 0 or idx == len(fid_list):
                                        progress_bar = "█" * int((idx / len(fid_list)) * 20)
                                        progress_bar += "░" * (20 - len(progress_bar))
                                        
                                        progress_embed = discord.Embed(
                                            title="➖ Removing Members from Record",
                                            description=f"**{self.record_name}**\n\n```\n[{progress_bar}] {int((idx/len(fid_list))*100)}%\n```\n✅ Success: {success_count} | ❌ Failed: {fail_count}",
                                            color=0x5865F2
                                        )
                                                progress_embed.set_footer(text=f"Processing {idx}/{len(fid_list)} FIDs...")
                                        
                                        await modal_interaction.edit_original_response(embed=progress_embed)
                                
                                # Create final result embed
                                result_embed = discord.Embed(
                                    title="➖ Remove Members - Complete",
                                    description=f"**Record:** {self.record_name}\n\n**Results:** {success_count} removed, {fail_count} failed\n━━━━━━━━━━━━━━━━━━━━━━",
                                    color=0x57F287 if success_count > 0 else 0xED4245
                                )
                                
                                # Add results
                                results_text = "\n".join(results[:20])  # Limit to 20 for embed
                                if results_text:
                                    result_embed.add_field(
                                        name="📋 Details",
                                        value=results_text,
                                        inline=False
                                    )
                                
                                if len(results) > 20:
                                    result_embed.set_footer(text=f"Showing 20 of {len(results)} results")
                                
                                await modal_interaction.edit_original_response(embed=result_embed)
                        
                        await select_interaction.response.send_modal(RemoveMembersModal(selected_record))
                
                embed = discord.Embed(
                    title="➖ Remove Members from Record",
                    description="Select a record to remove members from:",
                    color=0x5865F2
                )
                
                await interaction.response.send_message(embed=embed, view=RecordSelectView(), ephemeral=True)
                return
            
            # Handle record_view - Show select menu of records
            if custom_id == "record_view":
                # Get all records for this guild
                records = RecordsAdapter.get_all_records(interaction.guild.id)
                
                if not records:
                    await interaction.response.send_message(
                        "📋 No records found. Use **Create Record** to create one first!",
                        ephemeral=True
                    )
                    return
                
                # Create select menu with records
                options = []
                for record in records[:25]:  # Discord limit
                    member_count = record.get('member_count', 0)
                    options.append(
                        discord.SelectOption(
                            label=record['record_name'],
                            description=f"👥 {member_count} members",
                            value=record['record_name'],
                            emoji="📁"
                        )
                    )
                
                class RecordSelectView(discord.ui.View):
                    def __init__(self):
                        super().__init__(timeout=60)
                        
                        select = discord.ui.Select(
                            placeholder="Select a record to view...",
                            options=options,
                            custom_id="record_select_view"
                        )
                        select.callback = self.select_callback
                        self.add_item(select)
                    
                    async def select_callback(self, select_interaction: discord.Interaction):
                        selected_record = select_interaction.data['values'][0]
                        
                        await select_interaction.response.defer(ephemeral=True)
                        
                        # Get members and custom columns from the selected record
                        members = RecordsAdapter.get_record_members(select_interaction.guild.id, selected_record)
                        custom_columns = RecordsAdapter.get_custom_columns(select_interaction.guild.id, selected_record)
                        
                        if not members:
                            await select_interaction.followup.send(
                                f"📋 No members found in record **{selected_record}**.",
                                ephemeral=True
                            )
                            return
                        
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
                        
                        # Create interactive view with pagination
                        class MemberDetailsModal(discord.ui.Modal):
                            def __init__(self, record_name, member_data, custom_cols):
                                super().__init__(title=f"Edit: {member_data.get('nickname', 'Unknown')[:20]}")
                                self.record_name = record_name
                                self.fid = member_data.get('fid')
                                self.nickname = member_data.get('nickname', 'Unknown')
                                self.custom_cols = custom_cols
                                
                                # Note input (default)
                                self.note_input = discord.ui.TextInput(
                                    label="Custom Note",
                                    placeholder="Enter general tracking notes...",
                                    default=member_data.get('note', ''),
                                    style=discord.TextStyle.paragraph,
                                    required=False,
                                    max_length=500
                                )
                                self.add_item(self.note_input)
                                
                                # Add inputs for first 4 custom columns
                                self.custom_inputs = {}
                                for col in self.custom_cols[:4]:
                                    text_input = discord.ui.TextInput(
                                        label=col,
                                        placeholder=f"Value for {col}",
                                        default=member_data.get(col, ''),
                                        required=False,
                                        max_length=100
                                    )
                                    self.custom_inputs[col] = text_input
                                    self.add_item(text_input)

                            async def on_submit(self, interaction: discord.Interaction):
                                from db.mongo_adapters import RecordsAdapter as RecAdapter
                                guild_id = interaction.guild.id
                                
                                # Update note
                                RecAdapter.update_member_field(guild_id, self.record_name, self.fid, 'note', self.note_input.value or "")
                                
                                # Update custom columns
                                for col, text_input in self.custom_inputs.items():
                                    RecAdapter.update_member_field(guild_id, self.record_name, self.fid, col, text_input.value or "")
                                
                                success_embed = discord.Embed(
                                    title="✅ Member Data Updated",
                                    description=f"Successfully updated data for **{self.nickname}** in **{self.record_name}**.",
                                    color=0x57F287
                                )
                                await interaction.response.send_message(embed=success_embed, ephemeral=True)

                        class ProfileActionsView(discord.ui.View):
                            def __init__(self, record_name, member_data, custom_cols):
                                super().__init__(timeout=180)
                                self.record_name = record_name
                                self.member_data = member_data
                                self.custom_cols = custom_cols
                            
                            @discord.ui.button(label="Edit Details", emoji="📝", style=discord.ButtonStyle.primary)
                            async def edit_details(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
                                await btn_interaction.response.send_modal(MemberDetailsModal(self.record_name, self.member_data, self.custom_cols))

                        class MemberListView(discord.ui.View):
                            def __init__(self, members_data, record_name, level_map, custom_cols):
                                super().__init__(timeout=300)
                                self.members = members_data
                                self.record_name = record_name
                                self.level_mapping = level_map
                                self.custom_columns = custom_cols
                                self.current_page = 0
                                self.members_per_page = 15
                                self.sort_order = "desc"

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
                                
                                furnace_levels = [int(m.get('furnace_lv', 0) or 0) for m in self.members]
                                max_fl = max(furnace_levels) if furnace_levels else 0
                                avg_fl = sum(furnace_levels) / len(furnace_levels) if furnace_levels else 0

                                embed = discord.Embed(
                                    title=f"📁 {self.record_name} - Member List",
                                    description=(
                                        "```ml\n"
                                        "Record Statistics\n"
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
                                
                                embed.set_author(
                                    name="RECORD DATABASE • ACCESS GRANTED",
                                    icon_url="https://cdn.discordapp.com/attachments/1435569370389807144/1436745053442805830/unnamed_5.png"
                                )

                                start_idx = self.current_page * self.members_per_page
                                end_idx = start_idx + self.members_per_page
                                page_members = sorted_members[start_idx:end_idx]

                                member_list = ""
                                for idx, member in enumerate(page_members, start=start_idx + 1):
                                    nickname = member.get('nickname', 'Unknown')
                                    fid = member.get('fid', 'N/A')
                                    furnace_lv = int(member.get('furnace_lv', 0) or 0)
                                    level = self.level_mapping.get(furnace_lv, str(furnace_lv))
                                    
                                    line = f"**{idx:02d}.** 👤 {nickname}\n└ 🆔 `ID: {fid}` | ⚔️ `FC: {level}`"
                                    
                                    extras = []
                                    for col in self.custom_columns:
                                        val = member.get(col, '')
                                        extras.append(f"🏷️ `{col}: {val if val else '—'}`")
                                    
                                    if extras: line += f"\n└ {' | '.join(extras)}"
                                    
                                    member_list += line + "\n\n"

                                embed.description += member_list
                                embed.set_footer(text=f"Page {self.current_page + 1}/{total_pages} • MongoDB" if total_pages > 1 else "Stored in MongoDB")
                                return embed

                            @discord.ui.button(emoji="⬅️", style=discord.ButtonStyle.secondary, row=0)
                            async def previous_page(self, button_interaction: discord.Interaction, button: discord.ui.Button):
                                if self.current_page > 0:
                                    self.current_page -= 1
                                    await button_interaction.response.edit_message(embed=self.create_embed(), view=self)
                                else:
                                    await button_interaction.response.defer()

                            @discord.ui.button(emoji="➡️", style=discord.ButtonStyle.secondary, row=0)
                            async def next_page(self, button_interaction: discord.Interaction, button: discord.ui.Button):
                                if self.current_page < self.get_total_pages() - 1:
                                    self.current_page += 1
                                    await button_interaction.response.edit_message(embed=self.create_embed(), view=self)
                                else:
                                    await button_interaction.response.defer()

                            @discord.ui.button(label="🔄 Sort", style=discord.ButtonStyle.secondary, row=0)
                            async def toggle_sort(self, button_interaction: discord.Interaction, button: discord.ui.Button):
                                self.sort_order = "asc" if self.sort_order == "desc" else "desc"
                                self.current_page = 0
                                await button_interaction.response.edit_message(embed=self.create_embed(), view=self)

                            @discord.ui.button(label="Profile", emoji="👤", style=discord.ButtonStyle.secondary, row=0)
                            async def view_profile(self, button_interaction: discord.Interaction, button: discord.ui.Button):
                                # Create a select menu for choosing a player with pagination
                                class ProfileSelectView(discord.ui.View):
                                    def __init__(self, members_data, record_name, level_map, custom_cols):
                                        super().__init__(timeout=60)
                                        self.record_name = record_name
                                        self.members = sorted(members_data, key=lambda x: int(x.get('furnace_lv', 0) or 0), reverse=True)
                                        self.level_mapping = level_map
                                        self.custom_cols = custom_cols
                                        self.current_page = 0
                                        self.members_per_page = 25
                                        self.update_components()

                                    def get_total_pages(self):
                                        return (len(self.members) - 1) // self.members_per_page + 1

                                    def update_components(self):
                                        self.clear_items()
                                        
                                        # Get members for current page
                                        start_idx = self.current_page * self.members_per_page
                                        end_idx = start_idx + self.members_per_page
                                        page_members = self.members[start_idx:end_idx]
                                        
                                        # Create select menu with member options
                                        options = []
                                        for idx, member in enumerate(page_members, start=start_idx + 1):
                                            nickname = member.get('nickname', 'Unknown')
                                            fid = member.get('fid', 'N/A')
                                            furnace_lv = int(member.get('furnace_lv', 0) or 0)
                                            level = self.level_mapping.get(furnace_lv, str(furnace_lv))
                                            
                                            options.append(
                                                discord.SelectOption(
                                                    label=f"#{idx:02d} {nickname[:80]}",
                                                    description=f"FID: {fid} | FC: {level}",
                                                    value=str(fid),
                                                    emoji="👤"
                                                )
                                            )
                                        
                                        select = discord.ui.Select(
                                            placeholder=f"Select a player (Page {self.current_page + 1}/{self.get_total_pages()})...",
                                            options=options,
                                            custom_id="player_select"
                                        )
                                        select.callback = self.player_selected
                                        self.add_item(select)
                                        
                                        # Add pagination buttons if needed
                                        if self.get_total_pages() > 1:
                                            prev_button = discord.ui.Button(
                                                emoji="⬅️",
                                                style=discord.ButtonStyle.secondary,
                                                disabled=(self.current_page == 0),
                                                row=1
                                            )
                                            prev_button.callback = self.previous_page
                                            self.add_item(prev_button)
                                            
                                            next_button = discord.ui.Button(
                                                emoji="➡️",
                                                style=discord.ButtonStyle.secondary,
                                                disabled=(self.current_page >= self.get_total_pages() - 1),
                                                row=1
                                            )
                                            next_button.callback = self.next_page
                                            self.add_item(next_button)

                                    async def previous_page(self, button_interaction: discord.Interaction):
                                        if self.current_page > 0:
                                            self.current_page -= 1
                                            self.update_components()
                                            await button_interaction.response.edit_message(
                                                content=f"**Select a player to view their profile:** (Page {self.current_page + 1}/{self.get_total_pages()})",
                                                view=self
                                            )
                                        else:
                                            await button_interaction.response.defer()

                                    async def next_page(self, button_interaction: discord.Interaction):
                                        if self.current_page < self.get_total_pages() - 1:
                                            self.current_page += 1
                                            self.update_components()
                                            await button_interaction.response.edit_message(
                                                content=f"**Select a player to view their profile:** (Page {self.current_page + 1}/{self.get_total_pages()})",
                                                view=self
                                            )
                                        else:
                                            await button_interaction.response.defer()

                                    async def player_selected(self, select_interaction: discord.Interaction):
                                        fid = select_interaction.data['values'][0]
                                        
                                        # Find the member
                                        member = next((m for m in self.members if m.get('fid') == fid), None)
                                        if not member:
                                            await select_interaction.response.send_message(
                                                "❌ Player not found.",
                                                ephemeral=True
                                            )
                                            return
                                        
                                        # Create profile embed
                                        nickname = member.get('nickname', 'Unknown')
                                        furnace_lv = int(member.get('furnace_lv', 0) or 0)
                                        level = self.level_mapping.get(furnace_lv, str(furnace_lv))
                                        avatar = member.get('avatar_image', '')
                                        
                                        # If no avatar in database, try to fetch from API
                                        if not avatar:
                                            try:
                                                from cogs.login_handler import LoginHandler
                                                login_handler = LoginHandler()
                                                result = await login_handler.fetch_player_data(str(fid))
                                                
                                                if result['status'] == 'success' and result['data']:
                                                    avatar = result['data'].get('avatar_image', '')
                                                    
                                                    # Update member data in RecordsAdapter with avatar
                                                    if avatar:
                                                        member['avatar_image'] = avatar
                                                        # Note: RecordsAdapter doesn't have direct member update, 
                                                        # but avatar will be fetched next time
                                            except Exception as e:
                                                print(f"Error fetching avatar from API: {e}")
                                        
                                        profile_embed = discord.Embed(
                                            title=f"👤 Player Profile",
                                            description=(
                                                f"**{nickname}**\n\n"
                                                f"```yaml\n"
                                                f"FID         : {fid}\n"
                                                f"Furnace Lv  : {level}\n"
                                                f"```"
                                            ),
                                            color=0x5865F2
                                        )
                                        
                                        # Add Note if exists
                                        note = member.get('note')
                                        if note:
                                            profile_embed.add_field(
                                                name="📝 Record Note",
                                                value=f"```\n{note}\n```",
                                                inline=False
                                            )
                                        
                                        # Add all custom column data
                                        for col in self.custom_cols:
                                            val = member.get(col)
                                            if val:
                                                profile_embed.add_field(
                                                    name=f"🏷️ {col}",
                                                    value=f"```\n{val}\n```",
                                                    inline=True
                                                )
                                        
                                        if avatar:
                                            try:
                                                profile_embed.set_image(url=avatar)
                                            except Exception as e:
                                                print(f"Error setting avatar: {e}")
                                        
                                        profile_embed.set_footer(
                                            text=f"Record: {self.record_name} • MongoDB",
                                            icon_url="https://cdn.discordapp.com/attachments/1435569370389807144/1445459239131680859/images_7_1.png"
                                        )
                                        
                                        # Show with ProfileActionsView for editing
                                        actions_view = ProfileActionsView(self.record_name, member, self.custom_cols)
                                        await select_interaction.response.send_message(
                                            embed=profile_embed,
                                            view=actions_view,
                                            ephemeral=True
                                        )

                                # Show profile selection menu
                                profile_view = ProfileSelectView(self.members, self.record_name, self.level_mapping, self.custom_columns)
                                await button_interaction.response.send_message(f"**Select a player:** (Page 1/{profile_view.get_total_pages()})", view=profile_view, ephemeral=True)

                        # Create and send the view
                        view = MemberListView(members, selected_record, level_mapping, custom_columns)
                        await select_interaction.followup.send(
                            embed=view.create_embed(),
                            view=view,
                            ephemeral=False
                        )
                
                embed = discord.Embed(
                    title="👁️ View Record",
                    description="Select a record to view its members:",
                    color=0x5865F2
                )
                
                await interaction.response.send_message(embed=embed, view=RecordSelectView(), ephemeral=True)
                return
            
            # Handle record_rename - Show select menu of records
            if custom_id == "record_rename":
                # Get all records for this guild
                records = RecordsAdapter.get_all_records(interaction.guild.id)
                
                if not records:
                    await interaction.response.send_message(
                        "📋 No records found. Use **Create Record** to create one first!",
                        ephemeral=True
                    )
                    return
                
                # Create select menu with records
                options = []
                for record in records[:25]:  # Discord limit
                    member_count = record.get('member_count', 0)
                    options.append(
                        discord.SelectOption(
                            label=record['record_name'],
                            description=f"👥 {member_count} members",
                            value=record['record_name'],
                            emoji="📁"
                        )
                    )
                
                class RecordSelectView(discord.ui.View):
                    def __init__(self):
                        super().__init__(timeout=60)
                        
                        select = discord.ui.Select(
                            placeholder="Select a record to rename...",
                            options=options,
                            custom_id="record_select_rename"
                        )
                        select.callback = self.select_callback
                        self.add_item(select)
                    
                    async def select_callback(self, select_interaction: discord.Interaction):
                        selected_record = select_interaction.data['values'][0]
                        
                        # Show modal for new name
                        class RenameModal(discord.ui.Modal, title=f"Rename {selected_record[:30]}"):
                            new_name = discord.ui.TextInput(
                                label="New Name",
                                placeholder="Enter new record name",
                                required=True,
                                max_length=50
                            )
                            
                            def __init__(self, old_name):
                                super().__init__()
                                self.old_name = old_name
                            
                            async def on_submit(self, modal_interaction: discord.Interaction):
                                new = self.new_name.value.strip()
                                
                                success = RecordsAdapter.rename_record(
                                    guild_id=modal_interaction.guild.id,
                                    old_name=self.old_name,
                                    new_name=new
                                )
                                
                                if success:
                                    embed = discord.Embed(
                                        title="✅ Record Renamed",
                                        description=f"Successfully renamed **{self.old_name}** to **{new}**",
                                        color=0x57F287
                                    )
                                    await modal_interaction.response.send_message(embed=embed, ephemeral=True)
                                else:
                                    await modal_interaction.response.send_message(
                                        f"❌ Failed to rename. **{new}** may already exist.",
                                        ephemeral=True
                                    )
                        
                        await select_interaction.response.send_modal(RenameModal(selected_record))
                
                embed = discord.Embed(
                    title="✏️ Rename Record",
                    description="Select a record to rename:",
                    color=0x5865F2
                )
                
                await interaction.response.send_message(embed=embed, view=RecordSelectView(), ephemeral=True)
                return
            
            # Handle record_delete - Show select menu of records
            if custom_id == "record_delete":
                # Get all records for this guild
                records = RecordsAdapter.get_all_records(interaction.guild.id)
                
                if not records:
                    await interaction.response.send_message(
                        "📋 No records found. Nothing to delete!",
                        ephemeral=True
                    )
                    return
                
                # Create select menu with records
                options = []
                for record in records[:25]:  # Discord limit
                    member_count = record.get('member_count', 0)
                    options.append(
                        discord.SelectOption(
                            label=record['record_name'],
                            description=f"👥 {member_count} members - ⚠️ Will be permanently deleted",
                            value=record['record_name'],
                            emoji="🗑️"
                        )
                    )
                
                class RecordSelectView(discord.ui.View):
                    def __init__(self):
                        super().__init__(timeout=60)
                        
                        select = discord.ui.Select(
                            placeholder="Select a record to delete...",
                            options=options,
                            custom_id="record_select_delete"
                        )
                        select.callback = self.select_callback
                        self.add_item(select)
                    
                    async def select_callback(self, select_interaction: discord.Interaction):
                        selected_record = select_interaction.data['values'][0]
                        
                        # Show confirmation modal
                        class DeleteConfirmModal(discord.ui.Modal, title=f"Delete {selected_record[:25]}?"):
                            confirm = discord.ui.TextInput(
                                label="Type 'DELETE' to confirm",
                                placeholder="DELETE",
                                required=True,
                                max_length=6
                            )
                            
                            def __init__(self, record_name):
                                super().__init__()
                                self.record_name = record_name
                            
                            async def on_submit(self, modal_interaction: discord.Interaction):
                                if self.confirm.value.upper() != "DELETE":
                                    await modal_interaction.response.send_message(
                                        "❌ Deletion cancelled. You must type 'DELETE' to confirm.",
                                        ephemeral=True
                                    )
                                    return
                                
                                success = RecordsAdapter.delete_record(
                                    guild_id=modal_interaction.guild.id,
                                    record_name=self.record_name
                                )
                                
                                if success:
                                    embed = discord.Embed(
                                        title="✅ Record Deleted",
                                        description=f"Successfully deleted record: **{self.record_name}**",
                                        color=0x57F287
                                    )
                                    await modal_interaction.response.send_message(embed=embed, ephemeral=True)
                                else:
                                    await modal_interaction.response.send_message(
                                        f"❌ Failed to delete **{self.record_name}**.",
                                        ephemeral=True
                                    )
                        
                        await select_interaction.response.send_modal(DeleteConfirmModal(selected_record))
                
                embed = discord.Embed(
                    title="🗑️ Delete Record",
                    description="⚠️ **Warning:** This action cannot be undone!\n\nSelect a record to delete:",
                    color=0xED4245
                )
                
                await interaction.response.send_message(embed=embed, view=RecordSelectView(), ephemeral=True)
                return
            
            # Handle record_columns - Configure custom data fields for a record
            if custom_id == "record_columns":
                # Get all records for this guild
                records = RecordsAdapter.get_all_records(interaction.guild.id)
                if not records:
                    await interaction.response.send_message("📋 No records found. Create one first!", ephemeral=True)
                    return
                
                # Create select menu with records
                options = [discord.SelectOption(label=r['record_name'], description=f"👥 {r.get('member_count', 0)} members", value=r['record_name'], emoji="📁") for r in records[:25]]
                
                class RecordSelectView(discord.ui.View):
                    def __init__(self):
                        super().__init__(timeout=60)
                        select = discord.ui.Select(placeholder="Select a record to configure columns...", options=options)
                        select.callback = self.select_callback
                        self.add_item(select)
                    
                    async def select_callback(self, select_interaction: discord.Interaction):
                        selected_record = select_interaction.data['values'][0]
                        current_columns = RecordsAdapter.get_custom_columns(select_interaction.guild.id, selected_record)
                        
                        class ColumnManagementView(discord.ui.View):
                            def __init__(self, record_name, columns):
                                super().__init__(timeout=180)
                                self.record_name = record_name
                                self.columns = columns
                                self.update_view()
                            
                            def update_view(self):
                                self.clear_items()
                                add_btn = discord.ui.Button(label="Add Column", emoji="➕", style=discord.ButtonStyle.success, row=0)
                                add_btn.callback = self.add_column_callback
                                self.add_item(add_btn)
                                if self.columns:
                                    set_btn = discord.ui.Button(label="Set Player Data", emoji="📝", style=discord.ButtonStyle.primary, row=0)
                                    set_btn.callback = self.set_player_data_callback
                                    self.add_item(set_btn)
                                    remove_select = discord.ui.Select(
                                        placeholder="Select a column to remove...",
                                        options=[discord.SelectOption(label=col, value=col, emoji="🗑️") for col in self.columns],
                                        row=1
                                    )
                                    remove_select.callback = self.remove_column_callback
                                    self.add_item(remove_select)

                            async def add_column_callback(self, btn_interaction: discord.Interaction):
                                mgmt_view = self
                                class AddColumnModal(discord.ui.Modal, title=f"Add Column to {mgmt_view.record_name[:20]}"):
                                    col_name = discord.ui.TextInput(label="Column Name", placeholder="e.g. Rank, Role, Task", required=True, max_length=20)
                                    async def on_submit(self, modal_interaction: discord.Interaction):
                                        new_col = self.col_name.value.strip()
                                        if not new_col:
                                            await modal_interaction.response.send_message("❌ Column name cannot be empty.", ephemeral=True)
                                            return
                                        success = RecordsAdapter.add_custom_column(modal_interaction.guild.id, mgmt_view.record_name, new_col)
                                        if success:
                                            mgmt_view.columns = RecordsAdapter.get_custom_columns(modal_interaction.guild.id, mgmt_view.record_name)
                                            mgmt_view.update_view()
                                            await modal_interaction.response.edit_message(embed=mgmt_view.create_embed(), view=mgmt_view)
                                        else:
                                            await modal_interaction.response.send_message(f"❌ Could not add column **{new_col}**. It may already exist.", ephemeral=True)
                                await btn_interaction.response.send_modal(AddColumnModal())

                            async def set_player_data_callback(self, btn_interaction: discord.Interaction):
                                mgmt_view = self
                                members = RecordsAdapter.get_record_members(btn_interaction.guild.id, mgmt_view.record_name)
                                if not members:
                                    await btn_interaction.response.send_message("❌ No members in this record.", ephemeral=True)
                                    return
                                level_map = {35:"FC1",40:"FC2",45:"FC3",50:"FC4",55:"FC5",60:"FC6",65:"FC7",70:"FC8",75:"FC9",80:"FC10"}
                                options = []
                                for m in members[:25]:
                                    nick = m.get('nickname','Unknown')[:80]
                                    fid = str(m.get('fid',''))
                                    fl = int(m.get('furnace_lv',0) or 0)
                                    fc = next((v for k,v in sorted(level_map.items(),reverse=True) if fl>=k), str(fl))
                                    col_preview = " | ".join([f"{c}: {m.get(c,'—')}" for c in mgmt_view.columns[:2]])
                                    desc = f"FID: {fid} | {fc}" + (f" | {col_preview}" if col_preview else "")
                                    options.append(discord.SelectOption(label=nick, description=desc[:100], value=fid, emoji="👤"))

                                class PlayerSelectView(discord.ui.View):
                                    def __init__(self):
                                        super().__init__(timeout=60)
                                        sel = discord.ui.Select(placeholder="Select a player to fill in data...", options=options)
                                        sel.callback = self.player_chosen
                                        self.add_item(sel)
                                    async def player_chosen(self, sel_interaction: discord.Interaction):
                                        fid = sel_interaction.data['values'][0]
                                        member = next((m for m in members if str(m.get('fid',''))==fid), None)
                                        if not member:
                                            await sel_interaction.response.send_message("❌ Player not found.", ephemeral=True)
                                            return
                                        class FillDataModal(discord.ui.Modal, title=f"Fill Data: {member.get('nickname','')[:20]}"):
                                            pass
                                        modal = FillDataModal()
                                        col_inputs = {}
                                        for col in mgmt_view.columns[:5]:
                                            ti = discord.ui.TextInput(
                                                label=col, placeholder=f"Enter value for {col}...",
                                                default=str(member.get(col,'')), required=False, max_length=100
                                            )
                                            col_inputs[col] = ti
                                            modal.add_item(ti)
                                        async def on_submit(modal_interaction: discord.Interaction):
                                            for col, ti in col_inputs.items():
                                                RecordsAdapter.update_member_field(
                                                    modal_interaction.guild.id, mgmt_view.record_name, fid, col, ti.value or ""
                                                )
                                            saved = "\n".join([f"• **{c}**: {col_inputs[c].value or '—'}" for c in col_inputs])
                                            await modal_interaction.response.send_message(
                                                embed=discord.Embed(
                                                    title="✅ Player Data Updated",
                                                    description=f"**{member.get('nickname','Unknown')}**\n\n{saved}",
                                                    color=0x57F287
                                                ), ephemeral=True
                                            )
                                        modal.on_submit = on_submit
                                        await sel_interaction.response.send_modal(modal)

                                await btn_interaction.response.send_message(
                                    content="**Select a player to fill in column data:**",
                                    view=PlayerSelectView(), ephemeral=True
                                )

                            async def remove_column_callback(self, sel_interaction: discord.Interaction):
                                col_to_remove = sel_interaction.data['values'][0]
                                if RecordsAdapter.remove_custom_column(sel_interaction.guild.id, self.record_name, col_to_remove):
                                    self.columns = RecordsAdapter.get_custom_columns(sel_interaction.guild.id, self.record_name)
                                    self.update_view()
                                    await sel_interaction.response.edit_message(embed=self.create_embed(), view=self)
                                else:
                                    await sel_interaction.response.send_message("❌ Failed to remove column.", ephemeral=True)

                            def create_embed(self):
                                if self.columns:
                                    col_list = "\n".join([f"• **{c}**" for c in self.columns])
                                else:
                                    col_list = "*No columns yet — press Add Column to create one.*"
                                embed = discord.Embed(
                                    title=f"⚙️ Custom Columns — {self.record_name}",
                                    description=(
                                        "Custom columns appear next to each player in the member list and in their profile.\n"
                                        "Players with no value for a column will simply show as empty.\n\n"
                                        f"**Current Columns:**\n{col_list}"
                                    ),
                                    color=0x5865F2
                                )
                                embed.set_footer(text="Use 'Set Player Data' to fill column values per player.")
                                return embed

                        view = ColumnManagementView(selected_record, current_columns)
                        await select_interaction.response.edit_message(embed=view.create_embed(), view=view)
                
                await interaction.response.send_message(embed=discord.Embed(title="⚙️ Custom Columns Configuration", description="Select a record to manage its columns:", color=0x5865F2), view=RecordSelectView(), ephemeral=True)
                return
        
        
        
        # Handle member_operations button from /manage
        if custom_id == "manage_member_ops":
            try:
                # Get server's assigned alliance (same as !Add command)
                try:
                    if not mongo_enabled() or not ServerAllianceAdapter:
                        await interaction.response.send_message(
                            "❌ MongoDB not enabled. Cannot access member operations.",
                            ephemeral=True
                        )
                        return
                    
                    alliance_id = ServerAllianceAdapter.get_alliance(interaction.guild.id)
                    if not alliance_id:
                        await interaction.response.send_message(
                            "❌ No alliance assigned to this server.\n\n"
                            "Please use `/settings` → Bot Operations → Assign Server Alliance to assign one.",
                            ephemeral=True
                        )
                        return
                    
                    # Get alliance name
                    from db_utils import get_db_connection
                    with get_db_connection('alliance.sqlite') as conn:
                        cursor = conn.cursor()
                        cursor.execute("SELECT name FROM alliance_list WHERE alliance_id = ?", (alliance_id,))
                        result = cursor.fetchone()
                        alliance_name = result[0] if result else f"Alliance {alliance_id}"
                    
                    print(f"DEBUG: Server {interaction.guild.id} has alliance: {alliance_id} ({alliance_name})")
                except Exception as e:
                    print(f"Error getting server alliance: {e}")
                    await interaction.response.send_message(
                        f"❌ Error loading alliance information: {e}",
                        ephemeral=True
                    )
                    return

                # Create member operations menu for the server's alliance
                embed = discord.Embed(
                    title=f"👥 {alliance_name}",
                    description=(
                        "```ansi\n"
                        "\u001b[1;37mMEMBER MANAGEMENT PANEL\u001b[0m\n"
                        "```\n"
                        "**Select an operation:**"
                    ),
                    color=0x2B2D31
                )
                embed.set_footer(
                    text=f"{interaction.guild.name} x Magnus🚀",
                    icon_url="https://cdn.discordapp.com/attachments/1435569370389807144/1436745053442805830/unnamed_5.png"
                )

                view = discord.ui.View(timeout=300)
                view.add_item(discord.ui.Button(
                    label="View Members",
                    emoji="👁️",
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"view_members_{alliance_id}",
                    row=0
                ))
                view.add_item(discord.ui.Button(
                    label="Add Members",
                    emoji="➕",
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"add_members_{alliance_id}",
                    row=0
                ))
                view.add_item(discord.ui.Button(
                    label="Remove Members",
                    emoji="➖",
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"remove_members_{alliance_id}",
                    row=0
                ))
                view.add_item(discord.ui.Button(
                    label="Back",
                    emoji="◀️",
                    style=discord.ButtonStyle.secondary,
                    custom_id="back_to_manage",
                    row=1
                ))

                # Edit the message instead of sending a new one
                await interaction.response.edit_message(embed=embed, view=view)
                return
            except Exception as e:
                print(f"Member operations error: {e}")
                await interaction.response.send_message(
                    "❌ An error occurred while loading member operations.",
                    ephemeral=True
                )
                return
        
        # Handle view_members button
        if custom_id.startswith("view_members_"):
            try:
                alliance_id = int(custom_id.split("_")[2])
                
                await interaction.response.defer(ephemeral=True)
                
                # Get members from MongoDB
                try:
                    from db.mongo_adapters import AllianceMembersAdapter
                    all_members = AllianceMembersAdapter.get_all_members()
                    members = [m for m in all_members if int(m.get('alliance', 0) or m.get('alliance_id', 0)) == alliance_id]
                except Exception as e:
                    print(f"Error getting members: {e}")
                    members = []

                if not members:
                    await interaction.followup.send(
                        "📋 No members found in this alliance.",
                        ephemeral=True
                    )
                    return

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
                # Create interactive view with pagination (matching !showlist)
                class MemberListView(discord.ui.View):
                    def __init__(self, members_data, alliance_name, level_map):
                        super().__init__(timeout=None)
                        self.all_members = members_data  # Store all members
                        self.alliance_name = alliance_name
                        self.level_mapping = level_map
                        self.current_page = 0
                        self.members_per_page = 15
                        self.sort_order = "desc"
                        self.active_filter = None  # None = All Members, or "FC 1", "FC 2", etc.
                        
                        # FC level ranges (furnace level min-max)
                        self.fc_ranges = {
                            "FC 1": (35, 39),
                            "FC 2": (40, 44),
                            "FC 3": (45, 49),
                            "FC 4": (50, 54),
                            "FC 5": (55, 59),
                            "FC 6": (60, 64),
                            "FC 7": (65, 69),
                            "FC 8": (70, 74),
                        }

                    def get_filtered_members(self):
                        """Get members filtered by active FC filter"""
                        if self.active_filter is None:
                            return self.all_members
                        
                        if self.active_filter in self.fc_ranges:
                            min_level, max_level = self.fc_ranges[self.active_filter]
                            return [
                                m for m in self.all_members
                                if min_level <= int(m.get('furnace_lv', 0) or 0) <= max_level
                            ]
                        
                        return self.all_members

                    def get_sorted_members(self):
                        filtered = self.get_filtered_members()
                        return sorted(
                            filtered,
                            key=lambda x: int(x.get('furnace_lv', 0) or 0),
                            reverse=(self.sort_order == "desc")
                        )

                    def get_total_pages(self):
                        filtered_count = len(self.get_filtered_members())
                        return max(1, (filtered_count - 1) // self.members_per_page + 1)

                    def create_embed(self):
                        sorted_members = self.get_sorted_members()
                        filtered_members = self.get_filtered_members()
                        total_pages = self.get_total_pages()
                        
                        # Calculate statistics based on filtered members
                        furnace_levels = [int(m.get('furnace_lv', 0) or 0) for m in filtered_members]
                        max_fl = max(furnace_levels) if furnace_levels else 0
                        avg_fl = sum(furnace_levels) / len(furnace_levels) if furnace_levels else 0

                        # Create embed with alliance statistics
                        filter_text = f" - {self.active_filter}" if self.active_filter else ""
                        embed = discord.Embed(
                            title=f"👥 {self.alliance_name} - Member List{filter_text}",
                            description=(
                                "```ml\n"
                                "Alliance Statistics\n"
                                "══════════════════════════\n"
                                f"📊 Total Members    : {len(filtered_members)}\n"
                                f"⚔️ Highest Level    : {self.level_mapping.get(max_fl, str(max_fl))}\n"
                                f"📈 Average Level    : {self.level_mapping.get(int(avg_fl), str(int(avg_fl)))}\n"
                                "══════════════════════════\n"
                                "```\n"
                                f"**Member List{filter_text}**\n"
                                "━━━━━━━━━━━━━━━━━━━━━━\n"
                            ),
                            color=0x5865F2
                        )
                        
                        embed.set_author(
                            name="MEMBER DATABASE • ACCESS GRANTED",
                            icon_url="https://cdn.discordapp.com/attachments/1435569370389807144/1436745053442805830/unnamed_5.png"
                        )

                        # Get members for current page
                        start_idx = self.current_page * self.members_per_page
                        end_idx = start_idx + self.members_per_page
                        page_members = sorted_members[start_idx:end_idx]

                        # Add members to embed
                        if page_members:
                            member_list = ""
                            for idx, member in enumerate(page_members, start=start_idx + 1):
                                nickname = member.get('nickname', 'Unknown')
                                fid = member.get('fid', 'N/A')
                                furnace_lv = int(member.get('furnace_lv', 0) or 0)
                                level = self.level_mapping.get(furnace_lv, str(furnace_lv))
                                
                                member_list += f"**{idx:02d}.** 👤 {nickname}\n└ 🆔 `ID: {fid}` | ⚔️ `FC: {level}`\n\n"
                            embed.description += member_list
                        else:
                            embed.description += "*No members found with this filter.*\n\n"

                        # Footer with page info and filter status
                        footer_text = f"Page {self.current_page + 1}/{total_pages}"
                        if self.active_filter:
                            footer_text += f" • Filtered by {self.active_filter}"
                        footer_text += " • Stored in MongoDB"
                        
                        embed.set_footer(
                            text=footer_text,
                            icon_url="https://cdn.discordapp.com/attachments/1435569370389807144/1445459239131680859/images_7_1.png"
                        )

                        return embed

                    @discord.ui.button(emoji="⬅️", style=discord.ButtonStyle.secondary, row=0)
                    async def previous_page(self, button_interaction: discord.Interaction, button: discord.ui.Button):
                        if self.current_page > 0:
                            self.current_page -= 1
                            await button_interaction.response.edit_message(embed=self.create_embed(), view=self)
                        else:
                            await button_interaction.response.defer()

                    @discord.ui.button(emoji="➡️", style=discord.ButtonStyle.secondary, row=0)
                    async def next_page(self, button_interaction: discord.Interaction, button: discord.ui.Button):
                        if self.current_page < self.get_total_pages() - 1:
                            self.current_page += 1
                            await button_interaction.response.edit_message(embed=self.create_embed(), view=self)
                        else:
                            await button_interaction.response.defer()

                    @discord.ui.select(
                        placeholder="Filter by FC Level",
                        options=[
                            discord.SelectOption(label="All Members", value="all", emoji="👥", description="Show all alliance members"),
                            discord.SelectOption(label="Sort ↑ Ascending", value="sort_asc", emoji="🔼", description="Sort by FC level (low to high)"),
                            discord.SelectOption(label="Sort ↓ Descending", value="sort_desc", emoji="🔽", description="Sort by FC level (high to low)"),
                            discord.SelectOption(label="FC 1", value="FC 1", emoji="1️⃣", description="FC 1 to FC 1-4"),
                            discord.SelectOption(label="FC 2", value="FC 2", emoji="2️⃣", description="FC 2 to FC 2-4"),
                            discord.SelectOption(label="FC 3", value="FC 3", emoji="3️⃣", description="FC 3 to FC 3-4"),
                            discord.SelectOption(label="FC 4", value="FC 4", emoji="4️⃣", description="FC 4 to FC 4-4"),
                            discord.SelectOption(label="FC 5", value="FC 5", emoji="5️⃣", description="FC 5 to FC 5-4"),
                            discord.SelectOption(label="FC 6", value="FC 6", emoji="6️⃣", description="FC 6 to FC 6-4"),
                            discord.SelectOption(label="FC 7", value="FC 7", emoji="7️⃣", description="FC 7 to FC 7-4"),
                            discord.SelectOption(label="FC 8", value="FC 8", emoji="8️⃣", description="FC 8 to FC 8-4"),
                        ],
                        row=1
                    )
                    async def filter_select(self, select_interaction: discord.Interaction, select: discord.ui.Select):
                        selected = select_interaction.data['values'][0]
                        
                        if selected == "all":
                            self.active_filter = None
                            self.current_page = 0
                        elif selected == "sort_asc":
                            self.sort_order = "asc"
                            self.current_page = 0
                        elif selected == "sort_desc":
                            self.sort_order = "desc"
                            self.current_page = 0
                        else:
                            # FC level filter
                            self.active_filter = selected
                            self.current_page = 0
                        
                        await select_interaction.response.edit_message(embed=self.create_embed(), view=self)

                    @discord.ui.button(label="Profile", emoji="👤", style=discord.ButtonStyle.secondary, row=0)
                    async def view_profile(self, button_interaction: discord.Interaction, button: discord.ui.Button):
                        # Create a select menu for choosing a player with pagination
                        class ProfileSelectView(discord.ui.View):
                            def __init__(self, members_data, level_map):
                                super().__init__(timeout=None)
                                self.members = sorted(
                                    members_data,
                                    key=lambda x: int(x.get('furnace_lv', 0) or 0),
                                    reverse=True
                                )
                                self.level_mapping = level_map
                                self.current_page = 0
                                self.members_per_page = 25
                                self.update_components()

                            def get_total_pages(self):
                                return (len(self.members) - 1) // self.members_per_page + 1

                            def update_components(self):
                                self.clear_items()
                                
                                # Get members for current page
                                start_idx = self.current_page * self.members_per_page
                                end_idx = start_idx + self.members_per_page
                                page_members = self.members[start_idx:end_idx]
                                
                                # Create select menu with member options
                                options = []
                                for idx, member in enumerate(page_members, start=start_idx + 1):
                                    nickname = member.get('nickname', 'Unknown')
                                    fid = member.get('fid', 'N/A')
                                    furnace_lv = int(member.get('furnace_lv', 0) or 0)
                                    level = self.level_mapping.get(furnace_lv, str(furnace_lv))
                                    
                                    options.append(
                                        discord.SelectOption(
                                            label=f"#{idx:02d} {nickname[:80]}",
                                            description=f"FID: {fid} | FC: {level}",
                                            value=str(fid),
                                            emoji="👤"
                                        )
                                    )
                                
                                select = discord.ui.Select(
                                    placeholder=f"Select a player (Page {self.current_page + 1}/{self.get_total_pages()})...",
                                    options=options,
                                    custom_id="player_select"
                                )
                                select.callback = self.player_selected
                                self.add_item(select)
                                
                                # Add pagination buttons if needed
                                if self.get_total_pages() > 1:
                                    prev_button = discord.ui.Button(
                                        emoji="⬅️",
                                        style=discord.ButtonStyle.secondary,
                                        disabled=(self.current_page == 0),
                                        row=1
                                    )
                                    prev_button.callback = self.previous_page
                                    self.add_item(prev_button)
                                    
                                    next_button = discord.ui.Button(
                                        emoji="➡️",
                                        style=discord.ButtonStyle.secondary,
                                        disabled=(self.current_page >= self.get_total_pages() - 1),
                                        row=1
                                    )
                                    next_button.callback = self.next_page
                                    self.add_item(next_button)

                            async def previous_page(self, button_interaction: discord.Interaction):
                                if self.current_page > 0:
                                    self.current_page -= 1
                                    self.update_components()
                                    await button_interaction.response.edit_message(
                                        content=f"**Select a player to view their profile:** (Page {self.current_page + 1}/{self.get_total_pages()})",
                                        view=self
                                    )
                                else:
                                    await button_interaction.response.defer()

                            async def next_page(self, button_interaction: discord.Interaction):
                                if self.current_page < self.get_total_pages() - 1:
                                    self.current_page += 1
                                    self.update_components()
                                    await button_interaction.response.edit_message(
                                        content=f"**Select a player to view their profile:** (Page {self.current_page + 1}/{self.get_total_pages()})",
                                        view=self
                                    )
                                else:
                                    await button_interaction.response.defer()

                            async def player_selected(self, select_interaction: discord.Interaction):
                                fid = select_interaction.data['values'][0]
                                
                                # Find the member
                                member = next((m for m in self.members if m.get('fid') == fid), None)
                                if not member:
                                    await select_interaction.response.send_message(
                                        "❌ Player not found.",
                                        ephemeral=True
                                    )
                                    return
                                
                                # Create profile embed
                                nickname = member.get('nickname', 'Unknown')
                                furnace_lv = int(member.get('furnace_lv', 0) or 0)
                                level = self.level_mapping.get(furnace_lv, str(furnace_lv))
                                avatar = member.get('avatar_image', '')
                                
                                # If no avatar in database, try to fetch from API
                                if not avatar:
                                    try:
                                        from cogs.login_handler import LoginHandler
                                        login_handler = LoginHandler()
                                        result = await login_handler.fetch_player_data(str(fid))
                                        
                                        print(f"DEBUG [bot_operations]: Fetching avatar from API for FID {fid}")
                                        
                                        if result['status'] == 'success' and result['data']:
                                            avatar = result['data'].get('avatar_image', '')
                                            print(f"DEBUG [bot_operations]: Avatar fetched from API: {avatar}")
                                            
                                            # Update member data in MongoDB with avatar
                                            if avatar and AllianceMembersAdapter:
                                                member['avatar_image'] = avatar
                                                AllianceMembersAdapter.upsert_member(str(fid), member)
                                                print(f"DEBUG [bot_operations]: Updated member {fid} with avatar in MongoDB")
                                    except Exception as e:
                                        print(f"DEBUG [bot_operations]: Error fetching avatar from API: {e}")
                                        import traceback
                                        traceback.print_exc()
                                
                                profile_embed = discord.Embed(
                                    title=f"👤 Player Profile",
                                    description=(
                                        f"**{nickname}**\n\n"
                                        f"```yaml\n"
                                        f"FID         : {fid}\n"
                                        f"Furnace Lv  : {level}\n"
                                        f"```"
                                    ),
                                    color=0x5865F2
                                )
                                
                                # Debug logging for avatar
                                print(f"DEBUG [bot_operations]: Setting avatar for {nickname} (FID: {fid})")
                                print(f"DEBUG [bot_operations]: Avatar URL: {avatar}")
                                
                                if avatar:
                                    try:
                                        profile_embed.set_image(url=avatar)
                                        print(f"DEBUG [bot_operations]: Avatar set successfully")
                                    except Exception as e:
                                        print(f"DEBUG [bot_operations]: Error setting avatar: {e}")
                                
                                profile_embed.set_footer(
                                    text="Stored in MongoDB",
                                    icon_url="https://cdn.discordapp.com/attachments/1435569370389807144/1445459239131680859/images_7_1.png"
                                )
                                
                                await select_interaction.response.send_message(
                                    embed=profile_embed,
                                    ephemeral=True
                                )

                        # Show profile selection menu
                        profile_view = ProfileSelectView(self.all_members, self.level_mapping)
                        total_pages = profile_view.get_total_pages()
                        await button_interaction.response.send_message(
                            f"**Select a player to view their profile:** (Page 1/{total_pages})",
                            view=profile_view,
                            ephemeral=True
                        )

                # Create and send persistent view
                view = PersistentMemberListView(alliance_id=alliance_id)
                members, alliance_name = await view.fetch_members_and_alliance_name()
                embed = await view.create_embed(members, alliance_name)
                await interaction.followup.send(embed=embed, view=view, ephemeral=False)
                return
            except Exception as e:
                print(f"View members error: {e}")
                await interaction.followup.send(
                    "❌ An error occurred while loading members.",
                    ephemeral=True
                )
                return
        
        # Handle add_members button
        if custom_id.startswith("add_members_"):
            try:
                alliance_id = int(custom_id.split("_")[2])
                
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
                
                # Create modal for FID input
                class AddMembersModal(discord.ui.Modal, title=f"Add Members"):
                    fids_input = discord.ui.TextInput(
                        label="Enter FIDs (comma-separated)",
                        placeholder="123456789,987654321,555555555",
                        style=discord.TextStyle.paragraph,
                        required=True,
                        max_length=1000
                    )
                    
                    def __init__(self, alliance_id_param, alliance_name_param):
                        super().__init__()
                        self.alliance_id = alliance_id_param
                        self.alliance_name = alliance_name_param
                    
                    async def on_submit(self, modal_interaction: discord.Interaction):
                        # Parse FIDs - support both comma and newline separated
                        import re
                        fids_str = self.fids_input.value.strip().replace(',', '\n')
                        fid_list = [fid.strip() for fid in fids_str.split('\n')]
                        valid_fids = [fid for fid in fid_list if re.match(r'^\d{9}$', fid)]
                        
                        if not valid_fids:
                            await modal_interaction.response.send_message(
                                "❌ No valid FIDs found. FIDs must be exactly 9 digits.",
                                ephemeral=True
                            )
                            return
                        
                        # Send initial processing message with animation
                        processing_embed = discord.Embed(
                            title="➕ Adding Members",
                            description=f"{'➕ Adding' if True else '➖ Removing'} **{len(valid_fids)}** member(s)...\n\n```\nPlease wait while we process your request.\n```",
                            color=0x5865F2
                        )
                        processing_embed.set_footer(text=f"Processing 0/{len(valid_fids)} FIDs...")
                        
                        await modal_interaction.response.send_message(embed=processing_embed, ephemeral=True)
                        
                        # Process FIDs with status updates
                        from cogs.login_handler import LoginHandler
                        from db.mongo_adapters import AllianceMembersAdapter
                        
                        login_handler = LoginHandler()
                        success_list = []
                        failed_list = []
                        
                        for idx, fid in enumerate(valid_fids, 1):
                            try:
                                # Fetch player data
                                result = await login_handler.fetch_player_data(fid)
                                if result['status'] == 'success' and result['data']:
                                    player_data = result['data']
                                    
                                    # Add to alliance
                                    member_data = {
                                        'fid': fid,
                                        'nickname': player_data.get('nickname', 'Unknown'),
                                        'furnace_lv': player_data.get('stove_lv', 0),
                                        'alliance': self.alliance_id,
                                        'avatar_image': player_data.get('avatar_image', '')
                                    }
                                    
                                    if AllianceMembersAdapter.upsert_member(str(fid), member_data):
                                        success_list.append({
                                            'fid': fid,
                                            'nickname': player_data.get('nickname', 'Unknown'),
                                            'furnace': player_data.get('stove_lv', 0)
                                        })
                                    else:
                                        failed_list.append({'fid': fid, 'reason': 'Database error'})
                                else:
                                    failed_list.append({'fid': fid, 'reason': 'Player not found'})
                            except Exception as e:
                                print(f"Error adding FID {fid}: {e}")
                                failed_list.append({'fid': fid, 'reason': str(e)[:50]})
                            
                            # Update progress every 3 FIDs or on last FID
                            if idx % 3 == 0 or idx == len(valid_fids):
                                progress_embed = discord.Embed(
                                    title="➕ Adding Members",
                                    description=f"Processing **{len(valid_fids)}** member(s)...\n\n```ansi\n\u001b[2;32m✓ Success: {len(success_list)}\n\u001b[2;31m✗ Failed:  {len(failed_list)}\n\u001b[2;37m⟳ Pending: {len(valid_fids) - idx}\u001b[0m\n```",
                                    color=0x5865F2
                                )
                                progress_embed.set_footer(text=f"Processing {idx}/{len(valid_fids)} FIDs...")
                                
                                try:
                                    await modal_interaction.edit_original_response(embed=progress_embed)
                                except:
                                    pass
                        
                        # Create final result embed
                        result_embed = discord.Embed(
                            title="➕ Add Members - Complete",
                            description=(
                                f"**{self.alliance_name}**\n\n"
                                f"```yaml\n"
                                f"Total      : {len(valid_fids)} FIDs\n"
                                f"✓ Success  : {len(success_list)}\n"
                                f"✗ Failed   : {len(failed_list)}\n"
                                f"```"
                            ),
                            color=0x57F287 if len(success_list) > 0 else 0xED4245
                        )
                        
                        # Add success details
                        if success_list:
                            success_text = ""
                            for member in success_list[:10]:
                                success_text += f"✓ `{member['fid']}` - {member['nickname']} (FC {member['furnace']})\n"
                            if len(success_list) > 10:
                                success_text += f"... and {len(success_list) - 10} more"
                            
                            result_embed.add_field(
                                name="✅ Successfully Added",
                                value=success_text,
                                inline=False
                            )
                        
                        # Add failure details
                        if failed_list:
                            failed_text = ""
                            for fail in failed_list[:10]:
                                failed_text += f"✗ `{fail['fid']}` - {fail['reason']}\n"
                            if len(failed_list) > 10:
                                failed_text += f"... and {len(failed_list) - 10} more"
                            
                            result_embed.add_field(
                                name="❌ Failed to Add",
                                value=failed_text,
                                inline=False
                            )
                        
                                            result_embed.set_footer(text=f"Showing 20 of {len(results)} results")
                        
                        await modal_interaction.edit_original_response(embed=result_embed)
                
                await interaction.response.send_modal(AddMembersModal(alliance_id, alliance_name))
                return
            except Exception as e:
                print(f"Add members error: {e}")
                await interaction.response.send_message(
                    f"❌ An error occurred: {e}",
                    ephemeral=True
                )
                return
        
        # Handle remove_members button
        if custom_id.startswith("remove_members_"):
            try:
                alliance_id = int(custom_id.split("_")[2])
                
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
                
                # Create modal for FID input
                class RemoveMembersModal(discord.ui.Modal, title=f"Remove Members"):
                    fids_input = discord.ui.TextInput(
                        label="Enter FIDs (comma-separated)",
                        placeholder="123456789,987654321,555555555",
                        style=discord.TextStyle.paragraph,
                        required=True,
                        max_length=1000
                    )
                    
                    def __init__(self, alliance_id_param, alliance_name_param):
                        super().__init__()
                        self.alliance_id = alliance_id_param
                        self.alliance_name = alliance_name_param
                    
                    async def on_submit(self, modal_interaction: discord.Interaction):
                        # Parse FIDs - support both comma and newline separated
                        import re
                        fids_str = self.fids_input.value.strip().replace(',', '\n')
                        fid_list = [fid.strip() for fid in fids_str.split('\n')]
                        valid_fids = [fid for fid in fid_list if re.match(r'^\d{9}$', fid)]
                        
                        if not valid_fids:
                            await modal_interaction.response.send_message(
                                "❌ No valid FIDs found. FIDs must be exactly 9 digits.",
                                ephemeral=True
                            )
                            return
                        
                        # Send initial processing message with animation
                        processing_embed = discord.Embed(
                            title="➖ Removing Members",
                            description=f"➖ Removing **{len(valid_fids)}** member(s)...\n\n```\nPlease wait while we process your request.\n```",
                            color=0x5865F2
                        )
                        processing_embed.set_footer(text=f"Processing 0/{len(valid_fids)} FIDs...")
                        
                        await modal_interaction.response.send_message(embed=processing_embed, ephemeral=True)
                        
                        # Remove members with status updates
                        from db.mongo_adapters import AllianceMembersAdapter
                        
                        success_list = []
                        failed_list = []
                        
                        for idx, fid in enumerate(valid_fids, 1):
                            try:
                                # Get member info before deleting
                                all_members = AllianceMembersAdapter.get_all_members()
                                member = next((m for m in all_members if m.get('fid') == fid), None)
                                
                                if AllianceMembersAdapter.delete_member(fid):
                                    success_list.append({
                                        'fid': fid,
                                        'nickname': member.get('nickname', 'Unknown') if member else 'Unknown'
                                    })
                                else:
                                    failed_list.append({'fid': fid, 'reason': 'Not found in database'})
                            except Exception as e:
                                print(f"Error removing FID {fid}: {e}")
                                failed_list.append({'fid': fid, 'reason': str(e)[:50]})
                            
                            # Update progress every 3 FIDs or on last FID
                            if idx % 3 == 0 or idx == len(valid_fids):
                                progress_embed = discord.Embed(
                                    title="➖ Removing Members",
                                    description=f"Processing **{len(valid_fids)}** member(s)...\n\n```ansi\n\u001b[2;32m✓ Removed: {len(success_list)}\n\u001b[2;31m✗ Failed:  {len(failed_list)}\n\u001b[2;37m⟳ Pending: {len(valid_fids) - idx}\u001b[0m\n```",
                                    color=0x5865F2
                                )
                                progress_embed.set_footer(text=f"Processing {idx}/{len(valid_fids)} FIDs...")
                                
                                try:
                                    await modal_interaction.edit_original_response(embed=progress_embed)
                                except:
                                    pass
                        
                        # Create final result embed
                        result_embed = discord.Embed(
                            title="➖ Remove Members - Complete",
                            description=(
                                f"**{self.alliance_name}**\n\n"
                                f"```yaml\n"
                                f"Total      : {len(valid_fids)} FIDs\n"
                                f"✓ Removed  : {len(success_list)}\n"
                                f"✗ Failed   : {len(failed_list)}\n"
                                f"```"
                            ),
                            color=0x57F287 if len(success_list) > 0 else 0xED4245
                        )
                        
                        # Add success details
                        if success_list:
                            success_text = ""
                            for member in success_list[:10]:
                                success_text += f"✓ `{member['fid']}` - {member['nickname']}\n"
                            if len(success_list) > 10:
                                success_text += f"... and {len(success_list) - 10} more"
                            
                            result_embed.add_field(
                                name="✅ Successfully Removed",
                                value=success_text,
                                inline=False
                            )
                        
                        # Add failure details
                        if failed_list:
                            failed_text = ""
                            for fail in failed_list[:10]:
                                failed_text += f"✗ `{fail['fid']}` - {fail['reason']}\n"
                            if len(failed_list) > 10:
                                failed_text += f"... and {len(failed_list) - 10} more"
                            
                            result_embed.add_field(
                                name="❌ Failed to Remove",
                                value=failed_text,
                                inline=False
                            )
                        
                                            result_embed.set_footer(text=f"Showing 20 of {len(results)} results")
                        
                        await modal_interaction.edit_original_response(embed=result_embed)
                
                await interaction.response.send_modal(RemoveMembersModal(alliance_id, alliance_name))
                return
            except Exception as e:
                print(f"Remove members error: {e}")
                await interaction.response.send_message(
                    f"❌ An error occurred: {e}",
                    ephemeral=True
                )
                return
        
        # Handle return to settings
        if custom_id == "return_to_settings":
            # Import the settings command from alliance cog
            alliance_cog = self.bot.get_cog("Alliance")
            if alliance_cog:
                # Call the settings method directly
                await alliance_cog.settings(interaction)
            else:
                await interaction.response.send_message(
                    "❌ Settings menu not available.",
                    ephemeral=True
                )
            return
        
        # Handle return to /manage
        if custom_id == "return_to_manage":
            try:
                self.settings_cursor.execute("SELECT is_initial FROM admin WHERE id = ?", (interaction.user.id,))
                result = self.settings_cursor.fetchone()
                
                if (not result or result[0] != 1) and not await is_bot_owner(self.bot, interaction.user.id):
                    await interaction.response.send_message(
                        "❌ Only global administrators can use this command.",
                        ephemeral=True
                    )
                    return

                embed = discord.Embed(
                    title="⚙️ Dashboard",
                    description=(
                        "```ansi\n"
                        "\u001b[2;36m╔═══════════════════════════════════╗\n"
                        "\u001b[2;36m║  \u001b[1;37mCONTROL PANEL\u001b[0m\u001b[2;36m     ║\n"
                        "\u001b[2;36m╚═══════════════════════════════════╝\u001b[0m\n"
                        "```\n"
                        "**Select an operation to continue:**\n\n"
                        "👥 **Member Operations**\n"
                        "   ▸ Manage alliance members\n"
                        "   ▸  Update Alliance log\n\n"
                        "📁 **Records Management**\n"
                        "   ▸ Keep track of Players\n"
                        "   ▸ Create and manage groups\n"
                    ),
                    color=0x2B2D31
                )
                embed.set_footer(
                    text=f"{interaction.guild.name} x Magnus🚀",
                    icon_url="https://cdn.discordapp.com/attachments/1435569370389807144/1436745053442805830/unnamed_5.png"
                )

                view = discord.ui.View()
                view.add_item(discord.ui.Button(
                    label="Member Operations",
                    emoji="👥",
                    style=discord.ButtonStyle.secondary,
                    custom_id="manage_member_ops",
                    row=0
                ))
                view.add_item(discord.ui.Button(
                    label="Records",
                    emoji="📁",
                    style=discord.ButtonStyle.secondary,
                    custom_id="records_menu",
                    row=0
                ))

                await interaction.response.edit_message(embed=embed, view=view)
            except Exception as e:
                print(f"Return to manage error: {e}")
                await interaction.response.send_message(
                    "❌ An error occurred.",
                    ephemeral=True
                )
            return
        
        if custom_id == "alliance_control_messages":
            try:
                self.settings_cursor.execute("SELECT is_initial FROM admin WHERE id = ?", (interaction.user.id,))
                result = self.settings_cursor.fetchone()
                
                if (not result or result[0] != 1) and not await is_bot_owner(self.bot, interaction.user.id):
                    await interaction.response.send_message(
                        "❌ Only global administrators can use this command.", 
                        ephemeral=True
                    )
                    return

                self.settings_cursor.execute("SELECT value FROM auto LIMIT 1")
                result = self.settings_cursor.fetchone()
                current_value = result[0] if result else 1

                embed = discord.Embed(
                    title="💬 Alliance Control Messages Settings",
                    description=f"Alliance Control Information Message is Currently {'On' if current_value == 1 else 'Off'}",
                    color=discord.Color.green() if current_value == 1 else discord.Color.red()
                )

                view = discord.ui.View()
                
                open_button = discord.ui.Button(
                    label="Turn On",
                    emoji="✅",
                    style=discord.ButtonStyle.success,
                    custom_id="control_messages_open",
                    disabled=current_value == 1
                )
                
                close_button = discord.ui.Button(
                    label="Turn Off",
                    emoji="❌",
                    style=discord.ButtonStyle.danger,
                    custom_id="control_messages_close",
                    disabled=current_value == 0
                )

                async def open_callback(button_interaction: discord.Interaction):
                    self.settings_cursor.execute("UPDATE auto SET value = 1")
                    self.settings_db.commit()
                    
                    embed.description = "Alliance Control Information Message Turned On"
                    embed.color = discord.Color.green()
                    
                    open_button.disabled = True
                    close_button.disabled = False
                    
                    await button_interaction.response.edit_message(embed=embed, view=view)

                async def close_callback(button_interaction: discord.Interaction):
                    self.settings_cursor.execute("UPDATE auto SET value = 0")
                    self.settings_db.commit()
                    
                    embed.description = "Alliance Control Information Message Turned Off"
                    embed.color = discord.Color.red()
                    
                    open_button.disabled = False
                    close_button.disabled = True
                    
                    await button_interaction.response.edit_message(embed=embed, view=view)

                open_button.callback = open_callback
                close_button.callback = close_callback

                view.add_item(open_button)
                view.add_item(close_button)

                await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

            except Exception as e:
                print(f"Alliance control messages error: {e}")
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "❌ An error occurred while managing alliance control messages.",
                        ephemeral=True
                    )
                    
        elif custom_id == "assign_server_alliance":
            try:
                # Check if user is global admin or bot owner
                self.settings_cursor.execute("SELECT is_initial FROM admin WHERE id = ?", (interaction.user.id,))
                result = self.settings_cursor.fetchone()
                
                if (not result or result[0] != 1) and not await is_bot_owner(self.bot, interaction.user.id):
                    await interaction.response.send_message(
                        "❌ Only global administrators can assign server alliances.", 
                        ephemeral=True
                    )
                    return

                # Check if MongoDB is enabled
                if not mongo_enabled() or not ServerAllianceAdapter:
                    await interaction.response.send_message(
                        "❌ MongoDB not enabled. Cannot assign server alliance.",
                        ephemeral=True
                    )
                    return

                # Get all servers the bot is in
                all_guilds = sorted(self.bot.guilds, key=lambda g: g.name.lower())
                
                if not all_guilds:
                    await interaction.response.send_message(
                        "❌ Bot is not in any servers.",
                        ephemeral=True
                    )
                    return

                # Create server selection view
                class ServerSelectionView(discord.ui.View):
                    def __init__(self, guilds_list, current_page=0, search_term=None):
                        super().__init__(timeout=180)
                        self.guilds = guilds_list
                        self.current_page = current_page
                        self.search_term = search_term
                        self.servers_per_page = 25  # Discord limit for select options
                        self.total_pages = (len(guilds_list) + self.servers_per_page - 1) // self.servers_per_page
                        
                        # Add server selection dropdown
                        start_idx = current_page * self.servers_per_page
                        end_idx = min(start_idx + self.servers_per_page, len(guilds_list))
                        
                        seen_server_ids = set()
                        server_options = []
                        for guild in guilds_list[start_idx:]:
                            if guild.id in seen_server_ids:
                                continue
                            seen_server_ids.add(guild.id)
                            
                            # Get current alliance assignment status
                            alliance_id = ServerAllianceAdapter.get_alliance(guild.id)
                            has_alliance = "🏰" if alliance_id else "⚪"
                            label = f"{guild.name[:90]}"  # Truncate to fit Discord limits
                            description = f"ID: {guild.id} | {has_alliance}"
                            server_options.append(
                                discord.SelectOption(
                                    label=label,
                                    value=str(guild.id),
                                    description=description[:100],
                                    emoji="🏰" if alliance_id else "⚪"
                                )
                            )
                            if len(server_options) >= 25:
                                break
                        
                        server_select = discord.ui.Select(
                            placeholder="Select a server to assign alliance...",
                            options=server_options,
                            custom_id="server_select_alliance"
                        )
                        server_select.callback = self.server_selected
                        self.add_item(server_select)
                        
                        # Add search button
                        search_button = discord.ui.Button(
                            label="🔍 Search Server",
                            style=discord.ButtonStyle.primary,
                            custom_id="search_server_alliance",
                            row=1
                        )
                        search_button.callback = self.search_server
                        self.add_item(search_button)
                        
                        # Add clear search button if searching
                        if search_term:
                            clear_button = discord.ui.Button(
                                label="✖ Clear Search",
                                style=discord.ButtonStyle.secondary,
                                custom_id="clear_search_alliance",
                                row=1
                            )
                            clear_button.callback = self.clear_search
                            self.add_item(clear_button)
                        
                        # Add pagination buttons if needed
                        if self.total_pages > 1:
                            if current_page > 0:
                                prev_button = discord.ui.Button(
                                    label="◀ Previous",
                                    style=discord.ButtonStyle.secondary,
                                    custom_id="prev_page_alliance",
                                    row=2
                                )
                                prev_button.callback = self.previous_page
                                self.add_item(prev_button)
                            
                            if current_page < self.total_pages - 1:
                                next_button = discord.ui.Button(
                                    label="Next ▶",
                                    style=discord.ButtonStyle.secondary,
                                    custom_id="next_page_alliance",
                                    row=2
                                )
                                next_button.callback = self.next_page
                                self.add_item(next_button)
                    
                    async def previous_page(self, button_interaction: discord.Interaction):
                        new_page = max(0, self.current_page - 1)
                        new_view = ServerSelectionView(self.guilds, new_page, self.search_term)
                        embed = self.create_embed(new_page)
                        await button_interaction.response.edit_message(embed=embed, view=new_view)
                    
                    async def next_page(self, button_interaction: discord.Interaction):
                        new_page = min(self.total_pages - 1, self.current_page + 1)
                        new_view = ServerSelectionView(self.guilds, new_page, self.search_term)
                        embed = self.create_embed(new_page)
                        await button_interaction.response.edit_message(embed=embed, view=new_view)
                    
                    def create_embed(self, page):
                        start_idx = page * self.servers_per_page
                        end_idx = min(start_idx + self.servers_per_page, len(self.guilds))
                        
                        title = "🏰 Assign Server Alliance"
                        if self.search_term:
                            title += f" - Search: '{self.search_term}'"
                        
                        embed = discord.Embed(
                            title=title,
                            description=(
                                "```ansi\n"
                                "\u001b[2;36m╔═══════════════════════════════════╗\n"
                                "\u001b[2;36m║  \u001b[1;37mSELECT A SERVER\u001b[0m\u001b[2;36m              ║\n"
                                "\u001b[2;36m╚═══════════════════════════════════╝\u001b[0m\n"
                                "```\n"
                                f"**Select a server from the dropdown below:**\n\n"
                                f"🏰 = Alliance assigned | ⚪ = No alliance\n"
                                f"Showing {start_idx + 1}-{end_idx} of {len(self.guilds)} servers"
                            ),
                            color=0x2B2D31
                        )
                        
                        if self.total_pages > 1:
                            embed.set_footer(text=f"Page {page + 1}/{self.total_pages}")
                        
                        return embed
                    
                    async def search_server(self, search_interaction: discord.Interaction):
                        """Show search modal for finding servers by name"""
                        class ServerSearchModal(discord.ui.Modal, title="🔍 Search Server"):
                            search_input = discord.ui.TextInput(
                                label="Server Name",
                                placeholder="Enter server name to search...",
                                style=discord.TextStyle.short,
                                required=True,
                                min_length=1,
                                max_length=50
                            )
                            
                            def __init__(self, all_guilds_list):
                                super().__init__()
                                self.all_guilds = all_guilds_list
                            
                            async def on_submit(self, modal_interaction: discord.Interaction):
                                search_term = self.search_input.value.lower()
                                
                                # Search for matching servers
                                matches = [
                                    g for g in self.all_guilds 
                                    if search_term in g.name.lower()
                                ]
                                
                                if not matches:
                                    await modal_interaction.response.send_message(
                                        f"❌ No servers found matching '{self.search_input.value}'",
                                        ephemeral=True
                                    )
                                    return
                                
                                # Show filtered results
                                new_view = ServerSelectionView(matches, 0, self.search_input.value)
                                embed = new_view.create_embed(0)
                                await modal_interaction.response.edit_message(embed=embed, view=new_view)
                        
                        modal = ServerSearchModal(all_guilds)
                        await search_interaction.response.send_modal(modal)
                    
                    async def clear_search(self, clear_interaction: discord.Interaction):
                        """Clear search and show all servers"""
                        new_view = ServerSelectionView(all_guilds, 0, None)
                        embed = new_view.create_embed(0)
                        await clear_interaction.response.edit_message(embed=embed, view=new_view)
                    
                    async def server_selected(self, select_interaction: discord.Interaction):
                        """Handle server selection and show alliance selection"""
                        selected_guild_id = int(select_interaction.data['values'][0])
                        selected_guild = discord.utils.get(self.guilds, id=selected_guild_id)
                        
                        if not selected_guild:
                            await select_interaction.response.send_message(
                                "❌ Server not found.",
                                ephemeral=True
                            )
                            return
                        
                        # Get all alliances from both MongoDB and SQLite to ensure full list
                        alliances_map = {} # alliance_id -> name
                        
                        # Try MongoDB
                        if mongo_enabled():
                            try:
                                from db.mongo_adapters import AlliancesAdapter
                                for doc in AlliancesAdapter.get_all():
                                    aid = doc.get('alliance_id') or doc.get('id')
                                    if aid:
                                        alliances_map[int(aid)] = doc.get('name', 'Unknown')
                            except Exception as e:
                                print(f"MongoDB alliance fetch failed: {e}")

                        # Always check SQLite as fallback/supplement
                        try:
                            with sqlite3.connect('db/alliance.sqlite') as alliance_db:
                                alliance_cursor = alliance_db.cursor()
                                alliance_cursor.execute("SELECT alliance_id, name FROM alliance_list")
                                for aid, name in alliance_cursor.fetchall():
                                    alliances_map[int(aid)] = name
                        except Exception as e:
                            print(f"SQLite alliance fetch failed: {e}")
                        
                        alliances = sorted(alliances_map.items(), key=lambda x: x[1])

                        if not alliances:
                            await select_interaction.response.send_message(
                                "❌ No alliances found. Please create an alliance first.",
                                ephemeral=True
                            )
                            return

                        # Check if server already has an assigned alliance
                        current_alliance_id = ServerAllianceAdapter.get_alliance(selected_guild_id)
                        current_alliance_name = None
                        if current_alliance_id:
                            try:
                                from db.mongo_adapters import mongo_enabled, AlliancesAdapter
                                if mongo_enabled():
                                    target_alliance = AlliancesAdapter.get(current_alliance_id)
                                    if target_alliance:
                                        current_alliance_name = target_alliance.get('name')
                                else:
                                    raise Exception("Mongo fallback")
                            except Exception:
                                try:
                                    alliance_db = sqlite3.connect('db/alliance.sqlite')
                                    alliance_cursor = alliance_db.cursor()
                                    alliance_cursor.execute("SELECT name FROM alliance_list WHERE alliance_id = ?", (current_alliance_id,))
                                    result = alliance_cursor.fetchone()
                                    current_alliance_name = result[0] if result else None
                                except sqlite3.OperationalError:
                                    current_alliance_name = None
                            
                            if not current_alliance_name:
                                current_alliance_name = f"Alliance {current_alliance_id}"

                        # Create embed
                        embed = discord.Embed(
                            title="🏰 Assign Alliance",
                            description=(
                                f"**Server:** {selected_guild.name}\n"
                                f"**Server ID:** `{selected_guild_id}`\n\n"
                                "Select an alliance to assign to this server.\n\n"
                                "**What this does:**\n"
                                "• Members can use `!Add <FID>` to add players to this alliance\n"
                                "• Members can use `!Remove <FID>` to remove players from this alliance\n\n"
                                f"**Current Assignment:** {current_alliance_name if current_alliance_name else 'None'}\n"
                                "━━━━━━━━━━━━━━━━━━━━━━"
                            ),
                            color=discord.Color.blue()
                        )

                        # Prepare alliances with member counts for the paginated view
                        alliances_with_counts = []
                        seen_alliance_ids = set()
                        for alliance_id, name in alliances:
                            if alliance_id in seen_alliance_ids:
                                continue
                            seen_alliance_ids.add(alliance_id)
                            
                            member_count = 0
                            try:
                                with sqlite3.connect('db/users.sqlite') as users_db:
                                    users_cursor = users_db.cursor()
                                    users_cursor.execute("SELECT COUNT(*) FROM users WHERE alliance = ?", (alliance_id,))
                                    member_count = users_cursor.fetchone()[0]
                            except Exception:
                                pass
                            
                            alliances_with_counts.append((alliance_id, name, member_count))

                        # Use the paginated AllianceSelectView
                        from .alliance_member_operations import AllianceSelectView
                        alliance_view = AllianceSelectView(alliances_with_counts, self, user_id=interaction.user.id)

                        async def alliance_select_callback(alliance_interaction: discord.Interaction):
                            try:
                                selected_alliance_id = int(alliance_view.current_select.values[0])
                                
                                # Get alliance name from the list
                                alliance_name = next((name for aid, name, count in alliances_with_counts if aid == selected_alliance_id), f"Alliance {selected_alliance_id}")

                                # Assign alliance to server
                                success = ServerAllianceAdapter.set_alliance(
                                    guild_id=selected_guild_id,
                                    alliance_id=selected_alliance_id,
                                    assigned_by=alliance_interaction.user.id
                                )

                                if success:
                                    success_embed = discord.Embed(
                                        title="✅ Server Alliance Assigned",
                                        description=(
                                            f"**Alliance:** {alliance_name}\n"
                                            f"**Alliance ID:** `{selected_alliance_id}`\n"
                                            f"**Server:** {selected_guild.name}\n"
                                            f"**Server ID:** `{selected_guild_id}`\n\n"
                                            "**Available Commands:**\n"
                                            "• `!Add 123456789` - Add member by FID\n"
                                            "• `!Add 123456789,987654321` - Add multiple members\n"
                                            "• `!Remove 123456789` - Remove member by FID\n"
                                            "• `!Remove 123456789,987654321` - Remove multiple members"
                                        ),
                                        color=discord.Color.green()
                                    )
                                    success_embed.set_footer(
                                        text=f"Assigned by {alliance_interaction.user.display_name}",
                                        icon_url=alliance_interaction.user.display_avatar.url
                                    )
                                    await alliance_interaction.response.edit_message(embed=success_embed, view=None)
                                else:
                                    await alliance_interaction.response.send_message(
                                        "❌ Failed to assign alliance to server.",
                                        ephemeral=True
                                    )

                            except Exception as e:
                                print(f"Alliance select error: {e}")
                                await alliance_interaction.response.send_message(
                                    "❌ An error occurred while assigning the alliance.",
                                    ephemeral=True
                                )

                        alliance_view.callback = alliance_select_callback

                        # Add remove assignment button if there's a current assignment
                        if current_alliance_id:
                            remove_button = discord.ui.Button(
                                label="Remove Assignment",
                                emoji="🗑️",
                                style=discord.ButtonStyle.danger,
                                custom_id="remove_server_alliance"
                            )

                            async def remove_callback(button_interaction: discord.Interaction):
                                try:
                                    success = ServerAllianceAdapter.remove_alliance(selected_guild_id)
                                    if success:
                                        remove_embed = discord.Embed(
                                            title="✅ Server Alliance Removed",
                                            description=(
                                                f"Removed alliance assignment from:\n"
                                                f"**Server:** {selected_guild.name}\n"
                                                f"**Server ID:** `{selected_guild_id}`"
                                            ),
                                            color=discord.Color.green()
                                        )
                                        remove_embed.set_footer(
                                            text=f"Removed by {button_interaction.user.display_name}",
                                            icon_url=button_interaction.user.display_avatar.url
                                        )
                                        await button_interaction.response.edit_message(embed=remove_embed, view=None)
                                    else:
                                        await button_interaction.response.send_message(
                                            "❌ Failed to remove alliance assignment.",
                                            ephemeral=True
                                        )
                                except Exception as e:
                                    print(f"Remove alliance error: {e}")
                                    await button_interaction.response.send_message(
                                        "❌ An error occurred while removing the alliance.",
                                        ephemeral=True
                                    )

                            remove_button.callback = remove_callback
                            alliance_view.add_item(remove_button)

                        await select_interaction.response.edit_message(embed=embed, view=alliance_view)
                
                # Create and send initial server selection view
                view = ServerSelectionView(all_guilds, 0)
                embed = view.create_embed(0)
                await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

            except Exception as e:
                print(f"Assign server alliance error: {e}")
                import traceback
                traceback.print_exc()
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "❌ An error occurred while processing your request.",
                        ephemeral=True
                    )
                    
        elif custom_id == "set_member_list_password":
            try:
                # Check if user is global admin or bot owner
                self.settings_cursor.execute("SELECT is_initial FROM admin WHERE id = ?", (interaction.user.id,))
                result = self.settings_cursor.fetchone()
                
                if (not result or result[0] != 1) and not await is_bot_owner(self.bot, interaction.user.id):
                    await interaction.response.send_message(
                        "❌ Only global administrators can set member list passwords.", 
                        ephemeral=True
                    )
                    return

                # Check if MongoDB is enabled
                if not mongo_enabled() or not ServerAllianceAdapter:
                    await interaction.response.send_message(
                        "❌ MongoDB not enabled. Cannot set password.",
                        ephemeral=True
                    )
                    return

                # Get all servers the bot is in
                all_guilds = sorted(self.bot.guilds, key=lambda g: g.name.lower())
                
                if not all_guilds:
                    await interaction.response.send_message(
                        "❌ Bot is not in any servers.",
                        ephemeral=True
                    )
                    return

                # Create server selection view
                class ServerSelectionView(discord.ui.View):
                    def __init__(self, guilds_list, current_page=0, search_term=None):
                        super().__init__(timeout=180)
                        self.guilds = guilds_list
                        self.current_page = current_page
                        self.search_term = search_term
                        self.servers_per_page = 25  # Discord limit for select options
                        self.total_pages = (len(guilds_list) + self.servers_per_page - 1) // self.servers_per_page
                        
                        # Add server selection dropdown
                        start_idx = current_page * self.servers_per_page
                        end_idx = min(start_idx + self.servers_per_page, len(guilds_list))
                        
                        server_options = []
                        for guild in guilds_list[start_idx:end_idx]:
                            # Get current password status
                            has_password = "🔐" if ServerAllianceAdapter.get_password(guild.id) else "🔓"
                            label = f"{guild.name[:90]}"  # Truncate to fit Discord limits
                            description = f"ID: {guild.id} | {has_password}"
                            server_options.append(
                                discord.SelectOption(
                                    label=label,
                                    value=str(guild.id),
                                    description=description[:100],
                                    emoji="🏰"
                                )
                            )
                        
                        server_select = discord.ui.Select(
                            placeholder="Select a server to set password...",
                            options=server_options,
                            custom_id="server_select"
                        )
                        server_select.callback = self.server_selected
                        self.add_item(server_select)
                        
                        # Add search button
                        search_button = discord.ui.Button(
                            label="🔍 Search Server",
                            style=discord.ButtonStyle.primary,
                            custom_id="search_server_pwd",
                            row=1
                        )
                        search_button.callback = self.search_server
                        self.add_item(search_button)
                        
                        # Add clear search button if searching
                        if search_term:
                            clear_button = discord.ui.Button(
                                label="✖ Clear Search",
                                style=discord.ButtonStyle.secondary,
                                custom_id="clear_search_pwd",
                                row=1
                            )
                            clear_button.callback = self.clear_search
                            self.add_item(clear_button)
                        
                        # Add pagination buttons if needed
                        if self.total_pages > 1:
                            if current_page > 0:
                                prev_button = discord.ui.Button(
                                    label="◀ Previous",
                                    style=discord.ButtonStyle.secondary,
                                    custom_id="prev_page_pwd",
                                    row=2
                                )
                                prev_button.callback = self.previous_page
                                self.add_item(prev_button)
                            
                            if current_page < self.total_pages - 1:
                                next_button = discord.ui.Button(
                                    label="Next ▶",
                                    style=discord.ButtonStyle.secondary,
                                    custom_id="next_page_pwd",
                                    row=2
                                )
                                next_button.callback = self.next_page
                                self.add_item(next_button)
                    
                    async def previous_page(self, button_interaction: discord.Interaction):
                        new_page = max(0, self.current_page - 1)
                        new_view = ServerSelectionView(self.guilds, new_page, self.search_term)
                        embed = self.create_embed(new_page)
                        await button_interaction.response.edit_message(embed=embed, view=new_view)
                    
                    async def next_page(self, button_interaction: discord.Interaction):
                        new_page = min(self.total_pages - 1, self.current_page + 1)
                        new_view = ServerSelectionView(self.guilds, new_page, self.search_term)
                        embed = self.create_embed(new_page)
                        await button_interaction.response.edit_message(embed=embed, view=new_view)
                    
                    def create_embed(self, page):
                        start_idx = page * self.servers_per_page
                        end_idx = min(start_idx + self.servers_per_page, len(self.guilds))
                        
                        title = "🔐 Set Member List Password"
                        if self.search_term:
                            title += f" - Search: '{self.search_term}'"
                        
                        embed = discord.Embed(
                            title=title,
                            description=(
                                "```ansi\n"
                                "\u001b[2;36m╔═══════════════════════════════════╗\n"
                                "\u001b[2;36m║  \u001b[1;37mSELECT A SERVER\u001b[0m\u001b[2;36m              ║\n"
                                "\u001b[2;36m╚═══════════════════════════════════╝\u001b[0m\n"
                                "```\n"
                                f"**Select a server from the dropdown below:**\n\n"
                                f"🔐 = Password set | 🔓 = No password\n"
                                f"Showing {start_idx + 1}-{end_idx} of {len(self.guilds)} servers"
                            ),
                            color=0x2B2D31
                        )
                        
                        if self.total_pages > 1:
                            embed.set_footer(text=f"Page {page + 1}/{self.total_pages}")
                        
                        return embed
                    
                    async def search_server(self, search_interaction: discord.Interaction):
                        """Show search modal for finding servers by name"""
                        class ServerSearchModal(discord.ui.Modal, title="🔍 Search Server"):
                            search_input = discord.ui.TextInput(
                                label="Server Name",
                                placeholder="Enter server name to search...",
                                style=discord.TextStyle.short,
                                required=True,
                                min_length=1,
                                max_length=50
                            )
                            
                            def __init__(self, all_guilds_list):
                                super().__init__()
                                self.all_guilds = all_guilds_list
                            
                            async def on_submit(self, modal_interaction: discord.Interaction):
                                search_term = self.search_input.value.lower()
                                
                                # Search for matching servers
                                matches = [
                                    g for g in self.all_guilds 
                                    if search_term in g.name.lower()
                                ]
                                
                                if not matches:
                                    await modal_interaction.response.send_message(
                                        f"❌ No servers found matching '{self.search_input.value}'",
                                        ephemeral=True
                                    )
                                    return
                                
                                # Show filtered results
                                new_view = ServerSelectionView(matches, 0, self.search_input.value)
                                embed = new_view.create_embed(0)
                                await modal_interaction.response.edit_message(embed=embed, view=new_view)
                        
                        modal = ServerSearchModal(all_guilds)
                        await search_interaction.response.send_modal(modal)
                    
                    async def clear_search(self, clear_interaction: discord.Interaction):
                        """Clear search and show all servers"""
                        new_view = ServerSelectionView(all_guilds, 0, None)
                        embed = new_view.create_embed(0)
                        await clear_interaction.response.edit_message(embed=embed, view=new_view)
                    
                    async def server_selected(self, select_interaction: discord.Interaction):
                        """Handle server selection and show password modal"""
                        selected_guild_id = int(select_interaction.data['values'][0])
                        selected_guild = discord.utils.get(self.guilds, id=selected_guild_id)
                        
                        if not selected_guild:
                            await select_interaction.response.send_message(
                                "❌ Server not found.",
                                ephemeral=True
                            )
                            return
                        
                        # Create password modal for selected server
                        class SetPasswordModal(discord.ui.Modal, title="🔐 Set Member List Password"):
                            password_input = discord.ui.TextInput(
                                label="Password",
                                placeholder="Enter password for !showlist command",
                                style=discord.TextStyle.short,
                                required=True,
                                max_length=50
                            )
                            
                            def __init__(self, guild_id: int, guild_name: str):
                                super().__init__()
                                self.guild_id = guild_id
                                self.guild_name = guild_name
                            
                            async def on_submit(self, modal_interaction: discord.Interaction):
                                try:
                                    password = self.password_input.value.strip()
                                    
                                    if not password:
                                        await modal_interaction.response.send_message(
                                            "❌ Password cannot be empty.",
                                            ephemeral=True
                                        )
                                        return
                                    
                                    # Save password to MongoDB for selected server
                                    success = ServerAllianceAdapter.set_password(
                                        guild_id=self.guild_id,
                                        password=password,
                                        set_by=modal_interaction.user.id
                                    )
                                    
                                    if success:
                                        embed = discord.Embed(
                                            title="✅ Password Set Successfully",
                                            description=(
                                                f"**Server:** {self.guild_name}\n"
                                                f"**Server ID:** `{self.guild_id}`\n"
                                                f"**Password:** `{password}`\n\n"
                                                "**Usage:**\n"
                                                "Members can now use `!showlist` and enter this password to view the member list."
                                            ),
                                            color=discord.Color.green()
                                        )
                                        embed.set_footer(
                                            text=f"Set by {modal_interaction.user.display_name}",
                                            icon_url=modal_interaction.user.display_avatar.url
                                        )
                                        await modal_interaction.response.send_message(embed=embed, ephemeral=True)
                                    else:
                                        await modal_interaction.response.send_message(
                                            "❌ Failed to set password.",
                                            ephemeral=True
                                        )
                                
                                except Exception as e:
                                    print(f"Set password error: {e}")
                                    await modal_interaction.response.send_message(
                                        "❌ An error occurred while setting the password.",
                                        ephemeral=True
                                    )
                        
                        # Show password modal
                        modal = SetPasswordModal(selected_guild.id, selected_guild.name)
                        await select_interaction.response.send_modal(modal)
                
                # Create and send initial server selection view
                view = ServerSelectionView(all_guilds, 0)
                embed = view.create_embed(0)
                await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

            except Exception as e:
                print(f"Set member list password error: {e}")
                import traceback
                traceback.print_exc()
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "❌ An error occurred while processing your request.",
                        ephemeral=True
                    )
                    
        elif custom_id in ["assign_alliance", "add_admin", "remove_admin", "main_menu", "bot_status", "bot_settings"]:
            try:
                if custom_id == "assign_alliance":
                    try:
                        with sqlite3.connect('db/settings.sqlite') as settings_db:
                            cursor = settings_db.cursor()
                            cursor.execute("SELECT is_initial FROM admin WHERE id = ?", (interaction.user.id,))
                            result = cursor.fetchone()
                            
                            if (not result or result[0] != 1) and not await is_bot_owner(self.bot, interaction.user.id):
                                await interaction.response.send_message(
                                    "❌ Only global administrators can use this command.", 
                                    ephemeral=True
                                )
                                return

                            if mongo_enabled() and AdminsAdapter:
                                try:
                                    # Assuming get_all returns list of dicts with 'id' and 'is_initial'
                                    admins_data = AdminsAdapter.get_all()
                                    admins = [(d['id'], d.get('is_initial', 0)) for d in admins_data]
                                except Exception as e:
                                    print(f"MongoDB fetch admins failed: {e}")
                                    admins = []
                                    # Fallback to SQLite if MongoDB fails or returns empty?
                                    if not admins:
                                        cursor.execute("""
                                            SELECT id, is_initial 
                                            FROM admin 
                                            ORDER BY is_initial DESC, id
                                        """)
                                        admins = cursor.fetchall()
                            else:
                                cursor.execute("""
                                    SELECT id, is_initial 
                                    FROM admin 
                                    ORDER BY is_initial DESC, id
                                """)
                                admins = cursor.fetchall()

                            if not admins:
                                await interaction.response.send_message(
                                    "❌ No administrators found.", 
                                    ephemeral=True
                                )
                                return

                            admin_options = []
                            for admin_id, is_initial in admins:
                                try:
                                    user = await self.bot.fetch_user(admin_id)
                                    admin_name = f"{user.name} ({admin_id})"
                                except Exception as e:
                                    admin_name = f"Unknown User ({admin_id})"
                                
                                admin_options.append(
                                    discord.SelectOption(
                                        label=admin_name[:100],
                                        value=str(admin_id),
                                        description=f"{'Global Admin' if is_initial == 1 else 'Server Admin'}",
                                        emoji="👑" if is_initial == 1 else "👤"
                                    )
                                )

                            admin_embed = discord.Embed(
                                title="👤 Admin Selection",
                                description=(
                                    "Please select an administrator to assign alliance:\n\n"
                                    "**Administrator List**\n"
                                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                                    "Select an administrator from the list below:\n"
                                ),
                                color=discord.Color.blue()
                            )

                            admin_select = discord.ui.Select(
                                placeholder="Select an administrator...",
                                options=admin_options
                            )
                            
                            admin_view = discord.ui.View()
                            admin_view.add_item(admin_select)

                            async def admin_callback(admin_interaction: discord.Interaction):
                                try:
                                    selected_admin_id = int(admin_select.values[0])
                                    
                                    self.c_alliance.execute("""
                                        SELECT alliance_id, name 
                                        FROM alliance_list 
                                        ORDER BY name
                                    """)
                                    alliances = self.c_alliance.fetchall()

                                    if not alliances:
                                        await admin_interaction.response.send_message(
                                            "❌ No alliances found.", 
                                            ephemeral=True
                                        )
                                        return

                                    alliances_with_counts = []
                                    for alliance_id, name in alliances:
                                        with sqlite3.connect('db/users.sqlite') as users_db:
                                            cursor = users_db.cursor()
                                            cursor.execute("SELECT COUNT(*) FROM users WHERE alliance = ?", (alliance_id,))
                                            member_count = cursor.fetchone()[0]
                                            alliances_with_counts.append((alliance_id, name, member_count))

                                    alliance_embed = discord.Embed(
                                        title="🏰 Alliance Selection",
                                        description=(
                                            "Please select an alliance to assign to the administrator:\n\n"
                                            "**Alliance List**\n"
                                            "━━━━━━━━━━━━━━━━━━━━━━\n"
                                            "Select an alliance from the list below:\n"
                                        ),
                                        color=discord.Color.blue()
                                    )

                                    view = AllianceSelectView(alliances_with_counts, self)
                                    
                                    async def alliance_callback(alliance_interaction: discord.Interaction):
                                        try:
                                            selected_alliance_id = int(view.current_select.values[0])
                                            
                                            with sqlite3.connect('db/settings.sqlite') as settings_db:
                                                cursor = settings_db.cursor()
                                                cursor.execute("""
                                                    INSERT INTO adminserver (admin, alliances_id)
                                                    VALUES (?, ?)
                                                """, (selected_admin_id, selected_alliance_id))
                                                settings_db.commit()

                                            with sqlite3.connect('db/alliance.sqlite') as alliance_db:
                                                cursor = alliance_db.cursor()
                                                cursor.execute("SELECT name FROM alliance_list WHERE alliance_id = ?", (selected_alliance_id,))
                                                alliance_name = cursor.fetchone()[0]
                                            try:
                                                admin_user = await self.bot.fetch_user(selected_admin_id)
                                                admin_name = admin_user.name
                                            except:
                                                admin_name = f"Unknown User ({selected_admin_id})"

                                            success_embed = discord.Embed(
                                                title="✅ Alliance Assigned",
                                                description=(
                                                    f"Successfully assigned alliance to administrator:\n\n"
                                                    f"👤 **Administrator:** {admin_name}\n"
                                                    f"🆔 **Admin ID:** {selected_admin_id}\n"
                                                    f"🏰 **Alliance:** {alliance_name}\n"
                                                    f"🆔 **Alliance ID:** {selected_alliance_id}"
                                                ),
                                                color=discord.Color.green()
                                            )
                                            
                                            if not alliance_interaction.response.is_done():
                                                await alliance_interaction.response.edit_message(
                                                    embed=success_embed,
                                                    view=None
                                                )
                                            else:
                                                await alliance_interaction.message.edit(
                                                    embed=success_embed,
                                                    view=None
                                                )
                                            
                                        except Exception as e:
                                            print(f"Alliance callback error: {e}")
                                            if not alliance_interaction.response.is_done():
                                                await alliance_interaction.response.send_message(
                                                    "❌ An error occurred while assigning the alliance.",
                                                    ephemeral=True
                                                )
                                            else:
                                                await alliance_interaction.followup.send(
                                                    "❌ An error occurred while assigning the alliance.",
                                                    ephemeral=True
                                                )

                                    view.callback = alliance_callback
                                    
                                    if not admin_interaction.response.is_done():
                                        await admin_interaction.response.edit_message(
                                            embed=alliance_embed,
                                            view=view
                                        )
                                    else:
                                        await admin_interaction.message.edit(
                                            embed=alliance_embed,
                                            view=view
                                        )

                                except Exception as e:
                                    print(f"Admin callback error: {e}")
                                    if not admin_interaction.response.is_done():
                                        await admin_interaction.response.send_message(
                                            "An error occurred while processing your request.",
                                            ephemeral=True
                                        )
                                    else:
                                        await admin_interaction.followup.send(
                                            "An error occurred while processing your request.",
                                            ephemeral=True
                                        )

                            admin_select.callback = admin_callback
                            
                            try:
                                await interaction.response.send_message(
                                    embed=admin_embed,
                                    view=admin_view,
                                    ephemeral=True
                                )
                            except Exception as e:
                                await interaction.followup.send(
                                    "An error occurred while sending the initial message.",
                                    ephemeral=True
                                )

                    except Exception as e:
                        try:
                            await interaction.response.send_message(
                                "An error occurred while processing your request.",
                                ephemeral=True
                            )
                        except:
                            pass
                elif custom_id == "add_admin":
                    try:
                        self.settings_cursor.execute("SELECT is_initial FROM admin WHERE id = ?", (interaction.user.id,))
                        result = self.settings_cursor.fetchone()
                        
                        if (not result or result[0] != 1) and not await is_bot_owner(self.bot, interaction.user.id):
                            await interaction.response.send_message(
                                "❌ Only global administrators can use this command", 
                                ephemeral=True
                            )
                            return

                        await interaction.response.send_message(
                            "Please tag the admin you want to add (@user).", 
                            ephemeral=True
                        )

                        def check(m):
                            return m.author.id == interaction.user.id and len(m.mentions) == 1

                        try:
                            message = await self.bot.wait_for('message', timeout=30.0, check=check)
                            new_admin = message.mentions[0]
                            
                            await message.delete()
                            
                            # MongoDB Upsert
                            if mongo_enabled() and AdminsAdapter:
                                try:
                                    AdminsAdapter.upsert(new_admin.id, 0)
                                except Exception as e:
                                    print(f"MongoDB admin upsert failed: {e}")

                            self.settings_cursor.execute("""
                                INSERT OR IGNORE INTO admin (id, is_initial)
                                VALUES (?, 0)
                            """, (new_admin.id,))
                            self.settings_db.commit()

                            success_embed = discord.Embed(
                                title="✅ Administrator Successfully Added",
                                description=(
                                    f"**New Administrator Information**\n"
                                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                                    f"👤 **Name:** {new_admin.name}\n"
                                    f"🆔 **Discord ID:** {new_admin.id}\n"
                                    f"📅 **Account Creation Date:** {new_admin.created_at.strftime('%d/%m/%Y')}\n"
                                ),
                                color=discord.Color.green()
                            )
                            success_embed.set_thumbnail(url=new_admin.display_avatar.url)
                            
                            await interaction.edit_original_response(
                                content=None,
                                embed=success_embed
                            )

                        except asyncio.TimeoutError:
                            await interaction.edit_original_response(
                                content="❌ Timeout No user has been tagged.",
                                embed=None
                            )

                    except Exception as e:
                        if not interaction.response.is_done():
                            await interaction.response.send_message(
                                "❌ An error occurred while adding an administrator.",
                                ephemeral=True
                            )

                elif custom_id == "remove_admin":
                    try:
                        self.settings_cursor.execute("SELECT is_initial FROM admin WHERE id = ?", (interaction.user.id,))
                        result = self.settings_cursor.fetchone()
                        
                        if (not result or result[0] != 1) and not await is_bot_owner(self.bot, interaction.user.id):
                            await interaction.response.send_message(
                                "❌ Only global administrators can use this command.", 
                                ephemeral=True
                            )
                            return

                        if mongo_enabled() and AdminsAdapter:
                            try:
                                admins_data = AdminsAdapter.get_all()
                                admins = [(d['id'], d.get('is_initial', 0)) for d in admins_data]
                            except Exception as e:
                                print(f"MongoDB fetch admins failed: {e}")
                                admins = []
                        else:
                            admins = []

                        if not admins:
                            self.settings_cursor.execute("""
                                SELECT id, is_initial FROM admin 
                                ORDER BY is_initial DESC, id
                            """)
                            admins = self.settings_cursor.fetchall()

                        if not admins:
                            await interaction.response.send_message(
                                "❌ No administrator registered in the system.", 
                                ephemeral=True
                            )
                            return

                        admin_select_embed = discord.Embed(
                            title="👤 Administrator Deletion",
                            description=(
                                "Select the administrator you want to delete:\n\n"
                                "**Administrator List**\n"
                                "━━━━━━━━━━━━━━━━━━━━━━\n"
                            ),
                            color=discord.Color.red()
                        )

                        options = []
                        for admin_id, is_initial in admins:
                            try:
                                user = await self.bot.fetch_user(admin_id)
                                admin_name = f"{user.name}"
                            except:
                                admin_name = "Unknown User"

                            options.append(
                                discord.SelectOption(
                                    label=f"{admin_name[:50]}",
                                    value=str(admin_id),
                                    description=f"{'Global Admin' if is_initial == 1 else 'Server Admin'} - ID: {admin_id}",
                                    emoji="👑" if is_initial == 1 else "👤"
                                )
                            )
                        
                        admin_select = discord.ui.Select(
                            placeholder="Select the administrator you want to delete...",
                            options=options,
                            custom_id="admin_select"
                        )

                        admin_view = discord.ui.View(timeout=None)
                        admin_view.add_item(admin_select)

                        async def admin_callback(select_interaction: discord.Interaction):
                            try:
                                selected_admin_id = int(select_interaction.data["values"][0])
                                
                                self.settings_cursor.execute("""
                                    SELECT id, is_initial FROM admin WHERE id = ?
                                """, (selected_admin_id,))
                                admin_info = self.settings_cursor.fetchone()

                                self.settings_cursor.execute("""
                                    SELECT alliances_id
                                    FROM adminserver
                                    WHERE admin = ?
                                """, (selected_admin_id,))
                                admin_alliances = self.settings_cursor.fetchall()

                                alliance_names = []
                                if admin_alliances: 
                                    alliance_ids = [alliance[0] for alliance in admin_alliances]
                                    
                                    alliance_cursor = self.alliance_db.cursor()
                                    placeholders = ','.join('?' * len(alliance_ids))
                                    query = f"SELECT alliance_id, name FROM alliance_list WHERE alliance_id IN ({placeholders})"
                                    alliance_cursor.execute(query, alliance_ids)
                                    
                                    alliance_results = alliance_cursor.fetchall()
                                    alliance_names = [alliance[1] for alliance in alliance_results]

                                try:
                                    user = await self.bot.fetch_user(selected_admin_id)
                                    admin_name = user.name
                                    avatar_url = user.display_avatar.url
                                except Exception as e:
                                    admin_name = f"Bilinmeyen Kullanıcı ({selected_admin_id})"
                                    avatar_url = None

                                info_embed = discord.Embed(
                                    title="⚠️ Administrator Deletion Confirmation",
                                    description=(
                                        f"**Administrator Information**\n"
                                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                                        f"👤 **Name:** `{admin_name}`\n"
                                        f"🆔 **Discord ID:** `{selected_admin_id}`\n"
                                        f"👤 **Access Level:** `{'Global Admin' if admin_info[1] == 1 else 'Server Admin'}`\n"
                                        f"🔍 **Access Type:** `{'All Alliances' if admin_info[1] == 1 else 'Server + Special Access'}`\n"
                                        f"📊 **Available Alliances:** `{len(alliance_names)}`\n"
                                        "━━━━━━━━━━━━━━━━━━━━━━\n"
                                    ),
                                    color=discord.Color.yellow()
                                )

                                if alliance_names:
                                    info_embed.add_field(
                                        name="🏰 Alliances Authorized",
                                        value="\n".join([f"• {name}" for name in alliance_names[:10]]) + 
                                              ("\n• ..." if len(alliance_names) > 10 else ""),
                                        inline=False
                                    )
                                else:
                                    info_embed.add_field(
                                        name="🏰 Alliances Authorized",
                                        value="This manager does not yet have an authorized alliance.",
                                        inline=False
                                    )

                                if avatar_url:
                                    info_embed.set_thumbnail(url=avatar_url)

                                confirm_view = discord.ui.View()
                                
                                confirm_button = discord.ui.Button(
                                    label="Confirm", 
                                    style=discord.ButtonStyle.danger,
                                    custom_id="confirm_remove"
                                )
                                cancel_button = discord.ui.Button(
                                    label="Cancel", 
                                    style=discord.ButtonStyle.secondary,
                                    custom_id="cancel_remove"
                                )

                                async def confirm_callback(button_interaction: discord.Interaction):
                                    try:
                                        if mongo_enabled() and AdminsAdapter:
                                            try:
                                                # Assuming delete method exists
                                                if hasattr(AdminsAdapter, 'delete'):
                                                    AdminsAdapter.delete(selected_admin_id)
                                                elif hasattr(AdminsAdapter, 'remove'):
                                                    AdminsAdapter.remove(selected_admin_id)
                                            except Exception as e:
                                                print(f"MongoDB admin delete failed: {e}")

                                        self.settings_cursor.execute("DELETE FROM adminserver WHERE admin = ?", (selected_admin_id,))
                                        self.settings_cursor.execute("DELETE FROM admin WHERE id = ?", (selected_admin_id,))
                                        self.settings_db.commit()

                                        success_embed = discord.Embed(
                                            title="✅ Administrator Deleted Successfully",
                                            description=(
                                                f"**Deleted Administrator**\n"
                                                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                                                f"👤 **Name:** `{admin_name}`\n"
                                                f"🆔 **Discord ID:** `{selected_admin_id}`\n"
                                            ),
                                            color=discord.Color.green()
                                        )
                                        
                                        await button_interaction.response.edit_message(
                                            embed=success_embed,
                                            view=None
                                        )
                                    except Exception as e:
                                        await button_interaction.response.send_message(
                                            "❌ An error occurred while deleting the administrator.",
                                            ephemeral=True
                                        )

                                async def cancel_callback(button_interaction: discord.Interaction):
                                    cancel_embed = discord.Embed(
                                        title="❌ Process Canceled",
                                        description="Administrator deletion canceled.",
                                        color=discord.Color.red()
                                    )
                                    await button_interaction.response.edit_message(
                                        embed=cancel_embed,
                                        view=None
                                    )

                                confirm_button.callback = confirm_callback
                                cancel_button.callback = cancel_callback

                                confirm_view.add_item(confirm_button)
                                confirm_view.add_item(cancel_button)

                                await select_interaction.response.edit_message(
                                    embed=info_embed,
                                    view=confirm_view
                                )

                            except Exception as e:
                                await select_interaction.response.send_message(
                                    "❌ An error occurred during processing.",
                                    ephemeral=True
                                )

                        admin_select.callback = admin_callback

                        await interaction.response.send_message(
                            embed=admin_select_embed,
                            view=admin_view,
                            ephemeral=True
                        )

                    except Exception as e:
                        print(f"Remove admin error: {e}")
                        if not interaction.response.is_done():
                            await interaction.response.send_message(
                                "❌ An error occurred during the administrator deletion process.",
                                ephemeral=True
                            )

                elif custom_id == "main_menu":
                    try:
                        alliance_cog = self.bot.get_cog("Alliance")
                        if alliance_cog:
                            await alliance_cog.show_main_menu(interaction)
                        else:
                            await interaction.response.send_message(
                                "❌ Ana menüye dönüş sırasında bir hata oluştu.",
                                ephemeral=True
                            )
                    except Exception as e:
                        print(f"[ERROR] Main Menu error in bot operations: {e}")
                        if not interaction.response.is_done():
                            await interaction.response.send_message(
                                "An error occurred while returning to main menu.", 
                                ephemeral=True
                            )
                        else:
                            await interaction.followup.send(
                                "An error occurred while returning to main menu.",
                                ephemeral=True
                            )

            except Exception as e:
                if not interaction.response.is_done():
                    print(f"Error processing {custom_id}: {e}")
                    await interaction.response.send_message(
                        "An error occurred while processing your request.",
                        ephemeral=True
                    )

        elif custom_id == "view_admin_permissions":
            try:
                with sqlite3.connect('db/settings.sqlite') as settings_db:
                    cursor = settings_db.cursor()
                    cursor.execute("SELECT is_initial FROM admin WHERE id = ?", (interaction.user.id,))
                    result = cursor.fetchone()
                    
                    if (not result or result[0] != 1) and not await is_bot_owner(self.bot, interaction.user.id):
                        await interaction.response.send_message(
                            "❌ Only global administrators can use this command.", 
                            ephemeral=True
                        )
                        return

                    with sqlite3.connect('db/alliance.sqlite') as alliance_db:
                        alliance_cursor = alliance_db.cursor()
                        
                        cursor.execute("""
                            SELECT a.id, a.is_initial, admin_server.alliances_id
                            FROM admin a
                            JOIN adminserver admin_server ON a.id = admin_server.admin
                            ORDER BY a.is_initial DESC, a.id
                        """)
                        admin_permissions = cursor.fetchall()

                        if not admin_permissions:
                            await interaction.response.send_message(
                                "No admin permissions found.", 
                                ephemeral=True
                            )
                            return

                        admin_alliance_info = []
                        for admin_id, is_initial, alliance_id in admin_permissions:
                            alliance_cursor.execute("""
                                SELECT name FROM alliance_list 
                                WHERE alliance_id = ?
                            """, (alliance_id,))
                            alliance_result = alliance_cursor.fetchone()
                            if alliance_result:
                                admin_alliance_info.append((admin_id, is_initial, alliance_id, alliance_result[0]))

                        embed = discord.Embed(
                            title="👥 Admin Alliance Permissions",
                            description=(
                                "Select an admin to view or modify permissions:\n\n"
                                "**Admin List**\n"
                                "━━━━━━━━━━━━━━━━━━━━━━\n"
                            ),
                            color=discord.Color.blue()
                        )

                        options = []
                        for admin_id, is_initial, alliance_id, alliance_name in admin_alliance_info:
                            try:
                                user = await interaction.client.fetch_user(admin_id)
                                admin_name = user.name
                            except:
                                admin_name = f"Unknown User ({admin_id})"

                            option_label = f"{admin_name[:50]}"
                            option_desc = f"Alliance: {alliance_name[:50]}"
                            
                            options.append(
                                discord.SelectOption(
                                    label=option_label,
                                    value=f"{admin_id}:{alliance_id}",
                                    description=option_desc,
                                    emoji="👑" if is_initial == 1 else "👤"
                                )
                            )

                        if not options:
                            await interaction.response.send_message(
                                "No admin-alliance permissions found.", 
                                ephemeral=True
                            )
                            return

                        select = discord.ui.Select(
                            placeholder="Select an admin to remove permission...",
                            options=options,
                            custom_id="admin_permission_select"
                        )

                        async def select_callback(select_interaction: discord.Interaction):
                            try:
                                admin_id, alliance_id = select.values[0].split(":")
                                
                                confirm_embed = discord.Embed(
                                    title="⚠️ Confirm Permission Removal",
                                    description=(
                                        f"Are you sure you want to remove the alliance permission?\n\n"
                                        f"**Admin:** {admin_name} ({admin_id})\n"
                                        f"**Alliance:** {alliance_name} ({alliance_id})"
                                    ),
                                    color=discord.Color.yellow()
                                )

                                confirm_view = discord.ui.View()
                                
                                async def confirm_callback(confirm_interaction: discord.Interaction):
                                    try:
                                        success = await self.confirm_permission_removal(int(admin_id), int(alliance_id), confirm_interaction)
                                        
                                        if success:
                                            success_embed = discord.Embed(
                                                title="✅ Permission Removed",
                                                description=(
                                                    f"Successfully removed alliance permission:\n\n"
                                                    f"**Admin:** {admin_name} ({admin_id})\n"
                                                    f"**Alliance:** {alliance_name} ({alliance_id})"
                                                ),
                                                color=discord.Color.green()
                                            )
                                            await confirm_interaction.response.edit_message(
                                                embed=success_embed,
                                                view=None
                                            )
                                        else:
                                            await confirm_interaction.response.send_message(
                                                "An error occurred while removing the permission.",
                                                ephemeral=True
                                            )
                                    except Exception as e:
                                        print(f"Confirm callback error: {e}")
                                        await confirm_interaction.response.send_message(
                                            "An error occurred while removing the permission.",
                                            ephemeral=True
                                        )

                                async def cancel_callback(cancel_interaction: discord.Interaction):
                                    cancel_embed = discord.Embed(
                                        title="❌ Operation Cancelled",
                                        description="Permission removal has been cancelled.",
                                        color=discord.Color.red()
                                    )
                                    await cancel_interaction.response.edit_message(
                                        embed=cancel_embed,
                                        view=None
                                    )

                                confirm_button = discord.ui.Button(
                                    label="Confirm",
                                    style=discord.ButtonStyle.danger,
                                    custom_id="confirm_remove"
                                )
                                confirm_button.callback = confirm_callback
                                
                                cancel_button = discord.ui.Button(
                                    label="Cancel",
                                    style=discord.ButtonStyle.secondary,
                                    custom_id="cancel_remove"
                                )
                                cancel_button.callback = cancel_callback

                                confirm_view.add_item(confirm_button)
                                confirm_view.add_item(cancel_button)

                                await select_interaction.response.edit_message(
                                    embed=confirm_embed,
                                    view=confirm_view
                                )

                            except Exception as e:
                                print(f"Select callback error: {e}")
                                await select_interaction.response.send_message(
                                    "An error occurred while processing your selection.",
                                    ephemeral=True
                                )

                        select.callback = select_callback
                        
                        view = discord.ui.View()
                        view.add_item(select)

                        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

            except Exception as e:
                print(f"View admin permissions error: {e}")
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "An error occurred while loading admin permissions.",
                        ephemeral=True
                    )

        elif custom_id == "view_administrators":
            try:
                self.settings_cursor.execute("SELECT is_initial FROM admin WHERE id = ?", (interaction.user.id,))
                result = self.settings_cursor.fetchone()
                
                if (not result or result[0] != 1) and not await is_bot_owner(self.bot, interaction.user.id):
                    await interaction.response.send_message(
                        "❌ Only global administrators can use this command.", 
                        ephemeral=True
                    )
                    return

                self.settings_cursor.execute("""
                    SELECT a.id, a.is_initial 
                    FROM admin a
                    ORDER BY a.is_initial DESC, a.id
                """)
                admins = self.settings_cursor.fetchall()

                if not admins:
                    await interaction.response.send_message(
                        "❌ No administrators found in the system.", 
                        ephemeral=True
                    )
                    return

                admin_list_embed = discord.Embed(
                    title="👥 Administrator List",
                    description="List of all administrators and their permissions:\n━━━━━━━━━━━━━━━━━━━━━━",
                    color=discord.Color.blue()
                )

                for admin_id, is_initial in admins:
                    try:
                        user = await self.bot.fetch_user(admin_id)
                        admin_name = user.name
                        admin_avatar = user.display_avatar.url

                        self.settings_cursor.execute("""
                            SELECT alliances_id 
                            FROM adminserver 
                            WHERE admin = ?
                        """, (admin_id,))
                        alliance_ids = self.settings_cursor.fetchall()

                        alliance_names = []
                        if alliance_ids:
                            alliance_id_list = [aid[0] for aid in alliance_ids]
                            placeholders = ','.join('?' * len(alliance_id_list))
                            self.c_alliance.execute(f"""
                                SELECT name 
                                FROM alliance_list 
                                WHERE alliance_id IN ({placeholders})
                            """, alliance_id_list)
                            alliance_names = [name[0] for name in self.c_alliance.fetchall()]

                        admin_info = (
                            f"👤 **Name:** {admin_name}\n"
                            f"🆔 **ID:** {admin_id}\n"
                            f"👑 **Role:** {'Global Admin' if is_initial == 1 else 'Server Admin'}\n"
                            f"🔍 **Access Type:** {'All Alliances' if is_initial == 1 else 'Server + Special Access'}\n"
                        )

                        if alliance_names:
                            alliance_text = "\n".join([f"• {name}" for name in alliance_names[:5]])
                            if len(alliance_names) > 5:
                                alliance_text += f"\n• ... and {len(alliance_names) - 5} more"
                            admin_info += f"🏰 **Managing Alliances:**\n{alliance_text}\n"
                        else:
                            admin_info += "🏰 **Managing Alliances:** No alliances assigned\n"

                        admin_list_embed.add_field(
                            name=f"{'👑' if is_initial == 1 else '👤'} {admin_name}",
                            value=f"{admin_info}\n━━━━━━━━━━━━━━━━━━━━━━",
                            inline=False
                        )

                    except Exception as e:
                        print(f"Error processing admin {admin_id}: {e}")
                        admin_list_embed.add_field(
                            name=f"Unknown User ({admin_id})",
                            value="Error loading administrator information\n━━━━━━━━━━━━━━━━━━━━━━",
                            inline=False
                        )

                view = discord.ui.View()
                view.add_item(discord.ui.Button(
                    label="Back to Bot Operations",
                    emoji="◀️",
                    style=discord.ButtonStyle.secondary,
                    custom_id="bot_operations",
                    row=0
                ))

                await interaction.response.send_message(
                    embed=admin_list_embed,
                    view=view,
                    ephemeral=True
                )

            except Exception as e:
                print(f"View administrators error: {e}")
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "❌ An error occurred while loading administrator list.",
                        ephemeral=True
                    )

        elif custom_id == "transfer_old_database":
            try:
                self.settings_cursor.execute("SELECT is_initial FROM admin WHERE id = ?", (interaction.user.id,))
                result = self.settings_cursor.fetchone()
                
                if (not result or result[0] != 1) and not await is_bot_owner(self.bot, interaction.user.id):
                    await interaction.response.send_message(
                        "❌ Only global administrators can use this command.", 
                        ephemeral=True
                    )
                    return

                database_cog = self.bot.get_cog('DatabaseTransfer')
                if database_cog:
                    await database_cog.transfer_old_database(interaction)
                else:
                    await interaction.response.send_message(
                        "❌ Database transfer module not loaded.", 
                        ephemeral=True
                    )

            except Exception as e:
                print(f"Transfer old database error: {e}")
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "❌ An error occurred while transferring the database.",
                        ephemeral=True
                    )

        elif custom_id == "check_updates":
            try:
                self.settings_cursor.execute("SELECT is_initial FROM admin WHERE id = ?", (interaction.user.id,))
                result = self.settings_cursor.fetchone()
                
                if (not result or result[0] != 1) and not await is_bot_owner(self.bot, interaction.user.id):
                    await interaction.response.send_message(
                        "❌ Only global administrators can use this command.", 
                        ephemeral=True
                    )
                    return

                current_version, new_version, update_notes, updates_needed = await self.check_for_updates()

                if not current_version or not new_version:
                    await interaction.response.send_message(
                        "❌ Failed to check for updates. Please try again later.", 
                        ephemeral=True
                    )
                    return

                main_embed = discord.Embed(
                    title="🔄 Bot Update Status",
                    color=discord.Color.blue() if not updates_needed else discord.Color.yellow()
                )

                main_embed = discord.Embed(
                    title="🔄 Bot Update Status",
                    color=discord.Color.blue() if not updates_needed else discord.Color.yellow()
                )

                main_embed.add_field(
                    name="Current Version",
                    value=f"`{current_version}`",
                    inline=True
                )

                main_embed.add_field(
                    name="Latest Version",
                    value=f"`{new_version}`",
                    inline=True
                )

                if updates_needed:
                    main_embed.add_field(
                        name="Status",
                        value="🔄 **Update Available**",
                        inline=True
                    )

                    if update_notes:
                        notes_text = "\n".join([f"• {note.lstrip('- *•').strip()}" for note in update_notes[:10]])
                        if len(update_notes) > 10:
                            notes_text += f"\n• ... and more!"
                        
                        main_embed.add_field(
                            name="Release Notes",
                            value=notes_text[:1024],  # Discord field limit
                            inline=False
                        )

                    main_embed.add_field(
                        name="How to Update",
                        value=(
                            "To update to the new version:\n"
                            "🔄 **Restart the bot** (main.py)\n"
                            "✅ Accept the update when prompted\n\n"
                            "The bot will automatically download and install the update."
                        ),
                        inline=False
                    )
                else:
                    main_embed.add_field(
                        name="Status",
                        value="✅ **Up to Date**",
                        inline=True
                    )
                    main_embed.description = "Your bot is running the latest version!"

                await interaction.response.send_message(
                    embed=main_embed,
                    ephemeral=True
                )

            except Exception as e:
                print(f"Check updates error: {e}")
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "❌ An error occurred while checking for updates.",
                        ephemeral=True
                    )

    async def show_bot_operations_menu(self, interaction: discord.Interaction):
        try:
            embed = discord.Embed(
                title="🤖 Bot Operations",
                description=(
                    "Please choose an operation:\n\n"
                    "**Available Operations**\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    "👥 **Admin Management**\n"
                    "└ Manage bot administrators\n\n"
                    "🔍 **Admin Permissions**\n"
                    "└ View and manage admin permissions\n\n"
                    "🔄 **Bot Updates**\n"
                    "└ Check and manage updates\n\n"
                    "🌐 **Remote Access**\n"
                    "└ Manage channels across all servers\n"
                    "━━━━━━━━━━━━━━━━━━━━━━"
                ),
                color=discord.Color.blue()
            )
            
            view = discord.ui.View()
            view.add_item(discord.ui.Button(
                label="Add Admin",
                emoji="➕",
                style=discord.ButtonStyle.success,
                custom_id="add_admin",
                row=1
            ))
            view.add_item(discord.ui.Button(
                label="Remove Admin",
                emoji="➖",
                style=discord.ButtonStyle.danger,
                custom_id="remove_admin",
                row=1
            ))
            view.add_item(discord.ui.Button(
                label="View Administrators",
                emoji="👥",
                style=discord.ButtonStyle.primary,
                custom_id="view_administrators",
                row=1
            ))
            view.add_item(discord.ui.Button(
                label="Assign Alliance to Admin",
                emoji="🔗",
                style=discord.ButtonStyle.success,
                custom_id="assign_alliance",
                row=2
            ))
            view.add_item(discord.ui.Button(
                label="Assign Server Alliance",
                emoji="🏰",
                style=discord.ButtonStyle.success,
                custom_id="assign_server_alliance",
                row=2
            ))
            view.add_item(discord.ui.Button(
                label="Set Member List Password",
                emoji="🔐",
                style=discord.ButtonStyle.primary,
                custom_id="set_member_list_password",
                row=2
            ))
            view.add_item(discord.ui.Button(
                label="Delete Admin Permissions",
                emoji="➖",
                style=discord.ButtonStyle.danger,
                custom_id="view_admin_permissions",
                row=2
            ))
            view.add_item(discord.ui.Button(
                label="Transfer Old Database",
                emoji="🔄",
                style=discord.ButtonStyle.primary,
                custom_id="transfer_old_database",
                row=3
            ))
            view.add_item(discord.ui.Button(
                label="Check for Updates",
                emoji="🔄",
                style=discord.ButtonStyle.primary,
                custom_id="check_updates",
                row=3
            ))
            view.add_item(discord.ui.Button(
                label="Log System",
                emoji="📋",
                style=discord.ButtonStyle.primary,
                custom_id="log_system",
                row=3
            ))
            view.add_item(discord.ui.Button(
                label="Alliance Control Messages",
                emoji="💬",
                style=discord.ButtonStyle.primary,
                custom_id="alliance_control_messages",
                row=3
            ))
            view.add_item(discord.ui.Button(
                label="Remote Access",
                emoji="🌐",
                style=discord.ButtonStyle.success,
                custom_id="remote_access",
                row=4
            ))
            view.add_item(discord.ui.Button(
                label="Main Menu",
                emoji="🏠",
                style=discord.ButtonStyle.secondary,
                custom_id="main_menu",
                row=4
            ))

            await interaction.response.edit_message(embed=embed, view=view)

        except Exception as e:
            if not any(error_code in str(e) for error_code in ["10062", "40060"]):
                print(f"Show bot operations menu error: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ An error occurred while showing the menu.",
                    ephemeral=True
                )

    async def confirm_permission_removal(self, admin_id: int, alliance_id: int, confirm_interaction: discord.Interaction):
        try:
            self.settings_cursor.execute("""
                DELETE FROM adminserver 
                WHERE admin = ? AND alliances_id = ?
            """, (admin_id, alliance_id))
            self.settings_db.commit()
            return True
        except Exception as e:
            return False

    @app_commands.command(name="manage", description="Quick access to management operations")
    async def manage(self, interaction: discord.Interaction):
        """Quick access menu for Member Operations and Records"""
        try:
            # Defer immediately to prevent timeout
            await interaction.response.defer(ephemeral=True)
            
            # Check if password is set
            if not mongo_enabled() or not ServerAllianceAdapter:
                await interaction.followup.send(
                    "❌ MongoDB not enabled. Cannot access management operations.",
                    ephemeral=True
                )
                return
            
            stored_password = ServerAllianceAdapter.get_password(interaction.guild.id)
            print(f"DEBUG /manage: guild_id={interaction.guild.id} (type: {type(interaction.guild.id)}), stored_password={stored_password}")
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
                await interaction.followup.send(embed=error_embed, view=view, ephemeral=True)
                return
            
            # Check if user has a valid authentication session
            if AuthSessionsAdapter and AuthSessionsAdapter.is_session_valid(
                interaction.guild.id, 
                interaction.user.id, 
                stored_password
            ):
                # Calculate signal strength from bot latency
                latency_ms = round(self.bot.latency * 1000)
                
                # Convert latency to signal percentage and bars
                if latency_ms < 100:
                    signal_percent = min(95 + (100 - latency_ms) // 20, 99)
                    signal_bars = "█████"
                    bar_color = "\u001b[1;32m"  # Bright green
                elif latency_ms < 200:
                    signal_percent = 85 + (200 - latency_ms) // 10
                    signal_bars = "████░"
                    bar_color = "\u001b[1;32m"  # Green
                elif latency_ms < 300:
                    signal_percent = 70 + (300 - latency_ms) // 7
                    signal_bars = "███░░"
                    bar_color = "\u001b[1;33m"  # Yellow
                else:
                    signal_percent = max(50, 70 - (latency_ms - 300) // 10)
                    signal_bars = "██░░░"
                    bar_color = "\u001b[1;31m"  # Red
                
                # User has valid session, skip authentication
                # Truncate username if too long for the ANSI box
                user_display = interaction.user.display_name[:15] if len(interaction.user.display_name) > 15 else interaction.user.display_name
                user_id_str = str(interaction.user.id)
                
                dashboard_embed = discord.Embed(
                    title="⚙️ Dashboard",
                    description=(
                        "```ansi\n"
                        "\u001b[0;33m    ✦\u001b[0m      \u001b[2;37m·\u001b[0m    \u001b[0;33m✧\u001b[0m     \u001b[2;37m·\u001b[0m\n"
                        "\u001b[0;36m  ╔═══════════════════════╗\n"
                        "\u001b[0;36m  ║ \u001b[1;37m◢◣ CONTROL PANEL ◢◣\u001b[0m\u001b[0;36m ║\n"
                        "\u001b[0;36m  ╠═══════════════════════╣\n"
                        f"\u001b[0;36m  ║ \u001b[1;35m👤\u001b[0m USER: \u001b[1;37m{user_display:<15}\u001b[0m\u001b[0;36m║\n"
                        f"\u001b[0;36m  ║ \u001b[1;35m🆔\u001b[0m ID: \u001b[0;37m{user_id_str:<17}\u001b[0m\u001b[0;36m║\n"
                        "\u001b[0;36m  ╠═══════════════════════╣\n"
                        "\u001b[0;36m  ║ \u001b[1;32m▸\u001b[0m STATUS: \u001b[1;32mONLINE\u001b[0m     \u001b[0;36m ║\n"
                        f"\u001b[0;36m  ║ \u001b[1;34m◉\u001b[0m SIGNAL: {bar_color}{signal_bars}\u001b[0m {signal_percent:>2}% \u001b[0;36m  ║\n"
                        "\u001b[0;36m  ╚═══════════════════════╝\n"
                        "\u001b[2;37m    ·\u001b[0m   \u001b[0;33m✦\u001b[0m      \u001b[2;37m·\u001b[0m   \u001b[0;33m✧\u001b[0m\u001b[0m\n"
                        "```\n"
                        "**Select an operation to continue:**\n\n"
                        "👥 **Member Operations**\n"
                        "   ▸ Manage alliance members\n"
                        "   ▸ Update Alliance log\n\n"
                        "📁 **Records Management**\n"
                        "   ▸ Keep track of Players\n"
                        "   ▸ Create and manage groups\n\n"
                        "🎁 **Gift Code Management**\n"
                        "   ▸ Manage gift codes\n"
                        "   ▸ Auto-redeem settings\n\n"
                        "🏰 **Alliance Monitor**\n"
                        "   ▸ Track name changes\n"
                        "   ▸ Monitor furnace levels\n\n"
                        "🔮 **Other Features**\n"
                        "   ▸ More features coming soon\n"
                    ),
                    color=0x2B2D31
                )
                dashboard_embed.set_thumbnail(url=interaction.user.display_avatar.url)
                dashboard_embed.add_field(
                    name="🎮 Current Operator",
                    value=f"{interaction.user.mention}",
                    inline=True
                )
                dashboard_embed.set_footer(
                    text=f"{interaction.guild.name} x Magnus🚀",
                    icon_url="https://cdn.discordapp.com/attachments/1435569370389807144/1436745053442805830/unnamed_5.png"
                )

                dashboard_view = discord.ui.View()
                dashboard_view.add_item(discord.ui.Button(
                    label="Member Operations",
                    emoji="👥",
                    style=discord.ButtonStyle.secondary,
                    custom_id="manage_member_ops",
                    row=0
                ))
                dashboard_view.add_item(discord.ui.Button(
                    label="Records",
                    emoji="📁",
                    style=discord.ButtonStyle.secondary,
                    custom_id="records_menu",
                    row=0
                ))
                dashboard_view.add_item(discord.ui.Button(
                    label="Gift Codes",
                    emoji="🎁",
                    style=discord.ButtonStyle.secondary,
                    custom_id="giftcode_menu",
                    row=0
                ))
                dashboard_view.add_item(discord.ui.Button(
                    label="Alliance Monitor",
                    emoji="🏰",
                    style=discord.ButtonStyle.secondary,
                    custom_id="manage_alliance_monitor",
                    row=1
                ))
                dashboard_view.add_item(discord.ui.Button(
                    label="Other Features",
                    emoji="🔮",
                    style=discord.ButtonStyle.secondary,
                    custom_id="manage_other_features",
                    row=1
                ))

                await interaction.followup.send(
                    content="✅ **Access Granted** (Session Active)",
                    embed=dashboard_embed, 
                    view=dashboard_view, 
                    ephemeral=True
                )
                return

            # Create password modal
            class ManageAuthModal(discord.ui.Modal, title="🛡️ Security Verification"):
                password_input = discord.ui.TextInput(
                    label="Enter Access Code",
                    placeholder="••••••••••••",
                    style=discord.TextStyle.short,
                    required=True,
                    max_length=50
                )

                def __init__(self, guild_id: int, guild_name: str):
                    super().__init__()
                    self.guild_id = guild_id
                    self.guild_name = guild_name

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
                                value="Click the **Authenticate** button to retry.",
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
                        
                        # Calculate signal strength from bot latency
                        latency_ms = round(modal_interaction.client.latency * 1000)
                        
                        # Convert latency to signal percentage and bars
                        if latency_ms < 100:
                            signal_percent = min(95 + (100 - latency_ms) // 20, 99)
                            signal_bars = "█████"
                            bar_color = "\u001b[1;32m"  # Bright green
                        elif latency_ms < 200:
                            signal_percent = 85 + (200 - latency_ms) // 10
                            signal_bars = "████░"
                            bar_color = "\u001b[1;32m"  # Green
                        elif latency_ms < 300:
                            signal_percent = 70 + (300 - latency_ms) // 7
                            signal_bars = "███░░"
                            bar_color = "\u001b[1;33m"  # Yellow
                        else:
                            signal_percent = max(50, 70 - (latency_ms - 300) // 10)
                            signal_bars = "██░░░"
                            bar_color = "\u001b[1;31m"  # Red
                        
                        # Show dashboard
                        # Truncate username if too long for the ANSI box
                        user_display = modal_interaction.user.display_name[:15] if len(modal_interaction.user.display_name) > 15 else modal_interaction.user.display_name
                        user_id_str = str(modal_interaction.user.id)
                        
                        dashboard_embed = discord.Embed(
                            title="⚙️ Dashboard",
                            description=(
                                "```ansi\n"
                                "\u001b[0;33m    ✦\u001b[0m      \u001b[2;37m·\u001b[0m    \u001b[0;33m✧\u001b[0m     \u001b[2;37m·\u001b[0m\n"
                                "\u001b[0;36m  ╔═══════════════════════╗\n"
                                "\u001b[0;36m  ║ \u001b[1;37m◢◣ CONTROL PANEL ◢◣\u001b[0m\u001b[0;36m ║\n"
                                "\u001b[0;36m  ╠═══════════════════════╣\n"
                                f"\u001b[0;36m  ║ \u001b[1;35m👤\u001b[0m USER: \u001b[1;37m{user_display:<15}\u001b[0m\u001b[0;36m║\n"
                                f"\u001b[0;36m  ║ \u001b[1;35m🆔\u001b[0m ID: \u001b[0;37m{user_id_str:<17}\u001b[0m\u001b[0;36m║\n"
                                "\u001b[0;36m  ╠═══════════════════════╣\n"
                                "\u001b[0;36m  ║ \u001b[1;32m▸\u001b[0m STATUS: \u001b[1;32mONLINE\u001b[0m     \u001b[0;36m ║\n"
                                f"\u001b[0;36m  ║ \u001b[1;34m◉\u001b[0m SIGNAL: {bar_color}{signal_bars}\u001b[0m {signal_percent:>2}% \u001b[0;36m  ║\n"
                                "\u001b[0;36m  ╚═══════════════════════╝\n"
                                "\u001b[2;37m    ·\u001b[0m   \u001b[0;33m✦\u001b[0m      \u001b[2;37m·\u001b[0m   \u001b[0;33m✧\u001b[0m\u001b[0m\n"
                                "```\n"
                                "**Select an operation to continue:**\n\n"
                                "👥 **Member Operations**\n"
                                "   ▸ Manage alliance members\n"
                                "   ▸ Update Alliance log\n\n"
                                "📁 **Records Management**\n"
                                "   ▸ Keep track of Players\n"
                                "   ▸ Create and manage groups\n\n"
                                "🎁 **Gift Code Management**\n"
                                "   ▸ Manage gift codes\n"
                                "   ▸ Auto-redeem settings\n"
                            ),
                            color=0x2B2D31
                        )
                        dashboard_embed.set_thumbnail(url=modal_interaction.user.display_avatar.url)
                        dashboard_embed.add_field(
                            name="🎮 Current Operator",
                            value=f"{modal_interaction.user.mention}",
                            inline=True
                        )
                        dashboard_embed.set_footer(
                            text=f"{self.guild_name} x Magnus🚀",
                            icon_url="https://cdn.discordapp.com/attachments/1435569370389807144/1436745053442805830/unnamed_5.png"
                        )

                        dashboard_view = discord.ui.View()
                        dashboard_view.add_item(discord.ui.Button(
                            label="Member Operations",
                            emoji="👥",
                            style=discord.ButtonStyle.secondary,
                            custom_id="manage_member_ops",
                            row=0
                        ))
                        dashboard_view.add_item(discord.ui.Button(
                            label="Records",
                            emoji="📁",
                            style=discord.ButtonStyle.secondary,
                            custom_id="records_menu",
                            row=0
                        ))
                        dashboard_view.add_item(discord.ui.Button(
                            label="Gift Codes",
                            emoji="🎁",
                            style=discord.ButtonStyle.secondary,
                            custom_id="giftcode_menu",
                            row=0
                        ))

                        # Send success message with dashboard
                        await modal_interaction.response.send_message(
                            content="✅ **Access Granted**",
                            embed=dashboard_embed,
                            view=dashboard_view,
                            ephemeral=True
                        )
                    
                    except Exception as e:
                        print(f"Error in manage auth modal: {e}")
                        import traceback
                        traceback.print_exc()
                        await modal_interaction.response.send_message(
                            "❌ An error occurred during authentication.",
                            ephemeral=True
                        )

            # Create a view with authentication button
            class ManageAuthView(discord.ui.View):
                def __init__(self, guild_id: int, guild_name: str):
                    super().__init__(timeout=60)
                    self.guild_id = guild_id
                    self.guild_name = guild_name

                @discord.ui.button(label="Authenticate", emoji="🔐", style=discord.ButtonStyle.secondary, custom_id="manage_auth")
                async def authenticate(self, button_interaction: discord.Interaction, button: discord.ui.Button):
                    modal = ManageAuthModal(self.guild_id, self.guild_name)
                    await button_interaction.response.send_modal(modal)

            # Create authentication embed
            auth_embed = discord.Embed(
                title=interaction.guild.name,
                description="**Required Rank- R5/R4**\n\nAccess to alliance member database.",
                color=0x2B2D31
            )
            
            auth_embed.set_author(
                name="SECURITY VERIFICATION REQUIRED",
                icon_url="https://cdn.discordapp.com/attachments/1435569370389807144/1445470757844160543/unnamed_6_1.png"
            )
            
            auth_embed.add_field(
                name="🔒 Protected Resource",
                value="Management Dashboard",
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
            view = ManageAuthView(interaction.guild.id, interaction.guild.name)
            await interaction.followup.send(embed=auth_embed, view=view, ephemeral=True)

        except Exception as e:
            print(f"Manage command error: {e}")
            import traceback
            traceback.print_exc()
            await interaction.followup.send(
                "❌ An error occurred while loading the management menu.",
                ephemeral=True
            )

# ============================================================================
# PERSISTENT MEMBER LIST VIEW - Survives Bot Restarts
# ============================================================================

class PersistentMemberListView(discord.ui.View):
    """Persistent view for member list that works across bot restarts.
    
    This view stores only the alliance_id and fetches fresh member data
    on each interaction to ensure data accuracy.
    """
    
    def __init__(self, alliance_id: int = 0):
        super().__init__(timeout=None)
        self.alliance_id = alliance_id
        self.current_page = 0
        self.members_per_page = 15
        self.sort_order = "desc"
        self.active_filter = None
        
        # Furnace level mapping
        self.level_mapping = {
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
        
        # FC level ranges
        self.fc_ranges = {
            "FC 1": (35, 39),
            "FC 2": (40, 44),
            "FC 3": (45, 49),
            "FC 4": (50, 54),
            "FC 5": (55, 59),
            "FC 6": (60, 64),
            "FC 7": (65, 69),
            "FC 8": (70, 74),
        }
    
    async def fetch_members_and_alliance_name(self):
        """Fetch fresh member data from MongoDB"""
        try:
            from db.mongo_adapters import AllianceMembersAdapter
            all_members = AllianceMembersAdapter.get_all_members()
            members = [m for m in all_members if int(m.get('alliance', 0) or m.get('alliance_id', 0)) == self.alliance_id]
            
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
            
            return members, alliance_name
        except Exception as e:
            print(f"Error fetching members: {e}")
            return [], f"Alliance {self.alliance_id}"
    
    def get_filtered_members(self, all_members):
        """Get members filtered by active FC filter"""
        if self.active_filter is None:
            return all_members
        
        if self.active_filter in self.fc_ranges:
            min_level, max_level = self.fc_ranges[self.active_filter]
            return [
                m for m in all_members
                if min_level <= int(m.get('furnace_lv', 0) or 0) <= max_level
            ]
        
        return all_members
    
    def get_sorted_members(self, all_members):
        filtered = self.get_filtered_members(all_members)
        return sorted(
            filtered,
            key=lambda x: int(x.get('furnace_lv', 0) or 0),
            reverse=(self.sort_order == "desc")
        )
    
    def get_total_pages(self, all_members):
        filtered_count = len(self.get_filtered_members(all_members))
        return max(1, (filtered_count - 1) // self.members_per_page + 1)
    
    async def create_embed(self, all_members, alliance_name):
        sorted_members = self.get_sorted_members(all_members)
        filtered_members = self.get_filtered_members(all_members)
        total_pages = self.get_total_pages(all_members)
        
        # Calculate statistics
        furnace_levels = [int(m.get('furnace_lv', 0) or 0) for m in filtered_members]
        max_fl = max(furnace_levels) if furnace_levels else 0
        avg_fl = sum(furnace_levels) / len(furnace_levels) if furnace_levels else 0
        
        # Create embed
        filter_text = f" - {self.active_filter}" if self.active_filter else ""
        embed = discord.Embed(
            title=f"👥 {alliance_name} - Member List{filter_text}",
            description=(
                "```ml\n"
                "Alliance Statistics\n"
                "══════════════════════════\n"
                f"📊 Total Members    : {len(filtered_members)}\n"
                f"⚔️ Highest Level    : {self.level_mapping.get(max_fl, str(max_fl))}\n"
                f"📈 Average Level    : {self.level_mapping.get(int(avg_fl), str(int(avg_fl)))}\n"
                "══════════════════════════\n"
                "```\n"
                f"**Member List{filter_text}**\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
            ),
            color=0x5865F2
        )
        
        embed.set_author(
            name="MEMBER DATABASE • ACCESS GRANTED",
            icon_url="https://cdn.discordapp.com/attachments/1435569370389807144/1436745053442805830/unnamed_5.png"
        )
        
        # Get members for current page
        start_idx = self.current_page * self.members_per_page
        end_idx = start_idx + self.members_per_page
        page_members = sorted_members[start_idx:end_idx]
        
        # Add members to embed
        if page_members:
            member_list = ""
            for idx, member in enumerate(page_members, start=start_idx + 1):
                nickname = member.get('nickname', 'Unknown')
                fid = member.get('fid', 'N/A')
                furnace_lv = int(member.get('furnace_lv', 0) or 0)
                level = self.level_mapping.get(furnace_lv, str(furnace_lv))
                
                member_list += f"**{idx:02d}.** 👤 {nickname}\n└ 🆔 `ID: {fid}` | ⚔️ `FC: {level}`\n\n"
            embed.description += member_list
        else:
            embed.description += "*No members found with this filter.*\n\n"
        
        # Footer
        footer_text = f"Page {self.current_page + 1}/{total_pages}"
        if self.active_filter:
            footer_text += f" • Filtered by {self.active_filter}"
        footer_text += " • Stored in MongoDB"
        
                        embed.set_footer(
                            text=footer_text,
                            icon_url="https://cdn.discordapp.com/attachments/1435569370389807144/1445459239131680859/images_7_1.png"
                        )
        
        return embed
    
    @discord.ui.button(emoji="⬅️", style=discord.ButtonStyle.secondary, row=0, custom_id="memberlist_prev")
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            members, alliance_name = await self.fetch_members_and_alliance_name()
            if self.current_page > 0:
                self.current_page -= 1
                embed = await self.create_embed(members, alliance_name)
                await interaction.response.edit_message(embed=embed, view=self)
            else:
                await interaction.response.defer()
        except Exception as e:
            print(f"Error in previous_page: {e}")
            await interaction.response.send_message("❌ Error loading previous page", ephemeral=True)
    
    @discord.ui.button(emoji="➡️", style=discord.ButtonStyle.secondary, row=0, custom_id="memberlist_next")
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            members, alliance_name = await self.fetch_members_and_alliance_name()
            if self.current_page < self.get_total_pages(members) - 1:
                self.current_page += 1
                embed = await self.create_embed(members, alliance_name)
                await interaction.response.edit_message(embed=embed, view=self)
            else:
                await interaction.response.defer()
        except Exception as e:
            print(f"Error in next_page: {e}")
            await interaction.response.send_message("❌ Error loading next page", ephemeral=True)
    
    @discord.ui.select(
        placeholder="Filter by FC Level",
        options=[
            discord.SelectOption(label="All Members", value="all", emoji="👥", description="Show all alliance members"),
            discord.SelectOption(label="Sort ↑ Ascending", value="sort_asc", emoji="🔼", description="Sort by FC level (low to high)"),
            discord.SelectOption(label="Sort ↓ Descending", value="sort_desc", emoji="🔽", description="Sort by FC level (high to low)"),
            discord.SelectOption(label="FC 1", value="FC 1", emoji="1️⃣", description="FC 1 to FC 1-4"),
            discord.SelectOption(label="FC 2", value="FC 2", emoji="2️⃣", description="FC 2 to FC 2-4"),
            discord.SelectOption(label="FC 3", value="FC 3", emoji="3️⃣", description="FC 3 to FC 3-4"),
            discord.SelectOption(label="FC 4", value="FC 4", emoji="4️⃣", description="FC 4 to FC 4-4"),
            discord.SelectOption(label="FC 5", value="FC 5", emoji="5️⃣", description="FC 5 to FC 5-4"),
            discord.SelectOption(label="FC 6", value="FC 6", emoji="6️⃣", description="FC 6 to FC 6-4"),
            discord.SelectOption(label="FC 7", value="FC 7", emoji="7️⃣", description="FC 7 to FC 7-4"),
            discord.SelectOption(label="FC 8", value="FC 8", emoji="8️⃣", description="FC 8 to FC 8-4"),
        ],
        row=1,
        custom_id="memberlist_filter"
    )
    async def filter_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        try:
            selected = interaction.data['values'][0]
            
            if selected == "all":
                self.active_filter = None
                self.current_page = 0
            elif selected == "sort_asc":
                self.sort_order = "asc"
                self.current_page = 0
            elif selected == "sort_desc":
                self.sort_order = "desc"
                self.current_page = 0
            else:
                # FC level filter
                self.active_filter = selected
                self.current_page = 0
            
            members, alliance_name = await self.fetch_members_and_alliance_name()
            embed = await self.create_embed(members, alliance_name)
            await interaction.response.edit_message(embed=embed, view=self)
        except Exception as e:
            print(f"Error in filter_select: {e}")
            await interaction.response.send_message("❌ Error applying filter", ephemeral=True)
    
    @discord.ui.button(label="Profile", emoji="👤", style=discord.ButtonStyle.secondary, row=0, custom_id="memberlist_profile")
    async def view_profile(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            members, alliance_name = await self.fetch_members_and_alliance_name()
            
            if not members:
                await interaction.response.send_message("❌ No members found", ephemeral=True)
                return
            
            # Create profile selection view
            class ProfileSelectView(discord.ui.View):
                def __init__(self, members_data, level_map, alliance_id):
                    super().__init__(timeout=300)
                    self.members = sorted(
                        members_data,
                        key=lambda x: int(x.get('furnace_lv', 0) or 0),
                        reverse=True
                    )
                    self.level_mapping = level_map
                    self.alliance_id = alliance_id
                    self.current_page = 0
                    self.members_per_page = 25
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
                    for idx, member in enumerate(page_members, start=start_idx + 1):
                        nickname = member.get('nickname', 'Unknown')
                        fid = member.get('fid', 'N/A')
                        furnace_lv = int(member.get('furnace_lv', 0) or 0)
                        level = self.level_mapping.get(furnace_lv, str(furnace_lv))
                        
                        options.append(
                            discord.SelectOption(
                                label=f"#{idx:02d} {nickname[:80]}",
                                description=f"FID: {fid} | FC: {level}",
                                value=str(fid),
                                emoji="👤"
                            )
                        )
                    
                    select = discord.ui.Select(
                        placeholder=f"Select a player (Page {self.current_page + 1}/{self.get_total_pages()})...",
                        options=options,
                        custom_id="player_select"
                    )
                    select.callback = self.player_selected
                    self.add_item(select)
                    
                    # Pagination buttons
                    if self.get_total_pages() > 1:
                        prev_button = discord.ui.Button(
                            emoji="⬅️",
                            style=discord.ButtonStyle.secondary,
                            disabled=(self.current_page == 0),
                            row=1
                        )
                        prev_button.callback = self.previous_page
                        self.add_item(prev_button)
                        
                        next_button = discord.ui.Button(
                            emoji="➡️",
                            style=discord.ButtonStyle.secondary,
                            disabled=(self.current_page >= self.get_total_pages() - 1),
                            row=1
                        )
                        next_button.callback = self.next_page
                        self.add_item(next_button)
                
                async def previous_page(self, button_interaction: discord.Interaction):
                    if self.current_page > 0:
                        self.current_page -= 1
                        self.update_components()
                        await button_interaction.response.edit_message(
                            content=f"**Select a player to view their profile:** (Page {self.current_page + 1}/{self.get_total_pages()})",
                            view=self
                        )
                    else:
                        await button_interaction.response.defer()
                
                async def next_page(self, button_interaction: discord.Interaction):
                    if self.current_page < self.get_total_pages() - 1:
                        self.current_page += 1
                        self.update_components()
                        await button_interaction.response.edit_message(
                            content=f"**Select a player to view their profile:** (Page {self.current_page + 1}/{self.get_total_pages()})",
                            view=self
                        )
                    else:
                        await button_interaction.response.defer()
                
                async def player_selected(self, select_interaction: discord.Interaction):
                    fid = select_interaction.data['values'][0]
                    
                    # Find member
                    member = next((m for m in self.members if m.get('fid') == fid), None)
                    if not member:
                        await select_interaction.response.send_message("❌ Player not found.", ephemeral=True)
                        return
                    
                    # Create profile embed
                    nickname = member.get('nickname', 'Unknown')
                    furnace_lv = int(member.get('furnace_lv', 0) or 0)
                    level = self.level_mapping.get(furnace_lv, str(furnace_lv))
                    avatar = member.get('avatar_image', '')
                    
                    # Fetch avatar if missing
                    if not avatar:
                        try:
                            from cogs.login_handler import LoginHandler
                            login_handler = LoginHandler()
                            result = await login_handler.fetch_player_data(str(fid))
                            
                            if result['status'] == 'success' and result['data']:
                                avatar = result['data'].get('avatar_image', '')
                                
                                # Update MongoDB
                                if avatar:
                                    from db.mongo_adapters import AllianceMembersAdapter
                                    member['avatar_image'] = avatar
                                    AllianceMembersAdapter.upsert_member(str(fid), member)
                        except Exception as e:
                            print(f"Error fetching avatar: {e}")
                    
                    profile_embed = discord.Embed(
                        title=f"👤 Player Profile",
                        description=(
                            f"**{nickname}**\n\n"
                            f"```yaml\n"
                            f"FID         : {fid}\n"
                            f"Furnace Lv  : {level}\n"
                            f"```"
                        ),
                        color=0x5865F2
                    )
                    
                    if avatar:
                        try:
                            profile_embed.set_image(url=avatar)
                        except Exception as e:
                            print(f"Error setting avatar: {e}")
                    
                    profile_embed.set_footer(
                        text="Stored in MongoDB",
                        icon_url="https://cdn.discordapp.com/attachments/1435569370389807144/1445459239131680859/images_7_1.png"
                    )
                    
                    await select_interaction.response.send_message(embed=profile_embed, ephemeral=True)
            
            # Show profile selection
            profile_view = ProfileSelectView(members, self.level_mapping, self.alliance_id)
            total_pages = profile_view.get_total_pages()
            await interaction.response.send_message(
                f"**Select a player to view their profile:** (Page 1/{total_pages})",
                view=profile_view,
                ephemeral=True
            )
        except Exception as e:
            print(f"Error in view_profile: {e}")
            import traceback
            traceback.print_exc()
            await interaction.response.send_message("❌ Error loading profiles", ephemeral=True)


async def setup(bot):
    await bot.add_cog(BotOperations(bot, sqlite3.connect('db/settings.sqlite'))) 


