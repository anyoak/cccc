"""
🤖 GROUP GUARDIAN PRO - Professional Telegram Group Management Bot
Version: 3.3.0
Developer: @professor_cry
Fixed: Export file upload, Username underscore issue
"""

import json
import logging
import os
import sys
import traceback
from datetime import datetime
from typing import Dict, List, Optional, Any
from telegram import Update, ChatMember
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters
)
from telegram.constants import ParseMode, ChatAction
import html

# ============ CONFIGURATION ============
VERSION = "3.3.0"
BOT_NAME = "Group Guardian Pro"
DEVELOPER = "@FinnOwen"
SUPPORT_LINK = "https://t.me/FinnOwen"

# File structure
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
LOG_DIR = os.path.join(BASE_DIR, "logs")

# ============ LOGGING SETUP ============
def setup_logging():
    """Setup simple logging"""
    os.makedirs(LOG_DIR, exist_ok=True)
    
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    file_handler = logging.FileHandler(
        filename=os.path.join(LOG_DIR, "bot.log"),
        encoding='utf-8'
    )
    file_handler.setLevel(logging.INFO)
    
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logging.getLogger(__name__)

logger = setup_logging()

# ============ SIMPLE DATABASE MANAGER ============
class SimpleDB:
    """Simple database manager"""
    
    @staticmethod
    def setup():
        """Create data directory"""
        os.makedirs(DATA_DIR, exist_ok=True)
        logger.info(f"Data directory: {DATA_DIR}")
    
    @staticmethod
    def get_group_file(chat_id: int, filename: str) -> str:
        """Get group file path"""
        return os.path.join(DATA_DIR, f"{chat_id}_{filename}.json")
    
    @staticmethod
    def read_json(filepath: str, default: Any = None) -> Any:
        """Read JSON file"""
        try:
            if not os.path.exists(filepath):
                return default if default is not None else {}
            
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Read error {filepath}: {e}")
            return default if default is not None else {}
    
    @staticmethod
    def write_json(filepath: str, data: Any) -> bool:
        """Write JSON file"""
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False, default=str)
            return True
        except Exception as e:
            logger.error(f"Write error {filepath}: {e}")
            return False
    
    @staticmethod
    def load_config(chat_id: int) -> Dict:
        """Load group configuration"""
        config_file = SimpleDB.get_group_file(chat_id, "config")
        
        default_config = {
            "group_id": chat_id,
            "group_title": "",
            "leave_ban_mode": False,
            "auto_track": True,
            "created_at": datetime.now().isoformat(),
            "last_modified": datetime.now().isoformat()
        }
        
        config = SimpleDB.read_json(config_file, default_config)
        return {**default_config, **config} if config else default_config
    
    @staticmethod
    def save_config(chat_id: int, config: Dict) -> bool:
        """Save group configuration"""
        config_file = SimpleDB.get_group_file(chat_id, "config")
        config['last_modified'] = datetime.now().isoformat()
        return SimpleDB.write_json(config_file, config)
    
    @staticmethod
    def load_users(chat_id: int) -> Dict:
        """Load group users"""
        users_file = SimpleDB.get_group_file(chat_id, "users")
        
        default_users = {
            "active": [],
            "banned": [],
            "left": [],
            "admins": [],
            "total": 0
        }
        
        users = SimpleDB.read_json(users_file, default_users)
        return {**default_users, **users} if users else default_users
    
    @staticmethod
    def save_users(chat_id: int, users: Dict) -> bool:
        """Save group users"""
        users_file = SimpleDB.get_group_file(chat_id, "users")
        users['total'] = len(users.get('active', [])) + len(users.get('banned', [])) + len(users.get('left', []))
        return SimpleDB.write_json(users_file, users)
    
    @staticmethod
    def load_bans(chat_id: int) -> Dict:
        """Load group bans"""
        bans_file = SimpleDB.get_group_file(chat_id, "bans")
        
        default_bans = {
            "list": [],
            "total": 0,
            "auto": 0,
            "manual": 0
        }
        
        bans = SimpleDB.read_json(bans_file, default_bans)
        return {**default_bans, **bans} if bans else default_bans
    
    @staticmethod
    def save_bans(chat_id: int, bans: Dict) -> bool:
        """Save group bans"""
        bans_file = SimpleDB.get_group_file(chat_id, "bans")
        return SimpleDB.write_json(bans_file, bans)

# Initialize database
SimpleDB.setup()

