import asyncio
import random
import sqlite3
import logging
from datetime import datetime

from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, PhoneNumberInvalidError, FloodWaitError

# ========================
# CONFIG & LOGGING
# ========================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# REPLACE THIS WITH YOUR ACTUAL BOT TOKEN FROM @BotFather
BOT_TOKEN = "8403745447:AAHZ_0XehvLxQdcrTjpjVQgu-4s8gmPRhAw"

# Initialize bot and dispatcher
try:
    bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
    storage = MemoryStorage()
    dp = Dispatcher(bot, storage=storage)
    logger.info("Bot initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize bot: {e}")
    exit(1)

# ========================
# DATABASE SETUP
# ========================

def init_database():
    try:
        conn = sqlite3.connect("data.db", check_same_thread=False)
        cur = conn.cursor()

        # Bot users and their Telegram account sessions
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            tg_user_id     INTEGER PRIMARY KEY,
            api_id         INTEGER,
            api_hash       TEXT,
            phone          TEXT,
            session_string TEXT,
            is_logged_in   INTEGER DEFAULT 0,
            created_at     TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)

        # Groups where the logged-in account is detected
        cur.execute("""
        CREATE TABLE IF NOT EXISTS groups (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_user_id INTEGER,
            chat_id    INTEGER,
            title      TEXT,
            is_owner   INTEGER,
            UNIQUE(tg_user_id, chat_id)
        )
        """)

        # Logs: which message was sent where and when
        cur.execute("""
        CREATE TABLE IF NOT EXISTS message_logs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_user_id INTEGER,
            chat_id    INTEGER,
            message    TEXT,
            sent_at    TEXT,
            success    INTEGER
        )
        """)

        conn.commit()
        logger.info("Database initialized successfully")
        return conn, cur
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        raise

try:
    conn, cur = init_database()
except Exception as e:
    logger.error(f"Failed to initialize database: {e}")
    exit(1)

# ========================
# FSM STATES FOR /login
# ========================

class LoginStates(StatesGroup):
    waiting_api_id = State()
    waiting_api_hash = State()
    waiting_phone = State()
    waiting_otp = State()
    waiting_2fa = State()

# Temporary store Telethon clients during login
login_clients = {}

# ========================
# RANDOM MESSAGES
# ========================

RANDOM_MESSAGES = [
    "Hello everyone, have a great day!",
    "This is a random test message.",
    "Stay positive and keep going!",
    "Sending a friendly hello!",
    "Hope you are all doing well.",
    "Testing my Telegram automation bot.",
    "Wishing you success in everything.",
    "Just checking in, how is it going?",
    "Another random message for the group.",
    "Keep smiling and stay strong!",
    "Here is a friendly reminder to drink water.",
    "Automation is fun when you control it!"
]

# ========================
# HELPER FUNCTIONS
# ========================

async def get_user_client(tg_user_id: int):
    """
    Create a Telethon client for this bot user based on stored session.
    """
    try:
        cur.execute("""
            SELECT api_id, api_hash, session_string, is_logged_in
            FROM users
            WHERE tg_user_id=?
        """, (tg_user_id,))
        row = cur.fetchone()

        if not row:
            return None

        api_id, api_hash, session_string, is_logged_in = row
        if not is_logged_in or not session_string:
            return None

        client = TelegramClient(StringSession(session_string), api_id, api_hash)
        await client.connect()
        
        # Verify the session is still valid
        if not await client.is_user_authorized():
            # Session is invalid, mark as logged out
            cur.execute("""
                UPDATE users SET is_logged_in=0 
                WHERE tg_user_id=?
            """, (tg_user_id,))
            conn.commit()
            await client.disconnect()
            return None
            
        return client
    except Exception as e:
        logger.error(f"Error creating user client for {tg_user_id}: {e}")
        return None

def cleanup_login_session(user_id: int):
    """Clean up login session data"""
    if user_id in login_clients:
        try:
            client = login_clients[user_id][0]
            asyncio.create_task(client.disconnect())
        except:
            pass
        del login_clients[user_id]

# ========================
# COMMAND HANDLERS
# ========================

