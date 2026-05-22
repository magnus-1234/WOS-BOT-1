import os

file_path = 'f:/STARK-whiteout survival bot/DISCORD BOT/db/mongo_adapters.py'

old_method = '''    @staticmethod
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
            return []'''

new_method = '''    @staticmethod
    def get_recent_changes(days: int = 7, alliance_id: Optional[int] = None) -> list:
        try:
            db = _get_db()
            if db is None:
                return []
            
            match_stage = {
                "change_date": {
                    "$gte": datetime.utcnow() - timedelta(days=days)
                }
            }
            
            if alliance_id is not None:
                match_stage["alliance_id"] = int(alliance_id)
            
            pipeline = [
                {
                    "$match": match_stage
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
            return []'''

with open(file_path, 'r') as f:
    content = f.read()

if old_method in content:
    content = content.replace(old_method, new_method)
    with open(file_path, 'w') as f:
        f.write(content)
    print("Successfully updated mongo_adapters.py")
else:
    print("Could not find old method to replace")
