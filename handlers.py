import re
import logging
from typing import List, Dict, Optional
from datetime import datetime

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup,
    InlineKeyboardButton, InputMediaPhoto, FSInputFile
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

from config import config
from database import db
from utils import tmdb_client, fuzzy_matcher, formatter, filename_cleaner

logger = logging.getLogger(__name__)

# Define routers
user_router = Router()
admin_router = Router()

# States
class MovieSearch(StatesGroup):
    waiting_for_movie_name = State()

# Callback data patterns
class CallbackData:
    SEARCH_RESULT = "srch"
    REQUEST_MOVIE = "req"
    DELETE_REQUEST = "del"
    ADMIN_APPROVE = "appr"
    ADMIN_REJECT = "rej"
    SHOW_PENDING = "pend"
    PAGE = "page"

# Helper functions
def create_search_keyboard(movies: List[Dict], page: int = 1) -> InlineKeyboardMarkup:
    """Create inline keyboard for search results"""
    buttons = []
    
    for movie in movies:
        year = movie.get('year', 'N/A')
        button_text = f"{movie['title']} ({year})"
        callback_data = f"{CallbackData.SEARCH_RESULT}:{movie['id']}"
        buttons.append([InlineKeyboardButton(text=button_text, callback_data=callback_data)])
    
    # Navigation buttons if needed
    if len(movies) == config.RESULTS_PER_PAGE:
        nav_buttons = []
        if page > 1:
            nav_buttons.append(InlineKeyboardButton(
                text="⬅️ Previous", 
                callback_data=f"{CallbackData.PAGE}:{page-1}"
            ))
        nav_buttons.append(InlineKeyboardButton(
            text="➡️ Next", 
            callback_data=f"{CallbackData.PAGE}:{page+1}"
        ))
        buttons.append(nav_buttons)
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def create_movie_detail_keyboard(
    movie: Dict, 
    is_available: bool, 
    request_id: Optional[str] = None
) -> InlineKeyboardMarkup:
    """Create keyboard for movie details based on availability"""
    buttons = []
    
    if is_available:
        # Movie is available - show search group link
        buttons.append([
            InlineKeyboardButton(
                text="📂 Get from Search Group",
                url=config.SEARCH_GROUP_LINK
            )
        ])
    else:
        # Movie not available - show request button
        buttons.append([
            InlineKeyboardButton(
                text="📥 Request This Movie",
                callback_data=f"{CallbackData.REQUEST_MOVIE}:{movie['id']}"
            )
        ])
    
    # Back to search button
    buttons.append([
        InlineKeyboardButton(text="🔍 Search Again", callback_data="search_again")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def create_pending_requests_keyboard(requests: List[Dict]) -> InlineKeyboardMarkup:
    """Create keyboard for pending requests with delete buttons"""
    buttons = []
    
    for req in requests:
        button_text = f"🗑️ {req['title']} ({req.get('year', 'N/A')})"
        callback_data = f"{CallbackData.DELETE_REQUEST}:{str(req['_id'])}"
        buttons.append([InlineKeyboardButton(text=button_text, callback_data=callback_data)])
    
    buttons.append([
        InlineKeyboardButton(text="🔍 New Search", callback_data="search_again")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def create_admin_actions_keyboard(request_id: str) -> InlineKeyboardMarkup:
    """Create admin action buttons"""
    buttons = [
        [
            InlineKeyboardButton(
                text="✅ Uploaded",
                callback_data=f"{CallbackData.ADMIN_APPROVE}:{request_id}"
            ),
            InlineKeyboardButton(
                text="🚫 Reject",
                callback_data=f"{CallbackData.ADMIN_REJECT}:{request_id}"
            )
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# User Handlers
@user_router.message(Command("start"))
async def cmd_start(message: Message):
    """Start command handler"""
    welcome_text = (
        "🎬 *Welcome to Movie Request Bot!*\n\n"
        "I can help you find movies and request ones that aren't available.\n\n"
        "*How to use:*\n"
        "1. Send me a movie name\n"
        "2. I'll search TMDB and show results\n"
        "3. Select a movie to check availability\n"
        "4. Request if not available (max 3 pending requests)\n\n"
        "🔍 *Just send me a movie name to start!*"
    )
    
    await message.answer(welcome_text, parse_mode="Markdown")

@user_router.message(Command("myrequests"))
async def cmd_myrequests(message: Message):
    """Show user's pending requests"""
    user_id = message.from_user.id
    
    # Check quota
    can_request, pending_count, pending_requests = await db.check_user_quota(user_id)
    
    if pending_count == 0:
        await message.answer(
            "📭 You have no pending requests.\n"
            "Send me a movie name to search and request!"
        )
        return
    
    text = f"📋 *Your Pending Requests ({pending_count}/{config.MAX_PENDING_REQUESTS}):*\n\n"
    
    for i, req in enumerate(pending_requests, 1):
        text += f"{i}. *{req['title']}* ({req.get('year', 'N/A')})\n"
        text += f"   Requested: {req['created_at'].strftime('%Y-%m-%d')}\n\n"
    
    text += "\nClick a button below to delete a request:"
    
    keyboard = create_pending_requests_keyboard(pending_requests)
    await message.answer(text, parse_mode="Markdown", reply_markup=keyboard)

@user_router.message(F.text)
async def handle_movie_search(message: Message, state: FSMContext):
    """Handle movie search requests"""
    search_query = message.text.strip()
    
    if len(search_query) < 2:
        await message.answer("Please enter a longer movie name (at least 2 characters)")
        return
    
    await state.set_state(MovieSearch.waiting_for_movie_name)
    await state.update_data(search_query=search_query, page=1)
    
    # Show typing action
    await message.bot.send_chat_action(message.chat.id, "typing")
    
    # Search TMDB
    movies = await tmdb_client.search_movies(search_query)
    
    if not movies:
        await message.answer(
            "❌ No movies found. Please try a different search term."
        )
        await state.clear()
        return
    
    # Create response
    text = f"🔍 *Search Results for:* `{search_query}`\n\n"
    text += "*Select a movie:*\n"
    
    keyboard = create_search_keyboard(movies)
    await message.answer(text, parse_mode="Markdown", reply_markup=keyboard)
    await state.clear()

@user_router.callback_query(F.data.startswith(CallbackData.SEARCH_RESULT))
async def handle_movie_selection(callback: CallbackQuery):
    """Handle movie selection from search results"""
    await callback.answer()
    
    try:
        # Extract TMDB ID
        tmdb_id = int(callback.data.split(":")[1])
        
        # Get movie details
        movie = await tmdb_client.get_movie_details(tmdb_id)
        if not movie:
            await callback.message.edit_text("❌ Error fetching movie details")
            return
        
        # Check in legacy database
        legacy_movies = await db.find_movie_in_legacy(
            filename_cleaner.clean_filename(movie['title']),
            movie.get('year')
        )
        
        # Check availability using fuzzy matching
        is_available, best_match = fuzzy_matcher.match_movie(
            movie['title'],
            movie.get('year'),
            legacy_movies
        )
        
        # Log the check
        await db.log_activity(
            callback.from_user.id,
            "movie_check",
            {
                "tmdb_id": tmdb_id,
                "title": movie['title'],
                "available": is_available,
                "match_score": getattr(best_match, 'score', 0) if best_match else 0
            }
        )
        
        # Format caption
        caption = formatter.format_movie_caption(movie, is_available)
        
        # Create keyboard
        keyboard = create_movie_detail_keyboard(movie, is_available)
        
        # Send movie details with poster if available
        if movie.get('poster_path'):
            poster_url = f"https://image.tmdb.org/t/p/w500{movie['poster_path']}"
            try:
                await callback.message.answer_photo(
                    photo=poster_url,
                    caption=caption,
                    parse_mode="Markdown",
                    reply_markup=keyboard
                )
            except Exception as e:
                logger.error(f"Error sending photo: {e}")
                await callback.message.answer(
                    caption, parse_mode="Markdown", reply_markup=keyboard
                )
        else:
            await callback.message.answer(
                caption, parse_mode="Markdown", reply_markup=keyboard
            )
        
        # Delete original search message
        try:
            await callback.message.delete()
        except:
            pass
            
    except Exception as e:
        logger.error(f"Error in movie selection: {e}")
        await callback.message.edit_text("❌ Error processing selection")

@user_router.callback_query(F.data.startswith(CallbackData.REQUEST_MOVIE))
async def handle_movie_request(callback: CallbackQuery):
    """Handle movie request"""
    await callback.answer()
    
    user_id = callback.from_user.id
    tmdb_id = int(callback.data.split(":")[1])
    
    # Get movie details again
    movie = await tmdb_client.get_movie_details(tmdb_id)
    if not movie:
        await callback.message.edit_text("❌ Error fetching movie details")
        return
    
    # Check user quota
    can_request, pending_count, pending_requests = await db.check_user_quota(user_id)
    
    if not can_request:
        # User has max pending requests
        text = (
            f"⚠️ *Request Limit Reached*\n\n"
            f"You have {pending_count}/{config.MAX_PENDING_REQUESTS} pending requests.\n"
            f"You must delete one to make a new request.\n\n"
            f"*Your pending requests:*"
        )
        
        keyboard = create_pending_requests_keyboard(pending_requests)
        await callback.message.answer(text, parse_mode="Markdown", reply_markup=keyboard)
        return
    
    # Create request
    request_id = await db.create_request(
        user_id=user_id,
        tmdb_id=tmdb_id,
        title=movie['title'],
        year=movie.get('year')
    )
    
    if not request_id:
        await callback.message.edit_text("❌ Error creating request. Please try again.")
        return
    
    # Notify user
    user_text = (
        f"✅ *Request Submitted!*\n\n"
        f"🎬 *Movie:* {movie['title']}\n"
        f"📅 *Year:* {movie.get('year', 'N/A')}\n\n"
        f"Your request has been sent to the admin team.\n"
        f"You'll be notified when it's uploaded.\n\n"
        f"📋 *Pending requests:* {pending_count + 1}/{config.MAX_PENDING_REQUESTS}"
    )
    
    await callback.message.answer(user_text, parse_mode="Markdown")
    
    # Log the request
    await db.log_activity(
        user_id,
        "movie_request",
        {
            "tmdb_id": tmdb_id,
            "title": movie['title'],
            "request_id": request_id
        }
    )
    
    # Send to admin channel
    admin_text = formatter.format_request_notification({
        'title': movie['title'],
        'year': movie.get('year'),
        'user_id': user_id,
        'tmdb_id': tmdb_id,
        'created_at': datetime.utcnow()
    })
    
    admin_keyboard = create_admin_actions_keyboard(request_id)
    
    try:
        bot = callback.bot
        await bot.send_message(
            chat_id=config.ADMIN_CHANNEL_ID,
            text=admin_text,
            parse_mode="Markdown",
            reply_markup=admin_keyboard
        )
    except Exception as e:
        logger.error(f"Error sending to admin channel: {e}")

@user_router.callback_query(F.data.startswith(CallbackData.DELETE_REQUEST))
async def handle_delete_request(callback: CallbackQuery):
    """Handle request deletion"""
    await callback.answer()
    
    user_id = callback.from_user.id
    request_id = callback.data.split(":")[1]
    
    # Delete request
    deleted = await db.delete_request(request_id, user_id)
    
    if deleted:
        await callback.message.edit_text("✅ Request deleted successfully!")
        
        # Update message if it was a quota warning
        await asyncio.sleep(2)
        await callback.message.answer(
            "You can now make a new request. Send me a movie name!",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔍 Search Movies", callback_data="search_again")
            ]])
        )
    else:
        await callback.message.edit_text("❌ Error deleting request.")

@user_router.callback_query(F.data == "search_again")
async def handle_search_again(callback: CallbackQuery):
    """Handle search again button"""
    await callback.answer()
    await callback.message.answer("🔍 Send me a movie name to search:")

# Admin Handlers
@admin_router.callback_query(F.data.startswith(CallbackData.ADMIN_APPROVE))
async def handle_admin_approve(callback: CallbackQuery):
    """Handle admin approval"""
    await callback.answer()
    
    if callback.message.chat.id != config.ADMIN_CHANNEL_ID:
        return
    
    request_id = callback.data.split(":")[1]
    
    # Update request status
    updated = await db.update_request_status(request_id, "completed")
    
    if updated:
        # Get request details
        request = await db.system_db.requests.find_one({"_id": request_id})
        if request:
            # Notify user
            try:
                user_text = (
                    f"🎉 *Good News!*\n\n"
                    f"Your requested movie has been uploaded:\n\n"
                    f"🎬 *{request['title']}* ({request.get('year', 'N/A')})\n\n"
                    f"Check the file channel for download."
                )
                await callback.bot.send_message(
                    chat_id=request['user_id'],
                    text=user_text,
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Error notifying user: {e}")
        
        # Update admin message
        await callback.message.edit_text(
            f"✅ Request marked as uploaded.\n\n{callback.message.text}",
            parse_mode="Markdown"
        )
    else:
        await callback.answer("Error updating request", show_alert=True)

@admin_router.callback_query(F.data.startswith(CallbackData.ADMIN_REJECT))
async def handle_admin_reject(callback: CallbackQuery):
    """Handle admin rejection"""
    await callback.answer()
    
    if callback.message.chat.id != config.ADMIN_CHANNEL_ID:
        return
    
    request_id = callback.data.split(":")[1]
    
    # Update request status
    updated = await db.update_request_status(request_id, "rejected")
    
    if updated:
        # Get request details
        request = await db.system_db.requests.find_one({"_id": request_id})
        if request:
            # Notify user
            try:
                user_text = (
                    f"📭 *Request Update*\n\n"
                    f"Your movie request has been reviewed:\n\n"
                    f"🎬 *{request['title']}* ({request.get('year', 'N/A')})\n"
                    f"📊 *Status:* ❌ Rejected\n\n"
                    f"You can make a new request now."
                )
                await callback.bot.send_message(
                    chat_id=request['user_id'],
                    text=user_text,
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Error notifying user: {e}")
        
        # Update admin message
        await callback.message.edit_text(
            f"🚫 Request rejected.\n\n{callback.message.text}",
            parse_mode="Markdown"
        )
    else:
        await callback.answer("Error updating request", show_alert=True)

# Channel listener for auto-completion
@admin_router.channel_post()
async def handle_channel_post(message: Message):
    """Monitor file channel for auto-completion"""
    if message.chat.id != config.FILE_CHANNEL_ID:
        return
    
    # Extract text from caption or document filename
    text = ""
    if message.caption:
        text = message.caption
    elif message.document:
        text = message.document.file_name or ""
    
    if not text:
        return
    
    # Try to extract TMDB ID from caption (if added by admins)
    tmdb_match = re.search(r'TMDB[:_\- ]*(\d+)', text, re.IGNORECASE)
    if tmdb_match:
        tmdb_id = int(tmdb_match.group(1))
        
        # Find pending requests for this TMDB ID
        pending_requests = await db.find_pending_by_tmdb_id(tmdb_id)
        
        for request in pending_requests:
            # Update request status
            await db.update_request_status(str(request['_id']), "completed")
            
            # Notify user
            try:
                user_text = (
                    f"🎉 *Good News!*\n\n"
                    f"Your requested movie has been uploaded:\n\n"
                    f"🎬 *{request['title']}* ({request.get('year', 'N/A')})\n\n"
                    f"Check the file channel for download."
                )
                await message.bot.send_message(
                    chat_id=request['user_id'],
                    text=user_text,
                    parse_mode="Markdown"
                )
                
                # Log auto-completion
                await db.log_activity(
                    request['user_id'],
                    "auto_completed",
                    {
                        "request_id": str(request['_id']),
                        "tmdb_id": tmdb_id,
                        "title": request['title']
                    }
                )
            except Exception as e:
                logger.error(f"Error notifying user in auto-completion: {e}")
        
        return
    
    # If no TMDB ID, try fuzzy matching with pending requests
    cleaned_text = filename_cleaner.clean_filename(text)
    year = filename_cleaner.extract_year_from_filename(text)
    
    if cleaned_text:
        # Get all pending requests
        cursor = db.system_db.requests.find({"status": "pending"})
        all_requests = await cursor.to_list(length=100)
        
        for request in all_requests:
            cleaned_title = filename_cleaner.clean_filename(request['title'])
            
            # Calculate similarity
            similarity = fuzz.token_set_ratio(cleaned_text, cleaned_title)
            
            # Check year if available
            year_match = True
            if year and request.get('year'):
                year_match = abs(year - request['year']) <= 2
            
            if similarity >= config.FUZZY_MATCH_THRESHOLD and year_match:
                # Update request status
                await db.update_request_status(str(request['_id']), "completed")
                
                # Notify user
                try:
                    user_text = (
                        f"🎉 *Good News!*\n\n"
                        f"Your requested movie has been uploaded:\n\n"
                        f"🎬 *{request['title']}* ({request.get('year', 'N/A')})\n\n"
                        f"Check the file channel for download."
                    )
                    await message.bot.send_message(
                        chat_id=request['user_id'],
                        text=user_text,
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.error(f"Error notifying user in fuzzy auto-completion: {e}")
