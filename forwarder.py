import asyncio
import logging
from telethon import events
from telethon.errors import FloodWaitError
from filters import should_forward_message
from collections import deque

logger = logging.getLogger(__name__)

# In-memory duplicate cache for FAST lookups
duplicate_cache = {}  # {chat_id: deque([(msg_id), ...], maxlen=10000)}
CACHE_SIZE = 10000

def is_duplicate_fast(chat_id, message_id):
    """Fast in-memory duplicate check (no DB query)"""
    if chat_id not in duplicate_cache:
        duplicate_cache[chat_id] = deque(maxlen=CACHE_SIZE)
    
    if message_id in duplicate_cache[chat_id]:
        return True
    
    duplicate_cache[chat_id].append(message_id)
    return False

async def persist_to_db_async(db, chat_id, message_id):
    """Non-blocking DB write"""
    await asyncio.to_thread(db.mark_as_processed, chat_id, message_id)

async def update_stats_async(db, service_name, stat_type):
    """Non-blocking stats update"""
    await asyncio.to_thread(db.increment_stat, service_name, stat_type)

async def forward_message(client, event, service, db, blacklist):
    """
    OPTIMIZED: Zero-delay forwarding path
    
    Performance improvements:
    - Removed artificial 2s sleep
    - In-memory duplicate check (no DB query)
    - Async DB writes (non-blocking)
    - Stats updated in background
    """
    
    # ✅ FAST: In-memory duplicate check
    if is_duplicate_fast(event.chat_id, event.message.id):
        logger.info(f"[{service['name']}] Duplicate message {event.message.id} - skipping")
        asyncio.create_task(update_stats_async(db, service['name'], 'blocked'))
        return
    
    # ✅ FAST: Filter checks (no DB/network I/O)
    if not should_forward_message(event.message, blacklist):
        logger.info(f"[{service['name']}] Message {event.message.id} blocked by filters")
        asyncio.create_task(update_stats_async(db, service['name'], 'blocked'))
        return
    
    # ✅ IMMEDIATE FORWARDING - NO DELAY
    target_channels = [int(ch.strip()) for ch in service['target_channels'].split(',')]
    
    for target_chat in target_channels:
        try:
            # Send as new message (no "Forwarded from" watermark)
            await client.send_message(target_chat, event.message.text)
            logger.info(f"[{service['name']}] ✓ Forwarded msg {event.message.id} → {target_chat}")
        
        except FloodWaitError as e:
            # ✅ ONLY delay on actual Telegram flood limit
            logger.warning(f"FloodWait: sleeping {e.seconds}s for {target_chat}")
            await asyncio.sleep(e.seconds)
            await client.send_message(target_chat, event.message.text)
        
        except Exception as e:
            logger.error(f"[{service['name']}] Error forwarding to {target_chat}: {e}")
    
    # ✅ ASYNC: DB/stats updates happen AFTER sending (non-blocking)
    asyncio.create_task(persist_to_db_async(db, event.chat_id, event.message.id))
    asyncio.create_task(update_stats_async(db, service['name'], 'forwarded'))

def setup_handlers(client, db):
    """Setup event handlers for all active services"""
    services = db.get_services()
    blacklist = db.get_blacklist()
    
    active_handlers = set()
    
    for service in services:
        if not service['active']:
            continue
        
        source_channels = [int(ch.strip()) for ch in service['source_channels'].split(',')]
        
        # Prevent duplicate handlers
        handler_key = (service['name'], tuple(source_channels))
        if handler_key in active_handlers:
            continue
        
        active_handlers.add(handler_key)
        
        @client.on(events.NewMessage(chats=source_channels))
        async def handler(event, svc=service, bl=blacklist):
            await forward_message(client, event, svc, db, bl)
        
        logger.info(f"✓ Handler registered for '{service['name']}' (sources: {source_channels})")
