import logging
import sqlite3
import time
import os
import csv
import re
import threading
import json
import traceback
import phonenumbers
import pycountry
from datetime import datetime, timedelta
from threading import Thread, Lock
from queue import Queue
import telebot
from telebot import types

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Bot configuration
API_TOKEN = "8490533685:AAFqWp8cLxzkLIzRdILWn8UQsngURibH29A"
ADMIN_IDS = [6577308099, 5878787791]
MONITORED_GROUP_ID = -1003437559135  # OTP/Message group
WITHDRAW_LOG_CHANNEL = -1003492385395  # Withdrawal log channel
OTP_GROUP_LINK = "https://t.me/FutureTechotp"

bot = telebot.TeleBot(API_TOKEN, threaded=True, num_threads=20)

# Global variables
pending_withdrawals = {}
message_cache = {}
processing_lock = Lock()

# Database setup with connection pooling
class Database:
    _instance = None
    _lock = threading.RLock()
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Database, cls).__new__(cls)
            cls._instance.init_database()
        return cls._instance
    
    def init_database(self):
        with self._lock:
            self.conn = sqlite3.connect('bot_database.db', check_same_thread=False, timeout=30)
            self.conn.row_factory = sqlite3.Row
            self.create_tables()
            self.migrate_tables()
    
    def create_tables(self):
        c = self.conn.cursor()
        
        # Users table
        c.execute('''CREATE TABLE IF NOT EXISTS users
                    (user_id INTEGER PRIMARY KEY, 
                     username TEXT, 
                     first_name TEXT, 
                     last_name TEXT, 
                     join_date TEXT, 
                     is_banned INTEGER DEFAULT 0,
                     balance REAL DEFAULT 0.0,
                     total_earned REAL DEFAULT 0.0,
                     total_withdrawn REAL DEFAULT 0.0,
                     last_activity TEXT,
                     spam_warnings INTEGER DEFAULT 0,
                     suspended_until TEXT)''')
        
        # Numbers table with batch_name column
        c.execute('''CREATE TABLE IF NOT EXISTS numbers
                    (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                     country TEXT, 
                     number TEXT UNIQUE, 
                     country_code TEXT,
                     country_flag TEXT,
                     batch_name TEXT DEFAULT '',  -- Added batch name
                     is_used INTEGER DEFAULT 0, 
                     used_by INTEGER, 
                     use_date TEXT,
                     assignment_id INTEGER)''')
        
        # Number assignments table
        c.execute('''CREATE TABLE IF NOT EXISTS number_assignments
                    (id INTEGER PRIMARY KEY AUTOINCREMENT,
                     number TEXT,
                     user_id INTEGER,
                     assigned_date TEXT,
                     is_active INTEGER DEFAULT 1,
                     last_otp_date TEXT,
                     otp_count INTEGER DEFAULT 0,
                     total_revenue REAL DEFAULT 0.0)''')
        
        # Countries table
        c.execute('''CREATE TABLE IF NOT EXISTS countries
                    (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                     name TEXT UNIQUE, 
                     code TEXT,
                     flag TEXT,
                     total_numbers INTEGER DEFAULT 0,
                     used_numbers INTEGER DEFAULT 0,
                     price REAL DEFAULT 0.0)''')
        
        # OTP messages table (temporary storage for 15 minutes)
        c.execute('''CREATE TABLE IF NOT EXISTS otp_messages
                    (id INTEGER PRIMARY KEY AUTOINCREMENT,
                     number TEXT,
                     message TEXT,
                     otp_code TEXT,
                     timestamp TEXT,
                     received_date TEXT,
                     country TEXT,
                     country_flag TEXT,
                     message_id INTEGER,
                     forwarded_to INTEGER DEFAULT 0,
                     revenue_added INTEGER DEFAULT 0,
                     processed INTEGER DEFAULT 0)''')
        
        # Message tracking table
        c.execute('''CREATE TABLE IF NOT EXISTS message_tracking
                    (message_id INTEGER PRIMARY KEY,
                     number TEXT,
                     processed INTEGER DEFAULT 0,
                     processed_date TEXT)''')
        
        # Withdrawals table
        c.execute('''CREATE TABLE IF NOT EXISTS withdrawals
                    (id INTEGER PRIMARY KEY AUTOINCREMENT,
                     user_id INTEGER,
                     amount REAL,
                     address TEXT,
                     network TEXT,
                     status TEXT DEFAULT 'pending',
                     request_date TEXT,
                     process_date TEXT,
                     admin_id INTEGER,
                     tx_hash TEXT)''')
        
        # Support tickets table
        c.execute('''CREATE TABLE IF NOT EXISTS support_tickets
                    (id INTEGER PRIMARY KEY AUTOINCREMENT,
                     user_id INTEGER,
                     message TEXT,
                     message_type TEXT,
                     file_id TEXT,
                     status TEXT DEFAULT 'open',
                     created_date TEXT,
                     resolved_date TEXT,
                     admin_id INTEGER,
                     admin_reply TEXT)''')
        
        # Settings table
        c.execute('''CREATE TABLE IF NOT EXISTS settings
                    (id INTEGER PRIMARY KEY CHECK (id = 1),
                     batch_size INTEGER DEFAULT 1,
                     revenue_per_message REAL DEFAULT 0.005,
                     min_withdrawal REAL DEFAULT 3.0,
                     max_user_numbers INTEGER DEFAULT 50,
                     withdrawal_enabled INTEGER DEFAULT 1,
                     bot_enabled INTEGER DEFAULT 1,
                     start_message TEXT,
                     start_message_type TEXT DEFAULT 'text')''')
        
        # User stats table
        c.execute('''CREATE TABLE IF NOT EXISTS user_stats
                    (id INTEGER PRIMARY KEY AUTOINCREMENT,
                     user_id INTEGER,
                     date TEXT,
                     numbers_taken INTEGER DEFAULT 0,
                     messages_received INTEGER DEFAULT 0,
                     revenue_earned REAL DEFAULT 0.0)''')
        
        # Rate limiting table
        c.execute('''CREATE TABLE IF NOT EXISTS rate_limits
                    (user_id INTEGER PRIMARY KEY,
                     get_number_count INTEGER DEFAULT 0,
                     last_reset TEXT,
                     is_suspended INTEGER DEFAULT 0,
                     suspend_until TEXT)''')
        
        # Reset history table
        c.execute('''CREATE TABLE IF NOT EXISTS reset_history
                    (id INTEGER PRIMARY KEY AUTOINCREMENT,
                     user_id INTEGER,
                     number TEXT,
                     country_code TEXT,
                     reset_date TEXT,
                     reset_type TEXT DEFAULT 'user')''')
        
        # Insert default settings if not exists
        c.execute("INSERT OR IGNORE INTO settings (id, batch_size, revenue_per_message, min_withdrawal, max_user_numbers, withdrawal_enabled, bot_enabled) VALUES (1, 1, 0.005, 3.0, 50, 1, 1)")
        
        self.conn.commit()
    
    def migrate_tables(self):
        """Migrate database tables to add new columns if needed"""
        with self._lock:
            try:
                c = self.conn.cursor()
                
                # Check if batch_name column exists in numbers table
                c.execute("PRAGMA table_info(numbers)")
                columns = [col[1] for col in c.fetchall()]
                
                if 'batch_name' not in columns:
                    c.execute("ALTER TABLE numbers ADD COLUMN batch_name TEXT DEFAULT ''")
                    logger.info("Added batch_name column to numbers table")
                
                # Check if max_user_numbers column exists in settings
                c.execute("PRAGMA table_info(settings)")
                columns = [col[1] for col in c.fetchall()]
                if 'max_user_numbers' not in columns:
                    c.execute("ALTER TABLE settings ADD COLUMN max_user_numbers INTEGER DEFAULT 50")
                    c.execute("UPDATE settings SET max_user_numbers = 50 WHERE id = 1")
                    logger.info("Added max_user_numbers column to settings table")
                
                # Check for last_activity column in users table
                c.execute("PRAGMA table_info(users)")
                columns = [col[1] for col in c.fetchall()]
                if 'last_activity' not in columns:
                    c.execute("ALTER TABLE users ADD COLUMN last_activity TEXT")
                    logger.info("Added last_activity column to users table")
                
                # Check for reset_history table
                c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='reset_history'")
                if not c.fetchone():
                    c.execute('''CREATE TABLE reset_history
                                (id INTEGER PRIMARY KEY AUTOINCREMENT,
                                 user_id INTEGER,
                                 number TEXT,
                                 country_code TEXT,
                                 reset_date TEXT,
                                 reset_type TEXT DEFAULT 'user')''')
                    logger.info("Created reset_history table")
                
                self.conn.commit()
            except Exception as e:
                logger.error(f"Error migrating tables: {e}")
    
    def execute(self, query, params=()):
        with self._lock:
            try:
                c = self.conn.cursor()
                c.execute(query, params)
                self.conn.commit()
                return c
            except sqlite3.Error as e:
                logger.error(f"Database error: {e}")
                try:
                    self.conn = sqlite3.connect('bot_database.db', check_same_thread=False, timeout=30)
                    self.conn.row_factory = sqlite3.Row
                    c = self.conn.cursor()
                    c.execute(query, params)
                    self.conn.commit()
                    return c
                except sqlite3.Error as e2:
                    logger.error(f"Database reconnection failed: {e2}")
                    raise
    
    def fetchone(self, query, params=()):
        try:
            c = self.execute(query, params)
            result = c.fetchone()
            if result:
                return dict(result)
            return None
        except Exception as e:
            logger.error(f"Fetchone error: {e}")
            return None
    
    def fetchall(self, query, params=()):
        try:
            c = self.execute(query, params)
            rows = c.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Fetchall error: {e}")
            return []

# Initialize database
db = Database()

# Utility functions
def is_admin(user_id):
    return user_id in ADMIN_IDS

def get_setting(key):
    try:
        result = db.fetchone(f"SELECT {key} FROM settings WHERE id = 1")
        if result:
            return result[key]
        return None
    except Exception as e:
        logger.error(f"Error getting setting {key}: {e}")
        return None

def update_setting(key, value):
    try:
        query = f"UPDATE settings SET {key} = ? WHERE id = 1"
        db.execute(query, (value,))
        return True
    except Exception as e:
        logger.error(f"Error updating setting {key}: {e}")
        return False

def get_user_balance(user_id):
    try:
        result = db.fetchone("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        if result and 'balance' in result:
            return float(result['balance'])
        return 0.0
    except Exception as e:
        logger.error(f"Error getting user balance: {e}")
        return 0.0

def update_user_balance(user_id, amount):
    try:
        current = get_user_balance(user_id)
        new_balance = current + amount
        db.execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_balance, user_id))
        return new_balance
    except Exception as e:
        logger.error(f"Error updating user balance: {e}")
        return current

def add_revenue_to_user(user_id, amount):
    try:
        # Update balance
        new_balance = update_user_balance(user_id, amount)
        
        # Update total earned
        db.execute("UPDATE users SET total_earned = total_earned + ? WHERE user_id = ?", (amount, user_id))
        
        # Update today's revenue stats
        today = datetime.now().strftime("%Y-%m-%d")
        db.execute('''INSERT OR IGNORE INTO user_stats (user_id, date) VALUES (?, ?)''', (user_id, today))
        db.execute('''UPDATE user_stats SET revenue_earned = revenue_earned + ? 
                       WHERE user_id = ? AND date = ?''', (amount, user_id, today))
        return True
    except Exception as e:
        logger.error(f"Error adding revenue: {e}")
        return False

def increment_user_message_count(user_id):
    """Increment the message count for the user for today."""
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        db.execute('''INSERT OR IGNORE INTO user_stats (user_id, date) VALUES (?, ?)''', (user_id, today))
        db.execute('''UPDATE user_stats SET messages_received = messages_received + 1 
                      WHERE user_id = ? AND date = ?''', (user_id, today))
        return True
    except Exception as e:
        logger.error(f"Error incrementing message count: {e}")
        return False

def check_rate_limit(user_id):
    """Check if user is rate limited for get number button"""
    try:
        now = datetime.now()
        
        result = db.fetchone("SELECT * FROM rate_limits WHERE user_id = ?", (user_id,))
        
        if not result:
            db.execute('''INSERT INTO rate_limits (user_id, get_number_count, last_reset) 
                          VALUES (?, 1, ?)''', (user_id, now.strftime("%Y-%m-%d %H:%M:%S")))
            return True, ""
        
        last_reset = datetime.strptime(result['last_reset'], "%Y-%m-%d %H:%M:%S")
        
        # Reset counter if more than 1 minute passed
        if (now - last_reset).seconds > 60:
            db.execute('''UPDATE rate_limits SET get_number_count = 1, last_reset = ?, 
                          is_suspended = 0, suspend_until = NULL WHERE user_id = ?''',
                       (now.strftime("%Y-%m-%d %H:%M:%S"), user_id))
            return True, ""
        
        # Check if suspended
        if result['is_suspended'] == 1 and result['suspend_until']:
            suspend_until = datetime.strptime(result['suspend_until'], "%Y-%m-%d %H:%M:%S")
            if now < suspend_until:
                remaining = (suspend_until - now).seconds // 60
                return False, f"🚫 You are suspended for {remaining} minutes for spamming. Please wait."
        
        # Check count
        if result['get_number_count'] >= 4:
            suspend_until = now + timedelta(minutes=15)
            db.execute('''UPDATE rate_limits SET is_suspended = 1, suspend_until = ? 
                          WHERE user_id = ?''', (suspend_until.strftime("%Y-%m-%d %H:%M:%S"), user_id))
            return False, "🚫 You have been suspended for 15 minutes for clicking too fast (4+ times in 1 minute). Please wait."
        
        db.execute("UPDATE rate_limits SET get_number_count = get_number_count + 1 WHERE user_id = ?", (user_id,))
        return True, ""
    except Exception as e:
        logger.error(f"Error in rate limit: {e}")
        return True, ""

def extract_otp_from_message(text):
    """Extract OTP code from message text - enhanced for all formats"""
    if not text:
        return None
    
    # Clean text first
    text = str(text).strip()
    
    patterns = [
        r'\b\d{4,8}\b',  # 4 to 8 digit OTP
        r'code[:\s\-]*(\d{4,8})',
        r'OTP[:\s\-]*(\d{4,8})',
        r'verification[:\s\-]*(\d{4,8})',
        r'password[:\s\-]*(\d{4,8})',
        r'(\d{4,8})\s+is your',
        r'(\d{4,8})\s+is the',
        r'Your.*code.*is.*(\d{4,8})',
        r'Use.*(\d{4,8})',
        r'[Cc]ode.*\s(\d{4,8})',
        r'\b[A-Z0-9]{4,8}\b'  # Alphanumeric codes
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE | re.MULTILINE)
        if matches:
            for match in matches:
                if isinstance(match, tuple):
                    match = match[0]
                # Validate it's a number or alphanumeric
                if re.match(r'^[A-Z0-9]{4,8}$', match, re.IGNORECASE):
                    return match
    
    # Special cases
    # Look for patterns like "1234 is your verification code"
    special_patterns = [
        r'(\d{4,8})\s*(?:is|as)\s*(?:your|the)\s*(?:code|OTP|verification|password)',
        r'(?:code|OTP|verification|password)\s*(?:is|:)\s*(\d{4,8})'
    ]
    
    for pattern in special_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match and match.group(1):
            return match.group(1)
    
    return None

def extract_number_from_text(text):
    """Extract phone number from text - enhanced for all international formats"""
    if not text:
        return None
    
    text = str(text).strip()
    
    # Comprehensive patterns for international phone numbers
    patterns = [
        r'\+\d{1,4}[-.\s]?\d{1,14}(?:[-.\s]?\d{1,13})?',  # International format with separators
        r'\b\d{10,15}\b',                                   # Long digit sequences
        r'(?:\+?\d{1,4}[-.\s]?)?\(?\d{1,4}\)?[-.\s]?\d{1,4}[-.\s]?\d{1,9}',  # Various national formats
        r'tel[:=]?[\s]*([+\d][\d\s\-\(\)\.]+)',            # tel: links
        r'phone[:=]?[\s]*([+\d][\d\s\-\(\)\.]+)',          # phone: links
        r'mobile[:=]?[\s]*([+\d][\d\s\-\(\)\.]+)',         # mobile: links
    ]
    
    for pattern in patterns:
        try:
            matches = re.findall(pattern, text, re.IGNORECASE | re.MULTILINE)
            if matches:
                for match in matches:
                    if isinstance(match, tuple):
                        match = match[0]
                    # Clean the number: keep only digits and +
                    cleaned = re.sub(r'[^\d+]', '', match)
                    if cleaned and len(cleaned) >= 10:
                        # Ensure it starts with + for international numbers
                        if not cleaned.startswith('+') and len(cleaned) >= 10:
                            # Common country code patterns
                            if cleaned.startswith('1') and len(cleaned) == 11:
                                cleaned = '+' + cleaned  # US/Canada
                            elif cleaned.startswith('44') and len(cleaned) >= 10:
                                cleaned = '+' + cleaned  # UK
                            elif cleaned.startswith('91') and len(cleaned) >= 10:
                                cleaned = '+' + cleaned  # India
                            elif cleaned.startswith('61') and len(cleaned) >= 10:
                                cleaned = '+' + cleaned  # Australia
                            else:
                                # Default: assume it's international without +
                                if len(cleaned) >= 10:
                                    cleaned = '+' + cleaned
                        return cleaned
        except Exception as e:
            logger.error(f"Error in pattern {pattern}: {e}")
            continue
    
    return None

