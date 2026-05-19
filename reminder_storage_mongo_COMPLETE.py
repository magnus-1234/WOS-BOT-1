"""
MongoDB-backed storage for reminders.
This is a complete implementation that replaces the gitignored db/reminder_storage_mongo.py
"""
import logging
from datetime import datetime
from typing import List, Dict, Optional, Union
from pymongo import MongoClient
from bson import ObjectId

logger = logging.getLogger(__name__)

class ReminderStorageMongo:
    """MongoDB-backed reminder storage"""
    
    def __init__(self):
        """Initialize MongoDB connection"""
        try:
            from db.mongo_client_wrapper import get_mongo_client_sync
            self.client = get_mongo_client_sync()
            self.db = self.client['whiteout_survival_bot']
            self.col = self.db['reminders']
            logger.info("✅ MongoDB reminder storage initialized")
        except Exception as e:
            logger.error(f"❌ Failed to initialize MongoDB reminder storage: {e}")
            raise
    
    def add_reminder(self, user_id: str, channel_id: str, guild_id: str, message: str, reminder_time: datetime,
                    body: str = None, is_recurring: bool = False, recurrence_type: str = None, recurrence_interval: int = None,
                    original_pattern: str = None, mention: str = 'everyone', image_url: str = None,
                    thumbnail_url: str = None, footer_text: str = None, footer_icon_url: str = None, author_url: str = None,
                    recurrence_days: List[int] = None) -> Union[str, int]:
        """Add a new reminder to MongoDB"""
        try:
            reminder_doc = {
                'user_id': user_id,
                'channel_id': channel_id,
                'guild_id': guild_id,
                'message': message,
                'body': body,
                'reminder_time': reminder_time,
                'created_at': datetime.utcnow(),
                'is_active': True,
                'is_sent': False,
                'is_recurring': is_recurring,
                'recurrence_type': recurrence_type,
                'recurrence_interval': recurrence_interval,
                'recurrence_days': recurrence_days,
                'original_time_pattern': original_pattern,
                'mention': mention,
                'image_url': image_url,
                'thumbnail_url': thumbnail_url,
                'footer_text': footer_text,
                'footer_icon_url': footer_icon_url,
                'author_url': author_url
            }
            
            result = self.col.insert_one(reminder_doc)
            logger.info(f"✅ Added {'recurring ' if is_recurring else ''}reminder {result.inserted_id} for user {user_id}")
            return str(result.inserted_id)
        except Exception as e:
            logger.error(f"❌ Failed to add reminder to MongoDB: {e}")
            return -1
    
    def get_due_reminders(self) -> List[Dict]:
        """Get all reminders that are due"""
        try:
            now = datetime.utcnow()
            reminders = list(self.col.find({
                'is_active': True,
                'is_sent': False,
                'reminder_time': {'$lte': now}
            }).sort('reminder_time', 1))
            
            # Convert ObjectId to string for consistency
            for reminder in reminders:
                reminder['id'] = str(reminder['_id'])
            
            return reminders
        except Exception as e:
            logger.error(f"❌ Failed to get due reminders: {e}")
            return []
    
    def mark_reminder_sent(self, reminder_id: Union[str, ObjectId]) -> bool:
        """Mark a reminder as sent"""
        try:
            if isinstance(reminder_id, str):
                reminder_id = ObjectId(reminder_id)
            
            result = self.col.update_one(
                {'_id': reminder_id, 'is_sent': False},
                {'$set': {'is_sent': True}}
            )
            
            if result.modified_count > 0:
                logger.info(f"✅ Marked reminder {reminder_id} as sent")
                return True
            else:
                logger.debug(f"Reminder {reminder_id} was already sent or not found")
                return False
        except Exception as e:
            logger.error(f"❌ Failed to mark reminder as sent: {e}")
            return False
    
    def update_reminder_fields(self, reminder_id: Union[str, ObjectId], fields: dict) -> bool:
        """Update specific fields of a reminder"""
        if not fields:
            return False
        
        allowed = {
            'image_url', 'thumbnail_url', 'body', 'footer_text', 'footer_icon_url',
            'mention', 'reminder_time', 'author_url', 'message', 'channel_id',
            'is_recurring', 'recurrence_type', 'recurrence_interval', 'recurrence_days',
            'original_time_pattern', 'is_active', 'is_sent'
        }
        to_update = {k: v for k, v in fields.items() if k in allowed}
        
        if not to_update:
            return False
        
        try:
            if isinstance(reminder_id, str):
                reminder_id = ObjectId(reminder_id)
            
            result = self.col.update_one(
                {'_id': reminder_id},
                {'$set': to_update}
            )
            
            if result.modified_count > 0:
                logger.info(f"✅ Updated reminder {reminder_id} fields: {list(to_update.keys())}")
                return True
            return False
        except Exception as e:
            logger.error(f"❌ Failed to update reminder {reminder_id}: {e}")
            return False
    
    def get_user_reminders(self, user_id: str, limit: int = 10) -> List[Dict]:
        """Get active reminders for a specific user"""
        try:
            reminders = list(self.col.find({
                'user_id': user_id,
                'is_active': True,
                'is_sent': False
            }).sort('reminder_time', 1).limit(limit))
            
            # Convert ObjectId to string and datetime for consistency
            for reminder in reminders:
                reminder['id'] = str(reminder['_id'])
                if isinstance(reminder.get('reminder_time'), datetime):
                    pass  # Already datetime
                elif isinstance(reminder.get('reminder_time'), str):
                    reminder['reminder_time'] = datetime.fromisoformat(reminder['reminder_time'])
            
            return reminders
        except Exception as e:
            logger.error(f"❌ Failed to get user reminders: {e}")
            return []
    
    def delete_reminder(self, reminder_id: Union[str, ObjectId], user_id: str) -> bool:
        """Delete a reminder (mark as inactive)"""
        try:
            if isinstance(reminder_id, str):
                try:
                    reminder_id = ObjectId(reminder_id)
                except Exception:
                    # If it's not a valid ObjectId, return False
                    return False
            
            result = self.col.update_one(
                {'_id': reminder_id, 'user_id': user_id, 'is_active': True},
                {'$set': {'is_active': False}}
            )
            
            if result.modified_count > 0:
                logger.info(f"✅ Deleted reminder {reminder_id} for user {user_id}")
                return True
            else:
                logger.warning(f"❌ No active reminder found with ID {reminder_id} for user {user_id}")
                return False
        except Exception as e:
            logger.error(f"❌ Failed to delete reminder: {e}")
            return False
    
    def get_all_active_reminders(self) -> List[Dict]:
        """Get ALL active reminders in the system"""
        try:
            reminders = list(self.col.find({
                'is_active': True,
                'is_sent': False
            }).sort('reminder_time', 1))
            
            # Convert ObjectId to string for consistency
            for reminder in reminders:
                reminder['id'] = str(reminder['_id'])
            
            return reminders
        except Exception as e:
            logger.error(f"❌ Failed to get all active reminders: {e}")
            return []
    
    def update_reminder_time(self, reminder_id: Union[str, ObjectId], new_time: datetime) -> bool:
        """Update the reminder time (used for recurring reminders)"""
        try:
            if isinstance(reminder_id, str):
                reminder_id = ObjectId(reminder_id)
            
            result = self.col.update_one(
                {'_id': reminder_id},
                {'$set': {'reminder_time': new_time, 'is_sent': False}}
            )
            
            if result.modified_count > 0:
                logger.info(f"✅ Updated reminder {reminder_id} time to {new_time}")
                return True
            return False
        except Exception as e:
            logger.error(f"❌ Failed to update reminder time: {e}")
            return False
