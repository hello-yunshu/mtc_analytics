# -*- coding: utf-8 -*-
"""
AI 黄金分析主程序
支持：每日报告 + 实时监控模式
五因子预测：持仓动量、价格趋势、背离信号、波动率、新闻情绪
"""

import sys
import os
import time
import json
import requests
from datetime import datetime, timezone, timedelta

from core.config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TOP_N,
    SCHEDULE_HOUR, SCHEDULE_MINUTE,
    SCHEDULE_HOUR2, SCHEDULE_MINUTE2,
    TELEGRAM_PUSH_HOUR, TELEGRAM_PUSH_MINUTE,
)
from core.fetcher import fetch_holdings_data, calculate_net_positions
from core.analyzer import HoldingsAnalyzer
from core.trend_analyzer import TrendAnalyzer
from core.alert_engine import AlertEngine
from core.gold_price import get_realtime_price, get_daily_history, archive_realtime_price, get_price_summary
from core.news_sentiment import fetch_news_sentiment
from core.predictor import GoldPricePredictor
from core.reporter import format_report, format_alert_only
from core.telegram_bot import TelegramBot
from core.macro_fetcher import fetch_macro_indicators
from core.model_iteration import run_iteration, get_iteration_status
from core.institutional_consensus import fetch_institutional_consensus, compare_with_consensus, compute_consensus_with_manual, get_manual_views
from core.utils import load_json


def _get_web_settings():
    return load_json("data/web_settings.json") or {}


def _get_schedule_time():
    ws = _get_web_settings()
    hour = ws.get("schedule_hour", SCHEDULE_HOUR)
    minute = ws.get("schedule_minute", SCHEDULE_MINUTE)
    return hour, minute


def _get_schedule_time2():
    ws = _get_web_settings()
    hour = ws.get("schedule_hour2", SCHEDULE_HOUR2)
    minute = ws.get("schedule_minute2", SCHEDULE_MINUTE2)
    return hour, minute


def _get_telegram_push_time():
    ws = _get_web_settings()
    hour = ws.get("telegram_push_hour", TELEGRAM_PUSH_HOUR)
    minute = ws.get("telegram_push_minute", TELEGRAM_PUSH_MINUTE)
    return hour, minute


def push_latest_report():
    """读取最新报告并推送到 Telegram"""
    from core import db
    try:
        dates = db.get_report_dates(days=30)
        if not dates:
            print("[Telegram 推送] 未找到任何报告，跳过推送")
            return
        date_str = dates[0]
        report = db.get_report(date_str)
        if not report:
            print("[Telegram 推送] 报告内容为空，跳过推送")
            return
    except Exception as e:
        print(f"[Telegram 推送] 读取报告失败: {e}")
        return
    print(f"[Telegram 推送] 推送最新报告: {date_str}")
    tg_token, tg_chat_id = _get_telegram_config()
    if not tg_token or tg_token == "YOUR_BOT_TOKEN_HERE":
        print("[Telegram 推送] 未配置 Bot Token，跳过")
        return
    bot = TelegramBot(tg_token, tg_chat_id)
    if bot.send_message(report):
        print("[Telegram 推送] 推送成功")
    else:
        print("[Telegram 推送] 推送失败")


def _get_top_n():
    ws = _get_web_settings()
    return ws.get("top_n", TOP_N)


def _get_realtime_intervals():
    ws = _get_web_settings()
    trading = ws.get("realtime_interval_trading", 30)
    nontrading = ws.get("realtime_interval_nontrading", 240)
    return trading, nontrading


def _get_telegram_config():
    ws = _get_web_settings()
    token = ws.get("telegram_bot_token", TELEGRAM_BOT_TOKEN)
    chat_id = ws.get("telegram_chat_id", TELEGRAM_CHAT_ID)
    return token, chat_id


from core.db import (
    upsert_gold_prices, upsert_holdings, insert_macro_snapshot,
    upsert_news_sentiment, insert_support_resistance, upsert_report,
    upsert_prediction_tracking, get_unverified_predictions,
    update_prediction_verification, get_all_prediction_tracking, cleanup,
)


