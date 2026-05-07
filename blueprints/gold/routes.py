# -*- coding: utf-8 -*-
"""
Gold Analytics Blueprint - Routes

All routes from the original web_app.py, adapted for Blueprint pattern.
API routes are prefixed with /gold via Blueprint registration.
"""

import json
import re
import os
import time
import math
import threading
import logging
from datetime import datetime, timezone

from flask import (
    render_template, request, jsonify, session,
    Response, stream_with_context, current_app
)

from core.utils import load_json, save_json, encrypt_value, decrypt_value, is_trading_hours
from core.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_ENABLED, SENSITIVE_FIELDS, get_telegram_config
from core.auth import (
    login_required, csrf_required, generate_csrf_token,
    verify_password, api_error, get_or_create_default_password,
)
from core.llm_utils import (
    DEFAULT_LLM_BASE_URL, DEFAULT_LLM_BUDGET, DEFAULT_LLM_MODEL,
    get_model_token_limits, normalize_llm_base_url, normalize_llm_budget,
    normalize_llm_model,
)
from core.macro_fetcher import get_macro_summary
from core.security import (
    is_ip_banned, check_api_rate_limit, check_login_rate_limit,
    record_failed_login, clear_login_attempts, get_logger as get_security_logger,
    cleanup_expired_entries,
)
from core import db

from . import gold_bp


_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
SETTINGS_FILE = os.path.join(_PROJECT_ROOT, "data", "web_settings.json")
REPORTS_DIR = os.path.join(_PROJECT_ROOT, "data", "reports")

_sse_connections = 0
_sse_lock = threading.Lock()
SSE_MAX_CONNECTIONS = 10

_sse_invalidated_sessions = set()
_sse_invalidated_lock = threading.Lock()

_latest_price = {"data": None, "lock": threading.Lock()}
_last_alert_price = {"value": None, "time": 0, "lock": threading.Lock()}
_cached_macro = {"data": None, "lock": threading.Lock()}
_cached_sr = {"data": None, "lock": threading.Lock()}
_cached_news = {"data": None, "lock": threading.Lock()}
_cached_holdings = {"data": None, "lock": threading.Lock()}
_cached_gold_prices = {"data": None, "lock": threading.Lock()}
_cached_technical = {"data": None, "lock": threading.Lock()}

PRICE_ALERT_THRESHOLD_PCT = 1.5
PRICE_ALERT_COOLDOWN = 600

_security_logger = get_security_logger()

_task_state = {
    "status": "idle",
    "started_at": None,
    "finished_at": None,
    "error": None,
    "report_date": None,
    "lock": threading.Lock(),
}
TASK_STALE_SECONDS = 600


