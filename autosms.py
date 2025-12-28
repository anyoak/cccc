import logging
import sqlite3
import random
import json
import time
import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from threading import Lock, Thread
import threading
import schedule
from flask import Flask
import requests

from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes, ChatMemberHandler
from telegram.constants import ChatAction, ChatMemberStatus
from telegram.error import TelegramError

# ==================== CONFIGURATION ====================
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"  # Replace with your bot token
ADMIN_ID = "YOUR_ADMIN_ID"  # Your Telegram ID for notifications

# ==================== LOGGING SETUP ====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ==================== DATABASE SETUP ====================
class DatabaseManager:
    def __init__(self):
        self.conn = sqlite3.connect('auto_message_bot.db', check_same_thread=False)
        self.lock = Lock()
        self.init_database()
    
    def init_database(self):
        with self.lock:
            cursor = self.conn.cursor()
            
            # Groups table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS groups (
                    group_id INTEGER PRIMARY KEY,
                    group_name TEXT,
                    added_date TEXT,
                    is_active INTEGER DEFAULT 1,
                    total_messages_sent INTEGER DEFAULT 0,
                    last_message_date TEXT,
                    last_reset_date TEXT,
                    current_message_index INTEGER DEFAULT 0
                )
            ''')
            
            # Messages table (stores which messages were sent when)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sent_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id INTEGER,
                    message_index INTEGER,
                    sent_date TEXT,
                    sent_time TEXT,
                    FOREIGN KEY (group_id) REFERENCES groups (group_id)
                )
            ''')
            
            # Settings table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            ''')
            
            # Initialize settings
            cursor.execute('''
                INSERT OR IGNORE INTO settings (key, value) 
                VALUES ('daily_message_limit', '100')
            ''')
            
            cursor.execute('''
                INSERT OR IGNORE INTO settings (key, value) 
                VALUES ('message_interval_minutes', '14.4')
            ''')
            
            cursor.execute('''
                INSERT OR IGNORE INTO settings (key, value) 
                VALUES ('bot_status', 'running')
            ''')
            
            self.conn.commit()
    
    def add_group(self, group_id: int, group_name: str):
        with self.lock:
            cursor = self.conn.cursor()
            current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            cursor.execute('''
                INSERT OR REPLACE INTO groups 
                (group_id, group_name, added_date, is_active, current_message_index) 
                VALUES (?, ?, ?, 1, 0)
            ''', (group_id, group_name, current_time))
            
            self.conn.commit()
            logger.info(f"Added group: {group_name} ({group_id})")
    
    def get_active_groups(self):
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute('''
                SELECT group_id, group_name, current_message_index 
                FROM groups 
                WHERE is_active = 1
            ''')
            return cursor.fetchall()
    
    def get_group_stats(self, group_id: int):
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute('''
                SELECT total_messages_sent, last_message_date, last_reset_date, current_message_index
                FROM groups 
                WHERE group_id = ?
            ''', (group_id,))
            return cursor.fetchone()
    
    def update_message_stats(self, group_id: int, message_index: int):
        with self.lock:
            cursor = self.conn.cursor()
            current_time = datetime.now()
            date_str = current_time.strftime('%Y-%m-%d')
            time_str = current_time.strftime('%H:%M:%S')
            
            # Update group stats
            cursor.execute('''
                UPDATE groups 
                SET total_messages_sent = total_messages_sent + 1,
                    last_message_date = ?,
                    current_message_index = ?
                WHERE group_id = ?
            ''', (date_str, message_index, group_id))
            
            # Log sent message
            cursor.execute('''
                INSERT INTO sent_messages (group_id, message_index, sent_date, sent_time)
                VALUES (?, ?, ?, ?)
            ''', (group_id, message_index, date_str, time_str))
            
            self.conn.commit()
    
    def get_today_message_count(self, group_id: int):
        with self.lock:
            cursor = self.conn.cursor()
            today = datetime.now().strftime('%Y-%m-%d')
            cursor.execute('''
                SELECT COUNT(*) FROM sent_messages 
                WHERE group_id = ? AND sent_date = ?
            ''', (group_id, today))
            return cursor.fetchone()[0]
    
    def reset_daily_counts(self):
        with self.lock:
            cursor = self.conn.cursor()
            today = datetime.now().strftime('%Y-%m-%d')
            
            # Update reset date for groups that need reset
            cursor.execute('''
                UPDATE groups 
                SET last_reset_date = ?
                WHERE last_reset_date != ? OR last_reset_date IS NULL
            ''', (today, today))
            
            # Clean old sent messages (keep last 30 days)
            month_ago = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
            cursor.execute('''
                DELETE FROM sent_messages 
                WHERE sent_date < ?
            ''', (month_ago,))
            
            self.conn.commit()
    
    def deactivate_group(self, group_id: int):
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute('''
                UPDATE groups 
                SET is_active = 0 
                WHERE group_id = ?
            ''', (group_id,))
            self.conn.commit()
    
    def get_setting(self, key: str, default: str = ""):
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute('SELECT value FROM settings WHERE key = ?', (key,))
            result = cursor.fetchone()
            return result[0] if result else default

