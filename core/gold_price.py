# -*- coding: utf-8 -*-
"""
实时金价模块 - 多源实时抓取 + 每日归档

数据源（按优先级）：
  1. AKShare 伦敦金 XAU（新浪源，国内稳定，USD/oz）
  2. AKShare COMEX 黄金（新浪源，国内稳定，USD/oz）
  3. Yahoo Finance 5分钟K线（GC=F COMEX黄金期货，实时）
  4. api.gold-api.com（现货黄金XAU，备用）
  5. Swissquote Forex Feed（买卖价，备用）
  6. 数据库缓存（最后手段）

国内金价（补充）：
  - AKShare 上海黄金交易所 Au99.99（CNY/g）
  - AKShare 沪金期货实时行情（CNY/g）

历史日线：
  - AKShare 上海黄金交易所历史数据（SGE，优先）
  - Yahoo Finance API（备用）

存储策略：
  - 数据库 gold_prices 表 → 每日OHLC归档
  - 数据库 gold_prices_intra 表 → 盘中快照
  - 内存缓存 → 实时快照、国内金价
"""

import requests
import threading
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional

from . import db

_realtime_cache = None
_realtime_cache_lock = threading.Lock()
_domestic_cache = None
_domestic_cache_lock = threading.Lock()
_daily_cache_date = None
_daily_cache_prices = None
_daily_cache_lock = threading.Lock()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
}

_AKSHARE_AVAILABLE = None

_US_FIXED_HOLIDAYS = {
    (1, 1), (7, 4), (11, 27), (12, 25),
}


def _compute_us_holidays(year: int) -> set:
    """计算指定年份的美国假日（COMEX休市日）"""
    from datetime import date, timedelta as _td
    holidays = set()

    holidays.add(f"{year}-01-01")

    mlk_day = date(year, 1, 1)
    while mlk_day.weekday() != 0:
        mlk_day += _td(days=1)
    mlk_day += _td(days=14)
    holidays.add(mlk_day.isoformat())

    pres_day = date(year, 2, 1)
    while pres_day.weekday() != 0:
        pres_day += _td(days=1)
    pres_day += _td(days=14)
    holidays.add(pres_day.isoformat())

    easter = _easter_sunday(year)
    good_friday = (easter - timedelta(days=2)).isoformat()
    holidays.add(good_friday)

    mem_day = date(year, 5, 31)
    while mem_day.weekday() != 0:
        mem_day -= _td(days=1)
    holidays.add(mem_day.isoformat())

    if date(year, 7, 4).weekday() == 5:
        holidays.add(f"{year}-07-03")
    elif date(year, 7, 4).weekday() == 6:
        holidays.add(f"{year}-07-05")
    else:
        holidays.add(f"{year}-07-04")

    labor_day = date(year, 9, 1)
    while labor_day.weekday() != 0:
        labor_day += _td(days=1)
    holidays.add(labor_day.isoformat())

    thanksgiving = date(year, 11, 1)
    while thanksgiving.weekday() != 3:
        thanksgiving += _td(days=1)
    thanksgiving += _td(days=21)
    holidays.add(thanksgiving.isoformat())

    if date(year, 12, 25).weekday() == 5:
        holidays.add(f"{year}-12-24")
    elif date(year, 12, 25).weekday() == 6:
        holidays.add(f"{year}-12-26")
    else:
        holidays.add(f"{year}-12-25")

    return holidays


def _easter_sunday(year: int):
    """计算复活节日期（Anonymous Gregorian algorithm）"""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    from datetime import date as _date
    return _date(year, month, day)


_US_HOLIDAYS_CACHE: Dict[int, set] = {}


def _get_us_holidays(year: int) -> set:
    if year not in _US_HOLIDAYS_CACHE:
        _US_HOLIDAYS_CACHE[year] = _compute_us_holidays(year)
    return _US_HOLIDAYS_CACHE[year]


