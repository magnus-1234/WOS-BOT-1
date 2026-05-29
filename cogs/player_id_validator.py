import discord
from discord.ext import commands
import re
import logging
import sqlite3
import json

# Import db_utils for consistent connection handling
try:
    from db_utils import get_db_connection
except ImportError:
    # Fallback
    def get_db_connection(db_name):
        return sqlite3.connect(f'db/{db_name}')

logger = logging.getLogger(__name__)

class PlayerIDValidator(commands.Cog):
    """Cog to validate player IDs in messages and react with emojis"""
    
    def __init__(self, bot):
        self.bot = bot
        self.conn_users = get_db_connection('users.sqlite')
        self.c_users = self.conn_users.cursor()
        self.conn_alliance = get_db_connection('alliance.sqlite')
        self.c_alliance = self.conn_alliance.cursor()
        
    def _get_local_player_info(self, player_id: str):
        """Check if player exists in local DB and return info including alliance name"""
        try:
            # Check users table
            self.c_users.execute("SELECT nickname, furnace_lv, alliance FROM users WHERE fid = ?", (player_id,))
            result = self.c_users.fetchone()
            
            if result:
                nickname, furnace_lv, alliance_id = result
                logger.info(f"Player {player_id} found in local DB. Nickname: {nickname}, Alliance ID: {alliance_id}")
                
                # Get alliance name if alliance_id exists
                alliance_name = None
                if alliance_id:
                    try:
                        # Ensure alliance_id is an integer for the query
                        try:
                            alliance_id_int = int(alliance_id)
                        except (ValueError, TypeError):
                            logger.warning(f"Invalid alliance_id format for player {player_id}: {alliance_id}")
                            alliance_id_int = None
                        
                        if alliance_id_int is not None:
                            # Note: alliance_list is usually in alliance.sqlite based on other cogs
                            self.c_alliance.execute("SELECT name FROM alliance_list WHERE alliance_id = ?", (alliance_id_int,))
                            alliance_res = self.c_alliance.fetchone()
                            if alliance_res:
                                alliance_name = alliance_res[0]
                                logger.info(f"Alliance found for ID {alliance_id_int}: {alliance_name}")
                            else:
                                logger.warning(f"Alliance ID {alliance_id_int} found in users but not in alliance_list")
                    except Exception as e:
                        logger.error(f"Error fetching alliance name for {alliance_id}: {e}")
                else:
                    logger.info(f"No alliance ID set for player {player_id}")
                
                return {
                    "found": True,
                    "nickname": nickname,
                    "level": furnace_lv,
                    "alliance_name": alliance_name
                }
            return {"found": False}
        except Exception as e:
            logger.error(f"Error checking local DB for player {player_id}: {e}")
            return {"found": False}

    async def _validate_via_api(self, player_id: str):
        """Validate via API using GiftOperations"""
        try:
            gift_ops = self.bot.get_cog('GiftOperations')
            if not gift_ops:
                return {"valid": False, "error": "GiftOperations cog not found"}
            
            # Use get_stove_info_wos directly to get details if possible, 
            # or verify_test_fid which wraps it but might not return all details in the format we want.
            # verify_test_fid returns (is_valid, msg).
            # Let's try to use get_stove_info_wos if accessible for more data, 
            # but verify_test_fid is safer if get_stove_info_wos isn't exposed or complex.
            # Looking at GiftOperations source, verify_test_fid logs details but returns simple tuple.
            # However, we can use get_stove_info_wos if we want the data.
            
            if hasattr(gift_ops, 'get_stove_info_wos'):
                session, response = await gift_ops.get_stove_info_wos(player_id)
                try:
                    data = response.json()
                    if data.get("msg") == "success":
                        player_data = data.get("data", {})
                        return {
                            "valid": True,
                            "nickname": player_data.get("nickname", "Unknown"),
                            "level": player_data.get("stove_lv", "Unknown")
                        }
                except Exception:
                    pass
            
            # Fallback to verify_test_fid if get_stove_info_wos fails or isn't usable directly
            is_valid, msg = await gift_ops.verify_test_fid(player_id)
            if is_valid:
                return {"valid": True, "nickname": "Unknown", "level": "Unknown"}
            
            return {"valid": False}
            
        except Exception as e:
            logger.error(f"Error validating via API for {player_id}: {e}")
            return {"valid": False}

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listen for messages containing 8- or 9-digit player IDs"""
        if message.author.bot:
            return
        
        pattern = r'\b\d{8,9}\b'
        matches = re.findall(pattern, message.content)
        
        if not matches:
            return
        
        for player_id in matches:
            try:
                # 1. Check Local DB
                local_info = self._get_local_player_info(player_id)
                
                if local_info["found"]:
                    # Valid in Local DB
                    await message.add_reaction('✅')
                    
                    # Create Embed
                    embed = discord.Embed(title="ℹ️ Player Info", color=discord.Color.green())
                    embed.add_field(name="👤 Nickname", value=local_info.get("nickname", "Unknown"), inline=True)
                    embed.add_field(name="🆔 Player ID", value=player_id, inline=True)
                    embed.add_field(name="🔥 Furnace Level", value=str(local_info.get("level", "Unknown")), inline=True)
                    
                    # Add Alliance field ONLY if found locally
                    if local_info.get("alliance_name"):
                        embed.add_field(name="🛡️ Alliance", value=local_info["alliance_name"], inline=False)
                    
                    await message.channel.send(embed=embed)
                    logger.info(f"Local player ID detected: {player_id}")
                    break # Handle only first ID
                
                # 2. Check API
                else:
                    api_info = await self._validate_via_api(player_id)
                    if api_info["valid"]:
                        # Valid via API
                        await message.add_reaction('✅')
                        
                        # Create Embed (No Alliance Field)
                        embed = discord.Embed(title="ℹ️ Player Info", color=discord.Color.blue())
                        embed.add_field(name="👤 Nickname", value=api_info.get("nickname", "Unknown"), inline=True)
                        embed.add_field(name="🆔 Player ID", value=player_id, inline=True)
                        embed.add_field(name="🔥 Furnace Level", value=str(api_info.get("level", "Unknown")), inline=True)
                        
                        await message.channel.send(embed=embed)
                        logger.info(f"API player ID detected: {player_id}")
                        break
                    else:
                        # Invalid
                        await message.add_reaction('❌')
                        logger.info(f"Invalid player ID detected: {player_id}")
                        break

            except discord.Forbidden:
                logger.warning(f"Missing permissions in channel {message.channel.id}")
                break
            except Exception as e:
                logger.error(f"Error processing player ID {player_id}: {e}")
                break

async def setup(bot):
    await bot.add_cog(PlayerIDValidator(bot))
