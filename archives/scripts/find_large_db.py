import os
from pymongo import MongoClient
import certifi

URI_MAIN = "mongodb+srv://iammagnusx1_db_user:zYFHUOjjXhfGLpMs@reminder.hlx5aem.mongodb.net/?appName=REMINDER"
URI_FALLBACK = "mongodb+srv://yourbook444362_db_user:3KAXZB6hkJ1DAWPT@wosbot.yal4g3b.mongodb.net/?appName=WOSBOT"

client_main = MongoClient(URI_MAIN, tlsCAFile=certifi.where())
client_fallback = MongoClient(URI_FALLBACK, tlsCAFile=certifi.where())

def check_db(client, client_name):
    print(f"\n--- Checking {client_name} ---")
    for db_name in client.list_database_names():
        if db_name in ['admin', 'local']:
            continue
        db = client[db_name]
        for coll_name in db.list_collection_names():
            if 'adminserver' in coll_name or 'alliance' in coll_name or 'server' in coll_name:
                count = db[coll_name].count_documents({})
                if count > 0:
                    print(f"[{db_name}][{coll_name}] has {count} documents")

check_db(client_main, "MAIN CLUSTER")
check_db(client_fallback, "FALLBACK CLUSTER")
