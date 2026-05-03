# -*- coding: utf-8 -*-
"""
宏观指标获取模块 - 多数据源 fallback

数据源优先级：
  1. AKShare（免费，国内源，稳定，批量获取美债/美元指数/VIX/原油）
  2. Yahoo Finance query2 API（免费，无需 API Key）
  3. Yahoo Finance query1 API（备用）
  4. Stooq.com CSV API（免费，无需 API Key，部分指标）
  5. FRED API（免费，需 API Key，最可靠的国债/VIX/原油数据）

指标：
  - 美债10年期收益率
  - 美债2年期收益率
  - 美债5年期收益率
  - 美元指数 DXY
  - VIX 恐慌指数
  - 原油 WTI

缓存策略：同一天内只请求一次
"""

import os
import re
import csv
import json
import time
import subprocess
import requests
from datetime import datetime, timezone
from io import StringIO
from typing import Dict, Optional
from urllib.parse import quote

_macro_cache = None

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

YAHOO_SYMBOLS = {
    "us_10y_yield": {"symbol": "^TNX", "name": "美债10Y收益率", "unit": "%", "direction": "inverse"},
    "us_2y_yield": {"symbol": "", "name": "美债2Y收益率", "unit": "%", "direction": "inverse", "skip_yahoo": True},
    "us_5y_yield": {"symbol": "^FVX", "name": "美债5Y收益率", "unit": "%", "direction": "inverse"},
    "tips_10y_yield": {"symbol": "", "name": "TIPS10Y收益率", "unit": "%", "direction": "inverse", "skip_yahoo": True},
    "dxy": {"symbol": "DX-Y.NYB", "name": "美元指数", "unit": "", "direction": "inverse"},
    "vix": {"symbol": "^VIX", "name": "VIX恐慌指数", "unit": "", "direction": "positive"},
    "crude_oil": {"symbol": "CL=F", "name": "WTI原油", "unit": "USD", "direction": "positive"},
    "gld_etf": {"symbol": "GLD", "name": "GLD黄金ETF", "unit": "USD", "direction": "positive"},
}

STOOQ_SYMBOLS = {
    "dxy": "dx.f",
    "crude_oil": "cl.f",
    "vix": "vix",
    "gld_etf": "gld.us",
}

FRED_SERIES = {
    "us_10y_yield": "DGS10",
    "us_5y_yield": "DGS5",
    "us_2y_yield": "DGS2",
    "tips_10y_yield": "DFII10",
    "vix": "VIXCLS",
    "crude_oil": "DCOILWTICO",
    "dxy": "DTWEXBGS",
}

_AKSHARE_AVAILABLE = None


def _not_nan(val):
    import math
    if val is None:
        return False
    try:
        if math.isnan(float(val)):
            return False
    except (ValueError, TypeError):
        pass
    return True


def _check_akshare():
    global _AKSHARE_AVAILABLE
    if _AKSHARE_AVAILABLE is None:
        try:
            import akshare
            _AKSHARE_AVAILABLE = True
        except ImportError:
            _AKSHARE_AVAILABLE = False
    return _AKSHARE_AVAILABLE