def get_country_from_number(number):
    """Get country information from phone number using phonenumbers and pycountry"""
    try:
        # Clean number
        number = str(number).strip()
        
        # Parse phone number
        parsed = phonenumbers.parse(number)
        country_code = phonenumbers.region_code_for_number(parsed)
        
        if country_code:
            # Get country info
            country = pycountry.countries.get(alpha_2=country_code)
            if country:
                # Get flag emoji from country code
                flag = get_flag_emoji(country_code)
                return flag, country.name
        
        return ('🌍', 'Unknown')
    except Exception as e:
        logger.error(f"Error getting country from number {number}: {e}")
        return ('🌍', 'Unknown')

def get_flag_emoji(country_code):
    """Convert country code to flag emoji"""
    try:
        if not country_code or len(country_code) != 2:
            return '🏳️'
        
        # Regional Indicator Symbols
        offset = 127397
        return chr(ord(country_code[0].upper()) + offset) + chr(ord(country_code[1].upper()) + offset)
    except:
        return '🏳️'

def format_otp_message(number, message_text, timestamp, revenue_added=False, user_balance=0.0):
    """Format OTP message in the specified style"""
    country_flag, country_name = get_country_from_number(number)
    otp_code = extract_otp_from_message(message_text)
    
    # Create message with the exact format requested
    message = f"""🆕 Text Message Found!

└ {country_flag} Number: `{number}`
└ 🔐 OTP: `{otp_code if otp_code else 'Not Found'}`

ᐛ ʀᴇᴠᴇɴᴜᴇ ᴄʀᴇᴅɪᴛᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ!
෴ ᴄᴜʀʀᴇɴᴛ ʙᴀʟᴀɴᴄᴇ: ${user_balance:.3f}"""
    
    return message, otp_code, country_flag, country_name

def process_bulk_otp_messages():
    """Process OTP messages in bulk for efficiency"""
    try:
        # Get unprocessed messages
        messages = db.fetchall('''SELECT * FROM otp_messages 
                                  WHERE processed = 0 
                                  ORDER BY timestamp ASC LIMIT 100''')
        
        if not messages:
            return
        
        for msg in messages:
            try:
                # Check if number is assigned
                assignment = db.fetchone('''SELECT user_id FROM number_assignments 
                                            WHERE number = ? AND is_active = 1''', (msg['number'],))
                
                if assignment:
                    user_id = assignment['user_id']
                    
                    # Format message - use the new format
                    formatted_msg, otp_code, flag, country = format_otp_message(
                        msg['number'], msg['message'], 
                        datetime.strptime(msg['timestamp'], "%Y-%m-%d %H:%M:%S"),
                        False,  # Always show revenue credited
                        get_user_balance(user_id)
                    )
                    
                    # Add thanks button
                    markup = types.InlineKeyboardMarkup()
                    thanks_btn = types.InlineKeyboardButton("🌺 Thanks For Using Our Bot", callback_data="thanks")
                    markup.add(thanks_btn)
                    
                    # Send to user with Markdown parsing
                    try:
                        sent_msg = bot.send_message(user_id, formatted_msg, reply_markup=markup, parse_mode='Markdown')
                        
                        # Increment message count for the user
                        increment_user_message_count(user_id)
                        
                        # Add revenue if not added
                        if msg['revenue_added'] == 0:
                            revenue = get_setting('revenue_per_message') or 0.005
                            if revenue:
                                add_revenue_to_user(user_id, revenue)
                                
                                # Update assignment stats
                                db.execute('''UPDATE number_assignments 
                                              SET otp_count = otp_count + 1, 
                                                  total_revenue = total_revenue + ?,
                                                  last_otp_date = ?
                                              WHERE number = ? AND user_id = ?''',
                                           (revenue, msg['timestamp'], msg['number'], user_id))
                        
                        # Mark as processed and forwarded
                        db.execute('''UPDATE otp_messages 
                                      SET forwarded_to = ?, revenue_added = 1, processed = 1 
                                      WHERE id = ?''', (user_id, msg['id']))
                        
                        # Try to delete from group if possible
                        try:
                            if MONITORED_GROUP_ID and msg.get('message_id'):
                                bot.delete_message(MONITORED_GROUP_ID, msg['message_id'])
                        except Exception as e:
                            logger.error(f"Could not delete message from group: {e}")
                        
                    except Exception as e:
                        logger.error(f"Error sending to user {user_id}: {e}")
                        # Mark as processed even if failed
                        db.execute("UPDATE otp_messages SET processed = 1 WHERE id = ?", (msg['id'],))
                else:
                    # No assignment, mark as processed
                    db.execute("UPDATE otp_messages SET processed = 1 WHERE id = ?", (msg['id'],))
                    
            except Exception as e:
                logger.error(f"Error processing message {msg.get('id', 'unknown')}: {e}")
                db.execute("UPDATE otp_messages SET processed = 1 WHERE id = ?", (msg['id'],))
        
        # Delete old processed messages (older than 15 minutes)
        cutoff = (datetime.now() - timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S")
        db.execute("DELETE FROM otp_messages WHERE timestamp < ? AND processed = 1", (cutoff,))
        
    except Exception as e:
        logger.error(f"Error in bulk processing: {e}")

# Start OTP processing thread
def start_otp_processor():
    while True:
        try:
            process_bulk_otp_messages()
            time.sleep(5)  # Process every 5 seconds
        except Exception as e:
            logger.error(f"OTP processor error: {e}")
            time.sleep(10)

# Main menu
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    try:
        user_id = message.from_user.id
        
        # Check if bot is enabled
        if not is_admin(user_id) and get_setting('bot_enabled') == 0:
            bot.reply_to(message, "⚠️ Service Unavailable!\nThe bot has been temporarily disabled by the admin for maintenance purposes. Please try again after a while.")
            return
        
        # Check if user exists
        result = db.fetchone("SELECT is_banned, suspended_until FROM users WHERE user_id = ?", (user_id,))
        
        if result and result['is_banned'] == 1:
            bot.reply_to(message, "❌ You are banned from using this bot.")
            return
        
        if result and result['suspended_until']:
            suspend_until = datetime.strptime(result['suspended_until'], "%Y-%m-%d %H:%M:%S")
            if datetime.now() < suspend_until:
                remaining = (suspend_until - datetime.now()).seconds // 60
                bot.reply_to(message, f"⏳ You are suspended for {remaining} more minutes.")
                return
        
        # Add/update user
        if not result:
            join_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            db.execute('''INSERT INTO users (user_id, username, first_name, last_name, join_date, last_activity) 
                          VALUES (?, ?, ?, ?, ?, ?)''',
                       (user_id, message.from_user.username, message.from_user.first_name,
                        message.from_user.last_name, join_date, join_date))
        else:
            db.execute("UPDATE users SET last_activity = ? WHERE user_id = ?",
                       (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), user_id))
        
        # Check for custom start message
        start_msg_type = get_setting('start_message_type')
        start_msg = get_setting('start_message')
        
        if start_msg and start_msg_type == 'text':
            bot.send_message(message.chat.id, start_msg)
        elif start_msg and start_msg_type == 'photo':
            bot.send_photo(message.chat.id, start_msg, caption="Welcome!")
        elif start_msg and start_msg_type == 'video':
            bot.send_video(message.chat.id, start_msg, caption="Welcome!")
        elif start_msg and start_msg_type == 'document':
            bot.send_document(message.chat.id, start_msg, caption="Welcome!")
        else:
            # Default welcome message
            welcome_msg = """🤖 Welcome to FutureTech Bot!

💰 Earn money by receiving SMS/OTP
🌍 Numbers from multiple countries
⚡ Fast and reliable service

~ Main Channel: @FutureTech30
~ Public Otp Group: @FutureTechotp

/reset  - use this command for remove assigned number

Use the buttons below to navigate:"""
            bot.send_message(message.chat.id, welcome_msg)
        
        show_main_menu(message.chat.id)
    except Exception as e:
        logger.error(f"Error in send_welcome: {e}")
        bot.reply_to(message, "❌ An error occurred. Please try again.")

def show_main_menu(chat_id):
    try:
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        
        btn1 = types.KeyboardButton("📇 Get Number")
        btn2 = types.KeyboardButton("💰 Balance")
        btn3 = types.KeyboardButton("📊 Active Numbers")
        btn4 = types.KeyboardButton("⁉️ Support")
        
        markup.add(btn1, btn2, btn3, btn4)
        
        bot.send_message(chat_id, "Main Menu:", reply_markup=markup)
    except Exception as e:
        logger.error(f"Error showing main menu: {e}")

# Handle button clicks
@bot.message_handler(func=lambda message: message.text in ["📇 Get Number", "💰 Balance", "📊 Active Numbers", "⁉️ Support"])
def handle_buttons(message):
    try:
        user_id = message.from_user.id
        
        if message.text == "📇 Get Number":
            handle_get_number(message)
        elif message.text == "💰 Balance":
            handle_balance(message)
        elif message.text == "📊 Active Numbers":
            handle_active_numbers(message)
        elif message.text == "⁉️ Support":
            handle_support(message)
    except Exception as e:
        logger.error(f"Error handling button: {e}")

def handle_get_number(message):
    try:
        user_id = message.from_user.id
        
        # Check rate limit
        allowed, msg = check_rate_limit(user_id)
        if not allowed:
            bot.send_message(message.chat.id, msg)
            return
        
        # Get user's current active numbers and max limit
        current_active = db.fetchone('''SELECT COUNT(*) as count 
                                         FROM number_assignments 
                                         WHERE user_id = ? AND is_active = 1''', (user_id,))
        current_count = current_active['count'] if current_active else 0
        max_numbers = get_setting('max_user_numbers') or 50
        
        # Check if user has reached the limit
        if current_count >= max_numbers:
            bot.send_message(message.chat.id, f"❌ You can have maximum {max_numbers} active numbers. Please wait until some expire.")
            return
        
        # Get available countries with actual available numbers
        countries = db.fetchall('''SELECT DISTINCT c.name, c.flag, c.code,
                                          COUNT(n.id) as available_count
                                   FROM countries c
                                   JOIN numbers n ON c.code = n.country_code
                                   WHERE n.is_used = 0 
                                   AND NOT EXISTS (
                                       SELECT 1 FROM number_assignments na 
                                       WHERE na.number = n.number AND na.is_active = 1
                                   )
                                   GROUP BY c.code, c.name, c.flag
                                   HAVING available_count > 0
                                   ORDER BY c.name''')
        
        if not countries:
            bot.send_message(message.chat.id, "❌ No countries with available numbers found.")
            return
        
        # Create inline keyboard with countries
        markup = types.InlineKeyboardMarkup(row_width=2)
        
        for country in countries:
            btn = types.InlineKeyboardButton(
                f"{country['flag']} {country['name']} ({country['available_count']})",
                callback_data=f"getnum_{country['code']}"
            )
            markup.add(btn)
        
        msg = f"🌍 Select a country:\n\n"
        msg += f"📊 Your active numbers: {current_count}/{max_numbers}\n"
        msg += f"📱 Available numbers shown in parentheses\n"
        msg += f"🎯 Batch size: {get_setting('batch_size') or 1} number(s) per request\n\n"
        msg += f"💡 Tip: Use /reset to remove your assigned numbers"
        
        bot.send_message(message.chat.id, msg, reply_markup=markup)
    except Exception as e:
        logger.error(f"Error in handle_get_number: {e}")
        bot.reply_to(message, "❌ An error occurred. Please try again.")

def handle_balance(message):
    try:
        user_id = message.from_user.id
        balance = get_user_balance(user_id)
        
        markup = types.InlineKeyboardMarkup()
        
        # Get withdrawal settings
        withdrawal_enabled = get_setting('withdrawal_enabled')
        min_withdrawal = get_setting('min_withdrawal') or 3.0
        
        if withdrawal_enabled == 1 and balance >= min_withdrawal:
            withdraw_btn = types.InlineKeyboardButton("💸 Withdraw", callback_data="withdraw_request")
            markup.add(withdraw_btn)
        
        # Get earnings stats
        today = datetime.now().strftime("%Y-%m-%d")
        today_stats = db.fetchone('''SELECT messages_received, revenue_earned 
                                     FROM user_stats WHERE user_id = ? AND date = ?''', (user_id, today))
        
        total_stats = db.fetchone('''SELECT total_earned, total_withdrawn FROM users WHERE user_id = ?''', (user_id,))
        
        # Get accurate message count
        messages_today = today_stats['messages_received'] if today_stats and today_stats.get('messages_received') else 0
        revenue_today = today_stats['revenue_earned'] if today_stats and today_stats.get('revenue_earned') else 0.0
        
        msg = f"""💰 Your Balance:

💵 Available: ${balance:.3f}
📊 Today's Earnings: ${revenue_today:.3f} ({messages_today} messages)
📈 Total Earned: ${total_stats['total_earned'] if total_stats and total_stats.get('total_earned') else 0:.3f}
💸 Total Withdrawn: ${total_stats['total_withdrawn'] if total_stats and total_stats.get('total_withdrawn') else 0:.3f}

💡 Minimum Withdrawal: ${min_withdrawal:.2f}
"""
        
        if markup.keyboard:
            bot.send_message(message.chat.id, msg, reply_markup=markup)
        else:
            bot.send_message(message.chat.id, msg)
    except Exception as e:
        logger.error(f"Error in handle_balance: {e}")
        bot.reply_to(message, "❌ An error occurred. Please try again.")

def handle_active_numbers(message):
    try:
        user_id = message.from_user.id
        
        # Get total count
        total_count = db.fetchone('''SELECT COUNT(*) as count 
                                     FROM number_assignments 
                                     WHERE user_id = ? AND is_active = 1''', (user_id,))
        total = total_count['count'] if total_count else 0
        
        if total == 0:
            markup = types.InlineKeyboardMarkup()
            otp_group_btn = types.InlineKeyboardButton("↗️ OTP GROUP", url=OTP_GROUP_LINK)
            markup.add(otp_group_btn)
            bot.send_message(message.chat.id, "📭 You don't have any active numbers.", reply_markup=markup)
            return
        
        # Show first page
        show_active_numbers_page(message.chat.id, user_id, 0)
        
    except Exception as e:
        logger.error(f"Error in handle_active_numbers: {e}")
        bot.reply_to(message, "❌ An error occurred. Please try again.")

