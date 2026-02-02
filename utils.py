import re
import asyncio
from typing import Optional, Tuple, List, Dict
from datetime import datetime
import logging

from thefuzz import fuzz
from tmdbv3api import TMDb, Movie, Search
from tmdbv3api.exceptions import TMDbException

from config import config

logger = logging.getLogger(__name__)

class TMDBClient:
    """TMDB API client with caching and rate limiting"""
    
    def __init__(self):
        self.tmdb = TMDb()
        self.tmdb.api_key = config.TMDB_API_KEY
        self.tmdb.language = 'en'
        self.movie = Movie()
        self.search = Search()
        self._cache = {}
        
    async def search_movies(self, query: str, page: int = 1) -> List[Dict]:
        """Search movies on TMDB"""
        try:
            loop = asyncio.get_event_loop()
            results = await loop.run_in_executor(
                None, lambda: self.search.movies({"query": query, "page": page})
            )
            
            # Process results
            processed = []
            for movie in results[:config.RESULTS_PER_PAGE]:
                processed.append({
                    'id': movie.id,
                    'title': movie.title,
                    'original_title': getattr(movie, 'original_title', movie.title),
                    'year': int(movie.release_date.split('-')[0]) if movie.release_date else None,
                    'release_date': movie.release_date,
                    'overview': movie.overview,
                    'poster_path': movie.poster_path
                })
            return processed
        except TMDbException as e:
            logger.error(f"TMDB search error: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected TMDB error: {e}")
            return []
    
    async def get_movie_details(self, movie_id: int) -> Optional[Dict]:
        """Get detailed movie information"""
        try:
            loop = asyncio.get_event_loop()
            details = await loop.run_in_executor(
                None, lambda: self.movie.details(movie_id)
            )
            
            # Get genres
            genres = [genre['name'] for genre in getattr(details, 'genres', [])]
            
            return {
                'id': details.id,
                'title': details.title,
                'original_title': getattr(details, 'original_title', details.title),
                'year': int(details.release_date.split('-')[0]) if details.release_date else None,
                'release_date': details.release_date,
                'overview': details.overview,
                'poster_path': details.poster_path,
                'vote_average': details.vote_average,
                'vote_count': details.vote_count,
                'runtime': details.runtime,
                'genres': genres,
                'imdb_id': getattr(details, 'imdb_id', None)
            }
        except Exception as e:
            logger.error(f"Error getting movie details: {e}")
            return None


class FilenameCleaner:
    """Clean and normalize filenames for matching"""
    
    # Patterns to remove
    QUALITY_PATTERNS = [
        r'1080[pi]', r'720[pi]', r'2160[pi]', r'4K', r'8K',
        r'HEVC', r'x264', r'x265', r'10.?bit', r'8.?bit',
        r'WEB[.-]?DL', r'WEB[.-]?Rip', r'Blu[.-]?Ray', r'BDRip',
        r'HDTV', r'HD[.-]?Rip', r'DVD[.-]?Rip',
        r'Dual[ .-]?Audio', r'Multi[ .-]?Audio',
        r'TrueHD', r'DTS[.-]?HD', r'AC3', r'AAC',
        r'Subs?', r'ESub', r'Eng[.-]?Sub'
    ]
    
    # Container patterns
    CONTAINER_PATTERNS = [
        r'\.mkv$', r'\.mp4$', r'\.avi$', r'\.mov$',
        r'\.wmv$', r'\.flv$', r'\.webm$'
    ]
    
    # Group/team tags (in brackets)
    GROUP_PATTERN = r'\[[^\]]+\]'
    
    @classmethod
    def clean_filename(cls, filename: str) -> str:
        """Clean a filename for matching"""
        if not filename:
            return ""
        
        # Convert to lowercase
        cleaned = filename.lower()
        
        # Remove group tags
        cleaned = re.sub(cls.GROUP_PATTERN, '', cleaned)
        
        # Remove quality tags
        for pattern in cls.QUALITY_PATTERNS:
            cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)
        
        # Remove container extensions
        for pattern in cls.CONTAINER_PATTERNS:
            cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)
        
        # Remove special characters and multiple spaces
        cleaned = re.sub(r'[^\w\s]', ' ', cleaned)
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        
        # Remove common words
        stop_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by'}
        words = [word for word in cleaned.split() if word not in stop_words]
        
        return ' '.join(words)
    
    @classmethod
    def extract_year_from_filename(cls, filename: str) -> Optional[int]:
        """Extract year from filename (looking for 4-digit years)"""
        matches = re.findall(r'\b(19[0-9]{2}|20[0-9]{2})\b', filename)
        if matches:
            try:
                return int(matches[0])
            except ValueError:
                return None
        return None


