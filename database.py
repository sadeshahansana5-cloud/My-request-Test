import asyncio
from typing import Optional, Dict, Any, List
from datetime import datetime
import logging

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError
from bson import ObjectId

from config import config

logger = logging.getLogger(__name__)

class DatabaseManager:
    """Dual MongoDB connection manager"""
    
    def __init__(self):
        self.legacy_client: Optional[AsyncIOMotorClient] = None
        self.system_client: Optional[AsyncIOMotorClient] = None
        self.legacy_db = None
        self.system_db = None
        
    async def connect(self):
        """Establish connections to both MongoDB instances"""
        try:
            # Legacy DB (read-only)
            self.legacy_client = AsyncIOMotorClient(
                config.MONGO_URI_LEGACY,
                serverSelectionTimeoutMS=5000,
                maxPoolSize=10
            )
            await self.legacy_client.admin.command('ping')
            self.legacy_db = self.legacy_client[config.MONGO_DB_LEGACY]
            logger.info("Connected to Legacy MongoDB")
            
            # System DB (read/write)
            self.system_client = AsyncIOMotorClient(
                config.MONGO_URI_SYSTEM,
                serverSelectionTimeoutMS=5000,
                maxPoolSize=10
            )
            await self.system_client.admin.command('ping')
            self.system_db = self.system_client[config.MONGO_DB_SYSTEM]
            
            # Create indexes
            await self._create_indexes()
            logger.info("Connected to System MongoDB")
            
        except (ConnectionFailure, ServerSelectionTimeoutError) as e:
            logger.error(f"MongoDB connection failed: {e}")
            raise
    
    async def _create_indexes(self):
        """Create necessary indexes"""
        if self.system_db:
            await self.system_db.requests.create_index([("user_id", 1)])
            await self.system_db.requests.create_index([("status", 1)])
            await self.system_db.requests.create_index([("tmdb_id", 1)])
            await self.system_db.requests.create_index([("created_at", -1)])
            
            await self.system_db.users.create_index([("user_id", 1)], unique=True)
    
    async def close(self):
        """Close database connections"""
        if self.legacy_client:
            self.legacy_client.close()
        if self.system_client:
            self.system_client.close()
    
    async def find_movie_in_legacy(self, cleaned_name: str, year: Optional[int] = None) -> List[Dict]:
        """
        Search for movie in legacy database using cleaned filename
        """
        try:
            query = {"cleaned_filename": {"$regex": cleaned_name, "$options": "i"}}
            if year:
                query["year"] = year
            
            cursor = self.legacy_db.movies.find(query).limit(5)
            return await cursor.to_list(length=5)
        except Exception as e:
            logger.error(f"Error searching legacy DB: {e}")
            return []
    
    async def check_user_quota(self, user_id: int) -> tuple[bool, int, list]:
        """
        Check user's pending requests quota
        Returns: (can_request, pending_count, pending_requests)
        """
        try:
            pending_requests = await self.system_db.requests.find({
                "user_id": user_id,
                "status": "pending"
            }).sort("created_at", -1).to_list(length=config.MAX_PENDING_REQUESTS)
            
            pending_count = len(pending_requests)
            can_request = pending_count < config.MAX_PENDING_REQUESTS
            
            return can_request, pending_count, pending_requests
        except Exception as e:
            logger.error(f"Error checking user quota: {e}")
            return False, 0, []
    
    async def create_request(self, user_id: int, tmdb_id: int, title: str, year: int) -> Optional[str]:
        """
        Create a new movie request
        """
        try:
            request = {
                "user_id": user_id,
                "tmdb_id": tmdb_id,
                "title": title,
                "year": year,
                "status": "pending",
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow()
            }
            
            result = await self.system_db.requests.insert_one(request)
            return str(result.inserted_id)
        except Exception as e:
            logger.error(f"Error creating request: {e}")
            return None
    
    async def delete_request(self, request_id: str, user_id: int) -> bool:
        """
        Delete a user's request
        """
        try:
            result = await self.system_db.requests.delete_one({
                "_id": ObjectId(request_id),
                "user_id": user_id
            })
            return result.deleted_count > 0
        except Exception as e:
            logger.error(f"Error deleting request: {e}")
            return False
    
    async def update_request_status(self, request_id: str, status: str) -> bool:
        """
        Update request status (admin actions)
        """
        try:
            result = await self.system_db.requests.update_one(
                {"_id": ObjectId(request_id)},
                {
                    "$set": {
                        "status": status,
                        "updated_at": datetime.utcnow()
                    }
                }
            )
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Error updating request status: {e}")
            return False
    
    async def find_pending_by_tmdb_id(self, tmdb_id: int) -> List[Dict]:
        """
        Find pending requests by TMDB ID
        """
        try:
            cursor = self.system_db.requests.find({
                "tmdb_id": tmdb_id,
                "status": "pending"
            })
            return await cursor.to_list(length=None)
        except Exception as e:
            logger.error(f"Error finding pending requests: {e}")
            return []
    
    async def log_activity(self, user_id: int, action: str, details: Dict):
        """
        Log user activity
        """
        try:
            log_entry = {
                "user_id": user_id,
                "action": action,
                "details": details,
                "timestamp": datetime.utcnow()
            }
            await self.system_db.logs.insert_one(log_entry)
        except Exception as e:
            logger.error(f"Error logging activity: {e}")

# Singleton instance
db = DatabaseManager()
