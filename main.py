import asyncio
import logging
import signal
from contextlib import asynccontextmanager

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from config import config
from database import db
from handlers import user_router, admin_router

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Global bot instance
bot = None
dp = None

@asynccontextmanager
async def lifespan():
    """Application lifespan management"""
    # Startup
    logger.info("Starting Movie Request Bot...")
    
    # Validate config
    try:
        config.validate()
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        raise
    
    # Connect to databases
    try:
        await db.connect()
        logger.info("Databases connected successfully")
    except Exception as e:
        logger.error(f"Failed to connect to databases: {e}")
        raise
    
    yield
    
    # Shutdown
    logger.info("Shutting down Movie Request Bot...")
    await db.close()

async def main():
    """Main application entry point"""
    global bot, dp
    
    async with lifespan():
        # Initialize bot and dispatcher
        bot = Bot(token=config.BOT_TOKEN, parse_mode="Markdown")
        storage = MemoryStorage()
        dp = Dispatcher(storage=storage)
        
        # Include routers
        dp.include_router(user_router)
        dp.include_router(admin_router)
        
        # Set up signal handlers
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(
                sig,
                lambda s=sig: asyncio.create_task(shutdown(s))
            )
        
        # Start polling
        logger.info("Bot started polling")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

async def shutdown(signal_name: str = None):
    """Graceful shutdown"""
    if signal_name:
        logger.info(f"Received {signal_name}, shutting down...")
    
    if dp:
        await dp.stop_polling()
    
    if bot:
        await bot.session.close()
    
    logger.info("Bot shutdown complete")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        raise