def show_active_numbers_page(chat_id, user_id, page=0):
    try:
        limit = 10
        offset = page * limit
        
        # Get user's active number assignments
        assignments = db.fetchall('''SELECT na.number, na.assigned_date, na.otp_count, na.total_revenue,
                                            n.country, n.country_flag
                                     FROM number_assignments na
                                     LEFT JOIN numbers n ON na.number = n.number
                                     WHERE na.user_id = ? AND na.is_active = 1
                                     LIMIT ? OFFSET ?''', (user_id, limit, offset))
        
        if not assignments:
            bot.send_message(chat_id, "📭 You don't have any active numbers.")
            return
        
        # Get total count for pagination
        total_count = db.fetchone('''SELECT COUNT(*) as count 
                                     FROM number_assignments 
                                     WHERE user_id = ? AND is_active = 1''', (user_id,))
        total = total_count['count'] if total_count else 0
        
        msg = f"📊 Your Active Numbers (Page {page + 1}):\n\n"
        for i, assign in enumerate(assignments, offset + 1):
            msg += f"{i}. {assign['country_flag']} `{assign['number']}`\n"
            msg += f"   📅 Assigned: {assign['assigned_date']}\n"
            msg += f"   📨 OTPs: {assign['otp_count']}\n"
            msg += f"   💰 Revenue: ${assign['total_revenue']:.3f}\n\n"
        
        markup = types.InlineKeyboardMarkup(row_width=3)
        
        # OTP Group button
        otp_group_btn = types.InlineKeyboardButton("↗️ OTP GROUP", url=OTP_GROUP_LINK)
        markup.add(otp_group_btn)
        
        # Refresh button
        refresh_btn = types.InlineKeyboardButton("🔄 Refresh Database", callback_data="refresh_database")
        markup.add(refresh_btn)
        
        # Pagination buttons
        pagination_btns = []
        if page > 0:
            prev_btn = types.InlineKeyboardButton("⬅️ Previous", callback_data=f"active_page_{page-1}")
            pagination_btns.append(prev_btn)
        
        if offset + limit < total:
            next_btn = types.InlineKeyboardButton("Next ➡️", callback_data=f"active_page_{page+1}")
            pagination_btns.append(next_btn)
        
        if pagination_btns:
            markup.row(*pagination_btns)
        
        bot.send_message(chat_id, msg, reply_markup=markup, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in show_active_numbers_page: {e}")

def handle_support(message):
    try:
        user_id = message.from_user.id
        
        msg = """⁉️ Support Center

Please describe your issue or question. You can send:
• Text message
• Photo with caption
• Video with caption
• Document with caption

Our admin team will respond as soon as possible.

Type /cancel to cancel."""
        
        bot.send_message(message.chat.id, msg)
        bot.register_next_step_handler(message, process_support_message)
    except Exception as e:
        logger.error(f"Error in handle_support: {e}")

def process_support_message(message):
    try:
        user_id = message.from_user.id
        
        if message.text == '/cancel':
            bot.send_message(message.chat.id, "Support request cancelled.")
            show_main_menu(message.chat.id)
            return
        
        # Create support ticket
        ticket_id = create_support_ticket(user_id, message)
        
        # Forward to admin
        for admin_id in ADMIN_IDS:
            try:
                forward_msg = f"🆘 Support Ticket #{ticket_id}\n\n"
                forward_msg += f"User: @{message.from_user.username or 'N/A'} (ID: {user_id})\n"
                forward_msg += f"Name: {message.from_user.first_name} {message.from_user.last_name or ''}\n\n"
                
                if message.text:
                    forward_msg += f"Message: {message.text}"
                    bot.send_message(admin_id, forward_msg)
                elif message.photo:
                    forward_msg += f"Caption: {message.caption or 'No caption'}"
                    bot.send_photo(admin_id, message.photo[-1].file_id, caption=forward_msg)
                elif message.video:
                    forward_msg += f"Caption: {message.caption or 'No caption'}"
                    bot.send_video(admin_id, message.video.file_id, caption=forward_msg)
                elif message.document:
                    forward_msg += f"Caption: {message.caption or 'No caption'}"
                    bot.send_document(admin_id, message.document.file_id, caption=forward_msg)
            except Exception as e:
                logger.error(f"Error forwarding to admin {admin_id}: {e}")
        
        bot.send_message(message.chat.id, "✅ Your support request has been submitted. We'll respond soon.")
        show_main_menu(message.chat.id)
    except Exception as e:
        logger.error(f"Error in process_support_message: {e}")

def create_support_ticket(user_id, message):
    try:
        ticket_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        if message.text:
            msg_type = 'text'
            content = message.text
            file_id = None
        elif message.photo:
            msg_type = 'photo'
            content = message.caption or ''
            file_id = message.photo[-1].file_id
        elif message.video:
            msg_type = 'video'
            content = message.caption or ''
            file_id = message.video.file_id
        elif message.document:
            msg_type = 'document'
            content = message.caption or ''
            file_id = message.document.file_id
        else:
            msg_type = 'unknown'
            content = ''
            file_id = None
        
        db.execute('''INSERT INTO support_tickets 
                      (user_id, message, message_type, file_id, created_date)
                      VALUES (?, ?, ?, ?, ?)''',
                   (user_id, content, msg_type, file_id, ticket_date))
        
        result = db.fetchone("SELECT last_insert_rowid() as id")
        return result['id'] if result else 0
    except:
        return 0

# Callback query handler
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    try:
        user_id = call.from_user.id
        chat_id = call.message.chat.id
        message_id = call.message.message_id
        
        logger.info(f"Callback received: {call.data} from user {user_id}")
        
        if call.data.startswith('getnum_'):
            country_code = call.data.split('_')[1]
            process_get_numbers(call, country_code)
        
        elif call.data == 'refresh_balance':
            bot.answer_callback_query(call.id, "Refreshing balance...")
            handle_balance(call.message)
        
        elif call.data == 'withdraw_request':
            process_withdraw_request(call)
        
        elif call.data == 'refresh_database':
            refresh_user_database(call)
        
        elif call.data.startswith('approve_withdraw_'):
            withdraw_id = int(call.data.split('_')[2])
            approve_withdrawal(call, withdraw_id)
        
        elif call.data.startswith('reject_withdraw_'):
            withdraw_id = int(call.data.split('_')[2])
            reject_withdrawal(call, withdraw_id)
        
        elif call.data == 'admin_panel':
            if is_admin(user_id):
                show_admin_panel(chat_id)
            else:
                bot.answer_callback_query(call.id, "❌ Access denied!")
        
        elif call.data == 'admin_status':
            if is_admin(user_id):
                show_admin_status(chat_id)
            else:
                bot.answer_callback_query(call.id, "❌ Access denied!")
        
        elif call.data == 'admin_settings':
            if is_admin(user_id):
                show_admin_settings(chat_id)
            else:
                bot.answer_callback_query(call.id, "❌ Access denied!")
        
        elif call.data == 'admin_broadcast':
            if is_admin(user_id):
                start_broadcast(chat_id)
            else:
                bot.answer_callback_query(call.id, "❌ Access denied!")
        
        elif call.data == 'admin_users':
            if is_admin(user_id):
                show_user_management(chat_id)
            else:
                bot.answer_callback_query(call.id, "❌ Access denied!")
        
        elif call.data == 'admin_numbers':
            if is_admin(user_id):
                show_number_management(chat_id)
            else:
                bot.answer_callback_query(call.id, "❌ Access denied!")
        
        elif call.data == 'admin_withdrawals':
            if is_admin(user_id):
                show_withdrawal_management(chat_id, 0)
            else:
                bot.answer_callback_query(call.id, "❌ Access denied!")
        
        elif call.data == 'admin_tickets':
            if is_admin(user_id):
                show_ticket_management(chat_id, 0)
            else:
                bot.answer_callback_query(call.id, "❌ Access denied!")
        
        elif call.data == 'admin_export':
            if is_admin(user_id):
                export_stats(chat_id)
            else:
                bot.answer_callback_query(call.id, "❌ Access denied!")
        
        elif call.data == 'admin_reset':
            if is_admin(user_id):
                show_admin_reset_system(chat_id)
            else:
                bot.answer_callback_query(call.id, "❌ Access denied!")
        
        elif call.data == 'admin_numbers_report':
            if is_admin(user_id):
                generate_numbers_report(chat_id)
            else:
                bot.answer_callback_query(call.id, "❌ Access denied!")
        
        elif call.data == 'reset_delete_used':
            if is_admin(user_id):
                reset_delete_used_numbers(chat_id)
            else:
                bot.answer_callback_query(call.id, "❌ Access denied!")
        
        elif call.data == 'reset_confirm_delete_used':
            if is_admin(user_id):
                reset_confirm_delete_used(call)
            else:
                bot.answer_callback_query(call.id, "❌ Access denied!")
        
        elif call.data.startswith('reset_country_'):
            country_code = call.data.split('_')[2]
            if is_admin(user_id):
                show_reset_country_confirmation(call, country_code)
            else:
                bot.answer_callback_query(call.id, "❌ Access denied!")
        
        elif call.data.startswith('confirm_reset_country_'):
            country_code = call.data.split('_')[3]
            if is_admin(user_id):
                reset_all_assignments_for_country(call, country_code)
            else:
                bot.answer_callback_query(call.id, "❌ Access denied!")
        
        elif call.data == 'reset_all_assignments':
            if is_admin(user_id):
                show_reset_country_selection(chat_id)
            else:
                bot.answer_callback_query(call.id, "❌ Access denied!")
        
        elif call.data.startswith('set_batch_'):
            batch_size = int(call.data.split('_')[2])
            update_setting('batch_size', batch_size)
            bot.answer_callback_query(call.id, f"✅ Batch size set to {batch_size}")
            show_admin_settings(chat_id)
        
        elif call.data.startswith('set_revenue_'):
            revenue = float(call.data.split('_')[2])
            update_setting('revenue_per_message', revenue)
            bot.answer_callback_query(call.id, f"✅ Revenue per message set to ${revenue}")
            show_admin_settings(chat_id)
        
        elif call.data.startswith('set_min_withdraw_'):
            amount = float(call.data.split('_')[3])
            update_setting('min_withdrawal', amount)
            bot.answer_callback_query(call.id, f"✅ Minimum withdrawal set to ${amount}")
            show_admin_settings(chat_id)
        
        elif call.data.startswith('set_max_num_'):
            max_num = int(call.data.split('_')[3])
            update_setting('max_user_numbers', max_num)
            bot.answer_callback_query(call.id, f"✅ Maximum numbers per user set to {max_num}")
            show_admin_settings(chat_id)
        
        elif call.data == 'toggle_withdrawal':
            current = get_setting('withdrawal_enabled')
            new_status = 0 if current == 1 else 1
            update_setting('withdrawal_enabled', new_status)
            status_text = "enabled" if new_status == 1 else "disabled"
            bot.answer_callback_query(call.id, f"✅ Withdrawal {status_text}")
            show_admin_settings(chat_id)
        
        elif call.data == 'toggle_bot':
            current = get_setting('bot_enabled')
            new_status = 0 if current == 1 else 1
            update_setting('bot_enabled', new_status)
            status_text = "enabled" if new_status == 1 else "disabled"
            bot.answer_callback_query(call.id, f"✅ Bot {status_text}")
            show_admin_settings(chat_id)
        
        elif call.data == 'add_numbers':
            if is_admin(user_id):
                ask_for_filename(chat_id)
            else:
                bot.answer_callback_query(call.id, "❌ Access denied!")
        
        elif call.data == 'view_numbers':
            if is_admin(user_id):
                show_number_stats(chat_id)
            else:
                bot.answer_callback_query(call.id, "❌ Access denied!")
        
        elif call.data == 'delete_country_list':
            if is_admin(user_id):
                show_delete_country_list(chat_id)
            else:
                bot.answer_callback_query(call.id, "❌ Access denied!")
        
        elif call.data.startswith('delete_country_'):
            country_code = call.data.split('_')[2]
            if is_admin(user_id):
                delete_country(chat_id, country_code)
            else:
                bot.answer_callback_query(call.id, "❌ Access denied!")
        
        elif call.data == 'skip_duplicates':
            if is_admin(user_id):
                process_numbers_with_skip(call)
            else:
                bot.answer_callback_query(call.id, "❌ Access denied!")
        
        elif call.data == 'overwrite_duplicates':
            if is_admin(user_id):
                process_numbers_with_overwrite(call)
            else:
                bot.answer_callback_query(call.id, "❌ Access denied!")
        
        elif call.data.startswith('view_withdraw_'):
            withdraw_id = int(call.data.split('_')[2])
            if is_admin(user_id):
                show_withdrawal_details(chat_id, withdraw_id)
            else:
                bot.answer_callback_query(call.id, "❌ Access denied!")
        
        elif call.data.startswith('view_ticket_'):
            ticket_id = int(call.data.split('_')[2])
            if is_admin(user_id):
                show_ticket_details(chat_id, ticket_id)
            else:
                bot.answer_callback_query(call.id, "❌ Access denied!")
        
        elif call.data.startswith('reply_ticket_'):
            ticket_id = int(call.data.split('_')[2])
            if is_admin(user_id):
                start_ticket_reply(chat_id, ticket_id)
            else:
                bot.answer_callback_query(call.id, "❌ Access denied!")
        
        elif call.data.startswith('active_page_'):
            page = int(call.data.split('_')[2])
            show_active_numbers_page(chat_id, user_id, page)
        
        elif call.data.startswith('tickets_page_'):
            page = int(call.data.split('_')[2])
            if is_admin(user_id):
                show_ticket_management(chat_id, page)
            else:
                bot.answer_callback_query(call.id, "❌ Access denied!")
        
        elif call.data.startswith('withdraw_page_'):
            page = int(call.data.split('_')[2])
            if is_admin(user_id):
                show_withdrawal_management(chat_id, page)
            else:
                bot.answer_callback_query(call.id, "❌ Access denied!")
        
        elif call.data == 'thanks':
            bot.answer_callback_query(call.id, "🌺 Thank you for using our service!")
        
        else:
            bot.answer_callback_query(call.id, "Unknown command")
    
    except Exception as e:
        logger.error(f"Callback error: {e}")
        logger.error(traceback.format_exc())
        try:
            bot.answer_callback_query(call.id, "❌ An error occurred!")
        except:
            pass

def process_get_numbers(call, country_code):
    try:
        user_id = call.from_user.id
        
        # Get user's current active numbers count
        current_active = db.fetchone('''SELECT COUNT(*) as count 
                                         FROM number_assignments 
                                         WHERE user_id = ? AND is_active = 1''', (user_id,))
        current_count = current_active['count'] if current_active else 0
        
        # Get max limit from settings
        max_numbers = get_setting('max_user_numbers') or 50
        
        # Check if user has reached the limit
        if current_count >= max_numbers:
            bot.answer_callback_query(call.id, f"❌ You can have maximum {max_numbers} active numbers. Please wait until some expire.")
            return
        
        # Get batch size
        batch_size = get_setting('batch_size') or 1
        
        # Calculate how many more numbers user can get
        remaining = max_numbers - current_count
        if batch_size > remaining:
            batch_size = remaining
        
        # Get available numbers for this country that are NOT currently assigned to anyone
        numbers = db.fetchall('''SELECT number, country, country_flag 
                                 FROM numbers 
                                 WHERE country_code = ? AND is_used = 0 
                                 AND NOT EXISTS (
                                     SELECT 1 FROM number_assignments na 
                                     WHERE na.number = numbers.number AND na.is_active = 1
                                 )
                                 LIMIT ?''', (country_code, batch_size))
        
        if not numbers:
            bot.answer_callback_query(call.id, "❌ No numbers available for this country!")
            return
        
        # Assign numbers to user
        assigned_numbers = []
        for num in numbers:
            assigned_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # Mark as used
            db.execute('''UPDATE numbers SET is_used = 1, used_by = ?, use_date = ? 
                          WHERE number = ?''', (user_id, assigned_date, num['number']))
            
            # Create assignment - FIXED: Use INSERT OR IGNORE to avoid duplicates
            db.execute('''INSERT OR IGNORE INTO number_assignments (number, user_id, assigned_date)
                          VALUES (?, ?, ?)''', (num['number'], user_id, assigned_date))
            
            assigned_numbers.append(num)
        
        if not assigned_numbers:
            bot.answer_callback_query(call.id, "❌ Could not assign numbers. Please try again.")
            return
        
        # Create message with numbers
        msg = f"✅ Here are your {len(assigned_numbers)} number(s):\n\n"
        for i, num in enumerate(assigned_numbers, 1):
            msg += f"{i}. {num['country_flag']} `{num['number']}`\n"
        
        msg += f"\n📨 OTPs will be forwarded automatically when received.\n"
        msg += f"📊 Active numbers: {current_count + len(assigned_numbers)}/{max_numbers}\n"
        msg += f"\n*Tap on a number to copy it.*"
        
        # Create inline keyboard
        markup = types.InlineKeyboardMarkup()
        otp_group_btn = types.InlineKeyboardButton("↗️ OTP GROUP", url=OTP_GROUP_LINK)
        refresh_btn = types.InlineKeyboardButton("🔄 Refresh Database", callback_data="refresh_database")
        markup.add(otp_group_btn)
        markup.add(refresh_btn)
        
        bot.edit_message_text(msg, call.message.chat.id, call.message.message_id, 
                              reply_markup=markup, parse_mode='Markdown')
        
        # Update country stats
        db.execute('''UPDATE countries SET used_numbers = used_numbers + ? 
                      WHERE code = ?''', (len(assigned_numbers), country_code))
        
        # Check if numbers have run out for this country
        country_stats = db.fetchone('''SELECT c.name, c.total_numbers, c.used_numbers FROM countries c WHERE code = ?''', (country_code,))
        if country_stats and country_stats['total_numbers'] == country_stats['used_numbers']:
            for admin_id in ADMIN_IDS:
                try:
                    bot.send_message(admin_id, f"⚠️ Numbers for {country_stats['name']} have run out! Please add more numbers.")
                except Exception as e:
                    logger.error(f"Error notifying admin: {e}")
        
        # Update user stats
        today = datetime.now().strftime("%Y-%m-%d")
        db.execute('''INSERT OR IGNORE INTO user_stats (user_id, date) VALUES (?, ?)''', (user_id, today))
        db.execute('''UPDATE user_stats SET numbers_taken = numbers_taken + ? 
                      WHERE user_id = ? AND date = ?''', (len(assigned_numbers), user_id, today))
        
        bot.answer_callback_query(call.id, "✅ Numbers assigned successfully!")
    except Exception as e:
        logger.error(f"Error in process_get_numbers: {e}")
        bot.answer_callback_query(call.id, "❌ An error occurred!")

def process_withdraw_request(call):
    try:
        user_id = call.from_user.id
        balance = get_user_balance(user_id)
        min_withdrawal = get_setting('min_withdrawal') or 3.0
        
        if balance < min_withdrawal:
            bot.answer_callback_query(call.id, f"❌ Minimum withdrawal is ${min_withdrawal:.2f}")
            return
        
        msg = f"""💸 Withdrawal Request

Your balance: ${balance:.3f}
Minimum withdrawal: ${min_withdrawal:.2f}

Please send your withdrawal details in this format:

Network: USDT (BSC Network)
Address: Your Binance Pay ID or Wallet Address
Amount: ${min_withdrawal:.2f} or more

Example:
Network: USDT (BSC)
Address: 0x742d35Cc6634C0532925a3b844Bc9e76E3f00000
Amount: {min_withdrawal:.2f}

Type /cancel to cancel."""
        
        bot.send_message(call.message.chat.id, msg)
        bot.register_next_step_handler(call.message, process_withdrawal_details)
        bot.answer_callback_query(call.id, "Please enter withdrawal details")
    except Exception as e:
        logger.error(f"Error in process_withdraw_request: {e}")
        bot.answer_callback_query(call.id, "❌ An error occurred!")

def process_withdrawal_details(message):
    try:
        user_id = message.from_user.id
        
        if message.text == '/cancel':
            bot.send_message(message.chat.id, "Withdrawal cancelled.")
            show_main_menu(message.chat.id)
            return
        
        # Parse withdrawal details
        text = message.text
        network = None
        address = None
        amount = None
        
        # Extract network
        network_match = re.search(r'Network:\s*(.+)', text, re.IGNORECASE)
        if network_match:
            network = network_match.group(1).strip()
        
        # Extract address
        address_match = re.search(r'Address:\s*(.+)', text, re.IGNORECASE)
        if address_match:
            address = address_match.group(1).strip()
        
        # Extract amount
        amount_match = re.search(r'Amount:\s*\$?(\d+\.?\d*)', text, re.IGNORECASE)
        if amount_match:
            amount = float(amount_match.group(1))
        
        # Validate
        if not all([network, address, amount]):
            bot.send_message(message.chat.id, "❌ Invalid format. Please check the example and try again.")
            return
        
        balance = get_user_balance(user_id)
        min_withdrawal = get_setting('min_withdrawal') or 3.0
        
        if amount < min_withdrawal:
            bot.send_message(message.chat.id, f"❌ Amount must be at least ${min_withdrawal:.2f}")
            return
        
        if amount > balance:
            bot.send_message(message.chat.id, "❌ Insufficient balance")
            return
        
        # Create withdrawal request
        request_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        db.execute('''INSERT INTO withdrawals 
                      (user_id, amount, address, network, request_date)
                      VALUES (?, ?, ?, ?, ?)''',
                   (user_id, amount, address, network, request_date))
        
        withdrawal_id = db.fetchone("SELECT last_insert_rowid() as id")
        withdrawal_id = withdrawal_id['id'] if withdrawal_id else 0
        
        # Send to withdrawal log channel
        log_msg = f"""🔄 New Withdrawal Request #{withdrawal_id}

👤 User: @{message.from_user.username or 'N/A'} (ID: {user_id})
💰 Amount: ${amount:.3f}
🌐 Network: {network}
📍 Address: {address}
📅 Date: {request_date}

Balance: ${balance:.3f}"""
        
        # Create approve/reject buttons
        markup = types.InlineKeyboardMarkup()
        approve_btn = types.InlineKeyboardButton("✅ Approve", callback_data=f"approve_withdraw_{withdrawal_id}")
        reject_btn = types.InlineKeyboardButton("❌ Reject", callback_data=f"reject_withdraw_{withdrawal_id}")
        markup.add(approve_btn, reject_btn)
        
        # Send to all admins
        for admin_id in ADMIN_IDS:
            try:
                bot.send_message(admin_id, log_msg, reply_markup=markup)
            except Exception as e:
                logger.error(f"Error sending to admin {admin_id}: {e}")
        
        # Send to withdrawal log channel
        try:
            bot.send_message(WITHDRAW_LOG_CHANNEL, log_msg, reply_markup=markup)
        except Exception as e:
            logger.error(f"Error sending to withdrawal log channel: {e}")
        
        bot.send_message(message.chat.id, f"✅ Withdrawal request #{withdrawal_id} submitted for ${amount:.3f}. Waiting for admin approval.")
        show_main_menu(message.chat.id)
    except Exception as e:
        logger.error(f"Error in process_withdrawal_details: {e}")
        bot.send_message(message.chat.id, "❌ An error occurred. Please try again.")

def refresh_user_database(call):
    try:
        user_id = call.from_user.id
        
        # Get user's assigned numbers
        assignments = db.fetchall('''SELECT number FROM number_assignments 
                                     WHERE user_id = ? AND is_active = 1''', (user_id,))
        
        if not assignments:
            bot.answer_callback_query(call.id, "❌ No active numbers found!")
            return
        
        found_new = False
        for assign in assignments:
            number = assign['number']
            
            # Check database for unprocessed OTPs
            unprocessed_otps = db.fetchall('''SELECT * FROM otp_messages 
                                              WHERE number = ? AND processed = 0
                                              ORDER BY timestamp DESC''', (number,))
            
            if unprocessed_otps:
                found_new = True
                for otp in unprocessed_otps:
                    # Check if already forwarded
                    if otp['forwarded_to'] == user_id:
                        continue
                    
                    try:
                        # Forward to user
                        formatted_msg, otp_code, flag, country = format_otp_message(
                            otp['number'], otp['message'], 
                            datetime.strptime(otp['timestamp'], "%Y-%m-%d %H:%M:%S"),
                            False,
                            get_user_balance(user_id)
                        )
                        
                        # Add thanks button
                        markup = types.InlineKeyboardMarkup()
                        thanks_btn = types.InlineKeyboardButton("🌺 Thanks For Using Our Bot", callback_data="thanks")
                        markup.add(thanks_btn)
                        
                        bot.send_message(user_id, formatted_msg, reply_markup=markup, parse_mode='Markdown')
                        
                        # Increment message count
                        increment_user_message_count(user_id)
                        
                        # Mark as forwarded and processed
                        db.execute('''UPDATE otp_messages 
                                      SET forwarded_to = ?, processed = 1 
                                      WHERE id = ?''', (user_id, otp['id']))
                        
                        # Add revenue if not added
                        if otp['revenue_added'] == 0:
                            revenue = get_setting('revenue_per_message') or 0.005
                            if revenue:
                                add_revenue_to_user(user_id, revenue)
                                db.execute("UPDATE otp_messages SET revenue_added = 1 WHERE id = ?", (otp['id'],))
                                
                                # Update assignment revenue
                                db.execute('''UPDATE number_assignments 
                                              SET otp_count = otp_count + 1, 
                                                  total_revenue = total_revenue + ?
                                              WHERE number = ? AND user_id = ?''',
                                           (revenue, number, user_id))
                        
                        # Delete from database after forwarding
                        db.execute("DELETE FROM otp_messages WHERE id = ?", (otp['id'],))
                        
                    except Exception as e:
                        logger.error(f"Error forwarding OTP: {e}")
        
        if found_new:
            bot.answer_callback_query(call.id, "✅ Found and forwarded new messages!")
        else:
            bot.answer_callback_query(call.id, "❌ No new messages found.")
    except Exception as e:
        logger.error(f"Error in refresh_user_database: {e}")
        bot.answer_callback_query(call.id, "❌ An error occurred!")

def approve_withdrawal(call, withdraw_id):
    try:
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "❌ Access denied!")
            return
        
        withdrawal = db.fetchone("SELECT * FROM withdrawals WHERE id = ?", (withdraw_id,))
        if not withdrawal:
            bot.answer_callback_query(call.id, "❌ Withdrawal not found!")
            return
        
        if withdrawal['status'] != 'pending':
            bot.answer_callback_query(call.id, f"❌ Already {withdrawal['status']}!")
            return
        
        # Check balance before deducting
        user_balance = get_user_balance(withdrawal['user_id'])
        if user_balance < withdrawal['amount']:
            bot.answer_callback_query(call.id, "❌ User doesn't have enough balance!")
            return
        
        process_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Deduct balance
        update_user_balance(withdrawal['user_id'], -withdrawal['amount'])
        
        # Update withdrawal status
        db.execute('''UPDATE withdrawals SET status = 'approved', 
                      process_date = ?, admin_id = ?
                      WHERE id = ?''',
                   (process_date, call.from_user.id, withdraw_id))
        
        # Update user's total withdrawn
        db.execute('''UPDATE users SET total_withdrawn = total_withdrawn + ? 
                      WHERE user_id = ?''',
                   (withdrawal['amount'], withdrawal['user_id']))
        
        # Notify user
        user_msg = f"""✅ Withdrawal Approved!

💰 Amount: ${withdrawal['amount']:.3f}
🌐 Network: {withdrawal['network']}
📍 Address: {withdrawal['address']}
📅 Processed: {process_date}

Your withdrawal has been processed successfully."""
        
        try:
            bot.send_message(withdrawal['user_id'], user_msg)
        except:
            pass
        
        # Send to withdrawal log channel
        try:
            log_msg = f"""✅ Withdrawal Approved #{withdraw_id}

👤 User: {withdrawal['user_id']}
💰 Amount: ${withdrawal['amount']:.3f}
🌐 Network: {withdrawal['network']}
📍 Address: {withdrawal['address']}
📅 Processed: {process_date}
👨‍💼 Admin: {call.from_user.id}"""
            bot.send_message(WITHDRAW_LOG_CHANNEL, log_msg)
        except Exception as e:
            logger.error(f"Error sending to withdrawal log channel: {e}")
        
        bot.answer_callback_query(call.id, "✅ Withdrawal approved!")
    except Exception as e:
        logger.error(f"Error in approve_withdrawal: {e}")
        bot.answer_callback_query(call.id, "❌ An error occurred!")

