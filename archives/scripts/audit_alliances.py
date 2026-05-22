from pymongo import MongoClient
try:
    import certifi
    ca = certifi.where()
except ImportError:
    ca = None

def list_all_alliances(uri):
    print(f"\n--- Global Alliance Audit ---")
    try:
        client = MongoClient(uri, tlsCAFile=ca, serverSelectionTimeoutMS=5000)
        dbs = client.list_database_names()
        for db_name in dbs:
            if db_name in ['admin', 'local', 'config']: continue
            db = client[db_name]
            colls = db.list_collection_names()
            for coll in colls:
                if 'alliance' in coll.lower() and 'member' not in coll.lower():
                    try:
                        docs = list(db[coll].find({}, {"name": 1, "alliance_id": 1, "id": 1}))
                        if docs:
                            print(f"\nDB: {db_name} | Collection: {coll} | Count: {len(docs)}")
                            for d in docs:
                                name = d.get('name')
                                aid = d.get('alliance_id') or d.get('id')
                                print(f"  - [{aid}] {name}")
                    except:
                        pass
    except Exception as e:
        print(f" Error: {e}")

URI_REMINDER = "mongodb+srv://iammagnusx1_db_user:zYFHUOjjXhfGLpMs@reminder.hlx5aem.mongodb.net/?appName=REMINDER"
list_all_alliances(URI_REMINDER)
