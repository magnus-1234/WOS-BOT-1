from typing import Dict, Any, Optional, List, Union
import logging
from datetime import datetime
from .mongo_adapters import _get_db_main, _get_db_main_async

logger = logging.getLogger(__name__)

class ModerationSettingsAdapter:
    COLL = 'moderation_settings'

    @staticmethod
    async def get_settings(guild_id: int) -> Dict[str, Any]:
        try:
            db = await _get_db_main_async()
            doc = await db[ModerationSettingsAdapter.COLL].find_one({'_id': str(guild_id)})
            if doc:
                doc.pop('_id', None)
                return doc
            return {
                "automod": {
                    "enabled": False,
                    "anti_spam": False,
                    "anti_link": False,
                    "anti_invites": False,
                    "max_mentions": 5,
                    "bypass_roles": []
                },
                "logging": {
                    "enabled": False,
                    "channel_id": None,
                    "events": ["ban", "kick", "warn", "mute", "unban", "unmute"]
                },
                "escalation": {
                    "enabled": False,
                    "rules": []
                }
            }
        except Exception as e:
            logger.error(f'Failed to get moderation settings for guild {guild_id}: {e}')
            return {}

    @staticmethod
    async def set_settings(guild_id: int, settings: Dict[str, Any]) -> bool:
        try:
            db = await _get_db_main_async()
            now = datetime.utcnow().isoformat()
            await db[ModerationSettingsAdapter.COLL].update_one(
                {'_id': str(guild_id)},
                {'$set': {**settings, 'updated_at': now}, '$setOnInsert': {'created_at': now}},
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f'Failed to set moderation settings for guild {guild_id}: {e}')
            return False

class BlacklistAdapter:
    COLL = 'moderation_blacklist'

    @staticmethod
    async def get_blacklist(guild_id: int) -> List[str]:
        try:
            db = await _get_db_main_async()
            doc = await db[BlacklistAdapter.COLL].find_one({'_id': str(guild_id)})
            if doc:
                return doc.get('words', [])
            return []
        except Exception as e:
            logger.error(f'Failed to get blacklist for guild {guild_id}: {e}')
            return []

    @staticmethod
    async def set_blacklist(guild_id: int, words: List[str]) -> bool:
        try:
            db = await _get_db_main_async()
            now = datetime.utcnow().isoformat()
            await db[BlacklistAdapter.COLL].update_one(
                {'_id': str(guild_id)},
                {'$set': {'words': words, 'updated_at': now}, '$setOnInsert': {'created_at': now}},
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f'Failed to set blacklist for guild {guild_id}: {e}')
            return False

class ModerationActionsAdapter:
    COLL = 'moderation_actions'

    @staticmethod
    async def add_action(guild_id: int, user_id: int, moderator_id: int, action_type: str, reason: str = None, duration: int = None) -> bool:
        try:
            db = await _get_db_main_async()
            now = datetime.utcnow().isoformat()
            await db[ModerationActionsAdapter.COLL].insert_one({
                'guild_id': int(guild_id),
                'user_id': int(user_id),
                'moderator_id': int(moderator_id),
                'action_type': action_type,
                'reason': reason,
                'duration': duration,
                'created_at': now
            })
            return True
        except Exception as e:
            logger.error(f'Failed to add moderation action for guild {guild_id}: {e}')
            return False

    @staticmethod
    async def get_actions(guild_id: int, user_id: int = None, limit: int = 100) -> List[Dict[str, Any]]:
        try:
            db = await _get_db_main_async()
            query = {'guild_id': int(guild_id)}
            if user_id:
                query['user_id'] = int(user_id)
            
            cursor = db[ModerationActionsAdapter.COLL].find(query).sort('created_at', -1).limit(limit)
            docs = await cursor.to_list(length=None)
            for doc in docs:
                doc['_id'] = str(doc['_id'])
            return docs
        except Exception as e:
            logger.error(f'Failed to get moderation actions for guild {guild_id}: {e}')
            return []

    @staticmethod
    async def get_stats(guild_id: int) -> Dict[str, int]:
        try:
            db = await _get_db_main_async()
            pipeline = [
                {'$match': {'guild_id': int(guild_id)}},
                {'$group': {'_id': '$action_type', 'count': {'$sum': 1}}}
            ]
            cursor = db[ModerationActionsAdapter.COLL].aggregate(pipeline)
            stats = {
                "warn": 0,
                "mute": 0,
                "ban": 0,
                "kick": 0,
                "automod": 0,
                "total": 0
            }
            async for doc in cursor:
                action = str(doc['_id']).lower()
                count = doc['count']
                if action in stats:
                    stats[action] = count
                stats['total'] += count
            return stats
        except Exception as e:
            logger.error(f'Failed to get moderation stats for guild {guild_id}: {e}')
            return {"warn": 0, "mute": 0, "ban": 0, "kick": 0, "automod": 0, "total": 0}
