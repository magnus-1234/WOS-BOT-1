from pymongo import MongoClient
import certifi

def final_audit(uri):
    client = MongoClient(uri, tlsCAFile=certifi.where())
    db = client['reminderbot']
    
    print("\n--- Consolidated Alliance Member Audit ---")
    pipeline = [
        {"$group": {"_id": "$alliance", "count": {"$sum": 1}}}
    ]
    results = list(db['alliance_members'].aggregate(pipeline))
    for r in results:
        print(f" Alliance: {r['_id']} | Members: {r['count']}")
        
    print("\n--- Consolidated Alliance List Audit ---")
    alliances = list(db['alliance__alliance_list'].find({}))
    for a in alliances:
        print(f" Name: {a.get('name')} | ID: {a.get('alliance_id') or a.get('id')}")

    print("\n--- Searching for GTA/3063 specifically ---")
    terms = ["GTA", "3063", "GTACAT"]
    for term in terms:
        m_count = db['alliance_members'].count_documents({"alliance": {"$regex": term, "$options": "i"}})
        l_count = db['alliance__alliance_list'].count_documents({"name": {"$regex": term, "$options": "i"}})
        print(f" Term '{term}': {m_count} documents in members, {l_count} in list")

URI_MAIN = "mongodb+srv://iammagnusx1_db_user:zYFHUOjjXhfGLpMs@reminder.hlx5aem.mongodb.net/?appName=REMINDER"
final_audit(URI_MAIN)
