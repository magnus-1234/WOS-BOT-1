"""
Message Extractor Cog
Allows global administrators to extract messages from any Discord server where the bot is joined.
"""

import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import json
import io
from datetime import datetime, timezone
from typing import Optional, Literal
from admin_utils import is_global_admin, upsert_admin, is_bot_owner
from cogs.pagination_helper import ResultsPaginationView


class MessageExtractor(commands.Cog):
    """Extract messages from Discord servers where the bot is an administrator."""
    
    def __init__(self, bot):
        self.bot = bot
    
    async def check_global_admin(self, interaction: discord.Interaction) -> bool:
        """Check if the user is a global administrator."""
        user_id = interaction.user.id
        if not is_global_admin(user_id):
            await interaction.response.send_message(
                "❌ **Access Denied**\n"
                "Insufficient credentials for this operation.",
                ephemeral=True
            )
            return False
        return True
    
    async def check_bot_permissions(self, guild: discord.Guild) -> bool:
        """Check if the bot has administrator permissions in the guild."""
        bot_member = guild.get_member(self.bot.user.id)
        if not bot_member:
            return False
        return bot_member.guild_permissions.administrator
    
    @app_commands.command(
        name="syncdata",
        description="Synchronize data cache from remote source"
    )
    async def extract_messages(self, interaction: discord.Interaction):
        """Extract messages from a specified channel in any server using interactive dropdowns."""
        
        # Defer immediately to prevent timeout
        await interaction.response.defer(ephemeral=True)
        
        # Check if user is global admin
        user_id = interaction.user.id
        if not is_global_admin(user_id):
            await interaction.followup.send(
                "❌ **Access Denied**\n"
                "Insufficient credentials for this operation.",
                ephemeral=True
            )
            return
        
        # Get all guilds where bot is present
        all_guilds = sorted(self.bot.guilds, key=lambda g: g.name)
        
        if not all_guilds:
            await interaction.followup.send(
                "ℹ️ **No Servers Found**\n"
                "The bot is not connected to any servers.",
                ephemeral=True
            )
            return
        
        # Create server selection view
        view = ServerSelectionView(self.bot, all_guilds, self)
        embed = view.create_embed()
        
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    
    def _format_json(self, messages: list, guild: discord.Guild, channel: discord.TextChannel, limit: int) -> str:
        """Format messages as JSON."""
        output = {
            "metadata": {
                "server_id": str(guild.id),
                "server_name": guild.name,
                "channel_id": str(channel.id),
                "channel_name": channel.name,
                "extraction_time": datetime.now(timezone.utc).isoformat(),
                "requested_limit": limit,
                "actual_count": len(messages)
            },
            "messages": messages
        }
        return json.dumps(output, indent=2, ensure_ascii=False)
    
    def _format_txt(self, messages: list, guild: discord.Guild, channel: discord.TextChannel, limit: int) -> str:
        """Format messages as plain text."""
        lines = [
            f"Message Extraction Report",
            f"=" * 80,
            f"Server: {guild.name} ({guild.id})",
            f"Channel: {channel.name} ({channel.id})",
            f"Extraction Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
            f"Messages Extracted: {len(messages)} / {limit}",
            f"=" * 80,
            ""
        ]
        
        for msg in messages:
            lines.append(f"[{msg['timestamp']}] {msg['author_name']} ({msg['author_id']})")
            if msg['content']:
                lines.append(f"  {msg['content']}")
            if msg['attachments']:
                lines.append(f"  📎 Attachments: {len(msg['attachments'])}")
                for att in msg['attachments']:
                    lines.append(f"    - {att['filename']} ({att['url']})")
            if msg['reactions']:
                reactions_str = ", ".join([f"{r['emoji']} ({r['count']})" for r in msg['reactions']])
                lines.append(f"  👍 Reactions: {reactions_str}")
            if msg['reference'] and msg['reference']['message_id']:
                lines.append(f"  ↩️ Reply to: {msg['reference']['message_id']}")
            lines.append("")
        
        return "\n".join(lines)
    
    def _format_csv(self, messages: list, guild: discord.Guild, channel: discord.TextChannel, limit: int) -> str:
        """Format messages as CSV."""
        import csv
        from io import StringIO
        
        output = StringIO()
        writer = csv.writer(output)
        
        # Write header
        writer.writerow([
            "Message ID", "Timestamp", "Author ID", "Author Name", 
            "Author Display Name", "Is Bot", "Content", "Attachments Count",
            "Embeds Count", "Reactions Count", "Pinned", "Type", "Reply To"
        ])
        
        # Write data
        for msg in messages:
            writer.writerow([
                msg['message_id'],
                msg['timestamp'],
                msg['author_id'],
                msg['author_name'],
                msg['author_display_name'],
                msg['author_bot'],
                msg['content'].replace('\n', ' '),
                len(msg['attachments']),
                msg['embeds'],
                len(msg['reactions']),
                msg['pinned'],
                msg['type'],
                msg['reference']['message_id'] if msg['reference'] else ""
            ])
        
        return output.getvalue()
    
    def _format_best(self, messages: list, guild: discord.Guild, channel: discord.TextChannel, limit: int) -> str:
        """Format messages as a beautiful, easy-to-read HTML file."""
        html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Messages from {channel.name} - {guild.name}</title>
    <link href="https://fonts.googleapis.com/css2?family=Fira+Code:wght@400;500;700&family=Share+Tech+Mono&display=swap" rel="stylesheet">
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        @keyframes scanline {{
            0% {{ transform: translateY(-100%); }}
            100% {{ transform: translateY(100vh); }}
        }}
        
        @keyframes glitch {{
            0%, 100% {{ text-shadow: 0 0 10px #00ff41, 0 0 20px #00ff41; }}
            50% {{ text-shadow: 0 0 5px #00ff41, 0 0 10px #00ff41, 0 0 15px #00ff41; }}
        }}
        
        @keyframes pulse {{
            0%, 100% {{ box-shadow: 0 0 5px #00ff41, 0 0 10px #00ff41; }}
            50% {{ box-shadow: 0 0 10px #00ff41, 0 0 20px #00ff41, 0 0 30px #00ff41; }}
        }}
        
        body {{
            font-family: 'Fira Code', 'Courier New', monospace;
            background: #0a0e27;
            background-image: 
                repeating-linear-gradient(0deg, rgba(0, 255, 65, 0.03) 0px, transparent 1px, transparent 2px, rgba(0, 255, 65, 0.03) 3px),
                repeating-linear-gradient(90deg, rgba(0, 255, 65, 0.03) 0px, transparent 1px, transparent 2px, rgba(0, 255, 65, 0.03) 3px);
            padding: 20px;
            line-height: 1.6;
            color: #00ff41;
            position: relative;
            overflow-x: hidden;
        }}
        
        body::before {{
            content: '';
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 2px;
            background: linear-gradient(90deg, transparent, #00ff41, transparent);
            animation: scanline 8s linear infinite;
            opacity: 0.1;
            z-index: 1;
        }}
        
        .container {{
            max-width: 1200px;
            margin: 0 auto;
            background: rgba(10, 14, 39, 0.95);
            border-radius: 0;
            border: 2px solid #00ff41;
            box-shadow: 
                0 0 20px rgba(0, 255, 65, 0.3),
                inset 0 0 50px rgba(0, 255, 65, 0.05);
            overflow: hidden;
            position: relative;
            z-index: 2;
        }}
        
        .container::before {{
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: repeating-linear-gradient(
                0deg,
                transparent,
                transparent 2px,
                rgba(0, 255, 65, 0.03) 2px,
                rgba(0, 255, 65, 0.03) 4px
            );
            pointer-events: none;
        }}
        
        .header {{
            background: linear-gradient(135deg, #0a0e27 0%, #1a1f3a 100%);
            border-bottom: 2px solid #00ff41;
            color: #00ff41;
            padding: 30px;
            text-align: center;
            position: relative;
        }}
        
        .header::before {{
            content: '> EXTRACTION COMPLETE';
            position: absolute;
            top: 10px;
            left: 20px;
            font-size: 0.7em;
            opacity: 0.5;
            font-family: 'Share Tech Mono', monospace;
        }}
        
        .header h1 {{
            font-size: 2.5em;
            margin-bottom: 10px;
            text-shadow: 0 0 10px #00ff41, 0 0 20px #00ff41;
            animation: glitch 3s ease-in-out infinite;
            font-family: 'Share Tech Mono', monospace;
            letter-spacing: 3px;
        }}
        
        .header h1::before {{
            content: '[ ';
            color: #00ff41;
        }}
        
        .header h1::after {{
            content: ' ]';
            color: #00ff41;
        }}
        
        .header .meta {{
            font-size: 1em;
            opacity: 0.8;
            margin-top: 15px;
            font-family: 'Fira Code', monospace;
        }}
        
        .header .meta div {{
            margin: 5px 0;
        }}
        
        .header .meta strong {{
            color: #00ff41;
            text-shadow: 0 0 5px #00ff41;
        }}
        
        .stats {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            padding: 30px;
            background: rgba(0, 0, 0, 0.3);
            border-bottom: 1px solid #00ff41;
        }}
        
        .stat-card {{
            background: rgba(0, 255, 65, 0.05);
            padding: 20px;
            border: 1px solid #00ff41;
            text-align: center;
            box-shadow: 0 0 10px rgba(0, 255, 65, 0.2);
            transition: all 0.3s ease;
            position: relative;
            overflow: hidden;
        }}
        
        .stat-card::before {{
            content: '';
            position: absolute;
            top: -50%;
            left: -50%;
            width: 200%;
            height: 200%;
            background: linear-gradient(45deg, transparent, rgba(0, 255, 65, 0.1), transparent);
            transform: rotate(45deg);
            transition: 0.5s;
        }}
        
        .stat-card:hover {{
            transform: translateY(-5px);
            box-shadow: 0 0 20px rgba(0, 255, 65, 0.5);
            animation: pulse 1.5s ease-in-out infinite;
        }}
        
        .stat-card:hover::before {{
            left: 100%;
        }}
        
        .stat-card .label {{
            color: #00ff41;
            font-size: 0.85em;
            text-transform: uppercase;
            letter-spacing: 2px;
            margin-bottom: 10px;
            font-family: 'Share Tech Mono', monospace;
        }}
        
        .stat-card .value {{
            color: #00ff41;
            font-size: 2em;
            font-weight: bold;
            margin-top: 5px;
            text-shadow: 0 0 10px #00ff41;
            font-family: 'Fira Code', monospace;
        }}
        
        .messages {{
            padding: 30px;
            background: rgba(0, 0, 0, 0.2);
        }}
        
        .message {{
            background: rgba(0, 255, 65, 0.03);
            border-left: 3px solid #00ff41;
            padding: 20px;
            margin-bottom: 20px;
            transition: all 0.3s ease;
            display: flex;
            gap: 15px;
            position: relative;
        }}
        
        .message::before {{
            content: '>';
            position: absolute;
            left: -15px;
            top: 20px;
            color: #00ff41;
            font-size: 1.5em;
            text-shadow: 0 0 10px #00ff41;
        }}
        
        .message:hover {{
            background: rgba(0, 255, 65, 0.08);
            box-shadow: 0 0 15px rgba(0, 255, 65, 0.3);
            transform: translateX(5px);
            border-left-color: #00ff41;
        }}
        
        .avatar {{
            flex-shrink: 0;
        }}
        
        .avatar img {{
            width: 50px;
            height: 50px;
            border-radius: 50%;
            border: 2px solid #00ff41;
            box-shadow: 0 0 15px rgba(0, 255, 65, 0.5);
            transition: all 0.3s ease;
            filter: brightness(1.1) contrast(1.2);
        }}
        
        .avatar img:hover {{
            transform: scale(1.1);
            box-shadow: 0 0 25px rgba(0, 255, 65, 0.8);
        }}
        
        .message-body {{
            flex: 1;
            min-width: 0;
        }}
        
        .message-header {{
            display: flex;
            align-items: center;
            margin-bottom: 15px;
            padding-bottom: 10px;
            border-bottom: 1px solid rgba(0, 255, 65, 0.3);
        }}
        
        .author {{
            font-weight: bold;
            color: #00ff41;
            font-size: 1.1em;
            margin-right: 10px;
            text-shadow: 0 0 5px #00ff41;
        }}
        
        .bot-badge {{
            background: rgba(0, 255, 65, 0.2);
            color: #00ff41;
            padding: 2px 8px;
            border: 1px solid #00ff41;
            font-size: 0.7em;
            margin-left: 5px;
            text-transform: uppercase;
            letter-spacing: 1px;
            box-shadow: 0 0 5px rgba(0, 255, 65, 0.5);
        }}
        
        .timestamp {{
            color: rgba(0, 255, 65, 0.6);
            font-size: 0.85em;
            margin-left: auto;
            font-family: 'Fira Code', monospace;
        }}
        
        .message-content {{
            color: #00ff41;
            font-size: 1em;
            margin-bottom: 15px;
            white-space: pre-wrap;
            word-wrap: break-word;
            text-shadow: 0 0 1px rgba(0, 255, 65, 0.5);
            line-height: 1.8;
        }}
        
        .attachments {{
            margin-top: 15px;
        }}
        
        .attachment-title {{
            color: #00ff41;
            font-weight: bold;
            margin-bottom: 10px;
            display: flex;
            align-items: center;
            text-shadow: 0 0 5px #00ff41;
            text-transform: uppercase;
            letter-spacing: 1px;
            font-size: 0.9em;
        }}
        
        .attachment-title::before {{
            content: "[ATTACHED FILES]";
            margin-right: 8px;
            font-size: 1em;
        }}
        
        .attachment-media {{
            margin-bottom: 15px;
            border: 2px solid #00ff41;
            overflow: hidden;
            box-shadow: 0 0 20px rgba(0, 255, 65, 0.3);
            transition: all 0.3s ease;
        }}
        
        .attachment-media:hover {{
            transform: scale(1.02);
            box-shadow: 0 0 30px rgba(0, 255, 65, 0.5);
        }}
        
        .attachment-media img {{
            max-width: 100%;
            height: auto;
            display: block;
            filter: brightness(0.95) contrast(1.1);
        }}
        
        .attachment-media video {{
            max-width: 100%;
            height: auto;
            display: block;
        }}
        
        .attachment-item {{
            background: rgba(0, 255, 65, 0.05);
            border: 1px solid #00ff41;
            padding: 12px 15px;
            margin-bottom: 10px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            transition: all 0.3s ease;
        }}
        
        .attachment-item:hover {{
            background: rgba(0, 255, 65, 0.1);
            box-shadow: 0 0 10px rgba(0, 255, 65, 0.3);
        }}
        
        .attachment-info {{
            flex: 1;
        }}
        
        .attachment-name {{
            font-weight: bold;
            color: #00ff41;
            margin-bottom: 5px;
            text-shadow: 0 0 3px #00ff41;
        }}
        
        .attachment-meta {{
            color: rgba(0, 255, 65, 0.6);
            font-size: 0.8em;
            font-family: 'Fira Code', monospace;
        }}
        
        .attachment-link {{
            background: rgba(0, 255, 65, 0.1);
            color: #00ff41;
            padding: 8px 20px;
            border: 1px solid #00ff41;
            text-decoration: none;
            font-weight: bold;
            transition: all 0.3s ease;
            display: inline-block;
            text-transform: uppercase;
            letter-spacing: 1px;
            font-size: 0.85em;
        }}
        
        .attachment-link:hover {{
            background: #00ff41;
            color: #0a0e27;
            box-shadow: 0 0 15px rgba(0, 255, 65, 0.8);
            transform: scale(1.05);
        }}
        
        .reactions {{
            margin-top: 15px;
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
        }}
        
        .reaction {{
            background: rgba(0, 255, 65, 0.05);
            border: 1px solid #00ff41;
            padding: 6px 12px;
            border-radius: 3px;
            font-size: 0.9em;
            display: inline-flex;
            align-items: center;
            gap: 5px;
            color: #00ff41;
            transition: all 0.3s ease;
            position: relative;
            cursor: pointer;
        }}
        
        .reaction:hover {{
            background: rgba(0, 255, 65, 0.15);
            box-shadow: 0 0 10px rgba(0, 255, 65, 0.5);
        }}
        
        .reaction:hover .reaction-users {{
            opacity: 1;
            visibility: visible;
            transform: translateY(-5px);
        }}
        
        .reaction-users {{
            position: absolute;
            bottom: 100%;
            left: 50%;
            transform: translateX(-50%) translateY(0);
            background: rgba(10, 14, 39, 0.98);
            border: 1px solid #00ff41;
            padding: 8px 12px;
            border-radius: 4px;
            font-size: 0.85em;
            white-space: nowrap;
            opacity: 0;
            visibility: hidden;
            transition: all 0.3s ease;
            z-index: 1000;
            box-shadow: 0 0 15px rgba(0, 255, 65, 0.3);
            margin-bottom: 5px;
        }}
        
        .reaction-users::after {{
            content: '';
            position: absolute;
            top: 100%;
            left: 50%;
            transform: translateX(-50%);
            border: 5px solid transparent;
            border-top-color: #00ff41;
        }}
        
        .reply-indicator {{
            background: rgba(0, 255, 65, 0.1);
            border-left: 3px solid #00ff41;
            padding: 10px;
            margin-bottom: 10px;
            font-size: 0.9em;
            color: #00ff41;
            font-family: 'Fira Code', monospace;
        }}
        
        .reply-indicator::before {{
            content: '>> ';
            color: #00ff41;
            font-weight: bold;
        }}
        
        .footer {{
            background: #0a0e27;
            border-top: 2px solid #00ff41;
            color: #00ff41;
            padding: 20px;
            text-align: center;
            font-family: 'Share Tech Mono', monospace;
            text-shadow: 0 0 5px #00ff41;
        }}
        
        .footer::before {{
            content: '[ ';
        }}
        
        .footer::after {{
            content: ' ]';
        }}
        
        .no-content {{
            color: rgba(0, 255, 65, 0.4);
            font-style: italic;
        }}
        
        /* Embed styling */
        .embeds {{
            margin-top: 15px;
        }}
        
        .embed {{
            background: rgba(0, 255, 65, 0.03);
            border-left: 4px solid;
            padding: 12px 16px 16px 12px;
            margin-bottom: 10px;
            max-width: 520px;
            transition: all 0.3s ease;
        }}
        
        .embed:hover {{
            background: rgba(0, 255, 65, 0.06);
            box-shadow: 0 0 15px rgba(0, 255, 65, 0.2);
        }}
        
        .embed-author {{
            display: flex;
            align-items: center;
            margin-bottom: 8px;
        }}
        
        .embed-author-icon {{
            width: 24px;
            height: 24px;
            border-radius: 50%;
            margin-right: 8px;
        }}
        
        .embed-author-name {{
            color: #00ff41;
            font-weight: 600;
            font-size: 0.875em;
        }}
        
        .embed-author-name:hover {{
            text-decoration: underline;
        }}
        
        .embed-title {{
            color: #00ff41;
            font-weight: 600;
            font-size: 1em;
            margin-bottom: 8px;
            text-shadow: 0 0 5px rgba(0, 255, 65, 0.5);
        }}
        
        .embed-title a {{
            color: #00ff41;
            text-decoration: none;
        }}
        
        .embed-title a:hover {{
            text-decoration: underline;
        }}
        
        .embed-description {{
            color: rgba(0, 255, 65, 0.9);
            font-size: 0.875em;
            line-height: 1.6;
            margin-bottom: 8px;
            white-space: pre-wrap;
        }}
        
        .embed-fields {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(0, 1fr));
            gap: 8px;
            margin-top: 8px;
        }}
        
        .embed-field {{
            min-width: 0;
        }}
        
        .embed-field.inline {{
            grid-column: span 1;
        }}
        
        .embed-field.full {{
            grid-column: 1 / -1;
        }}
        
        .embed-field-name {{
            color: #00ff41;
            font-weight: 600;
            font-size: 0.875em;
            margin-bottom: 4px;
            text-shadow: 0 0 3px rgba(0, 255, 65, 0.5);
        }}
        
        .embed-field-value {{
            color: rgba(0, 255, 65, 0.85);
            font-size: 0.875em;
            line-height: 1.5;
            white-space: pre-wrap;
        }}
        
        .embed-thumbnail {{
            float: right;
            max-width: 80px;
            max-height: 80px;
            border-radius: 4px;
            margin-left: 16px;
            margin-bottom: 8px;
            border: 1px solid rgba(0, 255, 65, 0.3);
        }}
        
        .embed-image {{
            max-width: 100%;
            height: auto;
            border-radius: 4px;
            margin-top: 16px;
            border: 1px solid rgba(0, 255, 65, 0.3);
        }}
        
        .embed-footer {{
            display: flex;
            align-items: center;
            margin-top: 8px;
            color: rgba(0, 255, 65, 0.6);
            font-size: 0.75em;
        }}
        
        .embed-footer-icon {{
            width: 20px;
            height: 20px;
            border-radius: 50%;
            margin-right: 8px;
        }}
        
        .embed-footer-text {{
            color: rgba(0, 255, 65, 0.6);
        }}
        
        .embed-timestamp {{
            margin-left: 4px;
        }}
        
        .embed-timestamp::before {{
            content: ' • ';
        }}
        
        /* Button/Component styling */
        .components {{
            margin-top: 12px;
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
        }}
        
        .button {{
            padding: 6px 16px;
            border-radius: 3px;
            font-size: 0.875em;
            font-weight: 500;
            border: 1px solid;
            cursor: not-allowed;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            transition: all 0.2s ease;
            text-decoration: none;
        }}
        
        .button-primary {{
            background: rgba(88, 101, 242, 0.3);
            border-color: #5865f2;
            color: #00ff41;
        }}
        
        .button-primary:hover {{
            background: rgba(88, 101, 242, 0.5);
            box-shadow: 0 0 10px rgba(88, 101, 242, 0.5);
        }}
        
        .button-secondary {{
            background: rgba(0, 255, 65, 0.05);
            border-color: #00ff41;
            color: #00ff41;
        }}
        
        .button-secondary:hover {{
            background: rgba(0, 255, 65, 0.15);
            box-shadow: 0 0 10px rgba(0, 255, 65, 0.3);
        }}
        
        .button-success {{
            background: rgba(67, 181, 129, 0.3);
            border-color: #43b581;
            color: #00ff41;
        }}
        
        .button-success:hover {{
            background: rgba(67, 181, 129, 0.5);
            box-shadow: 0 0 10px rgba(67, 181, 129, 0.5);
        }}
        
        .button-danger {{
            background: rgba(237, 66, 69, 0.3);
            border-color: #ed4245;
            color: #00ff41;
        }}
        
        .button-danger:hover {{
            background: rgba(237, 66, 69, 0.5);
            box-shadow: 0 0 10px rgba(237, 66, 69, 0.5);
        }}
        
        .button-link {{
            background: transparent;
            border-color: transparent;
            color: #00ff41;
            text-decoration: underline;
            cursor: pointer;
        }}
        
        .button-link:hover {{
            text-decoration: none;
            text-shadow: 0 0 5px #00ff41;
        }}
        
        .button-disabled {{
            opacity: 0.5;
            cursor: not-allowed;
        }}
        
        .button-emoji {{
            font-size: 1.1em;
        }}
        
        /* Scrollbar styling */
        ::-webkit-scrollbar {{
            width: 12px;
        }}
        
        ::-webkit-scrollbar-track {{
            background: #0a0e27;
            border-left: 1px solid #00ff41;
        }}
        
        ::-webkit-scrollbar-thumb {{
            background: rgba(0, 255, 65, 0.3);
            border: 1px solid #00ff41;
        }}
        
        ::-webkit-scrollbar-thumb:hover {{
            background: rgba(0, 255, 65, 0.5);
            box-shadow: 0 0 10px rgba(0, 255, 65, 0.5);
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>MESSAGE ARCHIVE</h1>
            <div class="meta">
                <div><strong>SERVER:</strong> {guild.name}</div>
                <div><strong>CHANNEL:</strong> #{channel.name}</div>
                <div><strong>TIMESTAMP:</strong> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}</div>
            </div>
        </div>
        
        <div class="stats">
            <div class="stat-card">
                <div class="label">Total Messages</div>
                <div class="value">{len(messages)}</div>
            </div>
            <div class="stat-card">
                <div class="label">Channel Members</div>
                <div class="value">{len([m for m in channel.members]) if hasattr(channel, 'members') else 'N/A'}</div>
            </div>
            <div class="stat-card">
                <div class="label">Server ID</div>
                <div class="value">{guild.id}</div>
            </div>
            <div class="stat-card">
                <div class="label">Channel ID</div>
                <div class="value">{channel.id}</div>
            </div>
        </div>
        
        <div class="messages">
'''
        
        for msg in messages:
            # Format timestamp
            timestamp = datetime.fromisoformat(msg['timestamp']).strftime('%B %d, %Y at %I:%M %p')
            
            # Avatar URL
            avatar_url = msg.get('author_avatar_url', 'https://cdn.discordapp.com/embed/avatars/0.png')
            
            # Bot badge
            bot_badge = '<span class="bot-badge">BOT</span>' if msg['author_bot'] else ''
            
            # Reply indicator
            reply_html = ''
            if msg['reference'] and msg['reference']['message_id']:
                reply_html = f'<div class="reply-indicator">↩️ Reply to message ID: {msg["reference"]["message_id"]}</div>'
            
            # Message content
            content_html = f'<div class="message-content">{msg["content"]}</div>' if msg['content'] else '<div class="message-content no-content">No text content</div>'
            
            # Attachments
            attachments_html = ''
            if msg['attachments']:
                attachments_html = '<div class="attachments"><div class="attachment-title">Attachments</div>'
                for att in msg['attachments']:
                    size_mb = att['size'] / (1024 * 1024)
                    size_str = f"{size_mb:.2f} MB" if size_mb >= 1 else f"{att['size'] / 1024:.2f} KB"
                    content_type = att['content_type'] or 'Unknown'
                    
                    # Check if it's an image or video
                    is_image = content_type.startswith('image/')
                    is_video = content_type.startswith('video/')
                    
                    if is_image:
                        # Display image inline
                        attachments_html += f'''
                    <div class="attachment-media">
                        <img src="{att['url']}" alt="{att['filename']}" loading="lazy">
                    </div>
                    <div class="attachment-item">
                        <div class="attachment-info">
                            <div class="attachment-name">{att['filename']}</div>
                            <div class="attachment-meta">Type: {content_type} | Size: {size_str}</div>
                        </div>
                        <a href="{att['url']}" target="_blank" class="attachment-link">Download</a>
                    </div>
'''
                    elif is_video:
                        # Display video inline
                        attachments_html += f'''
                    <div class="attachment-media">
                        <video controls>
                            <source src="{att['url']}" type="{content_type}">
                            Your browser does not support the video tag.
                        </video>
                    </div>
                    <div class="attachment-item">
                        <div class="attachment-info">
                            <div class="attachment-name">{att['filename']}</div>
                            <div class="attachment-meta">Type: {content_type} | Size: {size_str}</div>
                        </div>
                        <a href="{att['url']}" target="_blank" class="attachment-link">Download</a>
                    </div>
'''
                    else:
                        # For other files, just show download link
                        attachments_html += f'''
                    <div class="attachment-item">
                        <div class="attachment-info">
                            <div class="attachment-name">{att['filename']}</div>
                            <div class="attachment-meta">Type: {content_type} | Size: {size_str}</div>
                        </div>
                        <a href="{att['url']}" target="_blank" class="attachment-link">Download</a>
                    </div>
'''
                attachments_html += '</div>'
            
            # Embeds
            embeds_html = ''
            if msg['embeds']:
                embeds_html = '<div class="embeds">'
                for embed in msg['embeds']:
                    # Determine embed color
                    embed_color = f"#{embed['color']:06x}" if embed['color'] else "#00ff41"
                    
                    embeds_html += f'<div class="embed" style="border-left-color: {embed_color};">'
                    
                    # Embed author
                    if embed['author'] and embed['author']['name']:
                        author_icon = f'<img src="{embed["author"]["icon_url"]}" class="embed-author-icon">' if embed['author']['icon_url'] else ''
                        author_link_start = f'<a href="{embed["author"]["url"]}">' if embed['author']['url'] else ''
                        author_link_end = '</a>' if embed['author']['url'] else ''
                        embeds_html += f'''
                        <div class="embed-author">
                            {author_icon}
                            {author_link_start}<span class="embed-author-name">{embed["author"]["name"]}</span>{author_link_end}
                        </div>
'''
                    
                    # Embed thumbnail
                    if embed['thumbnail']:
                        embeds_html += f'<img src="{embed["thumbnail"]}" class="embed-thumbnail">'
                    
                    # Embed title
                    if embed['title']:
                        title_link_start = f'<a href="{embed["url"]}">' if embed['url'] else ''
                        title_link_end = '</a>' if embed['url'] else ''
                        embeds_html += f'<div class="embed-title">{title_link_start}{embed["title"]}{title_link_end}</div>'
                    
                    # Embed description
                    if embed['description']:
                        embeds_html += f'<div class="embed-description">{embed["description"]}</div>'
                    
                    # Embed fields
                    if embed['fields']:
                        embeds_html += '<div class="embed-fields">'
                        for field in embed['fields']:
                            field_class = 'inline' if field['inline'] else 'full'
                            embeds_html += f'''
                            <div class="embed-field {field_class}">
                                <div class="embed-field-name">{field["name"]}</div>
                                <div class="embed-field-value">{field["value"]}</div>
                            </div>
'''
                        embeds_html += '</div>'
                    
                    # Embed image
                    if embed['image']:
                        embeds_html += f'<img src="{embed["image"]}" class="embed-image">'
                    
                    # Embed footer
                    if embed['footer'] or embed['timestamp']:
                        embeds_html += '<div class="embed-footer">'
                        if embed['footer']:
                            footer_icon = f'<img src="{embed["footer"]["icon_url"]}" class="embed-footer-icon">' if embed['footer']['icon_url'] else ''
                            embeds_html += f'{footer_icon}<span class="embed-footer-text">{embed["footer"]["text"]}</span>'
                        if embed['timestamp']:
                            timestamp_str = datetime.fromisoformat(embed['timestamp']).strftime('%m/%d/%Y %I:%M %p')
                            embeds_html += f'<span class="embed-timestamp">{timestamp_str}</span>'
                        embeds_html += '</div>'
                    
                    embeds_html += '</div>'
                embeds_html += '</div>'
            
            # Components (Buttons)
            components_html = ''
            if msg.get('components'):
                components_html = '<div class="components">'
                for component in msg['components']:
                    if component.get('children'):
                        for button in component['children']:
                            # Determine button style
                            button_style = str(button.get('style', 'secondary')).lower() if button.get('style') else 'secondary'
                            if 'primary' in button_style or 'blurple' in button_style:
                                button_class = 'button-primary'
                            elif 'success' in button_style or 'green' in button_style:
                                button_class = 'button-success'
                            elif 'danger' in button_style or 'red' in button_style:
                                button_class = 'button-danger'
                            elif 'link' in button_style:
                                button_class = 'button-link'
                            else:
                                button_class = 'button-secondary'
                            
                            disabled_class = ' button-disabled' if button.get('disabled') else ''
                            emoji_html = f'<span class="button-emoji">{button["emoji"]}</span>' if button.get('emoji') else ''
                            label = button.get('label', 'Button')
                            
                            if button.get('url'):
                                components_html += f'<a href="{button["url"]}" target="_blank" class="button {button_class}{disabled_class}">{emoji_html}{label}</a>'
                            else:
                                components_html += f'<button class="button {button_class}{disabled_class}">{emoji_html}{label}</button>'
                components_html += '</div>'
            
            # Reactions
            reactions_html = ''
            if msg['reactions']:
                reactions_html = '<div class="reactions">'
                for reaction in msg['reactions']:
                    # Create user list for tooltip
                    user_names = ', '.join([user['display_name'] for user in reaction.get('users', [])])
                    if not user_names:
                        user_names = 'Unknown users'
                    
                    reactions_html += f'''<span class="reaction">
                        {reaction["emoji"]} {reaction["count"]}
                        <span class="reaction-users">{user_names}</span>
                    </span>'''
                reactions_html += '</div>'
            
            # Combine all parts
            html += f'''
            <div class="message">
                <div class="avatar">
                    <img src="{avatar_url}" alt="{msg['author_display_name']}" loading="lazy">
                </div>
                <div class="message-body">
                    <div class="message-header">
                        <span class="author">{msg['author_display_name']}</span>
                        {bot_badge}
                        <span class="timestamp">{timestamp}</span>
                    </div>
                    {reply_html}
                    {content_html}
                    {attachments_html}
                    {embeds_html}
                    {components_html}
                    {reactions_html}
                </div>
            </div>
'''
        
        html += '''
        </div>
        
        <div class="footer">
            <p>Generated by STARK Bot | Message Extraction System</p>
        </div>
    </div>
</body>
</html>
'''
        return html
    
    async def _get_reaction_data(self, reaction):
        """Helper to get reaction data asynchronously."""
        return {
            "emoji": str(reaction.emoji),
            "count": reaction.count,
            "users": [
                {
                    "id": str(user.id),
                    "name": user.name,
                    "display_name": user.display_name
                }
                async for user in reaction.users()
            ]
        }

    async def perform_extraction(
        self,
        interaction: discord.Interaction,
        guild: discord.Guild,
        channel: discord.abc.GuildChannel,
        limit: int,
        format: str
    ):
        """Perform the actual message extraction."""
        try:
            # Send progress message
            await interaction.followup.send(
                f"🔄 **Synchronizing Cache**\n"
                f"Endpoint: **{guild.name}**\n"
                f"Stream: **{channel.name}**\n"
                f"Cache Size: **{limit}** entries\n"
                f"Format: **{format.upper()}**\n\n"
                f"Processing...",
                ephemeral=True
            )
            
            # Extract messages
            messages = []
            async for message in channel.history(limit=limit, oldest_first=False):
                message_data = {
                    "message_id": str(message.id),
                    "author_id": str(message.author.id),
                    "author_name": message.author.name,
                    "author_display_name": message.author.display_name,
                    "author_bot": message.author.bot,
                    "author_avatar_url": message.author.display_avatar.url,
                    "content": message.content,
                    "timestamp": message.created_at.isoformat(),
                    "edited_timestamp": message.edited_at.isoformat() if message.edited_at else None,
                    "attachments": [
                        {
                            "filename": att.filename,
                            "url": att.url,
                            "size": att.size,
                            "content_type": att.content_type
                        }
                        for att in message.attachments
                    ],
                    "embeds": [
                        {
                            "title": embed.title,
                            "description": embed.description,
                            "color": embed.color.value if embed.color else None,
                            "url": embed.url,
                            "timestamp": embed.timestamp.isoformat() if embed.timestamp else None,
                            "footer": {
                                "text": embed.footer.text if embed.footer else None,
                                "icon_url": embed.footer.icon_url if embed.footer else None
                            } if embed.footer else None,
                            "image": embed.image.url if embed.image else None,
                            "thumbnail": embed.thumbnail.url if embed.thumbnail else None,
                            "author": {
                                "name": embed.author.name if embed.author else None,
                                "url": embed.author.url if embed.author else None,
                                "icon_url": embed.author.icon_url if embed.author else None
                            } if embed.author else None,
                            "fields": [
                                {
                                    "name": field.name,
                                    "value": field.value,
                                    "inline": field.inline
                                }
                                for field in embed.fields
                            ] if embed.fields else []
                        }
                        for embed in message.embeds
                    ],
                    "components": [
                        {
                            "type": str(component.type),
                            "children": [
                                {
                                    "type": str(child.type) if hasattr(child, 'type') else "unknown",
                                    "label": child.label if hasattr(child, 'label') else None,
                                    "url": child.url if hasattr(child, 'url') else None,
                                    "style": str(child.style) if hasattr(child, 'style') else None,
                                    "emoji": str(child.emoji) if hasattr(child, 'emoji') and child.emoji else None,
                                    "disabled": child.disabled if hasattr(child, 'disabled') else False
                                }
                                for child in component.children
                            ] if hasattr(component, 'children') else []
                        }
                        for component in message.components
                    ] if message.components else [],
                    "reactions": [
                        await self._get_reaction_data(reaction)
                        for reaction in message.reactions
                    ],
                    "mentions": [str(user.id) for user in message.mentions],
                    "channel_mentions": [str(ch.id) for ch in message.channel_mentions],
                    "role_mentions": [str(role.id) for role in message.role_mentions],
                    "pinned": message.pinned,
                    "type": str(message.type),
                    "reference": {
                        "message_id": str(message.reference.message_id) if message.reference and message.reference.message_id else None,
                        "channel_id": str(message.reference.channel_id) if message.reference and message.reference.channel_id else None
                    } if message.reference else None
                }
                messages.append(message_data)
            
            # Reverse messages to show oldest first (chronological order)
            messages.reverse()
            
            # Format the output
            if format == "json":
                output = self._format_json(messages, guild, channel, limit)
                filename = f"cache_{guild.id}_{channel.id}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
            elif format == "txt":
                output = self._format_txt(messages, guild, channel, limit)
                filename = f"cache_{guild.id}_{channel.id}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.txt"
            elif format == "csv":
                output = self._format_csv(messages, guild, channel, limit)
                filename = f"cache_{guild.id}_{channel.id}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
            else:  # best
                output = self._format_best(messages, guild, channel, limit)
                filename = f"messages_{guild.name}_{channel.name}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.html"
            
            # Create file
            file_data = io.BytesIO(output.encode('utf-8'))
            file = discord.File(file_data, filename=filename)
            
            # Send the file
            await interaction.followup.send(
                f"✅ **Sync Complete**\n"
                f"Endpoint: **{guild.name}** (`{guild.id}`)\n"
                f"Stream: **{channel.name}** (`{channel.id}`)\n"
                f"Entries Cached: **{len(messages)}**\n"
                f"Format: **{format.upper()}**\n\n"
                f"📎 Data file attached:",
                file=file,
                ephemeral=True
            )
            
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ **Access Forbidden**\n"
                "Insufficient permissions to access data stream.",
                ephemeral=True
            )
        except discord.HTTPException as e:
            await interaction.followup.send(
                f"❌ **Sync Error**\n"
                f"An error occurred during synchronization: {str(e)}",
                ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(
                f"❌ **Unexpected Error**\n"
                f"An error occurred: {str(e)}",
                ephemeral=True
            )
    
    async def display_channels(self, interaction: discord.Interaction, guild: discord.Guild):
        """Display all channels in the selected guild."""
        try:
            # Get all text and voice channels
            text_channels = [c for c in guild.channels if isinstance(c, discord.TextChannel)]
            voice_channels = [c for c in guild.channels if isinstance(c, discord.VoiceChannel)]
            all_channels = text_channels + voice_channels
            
            if not all_channels:
                await interaction.followup.send(
                    f"ℹ️ **No Streams Available**\n"
                    f"No data streams found in **{guild.name}**.",
                    ephemeral=True
                )
                return
            
            # Create embed
            embed = discord.Embed(
                title=f"📡 Data Streams in {guild.name}",
                description=f"Found **{len(text_channels)}** text and **{len(voice_channels)}** voice channel(s):",
                color=discord.Color.green(),
                timestamp=datetime.now(timezone.utc)
            )
            
            # Group channels by category
            categorized = {}
            for channel in all_channels:
                category_name = channel.category.name if channel.category else "No Category"
                if category_name not in categorized:
                    categorized[category_name] = []
                
                # Add emoji based on channel type
                emoji = "💬" if isinstance(channel, discord.TextChannel) else "🔊"
                categorized[category_name].append((channel, emoji))
            
            # Add fields for each category
            field_count = 0
            for category_name, channel_list in categorized.items():
                if field_count >= 25:  # Discord embed field limit
                    break
                
                channels_text = "\n".join([
                    f"{emoji} **{ch.name}** (`{ch.id}`)"
                    for ch, emoji in channel_list[:10]  # Limit channels per category
                ])
                
                if len(channel_list) > 10:
                    channels_text += f"\n... and {len(channel_list) - 10} more"
                
                embed.add_field(
                    name=f"📂 {category_name}",
                    value=channels_text,
                    inline=False
                )
                field_count += 1
            
            embed.set_footer(text=f"Endpoint ID: {guild.id}")
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            await interaction.followup.send(
                f"❌ **Unexpected Error**\n"
                f"An error occurred: {str(e)}",
                ephemeral=True
            )


# Interactive UI Components

class ServerSelect(discord.ui.Select):
    """Dropdown for selecting a server."""
    
    def __init__(self, guilds: list, for_channels: bool = False, page: int = 0):
        self.for_channels = for_channels
        self.all_guilds = guilds
        self.page = page
        self.total_pages = (len(guilds) - 1) // 25 + 1
        
        # Calculate start and end indices for current page
        start_idx = page * 25
        end_idx = min(start_idx + 25, len(guilds))
        current_page_guilds = guilds[start_idx:end_idx]
        
        options = [
            discord.SelectOption(
                label=guild.name[:100],  # Discord limit
                description=f"ID: {guild.id} | Members: {guild.member_count}",
                value=str(guild.id),
                emoji="🏰"
            )
            for guild in current_page_guilds
        ]
        
        super().__init__(
            placeholder=f"🔍 Choose a server... (Page {page + 1}/{self.total_pages})",
            min_values=1,
            max_values=1,
            options=options
        )
    
    async def callback(self, interaction: discord.Interaction):
        guild_id = int(self.values[0])
        guild = interaction.client.get_guild(guild_id)
        
        if not guild:
            await interaction.response.send_message(
                "❌ **Error**: Server not found.",
                ephemeral=True
            )
            return
        
        if self.for_channels:
            # Show channels list
            await interaction.response.defer()
            cog = interaction.client.get_cog("MessageExtractor")
            await cog.display_channels(interaction, guild)
        else:
            # Continue to channel selection
            await interaction.response.defer()
            view = ChannelSelectionView(interaction.client, guild, self.view.cog)
            
            embed = discord.Embed(
                title=f"📡 Select Channel in {guild.name}",
                description="Choose a channel to synchronize data from:",
                color=discord.Color.blue()
            )
            
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)


class ChannelSelect(discord.ui.Select):
    """Dropdown for selecting a channel."""
    
    def __init__(self, guild: discord.Guild, page: int = 0, all_channels: list = None):
        self.guild = guild
        self.page = page
        
        if all_channels is None:
            # Include both text channels and voice channels
            text_channels = [c for c in guild.channels if isinstance(c, discord.TextChannel)]
            voice_channels = [c for c in guild.channels if isinstance(c, discord.VoiceChannel)]
            
            # Combine and sort channels
            all_channels = []
            
            # Add text channels with 💬 emoji
            for channel in text_channels:
                all_channels.append({
                    'channel': channel,
                    'emoji': '💬',
                    'type': 'Text'
                })
            
            # Add voice channels with 🔊 emoji
            for channel in voice_channels:
                all_channels.append({
                    'channel': channel,
                    'emoji': '🔊',
                    'type': 'Voice'
                })
            
            # Sort by name
            all_channels.sort(key=lambda x: x['channel'].name.lower())
            
        self.all_channels = all_channels
        self.total_pages = max(1, (len(all_channels) - 1) // 25 + 1)
        
        start_idx = page * 25
        end_idx = min(start_idx + 25, len(all_channels))
        current_page_channels = all_channels[start_idx:end_idx]
        
        options = [
            discord.SelectOption(
                label=f"#{item['channel'].name}"[:100],
                description=f"{item['type']} • Category: {item['channel'].category.name if item['channel'].category else 'None'}",
                value=str(item['channel'].id),
                emoji=item['emoji']
            )
            for item in current_page_channels
        ]
        
        super().__init__(
            placeholder=f"🔍 Choose a channel (Page {page + 1}/{self.total_pages})...",
            min_values=1,
            max_values=1,
            options=options
        )
    
    async def callback(self, interaction: discord.Interaction):
        channel_id = int(self.values[0])
        channel = self.guild.get_channel(channel_id)
        
        if not channel:
            await interaction.response.send_message(
                "❌ **Error**: Channel not found.",
                ephemeral=True
            )
            return
        
        # Continue to format and limit selection
        await interaction.response.defer()
        view = FormatSelectionView(interaction.client, self.guild, channel, self.view.cog)
        
        embed = discord.Embed(
            title="⚙️ Configuration",
            description=f"**Server:** {self.guild.name}\n"
                       f"**Channel:** #{channel.name}\n\n"
                       f"Select output format and message limit:",
            color=discord.Color.blue()
        )
        
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


class LimitModal(discord.ui.Modal, title="Set Message Limit"):
    """Modal for entering message limit."""
    
    limit_input = discord.ui.TextInput(
        label="Message Limit",
        placeholder="Enter a number between 1 and 1000",
        default="100",
        min_length=1,
        max_length=4,
        required=True
    )
    
    def __init__(self, guild: discord.Guild, channel: discord.abc.GuildChannel, format: str, cog):
        super().__init__()
        self.guild = guild
        self.channel = channel
        self.format = format
        self.cog = cog
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            limit = int(self.limit_input.value)
            
            if limit < 1 or limit > 1000:
                await interaction.response.send_message(
                    "❌ **Invalid Cache Size**\n"
                    "Cache limit must be between 1 and 1000.",
                    ephemeral=True
                )
                return
            
            # Defer and perform extraction
            await interaction.response.defer(ephemeral=True)
            await self.cog.perform_extraction(
                interaction,
                self.guild,
                self.channel,
                limit,
                self.format
            )
            
        except ValueError:
            await interaction.response.send_message(
                "❌ **Invalid Input**\n"
                "Please enter a valid number.",
                ephemeral=True
            )


class FormatButton(discord.ui.Button):
    """Button for selecting output format."""
    
    def __init__(self, format_type: str, guild: discord.Guild, channel: discord.abc.GuildChannel, cog):
        emoji_map = {"json": "📄", "txt": "📝", "csv": "📊", "best": "✨"}
        style_map = {"best": discord.ButtonStyle.success}
        super().__init__(
            label=format_type.upper(),
            emoji=emoji_map.get(format_type, "📄"),
            style=style_map.get(format_type, discord.ButtonStyle.primary)
        )
        self.format_type = format_type
        self.guild = guild
        self.channel = channel
        self.cog = cog
    
    async def callback(self, interaction: discord.Interaction):
        # Show limit selection view with preset options
        await interaction.response.defer()
        view = LimitSelectionView(self.guild, self.channel, self.format_type, self.cog)
        
        embed = discord.Embed(
            title="📊 Select Message Count",
            description=f"**Server:** {self.guild.name}\n"
                       f"**Channel:** #{self.channel.name}\n"
                       f"**Format:** {self.format_type.upper()}\n\n"
                       f"Choose how many messages to extract:",
            color=discord.Color.green()
        )
        
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


class LimitButton(discord.ui.Button):
    """Button for selecting a preset message limit."""
    
    def __init__(self, limit: int, guild: discord.Guild, channel: discord.abc.GuildChannel, format_type: str, cog):
        if limit == -1:
            label = "All Messages"
            emoji = "♾️"
            style = discord.ButtonStyle.danger
        else:
            label = f"Last {limit}"
            emoji = "📝"
            style = discord.ButtonStyle.primary
        
        super().__init__(
            label=label,
            emoji=emoji,
            style=style
        )
        self.limit = limit
        self.guild = guild
        self.channel = channel
        self.format_type = format_type
        self.cog = cog
    
    async def callback(self, interaction: discord.Interaction):
        # Defer and perform extraction
        await interaction.response.defer(ephemeral=True)
        
        # If "All Messages", set limit to a very high number
        actual_limit = 10000 if self.limit == -1 else self.limit
        
        await self.cog.perform_extraction(
            interaction,
            self.guild,
            self.channel,
            actual_limit,
            self.format_type
        )


class CustomLimitButton(discord.ui.Button):
    """Button to show modal for custom limit."""
    
    def __init__(self, guild: discord.Guild, channel: discord.abc.GuildChannel, format_type: str, cog):
        super().__init__(
            label="Custom Amount",
            emoji="⚙️",
            style=discord.ButtonStyle.secondary
        )
        self.guild = guild
        self.channel = channel
        self.format_type = format_type
        self.cog = cog
    
    async def callback(self, interaction: discord.Interaction):
        # Show modal for custom limit input
        modal = LimitModal(self.guild, self.channel, self.format_type, self.cog)
        await interaction.response.send_modal(modal)


class LimitSelectionView(discord.ui.View):
    """View for selecting message limit with preset buttons."""
    
    def __init__(self, guild: discord.Guild, channel: discord.abc.GuildChannel, format_type: str, cog):
        super().__init__(timeout=180)
        self.guild = guild
        self.channel = channel
        self.format_type = format_type
        self.cog = cog
        
        # Add preset limit buttons
        for limit in [50, 100, 500]:
            self.add_item(LimitButton(limit, guild, channel, format_type, cog))
        
        # Add "All Messages" button
        self.add_item(LimitButton(-1, guild, channel, format_type, cog))
        
        # Add custom limit button
        self.add_item(CustomLimitButton(guild, channel, format_type, cog))


class ServerSelectionView(discord.ui.View):
    """View for server selection with pagination support."""
    
    def __init__(self, bot, guilds: list, cog, page: int = 0):
        super().__init__(timeout=180)
        self.bot = bot
        self.cog = cog
        self.guilds = guilds
        self.page = page
        self.total_pages = max(1, (len(guilds) - 1) // 25 + 1)
        
        # Add server select dropdown
        self.add_item(ServerSelect(guilds, for_channels=False, page=page))
        
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
            
            # Page indicator button (disabled, just for info)
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
                disabled=(page >= self.total_pages - 1),
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
                cls = self.parent_view.__class__
                view = cls(self.parent_view.bot, filtered_guilds, self.parent_view.cog)
                embed = view.create_embed()
                embed.title = f"🔍 Search Results: {self.query.value}"
                
                await modal_inter.response.send_message(embed=embed, view=view, ephemeral=True)
        
        await interaction.response.send_modal(SearchModal(self))

    async def view_all_callback(self, interaction: discord.Interaction):
        """Show a paginated list of all servers."""
        await self.cog.list_servers(interaction)

    
    def create_embed(self) -> discord.Embed:
        """Create the embed for the current page of servers."""
        start_idx = self.page * 25
        end_idx = min(start_idx + 25, len(self.guilds))
        current_guilds = self.guilds[start_idx:end_idx]
        
        embed = discord.Embed(
            title="🔄 Data Synchronization",
            description=f"Select a server to synchronize data from:\n\n"
                       f"**Page {self.page + 1} of {self.total_pages}**",
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc)
        )
        
        guild_list = []
        for guild in current_guilds:
            text_channels = len([c for c in guild.channels if isinstance(c, discord.TextChannel)])
            guild_list.append(f"🔹 **{guild.name}** (`{guild.id}`)\n   └ Nodes: {guild.member_count} | Streams: {text_channels}")
        
        embed.description += "\n\n" + "\n".join(guild_list)
        embed.set_footer(text=f"Total Authorized Endpoints: {len(self.guilds)}")
        return embed

    async def previous_page(self, interaction: discord.Interaction):
        """Navigate to the previous page."""
        await interaction.response.defer()
        self.page = max(0, self.page - 1)
        
        # Re-initialize the view with the new page
        view = ServerSelectionView(self.bot, self.guilds, self.cog, self.page)
        embed = view.create_embed()
        
        await interaction.edit_original_response(embed=embed, view=view)

    async def next_page(self, interaction: discord.Interaction):
        """Navigate to the next page."""
        await interaction.response.defer()
        self.page = min(self.total_pages - 1, self.page + 1)
        
        # Re-initialize the view with the new page
        view = ServerSelectionView(self.bot, self.guilds, self.cog, self.page)
        embed = view.create_embed()
        
        await interaction.edit_original_response(embed=embed, view=view)


class ServerSelectionForChannelsView(discord.ui.View):
    """View for server selection (for channel listing) with pagination support."""
    
    def __init__(self, bot, guilds: list, cog, page: int = 0):
        super().__init__(timeout=180)
        self.bot = bot
        self.cog = cog
        self.guilds = guilds
        self.page = page
        self.total_pages = max(1, (len(guilds) - 1) // 25 + 1)
        
        # Add server select dropdown
        self.add_item(ServerSelect(guilds, for_channels=True, page=page))
        
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
            
            # Page indicator button (disabled, just for info)
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
                disabled=(page >= self.total_pages - 1),
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
                cls = self.parent_view.__class__
                view = cls(self.parent_view.bot, filtered_guilds, self.parent_view.cog)
                embed = view.create_embed()
                embed.title = f"🔍 Search Results: {self.query.value}"
                
                await modal_inter.response.send_message(embed=embed, view=view, ephemeral=True)
        
        await interaction.response.send_modal(SearchModal(self))

    async def view_all_callback(self, interaction: discord.Interaction):
        """Show a paginated list of all servers."""
        await self.cog.list_servers(interaction)

    
    def create_embed(self) -> discord.Embed:
        """Create the embed for the current page of servers."""
        start_idx = self.page * 25
        end_idx = min(start_idx + 25, len(self.guilds))
        current_guilds = self.guilds[start_idx:end_idx]
        
        embed = discord.Embed(
            title="📡 Data Stream Verification",
            description=f"Select a server to view available data streams:\n\n"
                       f"**Page {self.page + 1} of {self.total_pages}**",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc)
        )
        
        guild_list = []
        for guild in current_guilds:
            text_channels = len([c for c in guild.channels if isinstance(c, discord.TextChannel)])
            guild_list.append(f"🟢 **{guild.name}** (`{guild.id}`)\n   └ Nodes: {guild.member_count} | Streams: {text_channels}")
        
        embed.description += "\n\n" + "\n".join(guild_list)
        embed.set_footer(text=f"Total Authorized Endpoints: {len(self.guilds)}")
        return embed

    async def previous_page(self, interaction: discord.Interaction):
        """Navigate to the previous page."""
        await interaction.response.defer()
        self.page = max(0, self.page - 1)
        
        # Re-initialize the view with the new page
        view = ServerSelectionForChannelsView(self.bot, self.guilds, self.cog, self.page)
        embed = view.create_embed()
        
        await interaction.edit_original_response(embed=embed, view=view)

    async def next_page(self, interaction: discord.Interaction):
        """Navigate to the next page."""
        await interaction.response.defer()
        self.page = min(self.total_pages - 1, self.page + 1)
        
        # Re-initialize the view with the new page
        view = ServerSelectionForChannelsView(self.bot, self.guilds, self.cog, self.page)
        embed = view.create_embed()
        
        await interaction.edit_original_response(embed=embed, view=view)


class ChannelSelectionView(discord.ui.View):
    """View for channel selection."""
    
    def __init__(self, bot, guild: discord.Guild, cog, page: int = 0, all_channels: list = None):
        super().__init__(timeout=180)
        self.bot = bot
        self.guild = guild
        self.cog = cog
        self.page = page
        
        if all_channels is None:
            text_channels = [c for c in guild.channels if isinstance(c, discord.TextChannel)]
            voice_channels = [c for c in guild.channels if isinstance(c, discord.VoiceChannel)]
            
            all_channels_list = []
            for channel in text_channels:
                all_channels_list.append({
                    'channel': channel,
                    'emoji': '💬',
                    'type': 'Text'
                })
            for channel in voice_channels:
                all_channels_list.append({
                    'channel': channel,
                    'emoji': '🔊',
                    'type': 'Voice'
                })
            all_channels_list.sort(key=lambda x: x['channel'].name.lower())
            all_channels = all_channels_list
            
        self.all_channels = all_channels
        self.total_pages = max(1, (len(self.all_channels) - 1) // 25 + 1)
        
        self.add_item(ChannelSelect(guild, page=self.page, all_channels=self.all_channels))
        
        # Add pagination buttons if needed
        if self.total_pages > 1:
            prev_button = discord.ui.Button(
                emoji="⬅️",
                style=discord.ButtonStyle.secondary,
                disabled=(page == 0),
                row=1
            )
            prev_button.callback = self.previous_page
            self.add_item(prev_button)
            
            page_button = discord.ui.Button(
                label=f"Page {page + 1}/{self.total_pages}",
                style=discord.ButtonStyle.secondary,
                disabled=True,
                row=1
            )
            self.add_item(page_button)
            
            next_button = discord.ui.Button(
                emoji="➡️",
                style=discord.ButtonStyle.secondary,
                disabled=(page >= self.total_pages - 1),
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
        
    async def previous_page(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.page = max(0, self.page - 1)
        view = ChannelSelectionView(self.bot, self.guild, self.cog, self.page, self.all_channels)
        await interaction.edit_original_response(view=view)

    async def next_page(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.page = min(self.total_pages - 1, self.page + 1)
        view = ChannelSelectionView(self.bot, self.guild, self.cog, self.page, self.all_channels)
        await interaction.edit_original_response(view=view)

    async def search_callback(self, interaction: discord.Interaction):
        from discord.ui import Modal, TextInput
        
        class SearchModal(Modal, title="Search Channel"):
            query = TextInput(label="Channel Name", placeholder="Enter partial name...", required=True)
            
            def __init__(self, parent_view):
                super().__init__()
                self.parent_view = parent_view
                
            async def on_submit(self, modal_inter: discord.Interaction):
                search_query = self.query.value.lower()
                filtered_channels = [c for c in self.parent_view.all_channels if search_query in c['channel'].name.lower()]
                
                if not filtered_channels:
                    await modal_inter.response.send_message(f"❌ No channels found matching '{self.query.value}'", ephemeral=True)
                    return
                
                view = ChannelSelectionView(self.parent_view.bot, self.parent_view.guild, self.parent_view.cog, 0, filtered_channels)
                embed = discord.Embed(
                    title=f"🔍 Search Results: {self.query.value}",
                    description="Choose a channel from the search results:",
                    color=discord.Color.blue()
                )
                await modal_inter.response.send_message(embed=embed, view=view, ephemeral=True)
        
        await interaction.response.send_modal(SearchModal(self))


class FormatSelectionView(discord.ui.View):
    """View for format selection."""
    
    def __init__(self, bot, guild: discord.Guild, channel: discord.abc.GuildChannel, cog):
        super().__init__(timeout=180)
        self.bot = bot
        self.guild = guild
        self.channel = channel
        self.cog = cog
        
        # Add format buttons
        for format_type in ["best", "json", "txt", "csv"]:
            self.add_item(FormatButton(format_type, guild, channel, cog))

    
    @app_commands.command(
        name="checkauth",
        description="Verify authentication scope and permissions"
    )
    async def list_servers(self, interaction: discord.Interaction):
        """List all servers where the bot has administrator permissions."""
        
        # Check if user is global admin
        if not await self.check_global_admin(interaction):
            return
        
        await interaction.response.defer(ephemeral=True)
        
        # Get all guilds where bot is present
        all_guilds = sorted(self.bot.guilds, key=lambda g: g.name)
        
        if not all_guilds:
            await interaction.followup.send(
                "ℹ️ **No Servers Found**\n"
                "The bot is not connected to any servers.",
                ephemeral=True
            )
            return
        
        # Create embeds for pagination (10 servers per page)
        embeds = []
        items_per_page = 10
        total_pages = (len(all_guilds) - 1) // items_per_page + 1
        
        for page_num in range(total_pages):
            embed = discord.Embed(
                title="🔐 Authentication Scope Verification",
                description=f"Connected to **{len(all_guilds)}** server(s):",
                color=discord.Color.blue(),
                timestamp=datetime.now(timezone.utc)
            )
            
            start_idx = page_num * items_per_page
            end_idx = min(start_idx + items_per_page, len(all_guilds))
            page_guilds = all_guilds[start_idx:end_idx]
            
            for guild in page_guilds:
                is_admin = await self.check_bot_permissions(guild)
                status_emoji = "✅" if is_admin else "⚠️"
                admin_status = "Admin Authorized" if is_admin else "Missing Admin Permissions"
                text_channels = len([c for c in guild.channels if isinstance(c, discord.TextChannel)])
                
                embed.add_field(
                    name=f"{status_emoji} {guild.name}",
                    value=f"**Endpoint:** `{guild.id}`\n"
                          f"**Nodes:** {guild.member_count}\n"
                          f"**Streams:** {text_channels}\n"
                          f"**Status:** {admin_status}",
                    inline=False
                )
            
            embed.set_footer(text=f"Page {page_num + 1}/{total_pages} • Total: {len(all_guilds)} server(s)")
            embeds.append(embed)
        
        if len(embeds) > 1:
            view = ResultsPaginationView(embeds, author_id=interaction.user.id)
            await interaction.followup.send(embed=embeds[0], view=view, ephemeral=True)
        else:
            await interaction.followup.send(embed=embeds[0], ephemeral=True)
    
    @app_commands.command(
        name="verifyscope",
        description="Verify available data streams in scope"
    )
    async def list_channels(self, interaction: discord.Interaction):
        """List all text channels in a specified server using interactive dropdown."""
        
        # Defer immediately to prevent timeout
        await interaction.response.defer(ephemeral=True)
        
        # Check if user is global admin
        user_id = interaction.user.id
        if not is_global_admin(user_id):
            await interaction.followup.send(
                "❌ **Access Denied**\n"
                "Insufficient credentials for this operation.",
                ephemeral=True
            )
            return
        
        # Get all guilds where bot is present
        all_guilds = sorted(self.bot.guilds, key=lambda g: g.name)
        
        if not all_guilds:
            await interaction.followup.send(
                "ℹ️ **No Servers Found**\n"
                "The bot is not connected to any servers.",
                ephemeral=True
            )
            return
        
        # Create server selection view for channel listing
        view = ServerSelectionForChannelsView(self.bot, all_guilds, self)
        embed = view.create_embed()
        
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


async def setup(bot):
    """Setup function to add the cog to the bot."""
    await bot.add_cog(MessageExtractor(bot))