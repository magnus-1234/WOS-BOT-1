from pymongo import MongoClient
try:
    import certifi
    ca = certifi.where()
except ImportError:
    ca = None

def verify_merges(uri, db_name):
    print(f"\n--- Verifying Consolidated Data in {db_name} ---")
    try:
        client = MongoClient(uri, tlsCAFile=ca, serverSelectionTimeoutMS=5000)
        db = client[db_name]
        
        # Check specific alliances
        search_terms = ["GTA", "3063", "S667"]
        colls_to_check = ["alliance_members", "alliance__alliance_list", "alliance__alliancesettings"]
        
        for coll in colls_to_check:
            if coll not in db.list_collection_names():
                print(f" ERROR: Collection {coll} missing!")
                continue
            
            print(f"\nChecking collection: {coll}")
            for term in search_terms:
                count = db[coll].count_documents({
                    "$or": [
                        {"name": {"$regex": term, "$options": "i"}},
                        {"nickname": {"$regex": term, "$options": "i"}},
                        {"alliance_name": {"$regex": term, "$options": "i"}},
                        {"fid": {"$regex": term, "$options": "i"}},
                        {"_id": {"$regex": term, "$options": "i"}}
                    ]
                })
                print(f"  Match for '{term}': {count} documents")
                if count > 0:
                    sample = db[coll].find_one({
                        "$or": [
                            {"name": {"$regex": term, "$options": "i"}},
                            {"nickname": {"$regex": term, "$options": "i"}},
                            {"alliance_name": {"$regex": term, "$options": "i"}},
                            {"fid": {"$regex": term, "$options": "i"}},
                            {"_id": {"$regex": term, "$options": "i"}}
                        ]
                    })
                    print(f"  Sample ID: {sample.get('_id')}")

        # Total counts
        for coll in db.list_collection_names():
            print(f" - Collection {coll}: {db[coll].count_documents({})} docs")

    except Exception as e:
        print(f" Error during verification: {e}")

URI_REMINDER = "mongodb+srv://iammagnusx1_db_user:zYFHUOjjXhfGLpMs@reminder.hlx5aem.mongodb.net/?appName=REMINDER"
verify_merges(URI_REMINDER, "reminderbot")
