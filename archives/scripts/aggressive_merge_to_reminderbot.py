from pymongo import MongoClient
import certifi

# Main Target
URI_MAIN = "mongodb+srv://iammagnusx1_db_user:zYFHUOjjXhfGLpMs@reminder.hlx5aem.mongodb.net/?appName=REMINDER"
URI_FALLBACK = "mongodb+srv://yourbook444362_db_user:3KAXZB6hkJ1DAWPT@wosbot.yal4g3b.mongodb.net/?appName=WOSBOT"

client_main = MongoClient(URI_MAIN, tlsCAFile=certifi.where())
client_fallback = MongoClient(URI_FALLBACK, tlsCAFile=certifi.where())

TARGET_DB = client_main["reminderbot"]

def merge_db(client, db_name):
    print(f"\nMerging FROM: {db_name}")
    db = client[db_name]
    if db_name == "reminderbot" and client == client_main:
        print(" Skipping self-merge for main cluster's reminderbot")
        return

    for coll_name in db.list_collection_names():
        try:
            docs = list(db[coll_name].find())
            if not docs: continue
            
            # Map source collections to target collections in 'reminderbot'
            # We want to normalize names if possible
            target_coll = coll_name
            
            # Normalize common names
            if "alliance" in coll_name.lower() and "list" in coll_name.lower():
                target_coll = "alliance__alliance_list"
            elif "alliance" in coll_name.lower() and "setting" in coll_name.lower() and "monitoring" not in coll_name.lower():
                target_coll = "alliance__alliancesettings"
            elif "member" in coll_name.lower():
                target_coll = "alliance_members"
            elif "gift" in coll_name.lower() and "code" in coll_name.lower() and "redeem" not in coll_name.lower():
                target_coll = "gift_codes"
            
            print(f"  Merging {len(docs)} documents: {coll_name} -> {target_coll}")
            for doc in docs:
                TARGET_DB[target_coll].update_one({'_id': doc['_id']}, {'$set': doc}, upsert=True)
        except Exception as e:
            print(f"  Error merging {coll_name}: {e}")

# Process all found databases on Main
main_dbs = client_main.list_database_names()
for db_name in main_dbs:
    if db_name in ['admin', 'local', 'config', 'sample_mflix']: continue
    merge_db(client_main, db_name)

# Process all found databases on Fallback
fallback_dbs = client_fallback.list_database_names()
for db_name in fallback_dbs:
    if db_name in ['admin', 'local', 'config', 'sample_mflix']: continue
    merge_db(client_fallback, db_name)

print("\n--- FINAL MERGED DATA AUDIT ---")
for coll in TARGET_DB.list_collection_names():
    print(f"Collection {coll}: {TARGET_DB[coll].count_documents({})} docs")

# Specifically check for GTA again
print("\nFinal Check for GTA:")
gta_count = TARGET_DB["alliance_members"].count_documents({"$or": [
    {"name": {"$regex": "GTA", "$options": "i"}},
    {"alliance": {"$regex": "GTA", "$options": "i"}}
]})
print(f"GTA Matches in alliance_members: {gta_count}")

all_alliances = list(TARGET_DB["alliance__alliance_list"].find({}, {"name": 1}))
print(f"All Alliances in list: {[a.get('name') for a in all_alliances]}")