@dp.message_handler(commands=["start", "help"])
async def cmd_start(message: types.Message):
    text = (
        "🤖 <b>Telegram Auto-Message Bot</b>\n\n"
        "I can help you send automated messages to your Telegram groups.\n\n"
        "<b>Available Commands:</b>\n"
        "/login - Login with your Telegram account\n"
        "/logout - Logout and clear your session\n"
        "/send - Send random messages to your owned groups\n"
        "/logs - View your message history\n"
        "/status - Check your login status\n\n"
        "<i>Note: You need to get API credentials from https://my.telegram.org</i>"
    )
    await message.reply(text)

@dp.message_handler(commands=["status"])
async def cmd_status(message: types.Message):
    """Check user login status"""
    user_id = message.from_user.id
    
    cur.execute("""
        SELECT is_logged_in, phone, created_at 
        FROM users WHERE tg_user_id=?
    """, (user_id,))
    row = cur.fetchone()
    
    if row and row[0] == 1:
        await message.reply(
            f"✅ <b>Logged In</b>\n\n"
            f"📱 Phone: <code>{row[1]}</code>\n"
            f"📅 Since: <code>{row[2]}</code>"
        )
    else:
        await message.reply("❌ <b>Not logged in</b>\nUse /login to get started.")

# ---------- /login (step 1) ----------
@dp.message_handler(commands=["login"])
async def cmd_login(message: types.Message, state: FSMContext):
    # Check if already logged in
    cur.execute("SELECT is_logged_in FROM users WHERE tg_user_id=?", (message.from_user.id,))
    row = cur.fetchone()
    if row and row[0] == 1:
        await message.reply("✅ You are already logged in! Use /logout first to login again.")
        return

    await message.reply(
        "🔐 <b>Telegram Login</b>\n\n"
        "First, send your <code>api_id</code>:\n\n"
        "<i>Get it from: https://my.telegram.org/apps</i>"
    )
    await LoginStates.waiting_api_id.set()

@dp.message_handler(state=LoginStates.waiting_api_id)
async def process_api_id(message: types.Message, state: FSMContext):
    try:
        api_id = int(message.text.strip())
        if api_id <= 0:
            raise ValueError("Invalid API ID")
    except ValueError:
        await message.reply("❌ api_id must be a positive number. Please send again:")
        return

    await state.update_data(api_id=api_id)
    await message.reply("✅ api_id received.\nNow send your <code>api_hash</code>:")
    await LoginStates.waiting_api_hash.set()

@dp.message_handler(state=LoginStates.waiting_api_hash)
async def process_api_hash(message: types.Message, state: FSMContext):
    api_hash = message.text.strip()
    if not api_hash or len(api_hash) < 10:
        await message.reply("❌ Invalid api_hash. Please send again:")
        return

    await state.update_data(api_hash=api_hash)
    await message.reply(
        "✅ api_hash received.\n"
        "Now send your phone number with country code:\n"
        "<i>Example: <code>+8801XXXXXXXXX</code></i>"
    )
    await LoginStates.waiting_phone.set()

@dp.message_handler(state=LoginStates.waiting_phone)
async def process_phone(message: types.Message, state: FSMContext):
    phone = message.text.strip()
    data = await state.get_data()
    api_id = data["api_id"]
    api_hash = data["api_hash"]

    # Clean up any existing session
    cleanup_login_session(message.from_user.id)

    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.connect()

    try:
        sent_code = await client.send_code_request(phone)
        logger.info(f"Code sent to {phone}")
    except PhoneNumberInvalidError:
        await message.reply("❌ Invalid phone number. Please check and try /login again.")
        await client.disconnect()
        return
    except FloodWaitError as e:
        await message.reply(f"❌ Flood wait: Please wait {e.seconds} seconds before trying again.")
        await client.disconnect()
        return
    except Exception as e:
        await message.reply(
            f"❌ Failed to send code: <code>{e}</code>\n"
            "Please try /login again."
        )
        await client.disconnect()
        return

    login_clients[message.from_user.id] = (client, phone, api_id, api_hash)

    await message.reply("📲 OTP sent to your Telegram account. Please send the code you received:")
    await LoginStates.waiting_otp.set()

