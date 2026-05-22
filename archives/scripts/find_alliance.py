import pymongo
client = pymongo.MongoClient('mongodb+srv://iammagnusx1_db_user:zYFHUOjjXhfGLpMs@reminder.hlx5aem.mongodb.net/?appName=REMINDER')
db = client['reminderbot']
print("From alliance_members:")
guilds = {}
for doc in db['alliance_members'].find():
    alliance = doc.get("alliance")
    if alliance not in guilds:
        guilds[alliance] = 0
    guilds[alliance] += 1

print("Members per alliance:")
for g, c in guilds.items():
    print(f"Alliance {g}: {c} members")
