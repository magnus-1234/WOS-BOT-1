import pymongo

uri = "mongodb+srv://iammagnusx1_db_user:zYFHUOjjXhfGLpMs@reminder.hlx5aem.mongodb.net/?appName=REMINDER"
client = pymongo.MongoClient(uri)
db = client['reminderbot']
collection = db['alliance_members']

members = list(collection.find({"alliance": 1}))

seen_fids = {}
duplicates = []

for m in members:
    fid = m.get('fid')
    if fid is None:
        continue
    
    fid_str = str(fid).strip()
    
    if fid_str in seen_fids:
        # Just keep the first one we saw
        duplicates.append(m['_id'])
    else:
        seen_fids[fid_str] = m

print(f"Found {len(duplicates)} duplicate records based on string FID.")

if duplicates:
    result = collection.delete_many({"_id": {"$in": duplicates}})
    print(f"Deleted {result.deleted_count} duplicate records.")
    
    # Optional: ensure all fids are strings or ints
    # collection.update_many({"alliance": 1}, [{"$set": {"fid": {"$toInt": "$fid"}}}])
