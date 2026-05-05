# -*- coding: utf-8 -*-
"""
数据库模块 - SQLite 长期存储

所有历史数据统一存入 SQLite，前端只读缓存/数据库，后台线程负责刷新。

表结构：
  gold_prices       - 日线金价（保留365天）
  gold_prices_intra  - 日内金价快照（保留30天）
  holdings          - CFTC持仓数据（保留180天）
  macro_indicators   - 宏观指标快照（保留90天）
  news_sentiment     - 新闻情绪快照（保留90天）
  support_resistance - 支撑/阻力位快照（保留30天）
  reports            - 每日报告（保留365天）
  price_events       - 金价异动事件（保留365天）
  prediction_tracking - 预测追踪记录（保留365天）
  iteration_history  - 模型迭代历史（保留365天）
  iteration_state    - 模型迭代全局状态（单行表）

保留策略：
  gold_prices       365天
  gold_prices_intra  30天
  holdings          180天
  macro_indicators   90天
  news_sentiment     90天
  support_resistance 30天
  reports            365天
  price_events       365天
  prediction_tracking 365天
  iteration_history  365天
"""

import os
import json
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
DB_PATH = os.path.join(_PROJECT_ROOT, "data", "gold_tracker.db")
_DATA_DIR = os.path.join(_PROJECT_ROOT, "data")

_db_lock = threading.Lock()

RETENTION = {
    "gold_prices": 365,
    "gold_prices_intra": 30,
    "holdings": 180,
    "holdings_daily": 180,
    "macro_indicators": 90,
    "news_sentiment": 90,
    "support_resistance": 30,
    "technical_analysis": 90,
    "reports": 365,
    "price_events": 365,
    "prediction_tracking": 365,
    "iteration_history": 365,
    "weight_snapshots": 365,
}

_ALLOWED_TABLES = frozenset(RETENTION.keys())
_ALLOWED_PREDICTION_COLS = frozenset([
    "actual_direction_5d", "actual_change_pct_5d",
    "actual_direction_10d", "actual_change_pct_10d",
    "actual_direction_20d", "actual_change_pct_20d",
    "verified_periods",
])


def _get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-2000")
    try:
        os.chmod(DB_PATH, 0o600)
    except OSError:
        pass
    return conn


from contextlib import contextmanager

@contextmanager
def _conn_ctx():
    conn = _get_conn()
    try:
        yield conn
    finally:
        conn.close()


# ==================== 权重快照 ====================

def insert_weight_snapshot(name: str, reason: str, weights: Dict) -> str:
    with _db_lock:
        conn = _get_conn()
        try:
            conn.execute(
                "INSERT INTO weight_snapshots (name, timestamp, reason, weights) VALUES (?,?,?,?)",
                (name, datetime.now().isoformat(), reason,
                 json.dumps(weights, ensure_ascii=False))
            )
            conn.commit()
            count = conn.execute("SELECT COUNT(*) as cnt FROM weight_snapshots").fetchone()["cnt"]
            if count > 20:
                conn.execute(
                    "DELETE FROM weight_snapshots WHERE id IN (SELECT id FROM weight_snapshots ORDER BY id LIMIT ?)",
                    (count - 20,)
                )
                conn.commit()
        finally:
            conn.close()
    return name


def get_weight_snapshot(name: str) -> Optional[Dict]:
    with _db_lock:
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT name, timestamp, reason, weights FROM weight_snapshots WHERE name = ?",
                (name,)
            ).fetchone()
            if not row:
                return None
            try:
                weights = json.loads(row["weights"])
            except (json.JSONDecodeError, TypeError):
                return None
            return {
                "name": row["name"],
                "timestamp": row["timestamp"],
                "reason": row["reason"],
                "weights": weights,
            }
        finally:
            conn.close()


def get_latest_weight_snapshot() -> Optional[Dict]:
    with _db_lock:
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT name, timestamp, reason, weights FROM weight_snapshots ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if not row:
                return None
            try:
                weights = json.loads(row["weights"])
            except (json.JSONDecodeError, TypeError):
                return None
            return {
                "name": row["name"],
                "timestamp": row["timestamp"],
                "reason": row["reason"],
                "weights": weights,
            }
        finally:
            conn.close()


