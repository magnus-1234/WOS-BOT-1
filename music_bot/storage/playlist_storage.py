"""
Playlist Storage Module
Handles saving and loading music playlists in MongoDB.
"""

import os
from datetime import datetime
from typing import List, Dict, Optional, Any

# Try to import MongoDB support (Motor for async)
try:
    from motor.motor_asyncio import AsyncIOMotorClient
    MONGO_AVAILABLE = True
except ImportError:
    MONGO_AVAILABLE = False


class PlaylistStorage:
    """Manages playlist persistence in a dedicated MongoDB collection"""
    
    def __init__(self):
        self.mongo_client = None
        self.mongo_db = None
        self.mongo_enabled = False
        self.initialized = False
        self.collection_name = os.getenv('MUSIC_PLAYLIST_COLLECTION', 'music_playlists')
        
        print("[PlaylistStorage] Playlist storage module loaded")
    
    async def initialize(self):
        """Async initialization - call this on bot startup"""
        if self.initialized:
            return
        
        print("[PlaylistStorage] Initializing playlist storage...")
        
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
                print("[PlaylistStorage] No MONGO_URI configured in environment variables")
            else:
                for uri_label, uri in uris_to_try:
                    try:
                        print(f"[PlaylistStorage] Attempting to connect to {uri_label} MongoDB...")
                        
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
                        print(f"[PlaylistStorage] Connected to {uri_label} MongoDB successfully!")
                        print(f"[PlaylistStorage] Database: {db_name}")
                        print(f"[PlaylistStorage] Collection: {self.collection_name}")
                        print(f"[PlaylistStorage] Collections: {', '.join(collections) if collections else 'none (will be created)'}")
                        
                        collection = self.mongo_db[self.collection_name]
                        await collection.create_index(
                            [('guild_id', 1), ('user_id', 1), ('name', 1)],
                            unique=True,
                            name='guild_user_playlist_unique'
                        )
                        await collection.create_index(
                            [('user_id', 1), ('updated_at', -1)],
                            name='user_updated_at'
                        )
                        count = await collection.count_documents({})
                        print(f"[PlaylistStorage] Found {count} existing playlist(s) in database")
                        
                        # Success! Break out of the loop
                        break
                        
                    except Exception as e:
                        print(f"[PlaylistStorage] {uri_label.capitalize()} MongoDB connection failed: {e}")
                        print(f"[PlaylistStorage] Error details: {type(e).__name__}")
                        self.mongo_enabled = False
                        self.mongo_client = None
                        self.mongo_db = None
                        # Continue to next URI
                        continue
        else:
            print("[PlaylistStorage] motor package not installed - MongoDB unavailable")

        if not self.mongo_enabled:
            raise RuntimeError("Playlist storage requires MongoDB. Set MONGO_URI or MONGO_URI_FALLBACK for cloud persistence.")
        
        self.initialized = True
        print("[PlaylistStorage] Initialization complete\n")
    
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
                collection = self.mongo_db[self.collection_name]
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
                collection = self.mongo_db[self.collection_name]
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
        return None
    
    async def list_playlists(self, guild_id: int, user_id: int, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        """
        List all playlists for a user in a guild
        
        Returns:
            List of dictionaries with keys: name, track_count, created_at, updated_at
        """
        if self.mongo_enabled:
            try:
                collection = self.mongo_db[self.collection_name]
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
        return []
    
    async def delete_playlist(self, guild_id: int, user_id: int, name: str) -> bool:
        """
        Delete a playlist
        
        Returns:
            True if deleted, False if not found or error
        """
        if self.mongo_enabled:
            try:
                collection = self.mongo_db[self.collection_name]
                result = await collection.delete_one({
                    'guild_id': guild_id,
                    'user_id': user_id,
                    'name': name
                })
                return result.deleted_count > 0
            except Exception as e:
                print(f"[PlaylistStorage] MongoDB delete error: {e}")
                return False
        return False
    
    async def count_playlists(self, guild_id: int, user_id: int) -> int:
        """Get total count of playlists for pagination"""
        if self.mongo_enabled:
            try:
                collection = self.mongo_db[self.collection_name]
                count = await collection.count_documents({
                    'guild_id': guild_id,
                    'user_id': user_id
                })
                return count
            except Exception as e:
                print(f"[PlaylistStorage] MongoDB count error: {e}")
                return 0
        return 0


# Global instance
playlist_storage = PlaylistStorage()