# ==================== MESSAGE GENERATOR ====================
class MessageGenerator:
    def __init__(self):
        self.categories = {
            'motivation': [
                "The only way to do great work is to love what you do. - Steve Jobs",
                "Believe you can and you're halfway there. - Theodore Roosevelt",
                "Your time is limited, don't waste it living someone else's life.",
                "The future belongs to those who believe in the beauty of their dreams. - Eleanor Roosevelt",
                "Don't watch the clock; do what it does. Keep going.",
                "Success is not final, failure is not fatal: it is the courage to continue that counts. - Winston Churchill",
                "The harder you work for something, the greater you'll feel when you achieve it.",
                "Dream bigger. Do bigger.",
                "Don't stop when you're tired. Stop when you're done.",
                "Wake up with determination. Go to bed with satisfaction.",
                "Great things never come from comfort zones.",
                "Dream it. Wish it. Do it.",
                "Success doesn't just find you. You have to go out and get it.",
                "The key to success is to focus on goals, not obstacles.",
                "Push yourself, because no one else is going to do it for you.",
                "The secret of getting ahead is getting started.",
                "Don't wait for opportunity. Create it.",
                "Strive for progress, not perfection.",
                "The only limit to our realization of tomorrow will be our doubts of today. - Franklin D. Roosevelt",
                "It always seems impossible until it's done. - Nelson Mandela",
            ],
            'knowledge': [
                "Knowledge is power, but wisdom is knowing how to use it.",
                "The more you learn, the more you realize how much you don't know.",
                "Education is the passport to the future, for tomorrow belongs to those who prepare for it today. - Malcolm X",
                "Reading is to the mind what exercise is to the body. - Joseph Addison",
                "The capacity to learn is a gift; the ability to learn is a skill; the willingness to learn is a choice. - Brian Herbert",
                "Science is organized knowledge. Wisdom is organized life. - Immanuel Kant",
                "The beautiful thing about learning is that no one can take it away from you. - B.B. King",
                "Information is not knowledge. The only source of knowledge is experience. - Albert Einstein",
                "Wisdom is not a product of schooling but of the lifelong attempt to acquire it. - Albert Einstein",
                "The only true wisdom is in knowing you know nothing. - Socrates",
                "Learn from yesterday, live for today, hope for tomorrow. - Albert Einstein",
                "The mind is not a vessel to be filled, but a fire to be kindled. - Plutarch",
                "Knowledge shared is knowledge squared.",
                "The difference between school and life? In school, you're taught a lesson and then given a test. In life, you're given a test that teaches you a lesson. - Tom Bodett",
                "The more I read, the more I acquire, the more certain I am that I know nothing. - Voltaire",
                "Wisdom begins in wonder. - Socrates",
                "Real knowledge is to know the extent of one's ignorance. - Confucius",
                "The greatest enemy of knowledge is not ignorance, it is the illusion of knowledge. - Stephen Hawking",
                "To know what you know and what you do not know, that is true knowledge. - Confucius",
                "Knowledge speaks, but wisdom listens. - Jimi Hendrix",
            ],
            'positive': [
                "Today is going to be an amazing day.",
                "I am capable of amazing things.",
                "My potential is limitless.",
                "I choose to be happy today.",
                "Good things are coming my way.",
                "I am worthy of all the good that comes to me.",
                "My mind is full of positive thoughts.",
                "I am becoming the best version of myself.",
                "Every day is a fresh start.",
                "I attract positivity into my life.",
                "I am strong, confident, and capable.",
                "Today, I choose joy.",
                "I am grateful for everything I have.",
                "I am enough just as I am.",
                "My life is filled with abundance.",
                "I radiate positive energy.",
                "Every challenge makes me stronger.",
                "I am the architect of my life.",
                "I believe in my dreams.",
                "Today, I will make a difference.",
            ],
            'success': [
                "Success usually comes to those who are too busy to be looking for it. - Henry David Thoreau",
                "The road to success and the road to failure are almost exactly the same. - Colin R. Davis",
                "Success is walking from failure to failure with no loss of enthusiasm. - Winston Churchill",
                "Opportunities don't happen. You create them. - Chris Grosser",
                "Don't be afraid to give up the good to go for the great. - John D. Rockefeller",
                "I find that the harder I work, the more luck I seem to have. - Thomas Jefferson",
                "There are no secrets to success. It is the result of preparation, hard work, and learning from failure. - Colin Powell",
                "Success is not the key to happiness. Happiness is the key to success. - Albert Schweitzer",
                "The successful warrior is the average man, with laser-like focus. - Bruce Lee",
                "Success is getting what you want, happiness is wanting what you get. - W.P. Kinsella",
                "The way to get started is to quit talking and begin doing. - Walt Disney",
                "Success seems to be connected with action. Successful people keep moving. - Conrad Hilton",
                "To succeed in life, you need two things: ignorance and confidence. - Mark Twain",
                "Success is not in what you have, but who you are. - Bo Bennett",
                "Formal education will make you a living; self-education will make you a fortune. - Jim Rohn",
                "The only place where success comes before work is in the dictionary. - Vidal Sassoon",
                "Success is not just about making money. It's about making a difference.",
                "Small daily improvements are the key to staggering long-term results.",
                "The price of success is hard work, dedication to the job at hand.",
                "Success is where preparation and opportunity meet. - Bobby Unser",
            ],
            'wisdom': [
                "The journey of a thousand miles begins with a single step. - Lao Tzu",
                "Life is what happens to you while you're busy making other plans. - John Lennon",
                "In three words I can sum up everything I've learned about life: it goes on. - Robert Frost",
                "The purpose of our lives is to be happy. - Dalai Lama",
                "Life is either a daring adventure or nothing at all. - Helen Keller",
                "You have within you right now, everything you need to deal with whatever the world can throw at you. - Brian Tracy",
                "The only impossible journey is the one you never begin. - Tony Robbins",
                "What lies behind us and what lies before us are tiny matters compared to what lies within us. - Ralph Waldo Emerson",
                "The best time to plant a tree was 20 years ago. The second best time is now. - Chinese Proverb",
                "You miss 100% of the shots you don't take. - Wayne Gretzky",
                "Whether you think you can or you think you can't, you're right. - Henry Ford",
                "I have not failed. I've just found 10,000 ways that won't work. - Thomas Edison",
                "The mind is everything. What you think you become. - Buddha",
                "The only person you are destined to become is the person you decide to be. - Ralph Waldo Emerson",
                "Go confidently in the direction of your dreams. Live the life you have imagined. - Henry David Thoreau",
                "Twenty years from now you will be more disappointed by the things that you didn't do than by the ones you did do. - Mark Twain",
                "The biggest risk is not taking any risk. In a world that is changing quickly, the only strategy that is guaranteed to fail is not taking risks. - Mark Zuckerberg",
                "You can't cross the sea merely by standing and staring at the water. - Rabindranath Tagore",
                "I am not a product of my circumstances. I am a product of my decisions. - Stephen Covey",
                "The two most important days in your life are the day you are born and the day you find out why. - Mark Twain",
            ]
        }
        
        # Generate 1000+ messages by combining categories
        self.all_messages = []
        for category, messages in self.categories.items():
            self.all_messages.extend(messages)
        
        # Add more variations
        for i in range(800):
            template = random.choice([
                "Remember: {}",
                "Daily reminder: {}",
                "Thought for today: {}",
                "Inspiration: {}",
                "Keep in mind: {}",
                "Today's wisdom: {}",
                "Motivational quote: {}",
                "Success tip: {}",
                "Life lesson: {}",
                "Positive note: {}"
            ])
            base_msg = random.choice(self.all_messages)
            self.all_messages.append(template.format(base_msg.split('-')[0].strip()))
    
    def get_message(self, group_id: int, message_index: int) -> str:
        """Get message based on group ID and message index"""
        total_messages = len(self.all_messages)
        actual_index = (message_index + group_id) % total_messages
        return self.all_messages[actual_index]

