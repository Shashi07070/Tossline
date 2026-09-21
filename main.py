import asyncio
import logging
import os
from telethon import TelegramClient
from dotenv import load_dotenv
from database import Database
from forwarder import setup_handlers
from admin_commands import setup_admin_commands

# Load environment variables
load_dotenv()

# Configuration
API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
PHONE = os.getenv('PHONE')
ADMIN_USER_ID = int(os.getenv('ADMIN_USER_ID'))

# Logging configuration
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Initialize Telegram client
client = TelegramClient('session_name', API_ID, API_HASH)

async def main():
    """
    Main bot entry point
    
    OPTIMIZED FOR ZERO-LATENCY FORWARDING:
    - Event-driven architecture (no polling)
    - Async DB operations
    - In-memory caching
    - Background task processing
    """
    
    # Initialize database
    db = Database()
    logger.info("✓ Database initialized")
    
    # Start Telegram client
    await client.start(phone=PHONE)
    logger.info("✓ Telegram client started")
    
    # Get bot info
    me = await client.get_me()
    logger.info(f"✓ Logged in as: {me.first_name} ({me.phone})")
    
    # Setup admin commands
    setup_admin_commands(client, db, ADMIN_USER_ID)
    logger.info("✓ Admin commands registered")
    
    # Setup message forwarding handlers (OPTIMIZED - NO DELAYS)
    setup_handlers(client, db)
    logger.info("✓ Forwarding handlers registered")
    
    # Print active services
    services = db.get_services()
    active_services = [s for s in services if s['active']]
    logger.info(f"✓ Active services: {len(active_services)}/{len(services)}")
    
    for service in active_services:
        logger.info(f"  • {service['name']}: {service['source_channels']} → {service['target_channels']}")
    
    # Run until disconnected
    logger.info("🚀 Bot is running with ZERO-DELAY forwarding... Press Ctrl+C to stop")
    await client.run_until_disconnected()
    
    # Cleanup
    db.close()
    logger.info("✓ Bot stopped gracefully")

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
