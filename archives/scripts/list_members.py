import pymongo

uri = "mongodb+srv://iammagnusx1_db_user:zYFHUOjjXhfGLpMs@reminder.hlx5aem.mongodb.net/?appName=REMINDER"
client = pymongo.MongoClient(uri)
db = client['reminderbot']
collection = db['alliance_members']

members = list(collection.find({"alliance": 1}))
print(f"Total remaining members: {len(members)}")

# Sort members by nickname for easy visual inspection of near-duplicates
members.sort(key=lambda x: x.get('nickname', '').lower())

for m in members:
    nick = m.get('nickname', '')
    fid = m.get('fid') or m.get('id') or m.get('_id')
    print(f"Name: {nick:<25} | ID: {fid}")