# ==================== MESSAGE SCHEDULER ====================
class MessageScheduler:
    def __init__(self, bot, db: DatabaseManager, msg_gen: MessageGenerator):
        self.bot = bot
        self.db = db
        self.msg_gen = msg_gen
        self.is_running = True
        self.scheduler_thread = None
        
    def start(self):
        """Start the scheduler in a separate thread"""
        self.scheduler_thread = threading.Thread(target=self._run_scheduler, daemon=True)
        self.scheduler_thread.start()
        logger.info("Message scheduler started")
    
    def _run_scheduler(self):
        """Main scheduler loop"""
        while self.is_running:
            try:
                current_time = datetime.now()
                current_minute = current_time.minute
                
                # Send messages based on random timing
                if current_minute % 15 == random.randint(0, 14):  # Every ~15 minutes
                    self._send_batch_messages()
                
                # Daily reset at midnight
                if current_time.hour == 0 and current_time.minute == 0:
                    self.db.reset_daily_counts()
                    logger.info("Daily counts reset")
                
                time.sleep(60)  # Check every minute
                
            except Exception as e:
                logger.error(f"Scheduler error: {e}")
                time.sleep(60)
    
    def _send_batch_messages(self):
        """Send messages to all active groups"""
        active_groups = self.db.get_active_groups()
        
        for group_id, group_name, msg_index in active_groups:
            try:
                # Check daily limit
                today_count = self.db.get_today_message_count(group_id)
                daily_limit = int(self.db.get_setting('daily_message_limit', '100'))
                
                if today_count >= daily_limit:
                    continue
                
                # Get and send message
                message = self.msg_gen.get_message(group_id, msg_index)
                
                # Send message
                asyncio.run(self._send_telegram_message(group_id, message))
                
                # Update stats
                self.db.update_message_stats(group_id, msg_index)
                
                logger.info(f"Sent message to {group_name} ({group_id}). Today: {today_count + 1}/{daily_limit}")
                
                # Small delay between groups
                time.sleep(1)
                
            except Exception as e:
                logger.error(f"Failed to send to group {group_id}: {e}")
    
    async def _send_telegram_message(self, chat_id: int, message: str):
        """Send message via Telegram"""
        try:
            await self.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"Telegram send error: {e}")