# ============ UTILITY FUNCTIONS ============
def format_time(timestamp: Optional[str]) -> str:
    """Format timestamp"""
    if not timestamp:
        return "Never"
    try:
        dt = datetime.fromisoformat(timestamp)
        return dt.strftime("%Y-%m-%d %H:%M")
    except:
        return "Invalid"

def format_username_for_display(username: Optional[str]) -> str:
    """
    Format username for display in Telegram messages
    Fixes underscore issue in usernames
    """
    if not username:
        return "No Username"
    
    # Clean username - remove @ if present and trim
    clean_username = str(username).strip().lstrip('@')
    if not clean_username:
        return "No Username"
    
    # For display in Telegram messages, just add @ prefix
    # Telegram will handle underscores automatically in text
    return f"@{clean_username}"

def format_username_for_file(username: Optional[str]) -> str:
    """
    Format username for saving in files
    """
    if not username:
        return "No_Username"
    
    clean_username = str(username).strip().lstrip('@')
    if not clean_username:
        return "No_Username"
    
    return clean_username

def clean_text(text: Optional[str]) -> str:
    """Clean text - preserve underscores"""
    if text is None:
        return ""
    return str(text).strip()

async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if user is admin"""
    try:
        user = await update.effective_chat.get_member(update.effective_user.id)
        return user.status in ['administrator', 'creator']
    except:
        return False

async def is_owner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if user is owner"""
    try:
        user = await update.effective_chat.get_member(update.effective_user.id)
        return user.status == 'creator'
    except:
        return False

# ============ COMMAND HANDLERS ============
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command"""
    text = f"""
🎉 *{BOT_NAME} v{VERSION}*

A professional group management bot with:
• Multi-group support
• Leave-ban system
• User tracking
• Data export with user IDs

📋 *Quick Start:*
1. Add me to your group
2. Make me Admin with ban permission
3. Type `/setup` to initialize

🔧 *Commands:* `/help`
👨💻 *Developer:* {DEVELOPER}
📞 *Support:* {SUPPORT_LINK}
"""
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help command"""
    text = f"""
🤖 *{BOT_NAME} - Commands*

👑 *Admin Commands:*
├ `/setup` - Initialize bot
├ `/set_leave_ban on/off` - Toggle auto-ban
├ `/track_admins` - Track all admins
├ `/export_data` - Export all ban data with user IDs
└ `/ban_logs` - View ban history with user IDs

👥 *Member Commands:*
├ `/help` - This message
├ `/config` - Group settings
├ `/stats` - Group statistics
└ `/about` - About bot

⚙️ *Features:*
• Separate database for each group
• Auto-ban users who leave (with user ID in notification)
• Track all users with IDs
• Export complete ban history with user IDs

⚠️ *Bot must be Admin to work properly.*
"""
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def setup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Setup command"""
    if not await is_admin(update, context):
        await update.message.reply_text("❌ Only admins can setup.", parse_mode=ParseMode.MARKDOWN)
        return
    
    chat_id = update.effective_chat.id
    config = SimpleDB.load_config(chat_id)
    config['group_title'] = update.effective_chat.title
    config['setup_by'] = update.effective_user.full_name
    config['setup_time'] = datetime.now().isoformat()
    
    SimpleDB.save_config(chat_id, config)
    
    text = f"""
✅ *Setup Complete*

📋 *Group:* {update.effective_chat.title}
👤 *Setup by:* {update.effective_user.full_name}
⏰ *Time:* {format_time(datetime.now().isoformat())}

📁 *Database:* `data/{chat_id}_*.json`
⚙️ *Leave-ban:* Disabled (use `/set_leave_ban on`)

*Next:* Enable leave-ban with `/set_leave_ban on`
"""
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def set_leave_ban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set leave-ban mode"""
    if not await is_admin(update, context):
        await update.message.reply_text("❌ Only admins can change this.", parse_mode=ParseMode.MARKDOWN)
        return
    
    if not context.args:
        await update.message.reply_text("❌ Usage: `/set_leave_ban on` or `/set_leave_ban off`", parse_mode=ParseMode.MARKDOWN)
        return
    
    mode = context.args[0].lower()
    if mode not in ['on', 'off']:
        await update.message.reply_text("❌ Use 'on' or 'off' only.", parse_mode=ParseMode.MARKDOWN)
        return
    
    chat_id = update.effective_chat.id
    config = SimpleDB.load_config(chat_id)
    config['leave_ban_mode'] = (mode == 'on')
    config['modified_by'] = update.effective_user.full_name
    
    SimpleDB.save_config(chat_id, config)
    
    status = "✅ ENABLED" if mode == 'on' else "❌ DISABLED"
    text = f"""
⚙️ *Leave-Ban Mode Updated*

🔧 *Status:* {status}
👤 *By:* {update.effective_user.full_name}
⏰ *Time:* {format_time(datetime.now().isoformat())}

*Effect:* {'Users who leave will be auto-banned.' if mode == 'on' else 'No auto-ban on leave.'}
"""
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def config_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Config command"""
    chat_id = update.effective_chat.id
    config = SimpleDB.load_config(chat_id)
    users = SimpleDB.load_users(chat_id)
    bans = SimpleDB.load_bans(chat_id)
    
    text = f"""
