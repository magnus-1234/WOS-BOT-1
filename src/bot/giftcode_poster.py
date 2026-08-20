import asyncio
import json
import os
from datetime import datetime
import logging
from typing import Dict, List, Optional

import discord

from gift_codes import get_active_gift_codes
try:
    from db.mongo_adapters import mongo_enabled, GiftcodeStateAdapter, GiftCodesAdapter, SentGiftCodesAdapter
except Exception:
    mongo_enabled = lambda: False
    GiftcodeStateAdapter = None
    GiftCodesAdapter = None
    SentGiftCodesAdapter = None

logger = logging.getLogger(__name__)

# State file to persist configured channels and sent codes
STATE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'data', 'giftcode_state.json')

# Default check interval in seconds (reduced to 10s by default for faster checks)
DEFAULT_INTERVAL = int(os.getenv('GIFTCODE_CHECK_INTERVAL', '10'))  # 10 seconds


class GiftCodePoster:
    def __init__(self):
        # Structure: {
        #   "channels": {"<guild_id>": <channel_id>, ...},
        #   "sent": {"<guild_id>": ["CODE1","CODE2"], "global": [..]}
        # }
        self.state: Dict = {"channels": {}, "sent": {}}
        self.lock = asyncio.Lock()
        self._load_state()

    def _normalize_code(self, code: str) -> str:
        """Normalize code strings for consistent comparison/storage."""
        if not code:
            return ""
        return str(code).strip().upper()

    def _load_state(self):
        loaded_from_mongo = False
        try:
            # Prefer Mongo when available
            if mongo_enabled() and GiftcodeStateAdapter is not None:
                try:
                    s = GiftcodeStateAdapter.get_state()
                    if s:
                        self.state = s
                        # Ensure normalized shapes
                        self.state.setdefault('channels', {})
                        self.state.setdefault('sent', {})
                        self.state.setdefault('initialized', False)
                        loaded_from_mongo = True
                except Exception:
                    pass
            if not loaded_from_mongo:
                if os.path.exists(STATE_FILE):
                    with open(STATE_FILE, 'r', encoding='utf-8') as f:
                        self.state = json.load(f)
                else:
                    self._save_state_sync()
        except Exception as e:
            logger.error(f"Failed to load giftcode state: {e}")
        # Normalize any existing sent codes to ensure consistent comparisons
        try:
            sent = self.state.setdefault('sent', {})
            for guild_id, codes in list(sent.items()):
                normalized = [self._normalize_code(c) for c in (codes or []) if c]
                self.state['sent'][str(guild_id)] = list(dict.fromkeys(normalized))
        except Exception:
            pass
        # If Mongo is available, and we have a separate gift_codes collection,
        # pull any globally recorded codes so they count as already-sent.
        try:
            if mongo_enabled() and GiftCodesAdapter is not None:
                try:
                    all_codes = GiftCodesAdapter.get_all() or []
                    # get_all returns list of tuples like (code, date, status)
                    global_codes = [self._normalize_code(t[0]) for t in all_codes if t and t[0]]
                    if global_codes:
                        self.state.setdefault('sent', {}).setdefault('global', [])
                        # merge and dedupe
                        existing = set(self.state['sent'].get('global', []))
                        merged = list(dict.fromkeys([*existing, *global_codes]))
                        self.state['sent']['global'] = merged
                except Exception:
                    pass
        except Exception:
            pass
        # Ensure initialized flag exists so we can detect first-run behavior
        try:
            self.state.setdefault('initialized', False)
        except Exception:
            pass
        
        # === MIGRATION: Sync existing codes to SentGiftCodesAdapter ===
        try:
            if mongo_enabled() and SentGiftCodesAdapter is not None:
                # Check if migration has been done
                if not self.state.get('migrated_to_sent_adapter', False):
                    logger.info("🔄 Starting migration of sent codes to SentGiftCodesAdapter...")
                    migration_count = 0
                    
                    # Migrate guild-specific codes
                    sent_data = self.state.get('sent', {})
                    for guild_id_str, codes in sent_data.items():
                        if guild_id_str == 'global':
                            continue  # Skip global for now
                        
                        try:
                            guild_id = int(guild_id_str)
                            if codes:
                                normalized_codes = [self._normalize_code(c) for c in codes if c]
                                if normalized_codes:
                                    success = SentGiftCodesAdapter.mark_codes_sent(
                                        guild_id=guild_id,
                                        codes=normalized_codes,
                                        source='migration'
                                    )
                                    if success:
                                        migration_count += len(normalized_codes)
                                        logger.info(f"✅ Migrated {len(normalized_codes)} codes for guild {guild_id}")
                        except Exception as e:
                            logger.warning(f"Failed to migrate codes for guild {guild_id_str}: {e}")
                    
                    # Mark migration as complete
                    self.state['migrated_to_sent_adapter'] = True
                    self._save_state_sync()
                    logger.info(f"🎯 Migration complete: {migration_count} total codes migrated to MongoDB")
                else:
                    logger.debug("Migration already completed, skipping")
        except Exception as e:
            logger.warning(f"Migration to SentGiftCodesAdapter failed (non-fatal): {e}")

    def _save_state_sync(self):
        try:
            # Prefer Mongo when available
            if mongo_enabled() and GiftcodeStateAdapter is not None:
                try:
                    GiftcodeStateAdapter.set_state(self.state)
                    return
                except Exception:
                    pass
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            with open(STATE_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to write giftcode state: {e}")

    async def _save_state(self):
        async with self.lock:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._save_state_sync)

    def set_channel(self, guild_id: int, channel_id: int):
        self.state.setdefault('channels', {})[str(guild_id)] = int(channel_id)
        # ensure sent dict exists for guild
        self.state.setdefault('sent', {}).setdefault(str(guild_id), [])
        # persist synchronously (caller should await saved state when possible)
        try:
            self._save_state_sync()
        except Exception:
            pass

    def unset_channel(self, guild_id: int):
        self.state.get('channels', {}).pop(str(guild_id), None)
        try:
            self._save_state_sync()
        except Exception:
            pass

    def get_channel(self, guild_id: int):
        return self.state.get('channels', {}).get(str(guild_id))

    def list_channels(self) -> Dict[str, int]:
        return {int(k): int(v) for k, v in self.state.get('channels', {}).items()}

    def _get_sent_set_unlocked(self, guild_id: int):
        sent = self.state.setdefault('sent', {})
        guild_codes = sent.setdefault(str(guild_id), [])
        global_codes = sent.setdefault('global', [])
        combined = list((guild_codes or []) + (global_codes or []))
        fallback_set = set(self._normalize_code(c) for c in combined if c)

        mongo_codes = set()
        if mongo_enabled() and SentGiftCodesAdapter is not None:
            try:
                m_codes = SentGiftCodesAdapter.get_sent_codes(guild_id)
                if m_codes:
                    mongo_codes = set(self._normalize_code(c) for c in m_codes if c)
                    logger.debug(f"📊 MongoDB: Retrieved {len(mongo_codes)} sent codes for guild {guild_id}")
                else:
                    logger.debug(f"MongoDB: No codes found for guild {guild_id}")
            except Exception as e:
                logger.warning(f"⚠️ MongoDB retrieval failed for guild {guild_id}: {e}")

        final_set = fallback_set.union(mongo_codes)
        if final_set:
            logger.debug(f"📂 Final Set: Retrieved {len(final_set)} sent codes for guild {guild_id}")
        return final_set

    async def reserve_unsent_codes(self, guild_id: int, codes: List[str]):
        """Atomically find unsent codes and record them before Discord posting."""
        async with self.lock:
            sent_set = self._get_sent_set_unlocked(guild_id)
            new_codes = []
            for code in codes or []:
                normalized = self._normalize_code(code)
                if normalized and normalized not in sent_set:
                    new_codes.append(normalized)
                    sent_set.add(normalized)

            if not new_codes:
                return []

            self.state.setdefault('sent', {})[str(guild_id)] = list(sorted(sent_set))

            if mongo_enabled() and SentGiftCodesAdapter is not None:
                try:
                    SentGiftCodesAdapter.mark_codes_sent(
                        guild_id=guild_id,
                        codes=new_codes,
                        source='auto_reserve'
                    )
                except Exception as e:
                    logger.error(f"❌ MongoDB: Exception reserving codes for guild {guild_id}: {e}")

            try:
                import os
                os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
                with open(STATE_FILE, 'w', encoding='utf-8') as f:
                    import json
                    json.dump(self.state, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.error(f"❌ Local File: Failed to reserve codes for guild {guild_id}: {e}")

            if mongo_enabled() and GiftcodeStateAdapter is not None:
                try:
                    GiftcodeStateAdapter.set_state(self.state)
                except Exception as e:
                    logger.debug(f"GiftcodeStateAdapter reserve save failed: {e}")

            if mongo_enabled() and GiftCodesAdapter is not None:
                try:
                    now = datetime.utcnow().isoformat()
                    for code in new_codes:
                        try:
                            GiftCodesAdapter.insert(code, now, validation_status='posted')
                        except Exception:
                            logger.debug(f"Failed to insert reserved code into GiftCodesAdapter: {code}")
                except Exception:
                    pass

            return new_codes

    async def mark_sent(self, guild_id: int, codes: List[str]):
        async with self.lock:
            sent_list = self.state.setdefault('sent', {}).setdefault(str(guild_id), [])
            sent = set(self._normalize_code(c) for c in (sent_list or []))
            
            new_codes = []
            for c in (codes or []):
                if c:
                    normalized = self._normalize_code(c)
                    if normalized not in sent:
                        new_codes.append(normalized)
                        sent.add(normalized)
            
            # Only update if there are new codes
            if not new_codes:
                logger.debug(f"No new codes to mark for guild {guild_id}")
                return
            
            # store back
            # keep deterministic order
            self.state['sent'][str(guild_id)] = list(sorted(sent))
            
            # === PRIMARY STORAGE: MongoDB (Preferred) ===
            mongo_success = False
            if mongo_enabled() and SentGiftCodesAdapter is not None:
                try:
                    # Use the dedicated SentGiftCodesAdapter for robust tracking
                    mongo_success = SentGiftCodesAdapter.mark_codes_sent(
                        guild_id=guild_id,
                        codes=new_codes,
                        source='auto'
                    )
                    if mongo_success:
                        logger.info(f"✅ MongoDB: Marked {len(new_codes)} new codes as sent for guild {guild_id}")
                    else:
                        logger.warning(f"⚠️ MongoDB: Failed to mark codes (returned False) for guild {guild_id}")
                except Exception as e:
                    logger.error(f"❌ MongoDB: Exception marking codes for guild {guild_id}: {e}")
                    mongo_success = False
            
            # === FALLBACK STORAGE: Local JSON File ===
            file_success = False
            try:
                import os
                os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
                with open(STATE_FILE, 'w', encoding='utf-8') as f:
                    import json
                    json.dump(self.state, f, ensure_ascii=False, indent=2)
                file_success = True
                logger.info(f"✅ Local File: Saved state for guild {guild_id}")
            except Exception as e:
                logger.error(f"❌ Local File: Failed to save state for guild {guild_id}: {e}")
            
            # === BACKUP: Legacy GiftcodeStateAdapter ===
            state_adapter_success = False
            if mongo_enabled() and GiftcodeStateAdapter is not None:
                try:
                    state_adapter_success = GiftcodeStateAdapter.set_state(self.state)
                    if state_adapter_success:
                        logger.debug(f"✅ GiftcodeStateAdapter: Saved state for guild {guild_id}")
                except Exception as e:
                    logger.debug(f"GiftcodeStateAdapter save failed: {e}")
            
            # === Log Persistence Results ===
            if mongo_success:
                logger.info(f"🎯 SUCCESS: Codes persisted to MongoDB for guild {guild_id}")
            elif file_success or state_adapter_success:
                logger.warning(f"⚠️ PARTIAL: Codes saved to fallback storage only for guild {guild_id}")
            else:
                logger.error(f"❌ CRITICAL: All persistence methods failed for guild {guild_id}")
                
        # Also persist each new code into the Mongo `gift_codes` collection
        try:
            if mongo_enabled() and GiftCodesAdapter is not None:
                from datetime import datetime as _dt
                now = _dt.utcnow().isoformat()
                for c in (new_codes or []):
                    if not c:
                        continue
                    try:
                        GiftCodesAdapter.insert(self._normalize_code(c), now, validation_status='posted')
                    except Exception:
                        # Non-fatal: continue on insert errors
                        logger.debug(f"Failed to insert code into GiftCodesAdapter: {c}")
        except Exception:
            pass

    async def get_sent_set(self, guild_id: int):
        async with self.lock:
            return self._get_sent_set_unlocked(guild_id)


poster = GiftCodePoster()



class RedeemModal(discord.ui.Modal, title="Redeem Gift Code"):
    player_id = discord.ui.TextInput(
        label="Player ID (FID)",
        placeholder="Enter your numeric Player ID...",
        min_length=1,
        max_length=15,
        required=True
    )
    state_id = discord.ui.TextInput(
        label="State Number",
        placeholder="Optional if state is known",
        max_length=10,
        required=False
    )

    def __init__(self, code_str):
        super().__init__()
        self.code_str = code_str

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        fid = self.player_id.value.strip()
        
        if not fid.isdigit():
            await interaction.followup.send("❌ Invalid Player ID. Please enter numbers only.", ephemeral=True)
            return

        # Get GiftOperations cog
        cog = interaction.client.get_cog('GiftOperations')
        if not cog:
            await interaction.followup.send("❌ GiftOperations cog not available.", ephemeral=True)
            return

        try:
            # Call the redemption function
            state_val = self.state_id.value.strip() if self.state_id.value else "0"
            status = await cog.claim_giftcode_rewards_wos(fid, self.code_str, state_val)
            
            # Map status to friendly message and color
            status_map = {
                "SUCCESS": ("✅ Redemption Successful!", 0x00FF00), # Green
                "RECEIVED": ("✅ Already Received!", 0x00FF00),
                "SAME TYPE EXCHANGE": ("⚠️ Already Claimed Type", 0xFFA500), # Orange
                "TIME OUT": ("❌ Request Timed Out", 0xFF0000), # Red
                "CDK NOT FOUND": ("❌ Code Not Found", 0xFF0000),
                "CDK EXPIRED": ("❌ Code Expired", 0xFF0000),
                "PLAYER NOT FOUND": ("❌ Player Not Found", 0xFF0000),
                "ERR_CDK_CLAIM_LIMIT": ("❌ Claim Limit Reached", 0xFF0000),
            }
            
            # Default fallback
            title, color = status_map.get(status, (f"Redemption Status: {status}", 0x808080))
            
            description = f"**Code:** `{self.code_str}`\n**Player ID:** `{fid}`"
            
            # Add helpful context for specific errors
            if status == "SAME TYPE EXCHANGE":
                description += "\n\n*You have already claimed a gift code of this type (e.g. weekly code).* "
            elif status == "PLAYER NOT FOUND":
                description += "\n\n*Please check if the Player ID is correct.*"

            embed = discord.Embed(
                title=title,
                description=description,
                color=color
            )
            embed.set_footer(text="Magnus", icon_url="https://cdn.discordapp.com/attachments/1435569370389807144/1436745053442805830/unnamed_5.png?ex=69291c5a&is=6927cada&hm=bc41859d9908f2178273050b2945ce63d0a32ec55f1edda3d41aea0970b4030e")
            
            await interaction.followup.send(embed=embed, ephemeral=True)
                
        except Exception as e:
            logger.error(f"Error redeeming code via modal: {e}")
            await interaction.followup.send(f"❌ An error occurred: {str(e)}", ephemeral=True)

class GiftCodeView(discord.ui.View):
    def __init__(self, codes_list):
        super().__init__(timeout=None)
        self.codes = codes_list or []
        self.message = None

    @discord.ui.button(label="Copy Code", style=discord.ButtonStyle.primary, custom_id="giftcode_copy")
    async def copy_button(self, interaction_button: discord.Interaction, button: discord.ui.Button):
        # Send all active gift codes in a simple plain-text DM (one code per line).
        # If DMs are closed, fall back to an ephemeral message with the same plain text.
        
        # If codes are empty (e.g. after restart), try to fetch them
        if not self.codes:
            try:
                self.codes = await get_active_gift_codes()
            except Exception:
                pass

        if not self.codes:
            try:
                await interaction_button.response.send_message("No gift codes available to copy.", ephemeral=True)
            except Exception:
                logger.debug("Failed to send ephemeral no-codes message")
            return

        # Build a simple plain-text list of codes (only the code strings)
        code_list = [c.get('code', '').strip() for c in self.codes if c.get('code')]
        if not code_list:
            try:
                await interaction_button.response.send_message("Couldn't find any codes to copy.", ephemeral=True)
            except Exception:
                logger.debug("Failed to send ephemeral no-code-found message")
            return

        plain_text = "\n".join(code_list)
        # Append the signature line with the server name
        server_name = interaction_button.guild.name if interaction_button.guild else "Unknown Server"
        plain_text += f"\n\nGift Code :gift:  {server_name}"

        try:
            await interaction_button.response.defer(ephemeral=True)
        except Exception:
            pass

        user = interaction_button.user
        dm_sent = False
        try:
            await user.send(plain_text)
            dm_sent = True
        except Exception as dm_err:
            logger.info(f"Could not send DM to user {getattr(user, 'id', 'unknown')}: {dm_err}")

        try:
            if dm_sent:
                await interaction_button.followup.send("I've sent all active gift codes to your DMs. Check your messages!", ephemeral=True)
            else:
                await interaction_button.followup.send(f"Couldn't DM you. Here are the codes:\n\n{plain_text}", ephemeral=True)
        except Exception:
            logger.debug("Failed to send followup after DM attempt")

    @discord.ui.button(label="Redeem", style=discord.ButtonStyle.success, custom_id="giftcode_redeem_manual")
    async def redeem_button(self, interaction_button: discord.Interaction, button: discord.ui.Button):
        """Open a modal to redeem the latest code manually."""
        # Determine the latest code
        if not self.codes:
            # Try to fetch if empty (though usually passed in)
            # Use wait_for to ensure we don't block the interaction for too long
            try:
                # We give it 2.5 seconds max (Discord interaction token lasts 3s if not deferred, 
                # but we prefer to fail fast and defer if needed, though here we want to show modal immediately if possible)
                # Actually, modals cannot be sent after defer(). So we MUST NOT defer if we want to send a modal.
                # But we also cannot take > 3 seconds.
                
                self.codes = await asyncio.wait_for(get_active_gift_codes(), timeout=2.0)
            except asyncio.TimeoutError:
                # If fetching takes too long, we can't show the modal safely without risking "Unknown interaction"
                # So we inform the user and trigger a background fetch to populate cache for next time.
                asyncio.create_task(get_active_gift_codes())
                await interaction_button.response.send_message(
                    "🎁 Codes are being refreshed... Please click **Redeem** again in a few seconds!", 
                    ephemeral=True
                )
                return
            except Exception:
                pass
        
        if not self.codes:
            await interaction_button.response.send_message("No active gift codes to redeem.", ephemeral=True)
            return

        # Use the first code (latest)
        latest_code = self.codes[0]
        code_str = latest_code.get('code') if isinstance(latest_code, dict) else str(latest_code)
        
        if not code_str:
            await interaction_button.response.send_message("Could not determine the gift code.", ephemeral=True)
            return

        # Open Modal
        await interaction_button.response.send_modal(RedeemModal(code_str))

    @discord.ui.button(label="Refresh Codes", style=discord.ButtonStyle.secondary, custom_id="giftcode_refresh")
    async def refresh_button(self, interaction_button: discord.Interaction, button: discord.ui.Button):
        await interaction_button.response.defer(ephemeral=True)
        try:
            new_codes_fresh = await get_active_gift_codes()
            if not new_codes_fresh:
                await interaction_button.followup.send("No active gift codes available right now.", ephemeral=True)
                return

            self.codes = new_codes_fresh
            # Rebuild embed
            new_embed = discord.Embed(
                title="✨ Active Whiteout Survival Gift Codes ✨",
                color=0xffd700,
                description=f"Last updated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}"
            )
            new_embed.set_thumbnail(url="https://i.postimg.cc/s2xHV7N7/Groovy-gift.gif")

            for code in (self.codes or [])[:10]:
                name = f"🎟️ Code:"
                value = f"```{code.get('code','')}```\n*Rewards:* {code.get('rewards','Rewards not specified')}\n*Expires:* {code.get('expiry','Unknown')}"
                new_embed.add_field(name=name, value=value, inline=False)

            if self.codes and len(self.codes) > 10:
                new_embed.set_footer(text=f"And {len(self.codes) - 10} more codes...")
            else:
                new_embed.set_footer(text="Use /giftcode to see all active codes!")

            # Edit the message containing the embed
            # Use interaction_button.message which is reliable even if self.message is lost after restart
            if interaction_button.message:
                try:
                    await interaction_button.message.edit(embed=new_embed)
                    await interaction_button.followup.send("Gift codes refreshed.", ephemeral=True)
                except Exception as edit_err:
                    logger.error(f"Failed to edit gift code message: {edit_err}")
                    await interaction_button.followup.send("Failed to update the gift codes message.", ephemeral=True)
            else:
                await interaction_button.followup.send(embed=new_embed, ephemeral=False)

        except Exception as e:
            logger.error(f"Error refreshing gift codes via button: {e}")
            await interaction_button.followup.send("Error while refreshing gift codes.", ephemeral=True)




async def post_new_codes_to_channel(bot: discord.Client, channel: discord.TextChannel, new_codes: List[Dict]):
    """Post new codes using the same embed style as /giftcode. Expects list of code dicts."""
    if not new_codes:
        return

    try:
        embed = discord.Embed(
            title="✨ New Whiteout Survival Gift Codes ✨",
            color=0xffd700,
            description=f"Last updated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )
        embed.set_thumbnail(url="https://i.postimg.cc/s2xHV7N7/Groovy-gift.gif")

        for code in new_codes[:10]:
            code_str = code.get('code', '')
            value = f"```{code_str}```\n*Rewards:* {code.get('rewards','Rewards not specified')}\n*Expires:* {code.get('expiry','Unknown')}"
            embed.add_field(name="🎟️ Code:", value=value, inline=False)

        if len(new_codes) > 10:
            embed.set_footer(text=f"And {len(new_codes) - 10} more codes...")
        else:
            embed.set_footer(text="Use /giftcode to see all active codes!")

        view = GiftCodeView(new_codes)
        sent = await channel.send(embed=embed, view=view)
        # Attach message reference to the view so Refresh can edit
        try:
            view.message = sent
        except Exception:
            logger.debug("Could not attach message reference to GiftCodeView")

        logger.info(f"Posted {len(new_codes)} new gift codes to {getattr(channel.guild,'name',None)} ({channel.id})")
    except Exception as e:
        logger.error(f"Failed to post gift codes to channel {getattr(channel,'id',None)}: {e}")


def _coerce_codes_for_posting(raw_codes) -> List[Dict]:
    """Normalize fetched/detected code payloads into poster embed dictionaries."""
    normalized = []
    for item in raw_codes or []:
        if isinstance(item, dict):
            code = item.get('code') or item.get('giftcode') or item.get('_id')
            expiry = item.get('expiry') or item.get('date') or item.get('expires') or 'Unknown'
            rewards = item.get('rewards') or item.get('reward') or item.get('description') or 'Rewards not specified'
            source = item.get('source') or 'active_fetch'
        elif isinstance(item, (tuple, list)) and item:
            code = item[0]
            expiry = item[1] if len(item) > 1 else 'Unknown'
            rewards = 'Rewards not specified'
            source = 'auto_redeem_detection'
        else:
            code = str(item).strip() if item else ''
            expiry = 'Unknown'
            rewards = 'Rewards not specified'
            source = 'active_fetch'

        code = str(code).strip() if code else ''
        if not code:
            continue
        normalized.append({
            'code': code,
            'rewards': str(rewards).strip() if rewards else 'Rewards not specified',
            'expiry': str(expiry).strip() if expiry else 'Unknown',
            'is_active': True,
            'source': source,
        })
    return normalized


async def _fetch_codes_for_posting(bot: discord.Client) -> List[Dict]:
    """Use the same consolidated source as ManageGiftCode/auto-redeem when available."""
    manage_cog = bot.get_cog('ManageGiftCode') if bot and hasattr(bot, 'get_cog') else None
    if manage_cog and hasattr(manage_cog, 'get_active_gift_codes_consolidated'):
        try:
            active_map = await manage_cog.get_active_gift_codes_consolidated(force_refresh=True)
            if active_map:
                logger.info(f"Fetched {len(active_map)} gift codes from ManageGiftCode consolidated source")
                return _coerce_codes_for_posting(list(active_map.items()))
        except Exception as e:
            logger.warning(f"ManageGiftCode consolidated fetch failed, falling back to poster fetch: {e}")

    return _coerce_codes_for_posting(await get_active_gift_codes())


async def run_check_once(bot: discord.Client, detected_codes: Optional[List] = None):
    """Fetch active codes and post new ones to configured channels. Returns summary dict."""
    try:
        fetched = _coerce_codes_for_posting(detected_codes)
        if fetched:
            logger.info(f"Using {len(fetched)} detected gift code(s) supplied by auto-redeem detection")
        else:
            # Try to fetch codes with retry logic
            MAX_FETCH_RETRIES = 3

            for attempt in range(MAX_FETCH_RETRIES):
                try:
                    fetched = await _fetch_codes_for_posting(bot)
                    if fetched:
                        logger.info(f"Successfully fetched {len(fetched)} gift codes (attempt {attempt + 1})")
                        break
                    else:
                        logger.warning(f"No codes returned from fetch (attempt {attempt + 1})")
                        if attempt < MAX_FETCH_RETRIES - 1:
                            await asyncio.sleep(5 * (attempt + 1))  # Exponential backoff
                except Exception as e:
                    logger.error(f"Error fetching codes (attempt {attempt + 1}/{MAX_FETCH_RETRIES}): {e}")
                    if attempt < MAX_FETCH_RETRIES - 1:
                        await asyncio.sleep(5 * (attempt + 1))  # Exponential backoff
        
        if not fetched:
            logger.warning("Failed to fetch any codes after retries")
            return {"posted": 0, "errors": 1}

        # Build mapping of normalized code -> full dict for richer embeds
        code_map = {poster._normalize_code(c.get('code','')): c for c in fetched if c.get('code')}
        fetched_codes = list(code_map.keys())
        fetched_set = set(fetched_codes)
        
        logger.info(f"Fetched codes: {fetched_codes}")

        posted_total = 0
        errors = 0

        channels = poster.list_channels()
        if not channels:
            logger.info("No channels configured for gift code posting")
            return {"posted": 0, "errors": 0}
            
        initialized = bool(poster.state.get('initialized'))
        logger.info(f"Processing {len(channels)} configured channels (initialized={initialized})")
        
        for guild_id, channel_id in channels.items():
            try:
                guild = bot.get_guild(guild_id)
                if not guild:
                    logger.debug(f"Bot not in guild {guild_id}")
                    continue
                channel = guild.get_channel(channel_id) or bot.get_channel(channel_id)
                if not channel:
                    logger.warning(f"Configured gift channel {channel_id} not found for guild {guild_id}")
                    continue

                new_code_keys = await poster.reserve_unsent_codes(guild_id, fetched_codes)
                if not new_code_keys:
                    logger.info(f"No new codes for guild {guild_id}")
                    continue

                logger.info(f"Reserved {len(new_code_keys)} new codes for guild {guild_id}: {new_code_keys}")

                # Prepare list of dicts for embed (use original casing from fetched map)
                new_code_dicts = [code_map[k] for k in new_code_keys if k in code_map]

                # Post new codes in one message (embed)
                await post_new_codes_to_channel(bot, channel, new_code_dicts)
                posted_total += len(new_code_keys)
                logger.info(f"Posted {len(new_code_keys)} new codes to guild {guild_id}")

            except Exception as e:
                logger.error(f"Error processing guild {guild_id}: {e}", exc_info=True)
                errors += 1

        # If this was the first run, persist initialized flag so subsequent runs behave normally
        try:
            if not poster.state.get('initialized'):
                poster.state['initialized'] = True
                await poster._save_state()
                logger.info("Marked poster as initialized")
        except Exception as e:
            logger.error(f"Error saving initialized state: {e}")

        logger.info(f"Check complete: posted={posted_total}, errors={errors}")
        return {"posted": posted_total, "errors": errors}
    except Exception as e:
        logger.error(f"Giftcode poster check failed: {e}", exc_info=True)
        return {"posted": 0, "errors": 1}



async def start_poster(bot: discord.Client, interval: int = DEFAULT_INTERVAL):
    """Background loop that periodically checks for new gift codes and posts them."""
    logger.info(f"Starting giftcode poster with interval={interval}s")
    consecutive_errors = 0
    max_consecutive_errors = 5
    
    while True:
        try:
            result = await run_check_once(bot)
            
            # Check if the check was successful
            if result.get('errors', 0) > 0:
                consecutive_errors += 1
                logger.warning(f"Check had errors. Consecutive errors: {consecutive_errors}/{max_consecutive_errors}")
                
                if consecutive_errors >= max_consecutive_errors:
                    logger.error(f"Too many consecutive errors ({consecutive_errors}). Increasing check interval temporarily.")
                    # Temporarily increase interval to avoid hammering on errors
                    await asyncio.sleep(interval * 3)
                    consecutive_errors = 0  # Reset after longer wait
                    continue
            else:
                # Reset consecutive errors on success
                if consecutive_errors > 0:
                    logger.info("Check succeeded, resetting error counter")
                consecutive_errors = 0
                
        except Exception as e:
            consecutive_errors += 1
            logger.error(f"Unhandled error in giftcode poster loop: {e}", exc_info=True)
            
            if consecutive_errors >= max_consecutive_errors:
                logger.critical(f"Critical: Too many consecutive errors ({consecutive_errors}). Bot may need attention.")
                # Wait longer on critical errors
                await asyncio.sleep(interval * 5)
                consecutive_errors = 0
                continue
        
        # Health check log every hour (3600 / interval checks)
        try:
            if hasattr(start_poster, '_check_count'):
                start_poster._check_count += 1
            else:
                start_poster._check_count = 1
                
            # Log health status every ~100 checks or 1000 seconds (whichever comes first)
            if start_poster._check_count % max(1, min(100, 1000 // interval)) == 0:
                logger.info(f"📊 Health check: Gift code poster healthy. Checks completed: {start_poster._check_count}, Consecutive errors: {consecutive_errors}")
        except Exception:
            pass
            
        await asyncio.sleep(interval)


async def run_now_and_report(bot: discord.Client, detected_codes: Optional[List] = None):
    return await run_check_once(bot, detected_codes)
