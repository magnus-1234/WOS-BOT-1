import os
import pprint
from pymongo import MongoClient
import certifi

URI_MAIN = "mongodb+srv://iammagnusx1_db_user:zYFHUOjjXhfGLpMs@reminder.hlx5aem.mongodb.net/?appName=REMINDER"
client = MongoClient(URI_MAIN, tlsCAFile=certifi.where())

COLL = 'server_alliances'
print("--- Dumping All Server Alliances cleanly ---")

for doc in client["discord_bot"][COLL].find():
    print(f"\nGuild _id type: {type(doc.get('_id'))} = {doc.get('_id')}")
    print(f"Has password? {'member_list_password' in doc}")
    print(f"Has alliance_id? {'alliance_id' in doc} or alliances_id? {'alliances_id' in doc}")
    pprint.pprint(doc)
