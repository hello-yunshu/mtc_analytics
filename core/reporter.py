# -*- coding: utf-8 -*-
"""
报告生成模块 - 综合多维度报告（按重要性排序）
"""

from datetime import datetime
from typing import Dict, List, Optional
import os

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_DATA_DIR = os.path.join(_PROJECT_ROOT, "data")
from .analyzer import LEVEL_ICON, LEVEL_LABEL, LEVEL_ORDER
from .alert_engine import DIMENSION_LABEL, DIMENSION_ORDER, DISPLAY_DIMENSION_ORDER


def format_report(date: str, contract: str, positions: List[Dict],
                  stats: Dict, alerts: List[Dict],
                  trend_data: Optional[Dict] = None,
                  prediction: Optional[Dict] = None,
                  news_sentiment: Optional[Dict] = None,
                  full_alerts: Optional[List[Dict]] = None,
                  data_freshness: Optional[Dict] = None,
                  holdings_date: Optional[str] = None) -> str:
    """生成格式化的每日报告（按重要性排序）"""
    lines = []

    all_alerts = list(alerts)
    if trend_data and trend_data.get("signals"):
        all_alerts.extend(trend_data["signals"])
    seen = set()
    unique_alerts = []
    for a in all_alerts:
        key = f"{a.get('type', '')}_{a.get('message', '')[:20]}"
        if key not in seen:
            seen.add(key)
            unique_alerts.append(a)
    unique_alerts.sort(key=lambda x: LEVEL_ORDER.get(x["level"], 99))

    # ===== 1. 综合解读 =====
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("💡 综合解读")
    lines.append(f"📊 黄金综合日报 | {date}")
    if data_freshness:
        freshness_parts = []
        has_stale = any(icon == "❌" for icon, _ in data_freshness.values())
        has_closed = any(icon == "⏸" for icon, _ in data_freshness.values())
        for name, (icon, desc) in data_freshness.items():
            freshness_parts.append(f"{icon}{name}:{desc}")
        if has_stale:
            lines.append("⚠️ 数据时效：" + " | ".join(freshness_parts))
        elif has_closed:
            lines.append("⏸ 数据时效：" + " | ".join(freshness_parts))
        else:
            lines.append("📋 数据时效：" + " | ".join(freshness_parts))
    if trend_data and trend_data.get("history_days", 0) > 1:
        lines.append(f"数据覆盖：{trend_data['history_days']} 个交易日")

    bull_signals = []
    bear_signals = []
    neutral_signals = []

    # 宏观面
    if prediction:
        factors = prediction.get("factors", {})
        real_rate = factors.get("real_rate", {}).get("score", 0)
        dollar = factors.get("dollar", {}).get("score", 0)
        infl = factors.get("inflation", {}).get("score", 0)
        if real_rate > 0.2:
            bull_signals.append(f"隐含实际利率下行（{real_rate:+.1f}）")
        elif real_rate < -0.2:
            bear_signals.append(f"隐含实际利率上行（{real_rate:+.1f}）")
        if dollar > 0.2:
            bull_signals.append(f"美元偏弱利好黄金（{dollar:+.1f}）")
        elif dollar < -0.2:
            bear_signals.append(f"美元偏强压制黄金（{dollar:+.1f}）")
        if infl > 0.2:
            bull_signals.append(f"通胀预期上行（{infl:+.1f}）")
        elif infl < -0.2:
            bear_signals.append(f"通胀预期下行（{infl:+.1f}）")

    # 技术面
    if prediction:
        factors = prediction.get("factors", {})
        pt = factors.get("price_trend", {}).get("score", 0)
        vol = factors.get("volatility", {}).get("score", 0)
        if pt > 0.2:
            bull_signals.append(f"技术趋势偏多（{pt:+.1f}）")
        elif pt < -0.2:
            bear_signals.append(f"技术趋势偏空（{pt:+.1f}）")
        if vol > 0.2:
            neutral_signals.append(f"波动率偏高需警惕（{vol:+.1f}）")
        elif vol < -0.2:
            neutral_signals.append(f"低波动可能变盘（{vol:+.1f}）")

    # 持仓面
    if stats['total_net_change'] > 0:
        bull_signals.append(f"机构整体加仓{stats['total_net_change']:+,}手")
    elif stats['total_net_change'] < 0:
        bear_signals.append(f"机构整体减仓{stats['total_net_change']:+,}手")

    if trend_data and trend_data.get("week", {}).get("days", 0) > 0:
        week = trend_data["week"]
        if week["change_pct"] > 3:
            bull_signals.append(f"中期持仓趋势偏多（周变化{week['change_pct']:+.1f}%）")
        elif week["change_pct"] < -3:
            bear_signals.append(f"中期持仓趋势偏空（周变化{week['change_pct']:+.1f}%）")

    # 情绪面
    effective_news = news_sentiment or (prediction.get("news_sentiment") if prediction else None)
    if effective_news:
        s_score = effective_news.get("sentiment_score", 0)
        bullish_n = effective_news.get("bullish_count", 0)
        bearish_n = effective_news.get("bearish_count", 0)
        if s_score > 0.2:
            bull_signals.append(f"新闻面偏多（利多{bullish_n}条 vs 利空{bearish_n}条）")
        elif s_score < -0.2:
            bear_signals.append(f"新闻面偏空（利空{bearish_n}条 vs 利多{bullish_n}条）")
        else:
            neutral_signals.append(f"新闻面中性（评分{s_score:+.2f}）")

        key_events = effective_news.get("key_events", [])
        for event in key_events[:5]:
            event_title = event.get("title", event) if isinstance(event, dict) else event
            event_lower = event_title.lower()
            if any(kw in event_lower for kw in ["降息", "宽松", "避险", "冲突", "战争", "关税", "购金", "增持", "央行", "衰退"]):
                bull_signals.append(f"关键事件：{event_title[:40]}")
            elif any(kw in event_lower for kw in ["加息", "鹰派", "抛售", "收紧", "暴跌", "大跌", "失守", "跌破"]):
                bear_signals.append(f"关键事件：{event_title[:40]}")

    # 模型预测
    if prediction:
        pred_dir = prediction.get("direction", "中性")
        pred_conf = prediction.get("confidence", 50)
        pred_score = prediction.get("score", 0)
        if pred_dir == "看多":
            bull_signals.append(f"十二因子模型看多（置信度{pred_conf}%，评分{pred_score:+.2f}）")
        elif pred_dir == "看空":
            bear_signals.append(f"十二因子模型看空（置信度{pred_conf}%，评分{pred_score:+.2f}）")
        else:
            neutral_signals.append(f"十二因子模型中性（评分{pred_score:+.2f}）")

        factors = prediction.get("factors", {})
        for key, name in [("etf_flow", "ETF资金流"), ("cb_gold", "央行购金"),
                          ("seasonality", "季节性"), ("divergence", "量价背离")]:
            f = factors.get(key, {})
            s = f.get("score", 0)
            if s > 0.3:
                bull_signals.append(f"{name}偏多（{s:+.1f}）")
            elif s < -0.3:
                bear_signals.append(f"{name}偏空（{s:+.1f}）")

    if bull_signals or bear_signals or neutral_signals:
        lines.append("")
    if bull_signals:
        lines.append(f"🔴 利多（{len(bull_signals)}）")
        for s in bull_signals:
            lines.append(f" • {s}")
    if bear_signals:
        lines.append(f"🟢 利空（{len(bear_signals)}）")
        for s in bear_signals:
            lines.append(f" • {s}")
    if neutral_signals:
        lines.append(f"⚪ 中性（{len(neutral_signals)}）")
        for s in neutral_signals:
            lines.append(f" • {s}")

    lines.append("")
    bull_count = len(bull_signals)
    bear_count = len(bear_signals)

    pred_score = prediction.get("score", 0) if prediction else 0
    pred_dir = prediction.get("direction", "中性") if prediction else "中性"
    pred_conf = prediction.get("confidence", 50) if prediction else 50

    model_signal = 0
    if pred_score > 0.08:
        model_signal = 1
    elif pred_score < -0.08:
        model_signal = -1

    signal_balance = bull_count - bear_count
    combined = model_signal * 2 + (1 if signal_balance > 1 else (-1 if signal_balance < -1 else 0))

    if abs(pred_score) < 0.08:
        if combined >= 2:
            lines.append(f"📊 综合判断：谨慎偏多（{bull_count}利多 vs {bear_count}利空），模型中性但利多信号较多")
        elif combined <= -2:
            lines.append(f"📊 综合判断：谨慎偏空（{bear_count}利空 vs {bull_count}利多），模型中性但利空信号较多")
        else:
            lines.append(f"📊 综合判断：中性震荡（{bull_count}利多 vs {bear_count}利空），模型评分{pred_score:+.2f}方向不明")
    elif pred_dir == "看多":
        if combined >= 2:
            lines.append(f"📊 综合判断：偏多（{bull_count}利多 vs {bear_count}利空），模型看多且信号一致（置信度{pred_conf}%）")
        else:
            lines.append(f"📊 综合判断：谨慎偏多（{bull_count}利多 vs {bear_count}利空），模型看多但利空信号较多（置信度{pred_conf}%）")
    elif pred_dir == "看空":
        if combined <= -2:
            lines.append(f"📊 综合判断：偏空（{bear_count}利空 vs {bull_count}利多），模型看空且信号一致（置信度{pred_conf}%）")
        else:
            lines.append(f"📊 综合判断：谨慎偏空（{bear_count}利空 vs {bull_count}利多），模型看空但利多信号较多（置信度{pred_conf}%）")
    else:
        if combined >= 2:
            lines.append(f"📊 综合判断：偏多（{bull_count}利多 vs {bear_count}利空），多头信号占优")
        elif combined <= -2:
            lines.append(f"📊 综合判断：偏空（{bear_count}利空 vs {bull_count}利多），空头信号占优")
        elif combined > 0:
            lines.append(f"📊 综合判断：谨慎偏多（{bull_count}利多 vs {bear_count}利空），多空分歧存在")
        elif combined < 0:
            lines.append(f"📊 综合判断：谨慎偏空（{bear_count}利空 vs {bull_count}利多），多空分歧存在")
        else:
            lines.append(f"📊 综合判断：中性震荡（{bull_count}利多 vs {bear_count}利空），方向不明")

    level_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for a in unique_alerts:
        level_counts[a["level"]] = level_counts.get(a["level"], 0) + 1

    if level_counts["critical"] > 0:
        lines.append(f"⚠️ 🔴🔴 {level_counts['critical']} 个【重】级信号，需立即关注！")
    if level_counts["high"] > 0:
        lines.append(f"⚠️ 🔴 {level_counts['high']} 个【高】级信号，需重点关注")
    if level_counts["medium"] > 0:
        lines.append(f"⚠️ 🟡 {level_counts['medium']} 个【中】级信号，值得警惕")

    if not unique_alerts:
        lines.append("✅ 今日无异常信号，市场相对平稳")

    # ===== 2. 智能预测 =====
    if prediction:
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("🔮 智能预测（十二因子多周期模型）")

        dir_icon = "🔴" if prediction["direction"] == "看多" else ("🟢" if prediction["direction"] == "看空" else "⚪")
        lines.append(f"{dir_icon} 预测方向：{prediction['direction']}（置信度 {prediction['confidence']}%）")
        lines.append(f"   综合评分：{prediction['score']:+.2f}")

        period_conflict = prediction.get("period_conflict")
        if period_conflict:
            lines.append(f"   ⚠️ {period_conflict['warning']}")

        period_trends = prediction.get("period_trends", {})
        if period_trends:
            pt_parts = []
            for pk in ["short", "medium", "long"]:
                pt = period_trends.get(pk, {})
                pdir = pt.get("direction", "中性")
                pscore = pt.get("score", 0)
                pconf = pt.get("confidence", 0)
                plabel = pt.get("label", pk)
                if pdir != "中性":
                    pt_parts.append(f"{plabel}{pdir}({pscore:+.2f}/{pconf}%)")
            if pt_parts:
                lines.append(f"   多周期：{' · '.join(pt_parts)}")

        factors = prediction.get("factors", {})
        factor_groups = [
            ("📊 宏观", {"real_rate": "实际利率", "dollar": "美元因子", "inflation": "通胀预期"}),
            ("📈 中观", {"momentum": "持仓动量", "extreme": "持仓极值", "divergence": "背离信号", "cb_gold": "央行购金", "etf_flow": "ETF资金流"}),
            ("📉 微观", {"price_trend": "技术趋势", "volatility": "波动率", "news_sentiment": "新闻情绪"}),
            ("📅 日历", {"seasonality": "季节性"}),
        ]
        for group_name, group_factors in factor_groups:
            group_parts = []
            for key, name in group_factors.items():
                f = factors.get(key, {})
                s = f.get("score", 0)
                if s > 0.2:
                    icon = "🔴"
                elif s < -0.2:
                    icon = "🟢"
                else:
                    icon = "⚪"
                group_parts.append(f"{icon}{name}{s:+.1f}")
            lines.append(f"   {group_name}：{' | '.join(group_parts)}")

        for key, name in [("real_rate", "实际利率"), ("dollar", "美元因子"),
                          ("inflation", "通胀预期"),
                          ("momentum", "持仓动量"), ("extreme", "持仓极值"),
                          ("divergence", "背离信号"), ("cb_gold", "央行购金"),
                          ("etf_flow", "ETF资金流"),
                          ("price_trend", "技术趋势"), ("volatility", "波动率"),
                          ("news_sentiment", "新闻情绪"), ("seasonality", "季节性")]:
            f = factors.get(key, {})
            signal = f.get("signal", "")
            if signal and signal not in ("数据不足", "无历史数据", "新闻数据暂无", "金价数据不足"):
                lines.append(f"   {name}：{signal}")

        llm_reasoning = prediction.get("llm_reasoning", "")
        if llm_reasoning:
            lines.append(f"🤖 AI 推理：{llm_reasoning}")

    # ===== 3. 宏观经济 =====
    dim_alerts_map = {}
    if full_alerts:
        for a in full_alerts:
            dk = a.get("dimension", "position")
            if dk not in dim_alerts_map:
                dim_alerts_map[dk] = []
            dim_alerts_map[dk].append(a)

    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("🏛️ 宏观经济环境")

    macro_added = False
    macro_lines = []

    if prediction:
        factors = prediction.get("factors", {})
        rr = factors.get("real_rate", {})
        dl = factors.get("dollar", {})
        infl = factors.get("inflation", {})
        rr_score = rr.get("score", 0)
        rr_signal = rr.get("signal", "")
        dl_score = dl.get("score", 0)
        dl_signal = dl.get("signal", "")
        infl_score = infl.get("score", 0)
        infl_signal = infl.get("signal", "")

        if rr_signal and rr_signal not in ("数据不足", "无历史数据"):
            rr_icon = "🔴" if rr_score > 0.2 else ("🟢" if rr_score < -0.2 else "⚪")
            macro_lines.append(f"{rr_icon} 实际利率：{rr_signal}（{rr_score:+.1f}）")
            macro_added = True
        if dl_signal and dl_signal not in ("数据不足", "无历史数据"):
            dl_icon = "🔴" if dl_score > 0.2 else ("🟢" if dl_score < -0.2 else "⚪")
            macro_lines.append(f"{dl_icon} 美元因子：{dl_signal}（{dl_score:+.1f}）")
            macro_added = True
        if infl_signal and infl_signal not in ("通胀预期中性", "数据不足"):
            infl_icon = "🔴" if infl_score > 0.2 else ("🟢" if infl_score < -0.2 else "⚪")
            macro_lines.append(f"{infl_icon} 通胀预期：{infl_signal}（{infl_score:+.1f}）")
            macro_added = True

    if "macro" in dim_alerts_map:
        for a in dim_alerts_map["macro"]:
            msg = a.get("message", "")
            macro_lines.append(f"  • {msg}")
            macro_added = True

    if macro_added:
        for ml in macro_lines:
            lines.append(ml)

        if "macro" in dim_alerts_map and prediction:
            factors = prediction.get("factors", {})
            rr = factors.get("real_rate", {})
            dl = factors.get("dollar", {})
            rr_signal = rr.get("signal", "")
            dl_signal = dl.get("signal", "")
            trend_parts = []
            if rr_signal and rr_signal not in ("数据不足", "无历史数据"):
                trend_parts.append(f"实际利率趋势：{rr_signal}")
            if dl_signal and dl_signal not in ("数据不足", "无历史数据"):
                trend_parts.append(f"美元趋势：{dl_signal}")
            if trend_parts:
                lines.append("长期判断：" + " | ".join(trend_parts))
    else:
        lines.append("宏观面暂无显著变化")

    lines.append("🔄 关联性")
    corr_added = False
    if "correlation" in dim_alerts_map:
        for a in dim_alerts_map["correlation"]:
            msg = a.get("message", "")
            lines.append(f"  • {msg}")
            corr_added = True
    if not corr_added:
        lines.append("金价与美元/美债/原油关联正常")

    # ===== 4. 技术分析 =====
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("📐 技术面分析")

    tech_added = False

    if prediction:
        factors = prediction.get("factors", {})
        pt = factors.get("price_trend", {})
        pt_score = pt.get("score", 0)
        pt_signal = pt.get("signal", "")
        vol = factors.get("volatility", {})
        vol_score = vol.get("score", 0)
        vol_signal = vol.get("signal", "")

        if pt_signal and pt_signal not in ("数据不足", "无历史数据", "金价数据不足"):
            pt_icon = "🔴" if pt_score > 0.2 else ("🟢" if pt_score < -0.2 else "⚪")
            lines.append(f"{pt_icon} 趋势判断：{pt_signal}（{pt_score:+.1f}）")
            tech_added = True
        if vol_signal and vol_signal not in ("数据不足", "无历史数据", "金价数据不足"):
            vol_icon = "🔴" if vol_score > 0.2 else ("🟢" if vol_score < -0.2 else "⚪")
            lines.append(f"{vol_icon} 波动率：{vol_signal}（{vol_score:+.1f}）")
            tech_added = True

    if "technical" in dim_alerts_map:
        lines.append("关键技术信号：")
        for a in dim_alerts_map["technical"]:
            lines.append(f"  • {a.get('message', '')}")
            tech_added = True

    if "volatility" in dim_alerts_map:
        lines.append("波动率信号：")
        for a in dim_alerts_map["volatility"]:
            lines.append(f"  • {a.get('message', '')}")
            tech_added = True

    if not tech_added:
        lines.append("技术面暂无显著信号")

    lines.append("🔀 量价背离")
    div_added = False
    if "divergence" in dim_alerts_map:
        for a in dim_alerts_map["divergence"]:
            lines.append(f"  • {a.get('message', '')}")
            div_added = True
    if not div_added:
        lines.append("量价关系正常，暂无背离信号")

    # ===== 5. 新闻情绪 =====
    effective_news = news_sentiment or (prediction.get("news_sentiment") if prediction else None)
    if effective_news:
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("📰 新闻情绪")

        score = effective_news.get("sentiment_score", 0)
        sentiment_label = effective_news.get("sentiment", "中性")
        bullish = effective_news.get("bullish_count", 0)
        bearish = effective_news.get("bearish_count", 0)
        neutral = effective_news.get("neutral_count", 0)
        confidence = effective_news.get("confidence", "medium")
        analyzer = effective_news.get("analyzer", "keyword")

        if score > 0.2:
            s_icon = "🔴"
        elif score < -0.2:
            s_icon = "🟢"
        else:
            s_icon = "⚪"

        conf_label = {"high": "高", "medium": "中", "low": "低"}.get(confidence, "中")
        analyzer_label = {"hybrid": "混合", "llm": "LLM", "keyword": "关键词", "none": "无"}.get(analyzer, analyzer)

        lines.append(f"{s_icon} 情绪：{sentiment_label}（评分 {score:+.2f}）")
        lines.append(f"   利多 {bullish} 条 | 利空 {bearish} 条 | 中性 {neutral} 条")
        lines.append(f"   可信度：{conf_label} | 分析方式：{analyzer_label}")

        sources_ok = effective_news.get("sources_ok", [])
        sources_failed = effective_news.get("sources_failed", [])
        if sources_ok:
            lines.append(f"   来源：{' | '.join(sources_ok)}")
        if sources_failed:
            lines.append(f"   ⚠️ 不可用：{' | '.join(sources_failed)}")

        llm_summary = effective_news.get("llm_summary", "")
        if llm_summary:
            lines.append(f"🤖 AI 摘要：{llm_summary}")

        key_events = effective_news.get("key_events", [])
        if key_events:
            lines.append(f"📰 关键事件：")
            for event in key_events[:5]:
                if isinstance(event, dict):
                    title = event.get("title", "")
                    link = event.get("link", "")
                    if link:
                        lines.append(f"   • {title} [链接]({link})")
                    else:
                        lines.append(f"   • {title}")
                else:
                    lines.append(f"   • {event}")

    # ===== 6. 期货多空 =====
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    holdings_date_label = f"（数据日期: {holdings_date}）" if holdings_date and holdings_date != date else ""
    lines.append(f"📊 期货多空持仓{holdings_date_label}")

    lines.append(f"前5大机构净多头合计：{stats['total_net']:,} 手")
    lines.append(f"今日净变化：{'+' if stats['total_net_change'] > 0 else ''}{stats['total_net_change']:,} 手")
    lines.append(f"整体趋势：{stats['trend']}")
    lines.append(f"加仓 {stats['increasing_count']} 家 | 减仓 {stats['decreasing_count']} 家 | 不变 {stats['unchanged_count']} 家")

    lines.append("📋 机构详细（前5大净多头）")
    for i, pos in enumerate(positions, 1):
        change_str = f"{'+' if pos['net_change'] > 0 else ''}{pos['net_change']:,}"
        net_str = f"{'+' if pos['net'] > 0 else ''}{pos['net']:,}"

        if pos['net_change'] > 0:
            arrow = "🔴"
        elif pos['net_change'] < 0:
            arrow = "🟢"
        else:
            arrow = "⚪"

        lines.append(f"{i}. {pos['name']}")
        lines.append(f"   多头:{pos['long']:,}  空头:{pos['short']:,}  净多头:{net_str}  {arrow}{change_str}")

    # 持仓结构信号
    if "position" in dim_alerts_map:
        lines.append("🏢 持仓结构信号：")
        for a in dim_alerts_map["position"]:
            lines.append(f"  • {a.get('message', '')}")

    # 长期趋势
    if trend_data and trend_data.get("history_days", 0) > 1:
        lines.append("📅 中长期持仓趋势")

        week = trend_data.get("week", {})
        if week.get("days", 0) > 0:
            lines.append(f"近{week['days']}日 {week.get('direction_icon', '')} {week.get('direction', '')}")
            lines.append(f"   净多头变化：{week['total_change']:+,} 手（{week['change_pct']:+.1f}%）")

        biweek = trend_data.get("biweek", {})
        if biweek.get("days", 0) > 0:
            lines.append(f"近{biweek['days']}日 {biweek.get('direction_icon', '')} {biweek.get('direction', '')}")
            lines.append(f"   净多头变化：{biweek['total_change']:+,} 手（{biweek['change_pct']:+.1f}%）")

        month = trend_data.get("month", {})
        if month.get("days", 0) > 0:
            lines.append(f"近{month['days']}日 {month.get('direction_icon', '')} {month.get('direction', '')}")
            lines.append(f"   净多头变化：{month['total_change']:+,} 手（{month['change_pct']:+.1f}%）")

        ratio = trend_data.get("ratio_trend", {})
        if ratio:
            lines.append(f"⚖️ 多空比：{ratio['latest_ratio']:.1f}（{ratio['trend']}）")

        broker_trends = trend_data.get("broker_trends", [])
        if broker_trends:
            lines.append(f"🏢 各机构中期变化")
            for bt in broker_trends:
                pct_str = f"{bt['change_pct']:+.1f}%"
                trend_icon = "📈" if bt["change_pct"] > 0 else ("📉" if bt["change_pct"] < 0 else "➡️")
                lines.append(f"   {bt['name']}  净多头:{bt['latest_net']:,}  {trend_icon} {pct_str}  {bt['recent_trend']}")

    # ===== 7. 央行购金 & ETF资金流 =====
    has_cb_etf = False
    if "cb_gold" in dim_alerts_map:
        has_cb_etf = True
    if "etf_flow" in dim_alerts_map:
        has_cb_etf = True
    if prediction:
        factors = prediction.get("factors", {})
        cb_factor = factors.get("cb_gold", {})
        etf_factor = factors.get("etf_flow", {})
        infl = factors.get("inflation", {})
        season = factors.get("seasonality", {})
        if (cb_factor.get("signal") and cb_factor.get("signal") != "央行购金中性"):
            has_cb_etf = True
        if (etf_factor.get("signal") and etf_factor.get("signal") != "ETF资金流中性"):
            has_cb_etf = True

    if has_cb_etf:
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("🏦 央行购金 & 📊 ETF资金流")

    if "cb_gold" in dim_alerts_map:
        lines.append("🏦 央行购金动态：")
        for a in dim_alerts_map["cb_gold"]:
            lines.append(f"  • {a.get('message', '')}")
        if prediction:
            cb_factor = prediction.get("factors", {}).get("cb_gold", {})
            cb_signal = cb_factor.get("signal", "")
            cb_score = cb_factor.get("score", 0)
            if cb_signal and cb_signal not in ("央行购金中性",):
                lines.append(f"  因子判断：{cb_signal}（评分{cb_score:+.1f}）")
    elif prediction:
        factors = prediction.get("factors", {})
        cb_factor = factors.get("cb_gold", {})
        cb_signal = cb_factor.get("signal", "")
        cb_score = cb_factor.get("score", 0)
        if cb_signal and cb_signal not in ("央行购金中性",):
            lines.append("🏦 央行购金：")
            lines.append(f"  {cb_signal}（评分{cb_score:+.1f}）")

    if "etf_flow" in dim_alerts_map:
        lines.append("📊 ETF资金流：")
        for a in dim_alerts_map["etf_flow"]:
            lines.append(f"  • {a.get('message', '')}")
        if prediction:
            etf_factor = prediction.get("factors", {}).get("etf_flow", {})
            etf_signal = etf_factor.get("signal", "")
            etf_score = etf_factor.get("score", 0)
            if etf_signal and etf_signal not in ("ETF资金流中性",):
                lines.append(f"  因子判断：{etf_signal}（评分{etf_score:+.1f}）")
    elif prediction:
        factors = prediction.get("factors", {})
        etf_factor = factors.get("etf_flow", {})
        etf_signal = etf_factor.get("signal", "")
        etf_score = etf_factor.get("score", 0)
        if etf_signal and etf_signal not in ("ETF资金流中性",):
            lines.append("📊 ETF资金流：")
            lines.append(f"  {etf_signal}（评分{etf_score:+.1f}）")

    if prediction:
        factors = prediction.get("factors", {})
        infl = factors.get("inflation", {})
        season = factors.get("seasonality", {})
        infl_signal = infl.get("signal", "")
        season_signal = season.get("signal", "")
        if (infl_signal and infl_signal not in ("通胀预期中性",)) or (season_signal and season_signal not in ("季节性中性",)):
            lines.append("🌐 通胀 & 季节性：")
            if infl_signal and infl_signal not in ("通胀预期中性",):
                lines.append(f"  {infl_signal}（评分{infl.get('score', 0):+.1f}）")
            if season_signal and season_signal not in ("季节性中性",):
                lines.append(f"  {season_signal}（评分{season.get('score', 0):+.1f}）")

    lines.append("📅 日历事件")
    cal_added = False
    if "calendar" in dim_alerts_map:
        for a in dim_alerts_map["calendar"]:
            lines.append(f"  • {a.get('message', '')}")
            cal_added = True
    if not cal_added:
        lines.append("近期无重大日历事件")

    # ===== 8. 全维度警示信号 =====
    display_alerts = full_alerts if full_alerts else unique_alerts
    if display_alerts:
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("🔔 全维度警示信号")

        has_dimension = any(a.get("dimension") for a in display_alerts)

        if has_dimension:
            dim_grouped = {}
            for alert in display_alerts:
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
            for alert in display_alerts:
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

    # ===== 9. 模型回测验证 =====
    try:
        from .db import get_all_prediction_tracking
        tracking = get_all_prediction_tracking(days=365)
        if tracking and len(tracking) >= 5:
            lines.append("")
            lines.append("━━━━━━━━━━━━━━━━━━━━━━")
            lines.append("📈 模型回测验证")

            verified = [r for r in tracking
                        if r.get("verified") and r.get("prediction") not in ("中性",)
                        and r.get("actual_direction") not in ("中性（不参与准确率统计）",)]
            neutral_count = sum(1 for r in tracking if r.get("prediction") == "中性")
            unverified_count = sum(1 for r in tracking if not r.get("verified"))

            total_verified = len(verified)
            correct = sum(1 for r in verified if r.get("prediction") == r.get("actual_direction"))

            recent_30_verified = [r for r in tracking[-30:]
                                  if r.get("verified") and r.get("prediction") not in ("中性",)
                                  and r.get("actual_direction") not in ("中性（不参与准确率统计）",)]
            recent_correct = sum(1 for r in recent_30_verified if r.get("prediction") == r.get("actual_direction"))

            overall_acc = correct / total_verified * 100 if total_verified > 0 else 0
            recent_acc = recent_correct / len(recent_30_verified) * 100 if len(recent_30_verified) > 0 else 0

            lines.append(f"  历史预测总数：{len(tracking)}次（已验证{total_verified}次，中性{neutral_count}次，未验证{unverified_count}次）")
            lines.append(f"  整体准确率：{correct}/{total_verified}（{overall_acc:.1f}%）")
            lines.append(f"  近{len(recent_30_verified)}次验证准确率：{recent_correct}/{len(recent_30_verified)}（{recent_acc:.1f}%）")

            bull_total = sum(1 for r in verified if r.get("prediction") == "看多")
            bull_correct = sum(1 for r in verified if r.get("prediction") == "看多" and r.get("actual_direction") == "看多")
            bear_total = sum(1 for r in verified if r.get("prediction") == "看空")
            bear_correct = sum(1 for r in verified if r.get("prediction") == "看空" and r.get("actual_direction") == "看空")

            if bull_total > 0:
                lines.append(f"  看多准确率：{bull_correct}/{bull_total}（{bull_correct/bull_total*100:.0f}%）")
            if bear_total > 0:
                lines.append(f"  看空准确率：{bear_correct}/{bear_total}（{bear_correct/bear_total*100:.0f}%）")

            high_conf = [r for r in verified if r.get("confidence", 0) >= 70]
            if len(high_conf) >= 5:
                hc_correct = sum(1 for r in high_conf if r.get("prediction") == r.get("actual_direction"))
                lines.append(f"  高置信度(≥70%)准确率：{hc_correct}/{len(high_conf)}（{hc_correct/len(high_conf)*100:.0f}%）")
    except Exception:
        pass

    # ===== 10. 模型自迭代状态 =====
    try:
        from .model_iteration import get_iteration_status
        from .utils import load_json as _load_settings
        iter_status = get_iteration_status()
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("🔄 模型自迭代")
        mode_label = "LLM+规则" if iter_status.get("llm_available") else "纯规则"
        lines.append(f"  迭代模式：{mode_label}")
        if iter_status.get("enabled"):
            lines.append(f"  状态：已启用（已验证{iter_status.get('verified_samples',0)}个样本）")
        else:
            lines.append(f"  状态：待激活（需{iter_status.get('min_samples_required',20)}个验证样本，当前{iter_status.get('verified_samples',0)}个）")
        if iter_status.get("total_iterations", 0) > 0:
            lines.append(f"  累计迭代：{iter_status['total_iterations']}次")
            lines.append(f"  上次迭代：{iter_status.get('last_iteration', '未知')}")
        if iter_status.get("llm_available"):
            tu = iter_status.get("token_usage", {})
            lines.append(f"  LLM Token：本月已用{tu.get('used',0)}/{iter_status.get('token_budget',10000)}")

        _ws = _load_settings(os.path.join(_DATA_DIR, "web_settings.json")) or {}
        _pred_thr = _ws.get("pred_threshold", 0.08)
        lines.append(f"  预测方向阈值：{_pred_thr}")

        current_weights = iter_status.get("current_weights", {})
        if current_weights:
            weight_parts = []
            from .model_iteration import FACTOR_LABELS
            for fk, label in FACTOR_LABELS.items():
                w = current_weights.get(fk)
                if w is not None:
                    weight_parts.append(f"{label}:{w:.3f}")
            if weight_parts:
                lines.append(f"  当前权重：{' | '.join(weight_parts)}")

        history = iter_status.get("recent_history", [])
        if history:
            last = history[-1]
            if last.get("adjustments"):
                for adj in last["adjustments"][:3]:
                    lines.append(f"  调整：{adj}")
            if last.get("diagnosis"):
                lines.append(f"  AI诊断：{last['diagnosis'][:60]}")
    except Exception:
        pass

    lines.append("")
    lines.append(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    return "\n".join(lines)


def format_alert_only(alerts: List[Dict], date: str) -> str:
    """仅生成警示部分（用于紧急推送）"""
    if not alerts:
        return ""

    lines = [f"🚨 沪金全维度警示 | {date}", ""]

    has_dimension = any(a.get("dimension") for a in alerts)

    if has_dimension:
        dim_grouped = {}
        for alert in alerts:
            dim = alert.get("dimension", "position")
            if dim not in dim_grouped:
                dim_grouped[dim] = []
            dim_grouped[dim].append(alert)

        for dim_key in DISPLAY_DIMENSION_ORDER:
            if dim_key not in dim_grouped:
                continue
            dim_label = DIMENSION_LABEL.get(dim_key, dim_key)
            lines.append(f"{dim_label}：")
            for alert in dim_grouped[dim_key]:
                level_icon = LEVEL_ICON.get(alert["level"], "")
                lines.append(f"  {level_icon} {alert['message']}")
            lines.append("")
    else:
        grouped = {}
        for alert in alerts:
            level = alert["level"]
            if level not in grouped:
                grouped[level] = []
            grouped[level].append(alert)

        for level in ["critical", "high", "medium", "low"]:
            if level not in grouped:
                continue
            icon = LEVEL_ICON.get(level, "")
            label = LEVEL_LABEL.get(level, "")
            lines.append(f"{icon} {label}级：")
            for alert in grouped[level]:
                lines.append(f"  • {alert['message']}")
            lines.append("")

    return "\n".join(lines)
