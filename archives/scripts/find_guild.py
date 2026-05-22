import pymongo
from collections import Counter

uri = "mongodb+srv://yourbook444362_db_user:3KAXZB6hkJ1DAWPT@wosbot.yal4g3b.mongodb.net/?appName=WOSBOT"
client = pymongo.MongoClient(uri)
db = client['reminderbot']
collection = db['auto_redeem_members']

guilds = {}
for doc in collection.find():
    guild_id = doc.get("guild_id")
    if guild_id not in guilds:
        guilds[guild_id] = 0
    guilds[guild_id] += 1

print("Members per guild:")
for g, c in guilds.items():
    print(f"Guild {g}: {c} members")
