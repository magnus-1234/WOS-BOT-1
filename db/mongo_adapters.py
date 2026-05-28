from typing import Optional
import os
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Union
import uuid
from pymongo import UpdateOne

from .mongo_client_wrapper import get_mongo_client_sync, get_mongo_client

logger = logging.getLogger(__name__)


def _get_robust_db(uri_env: str, fallback_env: str, db_name_env: str, default_db: str = 'reminderbot'):
    """Helper to get DB with failover support"""
    primary_uri = os.getenv(uri_env)
    fallback_uri = os.getenv(fallback_env)
    db_name = os.getenv(db_name_env, default_db)
    
    if not primary_uri and not fallback_uri:
        raise ValueError(f"Neither {uri_env} nor {fallback_env} set")

    # Try Primary
    if primary_uri:
        try:
            client = get_mongo_client_sync(primary_uri, serverSelectionTimeoutMS=2000)
            # Connectivity check
            client.admin.command('ping')
            return client[db_name]
        except Exception as e:
            if fallback_uri:
                logger.warning(f"Primary MongoDB ({uri_env}) unreachable, switching to fallback: {e}")
            else:
                logger.error(f"Primary MongoDB ({uri_env}) unreachable and no fallback set: {e}")
                raise

    # Try Fallback
    if fallback_uri:
        try:
            client = get_mongo_client_sync(fallback_uri, serverSelectionTimeoutMS=2000)
            client.admin.command('ping')
            return client[db_name]
        except Exception as e:
            logger.error(f"Fallback MongoDB ({fallback_env}) also unreachable: {e}")
            # If both fail, and we have a primary, return primary to let error propagate normally
            if primary_uri:
                return get_mongo_client_sync(primary_uri)[db_name]
            raise

async def _get_robust_db_async(uri_env: str, fallback_env: str, db_name_env: str, default_db: str = 'reminderbot'):
    """Helper to get DB with failover support asynchronously"""
    primary_uri = os.getenv(uri_env)
    fallback_uri = os.getenv(fallback_env)
    db_name = os.getenv(db_name_env, default_db)
    
    if not primary_uri and not fallback_uri:
        raise ValueError(f"Neither {uri_env} nor {fallback_env} set")

    # Try Primary
    if primary_uri:
        try:
            client = await get_mongo_client(primary_uri, serverSelectionTimeoutMS=2000)
            await client.admin.command('ping')
            return client[db_name]
        except Exception as e:
            if fallback_uri:
                logger.warning(f"Primary MongoDB ({uri_env}) unreachable (async), switching to fallback: {e}")
            else:
                raise

    # Try Fallback
    if fallback_uri:
        try:
            client = await get_mongo_client(fallback_uri, serverSelectionTimeoutMS=2000)
            await client.admin.command('ping')
            return client[db_name]
        except Exception as e:
            logger.error(f"Fallback MongoDB ({fallback_env}) also unreachable (async): {e}")
            if primary_uri:
                client = await get_mongo_client(primary_uri)
                return client[db_name]
            raise

def _get_db_main():
    return _get_robust_db('MONGO_URI', 'MONGO_URI_FALLBACK', 'MONGO_DB_NAME')

async def _get_db_main_async():
    return await _get_robust_db_async('MONGO_URI', 'MONGO_URI_FALLBACK', 'MONGO_DB_NAME')

def _get_db_wos():
    return _get_robust_db('MONGO_URI', 'MONGO_URI_FALLBACK', 'MONGO_DB_WOS')

async def _get_db_wos_async():
    return await _get_robust_db_async('MONGO_URI', 'MONGO_URI_FALLBACK', 'MONGO_DB_WOS')

def _get_db_reminders():
    return _get_robust_db('MONGO_URI', 'MONGO_URI_FALLBACK', 'MONGO_DB_REMINDERS')

async def _get_db_reminders_async():
    return await _get_robust_db_async('MONGO_URI', 'MONGO_URI_FALLBACK', 'MONGO_DB_REMINDERS')


def mongo_enabled() -> bool:
    return bool(os.getenv('MONGO_URI'))


def get_mongo_db():
    """Public function to get MongoDB database instance (Main)"""
    return _get_db_main()

# Alias for generic DB access
_get_db = _get_db_main


class UserTimezonesAdapter:
    COLL = 'user_timezones'

    @staticmethod
    def load_all() -> Dict[str, str]:
        try:
            db = _get_db_main()
            docs = db[UserTimezonesAdapter.COLL].find({})
            return {str(d['_id']): d.get('timezone') for d in docs}
        except Exception as e:
            logger.error(f'Failed to load user_timezones from Mongo: {e}')
            return {}

    @staticmethod
    def get(user_id: str) -> Optional[str]:
        try:
            db = _get_db_main()
            d = db[UserTimezonesAdapter.COLL].find_one({'_id': str(user_id)})
            return d.get('timezone') if d else None
        except Exception as e:
            logger.error(f'Failed to get timezone for {user_id}: {e}')
            return None

    @staticmethod
    def set(user_id: str, tz_abbr: str) -> bool:
        try:
            db = _get_db_main()
            now = datetime.utcnow().isoformat()
            db[UserTimezonesAdapter.COLL].update_one(
                {'_id': str(user_id)},
                {'$set': {'timezone': tz_abbr.lower(), 'updated_at': now}, '$setOnInsert': {'created_at': now}},
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f'Failed to set timezone for {user_id}: {e}')
            return False

    @staticmethod
    async def get_async(user_id: str) -> Optional[str]:
        try:
            db = await _get_db_main_async()
            d = await db[UserTimezonesAdapter.COLL].find_one({'_id': str(user_id)})
            return d.get('timezone') if d else None
        except Exception as e:
            logger.error(f'Failed to get timezone (async) for {user_id}: {e}')
            return None

    @staticmethod
    async def set_async(user_id: str, tz_abbr: str) -> bool:
        try:
            db = await _get_db_main_async()
            now = datetime.utcnow().isoformat()
            await db[UserTimezonesAdapter.COLL].update_one(
                {'_id': str(user_id)},
                {'$set': {'timezone': tz_abbr.lower(), 'updated_at': now}, '$setOnInsert': {'created_at': now}},
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f'Failed to set timezone (async) for {user_id}: {e}')
            return False


class BirthdaysAdapter:
    COLL = 'birthdays'

    @staticmethod
    def load_all() -> dict:
        try:
            db = _get_db_main()
            docs = db[BirthdaysAdapter.COLL].find({})
            result = {}
            for d in docs:
                guild_id = d.get('guild_id')
                user_id = d.get('user_id') or d.get('_id')
                if guild_id:
                    key = f"{guild_id}_{user_id}"
                else:
                    key = str(user_id)
                result[key] = {
                    'day': int(d.get('day')),
                    'month': int(d.get('month')),
                    'player_id': d.get('player_id')
                }
            return result
        except Exception as e:
            logger.error(f'Failed to load birthdays from Mongo: {e}')
            return {}

    @staticmethod
    def get(guild_id, user_id) -> dict:
        try:
            db = _get_db_main()
            query = {'user_id': int(user_id)}
            if guild_id:
                query['guild_id'] = int(guild_id)
            else:
                query = {'_id': str(user_id)}
            
            d = db[BirthdaysAdapter.COLL].find_one(query)
            if not d:
                if guild_id:
                    d = db[BirthdaysAdapter.COLL].find_one({'_id': f"{guild_id}_{user_id}"})
                if not d:
                    return None
            
            d.pop('_id', None)
            return d
        except Exception as e:
            logger.error(f'Failed to get birthday for {user_id}: {e}')
            return None

    @staticmethod
    def set(guild_id, user_id, day: int, month: int, player_id=None) -> bool:
        try:
            db = _get_db_main()
            data = {
                'day': int(day),
                'month': int(month),
                'user_id': int(user_id),
                'updated_at': datetime.utcnow().isoformat()
            }
            if guild_id:
                data['guild_id'] = int(guild_id)
                doc_id = f"{guild_id}_{user_id}"
            else:
                doc_id = str(user_id)

            if player_id:
                data['player_id'] = str(player_id)
                
            db[BirthdaysAdapter.COLL].update_one(
                {'_id': doc_id},
                {'$set': data, '$setOnInsert': {'created_at': datetime.utcnow().isoformat()}},
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f'Failed to set birthday for {user_id}: {e}')
            return False

    @staticmethod
    def remove(guild_id, user_id) -> bool:
        try:
            db = _get_db_main()
            if guild_id:
                query = {'_id': f"{guild_id}_{user_id}"}
            else:
                query = {'_id': str(user_id)}
            res = db[BirthdaysAdapter.COLL].delete_one(query)
            return res.deleted_count > 0
        except Exception as e:
            logger.error(f'Failed to remove birthday for {user_id}: {e}')
            return False

    @staticmethod
    async def load_all_async() -> dict:
        try:
            db = await _get_db_main_async()
            result = {}
            async for d in db[BirthdaysAdapter.COLL].find({}):
                guild_id = d.get('guild_id')
                user_id = d.get('user_id') or d.get('_id')
                if guild_id:
                    key = f"{guild_id}_{user_id}"
                else:
                    key = str(user_id)
                result[key] = {
                    'day': int(d.get('day')),
                    'month': int(d.get('month')),
                    'player_id': d.get('player_id')
                }
            return result
        except Exception as e:
            logger.error(f'Failed to load birthdays (async) from Mongo: {e}')
            return {}

    @staticmethod
    async def get_async(guild_id, user_id) -> dict:
        try:
            db = await _get_db_main_async()
            query = {'user_id': int(user_id)}
            if guild_id:
                query['guild_id'] = int(guild_id)
            else:
                query = {'_id': str(user_id)}
            
            d = await db[BirthdaysAdapter.COLL].find_one(query)
            if not d:
                if guild_id:
                    d = await db[BirthdaysAdapter.COLL].find_one({'_id': f"{guild_id}_{user_id}"})
                if not d:
                    return None
            
            d.pop('_id', None)
            return d
        except Exception as e:
            logger.error(f'Failed to get birthday (async) for {user_id}: {e}')
            return None

    @staticmethod
    async def set_async(guild_id, user_id, day: int, month: int, player_id=None) -> bool:
        try:
            db = await _get_db_main_async()
            data = {
                'day': int(day),
                'month': int(month),
                'user_id': int(user_id),
                'updated_at': datetime.utcnow().isoformat()
            }
            if guild_id:
                data['guild_id'] = int(guild_id)
                doc_id = f"{guild_id}_{user_id}"
            else:
                doc_id = str(user_id)

            if player_id:
                data['player_id'] = str(player_id)
                
            await db[BirthdaysAdapter.COLL].update_one(
                {'_id': doc_id},
                {'$set': data, '$setOnInsert': {'created_at': datetime.utcnow().isoformat()}},
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f'Failed to set birthday (async) for {user_id}: {e}')
            return False

    @staticmethod
    async def remove_async(guild_id, user_id) -> bool:
        try:
            db = await _get_db_main_async()
            if guild_id:
                query = {'_id': f"{guild_id}_{user_id}"}
            else:
                query = {'_id': str(user_id)}
            res = await db[BirthdaysAdapter.COLL].delete_one(query)
            return res.deleted_count > 0
        except Exception as e:
            logger.error(f'Failed to remove birthday (async) for {user_id}: {e}')
            return False


class BirthdayChannelAdapter:
    COLL = 'birthday_channels'

    @staticmethod
    def get(guild_id) -> int:
        try:
            db = _get_db_main()
            d = db[BirthdayChannelAdapter.COLL].find_one({'_id': str(guild_id)})
            if d:
                return d.get('channel_id')
            return None
        except Exception as e:
            logger.error(f'Failed to get birthday channel for guild {guild_id}: {e}')
            return None

    @staticmethod
    def set(guild_id, channel_id) -> bool:
        try:
            db = _get_db_main()
            db[BirthdayChannelAdapter.COLL].update_one(
                {'_id': str(guild_id)},
                {'$set': {'channel_id': int(channel_id)}},
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f'Failed to set birthday channel for guild {guild_id}: {e}')
            return False

    @staticmethod
    def remove(guild_id) -> bool:
        try:
            db = _get_db_main()
            res = db[BirthdayChannelAdapter.COLL].delete_one({'_id': str(guild_id)})
            return res.deleted_count > 0
        except Exception as e:
            logger.error(f'Failed to remove birthday channel for guild {guild_id}: {e}')
            return False

    @staticmethod
    async def get_async(guild_id) -> int:
        try:
            db = await _get_db_main_async()
            d = await db[BirthdayChannelAdapter.COLL].find_one({'_id': str(guild_id)})
            if d:
                return d.get('channel_id')
            return None
        except Exception as e:
            logger.error(f'Failed to get birthday channel (async) for guild {guild_id}: {e}')
            return None

    @staticmethod
    async def set_async(guild_id, channel_id) -> bool:
        try:
            db = await _get_db_main_async()
            await db[BirthdayChannelAdapter.COLL].update_one(
                {'_id': str(guild_id)},
                {'$set': {'channel_id': int(channel_id)}},
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f'Failed to set birthday channel (async) for guild {guild_id}: {e}')
            return False

    @staticmethod
    async def remove_async(guild_id) -> bool:
        try:
            db = await _get_db_main_async()
            res = await db[BirthdayChannelAdapter.COLL].delete_one({'_id': str(guild_id)})
            return res.deleted_count > 0
        except Exception as e:
            logger.error(f'Failed to remove birthday channel (async) for guild {guild_id}: {e}')
            return False


class UserProfilesAdapter:
    COLL = 'user_profiles'

    @staticmethod
    def load_all() -> Dict[str, Any]:
        try:
            db = _get_db_main()
            docs = db[UserProfilesAdapter.COLL].find({})
            result = {}
            for d in docs:
                data = d.copy()
                data.pop('_id', None)
                result[str(d['_id'])] = data
            return result
        except Exception as e:
            logger.error(f'Failed to load user profiles from Mongo: {e}')
            return {}

    @staticmethod
    def get(user_id: str) -> Optional[Dict[str, Any]]:
        try:
            db = _get_db_main()
            d = db[UserProfilesAdapter.COLL].find_one({'_id': str(user_id)})
            if not d:
                return None
            d.pop('_id', None)
            return d
        except Exception as e:
            logger.error(f'Failed to get profile for {user_id}: {e}')
            return None

    @staticmethod
    def set(user_id: str, data: Dict[str, Any]) -> bool:
        try:
            db = _get_db_main()
            now = datetime.utcnow().isoformat()
            payload = data.copy()
            # Avoid conflicts where payload already contains 'created_at' which
            # would clash with our $setOnInsert created_at below.
            payload.pop('created_at', None)
            payload['updated_at'] = now
            db[UserProfilesAdapter.COLL].update_one({'_id': str(user_id)}, {'$set': payload, '$setOnInsert': {'created_at': now}}, upsert=True)
            return True
        except Exception as e:
            logger.error(f'Failed to set profile for {user_id}: {e}')
            return False

    @staticmethod
    async def get_async(user_id: str) -> Optional[Dict[str, Any]]:
        try:
            db = await _get_db_main_async()
            d = await db[UserProfilesAdapter.COLL].find_one({'_id': str(user_id)})
            if not d:
                return None
            d.pop('_id', None)
            return d
        except Exception as e:
            logger.error(f'Failed to get profile (async) for {user_id}: {e}')
            return None

    @staticmethod
    async def set_async(user_id: str, data: Dict[str, Any]) -> bool:
        try:
            db = await _get_db_main_async()
            now = datetime.utcnow().isoformat()
            payload = data.copy()
            payload.pop('created_at', None)
            payload['updated_at'] = now
            await db[UserProfilesAdapter.COLL].update_one({'_id': str(user_id)}, {'$set': payload, '$setOnInsert': {'created_at': now}}, upsert=True)
            return True
        except Exception as e:
            logger.error(f'Failed to set profile (async) for {user_id}: {e}')
            return False


class GiftcodeStateAdapter:
    COLL = 'giftcode_state'

    @staticmethod
    def get_state() -> Dict[str, Any]:
        try:
            db = _get_db_main()
            d = db[GiftcodeStateAdapter.COLL].find_one({'_id': 'giftcode_state'})
            if not d:
                return {}
            d.pop('_id', None)
            return d
        except Exception as e:
            logger.error(f'Failed to get giftcode state from Mongo: {e}')
            return {}

    @staticmethod
    def set_state(state: Dict[str, Any]) -> bool:
        try:
            db = _get_db_main()
            now = datetime.utcnow().isoformat()
            payload = state.copy()
            # Remove created_at from payload to avoid $set vs $setOnInsert conflict
            payload.pop('created_at', None)
            payload['updated_at'] = now
            db[GiftcodeStateAdapter.COLL].update_one({'_id': 'giftcode_state'}, {'$set': payload, '$setOnInsert': {'created_at': now}}, upsert=True)
            return True
        except Exception as e:
            logger.error(f'Failed to set giftcode state in Mongo: {e}')
            return False

    @staticmethod
    async def get_state_async() -> Dict[str, Any]:
        try:
            db = await _get_db_main_async()
            d = await db[GiftcodeStateAdapter.COLL].find_one({'_id': 'giftcode_state'})
            if not d:
                return {}
            d.pop('_id', None)
            return d
        except Exception as e:
            logger.error(f'Failed to get giftcode state (async) from Mongo: {e}')
            return {}

    @staticmethod
    async def set_state_async(state: Dict[str, Any]) -> bool:
        try:
            db = await _get_db_main_async()
            now = datetime.utcnow().isoformat()
            payload = state.copy()
            payload.pop('created_at', None)
            payload['updated_at'] = now
            await db[GiftcodeStateAdapter.COLL].update_one({'_id': 'giftcode_state'}, {'$set': payload, '$setOnInsert': {'created_at': now}}, upsert=True)
            return True
        except Exception as e:
            logger.error(f'Failed to set giftcode state (async) in Mongo: {e}')
            return False