def fetch_macro_indicators() -> Dict:
    global _macro_cache
    today = datetime.now().strftime("%Y-%m-%d")
    if _macro_cache and _macro_cache.get("date") == today:
        all_valid = any(v.get("value") is not None for v in _macro_cache.get("indicators", {}).values() if isinstance(v, dict))
        if all_valid:
            return _macro_cache

    print("  正在获取宏观指标...")
    indicators = {}

    if _check_akshare():
        ak_data = _fetch_akshare_batch()
        if ak_data:
            for key, data in ak_data.items():
                config = YAHOO_SYMBOLS.get(key)
                if config and data and _not_nan(data.get("price")):
                    _log_success(config['name'], data, config['unit'], "akshare")
                    indicators[key] = _build_indicator(data, config)

    for key, config in YAHOO_SYMBOLS.items():
        if key in indicators:
            continue

        if config.get("skip_yahoo"):
            continue

        data = None

        data = _fetch_yahoo_chart(config["symbol"], server="query2")
        if data:
            _log_success(config['name'], data, config['unit'], "yahoo-q2")
            indicators[key] = _build_indicator(data, config)
            time.sleep(0.5)
            continue

        time.sleep(0.5)
        data = _fetch_yahoo_chart(config["symbol"], server="query1")
        if data:
            _log_success(config['name'], data, config['unit'], "yahoo-q1")
            indicators[key] = _build_indicator(data, config)
            time.sleep(0.5)
            continue

        time.sleep(1)
        data = _fetch_yahoo_chart(config["symbol"], server="query2")
        if data:
            _log_success(config['name'], data, config['unit'], "yahoo-q2-retry")
            indicators[key] = _build_indicator(data, config)
            time.sleep(0.5)
            continue

        if key in STOOQ_SYMBOLS:
            data = _fetch_stooq(STOOQ_SYMBOLS[key])
            if data:
                _log_success(config['name'], data, config['unit'], "stooq")
                indicators[key] = _build_indicator(data, config)
                time.sleep(0.3)
                continue

        if key in FRED_SERIES:
            data = _fetch_fred(FRED_SERIES[key])
            if data:
                _log_success(config['name'], data, config['unit'], "fred")
                indicators[key] = _build_indicator(data, config)
                time.sleep(0.3)
                continue

        data = _fetch_yahoo_scrape(config["symbol"])
        if data:
            _log_success(config['name'], data, config['unit'], "yahoo-scrape")
            indicators[key] = _build_indicator(data, config)
        else:
            indicators[key] = None
            print(f"    {config['name']}: 所有数据源均失败")

    gold_impact = _calc_gold_impact(indicators)

    breakeven_inflation = _calc_breakeven_inflation(indicators)
    if breakeven_inflation is not None:
        indicators["breakeven_inflation"] = breakeven_inflation

    latest_data_ts = None
    for v in indicators.values():
        if isinstance(v, dict) and v.get("data_timestamp"):
            ts_str = v["data_timestamp"]
            try:
                ts_dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                if latest_data_ts is None or ts_dt > latest_data_ts:
                    latest_data_ts = ts_dt
            except ValueError:
                pass

    result = {
        "timestamp": latest_data_ts.strftime("%Y-%m-%d %H:%M:%S") if latest_data_ts else datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "date": today,
        "indicators": indicators,
        "gold_impact": gold_impact,
    }

    has_any = any(v is not None for v in indicators.values())
    if has_any:
        _macro_cache = result
    else:
        if _macro_cache:
            return _macro_cache

    return result


def _log_success(name, data, unit, source):
    print(f"    {name}: {data['price']:.2f}{unit} ({data['change_pct']:+.2f}%) [{source}]")


def _fetch_akshare_batch() -> Optional[Dict]:
    try:
        import akshare as ak
        result = {}

        try:
            df = ak.bond_zh_us_rate()
            if df is not None and len(df) > 1:
                latest = df.iloc[-1]
                prev = df.iloc[-2]
                for col, key in [
                    ("美国国债收益率10年", "us_10y_yield"),
                    ("美国国债收益率5年", "us_5y_yield"),
                    ("美国国债收益率2年", "us_2y_yield"),
                ]:
                    if col in df.columns and _not_nan(latest[col]) and _not_nan(prev[col]):
                        price = float(latest[col])
                        prev_val = float(prev[col])
                        change = price - prev_val
                        change_pct = (change / prev_val * 100) if prev_val else 0
                        result[key] = {
                            "price": round(price, 4),
                            "change": round(change, 4),
                            "change_pct": round(change_pct, 2),
                        }
        except Exception as e:
            print(f"    [akshare] bond_zh_us_rate error: {e}")

        for _attempt in range(2):
            try:
                df = ak.index_global_spot_em()
                if df is not None and len(df) > 0:
                    for name, key in [("美元指数", "dxy"), ("VIX", "vix")]:
                        row_match = df[df["名称"] == name]
                        if len(row_match) > 0:
                            row = row_match.iloc[0]
                            price = float(row["最新价"])
                            prev = float(row["昨收价"])
                            change = price - prev
                            change_pct = (change / prev * 100) if prev else 0
                            result[key] = {
                                "price": round(price, 4),
                                "change": round(change, 4),
                                "change_pct": round(change_pct, 2),
                            }
                break
            except Exception as e:
                if _attempt == 0 and ("SSL" in str(e) or "Connection" in str(e)):
                    import time
                    time.sleep(2)
                    continue
                print(f"    [akshare] index_global_spot_em error: {e}")

        try:
            df = ak.futures_foreign_commodity_realtime(symbol="CL")
            if df is not None and not df.empty:
                row = df.iloc[0]
                price = float(row.get("最新价", 0))
                prev_settle = float(row.get("昨日结算价", 0))
                if price > 0:
                    if not prev_settle or prev_settle <= 0:
                        prev_settle = price
                    change = price - prev_settle
                    change_pct = (change / prev_settle * 100) if prev_settle else 0
                    result["crude_oil"] = {
                        "price": round(price, 4),
                        "change": round(change, 4),
                        "change_pct": round(change_pct, 2),
                    }
        except Exception as e:
            print(f"    [akshare] futures_foreign_commodity_realtime error: {e}")

        return result if result else None

    except Exception as e:
        print(f"    [akshare] batch error: {e}")
        return None