class FuzzyMatcher:
    """Advanced fuzzy matching with multiple strategies"""
    
    @staticmethod
    def match_movie(title: str, year: Optional[int], 
                   legacy_movies: List[Dict]) -> Tuple[bool, Optional[Dict]]:
        """
        Match TMDB movie against legacy database entries
        
        Returns: (is_found, best_match)
        """
        if not legacy_movies:
            return False, None
        
        best_score = 0
        best_match = None
        
        cleaned_title = FilenameCleaner.clean_filename(title)
        
        for legacy_movie in legacy_movies:
            legacy_filename = legacy_movie.get('filename', '')
            legacy_cleaned = FilenameCleaner.clean_filename(legacy_filename)
            
            # Calculate multiple similarity scores
            token_set_ratio = fuzz.token_set_ratio(cleaned_title, legacy_cleaned)
            token_sort_ratio = fuzz.token_sort_ratio(cleaned_title, legacy_cleaned)
            partial_ratio = fuzz.partial_ratio(cleaned_title, legacy_cleaned)
            
            # Weighted average
            score = (token_set_ratio * 0.5 + 
                    token_sort_ratio * 0.3 + 
                    partial_ratio * 0.2)
            
            # Check year if available
            if year and 'year' in legacy_movie:
                year_diff = abs(year - legacy_movie['year'])
                if year_diff > 2:  # Allow 2-year difference
                    score *= 0.5  # Penalize year mismatch
            
            if score > best_score:
                best_score = score
                best_match = legacy_movie
        
        # Check if best score meets threshold
        if best_score >= config.FUZZY_MATCH_THRESHOLD:
            return True, best_match
        
        return False, None


class MessageFormatter:
    """Format messages and captions"""
    
    @staticmethod
    def format_movie_caption(movie: Dict, is_available: bool = False) -> str:
        """Format movie details caption"""
        status = "✅ Available" if is_available else "❌ Not Available"
        
        caption = (
            f"🎬 *{movie['title']}*"
            f"\n📅 *Year:* {movie.get('year', 'N/A')}"
            f"\n⭐ *Rating:* {movie.get('vote_average', 'N/A')}/10"
            f"\n⏱️ *Runtime:* {movie.get('runtime', 'N/A')} min"
        )
        
        if movie.get('genres'):
            caption += f"\n🎭 *Genres:* {', '.join(movie['genres'][:3])}"
        
        if movie.get('overview'):
            overview = movie['overview']
            if len(overview) > 300:
                overview = overview[:300] + "..."
            caption += f"\n\n📝 *Overview:*\n{overview}"
        
        caption += f"\n\n{status}"
        
        return caption
    
    @staticmethod
    def format_request_notification(request: Dict) -> str:
        """Format request notification for admin channel"""
        return (
            f"📥 *New Movie Request*\n\n"
            f"🎬 *Title:* {request['title']}\n"
            f"📅 *Year:* {request.get('year', 'N/A')}\n"
            f"👤 *User:* {request['user_id']}\n"
            f"🆔 *TMDB ID:* {request['tmdb_id']}\n"
            f"⏰ *Requested:* {request['created_at'].strftime('%Y-%m-%d %H:%M')}"
        )


# Initialize clients
tmdb_client = TMDBClient()
filename_cleaner = FilenameCleaner()
fuzzy_matcher = FuzzyMatcher()
formatter = MessageFormatter()
