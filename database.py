import sqlite3
import logging
from typing import List, Dict

logger = logging.getLogger(__name__)

class Database:
    def __init__(self, db_path='bot_data.db'):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.init_db()
    
    def init_db(self):
        """Initialize database tables"""
        cursor = self.conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS services (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                source_channels TEXT,
                target_channels TEXT,
                active INTEGER DEFAULT 1
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS blacklist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT UNIQUE
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS processed_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                message_id INTEGER,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS statistics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service_name TEXT UNIQUE,
                forwarded INTEGER DEFAULT 0,
                blocked INTEGER DEFAULT 0
            )
        ''')
        
        self.conn.commit()
        logger.info("✓ Database initialized")
    
    def get_services(self) -> List[Dict]:
        """Get all services"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT name, source_channels, target_channels, active FROM services")
        rows = cursor.fetchall()
        
        return [
            {
                'name': row[0],
                'source_channels': row[1],
                'target_channels': row[2],
                'active': row[3]
            }
            for row in rows
        ]
    
    def add_service(self, name: str, source_channels: str, target_channels: str):
        """Add a new service"""
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO services (name, source_channels, target_channels) VALUES (?, ?, ?)",
            (name, source_channels, target_channels)
        )
        self.conn.commit()
    
    def toggle_service(self, name: str) -> bool:
        """Toggle service active status"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT active FROM services WHERE name = ?", (name,))
        result = cursor.fetchone()
        
        if not result:
            return False
        
        new_status = 0 if result[0] else 1
        cursor.execute("UPDATE services SET active = ? WHERE name = ?", (new_status, name))
        self.conn.commit()
        return True
    
    def get_blacklist(self) -> List[str]:
        """Get all blacklisted keywords"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT keyword FROM blacklist")
        return [row[0].lower() for row in cursor.fetchall()]
    
    def add_blacklist(self, keyword: str):
        """Add keyword to blacklist"""
        cursor = self.conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO blacklist (keyword) VALUES (?)", (keyword,))
        self.conn.commit()
    
    def remove_blacklist(self, keyword: str):
        """Remove keyword from blacklist"""
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM blacklist WHERE keyword = ?", (keyword,))
        self.conn.commit()
    
    def mark_as_processed(self, chat_id: int, message_id: int):
        """Mark message as processed (called async from forwarder)"""
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO processed_messages (chat_id, message_id) VALUES (?, ?)",
            (chat_id, message_id)
        )
        self.conn.commit()
    
    def increment_stat(self, service_name: str, stat_type: str):
        """Increment forwarded or blocked count (called async from forwarder)"""
        cursor = self.conn.cursor()
        
        cursor.execute(
            "SELECT forwarded, blocked FROM statistics WHERE service_name = ?",
            (service_name,)
        )
        result = cursor.fetchone()
        
        if result:
            forwarded, blocked = result
            if stat_type == 'forwarded':
                forwarded += 1
            elif stat_type == 'blocked':
                blocked += 1
            
            cursor.execute(
                "UPDATE statistics SET forwarded = ?, blocked = ? WHERE service_name = ?",
                (forwarded, blocked, service_name)
            )
        else:
            forwarded = 1 if stat_type == 'forwarded' else 0
            blocked = 1 if stat_type == 'blocked' else 0
            cursor.execute(
                "INSERT INTO statistics (service_name, forwarded, blocked) VALUES (?, ?, ?)",
                (service_name, forwarded, blocked)
            )
        
        self.conn.commit()
    
    def get_statistics(self) -> List[Dict]:
        """Get all statistics"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT service_name, forwarded, blocked FROM statistics")
        rows = cursor.fetchall()
        
        return [
            {
                'service': row[0],
                'forwarded': row[1],
                'blocked': row[2]
            }
            for row in rows
        ]
    
    def close(self):
        """Close database connection"""
        self.conn.close()
