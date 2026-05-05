# -*- coding: utf-8 -*-
"""
历史数据回填模块 - 批量拉取过去N天的持仓、金价、宏观数据
"""

import time
from datetime import datetime, timedelta

from .fetcher import fetch_holdings_data, calculate_net_positions, get_latest_trade_date
from .gold_price import get_daily_history, get_cn_holidays
from .macro_fetcher import fetch_macro_indicators
from . import db


def backfill_history(days: int = 30, top_n: int = 5) -> dict:
    """
    回填过去N天的历史数据（持仓 + 金价 + 宏观）

    Args:
        days: 回填天数（默认30天）
        top_n: 跟踪前N大机构

    Returns:
        {"success": 5, "failed": 1, "skipped": 24, "total_attempted": 30,
         "gold_success": 30, "macro_success": 1}
    """
    existing_dates = set()
    try:
        existing_dates = set(db.get_holdings_dates())
    except Exception:
        pass

    latest_date = get_latest_trade_date()
    latest_dt = datetime.strptime(latest_date, "%Y%m%d")

    cn_holidays = set()
    for y in range(latest_dt.year - 1, latest_dt.year + 2):
        cn_holidays |= get_cn_holidays(y)

    dates_to_fetch = []
    for i in range(days - 1, -1, -1):
        dt = latest_dt - timedelta(days=i)
        if dt.weekday() >= 5:
            continue
        if dt.isoformat() in cn_holidays:
            continue
        date_str = dt.strftime("%Y%m%d")
        formatted = dt.strftime("%Y-%m-%d")
        if formatted not in existing_dates:
            dates_to_fetch.append(date_str)

    print(f"  需要回填 {len(dates_to_fetch)} 天的持仓数据（已有 {len(existing_dates)} 天）")

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

    gold_success = 0
    print(f"\n  正在回填金价历史数据...")
    try:
        gold_prices = get_daily_history(days=days + 10)
        if gold_prices:
            try:
                db.upsert_gold_prices(gold_prices)
                gold_success = len(gold_prices)
                print(f"  金价回填完成: {gold_success} 天")
            except Exception as e:
                print(f"  金价回填写入失败: {e}")
        else:
            print(f"  金价数据获取失败")
    except Exception as e:
        print(f"  金价回填失败: {e}")

    macro_success = 0
    print(f"  正在回填宏观经济数据...")
    try:
        macro_data = fetch_macro_indicators()
        if macro_data and macro_data.get("indicators"):
            try:
                db.insert_macro_snapshot(macro_data)
                macro_success = 1
                ind_count = len(macro_data["indicators"])
                print(f"  宏观数据回填完成: {ind_count} 个指标")
            except Exception as e:
                print(f"  宏观数据写入失败: {e}")
        else:
            print(f"  宏观数据获取失败")
    except Exception as e:
        print(f"  宏观数据回填失败: {e}")

    result = {
        "success": success,
        "failed": failed,
        "skipped": skipped,
        "total_attempted": len(dates_to_fetch),
        "total_history": total_history,
        "gold_success": gold_success,
        "macro_success": macro_success,
    }

    print(f"\n  回填完成！持仓:成功{success} 失败{failed} 跳过{skipped} | 金价:{gold_success}天 | 宏观:{macro_success}次")
    print(f"  历史数据总计: {total_history} 天")

    return result