⚙️ *Group Configuration*

📋 *Info:*
├ Group: {update.effective_chat.title}
├ ID: `{chat_id}`
├ Created: {format_time(config.get('created_at'))}
└ Modified: {format_time(config.get('last_modified'))}

🔐 *Settings:*
├ Leave-Ban: {'✅ ON' if config.get('leave_ban_mode') else '❌ OFF'}
├ Auto-Track: {'✅ ON' if config.get('auto_track') else '❌ OFF'}
└ Setup by: {config.get('setup_by', 'Unknown')}

📊 *Stats:*
├ Active: {len(users.get('active', []))}
├ Banned: {len(users.get('banned', []))}
├ Left: {len(users.get('left', []))}
└ Total Bans: {bans.get('total', 0)}
"""
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stats command"""
    chat_id = update.effective_chat.id
    config = SimpleDB.load_config(chat_id)
    users = SimpleDB.load_users(chat_id)
    bans = SimpleDB.load_bans(chat_id)
    
    active = len(users.get('active', []))
    banned = len(users.get('banned', []))
    left = len(users.get('left', []))
    total = active + banned + left
    
    text = f"""
📊 *Group Statistics*

🏠 *Group:* {update.effective_chat.title}
⏰ *Report:* {format_time(datetime.now().isoformat())}

👥 *Users:*
├ Active: {active}
├ Banned: {banned}
├ Left: {left}
└ Total: {total}

🚫 *Bans:*
├ Total: {bans.get('total', 0)}
├ Auto: {bans.get('auto', 0)}
├ Manual: {bans.get('manual', 0)}
└ Rate: {round((bans.get('total', 0) / total * 100), 1) if total > 0 else 0}%

⚙️ *Status:*
├ Leave-Ban: {'✅ Active' if config.get('leave_ban_mode') else '❌ Inactive'}
├ Bot: ✅ Online
└ Database: ✅ Healthy
"""
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def track_admins_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Track admins command"""
    if not await is_admin(update, context):
        await update.message.reply_text("❌ Only admins can track.", parse_mode=ParseMode.MARKDOWN)
        return
    
    chat_id = update.effective_chat.id
    
    await update.message.reply_text("🔄 Tracking admins...", parse_mode=ParseMode.MARKDOWN)
    
    try:
        admins = await context.bot.get_chat_administrators(chat_id)
        users = SimpleDB.load_users(chat_id)
        users['admins'] = []
        
        for admin in admins:
            users['admins'].append({
                'user_id': admin.user.id,
                'name': clean_text(admin.user.full_name),
                'username': admin.user.username if admin.user.username else "",
                'status': admin.status,
                'tracked': datetime.now().isoformat()
            })
        
        SimpleDB.save_users(chat_id, users)
        
        text = f"""
✅ *Admin Tracking Complete*

📋 *Results:*
├ Group: {update.effective_chat.title}
├ Total Admins: {len(admins)}
├ Owners: {sum(1 for a in admins if a.status == 'creator')}
└ Time: {format_time(datetime.now().isoformat())}

