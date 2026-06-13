import discord
from discord.ext import commands
from discord import app_commands
import sqlite3
import os
from pathlib import Path

db_dir = Path("db")
db_dir.mkdir(parents=True, exist_ok=True)
PFP_DB_PATH = db_dir / "pfp_voting.sqlite"
SETTINGS_DB_PATH = db_dir / "settings.sqlite"

def get_pfp_db():
    return sqlite3.connect(str(PFP_DB_PATH))

def get_settings_db():
    return sqlite3.connect(str(SETTINGS_DB_PATH))

class PFPVoteView(discord.ui.View):
    def __init__(self, event_id: int, candidates: list, current_index: int = 0):
        super().__init__(timeout=None)
        self.event_id = event_id
        self.candidates = candidates
        self.current_index = current_index
        
        if self.current_index <= 0:
            self.prev_button.disabled = True
        if self.current_index >= len(self.candidates) - 1:
            self.next_button.disabled = True

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.primary, custom_id="pfp_prev")
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_index -= 1
        await self.update_message(interaction)

    @discord.ui.button(label="Vote! 🌟", style=discord.ButtonStyle.success, custom_id="pfp_vote")
    async def vote_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        candidate = self.candidates[self.current_index]
        with get_pfp_db() as conn:
            c = conn.cursor()
            try:
                c.execute("INSERT INTO pfp_votes (event_id, voter_id, voted_player_id) VALUES (?, ?, ?)", 
                          (self.event_id, str(interaction.user.id), candidate['player_id']))
                conn.commit()
                await interaction.response.send_message(f"✅ You voted for **{candidate['nickname']}**'s PFP!", ephemeral=True)
            except sqlite3.IntegrityError:
                c.execute("UPDATE pfp_votes SET voted_player_id = ? WHERE event_id = ? AND voter_id = ?", 
                          (candidate['player_id'], self.event_id, str(interaction.user.id)))
                conn.commit()
                await interaction.response.send_message(f"✅ You changed your vote to **{candidate['nickname']}**'s PFP!", ephemeral=True)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary, custom_id="pfp_next")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_index += 1
        await self.update_message(interaction)

    async def update_message(self, interaction: discord.Interaction):
        embed = self.create_embed()
        new_view = PFPVoteView(self.event_id, self.candidates, self.current_index)
        await interaction.response.edit_message(embed=embed, view=new_view)
        
    def create_embed(self):
        candidate = self.candidates[self.current_index]
        embed = discord.Embed(
            title="✨ SvS Theme PFP Voting ✨",
            description=f"Voting for **{candidate['nickname']}**\n\nCandidate {self.current_index + 1} of {len(self.candidates)}",
            color=discord.Color.gold()
        )
        if candidate['avatar_url']:
            embed.set_image(url=candidate['avatar_url'])
        else:
            embed.add_field(name="No PFP", value="This player has not set an avatar yet or it wasn't tracked.")
        
        embed.set_footer(text="Use Previous and Next to browse candidates, and click Vote! to cast your choice.")
        return embed

class PFPAdminView(discord.ui.View):
    def __init__(self, cog, alliance_id: str):
        super().__init__(timeout=None)
        self.cog = cog
        self.alliance_id = alliance_id

    @discord.ui.button(label="Start PFP Event", style=discord.ButtonStyle.success, custom_id="pfp_admin_start")
    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.start_event(interaction, self.alliance_id)

    @discord.ui.button(label="Spawn Voting UI", style=discord.ButtonStyle.primary, custom_id="pfp_admin_spawn")
    async def spawn_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.spawn_ui(interaction)

    @discord.ui.button(label="End & Show Results", style=discord.ButtonStyle.danger, custom_id="pfp_admin_end")
    async def end_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.end_event(interaction)