def _clean_nan(obj):
    if isinstance(obj, dict):
        return {k: _clean_nan(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_clean_nan(v) for v in obj]
    elif isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
    return obj


def _encrypt_value(plaintext: str) -> str:
    return encrypt_value(plaintext, current_app.secret_key or "")


def _decrypt_value(ciphertext: str) -> str:
    return decrypt_value(ciphertext, current_app.secret_key or "")


DATE_PATTERN = re.compile(r'^\d{4}-\d{2}-\d{2}$')


DEFAULT_PASSWORD_HASH = get_or_create_default_password(os.path.join(_PROJECT_ROOT, "data"))

if len(DEFAULT_PASSWORD_HASH) == 64 and all(c in '0123456789abcdef' for c in DEFAULT_PASSWORD_HASH):
    _security_logger.warning("检测到旧式SHA256密码哈希，请通过Web界面重新设置密码以升级安全性")
    print("  [安全警告] 检测到旧式SHA256密码哈希，请通过Web界面重新设置密码")


def _check_and_notify_price_alert(price_data: dict):
    price = price_data.get("price")
    change_pct = price_data.get("change_pct")
    if price is None or change_pct is None:
        return
    if abs(change_pct) < PRICE_ALERT_THRESHOLD_PCT:
        return
    now = time.time()
    with _last_alert_price["lock"]:
        last_time = _last_alert_price["time"]
        if now - last_time < PRICE_ALERT_COOLDOWN:
            return
        _last_alert_price["time"] = now
    event_type = "surge" if change_pct > 0 else "crash"
    day_high = price_data.get("high", 0) or 0
    day_low = price_data.get("low", 0) or 0
    source = price_data.get("source", "")
    try:
        from core.db import insert_price_event
        insert_price_event(
            event_type=event_type, price=price, change_pct=change_pct,
            day_high=day_high, day_low=day_low, source=source, notified=True,
        )
    except Exception:
        pass
    try:
        tg_token, tg_chat_id = get_telegram_config(_get_settings(), _decrypt_value)
        if not tg_token or tg_token == "YOUR_BOT_TOKEN_HERE" or not tg_chat_id:
            return
        from core.telegram_bot import TelegramBot
        bot = TelegramBot(tg_token, tg_chat_id)
        direction = "🚀 暴涨" if change_pct > 0 else "💥 暴跌"
        msg = (
            f"{direction} 金价异动提醒！\n\n"
            f"💰 现价: ${price:.2f}\n"
            f"📊 涨跌: {change_pct:+.2f}%\n"
        )
        if day_high and day_low:
            msg += f"📈 日高: ${day_high:.2f}\n📉 日低: ${day_low:.2f}\n"
        msg += f"\n⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}"
        bot.send_message(msg)
    except Exception:
        pass


def _price_refresh_loop():
    while True:
        try:
            from core.gold_price import get_realtime_price, get_domestic_price
            price = get_realtime_price()
            if price:
                domestic = get_domestic_price()
                if domestic:
                    price["domestic"] = domestic
                with _latest_price["lock"]:
                    _latest_price["data"] = price
                _check_and_notify_price_alert(price)
        except Exception:
            pass
        time.sleep(30)


def _fetch_and_cache_macro():
    try:
        from core.macro_fetcher import fetch_macro_indicators
        data = fetch_macro_indicators()
        if data and data.get("indicators"):
            with _cached_macro["lock"]:
                _cached_macro["data"] = data
            try:
                from core.db import insert_macro_snapshot
                insert_macro_snapshot(data["indicators"], data.get("timestamp"))
            except Exception:
                pass
    except Exception:
        pass


def _calc_and_cache_support_resistance():
    try:
        from core.gold_price import get_daily_history
        prices = get_daily_history(60, prefer_international=True)
        if not prices or len(prices) < 2:
            return
        closes = [p["close"] for p in prices]
        highs = [p.get("high", p["close"]) for p in prices]
        lows = [p.get("low", p["close"]) for p in prices]
        current = closes[-1]
        pivot = (highs[-1] + lows[-1] + closes[-1]) / 3
        r1 = 2 * pivot - lows[-1]
        s1 = 2 * pivot - highs[-1]
        r2 = pivot + (highs[-1] - lows[-1])
        s2 = pivot - (highs[-1] - lows[-1])
        r3 = highs[-1] + 2 * (pivot - lows[-1])
        s3 = lows[-1] - 2 * (highs[-1] - pivot)
        recent_high = max(highs[-20:]) if len(highs) >= 20 else max(highs)
        recent_low = min(lows[-20:]) if len(lows) >= 20 else min(lows)
        ma5 = sum(closes[-5:]) / 5
        ma10 = sum(closes[-10:]) / 10 if len(closes) >= 10 else ma5
        ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else ma10
        sr_data = {
            "timestamp": datetime.now().isoformat(),
            "current": round(current, 2),
            "support": [
                {"level": "S1", "value": round(s1, 2), "type": "pivot"},
                {"level": "S2", "value": round(s2, 2), "type": "pivot"},
                {"level": "S3", "value": round(s3, 2), "type": "pivot"},
                {"level": "20日低点", "value": round(recent_low, 2), "type": "structural"},
            ],
            "resistance": [
                {"level": "R1", "value": round(r1, 2), "type": "pivot"},
                {"level": "R2", "value": round(r2, 2), "type": "pivot"},
                {"level": "R3", "value": round(r3, 2), "type": "pivot"},
                {"level": "20日高点", "value": round(recent_high, 2), "type": "structural"},
            ],
            "moving_averages": {"MA5": round(ma5, 2), "MA10": round(ma10, 2), "MA20": round(ma20, 2)},
            "pivot": round(pivot, 2),
        }
        with _cached_sr["lock"]:
            _cached_sr["data"] = sr_data
        try:
            from core.db import insert_support_resistance
            insert_support_resistance(sr_data)
        except Exception:
            pass
    except Exception:
        pass


def _fetch_and_cache_news():
    try:
        from core.news_sentiment import fetch_news_sentiment
        data = fetch_news_sentiment()
        if data:
            with _cached_news["lock"]:
                _cached_news["data"] = data
            try:
                from core.db import upsert_news_sentiment
                from core.gold_price import is_us_workday
                from datetime import date as _date, timedelta as _td
                today = _date.today()
                trade_date = today.isoformat()
                if not is_us_workday(today):
                    for offset in range(1, 8):
                        prev = today - _td(days=offset)
                        if is_us_workday(prev):
                            trade_date = prev.isoformat()
                            break
                upsert_news_sentiment(trade_date, data)
            except Exception:
                pass
    except Exception:
        pass


def _refresh_gold_prices_cache():
    try:
        from core.gold_price import get_daily_history
        prices = get_daily_history(60, prefer_international=True)
        if prices:
            with _cached_gold_prices["lock"]:
                _cached_gold_prices["data"] = prices
            try:
                from core.db import upsert_gold_prices
                upsert_gold_prices(prices)
            except Exception:
                pass
    except Exception:
        pass


def _refresh_holdings_cache():
    try:
        from core.db import get_holdings
        data = get_holdings(30)
        if data:
            with _cached_holdings["lock"]:
                _cached_holdings["data"] = data
    except Exception:
        pass


def _refresh_technical_cache():
    try:
        with _cached_gold_prices["lock"]:
            prices = _cached_gold_prices["data"]
        if not prices:
            prices = db.get_gold_prices(120)
        if prices and len(prices) >= 5:
            from datetime import datetime as _dt
            from core.gold_price import is_us_workday
            filtered = [p for p in prices
                        if not p.get("date") or is_us_workday(_dt.strptime(p["date"], "%Y-%m-%d").date())]
            if len(filtered) >= 5:
                ta_data = _calc_technical_analysis(filtered)
            else:
                ta_data = _calc_technical_analysis(prices)
            if ta_data:
                with _cached_technical["lock"]:
                    _cached_technical["data"] = ta_data
                try:
                    db.insert_technical_analysis(ta_data)
                except Exception:
                    pass
    except Exception:
        pass


def _background_refresh_loop():
    _fetch_and_cache_macro()
    _calc_and_cache_support_resistance()
    _fetch_and_cache_news()
    _refresh_gold_prices_cache()
    _refresh_holdings_cache()
    _refresh_technical_cache()
    cleanup_expired_entries()
    while True:
        try:
            ws = _get_settings()
            interval = (ws.get("realtime_interval_trading", 30) if is_trading_hours()
                        else ws.get("realtime_interval_nontrading", 240)) * 60
            time.sleep(interval)
            _fetch_and_cache_macro()
            _calc_and_cache_support_resistance()
            _fetch_and_cache_news()
            _refresh_gold_prices_cache()
            _refresh_holdings_cache()
            _refresh_technical_cache()
            cleanup_expired_entries()
        except Exception:
            time.sleep(300)


_bg_threads_started = False
_bg_threads_lock = threading.Lock()


def _ensure_background_threads():
    global _bg_threads_started
    with _bg_threads_lock:
        if _bg_threads_started:
            return
        _bg_threads_started = True
    t1 = threading.Thread(target=_price_refresh_loop, daemon=True)
    t1.start()
    t2 = threading.Thread(target=_background_refresh_loop, daemon=True)
    t2.start()


_settings_cache = {"data": None, "time": 0, "lock": threading.Lock()}
_SETTINGS_CACHE_TTL = 5


def _get_settings():
    with _settings_cache["lock"]:
        now = time.time()
        if _settings_cache["data"] and now - _settings_cache["time"] < _SETTINGS_CACHE_TTL:
            return _settings_cache["data"]
    settings = load_json(SETTINGS_FILE)
    if not settings:
        settings = {
            "password_hash": DEFAULT_PASSWORD_HASH,
            "telegram_bot_token": TELEGRAM_BOT_TOKEN,
            "telegram_chat_id": TELEGRAM_CHAT_ID,
            "llm_api_key": LLM_API_KEY,
            "llm_base_url": LLM_BASE_URL,
            "llm_model": LLM_MODEL,
            "llm_budget": DEFAULT_LLM_BUDGET,
            "schedule_hour": 18,
            "schedule_minute": 30,
            "schedule_hour2": 8,
            "schedule_minute2": 0,
            "telegram_push_hour": 18,
            "telegram_push_minute": 30,
            "realtime_interval_trading": 30,
            "realtime_interval_nontrading": 240,
            "top_n": 5,
            "alert_threshold_large": 1000,
        }
        save_json(SETTINGS_FILE, settings, private=True)
    with _settings_cache["lock"]:
        _settings_cache["data"] = settings
        _settings_cache["time"] = time.time()
    return settings


def _invalidate_settings_cache():
    with _settings_cache["lock"]:
        _settings_cache["data"] = None
        _settings_cache["time"] = 0


def _enrich_report_alerts(content):
    has_alerts = "🔔 全维度警示信号" in content
    has_new_format = "📊 期货多空持仓" in content
    if has_alerts and has_new_format:
        return content
    try:
        from core.alert_engine import AlertEngine, DIMENSION_LABEL, DISPLAY_DIMENSION_ORDER
        from core.analyzer import LEVEL_ICON, LEVEL_LABEL
        from core.gold_price import get_daily_history
        from core.macro_fetcher import get_macro_summary
        from core.db import get_holdings
        history = get_holdings(30)
        gold_prices = get_daily_history(60, prefer_international=True)
        macro_data = get_macro_summary()
        today_data = history[-1] if history else {}
        _ws = _get_settings()
        engine = AlertEngine(
            holdings_history=history,
            gold_prices=gold_prices or [],
            macro_data=macro_data,
            news_sentiment=None,
            prediction=None,
            support_resistance=None,
            enabled_dimensions=_ws.get("alert_dimensions"),
            alert_threshold_large=_ws.get("alert_threshold_large", 1000),
        )
        full_alerts = engine.generate_all_alerts(today_data)
        if not full_alerts:
            return content

        dim_alerts_map = {}
        for a in full_alerts:
            dk = a.get("dimension", "position")
            if dk not in dim_alerts_map:
                dim_alerts_map[dk] = []
            dim_alerts_map[dk].append(a)

        if not has_alerts:
            lines = ["", "━━━━━━━━━━━━━━━━━━━━━━", "🔔 全维度警示信号"]
            has_dimension = any(a.get("dimension") for a in full_alerts)
            if has_dimension:
                dim_grouped = {}
                for alert in full_alerts:
                    dim = alert.get("dimension", "position")
                    if dim not in dim_grouped:
                        dim_grouped[dim] = []
                    dim_grouped[dim].append(alert)
                for dim_key in DISPLAY_DIMENSION_ORDER:
                    if dim_key not in dim_grouped:
                        continue
                    dim_alerts = dim_grouped[dim_key]
                    dim_label = DIMENSION_LABEL.get(dim_key, dim_key)
                    level_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
                    for a in dim_alerts:
                        level_counts[a["level"]] = level_counts.get(a["level"], 0) + 1
                    count_parts = []
                    if level_counts["critical"]: count_parts.append(f"重{level_counts['critical']}")
                    if level_counts["high"]: count_parts.append(f"高{level_counts['high']}")
                    if level_counts["medium"]: count_parts.append(f"中{level_counts['medium']}")
                    if level_counts["low"]: count_parts.append(f"低{level_counts['low']}")
                    count_str = " ".join(count_parts)
                    lines.append(f"{dim_label}（{count_str}）：")
                    for alert in dim_alerts:
                        level_icon = LEVEL_ICON.get(alert["level"], "")
                        lines.append(f"  {level_icon} {alert['message']}")
            else:
                grouped = {}
                for alert in full_alerts:
                    level = alert["level"]
                    if level not in grouped:
                        grouped[level] = []
                    grouped[level].append(alert)
                for level in ["critical", "high", "medium", "low"]:
                    if level not in grouped:
                        continue
                    icon = LEVEL_ICON.get(level, "")
                    label = LEVEL_LABEL.get(level, "")
                    lines.append(f"{icon} {label}级信号（{len(grouped[level])}个）：")
                    for alert in grouped[level]:
                        lines.append(f"  • {alert['message']}")
            alert_section = "\n".join(lines)
            old_alert_pos = content.find("🔔 警示信号")
            if old_alert_pos >= 0:
                next_section = content.find("━━━━━━", old_alert_pos + 1)
                if next_section >= 0:
                    content = content[:old_alert_pos] + alert_section + "\n" + content[next_section:]
                else:
                    content = content[:old_alert_pos] + alert_section
            else:
                content += "\n" + alert_section

        if not has_new_format:
            content = _inject_sub_sections(content, dim_alerts_map)

    except Exception:
        pass
    return content


def _inject_sub_sections(content, dim_alerts_map):
    import re as _re

    overview_pos = content.find("📈 今日概况")
    if overview_pos >= 0 and "📊 期货多空" not in content[overview_pos:overview_pos + 200]:
        next_sep = content.find("━━━━━━", overview_pos + 1)
        if next_sep < 0:
            next_sep = len(content)
        old_overview = content[overview_pos:next_sep]
        new_lines = ["📈 今日概况", "📊 期货多空"]
        for line in old_overview.split('\n')[1:]:
            l = line.strip()
            if l:
                new_lines.append(l)

        dim_sub_map = {
            "technical": ("📐 技术面", dim_alerts_map.get("technical", [])),
            "volatility": ("🌊 波动率", dim_alerts_map.get("volatility", [])),
            "macro": ("🏛️ 宏观面", dim_alerts_map.get("macro", [])),
            "sentiment": ("📰 情绪面", dim_alerts_map.get("sentiment", [])),
            "calendar": ("📅 日历事件", dim_alerts_map.get("calendar", [])),
        }
        for dk, (header, alerts) in dim_sub_map.items():
            new_lines.append(header)
            if alerts:
                for a in alerts:
                    new_lines.append(f"  • {a.get('message', '')}")
            else:
                if dk == "calendar":
                    new_lines.append("近期无重大日历事件")
                elif dk == "sentiment":
                    new_lines.append("情绪面数据暂无")
                elif dk == "macro":
                    new_lines.append("宏观面暂无显著变化")
                elif dk == "volatility":
                    new_lines.append("波动率正常")
                else:
                    new_lines.append("暂无显著技术信号")

        content = content[:overview_pos] + "\n".join(new_lines) + "\n" + content[next_sep:]

    trend_pos = content.find("📅 长期趋势")
    if trend_pos >= 0 and "📊 期货多空趋势" not in content[trend_pos:trend_pos + 200]:
        next_sep = content.find("━━━━━━", trend_pos + 1)
        if next_sep < 0:
            next_sep = len(content)
        old_trend = content[trend_pos:next_sep]
        new_lines = []
        for line in old_trend.split('\n'):
            l = line.strip()
            if not l:
                continue
            if l == "📅 长期趋势":
                new_lines.append(l)
                new_lines.append("📊 期货多空趋势")
            elif l.startswith("📊 近") and "日趋势" in l:
                new_lines.append(l.replace("📊 近", "近"))
            else:
                new_lines.append(l)

        trend_sub_map = {
            "technical": ("📐 技术面趋势", dim_alerts_map.get("technical", [])),
            "macro": ("🏛️ 宏观面趋势", dim_alerts_map.get("macro", [])),
            "correlation": ("🔄 关联性趋势", dim_alerts_map.get("correlation", [])),
            "divergence": ("🔀 量价背离", dim_alerts_map.get("divergence", [])),
        }
        for dk, (header, alerts) in trend_sub_map.items():
            new_lines.append(header)
            if alerts:
                for a in alerts:
                    new_lines.append(f"  • {a.get('message', '')}")
            else:
                if dk == "divergence":
                    new_lines.append("量价关系暂无背离信号")
                elif dk == "correlation":
                    new_lines.append("关联性暂无显著变化")
                else:
                    new_lines.append("暂无显著趋势变化")

        content = content[:trend_pos] + "\n".join(new_lines) + "\n" + content[next_sep:]

    detail_pos = content.find("📋 机构持仓明细")
    if detail_pos >= 0 and "📊 期货持仓明细" not in content[detail_pos:detail_pos + 200]:
        next_sep = content.find("⏰", detail_pos + 1)
        if next_sep < 0:
            next_sep = len(content)
        old_detail = content[detail_pos:next_sep]
        new_lines = []
        for line in old_detail.split('\n'):
            l = line.strip()
            if not l:
                continue
            if l.startswith("📋 机构持仓明细"):
                new_lines.append("📋 机构持仓明细")
                new_lines.append("📊 期货持仓明细（前5大净多头）")
            else:
                new_lines.append(l)

        detail_sub_map = {
            "position": ("🏢 持仓结构", dim_alerts_map.get("position", [])),
            "divergence": ("🔀 量价背离", dim_alerts_map.get("divergence", [])),
            "cross": ("🔗 交叉确认", dim_alerts_map.get("cross", [])),
            "extreme": ("⚠️ 极端风险", dim_alerts_map.get("extreme", [])),
        }
        for dk, (header, alerts) in detail_sub_map.items():
            new_lines.append(header)
            if alerts:
                for a in alerts:
                    new_lines.append(f"  • {a.get('message', '')}")
            else:
                if dk == "extreme":
                    new_lines.append("暂无极端风险信号")
                elif dk == "cross":
                    new_lines.append("多维度暂无交叉确认信号")
                elif dk == "divergence":
                    new_lines.append("量价关系暂无背离信号")
                else:
                    new_lines.append("持仓结构暂无异常信号")

        content = content[:detail_pos] + "\n".join(new_lines) + "\n" + content[next_sep:]

    return content


def _calc_technical_analysis(prices):
    if not prices or len(prices) < 5:
        return None

    closes = [p["close"] for p in prices]
    highs = [p.get("high", p["close"]) for p in prices]
    lows = [p.get("low", p["close"]) for p in prices]
    current = closes[-1]

    ma_values = {}
    for period in [5, 10, 20, 60]:
        if len(closes) >= period:
            ma_values[f"MA{period}"] = round(sum(closes[-period:]) / period, 2)

    def calc_ema(data, period):
        multiplier = 2 / (period + 1)
        result = [data[0]]
        for val in data[1:]:
            result.append(val * multiplier + result[-1] * (1 - multiplier))
        return result

    rsi_val = None
    rsi_period = 14
    if len(closes) >= rsi_period + 1:
        gains = []
        losses = []
        for i in range(1, len(closes)):
            change = closes[i] - closes[i - 1]
            gains.append(max(0, change))
            losses.append(max(0, -change))
        if len(gains) >= rsi_period:
            avg_gain = sum(gains[:rsi_period]) / rsi_period
            avg_loss = sum(losses[:rsi_period]) / rsi_period
            for i in range(rsi_period, len(gains)):
                avg_gain = (avg_gain * (rsi_period - 1) + gains[i]) / rsi_period
                avg_loss = (avg_loss * (rsi_period - 1) + losses[i]) / rsi_period
            if avg_loss == 0:
                rsi_val = 100.0
            else:
                rs = avg_gain / avg_loss
                rsi_val = round(100 - (100 / (1 + rs)), 1)

    macd_data = None
    if len(closes) >= 35:
        ema12 = calc_ema(closes, 12)
        ema26 = calc_ema(closes, 26)
        macd_line = [f - s for f, s in zip(ema12, ema26)]
        if len(macd_line) >= 9:
            signal_line = calc_ema(macd_line, 9)
            hist = [m - s for m, s in zip(macd_line, signal_line)]
            macd_data = {
                "macd": round(macd_line[-1], 2),
                "signal": round(signal_line[-1], 2),
                "histogram": round(hist[-1], 2),
                "prev_histogram": round(hist[-2], 2) if len(hist) >= 2 else 0,
            }

    bb_data = None
    bb_period = 20
    if len(closes) >= bb_period:
        recent = closes[-bb_period:]
        mid = sum(recent) / bb_period
        variance = sum((x - mid) ** 2 for x in recent) / (bb_period - 1)
        std = variance ** 0.5
        upper = mid + 2 * std
        lower = mid - 2 * std
        bandwidth = (upper - lower) / mid * 100 if mid > 0 else 0
        pct_b = (current - lower) / (upper - lower) * 100 if (upper - lower) > 0 else 50
        bb_data = {
            "upper": round(upper, 2), "mid": round(mid, 2), "lower": round(lower, 2),
            "bandwidth": round(bandwidth, 2), "pct_b": round(pct_b, 1),
        }

    kdj_data = None
    kdj_period = 9
    if len(closes) >= kdj_period + 2:
        k_val = 50.0
        d_val = 50.0
        for i in range(kdj_period - 1, len(closes)):
            period_highs = highs[i - kdj_period + 1:i + 1]
            period_lows = lows[i - kdj_period + 1:i + 1]
            hh = max(period_highs)
            ll = min(period_lows)
            if hh - ll == 0:
                rsv = 50.0
            else:
                rsv = (closes[i] - ll) / (hh - ll) * 100
            k_val = 2 / 3 * k_val + 1 / 3 * rsv
            d_val = 2 / 3 * d_val + 1 / 3 * k_val
        j_val = 3 * k_val - 2 * d_val
        kdj_data = {"K": round(k_val, 1), "D": round(d_val, 1), "J": round(j_val, 1)}

    atr_val = None
    atr_period = 14
    if len(prices) >= atr_period + 1:
        true_ranges = []
        for i in range(1, len(prices)):
            h = prices[i].get("high", prices[i]["close"])
            l = prices[i].get("low", prices[i]["close"])
            pc = prices[i - 1]["close"]
            tr = max(h - l, abs(h - pc), abs(l - pc))
            true_ranges.append(tr)
        if len(true_ranges) >= atr_period:
            atr_val = sum(true_ranges[:atr_period]) / atr_period
            for i in range(atr_period, len(true_ranges)):
                atr_val = (atr_val * (atr_period - 1) + true_ranges[i]) / atr_period
            atr_val = round(atr_val, 2)

    ma_signals = []
    for name, val in ma_values.items():
        period = int(name[2:])
        if current > val:
            deviation = (current - val) / val * 100
            ma_signals.append({"name": name, "value": val, "position": "上方", "deviation": round(deviation, 2), "signal": "偏多" if deviation > 1 else "微多"})
        else:
            deviation = (val - current) / val * 100
            ma_signals.append({"name": name, "value": val, "position": "下方", "deviation": round(deviation, 2), "signal": "偏空" if deviation > 1 else "微空"})

    ma_cross_signals = []
    if "MA5" in ma_values and "MA10" in ma_values:
        if len(closes) >= 11:
            prev_ma5 = sum(closes[-6:-1]) / 5
            prev_ma10 = sum(closes[-11:-1]) / 10
            if prev_ma5 <= prev_ma10 and ma_values["MA5"] > ma_values["MA10"]:
                ma_cross_signals.append({"type": "金叉", "pair": "MA5/MA10", "signal": "短期偏多"})
            elif prev_ma5 >= prev_ma10 and ma_values["MA5"] < ma_values["MA10"]:
                ma_cross_signals.append({"type": "死叉", "pair": "MA5/MA10", "signal": "短期偏空"})
    if "MA10" in ma_values and "MA20" in ma_values:
        if len(closes) >= 21:
            prev_ma10 = sum(closes[-11:-1]) / 10
            prev_ma20 = sum(closes[-21:-1]) / 20
            if prev_ma10 <= prev_ma20 and ma_values["MA10"] > ma_values["MA20"]:
                ma_cross_signals.append({"type": "金叉", "pair": "MA10/MA20", "signal": "中期偏多"})
            elif prev_ma10 >= prev_ma20 and ma_values["MA10"] < ma_values["MA20"]:
                ma_cross_signals.append({"type": "死叉", "pair": "MA10/MA20", "signal": "中期偏空"})

    rsi_signal = None
    if rsi_val is not None:
        if rsi_val > 80: rsi_signal = {"level": "极度超买", "signal": "强烈偏空", "color": "bearish"}
        elif rsi_val > 70: rsi_signal = {"level": "超买", "signal": "偏空", "color": "bearish"}
        elif rsi_val > 50: rsi_signal = {"level": "偏强", "signal": "微多", "color": "bullish"}
        elif rsi_val >= 40: rsi_signal = {"level": "中性偏弱", "signal": "中性", "color": "neutral"}
        elif rsi_val >= 30: rsi_signal = {"level": "超卖边缘", "signal": "微多", "color": "bullish"}
        elif rsi_val >= 20: rsi_signal = {"level": "超卖", "signal": "偏多", "color": "bullish"}
        else: rsi_signal = {"level": "极度超卖", "signal": "强烈偏多", "color": "bullish"}

    macd_signal = None
    if macd_data:
        hist = macd_data["histogram"]
        prev_hist = macd_data["prev_histogram"]
        if macd_data["macd"] > macd_data["signal"]:
            if prev_hist <= 0 < hist:
                macd_signal = {"type": "金叉", "signal": "偏多", "color": "bullish"}
            else:
                macd_signal = {"type": "多头运行", "signal": "微多", "color": "bullish"}
        else:
            if prev_hist >= 0 > hist:
                macd_signal = {"type": "死叉", "signal": "偏空", "color": "bearish"}
            else:
                macd_signal = {"type": "空头运行", "signal": "微空", "color": "bearish"}

    bb_signal = None
    if bb_data:
        pct_b = bb_data["pct_b"]
        if pct_b > 100: bb_signal = {"position": "突破上轨", "signal": "超买偏空", "color": "bearish"}
        elif pct_b > 80: bb_signal = {"position": "上轨附近", "signal": "偏强注意压力", "color": "neutral"}
        elif pct_b > 20: bb_signal = {"position": "中轨附近", "signal": "中性", "color": "neutral"}
        elif pct_b > 0: bb_signal = {"position": "下轨附近", "signal": "偏弱注意支撑", "color": "neutral"}
        else: bb_signal = {"position": "跌破下轨", "signal": "超卖偏多", "color": "bullish"}

    kdj_signal = None
    if kdj_data:
        k, d, j = kdj_data["K"], kdj_data["D"], kdj_data["J"]
        if j > 100: kdj_signal = {"level": "超买区", "signal": "偏空", "color": "bearish"}
        elif j < 0: kdj_signal = {"level": "超卖区", "signal": "偏多", "color": "bullish"}
        elif k > d:
            if k >= 80: kdj_signal = {"level": "高位钝化", "signal": "中性偏空", "color": "neutral"}
            else: kdj_signal = {"level": "多头运行", "signal": "偏多", "color": "bullish"}
        elif k < d:
            if k <= 20: kdj_signal = {"level": "低位钝化", "signal": "中性偏多", "color": "neutral"}
            else: kdj_signal = {"level": "空头运行", "signal": "偏空", "color": "bearish"}
        else: kdj_signal = {"level": "交织", "signal": "中性", "color": "neutral"}

    chart_data = []
    for i, p in enumerate(prices):
        row = {"date": p.get("date", ""), "close": p.get("close", 0), "high": p.get("high", 0), "low": p.get("low", 0)}
        for period in [5, 10, 20, 60]:
            if i >= period - 1:
                row[f"MA{period}"] = round(sum(closes[i - period + 1:i + 1]) / period, 2)
        chart_data.append(row)

    macd_chart = []
    if len(closes) >= 35:
        ema12 = calc_ema(closes, 12)
        ema26 = calc_ema(closes, 26)
        macd_line = [f - s for f, s in zip(ema12, ema26)]
        if len(macd_line) >= 9:
            signal_line = calc_ema(macd_line, 9)
            hist = [m - s for m, s in zip(macd_line, signal_line)]
            warmup = 34
            for i in range(warmup, len(prices)):
                if i < len(macd_line) and i < len(signal_line) and i < len(hist):
                    macd_chart.append({
                        "date": prices[i].get("date", ""),
                        "macd": round(macd_line[i], 2),
                        "signal": round(signal_line[i], 2),
                        "histogram": round(hist[i], 2),
                    })

    rsi_chart = []
    if len(closes) >= rsi_period + 1:
        gains = []
        losses = []
        for i in range(1, len(closes)):
            change = closes[i] - closes[i - 1]
            gains.append(max(0, change))
            losses.append(max(0, -change))
        if len(gains) >= rsi_period:
            rsi_values = []
            avg_gain = sum(gains[:rsi_period]) / rsi_period
            avg_loss = sum(losses[:rsi_period]) / rsi_period
            for i in range(rsi_period, len(gains)):
                avg_gain = (avg_gain * (rsi_period - 1) + gains[i]) / rsi_period
                avg_loss = (avg_loss * (rsi_period - 1) + losses[i]) / rsi_period
                if avg_loss == 0:
                    rsi_values.append(100.0)
                else:
                    rs = avg_gain / avg_loss
                    rsi_values.append(round(100 - (100 / (1 + rs)), 1))
            offset = rsi_period + 1
            for i, rv in enumerate(rsi_values):
                price_idx = i + offset
                if price_idx < len(prices):
                    rsi_chart.append({"date": prices[price_idx].get("date", ""), "rsi": rv})

    result = {
        "timestamp": datetime.now().isoformat(),
        "current_price": current,
        "moving_averages": ma_values,
        "ma_signals": ma_signals,
        "ma_cross_signals": ma_cross_signals,
        "rsi": {"value": rsi_val, "signal": rsi_signal} if rsi_val is not None else None,
        "macd": {**macd_data, "signal": macd_signal} if macd_data else None,
        "bollinger": {**bb_data, "signal": bb_signal} if bb_data else None,
        "kdj": {**kdj_data, "signal": kdj_signal} if kdj_data else None,
        "atr": {"value": atr_val, "pct": round(atr_val / current * 100, 2) if atr_val and current else None} if atr_val else None,
        "chart_data": chart_data,
        "macd_chart": macd_chart,
        "rsi_chart": rsi_chart,
    }
    return _clean_nan(result)


# ==================== Blueprint Routes ====================

@gold_bp.route("/")
def index():
    _ensure_background_threads()
    return render_template("gold.html")


@gold_bp.route("/api/login", methods=["POST"])
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
        old_csrf = session.get('csrf_token')
        session.clear()
        session["logged_in"] = True
        session.permanent = True
        generate_csrf_token()
        clear_login_attempts(ip)
        _security_logger.info("登录成功: IP=%s", ip)
        return jsonify({"ok": True, "csrf_token": session['csrf_token']})
    fail_count = record_failed_login(ip)
    _security_logger.warning("登录失败: IP=%s 第%d次", ip, fail_count)
    return jsonify({"ok": False, "error": "密码错误"}), 403


@gold_bp.route("/api/logout", methods=["POST"])
@csrf_required
def api_logout():
    csrf_token = session.get("csrf_token", "")
    if csrf_token:
        with _sse_invalidated_lock:
            _sse_invalidated_sessions.add(csrf_token)
    session.pop("logged_in", None)
    session.pop("csrf_token", None)
    session.pop("csrf_token_time", None)
    return jsonify({"ok": True})


@gold_bp.route("/api/check_auth")
def api_check_auth():
    logged_in = session.get("logged_in", False)
    csrf = ""
    if logged_in:
        csrf = generate_csrf_token()
    return jsonify({"logged_in": logged_in, "csrf_token": csrf})


@gold_bp.route("/api/report")
@login_required
def api_report():
    records = db.get_report_dates_by_gen(5)
    if not records:
        return jsonify({"error": "暂无报告", "date": "", "content": ""})
    date_str = records[0]["data_date"]
    content = db.get_report(date_str)
    if not content:
        return jsonify({"error": "暂无报告", "date": "", "content": ""})
    meta = db.get_report_meta([date_str])
    gen_time = meta.get(date_str, "")
    content = _enrich_report_alerts(content)
    return jsonify({"date": date_str, "content": content, "gen_time": gen_time})


@gold_bp.route("/api/report_history")
@login_required
def api_report_history():
    records = db.get_report_dates_by_gen(5)
    gen_dates = [r["gen_date"] for r in records]
    data_dates = [r["data_date"] for r in records]
    meta = db.get_report_meta(data_dates)
    mtimes = [meta.get(d, "") for d in data_dates]
    return jsonify({"dates": gen_dates, "data_dates": data_dates, "mtimes": mtimes})


@gold_bp.route("/api/report_by_date/<date_str>")
@login_required
def api_report_by_date(date_str):
    if not DATE_PATTERN.match(date_str):
        return jsonify({"error": "日期格式无效", "date": date_str, "content": ""}), 400
    content = db.get_report(date_str)
    if not content:
        return jsonify({"error": "报告不存在", "date": date_str, "content": ""})
    meta = db.get_report_meta([date_str])
    gen_time = meta.get(date_str, "")
    content = _enrich_report_alerts(content)
    return jsonify({"date": date_str, "content": content, "gen_time": gen_time})


@gold_bp.route("/api/holdings_chart")
@login_required
def api_holdings_chart():
    with _cached_holdings["lock"]:
        history = _cached_holdings["data"]
    if not history:
        history = db.get_holdings(30)
        if history:
            with _cached_holdings["lock"]:
                _cached_holdings["data"] = history
    chart_data = []
    for record in history:
        total_long = sum(p.get("long_total", 0) or p.get("long", 0) for p in record.get("positions", []))
        total_short = sum(p.get("short_total", 0) or p.get("short", 0) for p in record.get("positions", []))
        total_net = sum(p.get("net", 0) for p in record.get("positions", []))
        total_change = sum(p.get("net_change", 0) for p in record.get("positions", []))
        chart_data.append({
            "date": record["date"], "long": total_long, "short": total_short,
            "net": total_net, "change": total_change,
        })
    return jsonify(chart_data)


@gold_bp.route("/api/gold_price_chart")
@login_required
def api_gold_price_chart():
    with _cached_gold_prices["lock"]:
        prices = _cached_gold_prices["data"]
    if not prices:
        prices = db.get_gold_prices(60)
        if prices:
            with _cached_gold_prices["lock"]:
                _cached_gold_prices["data"] = prices
    from datetime import datetime as _dt
    from core.gold_price import is_us_workday
    chart_data = []
    for p in prices[-60:]:
        d = p.get("date", "")
        if d:
            try:
                dt = _dt.strptime(d, "%Y-%m-%d").date()
                if not is_us_workday(dt):
                    continue
            except ValueError:
                pass
        chart_data.append({
            "date": d, "close": p.get("close", 0),
            "high": p.get("high", 0), "low": p.get("low", 0),
        })
    return jsonify(chart_data)


@gold_bp.route("/api/sentiment_chart")
@login_required
def api_sentiment_chart():
    archive = db.get_news_sentiment_history(90)
    return jsonify(archive)


@gold_bp.route("/api/macro_history_chart")
@login_required
def api_macro_history_chart():
    try:
        from core.db import get_macro_history
        history = get_macro_history(60)
    except Exception:
        history = []
    from core.macro_fetcher import YAHOO_SYMBOLS
    import statistics
    _CHART_KEYS = ["us_10y_yield", "dxy", "vix", "crude_oil"]
    by_date = {}
    for snapshot in history:
        date_key = snapshot.get("date", "")
        if not date_key:
            continue
        if date_key not in by_date:
            by_date[date_key] = {k: [] for k in _CHART_KEYS}
        indicators = snapshot.get("indicators", {})
        for key in _CHART_KEYS:
            ind = indicators.get(key, {})
            if ind and ind.get("value") is not None:
                try:
                    v = float(ind["value"])
                    cfg = YAHOO_SYMBOLS.get(key, {})
                    valid_range = cfg.get("valid_range")
                    if valid_range and (v < valid_range[0] or v > valid_range[1]):
                        continue
                    by_date[date_key][key].append(v)
                except (ValueError, TypeError):
                    pass
    chart_data = []
    for date_key in sorted(by_date.keys()):
        row = {"date": date_key}
        for key in _CHART_KEYS:
            vals = by_date[date_key][key]
            if vals:
                row[key] = statistics.median(vals)
            else:
                row[key] = None
        chart_data.append(row)
    return jsonify(_clean_nan(chart_data))


@gold_bp.route("/api/prediction_factors_chart")
@login_required
def api_prediction_factors_chart():
    latest = db.get_latest_prediction_tracking()
    if not latest:
        return jsonify([])
    factors = latest.get("factors_summary", {})
    factor_labels = {
        "real_rate": "实际利率", "dollar": "美元因子", "inflation": "通胀预期",
        "momentum": "持仓动量", "extreme": "持仓极值", "divergence": "背离信号",
        "cb_gold": "央行购金", "etf_flow": "ETF资金流", "price_trend": "技术趋势",
        "volatility": "波动率", "news_sentiment": "新闻情绪", "seasonality": "季节性",
    }
    result = []
    for key, label in factor_labels.items():
        f = factors.get(key, {})
        result.append({"key": key, "label": label, "score": f.get("score", 0), "signal": f.get("signal", "")})
    return jsonify(_clean_nan(result))


@gold_bp.route("/api/prediction_summary")
@login_required
def api_prediction_summary():
    latest = db.get_latest_prediction_tracking()
    if not latest:
        return jsonify({"error": "暂无预测数据"})
    settings = load_json(os.path.join(_PROJECT_ROOT, "data", "web_settings.json")) or {}
    threshold = float(settings.get("pred_threshold", 0.08))
    score = latest.get("score", 0)
    if score > threshold:
        direction_label = "看多"
        direction_color = "var(--bull)"
        direction_css = "var(--bull)"
        direction_border = "var(--bull)"
    elif score < -threshold:
        direction_label = "看空"
        direction_color = "var(--bear)"
        direction_css = "var(--bear)"
        direction_border = "var(--bear)"
    else:
        direction_label = "中性"
        direction_color = ""
        direction_css = "var(--text2)"
        direction_border = "var(--border)"

    bull_signals = 0
    bear_signals = 0
    factors = latest.get("factors_summary", {})
    factor_labels = {
        "real_rate": "实际利率", "dollar": "美元因子", "inflation": "通胀预期",
        "momentum": "持仓动量", "extreme": "持仓极值", "divergence": "背离信号",
        "cb_gold": "央行购金", "etf_flow": "ETF资金流", "price_trend": "技术趋势",
        "volatility": "波动率", "news_sentiment": "新闻情绪", "seasonality": "季节性",
    }
    factor_list = []
    for key, label in factor_labels.items():
        f = factors.get(key, {})
        fs = f.get("score", 0)
        if fs > threshold:
            signal_label = "偏多"
            fdir = "bullish"
            bull_signals += 1
        elif fs < -threshold:
            signal_label = "偏空"
            fdir = "bearish"
            bear_signals += 1
        else:
            signal_label = "中性"
            fdir = "neutral"
        factor_list.append({
            "key": key, "label": label, "score": fs, "signal": f.get("signal", signal_label),
            "direction": fdir,
            "color": "var(--bull)" if fdir == "bullish" else "var(--bear)" if fdir == "bearish" else "var(--text3)",
        })

    if abs(score) < threshold:
        if bull_signals > bear_signals + 3: judge = "谨慎偏多"
        elif bear_signals > bull_signals + 3: judge = "谨慎偏空"
        else: judge = "中性震荡"
    elif direction_label == "看多":
        judge = "偏多" if bull_signals >= bear_signals else "谨慎偏多"
    elif direction_label == "看空":
        judge = "偏空" if bear_signals >= bull_signals else "谨慎偏空"
    else:
        if bull_signals > bear_signals + 2: judge = "偏多"
        elif bear_signals > bull_signals + 2: judge = "偏空"
        elif bull_signals > bear_signals: judge = "谨慎偏多"
        elif bear_signals > bull_signals: judge = "谨慎偏空"
        else: judge = "中性震荡"

    judge_color = "var(--bull)" if "多" in judge else "var(--bear)" if "空" in judge else "var(--text2)"

    macro_factors = [f for f in factor_list if f["key"] in ("real_rate", "dollar", "inflation")]
    meso_factors = [f for f in factor_list if f["key"] in ("momentum", "extreme", "divergence", "cb_gold", "etf_flow")]
    micro_factors = [f for f in factor_list if f["key"] in ("price_trend", "volatility", "news_sentiment")]
    calendar_factors = [f for f in factor_list if f["key"] in ("seasonality",)]

    sentiment_score = 0
    for f in factor_list:
        if f["key"] == "news_sentiment":
            sentiment_score = f["score"]
    if sentiment_score > threshold:
        sentiment_dir = "偏多"
        sentiment_color = "var(--bull)"
        sentiment_border = "var(--bull)"
    elif sentiment_score < -threshold:
        sentiment_dir = "偏空"
        sentiment_color = "var(--bear)"
        sentiment_border = "var(--bear)"
    else:
        sentiment_dir = "中性"
        sentiment_color = "var(--text2)"
        sentiment_border = "var(--border)"

    period_trends = latest.get("period_trends", {})
    if not isinstance(period_trends, dict):
        period_trends = {}
    period_trends_api = {}
    for pk in ["short", "medium", "long"]:
        pt = period_trends.get(pk, {})
        if pt:
            pdir = pt.get("direction", "中性")
            period_trends_api[pk] = {
                "direction": pdir,
                "direction_color": "var(--bull)" if pdir == "看多" else "var(--bear)" if pdir == "看空" else "var(--text2)",
                "score": pt.get("score", 0), "confidence": pt.get("confidence", 0),
                "label": pt.get("label", ""), "horizon": pt.get("horizon", 0),
                "active_factors": pt.get("active_factors", 0), "total_factors": pt.get("total_factors", 0),
            }
        else:
            period_trends_api[pk] = {
                "direction": "中性", "direction_color": "var(--text2)", "score": 0, "confidence": 30,
                "label": {"short": "短期", "medium": "中期", "long": "长期"}.get(pk, ""),
                "horizon": {"short": 3, "medium": 10, "long": 20}.get(pk, 0),
                "active_factors": 0, "total_factors": 0,
            }

    return jsonify(_clean_nan({
        "date": latest.get("date", ""), "direction": direction_label,
        "direction_color": direction_color, "direction_css": direction_css,
        "direction_border": direction_border, "confidence": latest.get("confidence", 0),
        "score": score, "threshold": threshold, "judge": judge, "judge_color": judge_color,
        "bull_signals": bull_signals, "bear_signals": bear_signals,
        "price_at_prediction": latest.get("price_at_prediction", 0),
        "llm_reasoning": latest.get("llm_reasoning", ""),
        "factors": factor_list,
        "factor_groups": {"macro": macro_factors, "meso": meso_factors, "micro": micro_factors, "calendar": calendar_factors},
        "period_trends": period_trends_api,
        "sentiment_direction": sentiment_dir, "sentiment_color": sentiment_color,
        "sentiment_border": sentiment_border, "sentiment_score": sentiment_score,
        "verified": latest.get("verified", False),
        "actual_direction": latest.get("actual_direction", ""),
        "actual_change_pct": latest.get("actual_change_pct"),
        "consensus_alignment": latest.get("consensus_alignment", {}),
        "institutional_consensus": latest.get("institutional_consensus", {}),
    }))


@gold_bp.route("/api/macro")
@login_required
def api_macro():
    force = request.args.get("force") == "1"
    if force:
        import threading
        threading.Thread(target=_fetch_and_cache_macro, daemon=True).start()
        time.sleep(1)
    with _cached_macro["lock"]:
        data = _cached_macro["data"]
    if not data:
        try:
            from core.db import get_latest_macro
            data = get_latest_macro()
            if data:
                with _cached_macro["lock"]:
                    _cached_macro["data"] = data
        except Exception:
            pass
    if not data:
        try:
            from core.macro_fetcher import fetch_macro_indicators
            data = fetch_macro_indicators()
        except Exception:
            data = {}
    if data and not data.get("gold_impact"):
        try:
            from core.macro_fetcher import calc_gold_impact
            data["gold_impact"] = calc_gold_impact(data.get("indicators", {}))
        except Exception:
            data["gold_impact"] = "中性"
    return jsonify(_clean_nan(data or {}))


@gold_bp.route("/api/realtime_price")
@login_required
def api_realtime_price():
    with _latest_price["lock"]:
        data = _latest_price["data"]
    if not data:
        from core.gold_price import get_realtime_price
        data = get_realtime_price()
    return jsonify(data or {})


@gold_bp.route("/api/domestic_price")
@login_required
def api_domestic_price():
    from core.gold_price import get_domestic_price
    data = get_domestic_price()
    return jsonify(data or {})


@gold_bp.route("/api/price_stream")
def api_price_stream():
    if not session.get("logged_in"):
        return jsonify({"error": "未登录"}), 401

    global _sse_connections
    with _sse_lock:
        if _sse_connections >= SSE_MAX_CONNECTIONS:
            return jsonify({"error": "连接数已达上限"}), 503
        _sse_connections += 1

    session_id = session.get("csrf_token", "")

    def generate():
        last_ts = 0
        start_time = time.time()
        idle_count = 0
        try:
            while True:
                if time.time() - start_time > 1800:
                    break
                with _sse_invalidated_lock:
                    if session_id in _sse_invalidated_sessions:
                        _sse_invalidated_sessions.discard(session_id)
                        yield f"data: {json.dumps({'type': 'auth_required'})}\n\n"
                        break
                try:
                    with _latest_price["lock"]:
                        data = _latest_price["data"]
                    if data and data.get("timestamp", "") != last_ts:
                        last_ts = data.get("timestamp", "")
                        idle_count = 0
                        yield f"data: {json.dumps(data)}\n\n"
                    else:
                        idle_count += 1
                        if idle_count >= 600:
                            break
                        yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
                except Exception:
                    yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
                time.sleep(1)
        except GeneratorExit:
            pass
        except Exception:
            pass
        finally:
            global _sse_connections
            with _sse_lock:
                _sse_connections -= 1

    resp = Response(stream_with_context(generate()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    resp.headers["Connection"] = "keep-alive"
    return resp


@gold_bp.route("/api/support_resistance")
@login_required
def api_support_resistance():
    with _cached_sr["lock"]:
        sr_data = _cached_sr["data"]
    if sr_data:
        return jsonify(sr_data)
    try:
        from core.db import get_latest_support_resistance
        sr_data = get_latest_support_resistance()
        if sr_data:
            sup_count = len(sr_data.get("support", []))
            res_count = len(sr_data.get("resistance", []))
            if sup_count < 3 or res_count < 3:
                _calc_and_cache_support_resistance()
                with _cached_sr["lock"]:
                    sr_data = _cached_sr["data"]
            else:
                with _cached_sr["lock"]:
                    _cached_sr["data"] = sr_data
            if sr_data:
                return jsonify(sr_data)
    except Exception:
        pass
    _calc_and_cache_support_resistance()
    with _cached_sr["lock"]:
        sr_data = _cached_sr["data"]
    return jsonify(sr_data or {"support": [], "resistance": [], "current": 0})


@gold_bp.route("/api/technical_analysis")
@login_required
def api_technical_analysis():
    with _cached_technical["lock"]:
        ta_data = _cached_technical["data"]
    if ta_data:
        return jsonify(_clean_nan(ta_data))
    try:
        ta_data = db.get_latest_technical_analysis()
        if ta_data:
            with _cached_technical["lock"]:
                _cached_technical["data"] = ta_data
            return jsonify(_clean_nan(ta_data))
    except Exception:
        pass
    with _cached_gold_prices["lock"]:
        prices = _cached_gold_prices["data"]
    if not prices:
        prices = db.get_gold_prices(120)
        if prices:
            with _cached_gold_prices["lock"]:
                _cached_gold_prices["data"] = prices
    if not prices or len(prices) < 5:
        return jsonify({"error": "金价数据不足"})
    from datetime import datetime as _dt
    from core.gold_price import is_us_workday
    filtered = [p for p in prices
                if not p.get("date") or is_us_workday(_dt.strptime(p["date"], "%Y-%m-%d").date())]
    calc_prices = filtered if len(filtered) >= 5 else prices
    ta_data = _calc_technical_analysis(calc_prices)
    if ta_data:
        with _cached_technical["lock"]:
            _cached_technical["data"] = ta_data
        try:
            db.insert_technical_analysis(ta_data)
        except Exception:
            pass
    return jsonify(_clean_nan(ta_data) if ta_data else {"error": "计算失败"})


@gold_bp.route("/api/model_params", methods=["GET"])
@login_required
def api_get_model_params():
    from core.news_sentiment import BULLISH_KEYWORDS, BEARISH_KEYWORDS, NEGATION_WORDS, NEGATION_FLIP_MAP
    return jsonify({
        "bullish_keywords": {k: v for k, v in BULLISH_KEYWORDS.items()},
        "bearish_keywords": {k: v for k, v in BEARISH_KEYWORDS.items()},
        "negation_words": NEGATION_WORDS,
    })


@gold_bp.route("/api/settings", methods=["GET"])
@login_required
def api_get_settings():
    settings = dict(_get_settings())
    settings.pop("password_hash", None)
    settings["llm_budget"] = normalize_llm_budget(
        settings.get("llm_budget", settings.get("iteration_llm_budget", DEFAULT_LLM_BUDGET))
    )
    settings.pop("iteration_llm_budget", None)
    try:
        settings["llm_base_url"] = normalize_llm_base_url(settings.get("llm_base_url", DEFAULT_LLM_BASE_URL))
    except ValueError:
        settings["llm_base_url"] = DEFAULT_LLM_BASE_URL
    try:
        settings["llm_model"] = normalize_llm_model(settings.get("llm_model", DEFAULT_LLM_MODEL))
    except ValueError:
        settings["llm_model"] = DEFAULT_LLM_MODEL
    limits = get_model_token_limits(settings["llm_model"], settings)
    settings["llm_context_window"] = limits["context_window"]
    settings["llm_max_output_tokens"] = limits["max_output_tokens"]
    settings["llm_model_known"] = limits["known"]
    for field in SENSITIVE_FIELDS:
        raw = _decrypt_value(settings.get(field, ""))
        if raw:
            settings[f"{field}_masked"] = raw[:6] + "****" + raw[-4:] if len(raw) > 10 else "****"
        else:
            settings[f"{field}_masked"] = ""
        settings.pop(field, None)
    return jsonify(settings)


@gold_bp.route("/api/settings", methods=["POST"])
@login_required
@csrf_required
def api_save_settings():
    data = request.json or {}
    settings = _get_settings()

    int_fields = {
        "schedule_hour": (0, 23), "schedule_minute": (0, 59),
        "schedule_hour2": (0, 23), "schedule_minute2": (0, 59),
        "telegram_push_hour": (0, 23), "telegram_push_minute": (0, 59),
        "realtime_interval_trading": (5, 120), "realtime_interval_nontrading": (30, 720),
        "top_n": (3, 20), "alert_threshold_large": (100, 10000),
        "iteration_min_samples": (5, 100),
    }
    for field, (lo, hi) in int_fields.items():
        if field in data:
            try:
                val = int(data[field])
                settings[field] = max(lo, min(hi, val))
            except (ValueError, TypeError):
                pass
    if "llm_budget" in data:
        settings["llm_budget"] = normalize_llm_budget(data["llm_budget"])
    settings.pop("iteration_llm_budget", None)

    float_fields = {
        "w_momentum": (0, 1), "w_extreme": (0, 1), "w_divergence": (0, 1),
        "w_price_trend": (0, 1), "w_news_sentiment": (0, 1), "w_real_rate": (0, 1),
        "w_dollar": (0, 1), "w_volatility": (0, 1), "w_inflation": (0, 1),
        "w_cb_gold": (0, 1), "w_etf_flow": (0, 1), "w_seasonality": (0, 1),
        "pred_threshold": (0.05, 0.5), "iteration_max_adjustment": (0.005, 0.1),
        "iteration_llm_threshold": (0.1, 0.8),
    }
    for field, (lo, hi) in float_fields.items():
        if field in data:
            try:
                val = float(data[field])
                settings[field] = max(lo, min(hi, val))
            except (ValueError, TypeError):
                pass

    str_fields = ["telegram_chat_id"]
    for field in str_fields:
        if field in data:
            settings[field] = str(data[field])[:500]
    if "llm_base_url" in data:
        try:
            settings["llm_base_url"] = normalize_llm_base_url(data["llm_base_url"])
        except ValueError as e:
            return api_error(str(e), 400)
    if "llm_model" in data:
        try:
            settings["llm_model"] = normalize_llm_model(data["llm_model"])
        except ValueError as e:
            return api_error(str(e), 400)

    for field in SENSITIVE_FIELDS:
        if field in data:
            raw = str(data[field])[:200] if data[field] and not str(data[field]).startswith("****") else ""
            if raw:
                settings[field] = _encrypt_value(raw)
            elif not str(data[field]).startswith("****"):
                settings[field] = ""

    for kw_field in ["bullish_keywords", "bearish_keywords", "negation_words"]:
        if kw_field in data:
            settings[kw_field] = str(data[kw_field])[:5000]

    if "alert_dimensions" in data:
        dim_data = data["alert_dimensions"]
        if isinstance(dim_data, dict):
            allowed_dims = {"technical", "volatility", "macro", "correlation", "divergence", "sentiment", "position", "calendar", "cross", "extreme", "cb_gold", "etf_flow"}
            settings["alert_dimensions"] = {k: bool(v) for k, v in dim_data.items() if k in allowed_dims}

    save_json(SETTINGS_FILE, settings, private=True)
    _invalidate_settings_cache()

    if any(kw in data for kw in ("bullish_keywords", "bearish_keywords", "negation_words")):
        try:
            from core.news_sentiment import reload_keywords_from_settings
            reload_keywords_from_settings()
        except Exception:
            pass

    if any(k in data for k in ("llm_api_key", "llm_base_url", "llm_model")):
        try:
            from core.news_sentiment import reload_llm_config
            reload_llm_config()
        except Exception:
            pass

    return jsonify({"ok": True})


@gold_bp.route("/api/run_now", methods=["POST"])
@login_required
@csrf_required
def api_run_now():
    with _task_state["lock"]:
        if _task_state["status"] == "running":
            elapsed = time.time() - (_task_state["started_at"] or 0)
            if elapsed < TASK_STALE_SECONDS:
                return jsonify({"ok": False, "error": "任务正在执行中，请稍后再试", "status": "running"})
            else:
                _task_state["status"] = "interrupted"
                _task_state["error"] = "任务超时，已标记为中断"
                _task_state["finished_at"] = time.time()

    def _run_with_state():
        with _task_state["lock"]:
            _task_state["status"] = "running"
            _task_state["started_at"] = time.time()
            _task_state["finished_at"] = None
            _task_state["error"] = None
            _task_state["report_date"] = None
        report_date = datetime.now().strftime("%Y-%m-%d")
        try:
            from main import run_daily_task
            result = run_daily_task()
            with _task_state["lock"]:
                _task_state["status"] = "completed"
                _task_state["finished_at"] = time.time()
                _task_state["report_date"] = report_date
        except Exception as e:
            with _task_state["lock"]:
                _task_state["status"] = "failed"
                _task_state["finished_at"] = time.time()
                _task_state["error"] = str(e)
            try:
                db.delete_report(report_date)
                report_file = os.path.join(REPORTS_DIR, f"report_{report_date}.txt")
                if os.path.exists(report_file):
                    os.remove(report_file)
            except Exception:
                pass

    try:
        import threading
        t = threading.Thread(target=_run_with_state, daemon=True)
        t.start()
        return jsonify({"ok": True, "message": "任务已启动，请稍后查看报告"})
    except Exception:
        return jsonify({"ok": False, "error": "执行失败，请查看日志"}), 500


@gold_bp.route("/api/task_status")
@login_required
def api_task_status():
    with _task_state["lock"]:
        state = dict(_task_state)
    status = state["status"]
    started_at = state["started_at"]
    if status == "running" and started_at:
        elapsed = time.time() - started_at
        if elapsed > TASK_STALE_SECONDS:
            with _task_state["lock"]:
                _task_state["status"] = "interrupted"
                _task_state["error"] = "任务超时，已标记为中断"
                _task_state["finished_at"] = time.time()
            status = "interrupted"
            report_date = state.get("report_date") or datetime.now().strftime("%Y-%m-%d")
            try:
                db.delete_report(report_date)
                report_file = os.path.join(REPORTS_DIR, f"report_{report_date}.txt")
                if os.path.exists(report_file):
                    os.remove(report_file)
            except Exception:
                pass
    result = {
        "status": status,
        "started_at": datetime.fromtimestamp(started_at).isoformat() if started_at else None,
        "finished_at": datetime.fromtimestamp(state["finished_at"]).isoformat() if state["finished_at"] else None,
        "error": state["error"],
        "report_date": state["report_date"],
    }
    return jsonify(result)


@gold_bp.route("/api/report_delete/<date_str>", methods=["POST"])
@login_required
@csrf_required
def api_report_delete(date_str):
    if not DATE_PATTERN.match(date_str):
        return jsonify({"ok": False, "error": "日期格式无效"}), 400
    try:
        deleted = db.delete_report(date_str)
        report_file = os.path.join(REPORTS_DIR, f"report_{date_str}.txt")
        file_existed = os.path.exists(report_file)
        if file_existed:
            os.remove(report_file)
        if deleted or file_existed:
            return jsonify({"ok": True, "message": f"报告 {date_str} 已删除"})
        return jsonify({"ok": False, "error": f"报告 {date_str} 不存在"}), 404
    except Exception as e:
        return jsonify({"ok": False, "error": f"删除失败: {e}"}), 500


@gold_bp.route("/api/iteration_status")
@login_required
def api_iteration_status():
    try:
        from core.model_iteration import get_iteration_status
        return jsonify(get_iteration_status())
    except Exception:
        return jsonify({"error": "获取迭代状态失败"}), 500


@gold_bp.route("/api/institutional_consensus")
@login_required
def api_institutional_consensus():
    try:
        from core.institutional_consensus import fetch_institutional_consensus, get_manual_views, compute_consensus_with_manual
        auto = fetch_institutional_consensus()
        manual = get_manual_views()
        if manual:
            all_views, consensus = compute_consensus_with_manual(auto.get("institutions", []), manual)
            result = {
                "institutions": all_views, "consensus": consensus,
                "timestamp": auto.get("timestamp", ""), "source": auto.get("source", "") + "+manual",
            }
        else:
            result = auto
        return jsonify(result)
    except Exception:
        return jsonify({"error": "获取机构共识失败"}), 500


@gold_bp.route("/api/institutional_consensus/manual", methods=["POST"])
@login_required
@csrf_required
def api_save_manual_consensus():
    try:
        from core.institutional_consensus import save_manual_views
        data = request.get_json()
        views = data.get("views", [])
        save_manual_views(views)
        return jsonify({"ok": True})
    except Exception:
        return jsonify({"error": "保存机构观点失败"}), 500


@gold_bp.route("/api/iteration_run", methods=["POST"])
@login_required
@csrf_required
def api_iteration_run():
    try:
        from core.model_iteration import run_iteration
        result = run_iteration(force=True)
        return jsonify(result)
    except Exception:
        return jsonify({"status": "error", "reason": "迭代执行失败"}), 500


@gold_bp.route("/api/iteration_rollback", methods=["POST"])
@login_required
@csrf_required
def api_iteration_rollback():
    try:
        from core.model_iteration import rollback_last_iteration
        result = rollback_last_iteration()
        return jsonify(result)
    except Exception:
        return jsonify({"status": "error", "reason": "回滚执行失败"}), 500


@gold_bp.route("/api/iteration_grid_search", methods=["POST"])
@login_required
@csrf_required
def api_iteration_grid_search():
    try:
        from core.model_iteration import grid_search_weights, get_iteration_status
        status = get_iteration_status()
        current_weights = status.get("current_weights", {})
        if not current_weights:
            return jsonify({"available": False, "reason": "无当前权重数据"})
        from core.db import get_all_prediction_tracking
        tracking = get_all_prediction_tracking(365)
        result = grid_search_weights(tracking, current_weights)
        return jsonify(result)
    except Exception:
        return jsonify({"available": False, "reason": "网格搜索执行失败"}), 500


@gold_bp.route("/api/full_alerts")
@login_required
def api_full_alerts():
    try:
        from core.alert_engine import AlertEngine, DIMENSION_LABEL, generate_dimension_summary

        with _cached_holdings["lock"]:
            history = _cached_holdings["data"]
        if not history:
            history = db.get_holdings(30)
            if history:
                with _cached_holdings["lock"]:
                    _cached_holdings["data"] = history
        if not history:
            return jsonify({"alerts": [], "dimensions": {}})

        today_data = history[-1]

        with _cached_gold_prices["lock"]:
            gold_prices = _cached_gold_prices["data"]
        if not gold_prices:
            gold_prices = db.get_gold_prices(30)
            if gold_prices:
                with _cached_gold_prices["lock"]:
                    _cached_gold_prices["data"] = gold_prices
        if not gold_prices:
            from core.gold_price import get_daily_history
            gold_prices = get_daily_history(days=30) or []

        with _cached_macro["lock"]:
            macro_data = _cached_macro["data"]
        if not macro_data:
            macro_data = db.get_latest_macro()
            if macro_data:
                with _cached_macro["lock"]:
                    _cached_macro["data"] = macro_data
        if not macro_data:
            macro_data = {}

        with _cached_news["lock"]:
            news_sentiment = _cached_news["data"]
        if not news_sentiment:
            try:
                from core.db import get_latest_news_sentiment
                news_sentiment = get_latest_news_sentiment()
                if news_sentiment:
                    with _cached_news["lock"]:
                        _cached_news["data"] = news_sentiment
            except Exception:
                pass

        prediction = None
        try:
            if gold_prices and len(history) >= 3:
                from core.predictor import GoldPricePredictor
                predictor = GoldPricePredictor(history, gold_prices, news_sentiment, macro_data)
                prediction = predictor.predict(today_data)
        except Exception:
            pass

        with _cached_sr["lock"]:
            support_resistance = _cached_sr["data"]
        if not support_resistance:
            try:
                from core.db import get_latest_support_resistance
                support_resistance = get_latest_support_resistance()
                if support_resistance:
                    with _cached_sr["lock"]:
                        _cached_sr["data"] = support_resistance
            except Exception:
                pass

        _ws2 = _get_settings()
        engine = AlertEngine(
            holdings_history=history, gold_prices=gold_prices,
            macro_data=macro_data, news_sentiment=news_sentiment,
            prediction=prediction, support_resistance=support_resistance,
            enabled_dimensions=_ws2.get("alert_dimensions"),
            alert_threshold_large=_ws2.get("alert_threshold_large", 1000),
        )
        alerts = engine.generate_all_alerts(today_data)
        dim_summary = generate_dimension_summary(alerts)

        return jsonify(_clean_nan({"alerts": alerts, "dimensions": dim_summary, "dimension_labels": DIMENSION_LABEL}))
    except Exception:
        return jsonify({"alerts": [], "dimensions": {}, "error": "生成警示信号失败"})