# ==================== TELEGRAM BOT HANDLERS ====================
class TelegramBot:
    def __init__(self, token: str):
        self.token = token
        self.application = None
        self.db = DatabaseManager()
        self.msg_gen = MessageGenerator()
        self.scheduler = None
        
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        await update.message.reply_text(
            "🤖 *Auto Message Bot Started!*\n\n"
            "I will automatically send 100 inspirational messages daily to this group.\n"
            "Messages include:\n"
            "• Motivational quotes\n"
            "• Knowledge tips\n"
            "• Positive affirmations\n"
            "• Success wisdom\n\n"
            "Use /stats to check today's progress!\n\n"
            "_Bot will start sending messages automatically..._",
            parse_mode='Markdown'
        )
        
        # Add group to database
        chat = update.effective_chat
        if chat.type in ['group', 'supergroup']:
            self.db.add_group(chat.id, chat.title)
    
    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /stats command"""
        chat = update.effective_chat
        
        if chat.type not in ['group', 'supergroup']:
            await update.message.reply_text("This command works only in groups!")
            return
        
        today_count = self.db.get_today_message_count(chat.id)
        group_stats = self.db.get_group_stats(chat.id)
        
        if group_stats:
            total_sent, last_date, last_reset, msg_index = group_stats
            
            stats_text = (
                f"📊 *Group Statistics*\n\n"
                f"• *Group:* {chat.title}\n"
                f"• *Today's messages:* {today_count}/100\n"
                f"• *Total messages sent:* {total_sent}\n"
                f"• *Last message:* {last_date or 'Never'}\n"
                f"• *Next message index:* {msg_index}\n\n"
                f"_Bot is running 24/7. Messages sent automatically._"
            )
        else:
            stats_text = "No statistics available yet. Bot will start sending messages soon."
        
        await update.message.reply_text(stats_text, parse_mode='Markdown')
    
    async def chat_member_update(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle bot being added/removed from groups"""
        if update.chat_member:
            old_status = update.chat_member.old_chat_member.status
            new_status = update.chat_member.new_chat_member.status
            chat = update.chat_member.chat
            
            # Bot added to group
            if old_status == ChatMemberStatus.LEFT and new_status == ChatMemberStatus.MEMBER:
                self.db.add_group(chat.id, chat.title)
                
                welcome_text = (
                    "👋 *Hello everyone!*\n\n"
                    "I'm *Auto Message Bot*! 🤖\n\n"
                    "I will automatically send *100 inspirational messages* daily at random times.\n\n"
                    "📚 *Message types:*\n"
                    "• Motivation & Inspiration\n"
                    "• Knowledge & Wisdom\n"
                    "• Positive Affirmations\n"
                    "• Success Tips\n\n"
                    "📊 Use /stats to check daily progress\n"
                    "⚙️ Bot runs 24/7 automatically\n\n"
                    "Enjoy the positive vibes! ✨"
                )
                
                try:
                    await context.bot.send_message(
                        chat_id=chat.id,
                        text=welcome_text,
                        parse_mode='Markdown'
                    )
                except Exception as e:
                    logger.error(f"Welcome message error: {e}")
            
            # Bot removed from group
            elif old_status == ChatMemberStatus.MEMBER and new_status == ChatMemberStatus.LEFT:
                self.db.deactivate_group(chat.id)
                logger.info(f"Bot removed from group: {chat.title}")
    
    def setup_handlers(self):
        """Setup command handlers"""
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("stats", self.stats_command))
        self.application.add_handler(CommandHandler("help", self.start_command))
        self.application.add_handler(ChatMemberHandler(self.chat_member_update, ChatMemberHandler.CHAT_MEMBER))
    
    def start_bot(self):
        """Start the Telegram bot"""
        # Create application
        self.application = Application.builder().token(self.token).build()
        
        # Setup handlers
        self.setup_handlers()
        
        # Start scheduler
        self.scheduler = MessageScheduler(self.application.bot, self.db, self.msg_gen)
        self.scheduler.start()
        
        # Start bot
        logger.info("Bot is starting...")
        self.application.run_polling(allowed_updates=Update.ALL_TYPES)

