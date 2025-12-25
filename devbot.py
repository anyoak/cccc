import asyncio
from telethon import TelegramClient, events

# ========= CONFIG =========
api_id = 123456          # তোমার api_id
api_hash = "API_HASH"    # তোমার api_hash
group_id = -1003437559135

TARGET_BOTS = {
    "genesislillianbot",
    "youyou2323bot",
    "rachelmcadamsbot"
}
# ==========================

client = TelegramClient("bot_forward_cleaner", api_id, api_hash)


@client.on(events.NewMessage(chats=group_id))
async def handler(event):
    try:
        msg = event.message

        # Service/system message skip
        if msg.action:
            return

        sender = await msg.get_sender()

        # Only bot messages
        if not sender or not sender.bot:
            return

        username = (sender.username or "").lower()

        # Only target bots
        if username not in TARGET_BOTS:
            return

        # Loop prevent
        if msg.fwd_from:
            return

        # Forward message (copy)
        await client.forward_messages(
            entity=group_id,
            messages=msg,
            from_peer=group_id
        )

        # Delete original bot message
        await msg.delete()

        print(f"✅ Forwarded & deleted: @{username}")

    except Exception as e:
        print("❌ Handler error:", e)


async def main():
    print("🚀 UserBot starting...")
    await client.start()
    print("✅ UserBot running (no auto-off, no miss)")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