def _save_prediction_tracking(today_data, prediction, gold_prices, consensus_data=None):
    current_price = gold_prices[-1]["close"] if gold_prices else 0
    date_str = today_data.get("date", "")
    record = {
        "date": date_str,
        "prediction": prediction.get("direction", ""),
        "confidence": prediction.get("confidence", 0),
        "score": prediction.get("score", 0),
        "price_at_prediction": current_price,
        "factors_summary": {
            k: {"score": v.get("score", 0), "signal": v.get("signal", "")}
            for k, v in prediction.get("factors", {}).items()
        },
        "llm_reasoning": prediction.get("llm_reasoning", ""),
        "period_trends": prediction.get("period_trends", {}),
        "institutional_consensus": consensus_data or {},
        "consensus_alignment": prediction.get("consensus_alignment", {}),
    }
    try:
        upsert_prediction_tracking(date_str, record)
    except Exception:
        pass


def _verify_previous_prediction(gold_prices):
    if not gold_prices or len(gold_prices) < 2:
        return
    try:
        tracking = get_unverified_predictions()
    except Exception:
        return
    if not tracking:
        return
    prices_by_date = {}
    for p in gold_prices:
        d = p.get("date", "")
        if d:
            prices_by_date[d] = p.get("close", 0)
    sorted_dates = sorted(prices_by_date.keys())

    all_tracking = []
    try:
        all_tracking = get_all_prediction_tracking(days=365)
    except Exception:
        pass

    for record in tracking:
        pred_dir = record.get("prediction", "")
        if pred_dir == "中性":
            continue
        pred_date = record.get("date", "")
        pred_price = record.get("price_at_prediction", 0)
        if not pred_price or not pred_date:
            continue

        verified_data = {}
        verified_periods = []

        next_date = None
        for d in sorted_dates:
            if d > pred_date:
                next_date = d
                break

        if next_date and next_date in prices_by_date:
            next_price = prices_by_date[next_date]
            price_change_pct = (next_price - pred_price) / pred_price * 100 if pred_price > 0 else 0
        else:
            current_price = gold_prices[-1]["close"]
            price_change_pct = (current_price - pred_price) / pred_price * 100 if pred_price > 0 else 0

        if abs(price_change_pct) > 0.3:
            actual_dir = "看多" if price_change_pct > 0 else "看空"
            verify_date = next_date if next_date and next_date in prices_by_date else gold_prices[-1].get("date", "")
            verified_data["actual_direction"] = actual_dir
            verified_data["actual_change_pct"] = round(price_change_pct, 2)
            verified_data["verified_date"] = verify_date
            verified_periods.append("1d")

        period_checks = [
            ("5d", 5, "actual_direction_5d", "actual_change_pct_5d"),
            ("10d", 10, "actual_direction_10d", "actual_change_pct_10d"),
            ("20d", 20, "actual_direction_20d", "actual_change_pct_20d"),
        ]

        for period_name, days, dir_col, pct_col in period_checks:
            target_dates = [d for d in sorted_dates if d > pred_date]
            if len(target_dates) < days:
                continue
            target_date = target_dates[days - 1]
            if target_date in prices_by_date:
                target_price = prices_by_date[target_date]
                change_pct = (target_price - pred_price) / pred_price * 100 if pred_price > 0 else 0
                if abs(change_pct) > 0.3:
                    period_dir = "看多" if change_pct > 0 else "看空"
                    verified_data[dir_col] = period_dir
                    verified_data[pct_col] = round(change_pct, 2)
                    verified_periods.append(period_name)

        if verified_periods:
            verified_data["verified_periods"] = json.dumps(verified_periods)

        if verified_data.get("actual_direction"):
            try:
                update_prediction_verification(pred_date, verified_data)
            except Exception:
                pass