class RemindersAdapter:
    """Adapter for managing reminders in MongoDB"""
    COLL = 'reminders'

    @staticmethod
    def add_reminder(data: Dict[str, Any]) -> str:
        """Add a new reminder"""
        try:
            db = _get_db_reminders()
            now = datetime.utcnow().isoformat()
            
            # Ensure required fields
            data['created_at'] = data.get('created_at', now)
            data['is_active'] = 1
            data['is_sent'] = 0
            
            # Check for duplicate active reminder
            existing = db[RemindersAdapter.COLL].find_one({
                'user_id': data['user_id'],
                'channel_id': data['channel_id'],
                'reminder_time': data['reminder_time'],
                'message': data['message'],
                'is_active': 1,
                'is_sent': 0
            })
            
            if existing:
                # Update existing
                updates = {k: v for k, v in data.items() if v is not None and k not in ['_id', 'user_id', 'channel_id', 'reminder_time', 'message', 'created_at']}
                if updates:
                    db[RemindersAdapter.COLL].update_one({'_id': existing['_id']}, {'$set': updates})
                return str(existing['_id'])
            
            # Insert new
            # Generate a pseudo-unique int ID for compatibility
            import time
            import random
            reminder_id = int(time.time() * 1000) + random.randint(0, 999)
            data['_id'] = reminder_id
            
            db[RemindersAdapter.COLL].insert_one(data)
            return reminder_id
        except Exception as e:
            logger.error(f'Failed to add reminder to Mongo: {e}')
            return -1

    @staticmethod
    async def add_reminder_async(data: Dict[str, Any]) -> str:
        """Add a new reminder asynchronously"""
        try:
            db = await _get_db_reminders_async()
            now = datetime.utcnow().isoformat()
            data['created_at'] = data.get('created_at', now)
            data['is_active'] = 1
            data['is_sent'] = 0
            
            existing = await db[RemindersAdapter.COLL].find_one({
                'user_id': data['user_id'],
                'channel_id': data['channel_id'],
                'reminder_time': data['reminder_time'],
                'message': data['message'],
                'is_active': 1,
                'is_sent': 0
            })
            
            if existing:
                updates = {k: v for k, v in data.items() if v is not None and k not in ['_id', 'user_id', 'channel_id', 'reminder_time', 'message', 'created_at']}
                if updates:
                    await db[RemindersAdapter.COLL].update_one({'_id': existing['_id']}, {'$set': updates})
                return str(existing['_id'])
            
            import time
            import random
            reminder_id = int(time.time() * 1000) + random.randint(0, 999)
            data['_id'] = reminder_id
            
            await db[RemindersAdapter.COLL].insert_one(data)
            return reminder_id
        except Exception as e:
            logger.error(f'Failed to add reminder (async) to Mongo: {e}')
            return -1

    @staticmethod
    def get_due_reminders() -> List[Dict[str, Any]]:
        """Get all active reminders that are due"""
        try:
            db = _get_db_reminders()
            now = datetime.utcnow().isoformat()
            
            cursor = db[RemindersAdapter.COLL].find({
                '$and': [
                    {'$or': [{'is_active': 1}, {'is_active': True}, {'is_active': '1'}, {'is_active': 'true'}, {'is_active': 'True'}, {'is_active': {'$exists': False}}]},
                    {'$or': [{'is_sent': 0}, {'is_sent': False}, {'is_sent': '0'}, {'is_sent': 'false'}, {'is_sent': 'False'}, {'is_sent': {'$exists': False}}]},
                ],
                'reminder_time': {'$lte': now}
            }).sort('reminder_time', 1)
            
            docs = list(cursor)
            for doc in docs:
                # Ensure _id is serializable
                if '_id' in doc and not isinstance(doc['_id'], (str, int, float)):
                    doc['_id'] = str(doc['_id'])
            return docs
        except Exception as e:
            logger.error(f'Failed to get due reminders from Mongo: {e}')
            return []

    @staticmethod
    def mark_reminder_sent(reminder_id: Union[int, str]) -> bool:
        """Mark a reminder as sent"""
        try:
            db = _get_db_reminders()
            try:
                if isinstance(reminder_id, str) and reminder_id.isdigit():
                    reminder_id = int(reminder_id)
            except:
                pass
                
            res = db[RemindersAdapter.COLL].update_one(
                {'_id': reminder_id, 'is_sent': 0},
                {'$set': {'is_sent': 1}}
            )
            return res.modified_count > 0
        except Exception as e:
            logger.error(f'Failed to mark reminder {reminder_id} sent in Mongo: {e}')
            return False

    @staticmethod
    def update_reminder_fields(reminder_id: Union[int, str], fields: Dict[str, Any]) -> bool:
        """Update arbitrary fields"""
        try:
            db = _get_db_reminders()
            try:
                if isinstance(reminder_id, str) and reminder_id.isdigit():
                    reminder_id = int(reminder_id)
            except:
                pass

            allowed = {
                'image_url', 'thumbnail_url', 'body', 'footer_text', 'footer_icon_url',
                'mention', 'reminder_time', 'author_url', 'message', 'channel_id',
                'is_recurring', 'recurrence_type', 'recurrence_interval', 'recurrence_days',
                'original_time_pattern', 'is_active', 'is_sent'
            }
            updates = {k: v for k, v in fields.items() if k in allowed}
            
            if not updates:
                return False
                
            res = db[RemindersAdapter.COLL].update_one(
                {'_id': reminder_id},
                {'$set': updates}
            )
            return res.modified_count > 0
        except Exception as e:
            logger.error(f'Failed to update reminder {reminder_id} in Mongo: {e}')
            return False

    @staticmethod
    def get_user_reminders(user_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Get active reminders for a user"""
        try:
            db = _get_db_reminders()
            cursor = db[RemindersAdapter.COLL].find({
                'user_id': str(user_id),
                '$and': [
                    {'$or': [{'is_active': 1}, {'is_active': True}, {'is_active': '1'}, {'is_active': 'true'}, {'is_active': 'True'}, {'is_active': {'$exists': False}}]},
                    {'$or': [{'is_sent': 0}, {'is_sent': False}, {'is_sent': '0'}, {'is_sent': 'false'}, {'is_sent': 'False'}, {'is_sent': {'$exists': False}}]},
                ],
            }).sort('reminder_time', 1).limit(limit)
            docs = list(cursor)
            for doc in docs:
                if '_id' in doc and not isinstance(doc['_id'], (str, int, float)):
                    doc['_id'] = str(doc['_id'])
            return docs
        except Exception as e:
            logger.error(f'Failed to get reminders for {user_id} from Mongo: {e}')
            return []

    @staticmethod
    def delete_reminder(reminder_id: Union[int, str], user_id: str) -> bool:
        """Delete (deactivate) a reminder"""
        try:
            db = _get_db_reminders()
            try:
                if isinstance(reminder_id, str) and reminder_id.isdigit():
                    reminder_id = int(reminder_id)
            except:
                pass

            res = db[RemindersAdapter.COLL].update_one(
                {'_id': reminder_id, 'user_id': str(user_id), 'is_active': 1},
                {'$set': {'is_active': 0}}
            )
            return res.modified_count > 0
        except Exception as e:
            logger.error(f'Failed to delete reminder {reminder_id} in Mongo: {e}')
            return False

    @staticmethod
    def get_all_active_reminders() -> List[Dict[str, Any]]:
        """Get ALL active reminders (admin)"""
        try:
            db = _get_db_reminders()
            cursor = db[RemindersAdapter.COLL].find({
                '$and': [
                    {'$or': [{'is_active': 1}, {'is_active': True}, {'is_active': '1'}, {'is_active': 'true'}, {'is_active': 'True'}, {'is_active': {'$exists': False}}]},
                    {'$or': [{'is_sent': 0}, {'is_sent': False}, {'is_sent': '0'}, {'is_sent': 'false'}, {'is_sent': 'False'}, {'is_sent': {'$exists': False}}]},
                ],
            }).sort('reminder_time', 1)
            docs = list(cursor)
            for doc in docs:
                if '_id' in doc and not isinstance(doc['_id'], (str, int, float)):
                    doc['_id'] = str(doc['_id'])
            return docs
        except Exception as e:
            logger.error(f'Failed to get all active reminders from Mongo: {e}')
            return []


# ============================================================================
# ALLIANCE DATA ADAPTERS - For storing all alliance member info
# ============================================================================

class AllianceMembersAdapter:
    """Stores alliance members with all their data (player IDs, levels, etc.)"""
    COLL = 'alliance_members'

    @staticmethod
    def upsert_member(fid: str, data: Dict[str, Any]) -> bool:
        """Insert or update a single alliance member"""
        try:
            db = _get_db_wos()
            now = datetime.utcnow().isoformat()
            
            # Ensure _id is string fid
            data_copy = data.copy()
            data_copy['updated_at'] = now
            data_copy.pop('created_at', None)
            
            db[AllianceMembersAdapter.COLL].update_one(
                {'_id': str(fid)},
                {'$set': data_copy, '$setOnInsert': {'created_at': now}},
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f'Failed to upsert alliance member {fid} in Mongo: {e}')
            return False

    @staticmethod
    async def upsert_member_async(fid: str, data: Dict[str, Any]) -> bool:
        """Insert or update a single alliance member asynchronously"""
        try:
            db = await _get_db_wos_async()
            now = datetime.utcnow().isoformat()
            
            data_copy = data.copy()
            data_copy['updated_at'] = now
            data_copy.pop('created_at', None)
            
            await db[AllianceMembersAdapter.COLL].update_one(
                {'_id': str(fid)},
                {'$set': data_copy, '$setOnInsert': {'created_at': now}},
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f'Failed to upsert alliance member (async) {fid} in Mongo: {e}')
            return False

    @staticmethod
    async def get_all_members_async() -> list:
        """Get all alliance members asynchronously"""
        try:
            db = await _get_db_wos_async()
            cursor = db[AllianceMembersAdapter.COLL].find({})
            docs = await cursor.to_list(length=None)
            for doc in docs:
                doc.pop('_id', None)
            return docs
        except Exception as e:
            logger.error(f'Failed to get all alliance members (async) from Mongo: {e}')
            return []

    @staticmethod
    async def count_members_async() -> int:
        """Count monitored alliance members without loading every document."""
        try:
            db = await _get_db_wos_async()
            return int(await db[AllianceMembersAdapter.COLL].count_documents({}))
        except Exception as e:
            logger.error(f'Failed to count alliance members (async) from Mongo: {e}')
            return 0

    @staticmethod
    async def get_members_by_alliance_async(alliance_id: int) -> list:
        """Get all members for a specific alliance directly by alliance_id (targeted query).
        
        Checks both 'alliance' and 'alliance_id' field names to handle legacy documents.
        This is far more efficient than get_all_members_async() + Python filtering.
        """
        try:
            db = await _get_db_wos_async()
            cursor = db[AllianceMembersAdapter.COLL].find({
                '$or': [
                    {'alliance': int(alliance_id)},
                    {'alliance_id': int(alliance_id)}
                ]
            })
            docs = await cursor.to_list(length=None)
            for doc in docs:
                doc.pop('_id', None)
            return docs
        except Exception as e:
            logger.error(f'Failed to get alliance members for alliance {alliance_id} (async) from Mongo: {e}')
            return []

    @staticmethod
    async def get_recent_members_async(limit: int = 80) -> list:
        """Get recently checked/updated members for the public status feed."""
        try:
            db = await _get_db_wos_async()
            safe_limit = max(1, min(int(limit), 200))
            projection = {
                '_id': 0,
                'fid': 1,
                'nickname': 1,
                'furnace_lv': 1,
                'avatar_image': 1,
                'state_id': 1,
                'alliance_id': 1,
                'alliance': 1,
                'last_checked': 1,
                'updated_at': 1,
            }
            cursor = db[AllianceMembersAdapter.COLL].find({}, projection).sort('updated_at', -1).limit(safe_limit)
            return await cursor.to_list(length=None)
        except Exception as e:
            logger.error(f'Failed to get recent alliance members (async) from Mongo: {e}')
            return []

    @staticmethod
    def get_member(fid: str) -> Optional[Dict[str, Any]]:
        """Get a single alliance member"""
        try:
            db = _get_db_wos()
            doc = db[AllianceMembersAdapter.COLL].find_one({'_id': str(fid)})
            if doc:
                doc.pop('_id', None)  # Remove MongoDB _id
            return doc
        except Exception as e:
            logger.error(f'Failed to get alliance member {fid} from Mongo: {e}')
            return None

    @staticmethod
    async def get_member_async(fid: str) -> Optional[Dict[str, Any]]:
        """Get a single alliance member asynchronously"""
        try:
            db = await _get_db_wos_async()
            doc = await db[AllianceMembersAdapter.COLL].find_one({'_id': str(fid)})
            if doc:
                doc.pop('_id', None)
            return doc
        except Exception as e:
            logger.error(f'Failed to get alliance member (async) {fid} from Mongo: {e}')
            return None

    @staticmethod
    def get_all_members() -> list:
        """Get all alliance members"""
        try:
            db = _get_db_wos()
            docs = list(db[AllianceMembersAdapter.COLL].find({}))
            for doc in docs:
                doc.pop('_id', None)  # Remove MongoDB _id
            return docs
        except Exception as e:
            logger.error(f'Failed to get all alliance members from Mongo: {e}')
            return []

    @staticmethod
    def delete_member(fid: str) -> bool:
        """Delete a single alliance member"""
        try:
            db = _get_db_wos()
            result = db[AllianceMembersAdapter.COLL].delete_one({'_id': str(fid)})
            return result.deleted_count > 0
        except Exception as e:
            logger.error(f'Failed to delete alliance member {fid} from Mongo: {e}')
            return False

    @staticmethod
    def clear_all() -> bool:
        """Clear all alliance members"""
        try:
            db = _get_db_wos()
            db[AllianceMembersAdapter.COLL].delete_many({})
            logger.info('[Mongo] Cleared all alliance members')
            return True
        except Exception as e:
            logger.error(f'Failed to clear alliance members from Mongo: {e}')
            return False

    @staticmethod
    async def delete_member_async(fid: str) -> bool:
        """Delete a single alliance member asynchronously"""
        try:
            db = await _get_db_wos_async()
            result = await db[AllianceMembersAdapter.COLL].delete_one({'_id': str(fid)})
            return result.deleted_count > 0
        except Exception as e:
            logger.error(f'Failed to delete alliance member (async) {fid} from Mongo: {e}')
            return False

    @staticmethod
    async def clear_all_async() -> bool:
        """Clear all alliance members asynchronously"""
        try:
            db = await _get_db_wos_async()
            await db[AllianceMembersAdapter.COLL].delete_many({})
            logger.info('[Mongo] Cleared all alliance members (async)')
            return True
        except Exception as e:
            logger.error(f'Failed to clear alliance members (async) from Mongo: {e}')
            return False


class AllianceMetadataAdapter:
    """Stores alliance metadata (settings, config, etc.)"""
    COLL = 'alliance_metadata'

    @staticmethod
    def set_metadata(key: str, value: Any) -> bool:
        """Set alliance metadata"""
        try:
            db = _get_db_main()
            now = datetime.utcnow().isoformat()
            
            db[AllianceMetadataAdapter.COLL].update_one(
                {'_id': str(key)},
                {'$set': {'value': value, 'updated_at': now}, '$setOnInsert': {'created_at': now}},
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f'Failed to set alliance metadata {key}: {e}')
            return False

    @staticmethod
    def get_metadata(key: str) -> Optional[Any]:
        """Get alliance metadata"""
        try:
            db = _get_db_main()
            doc = db[AllianceMetadataAdapter.COLL].find_one({'_id': str(key)})
            return doc.get('value') if doc else None
        except Exception as e:
            logger.error(f'Failed to get alliance metadata {key}: {e}')
            return None

    @staticmethod
    async def set_metadata_async(key: str, value: Any) -> bool:
        """Set alliance metadata asynchronously"""
        try:
            db = await _get_db_main_async()
            now = datetime.utcnow().isoformat()
            await db[AllianceMetadataAdapter.COLL].update_one(
                {'_id': str(key)},
                {'$set': {'value': value, 'updated_at': now}, '$setOnInsert': {'created_at': now}},
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f'Failed to set alliance metadata (async) {key}: {e}')
            return False


class GiftCodesAdapter:
    """Adapter for managing gift codes in MongoDB (for gift_operationsapi cog)"""
    COLL = 'gift_codes'

    @staticmethod
    def get_all():
        """Get all gift codes as list of tuples: (code, date, validation_status)"""
        try:
            db = _get_db_wos()
            docs = db[GiftCodesAdapter.COLL].find({})
            # Filter out docs and ensure string types
            results = []
            for d in docs:
                _id = d.get('_id')
                if _id:
                    # Always ensure _id is a string (could be ObjectId)
                    results.append((str(_id), d.get('date'), d.get('validation_status')))
            return results
        except Exception as e:
            logger.error(f'Failed to get all gift codes from Mongo: {e}')
            return []

    @staticmethod
    async def get_all_async():
        """Get all gift codes asynchronously"""
        try:
            db = await _get_db_wos_async()
            cursor = db[GiftCodesAdapter.COLL].find({})
            docs = await cursor.to_list(length=None)
            results = []
            for d in docs:
                _id = d.get('_id')
                if _id:
                    results.append((str(_id), d.get('date'), d.get('validation_status')))
            return results
        except Exception as e:
            logger.error(f'Failed to get all gift codes (async) from Mongo: {e}')
            return []

    @staticmethod
    def insert(code: str, date: str, validation_status: str = 'pending') -> bool:
        """Insert a new gift code (ignores if already exists)"""
        try:
            db = _get_db_wos()
            db[GiftCodesAdapter.COLL].update_one(
                {'_id': code},
                {'$set': {'date': date, 'validation_status': validation_status, 'created_at': datetime.utcnow().isoformat()}},
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f'Failed to insert gift code {code}: {e}')
            return False

    @staticmethod
    def update_status(code: str, validation_status: str) -> bool:
        """Update validation status of a gift code"""
        try:
            db = _get_db_wos()
            db[GiftCodesAdapter.COLL].update_one(
                {'_id': code},
                {'$set': {'validation_status': validation_status, 'updated_at': datetime.utcnow().isoformat()}}
            )
            return True
        except Exception as e:
            logger.error(f'Failed to update status for {code}: {e}')
            return False

    @staticmethod
    def delete(code: str) -> bool:
        """Delete a gift code"""
        try:
            db = _get_db_wos()
            result = db[GiftCodesAdapter.COLL].delete_one({'_id': code})
            return result.deleted_count > 0
        except Exception as e:
            logger.error(f'Failed to delete gift code {code}: {e}')
            return False

    @staticmethod
    def clear_all() -> bool:
        """Clear all gift codes (use with caution)"""
        try:
            db = _get_db_wos()
            db[GiftCodesAdapter.COLL].delete_many({})
            return True
        except Exception as e:
            logger.error(f'Failed to clear all gift codes: {e}')
            return False

    @staticmethod
    async def insert_async(code: str, date: str, validation_status: str = 'pending') -> bool:
        """Insert a new gift code asynchronously"""
        try:
            db = await _get_db_wos_async()
            await db[GiftCodesAdapter.COLL].update_one(
                {'_id': code},
                {'$set': {'date': date, 'validation_status': validation_status, 'created_at': datetime.utcnow().isoformat()}},
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f'Failed to insert gift code (async) {code}: {e}')
            return False

    @staticmethod
    async def update_status_async(code: str, validation_status: str) -> bool:
        """Update validation status of a gift code asynchronously"""
        try:
            db = await _get_db_wos_async()
            await db[GiftCodesAdapter.COLL].update_one(
                {'_id': code},
                {'$set': {'validation_status': validation_status, 'updated_at': datetime.utcnow().isoformat()}}
            )
            return True
        except Exception as e:
            logger.error(f'Failed to update status (async) for {code}: {e}')
            return False

    @staticmethod
    async def delete_async(code: str) -> bool:
        """Delete a gift code asynchronously"""
        try:
            db = await _get_db_wos_async()
            result = await db[GiftCodesAdapter.COLL].delete_one({'_id': code})
            return result.deleted_count > 0
        except Exception as e:
            logger.error(f'Failed to delete gift code (async) {code}: {e}')
            return False

    @staticmethod
    async def clear_all_async() -> bool:
        """Clear all gift codes asynchronously"""
        try:
            db = await _get_db_wos_async()
            await db[GiftCodesAdapter.COLL].delete_many({})
            return True
        except Exception as e:
            logger.error(f'Failed to clear all gift codes (async): {e}')
            return False

    @staticmethod
    def get_code(code: str) -> Optional[Dict[str, Any]]:
        """Get a single gift code with all its fields"""
        try:
            db = _get_db_wos()
            doc = db[GiftCodesAdapter.COLL].find_one({'_id': code})
            if doc:
                return {
                    'giftcode': doc.get('_id'),
                    'giftcode_original': doc.get('giftcode_original', doc.get('_id')),
                    'date': doc.get('date'),
                    'validation_status': doc.get('validation_status'),
                    'auto_redeem_processed': doc.get('auto_redeem_processed', False),
                    'created_at': doc.get('created_at'),
                    'updated_at': doc.get('updated_at')
                }
            return None
        except Exception as e:
            logger.error(f'Failed to get gift code {code}: {e}')
            return None

    @staticmethod
    def get_all_with_status() -> List[Dict[str, Any]]:
        """Get all gift codes with their auto_redeem_processed status"""
        try:
            db = _get_db_wos()
            docs = db[GiftCodesAdapter.COLL].find({})
            results = []
            for d in docs:
                _id = d.get('_id')
                if _id:
                    results.append({
                        'giftcode': str(_id),
                        'giftcode_original': d.get('giftcode_original', str(_id)),
                        'date': d.get('date'),
                        'validation_status': d.get('validation_status'),
                        'auto_redeem_processed': d.get('auto_redeem_processed', False),
                        'created_at': d.get('created_at'),
                        'updated_at': d.get('updated_at')
                    })
            return results
        except Exception as e:
            logger.error(f'Failed to get all gift codes with status: {e}')
            return []

    @staticmethod
    async def get_code_async(code: str) -> Optional[Dict[str, Any]]:
        """Get a single gift code asynchronously"""
        try:
            db = await _get_db_wos_async()
            doc = await db[GiftCodesAdapter.COLL].find_one({'_id': code})
            if doc:
                return {
                    'giftcode': doc.get('_id'),
                    'giftcode_original': doc.get('giftcode_original', doc.get('_id')),
                    'date': doc.get('date'),
                    'validation_status': doc.get('validation_status'),
                    'auto_redeem_processed': doc.get('auto_redeem_processed', False),
                    'created_at': doc.get('created_at'),
                    'updated_at': doc.get('updated_at')
                }
            return None
        except Exception as e:
            logger.error(f'Failed to get gift code (async) {code}: {e}')
            return None

    @staticmethod
    async def get_all_with_status_async() -> List[Dict[str, Any]]:
        """Get all gift codes with their auto_redeem_processed status asynchronously"""
        try:
            db = await _get_db_wos_async()
            cursor = db[GiftCodesAdapter.COLL].find({})
            docs = await cursor.to_list(length=None)
            results = []
            for d in docs:
                _id = d.get('_id')
                if _id:
                    results.append({
                        'giftcode': str(_id),
                        'giftcode_original': d.get('giftcode_original', str(_id)),
                        'date': d.get('date'),
                        'validation_status': d.get('validation_status'),
                        'auto_redeem_processed': d.get('auto_redeem_processed', False),
                        'created_at': d.get('created_at'),
                        'updated_at': d.get('updated_at')
                    })
            return results
        except Exception as e:
            logger.error(f'Failed to get all gift codes with status (async): {e}')
            return []

    @staticmethod
    def mark_code_processed(code: str) -> bool:
        """Mark a gift code as processed for auto-redeem"""
        try:
            db = _get_db_wos()
            result = db[GiftCodesAdapter.COLL].update_one(
                {'_id': code},
                {
                    '$set': {
                        'auto_redeem_processed': True,
                        'updated_at': datetime.utcnow().isoformat()
                    }
                }
            )
            return result.modified_count > 0 or result.matched_count > 0
        except Exception as e:
            logger.error(f'Failed to mark code {code} as processed: {e}')
            return False

    @staticmethod
    async def mark_code_processed_async(code: str) -> bool:
        """Mark a gift code as processed for auto-redeem asynchronously"""
        try:
            db = await _get_db_wos_async()
            result = await db[GiftCodesAdapter.COLL].update_one(
                {'_id': code},
                {
                    '$set': {
                        'auto_redeem_processed': True,
                        'updated_at': datetime.utcnow().isoformat()
                    }
                }
            )
            return result.modified_count > 0 or result.matched_count > 0
        except Exception as e:
            logger.error(f'Failed to mark code (async) {code} as processed: {e}')
            return False

    @staticmethod
    def reset_code_processed(code: str) -> bool:
        """Reset auto_redeem_processed to False, allowing the code to be re-processed (sync)"""
        try:
            # Check both Main and WOS databases for robustness
            dbs = []
            try: dbs.append(_get_db_main())
            except: pass
            try: dbs.append(_get_db_wos())
            except: pass
            
            # De-duplicate databases (some might point to same one)
            unique_dbs = []
            seen_db_names = set()
            for db in dbs:
                if db is not None and db.name not in seen_db_names:
                    unique_dbs.append(db)
                    seen_db_names.add(db.name)

            matched_any = False
            for db in unique_dbs:
                # Robust match: check _id, or any custom 'giftcode' field
                # Also try uppercase for robustness
                code_upper = str(code).strip().upper()
                code_raw = str(code).strip()
                
                result = db[GiftCodesAdapter.COLL].update_many(
                    {'$or': [
                        {'_id': code_raw}, {'giftcode': code_raw},
                        {'_id': code_upper}, {'giftcode': code_upper}
                    ]},
                    {'$set': {'auto_redeem_processed': False, 'updated_at': datetime.utcnow().isoformat()}}
                )
                if result.matched_count > 0:
                    matched_any = True
            
            return matched_any
        except Exception as e:
            logger.error(f'Failed to reset code {code} processed status: {e}')
            return False

    @staticmethod
    async def reset_code_processed_async(code: str) -> bool:
        """Reset auto_redeem_processed to False, allowing the code to be re-processed (async)"""
        try:
            dbs = []
            try: dbs.append(await _get_db_main_async())
            except: pass
            try: dbs.append(await _get_db_wos_async())
            except: pass
            
            unique_dbs = []
            seen_db_names = set()
            for db in dbs:
                if db is not None and db.name not in seen_db_names:
                    unique_dbs.append(db)
                    seen_db_names.add(db.name)

            matched_any = False
            for db in unique_dbs:
                code_upper = str(code).strip().upper()
                code_raw = str(code).strip()
                
                result = await db[GiftCodesAdapter.COLL].update_many(
                    {'$or': [
                        {'_id': code_raw}, {'giftcode': code_raw},
                        {'_id': code_upper}, {'giftcode': code_upper}
                    ]},
                    {'$set': {'auto_redeem_processed': False, 'updated_at': datetime.utcnow().isoformat()}}
                )
                if result.matched_count > 0:
                    matched_any = True
            
            return matched_any
        except Exception as e:
            logger.error(f'Failed to reset code (async) {code} processed status: {e}')
            return False

    @staticmethod
    def mark_code_invalid(code: str) -> bool:
        """Mark a gift code as invalid/expired"""
        try:
            db = _get_db_wos()
            db[GiftCodesAdapter.COLL].update_one(
                {'_id': code},
                {
                    '$set': {
                        'validation_status': 'invalid',
                        'updated_at': datetime.utcnow().isoformat()
                    }
                }
            )
            return True
        except Exception as e:
            logger.error(f'Failed to mark code {code} as invalid: {e}')
            return False

    @staticmethod
    async def mark_code_invalid_async(code: str) -> bool:
        """Mark a gift code as invalid/expired asynchronously"""
        try:
            db = await _get_db_wos_async()
            await db[GiftCodesAdapter.COLL].update_one(
                {'_id': code},
                {
                    '$set': {
                        'validation_status': 'invalid',
                        'updated_at': datetime.utcnow().isoformat()
                    }
                }
            )
            return True
        except Exception as e:
            logger.error(f'Failed to mark code (async) {code} as invalid: {e}')
            return False


class SentGiftCodesAdapter:
    """Adapter for tracking gift codes sent to specific guilds in MongoDB"""
    COLL = 'sent_giftcodes'

    @staticmethod
    def get_sent_codes(guild_id: int) -> set:
        """Get set of gift codes already sent to a guild"""
        try:
            db = _get_db_main()
            doc = db[SentGiftCodesAdapter.COLL].find_one({'_id': str(guild_id)})
            if doc:
                return set(doc.get('codes', []))
            return set()
        except Exception as e:
            logger.error(f'Failed to get sent codes for guild {guild_id}: {e}')
            return set()

    @staticmethod
    def mark_codes_sent(guild_id: int, codes: List[str], source: str = 'auto') -> bool:
        """Mark gift codes as sent for a guild"""
        try:
            db = _get_db_main()
            now = datetime.utcnow().isoformat()
            
            # Using $addToSet for automatic deduplication in MongoDB
            db[SentGiftCodesAdapter.COLL].update_one(
                {'_id': str(guild_id)},
                {
                    '$addToSet': {'codes': {'$each': [str(c).strip() for c in codes if c]}},
                    '$set': {
                        'guild_id': int(guild_id),
                        'last_sent_at': now,
                        'updated_at': now,
                        'source': source
                    },
                    '$setOnInsert': {'created_at': now}
                },
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f'Failed to mark codes sent for guild {guild_id}: {e}')
            return False

    @staticmethod
    def is_code_sent(guild_id: int, code: str) -> bool:
        """Check if a specific code was already sent to a guild"""
        try:
            db = _get_db_main()
            count = db[SentGiftCodesAdapter.COLL].count_documents({
                '_id': str(guild_id),
                'codes': str(code).strip()
            })
            return count > 0
        except Exception:
            return False

    @staticmethod
    async def get_sent_codes_async(guild_id: int) -> set:
        """Get set of gift codes already sent to a guild asynchronously"""
        try:
            db = await _get_db_main_async()
            doc = await db[SentGiftCodesAdapter.COLL].find_one({'_id': str(guild_id)})
            if doc:
                return set(doc.get('codes', []))
            return set()
        except Exception as e:
            logger.error(f'Failed to get sent codes (async) for guild {guild_id}: {e}')
            return set()

    @staticmethod
    async def mark_codes_sent_async(guild_id: int, codes: List[str], source: str = 'auto') -> bool:
        """Mark gift codes as sent for a guild asynchronously"""
        try:
            db = await _get_db_main_async()
            now = datetime.utcnow().isoformat()
            await db[SentGiftCodesAdapter.COLL].update_one(
                {'_id': str(guild_id)},
                {
                    '$addToSet': {'codes': {'$each': [str(c).strip() for c in codes if c]}},
                    '$set': {
                        'guild_id': int(guild_id),
                        'last_sent_at': now,
                        'updated_at': now,
                        'source': source
                    },
                    '$setOnInsert': {'created_at': now}
                },
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f'Failed to mark codes sent (async) for guild {guild_id}: {e}')
            return False

    @staticmethod
    async def is_code_sent_async(guild_id: int, code: str) -> bool:
        """Check if a specific code was already sent to a guild asynchronously"""
        try:
            db = await _get_db_main_async()
            count = await db[SentGiftCodesAdapter.COLL].count_documents({
                '_id': str(guild_id),
                'codes': str(code).strip()
            })
            return count > 0
        except Exception:
            return False


class AutoRedeemSettingsAdapter:
    """Adapter for managing auto redeem settings in MongoDB"""
    COLL = 'auto_redeem_settings'

    @staticmethod
    def get_settings(guild_id: int) -> Optional[Dict[str, Any]]:
        """Get auto redeem settings for a guild"""
        try:
            db = _get_db_main()
            doc = db[AutoRedeemSettingsAdapter.COLL].find_one({'_id': str(guild_id)})
            if not doc:
                return None
            return {
                'enabled': bool(doc.get('enabled', False)),
                'priority': int(doc.get('priority', 999)),
                'updated_by': int(doc.get('updated_by', 0)),
                'updated_at': doc.get('updated_at')
            }
        except Exception as e:
            logger.error(f'Failed to get auto redeem settings for guild {guild_id}: {e}')
            return None

    @staticmethod
    def set_enabled(guild_id: int, enabled: bool, updated_by: int) -> bool:
        """Set auto redeem enabled/disabled state for a guild"""
        try:
            db = _get_db_main()
            now = datetime.utcnow().isoformat()
            db[AutoRedeemSettingsAdapter.COLL].update_one(
                {'_id': str(guild_id)},
                {
                    '$set': {
                        'guild_id': int(guild_id),
                        'enabled': bool(enabled),
                        'updated_by': int(updated_by),
                        'updated_at': now
                    },
                    '$setOnInsert': {
                        'created_at': now,
                        'priority': 999
                    }
                },
                upsert=True
            )
            logger.info(f'Set auto redeem enabled={enabled} for guild {guild_id}')
            return True
        except Exception as e:
            logger.error(f'Failed to set auto redeem settings for guild {guild_id}: {e}')
            return False

    @staticmethod
    async def get_settings_async(guild_id: int) -> Optional[Dict[str, Any]]:
        """Get auto redeem settings for a guild asynchronously"""
        try:
            db = await _get_db_main_async()
            doc = await db[AutoRedeemSettingsAdapter.COLL].find_one({'_id': str(guild_id)})
            if not doc:
                return None
            return {
                'enabled': bool(doc.get('enabled', False)),
                'priority': int(doc.get('priority', 999)),
                'updated_by': int(doc.get('updated_by', 0)),
                'updated_at': doc.get('updated_at')
            }
        except Exception as e:
            logger.error(f'Failed to get auto redeem settings (async) for guild {guild_id}: {e}')
            return None

    @staticmethod
    async def set_enabled_async(guild_id: int, enabled: bool, updated_by: int) -> bool:
        """Set auto redeem enabled/disabled state for a guild asynchronously"""
        try:
            db = await _get_db_main_async()
            now = datetime.utcnow().isoformat()
            await db[AutoRedeemSettingsAdapter.COLL].update_one(
                {'_id': str(guild_id)},
                {
                    '$set': {
                        'guild_id': int(guild_id),
                        'enabled': bool(enabled),
                        'updated_by': int(updated_by),
                        'updated_at': now
                    },
                    '$setOnInsert': {
                        'created_at': now,
                        'priority': 999
                    }
                },
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f'Failed to set auto redeem settings (async) for guild {guild_id}: {e}')
            return False

    @staticmethod
    def set_priority(guild_id: int, priority: int, updated_by: int) -> bool:
        """Set auto redeem priority for a guild"""
        try:
            db = _get_db_main()
            now = datetime.utcnow().isoformat()
            db[AutoRedeemSettingsAdapter.COLL].update_one(
                {'_id': str(guild_id)},
                {
                    '$set': {
                        'guild_id': int(guild_id),
                        'priority': int(priority),
                        'updated_by': int(updated_by),
                        'updated_at': now
                    },
                    '$setOnInsert': {
                        'created_at': now,
                        'enabled': False
                    }
                },
                upsert=True
            )
            logger.info(f'Set auto redeem priority={priority} for guild {guild_id}')
            return True
        except Exception as e:
            logger.error(f'Failed to set auto redeem priority for guild {guild_id}: {e}')
            return False

    @staticmethod
    def get_all_settings() -> List[Dict[str, Any]]:
        """Get all auto redeem settings for all guilds"""
        try:
            db = _get_db_main()
            docs = db[AutoRedeemSettingsAdapter.COLL].find({})
            return [
                {
                    'guild_id': int(d.get('guild_id', d.get('_id'))),
                    'enabled': bool(d.get('enabled', False)),
                    'priority': int(d.get('priority', 999)),
                    'updated_by': int(d.get('updated_by', 0)),
                    'updated_at': d.get('updated_at'),
                    'created_at': d.get('created_at')
                }
                for d in docs
            ]
        except Exception as e:
            logger.error(f'Failed to get all auto redeem settings: {e}')
            return []

    @staticmethod
    async def get_settings_async(guild_id: int) -> Optional[Dict[str, Any]]:
        """Get auto redeem settings for a guild asynchronously"""
        try:
            db = await _get_db_main_async()
            doc = await db[AutoRedeemSettingsAdapter.COLL].find_one({'_id': str(guild_id)})
            if not doc:
                return None
            return {
                'enabled': bool(doc.get('enabled', False)),
                'priority': int(doc.get('priority', 999)),
                'updated_by': int(doc.get('updated_by', 0)),
                'updated_at': doc.get('updated_at')
            }
        except Exception as e:
            logger.error(f'Failed to get auto redeem settings (async) for guild {guild_id}: {e}')
            return None

    @staticmethod
    async def set_enabled_async(guild_id: int, enabled: bool, updated_by: int) -> bool:
        """Set auto redeem enabled/disabled state for a guild asynchronously"""
        try:
            db = await _get_db_main_async()
            now = datetime.utcnow().isoformat()
            await db[AutoRedeemSettingsAdapter.COLL].update_one(
                {'_id': str(guild_id)},
                {
                    '$set': {
                        'guild_id': int(guild_id),
                        'enabled': bool(enabled),
                        'updated_by': int(updated_by),
                        'updated_at': now
                    },
                    '$setOnInsert': {
                        'created_at': now,
                        'priority': 999
                    }
                },
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f'Failed to set auto redeem settings (async) for guild {guild_id}: {e}')
            return False

    @staticmethod
    async def set_priority_async(guild_id: int, priority: int, updated_by: int) -> bool:
        """Set auto redeem priority state for a guild asynchronously"""
        try:
            db = await _get_db_main_async()
            now = datetime.utcnow().isoformat()
            await db[AutoRedeemSettingsAdapter.COLL].update_one(
                {'_id': str(guild_id)},
                {
                    '$set': {
                        'guild_id': int(guild_id),
                        'priority': int(priority),
                        'updated_by': int(updated_by),
                        'updated_at': now
                    },
                    '$setOnInsert': {
                        'created_at': now,
                        'enabled': False
                    }
                },
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f'Failed to set auto redeem priority (async) for guild {guild_id}: {e}')
            return False

    @staticmethod
    async def get_all_settings_async() -> List[Dict[str, Any]]:
        """Get all auto redeem settings for all guilds asynchronously"""
        try:
            db = await _get_db_main_async()
            cursor = db[AutoRedeemSettingsAdapter.COLL].find({})
            docs = await cursor.to_list(length=None)
            return [
                {
                    'guild_id': int(d.get('guild_id', d.get('_id'))),
                    'enabled': bool(d.get('enabled', False)),
                    'priority': int(d.get('priority', 999)),
                    'updated_by': int(d.get('updated_by', 0)),
                    'updated_at': d.get('updated_at'),
                    'created_at': d.get('created_at')
                }
                for d in docs
            ]
        except Exception as e:
            logger.error(f'Failed to get all auto redeem settings (async): {e}')
            return []


class AutoRedeemMembersAdapter:
    """Adapter for managing auto-redeem members in MongoDB"""
    COLL = 'auto_redeem_members'

    @staticmethod
    def get_members(guild_id: int) -> List[Dict[str, Any]]:
        """Get all auto-redeem members for a guild with support for hybrid schemas and multiple databases"""
        try:
            members = []
            seen_fids = set()
            search_gid = [int(guild_id), str(guild_id)]
            
            # List of databases to check for data
            db_list = []
            try:
                db_list.append(_get_db_main())
            except Exception:
                pass
                
            try:
                wos_db = _get_db_wos()
                # Only add if it's a different client or different DB name
                if not db_list or wos_db.client != db_list[0].client or wos_db.name != db_list[0].name:
                    db_list.append(wos_db)
            except Exception:
                pass

            for db in db_list:
                # Also check 'discord_bot' database on the same cluster as a fallback
                client_dbs = [db]
                try:
                    if 'discord_bot' in db.client.list_database_names() and db.name != 'discord_bot':
                        client_dbs.append(db.client['discord_bot'])
                except Exception:
                    pass

                for target_db in client_dbs:
                    docs = list(target_db[AutoRedeemMembersAdapter.COLL].find({'guild_id': {'$in': search_gid}}))
                    
                    for doc in docs:
                        # 1. Check for flat schema (Schema V2)
                        fid = doc.get('fid')
                        if fid and str(fid).lower() != 'none':
                            fid_str = str(fid).strip()
                            if fid_str not in seen_fids:
                                raw_nick = doc.get('nickname', 'Unknown')
                                if isinstance(raw_nick, dict):
                                    raw_nick = raw_nick.get('nickname', 'Unknown')
                                members.append({
                                    'fid': fid_str,
                                    'nickname': str(raw_nick) if raw_nick else 'Unknown',
                                    'furnace_lv': int(doc.get('furnace_lv', 0) or 0),
                                    'avatar_image': doc.get('avatar_image', ''),
                                    'added_by': int(doc.get('added_by', 0)),
                                    'added_at': doc.get('added_at')
                                })
                                seen_fids.add(fid_str)
                        
                        # 2. Check for grouped schema (Schema V1 - legacy)
                        if 'members' in doc and isinstance(doc['members'], list):
                            for m in doc['members']:
                                mfid = m.get('fid')
                                if mfid and str(mfid).lower() != 'none':
                                    mfid_str = str(mfid).strip()
                                    if mfid_str not in seen_fids:
                                        raw_nick = m.get('nickname', 'Unknown')
                                        if isinstance(raw_nick, dict):
                                            raw_nick = raw_nick.get('nickname', 'Unknown')
                                        members.append({
                                            'fid': mfid_str,
                                            'nickname': str(raw_nick) if raw_nick else 'Unknown',
                                            'furnace_lv': int(m.get('furnace_lv', 0) or 0),
                                            'avatar_image': m.get('avatar_image', ''),
                                            'added_by': int(m.get('added_by', 0)),
                                            'added_at': m.get('added_at')
                                        })
                                        seen_fids.add(mfid_str)

            return members
        except Exception as e:
            logger.error(f'Failed to get auto-redeem members for guild {guild_id}: {e}')
            return []

    @staticmethod
    def _get_target_dbs() -> List[Any]:
        """Helper to get all relevant databases to search or modify"""
        db_list = []
        clients_seen = set()
        
        def add_db(db):
            if db is not None:
                key = (db.client.address, db.name)
                if key not in clients_seen:
                    db_list.append(db)
                    clients_seen.add(key)
                    
                    # Also check 'discord_bot' on same cluster as fallback
                    try:
                        if 'discord_bot' in db.client.list_database_names() and db.name != 'discord_bot':
                            db_key = (db.client.address, 'discord_bot')
                            if db_key not in clients_seen:
                                db_list.append(db.client['discord_bot'])
                                clients_seen.add(db_key)
                    except Exception:
                        pass

        try:
            add_db(_get_db_main())
        except Exception:
            pass
            
        try:
            add_db(_get_db_wos())
        except Exception:
            pass
            
        return db_list

    @staticmethod
    def add_member(guild_id: int, fid: str, member_data: Dict[str, Any]) -> bool:
        """Add a member to auto-redeem list (writes to primary DB)"""
        try:
            # Validate FID - reject null, empty, or 'None' values
            if not fid or not str(fid).strip() or str(fid).strip().lower() == 'none':
                logger.warning(f'Rejected adding member with invalid FID: {fid}')
                return False
            
            # Ensure fid is a clean string
            fid = str(fid).strip()
            
            # Always write to the main DB
            db = _get_db_main()
            now = datetime.utcnow().isoformat()
            db[AutoRedeemMembersAdapter.COLL].update_one(
                {'guild_id': int(guild_id), 'fid': str(fid)},
                {
                    '$set': {
                        'guild_id': int(guild_id),
                        'fid': str(fid),
                        'nickname': member_data.get('nickname', ''),
                        'furnace_lv': int(member_data.get('furnace_lv', 0)),
                        'avatar_image': member_data.get('avatar_image', ''),
                        'added_by': int(member_data.get('added_by', 0)),
                        'added_at': member_data.get('added_at', now),
                        'updated_at': now
                    },
                    '$setOnInsert': {'created_at': now}
                },
                upsert=True
            )
            logger.info(f'Added auto-redeem member {fid} for guild {guild_id}')
            return True
        except Exception as e:
            logger.error(f'Failed to add auto-redeem member {fid} for guild {guild_id}: {e}')
            return False

    @staticmethod
    async def add_member_async(guild_id: int, fid: str, member_data: Dict[str, Any]) -> bool:
        """Add a member to auto-redeem list asynchronously"""
        try:
            if not fid or not str(fid).strip() or str(fid).strip().lower() == 'none':
                return False
            fid = str(fid).strip()
            db = await _get_db_main_async()
            now = datetime.utcnow().isoformat()
            await db[AutoRedeemMembersAdapter.COLL].update_one(
                {'guild_id': int(guild_id), 'fid': str(fid)},
                {
                    '$set': {
                        'guild_id': int(guild_id),
                        'fid': str(fid),
                        'nickname': member_data.get('nickname', ''),
                        'furnace_lv': int(member_data.get('furnace_lv', 0)),
                        'avatar_image': member_data.get('avatar_image', ''),
                        'added_by': int(member_data.get('added_by', 0)),
                        'added_at': member_data.get('added_at', now),
                        'updated_at': now
                    },
                    '$setOnInsert': {'created_at': now}
                },
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f'Failed to add auto-redeem member (async) {fid} for guild {guild_id}: {e}')
            return False

    @staticmethod
    def _get_target_dbs() -> List[Any]:
        """Helper to get all relevant databases to search or modify"""
        db_list = []
        clients_seen = set()
        
        def add_db(db):
            if db is not None:
                key = (db.client.address, db.name)
                if key not in clients_seen:
                    db_list.append(db)
                    clients_seen.add(key)
                    
                    # Also check 'discord_bot' on same cluster as fallback
                    try:
                        if 'discord_bot' in db.client.list_database_names() and db.name != 'discord_bot':
                            db_key = (db.client.address, 'discord_bot')
                            if db_key not in clients_seen:
                                db_list.append(db.client['discord_bot'])
                                clients_seen.add(db_key)
                    except Exception:
                        pass
        try:
            add_db(_get_db_main())
        except Exception:
            pass
        try:
            add_db(_get_db_wos())
        except Exception:
            pass
        return db_list

    @staticmethod
    async def find_member_anywhere_async(fid: str) -> Optional[int]:
        """Find which guild a member is registered in (cross-server check) asynchronously"""
        try:
            fid_str = str(fid).strip()
            db_list = await AutoRedeemMembersAdapter._get_target_dbs_async()
            
            for target_db in db_list:
                doc = await target_db[AutoRedeemMembersAdapter.COLL].find_one({'fid': fid_str})
                if doc:
                    return int(doc.get('guild_id'))
            return None
        except Exception as e:
            logger.error(f'Failed to find member {fid} anywhere: {e}')
            return None

    @staticmethod
    async def _get_target_dbs_async() -> List[Any]:
        """Helper to get all relevant databases asynchronously"""
        db_list = []
        clients_seen = set()
        
        async def add_db_async(db):
            if db is not None:
                key = (db.client.address, db.name)
                if key not in clients_seen:
                    db_list.append(db)
                    clients_seen.add(key)
                    try:
                        db_names = await db.client.list_database_names()
                        if 'discord_bot' in db_names and db.name != 'discord_bot':
                            db_key = (db.client.address, 'discord_bot')
                            if db_key not in clients_seen:
                                db_list.append(db.client['discord_bot'])
                                clients_seen.add(db_key)
                    except Exception:
                        pass

        try:
            db_main = await _get_db_main_async()
            await add_db_async(db_main)
        except Exception:
            pass
            
        try:
            db_wos = await _get_db_wos_async()
            await add_db_async(db_wos)
        except Exception:
            pass
            
        return db_list

    @staticmethod
    async def get_members_async(guild_id: int) -> List[Dict[str, Any]]:
        """Get all auto-redeem members for a guild asynchronously"""
        try:
            members = []
            seen_fids = set()
            search_gid = [int(guild_id), str(guild_id)]
            
            db_list = await AutoRedeemMembersAdapter._get_target_dbs_async()

            for target_db in db_list:
                cursor = target_db[AutoRedeemMembersAdapter.COLL].find({'guild_id': {'$in': search_gid}})
                docs = await cursor.to_list(length=1000)
                
                for doc in docs:
                    fid = doc.get('fid')
                    if fid and str(fid).lower() != 'none':
                        fid_str = str(fid).strip()
                        if fid_str not in seen_fids:
                            raw_nick = doc.get('nickname', 'Unknown')
                            if isinstance(raw_nick, dict):
                                raw_nick = raw_nick.get('nickname', 'Unknown')
                            members.append({
                                'fid': fid_str,
                                'nickname': str(raw_nick) if raw_nick else 'Unknown',
                                'furnace_lv': int(doc.get('furnace_lv', 0) or 0),
                                'avatar_image': doc.get('avatar_image', ''),
                                'added_by': int(doc.get('added_by', 0)),
                                'added_at': doc.get('added_at')
                            })
                            seen_fids.add(fid_str)
                    
                    if 'members' in doc and isinstance(doc['members'], list):
                        for m in doc['members']:
                            mfid = m.get('fid')
                            if mfid and str(mfid).lower() != 'none':
                                mfid_str = str(mfid).strip()
                                if mfid_str not in seen_fids:
                                    raw_nick = m.get('nickname', 'Unknown')
                                    if isinstance(raw_nick, dict):
                                        raw_nick = raw_nick.get('nickname', 'Unknown')
                                    members.append({
                                        'fid': mfid_str,
                                        'nickname': str(raw_nick) if raw_nick else 'Unknown',
                                        'furnace_lv': int(m.get('furnace_lv', 0) or 0),
                                        'avatar_image': m.get('avatar_image', ''),
                                        'added_by': int(m.get('added_by', 0)),
                                        'added_at': m.get('added_at')
                                    })
                                    seen_fids.add(mfid_str)
            return members
        except Exception as e:
            logger.error(f'Failed to get auto-redeem members (async) for guild {guild_id}: {e}')
            return []

    @staticmethod
    def remove_member(guild_id: int, fid: str) -> bool:
        """Remove a member from auto-redeem list (synchronous)"""
        try:
            fid_str = str(fid).strip()
            search_gid = [int(guild_id), str(guild_id)]
            removed = False

            db_list = AutoRedeemMembersAdapter._get_target_dbs()
            for db in db_list:
                # V2: flat doc where each member is a separate document
                res_v2 = db[AutoRedeemMembersAdapter.COLL].delete_many({
                    'guild_id': {'$in': search_gid},
                    'fid': fid_str
                })
                if res_v2.deleted_count > 0:
                    removed = True

                # V1: legacy grouped doc where members are embedded in an array
                res_v1 = db[AutoRedeemMembersAdapter.COLL].update_many(
                    {'guild_id': {'$in': search_gid}, 'members.fid': fid_str},
                    {'$pull': {'members': {'fid': fid_str}}}
                )
                if res_v1.modified_count > 0:
                    removed = True

            return removed
        except Exception as e:
            logger.error(f'Failed to remove auto-redeem member {fid} for guild {guild_id}: {e}')
            return False

    @staticmethod
    async def remove_member_async(guild_id: int, fid: str) -> bool:

        """Remove a member from auto-redeem list asynchronously"""
        try:
            fid_str = str(fid).strip()
            search_gid = [int(guild_id), str(guild_id)]
            removed = False
            
            db_list = await AutoRedeemMembersAdapter._get_target_dbs_async()
            for db in db_list:
                res_v2 = await db[AutoRedeemMembersAdapter.COLL].delete_many({
                    'guild_id': {'$in': search_gid},
                    'fid': fid_str
                })
                if res_v2.deleted_count > 0:
                    removed = True
                
                res_v1 = await db[AutoRedeemMembersAdapter.COLL].update_many(
                    {'guild_id': {'$in': search_gid}, 'members.fid': fid_str},
                    {'$pull': {'members': {'fid': fid_str}}}
                )
                if res_v1.modified_count > 0:
                    removed = True
            return removed
        except Exception as e:
            logger.error(f'Failed to remove auto-redeem member (async) {fid} for guild {guild_id}: {e}')
            return False

    @staticmethod
    async def member_exists_async(guild_id: int, fid: str) -> bool:
        """Check if member exists asynchronously"""
        try:
            fid_str = str(fid).strip()
            search_gid = [int(guild_id), str(guild_id)]
            db_list = await AutoRedeemMembersAdapter._get_target_dbs_async()
            for db in db_list:
                doc = await db[AutoRedeemMembersAdapter.COLL].find_one({
                    'guild_id': {'$in': search_gid},
                    'fid': fid_str
                })
                if doc: return True
                doc = await db[AutoRedeemMembersAdapter.COLL].find_one({
                    'guild_id': {'$in': search_gid},
                    'members.fid': fid_str
                })
                if doc: return True
            return False
        except Exception:
            return False

    @staticmethod
    async def batch_member_exists_async(guild_id: int, fids: List[str]) -> Dict[str, bool]:
        """Batch check if multiple members exist asynchronously"""
        try:
            results = {str(fid): False for fid in fids}
            fid_strs = [str(fid).strip() for fid in fids]
            search_gid = [int(guild_id), str(guild_id)]
            db_list = await AutoRedeemMembersAdapter._get_target_dbs_async()
            for db in db_list:
                cursor = db[AutoRedeemMembersAdapter.COLL].find({
                    'guild_id': {'$in': search_gid},
                    'fid': {'$in': fid_strs}
                })
                docs = await cursor.to_list(length=1000)
                for d in docs: results[str(d.get('fid'))] = True
                
                if not all(results.values()):
                    cursor_v1 = db[AutoRedeemMembersAdapter.COLL].find({
                        'guild_id': {'$in': search_gid},
                        'members.fid': {'$in': fid_strs}
                    })
                    docs_v1 = await cursor_v1.to_list(length=1000)
                    for doc in docs_v1:
                        if 'members' in doc:
                            for m in doc['members']:
                                mfid = str(m.get('fid'))
                                if mfid in results: results[mfid] = True
            return results
        except Exception:
            return {str(fid): False for fid in fids}


class AutoRedeemChannelsAdapter:
    """Adapter for managing auto redeem channel configuration in MongoDB"""
    COLL = 'auto_redeem_channels'

    @staticmethod
    def get_channel(guild_id: int) -> Optional[Dict[str, Any]]:
        """Get auto redeem channel configuration for a guild"""
        try:
            db = _get_db_main()
            doc = db[AutoRedeemChannelsAdapter.COLL].find_one({'_id': str(guild_id)})
            if not doc:
                return None
            return {
                'channel_id': int(doc.get('channel_id')),
                'added_by': int(doc.get('added_by', 0)),
                'added_at': doc.get('added_at')
            }
        except Exception as e:
            logger.error(f'Failed to get auto redeem channel for guild {guild_id}: {e}')
            return None

    @staticmethod
    def set_channel(guild_id: int, channel_id: int, added_by: int) -> bool:
        """Set auto redeem channel for a guild"""
        try:
            db = _get_db_main()
            now = datetime.utcnow().isoformat()
            db[AutoRedeemChannelsAdapter.COLL].update_one(
                {'_id': str(guild_id)},
                {
                    '$set': {
                        'guild_id': int(guild_id),
                        'channel_id': int(channel_id),
                        'added_by': int(added_by),
                        'added_at': now,
                        'updated_at': now
                    },
                    '$setOnInsert': {'created_at': now}
                },
                upsert=True
            )
            logger.info(f'Set auto redeem channel {channel_id} for guild {guild_id}')
            return True
        except Exception as e:
            logger.error(f'Failed to set auto redeem channel for guild {guild_id}: {e}')
            return False

    @staticmethod
    async def get_channel_async(guild_id: int) -> Optional[Dict[str, Any]]:
        """Get auto redeem channel configuration for a guild asynchronously"""
        try:
            db = await _get_db_main_async()
            doc = await db[AutoRedeemChannelsAdapter.COLL].find_one({'_id': str(guild_id)})
            if not doc: return None
            return {
                'channel_id': int(doc.get('channel_id')),
                'added_by': int(doc.get('added_by', 0)),
                'added_at': doc.get('added_at')
            }
        except Exception as e:
            logger.error(f'Failed to get auto redeem channel (async) for guild {guild_id}: {e}')
            return None

    @staticmethod
    async def set_channel_async(guild_id: int, channel_id: int, added_by: int) -> bool:
        """Set auto redeem channel for a guild asynchronously"""
        try:
            db = await _get_db_main_async()
            now = datetime.utcnow().isoformat()
            await db[AutoRedeemChannelsAdapter.COLL].update_one(
                {'_id': str(guild_id)},
                {
                    '$set': {
                        'guild_id': int(guild_id),
                        'channel_id': int(channel_id),
                        'added_by': int(added_by),
                        'added_at': now,
                        'updated_at': now
                    },
                    '$setOnInsert': {'created_at': now}
                },
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f'Failed to set auto redeem channel (async) for guild {guild_id}: {e}')
            return False


class WelcomeChannelAdapter:
    """Adapter for managing welcome channel settings in MongoDB"""
    COLL = 'welcome_channels'

    @staticmethod
    def get(guild_id: int) -> Optional[Dict[str, Any]]:
        """Get welcome channel settings for a guild"""
        try:
            db = _get_db_main()
            doc = db[WelcomeChannelAdapter.COLL].find_one({'_id': str(guild_id)})
            if not doc:
                return None
            # Handle channel_id being None (e.g., when only bg_image_url was set)
            channel_id_raw = doc.get('channel_id')
            channel_id = int(channel_id_raw) if channel_id_raw is not None else None
            return {
                'channel_id': channel_id,
                'enabled': bool(doc.get('enabled', True)),
                'bg_image_url': doc.get('bg_image_url')
            }
        except Exception as e:
            logger.error(f'Failed to get welcome channel for guild {guild_id}: {e}')
            return None

    @staticmethod
    def set(guild_id: int, channel_id: int, enabled: bool = True) -> bool:
        """Set/update welcome channel for a guild"""
        try:
            db = _get_db_main()
            now = datetime.utcnow().isoformat()
            db[WelcomeChannelAdapter.COLL].update_one(
                {'_id': str(guild_id)},
                {
                    '$set': {
                        'channel_id': int(channel_id),
                        'enabled': bool(enabled),
                        'updated_at': now
                    },
                    '$setOnInsert': {'created_at': now}
                },
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f'Failed to set welcome channel for guild {guild_id}: {e}')
            return False
    
    @staticmethod
    def set_bg_image(guild_id: int, bg_image_url: str) -> bool:
        """Set/update background image URL for a guild"""
        try:
            db = _get_db_main()
            now = datetime.utcnow().isoformat()
            db[WelcomeChannelAdapter.COLL].update_one(
                {'_id': str(guild_id)},
                {
                    '$set': {
                        'bg_image_url': str(bg_image_url),
                        'updated_at': now
                    },
                    '$setOnInsert': {'created_at': now}
                },
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f'Failed to set background image for guild {guild_id}: {e}')
            return False

    @staticmethod
    def delete(guild_id: int) -> bool:
        """Delete welcome channel configuration for a guild"""
        try:
            db = _get_db_main()
            result = db[WelcomeChannelAdapter.COLL].delete_one({'_id': str(guild_id)})
            return result.deleted_count > 0
        except Exception as e:
            logger.error(f'Failed to delete welcome channel for guild {guild_id}: {e}')
            return False

    @staticmethod
    def get_all() -> list:
        """Get all configured welcome channels"""
        try:
            db = _get_db_main()
            docs = list(db[WelcomeChannelAdapter.COLL].find({'enabled': True}))
            return [{
                'guild_id': int(d.get('_id')),
                'channel_id': int(d.get('channel_id')),
                'enabled': bool(d.get('enabled', True))
            } for d in docs]
        except Exception as e:
            logger.error(f'Failed to get all welcome channels: {e}')
            return []

    @staticmethod
    async def get_async(guild_id: int) -> Optional[Dict[str, Any]]:
        """Get welcome channel settings for a guild asynchronously"""
        try:
            db = await _get_db_main_async()
            doc = await db[WelcomeChannelAdapter.COLL].find_one({'_id': str(guild_id)})
            if not doc: return None
            channel_id_raw = doc.get('channel_id')
            return {
                'channel_id': int(channel_id_raw) if channel_id_raw is not None else None,
                'enabled': bool(doc.get('enabled', True)),
                'bg_image_url': doc.get('bg_image_url')
            }
        except Exception as e:
            logger.error(f'Failed to get welcome channel (async) for guild {guild_id}: {e}')
            return None

    @staticmethod
    async def set_async(guild_id: int, channel_id: int, enabled: bool = True) -> bool:
        """Set/update welcome channel for a guild asynchronously"""
        try:
            db = await _get_db_main_async()
            now = datetime.utcnow().isoformat()
            await db[WelcomeChannelAdapter.COLL].update_one(
                {'_id': str(guild_id)},
                {
                    '$set': {
                        'channel_id': int(channel_id),
                        'enabled': bool(enabled),
                        'updated_at': now
                    },
                    '$setOnInsert': {'created_at': now}
                },
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f'Failed to set welcome channel (async) for guild {guild_id}: {e}')
            return False

    @staticmethod
    async def set_bg_image_async(guild_id: int, bg_image_url: str) -> bool:
        """Set/update background image URL for a guild asynchronously"""
        try:
            db = await _get_db_main_async()
            now = datetime.utcnow().isoformat()
            await db[WelcomeChannelAdapter.COLL].update_one(
                {'_id': str(guild_id)},
                {
                    '$set': {
                        'bg_image_url': str(bg_image_url),
                        'updated_at': now
                    },
                    '$setOnInsert': {'created_at': now}
                },
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f'Failed to set background image (async) for guild {guild_id}: {e}')
            return False


class IDChannelsAdapter:
    """Adapter for managing ID registration channels in MongoDB"""
    COLL = 'id_channels'

    @staticmethod
    async def get_channel_async(guild_id: int) -> Optional[Dict[str, Any]]:
        """Get ID channel configuration for a guild asynchronously"""
        try:
            db = await _get_db_main_async()
            doc = await db[IDChannelsAdapter.COLL].find_one({'_id': str(guild_id)})
            if not doc:
                return None
            return {
                'channel_id': int(doc.get('channel_id')),
                'alliance_id': int(doc.get('alliance_id', 0)),
                'created_by': int(doc.get('created_by', 0)),
                'created_at': doc.get('created_at')
            }
        except Exception as e:
            logger.error(f'Failed to get ID channel (async) for guild {guild_id}: {e}')
            return None

    @staticmethod
    async def set_channel_async(guild_id: int, channel_id: int, created_by: int, alliance_id: int = 0) -> bool:
        """Set ID channel for a guild asynchronously"""
        try:
            db = await _get_db_main_async()
            now = datetime.utcnow().isoformat()
            await db[IDChannelsAdapter.COLL].update_one(
                {'_id': str(guild_id)},
                {
                    '$set': {
                        'guild_id': int(guild_id),
                        'channel_id': int(channel_id),
                        'alliance_id': int(alliance_id),
                        'created_by': int(created_by),
                        'updated_at': now
                    },
                    '$setOnInsert': {'created_at': now}
                },
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f'Failed to set ID channel (async) for guild {guild_id}: {e}')
            return False

    @staticmethod
    async def delete_async(guild_id: int) -> bool:
        """Delete ID channel configuration for a guild asynchronously"""
        try:
            db = await _get_db_main_async()
            result = await db[IDChannelsAdapter.COLL].delete_one({'_id': str(guild_id)})
            return result.deleted_count > 0
        except Exception as e:
            logger.error(f'Failed to delete ID channel (async) for guild {guild_id}: {e}')
            return False

    @staticmethod
    async def get_all_async() -> list:
        """Get all configured ID channels asynchronously"""
        try:
            db = await _get_db_main_async()
            cursor = db[IDChannelsAdapter.COLL].find({})
            docs = await cursor.to_list(length=1000)
            return [{
                'guild_id': int(d.get('guild_id', d.get('_id'))),
                'channel_id': int(d.get('channel_id')),
                'alliance_id': int(d.get('alliance_id', 0))
            } for d in docs]
        except Exception as e:
            logger.error(f'Failed to get all ID channels (async): {e}')
            return []

class BirthdayChannelAdapter:
    """Adapter for managing birthday channels in MongoDB"""
    COLL = 'birthday_channels'

    @staticmethod
    def get(guild_id: int) -> Optional[int]:
        try:
            db = _get_db_main()
            doc = db[BirthdayChannelAdapter.COLL].find_one({'_id': str(guild_id)})
            return int(doc['channel_id']) if doc and doc.get('channel_id') else None
        except Exception as e:
            logger.error(f'Failed to get birthday channel for guild {guild_id}: {e}')
            return None

    @staticmethod
    def set(guild_id: int, channel_id: int) -> bool:
        try:
            db = _get_db_main()
            now = datetime.utcnow().isoformat()
            db[BirthdayChannelAdapter.COLL].update_one(
                {'_id': str(guild_id)},
                {'$set': {'channel_id': int(channel_id), 'updated_at': now}, '$setOnInsert': {'created_at': now}},
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f'Failed to set birthday channel for guild {guild_id}: {e}')
            return False

    @staticmethod
    async def get_async(guild_id: int) -> Optional[int]:
        try:
            db = await _get_db_main_async()
            doc = await db[BirthdayChannelAdapter.COLL].find_one({'_id': str(guild_id)})
            return int(doc['channel_id']) if doc and doc.get('channel_id') else None
        except Exception as e:
            logger.error(f'Failed to get birthday channel (async) for guild {guild_id}: {e}')
            return None

    @staticmethod
    async def set_async(guild_id: int, channel_id: int) -> bool:
        try:
            db = await _get_db_main_async()
            now = datetime.utcnow().isoformat()
            await db[BirthdayChannelAdapter.COLL].update_one(
                {'_id': str(guild_id)},
                {'$set': {'channel_id': int(channel_id), 'updated_at': now}, '$setOnInsert': {'created_at': now}},
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f'Failed to set birthday channel (async) for guild {guild_id}: {e}')
            return False

    @staticmethod
    def remove(guild_id: int) -> bool:
        try:
            db = _get_db_main()
            db[BirthdayChannelAdapter.COLL].delete_one({'_id': str(guild_id)})
            return True
        except Exception as e:
            logger.error(f'Failed to remove birthday channel for guild {guild_id}: {e}')
            return False

    @staticmethod
    async def remove_async(guild_id: int) -> bool:
        try:
            db = await _get_db_main_async()
            await db[BirthdayChannelAdapter.COLL].delete_one({'_id': str(guild_id)})
            return True
        except Exception as e:
            logger.error(f'Failed to remove birthday channel (async) for guild {guild_id}: {e}')
            return False

class AdminsAdapter:
    COLL = 'admins'

    @staticmethod
    def count() -> int:
        try:
            db = _get_db_main()
            return db[AdminsAdapter.COLL].count_documents({})
        except Exception:
            return 0

    @staticmethod
    def get(user_id: int) -> Optional[Dict[str, Any]]:
        try:
            db = _get_db_main()
            d = db[AdminsAdapter.COLL].find_one({'_id': str(user_id)})
            return d
        except Exception:
            return None

    @staticmethod
    def upsert(user_id: int, is_initial: int) -> bool:
        try:
            db = _get_db_main()
            now = datetime.utcnow().isoformat()
            db[AdminsAdapter.COLL].update_one(
                {'_id': str(user_id)},
                {'$set': {'is_initial': int(is_initial), 'updated_at': now}, '$setOnInsert': {'created_at': now}},
                upsert=True
            )
            return True
        except Exception:
            return False

    @staticmethod
    def get_initial_admins() -> List[int]:
        try:
            db = _get_db_main()
            docs = list(db[AdminsAdapter.COLL].find({'is_initial': 1}))
            return [int(d['_id']) for d in docs]
        except Exception:
            return []

    @staticmethod
    async def count_async() -> int:
        try:
            db = await _get_db_main_async()
            return await db[AdminsAdapter.COLL].count_documents({})
        except Exception:
            return 0

    @staticmethod
    async def get_async(user_id: int) -> Optional[Dict[str, Any]]:
        try:
            db = await _get_db_main_async()
            d = await db[AdminsAdapter.COLL].find_one({'_id': str(user_id)})
            return d
        except Exception:
            return None

    @staticmethod
    async def upsert_async(user_id: int, is_initial: int) -> bool:
        try:
            db = await _get_db_main_async()
            now = datetime.utcnow().isoformat()
            await db[AdminsAdapter.COLL].update_one(
                {'_id': str(user_id)},
                {'$set': {'is_initial': int(is_initial), 'updated_at': now}, '$setOnInsert': {'created_at': now}},
                upsert=True
            )
            return True
        except Exception:
            return False

    @staticmethod
    async def get_initial_admins_async() -> List[int]:
        try:
            db = await _get_db_main_async()
            cursor = db[AdminsAdapter.COLL].find({'is_initial': 1})
            docs = await cursor.to_list(length=None)
            return [int(d['_id']) for d in docs]
        except Exception:
            return []

class AlliancesAdapter:
    COLL = 'alliance__alliance_list'

    @staticmethod
    def get_all() -> list:
        try:
            db = _get_db_main()
            docs = list(db[AlliancesAdapter.COLL].find({}))
            # Legacy field 'id' instead of 'alliance_id'? No, user said 'alliance_id' in schema but let's be safe
            # Debug output showed: "id": 1, "name": "S667", "discord_server_id": 1285973956424597554
            return [{'alliance_id': int(d.get('alliance_id') or d.get('id')), 'name': d.get('name'), 'discord_server_id': int(d.get('discord_server_id', 0))} for d in docs]
        except Exception:
            return []

    @staticmethod
    def find_by_name(name: str) -> Optional[Dict[str, Any]]:
        try:
            db = _get_db_main()
            d = db[AlliancesAdapter.COLL].find_one({'name': name})
            return d
        except Exception:
            return None

    @staticmethod
    def delete(alliance_id: int) -> bool:
        try:
            db = _get_db_main()
            res = db[AlliancesAdapter.COLL].delete_one({'_id': str(alliance_id)})
            return res.deleted_count > 0
        except Exception:
            return False

    @staticmethod
    async def find_by_name_async(name: str) -> Optional[Dict[str, Any]]:
        try:
            db = await _get_db_main_async()
            d = await db[AlliancesAdapter.COLL].find_one({'name': name})
            return d
        except Exception:
            return None

    @staticmethod
    async def delete_async(alliance_id: int) -> bool:
        try:
            db = await _get_db_main_async()
            res = await db[AlliancesAdapter.COLL].delete_one({'_id': str(alliance_id)})
            return res.deleted_count > 0
        except Exception:
            return False

    @staticmethod
    async def get_all_async() -> list:
        try:
            db = await _get_db_main_async()
            cursor = db[AlliancesAdapter.COLL].find({})
            docs = await cursor.to_list(length=None)
            return [{'alliance_id': int(d.get('alliance_id') or d.get('id')), 'name': d.get('name'), 'discord_server_id': int(d.get('discord_server_id', 0))} for d in docs]
        except Exception:
            return []

    @staticmethod
    def upsert(alliance_id: int, name: str, discord_server_id: int) -> bool:
        try:
            db = _get_db_main()
            now = datetime.utcnow().isoformat()
            db[AlliancesAdapter.COLL].update_one(
                {'_id': str(alliance_id)},
                {'$set': {'alliance_id': int(alliance_id), 'name': name, 'discord_server_id': int(discord_server_id), 'updated_at': now}, '$setOnInsert': {'created_at': now}},
                upsert=True
            )
            return True
        except Exception:
            return False

    @staticmethod
    async def upsert_async(alliance_id: int, name: str, discord_server_id: int) -> bool:
        try:
            db = await _get_db_main_async()
            now = datetime.utcnow().isoformat()
            await db[AlliancesAdapter.COLL].update_one(
                {'_id': str(alliance_id)},
                {'$set': {'alliance_id': int(alliance_id), 'name': name, 'discord_server_id': int(discord_server_id), 'updated_at': now}, '$setOnInsert': {'created_at': now}},
                upsert=True
            )
            return True
        except Exception:
            return False

class AllianceSettingsAdapter:
    COLL = 'alliance__alliancesettings'

    @staticmethod
    def get(alliance_id: int) -> Optional[Dict[str, Any]]:
        try:
            db = _get_db_main()
            # Query by 'alliance_id' as per schema: "alliance_id": 1
            d = db[AllianceSettingsAdapter.COLL].find_one({'alliance_id': int(alliance_id)})
            return d
        except Exception:
            return None

    @staticmethod
    def get_all() -> list:
        try:
            db = _get_db_main()
            docs = list(db[AllianceSettingsAdapter.COLL].find({}))
            # Schema: "alliance_id": 1, "channel_id": ...
            return [{'alliance_id': int(d.get('alliance_id')), 'channel_id': int(d.get('channel_id') or 0), 'interval': int(d.get('interval') or 0)} for d in docs]
        except Exception:
            return []

    @staticmethod
    def upsert(alliance_id: int, channel_id: int, interval: int, giftcodecontrol: Optional[int] = None, giftcode_channel: Optional[int] = None) -> bool:
        try:
            db = _get_db_main()
            now = datetime.utcnow().isoformat()
            payload = {'channel_id': int(channel_id), 'interval': int(interval)}
            if giftcodecontrol is not None:
                payload['giftcodecontrol'] = int(giftcodecontrol)
            if giftcode_channel is not None:
                payload['giftcode_channel'] = int(giftcode_channel)
            db[AllianceSettingsAdapter.COLL].update_one(
                {'_id': str(alliance_id)},
                {'$set': {**payload, 'updated_at': now}, '$setOnInsert': {'created_at': now}},
                upsert=True
            )
            return True
        except Exception:
            return False

    @staticmethod
    def delete(alliance_id: int) -> bool:
        try:
            db = _get_db_main()
            res = db[AllianceSettingsAdapter.COLL].delete_one({'_id': str(alliance_id)})
            return res.deleted_count > 0
        except Exception:
            return False

    @staticmethod
    def get_auto_redeem_alliances() -> List[int]:
        try:
            db = _get_db_main()
            docs = list(db[AllianceSettingsAdapter.COLL].find({'giftcodecontrol': 1}))
            return [int(d['_id']) for d in docs]
        except Exception:
            return []

    @staticmethod
    async def get_async(alliance_id: int) -> Optional[Dict[str, Any]]:
        try:
            db = await _get_db_main_async()
            d = await db[AllianceSettingsAdapter.COLL].find_one({'alliance_id': int(alliance_id)})
            return d
        except Exception:
            return None

    @staticmethod
    async def get_all_async() -> list:
        try:
            db = await _get_db_main_async()
            cursor = db[AllianceSettingsAdapter.COLL].find({})
            docs = await cursor.to_list(length=None)
            return [{'alliance_id': int(d.get('alliance_id')), 'channel_id': int(d.get('channel_id') or 0), 'interval': int(d.get('interval') or 0)} for d in docs]
        except Exception:
            return []

    @staticmethod
    async def upsert_async(alliance_id: int, channel_id: int, interval: int, giftcodecontrol: Optional[int] = None, giftcode_channel: Optional[int] = None) -> bool:
        try:
            db = await _get_db_main_async()
            now = datetime.utcnow().isoformat()
            payload = {'channel_id': int(channel_id), 'interval': int(interval)}
            if giftcodecontrol is not None:
                payload['giftcodecontrol'] = int(giftcodecontrol)
            if giftcode_channel is not None:
                payload['giftcode_channel'] = int(giftcode_channel)
            await db[AllianceSettingsAdapter.COLL].update_one(
                {'_id': str(alliance_id)},
                {'$set': {**payload, 'updated_at': now}, '$setOnInsert': {'created_at': now}},
                upsert=True
            )
            return True
        except Exception:
            return False

    @staticmethod
    async def delete_async(alliance_id: int) -> bool:
        try:
            db = await _get_db_main_async()
            res = await db[AllianceSettingsAdapter.COLL].delete_one({'_id': str(alliance_id)})
            return res.deleted_count > 0
        except Exception:
            return False

    @staticmethod
    async def get_auto_redeem_alliances_async() -> List[int]:
        try:
            db = await _get_db_main_async()
            cursor = db[AllianceSettingsAdapter.COLL].find({'giftcodecontrol': 1})
            docs = await cursor.to_list(length=None)
            return [int(d['_id']) for d in docs]
        except Exception:
            return []


class FurnaceHistoryAdapter:
    COLLECTION = 'furnace_history'

    @staticmethod
    def insert(data: Dict[str, Any]) -> bool:
        try:
            db = _get_db_wos()
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
    async def insert_async(data: Dict[str, Any]) -> bool:
        """Insert a single furnace history record asynchronously"""
        try:
            db = await _get_db_wos_async()
            if db is None:
                return False
            
            if "change_date" not in data:
                data["change_date"] = datetime.utcnow()
                
            await db[FurnaceHistoryAdapter.COLLECTION].insert_one(data)
            return True
        except Exception as e:
            logging.error(f"Error inserting furnace history (async): {e}")
            return False

    @staticmethod
    async def get_recent_changes_async(days: int = 7, alliance_id: Optional[int] = None) -> list:
        try:
            db = await _get_db_wos_async()
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
            
            cursor = db[FurnaceHistoryAdapter.COLLECTION].aggregate(pipeline)
            return await cursor.to_list(length=None)
        except Exception as e:
            logging.error(f"Error fetching furnace history (async): {e}")
            return []

    @staticmethod
    async def get_logs_async(days: int = 7, alliance_id: Optional[int] = None, limit: int = 50) -> list:
        """Fetch raw furnace level change events"""
        try:
            db = await _get_db_wos_async()
            if db is None:
                return []
            
            query = {
                "change_date": {
                    "$gte": datetime.utcnow() - timedelta(days=days)
                }
            }
            if alliance_id is not None:
                query["alliance_id"] = int(alliance_id)
            
            cursor = db[FurnaceHistoryAdapter.COLLECTION].find(query).sort("change_date", -1).limit(limit)
            docs = await cursor.to_list(length=None)
            for doc in docs:
                doc['id'] = str(doc.get('_id'))
                doc.pop('_id', None)
            return docs
        except Exception as e:
            logging.error(f"Error fetching furnace logs: {e}")
            return []


class AllianceEventsAdapter:
    """Consolidated adapter for all alliance monitoring events (names, avatars, furnace)"""
    COLL = 'alliance_events'

    @staticmethod
    async def log_event_async(event_type: str, fid: str, nickname: str, alliance_id: int, old_val: Any, new_val: Any, extra: Dict[str, Any] = None) -> bool:
        try:
            db = await _get_db_wos_async()
            event = {
                'type': event_type,
                'fid': str(fid),
                'nickname': nickname,
                'alliance_id': int(alliance_id),
                'old_value': old_val,
                'new_value': new_val,
                'timestamp': datetime.utcnow(),
                **(extra or {})
            }
            await db[AllianceEventsAdapter.COLL].insert_one(event)
            return True
        except Exception as e:
            logging.error(f"Error logging alliance event: {e}")
            return False

    @staticmethod
    async def get_recent_events_async(guild_id: int, limit: int = 50) -> list:
        try:
            db = await _get_db_wos_async()
            # First get the alliance_id for this guild
            monitor_coll = db['alliance_monitoring']
            monitor = await monitor_coll.find_one({'guild_id': int(guild_id)})
            if not monitor:
                return []
            
            alliance_id = monitor['alliance_id']
            cursor = db[AllianceEventsAdapter.COLL].find({'alliance_id': int(alliance_id)}).sort('timestamp', -1).limit(limit)
            docs = await cursor.to_list(length=None)
            for doc in docs:
                doc['id'] = str(doc.get('_id'))
                doc.pop('_id', None)
                if isinstance(doc.get('timestamp'), datetime):
                    doc['timestamp'] = doc['timestamp'].isoformat()
            return docs
        except Exception as e:
            logging.error(f"Error fetching alliance events: {e}")
            return []

    @staticmethod
    async def get_global_recent_events_async(limit: int = 80) -> list:
        """Get recent monitor events across all alliances for the public status feed."""
        try:
            db = await _get_db_wos_async()
            safe_limit = max(1, min(int(limit), 200))
            cursor = db[AllianceEventsAdapter.COLL].find({}).sort('timestamp', -1).limit(safe_limit)
            docs = await cursor.to_list(length=None)
            for doc in docs:
                doc['id'] = str(doc.get('_id'))
                doc.pop('_id', None)
                if isinstance(doc.get('timestamp'), datetime):
                    doc['timestamp'] = doc['timestamp'].isoformat()
            return docs
        except Exception as e:
            logging.error(f"Error fetching global alliance events: {e}")
            return []


class BotActivityAdapter:
    """Structured activity ledger for public live bot operations."""
    COLL = 'bot_activity'
    TTL_SECONDS = 60 * 60 * 24 * 7  # 7 days
    _indexes_created: bool = False

    @classmethod
    async def _ensure_indexes(cls) -> None:
        """Create TTL + query indexes once per process lifetime."""
        if cls._indexes_created:
            return
        try:
            db = await _get_db_wos_async()
            coll = db[cls.COLL]
            await coll.create_index(
                'created_at',
                expireAfterSeconds=cls.TTL_SECONDS,
                background=True,
                name='ttl_created_at',
            )
            for field in ('workflow', 'event_type', 'guild_id', 'fid'):
                await coll.create_index(
                    field, background=True, sparse=True, name=f'idx_{field}'
                )
            cls._indexes_created = True
            logger.info('BotActivityAdapter: indexes ensured on %s', cls.COLL)
        except Exception as exc:
            logger.warning('BotActivityAdapter: could not ensure indexes: %s', exc)

    @staticmethod
    async def insert_activity_async(activity: Dict[str, Any]) -> bool:
        try:
            db = await _get_db_wos_async()
            # Lazy index bootstrap
            if not BotActivityAdapter._indexes_created:
                import asyncio
                asyncio.ensure_future(BotActivityAdapter._ensure_indexes())
                
            now = datetime.utcnow()
            doc = dict(activity or {})
            if 'created_at' not in doc or not isinstance(doc.get('created_at'), datetime):
                doc['created_at'] = now
            doc.setdefault("updated_at", now)
            if "_id" not in doc:
                doc["_id"] = str(uuid.uuid4())
            await db[BotActivityAdapter.COLL].insert_one(doc)
            return True
        except Exception as e:
            logger.warning(f"Failed to insert bot activity: {e}")
            return False

    @staticmethod
    async def get_recent_activity_async(
        limit: int = 80,
        workflow: Optional[str] = None,
        guild_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        try:
            db = await _get_db_wos_async()
            query: Dict[str, Any] = {}
            if workflow:
                query['workflow'] = workflow
            if guild_id:
                query['guild_id'] = str(guild_id)

            safe_limit = max(1, min(int(limit), 200))
            cursor = (
                db[BotActivityAdapter.COLL]
                .find(query)
                .sort('created_at', -1)
                .limit(safe_limit)
            )
            docs = await cursor.to_list(length=safe_limit)
            for doc in docs:
                doc["id"] = str(doc.get("_id"))
                doc.pop("_id", None)
                for key in ("created_at", "updated_at"):
                    if isinstance(doc.get(key), datetime):
                        doc[key] = doc[key].isoformat()
            return docs
        except Exception as e:
            logger.warning(f"Failed to fetch bot activity: {e}")
            return []

    @staticmethod
    async def purge_old_async(days: int = 7) -> int:
        try:
            db = await _get_db_wos_async()
            cutoff = datetime.utcnow() - timedelta(days=days)
            result = await db[BotActivityAdapter.COLL].delete_many(
                {'created_at': {'$lt': cutoff}}
            )
            return result.deleted_count
        except Exception as e:
            logger.error(f"Failed to purge old bot activity: {e}")
            return 0


class AllianceMonitoringAdapter:
    COLL = 'alliance_monitoring'

    @staticmethod
    def get_all_monitors() -> list:
        try:
            db = _get_db_main()
            docs = list(db[AllianceMonitoringAdapter.COLL].find({'enabled': 1}))
            return [{
                'guild_id': int(d.get('guild_id')),
                'alliance_id': int(d.get('alliance_id')),
                'channel_id': int(d.get('channel_id')),
                'enabled': int(d.get('enabled', 1)),
                'check_interval': int(d.get('check_interval', 240))
            } for d in docs]
        except Exception as e:
            logger.error(f"Error getting alliance monitors from Mongo: {e}")
            return []

    @staticmethod
    async def get_all_monitors_async() -> list:
        try:
            db = await _get_db_main_async()
            cursor = db[AllianceMonitoringAdapter.COLL].find({'enabled': 1})
            docs = await cursor.to_list(length=None)
            return [{
                'guild_id': int(d.get('guild_id')),
                'alliance_id': int(d.get('alliance_id')),
                'channel_id': int(d.get('channel_id')),
                'enabled': int(d.get('enabled', 1)),
                'check_interval': int(d.get('check_interval', 240))
            } for d in docs]
        except Exception as e:
            logger.error(f"Error getting alliance monitors (async) from Mongo: {e}")
            return []

    @staticmethod
    def upsert_monitor(guild_id: int, alliance_id: int, channel_id: int, enabled: int = 1, check_interval: int = 240) -> bool:
        try:
            db = _get_db_main()
            now = datetime.utcnow().isoformat()
            db[AllianceMonitoringAdapter.COLL].update_one(
                {'guild_id': int(guild_id), 'alliance_id': int(alliance_id)},
                {
                    '$set': {
                        'guild_id': int(guild_id),
                        'alliance_id': int(alliance_id),
                        'channel_id': int(channel_id),
                        'enabled': int(enabled),
                        'check_interval': int(check_interval),
                        'updated_at': now
                    },
                    '$setOnInsert': {'created_at': now}
                },
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f"Error upserting alliance monitor: {e}")
            return False

    @staticmethod
    def delete_monitor(guild_id: int, alliance_id: int) -> bool:
        try:
            db = _get_db_main()
            res = db[AllianceMonitoringAdapter.COLL].delete_one({'guild_id': int(guild_id), 'alliance_id': int(alliance_id)})
            return res.deleted_count > 0
        except Exception as e:
            logger.error(f"Error deleting alliance monitor: {e}")
            return False

    @staticmethod
    async def upsert_monitor_async(guild_id: int, alliance_id: int, channel_id: int, enabled: int = 1, check_interval: int = 240) -> bool:
        try:
            db = await _get_db_main_async()
            now = datetime.utcnow().isoformat()
            await db[AllianceMonitoringAdapter.COLL].update_one(
                {'guild_id': int(guild_id), 'alliance_id': int(alliance_id)},
                {
                    '$set': {
                        'guild_id': int(guild_id),
                        'alliance_id': int(alliance_id),
                        'channel_id': int(channel_id),
                        'enabled': int(enabled),
                        'check_interval': int(check_interval),
                        'updated_at': now
                    },
                    '$setOnInsert': {'created_at': now}
                },
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f"Error upserting alliance monitor (async): {e}")
            return False

    @staticmethod
    async def delete_monitor_async(guild_id: int, alliance_id: int) -> bool:
        try:
            db = await _get_db_main_async()
            res = await db[AllianceMonitoringAdapter.COLL].delete_one({'guild_id': int(guild_id), 'alliance_id': int(alliance_id)})
            return res.deleted_count > 0
        except Exception as e:
            logger.error(f"Error deleting alliance monitor (async): {e}")
            return False


class ServerAllianceAdapter:
    """Adapter for managing server-alliance assignments in MongoDB"""
    COLL = 'server_alliances'

    @staticmethod
    def set_alliance(guild_id: int, alliance_id: int, assigned_by: int) -> bool:
        """Assign an alliance to a Discord server"""
        try:
            db = _get_db_main()
            now = datetime.utcnow().isoformat()
            # Legacy collection uses 'id' as guild_id and 'alliances_id' as alliance_id
            db[ServerAllianceAdapter.COLL].update_one(
                {'_id': str(guild_id)},
                {
                    '$set': {
                        'id': int(guild_id),
                        'alliances_id': int(alliance_id),
                        'assigned_by': int(assigned_by),
                        'assigned_at': now,
                        'updated_at': now
                    },
                    '$setOnInsert': {'created_at': now}
                },
                upsert=True
            )
            logger.info(f'Assigned alliance {alliance_id} to server {guild_id}')
            return True
        except Exception as e:
            logger.error(f'Failed to assign alliance to server {guild_id}: {e}')
            return False

    @staticmethod
    def get_alliance(guild_id: int) -> Optional[int]:
        """Get the assigned alliance ID for a Discord server"""
        try:
            db = _get_db_main()
            # Try finding by _id first (modern) or id (legacy)
            doc = db[ServerAllianceAdapter.COLL].find_one({'_id': str(guild_id)})
            if not doc:
                doc = db[ServerAllianceAdapter.COLL].find_one({'_id': int(guild_id)})
            if not doc:
                 # Fallback for documents that might have auto-generated _id
                 doc = db[ServerAllianceAdapter.COLL].find_one({'id': str(guild_id)})
            if not doc:
                 doc = db[ServerAllianceAdapter.COLL].find_one({'id': int(guild_id)})
            
            if doc:
                # Legacy field is 'alliances_id', modern might be 'alliance_id'
                return int(doc.get('alliances_id') or doc.get('alliance_id'))
            return None
        except Exception as e:
            logger.error(f'Failed to get alliance for server {guild_id}: {e}')
            return None

    @staticmethod
    async def get_alliance_async(guild_id: int) -> Optional[int]:
        """Get the assigned alliance ID for a Discord server asynchronously"""
        try:
            db = await _get_db_main_async()
            # Try finding by _id first (modern) or id (legacy)
            doc = await db[ServerAllianceAdapter.COLL].find_one({'_id': str(guild_id)})
            if not doc:
                doc = await db[ServerAllianceAdapter.COLL].find_one({'_id': int(guild_id)})
            if not doc:
                 # Fallback for documents that might have auto-generated _id
                 doc = await db[ServerAllianceAdapter.COLL].find_one({'id': str(guild_id)})
            if not doc:
                 doc = await db[ServerAllianceAdapter.COLL].find_one({'id': int(guild_id)})

            if doc:
                # Legacy field is 'alliances_id', modern might be 'alliance_id'
                return int(doc.get('alliances_id') or doc.get('alliance_id'))
            return None
        except Exception as e:
            logger.error(f'Failed to get alliance (async) for server {guild_id}: {e}')
            return None

    @staticmethod
    def remove_alliance(guild_id: int) -> bool:
        """Remove alliance assignment from a Discord server"""
        try:
            db = _get_db_main()
            result = db[ServerAllianceAdapter.COLL].delete_one({'_id': str(guild_id)})
            return result.deleted_count > 0
        except Exception as e:
            logger.error(f'Failed to remove alliance from server {guild_id}: {e}')
            return False

    @staticmethod
    def get_all() -> list:
        """Get all server-alliance mappings"""
        try:
            db = _get_db_main()
            docs = list(db[ServerAllianceAdapter.COLL].find({}))
            return [{
                'guild_id': int(d.get('guild_id')),
                'alliance_id': int(d.get('alliance_id')),
                'assigned_by': int(d.get('assigned_by')),
                'assigned_at': d.get('assigned_at')
            } for d in docs]
        except Exception as e:
            logger.error(f'Failed to get all server-alliance mappings: {e}')
            return []

    @staticmethod
    def set_password(guild_id: int, password: str, set_by: int) -> bool:
        """Set member list password for a Discord server"""
        try:
            db = _get_db_main()
            now = datetime.utcnow().isoformat()
            db[ServerAllianceAdapter.COLL].update_one(
                {'_id': str(guild_id)},
                {
                    '$set': {
                        'member_list_password': str(password),
                        'password_set_by': int(set_by),
                        'password_set_at': now,
                        'updated_at': now
                    },
                    '$setOnInsert': {'created_at': now, 'guild_id': int(guild_id)}
                },
                upsert=True
            )
            logger.info(f'Set member list password for server {guild_id}')
            
            # Invalidate all existing auth sessions when password changes
            try:
                AuthSessionsAdapter.invalidate_all_sessions(guild_id)
            except Exception as session_error:
                logger.warning(f'Failed to invalidate auth sessions for guild {guild_id}: {session_error}')
            
            return True
        except Exception as e:
            logger.error(f'Failed to set password for server {guild_id}: {e}')
            return False

    @staticmethod
    async def set_alliance_async(guild_id: int, alliance_id: int, assigned_by: int) -> bool:
        try:
            db = await _get_db_main_async()
            now = datetime.utcnow().isoformat()
            await db[ServerAllianceAdapter.COLL].update_one(
                {'_id': str(guild_id)},
                {
                    '$set': {
                        'id': int(guild_id),
                        'alliances_id': int(alliance_id),
                        'assigned_by': int(assigned_by),
                        'assigned_at': now,
                        'updated_at': now
                    },
                    '$setOnInsert': {'created_at': now}
                },
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f'Failed to assign alliance (async) to server {guild_id}: {e}')
            return False

    @staticmethod
    async def get_alliance_async(guild_id: int) -> Optional[int]:
        try:
            db = await _get_db_main_async()
            doc = await db[ServerAllianceAdapter.COLL].find_one({'_id': str(guild_id)})
            if not doc: doc = await db[ServerAllianceAdapter.COLL].find_one({'_id': int(guild_id)})
            if not doc: doc = await db[ServerAllianceAdapter.COLL].find_one({'id': str(guild_id)})
            if not doc: doc = await db[ServerAllianceAdapter.COLL].find_one({'id': int(guild_id)})
            if doc: return int(doc.get('alliances_id') or doc.get('alliance_id'))
            return None
        except Exception as e:
            logger.error(f'Failed to get alliance (async) for server {guild_id}: {e}')
            return None

    @staticmethod
    async def remove_alliance_async(guild_id: int) -> bool:
        try:
            db = await _get_db_main_async()
            result = await db[ServerAllianceAdapter.COLL].delete_one({'_id': str(guild_id)})
            return result.deleted_count > 0
        except Exception as e:
            logger.error(f'Failed to remove alliance (async) from server {guild_id}: {e}')
            return False

    @staticmethod
    async def get_all_async() -> list:
        try:
            db = await _get_db_main_async()
            cursor = db[ServerAllianceAdapter.COLL].find({})
            docs = await cursor.to_list(length=None)
            return [{
                'guild_id': int(d.get('guild_id') or d.get('id') or d.get('_id')),
                'alliance_id': int(d.get('alliances_id') or d.get('alliance_id')),
                'assigned_by': int(d.get('assigned_by')),
                'assigned_at': d.get('assigned_at')
            } for d in docs]
        except Exception as e:
            logger.error(f'Failed to get all server-alliance mappings (async): {e}')
            return []

    @staticmethod
    async def set_password_async(guild_id: int, password: str, set_by: int) -> bool:
        try:
            db = await _get_db_main_async()
            now = datetime.utcnow().isoformat()
            await db[ServerAllianceAdapter.COLL].update_one(
                {'_id': str(guild_id)},
                {
                    '$set': {
                        'member_list_password': str(password),
                        'password_set_by': int(set_by),
                        'password_set_at': now,
                        'updated_at': now
                    },
                    '$setOnInsert': {'created_at': now, 'guild_id': int(guild_id)}
                },
                upsert=True
            )
            # Invalidate all existing auth sessions when password changes
            try:
                await AuthSessionsAdapter.invalidate_all_sessions_async(guild_id)
            except Exception: pass
            return True
        except Exception as e:
            logger.error(f'Failed to set password (async) for server {guild_id}: {e}')
            return False

    @staticmethod
    def get_password(guild_id: int) -> Optional[str]:
        """Get member list password for a Discord server"""
        try:
            db = _get_db_main()
            doc = db[ServerAllianceAdapter.COLL].find_one({'_id': str(guild_id)})
            if doc:
                return doc.get('member_list_password')
            return None
        except Exception as e:
            logger.error(f'Failed to get password for server {guild_id}: {e}')
            return None

    @staticmethod
    async def get_password_async(guild_id: int) -> Optional[str]:
        """Get member list password for a Discord server asynchronously"""
        try:
            db = await _get_db_main_async()
            doc = await db[ServerAllianceAdapter.COLL].find_one({'_id': str(guild_id)})
            if doc:
                return doc.get('member_list_password')
            return None
        except Exception as e:
            logger.error(f'Failed to get password (async) for server {guild_id}: {e}')
            return None

    @staticmethod
    def verify_password(guild_id: int, password: str) -> bool:
        """Verify member list password for a Discord server"""
        try:
            stored_password = ServerAllianceAdapter.get_password(guild_id)
            if stored_password is None:
                return False
            return str(password) == str(stored_password)
        except Exception as e:
            logger.error(f'Failed to verify password for server {guild_id}: {e}')
            return False

    @staticmethod
    async def verify_password_async(guild_id: int, password: str) -> bool:
        """Verify member list password for a Discord server asynchronously"""
        try:
            stored_password = await ServerAllianceAdapter.get_password_async(guild_id)
            if stored_password is None:
                return False
            return str(password) == str(stored_password)
        except Exception as e:
            logger.error(f'Failed to verify password (async) for server {guild_id}: {e}')
            return False

    @staticmethod
    def get_state(guild_id: int) -> Optional[int]:
        """Get the assigned state number for a Discord server"""
        try:
            db = _get_db_main()
            doc = db[ServerAllianceAdapter.COLL].find_one({'_id': str(guild_id)})
            if not doc: doc = db[ServerAllianceAdapter.COLL].find_one({'_id': int(guild_id)})
            if not doc: doc = db[ServerAllianceAdapter.COLL].find_one({'id': str(guild_id)})
            if not doc: doc = db[ServerAllianceAdapter.COLL].find_one({'id': int(guild_id)})
            if doc:
                return doc.get('state')
            return None
        except Exception as e:
            logger.error(f'Failed to get state for server {guild_id}: {e}')
            return None

    @staticmethod
    async def get_state_async(guild_id: int) -> Optional[int]:
        """Get the assigned state number for a Discord server asynchronously"""
        try:
            db = await _get_db_main_async()
            doc = await db[ServerAllianceAdapter.COLL].find_one({'_id': str(guild_id)})
            if not doc: doc = await db[ServerAllianceAdapter.COLL].find_one({'_id': int(guild_id)})
            if not doc: doc = await db[ServerAllianceAdapter.COLL].find_one({'id': str(guild_id)})
            if not doc: doc = await db[ServerAllianceAdapter.COLL].find_one({'id': int(guild_id)})
            if doc:
                return doc.get('state')
            return None
        except Exception as e:
            logger.error(f'Failed to get state (async) for server {guild_id}: {e}')
            return None


class AuthSessionsAdapter:
    """Adapter for managing authentication sessions for /manage command"""
    COLL = 'auth_sessions'
    SESSION_DURATION_DAYS = 7

    @staticmethod
    def create_session(guild_id: int, user_id: int, password_hash: str) -> bool:
        """Create or update an authentication session for a user"""
        try:
            db = _get_db_main()
            now = datetime.utcnow()
            expires_at = now + timedelta(days=AuthSessionsAdapter.SESSION_DURATION_DAYS)
            
            db[AuthSessionsAdapter.COLL].update_one(
                {'_id': f"{guild_id}:{user_id}"},
                {
                    '$set': {
                        'guild_id': int(guild_id),
                        'user_id': int(user_id),
                        'password_hash': str(password_hash),
                        'created_at': now.isoformat(),
                        'expires_at': expires_at.isoformat(),
                        'updated_at': now.isoformat()
                    }
                },
                upsert=True
            )
            logger.info(f'Created auth session for user {user_id} in guild {guild_id}')
            return True
        except Exception as e:
            logger.error(f'Failed to create auth session for user {user_id} in guild {guild_id}: {e}')
            return False

    @staticmethod
    def get_session(guild_id: int, user_id: int) -> Optional[Dict[str, Any]]:
        """Get authentication session for a user"""
        try:
            db = _get_db_main()
            doc = db[AuthSessionsAdapter.COLL].find_one({'_id': f"{guild_id}:{user_id}"})
            if doc:
                doc.pop('_id', None)
            return doc
        except Exception as e:
            logger.error(f'Failed to get auth session for user {user_id} in guild {guild_id}: {e}')
            return None

    @staticmethod
    def is_session_valid(guild_id: int, user_id: int, current_password: str) -> bool:
        """Check if user has a valid authentication session"""
        try:
            session = AuthSessionsAdapter.get_session(guild_id, user_id)
            if not session:
                return False
            
            # Check if session has expired
            expires_at = datetime.fromisoformat(session.get('expires_at'))
            if datetime.utcnow() > expires_at:
                logger.info(f'Auth session expired for user {user_id} in guild {guild_id}')
                return False
            
            # Check if password has changed (compare with current password)
            stored_password_hash = session.get('password_hash')
            if str(current_password) != str(stored_password_hash):
                logger.info(f'Password changed, invalidating session for user {user_id} in guild {guild_id}')
                return False
            
            return True
        except Exception as e:
            logger.error(f'Failed to validate auth session for user {user_id} in guild {guild_id}: {e}')
            return False

    @staticmethod
    def invalidate_all_sessions(guild_id: int) -> bool:
        """Invalidate all authentication sessions for a guild (called when password changes)"""
        try:
            db = _get_db_main()
            result = db[AuthSessionsAdapter.COLL].delete_many({'guild_id': int(guild_id)})
            logger.info(f'Invalidated {result.deleted_count} auth sessions for guild {guild_id}')
            return True
        except Exception as e:
            logger.error(f'Failed to invalidate auth sessions for guild {guild_id}: {e}')
            return False

    @staticmethod
    def cleanup_expired_sessions() -> int:
        """Remove all expired sessions from the database"""
        try:
            db = _get_db_main()
            now = datetime.utcnow().isoformat()
            result = db[AuthSessionsAdapter.COLL].delete_many({
                'expires_at': {'$lt': now}
            })
            logger.info(f'Cleaned up {result.deleted_count} expired auth sessions')
            return result.deleted_count
        except Exception as e:
            logger.error(f'Failed to cleanup expired auth sessions: {e}')
            return 0

    @staticmethod
    async def create_session_async(guild_id: int, user_id: int, password_hash: str) -> bool:
        try:
            db = await _get_db_main_async()
            now = datetime.utcnow()
            expires_at = now + timedelta(days=AuthSessionsAdapter.SESSION_DURATION_DAYS)
            await db[AuthSessionsAdapter.COLL].update_one(
                {'_id': f"{guild_id}:{user_id}"},
                {
                    '$set': {
                        'guild_id': int(guild_id),
                        'user_id': int(user_id),
                        'password_hash': str(password_hash),
                        'created_at': now.isoformat(),
                        'expires_at': expires_at.isoformat(),
                        'updated_at': now.isoformat()
                    }
                },
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f'Failed to create auth session (async) for user {user_id} in guild {guild_id}: {e}')
            return False

    @staticmethod
    async def get_session_async(guild_id: int, user_id: int) -> Optional[Dict[str, Any]]:
        try:
            db = await _get_db_main_async()
            doc = await db[AuthSessionsAdapter.COLL].find_one({'_id': f"{guild_id}:{user_id}"})
            if doc: doc.pop('_id', None)
            return doc
        except Exception:
            return None

    @staticmethod
    async def is_session_valid_async(guild_id: int, user_id: int, current_password: str) -> bool:
        try:
            session = await AuthSessionsAdapter.get_session_async(guild_id, user_id)
            if not session: return False
            expires_at = datetime.fromisoformat(session.get('expires_at'))
            if datetime.utcnow() > expires_at: return False
            if str(current_password) != str(session.get('password_hash')): return False
            return True
        except Exception:
            return False

    @staticmethod
    async def invalidate_all_sessions_async(guild_id: int) -> bool:
        try:
            db = await _get_db_main_async()
            await db[AuthSessionsAdapter.COLL].delete_many({'guild_id': int(guild_id)})
            return True
        except Exception:
            return False

    @staticmethod
    async def cleanup_expired_sessions_async() -> int:
        try:
            db = await _get_db_main_async()
            now = datetime.utcnow().isoformat()
            result = await db[AuthSessionsAdapter.COLL].delete_many({'expires_at': {'$lt': now}})
            return result.deleted_count
        except Exception:
            return 0


class RecordsAdapter:
    """Adapter for managing custom player records in MongoDB"""
    COLL = 'custom_records'

    @staticmethod
    def create_record(guild_id: int, record_name: str, created_by: int) -> bool:
        """Create a new custom record"""
        try:
            db = _get_db_main()
            now = datetime.utcnow().isoformat()
            
            # Check if record already exists
            existing = db[RecordsAdapter.COLL].find_one({
                '_id': f"{guild_id}:{record_name}"
            })
            
            if existing:
                logger.warning(f'Record {record_name} already exists for guild {guild_id}')
                return False
            
            db[RecordsAdapter.COLL].insert_one({
                '_id': f"{guild_id}:{record_name}",
                'guild_id': int(guild_id),
                'record_name': str(record_name),
                'created_by': int(created_by),
                'created_at': now,
                'updated_at': now,
                'members': []
            })
            logger.info(f'Created record {record_name} for guild {guild_id}')
            return True
        except Exception as e:
            logger.error(f'Failed to create record {record_name} for guild {guild_id}: {e}')
            return False

    @staticmethod
    def delete_record(guild_id: int, record_name: str) -> bool:
        """Delete a custom record"""
        try:
            db = _get_db_main()
            result = db[RecordsAdapter.COLL].delete_one({
                '_id': f"{guild_id}:{record_name}"
            })
            if result.deleted_count > 0:
                logger.info(f'Deleted record {record_name} from guild {guild_id}')
                return True
            return False
        except Exception as e:
            logger.error(f'Failed to delete record {record_name} from guild {guild_id}: {e}')
            return False

    @staticmethod
    def get_record(guild_id: int, record_name: str) -> Optional[dict]:
        """Get a specific record"""
        try:
            db = _get_db_main()
            doc = db[RecordsAdapter.COLL].find_one({
                '_id': f"{guild_id}:{record_name}"
            })
            return doc
        except Exception as e:
            logger.error(f'Failed to get record {record_name} for guild {guild_id}: {e}')
            return None

    @staticmethod
    def get_custom_columns(guild_id: int, record_name: str) -> list:
        """Get custom column names for a record"""
        try:
            record = RecordsAdapter.get_record(guild_id, record_name)
            if record:
                return record.get('custom_columns', [])
            return []
        except Exception as e:
            logger.error(f'Failed to get custom columns for {record_name}: {e}')
            return []

    @staticmethod
    def add_custom_column(guild_id: int, record_name: str, column_name: str) -> bool:
        """Add a custom column name to a record"""
        try:
            db = _get_db_main()
            now = datetime.utcnow().isoformat()
            
            res = db[RecordsAdapter.COLL].update_one(
                {'_id': f"{guild_id}:{record_name}"},
                {
                    '$addToSet': {'custom_columns': str(column_name)},
                    '$set': {'updated_at': now}
                }
            )
            return res.modified_count > 0 or res.matched_count > 0
        except Exception as e:
            logger.error(f'Failed to add custom column {column_name} to {record_name}: {e}')
            return False

    @staticmethod
    def remove_custom_column(guild_id: int, record_name: str, column_name: str) -> bool:
        """Remove a custom column name from a record"""
        try:
            db = _get_db_main()
            now = datetime.utcnow().isoformat()
            
            res = db[RecordsAdapter.COLL].update_one(
                {'_id': f"{guild_id}:{record_name}"},
                {
                    '$pull': {'custom_columns': str(column_name)},
                    '$set': {'updated_at': now}
                }
            )
            return res.modified_count > 0
        except Exception as e:
            logger.error(f'Failed to remove custom column {column_name} from {record_name}: {e}')
            return False

    @staticmethod
    def get_all_records(guild_id: int) -> list:
        """Get all records for a guild"""
        try:
            db = _get_db_main()
            docs = list(db[RecordsAdapter.COLL].find({'guild_id': int(guild_id)}))
            return [{
                'record_name': d.get('record_name'),
                'created_by': d.get('created_by'),
                'created_at': d.get('created_at'),
                'member_count': len(d.get('members', []))
            } for d in docs]
        except Exception as e:
            logger.error(f'Failed to get all records for guild {guild_id}: {e}')
            return []

    @staticmethod
    def add_member_to_record(guild_id: int, record_name: str, fid: str, member_data: dict) -> bool:
        """Add a member to a record"""
        try:
            db = _get_db_main()
            now = datetime.utcnow().isoformat()
            
            # Check if member already exists in record
            record = db[RecordsAdapter.COLL].find_one({
                '_id': f"{guild_id}:{record_name}"
            })
            
            if not record:
                logger.warning(f'Record {record_name} not found for guild {guild_id}')
                return False
            
            # Find existing member to preserve their custom data (like notes)
            existing_member = next((m for m in record.get('members', []) if m.get('fid') == str(fid)), {})
            
            # Remove existing member if present
            members = [m for m in record.get('members', []) if m.get('fid') != str(fid)]
            
            # Add new member data, preserving existing custom fields
            member_entry = {
                'fid': str(fid),
                'nickname': member_data.get('nickname', 'Unknown'),
                'furnace_lv': int(member_data.get('furnace_lv', 0)),
                'avatar_image': member_data.get('avatar_image', ''),
                'added_at': existing_member.get('added_at', now),
                'added_by': existing_member.get('added_by', member_data.get('added_by', 0)),
                'note': existing_member.get('note', '')
            }
            
            # Merge any other custom data from existing_member
            for key, value in existing_member.items():
                if key not in member_entry:
                    member_entry[key] = value
            
            members.append(member_entry)
            
            # Update record
            db[RecordsAdapter.COLL].update_one(
                {'_id': f"{guild_id}:{record_name}"},
                {
                    '$set': {
                        'members': members,
                        'updated_at': now
                    }
                }
            )
            logger.info(f'Added/Updated member {fid} in record {record_name} for guild {guild_id}')
            return True
        except Exception as e:
            logger.error(f'Failed to add member {fid} to record {record_name}: {e}')
            return False

    @staticmethod
    def remove_member_from_record(guild_id: int, record_name: str, fid: str) -> bool:
        """Remove a member from a record"""
        try:
            db = _get_db_main()
            now = datetime.utcnow().isoformat()
            
            record = db[RecordsAdapter.COLL].find_one({
                '_id': f"{guild_id}:{record_name}"
            })
            
            if not record:
                return False
            
            # Remove member
            members = [m for m in record.get('members', []) if m.get('fid') != str(fid)]
            
            # Update record
            db[RecordsAdapter.COLL].update_one(
                {'_id': f"{guild_id}:{record_name}"},
                {
                    '$set': {
                        'members': members,
                        'updated_at': now
                    }
                }
            )
            logger.info(f'Removed member {fid} from record {record_name} in guild {guild_id}')
            return True
        except Exception as e:
            logger.error(f'Failed to remove member {fid} from record {record_name}: {e}')
            return False

    @staticmethod
    def get_record_members(guild_id: int, record_name: str) -> list:
        """Get all members in a record"""
        try:
            record = RecordsAdapter.get_record(guild_id, record_name)
            if record:
                return record.get('members', [])
            return []
        except Exception as e:
            logger.error(f'Failed to get members for record {record_name}: {e}')
            return []

    @staticmethod
    def update_member_field(guild_id: int, record_name: str, fid: str, field_name: str, value: Any) -> bool:
        """Update a specific field for a member in a record"""
        try:
            db = _get_db_main()
            now = datetime.utcnow().isoformat()
            
            record = db[RecordsAdapter.COLL].find_one({
                '_id': f"{guild_id}:{record_name}"
            })
            
            if not record:
                return False
            
            members = record.get('members', [])
            member_found = False
            for m in members:
                if m.get('fid') == str(fid):
                    m[field_name] = value
                    member_found = True
                    break
            
            if not member_found:
                return False
                
            db[RecordsAdapter.COLL].update_one(
                {'_id': f"{guild_id}:{record_name}"},
                {
                    '$set': {
                        'members': members,
                        'updated_at': now
                    }
                }
            )
            return True
        except Exception as e:
            logger.error(f'Failed to update member {fid} field {field_name} in record {record_name}: {e}')
            return False

    @staticmethod
    async def create_record_async(guild_id: int, record_name: str, created_by: int) -> bool:
        try:
            db = await _get_db_main_async()
            now = datetime.utcnow().isoformat()
            existing = await db[RecordsAdapter.COLL].find_one({'_id': f"{guild_id}:{record_name}"})
            if existing: return False
            await db[RecordsAdapter.COLL].insert_one({
                '_id': f"{guild_id}:{record_name}", 'guild_id': int(guild_id), 'record_name': str(record_name),
                'created_by': int(created_by), 'created_at': now, 'updated_at': now, 'members': []
            })
            return True
        except Exception: return False

    @staticmethod
    async def delete_record_async(guild_id: int, record_name: str) -> bool:
        try:
            db = await _get_db_main_async()
            res = await db[RecordsAdapter.COLL].delete_one({'_id': f"{guild_id}:{record_name}"})
            return res.deleted_count > 0
        except Exception: return False

    @staticmethod
    async def get_record_async(guild_id: int, record_name: str) -> Optional[dict]:
        try:
            db = await _get_db_main_async()
            return await db[RecordsAdapter.COLL].find_one({'_id': f"{guild_id}:{record_name}"})
        except Exception: return None

    @staticmethod
    async def get_all_records_async(guild_id: int) -> list:
        try:
            db = await _get_db_main_async()
            cursor = db[RecordsAdapter.COLL].find({'guild_id': int(guild_id)})
            docs = await cursor.to_list(length=None)
            return [{'record_name': d.get('record_name'), 'created_by': d.get('created_by'), 'created_at': d.get('created_at'), 'member_count': len(d.get('members', []))} for d in docs]
        except Exception: return []

    @staticmethod
    async def add_member_to_record_async(guild_id: int, record_name: str, fid: str, member_data: dict) -> bool:
        try:
            db = await _get_db_main_async()
            now = datetime.utcnow().isoformat()
            record = await db[RecordsAdapter.COLL].find_one({'_id': f"{guild_id}:{record_name}"})
            if not record: return False
            members = [m for m in record.get('members', []) if m.get('fid') != str(fid)]
            members.append({'fid': str(fid), 'nickname': member_data.get('nickname', 'Unknown'), 'furnace_lv': int(member_data.get('furnace_lv', 0)), 'avatar_image': member_data.get('avatar_image', ''), 'added_at': now, 'added_by': member_data.get('added_by', 0)})
            await db[RecordsAdapter.COLL].update_one({'_id': f"{guild_id}:{record_name}"}, {'$set': {'members': members, 'updated_at': now}})
            return True
        except Exception: return False

    @staticmethod
    async def remove_member_from_record_async(guild_id: int, record_name: str, fid: str) -> bool:
        try:
            db = await _get_db_main_async()
            now = datetime.utcnow().isoformat()
            record = await db[RecordsAdapter.COLL].find_one({'_id': f"{guild_id}:{record_name}"})
            if not record: return False
            members = [m for m in record.get('members', []) if m.get('fid') != str(fid)]
            await db[RecordsAdapter.COLL].update_one({'_id': f"{guild_id}:{record_name}"}, {'$set': {'members': members, 'updated_at': now}})
            return True
        except Exception: return False

    @staticmethod
    def rename_record(guild_id: int, old_name: str, new_name: str) -> bool:
        """Rename a record"""
        try:
            db = _get_db_main()
            
            # Check if new name already exists
            existing = db[RecordsAdapter.COLL].find_one({
                '_id': f"{guild_id}:{new_name}"
            })
            
            if existing:
                logger.warning(f'Record {new_name} already exists for guild {guild_id}')
                return False
            
            # Get old record
            old_record = db[RecordsAdapter.COLL].find_one({
                '_id': f"{guild_id}:{old_name}"
            })
            
            if not old_record:
                return False
            
            # Create new record with new name
            old_record['_id'] = f"{guild_id}:{new_name}"
            old_record['record_name'] = new_name
            old_record['updated_at'] = datetime.utcnow().isoformat()
            
            db[RecordsAdapter.COLL].insert_one(old_record)
            
            # Delete old record
            db[RecordsAdapter.COLL].delete_one({
                '_id': f"{guild_id}:{old_name}"
            })
            
            logger.info(f'Renamed record {old_name} to {new_name} in guild {guild_id}')
            return True
        except Exception as e:
            logger.error(f'Failed to rename record {old_name} to {new_name}: {e}')
            return False


class GiftCodeRedemptionAdapter:
    """Adapter for tracking gift code redemptions per server"""
    COLL = 'giftcode_redemptions'

    @staticmethod
    async def ensure_indexes_async() -> bool:
        try:
            db = await _get_db_main_async()
            await db[GiftCodeRedemptionAdapter.COLL].create_index([("guild_id", 1), ("code", 1)], background=True)
            await db[GiftCodeRedemptionAdapter.COLL].create_index([("guild_id", 1)], background=True)
            return True
        except Exception as e:
            logger.error(f"Failed to ensure giftcode redemption indexes: {e}")
            return False
    
    @staticmethod
    def track_redemption(guild_id: int, code: str, fid: str, status: str) -> bool:
        """
        Track a gift code redemption attempt
        
        Args:
            guild_id: Discord server ID
            code: Gift code that was redeemed
            fid: Player FID
            status: Redemption status ('success' or 'failed')
        """
        try:
            db = _get_db_main()
            now = datetime.utcnow().isoformat()
            
            # Create unique document ID per guild+code
            doc_id = f"{guild_id}:{code}"
            
            # Add redemption to array and update stats
            db[GiftCodeRedemptionAdapter.COLL].update_one(
                {'_id': doc_id},
                {
                    '$push': {
                        'redemptions': {
                            'fid': str(fid),
                            'redeemed_at': now,
                            'status': status
                        }
                    },
                    '$inc': {
                        f'stats.{status}': 1,
                        'stats.total_attempts': 1
                    },
                    '$set': {
                        'guild_id': int(guild_id),
                        'code': str(code),
                        'last_redeemed_at': now,
                        'updated_at': now
                    },
                    '$setOnInsert': {'created_at': now}
                },
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f'Failed to track redemption for {code} in guild {guild_id}: {e}')
            return False
    
    @staticmethod
    def get_code_stats(guild_id: int, code: str) -> Optional[Dict[str, Any]]:
        """
        Get statistics for a specific code in a server
        
        Returns:
            {
                'total_attempts': int,
                'success': int,
                'failed': int,
                'unique_users': int,
                'last_redeemed_at': str
            }
        """
        try:
            db = _get_db_main()
            doc_id = f"{guild_id}:{code}"
            doc = db[GiftCodeRedemptionAdapter.COLL].find_one({'_id': doc_id})
            
            if not doc:
                return None
            
            # Count unique users
            redemptions = doc.get('redemptions', [])
            unique_fids = set(r['fid'] for r in redemptions if r.get('status') == 'success')
            
            stats = doc.get('stats', {})
            return {
                'total_attempts': stats.get('total_attempts', 0),
                'success': stats.get('success', 0),
                'failed': stats.get('failed', 0),
                'unique_users': len(unique_fids),
                'last_redeemed_at': doc.get('last_redeemed_at')
            }
        except Exception as e:
            logger.error(f'Failed to get stats for {code} in guild {guild_id}: {e}')
            return None
    
    @staticmethod
    def get_all_stats(guild_id: int) -> list:
        """
        Get statistics for all codes in a server
        
        Returns list of:
            {
                'code': str,
                'total_attempts': int,
                'success': int,
                'failed': int,
                'unique_users': int,
                'last_redeemed_at': str
            }
        """
        try:
            db = _get_db_main()
            docs = list(db[GiftCodeRedemptionAdapter.COLL].find({'guild_id': int(guild_id)}))
            
            results = []
            for doc in docs:
                redemptions = doc.get('redemptions', [])
                unique_fids = set(r['fid'] for r in redemptions if r.get('status') == 'success')
                stats = doc.get('stats', {})
                
                results.append({
                    'code': doc.get('code'),
                    'total_attempts': stats.get('total_attempts', 0),
                    'success': stats.get('success', 0),
                    'failed': stats.get('failed', 0),
                    'unique_users': len(unique_fids),
                    'last_redeemed_at': doc.get('last_redeemed_at')
                })
            
            # Sort by most used
            results.sort(key=lambda x: x['unique_users'], reverse=True)
            return results
        except Exception as e:
            logger.error(f'Failed to get all stats for guild {guild_id}: {e}')
            return []
    
    @staticmethod
    def get_top_codes(guild_id: int, limit: int = 10) -> list:
        """Get most-used gift codes in a server"""
        try:
            all_stats = GiftCodeRedemptionAdapter.get_all_stats(guild_id)
            return all_stats[:limit]
        except Exception as e:
            logger.error(f'Failed to get top codes for guild {guild_id}: {e}')
            return []

    @staticmethod
    async def track_redemption_async(guild_id: int, code: str, fid: str, status: str) -> bool:
        try:
            db = await _get_db_main_async()
            now = datetime.utcnow().isoformat()
            doc_id = f"{guild_id}:{code}"
            await db[GiftCodeRedemptionAdapter.COLL].update_one(
                {'_id': doc_id},
                {
                    '$push': {'redemptions': {'fid': str(fid), 'redeemed_at': now, 'status': status}},
                    '$inc': {f'stats.{status}': 1, 'stats.total_attempts': 1},
                    '$set': {'guild_id': int(guild_id), 'code': str(code), 'last_redeemed_at': now, 'updated_at': now},
                    '$setOnInsert': {'created_at': now}
                },
                upsert=True
            )
            return True
        except Exception: return False

    @staticmethod
    async def track_redemptions_bulk_async(guild_id: int, code: str, records: List[Dict[str, str]]) -> bool:
        """Track many redemption attempts for one guild/code with a single MongoDB update."""
        if not records:
            return True
        try:
            db = await _get_db_main_async()
            now = datetime.utcnow().isoformat()
            doc_id = f"{guild_id}:{code}"

            redemptions = []
            status_counts: Dict[str, int] = {}
            for record in records:
                fid = str(record.get("fid", "")).strip()
                status = str(record.get("status", "failed") or "failed")
                if not fid:
                    continue
                redemptions.append({"fid": fid, "redeemed_at": now, "status": status})
                status_counts[status] = status_counts.get(status, 0) + 1

            if not redemptions:
                return True

            inc_doc = {"stats.total_attempts": len(redemptions)}
            for status, count in status_counts.items():
                inc_doc[f"stats.{status}"] = count

            await db[GiftCodeRedemptionAdapter.COLL].update_one(
                {"_id": doc_id},
                {
                    "$push": {"redemptions": {"$each": redemptions}},
                    "$inc": inc_doc,
                    "$set": {
                        "guild_id": int(guild_id),
                        "code": str(code),
                        "last_redeemed_at": now,
                        "updated_at": now,
                    },
                    "$setOnInsert": {"created_at": now},
                },
                upsert=True,
            )
            return True
        except Exception as e:
            logger.error(f"Failed to bulk track redemptions for {code} in guild {guild_id}: {e}")
            return False

    @staticmethod
    async def get_code_stats_async(guild_id: int, code: str) -> Optional[Dict[str, Any]]:
        try:
            db = await _get_db_main_async()
            doc = await db[GiftCodeRedemptionAdapter.COLL].find_one({'_id': f"{guild_id}:{code}"})
            if not doc: return None
            redemptions = doc.get('redemptions', [])
            unique_fids = set(r['fid'] for r in redemptions if r.get('status') == 'success')
            stats = doc.get('stats', {})
            return {'total_attempts': stats.get('total_attempts', 0), 'success': stats.get('success', 0), 'failed': stats.get('failed', 0), 'unique_users': len(unique_fids), 'last_redeemed_at': doc.get('last_redeemed_at')}
        except Exception: return None

    @staticmethod
    async def get_all_stats_async(guild_id: int) -> list:
        try:
            db = await _get_db_main_async()
            cursor = db[GiftCodeRedemptionAdapter.COLL].find({'guild_id': int(guild_id)})
            docs = await cursor.to_list(length=None)
            results = []
            for doc in docs:
                redemptions = doc.get('redemptions', [])
                unique_fids = set(r['fid'] for r in redemptions if r.get('status') == 'success')
                stats = doc.get('stats', {})
                results.append({'code': doc.get('code'), 'total_attempts': stats.get('total_attempts', 0), 'success': stats.get('success', 0), 'failed': stats.get('failed', 0), 'unique_users': len(unique_fids), 'last_redeemed_at': doc.get('last_redeemed_at')})
            results.sort(key=lambda x: x['unique_users'], reverse=True)
            return results
        except Exception: return []

    @staticmethod
    async def get_recent_redemptions_async(limit: int = 80) -> list:
        """Return recent per-code redemption summaries for the public bot feed."""
        try:
            db = await _get_db_main_async()
            safe_limit = max(1, min(int(limit), 200))
            cursor = db[GiftCodeRedemptionAdapter.COLL].find({}).sort('last_redeemed_at', -1).limit(safe_limit)
            docs = await cursor.to_list(length=None)
            results = []
            for doc in docs:
                doc['id'] = str(doc.get('_id'))
                doc.pop('_id', None)
                results.append(doc)
            return results
        except Exception as e:
            logger.error(f'Failed to get recent gift code redemptions: {e}')
            return []

class PersistentViewsAdapter:
    """Adapter for managing persistent views in MongoDB"""
    COLL = 'persistent_views'

    @staticmethod
    def register_view(guild_id: int, channel_id: int, message_id: int, view_type: str, metadata: Dict[str, Any] = None) -> bool:
        """Register a persistent view to be restored on startup"""
        try:
            db = _get_db_main()
            now = datetime.utcnow().isoformat()
            if metadata is None:
                metadata = {}
                
            data = {
                'guild_id': int(guild_id),
                'channel_id': int(channel_id),
                'message_id': int(message_id),
                'view_type': view_type,
                'metadata': metadata,
                'updated_at': now
            }
            
            # Use message_id as the document ID
            db[PersistentViewsAdapter.COLL].update_one(
                {'_id': str(message_id)},
                {'$set': data, '$setOnInsert': {'created_at': now}},
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f'Failed to register persistent view {message_id}: {e}')
            return False

    @staticmethod
    def get_all_views() -> list:
        """Get all registered persistent views"""
        try:
            db = _get_db_main()
            docs = list(db[PersistentViewsAdapter.COLL].find({}))
            # Convert _id (ObjectId) to string for JSON serialization safety
            for doc in docs:
                if '_id' in doc:
                    doc['_id'] = str(doc['_id'])
            return docs
        except Exception as e:
            logger.error(f'Failed to get all persistent views: {e}')
            return []

    @staticmethod
    def remove_view(message_id: int) -> bool:
        """Remove a persistent view (e.g. if message is deleted)"""
        try:
            db = _get_db_main()
            result = db[PersistentViewsAdapter.COLL].delete_one({'_id': str(message_id)})
            return result.deleted_count > 0
        except Exception as e:
            logger.error(f'Failed to remove persistent view {message_id}: {e}')
            return False

    @staticmethod
    def view_exists(message_id: int) -> bool:
        """Check if a persistent view exists"""
        try:
            db = _get_db_main()
            count = db[PersistentViewsAdapter.COLL].count_documents({'_id': str(message_id)})
            return count > 0
        except Exception as e:
            logger.error(f'Failed to check view existence {message_id}: {e}')
            return False

    @staticmethod
    async def register_view_async(guild_id: int, channel_id: int, message_id: int, view_type: str, metadata: Dict[str, Any] = None) -> bool:
        try:
            db = await _get_db_main_async()
            now = datetime.utcnow().isoformat()
            if metadata is None: metadata = {}
            data = {'guild_id': int(guild_id), 'channel_id': int(channel_id), 'message_id': int(message_id), 'view_type': view_type, 'metadata': metadata, 'updated_at': now}
            await db[PersistentViewsAdapter.COLL].update_one({'_id': str(message_id)}, {'$set': data, '$setOnInsert': {'created_at': now}}, upsert=True)
            return True
        except Exception: return False

    @staticmethod
    async def get_all_views_async() -> list:
        try:
            db = await _get_db_main_async()
            cursor = db[PersistentViewsAdapter.COLL].find({})
            docs = await cursor.to_list(length=None)
            for doc in docs:
                if '_id' in doc: doc['_id'] = str(doc['_id'])
            return docs
        except Exception: return []

    @staticmethod
    async def remove_view_async(message_id: int) -> bool:
        try:
            db = await _get_db_main_async()
            result = await db[PersistentViewsAdapter.COLL].delete_one({'_id': str(message_id)})
            return result.deleted_count > 0
        except Exception: return False



class AutoRedeemedCodesAdapter:
    COLL = 'auto_redeemed_codes'

    @staticmethod
    async def ensure_indexes_async() -> bool:
        try:
            db = await _get_db_main_async()
            await db[AutoRedeemedCodesAdapter.COLL].create_index(
                [("guild_id", 1), ("code", 1), ("fid", 1)],
                unique=True,
                background=True,
            )
            await db[AutoRedeemedCodesAdapter.COLL].create_index([("guild_id", 1), ("code", 1)], background=True)
            return True
        except Exception as e:
            logger.error(f"Failed to ensure auto redeemed code indexes: {e}")
            return False
    
    @staticmethod
    def mark_code_redeemed_for_member(guild_id: int, code: str, fid: str, status: str) -> bool:
        try:
            db = _get_db_main()
            now = datetime.utcnow().isoformat()
            db[AutoRedeemedCodesAdapter.COLL].update_one(
                {'guild_id': int(guild_id), 'code': code, 'fid': str(fid)},
                {
                    '$set': {
                        'status': status,
                        'updated_at': now
                    },
                    '$setOnInsert': {'created_at': now}
                },
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f'Failed to mark code {code} redeemed for {fid}: {e}')
            return False

    @staticmethod
    def is_redeemed(guild_id: int, code: str, fid: str) -> bool:
        try:
            db = _get_db_main()
            return db[AutoRedeemedCodesAdapter.COLL].count_documents({'guild_id': int(guild_id), 'code': code, 'fid': str(fid)}) > 0
        except Exception:
            return False

    @staticmethod
    def batch_check_members(guild_id: int, giftcode: str, fids: List[str]) -> Dict[str, bool]:
        """Batch check if multiple members have redeemed a code"""
        try:
            db = _get_db_main()
            # Find all redemptions for this code and these FIDs
            docs = db[AutoRedeemedCodesAdapter.COLL].find({
                'guild_id': int(guild_id),
                'code': giftcode,
                'fid': {'$in': [str(fid) for fid in fids]}
            })
            
            redeemed_fids = {d.get('fid') for d in docs}
            return {str(fid): str(fid) in redeemed_fids for fid in fids}
        except Exception as e:
            logger.error(f'Failed to batch check redemptions for {giftcode}: {e}')
            return {str(fid): False for fid in fids}

    @staticmethod
    async def mark_code_redeemed_for_member_async(guild_id: int, code: str, fid: str, status: str) -> bool:
        try:
            db = await _get_db_main_async()
            now = datetime.utcnow().isoformat()
            await db[AutoRedeemedCodesAdapter.COLL].update_one(
                {'guild_id': int(guild_id), 'code': code, 'fid': str(fid)},
                {
                    '$set': {'status': status, 'updated_at': now},
                    '$setOnInsert': {'created_at': now}
                },
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f'Failed to mark code (async) {code} redeemed for {fid}: {e}')
            return False

    @staticmethod
    async def mark_codes_redeemed_for_members_bulk_async(guild_id: int, code: str, records: List[Dict[str, str]]) -> bool:
        """Mark many members as redeemed for one guild/code using unordered bulk writes."""
        if not records:
            return True
        try:
            db = await _get_db_main_async()
            now = datetime.utcnow().isoformat()
            operations = []
            seen_fids = set()
            for record in records:
                fid = str(record.get("fid", "")).strip()
                if not fid or fid in seen_fids:
                    continue
                seen_fids.add(fid)
                status = str(record.get("status", "success") or "success")
                operations.append(
                    UpdateOne(
                        {"guild_id": int(guild_id), "code": code, "fid": fid},
                        {
                            "$set": {"status": status, "updated_at": now},
                            "$setOnInsert": {"created_at": now},
                        },
                        upsert=True,
                    )
                )

            if operations:
                await db[AutoRedeemedCodesAdapter.COLL].bulk_write(operations, ordered=False)
            return True
        except Exception as e:
            logger.error(f"Failed to bulk mark code (async) {code} redeemed for guild {guild_id}: {e}")
            return False

    @staticmethod
    async def is_redeemed_async(guild_id: int, code: str, fid: str) -> bool:
        try:
            db = await _get_db_main_async()
            count = await db[AutoRedeemedCodesAdapter.COLL].count_documents({'guild_id': int(guild_id), 'code': code, 'fid': str(fid)})
            return count > 0
        except Exception:
            return False

    @staticmethod
    async def batch_check_members_async(guild_id: int, giftcode: str, fids: List[str]) -> Dict[str, bool]:
        try:
            db = await _get_db_main_async()
            cursor = db[AutoRedeemedCodesAdapter.COLL].find({
                'guild_id': int(guild_id),
                'code': giftcode,
                'fid': {'$in': [str(fid) for fid in fids]}
            })
            docs = await cursor.to_list(length=None)
            redeemed_fids = {d.get('fid') for d in docs}
            return {str(fid): str(fid) in redeemed_fids for fid in fids}
        except Exception as e:
            logger.error(f'Failed to batch check redemptions (async) for {giftcode}: {e}')
            return {str(fid): False for fid in fids}

    @staticmethod
    def reset_code_redemptions(giftcode: str) -> int:
        """Delete ALL per-member redemption records for a gift code (sync). Returns count deleted."""
        try:
            db = _get_db_main()
            # Robust delete: check both 'code' and 'giftcode' / 'gift_code' fields
            result = db[AutoRedeemedCodesAdapter.COLL].delete_many(
                {'$or': [{'code': giftcode}, {'giftcode': giftcode}, {'gift_code': giftcode}]}
            )
            logger.info(f'[AutoRedeemed] Deleted {result.deleted_count} redemption records for code {giftcode}')
            return result.deleted_count
        except Exception as e:
            logger.error(f'Failed to reset redemptions for {giftcode}: {e}')
            return 0

    @staticmethod
    async def reset_code_redemptions_async(giftcode: str) -> int:
        """Delete ALL per-member redemption records for a gift code (async). Returns count deleted."""
        try:
            db = await _get_db_main_async()
            # Robust delete: check both 'code' and 'giftcode' / 'gift_code' fields
            result = await db[AutoRedeemedCodesAdapter.COLL].delete_many(
                {'$or': [{'code': giftcode}, {'giftcode': giftcode}, {'gift_code': giftcode}]}
            )
            logger.info(f'[AutoRedeemed] Deleted {result.deleted_count} redemption records for code {giftcode}')
            return result.deleted_count
        except Exception as e:
            logger.error(f'Failed to reset redemptions (async) for {giftcode}: {e}')
            return 0


# Alias for backward compatibility
PlayerTimezonesAdapter = UserTimezonesAdapter


class AutoTranslateAdapter:
    """Adapter for managing auto-translate configurations in MongoDB"""
    COLL = 'auto_translate_configs'

    @staticmethod
    def create_config(guild_id: int, data: Dict[str, Any]) -> Optional[str]:
        """Create a new auto-translate configuration"""
        try:
            db = _get_db_main()
            config_id = str(uuid.uuid4())
            now = datetime.utcnow().isoformat()
            
            doc = data.copy()
            doc['_id'] = config_id
            doc['config_id'] = config_id
            doc['guild_id'] = int(guild_id)
            doc['enabled'] = True
            doc['created_at'] = now
            doc['updated_at'] = now
            
            db[AutoTranslateAdapter.COLL].insert_one(doc)
            logger.info(f"Created auto-translate config {config_id} for guild {guild_id}")
            return config_id
        except Exception as e:
            logger.error(f"Failed to create auto-translate config: {e}")
            return None

    @staticmethod
    def get_config(config_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific configuration by ID"""
        try:
            db = _get_db_main()
            doc = db[AutoTranslateAdapter.COLL].find_one({'_id': config_id})
            if doc:
                doc['config_id'] = str(doc['_id'])
                doc.pop('_id', None)
            return doc
        except Exception as e:
            logger.error(f"Failed to get auto-translate config {config_id}: {e}")
            return None

    @staticmethod
    def get_guild_configs(guild_id: int) -> List[Dict[str, Any]]:
        """Get all configurations for a guild"""
        try:
            db = _get_db_main()
            docs = list(db[AutoTranslateAdapter.COLL].find({'guild_id': int(guild_id)}))
            for doc in docs:
                doc['config_id'] = str(doc['_id'])
                doc.pop('_id', None)
            return docs
        except Exception as e:
            logger.error(f"Failed to get auto-translate configs for guild {guild_id}: {e}")
            return []

    @staticmethod
    def get_configs_for_channel(channel_id: int) -> List[Dict[str, Any]]:
        """Get all enabled configurations where channel_id is the source"""
        try:
            db = _get_db_main()
            # Find configs where source_channel_id matches and enabled is True
            docs = list(db[AutoTranslateAdapter.COLL].find({
                'source_channel_id': int(channel_id),
                'enabled': True
            }))
            for doc in docs:
                doc['config_id'] = str(doc['_id'])
                doc.pop('_id', None)
            return docs
        except Exception as e:
            logger.error(f"Failed to get auto-translate configs for channel {channel_id}: {e}")
            return []

    @staticmethod
    def update_config(config_id: str, data: Dict[str, Any]) -> bool:
        """Update a configuration"""
        try:
            db = _get_db_main()
            now = datetime.utcnow().isoformat()
            
            update_data = data.copy()
            update_data['updated_at'] = now
            # Remove immutable fields if present
            update_data.pop('config_id', None)
            update_data.pop('guild_id', None)
            update_data.pop('_id', None)
            update_data.pop('created_at', None)
            
            result = db[AutoTranslateAdapter.COLL].update_one(
                {'_id': config_id},
                {'$set': update_data}
            )
            return result.modified_count > 0 or result.matched_count > 0
        except Exception as e:
            logger.error(f"Failed to update auto-translate config {config_id}: {e}")
            return False

    @staticmethod
    def delete_config(config_id: str) -> bool:
        """Delete a configuration"""
        try:
            db = _get_db_main()
            result = db[AutoTranslateAdapter.COLL].delete_one({'_id': config_id})
            return result.deleted_count > 0
        except Exception as e:
            logger.error(f"Failed to delete auto-translate config {config_id}: {e}")
            return False

    @staticmethod
    def toggle_config(config_id: str, enabled: bool) -> bool:
        """Enable or disable a configuration"""
        try:
            db = _get_db_main()
            now = datetime.utcnow().isoformat()
            result = db[AutoTranslateAdapter.COLL].update_one(
                {'_id': config_id},
                {'$set': {'enabled': enabled, 'updated_at': now}}
            )
            return result.modified_count > 0 or result.matched_count > 0
        except Exception as e:
            logger.error(f"Failed to toggle auto-translate config {config_id}: {e}")
            return False

    @staticmethod
    async def create_config_async(guild_id: int, data: Dict[str, Any]) -> Optional[str]:
        try:
            db = await _get_db_main_async()
            config_id = str(uuid.uuid4())
            now = datetime.utcnow().isoformat()
            doc = data.copy()
            doc['_id'] = config_id
            doc['config_id'] = config_id
            doc['guild_id'] = int(guild_id)
            doc['enabled'] = True
            doc['created_at'] = now
            doc['updated_at'] = now
            await db[AutoTranslateAdapter.COLL].insert_one(doc)
            return config_id
        except Exception: return None

    @staticmethod
    async def get_config_async(config_id: str) -> Optional[Dict[str, Any]]:
        try:
            db = await _get_db_main_async()
            doc = await db[AutoTranslateAdapter.COLL].find_one({'_id': config_id})
            if doc:
                doc['config_id'] = str(doc['_id'])
                doc.pop('_id', None)
            return doc
        except Exception: return None

    @staticmethod
    async def get_guild_configs_async(guild_id: int) -> List[Dict[str, Any]]:
        try:
            db = await _get_db_main_async()
            cursor = db[AutoTranslateAdapter.COLL].find({'guild_id': int(guild_id)})
            docs = await cursor.to_list(length=None)
            for doc in docs:
                doc['config_id'] = str(doc['_id'])
                doc.pop('_id', None)
            return docs
        except Exception: return []

    @staticmethod
    async def get_configs_for_channel_async(channel_id: int) -> List[Dict[str, Any]]:
        try:
            db = await _get_db_main_async()
            cursor = db[AutoTranslateAdapter.COLL].find({'source_channel_id': int(channel_id), 'enabled': True})
            docs = await cursor.to_list(length=None)
            for doc in docs:
                doc['config_id'] = str(doc['_id'])
                doc.pop('_id', None)
            return docs
        except Exception: return []

    @staticmethod
    async def update_config_async(config_id: str, data: Dict[str, Any]) -> bool:
        try:
            db = await _get_db_main_async()
            now = datetime.utcnow().isoformat()
            update_data = data.copy()
            update_data['updated_at'] = now
            for k in ['config_id', 'guild_id', '_id', 'created_at']: update_data.pop(k, None)
            result = await db[AutoTranslateAdapter.COLL].update_one({'_id': config_id}, {'$set': update_data})
            return result.modified_count > 0 or result.matched_count > 0
        except Exception: return False

    @staticmethod
    async def delete_config_async(config_id: str) -> bool:
        try:
            db = await _get_db_main_async()
            result = await db[AutoTranslateAdapter.COLL].delete_one({'_id': config_id})
            return result.deleted_count > 0
        except Exception: return False

    @staticmethod
    async def toggle_config_async(config_id: str, enabled: bool) -> bool:
        try:
            db = await _get_db_main_async()
            now = datetime.utcnow().isoformat()
            result = await db[AutoTranslateAdapter.COLL].update_one({'_id': config_id}, {'$set': {'enabled': enabled, 'updated_at': now}})
            return result.modified_count > 0 or result.matched_count > 0
        except Exception: return False


# ============================================================================
# SERVER LIMITS ADAPTER — Per-guild controls for scaling (1000+ servers)
# ============================================================================

class ServerLimitsAdapter:
    """Adapter for managing per-server limits and locks in MongoDB.
    
    Document schema:
    {
        "_id": "<guild_id>",
        "max_auto_redeem_members": -1,       # -1 = unlimited
        "alliance_monitor_locked": false,
        "updated_by": 123456789,
        "updated_at": "...",
        "created_at": "..."
    }
    """
    COLL = 'server_limits'

    # --- Sync methods ---

    @staticmethod
    def get(guild_id: int) -> Optional[Dict[str, Any]]:
        """Get limits for a guild. Returns None if no limits are set (defaults apply)."""
        try:
            db = _get_db_main()
            doc = db[ServerLimitsAdapter.COLL].find_one({'_id': str(guild_id)})
            if doc:
                doc['guild_id'] = str(doc['_id'])
                doc.pop('_id', None)
            return doc
        except Exception as e:
            logger.error(f'Failed to get server limits for guild {guild_id}: {e}')
            return None

    @staticmethod
    def set(guild_id: int, data: Dict[str, Any]) -> bool:
        """Upsert limits for a guild."""
        try:
            db = _get_db_main()
            now = datetime.utcnow().isoformat()
            payload = data.copy()
            payload.pop('created_at', None)
            payload.pop('guild_id', None)
            payload.pop('_id', None)
            payload['updated_at'] = now

            db[ServerLimitsAdapter.COLL].update_one(
                {'_id': str(guild_id)},
                {'$set': payload, '$setOnInsert': {'created_at': now}},
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f'Failed to set server limits for guild {guild_id}: {e}')
            return False

    @staticmethod
    def get_all() -> List[Dict[str, Any]]:
        """Get limits for all guilds that have custom settings."""
        try:
            db = _get_db_main()
            docs = list(db[ServerLimitsAdapter.COLL].find({}))
            results = []
            for doc in docs:
                doc['guild_id'] = str(doc['_id'])
                doc.pop('_id', None)
                results.append(doc)
            return results
        except Exception as e:
            logger.error(f'Failed to get all server limits: {e}')
            return []

    @staticmethod
    def delete(guild_id: int) -> bool:
        """Delete limits for a guild (reset to defaults)."""
        try:
            db = _get_db_main()
            result = db[ServerLimitsAdapter.COLL].delete_one({'_id': str(guild_id)})
            return result.deleted_count > 0
        except Exception as e:
            logger.error(f'Failed to delete server limits for guild {guild_id}: {e}')
            return False

    # --- Async methods ---

    @staticmethod
    async def get_async(guild_id: int) -> Optional[Dict[str, Any]]:
        """Get limits for a guild asynchronously."""
        try:
            db = await _get_db_main_async()
            doc = await db[ServerLimitsAdapter.COLL].find_one({'_id': str(guild_id)})
            if doc:
                doc['guild_id'] = str(doc['_id'])
                doc.pop('_id', None)
            return doc
        except Exception as e:
            logger.error(f'Failed to get server limits (async) for guild {guild_id}: {e}')
            return None

    @staticmethod
    async def set_async(guild_id: int, data: Dict[str, Any]) -> bool:
        """Upsert limits for a guild asynchronously."""
        try:
            db = await _get_db_main_async()
            now = datetime.utcnow().isoformat()
            payload = data.copy()
            payload.pop('created_at', None)
            payload.pop('guild_id', None)
            payload.pop('_id', None)
            payload['updated_at'] = now

            await db[ServerLimitsAdapter.COLL].update_one(
                {'_id': str(guild_id)},
                {'$set': payload, '$setOnInsert': {'created_at': now}},
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f'Failed to set server limits (async) for guild {guild_id}: {e}')
            return False

    @staticmethod
    async def get_all_async() -> List[Dict[str, Any]]:
        """Get limits for all guilds asynchronously."""
        try:
            db = await _get_db_main_async()
            cursor = db[ServerLimitsAdapter.COLL].find({})
            docs = await cursor.to_list(length=None)
            results = []
            for doc in docs:
                doc['guild_id'] = str(doc['_id'])
                doc.pop('_id', None)
                results.append(doc)
            return results
        except Exception as e:
            logger.error(f'Failed to get all server limits (async): {e}')
            return []

    @staticmethod
    async def delete_async(guild_id: int) -> bool:
        """Delete limits for a guild asynchronously (reset to defaults)."""
        try:
            db = await _get_db_main_async()
            result = await db[ServerLimitsAdapter.COLL].delete_one({'_id': str(guild_id)})
            return result.deleted_count > 0
        except Exception as e:
            logger.error(f'Failed to delete server limits (async) for guild {guild_id}: {e}')
            return False

    # --- Helper methods ---

    @staticmethod
    def get_max_redeem_members(guild_id: int) -> int:
        """Get the max auto-redeem members for a guild. Returns -1 for unlimited."""
        try:
            doc = ServerLimitsAdapter.get(guild_id)
            if doc:
                return int(doc.get('max_auto_redeem_members', -1))
            return -1  # default: unlimited
        except Exception:
            return -1

    @staticmethod
    async def get_max_redeem_members_async(guild_id: int) -> int:
        """Get the max auto-redeem members for a guild asynchronously. Returns -1 for unlimited."""
        try:
            doc = await ServerLimitsAdapter.get_async(guild_id)
            if doc:
                return int(doc.get('max_auto_redeem_members', -1))
            return -1
        except Exception:
            return -1

    @staticmethod
    def is_monitor_locked(guild_id: int) -> bool:
        """Check if alliance monitoring is locked for a guild."""
        try:
            doc = ServerLimitsAdapter.get(guild_id)
            if doc:
                return bool(doc.get('alliance_monitor_locked', False))
            return False  # default: unlocked
        except Exception:
            return False

    @staticmethod
    async def is_monitor_locked_async(guild_id: int) -> bool:
        """Check if alliance monitoring is locked for a guild asynchronously."""
        try:
            doc = await ServerLimitsAdapter.get_async(guild_id)
            if doc:
                return bool(doc.get('alliance_monitor_locked', False))
            return False
        except Exception:
            return False



# BotActivityAdapter is defined once at the top of the file


class PendingConfigAdapter:
    """Adapter for managing pending server self-registration requests.
    
    Stores: guild_id, guild_name, alliance_name, access_code, state, discord_user_id,
    discord_username, status (pending/approved/denied).
    One request per guild; one approved/pending per user globally.
    """
    COLL = 'pending_configs'

    @staticmethod
    async def submit_async(guild_id: int, guild_name: str, alliance_name: str,
                           access_code: str, discord_user_id: int,
                           discord_username: str, state: Optional[int] = None) -> bool:
        """Submit a new pending config request."""
        try:
            db = await _get_db_main_async()
            now = datetime.utcnow().isoformat()
            payload = {
                'guild_id': str(guild_id),
                'guild_name': str(guild_name),
                'alliance_name': str(alliance_name),
                'access_code': str(access_code),
                'discord_user_id': str(discord_user_id),
                'discord_username': str(discord_username),
                'status': 'pending',
                'submitted_at': now,
                'updated_at': now
            }
            if state is not None:
                payload['state'] = int(state)

            await db[PendingConfigAdapter.COLL].update_one(
                {'guild_id': str(guild_id)},
                {
                    '$set': payload,
                    '$setOnInsert': {'created_at': now}
                },
                upsert=True
            )
            logger.info(f'Pending config submitted for guild {guild_id} by user {discord_user_id}')
            return True
        except Exception as e:
            logger.error(f'Failed to submit pending config for guild {guild_id}: {e}')
            return False

    @staticmethod
    async def get_by_guild_async(guild_id: int) -> Optional[Dict[str, Any]]:
        """Get the pending/approved config request for a guild."""
        try:
            db = await _get_db_main_async()
            doc = await db[PendingConfigAdapter.COLL].find_one({'guild_id': str(guild_id)})
            return doc
        except Exception as e:
            logger.error(f'Failed to get pending config for guild {guild_id}: {e}')
            return None

    @staticmethod
    async def get_by_user_async(discord_user_id: int) -> Optional[Dict[str, Any]]:
        """Check if a user already has a pending/approved request on any server."""
        try:
            db = await _get_db_main_async()
            doc = await db[PendingConfigAdapter.COLL].find_one({
                'discord_user_id': str(discord_user_id),
                'status': {'$in': ['pending', 'approved']}
            })
            return doc
        except Exception as e:
            logger.error(f'Failed to get pending config for user {discord_user_id}: {e}')
            return None

    @staticmethod
    async def get_all_pending_async() -> list:
        """Get all pending requests for admin review."""
        try:
            db = await _get_db_main_async()
            cursor = db[PendingConfigAdapter.COLL].find({'status': 'pending'})
            docs = await cursor.to_list(length=None)
            return docs
        except Exception as e:
            logger.error(f'Failed to get all pending configs: {e}')
            return []

    @staticmethod
    async def approve_async(guild_id: int, admin_user_id: int) -> bool:
        """Approve request: apply access code + alliance name to server_alliances collection."""
        try:
            db = await _get_db_main_async()
            doc = await db[PendingConfigAdapter.COLL].find_one(
                {'guild_id': str(guild_id), 'status': 'pending'}
            )
            if not doc:
                return False
            now = datetime.utcnow().isoformat()
            server_payload = {
                'id': int(guild_id),
                'alliance_name': doc['alliance_name'],
                'member_list_password': doc['access_code'],
                'password_set_by': int(admin_user_id),
                'password_set_at': now,
                'updated_at': now
            }
            if doc.get('state') is not None:
                server_payload['state'] = int(doc['state'])

            await db[ServerAllianceAdapter.COLL].update_one(
                {'_id': str(guild_id)},
                {
                    '$set': server_payload,
                    '$setOnInsert': {'created_at': now}
                },
                upsert=True
            )
            await db[PendingConfigAdapter.COLL].update_one(
                {'guild_id': str(guild_id)},
                {'$set': {
                    'status': 'approved',
                    'approved_by': int(admin_user_id),
                    'approved_at': now,
                    'updated_at': now
                }}
            )
            logger.info(f'Approved pending config for guild {guild_id} by admin {admin_user_id}')
            return True
        except Exception as e:
            logger.error(f'Failed to approve pending config for guild {guild_id}: {e}')
            return False

    @staticmethod
    async def deny_async(guild_id: int, admin_user_id: int) -> bool:
        """Deny a pending config request."""
        try:
            db = await _get_db_main_async()
            now = datetime.utcnow().isoformat()
            await db[PendingConfigAdapter.COLL].update_one(
                {'guild_id': str(guild_id)},
                {'$set': {
                    'status': 'denied',
                    'denied_by': int(admin_user_id),
                    'denied_at': now,
                    'updated_at': now
                }}
            )
            logger.info(f'Denied pending config for guild {guild_id} by admin {admin_user_id}')
            return True
        except Exception as e:
            logger.error(f'Failed to deny pending config for guild {guild_id}: {e}')
            return False
