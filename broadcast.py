import asyncio
import random
import sqlite3
from datetime import datetime

from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError

# ========================
# CONFIG
# ========================

BOT_TOKEN = "8403745447:AAHZ_0XehvLxQdcrTjpjVQgu-4s8gmPRhAw"   # Put your BotFather token here

bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher(bot, storage=MemoryStorage())

# ========================
# DATABASE SETUP (SQLite)
# ========================

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
    is_logged_in   INTEGER DEFAULT 0
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
# key = tg_user_id, value = (client, phone, api_id, api_hash)
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
# HELPERS
# ========================

async def get_user_client(tg_user_id: int):
    """
    Create a Telethon client for this bot user based on stored session.
    """
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
    return client

# ========================
# COMMAND HANDLERS
# ========================

@dp.message_handler(commands=["start", "help"])
async def cmd_start(message: types.Message):
    text = (
        "Hello! I am a multi-user login bot 🤖\n\n"
        "Commands:\n"
        "/login - Login with your Telegram account (api_id/api_hash/phone/OTP/2FA)\n"
        "/logout - Logout and clear your saved session\n"
        "/send - Send random messages to all groups where you are the owner\n"
        "/logs - View your own recent message history\n"
    )
    await message.reply(text)


# ---------- /login (step 1) ----------
@dp.message_handler(commands=["login"])
async def cmd_login(message: types.Message, state: FSMContext):
    await message.reply(
        "🔐 <b>Login started</b>\n\n"
        "First, send your <code>api_id</code>:"
    )
    await LoginStates.waiting_api_id.set()


@dp.message_handler(state=LoginStates.waiting_api_id)
async def process_api_id(message: types.Message, state: FSMContext):
    try:
        api_id = int(message.text.strip())
    except ValueError:
        await message.reply("❌ api_id must be a number. Please send again:")
        return

    await state.update_data(api_id=api_id)
    await message.reply("✅ api_id received.\nNow send your <code>api_hash</code>:")
    await LoginStates.waiting_api_hash.set()


@dp.message_handler(state=LoginStates.waiting_api_hash)
async def process_api_hash(message: types.Message, state: FSMContext):
    api_hash = message.text.strip()
    await state.update_data(api_hash=api_hash)
    await message.reply(
        "✅ api_hash received.\n"
        "Now send your phone number with country code (e.g. <code>+8801XXXXXXXXX</code>):"
    )
    await LoginStates.waiting_phone.set()


@dp.message_handler(state=LoginStates.waiting_phone)
async def process_phone(message: types.Message, state: FSMContext):
    phone = message.text.strip()
    data = await state.get_data()
    api_id = data["api_id"]
    api_hash = data["api_hash"]

    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.connect()

    try:
        await client.send_code_request(phone)
    except Exception as e:
        await message.reply(
            f"❌ Failed to send code: <code>{e}</code>\n"
            "Please try /login again."
        )
        await client.disconnect()
        return

    login_clients[message.from_user.id] = (client, phone, api_id, api_hash)

    await message.reply("📲 Send the OTP (code) you received on Telegram:")
    await LoginStates.waiting_otp.set()


@dp.message_handler(state=LoginStates.waiting_otp)
async def process_otp(message: types.Message, state: FSMContext):
    code = message.text.strip()
    user_id = message.from_user.id

    if user_id not in login_clients:
        await message.reply("❌ Login session not found. Please start again with /login.")
        await state.finish()
        return

    client, phone, api_id, api_hash = login_clients[user_id]

    try:
        # Try sign in with code only
        await client.sign_in(phone=phone, code=code)

    except SessionPasswordNeededError:
        # 2FA password is enabled on this account
        await message.reply(
            "🔐 Your account has <b>2FA (cloud password)</b> enabled.\n"
            "Please send your Telegram <b>password</b>:"
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
        del login_clients[user_id]
        return

    # Login success without 2FA
    session_string = client.session.save()
    me = await client.get_me()
    name = f"{me.first_name or ''} {me.last_name or ''}".strip()
    username = me.username

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
        "✅ <b>Login Success</b>\n\n"
        f"Name: <code>{name}</code>\n"
        f"Username: @{username}\n"
        f"Phone: <code>{phone}</code>"
    )

    await state.finish()
    await client.disconnect()
    del login_clients[user_id]


