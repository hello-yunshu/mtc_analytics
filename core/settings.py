# -*- coding: utf-8 -*-
import time
import threading
from typing import Optional, Dict, Any, Callable

from core.utils import load_json, save_json, encrypt_value, decrypt_value
from core.config import SENSITIVE_FIELDS


class SettingsManager:
    def __init__(self, settings_file: str, secret_key_getter: Callable[[], str], ttl: float = 5.0):
        self._settings_file = settings_file
        self._secret_key_getter = secret_key_getter
        self._ttl = ttl
        self._cache = {"data": None, "time": 0, "lock": threading.Lock()}

    def get(self) -> dict:
        with self._cache["lock"]:
            now = time.time()
            if self._cache["data"] and now - self._cache["time"] < self._ttl:
                return self._cache["data"]
        settings = load_json(self._settings_file)
        if not settings:
            settings = {}
        with self._cache["lock"]:
            self._cache["data"] = settings
            self._cache["time"] = time.time()
        return settings

    def save(self, settings: dict, private: bool = True):
        save_json(self._settings_file, settings, private=private)
        self.invalidate()

    def invalidate(self):
        with self._cache["lock"]:
            self._cache["data"] = None
            self._cache["time"] = 0

    def encrypt(self, plaintext: str) -> str:
        return encrypt_value(plaintext, self._secret_key_getter() or "")

    def decrypt(self, ciphertext: str) -> str:
        return decrypt_value(ciphertext, self._secret_key_getter() or "")

    def mask_sensitive(self, settings: dict, fields: Optional[set] = None) -> dict:
        if fields is None:
            fields = SENSITIVE_FIELDS
        result = dict(settings)
        for field in fields:
            raw = self.decrypt(result.get(field, ""))
            if raw:
                result[f"{field}_masked"] = raw[:6] + "****" + raw[-4:] if len(raw) > 10 else "****"
            else:
                result[f"{field}_masked"] = ""
            result.pop(field, None)
        return result

    def apply_sensitive_fields(self, settings: dict, data: dict, fields: Optional[set] = None):
        if fields is None:
            fields = SENSITIVE_FIELDS
        for field in fields:
            if field in data:
                raw = str(data[field])[:200] if data[field] and not str(data[field]).startswith("****") else ""
                if raw:
                    settings[field] = self.encrypt(raw)
                elif not str(data[field]).startswith("****"):
                    settings[field] = ""

    def apply_int_fields(self, settings: dict, data: dict, field_ranges: Dict[str, tuple]):
        for field, (lo, hi) in field_ranges.items():
            if field in data:
                try:
                    val = int(data[field])
                    settings[field] = max(lo, min(hi, val))
                except (ValueError, TypeError):
                    pass

    def apply_float_fields(self, settings: dict, data: dict, field_ranges: Dict[str, tuple]):
        for field, (lo, hi) in field_ranges.items():
            if field in data:
                try:
                    val = float(data[field])
                    settings[field] = max(lo, min(hi, val))
                except (ValueError, TypeError):
                    pass

    def apply_str_fields(self, settings: dict, data: dict, fields: list, max_len: int = 500):
        for field in fields:
            if field in data:
                settings[field] = str(data[field])[:max_len]
