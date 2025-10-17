import os

# Telegram
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8441847556:AAGO_XbbN_eJJrL944JCO6uzHW7TDjS5VEQ)
ADMIN_ID = int(os.environ.get("ADMIN_ID", "6083895678"))  # replace with your Telegram ID

# Security / encryption
MASTER_SECRET = os.environ.get("MASTER_SECRET", "Dark112200")  # use env in prod

# Fee and timeouts
FEE_AMOUNT = float(os.environ.get("FEE_AMOUNT", "0.30"))
PAYMENT_TIMEOUT = int(os.environ.get("PAYMENT_TIMEOUT", str(30 * 60)))  # 30 minutes

# DB
DB_PATH = os.environ.get("DB_PATH", "payments.db")

# BSC / blockchain (for auto detection - optional)
BSCSCAN_API_KEY = os.environ.get("BSCSCAN_API_KEY", "AEUYN4PZ5XMBK5CFWHZ8MY7VZ83SGAWZSX")  # optional - use to poll txs
BSC_RPC = os.environ.get("BSC_RPC", "https://bsc-dataseed.binance.org/")  # web3 provider

# Misc
BASE_URL = os.environ.get("BASE_URL", "https://t.me/TapNCollectBot")  # use your bot link template for start param