def reject_withdrawal(call, withdraw_id):
    try:
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "❌ Access denied!")
            return
        
        withdrawal = db.fetchone("SELECT * FROM withdrawals WHERE id = ?", (withdraw_id,))
        if not withdrawal:
            bot.answer_callback_query(call.id, "❌ Withdrawal not found!")
            return
        
        if withdrawal['status'] != 'pending':
            bot.answer_callback_query(call.id, f"❌ Already {withdrawal['status']}!")
            return
        
        process_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Update withdrawal status
        db.execute('''UPDATE withdrawals SET status = 'rejected', 
                      process_date = ?, admin_id = ?
                      WHERE id = ?''',
                   (process_date, call.from_user.id, withdraw_id))
        
        # Notify user
        user_msg = f"""❌ Withdrawal Rejected!

💰 Amount: ${withdrawal['amount']:.3f}
🌐 Network: {withdrawal['network']}
📍 Address: {withdrawal['address']}
📅 Processed: {process_date}

Your withdrawal has been rejected. Contact support for more information."""
        
        try:
            bot.send_message(withdrawal['user_id'], user_msg)
        except:
            pass
        
        # Send to withdrawal log channel
        try:
            log_msg = f"""❌ Withdrawal Rejected #{withdraw_id}

👤 User: {withdrawal['user_id']}
💰 Amount: ${withdrawal['amount']:.3f}
🌐 Network: {withdrawal['network']}
📍 Address: {withdrawal['address']}
📅 Processed: {process_date}
👨‍💼 Admin: {call.from_user.id}"""
            bot.send_message(WITHDRAW_LOG_CHANNEL, log_msg)
        except Exception as e:
            logger.error(f"Error sending to withdrawal log channel: {e}")
        
        bot.answer_callback_query(call.id, "❌ Withdrawal rejected!")
    except Exception as e:
        logger.error(f"Error in reject_withdrawal: {e}")
        bot.answer_callback_query(call.id, "❌ An error occurred!")

# Admin panel functions
def show_admin_panel(chat_id):
    try:
        markup = types.InlineKeyboardMarkup(row_width=2)
        
        btn1 = types.InlineKeyboardButton("📊 Status", callback_data="admin_status")
        btn2 = types.InlineKeyboardButton("⚙️ Settings", callback_data="admin_settings")
        btn3 = types.InlineKeyboardButton("📤 Broadcast", callback_data="admin_broadcast")
        btn4 = types.InlineKeyboardButton("👤 User Management", callback_data="admin_users")
        btn5 = types.InlineKeyboardButton("📱 Number Management", callback_data="admin_numbers")
        btn6 = types.InlineKeyboardButton("💳 Withdrawals", callback_data="admin_withdrawals")
        btn7 = types.InlineKeyboardButton("🆘 Support Tickets", callback_data="admin_tickets")
        btn8 = types.InlineKeyboardButton("📈 Stats Export", callback_data="admin_export")
        btn9 = types.InlineKeyboardButton("🗑️ Reset System", callback_data="admin_reset")
        btn10 = types.InlineKeyboardButton("📄 Numbers Report", callback_data="admin_numbers_report")
        
        markup.add(btn1, btn2, btn3, btn4, btn5, btn6, btn7, btn8, btn9, btn10)
        
        bot.send_message(chat_id, "🔧 Admin Panel\n\nSelect an option:", reply_markup=markup)
    except Exception as e:
        logger.error(f"Error in show_admin_panel: {e}")

def show_admin_status(chat_id):
    try:
        # Get total users
        total_users_result = db.fetchone("SELECT COUNT(*) as count FROM users WHERE is_banned = 0")
        total_users = total_users_result['count'] if total_users_result else 0
        
        # Get active users (last 24 hours)
        yesterday = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        active_users_result = db.fetchone('''SELECT COUNT(DISTINCT user_id) as count FROM users 
                                             WHERE last_activity >= ? AND is_banned = 0''', (yesterday,))
        active_users = active_users_result['count'] if active_users_result else 0
        
        # Get banned users
        banned_users_result = db.fetchone("SELECT COUNT(*) as count FROM users WHERE is_banned = 1")
        banned_users = banned_users_result['count'] if banned_users_result else 0
        
        # Get total balance
        total_balance_result = db.fetchone("SELECT SUM(balance) as total FROM users")
        total_balance = total_balance_result['total'] if total_balance_result and total_balance_result['total'] else 0
        
        # Get accurate country stats
        country_stats = db.fetchall('''SELECT c.name, c.flag, 
                                        COALESCE(COUNT(n.id), 0) as total_numbers,
                                        COALESCE(SUM(CASE WHEN n.is_used = 1 THEN 1 ELSE 0 END), 0) as used_numbers
                                        FROM countries c
                                        LEFT JOIN numbers n ON c.code = n.country_code
                                        GROUP BY c.code, c.name, c.flag
                                        ORDER BY c.name''')
        
        # Get today's accurate stats
        today = datetime.now().strftime("%Y-%m-%d")
        today_stats = db.fetchone('''SELECT 
                                        COALESCE(SUM(numbers_taken), 0) as taken,
                                        COALESCE(SUM(messages_received), 0) as messages,
                                        COALESCE(SUM(revenue_earned), 0) as revenue
                                    FROM user_stats WHERE date = ?''', (today,))
        
        # Get active assignments count
        active_assignments = db.fetchone("SELECT COUNT(*) as count FROM number_assignments WHERE is_active = 1")
        active_assignment_count = active_assignments['count'] if active_assignments else 0
        
        # Get pending withdrawals count and amount
        pending_withdrawals = db.fetchone('''SELECT COUNT(*) as count, SUM(amount) as total 
                                             FROM withdrawals WHERE status = 'pending' ''')
        
        # Get OTP stats
        otp_stats = db.fetchone('''SELECT 
                                    COUNT(*) as total_otps,
                                    COUNT(DISTINCT number) as unique_numbers,
                                    COUNT(DISTINCT forwarded_to) as users_received
                                   FROM otp_messages WHERE processed = 1''')
        
        msg = f"""📊 Bot Status Report - {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

👥 User Statistics:
• Total Users: {total_users}
• Active Users (24h): {active_users}
• Banned Users: {banned_users}
• Total Balance in System: ${total_balance:.3f}
• Active Assignments: {active_assignment_count}

📈 Today's Activity:
• Numbers Taken: {today_stats['taken'] if today_stats else 0}
• Messages Received: {today_stats['messages'] if today_stats else 0}
• Revenue Distributed: ${today_stats['revenue'] if today_stats else 0:.3f}

💰 Withdrawals:
• Pending: {pending_withdrawals['count'] if pending_withdrawals and pending_withdrawals['count'] else 0}
• Total Pending Amount: ${pending_withdrawals['total'] if pending_withdrawals and pending_withdrawals['total'] else 0:.3f}

📨 OTP Statistics:
• Total OTPs Processed: {otp_stats['total_otps'] if otp_stats and otp_stats['total_otps'] else 0}
• Unique Numbers: {otp_stats['unique_numbers'] if otp_stats and otp_stats['unique_numbers'] else 0}
• Users Received: {otp_stats['users_received'] if otp_stats and otp_stats['users_received'] else 0}

🌍 Country Statistics:
"""
        
        if country_stats:
            for country in country_stats:
                total = country['total_numbers'] or 0
                used = country['used_numbers'] or 0
                if total > 0:
                    available = total - used
                    used_percent = (used / total) * 100 if total > 0 else 0
                    msg += f"\n{country['flag']} {country['name']}:"
                    msg += f"\n  Total: {total} | Used: {used} ({used_percent:.1f}%) | Available: {available}"
        else:
            msg += "\nNo countries with numbers found."
        
        bot.send_message(chat_id, msg)
    except Exception as e:
        logger.error(f"Error in show_admin_status: {e}")
        logger.error(traceback.format_exc())
        bot.send_message(chat_id, "❌ Error loading status")

