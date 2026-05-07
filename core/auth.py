# -*- coding: utf-8 -*-
import re
import secrets
import time
import logging
import os
from functools import wraps

from flask import session, request, jsonify, Blueprint
from werkzeug.security import generate_password_hash, check_password_hash

from core.utils import load_json, save_json
from core.security import (
    check_login_rate_limit, record_failed_login,
    clear_login_attempts, get_logger as get_security_logger,
)

CSRF_TOKEN_EXPIRY = 3600

_security_logger = get_security_logger()


def api_ok(**kwargs):
    result = {"ok": True}
    result.update(kwargs)
    return jsonify(result)


def api_error(msg, status=400):
    return jsonify({"ok": False, "error": msg}), status


def verify_password(password, stored_hash):
    return check_password_hash(stored_hash, password)


def generate_csrf_token():
    if 'csrf_token' not in session or 'csrf_token_time' not in session:
        session['csrf_token'] = secrets.token_hex(32)
        session['csrf_token_time'] = time.time()
    elif time.time() - session.get('csrf_token_time', 0) > CSRF_TOKEN_EXPIRY:
        session['csrf_token'] = secrets.token_hex(32)
        session['csrf_token_time'] = time.time()
    return session['csrf_token']


def validate_csrf():
    token = request.headers.get('X-CSRF-Token')
    if not token or token != session.get('csrf_token'):
        return False
    return True


def csrf_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not validate_csrf():
            return jsonify({"error": "CSRF验证失败"}), 403
        return f(*args, **kwargs)
    return decorated


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return jsonify({"error": "未登录"}), 401
        return f(*args, **kwargs)
    return decorated


