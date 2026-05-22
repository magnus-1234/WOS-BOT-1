import os
import sys
import sqlite3
from pathlib import Path
from dotenv import load_dotenv

# Add DISCORD BOT to path for imports
discord_bot_path = Path(__file__).parent / "DISCORD BOT"
sys.path.insert(0, str(discord_bot_path))
load_dotenv(discord_bot_path / ".env")

from db.mongo_adapters import AutoRedeemMembersAdapter, mongo_enabled

def sync():
    print("=" * 60)
    print("SYNC: MONGODB -> SQLITE (Auto-Redeem Members)")
    print("=" * 60)
    
    if not mongo_enabled():
        print("❌ MongoDB is not enabled in .env")
        return

    db_path = discord_bot_path / "db" / "giftcode.sqlite"
    print(f"Target SQLite: {db_path}")
    
    # 1. Connect to SQLite
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    
    # 2. Get all distinct guild IDs from MongoDB (or manually specify if needed)
    # Since our internal adapter requires guild_id, we'll try to find all guild_ids first
    from pymongo import MongoClient
    client = MongoClient(os.getenv('MONGO_URI'))
    db = client[os.getenv('MONGO_DB_NAME', 'reminderbot')]
    guild_ids = db['auto_redeem_members'].distinct('guild_id')
    
    print(f"Found {len(guild_ids)} guilds in MongoDB")
    
    total_synced = 0
    
    for gid in guild_ids:
        print(f"\nProcessing Guild {gid}...")
        try:
            members = AutoRedeemMembersAdapter.get_members(gid)
            print(f"   Fetched {len(members)} unique members from MongoDB")
            
            for m in members:
                fid = m.get('fid')
                if not fid:
                    continue
                nickname = m.get('nickname', 'Unknown')
                furnace_lv = m.get('furnace_lv', 0)
                avatar = m.get('avatar_image', '')
                added_by = m.get('added_by', 0)
                added_at = m.get('added_at', '')
                
                cursor.execute("""
                    INSERT OR REPLACE INTO auto_redeem_members 
                    (guild_id, fid, nickname, furnace_lv, avatar_image, added_by, added_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (int(gid), str(fid), nickname, int(furnace_lv), avatar, int(added_by), str(added_at)))
                total_synced += 1
                
            conn.commit()
            print(f"   Successfully synced {len(members)} members for guild {gid}")
            
        except Exception as e:
            print(f"   Error processing guild {gid}: {e}")

    conn.close()
    print("\n" + "=" * 60)
    print(f"SYNC COMPLETE: {total_synced} total member records updated.")
    print("=" * 60)

if __name__ == "__main__":
    sync()