def _calc_gold_impact(indicators: dict) -> str:
    score = 0.0
    if indicators.get("us_10y_yield") and indicators["us_10y_yield"].get("change") is not None:
        chg = indicators["us_10y_yield"]["change"]
        if chg < -0.05:
            score += 0.3
        elif chg > 0.05:
            score -= 0.3
    if indicators.get("dxy") and indicators["dxy"].get("change_pct") is not None:
        pct = indicators["dxy"]["change_pct"]
        if pct < -0.3:
            score += 0.3
        elif pct > 0.3:
            score -= 0.3
    if indicators.get("vix") and indicators["vix"].get("value") is not None:
        val = indicators["vix"]["value"]
        if val > 25:
            score += 0.3
        elif val < 15:
            score -= 0.1
    if score > 0.2:
        return "偏多"
    elif score < -0.2:
        return "偏空"
    return "中性"


def _calc_breakeven_inflation(indicators: dict) -> Optional[Dict]:
    nominal = indicators.get("us_10y_yield")
    tips = indicators.get("tips_10y_yield")
    if not nominal or not tips:
        return None
    try:
        n_val = float(nominal["value"]) if nominal.get("value") is not None else None
        t_val = float(tips["value"]) if tips.get("value") is not None else None
        if n_val is None or t_val is None:
            return None
        if n_val != n_val or t_val != t_val:
            return None
        bei = n_val - t_val
        n_prev = n_val - float(nominal.get("change", 0))
        t_prev = t_val - float(tips.get("change", 0))
        bei_prev = n_prev - t_prev
        bei_change = bei - bei_prev
        bei_change_pct = (bei_change / abs(bei_prev) * 100) if bei_prev != 0 else 0
        return {
            "value": round(bei, 4),
            "change": round(bei_change, 4),
            "change_pct": round(bei_change_pct, 2),
            "name": "盈亏平衡通胀率(10Y)",
            "unit": "%",
            "direction": "positive",
        }
    except (ValueError, TypeError):
        return None


def _build_indicator(data, config):
    import math
    value = data["price"]
    if value is not None:
        try:
            if math.isnan(float(value)):
                value = None
        except (ValueError, TypeError):
            pass
    change = data.get("change")
    change_pct = data.get("change_pct")
    if value is None:
        change = None
        change_pct = None
    else:
        if change is not None:
            try:
                if math.isnan(float(change)):
                    change = None
            except (ValueError, TypeError):
                pass
        if change_pct is not None:
            try:
                if math.isnan(float(change_pct)):
                    change_pct = None
            except (ValueError, TypeError):
                pass
    return {
        "value": value,
        "change": change,
        "change_pct": change_pct,
        "name": config["name"],
        "unit": config["unit"],
        "direction": config["direction"],
        "symbol": config["symbol"],
        "data_timestamp": data.get("data_timestamp"),
    }


def _fetch_yahoo_chart(symbol: str, server: str = "query2") -> Optional[Dict]:
    encoded = quote(symbol, safe='')
    url = f"https://{server}.finance.yahoo.com/v8/finance/chart/{encoded}?interval=1d&range=5d"

    data = _curl_get_json(url)
    if data is None:
        data = _requests_get_json(url)

    if data is None:
        return None

    result = data.get("chart", {}).get("result", [None])[0]
    if not result:
        return None

    meta = result.get("meta", {})
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")

    if price is None:
        return None

    returned_sym = meta.get("symbol", "")
    if returned_sym and returned_sym != symbol and returned_sym != encoded:
        return None

    change = price - prev if prev else 0
    change_pct = (change / prev * 100) if prev else 0

    mkt_time = meta.get("regularMarketTime")
    data_ts = None
    if mkt_time and isinstance(mkt_time, (int, float)):
        try:
            data_ts = datetime.fromtimestamp(int(mkt_time), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, OSError):
            pass

    return {
        "price": round(price, 4),
        "change": round(change, 4),
        "change_pct": round(change_pct, 2),
        "data_timestamp": data_ts,
    }