@dp.message_handler(state=LoginStates.waiting_otp)
async def process_otp(message: types.Message, state: FSMContext):
    code = message.text.strip().replace('-', '').replace(' ', '')
    user_id = message.from_user.id

    if user_id not in login_clients:
        await message.reply("❌ Login session expired. Please start again with /login.")
        await state.finish()
        return

    client, phone, api_id, api_hash = login_clients[user_id]

    try:
        # Try sign in with code only
        await client.sign_in(phone=phone, code=code)
        me = await client.get_me()

    except SessionPasswordNeededError:
        # 2FA password is enabled on this account
        await message.reply(
            "🔐 Your account has <b>2FA (cloud password)</b> enabled.\n"
            "Please send your Telegram password:"
        )
        await LoginStates.waiting_2fa.set()
        return

    except Exception as e:
        await message.reply(
            f"❌ Login with OTP failed: <code>{e}</code>\n"
            "Please try /login again."
        )
        await state.finish()
        await client.disconnect()
        cleanup_login_session(user_id)
        return

    # Login success without 2FA
    session_string = client.session.save()
    name = f"{me.first_name or ''} {me.last_name or ''}".strip()
    username = f"@{me.username}" if me.username else "No username"

    cur.execute("""
        INSERT INTO users (tg_user_id, api_id, api_hash, phone, session_string, is_logged_in)
        VALUES (?, ?, ?, ?, ?, 1)
        ON CONFLICT(tg_user_id) DO UPDATE SET
            api_id=excluded.api_id,
            api_hash=excluded.api_hash,
            phone=excluded.phone,
            session_string=excluded.session_string,
            is_logged_in=1
    """, (user_id, api_id, api_hash, phone, session_string))
    conn.commit()

    await message.reply(
        "✅ <b>Login Successful!</b>\n\n"
        f"👤 Name: <code>{name}</code>\n"
        f"📱 Username: {username}\n"
        f"📞 Phone: <code>{phone}</code>\n\n"
        "You can now use /send to message your groups."
    )

    await state.finish()
    await client.disconnect()
    cleanup_login_session(user_id)

# ---------- 2FA password ----------
@dp.message_handler(state=LoginStates.waiting_2fa)
async def process_2fa_password(message: types.Message, state: FSMContext):
    password = message.text.strip()
    user_id = message.from_user.id

    if user_id not in login_clients:
        await message.reply("❌ Login session expired. Please start again with /login.")
        await state.finish()
        return

    client, phone, api_id, api_hash = login_clients[user_id]

    try:
        await client.sign_in(password=password)
        me = await client.get_me()
    except Exception as e:
        await message.reply(
            f"❌ 2FA password incorrect or login failed: <code>{e}</code>\n"
            "Please try /login again."
        )
        await state.finish()
        await client.disconnect()
        cleanup_login_session(user_id)
        return

    # Login success with 2FA
    session_string = client.session.save()
    name = f"{me.first_name or ''} {me.last_name or ''}".strip()
    username = f"@{me.username}" if me.username else "No username"

    cur.execute("""
        INSERT INTO users (tg_user_id, api_id, api_hash, phone, session_string, is_logged_in)
        VALUES (?, ?, ?, ?, ?, 1)
        ON CONFLICT(tg_user_id) DO UPDATE SET
            api_id=excluded.api_id,
            api_hash=excluded.api_hash,
            phone=excluded.phone,
            session_string=excluded.session_string,
            is_logged_in=1
    """, (user_id, api_id, api_hash, phone, session_string))
    conn.commit()

    await message.reply(
        "✅ <b>Login Successful! (2FA)</b>\n\n"
        f"👤 Name: <code>{name}</code>\n"
        f"📱 Username: {username}\n"
        f"📞 Phone: <code>{phone}</code>\n\n"
        "You can now use /send to message your groups."
    )

    await state.finish()
    await client.disconnect()
    cleanup_login_session(user_id)

# ---------- /logout ----------
@dp.message_handler(commands=["logout"])
async def cmd_logout(message: types.Message):
    user_id = message.from_user.id

    cur.execute("""
        UPDATE users
        SET is_logged_in=0, session_string=NULL
        WHERE tg_user_id=?
    """, (user_id,))
    conn.commit()

    cleanup_login_session(user_id)
    await message.reply("🚪 Your Telegram account has been <b>logged out</b> and session cleared.")

