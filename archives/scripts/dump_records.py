import os
from pymongo import MongoClient
import certifi

URI_MAIN = "mongodb+srv://iammagnusx1_db_user:zYFHUOjjXhfGLpMs@reminder.hlx5aem.mongodb.net/?appName=REMINDER"

client = MongoClient(URI_MAIN, tlsCAFile=certifi.where())

print("--- server_alliances (discord_bot) ---")
for doc in client["discord_bot"]["server_alliances"].find():
    print(doc)

print("\n--- admins (discord_bot) ---")
for doc in client["discord_bot"]["admins"].find():
    print(doc)
