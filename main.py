# -*- coding: utf-8 -*-
"""
AI 黄金分析主程序
支持：每日报告 + 实时监控模式
五因子预测：持仓动量、价格趋势、背离信号、波动率、新闻情绪
"""

import os
import time
import json
import logging
import requests
from datetime import datetime, timezone, timedelta

from core.config import (
    TOP_N,
    SCHEDULE_HOUR, SCHEDULE_MINUTE,
    SCHEDULE_HOUR2, SCHEDULE_MINUTE2,
    TELEGRAM_PUSH_HOUR, TELEGRAM_PUSH_MINUTE,
    get_telegram_config,
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
from core.model_iteration import run_iteration
from core.institutional_consensus import fetch_institutional_consensus, compare_with_consensus, compute_consensus_with_manual, get_manual_views
from core.utils import load_json, is_trading_hours, decrypt_value

_logger = logging.getLogger(__name__)


_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.join(_PROJECT_ROOT, "data")
_LOCAL_TZ = timezone(timedelta(hours=8))


def _data_path(*parts):
    return os.path.join(_DATA_DIR, *parts)


def _get_web_settings():
    return load_json(_data_path("web_settings.json")) or {}


def _decrypt_web_setting(ciphertext: str) -> str:
    key_data = load_json(_data_path(".secret_key")) or {}
    return decrypt_value(ciphertext, key_data.get("secret_key", ""))


def _get_telegram_settings():
    return get_telegram_config(_get_web_settings(), _decrypt_web_setting)


def _parse_timestamp(value: str, default_tz=timezone.utc):
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        dt = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                dt = datetime.strptime(raw, fmt)
                break
            except ValueError:
                continue
        if dt is None:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=default_tz)
    return dt.astimezone(timezone.utc)


def _timestamp_age_minutes(dt_utc) -> float:
    if dt_utc is None:
        return 0.0
    return max(0.0, (datetime.now(timezone.utc) - dt_utc).total_seconds() / 60)


def _format_local_timestamp(dt_utc) -> str:
    return dt_utc.astimezone(_LOCAL_TZ).strftime("%Y-%m-%dT%H:%M:%S")


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
    from core import db
    try:
        records = db.get_report_dates_by_gen(days=30)
        if not records:
            _logger.info("[Telegram 推送] 未找到任何报告，跳过推送")
            return
        date_str = records[0]["data_date"]
        report = db.get_report(date_str)
        if not report:
            _logger.info("[Telegram 推送] 报告内容为空，跳过推送")
            return
    except Exception as e:
        _logger.error("[Telegram 推送] 读取报告失败: %s", e)
        return
    _logger.info("[Telegram 推送] 推送最新报告: %s", date_str)
    tg_token, tg_chat_id = _get_telegram_settings()
    if not tg_token or tg_token == "YOUR_BOT_TOKEN_HERE":
        _logger.info("[Telegram 推送] 未配置 Bot Token，跳过")
        return
    bot = TelegramBot(tg_token, tg_chat_id)
    if bot.send_message(report):
        _logger.info("[Telegram 推送] 推送成功")
    else:
        _logger.warning("[Telegram 推送] 推送失败")


def _get_top_n():
    ws = _get_web_settings()
    return ws.get("top_n", TOP_N)


def _get_realtime_intervals():
    ws = _get_web_settings()
    trading = ws.get("realtime_interval_trading", 30)
    nontrading = ws.get("realtime_interval_nontrading", 240)
    return trading, nontrading


from core.db import (
    upsert_gold_prices, upsert_holdings, insert_macro_snapshot,
    upsert_news_sentiment, insert_support_resistance, insert_report,
    upsert_prediction_tracking, get_unverified_predictions,
    update_prediction_verification, cleanup,
)


