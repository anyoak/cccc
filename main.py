import os
import asyncio
import random
import string
import sqlite3
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils import executor

from config import BOT_TOKEN, ADMIN_ID, FEE_AMOUNT, PAYMENT_TIMEOUT, DB_PATH, BASE_URL, BSCSCAN_API_KEY, BSC_RPC
from db_init import init_db, conn as DB_CONN
from encryption_utils import encrypt_privkey, decrypt_privkey

# Optional: web3 for on-chain actions (address generation & withdraw)
from web3 import Web3, HTTPProvider
w3 = Web3(HTTPProvider(BSC_RPC))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)
cur = DB_CONN.cursor()

# ------------------------
# Helpers
# ------------------------
def generate_id(length=8):
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

def generate_order_id(length=12):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

def create_deposit_address():  # creates a new private key + address (hot)
    acct = w3.eth.account.create()  # random account
    priv = acct.key.hex()[2:] if acct.key.hex().startswith("0x") else acct.key.hex()
    addr = acct.address
    return addr, priv

def insert_link(link_id, order_id, amount, address, priv_enc, admin_id):
    created_at = datetime.utcnow().isoformat()
    cur.execute("INSERT INTO links (link_id, order_id, amount, address, priv_enc, admin_id, created_at, expired) VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
                (link_id, order_id, amount, address, priv_enc, admin_id, created_at))
    DB_CONN.commit()

def mark_link_expired(link_id):
    cur.execute("UPDATE links SET expired=1 WHERE link_id=?", (link_id,))
    DB_CONN.commit()

def fetch_link(link_id):
    cur.execute("SELECT link_id, order_id, amount, address, created_at, expired FROM links WHERE link_id=?", (link_id,))
    return cur.fetchone()

def save_payment(payment_id, order_id, user_id, full_name, txid, amount_paid, status):
    created_at = datetime.utcnow().isoformat()
    cur.execute("INSERT INTO payments (payment_id, order_id, user_id, full_name, txid, amount_paid, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (payment_id, order_id, user_id, full_name, txid, amount_paid, status, created_at))
    DB_CONN.commit()

# ------------------------
# Admin: generate_link
# Usage: /generate_link <amount>
# It will create a unique deposit address and store encrypted private key.
# ------------------------
@dp.message_handler(commands=['generate_link'])
async def cmd_generate_link(msg: types.Message):
    if msg.from_user.id != ADMIN_ID:
        await msg.reply("Access denied.")
        return
    args = msg.get_args().split()
    if len(args) < 1:
        await msg.reply("Usage: /generate_link <amount>\nExample: /generate_link 10.5")
        return
    try:
        amount = float(args[0])
    except:
        await msg.reply("Invalid amount.")
        return

    # create deposit account
    address, priv = create_deposit_address()
    priv_enc = encrypt_privkey(priv)
    link_id = generate_id(10)
    order_id = generate_order_id(10)

    insert_link(link_id, order_id, amount, address, priv_enc, msg.from_user.id)
    link = f"{BASE_URL}?start={link_id}"  # user clicks this to open bot with link_id param
    await msg.reply(f"✅ Link generated\nLink: {link}\nOrder ID: {order_id}\nAmount: {amount} USDT\nAddress: `{address}`\nFee: {FEE_AMOUNT} USDT\n\nSend this link to the user.", parse_mode="Markdown")

# ------------------------
# User: /start <link_id>
# ------------------------
@dp.message_handler(commands=['start'])
async def cmd_start(msg: types.Message):
    args = msg.get_args().strip()
    if not args:
        await msg.reply("Access denied. Please use admin generated link.")
        return

    row = fetch_link(args)
    if not row:
        await msg.reply("Invalid or expired link.")
        return

    link_id, order_id, amount, address, created_at, expired = row
    if expired:
        await msg.reply("This payment link has expired.")
        return

    total_amount = round(amount + FEE_AMOUNT, 6)
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton(f"Copy Address & Pay {total_amount} USDT", callback_data=f"pay|{link_id}"))
    kb.add(InlineKeyboardButton("I HAVE PAID (Submit TXID)", callback_data=f"submit_tx|{link_id}"))

    # show address + QR image as message (we'll send address text; for QR we can send a separate image)
    text = (f"💸 *Payment Page*\n\nOrder ID: `{order_id}`\nAmount: *{amount} USDT*\nFee: *{FEE_AMOUNT} USDT*\nTotal: *{total_amount} USDT*\n\n"
            f"Address: `{address}`\n\nPress the button to copy address & pay. After payment, submit TXID using *I HAVE PAID* button.\n\n"
            f"⏳ Payment window: 30 minutes.")
    await msg.reply(text, parse_mode="Markdown", reply_markup=kb)