def show_admin_settings(chat_id):
    try:
        # Get current settings
        batch_size = get_setting('batch_size') or 1
        revenue = get_setting('revenue_per_message') or 0.005
        min_withdraw = get_setting('min_withdrawal') or 3.0
        max_user_numbers = get_setting('max_user_numbers') or 50
        withdrawal_enabled = get_setting('withdrawal_enabled') or 1
        bot_enabled = get_setting('bot_enabled') or 1
        
        msg = f"""⚙️ Bot Settings

Current Settings:
• Batch Size: {batch_size} numbers per request
• Revenue Per Message: ${revenue:.3f}
• Minimum Withdrawal: ${min_withdraw:.2f}
• Max Numbers Per User: {max_user_numbers}
• Withdrawal: {'✅ Enabled' if withdrawal_enabled == 1 else '❌ Disabled'}
• Bot Status: {'✅ Online' if bot_enabled == 1 else '❌ Offline'}

Select an option to change:"""
        
        markup = types.InlineKeyboardMarkup(row_width=3)
        
        # Batch size buttons
        batch_buttons = []
        for size in [1, 2, 3, 5, 10]:
            batch_buttons.append(types.InlineKeyboardButton(f"{size}", callback_data=f"set_batch_{size}"))
        markup.row(*batch_buttons)
        
        # Revenue buttons
        revenue_buttons = []
        for rev in [0.001, 0.002, 0.005, 0.01, 0.02]:
            revenue_buttons.append(types.InlineKeyboardButton(f"${rev}", callback_data=f"set_revenue_{rev}"))
        markup.row(*revenue_buttons)
        
        # Withdrawal buttons
        withdraw_buttons = [
            types.InlineKeyboardButton("Min $1", callback_data="set_min_withdraw_1"),
            types.InlineKeyboardButton("Min $3", callback_data="set_min_withdraw_3"),
            types.InlineKeyboardButton("Min $5", callback_data="set_min_withdraw_5"),
        ]
        markup.row(*withdraw_buttons)
        
        # Max numbers per user buttons
        max_num_buttons = [
            types.InlineKeyboardButton("10", callback_data="set_max_num_10"),
            types.InlineKeyboardButton("30", callback_data="set_max_num_30"),
            types.InlineKeyboardButton("50", callback_data="set_max_num_50"),
            types.InlineKeyboardButton("100", callback_data="set_max_num_100"),
            types.InlineKeyboardButton("200", callback_data="set_max_num_200"),
        ]
        markup.row(*max_num_buttons[:3])
        markup.row(*max_num_buttons[3:])
        
        # Toggle buttons
        toggle_withdraw = types.InlineKeyboardButton("Toggle Withdrawal", callback_data="toggle_withdrawal")
        toggle_bot = types.InlineKeyboardButton("Toggle Bot", callback_data="toggle_bot")
        markup.row(toggle_withdraw, toggle_bot)
        
        back_btn = types.InlineKeyboardButton("⬅️ Back", callback_data="admin_panel")
        markup.row(back_btn)
        
        bot.send_message(chat_id, msg, reply_markup=markup)
    except Exception as e:
        logger.error(f"Error in show_admin_settings: {e}")
        bot.send_message(chat_id, "❌ Error loading settings")

def start_broadcast(chat_id):
    try:
        msg = """📤 Broadcast Message

Send the message you want to broadcast to all users. You can send:
• Text message
• Photo with caption
• Video with caption
• Document with caption

Type /cancel to cancel."""
        
        bot.send_message(chat_id, msg)
        bot.register_next_step_handler_by_chat_id(chat_id, process_broadcast)
    except Exception as e:
        logger.error(f"Error in start_broadcast: {e}")

def process_broadcast(message):
    try:
        if message.text == '/cancel':
            bot.send_message(message.chat.id, "Broadcast cancelled.")
            show_admin_panel(message.chat.id)
            return
        
        # Get all non-banned users
        users = db.fetchall("SELECT user_id FROM users WHERE is_banned = 0")
        total = len(users)
        
        bot.send_message(message.chat.id, f"📤 Broadcasting to {total} users...")
        
        success = 0
        failed = 0
        
        for user in users:
            try:
                if message.text:
                    bot.send_message(user['user_id'], message.text)
                elif message.photo:
                    bot.send_photo(user['user_id'], message.photo[-1].file_id, caption=message.caption)
                elif message.video:
                    bot.send_video(user['user_id'], message.video.file_id, caption=message.caption)
                elif message.document:
                    bot.send_document(user['user_id'], message.document.file_id, caption=message.caption)
                
                success += 1
                time.sleep(0.1)  # Rate limiting
            except Exception as e:
                failed += 1
        
        bot.send_message(message.chat.id, f"✅ Broadcast completed!\n\nSuccess: {success}\nFailed: {failed}")
        show_admin_panel(message.chat.id)
    except Exception as e:
        logger.error(f"Error in process_broadcast: {e}")

def show_user_management(chat_id):
    try:
        # Get recent users
        users = db.fetchall('''SELECT user_id, username, first_name, last_name, balance, is_banned, join_date
                               FROM users ORDER BY join_date DESC LIMIT 20''')
        
        msg = "👤 Recent Users (Last 20)\n\n"
        
        markup = types.InlineKeyboardMarkup(row_width=2)
        
        for user in users:
            username = f"@{user['username']}" if user['username'] else "No username"
            status = "✅" if user['is_banned'] == 0 else "❌"
            msg += f"{status} {user['user_id']} - {username}\n"
            msg += f"   Name: {user['first_name']} {user['last_name'] or ''}\n"
            msg += f"   Balance: ${user['balance']:.3f}\n"
            msg += f"   Joined: {user['join_date']}\n\n"
            
            # Add ban/unban buttons
            if user['is_banned'] == 0:
                ban_btn = types.InlineKeyboardButton(f"Ban {user['user_id']}", callback_data=f"ban_{user['user_id']}")
                markup.add(ban_btn)
            else:
                unban_btn = types.InlineKeyboardButton(f"Unban {user['user_id']}", callback_data=f"unban_{user['user_id']}")
                markup.add(unban_btn)
        
        back_btn = types.InlineKeyboardButton("⬅️ Back", callback_data="admin_panel")
        markup.row(back_btn)
        
        bot.send_message(chat_id, msg, reply_markup=markup)
    except Exception as e:
        logger.error(f"Error in show_user_management: {e}")
        bot.send_message(chat_id, "❌ Error loading users")

def show_number_management(chat_id):
    try:
        markup = types.InlineKeyboardMarkup(row_width=2)
        
        add_btn = types.InlineKeyboardButton("➕ Add Numbers", callback_data="add_numbers")
        view_btn = types.InlineKeyboardButton("📊 View Stats", callback_data="view_numbers")
        delete_btn = types.InlineKeyboardButton("🗑️ Delete Country", callback_data="delete_country_list")
        report_btn = types.InlineKeyboardButton("📄 Numbers Report", callback_data="admin_numbers_report")
        back_btn = types.InlineKeyboardButton("⬅️ Back", callback_data="admin_panel")
        
        markup.add(add_btn, view_btn, delete_btn, report_btn)
        markup.row(back_btn)
        
        bot.send_message(chat_id, "📱 Number Management\n\nSelect an option:", reply_markup=markup)
    except Exception as e:
        logger.error(f"Error in show_number_management: {e}")
        bot.send_message(chat_id, "❌ Error loading number management")

def show_delete_country_list(chat_id):
    try:
        # Get countries with numbers
        countries = db.fetchall('''SELECT DISTINCT c.name, c.code, c.flag 
                                   FROM countries c 
                                   JOIN numbers n ON c.code = n.country_code 
                                   ORDER BY c.name''')
        
        if not countries:
            bot.send_message(chat_id, "❌ No countries available to delete!")
            return
        
        markup = types.InlineKeyboardMarkup(row_width=2)
        
        for country in countries:
            btn = types.InlineKeyboardButton(
                f"{country['flag']} {country['name']}",
                callback_data=f"delete_country_{country['code']}"
            )
            markup.add(btn)
        
        back_btn = types.InlineKeyboardButton("⬅️ Back", callback_data="admin_numbers")
        markup.row(back_btn)
        
        bot.send_message(chat_id, "🗑️ Select a country to delete:", reply_markup=markup)
    except Exception as e:
        logger.error(f"Error in show_delete_country_list: {e}")
        bot.send_message(chat_id, "❌ An error occurred!")

def delete_country(chat_id, country_code):
    try:
        # Get country info
        country = db.fetchone("SELECT name, flag FROM countries WHERE code = ?", (country_code,))
        if not country:
            bot.send_message(chat_id, "❌ Country not found!")
            return
        
        # Delete all numbers of this country
        db.execute("DELETE FROM numbers WHERE country_code = ?", (country_code,))
        
        # Delete number assignments for these numbers
        db.execute('''DELETE FROM number_assignments WHERE number IN 
                      (SELECT number FROM numbers WHERE country_code = ?)''', (country_code,))
        
        # Delete country from countries table
        db.execute("DELETE FROM countries WHERE code = ?", (country_code,))
        
        bot.send_message(chat_id, f"✅ {country['flag']} {country['name']} and all its numbers have been permanently deleted.")
    except Exception as e:
        logger.error(f"Error in delete_country: {e}")
        bot.send_message(chat_id, "❌ Error deleting country")

def ask_for_filename(chat_id):
    try:
        msg = """📁 Add Numbers - Step 1/3

Please send a name for this batch of numbers (e.g., 'US Numbers Batch 1'):
This name will help you identify the batch later.

Type /cancel to cancel."""
        
        bot.send_message(chat_id, msg)
        bot.register_next_step_handler_by_chat_id(chat_id, process_filename)
    except Exception as e:
        logger.error(f"Error in ask_for_filename: {e}")

def process_filename(message):
    try:
        if message.text == '/cancel':
            bot.send_message(message.chat.id, "Process cancelled.")
            show_admin_panel(message.chat.id)
            return
        
        batch_name = message.text
        
        # Save batch name in message cache
        message_cache[message.chat.id] = {'batch_name': batch_name}
        
        msg = """📁 Add Numbers - Step 2/3

Now please send the file containing the numbers.
Supported formats:
• CSV (.csv) - One number per line or column
• TXT (.txt) - One number per line
• JSON (.json) - Array of numbers or objects with 'number' field

The bot will automatically extract phone numbers from the file.

Type /cancel to cancel."""
        
        bot.send_message(message.chat.id, msg)
        bot.register_next_step_handler(message, process_number_file_upload)
    except Exception as e:
        logger.error(f"Error in process_filename: {e}")

def process_number_file_upload(message):
    try:
        if message.text == '/cancel':
            bot.send_message(message.chat.id, "Process cancelled.")
            show_admin_panel(message.chat.id)
            return
        
        if not message.document:
            bot.reply_to(message, "❌ Please send a file.")
            return
        
        bot.reply_to(message, "📥 Downloading and processing file...")
        
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        # Parse file based on extension
        filename = message.document.file_name.lower()
        
        if filename.endswith('.csv'):
            numbers = parse_csv_file(downloaded_file)
        elif filename.endswith('.txt'):
            numbers = parse_txt_file(downloaded_file)
        elif filename.endswith('.json'):
            numbers = parse_json_file(downloaded_file)
        else:
            numbers = parse_txt_file(downloaded_file)
        
        if not numbers:
            bot.reply_to(message, "❌ No valid phone numbers found in the file.")
            return
        
        # Clean and format numbers
        cleaned_numbers = []
        for number in numbers:
            # Remove all non-digit characters except +
            cleaned = re.sub(r'[^\d+]', '', number)
            if cleaned and not cleaned.startswith('+'):
                # Try to add + if it looks like an international number
                if len(cleaned) >= 10:
                    cleaned = '+' + cleaned
            if cleaned and len(cleaned) >= 10:
                cleaned_numbers.append(cleaned)
        
        if not cleaned_numbers:
            bot.reply_to(message, "❌ No valid phone numbers found after cleaning.")
            return
        
        # Save numbers in cache
        if message.chat.id in message_cache:
            message_cache[message.chat.id]['numbers'] = cleaned_numbers
            message_cache[message.chat.id]['filename'] = filename
        
        # Ask for duplicate handling
        markup = types.InlineKeyboardMarkup(row_width=2)
        skip_btn = types.InlineKeyboardButton("Skip Duplicates", callback_data="skip_duplicates")
        overwrite_btn = types.InlineKeyboardButton("Overwrite Duplicates", callback_data="overwrite_duplicates")
        markup.add(skip_btn, overwrite_btn)
        
        bot.send_message(message.chat.id, 
                         f"✅ Found {len(cleaned_numbers)} valid phone numbers in the file.\n\n"
                         "How do you want to handle duplicate numbers?\n"
                         "• Skip Duplicates: Only add new numbers\n"
                         "• Overwrite Duplicates: Update existing numbers",
                         reply_markup=markup)
        
    except Exception as e:
        logger.error(f"Error in process_number_file_upload: {e}")
        bot.reply_to(message, f"❌ Error: {str(e)}")

def parse_csv_file(content):
    """Parse CSV file and extract ALL phone numbers"""
    numbers = []
    try:
        # Try to decode as UTF-8
        text = content.decode('utf-8', errors='ignore')
        
        # Use aggressive regex to find all potential phone numbers
        # This pattern looks for any sequence that looks like a phone number
        potential_numbers = re.findall(r'[\+\(]?[1-9][\d\-\.\s\(\)]{8,}[\d]', text)
        
        for num in potential_numbers:
            extracted = extract_number_from_text(num)
            if extracted:
                numbers.append(extracted)
        
        # If no numbers found with the first pattern, try a simpler approach
        if not numbers:
            # Look for any sequence of 10+ digits
            digit_sequences = re.findall(r'\d{10,}', text)
            for seq in digit_sequences:
                numbers.append('+' + seq if len(seq) >= 10 else None)
        
        # Remove duplicates while preserving order
        seen = set()
        unique_numbers = []
        for num in numbers:
            if num and num not in seen:
                seen.add(num)
                unique_numbers.append(num)
        
        logger.info(f"Parsed {len(unique_numbers)} numbers from CSV")
        return unique_numbers
        
    except Exception as e:
        logger.error(f"Error parsing CSV: {e}")
        return []

