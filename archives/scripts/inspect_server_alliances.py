import os
from pymongo import MongoClient
from dotenv import load_dotenv

def main():
    load_dotenv()
    uri = os.getenv('MONGO_URI')
    if not uri:
        print("MONGO_URI not found in env.")
        return
        
    client = MongoClient(uri)
    db_name = os.getenv('MONGO_DB_MAIN', 'reminderbot')
    db = client[db_name]
    
    print(f"Inspecting collection 'server_alliances' in database '{db_name}':")
    docs = list(db['server_alliances'].find({}))
    for doc in docs:
        print(str(doc).encode('ascii', 'backslashreplace').decode('ascii'))
    client.close()

if __name__ == "__main__":
    main()