def _compute_cn_holidays(year: int) -> set:
    """计算指定年份的中国法定假日（SGE休市日，仅含工作日假日）"""
    from datetime import date as _date, timedelta as _td
    holidays = set()

    holidays.add(f"{year}-01-01")

    spring_festival_dates = {
        2024: (2, 10), 2025: (1, 29), 2026: (2, 17),
        2027: (2, 6), 2028: (1, 26), 2029: (2, 13), 2030: (2, 3),
    }
    if year in spring_festival_dates:
        m, d = spring_festival_dates[year]
        eve = _date(year, m, d) - _td(days=1)
        for offset in range(-1, 7):
            day = eve + _td(days=offset)
            if day.weekday() < 5:
                holidays.add(day.isoformat())

    qingming_dates = {2024: (4, 4), 2025: (4, 4), 2026: (4, 5), 2027: (4, 5), 2028: (4, 4), 2029: (4, 4), 2030: (4, 5)}
    if year in qingming_dates:
        m, d = qingming_dates[year]
        for offset in range(3):
            day = _date(year, m, d) + _td(days=offset)
            if day.weekday() < 5:
                holidays.add(day.isoformat())

    labor_day = _date(year, 5, 1)
    for offset in range(5):
        day = labor_day + _td(days=offset)
        if day.weekday() < 5:
            holidays.add(day.isoformat())

    duanwu_dates = {2024: (6, 10), 2025: (5, 31), 2026: (6, 19), 2027: (6, 9), 2028: (5, 28), 2029: (6, 16), 2030: (6, 5)}
    if year in duanwu_dates:
        m, d = duanwu_dates[year]
        for offset in range(3):
            day = _date(year, m, d) + _td(days=offset)
            if day.weekday() < 5:
                holidays.add(day.isoformat())

    mid_autumn_dates = {2024: (9, 17), 2025: (10, 6), 2026: (9, 25), 2027: (9, 15), 2028: (10, 3), 2029: (9, 22), 2030: (9, 12)}
    if year in mid_autumn_dates:
        m, d = mid_autumn_dates[year]
        for offset in range(3):
            day = _date(year, m, d) + _td(days=offset)
            if day.weekday() < 5:
                holidays.add(day.isoformat())

    national_day = _date(year, 10, 1)
    for offset in range(7):
        day = national_day + _td(days=offset)
        if day.weekday() < 5:
            holidays.add(day.isoformat())

    return holidays


_CN_HOLIDAYS_CACHE: Dict[int, set] = {}


def get_cn_holidays(year: int) -> set:
    """获取指定年份的中国法定假日（公开接口）"""
    if year not in _CN_HOLIDAYS_CACHE:
        _CN_HOLIDAYS_CACHE[year] = _compute_cn_holidays(year)
    return _CN_HOLIDAYS_CACHE[year]


def get_market_status() -> Dict:
    """
    判断COMEX黄金期货市场状态
    返回 {"status": "open"|"closed"|"pre_market", "reason": str, "next_open": str}
    """
    now_et = datetime.now(timezone(timedelta(hours=-5)))
    weekday = now_et.weekday()
    date_str = now_et.strftime("%Y-%m-%d")
    hour = now_et.hour
    minute = now_et.minute

    us_holidays = _get_us_holidays(now_et.year)

    if date_str in us_holidays:
        return {"status": "closed", "reason": "美国假日休市", "next_open": ""}

    if weekday == 5:
        return {"status": "closed", "reason": "周六休市", "next_open": "周日18:00 ET"}
    if weekday == 6 and hour < 18:
        return {"status": "closed", "reason": "周末休市", "next_open": "今日18:00 ET"}

    if weekday == 4 and hour >= 17:
        return {"status": "closed", "reason": "周五收盘后休市", "next_open": "周日18:00 ET"}

    if 17 <= hour < 18:
        return {"status": "closed", "reason": "日内休市(17:00-18:00 ET)", "next_open": "今日18:00 ET"}

    return {"status": "open", "reason": "", "next_open": ""}


def _check_akshare():
    global _AKSHARE_AVAILABLE
    if _AKSHARE_AVAILABLE is None:
        try:
            import akshare
            _AKSHARE_AVAILABLE = True
        except ImportError:
            _AKSHARE_AVAILABLE = False
    return _AKSHARE_AVAILABLE


