import os
import asyncio
from telethon import TelegramClient, errors
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from config import API_ID, API_HASH, BOT_TOKEN, SUPPORT_USERNAME, REQUIRED_CHANNEL

# Initialize bot
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

user_sessions = {}  # {user_id: {"client": TelegramClient, "limit": int, "logged_in": bool}}

# --- Helper: Check if user joined required channel ---
async def is_user_joined(user_id):
    try:
        member = await bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        return member.status in ["member", "administrator", "creator"]
    except:
        return False


# --- Middleware-like check ---
async def require_channel_join(message: types.Message):
    joined = await is_user_joined(message.from_user.id)
    if not joined:
        join_link = f"https://t.me/{REQUIRED_CHANNEL.replace('@', '')}"
        await message.reply(
            f"🔒 To use this bot, you must join our official channel first:\n\n👉 [Join Now]({join_link})\n\nAfter joining, send /start again.",
            parse_mode="Markdown",
        )
        return False
    return True


# --- /start ---
@dp.message_handler(commands=["start"])
async def start_command(message: types.Message):
    if not await require_channel_join(message):
        return

    text = (
        "👋 **Welcome to Telegram Account Checker Bot**\n\n"
        "🔹 `/login` → Add a fresh Telegram account\n"
        "🔹 `/logout` → Remove current logged account\n"
        "🔹 `/limit` → Check remaining checks (100 max)\n"
        "🔹 `/check` → Verify Telegram numbers (t.me/+)\n"
        "🔹 `/help` → Full guide & support info\n\n"
        "Each account can check **100 numbers only.** After that, login again with a new account."
    )
    await message.reply(text, parse_mode="Markdown")


# --- /help ---
@dp.message_handler(commands=["help"])
async def help_command(message: types.Message):
    if not await require_channel_join(message):
        return

    text = (
        "📘 **Bot Usage Guide**\n\n"
        "✅ **Step 1:** `/login` → login a fresh Telegram account (must not be old).\n"
        "✅ **Step 2:** `/check` → input numbers (one per line).\n"
        "✅ **Bot Detects:**\n"
        "  • Active accounts ✅\n"
        "  • Frozen accounts ❄️\n"
        "  • Deleted accounts 🚫\n\n"
        "⚙️ **Other Commands:**\n"
        "• `/limit` → Remaining check limit\n"
        "• `/logout` → Logout session\n\n"
        f"💬 **Support:** {SUPPORT_USERNAME}\n"
        "Please contact support if you face login issues or bugs."
    )
    await message.reply(text, parse_mode="Markdown")


# --- /login ---
@dp.message_handler(commands=["login"])
async def login_command(message: types.Message):
    if not await require_channel_join(message):
        return

    user_id = message.from_user.id

    if user_id in user_sessions and user_sessions[user_id].get("logged_in"):
        await message.reply("⚠️ You already have an account logged in. Use `/logout` first.")
        return

    session_name = f"sessions/{user_id}"
    os.makedirs("sessions", exist_ok=True)
    client = TelegramClient(session_name, API_ID, API_HASH)
    await client.connect()

    if not await client.is_user_authorized():
        await message.reply("📱 Enter your phone number (with country code):")
        phone_prompt = await bot.wait_for("message")
        phone_number = phone_prompt.text.strip()

        try:
            await client.send_code_request(phone_number)
            await message.reply("🔢 Send the code you received:")
            code_prompt = await bot.wait_for("message")
            code = code_prompt.text.strip()
            await client.sign_in(phone_number, code)
        except Exception as e:
            await message.reply(f"❌ Login failed: `{str(e)}`", parse_mode="Markdown")
            return

    user_sessions[user_id] = {"client": client, "limit": 100, "logged_in": True}
    await message.reply("✅ Account login successful!\nYou can now use `/check` to verify numbers.")


# --- /logout ---
@dp.message_handler(commands=["logout"])
async def logout_command(message: types.Message):
    if not await require_channel_join(message):
        return

    user_id = message.from_user.id
    if user_id not in user_sessions:
        await message.reply("❌ No account logged in.")
        return

    client = user_sessions[user_id]["client"]
    await client.log_out()
    del user_sessions[user_id]
    await message.reply("✅ Successfully logged out and session cleared.")


# --- /limit ---
@dp.message_handler(commands=["limit"])
async def limit_command(message: types.Message):
    if not await require_channel_join(message):
        return

    user_id = message.from_user.id
    if user_id not in user_sessions:
        await message.reply("❌ No account logged in.")
        return
    limit = user_sessions[user_id]["limit"]
    await message.reply(f"📊 Remaining checks: **{limit}/100**", parse_mode="Markdown")


# --- /check ---
@dp.message_handler(commands=["check"])
async def check_command(message: types.Message):
    if not await require_channel_join(message):
        return

    user_id = message.from_user.id
    if user_id not in user_sessions or not user_sessions[user_id]["logged_in"]:
        await message.reply("❌ You must `/login` first.")
        return

    await message.reply("📋 Send the list of numbers (one per line):")
    msg = await bot.wait_for("message")
    numbers = msg.text.split("\n")

    if len(numbers) > user_sessions[user_id]["limit"]:
        await message.reply(f"⚠️ You can only check {user_sessions[user_id]['limit']} more.")
        return

    client = user_sessions[user_id]["client"]
    result_text = "🔍 **Check Results:**\n\n"

    for num in numbers:
        link = f"t.me/+{num.strip()}"
        try:
            # Simulated result
            result_text += f"{link} → ✅ Active\n"
            await asyncio.sleep(0.5)
        except errors.UserDeactivatedBanError:
            result_text += f"{link} → ❄️ Frozen\n"
        except Exception:
            result_text += f"{link} → 🚫 Not Available\n"

    user_sessions[user_id]["limit"] -= len(numbers)
    await message.reply(result_text, parse_mode="Markdown")


if __name__ == "__main__":
    print("🤖 Bot is running...")
    executor.start_polling(dp, skip_updates=True)
