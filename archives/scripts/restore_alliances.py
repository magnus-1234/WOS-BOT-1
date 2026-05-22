from pymongo import MongoClient
import certifi

def restore_missing_alliances(uri):
    client = MongoClient(uri, tlsCAFile=certifi.where())
    db = client['reminderbot']
    
    # Missing alliances to add
    updates = [
        {"id": 5, "name": "LTS"},
        {"id": 8, "name": "KOR 3063"},
        {"id": 13, "name": "GTA"},
        {"id": 7, "name": "CAT"},
        {"id": 9, "name": "TAKUMI"},
        {"id": 11, "name": "SBROWNDUKE"},
        {"id": 12, "name": "MOONKISSED"}
    ]
    
    print("--- Restoring missing alliances to alliance__alliance_list ---")
    for update in updates:
        # Upsert by ID
        db['alliance__alliance_list'].update_one(
            {"id": update['id']},
            {"$set": {"name": update['name']}},
            upsert=True
        )
        print(f" Restored ID {update['id']} as {update['name']}")
        
    print("\n--- Final Alliance List ---")
    for a in db['alliance__alliance_list'].find({}):
        print(f" ID: {a.get('id')} | Name: {a.get('name')}")

URI_MAIN = "mongodb+srv://iammagnusx1_db_user:zYFHUOjjXhfGLpMs@reminder.hlx5aem.mongodb.net/?appName=REMINDER"
restore_missing_alliances(URI_MAIN)