def parse_txt_file(content):
    """Parse TXT file and extract ALL phone numbers"""
    numbers = []
    try:
        text = content.decode('utf-8', errors='ignore')
        
        # Split by lines and process each line
        lines = text.split('\n')
        for line in lines:
            # Look for phone number patterns in each line
            matches = re.findall(r'[\+\(]?[1-9][\d\-\.\s\(\)]{8,}[\d]', line)
            for match in matches:
                extracted = extract_number_from_text(match)
                if extracted:
                    numbers.append(extracted)
        
        # Fallback: extract all 10+ digit sequences
        if not numbers:
            digit_sequences = re.findall(r'\b\d{10,}\b', text)
            for seq in digit_sequences:
                if len(seq) >= 10:
                    numbers.append('+' + seq)
        
        # Remove duplicates
        seen = set()
        unique_numbers = []
        for num in numbers:
            if num and num not in seen:
                seen.add(num)
                unique_numbers.append(num)
        
        logger.info(f"Parsed {len(unique_numbers)} numbers from TXT")
        return unique_numbers
        
    except Exception as e:
        logger.error(f"Error parsing TXT: {e}")
        return []

def parse_json_file(content):
    numbers = []
    try:
        data = json.loads(content.decode('utf-8'))
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and 'number' in item:
                    num = extract_number_from_text(str(item['number']))
                    if num:
                        numbers.append(num)
                elif isinstance(item, str):
                    num = extract_number_from_text(item)
                    if num:
                        numbers.append(num)
    except:
        pass
    
    return numbers

def process_numbers_with_skip(call):
    try:
        chat_id = call.message.chat.id
        
        if chat_id not in message_cache or 'numbers' not in message_cache[chat_id]:
            bot.answer_callback_query(call.id, "❌ No numbers found in cache!")
            return
        
        numbers = message_cache[chat_id]['numbers']
        
        # Ask for country info
        msg = bot.send_message(chat_id, "Now, please send the country information in the format:\nCountry Name|Country Code|Flag\n\nExample: United States|US|🇺🇸")
        bot.register_next_step_handler(msg, lambda m: add_numbers_to_db(m, numbers, 'skip'))
        
        bot.answer_callback_query(call.id, "✅ Please enter country info")
    except Exception as e:
        logger.error(f"Error in process_numbers_with_skip: {e}")
        bot.answer_callback_query(call.id, "❌ An error occurred!")

def process_numbers_with_overwrite(call):
    try:
        chat_id = call.message.chat.id
        
        if chat_id not in message_cache or 'numbers' not in message_cache[chat_id]:
            bot.answer_callback_query(call.id, "❌ No numbers found in cache!")
            return
        
        numbers = message_cache[chat_id]['numbers']
        
        # Ask for country info
        msg = bot.send_message(chat_id, "Now, please send the country information in the format:\nCountry Name|Country Code|Flag\n\nExample: United States|US|🇺🇸")
        bot.register_next_step_handler(msg, lambda m: add_numbers_to_db(m, numbers, 'overwrite'))
        
        bot.answer_callback_query(call.id, "✅ Please enter country info")
    except Exception as e:
        logger.error(f"Error in process_numbers_with_overwrite: {e}")
        bot.answer_callback_query(call.id, "❌ An error occurred!")

def add_numbers_to_db(message, numbers, duplicate_handling):
    try:
        chat_id = message.chat.id
        
        if message.text == '/cancel':
            bot.send_message(chat_id, "Process cancelled.")
            show_admin_panel(chat_id)
            return
        
        country_info = message.text.split('|')
        if len(country_info) != 3:
            bot.reply_to(message, "❌ Invalid format. Use: Country Name|Code|Flag\nExample: United States|US|🇺🇸")
            return
        
        country_name, country_code, country_flag = country_info
        
        # Get the batch name from cache
        batch_name = message_cache[chat_id]['batch_name'] if chat_id in message_cache and 'batch_name' in message_cache[chat_id] else 'Default Batch'
        
        bot.reply_to(message, f"⏳ Processing {len(numbers)} numbers for {country_flag} {country_name}...")
        
        added = 0
        skipped = 0
        updated = 0
        
        for number in numbers:
            try:
                # Get country info from number
                flag, name = get_country_from_number(number)
                
                # Use provided country info or auto-detected
                final_country = country_name if country_name else name
                final_code = country_code if country_code else 'Unknown'
                final_flag = country_flag if country_flag else flag
                
                # Check if number exists
                existing = db.fetchone("SELECT id FROM numbers WHERE number = ?", (number,))
                
                if existing:
                    if duplicate_handling == 'skip':
                        skipped += 1
                        continue
                    elif duplicate_handling == 'overwrite':
                        # Update existing number with batch name
                        db.execute('''UPDATE numbers SET country = ?, country_code = ?, country_flag = ?, batch_name = ?
                                      WHERE number = ?''',
                                   (final_country, final_code, final_flag, batch_name, number))
                        updated += 1
                else:
                    # Add new number with batch name
                    db.execute('''INSERT INTO numbers (country, number, country_code, country_flag, batch_name)
                                  VALUES (?, ?, ?, ?, ?)''',
                               (final_country, number, final_code, final_flag, batch_name))
                    added += 1
                    
            except Exception as e:
                logger.error(f"Error processing number {number}: {e}")
                skipped += 1
        
        # Update country stats
        country = db.fetchone("SELECT * FROM countries WHERE code = ?", (country_code,))
        if country:
            db.execute('''UPDATE countries SET total_numbers = total_numbers + ? 
                          WHERE code = ?''', (added, country_code))
        else:
            db.execute('''INSERT INTO countries (name, code, flag, total_numbers)
                          VALUES (?, ?, ?, ?)''',
                       (country_name, country_code, country_flag, added))
        
        # Clear cache
        if chat_id in message_cache:
            del message_cache[chat_id]
        
        # Notify all users
        users = db.fetchall("SELECT user_id FROM users WHERE is_banned = 0")
        notified = 0
        
        for user in users:
            try:
                bot.send_message(user['user_id'], 
                                f"🆕 New numbers added for {country_flag} {country_name}! Use '📇 Get Number' to get one.")
                notified += 1
                time.sleep(0.1)
            except:
                continue
        
        result_msg = f"✅ Numbers added for {country_flag} {country_name}!\n\n"
        result_msg += f"Batch Name: {batch_name}\n"
        result_msg += f"Added: {added}\n"
        if duplicate_handling == 'skip':
            result_msg += f"Skipped (duplicates): {skipped}\n"
        else:
            result_msg += f"Updated: {updated}\n"
            result_msg += f"Failed: {skipped}\n"
        result_msg += f"Notified {notified} users."
        
        bot.send_message(chat_id, result_msg)
        
    except Exception as e:
        logger.error(f"Error in add_numbers_to_db: {e}")
        bot.reply_to(message, f"❌ Error: {str(e)}")

def show_number_stats(chat_id):
    try:
        # Get accurate country statistics with real-time data
        country_stats = db.fetchall('''SELECT 
                                        c.name,
                                        c.flag,
                                        c.code,
                                        COALESCE(COUNT(DISTINCT n.id), 0) as total_numbers,
                                        COALESCE(SUM(CASE WHEN n.is_used = 1 THEN 1 ELSE 0 END), 0) as used_numbers,
                                        COALESCE(SUM(CASE WHEN n.is_used = 0 THEN 1 ELSE 0 END), 0) as available_numbers
                                    FROM countries c
                                    LEFT JOIN numbers n ON c.code = n.country_code
                                    GROUP BY c.code, c.name, c.flag
                                    HAVING total_numbers > 0
                                    ORDER BY c.name''')
        
        if not country_stats:
            bot.send_message(chat_id, "❌ No countries with numbers found.")
            return
        
        msg = "📊 Number Statistics\n\n"
        msg += "🌍 Country-wise Statistics:\n"
        
        total_all = 0
        used_all = 0
        available_all = 0
        
        for country in country_stats:
            total = country['total_numbers'] or 0
            used = country['used_numbers'] or 0
            available = country['available_numbers'] or 0
            
            total_all += total
            used_all += used
            available_all += available
            
            if total > 0:
                used_percent = (used / total) * 100 if total > 0 else 0
                msg += f"\n{country['flag']} {country['name']} ({country['code']}):"
                msg += f"\n  Total: {total}"
                msg += f"\n  Used: {used} ({used_percent:.1f}%)"
                msg += f"\n  Available: {available}\n"
        
        msg += f"\n📈 Overall Statistics:"
        msg += f"\nTotal Numbers: {total_all}"
        msg += f"\nTotal Used: {used_all}"
        msg += f"\nTotal Available: {available_all}"
        
        # Get batch statistics
        batches = db.fetchall('''SELECT 
                                    batch_name,
                                    COUNT(*) as count,
                                    SUM(CASE WHEN is_used = 1 THEN 1 ELSE 0 END) as used,
                                    SUM(CASE WHEN is_used = 0 THEN 1 ELSE 0 END) as available
                                 FROM numbers 
                                 WHERE batch_name IS NOT NULL AND batch_name != ''
                                 GROUP BY batch_name
                                 ORDER BY batch_name''')
        
        if batches:
            msg += "\n\n📦 Batch Statistics:\n"
            for batch in batches:
                msg += f"\nBatch: {batch['batch_name']}"
                msg += f"\n  Total: {batch['count']}"
                msg += f"\n  Used: {batch['used']}"
                msg += f"\n  Available: {batch['available']}\n"
        
        # Get assignment statistics
        assignments = db.fetchall('''SELECT 
                                        COUNT(*) as total_assignments,
                                        COUNT(DISTINCT user_id) as active_users,
                                        SUM(otp_count) as total_otps,
                                        SUM(total_revenue) as total_revenue
                                     FROM number_assignments 
                                     WHERE is_active = 1''')
        
        if assignments and assignments[0]:
            msg += f"\n📋 Assignment Statistics:"
            msg += f"\nActive Assignments: {assignments[0]['total_assignments'] or 0}"
            msg += f"\nActive Users: {assignments[0]['active_users'] or 0}"
            msg += f"\nTotal OTPs Received: {assignments[0]['total_otps'] or 0}"
            msg += f"\nTotal Revenue Generated: ${assignments[0]['total_revenue'] or 0:.3f}"
        
        bot.send_message(chat_id, msg)
    except Exception as e:
        logger.error(f"Error in show_number_stats: {e}")
        bot.send_message(chat_id, "❌ Error loading number stats")

def show_withdrawal_management(chat_id, page=0):
    try:
        limit = 10
        offset = page * limit
        
        # Get total pending withdrawals count
        total_withdrawals = db.fetchone("SELECT COUNT(*) as count FROM withdrawals WHERE status = 'pending'")
        total = total_withdrawals['count'] if total_withdrawals else 0
        
        if total == 0:
            bot.send_message(chat_id, "✅ No pending withdrawals.")
            return
        
        # Get pending withdrawals with pagination
        withdrawals = db.fetchall('''SELECT w.id, w.user_id, w.amount, w.network, w.request_date, 
                                            u.username, u.first_name
                                     FROM withdrawals w
                                     LEFT JOIN users u ON w.user_id = u.user_id
                                     WHERE w.status = 'pending'
                                     ORDER BY w.request_date DESC
                                     LIMIT ? OFFSET ?''', (limit, offset))
        
        if not withdrawals:
            bot.send_message(chat_id, "No withdrawals found for this page.")
            return
        
        msg = f"💳 Pending Withdrawals (Page {page + 1})\n\n"
        
        markup = types.InlineKeyboardMarkup(row_width=2)
        
        for withdraw in withdrawals:
            username = f"@{withdraw['username']}" if withdraw['username'] else "No username"
            msg += f"ID: {withdraw['id']}\n"
            msg += f"User: {withdraw['first_name']} ({username})\n"
            msg += f"Amount: ${withdraw['amount']:.3f}\n"
            msg += f"Network: {withdraw['network']}\n"
            msg += f"Date: {withdraw['request_date']}\n\n"
            
            # Add view button
            view_btn = types.InlineKeyboardButton(f"View #{withdraw['id']}", callback_data=f"view_withdraw_{withdraw['id']}")
            markup.add(view_btn)
        
        # Pagination buttons
        pagination_btns = []
        if page > 0:
            prev_btn = types.InlineKeyboardButton("⬅️ Previous", callback_data=f"withdraw_page_{page-1}")
            pagination_btns.append(prev_btn)
        
        if offset + limit < total:
            next_btn = types.InlineKeyboardButton("Next ➡️", callback_data=f"withdraw_page_{page+1}")
            pagination_btns.append(next_btn)
        
        if pagination_btns:
            markup.row(*pagination_btns)
        
        back_btn = types.InlineKeyboardButton("⬅️ Back", callback_data="admin_panel")
        markup.row(back_btn)
        
        bot.send_message(chat_id, msg, reply_markup=markup)
    except Exception as e:
        logger.error(f"Error in show_withdrawal_management: {e}")
        bot.send_message(chat_id, "❌ Error loading withdrawals")

def show_withdrawal_details(chat_id, withdraw_id):
    try:
        withdrawal = db.fetchone('''SELECT w.*, u.username, u.first_name, u.last_name, u.balance
                                    FROM withdrawals w
                                    LEFT JOIN users u ON w.user_id = u.user_id
                                    WHERE w.id = ?''', (withdraw_id,))
        
        if not withdrawal:
            bot.send_message(chat_id, "❌ Withdrawal not found.")
            return
        
        msg = f"""💳 Withdrawal Details #{withdraw_id}

👤 User Information:
• ID: {withdrawal['user_id']}
• Username: @{withdrawal['username'] or 'N/A'}
• Name: {withdrawal['first_name']} {withdrawal['last_name'] or ''}
• Current Balance: ${withdrawal['balance']:.3f}

💰 Transaction:
• Amount: ${withdrawal['amount']:.3f}
• Network: {withdrawal['network']}
• Address: {withdrawal['address']}
• Status: {withdrawal['status'].upper()}
• Request Date: {withdrawal['request_date']}
• Process Date: {withdrawal['process_date'] or 'Not processed yet'}
"""
        
        markup = types.InlineKeyboardMarkup()
        
        if withdrawal['status'] == 'pending':
            approve_btn = types.InlineKeyboardButton("✅ Approve", callback_data=f"approve_withdraw_{withdraw_id}")
            reject_btn = types.InlineKeyboardButton("❌ Reject", callback_data=f"reject_withdraw_{withdraw_id}")
            markup.add(approve_btn, reject_btn)
        
        back_btn = types.InlineKeyboardButton("⬅️ Back", callback_data="admin_withdrawals")
        markup.row(back_btn)
        
        bot.send_message(chat_id, msg, reply_markup=markup)
    except Exception as e:
        logger.error(f"Error in show_withdrawal_details: {e}")
        bot.send_message(chat_id, "❌ Error loading withdrawal details")

def show_ticket_management(chat_id, page=0):
    try:
        limit = 10
        offset = page * limit
        
        # Get total open tickets count
        total_tickets = db.fetchone("SELECT COUNT(*) as count FROM support_tickets WHERE status = 'open'")
        total = total_tickets['count'] if total_tickets else 0
        
        if total == 0:
            bot.send_message(chat_id, "✅ No open support tickets.")
            return
        
        # Get open tickets with pagination
        tickets = db.fetchall('''SELECT t.id, t.user_id, t.message, t.created_date, 
                                        u.username, u.first_name
                                 FROM support_tickets t
                                 LEFT JOIN users u ON t.user_id = u.user_id
                                 WHERE t.status = 'open'
                                 ORDER BY t.created_date DESC 
                                 LIMIT ? OFFSET ?''', (limit, offset))
        
        if not tickets:
            bot.send_message(chat_id, "No tickets found for this page.")
            return
        
        msg = f"🆘 Open Support Tickets (Page {page + 1})\n\n"
        
        markup = types.InlineKeyboardMarkup(row_width=2)
        
        for ticket in tickets:
            username = f"@{ticket['username']}" if ticket['username'] else "No username"
            msg += f"Ticket #{ticket['id']}\n"
            msg += f"User: {ticket['first_name']} ({username})\n"
            msg += f"Date: {ticket['created_date']}\n"
            msg += f"Message: {ticket['message'][:50]}...\n\n"
            
            # Add view button
            view_btn = types.InlineKeyboardButton(f"View #{ticket['id']}", callback_data=f"view_ticket_{ticket['id']}")
            markup.add(view_btn)
        
        # Pagination buttons
        pagination_btns = []
        if page > 0:
            prev_btn = types.InlineKeyboardButton("⬅️ Previous", callback_data=f"tickets_page_{page-1}")
            pagination_btns.append(prev_btn)
        
        if offset + limit < total:
            next_btn = types.InlineKeyboardButton("Next ➡️", callback_data=f"tickets_page_{page+1}")
            pagination_btns.append(next_btn)
        
        if pagination_btns:
            markup.row(*pagination_btns)
        
        back_btn = types.InlineKeyboardButton("⬅️ Back", callback_data="admin_panel")
        markup.row(back_btn)
        
        bot.send_message(chat_id, msg, reply_markup=markup)
    except Exception as e:
        logger.error(f"Error in show_ticket_management: {e}")
        bot.send_message(chat_id, "❌ Error loading tickets")