# ------------------------
# Callback handler for pay & submit_tx
# ------------------------
@dp.callback_query_handler(lambda c: c.data and c.data.startswith(("pay|","submit_tx|")))
async def cb_pay_submit(call: types.CallbackQuery):
    data = call.data.split("|")
    action = data[0]
    link_id = data[1]

    row = fetch_link(link_id)
    if not row:
        await call.answer("Invalid or expired link.", show_alert=True)
        return
    _, order_id, amount, address, created_at, expired = row
    if expired:
        await call.answer("This link is expired.", show_alert=True)
        return

    if action == "pay":
        # show address as alert to enable copy on mobile + send QR
        await call.answer(f"Address copied (tap to copy): {address}", show_alert=True)
        # send QR image as new message
        # generate QR and send
        import qrcode, tempfile
        img = qrcode.make(address)
        tmp = tempfile.mktemp(suffix=".png")
        img.save(tmp)
        await bot.send_photo(chat_id=call.from_user.id, photo=open(tmp, "rb"), caption=f"Send *{round(amount+FEE_AMOUNT,6)} USDT* to this address.\nInclude fee {FEE_AMOUNT} USDT.", parse_mode="Markdown")
        try:
            os.remove(tmp)
        except: pass

        # start countdown animation in the same chat (edits)
        m = await bot.send_message(call.from_user.id, "⏳ Waiting for payment... 30:00")
        # countdown micro loop: update every 30s or so (not too frequent to avoid rate limits)
        total = PAYMENT_TIMEOUT
        interval = 15  # seconds per update (can be 30)
        elapsed = 0
        while elapsed < total:
            remain = total - elapsed
            minutes = remain // 60
            seconds = remain % 60
            try:
                await bot.edit_message_text(f"⏳ Waiting for payment... {int(minutes):02d}:{int(seconds):02d}", call.from_user.id, m.message_id)
            except:
                pass
            await asyncio.sleep(interval)
            elapsed += interval
            # you could optionally poll blockchain here to detect incoming tx automatically
            # quick break if detected - e.g., check_payment_for_order(order_id)
        # after timeout
        # if no payment — expire link
        mark_link_expired(link_id)
        try:
            await bot.edit_message_text(f"❌ Payment window expired for Order ID {order_id}.", call.from_user.id, m.message_id)
        except:
            pass
        await bot.send_message(call.from_user.id, "Payment window expired. Please request a new link.")
        return

    if action == "submit_tx":
        # ask user to send TXID via reply
        await call.answer()
        await bot.send_message(call.from_user.id, "Send the TXID (transaction hash) as a message reply here. Example: 0x123abc...")
        # set a temporary state — simple approach: wait for next message from user and treat as TXID
        # For simplicity, we will register a short-lived handler using register_message_handler filtering by user_id.
        async def tx_handler(tx_msg: types.Message):
            txid = tx_msg.text.strip()
            # basic validation
            if not txid.startswith("0x") or len(txid) < 10:
                await tx_msg.reply("Invalid TXID format. Please send a correct transaction hash.")
                return
            # Here ideally validate on-chain: check tx to `address` and amount >= required
            # For now we save payment as pending and notify admin
            payment_id = generate_id(12)
            full_name = f"{tx_msg.from_user.full_name}"
            save_payment(payment_id, order_id, tx_msg.from_user.id, full_name, txid, None, "Pending")
            await tx_msg.reply("Thanks. We received your TXID. Waiting for confirmation. Admin will be notified.")
            # notify admin
            await bot.send_message(ADMIN_ID, f"New payment submitted for Order {order_id}\nUser: {full_name} ({tx_msg.from_user.id})\nTXID: {txid}\nPlease verify.")
            # unregister this handler - aiogram doesn't support unregister easily; we'll just return and ignore duplicates
        # register the one-off handler
        dp.register_message_handler(tx_handler, lambda m: m.from_user.id == call.from_user.id, content_types=types.ContentTypes.TEXT, state=None)
        return

