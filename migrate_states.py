import os
import sqlite3
import asyncio
from datetime import datetime
import sys

# Ensure correct path context
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from db.mongo_adapters import (
    _get_db_main, _get_db_wos, 
    AutoRedeemSettingsAdapter,
    AllianceMembersAdapter,
    PlayerStateAdapter,
    AutoRedeemMembersAdapter
)

def get_all_sqlite_members():
    members = []
    try:
        if not os.path.exists('db/giftcode.sqlite'):
            return members
        with sqlite3.connect('db/giftcode.sqlite') as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT guild_id, fid, state_id FROM auto_redeem_members")
            for row in cursor.fetchall():
                members.append({'guild_id': row[0], 'fid': str(row[1]).strip(), 'state_id': str(row[2]).strip() if row[2] else None})
    except Exception as e:
        print(f"Error reading SQLite: {e}")
    return members

def get_all_mongo_members():
    members = []
    try:
        db = _get_db_main()
        for doc in db[AutoRedeemMembersAdapter.COLL].find({}):
            members.append({
                'guild_id': doc.get('guild_id'),
                'fid': str(doc.get('fid')).strip(),
                'state_id': str(doc.get('state_id')).strip() if doc.get('state_id') else None
            })
    except Exception as e:
        print(f"Error reading Mongo: {e}")
    return members

def main():
    print("Starting migration of player state numbers...")
    
    # 1. Gather all users from both SQLite and MongoDB
    all_members = {}
    
    sqlite_members = get_all_sqlite_members()
    for m in sqlite_members:
        all_members[m['fid']] = m
        
    mongo_members = get_all_mongo_members()
    for m in mongo_members:
        all_members[m['fid']] = m
        
    print(f"Found {len(all_members)} unique players in auto-redeem databases.")
    
    # 2. Iterate and fix missing state_ids
    fixed_count = 0
    already_good_count = 0
    failed_count = 0
    
    db_wos = _get_db_wos()
    alliance_coll = db_wos[AllianceMembersAdapter.COLL]
    
    for fid, data in all_members.items():
        guild_id = data.get('guild_id')
        state_id = data.get('state_id')
        
        # Check if they already have a valid kid
        if state_id and state_id not in ('0', 'None', '', 'null'):
            PlayerStateAdapter.set_kid(fid, state_id)
            already_good_count += 1
            continue
            
        # They are missing kid. Step A: Try Alliance Monitor
        alliance_doc = alliance_coll.find_one({'_id': fid})
        new_kid = None
        if alliance_doc:
            new_kid = alliance_doc.get('state') or alliance_doc.get('state_id')
        
        # Step B: Fallback to Server Default
        if not new_kid or str(new_kid).strip() in ('0', 'None', ''):
            settings = AutoRedeemSettingsAdapter.get_settings(guild_id)
            if settings and settings.get('default_state'):
                new_kid = settings.get('default_state')
                
        # If we found it, save it!
        if new_kid and str(new_kid).strip() not in ('0', 'None', ''):
            PlayerStateAdapter.set_kid(fid, str(new_kid).strip())
            print(f"[FIXED] Player {fid} assigned State {new_kid}")
            fixed_count += 1
        else:
            print(f"[FAILED] Could not find State for Player {fid}")
            failed_count += 1

    print("\n=== MIGRATION COMPLETE ===")
    print(f"Total Processed: {len(all_members)}")
    print(f"Already Good: {already_good_count}")
    print(f"Successfully Fixed/Backfilled: {fixed_count}")
    print(f"Still Missing State: {failed_count}")

if __name__ == '__main__':
    main()