# ---------- 2FA password ----------
@dp.message_handler(state=LoginStates.waiting_2fa)
async def process_2fa_password(message: types.Message, state: FSMContext):
    password = message.text.strip()
    user_id = message.from_user.id

    if user_id not in login_clients:
        await message.reply("❌ Login session not found. Please start again with /login.")
        await state.finish()
        return

    client, phone, api_id, api_hash = login_clients[user_id]

    try:
        await client.sign_in(password=password)
    except Exception as e:
        await message.reply(
            f"❌ 2FA password incorrect or login failed: <code>{e}</code>\n"
            "Please try /login again."
        )
        await state.finish()
        await client.disconnect()
        del login_clients[user_id]
        return

    # Login success with 2FA
    session_string = client.session.save()
    me = await client.get_me()
    name = f"{me.first_name or ''} {me.last_name or ''}".strip()
    username = me.username

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
        "✅ <b>Login Success (2FA)</b>\n\n"
        f"Name: <code>{name}</code>\n"
        f"Username: @{username}\n"
        f"Phone: <code>{phone}</code>"
    )

    await state.finish()
    await client.disconnect()
    del login_clients[user_id]


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

    await message.reply("🚪 Your Telegram account has been <b>logged out</b> and the session is cleared.")


# ---------- /send ----------
@dp.message_handler(commands=["send"])
async def cmd_send(message: types.Message):
    user_id = message.from_user.id

    client = await get_user_client(user_id)
    if not client:
        await message.reply("❌ Please /login first, then use /send.")
        return

    await message.reply("📤 Sending random messages to your owned groups...")

    sent_count = 0
    fail_count = 0
    logs_lines = []

    async with client:
        async for dialog in client.iter_dialogs():
            entity = dialog.entity

            # Check if it's a group / supergroup
            is_group = dialog.is_group or getattr(entity, "megagroup", False)
            if not is_group:
                continue

            # Check owner (creator)
            is_owner = getattr(entity, "creator", False)
            if not is_owner:
                continue

            chat_id = entity.id
            title = getattr(entity, "title", "No Title")

            msg = random.choice(RANDOM_MESSAGES)
            success = 0

            try:
                await client.send_message(chat_id, msg)
                success = 1
                sent_count += 1
                status_icon = "✅"
            except Exception as e:
                fail_count += 1
                status_icon = f"❌ ({e})"

            # Update groups table (owner=1)
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

            logs_lines.append(f"{status_icon} <b>{title}</b> (<code>{chat_id}</code>)")

    if not logs_lines:
        await message.reply("😕 No owner groups found or no message was sent.")
        return

    logs_text = "\n".join(logs_lines)

    summary = (
        f"✅ <b>Send completed</b>\n\n"
        f"Successful groups: <b>{sent_count}</b>\n"
        f"Failed groups: <b>{fail_count}</b>\n\n"
        f"<b>Details:</b>\n{logs_text}"
    )

    await message.reply(summary)


# ---------- /logs ----------
@dp.message_handler(commands=["logs"])
async def cmd_logs(message: types.Message):
    user_id = message.from_user.id

    # Show last 20 logs only for this user
    cur.execute("""
        SELECT m.sent_at, m.success, m.message, m.chat_id, g.title
        FROM message_logs m
        LEFT JOIN groups g
            ON g.tg_user_id = m.tg_user_id AND g.chat_id = m.chat_id
        WHERE m.tg_user_id = ?
        ORDER BY m.id DESC
        LIMIT 20
    """, (user_id,))
    rows = cur.fetchall()

    if not rows:
        await message.reply("📄 You have no message history yet.")
        return

    lines = []
    for sent_at, success, text_msg, chat_id, title in rows:
        status_icon = "✅" if success == 1 else "❌"
        group_name = title or "Unknown group"
        # Shorten message preview
        preview = (text_msg[:40] + "...") if len(text_msg) > 40 else text_msg
        lines.append(
            f"{status_icon} <b>{group_name}</b> (<code>{chat_id}</code>)\n"
            f"   Time: <code>{sent_at}</code>\n"
            f"   Msg: <code>{preview}</code>"
        )

    text = "<b>Your last 20 message logs:</b>\n\n" + "\n\n".join(lines)
    await message.reply(text)


# ========================
# RUN BOT
# ========================

if __name__ == "__main__":
    print("Bot is running...")
    executor.start_polling(dp, skip_updates=True)
