"""
Music State Storage
Saves and restores music playback state for persistence across bot restarts.
Uses MongoDB when available (for Render persistence), falls back to SQLite for local development.
"""

import os
import sqlite3
from typing import Optional, Dict, List, Any
from datetime import datetime
from pathlib import Path

# Try to import MongoDB support (Motor for async)
try:
    from motor.motor_asyncio import AsyncIOMotorClient
    MONGO_AVAILABLE = True
except ImportError:
    MONGO_AVAILABLE = False


class MusicStateStorage:
    """Manages music playback state persistence with MongoDB (preferred) and SQLite (fallback)"""
    
    def __init__(self, db_path: str = "data/music_states.db"):
        self.mongo_client = None
        self.mongo_db = None
        self.mongo_enabled = False
        self.sqlite_path = db_path
        self.initialized = False
        
        # Initialize SQLite as fallback (always available)
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_sqlite()
        
        print("[MusicStateStorage] 📦 Music state storage module loaded")
    
    async def initialize(self):
        """Async initialization - call this on bot startup"""
        if self.initialized:
            return
        
        print("[MusicStateStorage] 🔄 Initializing music state storage...")
        
        # Try MongoDB first with automatic fallback
        if MONGO_AVAILABLE:
            # Get MongoDB URIs
            primary_uri = os.getenv('MONGO_URI')
            fallback_uri = os.getenv('MONGO_URI_FALLBACK')
            
            # Try primary URI first, then fallback
            uris_to_try = []
            if primary_uri:
                uris_to_try.append(('primary', primary_uri))
            if fallback_uri and fallback_uri != primary_uri:
                uris_to_try.append(('fallback', fallback_uri))
            
            if not uris_to_try:
                print("[MusicStateStorage] ⚠️ No MONGO_URI configured in environment variables")
            else:
                for uri_label, uri in uris_to_try:
                    try:
                        print(f"[MusicStateStorage] 🔌 Attempting to connect to {uri_label} MongoDB...")
                        
                        # Create async Motor client
                        self.mongo_client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
                        
                        # Get database name from environment or use default
                        db_name = os.getenv('MONGO_DB_NAME', 'discord_bot')
                        self.mongo_db = self.mongo_client[db_name]
                        
                        # Test connection with ping
                        await self.mongo_client.admin.command('ping')
                        
                        self.mongo_enabled = True
                        print(f"[MusicStateStorage] ✅ Connected to {uri_label} MongoDB successfully!")
                        print(f"[MusicStateStorage] 📊 Database: {db_name}")
                        
                        # Test read operation
                        try:
                            collection = self.mongo_db['music_states']
                            count = await collection.count_documents({})
                            print(f"[MusicStateStorage] 🎵 Found {count} existing music state(s) in database")
                        except Exception as e:
                            print(f"[MusicStateStorage] ⚠️ Could not count states: {e}")
                        
                        # Success! Break out of the loop
                        break
                        
                    except Exception as e:
                        print(f"[MusicStateStorage] ❌ {uri_label.capitalize()} MongoDB connection failed: {e}")
                        self.mongo_enabled = False
                        self.mongo_client = None
                        self.mongo_db = None
                        continue
        else:
            print("[MusicStateStorage] ⚠️ motor package not installed - MongoDB unavailable")
        
        # Fallback to SQLite
        if not self.mongo_enabled:
            print("[MusicStateStorage] ℹ️ Using SQLite for music state storage")
            print(f"[MusicStateStorage] 📂 SQLite path: {self.sqlite_path}")
            print("[MusicStateStorage] ⚠️ Note: SQLite data will NOT persist on cloud platforms like Render")
        
        self.initialized = True
        print("[MusicStateStorage] ✅ Initialization complete\n")
    
    def _init_sqlite(self):
        """Initialize SQLite database schema"""
        try:
            conn = sqlite3.connect(self.sqlite_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS music_states (
                    guild_id INTEGER PRIMARY KEY,
                    channel_id INTEGER NOT NULL,
                    text_channel_id INTEGER,
                    persistent_channel_id INTEGER,
                    current_track_uri TEXT,
                    current_track_title TEXT,
                    current_track_author TEXT,
                    current_track_position INTEGER DEFAULT 0,
                    loop_mode TEXT DEFAULT 'off',
                    volume INTEGER DEFAULT 100,
                    playlist_name TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS music_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    position INTEGER NOT NULL,
                    uri TEXT NOT NULL,
                    title TEXT,
                    author TEXT,
                    requester_id INTEGER,
                    requester_name TEXT,
                    FOREIGN KEY (guild_id) REFERENCES music_states(guild_id) ON DELETE CASCADE
                )
            """)
            
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[MusicStateStorage] Error initializing SQLite: {e}")
    
    async def save_state(
        self,
        guild_id: int,
        channel_id: int,
        text_channel_id: Optional[int] = None,
        current_track: Optional[Dict[str, Any]] = None,
        queue: Optional[List[Dict[str, Any]]] = None,
        loop_mode: str = "off",
        volume: int = 100,
        playlist_name: Optional[str] = None
    ) -> bool:
        """Save music state for a guild"""
        now = datetime.utcnow().isoformat()
        
        if self.mongo_enabled:
            try:
                collection = self.mongo_db['music_states']
                
                # Prepare state document
                state_doc = {
                    'guild_id': guild_id,
                    'channel_id': channel_id,
                    'text_channel_id': text_channel_id,
                    'loop_mode': loop_mode,
                    'volume': volume,
                    'playlist_name': playlist_name,
                    'updated_at': now
                }
                
                # Add current track if exists
                if current_track:
                    state_doc['current_track'] = {
                        'uri': current_track.get('uri'),
                        'title': current_track.get('title'),
                        'author': current_track.get('author'),
                        'position': current_track.get('position', 0)
                    }
                else:
                    state_doc['current_track'] = None
                
                # Add queue
                if queue:
                    state_doc['queue'] = queue
                else:
                    state_doc['queue'] = []
                
                # Upsert the state
                await collection.update_one(
                    {'guild_id': guild_id},
                    {
                        '$set': state_doc,
                        '$setOnInsert': {'created_at': now}
                    },
                    upsert=True
                )
                return True
            except Exception as e:
                print(f"[MusicStateStorage] MongoDB save error: {e}")
                return False
        else:
            # SQLite fallback
            try:
                conn = sqlite3.connect(self.sqlite_path)
                cursor = conn.cursor()
                
                # Save main state
                cursor.execute("""
                    INSERT OR REPLACE INTO music_states 
                    (guild_id, channel_id, text_channel_id, current_track_uri, 
                     current_track_title, current_track_author, current_track_position,
                     loop_mode, volume, playlist_name, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    guild_id,
                    channel_id,
                    text_channel_id,
                    current_track.get('uri') if current_track else None,
                    current_track.get('title') if current_track else None,
                    current_track.get('author') if current_track else None,
                    current_track.get('position', 0) if current_track else 0,
                    loop_mode,
                    volume,
                    playlist_name,
                    datetime.now()
                ))
                
                # Clear old queue
                cursor.execute("DELETE FROM music_queue WHERE guild_id = ?", (guild_id,))
                
                # Save queue
                if queue:
                    for position, track in enumerate(queue):
                        cursor.execute("""
                            INSERT INTO music_queue 
                            (guild_id, position, uri, title, author, requester_id, requester_name)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        """, (
                            guild_id,
                            position,
                            track.get('uri'),
                            track.get('title'),
                            track.get('author'),
                            track.get('requester_id'),
                            track.get('requester_name')
                        ))
                
                conn.commit()
                conn.close()
                return True
                
            except Exception as e:
                print(f"[MusicStateStorage] SQLite save error: {e}")
                return False
    
    async def load_state(self, guild_id: int) -> Optional[Dict[str, Any]]:
        """Load music state for a guild"""
        if self.mongo_enabled:
            try:
                collection = self.mongo_db['music_states']
                state = await collection.find_one({'guild_id': guild_id})
                
                if state:
                    # Remove MongoDB _id field
                    state.pop('_id', None)
                    return state
                return None
            except Exception as e:
                print(f"[MusicStateStorage] MongoDB load error: {e}")
                return None
        else:
            # SQLite fallback
            try:
                conn = sqlite3.connect(self.sqlite_path)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                # Load main state
                cursor.execute("""
                    SELECT * FROM music_states WHERE guild_id = ?
                """, (guild_id,))
                
                row = cursor.fetchone()
                if not row:
                    conn.close()
                    return None
                
                state = dict(row)
                
                # Load queue
                cursor.execute("""
                    SELECT uri, title, author, requester_id, requester_name
                    FROM music_queue
                    WHERE guild_id = ?
                    ORDER BY position ASC
                """, (guild_id,))
                
                queue_rows = cursor.fetchall()
                state['queue'] = [dict(row) for row in queue_rows]
                
                # Build current track dict
                if state['current_track_uri']:
                    state['current_track'] = {
                        'uri': state['current_track_uri'],
                        'title': state['current_track_title'],
                        'author': state['current_track_author'],
                        'position': state['current_track_position']
                    }
                else:
                    state['current_track'] = None
                
                conn.close()
                return state
                
            except Exception as e:
                print(f"[MusicStateStorage] SQLite load error: {e}")
                return None
    
    async def delete_state(self, guild_id: int) -> bool:
        """Delete music state for a guild"""
        if self.mongo_enabled:
            try:
                collection = self.mongo_db['music_states']
                result = await collection.delete_one({'guild_id': guild_id})
                return result.deleted_count > 0
            except Exception as e:
                print(f"[MusicStateStorage] MongoDB delete error: {e}")
                return False
        else:
            # SQLite fallback
            try:
                conn = sqlite3.connect(self.sqlite_path)
                cursor = conn.cursor()
                
                cursor.execute("DELETE FROM music_states WHERE guild_id = ?", (guild_id,))
                cursor.execute("DELETE FROM music_queue WHERE guild_id = ?", (guild_id,))
                
                conn.commit()
                conn.close()
                return True
                
            except Exception as e:
                print(f"[MusicStateStorage] SQLite delete error: {e}")
                return False
    
    async def get_all_states(self) -> List[Dict[str, Any]]:
        """Get all saved music states"""
        if self.mongo_enabled:
            try:
                collection = self.mongo_db['music_states']
                cursor = collection.find({})
                
                states = []
                async for state in cursor:
                    state.pop('_id', None)
                    states.append(state)
                return states
            except Exception as e:
                print(f"[MusicStateStorage] MongoDB get_all error: {e}")
                return []
        else:
            # SQLite fallback
            try:
                conn = sqlite3.connect(self.sqlite_path)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                cursor.execute("SELECT guild_id FROM music_states")
                rows = cursor.fetchall()
                
                states = []
                for row in rows:
                    # Use sync version for SQLite
                    state = self._load_state_sync(row['guild_id'])
                    if state:
                        states.append(state)
                
                conn.close()
                return states
                
            except Exception as e:
                print(f"[MusicStateStorage] SQLite get_all error: {e}")
                return []
    
    def _load_state_sync(self, guild_id: int) -> Optional[Dict[str, Any]]:
        """Synchronous version of load_state for SQLite"""
        try:
            conn = sqlite3.connect(self.sqlite_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute("SELECT * FROM music_states WHERE guild_id = ?", (guild_id,))
            row = cursor.fetchone()
            if not row:
                conn.close()
                return None
            
            state = dict(row)
            
            cursor.execute("""
                SELECT uri, title, author, requester_id, requester_name
                FROM music_queue WHERE guild_id = ? ORDER BY position ASC
            """, (guild_id,))
            
            queue_rows = cursor.fetchall()
            state['queue'] = [dict(row) for row in queue_rows]
            
            if state['current_track_uri']:
                state['current_track'] = {
                    'uri': state['current_track_uri'],
                    'title': state['current_track_title'],
                    'author': state['current_track_author'],
                    'position': state['current_track_position']
                }
            else:
                state['current_track'] = None
            
            conn.close()
            return state
        except Exception as e:
            print(f"[MusicStateStorage] Error in _load_state_sync: {e}")
            return None
    
    async def set_persistent_channel(self, guild_id: int, channel_id: int) -> bool:
        """Set persistent voice channel for a guild"""
        now = datetime.utcnow().isoformat()
        
        if self.mongo_enabled:
            try:
                collection = self.mongo_db['music_states']
                await collection.update_one(
                    {'guild_id': guild_id},
                    {
                        '$set': {
                            'persistent_channel_id': channel_id,
                            'updated_at': now
                        },
                        '$setOnInsert': {
                            'guild_id': guild_id,
                            'channel_id': channel_id,
                            'created_at': now
                        }
                    },
                    upsert=True
                )
                return True
            except Exception as e:
                print(f"[MusicStateStorage] MongoDB set_persistent_channel error: {e}")
                return False
        else:
            # SQLite fallback
            try:
                conn = sqlite3.connect(self.sqlite_path)
                cursor = conn.cursor()
                
                cursor.execute("SELECT guild_id FROM music_states WHERE guild_id = ?", (guild_id,))
                exists = cursor.fetchone()
                
                if exists:
                    cursor.execute("""
                        UPDATE music_states 
                        SET persistent_channel_id = ?, updated_at = ?
                        WHERE guild_id = ?
                    """, (channel_id, datetime.now(), guild_id))
                else:
                    cursor.execute("""
                        INSERT INTO music_states 
                        (guild_id, channel_id, persistent_channel_id, updated_at)
                        VALUES (?, ?, ?, ?)
                    """, (guild_id, channel_id, channel_id, datetime.now()))
                
                conn.commit()
                conn.close()
                return True
                
            except Exception as e:
                print(f"[MusicStateStorage] SQLite set_persistent_channel error: {e}")
                return False
    
    async def get_persistent_channel(self, guild_id: int) -> Optional[int]:
        """Get persistent voice channel for a guild"""
        if self.mongo_enabled:
            try:
                collection = self.mongo_db['music_states']
                state = await collection.find_one(
                    {'guild_id': guild_id},
                    {'persistent_channel_id': 1}
                )
                return state.get('persistent_channel_id') if state else None
            except Exception as e:
                print(f"[MusicStateStorage] MongoDB get_persistent_channel error: {e}")
                return None
        else:
            # SQLite fallback
            try:
                conn = sqlite3.connect(self.sqlite_path)
                cursor = conn.cursor()
                
                cursor.execute("""
                    SELECT persistent_channel_id FROM music_states WHERE guild_id = ?
                """, (guild_id,))
                
                row = cursor.fetchone()
                conn.close()
                
                if row and row[0]:
                    return row[0]
                return None
                
            except Exception as e:
                print(f"[MusicStateStorage] SQLite get_persistent_channel error: {e}")
                return None
    
    async def clear_persistent_channel(self, guild_id: int) -> bool:
        """Clear persistent voice channel for a guild"""
        now = datetime.utcnow().isoformat()
        
        if self.mongo_enabled:
            try:
                collection = self.mongo_db['music_states']
                await collection.update_one(
                    {'guild_id': guild_id},
                    {
                        '$set': {
                            'persistent_channel_id': None,
                            'updated_at': now
                        }
                    }
                )
                return True
            except Exception as e:
                print(f"[MusicStateStorage] MongoDB clear_persistent_channel error: {e}")
                return False
        else:
            # SQLite fallback
            try:
                conn = sqlite3.connect(self.sqlite_path)
                cursor = conn.cursor()
                
                cursor.execute("""
                    UPDATE music_states 
                    SET persistent_channel_id = NULL, updated_at = ?
                    WHERE guild_id = ?
                """, (datetime.now(), guild_id))
                
                conn.commit()
                conn.close()
                return True
                
            except Exception as e:
                print(f"[MusicStateStorage] SQLite clear_persistent_channel error: {e}")
                return False


# Global instance
music_state_storage = MusicStateStorage()
