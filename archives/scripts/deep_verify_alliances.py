from pymongo import MongoClient
try:
    import certifi
    ca = certifi.where()
except ImportError:
    ca = None

def deep_audit(uri):
    print(f"\n--- DEEP ALLIANCE & MEMBER AUDIT ---")
    client = MongoClient(uri, tlsCAFile=ca, serverSelectionTimeoutMS=5000)
    dbs = client.list_database_names()
    
    for db_name in dbs:
        if db_name in ['admin', 'local', 'config']: continue
        db = client[db_name]
        print(f"\n[Database: {db_name}]")
        
        colls = db.list_collection_names()
        for coll_name in colls:
            # Check Alliance collections
            if 'alliance' in coll_name.lower():
                try:
                    count = db[coll_name].count_documents({})
                    if count > 0:
                        print(f"  Collection: {coll_name} | Total Docs: {count}")
                        # Look for GTA or 3063
                        matches = list(db[coll_name].find({
                            "$or": [
                                {"name": {"$regex": "GTA|3063", "$options": "i"}},
                                {"alliance_name": {"$regex": "GTA|3063", "$options": "i"}}
                            ]
                        }))
                        if matches:
                            print(f"    FOUND {len(matches)} matches for GTA/3063:")
                            for m in matches:
                                print(f"      - {m.get('name') or m.get('alliance_name')} (ID: {m.get('alliance_id') or m.get('_id')})")
                        else:
                            # List top 5 names if no match
                            samples = list(db[coll_name].find({}, {"name": 1, "alliance_name": 1}).limit(5))
                            names = [s.get('name') or s.get('alliance_name') for s in samples]
                            print(f"    Sample names: {names}")
                except Exception as e:
                    print(f"    Error reading {coll_name}: {e}")
            
            # Check Member collections
            if 'member' in coll_name.lower():
                try:
                    gta_members = db[coll_name].count_documents({
                        "$or": [
                            {"alliance": {"$regex": "GTA|3063", "$options": "i"}},
                            {"alliance_name": {"$regex": "GTA|3063", "$options": "i"}}
                        ]
                    })
                    if gta_members > 0:
                        print(f"  Collection: {coll_name} | GTA/3063 Members: {gta_members}")
                except:
                    pass

URI_REMINDER = "mongodb+srv://iammagnusx1_db_user:zYFHUOjjXhfGLpMs@reminder.hlx5aem.mongodb.net/?appName=REMINDER"
deep_audit(URI_REMINDER)