def show_ticket_details(chat_id, ticket_id):
    try:
        ticket = db.fetchone('''SELECT t.*, u.username, u.first_name, u.last_name
                                FROM support_tickets t
                                LEFT JOIN users u ON t.user_id = u.user_id
                                WHERE t.id = ?''', (ticket_id,))
        
        if not ticket:
            bot.send_message(chat_id, "❌ Ticket not found.")
            return
        
        msg = f"""🆘 Support Ticket #{ticket_id}

👤 User Information:
• ID: {ticket['user_id']}
• Username: @{ticket['username'] or 'N/A'}
• Name: {ticket['first_name']} {ticket['last_name'] or ''}

📝 Ticket Details:
• Status: {ticket['status'].upper()}
• Created: {ticket['created_date']}
• Resolved: {ticket['resolved_date'] or 'Not resolved yet'}

💬 Message:
{ticket['message']}

"""
        
        if ticket['admin_reply']:
            msg += f"📤 Admin Reply:\n{ticket['admin_reply']}\n"
        
        markup = types.InlineKeyboardMarkup()
        
        if ticket['status'] == 'open':
            reply_btn = types.InlineKeyboardButton("📤 Reply", callback_data=f"reply_ticket_{ticket_id}")
            markup.add(reply_btn)
        
        back_btn = types.InlineKeyboardButton("⬅️ Back", callback_data="admin_tickets")
        markup.row(back_btn)
        
        bot.send_message(chat_id, msg, reply_markup=markup)
    except Exception as e:
        logger.error(f"Error in show_ticket_details: {e}")
        bot.send_message(chat_id, "❌ Error loading ticket details")

def start_ticket_reply(chat_id, ticket_id):
    try:
        msg = f"📤 Please type your reply for ticket #{ticket_id}:\n\nType /cancel to cancel."
        bot.send_message(chat_id, msg)
        bot.register_next_step_handler_by_chat_id(chat_id, lambda m: process_ticket_reply(m, ticket_id))
    except Exception as e:
        logger.error(f"Error in start_ticket_reply: {e}")

def process_ticket_reply(message, ticket_id):
    try:
        if message.text == '/cancel':
            bot.send_message(message.chat.id, "Ticket reply cancelled.")
            return
        
        # Get ticket
        ticket = db.fetchone('''SELECT t.*, u.user_id as user_id
                                FROM support_tickets t
                                LEFT JOIN users u ON t.user_id = u.user_id
                                WHERE t.id = ?''', (ticket_id,))
        
        if not ticket:
            bot.send_message(message.chat.id, "❌ Ticket not found.")
            return
        
        # Update ticket
        resolved_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        db.execute('''UPDATE support_tickets SET status = 'closed', resolved_date = ?, 
                      admin_id = ?, admin_reply = ? WHERE id = ?''',
                   (resolved_date, message.from_user.id, message.text, ticket_id))
        
        # Notify user WITHOUT showing admin username
        user_msg = f"""📩 Reply to your support ticket #{ticket_id}

Message: {message.text}

Status: ✅ Your ticket has been resolved and closed.
"""
        try:
            bot.send_message(ticket['user_id'], user_msg)
        except Exception as e:
            logger.error(f"Error sending reply to user {ticket['user_id']}: {e}")
        
        bot.send_message(message.chat.id, f"✅ Reply sent for ticket #{ticket_id} and ticket closed.")
    except Exception as e:
        logger.error(f"Error in process_ticket_reply: {e}")
        bot.send_message(message.chat.id, "❌ An error occurred.")

def export_stats(chat_id):
    try:
        # Create CSV data
        today = datetime.now().strftime("%Y-%m-%d")
        
        # User stats
        users = db.fetchall('''SELECT user_id, username, first_name, last_name, balance, 
                                      total_earned, total_withdrawn, join_date, is_banned
                               FROM users ORDER BY join_date DESC''')
        
        # Create CSV
        import io
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Write header
        writer.writerow(['User ID', 'Username', 'First Name', 'Last Name', 'Balance', 
                         'Total Earned', 'Total Withdrawn', 'Join Date', 'Status'])
        
        # Write data
        for user in users:
            status = 'Active' if user['is_banned'] == 0 else 'Banned'
            writer.writerow([
                user['user_id'],
                user['username'] or '',
                user['first_name'] or '',
                user['last_name'] or '',
                f"{user['balance']:.3f}",
                f"{user['total_earned'] or 0:.3f}",
                f"{user['total_withdrawn'] or 0:.3f}",
                user['join_date'],
                status
            ])
        
        # Send as document
        bot.send_document(chat_id, ('users_export.csv', output.getvalue()))
        output.close()
        
        bot.send_message(chat_id, "✅ Stats exported successfully!")
    except Exception as e:
        logger.error(f"Error in export_stats: {e}")
        bot.send_message(chat_id, "❌ Error exporting stats")

# New functions for resetting assignments by country
def show_reset_country_selection(chat_id):
    """Show list of countries to reset assignments for"""
    try:
        # Get countries with active assignments
        countries = db.fetchall('''SELECT DISTINCT n.country_code, c.name, c.flag, 
                                          COUNT(na.id) as active_count
                                   FROM number_assignments na
                                   JOIN numbers n ON na.number = n.number
                                   JOIN countries c ON n.country_code = c.code
                                   WHERE na.is_active = 1
                                   GROUP BY n.country_code, c.name, c.flag
                                   HAVING active_count > 0
                                   ORDER BY c.name''')
        
        if not countries:
            bot.send_message(chat_id, "✅ No active assignments found in any country.")
            return
        
        markup = types.InlineKeyboardMarkup(row_width=2)
        
        for country in countries:
            btn_text = f"{country['flag']} {country['name']} ({country['active_count']})"
            btn = types.InlineKeyboardButton(btn_text, callback_data=f"reset_country_{country['country_code']}")
            markup.add(btn)
        
        back_btn = types.InlineKeyboardButton("⬅️ Back", callback_data="admin_reset")
        markup.row(back_btn)
        
        bot.send_message(chat_id, "🌍 Select a country to reset ALL assignments:\n\n" +
                         "⚠️ This will deactivate ALL active number assignments for the selected country.", 
                         reply_markup=markup)
    except Exception as e:
        logger.error(f"Error in show_reset_country_selection: {e}")
        bot.send_message(chat_id, "❌ Error loading country list.")

def show_reset_country_confirmation(call, country_code):
    """Show confirmation for resetting assignments in a country"""
    try:
        # Get country info
        country = db.fetchone('''SELECT c.name, c.flag, COUNT(na.id) as active_count
                                 FROM countries c
                                 JOIN numbers n ON c.code = n.country_code
                                 JOIN number_assignments na ON n.number = na.number
                                 WHERE c.code = ? AND na.is_active = 1''', (country_code,))
        
        if not country or country['active_count'] == 0:
            bot.answer_callback_query(call.id, "❌ No active assignments found for this country!")
            return
        
        markup = types.InlineKeyboardMarkup(row_width=2)
        confirm_btn = types.InlineKeyboardButton("✅ Yes, Reset All", callback_data=f"confirm_reset_country_{country_code}")
        cancel_btn = types.InlineKeyboardButton("❌ Cancel", callback_data="admin_reset")
        markup.add(confirm_btn, cancel_btn)
        
        msg = f"⚠️ **CONFIRM RESET**\n\n"
        msg += f"Country: {country['flag']} {country['name']}\n"
        msg += f"Active Assignments: {country['active_count']}\n\n"
        msg += "This will:\n"
        msg += "1. Deactivate ALL active assignments for this country\n"
        msg += "2. Mark all numbers as available again\n"
        msg += "3. Notify affected users\n\n"
        msg += "**This action cannot be undone!**"
        
        bot.edit_message_text(msg, call.message.chat.id, call.message.message_id, 
                              reply_markup=markup, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in show_reset_country_confirmation: {e}")
        bot.answer_callback_query(call.id, "❌ Error loading confirmation!")

def reset_all_assignments_for_country(call, country_code):
    """Reset all assignments for a specific country"""
    try:
        # Get country info and affected assignments
        country = db.fetchone("SELECT name, flag FROM countries WHERE code = ?", (country_code,))
        if not country:
            bot.answer_callback_query(call.id, "❌ Country not found!")
            return
        
        # Get all active assignments for this country
        assignments = db.fetchall('''SELECT na.number, na.user_id, u.username
                                     FROM number_assignments na
                                     JOIN numbers n ON na.number = n.number
                                     LEFT JOIN users u ON na.user_id = u.user_id
                                     WHERE n.country_code = ? AND na.is_active = 1''', (country_code,))
        
        if not assignments:
            bot.answer_callback_query(call.id, "❌ No assignments found!")
            return
        
        reset_count = 0
        notified_users = set()
        
        for assignment in assignments:
            try:
                # Deactivate the assignment
                db.execute("UPDATE number_assignments SET is_active = 0 WHERE number = ?", 
                           (assignment['number'],))
                
                # Mark number as available again
                db.execute("UPDATE numbers SET is_used = 0, used_by = NULL, use_date = NULL WHERE number = ?", 
                           (assignment['number'],))
                
                reset_count += 1
                
                # Notify user (only once per user)
                if assignment['user_id'] not in notified_users:
                    try:
                        user_msg = f"📢 **Important Notice**\n\n"
                        user_msg += f"Your number assignments for {country['flag']} {country['name']} "
                        user_msg += "have been reset by admin.\n\n"
                        user_msg += "You can get new numbers from the '📇 Get Number' menu."
                        bot.send_message(assignment['user_id'], user_msg, parse_mode='Markdown')
                        notified_users.add(assignment['user_id'])
                    except Exception as e:
                        logger.error(f"Could not notify user {assignment['user_id']}: {e}")
                        
            except Exception as e:
                logger.error(f"Error resetting number {assignment.get('number')}: {e}")
        
        # Update country stats
        db.execute('''UPDATE countries SET used_numbers = used_numbers - ? 
                      WHERE code = ?''', (reset_count, country_code))
        
        # Send confirmation
        success_msg = f"✅ **Reset Complete**\n\n"
        success_msg += f"Country: {country['flag']} {country['name']}\n"
        success_msg += f"Assignments Reset: {reset_count}\n"
        success_msg += f"Users Notified: {len(notified_users)}\n\n"
        success_msg += "All numbers are now available for new assignments."
        
        bot.edit_message_text(success_msg, call.message.chat.id, call.message.message_id, 
                              parse_mode='Markdown')
        
        bot.answer_callback_query(call.id, f"✅ Reset {reset_count} assignments!")
        
    except Exception as e:
        logger.error(f"Error in reset_all_assignments_for_country: {e}")
        bot.answer_callback_query(call.id, "❌ Error resetting assignments!")

def show_admin_reset_system(chat_id):
    try:
        markup = types.InlineKeyboardMarkup(row_width=2)
        
        btn1 = types.InlineKeyboardButton("🗑️ Delete Used Numbers", callback_data="reset_delete_used")
        btn2 = types.InlineKeyboardButton("🌍 Reset Country Assignments", callback_data="reset_all_assignments")
        btn3 = types.InlineKeyboardButton("🔄 Reset All Assignments", callback_data="reset_all_assignments_global")
        btn4 = types.InlineKeyboardButton("🧹 Clean OTP Messages", callback_data="reset_clean_otp")
        btn5 = types.InlineKeyboardButton("📊 Fix Stats Count", callback_data="reset_fix_stats")
        
        markup.add(btn1, btn2, btn3, btn4, btn5)
        
        back_btn = types.InlineKeyboardButton("⬅️ Back", callback_data="admin_panel")
        markup.row(back_btn)
        
        msg = """🗑️ Reset System
        
⚠️ WARNING: These actions are PERMANENT and cannot be undone!

Options:
1. 🗑️ Delete Used Numbers - Permanently delete all used numbers
2. 🌍 Reset Country Assignments - Deactivate assignments for a specific country
3. 🔄 Reset All Assignments - Deactivate ALL active assignments globally
4. 🧹 Clean OTP Messages - Delete old OTP messages
5. 📊 Fix Stats Count - Fix incorrect statistics"""
        
        bot.send_message(chat_id, msg, reply_markup=markup)
    except Exception as e:
        logger.error(f"Error in show_admin_reset_system: {e}")

def reset_delete_used_numbers(chat_id):
    try:
        # Get count of used numbers
        used_count = db.fetchone("SELECT COUNT(*) as count FROM numbers WHERE is_used = 1")
        count = used_count['count'] if used_count else 0
        
        if count == 0:
            bot.send_message(chat_id, "✅ No used numbers found to delete.")
            return
        
        # Ask for confirmation
        markup = types.InlineKeyboardMarkup()
        confirm_btn = types.InlineKeyboardButton("✅ Confirm Delete", callback_data="reset_confirm_delete_used")
        cancel_btn = types.InlineKeyboardButton("❌ Cancel", callback_data="admin_reset")
        markup.add(confirm_btn, cancel_btn)
        
        bot.send_message(chat_id, f"⚠️ Are you sure you want to PERMANENTLY delete {count} used numbers?\n\nThis action cannot be undone!", reply_markup=markup)
    except Exception as e:
        logger.error(f"Error in reset_delete_used_numbers: {e}")

def reset_confirm_delete_used(call):
    try:
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "❌ Access denied!")
            return
        
        # Get used numbers
        used_numbers = db.fetchall("SELECT number, country_code FROM numbers WHERE is_used = 1")
        
        if not used_numbers:
            bot.answer_callback_query(call.id, "✅ No used numbers found!")
            return
        
        deleted_count = 0
        
        for num in used_numbers:
            try:
                # Delete from all tables
                db.execute("DELETE FROM numbers WHERE number = ?", (num['number'],))
                db.execute("DELETE FROM number_assignments WHERE number = ?", (num['number'],))
                db.execute("DELETE FROM otp_messages WHERE number = ?", (num['number'],))
                db.execute("DELETE FROM message_tracking WHERE number = ?", (num['number'],))
                
                # Add to reset history
                reset_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                db.execute('''INSERT INTO reset_history (number, country_code, reset_date, reset_type)
                              VALUES (?, ?, ?, ?)''',
                           (num['number'], num['country_code'], reset_date, 'admin'))
                
                # Update country stats
                if num['country_code']:
                    db.execute('''UPDATE countries 
                                  SET total_numbers = total_numbers - 1, 
                                      used_numbers = used_numbers - 1 
                                  WHERE code = ?''', (num['country_code'],))
                
                deleted_count += 1
            except Exception as e:
                logger.error(f"Error deleting number {num['number']}: {e}")
        
        # Send confirmation
        bot.edit_message_text(f"✅ Successfully deleted {deleted_count} used numbers permanently!\n\nThese numbers will never be assigned to anyone again.",
                             call.message.chat.id, call.message.message_id)
        
        bot.answer_callback_query(call.id, "✅ Deleted used numbers!")
    except Exception as e:
        logger.error(f"Error in reset_confirm_delete_used: {e}")
        bot.answer_callback_query(call.id, "❌ Error deleting numbers!")

