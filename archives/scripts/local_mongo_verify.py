import certifi
from pymongo import MongoClient

def verify_data():
    uri = "mongodb+srv://iammagnusx1_db_user:zYFHUOjjXhfGLpMs@reminder.hlx5aem.mongodb.net/?appName=REMINDER"
    client = MongoClient(uri, tlsCAFile=certifi.where())
    db = client['reminderbot']

    print("--- ALLIANCE LIST ---")
    list_col = db['alliance__alliance_list']
    for a in list_col.find({}):
        aid = a.get('id') or a.get('alliance_id')
        name = a.get('name')
        print(f"ID: {aid}, Name: {name}")

    print("\n--- GTA (ID 13) MEMBERS ---")
    count_13 = db['alliance_members'].count_documents({'alliance': 13})
    print(f"Count: {count_13}")
    
    list_13 = db['alliance__alliance_list'].find_one({'$or': [{'id': 13}, {'alliance_id': 13}]})
    print(f"ID 13 in list: {list_13}")

    print("\n--- 3063 (ID 8) MEMBERS ---")
    count_8 = db['alliance_members'].count_documents({'alliance': 8})
    print(f"Count: {count_8}")
    
    list_8 = db['alliance__alliance_list'].find_one({'$or': [{'id': 8}, {'alliance_id': 8}]})
    print(f"ID 8 in list: {list_8}")

if __name__ == "__main__":
    verify_data()