def _curl_get_json(url: str) -> Optional[Dict]:
    try:
        result = subprocess.run(
            ["curl", "-s", "-m", "15", "-H",
             "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
             url],
            capture_output=True, text=True, timeout=20
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return None


def _requests_get_json(url: str) -> Optional[Dict]:
    for attempt in range(2):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            if resp.status_code == 429:
                wait = 3 * (attempt + 1)
                time.sleep(wait)
                continue
            if resp.status_code != 200:
                return None
            return resp.json()
        except (requests.exceptions.RequestException, ValueError):
            if attempt < 1:
                time.sleep(2)
                continue
            return None
    return None


def _fetch_stooq(symbol: str) -> Optional[Dict]:
    try:
        url = f"https://stooq.com/q/l/?s={symbol}&f=sd2t2ohlcv&h&e=csv"
        resp = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        }, timeout=10)

        if resp.status_code != 200:
            return None

        reader = csv.DictReader(StringIO(resp.text))
        for row in reader:
            close_str = row.get("Close", "").strip()
            open_str = row.get("Open", "").strip()
            if not close_str or close_str == "N/D":
                return None

            price = float(close_str)
            prev = float(open_str) if open_str and open_str != "N/D" else price
            change = price - prev
            change_pct = (change / prev * 100) if prev else 0

            return {
                "price": round(price, 4),
                "change": round(change, 4),
                "change_pct": round(change_pct, 2),
            }

    except (requests.exceptions.RequestException, ValueError, KeyError):
        return None

    return None


def _fetch_fred(series_id: str) -> Optional[Dict]:
    api_key = os.environ.get("FRED_API_KEY", "")
    if not api_key:
        return None

    try:
        url = f"https://api.stlouisfed.org/fred/series/observations"
        params = {
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 2,
        }
        resp = requests.get(url, params=params, timeout=15)

        if resp.status_code != 200:
            return None

        data = resp.json()
        observations = data.get("observations", [])
        if len(observations) < 1:
            return None

        latest = observations[0]
        if latest.get("value") == ".":
            return None

        price = float(latest["value"])
        prev = float(observations[1]["value"]) if len(observations) > 1 and observations[1].get("value") != "." else price
        change = price - prev
        change_pct = (change / prev * 100) if prev else 0

        return {
            "price": round(price, 4),
            "change": round(change, 4),
            "change_pct": round(change_pct, 2),
        }

    except (requests.exceptions.RequestException, ValueError, KeyError):
        return None


def _fetch_yahoo_scrape(symbol: str) -> Optional[Dict]:
    encoded = quote(symbol, safe='')
    url = f"https://finance.yahoo.com/quote/{encoded}/"

    try:
        resp = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "text/html",
        }, timeout=15)

        if resp.status_code != 200:
            return None

        pattern = rf'"{re.escape(symbol)}".*?"regularMarketPrice":\{{"raw":([\d.]+)'
        match = re.search(pattern, resp.text)
        if match:
            price = float(match.group(1))
            prev_pattern = rf'"{re.escape(symbol)}".*?"regularMarketPreviousClose":\{{"raw":([\d.]+)'
            prev_match = re.search(prev_pattern, resp.text)
            prev = float(prev_match.group(1)) if prev_match else price
            change = price - prev
            change_pct = (change / prev * 100) if prev else 0
            return {
                "price": round(price, 4),
                "change": round(change, 4),
                "change_pct": round(change_pct, 2),
            }

        return None

    except requests.exceptions.RequestException:
        return None


def get_macro_summary() -> Dict:
    data = fetch_macro_indicators()
    indicators = data.get("indicators", {})

    summary = {
        "timestamp": data.get("timestamp", ""),
        "date": data.get("date", ""),
        "us_10y": None,
        "dxy": None,
        "vix": None,
        "crude_oil": None,
        "gold_impact": "中性",
    }

    for key, target in [("us_10y_yield", "us_10y"), ("dxy", "dxy"), ("vix", "vix"), ("crude_oil", "crude_oil")]:
        if indicators.get(key) and indicators[key].get("value") is not None:
            i = indicators[key]
            summary[target] = {
                "value": i["value"],
                "change": i["change"],
                "change_pct": i["change_pct"],
                "name": i["name"],
            }

    score = 0.0
    if indicators.get("us_10y_yield") and indicators["us_10y_yield"].get("change") is not None:
        chg = indicators["us_10y_yield"]["change"]
        if chg < -0.05:
            score += 0.3
        elif chg > 0.05:
            score -= 0.3

    if indicators.get("dxy") and indicators["dxy"].get("change_pct") is not None:
        pct = indicators["dxy"]["change_pct"]
        if pct < -0.3:
            score += 0.3
        elif pct > 0.3:
            score -= 0.3

    if indicators.get("vix") and indicators["vix"].get("value") is not None:
        val = indicators["vix"]["value"]
        if val > 25:
            score += 0.3
        elif val < 15:
            score -= 0.1

    if score > 0.2:
        summary["gold_impact"] = "偏多"
    elif score < -0.2:
        summary["gold_impact"] = "偏空"

    return summary
