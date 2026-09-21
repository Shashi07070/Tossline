import asyncio
import logging
import os
from telethon import TelegramClient, events
from dotenv import load_dotenv
from database import Database
from forwarder import setup_handlers

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

# Global database instance
db = None

# ==================== ADMIN COMMANDS ====================

@client.on(events.NewMessage(pattern='/start', from_users=ADMIN_USER_ID))
async def cmd_start(event):
    """Show help message"""
    await event.respond(
        "🤖 **Telegram Forwarding Bot** (Optimized)\n\n"
        "**Service Management:**\n"
        "• `/add_service <name> <source_ids> <target_ids>` - Create new service\n"
        "• `/list_services` - Show all services\n"
        "• `/toggle_service <name>` - Enable/disable service\n\n"
        "**Blacklist Management:**\n"
        "• `/add_blacklist <keyword>` - Block keyword\n"
        "• `/remove_blacklist <keyword>` - Unblock keyword\n"
        "• `/list_blacklist` - Show blocked keywords\n\n"
        "**Statistics:**\n"
        "• `/stats` - Show forwarding statistics\n\n"
        "**System:**\n"
        "• `/reload` - Reload handlers (apply changes)\n\n"
        "⚡ **Performance:** Zero-delay forwarding enabled"
    )

@client.on(events.NewMessage(pattern=r'/add_service\s+', from_users=ADMIN_USER_ID))
async def cmd_add_service(event):
    """Add new forwarding service"""
    try:
        parts = event.message.text.split(maxsplit=3)
        
        if len(parts) != 4:
            await event.respond(
                "❌ Invalid format\n\n"
                "Usage: `/add_service <name> <source_ids> <target_ids>`\n\n"
                "Example:\n"
                "`/add_service news -1001234567890 -1009876543210`"
            )
            return
        
        name = parts[1]
        source_channels = parts[2]
        target_channels = parts[3]
        
        db.add_service(name, source_channels, target_channels)
        await event.respond(f"✅ Service **{name}** created\n\n⚠️ Run `/reload` to activate")
        logger.info(f"Admin created service: {name}")
    
    except Exception as e:
        await event.respond(f"❌ Error: {e}")
        logger.error(f"Error adding service: {e}")

@client.on(events.NewMessage(pattern='/list_services', from_users=ADMIN_USER_ID))
async def cmd_list_services(event):
    """List all services"""
    services = db.get_services()
    
    if not services:
        await event.respond("📭 No services configured\n\nUse `/add_service` to create one")
        return
    
    response = "**📋 Services:**\n\n"
    for service in services:
        status = "✅ Active" if service['active'] else "❌ Disabled"
        response += (
            f"**{service['name']}** ({status})\n"
            f"├ Source: `{service['source_channels']}`\n"
            f"└ Target: `{service['target_channels']}`\n\n"
        )
    
    await event.respond(response)

@client.on(events.NewMessage(pattern=r'/toggle_service\s+', from_users=ADMIN_USER_ID))
async def cmd_toggle_service(event):
    """Enable/disable a service"""
    try:
        name = event.message.text.split(maxsplit=1)[1]
        
        if db.toggle_service(name):
            services = db.get_services()
            service = next((s for s in services if s['name'] == name), None)
            status = "enabled ✅" if service['active'] else "disabled ❌"
            await event.respond(f"Service **{name}** {status}\n\n⚠️ Run `/reload` to apply")
            logger.info(f"Admin toggled service: {name} → {status}")
        else:
            await event.respond(f"❌ Service **{name}** not found")
    
    except IndexError:
        await event.respond("❌ Usage: `/toggle_service <name>`")
    except Exception as e:
        await event.respond(f"❌ Error: {e}")

@client.on(events.NewMessage(pattern=r'/add_blacklist\s+', from_users=ADMIN_USER_ID))
async def cmd_add_blacklist(event):
    """Add keyword to blacklist"""
    try:
        keyword = event.message.text.split(maxsplit=1)[1]
        db.add_blacklist(keyword)
        await event.respond(f"🚫 Blacklisted: **{keyword}**\n\n⚠️ Run `/reload` to apply")
        logger.info(f"Admin added blacklist: {keyword}")
    
    except IndexError:
        await event.respond("❌ Usage: `/add_blacklist <keyword>`")
    except Exception as e:
        await event.respond(f"❌ Error: {e}")

@client.on(events.NewMessage(pattern=r'/remove_blacklist\s+', from_users=ADMIN_USER_ID))
async def cmd_remove_blacklist(event):
    """Remove keyword from blacklist"""
    try:
        keyword = event.message.text.split(maxsplit=1)[1]
        db.remove_blacklist(keyword)
        await event.respond(f"✅ Removed from blacklist: **{keyword}**\n\n⚠️ Run `/reload` to apply")
        logger.info(f"Admin removed blacklist: {keyword}")
    
    except IndexError:
        await event.respond("❌ Usage: `/remove_blacklist <keyword>`")
    except Exception as e:
        await event.respond(f"❌ Error: {e}")

@client.on(events.NewMessage(pattern='/list_blacklist', from_users=ADMIN_USER_ID))
async def cmd_list_blacklist(event):
    """List all blacklisted keywords"""
    blacklist = db.get_blacklist()
    
    if not blacklist:
        await event.respond("✅ No blacklisted keywords\n\nUse `/add_blacklist` to add one")
        return
    
    response = "**🚫 Blacklisted Keywords:**\n\n"
    response += "\n".join(f"• {keyword}" for keyword in blacklist)
    await event.respond(response)

@client.on(events.NewMessage(pattern='/stats', from_users=ADMIN_USER_ID))
async def cmd_stats(event):
    """Show forwarding statistics"""
    stats = db.get_statistics()
    
    if not stats:
        await event.respond("📊 No statistics available yet")
        return
    
    response = "**📊 Forwarding Statistics:**\n\n"
    total_forwarded = 0
    total_blocked = 0
    
    for stat in stats:
        response += (
            f"**{stat['service']}**\n"
            f"├ Forwarded: {stat['forwarded']}\n"
            f"└ Blocked: {stat['blocked']}\n\n"
        )
        total_forwarded += stat['forwarded']
        total_blocked += stat['blocked']
    
    response += (
        f"**Total**\n"
        f"├ Forwarded: {total_forwarded}\n"
        f"└ Blocked: {total_blocked}"
    )
    
    await event.respond(response)

@client.on(events.NewMessage(pattern='/reload', from_users=ADMIN_USER_ID))
async def cmd_reload(event):
    """Reload handlers to apply configuration changes"""
    try:
        # Re-import to get fresh module
        import importlib
        import forwarder
        importlib.reload(forwarder)
        
        # Setup new handlers
        from forwarder import setup_handlers
        setup_handlers(client, db)
        
        await event.respond("✅ Handlers reloaded\n\nAll configuration changes are now active")
        logger.info("Admin reloaded handlers")
    
    except Exception as e:
        await event.respond(f"❌ Error reloading: {e}")
        logger.error(f"Error reloading handlers: {e}")

# ==================== MAIN ====================

async def main():
    """Main bot entry point"""
    global db
    
    # Initialize database
    db = Database()
    logger.info("✓ Database initialized")
    
    # Start Telegram client
    await client.start(phone=PHONE)
    logger.info("✓ Telegram client started")
    
    # Get bot info
    me = await client.get_me()
    logger.info(f"✓ Logged in as: {me.first_name} ({me.phone})")
    
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
