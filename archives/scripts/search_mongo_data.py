from pymongo import MongoClient
try:
    import certifi
    ca = certifi.where()
except ImportError:
    ca = None

def search_cluster(uri, name, search_term):
    print(f"\n--- Searching Cluster: {name} for '{search_term}' ---")
    try:
        client = MongoClient(uri, tlsCAFile=ca, serverSelectionTimeoutMS=5000)
        dbs = client.list_database_names()
        for db_name in dbs:
            if db_name in ['admin', 'local', 'config']: continue
            db = client[db_name]
            colls = db.list_collection_names()
            for coll_name in colls:
                try:
                    # Search in all fields for the term
                    count = db[coll_name].count_documents({
                        "$or": [
                            {"name": {"$regex": search_term, "$options": "i"}},
                            {"nickname": {"$regex": search_term, "$options": "i"}},
                            {"alliance_name": {"$regex": search_term, "$options": "i"}},
                            {"fid": {"$regex": search_term, "$options": "i"}},
                            {"_id": {"$regex": search_term, "$options": "i"}}
                        ]
                    })
                    if count > 0:
                        print(f" FOUND in {db_name}.{coll_name}: {count} documents")
                        # Print one sample
                        sample = db[coll_name].find_one({
                            "$or": [
                                {"name": {"$regex": search_term, "$options": "i"}},
                                {"nickname": {"$regex": search_term, "$options": "i"}},
                                {"alliance_name": {"$regex": search_term, "$options": "i"}},
                                {"fid": {"$regex": search_term, "$options": "i"}},
                                {"_id": {"$regex": search_term, "$options": "i"}}
                            ]
                        })
                        print(f" Sample: {sample}")
                except Exception as e:
                    pass
    except Exception as e:
        print(f" Error: {e}")

URI_REMINDER = "mongodb+srv://iammagnusx1_db_user:zYFHUOjjXhfGLpMs@reminder.hlx5aem.mongodb.net/?appName=REMINDER"
URI_WOSBOT = "mongodb+srv://yourbook444362_db_user:3KAXZB6hkJ1DAWPT@wosbot.yal4g3b.mongodb.net/?appName=WOSBOT"

search_cluster(URI_REMINDER, "REMINDER", "GTA")
search_cluster(URI_REMINDER, "REMINDER", "3063")
search_cluster(URI_WOSBOT, "WOSBOT", "GTA")
search_cluster(URI_WOSBOT, "WOSBOT", "3063")
