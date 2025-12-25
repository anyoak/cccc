from telethon import TelegramClient, events

# ========= CONFIG =========
api_id = 38691424      # <-- তোমার api_id
api_hash = "c5dced87ba4b0a8c655b9682eaf5c742"    # <-- তোমার api_hash
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

        # Service message skip
        if msg.action:
            return

        sender = await msg.get_sender()

        # Sender must be bot
        if not sender or not sender.bot:
            return

        username = (sender.username or "").lower()

        # Only selected bots
        if username not in TARGET_BOTS:
            return

        # Avoid loop
        if msg.fwd_from:
            return

        # Forward (copy) message to same group
        await client.forward_messages(
            entity=group_id,
            messages=msg,
            from_peer=group_id
        )

        # Delete original bot message
        await msg.delete()

        print(f"✅ Processed bot message from @{username}")

    except Exception as e:
        print("❌ Error:", e)


print("🚀 Bot message forward+delete UserBot running...")
client.start()
client.run_until_disconnected()