def _save_prediction_tracking(today_data, prediction, gold_prices, consensus_data=None, report_date=None):
    current_price = gold_prices[-1]["close"] if gold_prices else 0
    date_str = report_date or today_data.get("date", "")
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

    verified_count = 0
    skipped_count = 0
    for record in tracking:
        pred_dir = record.get("prediction", "")
        pred_date = record.get("date", "")
        pred_price = record.get("price_at_prediction", 0)
        if not pred_price or not pred_date:
            skipped_count += 1
            continue

        if pred_date in prices_by_date and prices_by_date[pred_date] > 0:
            base_price = prices_by_date[pred_date]
        else:
            base_price = pred_price

        verified_data = {}
        verified_periods = []
        latest_verified_date = ""

        next_date = None
        for d in sorted_dates:
            if d > pred_date:
                next_date = d
                break

        if next_date and next_date in prices_by_date:
            next_price = prices_by_date[next_date]
            price_change_pct = (next_price - base_price) / base_price * 100 if base_price > 0 else 0
            latest_verified_date = next_date
        else:
            current_price = gold_prices[-1]["close"]
            price_change_pct = (current_price - base_price) / base_price * 100 if base_price > 0 else 0
            latest_verified_date = gold_prices[-1].get("date", "")

        if abs(price_change_pct) > 0.15:
            actual_dir = "看多" if price_change_pct > 0 else "看空"
            verified_data["actual_direction"] = actual_dir
            verified_data["actual_change_pct"] = round(price_change_pct, 2)
            verified_periods.append("1d")
        elif next_date and next_date in prices_by_date:
            verified_data["actual_direction"] = "中性（不参与准确率统计）"
            verified_data["actual_change_pct"] = round(price_change_pct, 2)
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
                change_pct = (target_price - base_price) / base_price * 100 if base_price > 0 else 0
                latest_verified_date = target_date
                if abs(change_pct) > 0.15:
                    period_dir = "看多" if change_pct > 0 else "看空"
                    verified_data[dir_col] = period_dir
                    verified_data[pct_col] = round(change_pct, 2)
                    verified_periods.append(period_name)
                else:
                    verified_data[dir_col] = "中性"
                    verified_data[pct_col] = round(change_pct, 2)
                    verified_periods.append(period_name)

        if verified_periods:
            verified_data["verified_periods"] = json.dumps(verified_periods)
            verified_data["verified_date"] = latest_verified_date or gold_prices[-1].get("date", "")

        if verified_periods:
            try:
                update_prediction_verification(pred_date, verified_data)
                verified_count += 1
            except Exception:
                pass

    if tracking:
        _logger.info("  预测验证: 共%d条待验证, 成功%d条, 跳过%d条(无价格/日期)", len(tracking), verified_count, skipped_count)


def _has_today_report():
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        report = db.get_report(today)
        return bool(report)
    except Exception:
        return False


