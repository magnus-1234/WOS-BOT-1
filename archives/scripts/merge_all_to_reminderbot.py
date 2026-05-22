import os
from pymongo import MongoClient
try:
    import certifi
    ca = certifi.where()
except ImportError:
    ca = None

# URIs
URI_MAIN = "mongodb+srv://iammagnusx1_db_user:zYFHUOjjXhfGLpMs@reminder.hlx5aem.mongodb.net/?appName=REMINDER"
URI_FALLBACK = "mongodb+srv://yourbook444362_db_user:3KAXZB6hkJ1DAWPT@wosbot.yal4g3b.mongodb.net/?appName=WOSBOT"

client_main = MongoClient(URI_MAIN, tlsCAFile=ca)
client_fallback = MongoClient(URI_FALLBACK, tlsCAFile=ca)

TARGET_DB = client_main["reminderbot"]

# Sources to merge FROM
# Format: (client, db_name, collection_mapping)
# collection_mapping: { source_coll: target_coll }
SOURCES = [
    (client_main, "wos_bot", {
        "alliance_members": "alliance_members",
        "gift_codes": "gift_codes",
        "furnace_history": "furnace_history"
    }),
    (client_main, "whiteout", {
        "alliance__alliance_list": "alliance__alliance_list",
        "alliance__alliancesettings": "alliance__alliancesettings",
        "giftcode__gift_codes": "gift_codes",
        "id_channel__id_channels": "id_channels"
    }),
    (client_main, "sqlite_imports", {
        "alliance__alliance_list": "alliance__alliance_list",
        "alliance__alliancesettings": "alliance__alliancesettings",
        "giftcode__gift_codes": "gift_codes",
        "settings__admin": "admins"
    }),
    (client_fallback, "wos_bot", {
        "alliance_members": "alliance_members",
        "gift_codes": "gift_codes",
        "furnace_history": "furnace_history"
    })
]

print("--- STARTING COMPREHENSIVE MERGE INTO 'reminderbot' ---\n")

for client, db_name, mapping in SOURCES:
    print(f"\nProcessing Source Database: {db_name}")
    db = client[db_name]
    for source_coll, target_coll in mapping.items():
        try:
            if source_coll not in db.list_collection_names():
                continue
            
            docs = list(db[source_coll].find())
            if not docs:
                continue
                
            print(f" Merging {len(docs)} documents from {source_coll} -> {target_coll}")
            for doc in docs:
                # Upsert into target reminderbot database
                TARGET_DB[target_coll].update_one(
                    {'_id': doc['_id']},
                    {'$set': doc},
                    upsert=True
                )
        except Exception as e:
            print(f"  Error merging {source_coll}: {e}")

print("\n--- MERGE COMPLETE ---")
