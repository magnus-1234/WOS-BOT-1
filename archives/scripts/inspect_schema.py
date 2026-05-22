from pymongo import MongoClient
import certifi

def inspect_schema(uri):
    client = MongoClient(uri, tlsCAFile=certifi.where())
    db = client['reminderbot']
    
    colls = ['alliance__alliance_list', 'alliances', 'server_alliances', 'alliance_members']
    for coll in colls:
        print(f"\n--- Collection: {coll} (Sample) ---")
        doc = db[coll].find_one()
        if doc:
            print(doc)
        else:
            print("Empty collection")

URI_MAIN = "mongodb+srv://iammagnusx1_db_user:zYFHUOjjXhfGLpMs@reminder.hlx5aem.mongodb.net/?appName=REMINDER"
inspect_schema(URI_MAIN)