def run_daily_task(skip_telegram=False):
    """执行每日完整任务（持仓+金价+新闻+预测）"""
    print(f"\n{'='*50}")
    print(f"  AI 黄金分析 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}\n")
    
    # 1. 获取实时金价并归档
    print("[1/8] 正在获取实时金价...")
    data_freshness = {}
    realtime = get_realtime_price()
    archive_realtime_price(realtime)
    price_summary = get_price_summary(realtime)
    if price_summary.get("available"):
        ps = price_summary
        print(f"  现货黄金: {ps['price']:.2f} USD/oz ({ps['change_pct']:+.2f}%) 来源:{ps['source']}")
        if ps.get("intraday_range"):
            print(f"  日内波幅: {ps['intraday_range']:.2f} USD")
        price_ts = ps.get("timestamp", "")
        if price_ts:
            try:
                pt = datetime.fromisoformat(price_ts.replace("Z", "+00:00"))
                if pt.tzinfo is not None:
                    pt = pt.astimezone(timezone.utc).replace(tzinfo=None)
                age_minutes = (datetime.now(timezone.utc) - pt).total_seconds() / 60
                ts_local = (pt + timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M:%S")
                if age_minutes < 30:
                    data_freshness["金价"] = ("✅", f"实时|ts={ts_local}")
                elif age_minutes < 120:
                    data_freshness["金价"] = ("⚠️", f"延迟{age_minutes:.0f}分钟|ts={ts_local}")
                else:
                    data_freshness["金价"] = ("❌", f"严重延迟{age_minutes/60:.1f}小时|ts={ts_local}")
            except Exception:
                data_freshness["金价"] = ("⚪", "时间戳未知")
        else:
            data_freshness["金价"] = ("⚪", "时间戳未知")

    # 2. 获取宏观指标
    print("[2/8] 正在获取宏观指标...")
    macro_data = fetch_macro_indicators()
    if macro_data and macro_data.get("indicators"):
        try:
            insert_macro_snapshot(macro_data["indicators"], macro_data.get("timestamp"))
        except Exception:
            pass
        macro_ts = macro_data.get("timestamp", "")
        if macro_ts:
            try:
                mt = datetime.fromisoformat(macro_ts.replace("Z", "+00:00"))
                if mt.tzinfo is not None:
                    mt = mt.astimezone(timezone.utc).replace(tzinfo=None)
                age_hours = (datetime.now(timezone.utc) - mt).total_seconds() / 3600
                ts_local = (mt + timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M:%S")
                if age_hours < 4:
                    data_freshness["宏观"] = ("✅", f"最新|ts={ts_local}")
                elif age_hours < 12:
                    data_freshness["宏观"] = ("⚠️", f"延迟{age_hours:.1f}小时|ts={ts_local}")
                else:
                    data_freshness["宏观"] = ("❌", f"严重延迟{age_hours:.0f}小时|ts={ts_local}")
            except Exception:
                data_freshness["宏观"] = ("⚪", "时间戳未知")
        else:
            data_freshness["宏观"] = ("⚪", "时间戳未知")

    # 3. 获取持仓数据
    print("[3/8] 正在获取持仓数据...")
    holdings = fetch_holdings_data()
    
    if not holdings.get("long_top") and not holdings.get("short_top"):
        print("[ERROR] 未能获取到持仓数据，可能非交易日或数据源异常")
        if not skip_telegram:
            tg_token, tg_chat_id = _get_telegram_config()
            if tg_token and tg_token != "YOUR_BOT_TOKEN_HERE":
                bot = TelegramBot(tg_token, tg_chat_id)
                bot.send_message(
                    f"⚠️ AI 黄金分析 | {datetime.now().strftime('%Y-%m-%d')}\n\n"
                    "今日未能获取到持仓数据，可能非交易日或数据源异常。"
                )
        return False
    
    print(f"  合约: {holdings['contract']}  多头: {len(holdings['long_top'])}条  空头: {len(holdings['short_top'])}条")

    holdings_date = holdings.get("date", "")
    if holdings_date:
        try:
            hd = datetime.strptime(holdings_date, "%Y-%m-%d")
            age_days = (datetime.now() - hd).days
            if age_days <= 1:
                data_freshness["持仓"] = ("✅", f"最新|ts={holdings_date}T00:00:00")
            elif age_days <= 3:
                data_freshness["持仓"] = ("⚠️", f"延迟{age_days}天|ts={holdings_date}T00:00:00")
            else:
                data_freshness["持仓"] = ("❌", f"严重延迟{age_days}天|ts={holdings_date}T00:00:00")
        except Exception:
            data_freshness["持仓"] = ("⚪", "日期未知")
    else:
        data_freshness["持仓"] = ("⚪", "日期未知")
    
    # 3. 计算净多头
    print("[4/8] 正在计算净多头...")
    positions = calculate_net_positions(holdings, top_n=_get_top_n())
    
    today_data = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "holdings_date": holdings["date"],
        "trade_date": holdings.get("trade_date", ""),
        "contract": holdings["contract"],
        "positions": positions,
        "total_long": holdings.get("total_long", 0),
        "total_short": holdings.get("total_short", 0),
    }
    
    for pos in positions:
        chg = pos['net_change']
        print(f"  {pos['name']:<12} 净多头: {pos['net']:>8,} 手  变化: {'+' if chg>0 else ''}{chg:>6,} 手")
    
    # 4. 短期警示分析
    print("[5/8] 正在分析短期警示...")
    analyzer = HoldingsAnalyzer()
    analyzer.add_today_data(today_data)
    try:
        upsert_holdings(holdings["date"], positions,
                        trade_date=holdings.get("trade_date", ""),
                        contract=holdings.get("contract", ""),
                        total_long=holdings.get("total_long", 0),
                        total_short=holdings.get("total_short", 0))
    except Exception:
        pass
    alerts = analyzer.generate_alerts(today_data)
    stats = analyzer.get_summary_stats(today_data)
    if alerts:
        print(f"  短期警示: {len(alerts)} 个")
    
    # 5. 长期趋势分析
    print("[6/8] 正在分析长期趋势...")
    trend_data = None
    if len(analyzer.history) > 1:
        trend_analyzer = TrendAnalyzer(analyzer.history)
        trend_data = trend_analyzer.analyze_long_term(today_data)
        print(f"  历史数据: {trend_data['history_days']} 个交易日")
    
    # 6. 新闻情绪分析
    print("[7/9] 正在分析新闻情绪...")
    news_sentiment = None
    try:
        news_sentiment = fetch_news_sentiment()
        if news_sentiment:
            try:
                upsert_news_sentiment(holdings["date"], news_sentiment)
            except Exception:
                pass
            news_ts = news_sentiment.get("timestamp", "")
            if news_ts:
                try:
                    nt = datetime.fromisoformat(news_ts.replace("Z", "+00:00").replace("+08:00", ""))
                    if nt.tzinfo is not None:
                        nt = nt.astimezone(timezone.utc).replace(tzinfo=None)
                    age_hours = (datetime.now(timezone.utc) - nt).total_seconds() / 3600
                    ts_local = (nt + timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M:%S")
                    if age_hours < 2:
                        data_freshness["新闻"] = ("✅", f"最新|ts={ts_local}")
                    elif age_hours < 6:
                        data_freshness["新闻"] = ("⚠️", f"延迟{age_hours:.1f}小时|ts={ts_local}")
                    else:
                        data_freshness["新闻"] = ("❌", f"严重延迟{age_hours:.0f}小时|ts={ts_local}")
                except Exception:
                    data_freshness["新闻"] = ("⚪", "时间戳未知")
            else:
                data_freshness["新闻"] = ("⚪", "时间戳未知")
    except (requests.exceptions.RequestException, ValueError) as e:
        print(f"  [WARN] 新闻分析失败: {e}")
        data_freshness["新闻"] = ("❌", "获取失败")

    # 6.5 机构共识
    print("[8/9] 正在获取机构共识...")
    consensus_data = None
    consensus_comparison = None
    try:
        auto_consensus = fetch_institutional_consensus(news_sentiment)
        manual_views = get_manual_views()
        if manual_views:
            all_views, consensus = compute_consensus_with_manual(
                auto_consensus.get("institutions", []), manual_views
            )
            consensus_data = {
                "institutions": all_views,
                "consensus": consensus,
                "timestamp": auto_consensus.get("timestamp", ""),
                "source": auto_consensus.get("source", "") + "+manual",
            }
        else:
            consensus_data = auto_consensus

        inst_count = len(consensus_data.get("institutions", []))
        cons_dir = consensus_data.get("consensus", {}).get("direction", "无数据")
        print(f"  机构观点: {inst_count}家, 共识方向: {cons_dir}")
    except Exception as e:
        print(f"  [WARN] 机构共识获取失败: {e}")

    # 7. 智能预测
    print("[9/9] 正在运行智能预测...")
    prediction = None
    gold_prices = []
    try:
        gold_prices = get_daily_history(days=30) or []
        if gold_prices:
            try:
                upsert_gold_prices(gold_prices)
            except Exception:
                pass
        if gold_prices and len(analyzer.history) >= 3:
            _verify_previous_prediction(gold_prices)
            iteration_result = run_iteration()
            predictor = GoldPricePredictor(analyzer.history, gold_prices, news_sentiment, macro_data)
            prediction = predictor.predict(today_data)
            prediction["news_sentiment"] = news_sentiment
            if iteration_result.get("status") == "adjusted":
                prediction["iteration_result"] = iteration_result

            if consensus_data and consensus_data.get("consensus"):
                consensus_comparison = compare_with_consensus(
                    prediction["direction"], consensus_data["consensus"]
                )
                prediction["consensus_alignment"] = consensus_comparison
                conf_adj = consensus_comparison.get("confidence_adjustment", 0)
                if conf_adj != 0:
                    prediction["confidence"] = max(20, min(98, prediction["confidence"] + conf_adj))

            print(f"  预测: {prediction['direction']}（置信度{prediction['confidence']}%，评分{prediction['score']:+.2f}）")
            if consensus_comparison:
                print(f"  机构共识: {consensus_comparison.get('description', '')}")
            _save_prediction_tracking(today_data, prediction, gold_prices, consensus_data)
        else:
            print("  数据不足，跳过预测")
    except Exception as e:
        print(f"  [WARN] 预测失败: {e}")

    # 8. 全维度警示引擎
    print("\n正在运行全维度警示引擎...")
    full_alerts = []
    try:
        support_resistance = None
        if gold_prices and len(gold_prices) >= 2:
            try:
                closes = [p["close"] for p in gold_prices]
                highs = [p.get("high", p["close"]) for p in gold_prices]
                lows = [p.get("low", p["close"]) for p in gold_prices]
                h, l, c = highs[-1], lows[-1], closes[-1]
                pivot = (h + l + c) / 3
                support_resistance = {
                    "current": c,
                    "resistance": [
                        {"level": "R1", "value": round(2 * pivot - l, 2)},
                        {"level": "R2", "value": round(pivot + (h - l), 2)},
                    ],
                    "support": [
                        {"level": "S1", "value": round(2 * pivot - h, 2)},
                        {"level": "S2", "value": round(pivot - (h - l), 2)},
                    ],
                }
                try:
                    insert_support_resistance({
                        "timestamp": datetime.now().isoformat(),
                        **support_resistance,
                    })
                except Exception:
                    pass
            except Exception:
                pass

        _ws = _get_web_settings()
        alert_engine = AlertEngine(
            holdings_history=analyzer.history,
            gold_prices=gold_prices or [],
            macro_data=macro_data,
            news_sentiment=news_sentiment,
            prediction=prediction,
            support_resistance=support_resistance,
            enabled_dimensions=_ws.get("alert_dimensions"),
            alert_threshold_large=_ws.get("alert_threshold_large", 1000),
        )
        full_alerts = alert_engine.generate_all_alerts(today_data)
        if full_alerts:
            dim_counts = {}
            for a in full_alerts:
                d = a.get("dimension", "unknown")
                dim_counts[d] = dim_counts.get(d, 0) + 1
            dim_str = " ".join(f"{k}({v})" for k, v in dim_counts.items())
            print(f"  全维度警示: {len(full_alerts)}个 [{dim_str}]")
    except Exception as e:
        print(f"  [WARN] 全维度警示引擎失败: {e}")
        full_alerts = alerts
    
    # 生成并发送报告
    print("\n正在生成报告...")
    
    # 在报告头部添加生成时金价
    report = ""
    if price_summary and price_summary.get("available"):
        ps = price_summary
        report += f"💰 生成时金价: {ps['price']:.2f} USD/oz ({ps['change_pct']:+.2f}%)"
        if ps.get("high") and ps.get("low"):
            report += f" | 高:{ps['high']:.2f} 低:{ps['low']:.2f}"
        if ps.get("intraday_range"):
            report += f" | 波幅:{ps['intraday_range']:.2f}"
        report += f"\n   来源: {ps.get('source', 'N/A')} | {ps.get('timestamp', '')}\n\n"
    
    report += format_report(
        datetime.now().strftime("%Y-%m-%d"), holdings["contract"],
        positions, stats, alerts, trend_data, prediction,
        news_sentiment=news_sentiment,
        full_alerts=full_alerts,
        data_freshness=data_freshness,
        holdings_date=holdings["date"]
    )
    
    tg_token, tg_chat_id = _get_telegram_config()
    if skip_telegram:
        print("  跳过 Telegram 推送（仅生成报告）")
    elif not tg_token or tg_token == "YOUR_BOT_TOKEN_HERE":
        print(f"\n{'='*50}")
        print(report)
        print(f"{'='*50}")
    else:
        bot = TelegramBot(tg_token, tg_chat_id)
        bot.send_message(report)
        
        # 高级别警示额外推送
        all_high = [a for a in full_alerts if a["level"] in ("high", "critical")]
        if trend_data:
            all_high.extend([a for a in trend_data.get("signals", []) if a["level"] in ("high", "critical")])
        if all_high:
            alert_msg = format_alert_only(all_high, datetime.now().strftime("%Y-%m-%d"))
            bot.send_message(alert_msg)
        
        print("  报告已发送到 Telegram")
    
    # 保存到本地
    report_date = datetime.now().strftime("%Y-%m-%d")
    report_file = os.path.join("data", "reports", f"report_{report_date}.txt")
    os.makedirs(os.path.join("data", "reports"), exist_ok=True)
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(report)
    try:
        upsert_report(report_date, report)
    except Exception:
        pass
    try:
        cleanup()
    except Exception:
        pass
    print(f"  报告已保存到: {report_file}")
    
    return True


def run_realtime():
    """
    实时监控模式
    交易时段（北京时间 09:00-15:00 / 21:00-03:00）每30分钟执行一次
    非交易时段每4小时执行一次
    """
    print(f"🚀 实时监控模式已启动 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  交易时段: 每30分钟执行一次")
    print(f"  非交易时段: 每4小时执行一次")
    print(f"  按 Ctrl+C 停止\n")
    
    last_run = 0
    
    while True:
        now = datetime.now()
        now_utc = datetime.now(timezone.utc)
        hour_utc = now_utc.hour
        minute_utc = now_utc.minute
        weekday_utc = now_utc.weekday()

        is_trading = False
        if weekday_utc < 5:
            t = hour_utc * 60 + minute_utc
            if 30 <= t < 570:
                is_trading = True
            elif 740 <= t < 1050:
                is_trading = True
        
        # 计算间隔
        rt_trading, rt_nontrading = _get_realtime_intervals()
        if is_trading:
            interval = rt_trading * 60
        else:
            interval = rt_nontrading * 60
        
        elapsed = time.time() - last_run
        
        if elapsed >= interval:
            print(f"\n{'='*50}")
            mode = "交易时段" if is_trading else "非交易时段"
            print(f"  [{mode}] {now.strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"{'='*50}")
            
            try:
                run_daily_task()
            except Exception as e:
                print(f"[ERROR] 执行失败: {e}")
            
            last_run = time.time()
        
        time.sleep(60)  # 每分钟检查一次


def do_backfill(days: int = 30):
    """回填历史数据"""
    print(f"\n{'='*50}")
    print(f"  历史数据回填 | 回填 {days} 天")
    print(f"{'='*50}\n")
    
    from core.backfill import backfill_history
    result = backfill_history(days=days, top_n=_get_top_n())
    
    print(f"\n回填结果: 成功{result['success']} 失败{result['failed']} 跳过{result['skipped']}")
    print(f"历史数据总计: {result['total_history']} 天")
    
    if result['success'] > 0:
        print("\n正在基于历史数据生成分析报告...")
        run_daily_task()


def test_bot():
    """测试 Telegram Bot"""
    tg_token, tg_chat_id = _get_telegram_config()
    if not tg_token or tg_token == "YOUR_BOT_TOKEN_HERE":
        print("请先在系统设置中配置 Telegram Bot Token")
        return
    
    bot = TelegramBot(tg_token, tg_chat_id)
    print("正在测试 Bot 连接...")
    if bot.test_connection():
        print("正在发送测试消息...")
        bot.send_message(
            "✅ AI 黄金分析 Bot 连接成功！\n\n"
            "五因子预测模型：持仓动量 + 价格趋势 + 背离信号 + 波动率 + 新闻情绪\n"
            "支持：每日报告 + 实时监控模式"
        )
        print("测试完成！")
    else:
        print("Bot 连接失败，请检查 Token 和 Chat ID")


def test_fetch():
    """测试数据获取"""
    print("正在测试数据获取...")
    
    # 测试实时金价
    print("\n--- 实时金价 ---")
    rt = get_realtime_price()
    if rt:
        print(f"  现货黄金: {rt['price']:.2f} USD/oz ({rt['change_pct']:+.2f}%)")
    
    # 测试持仓
    print("\n--- 持仓数据 ---")
    holdings = fetch_holdings_data()
    print(f"  日期: {holdings['date']}  合约: {holdings['contract']}")
    
    if holdings.get("long_top"):
        positions = calculate_net_positions(holdings, top_n=_get_top_n())
        for pos in positions:
            chg = pos['net_change']
            print(f"  {pos['name']:<12} 净多头: {pos['net']:>8,} 手  变化: {'+' if chg>0 else ''}{chg:>6,} 手")
    
    # 测试新闻
    print("\n--- 新闻情绪 ---")
    try:
        news = fetch_news_sentiment()
        print(f"  情绪: {news['sentiment']} ({news['sentiment_score']:+.2f})")
        print(f"  利多: {news['bullish_count']}  利空: {news['bearish_count']}  中性: {news['neutral_count']}")
        if news.get("key_events"):
            for e in news["key_events"][:3]:
                print(f"  • {e}")
    except Exception as e:
        print(f"  新闻获取失败: {e}")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="AI 黄金分析系统（五因子预测）")
    parser.add_argument("--run", action="store_true", help="执行每日任务")
    parser.add_argument("--realtime", action="store_true", help="启动实时监控模式")
    parser.add_argument("--backfill", type=int, nargs="?", const=30, help="回填历史数据")
    parser.add_argument("--test-bot", action="store_true", help="测试 Telegram Bot")
    parser.add_argument("--test-fetch", action="store_true", help="测试所有数据获取")
    parser.add_argument("--schedule", action="store_true", help="启动每日定时任务")
    
    args = parser.parse_args()
    
    if args.test_bot:
        test_bot()
    elif args.test_fetch:
        test_fetch()
    elif args.backfill:
        do_backfill(days=args.backfill)
    elif args.realtime:
        run_realtime()
    elif args.schedule:
        import schedule

        sch_hour, sch_min = _get_schedule_time()
        sch_hour2, sch_min2 = _get_schedule_time2()
        tg_hour, tg_min = _get_telegram_push_time()
        print(f"AI 黄金分析定时任务已启动")
        print(f"  每天 {sch_hour:02d}:{sch_min:02d} 生成报告")
        print(f"  每天 {sch_hour2:02d}:{sch_min2:02d} 生成报告")
        print(f"  每天 {tg_hour:02d}:{tg_min:02d} Telegram 推送最新报告")
        print(f"  按 Ctrl+C 停止\n")
        
        schedule.every().day.at(f"{sch_hour:02d}:{sch_min:02d}").do(lambda: run_daily_task(skip_telegram=True))
        schedule.every().day.at(f"{sch_hour2:02d}:{sch_min2:02d}").do(lambda: run_daily_task(skip_telegram=True))
        schedule.every().day.at(f"{tg_hour:02d}:{tg_min:02d}").do(push_latest_report)
        run_daily_task(skip_telegram=True)
        
        while True:
            schedule.run_pending()
            time.sleep(60)
    elif args.run:
        run_daily_task()
    else:
        parser.print_help()
        print("\n快速开始:")
        print("  python main.py --backfill       # 回填30天历史数据（推荐首次运行）")
        print("  python main.py --test-fetch     # 测试所有数据获取")
        print("  python main.py --test-bot       # 测试 Telegram Bot")
        print("  python main.py --run            # 手动执行一次完整报告")
        print("  python main.py --schedule       # 每日定时执行")
        print("  python main.py --realtime       # 实时监控模式（推荐）")