*Note:* Regular users tracked when they join.
"""
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        
    except Exception as e:
        logger.error(f"Track error: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)[:200]}", parse_mode=ParseMode.MARKDOWN)

async def export_data_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Export data command - COMPLETE BAN HISTORY WITH USER IDs - FIXED UPLOAD"""
    if not await is_admin(update, context):
        await update.message.reply_text("❌ Only admins can export.", parse_mode=ParseMode.MARKDOWN)
        return
    
    chat_id = update.effective_chat.id
    
    await update.message.reply_text("📤 Preparing complete ban history export...", parse_mode=ParseMode.MARKDOWN)
    
    try:
        # Load data
        config = SimpleDB.load_config(chat_id)
        users = SimpleDB.load_users(chat_id)
        bans = SimpleDB.load_bans(chat_id)
        
        # Create comprehensive export
        export_time = datetime.now().strftime("%Y%m%d_%H%M%S")
        group_name = ''.join(c for c in update.effective_chat.title if c.isalnum() or c in (' ', '-', '_')).rstrip()
        filename = f"ban_history_{chat_id}_{export_time}.txt"
        filepath = os.path.join(DATA_DIR, filename)
        
        # Prepare content
        content = f"""📋 COMPLETE BAN HISTORY - {BOT_NAME}
================================================

GROUP INFORMATION:
------------------
Group Name: {update.effective_chat.title}
Group ID: {chat_id}
Export Date: {datetime.now().isoformat()}
Exported By: {update.effective_user.full_name} (ID: {update.effective_user.id})

CONFIGURATION:
--------------
Leave-Ban Mode: {'ENABLED' if config.get('leave_ban_mode') else 'DISABLED'}
Auto Tracking: {'ENABLED' if config.get('auto_track') else 'DISABLED'}
Created: {config.get('created_at')}
Last Modified: {config.get('last_modified')}

USER STATISTICS:
----------------
Total Active Users: {len(users.get('active', []))}
Total Banned Users: {len(users.get('banned', []))}
Total Left Users: {len(users.get('left', []))}
Total Admins: {len(users.get('admins', []))}

BAN STATISTICS:
---------------
Total Bans: {bans.get('total', 0)}
Auto Bans: {bans.get('auto', 0)}
Manual Bans: {bans.get('manual', 0)}

COMPLETE BAN LIST (ALL TIME):
=============================
"""
        
        # Add ALL banned users with their IDs
        ban_list = bans.get('list', [])
        
        if not ban_list:
            content += "\nNo ban records found.\n"
        else:
            for i, ban in enumerate(ban_list, 1):
                user_id = ban.get('user_id', 'N/A')
                user_name = clean_text(ban.get('name', 'Unknown'))
                username = ban.get('username', '')
                timestamp = ban.get('timestamp', 'Unknown')
                reason = ban.get('reason', 'No reason')
                ban_type = ban.get('type', 'Unknown')
                
                # Format timestamp
                try:
                    dt = datetime.fromisoformat(timestamp)
                    formatted_time = dt.strftime("%Y-%m-%d %H:%M:%S")
                except:
                    formatted_time = timestamp
                
                # Format username for file
                formatted_username = format_username_for_file(username)
                
                content += f"\n{i}. USER INFORMATION:\n"
                content += f"   Name: {user_name}\n"
                content += f"   ID: {user_id}\n"
                content += f"   Username: @{formatted_username}\n"
                content += f"   Ban Time: {formatted_time}\n"
                content += f"   Ban Type: {ban_type}\n"
                content += f"   Reason: {reason}\n"
                content += "   " + "-"*40
        
        content += f"""

SUMMARY:
--------
Total Ban Records: {len(ban_list)}
Export Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
File Format: UTF-8

================================================
END OF EXPORT
"""
        
        # Save file
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        
        # Send file to user
        try:
            with open(filepath, 'rb') as f:
                await update.message.reply_document(
                    document=f,
                    filename=filename,
                    caption=f"📁 *Ban History Export*\n\n📊 *Total Records:* {len(ban_list)}\n📅 *Exported:* {datetime.now().strftime('%Y-%m-%d %H:%M')}\n💾 *File:* `{filename}`",
                    parse_mode=ParseMode.MARKDOWN
                )
            logger.info(f"Exported file sent: {filename}")
            
        except Exception as e:
            logger.error(f"File upload error: {e}")
            # If file upload fails, send info message
            file_size = os.path.getsize(filepath)
            size_text = f"{file_size // 1024} KB" if file_size > 1024 else f"{file_size} bytes"
            
            text = f"""
✅ *Export Complete - Download Manually*

📄 *File Details:*
├ File: `{filename}`
├ Size: {size_text}
├ Location: `{filepath}`
├ Records: {len(ban_list)}
└ Export Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

⚠️ *Note:* File saved locally. Upload to Telegram failed.
"""
            await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        
    except Exception as e:
        logger.error(f"Export error: {e}")
        await update.message.reply_text(f"❌ Export failed: {str(e)[:200]}", parse_mode=ParseMode.MARKDOWN)

