from typing import Dict, Any, Optional, List, Union
import logging
from datetime import datetime
from .mongo_adapters import _get_db_main, _get_db_main_async

logger = logging.getLogger(__name__)

class ReactionRolesAdapter:
    COLL = 'reaction_roles'

    @staticmethod
    async def get_reaction_roles(guild_id: int) -> List[Dict[str, Any]]:
        try:
            db = await _get_db_main_async()
            cursor = db[ReactionRolesAdapter.COLL].find({'guild_id': int(guild_id)})
            docs = await cursor.to_list(length=None)
            for doc in docs:
                doc['_id'] = str(doc['_id'])
            return docs
        except Exception as e:
            logger.error(f'Failed to get reaction roles for guild {guild_id}: {e}')
            return []

    @staticmethod
    async def add_reaction_role(guild_id: int, message_id: int, emoji: str, role_id: int) -> bool:
        try:
            db = await _get_db_main_async()
            now = datetime.utcnow().isoformat()
            await db[ReactionRolesAdapter.COLL].update_one(
                {'guild_id': int(guild_id), 'message_id': int(message_id), 'emoji': emoji},
                {'$set': {'guild_id': int(guild_id), 'message_id': int(message_id), 'emoji': emoji, 'role_id': int(role_id), 'updated_at': now}, '$setOnInsert': {'created_at': now}},
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f'Failed to add reaction role for guild {guild_id}: {e}')
            return False

    @staticmethod
    async def remove_reaction_role(guild_id: int, message_id: int, emoji: str) -> bool:
        try:
            db = await _get_db_main_async()
            res = await db[ReactionRolesAdapter.COLL].delete_one({'guild_id': int(guild_id), 'message_id': int(message_id), 'emoji': emoji})
            return res.deleted_count > 0
        except Exception as e:
            logger.error(f'Failed to remove reaction role for guild {guild_id}: {e}')
            return False

class AutoRolesAdapter:
    COLL = 'auto_roles'

    @staticmethod
    async def get_auto_roles(guild_id: int) -> List[int]:
        try:
            db = await _get_db_main_async()
            doc = await db[AutoRolesAdapter.COLL].find_one({'_id': str(guild_id)})
            if doc:
                return doc.get('role_ids', [])
            return []
        except Exception as e:
            logger.error(f'Failed to get auto roles for guild {guild_id}: {e}')
            return []

    @staticmethod
    async def set_auto_roles(guild_id: int, role_ids: List[int]) -> bool:
        try:
            db = await _get_db_main_async()
            now = datetime.utcnow().isoformat()
            await db[AutoRolesAdapter.COLL].update_one(
                {'_id': str(guild_id)},
                {'$set': {'role_ids': role_ids, 'updated_at': now}, '$setOnInsert': {'created_at': now}},
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f'Failed to set auto roles for guild {guild_id}: {e}')
            return False
