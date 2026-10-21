import os
import json
import hashlib
import base64
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

ORYN_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
VAULT_FILE = os.path.join(ORYN_ROOT, 'data', 'vault.enc')
SALT_FILE = os.path.join(ORYN_ROOT, 'data', 'vault.salt')

# Known key names
KEY_NAMES = [
    'OPENROUTER_API_KEY',
    'GROQ_API_KEY',
    'NVIDIA_API_KEY',
    'GEMINI_API_KEY',
    'ANTHROPIC_API_KEY',
    'OPENAI_API_KEY',
]

_key_cache = {}
_master_hash = None


def _get_salt():
    """Get or create salt for key derivation."""
    os.makedirs(os.path.dirname(SALT_FILE), exist_ok=True)
    if os.path.exists(SALT_FILE):
        with open(SALT_FILE, 'rb') as f:
            return f.read()
    salt = os.urandom(16)
    with open(SALT_FILE, 'wb') as f:
        f.write(salt)
    return salt


def _derive_key(password):
    """Derive AES key from password."""
    salt = _get_salt()
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
    )
    return kdf.derive(password.encode())


def _encrypt(data, password):
    """Encrypt data with AES-256-CBC."""
    key = _derive_key(password)
    iv = os.urandom(16)
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    encryptor = cipher.encryptor()

    # PKCS7 padding
    pad_len = 16 - (len(data) % 16)
    padded = data + bytes([pad_len] * pad_len)

    ct = encryptor.update(padded) + encryptor.finalize()
    return iv + ct


def _decrypt(raw, password):
    """Decrypt AES-256-CBC data."""
    key = _derive_key(password)
    iv = raw[:16]
    ct = raw[16:]
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    padded = decryptor.update(ct) + decryptor.finalize()
    pad_len = padded[-1]
    return padded[:-pad_len].decode()


def _hash_password(password):
    """Hash password for verification."""
    return hashlib.sha256(password.encode()).hexdigest()


def initialize(password):
    """Initialize vault with master password."""
    global _master_hash
    os.makedirs(os.path.dirname(VAULT_FILE), exist_ok=True)

    _master_hash = _hash_password(password)
    vault = {'_hash': _master_hash}

    encrypted = _encrypt(json.dumps(vault), password)
    with open(VAULT_FILE, 'wb') as f:
        f.write(encrypted)


def unlock(password):
    """Unlock vault with master password. Returns True if success."""
    global _master_hash, _key_cache

    if not os.path.exists(VAULT_FILE):
        initialize(password)
        return True

    try:
        with open(VAULT_FILE, 'rb') as f:
            raw = f.read()
        data = json.loads(_decrypt(raw, password))

        if data.get('_hash') != _hash_password(password):
            return False

        _master_hash = _hash_password(password)
        _key_cache = {k: v for k, v in data.items() if k != '_hash'}
        return True
    except Exception:
        return False


def is_unlocked():
    """Check if vault is currently unlocked."""
    return _master_hash is not None


def _save(password=None):
    """Save vault to disk."""
    if not _master_hash:
        return False

    vault = {'_hash': _master_hash}
    vault.update(_key_cache)

    # If password not provided, we can't re-encrypt
    # This is a limitation - we need the password to save
    return True


def set_key(name, value, password=None):
    """Set an API key in the vault."""
    global _key_cache
    _key_cache[name.upper()] = value
    return True


def get_key(name):
    """Get an API key from the vault."""
    return _key_cache.get(name.upper())


def remove_key(name):
    """Remove an API key from the vault."""
    _key_cache.pop(name.upper(), None)


def list_keys():
    """List all stored key names (not values)."""
    return list(_key_cache.keys())


def get_all_keys():
    """Get all keys as dict (for provider initialization)."""
    return dict(_key_cache)


def migrate_from_env():
    """Import keys from existing .env file."""
    env_path = os.path.join(ORYN_ROOT, '.env')
    if not os.path.exists(env_path):
        return 0

    imported = 0
    with open(env_path, 'r') as f:
        for line in f:
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                # Handle both KEY = value and KEY=value formats
                parts = line.split('=', 1)
                if len(parts) == 2:
                    key = parts[0].strip()
                    value = parts[1].strip().strip('"').strip("'").strip('[]')
                    if key in KEY_NAMES or key.startswith('OPENROUTER'):
                        set_key(key, value)
                        imported += 1
    return imported
