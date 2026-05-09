# -*- coding: utf-8 -*-
"""
MTC Analytics - 多维度金融分析平台

主应用入口，注册所有 Blueprint，提供 Portal 首页。
启动方式：python app.py
"""

import os
import secrets
import logging
import time
import gzip
import hashlib

from flask import Flask, render_template, request, jsonify, session
from werkzeug.security import generate_password_hash

from core.config import SENSITIVE_FIELDS
from core.auth import validate_new_password
from core.llm_utils import (
    DEFAULT_LLM_BASE_URL, DEFAULT_LLM_BUDGET, DEFAULT_LLM_MODEL,
    get_model_token_limits, normalize_llm_base_url, normalize_llm_budget,
    normalize_llm_model,
)
from core.utils import load_json, save_json, encrypt_value, decrypt_value
from core.security import is_ip_banned, check_api_rate_limit, get_logger as get_security_logger
from blueprints.gold import gold_bp

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.join(_PROJECT_ROOT, "data")
SETTINGS_FILE = os.path.join(_DATA_DIR, "web_settings.json")


def create_app():
    app = Flask(__name__,
                template_folder='portal/templates',
                static_folder='static',
                static_url_path='/static')

    os.makedirs(os.path.join(_DATA_DIR, "reports"), exist_ok=True)

    _SECRET_KEY_FILE = os.path.join(_DATA_DIR, ".secret_key")

    def _load_or_create_secret_key():
        import fcntl
        os.makedirs(os.path.dirname(_SECRET_KEY_FILE) or ".", exist_ok=True)
        lock_file = _SECRET_KEY_FILE + ".lock"
        with open(lock_file, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                key = load_json(_SECRET_KEY_FILE)
                if key and key.get("secret_key"):
                    return key["secret_key"]
                new_key = secrets.token_hex(32)
                save_json(_SECRET_KEY_FILE, {"secret_key": new_key}, private=True)
                return new_key
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

    app.secret_key = _load_or_create_secret_key()
    _current_secret_key = app.secret_key

    @app.before_request
    def _sync_secret_key():
        nonlocal _current_secret_key
        try:
            key_data = load_json(_SECRET_KEY_FILE)
            if key_data and key_data.get("secret_key") and key_data["secret_key"] != _current_secret_key:
                _current_secret_key = key_data["secret_key"]
                app.secret_key = _current_secret_key
        except Exception:
            pass
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['SESSION_COOKIE_SECURE'] = os.environ.get('HTTPS', '').lower() in ('1', 'true', 'yes') or os.environ.get('FLASK_ENV') == 'production'
    app.config['PERMANENT_SESSION_LIFETIME'] = 3600 * 8
    app.config['MAX_CONTENT_LENGTH'] = 1 * 1024 * 1024

    app.register_blueprint(gold_bp, url_prefix='/gold')

    _security_logger = get_security_logger()

    @app.before_request
    def _security_middleware():
        ip = request.remote_addr or "0.0.0.0"
        if is_ip_banned(ip):
            _security_logger.warning("被封禁IP访问: %s %s", ip, request.path)
            return jsonify({"error": "访问被拒绝"}), 403

        if request.path.startswith("/gold/api/") and request.method != "OPTIONS":
            if not check_api_rate_limit(ip):
                return jsonify({"error": "请求过于频繁"}), 429

    @app.after_request
    def _set_security_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['X-XSS-Protection'] = '1; mode=block'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        if request.path.startswith("/gold/api/") and not response.content_type.startswith('text/event-stream'):
            response.headers['Content-Type'] = 'application/json; charset=utf-8'

        if request.path.startswith('/static/'):
            response.headers['Cache-Control'] = 'public, max-age=86400'
            if response.content_type and ('javascript' in response.content_type or 'css' in response.content_type):
                response.headers['Cache-Control'] = 'public, max-age=604800'

        if (request.path.startswith('/gold/api/') or request.path.startswith('/api/')) and not request.path.endswith('/price_stream'):
            if response.status_code == 200 and response.content_type and 'json' in response.content_type:
                response.headers['Cache-Control'] = 'private, max-age=30'
                try:
                    body = response.get_data()
                    if len(body) > 500:
                        accept_enc = request.headers.get('Accept-Encoding', '')
                        if 'gzip' in accept_enc:
                            compressed = gzip.compress(body, compresslevel=6)
                            if len(compressed) < len(body):
                                response.set_data(compressed)
                                response.headers['Content-Encoding'] = 'gzip'
                                response.headers['Vary'] = 'Accept-Encoding'
                except Exception:
                    pass

        return response

    @app.errorhandler(404)
    def _not_found(e):
        return jsonify({"error": "资源不存在"}), 404

    @app.errorhandler(413)
    def _request_too_large(e):
        return jsonify({"error": "请求体过大"}), 413

    @app.errorhandler(500)
    def _internal_error(e):
        return jsonify({"error": "服务器内部错误"}), 500

    @app.route("/")
    def portal():
        return render_template("portal.html")

    @app.route("/api/health")
    def health_check():
        return jsonify({"status": "ok", "version": "2.0.0", "name": "MTC Analytics"})

    @app.route("/api/change_password", methods=["POST"])
    def api_change_password():
        if not session.get("logged_in"):
            return jsonify({"ok": False, "error": "未登录"}), 401

        csrf_token = request.headers.get('X-CSRF-Token')
        if not csrf_token or csrf_token != session.get('csrf_token'):
            return jsonify({"ok": False, "error": "CSRF验证失败"}), 403

        data = request.json or {}
        new_pw = str(data.get("new_password", ""))
        confirm_pw = str(data.get("new_password_confirm", ""))

        err = validate_new_password(new_pw, confirm_pw)
        if err:
            return jsonify({"ok": False, "error": err}), 400

        settings = load_json(SETTINGS_FILE)
        if not settings:
            settings = {}
        settings["password_hash"] = generate_password_hash(new_pw, method='pbkdf2:sha256')
        save_json(SETTINGS_FILE, settings, private=True)

        new_secret = secrets.token_hex(32)
        save_json(_SECRET_KEY_FILE, {"secret_key": new_secret}, private=True)
        app.secret_key = new_secret

        session.clear()
        session["logged_in"] = True
        session.permanent = True
        session["csrf_token"] = secrets.token_hex(32)
        session["csrf_token_time"] = time.time()

        _security_logger.info("密码修改成功，已刷新secret_key使其他会话失效: IP=%s", request.remote_addr or "0.0.0.0")
        return jsonify({"ok": True, "csrf_token": session["csrf_token"]})

    @app.route("/api/portal_settings", methods=["GET"])
    def api_get_portal_settings():
        if not session.get("logged_in"):
            return jsonify({"error": "未登录"}), 401
        settings = load_json(SETTINGS_FILE) or {}
        result = {}
        for field in SENSITIVE_FIELDS:
            raw = decrypt_value(settings.get(field, ""), app.secret_key or "")
            if raw:
                result[f"{field}_masked"] = raw[:6] + "****" + raw[-4:] if len(raw) > 10 else "****"
            else:
                result[f"{field}_masked"] = ""
        try:
            llm_base_url = normalize_llm_base_url(settings.get("llm_base_url", DEFAULT_LLM_BASE_URL))
        except ValueError:
            llm_base_url = DEFAULT_LLM_BASE_URL
        try:
            llm_model = normalize_llm_model(settings.get("llm_model", DEFAULT_LLM_MODEL))
        except ValueError:
            llm_model = DEFAULT_LLM_MODEL
        llm_limits = get_model_token_limits(llm_model, settings)
        result["llm_base_url"] = llm_base_url
        result["llm_model"] = llm_model
        result["llm_budget"] = normalize_llm_budget(
            settings.get("llm_budget", settings.get("iteration_llm_budget", DEFAULT_LLM_BUDGET))
        )
        result["llm_context_window"] = llm_limits["context_window"]
        result["llm_max_output_tokens"] = llm_limits["max_output_tokens"]
        result["llm_model_known"] = llm_limits["known"]
        result["llm_budget_ratios"] = settings.get("llm_budget_ratios", {})
        result["iteration_llm_threshold"] = float(settings.get("iteration_llm_threshold", 0.4))
        result["llm_reasoning_interval"] = int(settings.get("llm_reasoning_interval", 6))
        result["telegram_chat_id"] = settings.get("telegram_chat_id", "")
        result["run_mode"] = settings.get("run_mode", os.environ.get("RUN_MODE", "web+schedule"))
        return jsonify(result)

    @app.route("/api/portal_settings", methods=["POST"])
    def api_save_portal_settings():
        if not session.get("logged_in"):
            return jsonify({"ok": False, "error": "未登录"}), 401
        csrf_token = request.headers.get('X-CSRF-Token')
        if not csrf_token or csrf_token != session.get('csrf_token'):
            return jsonify({"ok": False, "error": "CSRF验证失败"}), 403
        data = request.json or {}
        settings = load_json(SETTINGS_FILE) or {}
        if "llm_base_url" in data:
            try:
                settings["llm_base_url"] = normalize_llm_base_url(data["llm_base_url"])
            except ValueError as e:
                return jsonify({"ok": False, "error": str(e)}), 400
        if "llm_model" in data:
            try:
                settings["llm_model"] = normalize_llm_model(data["llm_model"])
            except ValueError as e:
                return jsonify({"ok": False, "error": str(e)}), 400
        if "telegram_chat_id" in data:
            settings["telegram_chat_id"] = str(data["telegram_chat_id"])[:500]
        if "llm_budget" in data:
            settings["llm_budget"] = normalize_llm_budget(data["llm_budget"])
        elif "llm_budget" not in settings and "iteration_llm_budget" in settings:
            settings["llm_budget"] = normalize_llm_budget(settings["iteration_llm_budget"])
        settings.pop("iteration_llm_budget", None)
        if "llm_budget_ratios" in data:
            ratios = data["llm_budget_ratios"]
            if isinstance(ratios, dict):
                valid_cats = {"diagnose", "reasoning", "news", "consensus"}
                clean = {}
                for cat in valid_cats:
                    if cat in ratios:
                        try:
                            v = float(ratios[cat])
                            if 0 <= v <= 1:
                                clean[cat] = v
                        except (TypeError, ValueError):
                            pass
                if clean:
                    settings["llm_budget_ratios"] = clean
        if "iteration_llm_threshold" in data:
            try:
                v = float(data["iteration_llm_threshold"])
                settings["iteration_llm_threshold"] = max(0.1, min(0.8, v))
            except (TypeError, ValueError):
                pass
        if "llm_reasoning_interval" in data:
            try:
                v = int(data["llm_reasoning_interval"])
                settings["llm_reasoning_interval"] = max(1, min(72, v))
            except (TypeError, ValueError):
                pass
        if "run_mode" in data:
            valid_modes = {"web", "schedule", "realtime", "web+schedule", "web+realtime"}
            mode = str(data["run_mode"]).strip()
            if mode in valid_modes:
                settings["run_mode"] = mode
        for field in SENSITIVE_FIELDS:
            if field in data:
                raw = str(data[field])[:200] if data[field] and not str(data[field]).startswith("****") else ""
                if raw:
                    settings[field] = encrypt_value(raw, app.secret_key or "")
                elif not str(data[field]).startswith("****"):
                    settings[field] = ""
        save_json(SETTINGS_FILE, settings, private=True)
        if any(k in data for k in ("llm_api_key", "llm_base_url", "llm_model")):
            try:
                from core.news_sentiment import reload_llm_config
                reload_llm_config()
            except Exception:
                pass
        return jsonify({"ok": True})

    return app


if __name__ == "__main__":
    app = create_app()
    print("\n" + "=" * 50)
    print("  MTC Analytics - 多维度金融分析平台")
    print("  访问地址: http://127.0.0.1:8080")
    print("=" * 50 + "\n")
    app.run(host="127.0.0.1", port=8080, debug=False)
