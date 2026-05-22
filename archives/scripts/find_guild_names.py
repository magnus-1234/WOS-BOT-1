import pymongo
client = pymongo.MongoClient('mongodb+srv://iammagnusx1_db_user:zYFHUOjjXhfGLpMs@reminder.hlx5aem.mongodb.net/?appName=REMINDER')
db = client['reminderbot']
print("From auto_redeem_settings:")
for g in db['auto_redeem_settings'].find():
    print(f"Guild ID: {g.get('guild_id')}, Name: {g.get('guild_name', 'Unknown')}")
