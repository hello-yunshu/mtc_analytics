# -*- coding: utf-8 -*-
import json
import os
import base64
import hashlib
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def load_json(filepath: str) -> Optional[dict]:
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning("读取 %s 失败: %s", filepath, e)
    return None


def save_json(filepath: str, data, private: bool = False):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    if private:
        try:
            os.chmod(filepath, 0o600)
        except OSError:
            pass


def _derive_fernet_key(secret_key: str) -> bytes:
    secret = secret_key.encode("utf-8") if secret_key else b""
    if not secret:
        raise ValueError("secret_key is required for encryption")
    key_material = hashlib.pbkdf2_hmac("sha256", secret, b"gold_tracker_fernet_salt", 200000)
    return base64.urlsafe_b64encode(key_material[:32])


def encrypt_value(plaintext: str, secret_key: str) -> str:
    if not plaintext:
        return ""
    try:
        from cryptography.fernet import Fernet
        key = _derive_fernet_key(secret_key)
        f = Fernet(key)
        encrypted = f.encrypt(plaintext.encode("utf-8"))
        return "fnet:" + encrypted.decode("ascii")
    except ImportError:
        raise RuntimeError("cryptography 库未安装，无法安全加密。请运行: pip install cryptography")


def decrypt_value(ciphertext: str, secret_key: str) -> str:
    if not ciphertext:
        return ""
    if ciphertext.startswith("fnet:"):
        try:
            from cryptography.fernet import Fernet
            key = _derive_fernet_key(secret_key)
            f = Fernet(key)
            decrypted = f.decrypt(ciphertext[5:].encode("ascii"))
            return decrypted.decode("utf-8")
        except ImportError:
            logger.error("cryptography 库未安装，无法解密 Fernet 密文")
            return ""
        except Exception:
            return ""
    if ciphertext.startswith("enc:"):
        logger.warning("检测到不安全的旧格式加密数据，请重新加密")
        return ""
    return ciphertext


def is_trading_hours():
    from core.gold_price import get_market_status
    return get_market_status().get("status") == "open"