def _parse_akshare_time(row):
    try:
        for col in ["更新时间", "时间", "datetime", "date", "time"]:
            val = row.get(col, "")
            if val and str(val).strip():
                s = str(val).strip()
                for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y%m%d", "%Y-%m-%d"]:
                    try:
                        dt = datetime.strptime(s, fmt)
                        utc_offset = datetime.now() - datetime.now(timezone.utc).replace(tzinfo=None)
                        dt_utc = dt - utc_offset
                        return dt_utc.strftime("%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        continue
    except Exception:
        pass
    return None


def get_realtime_price() -> Optional[Dict]:
    """
    获取实时金价（多源fallback）
    成功时更新缓存，失败时返回上次缓存
    """
    market_status = get_market_status()

    if _check_akshare():
        result = _fetch_akshare_xau()
        if result:
            _fetch_domestic_price()
            result["market_status"] = market_status
            return result

        result = _fetch_akshare_comex()
        if result:
            _fetch_domestic_price()
            result["market_status"] = market_status
            return result

    result = _fetch_yahoo_realtime()
    if result:
        _fetch_domestic_price()
        result["market_status"] = market_status
        return result

    result = _fetch_gold_api()
    if result:
        result["market_status"] = market_status
        return result

    result = _fetch_swissquote()
    if result:
        result["market_status"] = market_status
        return result

    print(f"  [WARN] 所有实时金价源均失败，使用上次缓存")
    with _realtime_cache_lock:
        if _realtime_cache:
            _realtime_cache["market_status"] = market_status
            return _realtime_cache
    try:
        rows = db.get_intraday_snapshots(1)
        if rows:
            latest = rows[-1]
            return {
                "price": latest.get("price", 0),
                "change": 0,
                "change_pct": 0,
                "high": 0,
                "low": 0,
                "volume": 0,
                "timestamp": latest.get("time", ""),
                "source": latest.get("source", "db_cache"),
                "market_status": market_status,
            }
    except Exception:
        pass
    return None


def _fetch_akshare_xau() -> Optional[Dict]:
    """从 AKShare 获取伦敦金 XAU 实时价格（新浪源，USD/oz）"""
    global _realtime_cache
    try:
        import akshare as ak
        df = ak.futures_foreign_commodity_realtime(symbol="XAU")
        if df is None or df.empty:
            return None

        row = df.iloc[0]
        price = float(row.get("最新价", 0))
        if not price or price <= 0:
            return None

        prev_settle = float(row.get("昨日结算价", 0))
        if not prev_settle or prev_settle <= 0:
            prev_settle = price
        change = price - prev_settle
        change_pct = (change / prev_settle * 100) if prev_settle else 0

        high = float(row.get("最高价", 0))
        low = float(row.get("最低价", 0))

        result_data = {
            "price": round(price, 2),
            "change": round(change, 2),
            "change_pct": round(change_pct, 2),
            "high": round(high, 2) if high > 0 else 0,
            "low": round(low, 2) if low > 0 else 0,
            "volume": 0,
            "prev_close": round(prev_settle, 2),
            "timestamp": _parse_akshare_time(row) or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "source": "akshare_xau",
            "contract": "XAU",
        }

        with _realtime_cache_lock:
            _realtime_cache = result_data
        print(f"  AKShare 伦敦金: {result_data['price']:.2f} USD/oz ({change_pct:+.2f}%)")
        return result_data

    except Exception as e:
        print(f"  [WARN] AKShare 伦敦金获取失败: {e}")
        return None


def _fetch_akshare_comex() -> Optional[Dict]:
    """从 AKShare 获取 COMEX 黄金实时价格（新浪源，USD/oz）"""
    global _realtime_cache
    try:
        import akshare as ak
        df = ak.futures_foreign_commodity_realtime(symbol="GC")
        if df is None or df.empty:
            return None

        row = df.iloc[0]
        price = float(row.get("最新价", 0))
        if not price or price <= 0:
            return None

        prev_settle = float(row.get("昨日结算价", 0))
        if not prev_settle or prev_settle <= 0:
            prev_settle = price
        change = price - prev_settle
        change_pct = (change / prev_settle * 100) if prev_settle else 0

        high = float(row.get("最高价", 0))
        low = float(row.get("最低价", 0))

        result_data = {
            "price": round(price, 2),
            "change": round(change, 2),
            "change_pct": round(change_pct, 2),
            "high": round(high, 2) if high > 0 else 0,
            "low": round(low, 2) if low > 0 else 0,
            "volume": 0,
            "prev_close": round(prev_settle, 2),
            "timestamp": _parse_akshare_time(row) or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "source": "akshare_comex",
            "contract": "GC",
        }

        with _realtime_cache_lock:
            _realtime_cache = result_data
        print(f"  AKShare COMEX黄金: {result_data['price']:.2f} USD/oz ({change_pct:+.2f}%)")
        return result_data

    except Exception as e:
        print(f"  [WARN] AKShare COMEX黄金获取失败: {e}")
        return None


def _fetch_domestic_price():
    """获取国内金价（SGE Au99.99 + 沪金期货），不阻塞主流程"""
    global _domestic_cache
    try:
        import akshare as ak

        domestic = {}

        for attempt in range(2):
            try:
                df = ak.spot_quotations_sge(symbol="Au99.99")
                if df is not None and not df.empty:
                    latest = df.iloc[-1]
                    sge_price = float(latest.get("现价", 0))
                    if sge_price > 0:
                        domestic["sge_au9999"] = {
                            "price": round(sge_price, 2),
                            "unit": "CNY/g",
                            "source": "SGE",
                            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        }
                break
            except Exception as e:
                if attempt == 0 and ("Expecting value" in str(e) or "JSON" in str(e)):
                    import time
                    time.sleep(1)
                    continue
                print(f"  [WARN] AKShare SGE Au99.99 获取失败: {e}")

        try:
            df = ak.futures_zh_realtime(symbol="黄金")
            if df is not None and not df.empty:
                main_row = df[df["symbol"] == "AU0"]
                if main_row.empty:
                    main_row = df[df["symbol"].str.match(r"^AU\d{1,2}0$")]
                if main_row.empty:
                    main_row = df.head(1)
                if not main_row.empty:
                    row = main_row.iloc[0]
                    shfe_price = float(row.get("trade", 0))
                    prev_settle = float(row.get("prevsettlement", 0))
                    if shfe_price > 0:
                        change = shfe_price - prev_settle if prev_settle > 0 else 0
                        change_pct = (change / prev_settle * 100) if prev_settle > 0 else 0
                        domestic["shfe_au"] = {
                            "price": round(shfe_price, 2),
                            "change": round(change, 2),
                            "change_pct": round(change_pct, 2),
                            "unit": "CNY/g",
                            "contract": row.get("symbol", ""),
                            "source": "SHFE",
                            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        }
        except Exception as e:
            print(f"  [WARN] AKShare 沪金期货获取失败: {e}")

        if domestic:
            domestic["update_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with _domestic_cache_lock:
                _domestic_cache = domestic

    except ImportError:
        pass
    except Exception as e:
        print(f"  [WARN] 国内金价获取失败: {e}")


def get_domestic_price() -> Optional[Dict]:
    """获取国内金价（SGE Au99.99 + 沪金期货），带缓存"""
    with _domestic_cache_lock:
        if _domestic_cache:
            update_time = _domestic_cache.get("update_time", "")
            if update_time:
                cache_age = (datetime.now() - datetime.strptime(update_time, "%Y-%m-%d %H:%M:%S")).total_seconds()
                if cache_age < 300:
                    return _domestic_cache

    _fetch_domestic_price()
    with _domestic_cache_lock:
        return _domestic_cache


def _fetch_yahoo_realtime() -> Optional[Dict]:
    """从 Yahoo Finance 获取5分钟K线实时数据"""
    global _realtime_cache
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/GC=F?interval=5m&range=1d"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()

        data = resp.json()
        result = data["chart"]["result"][0]
        meta = result["meta"]

        price = meta.get("regularMarketPrice", 0)
        if not price:
            return None

        prev_close = meta.get("previousClose", price)
        change = price - prev_close
        change_pct = (change / prev_close * 100) if prev_close else 0

        result_data = {
            "price": round(price, 2),
            "change": round(change, 2),
            "change_pct": round(change_pct, 2),
            "high": round(meta.get("regularMarketDayHigh", 0), 2),
            "low": round(meta.get("regularMarketDayLow", 0), 2),
            "volume": int(meta.get("regularMarketVolume", 0)),
            "prev_close": round(prev_close, 2),
            "timestamp": datetime.fromtimestamp(int(meta["regularMarketTime"]), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if meta.get("regularMarketTime") and isinstance(meta["regularMarketTime"], (int, float)) else datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "source": "yahoo_finance",
            "contract": meta.get("symbol", "GC=F"),
        }

        with _realtime_cache_lock:
            _realtime_cache = result_data
        print(f"  Yahoo Finance 实时金价: {result_data['price']:.2f} USD/oz")
        return result_data

    except requests.exceptions.RequestException as e:
        print(f"  [WARN] Yahoo Finance 实时获取失败: {e}")
        return None
    except (KeyError, ValueError) as e:
        print(f"  [WARN] Yahoo Finance 数据解析失败: {e}")
        return None


def _fetch_gold_api() -> Optional[Dict]:
    """从 api.gold-api.com 获取现货金价"""
    global _realtime_cache
    try:
        url = "https://api.gold-api.com/price/XAU"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()

        data = resp.json()
        price = data.get("price", 0)
        if not price:
            return None

        prev_close = _realtime_cache.get("prev_close", 0) if _realtime_cache else 0
        if not prev_close or prev_close <= 0:
            prev_close = price
        change = price - prev_close
        change_pct = (change / prev_close * 100) if prev_close else 0

        result_data = {
            "price": round(price, 2),
            "change": round(change, 2),
            "change_pct": round(change_pct, 2),
            "high": 0,
            "low": 0,
            "volume": 0,
            "prev_close": round(prev_close, 2),
            "timestamp": data.get("timestamp", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")),
            "source": "gold-api.com",
            "contract": "XAU",
        }

        with _realtime_cache_lock:
            _realtime_cache = result_data
        print(f"  gold-api.com 现货金价: {result_data['price']:.2f} USD/oz")
        return result_data

    except requests.exceptions.RequestException as e:
        print(f"  [WARN] gold-api.com 获取失败: {e}")
        return None
    except (KeyError, ValueError) as e:
        print(f"  [WARN] gold-api.com 数据解析失败: {e}")
        return None


def _fetch_swissquote() -> Optional[Dict]:
    """从 Swissquote 获取买卖价"""
    global _realtime_cache
    try:
        url = "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/XAU/USD"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()

        data = resp.json()
        if not data or not data[0].get("spreadProfilePrices"):
            return None

        prices = data[0]["spreadProfilePrices"][0]
        bid = prices.get("bid", 0)
        ask = prices.get("ask", 0)
        price = (bid + ask) / 2

        sq_ts = data[0].get("ts")
        ts_str = None
        if sq_ts:
            try:
                ts_str = datetime.fromtimestamp(int(sq_ts) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            except (ValueError, OSError):
                pass

        prev_close = _realtime_cache.get("prev_close", 0) if _realtime_cache else 0
        if not prev_close or prev_close <= 0:
            prev_close = price
        change = price - prev_close
        change_pct = (change / prev_close * 100) if prev_close else 0

        result_data = {
            "price": round(price, 2),
            "change": round(change, 2),
            "change_pct": round(change_pct, 2),
            "high": 0,
            "low": 0,
            "volume": 0,
            "bid": round(bid, 2),
            "ask": round(ask, 2),
            "prev_close": round(prev_close, 2),
            "timestamp": ts_str or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "source": "swissquote",
            "contract": "XAU/USD",
        }

        with _realtime_cache_lock:
            _realtime_cache = result_data
        print(f"  Swissquote 金价: {result_data['price']:.2f} USD/oz (bid:{bid:.2f} ask:{ask:.2f})")
        return result_data

    except requests.exceptions.RequestException as e:
        print(f"  [WARN] Swissquote 获取失败: {e}")
        return None
    except (KeyError, ValueError, IndexError) as e:
        print(f"  [WARN] Swissquote 数据解析失败: {e}")
        return None


def get_intraday_kline(interval: str = "5m", range_str: str = "1d") -> List[Dict]:
    """
    获取盘中K线数据（用于更精细的分析）
    """
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/GC=F?interval={interval}&range={range_str}"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()

        data = resp.json()
        result = data["chart"]["result"][0]
        timestamps = result.get("timestamp")
        if not timestamps:
            meta = result.get("meta", {})
            price = meta.get("regularMarketPrice", 0)
            if price and price > 0:
                dt_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                print(f"  [WARN] K线timestamp缺失，使用meta降级数据（休市日）")
                return [{
                    "time": dt_str,
                    "open": round(meta.get("regularMarketOpen", meta.get("previousClose", price)), 2),
                    "high": round(meta.get("regularMarketDayHigh", price), 2),
                    "low": round(meta.get("regularMarketDayLow", price), 2),
                    "close": round(price, 2),
                    "volume": int(meta.get("regularMarketVolume", 0)),
                    "_degraded": True,
                }]
            print("  [WARN] K线数据解析失败: API未返回timestamp且无meta降级数据")
            return []
        quotes = result["indicators"]["quote"][0]

        klines = []
        for i in range(len(timestamps)):
            close = quotes["close"][i]
            if close is None:
                continue

            dt = datetime.fromtimestamp(timestamps[i], tz=timezone.utc)
            klines.append({
                "time": dt.strftime("%Y-%m-%d %H:%M:%S"),
                "open": round(quotes["open"][i] or 0, 2),
                "high": round(quotes["high"][i] or 0, 2),
                "low": round(quotes["low"][i] or 0, 2),
                "close": round(close, 2),
                "volume": int(quotes["volume"][i] or 0),
            })

        return klines

    except requests.exceptions.RequestException as e:
        print(f"  [WARN] K线数据获取失败: {e}")
        return []
    except (KeyError, ValueError) as e:
        print(f"  [WARN] K线数据解析失败: {e}")
        return []


def get_daily_history(days: int = 30, prefer_international: bool = False) -> List[Dict]:
    """
    获取每日金价历史（从数据库读取，不足时从API补充）
    """
    global _daily_cache_date, _daily_cache_prices
    with _daily_cache_lock:
        today = datetime.now().strftime("%Y-%m-%d")
        if _daily_cache_date == today and _daily_cache_prices:
            cached = _daily_cache_prices[-days:]
            if prefer_international:
                if cached and cached[-1].get("source") == "yahoo":
                    print(f"  金价日线使用内存缓存（国际，{len(cached)}天）")
                    return cached
            else:
                print(f"  金价日线使用内存缓存（{len(cached)}天）")
                return cached

    try:
        db_prices = db.get_gold_prices(days + 10)
        if db_prices and len(db_prices) >= days:
            with _daily_cache_lock:
                _daily_cache_date = today
                _daily_cache_prices = db_prices
            cached = db_prices[-days:]
            if prefer_international:
                if cached and cached[-1].get("source") == "yahoo":
                    print(f"  金价日线使用数据库缓存（国际，{len(cached)}天）")
                    return cached
            else:
                print(f"  金价日线使用数据库缓存（{len(cached)}天）")
                return cached
    except Exception:
        pass

    if not prefer_international and _check_akshare():
        prices = _fetch_sge_daily_history(days)
        if prices:
            _save_daily_to_db(prices)
            with _daily_cache_lock:
                _daily_cache_date = today
                _daily_cache_prices = prices
            return prices[-days:]

    prices = _fetch_yahoo_daily_history(days)
    if prices:
        _save_daily_to_db(prices)
        with _daily_cache_lock:
            _daily_cache_date = today
            _daily_cache_prices = prices
        return prices[-days:]

    with _daily_cache_lock:
        if _daily_cache_prices:
            return _daily_cache_prices[-days:]
    return []


def _save_daily_to_db(prices: List[Dict]):
    """将日线数据保存到数据库（跳过休市降级数据）"""
    try:
        clean = [p for p in prices if not p.get("_degraded")]
        if clean:
            db.upsert_gold_prices(clean)
    except Exception:
        pass


def _fetch_sge_daily_history(days: int = 30) -> Optional[List[Dict]]:
    """从 AKShare 获取上海黄金交易所 Au99.99 历史日线"""
    try:
        import akshare as ak
        df = ak.spot_hist_sge(symbol="Au99.99")
        if df is None or df.empty:
            return None

        prices = []
        for _, row in df.iterrows():
            close = row.get("close", 0)
            if not close or close <= 0:
                continue
            prices.append({
                "date": str(row.get("date", "")),
                "open": round(float(row.get("open", 0)), 2),
                "high": round(float(row.get("high", 0)), 2),
                "low": round(float(row.get("low", 0)), 2),
                "close": round(float(close), 2),
                "volume": 0,
                "source": "SGE",
                "unit": "CNY/g",
            })

        if not prices:
            return None

        print(f"  AKShare SGE 日线: 获取到 {len(prices)} 天数据 (CNY/g)")
        return prices[-(days + 10):]

    except Exception as e:
        print(f"  [WARN] AKShare SGE 日线获取失败: {e}")
        return None


def _fetch_yahoo_daily_history(days: int = 30) -> Optional[List[Dict]]:
    """从 Yahoo Finance 获取每日金价历史"""
    print(f"  正在从Yahoo Finance获取日线数据...")
    try:
        range_str = f"{days + 10}d"
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/GC=F?range={range_str}&interval=1d"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()

        data = resp.json()
        result = data["chart"]["result"][0]
        timestamps = result.get("timestamp")
        if not timestamps:
            meta = result.get("meta", {})
            price = meta.get("regularMarketPrice", 0)
            if price and price > 0:
                dt_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
                print(f"  [WARN] 日线timestamp缺失，使用meta降级数据（休市日）")
                return [{
                    "date": dt_str,
                    "open": round(meta.get("regularMarketOpen", meta.get("previousClose", price)), 2),
                    "high": round(meta.get("regularMarketDayHigh", price), 2),
                    "low": round(meta.get("regularMarketDayLow", price), 2),
                    "close": round(price, 2),
                    "volume": int(meta.get("regularMarketVolume", 0)),
                    "source": "yahoo",
                    "unit": "USD/oz",
                    "_degraded": True,
                }]
            print("  [WARN] 日线数据解析失败: API未返回timestamp且无meta降级数据")
            return None
        quotes = result["indicators"]["quote"][0]

        prices = []
        for i in range(len(timestamps)):
            close = quotes["close"][i]
            if close is None:
                continue

            dt = datetime.fromtimestamp(timestamps[i], tz=timezone.utc)
            prices.append({
                "date": dt.strftime("%Y-%m-%d"),
                "open": round(quotes["open"][i] or 0, 2),
                "high": round(quotes["high"][i] or 0, 2),
                "low": round(quotes["low"][i] or 0, 2),
                "close": round(close, 2),
                "volume": int(quotes["volume"][i] or 0),
                "source": "yahoo",
                "unit": "USD/oz",
            })

        print(f"  获取到 {len(prices)} 天日线数据")
        return prices[-(days + 10):]

    except requests.exceptions.RequestException as e:
        print(f"  [WARN] 日线获取失败: {e}")
        return None
    except (KeyError, ValueError) as e:
        print(f"  [WARN] 日线数据解析失败: {e}")
        return None


def archive_realtime_price(realtime: Optional[Dict] = None):
    """
    将实时金价归档到数据库
    每次调用都会追加一条快照到 gold_prices_intra 表
    同时更新 gold_prices 表的当日OHLC
    """
    if realtime is None:
        realtime = get_realtime_price()
    if not realtime:
        return None

    today = datetime.now().strftime("%Y-%m-%d")

    try:
        db.insert_intraday_snapshot(
            today,
            realtime["timestamp"],
            realtime["price"],
            realtime.get("change", 0),
            realtime.get("change_pct", 0),
            realtime.get("source", ""),
        )
    except Exception as e:
        print(f"  [WARN] 日内快照保存失败: {e}")

    try:
        snapshots = db.get_intraday_snapshots(1)
        today_snaps = [s for s in snapshots if s.get("date") == today]
        if today_snaps:
            prices = [s["price"] for s in today_snaps]
            db.upsert_gold_prices({
                "date": today,
                "open": round(prices[0], 2),
                "high": round(max(prices), 2),
                "low": round(min(prices), 2),
                "close": round(prices[-1], 2),
                "source": realtime.get("source", ""),
            })
    except Exception as e:
        print(f"  [WARN] 日线OHLC更新失败: {e}")

    return realtime


def get_today_intraday() -> List[Dict]:
    """获取今日盘中所有快照"""
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        snapshots = db.get_intraday_snapshots(1)
        return [s for s in snapshots if s.get("date") == today]
    except Exception:
        return []


def get_price_summary(realtime: Optional[Dict] = None) -> Dict:
    """获取金价概览（用于报告）"""
    if realtime is None:
        realtime = get_realtime_price()
    if not realtime:
        return {"available": False}

    klines = get_intraday_kline("5m", "1d")
    intraday_high = max([k["high"] for k in klines], default=realtime["price"])
    intraday_low = min([k["low"] for k in klines], default=realtime["price"])

    return {
        "available": True,
        "price": realtime["price"],
        "change": realtime["change"],
        "change_pct": realtime["change_pct"],
        "high": realtime.get("high") or intraday_high,
        "low": realtime.get("low") or intraday_low,
        "volume": realtime.get("volume", 0),
        "prev_close": realtime.get("prev_close", 0),
        "source": realtime.get("source", ""),
        "timestamp": realtime["timestamp"],
        "intraday_range": round(intraday_high - intraday_low, 2),
        "market_status": realtime.get("market_status", {}),
    }