def get_or_create_default_password(data_dir):
    pw_file = os.path.join(data_dir, ".default_password")
    initial_pw_file = os.path.join(data_dir, ".initial_password")
    lock_file = pw_file + ".lock"
    os.makedirs(os.path.dirname(pw_file), exist_ok=True)
    import fcntl
    with open(lock_file, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            existing = load_json(pw_file)
            if existing and existing.get("password_hash"):
                return existing["password_hash"]
            random_pw = secrets.token_urlsafe(12)
            pw_hash = generate_password_hash(random_pw, method='pbkdf2:sha256')
            save_json(pw_file, {"password_hash": pw_hash}, private=True)
            try:
                with open(initial_pw_file, "w", encoding="utf-8") as f:
                    f.write(random_pw)
                os.chmod(initial_pw_file, 0o600)
            except OSError:
                pass
            _security_logger.warning("首次启动 - 已生成随机登录密码: %s", random_pw)
            print(f"\n{'='*50}")
            print(f"  首次启动 - 已生成随机登录密码")
            print(f"  密码: {random_pw}")
            print(f"  请妥善保存，此密码仅显示一次")
            print(f"{'='*50}\n")
            return pw_hash
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def validate_new_password(new_pw, confirm_pw):
    if not new_pw:
        return "密码不能为空"
    if len(new_pw) < 8:
        return "密码长度至少8位"
    if not re.search(r'[A-Za-z]', new_pw):
        return "密码必须包含英文字母"
    if not (re.search(r'[0-9]', new_pw) or re.search(r'[^A-Za-z0-9]', new_pw)):
        return "密码必须包含数字或标点符号"
    if new_pw != confirm_pw:
        return "两次输入的密码不一致"
    return None


def create_auth_blueprint(settings_file, secret_key_file, data_dir):
    auth_bp = Blueprint('auth', __name__)

    DEFAULT_PASSWORD_HASH = get_or_create_default_password(data_dir)

    if len(DEFAULT_PASSWORD_HASH) == 64 and all(c in '0123456789abcdef' for c in DEFAULT_PASSWORD_HASH):
        _security_logger.warning("检测到旧式SHA256密码哈希，请通过Web界面重新设置密码以升级安全性")
        print("  [安全警告] 检测到旧式SHA256密码哈希，请通过Web界面重新设置密码")

    _settings_cache = {"data": None, "time": 0, "lock": __import__('threading').Lock()}
    _SETTINGS_CACHE_TTL = 5

    def _get_settings():
        import threading
        with _settings_cache["lock"]:
            now = time.time()
            if _settings_cache["data"] and now - _settings_cache["time"] < _SETTINGS_CACHE_TTL:
                return _settings_cache["data"]
        settings = load_json(settings_file)
        if not settings:
            settings = {"password_hash": DEFAULT_PASSWORD_HASH}
            save_json(settings_file, settings, private=True)
        with _settings_cache["lock"]:
            _settings_cache["data"] = settings
            _settings_cache["time"] = time.time()
        return settings

    def _invalidate_settings_cache():
        with _settings_cache["lock"]:
            _settings_cache["data"] = None
            _settings_cache["time"] = 0

    @auth_bp.route("/api/login", methods=["POST"])
    def api_login():
        ip = request.remote_addr or '0.0.0.0'
        if not check_login_rate_limit(ip):
            return jsonify({"ok": False, "error": "登录尝试过于频繁，请稍后再试"}), 429
        data = request.json or {}
        password = data.get("password", "")
        if not password:
            return jsonify({"ok": False, "error": "密码不能为空"}), 400
        settings = _get_settings()
        pw_hash = settings.get("password_hash", DEFAULT_PASSWORD_HASH)
        if verify_password(password, pw_hash):
            session.clear()
            session["logged_in"] = True
            session.permanent = True
            csrf = generate_csrf_token()
            clear_login_attempts(ip)
            _security_logger.info("登录成功: IP=%s", ip)
            return jsonify({"ok": True, "csrf_token": csrf})
        fail_count = record_failed_login(ip)
        _security_logger.warning("登录失败: IP=%s 第%d次", ip, fail_count)
        return jsonify({"ok": False, "error": "密码错误"}), 403

    @auth_bp.route("/api/logout", methods=["POST"])
    @csrf_required
    def api_logout():
        session.pop("logged_in", None)
        session.pop("csrf_token", None)
        session.pop("csrf_token_time", None)
        return jsonify({"ok": True})

    @auth_bp.route("/api/check_auth")
    def api_check_auth():
        logged_in = session.get("logged_in", False)
        csrf = ""
        if logged_in:
            csrf = generate_csrf_token()
        return jsonify({"logged_in": logged_in, "csrf_token": csrf})

    @auth_bp.route("/api/change_password", methods=["POST"])
    def api_change_password():
        if not session.get("logged_in"):
            return jsonify({"ok": False, "error": "未登录"}), 401
        if not validate_csrf():
            return jsonify({"ok": False, "error": "CSRF验证失败"}), 403

        data = request.json or {}
        new_pw = str(data.get("new_password", ""))
        confirm_pw = str(data.get("new_password_confirm", ""))

        err = validate_new_password(new_pw, confirm_pw)
        if err:
            return jsonify({"ok": False, "error": err}), 400

        settings = load_json(settings_file)
        if not settings:
            settings = {}
        settings["password_hash"] = generate_password_hash(new_pw, method='pbkdf2:sha256')
        save_json(settings_file, settings, private=True)
        _invalidate_settings_cache()

        new_secret = secrets.token_hex(32)
        save_json(secret_key_file, {"secret_key": new_secret}, private=True)

        from flask import current_app
        current_app.secret_key = new_secret

        session.clear()
        session["logged_in"] = True
        session.permanent = True
        session["csrf_token"] = secrets.token_hex(32)
        session["csrf_token_time"] = time.time()

        _security_logger.info("密码修改成功，已刷新secret_key使其他会话失效: IP=%s", request.remote_addr or "0.0.0.0")
        return jsonify({"ok": True, "csrf_token": session["csrf_token"]})

    return auth_bp
