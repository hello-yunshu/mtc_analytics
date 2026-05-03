# -*- coding: utf-8 -*-
import time
import logging
import collections
import threading


_banned_ips = {}
_banned_ips_lock = threading.Lock()
BAN_THRESHOLD = 10
BAN_DURATION = 3600

_api_rate_limits = collections.defaultdict(lambda: {"count": 0, "first": 0})
_api_rate_lock = threading.Lock()
API_RATE_LIMIT = 120
API_RATE_WINDOW = 60

_login_attempts = collections.defaultdict(lambda: {"count": 0, "first": 0})
_login_attempts_lock = threading.Lock()
LOGIN_RATE_LIMIT = 5
LOGIN_RATE_WINDOW = 300

_security_logger = logging.getLogger("mtc.security")
_security_logger.setLevel(logging.INFO)
if not _security_logger.handlers:
    _handler = logging.FileHandler("data/security.log", encoding="utf-8")
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _security_logger.addHandler(_handler)


def is_ip_banned(ip):
    with _banned_ips_lock:
        if ip in _banned_ips:
            if time.time() - _banned_ips[ip] < BAN_DURATION:
                return True
            del _banned_ips[ip]
    return False


def ban_ip(ip):
    with _banned_ips_lock:
        _banned_ips[ip] = time.time()


def check_api_rate_limit(ip):
    with _api_rate_lock:
        now = time.time()
        record = _api_rate_limits[ip]
        if now - record["first"] > API_RATE_WINDOW:
            record["count"] = 0
            record["first"] = now
        record["count"] += 1
        if record["count"] > API_RATE_LIMIT:
            return False
        return True


def check_login_rate_limit(ip):
    with _login_attempts_lock:
        now = time.time()
        attempt = _login_attempts[ip]
        if now - attempt["first"] > LOGIN_RATE_WINDOW:
            attempt["count"] = 0
            attempt["first"] = now
        attempt["count"] += 1
        if attempt["count"] > LOGIN_RATE_LIMIT:
            return False
        return True


def record_failed_login(ip):
    with _login_attempts_lock:
        now = time.time()
        attempt = _login_attempts[ip]
        if now - attempt["first"] > LOGIN_RATE_WINDOW:
            attempt["count"] = 0
            attempt["first"] = now
        attempt["count"] += 1
        if attempt["count"] >= BAN_THRESHOLD:
            ban_ip(ip)
            _security_logger.warning("IP %s 已被封禁 (连续%d次登录失败)", ip, attempt["count"])
            del _login_attempts[ip]
        return attempt["count"]


def clear_login_attempts(ip):
    with _login_attempts_lock:
        _login_attempts.pop(ip, None)


def cleanup_expired_entries():
    now = time.time()
    with _api_rate_lock:
        expired = [ip for ip, r in _api_rate_limits.items() if now - r["first"] > API_RATE_WINDOW * 2]
        for ip in expired:
            del _api_rate_limits[ip]
    with _login_attempts_lock:
        expired = [ip for ip, r in _login_attempts.items() if now - r["first"] > LOGIN_RATE_WINDOW * 2]
        for ip in expired:
            del _login_attempts[ip]


def get_logger():
    return _security_logger
