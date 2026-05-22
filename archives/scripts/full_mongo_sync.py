import os
import sqlite3
import logging
from pymongo import MongoClient
from dotenv import load_dotenv
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def get_db_path(db_name):
    repo_root = Path(__file__).resolve().parent
    return str(repo_root / "db" / db_name)

def sync_all():
    load_dotenv()
    mongo_uri = os.getenv('MONGO_URI')
    db_name = os.getenv('MONGO_DB_WOS', 'reminderbot')
    
    if not mongo_uri:
        logger.error("MONGO_URI not found in environment")
        return

    # 1. Connect to MongoDB
    try:
        client = MongoClient(mongo_uri)
        db = client[db_name]
        logger.info(f"Connected to MongoDB Primary: {db_name}")
    except Exception as e:
        logger.error(f"Failed to connect to MongoDB: {e}")
        return

    # 2. Sync Alliance List (from alliance.sqlite)
    alliance_db_path = get_db_path('alliance.sqlite')
    if os.path.exists(alliance_db_path):
        logger.info("Syncing alliance list...")
        try:
            with sqlite3.connect(alliance_db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM alliance_list")
                rows = cursor.fetchall()
                
                for row in rows:
                    alliance_id = int(row['alliance_id'])
                    doc = dict(row)
                    db.alliances.update_one({'_id': alliance_id}, {'$set': doc}, upsert=True)
                logger.info(f"Synced {len(rows)} alliances.")
        except Exception as e:
            logger.error(f"Error syncing alliances: {e}")
    else:
        logger.warning(f"Alliance DB not found at {alliance_db_path}")

    # 3. Sync Alliance Members (from users.sqlite)
    users_db_path = get_db_path('users.sqlite')
    if os.path.exists(users_db_path):
        logger.info("Syncing alliance members...")
        try:
            with sqlite3.connect(users_db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM users WHERE fid NOT NULL AND fid != ''")
                rows = cursor.fetchall()
                
                sync_count = 0
                for row in rows:
                    fid = str(row['fid'])
                    doc = dict(row)
                    # Mapping generic field names to consistent ones
                    doc['fid'] = fid
                    if 'alliance' in doc:
                        doc['alliance_id'] = doc['alliance']
                    
                    db.alliance_members.update_one({'_id': fid}, {'$set': doc}, upsert=True)
                    sync_count += 1
                logger.info(f"Synced {sync_count} members.")
        except Exception as e:
            logger.error(f"Error syncing members: {e}")
    else:
        logger.warning(f"Users DB not found at {users_db_path}")

    # 4. Sync Server-Alliance Mappings (from settings.sqlite)
    settings_db_path = get_db_path('settings.sqlite')
    if os.path.exists(settings_db_path):
        logger.info("Syncing server-alliance mappings...")
        try:
            with sqlite3.connect(settings_db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                # Assuming table exists based on previous code sightings
                try:
                    cursor.execute("SELECT guild_id, alliance_id FROM server_alliances")
                    rows = cursor.fetchall()
                    for row in rows:
                        guild_id = str(row['guild_id'])
                        aid = int(row['alliance_id'])
                        db.server_alliances.update_one({'_id': guild_id}, {'$set': {'alliance_id': aid}}, upsert=True)
                    logger.info(f"Synced {len(rows)} server-alliance mappings.")
                except sqlite3.OperationalError:
                    logger.warning("server_alliances table not found in settings.sqlite")
        except Exception as e:
            logger.error(f"Error syncing mappings: {e}")

    client.close()
    logger.info("Sync complete.")

if __name__ == "__main__":
    sync_all()
