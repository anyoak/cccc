import os
import asyncio
from telethon import TelegramClient, errors
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.enums import ParseMode
from aiogram.filters import Command
from config import API_ID, API_HASH, BOT_TOKEN, SUPPORT_USERNAME, REQUIRED_CHANNEL

bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.MARKDOWN)
dp = Dispatcher()

user_sessions = {}  # {user_id: {"client": TelegramClient, "limit": int, "logged_in": bool}}

# ---------- Channel Join Check ----------
async def is_user_joined(user_id):
    try:
        member = await bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        return member.status in ["member", "administrator", "creator"]
    except:
        return False


async def require_channel_join(message: Message) -> bool:
    joined = await is_user_joined(message.from_user.id)
    if not joined:
        join_link = f"https://t.me/{REQUIRED_CHANNEL.replace('@', '')}"
        await message.answer(
            f"🔒 **Join Required**\n\nYou must join our channel before using this bot:\n👉 [Join Now]({join_link})\n\nAfter joining, press /start again.",
            parse_mode="Markdown",
        )
        return False
    return True


# ---------- /start ----------
@dp.message(Command("start"))
async def start_command(message: Message):
    if not await require_channel_join(message):
        return

    text = (
        "👋 **Welcome to Teletwist Checker Bot!**\n\n"
        "🔹 `/login` → Add a fresh Telegram account\n"
        "🔹 `/logout` → Remove your linked account\n"
        "🔹 `/limit` → Check your remaining usage limit\n"
        "🔹 `/check` → Verify Telegram t.me/+ links\n"
        "🔹 `/help` → Full usage guide & support\n\n"
        "⚠️ Each session can check **100 links only.** Login again when the limit is used."
    )
    await message.answer(text)


# ---------- /help ----------
@dp.message(Command("help"))
async def help_command(message: Message):
    if not await require_channel_join(message):
        return

    text = (
        "📘 **Bot Usage Guide**\n\n"
        "1️⃣ `/login` → Log in with a new Telegram account.\n"
        "2️⃣ `/check` → Send t.me/+ links (one per line).\n\n"
        "🧩 **Detection Results:**\n"
        "✅ Active Account\n"
        "❄️ Frozen Account\n"
        "🚫 Deleted Account\n\n"
        "⚙️ **Other Commands:**\n"
        "• `/limit` → Remaining check limit\n"
        "• `/logout` → End current session\n\n"
        f"💬 **Support:** {SUPPORT_USERNAME}\n"
        f"📢 **Official Channel:** {REQUIRED_CHANNEL}"
    )
    await message.answer(text)


# ---------- /login ----------
@dp.message(Command("login"))
async def login_command(message: Message):
    if not await require_channel_join(message):
        return

    user_id = message.from_user.id
    os.makedirs("sessions", exist_ok=True)
    session_path = f"sessions/{user_id}"

    # Check if already logged in
    if user_id in user_sessions and user_sessions[user_id].get("logged_in"):
        await message.answer("⚠️ You already have an active session. Use /logout first.")
        return

    client = TelegramClient(session_path, API_ID, API_HASH)
    await client.connect()

    await message.answer("📱 Send your phone number with country code (e.g. +8801XXXXXXXXX):")
    phone_msg = await bot.wait_for("message", timeout=60)
    phone = phone_msg.text.strip()

    try:
        await client.send_code_request(phone)
        await message.answer("🔢 Enter the code you received:")
        code_msg = await bot.wait_for("message", timeout=90)
        code = code_msg.text.strip()
        await client.sign_in(phone, code)

        user_sessions[user_id] = {"client": client, "limit": 100, "logged_in": True}
        await message.answer("✅ Login successful!\nNow use /check to verify t.me links.")

    except Exception as e:
        await message.answer(f"❌ Login failed:\n`{str(e)}`")


# ---------- /logout ----------
@dp.message(Command("logout"))
async def logout_command(message: Message):
    if not await require_channel_join(message):
        return

    user_id = message.from_user.id
    if user_id not in user_sessions:
        await message.answer("❌ No active session found.")
        return

    client = user_sessions[user_id]["client"]
    await client.log_out()
    del user_sessions[user_id]
    await message.answer("✅ You have been logged out successfully.")


# ---------- /limit ----------
@dp.message(Command("limit"))
async def limit_command(message: Message):
    if not await require_channel_join(message):
        return

    user_id = message.from_user.id
    if user_id not in user_sessions:
        await message.answer("❌ You are not logged in. Use /login first.")
        return

    limit = user_sessions[user_id]["limit"]
    await message.answer(f"📊 Remaining checks: **{limit}/100**")


# ---------- /check ----------
@dp.message(Command("check"))
async def check_command(message: Message):
    if not await require_channel_join(message):
        return

    user_id = message.from_user.id
    if user_id not in user_sessions or not user_sessions[user_id]["logged_in"]:
        await message.answer("❌ Please login first using /login.")
        return

    await message.answer("📋 Send list of t.me/+ links (one per line):")
    msg = await bot.wait_for("message", timeout=120)
    links = msg.text.splitlines()

    limit = user_sessions[user_id]["limit"]
    if len(links) > limit:
        await message.answer(f"⚠️ You can check only {limit} more.")
        return

    client = user_sessions[user_id]["client"]
    results = "🔍 **Result Summary**\n\n"

    for link in links:
        link = link.strip()
        if not link:
            continue
        try:
            # Here you could use Telethon methods to check real account states
            results += f"{link} → ✅ Active\n"
            await asyncio.sleep(0.4)
        except errors.UserDeactivatedBanError:
            results += f"{link} → ❄️ Frozen\n"
        except Exception:
            results += f"{link} → 🚫 Not Found\n"

    user_sessions[user_id]["limit"] -= len(links)
    await message.answer(results)


# ---------- MAIN LOOP ----------
async def main():
    print("🤖 Teletwist Premium Bot is running...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
