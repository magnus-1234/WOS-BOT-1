"""
Playlist Storage Module
Handles saving and loading music playlists with MongoDB/SQLite dual support
"""

import os
import sqlite3
from datetime import datetime
from typing import List, Dict, Optional, Any
from pathlib import Path

# Try to import MongoDB support (Motor for async)
try:
    from motor.motor_asyncio import AsyncIOMotorClient
    MONGO_AVAILABLE = True
except ImportError:
    MONGO_AVAILABLE = False


class PlaylistStorage:
    """Manages playlist persistence with MongoDB (preferred) and SQLite (fallback)"""
    
    def __init__(self):
        self.mongo_client = None
        self.mongo_db = None
        self.mongo_enabled = False
        self.sqlite_path = None
        self.initialized = False
        
        # Initialize SQLite as fallback (always available)
        db_dir = Path(__file__).parent.parent.parent / 'db'
        db_dir.mkdir(exist_ok=True)
        self.sqlite_path = db_dir / 'playlists.sqlite'
        self._init_sqlite()
        
        print("[PlaylistStorage] 📦 Playlist storage module loaded")
    
    async def initialize(self):
        """Async initialization - call this on bot startup"""
        if self.initialized:
            return
        
        print("[PlaylistStorage] 🔄 Initializing playlist storage...")
        
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
                print("[PlaylistStorage] ⚠️ No MONGO_URI configured in environment variables")
            else:
                for uri_label, uri in uris_to_try:
                    try:
                        print(f"[PlaylistStorage] 🔌 Attempting to connect to {uri_label} MongoDB...")
                        
                        # Create async Motor client
                        self.mongo_client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
                        
                        # Get database name from environment or use default
                        db_name = os.getenv('MONGO_DB_NAME', 'discord_bot')
                        self.mongo_db = self.mongo_client[db_name]
                        
                        # Test connection with ping
                        await self.mongo_client.admin.command('ping')
                        
                        # Verify collection exists
                        collections = await self.mongo_db.list_collection_names()
                        
                        self.mongo_enabled = True
                        print(f"[PlaylistStorage] ✅ Connected to {uri_label} MongoDB successfully!")
                        print(f"[PlaylistStorage] 📊 Database: {db_name}")
                        print(f"[PlaylistStorage] 📁 Collections: {', '.join(collections) if collections else 'none (will be created)'}")
                        
                        # Test read operation
                        try:
                            collection = self.mongo_db['playlists']
                            count = await collection.count_documents({})
                            print(f"[PlaylistStorage] 🎵 Found {count} existing playlist(s) in database")
                        except Exception as e:
                            print(f"[PlaylistStorage] ⚠️ Could not count playlists: {e}")
                        
                        # Success! Break out of the loop
                        break
                        
                    except Exception as e:
                        print(f"[PlaylistStorage] ❌ {uri_label.capitalize()} MongoDB connection failed: {e}")
                        print(f"[PlaylistStorage] 🔍 Error details: {type(e).__name__}")
                        self.mongo_enabled = False
                        self.mongo_client = None
                        self.mongo_db = None
                        # Continue to next URI
                        continue
        else:
            print("[PlaylistStorage] ⚠️ motor package not installed - MongoDB unavailable")
        
        # Fallback to SQLite
        if not self.mongo_enabled:
            print("[PlaylistStorage] ℹ️ Using SQLite for playlist storage")
            print(f"[PlaylistStorage] 📂 SQLite path: {self.sqlite_path}")
            print("[PlaylistStorage] ⚠️ Note: SQLite data will NOT persist on cloud platforms like Render")
        
        self.initialized = True
        print("[PlaylistStorage] ✅ Initialization complete\n")
    
    def _init_sqlite(self):
        """Initialize SQLite database schema"""
        try:
            conn = sqlite3.connect(self.sqlite_path)
            c = conn.cursor()
            c.execute('''CREATE TABLE IF NOT EXISTS playlists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                tracks TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(guild_id, user_id, name)
            )''')
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[PlaylistStorage] Error initializing SQLite: {e}")
    
    async def save_playlist(self, guild_id: int, user_id: int, name: str, tracks: List[Dict[str, Any]]) -> bool:
        """
        Save a playlist
        
        Args:
            guild_id: Discord guild ID
            user_id: Discord user ID
            name: Playlist name
            tracks: List of track dictionaries with keys: title, author, uri, length
            
        Returns:
            True if successful, False otherwise
        """
        now = datetime.utcnow().isoformat()
        
        if self.mongo_enabled:
            try:
                collection = self.mongo_db['playlists']
                await collection.update_one(
                    {
                        'guild_id': guild_id,
                        'user_id': user_id,
                        'name': name
                    },
                    {
                        '$set': {
                            'tracks': tracks,
                            'updated_at': now
                        },
                        '$setOnInsert': {
                            'created_at': now
                        }
                    },
                    upsert=True
                )
                return True
            except Exception as e:
                print(f"[PlaylistStorage] MongoDB save error: {e}")
                return False
        else:
            try:
                import json
                conn = sqlite3.connect(self.sqlite_path)
                c = conn.cursor()
                
                # Check if playlist exists
                c.execute(
                    'SELECT id FROM playlists WHERE guild_id = ? AND user_id = ? AND name = ?',
                    (guild_id, user_id, name)
                )
                existing = c.fetchone()
                
                tracks_json = json.dumps(tracks)
                
                if existing:
                    # Update existing
                    c.execute(
                        'UPDATE playlists SET tracks = ?, updated_at = ? WHERE id = ?',
                        (tracks_json, now, existing[0])
                    )
                else:
                    # Insert new
                    c.execute(
                        'INSERT INTO playlists (guild_id, user_id, name, tracks, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)',
                        (guild_id, user_id, name, tracks_json, now, now)
                    )
                
                conn.commit()
                conn.close()
                return True
            except Exception as e:
                print(f"[PlaylistStorage] SQLite save error: {e}")
                return False
    
    async def load_playlist(self, guild_id: int, user_id: int, name: str) -> Optional[Dict[str, Any]]:
        """
        Load a playlist
        
        Returns:
            Dictionary with keys: name, tracks, created_at, updated_at
            None if not found
        """
        if self.mongo_enabled:
            try:
                collection = self.mongo_db['playlists']
                playlist = await collection.find_one({
                    'guild_id': guild_id,
                    'user_id': user_id,
                    'name': name
                })
                if playlist:
                    return {
                        'name': playlist['name'],
                        'tracks': playlist['tracks'],
                        'created_at': playlist['created_at'],
                        'updated_at': playlist['updated_at']
                    }
                return None
            except Exception as e:
                print(f"[PlaylistStorage] MongoDB load error: {e}")
                return None
        else:
            try:
                import json
                conn = sqlite3.connect(self.sqlite_path)
                c = conn.cursor()
                c.execute(
                    'SELECT name, tracks, created_at, updated_at FROM playlists WHERE guild_id = ? AND user_id = ? AND name = ?',
                    (guild_id, user_id, name)
                )
                row = c.fetchone()
                conn.close()
                
                if row:
                    return {
                        'name': row[0],
                        'tracks': json.loads(row[1]),
                        'created_at': row[2],
                        'updated_at': row[3]
                    }
                return None
            except Exception as e:
                print(f"[PlaylistStorage] SQLite load error: {e}")
                return None
    
    async def list_playlists(self, guild_id: int, user_id: int, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        """
        List all playlists for a user in a guild
        
        Returns:
            List of dictionaries with keys: name, track_count, created_at, updated_at
        """
        if self.mongo_enabled:
            try:
                collection = self.mongo_db['playlists']
                cursor = collection.find({
                    'guild_id': guild_id,
                    'user_id': user_id
                }).sort('updated_at', -1).skip(offset).limit(limit)
                
                playlists = []
                async for playlist in cursor:
                    playlists.append({
                        'name': playlist['name'],
                        'track_count': len(playlist.get('tracks', [])),
                        'created_at': playlist['created_at'],
                        'updated_at': playlist['updated_at']
                    })
                return playlists
            except Exception as e:
                print(f"[PlaylistStorage] MongoDB list error: {e}")
                return []
        else:
            try:
                import json
                conn = sqlite3.connect(self.sqlite_path)
                c = conn.cursor()
                c.execute(
                    'SELECT name, tracks, created_at, updated_at FROM playlists WHERE guild_id = ? AND user_id = ? ORDER BY updated_at DESC LIMIT ? OFFSET ?',
                    (guild_id, user_id, limit, offset)
                )
                rows = c.fetchall()
                conn.close()
                
                playlists = []
                for row in rows:
                    tracks = json.loads(row[1])
                    playlists.append({
                        'name': row[0],
                        'track_count': len(tracks),
                        'created_at': row[2],
                        'updated_at': row[3]
                    })
                return playlists
            except Exception as e:
                print(f"[PlaylistStorage] SQLite list error: {e}")
                return []
    
    async def delete_playlist(self, guild_id: int, user_id: int, name: str) -> bool:
        """
        Delete a playlist
        
        Returns:
            True if deleted, False if not found or error
        """
        if self.mongo_enabled:
            try:
                collection = self.mongo_db['playlists']
                result = await collection.delete_one({
                    'guild_id': guild_id,
                    'user_id': user_id,
                    'name': name
                })
                return result.deleted_count > 0
            except Exception as e:
                print(f"[PlaylistStorage] MongoDB delete error: {e}")
                return False
        else:
            try:
                conn = sqlite3.connect(self.sqlite_path)
                c = conn.cursor()
                c.execute(
                    'DELETE FROM playlists WHERE guild_id = ? AND user_id = ? AND name = ?',
                    (guild_id, user_id, name)
                )
                deleted = c.rowcount > 0
                conn.commit()
                conn.close()
                return deleted
            except Exception as e:
                print(f"[PlaylistStorage] SQLite delete error: {e}")
                return False
    
    async def count_playlists(self, guild_id: int, user_id: int) -> int:
        """Get total count of playlists for pagination"""
        if self.mongo_enabled:
            try:
                collection = self.mongo_db['playlists']
                count = await collection.count_documents({
                    'guild_id': guild_id,
                    'user_id': user_id
                })
                return count
            except Exception as e:
                print(f"[PlaylistStorage] MongoDB count error: {e}")
                return 0
        else:
            try:
                conn = sqlite3.connect(self.sqlite_path)
                c = conn.cursor()
                c.execute(
                    'SELECT COUNT(*) FROM playlists WHERE guild_id = ? AND user_id = ?',
                    (guild_id, user_id)
                )
                count = c.fetchone()[0]
                conn.close()
                return count
            except Exception as e:
                print(f"[PlaylistStorage] SQLite count error: {e}")
                return 0


# Global instance
playlist_storage = PlaylistStorage()
