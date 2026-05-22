import sqlite3
import os
import sys
from pymongo import MongoClient
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def reset_code(code_to_reset):
    code_to_reset = code_to_reset.upper()
    print(f"Resetting code: {code_to_reset}")

    # Reset SQLite
    try:
        conn = sqlite3.connect('db/giftcode.sqlite')
        cursor = conn.cursor()
        cursor.execute("UPDATE gift_codes SET auto_redeem_processed = 0 WHERE UPPER(giftcode) = ?", (code_to_reset,))
        print(f"SQLite: Updated {cursor.rowcount} rows")
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"SQLite Error: {e}")

    # Reset MongoDB
    try:
        # Load MONGO_URI from .env if possible, otherwise use a default or prompt
        mongo_uri = os.getenv('MONGO_URI')
        if not mongo_uri:
            # Try to read from bot's config or similar if you know where it is
            print("MONGO_URI not found in environment. Skipping MongoDB reset.")
        else:
            client = MongoClient(mongo_uri)
            db = client['whiteout_survival_bot']
            result = db['gift_codes'].update_many(
                {'giftcode': {'$regex': f'^{code_to_reset}$', '$options': 'i'}},
                {'$set': {'auto_redeem_processed': False}}
            )
            print(f"MongoDB: Updated {result.modified_count} docs")
    except Exception as e:
        print(f"MongoDB Error: {e}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        reset_code(sys.argv[1])
    else:
        reset_code("CHILDRENSDAY505")
