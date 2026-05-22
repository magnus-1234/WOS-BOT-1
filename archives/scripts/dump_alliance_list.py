from pymongo import MongoClient
import certifi

def audit_list_collection(uri):
    client = MongoClient(uri, tlsCAFile=certifi.where())
    db = client['reminderbot']
    coll = db['alliance__alliance_list']
    
    print("--- Full Dump of alliance__alliance_list ---")
    docs = list(coll.find({}))
    for doc in docs:
        print(doc)

URI_MAIN = "mongodb+srv://iammagnusx1_db_user:zYFHUOjjXhfGLpMs@reminder.hlx5aem.mongodb.net/?appName=REMINDER"
audit_list_collection(URI_MAIN)