def init_db():
    with _db_lock:
        conn = _get_conn()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS gold_prices (
                    date TEXT PRIMARY KEY,
                    open REAL, high REAL, low REAL, close REAL,
                    source TEXT, unit TEXT DEFAULT 'USD/oz', created_at TEXT
                );
                CREATE TABLE IF NOT EXISTS gold_prices_intra (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT, timestamp TEXT, price REAL,
                    source TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_intra_date ON gold_prices_intra(date);

                CREATE TABLE IF NOT EXISTS holdings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT, name TEXT,
                    long_change INTEGER, short_change INTEGER,
                    net_change INTEGER, net INTEGER,
                    long_total INTEGER, short_total INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_holdings_date ON holdings(date);

                CREATE TABLE IF NOT EXISTS macro_indicators (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT, indicator_key TEXT,
                    value REAL, change REAL, change_pct REAL,
                    source TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_macro_ts ON macro_indicators(timestamp);

                CREATE TABLE IF NOT EXISTS news_sentiment (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT, timestamp TEXT,
                    sentiment_score REAL, sentiment TEXT, confidence TEXT,
                    analyzer TEXT,
                    bullish_count INTEGER, bearish_count INTEGER, neutral_count INTEGER,
                    key_events TEXT, llm_summary TEXT,
                    sources_ok TEXT, sources_failed TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_news_date ON news_sentiment(date);

                CREATE TABLE IF NOT EXISTS support_resistance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT, current_price REAL,
                    data TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_sr_ts ON support_resistance(timestamp);

                CREATE TABLE IF NOT EXISTS technical_analysis (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT, current_price REAL,
                    data TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_ta_ts ON technical_analysis(timestamp);

                CREATE TABLE IF NOT EXISTS reports (
                    date TEXT PRIMARY KEY,
                    content TEXT, created_at TEXT
                );

                CREATE TABLE IF NOT EXISTS price_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    date TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    price REAL,
                    change_pct REAL,
                    day_high REAL,
                    day_low REAL,
                    day_range REAL,
                    source TEXT,
                    notified INTEGER DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_price_events_date ON price_events(date);
                CREATE INDEX IF NOT EXISTS idx_price_events_type ON price_events(event_type);

                CREATE TABLE IF NOT EXISTS prediction_tracking (
                    date TEXT PRIMARY KEY,
                    prediction TEXT,
                    confidence INTEGER,
                    score REAL,
                    price_at_prediction REAL,
                    factors_summary TEXT,
                    created_at TEXT
                );

                CREATE TABLE IF NOT EXISTS iteration_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    overall_accuracy REAL,
                    recent_accuracy REAL,
                    verified_samples INTEGER,
                    adjustments TEXT NOT NULL,
                    snapshot TEXT,
                    diagnosis TEXT,
                    token_usage INTEGER DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_iter_hist_date ON iteration_history(date);

                CREATE TABLE IF NOT EXISTS iteration_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    token_month TEXT DEFAULT '',
                    token_used INTEGER DEFAULT 0,
                    last_iteration_date TEXT DEFAULT '',
                    total_iterations INTEGER DEFAULT 0,
                    current_weights TEXT DEFAULT '{}',
                    updated_at TEXT
                );

                CREATE TABLE IF NOT EXISTS holdings_daily (
                    date TEXT PRIMARY KEY,
                    trade_date TEXT,
                    contract TEXT,
                    total_long INTEGER DEFAULT 0,
                    total_short INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS weight_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    reason TEXT,
                    weights TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ws_name ON weight_snapshots(name);
            """)
            conn.commit()
            row = conn.execute("SELECT 1 FROM iteration_state LIMIT 1").fetchone()
            if not row:
                conn.execute("INSERT INTO iteration_state (id, updated_at) VALUES (1, ?)", (datetime.now().isoformat(),))
                conn.commit()
            _migrate_prediction_tracking_schema(conn)
            _migrate_gold_prices_schema(conn)
        finally:
            conn.close()


def _migrate_prediction_tracking_schema(conn):
    cols = [r[1] for r in conn.execute("PRAGMA table_info(prediction_tracking)").fetchall()]
    if "llm_reasoning" not in cols:
        conn.execute("ALTER TABLE prediction_tracking ADD COLUMN llm_reasoning TEXT DEFAULT ''")
    if "verified" not in cols:
        conn.execute("ALTER TABLE prediction_tracking ADD COLUMN verified INTEGER DEFAULT 0")
    if "actual_direction" not in cols:
        conn.execute("ALTER TABLE prediction_tracking ADD COLUMN actual_direction TEXT DEFAULT ''")
    if "actual_change_pct" not in cols:
        conn.execute("ALTER TABLE prediction_tracking ADD COLUMN actual_change_pct REAL")
    if "verified_date" not in cols:
        conn.execute("ALTER TABLE prediction_tracking ADD COLUMN verified_date TEXT DEFAULT ''")
    if "period_trends" not in cols:
        conn.execute("ALTER TABLE prediction_tracking ADD COLUMN period_trends TEXT DEFAULT ''")
    if "actual_direction_5d" not in cols:
        conn.execute("ALTER TABLE prediction_tracking ADD COLUMN actual_direction_5d TEXT DEFAULT ''")
    if "actual_change_pct_5d" not in cols:
        conn.execute("ALTER TABLE prediction_tracking ADD COLUMN actual_change_pct_5d REAL")
    if "actual_direction_10d" not in cols:
        conn.execute("ALTER TABLE prediction_tracking ADD COLUMN actual_direction_10d TEXT DEFAULT ''")
    if "actual_change_pct_10d" not in cols:
        conn.execute("ALTER TABLE prediction_tracking ADD COLUMN actual_change_pct_10d REAL")
    if "actual_direction_20d" not in cols:
        conn.execute("ALTER TABLE prediction_tracking ADD COLUMN actual_direction_20d TEXT DEFAULT ''")
    if "actual_change_pct_20d" not in cols:
        conn.execute("ALTER TABLE prediction_tracking ADD COLUMN actual_change_pct_20d REAL")
    if "verified_periods" not in cols:
        conn.execute("ALTER TABLE prediction_tracking ADD COLUMN verified_periods TEXT DEFAULT ''")
    if "institutional_consensus" not in cols:
        conn.execute("ALTER TABLE prediction_tracking ADD COLUMN institutional_consensus TEXT DEFAULT ''")
    if "consensus_alignment" not in cols:
        conn.execute("ALTER TABLE prediction_tracking ADD COLUMN consensus_alignment TEXT DEFAULT ''")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pred_tracking_verified ON prediction_tracking(verified)")
    conn.commit()


def _migrate_gold_prices_schema(conn):
    cols = [r[1] for r in conn.execute("PRAGMA table_info(gold_prices)").fetchall()]
    if "unit" not in cols:
        conn.execute("ALTER TABLE gold_prices ADD COLUMN unit TEXT DEFAULT 'USD/oz'")
        conn.commit()


def cleanup():
    with _db_lock:
        conn = _get_conn()
        try:
            cutoffs = {}
            for table, days in RETENTION.items():
                cutoffs[table] = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

            conn.execute("DELETE FROM gold_prices WHERE date < ?", (cutoffs["gold_prices"],))
            conn.execute("DELETE FROM gold_prices_intra WHERE date < ?", (cutoffs["gold_prices_intra"],))
            conn.execute("DELETE FROM holdings WHERE date < ?", (cutoffs["holdings"],))
            conn.execute("DELETE FROM macro_indicators WHERE timestamp < ?", (cutoffs["macro_indicators"] + " 00:00:00",))
            conn.execute("DELETE FROM news_sentiment WHERE date < ?", (cutoffs["news_sentiment"],))
            conn.execute("DELETE FROM support_resistance WHERE timestamp < ?", (cutoffs["support_resistance"] + " 00:00:00",))
            conn.execute("DELETE FROM technical_analysis WHERE timestamp < ?", (cutoffs["technical_analysis"] + " 00:00:00",))
            conn.execute("DELETE FROM reports WHERE date < ?", (cutoffs["reports"],))
            conn.execute("DELETE FROM price_events WHERE date < ?", (cutoffs["price_events"],))
            conn.execute("DELETE FROM prediction_tracking WHERE date < ?", (cutoffs["prediction_tracking"],))
            conn.execute("DELETE FROM iteration_history WHERE date < ?", (cutoffs["iteration_history"],))
            conn.execute("DELETE FROM holdings_daily WHERE date < ?", (cutoffs["holdings_daily"],))
            conn.execute("DELETE FROM weight_snapshots WHERE timestamp < ?", (cutoffs["weight_snapshots"] + " 00:00:00",))

            try:
                from core.gold_price import is_us_workday
                rows = conn.execute("SELECT date FROM gold_prices").fetchall()
                for row in rows:
                    try:
                        from datetime import date as _date
                        d = _date.fromisoformat(row["date"])
                        if not is_us_workday(d):
                            conn.execute("DELETE FROM gold_prices WHERE date = ?", (row["date"],))
                    except Exception:
                        pass
            except Exception:
                pass

            conn.commit()
            conn.execute("VACUUM")
        finally:
            conn.close()

    _cleanup_report_files()


def _cleanup_report_files():
    import re
    reports_dir = os.path.join(_PROJECT_ROOT, "data", "reports")
    if not os.path.isdir(reports_dir):
        return
    cutoff = (datetime.now() - timedelta(days=RETENTION["reports"])).strftime("%Y-%m-%d")
    pattern = re.compile(r"^report_(\d{4}-\d{2}-\d{2})\.txt$")
    for f in os.listdir(reports_dir):
        m = pattern.match(f)
        if m and m.group(1) < cutoff:
            try:
                os.remove(os.path.join(reports_dir, f))
            except OSError:
                pass


# ==================== 金价日线 ====================

def upsert_gold_prices(prices):
    if not prices:
        return
    with _db_lock:
        conn = _get_conn()
        try:
            if isinstance(prices, dict):
                prices = [prices]
            for p in prices:
                d = p.get("date", "")
                if not d:
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO gold_prices (date, open, high, low, close, source, unit, created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (d, p.get("open"), p.get("high"), p.get("low"), p.get("close"),
                     p.get("source", ""), p.get("unit", "USD/oz"), datetime.now().isoformat())
                )
            conn.commit()
        finally:
            conn.close()


def get_gold_prices(days: int = 60) -> List[Dict]:
    with _db_lock:
        conn = _get_conn()
        try:
            cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            rows = conn.execute(
                "SELECT date, open, high, low, close, source, unit FROM gold_prices WHERE date >= ? ORDER BY date",
                (cutoff,)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def get_latest_gold_price() -> Optional[Dict]:
    with _db_lock:
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT date, open, high, low, close, source, unit FROM gold_prices ORDER BY date DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


# ==================== 日内金价快照 ====================

def insert_intraday_snapshot(date: str, timestamp: str, price: float,
                            change: float = 0, change_pct: float = 0, source: str = ""):
    with _db_lock:
        conn = _get_conn()
        try:
            conn.execute(
                "INSERT INTO gold_prices_intra (date, timestamp, price, source) VALUES (?,?,?,?)",
                (date, timestamp or datetime.now().isoformat(), price, source)
            )
            conn.commit()
        finally:
            conn.close()


def get_intraday_snapshots(date_or_days) -> List[Dict]:
    with _db_lock:
        conn = _get_conn()
        try:
            if isinstance(date_or_days, int):
                cutoff = (datetime.now() - timedelta(days=date_or_days)).strftime("%Y-%m-%d")
                rows = conn.execute(
                    "SELECT date, timestamp, price, source FROM gold_prices_intra WHERE date >= ? ORDER BY timestamp",
                    (cutoff,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT date, timestamp, price, source FROM gold_prices_intra WHERE date = ? ORDER BY timestamp",
                    (date_or_days,)
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


# ==================== 持仓数据 ====================

def upsert_holdings(date: str, positions: List[Dict], trade_date: str = "",
                    contract: str = "", total_long: int = 0, total_short: int = 0):
    with _db_lock:
        conn = _get_conn()
        try:
            existing = conn.execute("SELECT 1 FROM holdings WHERE date = ? LIMIT 1", (date,)).fetchone()
            if existing:
                conn.execute("DELETE FROM holdings WHERE date = ?", (date,))
            for p in positions:
                conn.execute(
                    "INSERT INTO holdings (date, name, long_change, short_change, net_change, net, long_total, short_total) VALUES (?,?,?,?,?,?,?,?)",
                    (date, p.get("name", ""),
                     p.get("long_change", 0), p.get("short_change", 0),
                     p.get("net_change", 0), p.get("net", 0),
                     p.get("long_total", 0) or p.get("long", 0), p.get("short_total", 0) or p.get("short", 0))
                )
            conn.execute(
                "INSERT OR REPLACE INTO holdings_daily (date, trade_date, contract, total_long, total_short) VALUES (?,?,?,?,?)",
                (date, trade_date, contract, total_long, total_short)
            )
            conn.commit()
        finally:
            conn.close()


def get_holdings(days: int = 30) -> List[Dict]:
    with _db_lock:
        conn = _get_conn()
        try:
            cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            rows = conn.execute(
                "SELECT date, name, long_change, short_change, net_change, net, long_total, short_total FROM holdings WHERE date >= ? ORDER BY date",
                (cutoff,)
            ).fetchall()
            daily_rows = conn.execute(
                "SELECT date, trade_date, contract, total_long, total_short FROM holdings_daily WHERE date >= ?",
                (cutoff,)
            ).fetchall()
            daily_map = {r["date"]: dict(r) for r in daily_rows}
            result = {}
            for r in rows:
                d = r["date"]
                if d not in result:
                    daily = daily_map.get(d, {})
                    result[d] = {
                        "date": d,
                        "trade_date": daily.get("trade_date", ""),
                        "contract": daily.get("contract", ""),
                        "total_long": daily.get("total_long", 0),
                        "total_short": daily.get("total_short", 0),
                        "positions": [],
                    }
                result[d]["positions"].append(dict(r))
            return [result[k] for k in sorted(result.keys())]
        finally:
            conn.close()


def get_holdings_dates() -> List[str]:
    with _db_lock:
        conn = _get_conn()
        try:
            rows = conn.execute("SELECT DISTINCT date FROM holdings ORDER BY date").fetchall()
            return [r["date"] for r in rows]
        finally:
            conn.close()


# ==================== 宏观指标 ====================

def insert_macro_snapshot(indicators: Dict, timestamp: Optional[str] = None):
    if not indicators:
        return
    ts = timestamp or datetime.now().isoformat()
    with _db_lock:
        conn = _get_conn()
        try:
            for key, val in indicators.items():
                if not isinstance(val, dict):
                    continue
                conn.execute(
                    "INSERT INTO macro_indicators (timestamp, indicator_key, value, change, change_pct, source) VALUES (?,?,?,?,?,?)",
                    (ts, key,
                     val.get("value"), val.get("change"), val.get("change_pct"),
                     val.get("source", ""))
                )
            conn.commit()
        finally:
            conn.close()


def get_latest_macro() -> Dict:
    with _db_lock:
        conn = _get_conn()
        try:
            latest_ts = conn.execute("SELECT MAX(timestamp) as ts FROM macro_indicators").fetchone()
            if not latest_ts or not latest_ts["ts"]:
                return {}
            ts = latest_ts["ts"]
            rows = conn.execute(
                "SELECT indicator_key, value, change, change_pct, source FROM macro_indicators WHERE timestamp = ?",
                (ts,)
            ).fetchall()
            indicators = {}
            for r in rows:
                indicators[r["indicator_key"]] = {
                    "value": r["value"],
                    "change": r["change"],
                    "change_pct": r["change_pct"],
                    "source": r["source"],
                }
            return {
                "timestamp": ts,
                "date": ts[:10],
                "indicators": indicators,
            }
        finally:
            conn.close()


def get_macro_history(days: int = 30) -> List[Dict]:
    with _db_lock:
        conn = _get_conn()
        try:
            cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            rows = conn.execute(
                "SELECT DISTINCT timestamp FROM macro_indicators WHERE timestamp >= ? ORDER BY timestamp",
                (cutoff + " 00:00:00",)
            ).fetchall()
            result = []
            for r in rows:
                ts = r["timestamp"]
                indicator_rows = conn.execute(
                    "SELECT indicator_key, value, change, change_pct, source FROM macro_indicators WHERE timestamp = ?",
                    (ts,)
                ).fetchall()
                indicators = {}
                for ir in indicator_rows:
                    indicators[ir["indicator_key"]] = {
                        "value": ir["value"],
                        "change": ir["change"],
                        "change_pct": ir["change_pct"],
                        "source": ir["source"],
                    }
                result.append({"timestamp": ts, "date": ts[:10], "indicators": indicators})
            return result
        finally:
            conn.close()


# ==================== 新闻情绪 ====================

def upsert_news_sentiment(date: str, data: Dict):
    with _db_lock:
        conn = _get_conn()
        try:
            existing = conn.execute("SELECT id FROM news_sentiment WHERE date = ? LIMIT 1", (date,)).fetchone()
            if existing:
                conn.execute("DELETE FROM news_sentiment WHERE date = ?", (date,))
            key_events = data.get("key_events", [])
            if not isinstance(key_events, str):
                key_events = json.dumps(key_events, ensure_ascii=False)
            sources_ok = data.get("sources_ok", "")
            if not isinstance(sources_ok, str):
                sources_ok = json.dumps(sources_ok, ensure_ascii=False) if sources_ok else ""
            sources_failed = data.get("sources_failed", "")
            if not isinstance(sources_failed, str):
                sources_failed = json.dumps(sources_failed, ensure_ascii=False) if sources_failed else ""
            conn.execute(
                "INSERT INTO news_sentiment (date, timestamp, sentiment_score, sentiment, confidence, analyzer, bullish_count, bearish_count, neutral_count, key_events, llm_summary, sources_ok, sources_failed) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (date, data.get("timestamp", datetime.now().isoformat()),
                 data.get("sentiment_score", 0), data.get("sentiment", ""),
                 data.get("confidence", ""), data.get("analyzer", ""),
                 data.get("bullish_count", 0), data.get("bearish_count", 0), data.get("neutral_count", 0),
                 key_events,
                 data.get("llm_summary", "") or "",
                 sources_ok, sources_failed)
            )
            conn.commit()
        finally:
            conn.close()


def get_latest_news_sentiment() -> Optional[Dict]:
    with _db_lock:
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT date, timestamp, sentiment_score, sentiment, confidence, analyzer, bullish_count, bearish_count, neutral_count, key_events, llm_summary, sources_ok, sources_failed FROM news_sentiment ORDER BY date DESC LIMIT 1"
            ).fetchone()
            if not row:
                return None
            result = dict(row)
            if result.get("key_events"):
                try:
                    result["key_events"] = json.loads(result["key_events"])
                except (json.JSONDecodeError, TypeError):
                    result["key_events"] = []
            return result
        finally:
            conn.close()


def get_news_sentiment_history(days: int = 90) -> List[Dict]:
    with _db_lock:
        conn = _get_conn()
        try:
            cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            rows = conn.execute(
                "SELECT date, timestamp, sentiment_score, sentiment, confidence, analyzer, bullish_count, bearish_count, neutral_count, key_events, llm_summary, sources_ok, sources_failed FROM news_sentiment WHERE date >= ? ORDER BY date",
                (cutoff,)
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                if d.get("key_events"):
                    try:
                        d["key_events"] = json.loads(d["key_events"])
                    except (json.JSONDecodeError, TypeError):
                        d["key_events"] = []
                result.append(d)
            return result
        finally:
            conn.close()


# ==================== 支撑/阻力位 ====================

def insert_support_resistance(data: Dict):
    with _db_lock:
        conn = _get_conn()
        try:
            conn.execute(
                "INSERT INTO support_resistance (timestamp, current_price, data) VALUES (?,?,?)",
                (data.get("timestamp", datetime.now().isoformat()),
                 data.get("current", 0),
                 json.dumps(data, ensure_ascii=False))
            )
            conn.commit()
        finally:
            conn.close()


def get_latest_support_resistance() -> Optional[Dict]:
    with _db_lock:
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT timestamp, current_price, data FROM support_resistance ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
            if not row:
                return None
            try:
                return json.loads(row["data"])
            except (json.JSONDecodeError, TypeError):
                return None
        finally:
            conn.close()


def insert_technical_analysis(data: Dict):
    with _db_lock:
        conn = _get_conn()
        try:
            conn.execute(
                "INSERT INTO technical_analysis (timestamp, current_price, data) VALUES (?,?,?)",
                (data.get("timestamp", datetime.now().isoformat()),
                 data.get("current_price", 0),
                 json.dumps(data, ensure_ascii=False))
            )
            conn.commit()
        finally:
            conn.close()


def get_latest_technical_analysis() -> Optional[Dict]:
    with _db_lock:
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT timestamp, current_price, data FROM technical_analysis ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
            if not row:
                return None
            try:
                return json.loads(row["data"])
            except (json.JSONDecodeError, TypeError):
                return None
        finally:
            conn.close()


def get_technical_analysis_history(days: int = 30) -> List[Dict]:
    with _db_lock:
        conn = _get_conn()
        try:
            cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            rows = conn.execute(
                "SELECT timestamp, current_price, data FROM technical_analysis WHERE timestamp >= ? ORDER BY timestamp",
                (cutoff + " 00:00:00",)
            ).fetchall()
            result = []
            for row in rows:
                try:
                    parsed = json.loads(row["data"])
                    parsed["db_timestamp"] = row["timestamp"]
                    result.append(parsed)
                except (json.JSONDecodeError, TypeError):
                    continue
            return result
        finally:
            conn.close()


# ==================== 报告 ====================

def upsert_report(date: str, content: str):
    with _db_lock:
        conn = _get_conn()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO reports (date, content, created_at) VALUES (?,?,?)",
                (date, content, datetime.now().isoformat())
            )
            conn.commit()
        finally:
            conn.close()


def get_report(date: str) -> Optional[str]:
    with _db_lock:
        conn = _get_conn()
        try:
            row = conn.execute("SELECT content FROM reports WHERE date = ?", (date,)).fetchone()
            return row["content"] if row else None
        finally:
            conn.close()


def get_report_dates(days: int = 30) -> List[str]:
    with _db_lock:
        conn = _get_conn()
        try:
            cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            rows = conn.execute(
                "SELECT date FROM reports WHERE date >= ? ORDER BY date DESC", (cutoff,)
            ).fetchall()
            return [r["date"] for r in rows]
        finally:
            conn.close()


def get_report_dates_by_gen(days: int = 30) -> List[Dict]:
    with _db_lock:
        conn = _get_conn()
        try:
            cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            rows = conn.execute(
                "SELECT date, created_at FROM reports WHERE date >= ? ORDER BY created_at DESC",
                (cutoff,)
            ).fetchall()
            seen = set()
            result = []
            for r in rows:
                data_date = r["date"]
                if data_date not in seen:
                    seen.add(data_date)
                    gen_date = r["created_at"][:10]
                    result.append({"gen_date": gen_date, "data_date": data_date})
            return result
        finally:
            conn.close()


def get_report_meta(dates: List[str]) -> dict:
    with _db_lock:
        conn = _get_conn()
        try:
            result = {}
            for d in dates:
                row = conn.execute("SELECT created_at FROM reports WHERE date = ?", (d,)).fetchone()
                result[d] = row["created_at"][:19].replace("T", " ") if row else ""
            return result
        finally:
            conn.close()


def upsert_prediction_tracking(date: str, data: Dict):
    with _db_lock:
        conn = _get_conn()
        try:
            existing = conn.execute("SELECT verified, actual_direction, actual_change_pct, verified_date, actual_direction_5d, actual_direction_10d, actual_direction_20d, verified_periods, institutional_consensus, consensus_alignment FROM prediction_tracking WHERE date = ?", (date,)).fetchone()
            if existing and existing["verified"]:
                conn.execute(
                    "UPDATE prediction_tracking SET prediction = ?, confidence = ?, score = ?, price_at_prediction = ?, factors_summary = ?, llm_reasoning = ?, period_trends = ?, institutional_consensus = ?, consensus_alignment = ? WHERE date = ?",
                    (data.get("prediction", ""), data.get("confidence", 0), data.get("score", 0),
                     data.get("price_at_prediction", 0),
                     json.dumps(data.get("factors_summary", {}), ensure_ascii=False) if not isinstance(data.get("factors_summary", {}), str) else data.get("factors_summary", "{}"),
                     data.get("llm_reasoning", ""),
                     json.dumps(data.get("period_trends", {}), ensure_ascii=False) if not isinstance(data.get("period_trends", {}), str) else data.get("period_trends", "{}"),
                     json.dumps(data.get("institutional_consensus", {}), ensure_ascii=False) if not isinstance(data.get("institutional_consensus", {}), str) else data.get("institutional_consensus", "{}"),
                     json.dumps(data.get("consensus_alignment", {}), ensure_ascii=False) if not isinstance(data.get("consensus_alignment", {}), str) else data.get("consensus_alignment", "{}"),
                     date)
                )
            else:
                factors_summary = data.get("factors_summary", {})
                if not isinstance(factors_summary, str):
                    factors_summary = json.dumps(factors_summary, ensure_ascii=False)
                period_trends = data.get("period_trends", {})
                if not isinstance(period_trends, str):
                    period_trends = json.dumps(period_trends, ensure_ascii=False)
                inst_consensus = data.get("institutional_consensus", {})
                if not isinstance(inst_consensus, str):
                    inst_consensus = json.dumps(inst_consensus, ensure_ascii=False)
                cons_align = data.get("consensus_alignment", {})
                if not isinstance(cons_align, str):
                    cons_align = json.dumps(cons_align, ensure_ascii=False)
                verified_val = 1 if data.get("verified") else 0
                if existing and not existing["verified"] and data.get("verified"):
                    verified_val = 1
                conn.execute(
                    "INSERT OR REPLACE INTO prediction_tracking (date, prediction, confidence, score, price_at_prediction, factors_summary, llm_reasoning, period_trends, institutional_consensus, consensus_alignment, verified, actual_direction, actual_change_pct, verified_date, actual_direction_5d, actual_change_pct_5d, actual_direction_10d, actual_change_pct_10d, actual_direction_20d, actual_change_pct_20d, verified_periods, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (date, data.get("prediction", ""), data.get("confidence", 0), data.get("score", 0),
                     data.get("price_at_prediction", 0), factors_summary,
                     data.get("llm_reasoning", ""),
                     period_trends,
                     inst_consensus,
                     cons_align,
                     verified_val,
                     data.get("actual_direction", existing["actual_direction"] if existing else ""),
                     data.get("actual_change_pct", existing["actual_change_pct"] if existing else None),
                     data.get("verified_date", existing["verified_date"] if existing else ""),
                     data.get("actual_direction_5d", existing["actual_direction_5d"] if existing else ""),
                     data.get("actual_change_pct_5d", existing.get("actual_change_pct_5d") if existing else None),
                     data.get("actual_direction_10d", existing["actual_direction_10d"] if existing else ""),
                     data.get("actual_change_pct_10d", existing.get("actual_change_pct_10d") if existing else None),
                     data.get("actual_direction_20d", existing["actual_direction_20d"] if existing else ""),
                     data.get("actual_change_pct_20d", existing.get("actual_change_pct_20d") if existing else None),
                     data.get("verified_periods", existing["verified_periods"] if existing else ""),
                     datetime.now().isoformat())
                )
            conn.commit()
        finally:
            conn.close()


def get_latest_prediction_tracking() -> Optional[Dict]:
    with _db_lock:
        conn = _get_conn()
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            row = conn.execute("SELECT * FROM prediction_tracking WHERE date <= ? ORDER BY date DESC LIMIT 1", (today,)).fetchone()
            if not row:
                return None
            return _row_to_prediction(row)
        finally:
            conn.close()


def get_all_prediction_tracking(days: int = 365) -> List[Dict]:
    with _db_lock:
        conn = _get_conn()
        try:
            cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            rows = conn.execute(
                "SELECT * FROM prediction_tracking WHERE date >= ? ORDER BY date ASC",
                (cutoff,)
            ).fetchall()
            return [_row_to_prediction(r) for r in rows]
        finally:
            conn.close()


def get_unverified_predictions() -> List[Dict]:
    with _db_lock:
        conn = _get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM prediction_tracking WHERE verified = 0 ORDER BY date ASC"
            ).fetchall()
            return [_row_to_prediction(r) for r in rows]
        finally:
            conn.close()


def update_prediction_verification(date: str, verified_data: Dict):
    with _db_lock:
        conn = _get_conn()
        try:
            sets = ["verified = 1", "actual_direction = ?", "actual_change_pct = ?", "verified_date = ?"]
            params = [verified_data.get("actual_direction", ""),
                      verified_data.get("actual_change_pct"),
                      verified_data.get("verified_date", "")]
            for col in ["actual_direction_5d", "actual_change_pct_5d",
                        "actual_direction_10d", "actual_change_pct_10d",
                        "actual_direction_20d", "actual_change_pct_20d",
                        "verified_periods"]:
                if col in verified_data and col in _ALLOWED_PREDICTION_COLS:
                    sets.append(f"{col} = ?")
                    params.append(verified_data[col])
            params.append(date)
            conn.execute(
                f"UPDATE prediction_tracking SET {', '.join(sets)} WHERE date = ?",
                params
            )
            conn.commit()
        finally:
            conn.close()


def _row_to_prediction(row) -> Dict:
    r = dict(row)
    if r.get("factors_summary"):
        try:
            r["factors_summary"] = json.loads(r["factors_summary"])
        except (json.JSONDecodeError, TypeError):
            r["factors_summary"] = {}
    else:
        r["factors_summary"] = {}
    if r.get("period_trends"):
        try:
            r["period_trends"] = json.loads(r["period_trends"])
        except (json.JSONDecodeError, TypeError):
            r["period_trends"] = {}
    else:
        r["period_trends"] = {}
    if r.get("verified_periods"):
        try:
            r["verified_periods"] = json.loads(r["verified_periods"])
        except (json.JSONDecodeError, TypeError):
            r["verified_periods"] = []
    else:
        r["verified_periods"] = []
    if r.get("institutional_consensus"):
        try:
            r["institutional_consensus"] = json.loads(r["institutional_consensus"])
        except (json.JSONDecodeError, TypeError):
            r["institutional_consensus"] = {}
    else:
        r["institutional_consensus"] = {}
    if r.get("consensus_alignment"):
        try:
            r["consensus_alignment"] = json.loads(r["consensus_alignment"])
        except (json.JSONDecodeError, TypeError):
            r["consensus_alignment"] = {}
    else:
        r["consensus_alignment"] = {}
    r["verified"] = bool(r.get("verified", 0))
    return r


# ==================== 迭代历史 ====================

def insert_iteration_history(record: Dict):
    with _db_lock:
        conn = _get_conn()
        try:
            adjustments = record.get("adjustments", [])
            if not isinstance(adjustments, str):
                adjustments = json.dumps(adjustments, ensure_ascii=False)
            conn.execute(
                "INSERT INTO iteration_history (date, timestamp, mode, overall_accuracy, recent_accuracy, verified_samples, adjustments, snapshot, diagnosis, token_usage) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (record.get("date", ""), record.get("timestamp", datetime.now().isoformat()),
                 record.get("mode", "rule"),
                 record.get("overall_accuracy"), record.get("recent_accuracy"),
                 record.get("verified_samples", 0),
                 adjustments,
                 record.get("snapshot", ""), record.get("diagnosis", ""),
                 record.get("token_usage", 0))
            )
            conn.commit()
        finally:
            conn.close()


def get_iteration_history(limit: int = 50) -> List[Dict]:
    with _db_lock:
        conn = _get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM iteration_history ORDER BY date DESC, timestamp DESC LIMIT ?",
                (limit,)
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                if d.get("adjustments"):
                    try:
                        d["adjustments"] = json.loads(d["adjustments"])
                    except (json.JSONDecodeError, TypeError):
                        d["adjustments"] = []
                else:
                    d["adjustments"] = []
                result.append(d)
            result.reverse()
            return result
        finally:
            conn.close()


def get_iteration_state() -> Dict:
    with _db_lock:
        conn = _get_conn()
        try:
            row = conn.execute("SELECT * FROM iteration_state WHERE id = 1").fetchone()
            if not row:
                return {
                    "token_month": "", "token_used": 0,
                    "last_iteration_date": "", "total_iterations": 0,
                    "current_weights": {},
                }
            result = {
                "token_month": row["token_month"] or "",
                "token_used": row["token_used"] or 0,
                "last_iteration_date": row["last_iteration_date"] or "",
                "total_iterations": row["total_iterations"] or 0,
                "current_weights": {},
            }
            if row["current_weights"]:
                try:
                    result["current_weights"] = json.loads(row["current_weights"])
                except (json.JSONDecodeError, TypeError):
                    result["current_weights"] = {}
            return result
        finally:
            conn.close()


def update_iteration_state(data: Dict):
    with _db_lock:
        conn = _get_conn()
        try:
            current_weights = data.get("current_weights", {})
            if not isinstance(current_weights, str):
                current_weights = json.dumps(current_weights, ensure_ascii=False)
            conn.execute(
                "UPDATE iteration_state SET token_month = ?, token_used = ?, last_iteration_date = ?, total_iterations = ?, current_weights = ?, updated_at = ? WHERE id = 1",
                (data.get("token_month", ""), int(data.get("token_used", 0)),
                 data.get("last_iteration_date", ""), int(data.get("total_iterations", 0)),
                 current_weights, datetime.now().isoformat())
            )
            conn.commit()
        finally:
            conn.close()


def delete_latest_iteration_history() -> bool:
    with _db_lock:
        conn = _get_conn()
        try:
            last_row = conn.execute("SELECT id FROM iteration_history ORDER BY id DESC LIMIT 1").fetchone()
            if last_row:
                conn.execute("DELETE FROM iteration_history WHERE id = ?", (last_row["id"],))
                conn.commit()
                return True
            return False
        finally:
            conn.close()


# ==================== 数据迁移 ====================

def migrate_from_json():
    init_db()
    from .utils import load_json

    prices = load_json(os.path.join(_DATA_DIR, "cache/gold_price_archive.json"))
    if prices and isinstance(prices, list):
        upsert_gold_prices(prices)
        print(f"  迁移金价归档: {len(prices)}条")

    holdings = load_json(os.path.join(_DATA_DIR, "cache/gold_holdings_history.json"))
    if holdings and isinstance(holdings, list):
        for record in holdings:
            d = record.get("date", "")
            positions = record.get("positions", [])
            if d and positions:
                upsert_holdings(d, positions)
        print(f"  迁移持仓历史: {len(holdings)}天")

    news_archive = load_json(os.path.join(_DATA_DIR, "cache/news_sentiment_archive.json"))
    if news_archive and isinstance(news_archive, list):
        for record in news_archive:
            d = record.get("date", "")
            if d:
                upsert_news_sentiment(d, record)
        print(f"  迁移新闻情绪归档: {len(news_archive)}条")

    macro_cache = load_json(os.path.join(_DATA_DIR, "cache/macro_indicators_cache.json"))
    if macro_cache and macro_cache.get("indicators"):
        insert_macro_snapshot(macro_cache["indicators"], macro_cache.get("timestamp"))
        print("  迁移宏观指标缓存: 1条")

    sr_cache = load_json(os.path.join(_DATA_DIR, "cache/support_resistance_cache.json"))
    if sr_cache:
        insert_support_resistance(sr_cache)
        print("  迁移支撑阻力缓存: 1条")

    reports_dir = os.path.join(_DATA_DIR, "reports")
    if os.path.isdir(reports_dir):
        for fname in os.listdir(reports_dir):
            if fname.startswith("report_") and fname.endswith(".txt"):
                d = fname[7:17]
                fpath = os.path.join(reports_dir, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        content = f.read()
                    if content:
                        upsert_report(d, content)
                except Exception:
                    pass
        print("  迁移报告文件")

    tracking = load_json(os.path.join(_DATA_DIR, "cache/prediction_tracking.json"))
    if tracking and isinstance(tracking, list):
        migrated = 0
        for record in tracking:
            d = record.get("date", "")
            if d:
                upsert_prediction_tracking(d, record)
                migrated += 1
        print(f"  迁移预测追踪: {migrated}条")

    iter_data = load_json(os.path.join(_DATA_DIR, "model_iteration.json"))
    if iter_data:
        state = {
            "token_month": iter_data.get("token_usage", {}).get("month", ""),
            "token_used": iter_data.get("token_usage", {}).get("used", 0),
            "last_iteration_date": iter_data.get("last_iteration_date", ""),
            "total_iterations": iter_data.get("total_iterations", 0),
            "current_weights": iter_data.get("current_weights", {}),
        }
        update_iteration_state(state)
        history = iter_data.get("history", [])
        for h in history:
            insert_iteration_history(h)
        print(f"  迁移迭代历史: {len(history)}条, 迭代状态1条")

    cleanup()
    print("  数据迁移完成，已清理过期数据")


def get_db_stats() -> Dict:
    with _db_lock:
        conn = _get_conn()
        try:
            stats = {}
            for table in _ALLOWED_TABLES:
                try:
                    row = conn.execute(f"SELECT COUNT(*) as cnt FROM {table}").fetchone()
                    stats[table] = row["cnt"]
                except Exception:
                    stats[table] = 0
            try:
                db_size = os.path.getsize(DB_PATH)
                stats["db_size_mb"] = round(db_size / 1024 / 1024, 2)
            except Exception:
                stats["db_size_mb"] = 0
            return stats
        finally:
            conn.close()


# ==================== 金价异动事件 ====================

def insert_price_event(event_type: str, price: float, change_pct: float,
                       day_high: float = 0, day_low: float = 0,
                       source: str = "", notified: bool = False):
    now = datetime.now()
    with _db_lock:
        conn = _get_conn()
        try:
            conn.execute(
                "INSERT INTO price_events (timestamp, date, event_type, price, change_pct, day_high, day_low, day_range, source, notified) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (now.isoformat(), now.strftime("%Y-%m-%d"), event_type,
                 price, change_pct, day_high, day_low,
                 round(day_high - day_low, 2) if day_high and day_low else 0,
                 source, 1 if notified else 0)
            )
            conn.commit()
        finally:
            conn.close()


def get_price_events(days: int = 30, event_type: Optional[str] = None) -> List[Dict]:
    with _db_lock:
        conn = _get_conn()
        try:
            cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            if event_type:
                rows = conn.execute(
                    "SELECT id, timestamp, date, event_type, price, change_pct, day_high, day_low, day_range, source, notified FROM price_events WHERE date >= ? AND event_type = ? ORDER BY timestamp DESC",
                    (cutoff, event_type)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, timestamp, date, event_type, price, change_pct, day_high, day_low, day_range, source, notified FROM price_events WHERE date >= ? ORDER BY timestamp DESC",
                    (cutoff,)
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def get_price_event_stats(days: int = 90) -> Dict:
    with _db_lock:
        conn = _get_conn()
        try:
            cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            surge_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM price_events WHERE date >= ? AND event_type = 'surge'",
                (cutoff,)
            ).fetchone()["cnt"]
            crash_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM price_events WHERE date >= ? AND event_type = 'crash'",
                (cutoff,)
            ).fetchone()["cnt"]
            recent = conn.execute(
                "SELECT timestamp, event_type, price, change_pct FROM price_events WHERE date >= ? ORDER BY timestamp DESC LIMIT 5",
                (cutoff,)
            ).fetchall()
            return {
                "surge_count": surge_count,
                "crash_count": crash_count,
                "total": surge_count + crash_count,
                "recent": [dict(r) for r in recent],
            }
        finally:
            conn.close()


init_db()