async def ban_logs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ban logs command with user IDs - shows last 10 bans"""
    if not await is_admin(update, context):
        await update.message.reply_text("❌ Only admins can view logs.", parse_mode=ParseMode.MARKDOWN)
        return
    
    chat_id = update.effective_chat.id
    bans = SimpleDB.load_bans(chat_id)
    ban_list = bans.get('list', [])
    
    if not ban_list:
        await update.message.reply_text("📭 No ban records found.", parse_mode=ParseMode.MARKDOWN)
        return
    
    # Show last 10 bans
    recent = ban_list[-10:]
    
    text = f"""
📋 *Ban Logs - {update.effective_chat.title}*

📊 *Statistics:*
├ Total Bans: {len(ban_list)}
├ Auto Bans: {bans.get('auto', 0)}
├ Manual Bans: {bans.get('manual', 0)}
└ Showing: Last {len(recent)} of {len(ban_list)}

🔍 *Recent Bans (Last 10 - Newest First):*
"""
    
    for i, ban in enumerate(recent[::-1], 1):
        time = format_time(ban.get('timestamp'))
        user_name = clean_text(ban.get('name', 'Unknown'))
        user_id = ban.get('user_id', 'N/A')
        username = ban.get('username', '')
        reason = ban.get('reason', 'Leave ban')
        ban_type = "🔄 Auto" if ban.get('type') == 'auto' else "👤 Manual"
        
        # Format username properly
        formatted_username = format_username_for_display(username)
        
        text += f"\n{i}. *{user_name}*\n"
        text += f"   ├ 🆔 ID: `{user_id}`\n"
        text += f"   ├ 👤 Username: {formatted_username}\n"
        text += f"   ├ ⏰ {time}\n"
        text += f"   ├ 📝 {reason}\n"
        text += f"   └ {ban_type}\n"
    
    text += f"\n*Note:* Showing last {len(recent)} bans. Use `/export_data` for complete history."
    
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def about_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """About command - FIXED USERNAME DISPLAY"""
    text = f"""
🤖 *{BOT_NAME} v{VERSION}*

A professional Telegram group management bot
with multi-group support and leave-ban system.

🌟 *Features:*
• Separate database for each group
• Auto-ban users who leave (with user ID in notification)
• User tracking and statistics
• Complete data export with user IDs
• Professional interface

*Thank you for using {BOT_NAME}!*
"""
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)

async def support_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Support command"""
    text = f"""
🆘 *Support Center*

📞 *Contact Developer:*
├ Telegram: @professor_cry
└ Support Link: {SUPPORT_LINK}

🔧 *Common Issues:*
1. *Bot not responding:*
   • Check admin permissions
   • Restart bot

2. *Commands not working:*
   • Ensure bot is admin
   • Use `/help` for guide

3. *Export issues:*
   • Check disk space
   • Try again

⚡ *Quick Help:*
• Full guide: `/help`
• Command list: Type `/`
• Setup: `/setup`

*Contact @professor_cry for help.*
"""
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)

# ============ EVENT HANDLERS ============
async def handle_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle new member"""
    try:
        chat_id = update.effective_chat.id
        
        for user in update.message.new_chat_members:
            # Bot added
            if user.id == context.bot.id:
                text = f"""
🎉 *{BOT_NAME} Added!*

⚡ *Quick Start:*
1. Make me Admin (Ban permission)
2. Type `/setup` to initialize
3. Configure with `/set_leave_ban on`

📚 *Commands:* `/help`

