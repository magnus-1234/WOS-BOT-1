"""
Repair script: Fix all server_alliances documents that are missing alliances_id.

For each document in server_alliances that has no alliances_id:
  1. Try to look up the alliance_name in SQLite alliance_list to get the numeric ID
  2. If not found in SQLite, try MongoDB alliances collection
  3. If found, patch the server_alliances document with alliances_id

Also checks pending_configs to get alliance_name for servers that were approved
through the new registration system.

Run this once on the server to repair all broken registrations.
"""
import asyncio
import os
import sys
import logging

# Add the project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


async def repair_server_alliances():
    try:
        from db.mongo_adapters import _get_db_main_async, mongo_enabled, ServerAllianceAdapter
    except ImportError as e:
        print(f"Import error: {e}")
        return

    if not mongo_enabled():
        print("MongoDB not enabled, cannot repair.")
        return

    db = await _get_db_main_async()
    
    # Load all SQLite alliances for quick lookup by name
    sqlite_alliances = {}  # name -> alliance_id
    try:
        import sqlite3
        with sqlite3.connect('db/alliance.sqlite', timeout=10) as adb:
            cur = adb.cursor()
            cur.execute("SELECT alliance_id, name FROM alliance_list")
            for row in cur.fetchall():
                sqlite_alliances[row[1].lower().strip()] = row[0]
        print(f"Loaded {len(sqlite_alliances)} alliances from SQLite")
    except Exception as e:
        print(f"Warning: SQLite load failed: {e}")

    # Load all MongoDB alliances for quick lookup
    mongo_alliances = {}
    try:
        cursor = db['alliances'].find({})
        async for doc in cursor:
            name = str(doc.get('name', '') or '').lower().strip()
            aid = doc.get('alliance_id') or doc.get('alliances_id')
            if name and aid:
                mongo_alliances[name] = int(aid)
        print(f"Loaded {len(mongo_alliances)} alliances from MongoDB")
    except Exception as e:
        print(f"Warning: MongoDB alliances load failed: {e}")

    # Load pending_configs for approved servers to get alliance_name
    guild_to_alliance_name = {}
    try:
        cursor = db['pending_configs'].find({'status': 'approved'})
        async for doc in cursor:
            gid = str(doc.get('guild_id', ''))
            name = str(doc.get('alliance_name', '') or '').strip()
            if gid and name:
                guild_to_alliance_name[gid] = name
        print(f"Loaded {len(guild_to_alliance_name)} approved registrations from pending_configs")
    except Exception as e:
        print(f"Warning: pending_configs load failed: {e}")

    # Find all server_alliances docs missing alliances_id
    broken_docs = []
    try:
        cursor = db[ServerAllianceAdapter.COLL].find({
            '$and': [
                {'alliances_id': {'$exists': False}},
                {'alliance_id': {'$exists': False}}
            ]
        })
        async for doc in cursor:
            broken_docs.append(doc)
    except Exception as e:
        print(f"Error finding broken docs: {e}")
        return

    # Also find docs where alliances_id = 0 or null
    try:
        cursor = db[ServerAllianceAdapter.COLL].find({
            '$or': [
                {'alliances_id': 0},
                {'alliances_id': None},
                {'alliance_id': 0},
                {'alliance_id': None}
            ]
        })
        async for doc in cursor:
            if doc not in broken_docs:
                broken_docs.append(doc)
    except Exception as e:
        print(f"Error finding zero-alliance docs: {e}")

    print(f"\nFound {len(broken_docs)} documents without valid alliances_id\n")

    repaired = 0
    failed = []

    for doc in broken_docs:
        guild_id = doc.get('_id') or doc.get('id')
        guild_id_str = str(guild_id)
        
        # Get alliance_name from the doc or from pending_configs
        alliance_name = (
            str(doc.get('alliance_name') or '').strip() or
            guild_to_alliance_name.get(guild_id_str, '')
        )
        
        if not alliance_name:
            print(f"  SKIP  guild={guild_id_str}: no alliance_name found anywhere")
            failed.append({'guild_id': guild_id_str, 'reason': 'no alliance_name'})
            continue

        # Try to resolve numeric alliance_id
        alliance_id = None
        lower_name = alliance_name.lower().strip()
        
        if lower_name in sqlite_alliances:
            alliance_id = sqlite_alliances[lower_name]
            source = 'SQLite'
        elif lower_name in mongo_alliances:
            alliance_id = mongo_alliances[lower_name]
            source = 'MongoDB'
        else:
            # Try to create it in SQLite
            try:
                import sqlite3
                with sqlite3.connect('db/alliance.sqlite', timeout=10) as adb:
                    cur = adb.cursor()
                    cur.execute(
                        "INSERT INTO alliance_list (name, discord_server_id) VALUES (?, ?)",
                        (alliance_name, int(guild_id_str) if guild_id_str.isdigit() else 0)
                    )
                    adb.commit()
                    alliance_id = cur.lastrowid
                    sqlite_alliances[lower_name] = alliance_id
                    source = 'SQLite (created)'
            except Exception as e:
                print(f"  SKIP  guild={guild_id_str} name={alliance_name!r}: could not create in SQLite: {e}")
                failed.append({'guild_id': guild_id_str, 'alliance_name': alliance_name, 'reason': str(e)})
                continue

        if not alliance_id:
            print(f"  SKIP  guild={guild_id_str} name={alliance_name!r}: alliance_id resolved to 0")
            failed.append({'guild_id': guild_id_str, 'alliance_name': alliance_name, 'reason': 'alliance_id=0'})
            continue

        # Patch the document
        try:
            from datetime import datetime
            await db[ServerAllianceAdapter.COLL].update_one(
                {'_id': doc['_id']},
                {'$set': {
                    'alliances_id': int(alliance_id),
                    'alliance_id': int(alliance_id),
                    'repaired_at': datetime.utcnow().isoformat()
                }}
            )
            print(f"  FIXED guild={guild_id_str} name={alliance_name!r} -> alliance_id={alliance_id} (from {source})")
            repaired += 1
        except Exception as e:
            print(f"  ERROR guild={guild_id_str}: patch failed: {e}")
            failed.append({'guild_id': guild_id_str, 'alliance_name': alliance_name, 'reason': str(e)})

    print(f"\n=== REPAIR COMPLETE ===")
    print(f"Repaired: {repaired}")
    print(f"Failed/Skipped: {len(failed)}")
    if failed:
        print("\nFailed guilds:")
        for f in failed:
            print(f"  {f}")


if __name__ == '__main__':
    from dotenv import load_dotenv
    load_dotenv()
    asyncio.run(repair_server_alliances())
