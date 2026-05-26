import os
import sys
import json
from pathlib import Path

# Add project root to path
sys.path.append(os.getcwd())

from dotenv import load_dotenv
load_dotenv()

from db.mongo_adapters import RemindersAdapter, _get_db_reminders

def main():
    try:
        db = _get_db_reminders()
        coll = db[RemindersAdapter.COLL]
        
        # Find stuck recurring reminders
        query = {
            '$or': [{'is_recurring': 1}, {'is_recurring': True}, {'is_recurring': '1'}],
            'is_sent': 1
        }
        
        count = coll.count_documents(query)
        print(f"Found {count} stuck recurring reminders.")
        
        if count > 0:
            result = coll.update_many(query, {'$set': {'is_sent': 0}})
            print(f"Successfully reset {result.modified_count} reminders.")
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