def run_daily_task(skip_telegram=False):
    """执行每日完整任务（持仓+金价+新闻+预测）"""
    from core.gold_price import get_market_status, get_sge_market_status
    comex_status = get_market_status()
    sge_status = get_sge_market_status()
    comex_closed = comex_status.get("status") != "open"
    sge_closed = sge_status.get("status") != "open"
    sge_holiday = sge_closed and sge_status.get("reason", "") != "非交易时段"

    _logger.info("")
    _logger.info("  AI 黄金分析 | %s", datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    if comex_closed:
        _logger.info("  COMEX: %s", comex_status.get('reason', '休市'))
    if sge_closed:
        _logger.info("  SGE: %s", sge_status.get('reason', '休市'))
    
    # 1. 获取实时金价并归档
    _logger.info("[1/9] 正在获取实时金价...")
    data_freshness = {}
    realtime = get_realtime_price()
    archive_realtime_price(realtime)
    price_summary = get_price_summary(realtime)
    if price_summary.get("available"):
        ps = price_summary
        ms = ps.get("market_status", {})
        market_note = ""
        if ms.get("status") == "closed":
            market_note = f" ⏸休市({ms.get('reason', '')})"
        _logger.info("  现货黄金: %.2f USD/oz (%+.2f%%) 来源:%s%s", ps['price'], ps['change_pct'], ps['source'], market_note)
        if ps.get("intraday_range"):
            _logger.info("  日内波幅: %.2f USD", ps['intraday_range'])
        price_ts = ps.get("timestamp", "")
        if price_ts:
            try:
                pt = _parse_timestamp(price_ts, timezone.utc)
                if pt is None:
                    raise ValueError("invalid price timestamp")
                age_minutes = _timestamp_age_minutes(pt)
                ts_local = _format_local_timestamp(pt)
                if ms.get("status") == "closed":
                    data_freshness["金价"] = ("⏸", f"休市({ms.get('reason', '')})|ts={ts_local}")
                elif age_minutes < 30:
                    data_freshness["金价"] = ("✅", f"实时|ts={ts_local}")
                elif age_minutes < 120:
                    data_freshness["金价"] = ("⚠️", f"延迟{age_minutes:.0f}分钟|ts={ts_local}")
                else:
                    data_freshness["金价"] = ("❌", f"严重延迟{age_minutes/60:.1f}小时|ts={ts_local}")
            except Exception:
                data_freshness["金价"] = ("⚪", "时间戳未知")
        else:
            if ms.get("status") == "closed":
                data_freshness["金价"] = ("⏸", f"休市({ms.get('reason', '')})")
            else:
                data_freshness["金价"] = ("⚪", "时间戳未知")

    # 2. 获取宏观指标
    _logger.info("[2/9] 正在获取宏观指标...")
    macro_data = fetch_macro_indicators()
    if macro_data and macro_data.get("indicators"):
        try:
            insert_macro_snapshot(macro_data["indicators"], macro_data.get("timestamp"))
        except Exception:
            pass
        macro_ts = macro_data.get("timestamp", "")
        if macro_ts:
            try:
                mt = _parse_timestamp(macro_ts, timezone.utc)
                if mt is None:
                    raise ValueError("invalid macro timestamp")
                age_hours = _timestamp_age_minutes(mt) / 60
                ts_local = _format_local_timestamp(mt)
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
    _logger.info("[3/9] 正在获取持仓数据...")
    holdings = {"long_top": [], "short_top": [], "date": "", "contract": "", "trade_date": ""}
    if sge_holiday:
        _logger.info("  SGE休市(%s)，跳过持仓数据获取", sge_status.get('reason', ''))
    else:
        holdings = fetch_holdings_data()
    
    has_holdings = bool(holdings.get("long_top") or holdings.get("short_top"))
    if not has_holdings and not sge_holiday:
        _logger.error("未能获取到持仓数据，可能非交易日或数据源异常")
    
    if has_holdings:
        _logger.info("  合约: %s  多头: %d条  空头: %d条", holdings['contract'], len(holdings['long_top']), len(holdings['short_top']))

    holdings_date = holdings.get("date", "")
    if holdings_date:
        try:
            hd = datetime.strptime(holdings_date, "%Y-%m-%d")
            age_days = (datetime.now() - hd).days
            if sge_closed and age_days <= 5:
                data_freshness["持仓"] = ("⏸", f"休市({sge_status.get('reason', '')})|ts={holdings_date}T00:00:00")
            elif age_days <= 1:
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
    _logger.info("[4/9] 正在计算净多头...")
    positions = calculate_net_positions(holdings, top_n=_get_top_n())
    
    trade_date_str = holdings.get("date") or datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    today_data = {
        "date": trade_date_str,
        "holdings_date": holdings["date"],
        "trade_date": holdings.get("trade_date", ""),
        "contract": holdings["contract"],
        "positions": positions,
        "total_long": holdings.get("total_long", 0),
        "total_short": holdings.get("total_short", 0),
    }
    
    for pos in positions:
        chg = pos['net_change']
        _logger.info("  %s 净多头: %d 手  变化: %s%d 手", pos['name'], pos['net'], '+' if chg > 0 else '', chg)
    
    # 4. 短期警示分析
    _logger.info("[5/9] 正在分析短期警示...")
    analyzer = HoldingsAnalyzer()
    if has_holdings:
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
        _logger.info("  短期警示: %d 个", len(alerts))
    
    # 5. 长期趋势分析
    _logger.info("[6/9] 正在分析长期趋势...")
    trend_data = None
    if len(analyzer.history) > 1:
        trend_analyzer = TrendAnalyzer(analyzer.history)
        trend_data = trend_analyzer.analyze_long_term(today_data)
        _logger.info("  历史数据: %d 个交易日", trend_data['history_days'])
    
    # 6. 新闻情绪分析
    _logger.info("[7/9] 正在分析新闻情绪...")
    news_sentiment = None
    try:
        news_sentiment = fetch_news_sentiment()
        if news_sentiment:
            try:
                news_date = news_sentiment.get("timestamp", "")[:10] or trade_date_str
                upsert_news_sentiment(news_date, news_sentiment)
            except Exception:
                pass
            news_ts = news_sentiment.get("timestamp", "")
            if news_ts:
                try:
                    nt = _parse_timestamp(news_ts, _LOCAL_TZ)
                    if nt is None:
                        raise ValueError("invalid news timestamp")
                    age_hours = _timestamp_age_minutes(nt) / 60
                    ts_local = _format_local_timestamp(nt)
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
        _logger.warning("新闻分析失败: %s", e)
        data_freshness["新闻"] = ("❌", "获取失败")

    # 6.5 机构共识
    _logger.info("[8/9] 正在获取机构共识...")
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
        _logger.info("  机构观点: %d家, 共识方向: %s", inst_count, cons_dir)
    except Exception as e:
        _logger.warning("机构共识获取失败: %s", e)

    # 7. 智能预测
    _logger.info("[9/9] 正在运行智能预测...")
    report_date_str = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    prediction = None
    gold_prices = []
    try:
        gold_prices = get_daily_history(days=60, prefer_international=True) or []
        if gold_prices:
            holdings_for_pred = analyzer.history if len(analyzer.history) >= 3 else []
            if not holdings_for_pred:
                _logger.info("  持仓数据不足，将仅基于金价/宏观/新闻因子运行预测（8/12因子可用）")
            _verify_previous_prediction(gold_prices)
            iteration_result = run_iteration()
            predictor = GoldPricePredictor(holdings_for_pred, gold_prices, news_sentiment, macro_data)
            prediction = predictor.predict(today_data)
            prediction["news_sentiment"] = news_sentiment
            if not holdings_for_pred:
                prediction["confidence"] = max(20, prediction["confidence"] - 15)
                prediction["partial_data"] = True
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

            _logger.info("  预测: %s（置信度%d%%，评分%+.2f）", prediction['direction'], prediction['confidence'], prediction['score'])
            if consensus_comparison:
                _logger.info("  机构共识: %s", consensus_comparison.get('description', ''))
            _save_prediction_tracking(today_data, prediction, gold_prices, consensus_data, report_date=report_date_str)
        else:
            _logger.info("  金价数据不足，尝试自动回填历史数据...")
            try:
                from core.backfill import backfill_history
                bf_result = backfill_history(days=60, top_n=_get_top_n())
                if bf_result.get('gold_success', 0) > 0 or bf_result.get('success', 0) > 0:
                    _logger.info("  回填完成: 持仓%d天 金价%d天，重新运行预测", bf_result['success'], bf_result.get('gold_success', 0))
                    gold_prices = get_daily_history(days=60, prefer_international=True) or []
                    if gold_prices:
                        upsert_gold_prices(gold_prices)
                    holdings = fetch_holdings_data(top_n=_get_top_n())
                    if holdings and holdings.get("positions"):
                        positions = calculate_net_positions(holdings)
                        analyzer = HoldingsAnalyzer(positions)
                    holdings_for_pred = analyzer.history if len(analyzer.history) >= 3 else []
                    if gold_prices:
                        predictor = GoldPricePredictor(holdings_for_pred, gold_prices, news_sentiment, macro_data)
                        prediction = predictor.predict(today_data)
                        prediction["news_sentiment"] = news_sentiment
                        if not holdings_for_pred:
                            prediction["confidence"] = max(20, prediction["confidence"] - 15)
                            prediction["partial_data"] = True
                        _logger.info("  预测: %s（置信度%d%%，评分%+.2f）", prediction['direction'], prediction['confidence'], prediction['score'])
                        _save_prediction_tracking(today_data, prediction, gold_prices, consensus_data, report_date=report_date_str)
                    else:
                        _logger.info("  回填后金价数据仍不足，等待下次定时任务")
                else:
                    _logger.info("  回填未获取到新数据，等待下次定时任务")
            except Exception as bfe:
                _logger.error("  自动回填失败: %s", bfe)
    except Exception as e:
        _logger.warning("  预测失败: %s", e)

    # 8. 全维度警示引擎
    _logger.info("正在运行全维度警示引擎...")
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
            _logger.info("  全维度警示: %d个 [%s]", len(full_alerts), dim_str)
    except Exception as e:
        _logger.warning("  全维度警示引擎失败: %s", e)
        full_alerts = alerts
    
    # 生成并发送报告
    _logger.info("正在生成报告...")
    
    # 在报告头部添加生成时金价
    report = ""
    if price_summary and price_summary.get("available"):
        ps = price_summary
        ms = ps.get("market_status", {})
        report += f"💰 生成时金价: {ps['price']:.2f} USD/oz ({ps['change_pct']:+.2f}%)"
        if ms.get("status") == "closed":
            report += f" ⏸休市({ms.get('reason', '')})"
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
    
    tg_token, tg_chat_id = _get_telegram_settings()
    if skip_telegram:
        _logger.info("  跳过 Telegram 推送（仅生成报告）")
    elif not tg_token or tg_token == "YOUR_BOT_TOKEN_HERE":
        _logger.info(report)
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
        
        _logger.info("  报告已发送到 Telegram")
    
    # 保存到本地
    report_date = datetime.now().strftime("%Y-%m-%d")
    report_file = _data_path("reports", f"report_{report_date}.txt")
    os.makedirs(os.path.dirname(report_file), exist_ok=True)
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(report)
    try:
        insert_report(report_date, report)
    except Exception:
        pass
    try:
        from core.llm_utils import mark_first_run_done
        mark_first_run_done()
    except Exception:
        pass
    try:
        cleanup()
    except Exception:
        pass
    _logger.info("  报告已保存到: %s", report_file)
    
    return True


def run_realtime():
    """
    实时监控模式
    交易时段（北京时间 09:00-15:00 / 21:00-03:00）每30分钟执行一次
    非交易时段每4小时执行一次
    """
    _logger.info("🚀 实时监控模式已启动 | %s", datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    _logger.info("  交易时段: 每30分钟执行一次")
    _logger.info("  非交易时段: 每4小时执行一次")
    _logger.info("  按 Ctrl+C 停止")
    
    last_run = time.time()
    if _has_today_report():
        _logger.info("  今日报告已存在，等待下一个周期执行")
    else:
        _logger.info("  今日报告尚未生成，等待下一个周期执行")
    
    while True:
        now = datetime.now()

        is_trading = is_trading_hours()
        
        rt_trading, rt_nontrading = _get_realtime_intervals()
        if is_trading:
            interval = rt_trading * 60
        else:
            interval = rt_nontrading * 60
        
        elapsed = time.time() - last_run
        
        if elapsed >= interval:
            mode = "交易时段" if is_trading else "非交易时段"
            _logger.info("  [%s] %s", mode, now.strftime('%Y-%m-%d %H:%M:%S'))
            
            try:
                from core.gold_price import get_market_status
                ms = get_market_status()
                if ms.get("status") == "closed":
                    reason = ms.get("reason", "")
                    if "周末" in reason:
                        _logger.info("  周末休市，仅更新实时金价缓存")
                        from core.gold_price import get_realtime_price
                        get_realtime_price()
                    else:
                        run_daily_task()
                else:
                    run_daily_task()
            except Exception as e:
                _logger.error("执行失败: %s", e)
            
            last_run = time.time()
        
        time.sleep(60)  # 每分钟检查一次


def do_backfill(days: int = 30):
    _logger.info("  历史数据回填 | 回填 %d 天", days)
    
    from core.backfill import backfill_history
    result = backfill_history(days=days, top_n=_get_top_n())
    
    _logger.info("回填结果: 成功%d 失败%d 跳过%d", result['success'], result['failed'], result['skipped'])
    _logger.info("历史数据总计: %d 天", result['total_history'])
    
    if result['success'] > 0:
        _logger.info("正在基于历史数据生成分析报告...")
        run_daily_task()


def test_bot():
    tg_token, tg_chat_id = _get_telegram_settings()
    if not tg_token or tg_token == "YOUR_BOT_TOKEN_HERE":
        _logger.info("请先在系统设置中配置 Telegram Bot Token")
        return
    
    bot = TelegramBot(tg_token, tg_chat_id)
    _logger.info("正在测试 Bot 连接...")
    if bot.test_connection():
        _logger.info("正在发送测试消息...")
        bot.send_message(
            "✅ AI 黄金分析 Bot 连接成功！\n\n"
            "五因子预测模型：持仓动量 + 价格趋势 + 背离信号 + 波动率 + 新闻情绪\n"
            "支持：每日报告 + 实时监控模式"
        )
        _logger.info("测试完成！")
    else:
        _logger.warning("Bot 连接失败，请检查 Token 和 Chat ID")


def test_fetch():
    _logger.info("正在测试数据获取...")
    
    _logger.info("--- 实时金价 ---")
    rt = get_realtime_price()
    if rt:
        _logger.info("  现货黄金: %.2f USD/oz (%+.2f%%)", rt['price'], rt['change_pct'])
    
    _logger.info("--- 持仓数据 ---")
    holdings = fetch_holdings_data()
    _logger.info("  日期: %s  合约: %s", holdings['date'], holdings['contract'])
    
    if holdings.get("long_top"):
        positions = calculate_net_positions(holdings, top_n=_get_top_n())
        for pos in positions:
            chg = pos['net_change']
            _logger.info("  %s 净多头: %d 手  变化: %s%d 手", pos['name'], pos['net'], '+' if chg > 0 else '', chg)
    
    _logger.info("--- 新闻情绪 ---")
    try:
        news = fetch_news_sentiment()
        _logger.info("  情绪: %s (%+.2f)", news['sentiment'], news['sentiment_score'])
        _logger.info("  利多: %d  利空: %d  中性: %d", news['bullish_count'], news['bearish_count'], news['neutral_count'])
        if news.get("key_events"):
            for e in news["key_events"][:5]:
                _logger.info("  • %s", e)
    except Exception as e:
        _logger.error("  新闻获取失败: %s", e)


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
        _logger.info("AI 黄金分析定时任务已启动")
        _logger.info("  每天 %02d:%02d 生成报告", sch_hour, sch_min)
        _logger.info("  每天 %02d:%02d 生成报告", sch_hour2, sch_min2)
        _logger.info("  每天 %02d:%02d Telegram 推送最新报告", tg_hour, tg_min)
        _logger.info("  按 Ctrl+C 停止")
        
        schedule.every().day.at(f"{sch_hour:02d}:{sch_min:02d}").do(lambda: run_daily_task(skip_telegram=True))
        schedule.every().day.at(f"{sch_hour2:02d}:{sch_min2:02d}").do(lambda: run_daily_task(skip_telegram=True))
        schedule.every().day.at(f"{tg_hour:02d}:{tg_min:02d}").do(push_latest_report)
        if not _has_today_report():
            _logger.info("  今日报告尚未生成，等待定时时间执行")
        else:
            _logger.info("  今日报告已存在，跳过启动时执行")
        
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