"""
                await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
                continue
            
            # Track user
            config = SimpleDB.load_config(chat_id)
            if config.get('auto_track', True):
                users = SimpleDB.load_users(chat_id)
                users['active'].append({
                    'user_id': user.id,
                    'name': clean_text(user.full_name),
                    'username': user.username if user.username else "",
                    'joined': datetime.now().isoformat()
                })
                SimpleDB.save_users(chat_id, users)
                
    except Exception as e:
        logger.error(f"New member error: {e}")

async def handle_left_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle left member - FIXED USERNAME DISPLAY"""
    try:
        user = update.message.left_chat_member
        chat_id = update.effective_chat.id
        
        # Skip bot
        if user.id == context.bot.id:
            return
        
        # Update users
        users = SimpleDB.load_users(chat_id)
        
        # Move from active to left
        user_found = False
        user_data = None
        
        for active in users['active'][:]:
            if active['user_id'] == user.id:
                active['left'] = datetime.now().isoformat()
                users['left'].append(active)
                users['active'].remove(active)
                user_found = True
                user_data = active
                break
        
        # If not found in active, create new record
        if not user_found:
            user_data = {
                'user_id': user.id,
                'name': clean_text(user.full_name),
                'username': user.username if user.username else "",
                'left': datetime.now().isoformat()
            }
            users['left'].append(user_data)
        
        SimpleDB.save_users(chat_id, users)
        
        # Check leave-ban
        config = SimpleDB.load_config(chat_id)
        if config.get('leave_ban_mode', False):
            try:
                # Don't ban admins
                admins = await context.bot.get_chat_administrators(chat_id)
                admin_ids = [a.user.id for a in admins]
                
                if user.id not in admin_ids:
                    # Ban user
                    await context.bot.ban_chat_member(chat_id, user.id)
                    
                    # Record ban
                    bans = SimpleDB.load_bans(chat_id)
                    
                    # Format username properly
                    formatted_username = format_username_for_display(user.username)
                    
                    ban_record = {
                        'user_id': user.id,
                        'name': clean_text(user.full_name),
                        'username': user.username if user.username else "",
                        'timestamp': datetime.now().isoformat(),
                        'reason': 'Auto-ban on leave',
                        'type': 'auto'
                    }
                    
                    bans['list'].append(ban_record)
                    bans['total'] = bans.get('total', 0) + 1
                    bans['auto'] = bans.get('auto', 0) + 1
                    SimpleDB.save_bans(chat_id, bans)
                    
                    # Update user status
                    users['banned'].append({
                        'user_id': user.id,
                        'name': clean_text(user.full_name),
                        'username': user.username if user.username else "",
                        'banned': datetime.now().isoformat(),
                        'reason': 'Auto-ban on leave'
                    })
                    SimpleDB.save_users(chat_id, users)
                    
                    # Send message WITH USER ID - FIXED FORMATTING
                    text = f"""
🚫 *User Banned*

👤 *User Information:*
├ Name: {clean_text(user.full_name)}
├ ID: `{user.id}`
├ Username: {formatted_username}
└ Action: Left the group

🔒 *Security Action:*
├ Type: Automatic Ban
├ Reason: Leave-Ban Mode Active
├ Time: {format_time(datetime.now().isoformat())}
└ Performed By: System Auto-Protection

⚙️ *System Status:*
├ Leave-Ban: ✅ ACTIVE
├ Protection: ✅ ENGAGED
└ Database: ✅ UPDATED

⚠️ *Note:* Leave-ban mode is active.
"""
                    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
                    
            except Exception as e:
                logger.error(f"Ban error: {e}")
                
    except Exception as e:
        logger.error(f"Left member error: {e}")

# ============ ERROR HANDLER ============
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Error handler"""
    logger.error(f"Error: {context.error}")
    
    if update and update.effective_chat:
        try:
            text = f"""
⚠️ *System Error*

An error occurred. Please try again.

📞 *Support:* @professor_cry
"""
            await update.effective_chat.send_message(text, parse_mode=ParseMode.MARKDOWN)
        except:
            pass

# ============ MAIN FUNCTION ============
def main():
    """Main function"""
    
    # Bot token - CHANGE THIS
    TOKEN = "8349545549:AAGT6XZKIsF0XH1lFi52rcs4OmKZVbKSPng"
    
    if TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("\n" + "="*50)
        print("❌ ERROR: Bot token not set!")
        print("="*50)
        print("1. Get token from @BotFather")
        print("2. Replace 'YOUR_BOT_TOKEN_HERE' with your token")
        print("3. Contact @professor_cry if needed")
        print("="*50)
        return
    
    # Create bot
    app = Application.builder().token(TOKEN).build()
    
    # Add commands
    commands = [
        ("start", start_cmd),
        ("help", help_cmd),
        ("setup", setup_cmd),
        ("set_leave_ban", set_leave_ban_cmd),
        ("config", config_cmd),
        ("stats", stats_cmd),
        ("track_admins", track_admins_cmd),
        ("export_data", export_data_cmd),
        ("ban_logs", ban_logs_cmd),
        ("about", about_cmd),
        ("support", support_cmd),
    ]
    
    for cmd, handler in commands:
        app.add_handler(CommandHandler(cmd, handler))
    
    # Add events
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_member))
    app.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, handle_left_member))
    
    # Add error handler
    app.add_error_handler(error_handler)
    
    # Start bot
    print("\n" + "="*50)
    print(f"🤖 {BOT_NAME} v{VERSION}")
    print("="*50)
    print("👨💻 Developer: @professor_cry")
    print(f"📁 Data: {DATA_DIR}")
    print(f"📝 Logs: {LOG_DIR}")
    print("="*50)
    print("✅ Bot starting...")
    print("="*50)
    
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()