def generate_numbers_report(chat_id):
    try:
        bot.send_message(chat_id, "📊 Generating numbers report...")
        
        # Get all numbers with their assignments and OTP counts
        numbers = db.fetchall('''SELECT 
                                    n.number,
                                    n.country,
                                    n.country_code,
                                    n.country_flag,
                                    n.is_used,
                                    n.batch_name,
                                    n.use_date,
                                    u.username as used_by_username,
                                    u.user_id as used_by_id,
                                    na.otp_count,
                                    na.total_revenue,
                                    na.assigned_date,
                                    na.last_otp_date
                                 FROM numbers n
                                 LEFT JOIN users u ON n.used_by = u.user_id
                                 LEFT JOIN number_assignments na ON n.number = na.number AND na.is_active = 1
                                 ORDER BY n.country, n.number''')
        
        if not numbers:
            bot.send_message(chat_id, "❌ No numbers found in database.")
            return
        
        # Create TXT file content
        import io
        output = io.StringIO()
        
        output.write("=" * 80 + "\n")
        output.write("NUMBERS REPORT\n")
        output.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        output.write("=" * 80 + "\n\n")
        
        output.write(f"Total Numbers: {len(numbers)}\n")
        
        # Group by country
        countries = {}
        for num in numbers:
            country = num['country'] or 'Unknown'
            if country not in countries:
                countries[country] = []
            countries[country].append(num)
        
        for country, nums in countries.items():
            output.write(f"\n{'='*60}\n")
            output.write(f"COUNTRY: {country}\n")
            output.write(f"{'='*60}\n\n")
            
            for i, num in enumerate(nums, 1):
                output.write(f"[{i}] {num['country_flag']} {num['number']}\n")
                output.write(f"    Status: {'✅ Used' if num['is_used'] == 1 else '🟢 Available'}\n")
                
                if num['is_used'] == 1:
                    output.write(f"    Used By: @{num['used_by_username'] or 'N/A'} (ID: {num['used_by_id'] or 'N/A'})\n")
                    output.write(f"    Use Date: {num['use_date'] or 'N/A'}\n")
                
                if num['otp_count']:
                    output.write(f"    OTPs Received: {num['otp_count']}\n")
                    output.write(f"    Revenue: ${num['total_revenue'] or 0:.3f}\n")
                    output.write(f"    Last OTP: {num['last_otp_date'] or 'N/A'}\n")
                
                if num['batch_name']:
                    output.write(f"    Batch: {num['batch_name']}\n")
                
                output.write(f"    Assigned Date: {num['assigned_date'] or 'N/A'}\n")
                output.write("\n")
        
        # Add summary
        output.write("\n" + "="*80 + "\n")
        output.write("SUMMARY\n")
        output.write("="*80 + "\n\n")
        
        total_used = sum(1 for n in numbers if n['is_used'] == 1)
        total_available = len(numbers) - total_used
        total_otps = sum(n['otp_count'] or 0 for n in numbers)
        total_revenue = sum(n['total_revenue'] or 0 for n in numbers)
        
        output.write(f"Total Numbers: {len(numbers)}\n")
        output.write(f"Used Numbers: {total_used}\n")
        output.write(f"Available Numbers: {total_available}\n")
        output.write(f"Total OTPs Received: {total_otps}\n")
        output.write(f"Total Revenue Generated: ${total_revenue:.3f}\n")
        output.write(f"Report Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        
        # Save to file and send
        filename = f"numbers_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        bot.send_document(chat_id, (filename, output.getvalue()))
        output.close()
        
        bot.send_message(chat_id, "✅ Numbers report generated successfully!")
    except Exception as e:
        logger.error(f"Error in generate_numbers_report: {e}")
        bot.send_message(chat_id, "❌ Error generating report!")

# Enhanced Group message monitoring - detects all messages including from other bots
@bot.message_handler(func=lambda message: message.chat.id == MONITORED_GROUP_ID)
def handle_group_message(message):
    """Monitor OTP group messages and store them for processing"""
    try:
        logger.info(f"New message in group {MONITORED_GROUP_ID} from {message.from_user.id if message.from_user else 'Unknown'}")
        
        # Get message text
        text = message.text or message.caption or ""
        
        if not text:
            logger.info("Message has no text content")
            return
        
        logger.info(f"Message text: {text[:200]}...")
        
        # Check if message already processed
        existing = db.fetchone("SELECT id FROM message_tracking WHERE message_id = ?", (message.message_id,))
        if existing:
            logger.info(f"Message {message.message_id} already processed")
            return
        
        # Enhanced number extraction - try multiple methods
        number = extract_number_from_text(text)
        
        if not number:
            logger.info(f"No number found in message: {text[:100]}...")
            return
        
        logger.info(f"Extracted number: {number}")
        
        # Get country info
        country_flag, country_name = get_country_from_number(number)
        logger.info(f"Country info: {country_name} {country_flag}")
        
        # Extract OTP - enhanced extraction
        otp_code = extract_otp_from_message(text)
        logger.info(f"Extracted OTP: {otp_code}")
        
        # Store in database
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Store message tracking
        db.execute("INSERT INTO message_tracking (message_id, number, processed_date) VALUES (?, ?, ?)",
                   (message.message_id, number, timestamp))
        
        # Store OTP message
        db.execute('''INSERT INTO otp_messages 
                      (number, message, otp_code, timestamp, received_date, 
                       country, country_flag, message_id)
                      VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                   (number, text, otp_code, timestamp, timestamp, 
                    country_name, country_flag, message.message_id))
        
        logger.info(f"Stored OTP message for number {number} with OTP: {otp_code}")
        
        # Try to process immediately for faster response
        try:
            assignment = db.fetchone('''SELECT user_id FROM number_assignments 
                                        WHERE number = ? AND is_active = 1''', (number,))
            
            if assignment:
                user_id = assignment['user_id']
                logger.info(f"Found assignment for user {user_id}")
                
                # Format message with the new format
                formatted_msg, otp_code, flag, country = format_otp_message(
                    number, text, timestamp, False, get_user_balance(user_id)
                )
                
                # Add thanks button
                markup = types.InlineKeyboardMarkup()
                thanks_btn = types.InlineKeyboardButton("🌺 Thanks For Using Our Bot", callback_data="thanks")
                markup.add(thanks_btn)
                
                # Send to user
                try:
                    bot.send_message(user_id, formatted_msg, reply_markup=markup, parse_mode='Markdown')
                    
                    # Increment message count
                    increment_user_message_count(user_id)
                    
                    # Add revenue
                    revenue = get_setting('revenue_per_message') or 0.005
                    if revenue:
                        add_revenue_to_user(user_id, revenue)
                        
                        # Update assignment stats
                        db.execute('''UPDATE number_assignments 
                                      SET otp_count = otp_count + 1, 
                                          total_revenue = total_revenue + ?,
                                          last_otp_date = ?
                                      WHERE number = ? AND user_id = ?''',
                                   (revenue, timestamp, number, user_id))
                    
                    # Mark as processed
                    db.execute('''UPDATE otp_messages 
                                  SET forwarded_to = ?, revenue_added = 1, processed = 1 
                                  WHERE message_id = ?''', (user_id, message.message_id))
                    
                    logger.info(f"Forwarded OTP to user {user_id}")
                    
                    # Try to delete from group
                    try:
                        if MONITORED_GROUP_ID:
                            bot.delete_message(MONITORED_GROUP_ID, message.message_id)
                            logger.info(f"Deleted message {message.message_id} from group")
                    except Exception as e:
                        logger.error(f"Could not delete message from group: {e}")
                        
                except Exception as e:
                    logger.error(f"Error sending to user {user_id}: {e}")
            else:
                logger.info(f"No active assignment found for number {number}")
                
        except Exception as e:
            logger.error(f"Error in immediate processing: {e}")
            logger.error(traceback.format_exc())
        
    except Exception as e:
        logger.error(f"Error processing group message: {e}")
        logger.error(traceback.format_exc())

# Admin commands
@bot.message_handler(commands=['panel'])
def admin_panel_command(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "❌ Access denied!")
        return
    
    show_admin_panel(message.chat.id)

@bot.message_handler(commands=['push'])
def disable_bot(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "❌ Access denied!")
        return
    
    update_setting('bot_enabled', 0)
    bot.reply_to(message, "✅ Bot has been disabled for all users.")

@bot.message_handler(commands=['on'])
def enable_bot(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "❌ Access denied!")
        return
    
    update_setting('bot_enabled', 1)
    
    # Notify all users
    users = db.fetchall("SELECT user_id FROM users WHERE is_banned = 0")
    notified = 0
    
    for user in users:
        try:
            bot.send_message(user['user_id'], "✅ Bot is now back online!")
            notified += 1
            time.sleep(0.1)
        except:
            continue
    
    bot.reply_to(message, f"✅ Bot enabled. Notified {notified} users.")

@bot.message_handler(commands=['reset'])
def reset_assignments(message):
    try:
        user_id = message.from_user.id
        
        # Get user's active assignments
        assignments = db.fetchall('''SELECT na.number, n.country_code 
                                     FROM number_assignments na
                                     LEFT JOIN numbers n ON na.number = n.number
                                     WHERE na.user_id = ? AND na.is_active = 1''', (user_id,))
        
        if not assignments:
            bot.reply_to(message, "❌ No active assignments to reset.")
            return
        
        # Process each assignment
        reset_count = 0
        for assign in assignments:
            try:
                # IMPORTANT: Delete the number COMPLETELY from database
                # This ensures the number will never be assigned to anyone again
                
                # 1. Delete from numbers table
                db.execute("DELETE FROM numbers WHERE number = ?", (assign['number'],))
                
                # 2. Delete from number_assignments table
                db.execute("DELETE FROM number_assignments WHERE number = ? AND user_id = ?",
                           (assign['number'], user_id))
                
                # 3. Add to reset history
                reset_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                db.execute('''INSERT INTO reset_history (user_id, number, country_code, reset_date, reset_type)
                              VALUES (?, ?, ?, ?, ?)''',
                           (user_id, assign['number'], assign['country_code'], reset_date, 'user'))
                
                # 4. Update country stats - decrement BOTH total and used numbers
                if assign['country_code']:
                    # First get current stats
                    country = db.fetchone('''SELECT total_numbers, used_numbers 
                                            FROM countries WHERE code = ?''', 
                                          (assign['country_code'],))
                    if country:
                        total = (country['total_numbers'] or 1) - 1
                        used = (country['used_numbers'] or 1) - 1
                        
                        # Ensure values don't go negative
                        total = max(0, total)
                        used = max(0, used)
                        
                        db.execute('''UPDATE countries 
                                      SET total_numbers = ?, used_numbers = ? 
                                      WHERE code = ?''', 
                                   (total, used, assign['country_code']))
                
                reset_count += 1
                
                # 5. Clean up any OTP messages for this number
                db.execute("DELETE FROM otp_messages WHERE number = ?", (assign['number'],))
                db.execute("DELETE FROM message_tracking WHERE number = ?", (assign['number'],))
                
            except Exception as e:
                logger.error(f"Error resetting number {assign['number']}: {e}")
        
        bot.reply_to(message, f"✅ Reset {reset_count} number assignments. These numbers have been PERMANENTLY removed from the database and will never be assigned to anyone again.")
    except Exception as e:
        logger.error(f"Error in reset_assignments: {e}")
        bot.reply_to(message, "❌ An error occurred. Please try again.")

@bot.message_handler(commands=['ban'])
def ban_user_command(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "❌ Access denied!")
        return
    
    try:
        target_id = int(message.text.split()[1])
        db.execute("UPDATE users SET is_banned = 1 WHERE user_id = ?", (target_id,))
        bot.reply_to(message, f"✅ User {target_id} has been banned.")
    except:
        bot.reply_to(message, "Usage: /ban [user_id]")

@bot.message_handler(commands=['unban'])
def unban_user_command(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "❌ Access denied!")
        return
    
    try:
        target_id = int(message.text.split()[1])
        db.execute("UPDATE users SET is_banned = 0 WHERE user_id = ?", (target_id,))
        bot.reply_to(message, f"✅ User {target_id} has been unbanned.")
    except:
        bot.reply_to(message, "Usage: /unban [user_id]")

@bot.message_handler(commands=['setstartmsg'])
def set_start_message(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "❌ Access denied!")
        return
    
    if message.reply_to_message:
        if message.reply_to_message.text:
            update_setting('start_message', message.reply_to_message.text)
            update_setting('start_message_type', 'text')
            bot.reply_to(message, "✅ Start message set (text).")
        
        elif message.reply_to_message.photo:
            update_setting('start_message', message.reply_to_message.photo[-1].file_id)
            update_setting('start_message_type', 'photo')
            bot.reply_to(message, "✅ Start message set (photo).")
        
        elif message.reply_to_message.video:
            update_setting('start_message', message.reply_to_message.video.file_id)
            update_setting('start_message_type', 'video')
            bot.reply_to(message, "✅ Start message set (video).")
        
        elif message.reply_to_message.document:
            update_setting('start_message', message.reply_to_message.document.file_id)
            update_setting('start_message_type', 'document')
            bot.reply_to(message, "✅ Start message set (document).")
    else:
        bot.reply_to(message, "Reply to a message with /setstartmsg to set it as start message.")

@bot.message_handler(commands=['addbalance'])
def add_balance_command(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "❌ Access denied!")
        return
    
    try:
        parts = message.text.split()
        target_id = int(parts[1])
        amount = float(parts[2])
        
        new_balance = update_user_balance(target_id, amount)
        bot.reply_to(message, f"✅ Added ${amount:.3f} to user {target_id}. New balance: ${new_balance:.3f}")
    except:
        bot.reply_to(message, "Usage: /addbalance [user_id] [amount]")

@bot.message_handler(commands=['removebalance'])
def remove_balance_command(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "❌ Access denied!")
        return
    
    try:
        parts = message.text.split()
        target_id = int(parts[1])
        amount = float(parts[2])
        
        current = get_user_balance(target_id)
        if current < amount:
            bot.reply_to(message, f"❌ User only has ${current:.3f}")
            return
        
        new_balance = update_user_balance(target_id, -amount)
        bot.reply_to(message, f"✅ Removed ${amount:.3f} from user {target_id}. New balance: ${new_balance:.3f}")
    except:
        bot.reply_to(message, "Usage: /removebalance [user_id] [amount]")

@bot.message_handler(commands=['setmaxnumbers'])
def set_max_numbers_command(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "❌ Access denied!")
        return
    
    try:
        parts = message.text.split()
        max_num = int(parts[1])
        update_setting('max_user_numbers', max_num)
        bot.reply_to(message, f"✅ Maximum numbers per user set to {max_num}")
    except:
        bot.reply_to(message, "Usage: /setmaxnumbers [number]")

# Start OTP processing thread
otp_processor_thread = threading.Thread(target=start_otp_processor, daemon=True)
otp_processor_thread.start()

# Database cleanup function
def cleanup_database():
    """Clean up database - remove orphaned records and fix inconsistencies"""
    try:
        # 1. Delete numbers that are marked as used but have no active assignment
        db.execute('''DELETE FROM numbers 
                      WHERE is_used = 1 
                      AND number NOT IN (SELECT number FROM number_assignments WHERE is_active = 1)''')
        
        # 2. Update country stats based on actual numbers
        countries = db.fetchall('''SELECT code FROM countries''')
        for country in countries:
            code = country['code']
            
            # Get actual counts from numbers table
            actual_stats = db.fetchone('''SELECT 
                                            COUNT(*) as total,
                                            SUM(CASE WHEN is_used = 1 THEN 1 ELSE 0 END) as used
                                          FROM numbers 
                                          WHERE country_code = ?''', (code,))
            
            if actual_stats:
                total = actual_stats['total'] or 0
                used = actual_stats['used'] or 0
                
                db.execute('''UPDATE countries 
                              SET total_numbers = ?, used_numbers = ? 
                              WHERE code = ?''', (total, used, code))
        
        # 3. Clean old OTP messages (older than 1 hour)
        cutoff = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        db.execute("DELETE FROM otp_messages WHERE timestamp < ?", (cutoff,))
        
        # 4. Clean old message tracking (older than 1 day)
        cutoff = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        db.execute("DELETE FROM message_tracking WHERE processed_date < ?", (cutoff,))
        
        return True
    except Exception as e:
        logger.error(f"Error in cleanup_database: {e}")
        return False

# Main bot loop
if __name__ == "__main__":
    print("🤖 Bot is starting...")
    print("🧹 Cleaning up database...")
    cleanup_database()
    print("✅ Database cleanup completed!")
    print(f"📊 Monitoring group: {MONITORED_GROUP_ID}")
    print(f"👑 Admins: {ADMIN_IDS}")
    print(f"📣 Withdrawal Log Channel: {WITHDRAW_LOG_CHANNEL}")
    print(f"🔗 OTP Group: {OTP_GROUP_LINK}")
    print("✅ All features enabled:")
    print("   1. ✅ Permanent number deletion on /reset")
    print("   2. ✅ Admin panel with reset system")
    print("   3. ✅ Numbers report generator (TXT file)")
    print("   4. ✅ Each number shows: Number + OTP count + User ID + DateTime")
    print("   5. ✅ No duplicate number assignment")
    print("   6. ✅ Accurate statistics")
    print("   7. ✅ IMPROVED: Better number detection from files")
    print("   8. ✅ NEW: Reset All Assignment Number feature with country selection")
    
    try:
        bot.infinity_polling(timeout=60, long_polling_timeout=30)
    except Exception as e:
        logger.error(f"Bot error: {e}")
        time.sleep(5)
        bot.infinity_polling(timeout=60, long_polling_timeout=30)
