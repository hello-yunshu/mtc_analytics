# -*- coding: utf-8 -*-
"""
数据抓取模块 - 从东方财富获取沪金前5大机构持仓数据
API来源：东方财富期货龙虎榜接口（公开免费，无需登录）
"""

import requests
from datetime import datetime
from typing import List, Dict, Optional


BASE_URL = "https://qhhqzl.eastmoney.com/marketFutuWeb/dragonAndTigerInfo"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
}


def get_latest_trade_date() -> str:
    """获取沪金最新交易日期（格式：YYYYMMDD）"""
    try:
        url = f"{BASE_URL}/getMarketMaxDate"
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") == 10000 and data.get("data"):
            for item in data["data"]:
                if item.get("code", "").upper() == "AU":
                    return item["tradeDay"]
    except Exception as e:
        print(f"[WARN] 获取最新交易日期失败: {e}")
    # 默认返回今天
    return datetime.now().strftime("%Y%m%d")


def fetch_holdings_data(date: Optional[str] = None) -> Dict:
    """
    从东方财富获取沪金持仓排名数据（多空 + 净持仓）
    
    Args:
        date: 交易日期，格式 YYYYMMDD。默认获取最新交易日。
    
    Returns:
        {
            "date": "2026-04-29",
            "trade_date": "20260429",
            "contract": "au",
            "long_top": [{"name": "国泰君安(代客)", "volume": 37978, "change": -533}, ...],
            "short_top": [{"name": "国泰君安(代客)", "volume": 11284, "change": 1029}, ...],
            "net_top": [{"name": "国泰君安(代客)", "net": 26694, "change": -1562}, ...],
            "total_long": 176307,
            "total_short": 51062,
            "total_long_change": -1944,
            "total_short_change": 3365,
        }
    """
    if date is None:
        date = get_latest_trade_date()
    
    # 1. 获取多空持仓排名
    long_top, short_top, totals = _fetch_long_short(date)
    
    # 2. 获取净持仓排名
    net_top = _fetch_net_position(date)
    
    # 格式化日期
    formatted_date = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
    
    return {
        "date": formatted_date,
        "trade_date": date,
        "contract": "au",
        "long_top": long_top,
        "short_top": short_top,
        "net_top": net_top,
        "total_long": totals.get("total_long", 0),
        "total_short": totals.get("total_short", 0),
        "total_long_change": totals.get("total_long_change", 0),
        "total_short_change": totals.get("total_short_change", 0),
    }


def _fetch_long_short(date: str) -> tuple:
    """获取多空持仓排名"""
    url = f"{BASE_URL}/getLongAndShortPosition"
    params = {"date": date, "contract": "au", "market": "113"}
    
    long_top = []
    short_top = []
    totals = {}
    
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        
        if data.get("code") == 10000 and data.get("data"):
            d = data["data"]
            
            totals = {
                "total_long": d.get("totalLongPosition", 0),
                "total_short": d.get("totalShortPosition", 0),
                "total_long_change": d.get("totalLongChange", 0),
                "total_short_change": d.get("totalShortChange", 0),
            }
            
            for item in d.get("longInfoList", []):
                long_top.append({
                    "name": item["futureCompanyName"],
                    "volume": item["longNum"],
                    "change": item["longChange"],
                })
            
            for item in d.get("shortInfoList", []):
                short_top.append({
                    "name": item["futureCompanyName"],
                    "volume": item["shortNum"],
                    "change": item["shortChange"],
                })
    
    except Exception as e:
        print(f"[ERROR] 获取多空持仓失败: {e}")
    
    return long_top, short_top, totals


def _fetch_net_position(date: str) -> List[Dict]:
    """获取净持仓排名"""
    url = f"{BASE_URL}/getLongAndShortNetPosition"
    params = {"date": date, "contract": "au", "market": "113"}
    
    net_top = []
    
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        
        if data.get("code") == 10000 and data.get("data"):
            d = data["data"]
            
            # 净多头列表
            for item in d.get("netLongInfoList", []):
                net_top.append({
                    "name": item["futureCompanyName"],
                    "net": item["netLong"],
                    "change": item["netLongChange"],
                })
            
            # 净空头列表（也加入，用负数标记）
            for item in d.get("netShortInfoList", []):
                net_top.append({
                    "name": item["futureCompanyName"],
                    "net": -item["netShort"],
                    "change": -item["netShortChange"],
                })
    
    except Exception as e:
        print(f"[ERROR] 获取净持仓失败: {e}")
    
    # 按净多头排序
    net_top.sort(key=lambda x: x["net"], reverse=True)
    
    return net_top


def calculate_net_positions(holdings: Dict, top_n: int = 5) -> List[Dict]:
    """
    从多空持仓数据计算各机构的净多头
    
    Returns:
        [{"name": "国泰君安(代客)", "long": 37978, "short": 11284, "net": 26694, "net_change": -1562}, ...]
    """
    # 构建多头和空头字典
    long_dict = {}
    for item in holdings.get("long_top", []):
        long_dict[item["name"]] = item
    
    short_dict = {}
    for item in holdings.get("short_top", []):
        short_dict[item["name"]] = item
    
    # 合并计算净多头
    all_names = set(list(long_dict.keys()) + list(short_dict.keys()))
    net_positions = []
    
    for name in all_names:
        long_vol = long_dict.get(name, {}).get("volume", 0)
        long_chg = long_dict.get(name, {}).get("change", 0)
        short_vol = short_dict.get(name, {}).get("volume", 0)
        short_chg = short_dict.get(name, {}).get("change", 0)
        
        net_positions.append({
            "name": name,
            "long": long_vol,
            "short": short_vol,
            "net": long_vol - short_vol,
            "net_change": long_chg - short_chg,
            "long_change": long_chg,
            "short_change": short_chg,
        })
    
    # 按净多头排序
    net_positions.sort(key=lambda x: x["net"], reverse=True)
    
    return net_positions[:top_n]