# ---------- /send ----------
@dp.message_handler(commands=["send"])
async def cmd_send(message: types.Message):
    user_id = message.from_user.id

    client = await get_user_client(user_id)
    if not client:
        await message.reply("❌ Please /login first, then use /send.")
        return

    await message.reply("📤 Scanning your groups and sending messages...")

    sent_count = 0
    fail_count = 0
    logs_lines = []

    try:
        async with client:
            async for dialog in client.iter_dialogs():
                # Skip non-group dialogs
                if not dialog.is_group:
                    continue

                entity = dialog.entity
                chat_id = entity.id
                title = getattr(entity, 'title', 'Unknown Group')

                # Check if user is the creator/owner
                try:
                    participant = await client.get_permissions(chat_id, user_id)
                    is_owner = participant.is_creator
                except:
                    is_owner = False

                if not is_owner:
                    continue

                msg = random.choice(RANDOM_MESSAGES)
                success = 0

                try:
                    await client.send_message(chat_id, msg)
                    success = 1
                    sent_count += 1
                    status_icon = "✅"
                except Exception as e:
                    fail_count += 1
                    error_msg = str(e)
                    if "Flood" in error_msg:
                        status_icon = "⏳ Flood wait"
                    else:
                        status_icon = f"❌ {error_msg[:20]}..."

                # Update groups table
                cur.execute("""
                    INSERT INTO groups (tg_user_id, chat_id, title, is_owner)
                    VALUES (?, ?, ?, 1)
                    ON CONFLICT(tg_user_id, chat_id) DO UPDATE SET
                        title=excluded.title,
                        is_owner=excluded.is_owner
                """, (user_id, chat_id, title))
                conn.commit()

                # Insert into message_logs
                cur.execute("""
                    INSERT INTO message_logs (tg_user_id, chat_id, message, sent_at, success)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    user_id,
                    chat_id,
                    msg,
                    datetime.utcnow().isoformat(),
                    success
                ))
                conn.commit()

                logs_lines.append(f"{status_icon} <b>{title}</b>")

    except Exception as e:
        await message.reply(f"❌ Error during sending: {e}")
        return

    if not logs_lines:
        await message.reply("😕 No groups found where you are the owner.")
        return

    logs_text = "\n".join(logs_lines)

    summary = (
        f"✅ <b>Sending Completed</b>\n\n"
        f"✅ Successful: <b>{sent_count}</b>\n"
        f"❌ Failed: <b>{fail_count}</b>\n\n"
        f"<b>Details:</b>\n{logs_text}"
    )

    await message.reply(summary)

# ---------- /logs ----------
@dp.message_handler(commands=["logs"])
async def cmd_logs(message: types.Message):
    user_id = message.from_user.id

    # Show last 15 logs for this user
    cur.execute("""
        SELECT m.sent_at, m.success, m.message, m.chat_id, g.title
        FROM message_logs m
        LEFT JOIN groups g ON g.tg_user_id = m.tg_user_id AND g.chat_id = m.chat_id
        WHERE m.tg_user_id = ?
        ORDER BY m.id DESC
        LIMIT 15
    """, (user_id,))
    rows = cur.fetchall()

    if not rows:
        await message.reply("📄 No message history found.")
        return

    lines = []
    for sent_at, success, text_msg, chat_id, title in rows:
        status_icon = "✅" if success == 1 else "❌"
        group_name = title or f"Chat {chat_id}"
        
        # Format date nicely
        try:
            dt = datetime.fromisoformat(sent_at.replace('Z', '+00:00'))
            time_str = dt.strftime("%Y-%m-%d %H:%M")
        except:
            time_str = sent_at
            
        # Shorten message preview
        preview = (text_msg[:35] + "...") if len(text_msg) > 35 else text_msg
        
        lines.append(
            f"{status_icon} <b>{group_name}</b>\n"
            f"   ⏰ {time_str}\n"
            f"   💬 {preview}"
        )

    text = "<b>Recent Message History:</b>\n\n" + "\n\n".join(lines)
    await message.reply(text)

# ========================
# ERROR HANDLER
# ========================

@dp.errors_handler()
async def errors_handler(update, error):
    logger.error(f"Update {update} caused error: {error}")
    return True

# ========================
# BOT STARTUP/SHUTDOWN
# ========================

async def on_startup(dp):
    logger.info("Bot started successfully")
    # Notify admin if needed
    # await bot.send_message(chat_id=ADMIN_ID, text="🤖 Bot started!")

async def on_shutdown(dp):
    logger.info("Bot shutting down...")
    # Clean up all login sessions
    for user_id in list(login_clients.keys()):
        cleanup_login_session(user_id)
    # Close database connection
    conn.close()
    await bot.close()

# ========================
# RUN BOT
# ========================

if __name__ == "__main__":
    try:
        logger.info("Starting bot...")
        executor.start_polling(
            dp,
            skip_updates=True,
            on_startup=on_startup,
            on_shutdown=on_shutdown
        )
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")
