import os
from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

@dataclass
class Config:
    """Application configuration"""
    # Bot
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    
    # MongoDB
    MONGO_URI_LEGACY: str = os.getenv("MONGO_URI_LEGACY", "mongodb://localhost:27017")
    MONGO_URI_SYSTEM: str = os.getenv("MONGO_URI_SYSTEM", "mongodb://localhost:27017")
    MONGO_DB_LEGACY: str = os.getenv("MONGO_DB_LEGACY", "legacy_movies")
    MONGO_DB_SYSTEM: str = os.getenv("MONGO_DB_SYSTEM", "movie_bot")
    
    # TMDB
    TMDB_API_KEY: str = os.getenv("TMDB_API_KEY", "")
    
    # Channels
    ADMIN_CHANNEL_ID: int = int(os.getenv("ADMIN_CHANNEL_ID", "-1001234567890"))
    FILE_CHANNEL_ID: int = int(os.getenv("FILE_CHANNEL_ID", "-1001234567891"))
    SEARCH_GROUP_LINK: str = os.getenv("SEARCH_GROUP_LINK", "https://t.me/your_group")
    
    # Settings
    MAX_PENDING_REQUESTS: int = int(os.getenv("MAX_PENDING_REQUESTS", "3"))
    FUZZY_MATCH_THRESHOLD: int = int(os.getenv("FUZZY_MATCH_THRESHOLD", "90"))
    RESULTS_PER_PAGE: int = int(os.getenv("RESULTS_PER_PAGE", "5"))
    
    # Redis (optional for caching)
    REDIS_URL: Optional[str] = os.getenv("REDIS_URL")
    
    @classmethod
    def validate(cls):
        """Validate required environment variables"""
        required = ["BOT_TOKEN", "TMDB_API_KEY"]
        missing = [var for var in required if not getattr(cls, var)]
        if missing:
            raise ValueError(f"Missing required environment variables: {missing}")

config = Config()