class PFPVotingCog(commands.Cog, name="PFPVoting"):
    """PFP Voting Event module for SvS Themes."""

    def __init__(self, bot):
        self.bot = bot
        self._init_db()

    def _init_db(self):
        with get_pfp_db() as conn:
            c = conn.cursor()
            c.execute('''CREATE TABLE IF NOT EXISTS pfp_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT,
                alliance_id TEXT,
                status TEXT
            )''')
            c.execute('''CREATE TABLE IF NOT EXISTS pfp_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER,
                player_id TEXT,
                nickname TEXT,
                avatar_url TEXT
            )''')
            c.execute('''CREATE TABLE IF NOT EXISTS pfp_votes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER,
                voter_id TEXT,
                voted_player_id TEXT,
                UNIQUE(event_id, voter_id)
            )''')
            c.execute('''CREATE TABLE IF NOT EXISTS allowed_servers (
                guild_id TEXT PRIMARY KEY
            )''')
            conn.commit()

    async def show_admin_menu(self, interaction: discord.Interaction):
        # We check allowed servers
        with get_pfp_db() as conn:
            c = conn.cursor()
            c.execute("SELECT 1 FROM allowed_servers WHERE guild_id = ?", (str(interaction.guild_id),))
            is_allowed = c.fetchone() is not None
        
        # Admin can toggle it if they are the owner
        is_owner = await self.bot.is_owner(interaction.user)
        
        if not is_allowed and not is_owner:
            await interaction.response.send_message("❌ This experimental feature is not enabled for this server.", ephemeral=True)
            return

        embed = discord.Embed(
            title="✨ PFP Voting Admin Panel ✨",
            description="Manage the SvS PFP Voting Event.\n" + ("(Experimental feature enabled for this server)" if is_allowed else "*(Not enabled for this server, but you are owner)*"),
            color=discord.Color.purple()
        )
        
        # We need an alliance ID. For now, fetch the first alliance for this server from alliance.sqlite
        alliance_id = "0"
        try:
            with sqlite3.connect("db/alliance.sqlite") as a_conn:
                ac = a_conn.cursor()
                ac.execute("SELECT alliance_id FROM alliance_list WHERE discord_server_id = ? LIMIT 1", (interaction.guild_id,))
                res = ac.fetchone()
                if res:
                    alliance_id = str(res[0])
        except:
            pass
            
        view = PFPAdminView(self, alliance_id)
        
        if is_owner:
            # Add toggle button
            toggle_btn = discord.ui.Button(
                label="Disable Server" if is_allowed else "Enable Server",
                style=discord.ButtonStyle.danger if is_allowed else discord.ButtonStyle.success,
                custom_id="pfp_admin_toggle",
                row=1
            )
            
            async def toggle_callback(btn_interaction: discord.Interaction):
                if not await self.bot.is_owner(btn_interaction.user):
                    return
                with get_pfp_db() as dbconn:
                    dc = dbconn.cursor()
                    if is_allowed:
                        dc.execute("DELETE FROM allowed_servers WHERE guild_id = ?", (str(btn_interaction.guild_id),))
                    else:
                        dc.execute("INSERT INTO allowed_servers (guild_id) VALUES (?)", (str(btn_interaction.guild_id),))
                    dbconn.commit()
                await self.show_admin_menu(btn_interaction)
                
            toggle_btn.callback = toggle_callback
            view.add_item(toggle_btn)

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def start_event(self, interaction: discord.Interaction, alliance_id: str):
        if str(alliance_id) == "0":
            await interaction.response.send_message("❌ Could not find an alliance linked to this server. Ensure you have set up an alliance.", ephemeral=True)
            return
            
        await interaction.response.defer(ephemeral=True)
        
        with get_pfp_db() as conn:
            c = conn.cursor()
            c.execute("SELECT event_id FROM pfp_events WHERE guild_id = ? AND alliance_id = ? AND status = 'active'", (str(interaction.guild_id), alliance_id))
            active_event = c.fetchone()
            
            if active_event:
                await interaction.followup.send(f"❌ A PFP Voting event is already active for Alliance `{alliance_id}`.", ephemeral=True)
                return
            
            # Fetch candidates from member_history
            candidates = []
            try:
                with get_settings_db() as s_conn:
                    s_c = s_conn.cursor()
                    s_c.execute("SELECT fid, nickname, avatar_image FROM member_history WHERE alliance_id = ? AND avatar_image != '' AND avatar_image IS NOT NULL", (alliance_id,))
                    candidates = s_c.fetchall()
            except Exception as e:
                await interaction.followup.send(f"❌ Failed to read member history. Ensure Alliance Monitor has run for this alliance at least once. Error: {e}", ephemeral=True)
                return
                
            if not candidates:
                await interaction.followup.send(f"❌ No members with PFPs found for Alliance `{alliance_id}`. Please ensure the Alliance Monitor is tracking them.", ephemeral=True)
                return
                
            # Create Event
            c.execute("INSERT INTO pfp_events (guild_id, alliance_id, status) VALUES (?, ?, 'active')", (str(interaction.guild_id), alliance_id))
            event_id = c.lastrowid
            
            # Insert candidates
            for fid, nickname, avatar_url in candidates:
                c.execute("INSERT INTO pfp_candidates (event_id, player_id, nickname, avatar_url) VALUES (?, ?, ?, ?)", 
                          (event_id, str(fid), nickname, avatar_url))
            
            conn.commit()
            
        embed = discord.Embed(
            title="🎉 PFP Voting Event Started! 🎉",
            description=f"A new SvS PFP Voting event has started for Alliance `{alliance_id}`!\n**{len(candidates)}** candidates were found.",
            color=discord.Color.green()
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def spawn_ui(self, interaction: discord.Interaction):
        with get_pfp_db() as conn:
            c = conn.cursor()
            c.execute("SELECT event_id, alliance_id FROM pfp_events WHERE guild_id = ? AND status = 'active' ORDER BY event_id DESC LIMIT 1", (str(interaction.guild_id),))
            active_event = c.fetchone()
            
            if not active_event:
                await interaction.response.send_message("❌ There is no active PFP Voting event right now.", ephemeral=True)
                return
                
            event_id, alliance_id = active_event
            
            c.execute("SELECT player_id, nickname, avatar_url FROM pfp_candidates WHERE event_id = ?", (event_id,))
            rows = c.fetchall()
            
            if not rows:
                await interaction.response.send_message("❌ No candidates found for this event.", ephemeral=True)
                return
                
            candidates = [{"player_id": r[0], "nickname": r[1], "avatar_url": r[2]} for r in rows]
            
            view = PFPVoteView(event_id, candidates, 0)
            embed = view.create_embed()
            
            # Send UI to channel publicly
            await interaction.channel.send(embed=embed, view=view)
            await interaction.response.send_message("✅ Voting panel sent to this channel!", ephemeral=True)

    async def end_event(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False) # Make this public
        with get_pfp_db() as conn:
            c = conn.cursor()
            c.execute("SELECT event_id, alliance_id FROM pfp_events WHERE guild_id = ? AND status = 'active'", (str(interaction.guild_id),))
            active_event = c.fetchone()
            
            if not active_event:
                await interaction.followup.send("❌ There is no active PFP Voting event right now.", ephemeral=True)
                return
                
            event_id, alliance_id = active_event
            
            # Count votes
            c.execute('''
                SELECT c.nickname, COUNT(v.id) as vote_count, c.avatar_url
                FROM pfp_candidates c
                LEFT JOIN pfp_votes v ON c.player_id = v.voted_player_id AND c.event_id = v.event_id
                WHERE c.event_id = ?
                GROUP BY c.player_id
                ORDER BY vote_count DESC
                LIMIT 10
            ''', (event_id,))
            
            results = c.fetchall()
            
            # End Event
            c.execute("UPDATE pfp_events SET status = 'ended' WHERE event_id = ?", (event_id,))
            conn.commit()
            
        embed = discord.Embed(
            title="🏆 PFP Voting Results 🏆",
            description=f"The PFP Voting Event for Alliance `{alliance_id}` has concluded! Here are the top PFPs:",
            color=discord.Color.gold()
        )
        
        if not results or results[0][1] == 0:
            embed.description += "\n\n*No votes were cast!*"
        else:
            for idx, (nickname, vote_count, avatar_url) in enumerate(results):
                if vote_count > 0:
                    medal = "🥇" if idx == 0 else "🥈" if idx == 1 else "🥉" if idx == 2 else f"#{idx+1}"
                    embed.add_field(name=f"{medal} {nickname}", value=f"**{vote_count}** votes", inline=False)
            
            # Set top winner as thumbnail
            if results[0][2]:
                embed.set_thumbnail(url=results[0][2])
                
        await interaction.followup.send(embed=embed)

async def setup(bot):
    await bot.add_cog(PFPVotingCog(bot))
