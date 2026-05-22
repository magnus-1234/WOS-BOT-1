import os
from pymongo import MongoClient
import certifi

URI_MAIN = "mongodb+srv://iammagnusx1_db_user:zYFHUOjjXhfGLpMs@reminder.hlx5aem.mongodb.net/?appName=REMINDER"
URI_FALLBACK = "mongodb+srv://yourbook444362_db_user:3KAXZB6hkJ1DAWPT@wosbot.yal4g3b.mongodb.net/?appName=WOSBOT"

client_main = MongoClient(URI_MAIN, tlsCAFile=certifi.where())
client_fallback = MongoClient(URI_FALLBACK, tlsCAFile=certifi.where())

source_db = client_main["reminderbot"]
target_discord_bot = client_main["discord_bot"]
target_wos_bot = client_fallback["wos_bot"] # Active WOS Database

discord_bot_collections = [
    ('server_alliances', 'server_alliances'),
    ('alliance_monitoring', 'alliance_monitoring'),
    ('alliances', 'alliance__alliance_list'),
    ('alliance_settings', 'alliance__alliancesettings'),
    ('admins', 'admins')
]

wos_bot_collections = [
    ('alliance_members', 'alliance_members'),
    ('gift_codes', 'gift_codes')
]

print("--- STARTING MERGE ---\n")

for source_coll, target_coll in discord_bot_collections:
    docs = list(source_db[source_coll].find())
    if docs:
        print(f"Merging {len(docs)} documents from {source_coll} to discord_bot.{target_coll}...")
        for doc in docs:
            # Upsert into target
            target_discord_bot[target_coll].update_one({'_id': doc['_id']}, {'$set': doc}, upsert=True)
            
for source_coll, target_coll in wos_bot_collections:
    docs = list(source_db[source_coll].find())
    if docs:
        print(f"Merging {len(docs)} documents from {source_coll} to wos_bot.{target_coll} (Fallback Cluster)...")
        for doc in docs:
            # Upsert into target
            target_wos_bot[target_coll].update_one({'_id': doc['_id']}, {'$set': doc}, upsert=True)

print("\n--- MERGE COMPLETE ---")