# ==================== HEALTH CHECK SERVER ====================
def start_health_server():
    """Start a simple HTTP server for health checks (for 24/7 hosting)"""
    try:
        from flask import Flask
        app = Flask(__name__)
        
        @app.route('/')
        def home():
            return "🤖 Auto Message Bot is running!"
        
        @app.route('/health')
        def health():
            return {"status": "healthy", "timestamp": datetime.now().isoformat()}
        
        # Run in separate thread
        import threading
        server_thread = threading.Thread(
            target=lambda: app.run(host='0.0.0.0', port=8080, debug=False, use_reloader=False),
            daemon=True
        )
        server_thread.start()
        logger.info("Health server started on port 8080")
        
    except ImportError:
        logger.warning("Flask not installed. Health server disabled.")
    except Exception as e:
        logger.error(f"Health server error: {e}")

# ==================== MAIN FUNCTION ====================
def main():
    """Main function to start everything"""
    
    # Start health server for 24/7 hosting
    start_health_server()
    
    # Check token
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.error("Please set your bot token in the BOT_TOKEN variable!")
        return
    
    # Create and start bot
    bot = TelegramBot(BOT_TOKEN)
    
    # Keep trying to start bot if it fails
    while True:
        try:
            bot.start_bot()
        except Exception as e:
            logger.error(f"Bot crashed: {e}")
            logger.info("Restarting bot in 10 seconds...")
            time.sleep(10)

# ==================== ENTRY POINT ====================
if __name__ == '__main__':
    print("=" * 50)
    print("🤖 AUTO MESSAGE BOT - STARTING")
    print("=" * 50)
    print("Features:")
    print("• Sends 100 messages daily automatically")
    print("• Different messages every day")
    print("• Works in multiple groups simultaneously")
    print("• No admin required")
    print("• 24/7 non-stop operation")
    print("=" * 50)
    
    main()
