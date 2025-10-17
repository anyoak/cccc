import base64, os
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.fernet import Fernet
from config import MASTER_SECRET

# NOTE: in production use KMS or securely store salt separately
SALT = b"app_static_salt_v1_please_change"

def derive_key(master_secret: str) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=SALT,
        iterations=390000,
    )
    return base64.urlsafe_b64encode(kdf.derive(master_secret.encode()))

def get_fernet():
    key = derive_key(MASTER_SECRET)
    return Fernet(key)

def encrypt_privkey(privkey_hex: str) -> str:
    f = get_fernet()
    token = f.encrypt(privkey_hex.encode())
    return token.decode()

def decrypt_privkey(token_str: str) -> str:
    f = get_fernet()
    return f.decrypt(token_str.encode()).decode()
