# -*- coding: utf-8 -*-
import os
import time
import threading
import logging

from flask import render_template, request, jsonify, session, current_app

from core.auth import login_required, csrf_required, generate_csrf_token, api_ok, api_error
from core.sse import SSEManager
from core.cache import ThreadSafeCache
from core.settings import SettingsManager
from core.utils import load_json, save_json, encrypt_value, decrypt_value
from core.security import get_logger as get_security_logger

from . import sector_bp

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
SETTINGS_FILE = os.path.join(_PROJECT_ROOT, "data", "sector_settings.json")

_security_logger = get_security_logger()
_sse_manager = SSEManager(max_connections=10)
_settings_mgr = None


def _get_settings_mgr():
    global _settings_mgr
    if _settings_mgr is None:
        _settings_mgr = SettingsManager(
            settings_file=SETTINGS_FILE,
            secret_key_getter=lambda: current_app.secret_key or "",
        )
    return _settings_mgr


def _encrypt_value(plaintext: str) -> str:
    return encrypt_value(plaintext, current_app.secret_key or "")


def _decrypt_value(ciphertext: str) -> str:
    return decrypt_value(ciphertext, current_app.secret_key or "")


@sector_bp.route("/")
def index():
    return render_template("sector.html")


@sector_bp.route("/api/check_auth")
def api_check_auth():
    logged_in = session.get("logged_in", False)
    csrf = ""
    if logged_in:
        csrf = generate_csrf_token()
    return jsonify({"logged_in": logged_in, "csrf_token": csrf})


@sector_bp.route("/api/settings")
@login_required
def api_get_settings():
    mgr = _get_settings_mgr()
    settings = mgr.get()
    masked = mgr.mask_sensitive(settings)
    return jsonify(masked)


@sector_bp.route("/api/settings", methods=["POST"])
@login_required
@csrf_required
def api_save_settings():
    mgr = _get_settings_mgr()
    data = request.json or {}
    settings = mgr.get()
    mgr.apply_str_fields(settings, data, ["custom_field_1", "custom_field_2"])
    mgr.apply_sensitive_fields(settings, data)
    mgr.save(settings)
    return api_ok()
