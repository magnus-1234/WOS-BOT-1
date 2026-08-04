import discord
import logging
import sqlite3
import os
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

TOPGG_REVIEW_URL = "https://top.gg/bot/1399025185046134866?s=0e3c921b2b5f8"


def _approval_dm_message(guild_name: str, alliance_name: str) -> str:
    return (
        f"✅ **Your registration has been approved!**\n\n"
        f"**Server:** {guild_name}\n"
        f"**Alliance:** `{alliance_name}`\n\n"
        f"Your access code is now active. Visit the dashboard and use "
        f"`/manage` or click **Alliance Monitor / Gift Codes** — then enter "
        f"the code you set during registration.\n\n"
        f"If Whiteout Survival Bot helps your server, please share a quick "
        f"review or feedback on Top.gg:\n{TOPGG_REVIEW_URL}"
    )


async def setup_approved_guild_channels(bot: discord.Client, guild_id: int, alliance_name: str, 
                                        submitter_id: int, admin_id: int, state_id: str = None) -> tuple[bool, str]:
    """
    Sets up auto-channels and SQLite settings for an already approved guild.
    This is idempotent and safe to run multiple times.
    """
    try:
        from db.mongo_adapters import mongo_enabled
        guild = bot.get_guild(guild_id)
        if not guild:
            return False, f"Bot is not in guild {guild_id}"

        # auto-redeem logs channel (formerly player-ids)
        logs_channel = discord.utils.get(guild.text_channels, name="🆔┃𝐩𝐥𝐚𝐲𝐞𝐫-𝐢𝐝𝐬")
        if not logs_channel:
            logs_channel = await guild.create_text_channel("🆔┃𝐩𝐥𝐚𝐲𝐞𝐫-𝐢𝐝𝐬")
            await logs_channel.send(
                "This channel will log auto-redeem activities and user registrations."
            )
            
            try:
                # Post the persistent panel in the same channel
                from cogs.manage_giftcode import AutoRedeemPanelView
                embed = discord.Embed(
                    title="🎁 Auto-Redeem Registration",
                    description=(
                        "Click the button below to enroll in automatic gift code redemption!\n\n"
                        "**What you need:**\n"
                        "• Your **Player ID** (FID)\n"
                        "• Your **State Number**\n\n"
                        "Once registered, any new gift codes posted by the bot will be automatically "
                        "redeemed directly to your in-game mailbox."
                    ),
                    color=0x5865F2
                )
                embed.set_thumbnail(url=bot.user.display_avatar.url if bot.user.display_avatar else None)
                manage_gc_cog = bot.get_cog("ManageGiftCode")
                if manage_gc_cog:
                    await logs_channel.send(embed=embed, view=AutoRedeemPanelView(manage_gc_cog))
            except Exception as e:
                logger.error(f"Error posting auto-redeem panel: {e}")
            
        # giftcode channel
        giftcode_channel = discord.utils.get(guild.text_channels, name="🎁┃𝐠𝐢𝐟𝐭𝐜𝐨𝐝𝐞")
        if not giftcode_channel:
            giftcode_channel = await guild.create_text_channel("🎁┃𝐠𝐢𝐟𝐭𝐜𝐨𝐝𝐞")
            
        # welcome channel
        welcome_channel = discord.utils.get(guild.text_channels, name="🎉┃welcome")
        if not welcome_channel:
            welcome_channel = await guild.create_text_channel("🎉┃welcome")
        
        # 1. Player IDs config
        try:
            alliance_id = 0
            try:
                with sqlite3.connect('db/alliance.sqlite', timeout=10) as alliance_db:
                    ac_cursor = alliance_db.cursor()
                    ac_cursor.execute("SELECT alliance_id FROM alliance_list WHERE name = ?", (alliance_name,))
                    row = ac_cursor.fetchone()
                    if row:
                        alliance_id = row[0]
                    else:
                        ac_cursor.execute("INSERT INTO alliance_list (name, discord_server_id) VALUES (?, ?)", (alliance_name, guild_id))
                        alliance_id = ac_cursor.lastrowid
                        alliance_db.commit()
                        try:
                            from db.mongo_adapters import AlliancesAdapter
                            if mongo_enabled() and hasattr(AlliancesAdapter, 'upsert_async'):
                                await AlliancesAdapter.upsert_async(alliance_id, alliance_name, guild_id)
                            elif mongo_enabled() and hasattr(AlliancesAdapter, 'upsert'):
                                AlliancesAdapter.upsert(alliance_id, alliance_name, guild_id)
                        except Exception as mongo_err:
                            logger.error(f"Error saving new alliance to mongo: {mongo_err}")
            except Exception as e:
                logger.error(f"Error finding/creating alliance_id for {alliance_name}: {e}")
                
            if alliance_id:
                try:
                    from db.mongo_adapters import ServerAllianceAdapter
                    if mongo_enabled():
                        if hasattr(ServerAllianceAdapter, 'set_alliance_async'):
                            await ServerAllianceAdapter.set_alliance_async(guild_id, alliance_id, submitter_id)
                        else:
                            ServerAllianceAdapter.set_alliance(guild_id, alliance_id, submitter_id)
                except Exception as e:
                    logger.error(f"Failed to set server alliance for {guild_id}: {e}")

                try:
                    with sqlite3.connect('db/settings.sqlite', timeout=10) as sdb:
                        sc = sdb.cursor()
                        sc.execute("INSERT OR IGNORE INTO admin (id, is_initial) VALUES (?, 0)", (submitter_id,))
                        sc.execute("INSERT OR IGNORE INTO adminserver (admin, alliances_id) VALUES (?, ?)", (submitter_id, alliance_id))
                        sdb.commit()
                except Exception as e:
                    logger.error(f"Failed to update sqlite adminserver for {submitter_id}: {e}")

            with sqlite3.connect('db/id_channel.sqlite', timeout=10) as db:
                cursor = db.cursor()
                cursor.execute('''CREATE TABLE IF NOT EXISTS id_channels
                             (guild_id INTEGER, alliance_id INTEGER, channel_id INTEGER, created_at TEXT, created_by INTEGER, UNIQUE(guild_id, channel_id))''')
                cursor.execute("INSERT OR REPLACE INTO id_channels (guild_id, alliance_id, channel_id, created_at, created_by) VALUES (?, ?, ?, ?, ?)",
                             (guild_id, alliance_id, logs_channel.id, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), admin_id))
                db.commit()
                
            try:
                from db.mongo_adapters import IDChannelsAdapter
                if mongo_enabled() and hasattr(IDChannelsAdapter, 'set_channel_async'):
                    await IDChannelsAdapter.set_channel_async(guild_id, logs_channel.id, alliance_id)
            except Exception:
                pass
        except Exception as e:
            logger.error(f"Failed to setup logs channel: {e}")

        # 2. Giftcode Channel config
        try:
            try:
                from db.mongo_adapters import AutoRedeemChannelsAdapter, AutoRedeemSettingsAdapter
                if mongo_enabled():
                    if hasattr(AutoRedeemChannelsAdapter, 'set_channel_async'):
                        await AutoRedeemChannelsAdapter.set_channel_async(guild_id, giftcode_channel.id, 0)
                    elif hasattr(AutoRedeemChannelsAdapter, 'set_channel'):
                        AutoRedeemChannelsAdapter.set_channel(guild_id, giftcode_channel.id, 0)
                        
                    if hasattr(AutoRedeemSettingsAdapter, 'update_settings_async'):
                        settings = {'enabled': True}
                    if state_id:
                        settings['default_state'] = state_id
                    await AutoRedeemSettingsAdapter.update_settings_async(guild_id, settings)
            except Exception:
                pass
            
            with sqlite3.connect('db/giftcode.sqlite', timeout=10) as gcdb:
                g_cursor = gcdb.cursor()
                g_cursor.execute("CREATE TABLE IF NOT EXISTS auto_redeem_channels (guild_id INTEGER, channel_id INTEGER, role_id INTEGER, PRIMARY KEY (guild_id))")
                g_cursor.execute("INSERT OR REPLACE INTO auto_redeem_channels (guild_id, channel_id, role_id) VALUES (?, ?, ?)", (guild_id, giftcode_channel.id, 0))
                
                g_cursor.execute("CREATE TABLE IF NOT EXISTS auto_redeem_settings (guild_id INTEGER PRIMARY KEY, enabled INTEGER DEFAULT 0, priority INTEGER DEFAULT 999, updated_by INTEGER, updated_at TEXT)")
                try:
                    g_cursor.execute("ALTER TABLE auto_redeem_settings ADD COLUMN default_state TEXT")
                except Exception:
                    pass
                g_cursor.execute("""
                    INSERT INTO auto_redeem_settings (guild_id, enabled, updated_by, updated_at, default_state)
                    VALUES (?, 1, ?, ?, ?)
                    ON CONFLICT(guild_id) DO UPDATE SET
                        enabled = 1,
                        updated_by = excluded.updated_by,
                        updated_at = excluded.updated_at,
                        default_state = COALESCE(excluded.default_state, default_state)
                """, (guild_id, admin_id, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), state_id or '0'))
                gcdb.commit()
        except Exception as e:
            logger.error(f"Failed to setup giftcode channel: {e}")
            
        # 3. Welcome Channel config
        try:
            try:
                from db.mongo_adapters import WelcomeChannelAdapter
                if mongo_enabled():
                    if hasattr(WelcomeChannelAdapter, 'set_async'):
                        await WelcomeChannelAdapter.set_async(guild_id, welcome_channel.id, enabled=True)
                    elif hasattr(WelcomeChannelAdapter, 'set'):
                        WelcomeChannelAdapter.set(guild_id, welcome_channel.id, enabled=True)
            except Exception:
                pass
        except Exception as e:
            logger.error(f"Failed to setup welcome channel: {e}")
            
        # 4. Reminder Channel config
        try:
            reminder_channel = discord.utils.get(guild.text_channels, name="⏰〢reminder")
            if not reminder_channel:
                reminder_channel = await guild.create_text_channel("⏰〢reminder")
            
            now_utc = datetime.utcnow()
            reminder_time = now_utc.replace(hour=23, minute=55, second=0, microsecond=0)
            if reminder_time <= now_utc:
                reminder_time += timedelta(days=1)
                
            reminder_data = {
                'user_id': str(admin_id),
                'channel_id': str(reminder_channel.id),
                'guild_id': str(guild_id),
                'message': 'ARENA ⚔️',
                'body': None,
                'reminder_time': reminder_time.isoformat(),
                'created_at': now_utc.isoformat(),
                'is_active': True,
                'is_sent': False,
                'is_recurring': True,
                'recurrence_type': 'daily',
                'recurrence_interval': 1,
                'original_time_pattern': 'daily at 23:55',
                'mention': 'everyone',
                'image_url': None,
                'thumbnail_url': 'https://files.catbox.moe/uok71x.png',
                'footer_text': None,
                'footer_icon_url': None,
                'author_url': None
            }
            
            try:
                from db.mongo_adapters import RemindersAdapter, mongo_enabled
                if mongo_enabled() and hasattr(RemindersAdapter, 'add_reminder_async'):
                    await RemindersAdapter.add_reminder_async(reminder_data)
            except Exception as e:
                logger.error(f"Failed to insert reminder in mongo: {e}")
                
            try:
                with sqlite3.connect('reminders.db', timeout=10) as r_db:
                    r_cursor = r_db.cursor()
                    r_cursor.execute('''
                        INSERT INTO reminders (user_id, channel_id, guild_id, message, body, reminder_time, created_at,
                                             is_recurring, recurrence_type, recurrence_interval, original_time_pattern, mention, thumbnail_url)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        str(admin_id), str(reminder_channel.id), str(guild_id), 'ARENA ⚔️', None, reminder_time.isoformat(),
                        now_utc.isoformat(), 1, 'daily', 1, 'daily at 23:55', 'everyone',
                        'https://files.catbox.moe/uok71x.png'
                    ))
                    r_db.commit()
            except Exception as e:
                logger.error(f"Failed to insert reminder in sqlite: {e}")
                
        except Exception as e:
            logger.error(f"Failed to setup reminder channel: {e}")
            
        return True, "Channels and settings successfully configured."
    except Exception as e:
        logger.error(f"Error creating auto-channels for {guild_id}: {e}")
        return False, f"Error: {e}"


async def process_registration_approval(bot: discord.Client, guild_id: int, guild_name: str,
                                        alliance_name: str, submitter_id: int, admin_id: int, state_id: str = None) -> tuple[bool, str]:
    """
    Approve a pending registration and create all necessary auto-channels.
    Returns a tuple of (success, message).
    """
    try:
        from db.mongo_adapters import PendingConfigAdapter, mongo_enabled
        if not mongo_enabled():
            return False, "❌ Database not available."
            
        ok = await PendingConfigAdapter.approve_async(guild_id, admin_id)
        if ok:
            # Setup channels now that approval succeeded
            await setup_approved_guild_channels(bot, guild_id, alliance_name, submitter_id, admin_id, state_id)

            # Notify submitter
            try:
                user = await bot.fetch_user(submitter_id)
                await user.send(_approval_dm_message(guild_name, alliance_name))
            except Exception as dm_err:
                logger.warning(f"Could not DM submitter {submitter_id}: {dm_err}")
                
            return True, (
                f"✅ **Approved!** Registration for **{guild_name}** is now active.\n"
                f"Alliance `{alliance_name}` has been configured."
            )
        else:
            return False, (
                f"⚠️ Could not find a pending request for guild `{guild_id}`. "
                f"It may have already been processed."
            )
    except Exception as e:
        logger.error(f"Error approving registration for guild {guild_id}: {e}")
        return False, f"❌ Error: {e}"



async def process_registration_denial(bot: discord.Client, guild_id: int, guild_name: str,
                                      submitter_id: int, admin_id: int, state_id: str = None) -> tuple[bool, str]:
    """
    Deny a pending registration and notify the submitter.
    Returns a tuple of (success, message).
    """
    try:
        from db.mongo_adapters import PendingConfigAdapter, mongo_enabled
        if not mongo_enabled():
            return False, "❌ Database not available."
            
        ok = await PendingConfigAdapter.deny_async(guild_id, admin_id)
        if ok:
            # Notify submitter
            try:
                user = await bot.fetch_user(submitter_id)
                await user.send(
                    f"❌ **Your registration request was denied.**\n\n"
                    f"**Server:** {guild_name}\n\n"
                    f"Please contact the bot administrator for more details.\n"
                    f"You may submit a new registration request when ready."
                )
            except Exception as dm_err:
                logger.warning(f"Could not DM submitter {submitter_id}: {dm_err}")
                
            return True, f"✅ **Denied!** Registration for **{guild_name}** has been denied."
        else:
            return False, (
                f"⚠️ Could not find a pending request for guild `{guild_id}`. "
                f"It may have already been processed."
            )
    except Exception as e:
        logger.error(f"Error denying registration for guild {guild_id}: {e}")
        return False, f"❌ Error: {e}"
