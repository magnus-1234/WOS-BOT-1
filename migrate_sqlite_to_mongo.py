import asyncio
import sqlite3
import logging
from datetime import datetime
import sys
import os
from dotenv import load_dotenv

load_dotenv()

# Ensure the root dir is in path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from db.mongo_adapters import (
    GiftCodesAdapter,
    AutoRedeemMembersAdapter,
    AutoRedeemCompletedGuildsAdapter,
    _get_db_main_async,
    _get_db_main
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def migrate_data():
    if not os.path.exists("db/giftcode.sqlite"):
        logger.error("db/giftcode.sqlite not found!")
        return
        
    conn = sqlite3.connect("db/giftcode.sqlite")
    cursor = conn.cursor()
    
    # 1. Ensure new adapter indexes
    await AutoRedeemCompletedGuildsAdapter.ensure_indexes_async()
    logger.info("Indexes ensured.")

    # 2. Migrate Gift Codes
    try:
        cursor.execute("SELECT giftcode, date, validation_status, added_by, added_at, auto_redeem_processed FROM gift_codes")
        gift_codes = cursor.fetchall()
        logger.info(f"Found {len(gift_codes)} gift codes in SQLite to migrate.")
        
        migrated_codes = 0
        for row in gift_codes:
            code, date_str, status, added_by, added_at, processed = row
            success = GiftCodesAdapter.add_or_update_code(
                code=code,
                date=date_str,
                validation_status=status,
                added_by=added_by,
                added_at=added_at
            )
            # update auto_redeem_processed manually since add_or_update_code doesn't do it directly
            if success and processed == 1:
                GiftCodesAdapter.mark_auto_redeem_processed(code)
            
            if success:
                migrated_codes += 1
        logger.info(f"Successfully migrated {migrated_codes} gift codes.")
    except Exception as e:
        logger.error(f"Error migrating gift codes: {e}")

    # 3. Migrate Auto Redeem Members
    try:
        cursor.execute("SELECT guild_id, fid, nickname, furnace_lv, avatar_image, added_by, added_at FROM auto_redeem_members")
        members = cursor.fetchall()
        logger.info(f"Found {len(members)} auto-redeem members in SQLite to migrate.")
        
        migrated_members = 0
        for row in members:
            guild_id, fid, nickname, furnace_lv, avatar_image, added_by, added_at = row
            member_data = {
                'nickname': nickname,
                'furnace_lv': furnace_lv,
                'avatar_image': avatar_image,
                'added_by': added_by,
                'added_at': added_at
            }
            success = await AutoRedeemMembersAdapter.add_member_async(int(guild_id), str(fid), member_data)
            if success:
                migrated_members += 1
        logger.info(f"Successfully migrated {migrated_members} auto-redeem members.")
    except Exception as e:
        logger.error(f"Error migrating auto-redeem members: {e}")

    # 4. Migrate Auto Redeem Completed Guilds
    try:
        cursor.execute("SELECT guild_id, giftcode, status, completed_at FROM auto_redeem_completed_guilds")
        completed = cursor.fetchall()
        logger.info(f"Found {len(completed)} completed guilds in SQLite to migrate.")
        
        migrated_completed = 0
        for row in completed:
            guild_id, code, status, completed_at = row
            success = await AutoRedeemCompletedGuildsAdapter.mark_completed_async(int(guild_id), code, status)
            if success:
                migrated_completed += 1
        logger.info(f"Successfully migrated {migrated_completed} completed guilds.")
    except Exception as e:
        logger.error(f"Error migrating completed guilds: {e}")

    conn.close()
    logger.info("Migration complete!")

if __name__ == "__main__":
    asyncio.run(migrate_data())
