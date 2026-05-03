# -*- coding: utf-8 -*-
"""
历史数据回填模块 - 从东方财富API批量拉取过去N天的持仓数据
"""

import time
from datetime import datetime, timedelta

from .fetcher import fetch_holdings_data, calculate_net_positions, get_latest_trade_date
from . import db


def backfill_history(days: int = 30, top_n: int = 5) -> dict:
    """
    回填过去N天的历史数据
    
    Args:
        days: 回填天数（默认30天）
        top_n: 跟踪前N大机构
    
    Returns:
        {"success": 5, "failed": 1, "skipped": 24, "total_attempted": 30}
    """
    existing_dates = set()
    try:
        existing_dates = set(db.get_holdings_dates())
    except Exception:
        pass
    
    latest_date = get_latest_trade_date()
    latest_dt = datetime.strptime(latest_date, "%Y%m%d")
    
    dates_to_fetch = []
    for i in range(days - 1, -1, -1):
        dt = latest_dt - timedelta(days=i)
        if dt.weekday() >= 5:
            continue
        date_str = dt.strftime("%Y%m%d")
        formatted = dt.strftime("%Y-%m-%d")
        if formatted not in existing_dates:
            dates_to_fetch.append(date_str)
    
    print(f"  需要回填 {len(dates_to_fetch)} 天的数据（已有 {len(existing_dates)} 天）")
    
    success = 0
    failed = 0
    skipped = 0
    
    for i, date_str in enumerate(dates_to_fetch):
        formatted = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
        
        try:
            holdings = fetch_holdings_data(date=date_str)
            
            if not holdings.get("long_top") and not holdings.get("short_top"):
                skipped += 1
                print(f"  [{i+1}/{len(dates_to_fetch)}] {formatted} - 非交易日，跳过")
                continue
            
            positions = calculate_net_positions(holdings, top_n=top_n)
            
            try:
                db.upsert_holdings(
                    formatted, positions,
                    trade_date=date_str,
                    contract=holdings.get("contract", ""),
                    total_long=holdings.get("total_long", 0),
                    total_short=holdings.get("total_short", 0),
                )
            except Exception:
                pass
            
            success += 1
            total_net = sum(p["net"] for p in positions)
            total_chg = sum(p["net_change"] for p in positions)
            print(f"  [{i+1}/{len(dates_to_fetch)}] {formatted} - 净多头合计:{total_net:,} 变化:{total_chg:+,}")
            
        except Exception as e:
            failed += 1
            print(f"  [{i+1}/{len(dates_to_fetch)}] {formatted} - 失败: {e}")
        
        time.sleep(0.5)
    
    total_history = len(existing_dates) + success
    result = {
        "success": success,
        "failed": failed,
        "skipped": skipped,
        "total_attempted": len(dates_to_fetch),
        "total_history": total_history,
    }
    
    print(f"\n  回填完成！成功:{success} 失败:{failed} 跳过:{skipped}")
    print(f"  历史数据总计: {total_history} 天")
    
    return result
