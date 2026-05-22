import pymongo

uri = "mongodb+srv://iammagnusx1_db_user:zYFHUOjjXhfGLpMs@reminder.hlx5aem.mongodb.net/?appName=REMINDER"
client = pymongo.MongoClient(uri)
db = client['reminderbot']
collection = db['alliance_members']

members = list(collection.find({"alliance": {"$in": ["1", 1]}}))

seen_nicks = {}
duplicates = []

for m in members:
    nick = m.get('nickname', '').lower().strip()
    if not nick: continue
    
    if nick in seen_nicks:
        # Keep the first one, delete subsequent
        duplicates.append(m)
    else:
        seen_nicks[nick] = m

print(f"Found {len(duplicates)} duplicate records based on nickname.")

if duplicates:
    dupe_ids = [d['_id'] for d in duplicates]
    result = collection.delete_many({"_id": {"$in": dupe_ids}})
    print(f"Deleted {result.deleted_count} duplicate records.")
    
    # Let's also standardize the alliance ID to integer 1 for the remaining ones
    update_res = collection.update_many({"alliance": "1"}, {"$set": {"alliance": 1}})
    print(f"Updated {update_res.modified_count} string '1' to int 1.")
