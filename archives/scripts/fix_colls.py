from pymongo import MongoClient
import certifi

def fix_mismatched_colls(uri):
    client = MongoClient(uri, tlsCAFile=certifi.where())
    db = client['reminderbot']
    
    # 1. Merge alliance_settings into alliance__alliancesettings
    print("--- Merging alliance_settings into alliance__alliancesettings ---")
    source_settings = list(db['alliance_settings'].find({}))
    for s in source_settings:
        aid = s.get('alliance_id')
        if aid is not None:
            # Upsert into target
            db['alliance__alliancesettings'].update_one(
                {"alliance_id": int(aid)},
                {"$set": {k: v for k, v in s.items() if k != '_id'}},
                upsert=True
            )
            print(f" Merged settings for ID {aid}")

    # 2. Merge alliances into alliance__alliance_list
    print("\n--- Merging alliances into alliance__alliance_list ---")
    source_alliances = list(db['alliances'].find({}))
    for a in source_alliances:
        aid = a.get('_id') # Sometimes ID is in _id
        name = a.get('name')
        if name:
            try:
                # Try to extract numeric ID if possible
                if isinstance(aid, (int, float)):
                    numeric_id = int(aid)
                else:
                    numeric_id = None
            except:
                numeric_id = None
                
            db['alliance__alliance_list'].update_one(
                {"name": name},
                {"$set": {"name": name, "id": numeric_id or a.get('id')}},
                upsert=True
            )
            print(f" Merged alliance {name}")

    print("\n--- Final Check of alliance__alliancesettings ---")
    for s in db['alliance__alliancesettings'].find({}):
        print(f" Alliance ID: {s.get('alliance_id')} | Channel: {s.get('channel_id')}")

URI_MAIN = "mongodb+srv://iammagnusx1_db_user:zYFHUOjjXhfGLpMs@reminder.hlx5aem.mongodb.net/?appName=REMINDER"
fix_mismatched_colls(URI_MAIN)
