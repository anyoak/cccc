import sqlite3
from config import DB_PATH

def init_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cur = conn.cursor()
    # links: each generated link + derived deposit address + encrypted privkey (if created here)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS links (
        link_id TEXT PRIMARY KEY,
        order_id TEXT,
        amount REAL,
        address TEXT,
        priv_enc TEXT,          -- encrypted private key hex (optional if you generate keys here)
        admin_id INTEGER,
        created_at TEXT,
        expired INTEGER DEFAULT 0
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS payments (
        payment_id TEXT PRIMARY KEY,
        order_id TEXT,
        user_id INTEGER,
        full_name TEXT,
        txid TEXT,
        amount_paid REAL,
        status TEXT,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS exports (
        export_id INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_id INTEGER,
        file_path TEXT,
        created_at TEXT
    )
    """)
    conn.commit()
    return conn

# for quick import
conn = init_db()
cur = conn.cursor()