# ------------------------
# Admin: /total_collection & /history
# ------------------------
@dp.message_handler(commands=['total_collection'])
async def cmd_total_collection(msg: types.Message):
    if msg.from_user.id != ADMIN_ID:
        await msg.reply("Access denied")
        return
    # sum successful payments amounts (assuming amount_paid recorded)
    cur.execute("SELECT SUM(amount_paid) FROM payments WHERE status='Success'")
    total = cur.fetchone()[0] or 0
    await msg.reply(f"Total collected (successful payments): {total} USDT")

@dp.message_handler(commands=['history'])
async def cmd_history(msg: types.Message):
    if msg.from_user.id != ADMIN_ID:
        await msg.reply("Access denied")
        return
    cur.execute("SELECT payment_id, order_id, user_id, txid, amount_paid, status, created_at FROM payments ORDER BY created_at DESC LIMIT 50")
    rows = cur.fetchall()
    lines = []
    for r in rows:
        lines.append(f"{r[0]} | {r[1]} | user:{r[2]} | {r[4]} | {r[5]} | {r[6]}")
    if not lines:
        await msg.reply("No history yet.")
    else:
        await msg.reply("\n".join(lines))

# ------------------------
# Admin: /withdraw_all <target_address>  (stub)
# ------------------------
@dp.message_handler(commands=['withdraw_all'])
async def cmd_withdraw_all(msg: types.Message):
    if msg.from_user.id != ADMIN_ID:
        await msg.reply("Access denied")
        return
    args = msg.get_args().split()
    if len(args) != 1:
        await msg.reply("Usage: /withdraw_all <target_bsc_address>")
        return
    target = args[0]
    # Implementation note: to withdraw you need control of private keys of the hot wallet holding funds.
    # This function should aggregate balances to one account and create/send a tx using web3 and private key.
    await msg.reply("Withdraw invoked. This demo stub does NOT actually send funds. Implement web3 sendTransaction here using the hot wallet's private key.")
    # Real implementation would:
    # 1. calculate total balance on hot wallet
    # 2. build tx, sign with private key, send
    # 3. log txid and notify admin
    return

# ------------------------
# Admin: /export_keys <pdf_password>
# ------------------------
from export_pdf import export_all_keys_to_pdf
@dp.message_handler(commands=['export_keys'])
async def cmd_export_keys(msg: types.Message):
    if msg.from_user.id != ADMIN_ID:
        await msg.reply("Access denied")
        return
    args = msg.get_args().split()
    if len(args) != 1:
        await msg.reply("Usage: /export_keys <pdf_password>")
        return
    pdf_password = args[0]
    await msg.reply("Starting export. This may take a moment...")
    out_file = export_all_keys_to_pdf(msg.from_user.id, f"export_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}", pdf_password)
    await bot.send_document(chat_id=ADMIN_ID, document=open(out_file, "rb"))
    try:
        os.remove(out_file)
    except:
        pass
    await msg.reply("Export complete and temporary file removed.")

# ------------------------
# Optional: BscScan polling function (simple)
# This is a helper to implement automatic detection. You MUST adapt to your BSCSCAN API and logic.
# ------------------------
import requests
def check_payment_on_chain(address, required_amount):
    """
    Query BscScan for recent txs to `address`. This is a simple demo. Use BscScan / web3 for robust solution.
    Returns txid if found else None.
    """
    if not BSCSCAN_API_KEY:
        return None
    try:
        url = f"https://api.bscscan.com/api?module=account&action=txlist&address={address}&startblock=0&endblock=99999999&page=1&offset=10&sort=desc&apikey={BSCSCAN_API_KEY}"
        resp = requests.get(url, timeout=10).json()
        if resp.get("status") != "1":
            return None
        for tx in resp.get("result", []):
            # check if tx is incoming to address and value >= required_amount (convert wei->ether)
            if tx.get("to","").lower() == address.lower():
                value_wei = int(tx.get("value", "0"))
                value_eth = w3.fromWei(value_wei, "ether")
                if float(value_eth) >= float(required_amount):
                    # found
                    return tx.get("hash")
    except Exception as e:
        print("BSCSCAN error:", e)
    return None

# ------------------------
# Startup
# ------------------------
if __name__ == "__main__":
    print("Bot starting...")
    executor.start_polling(dp, skip_updates=True)
