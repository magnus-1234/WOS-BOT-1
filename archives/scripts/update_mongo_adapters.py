import os
from datetime import datetime

file_path = 'f:/STARK-whiteout survival bot/DISCORD BOT/db/mongo_adapters.py'

# Read content
with open(file_path, 'r') as f:
    content = f.read()

# Update imports
if 'from datetime import datetime' in content and 'timedelta' not in content:
    content = content.replace('from datetime import datetime', 'from datetime import datetime, timedelta')

# Append class if not exists
if 'class FurnaceHistoryAdapter' not in content:
    new_class = '''

class FurnaceHistoryAdapter:
    COLLECTION = 'furnace_history'

    @staticmethod
    def insert(data: Dict[str, Any]) -> bool:
        try:
            db = _get_db()
            if db is None:
                return False
            
            if "change_date" not in data:
                data["change_date"] = datetime.utcnow()
                
            db[FurnaceHistoryAdapter.COLLECTION].insert_one(data)
            return True
        except Exception as e:
            logging.error(f"Error inserting furnace history: {e}")
            return False

    @staticmethod
    def get_recent_changes(days: int = 7) -> list:
        try:
            db = _get_db()
            if db is None:
                return []
            
            pipeline = [
                {
                    "$match": {
                        "change_date": {
                            "$gte": datetime.utcnow() - timedelta(days=days)
                        }
                    }
                },
                {
                    "$group": {
                        "_id": "$fid",
                        "nickname": {"$first": "$nickname"},
                        "total_growth": {"$sum": {"$subtract": ["$new_level", "$old_level"]}}
                    }
                },
                {
                    "$match": {
                        "total_growth": {"$gt": 0}
                    }
                },
                {
                    "$sort": {"total_growth": -1}
                }
            ]
            
            return list(db[FurnaceHistoryAdapter.COLLECTION].aggregate(pipeline))
        except Exception as e:
            logging.error(f"Error fetching furnace history: {e}")
            return []
'''
    content += new_class

# Write back
with open(file_path, 'w') as f:
    f.write(content)

print("Successfully updated mongo_adapters.py")
