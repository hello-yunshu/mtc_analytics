# -*- coding: utf-8 -*-
"""
预测算法模块 - 十二因子多周期黄金预测模型

参考框架：
- 东证期货《黄金择时因子及多周期合成》(2025)
- CFTC持仓分析最佳实践
- 湍流增强黄金定价模型
- World Gold Council《Gold as a strategic asset》

因子体系（四维度十二因子）：
  宏观维度（长周期）：
    F1. 实际利率因子 - 真实美债收益率数据
    F2. 美元因子 - 真实美元指数数据
    F9. 通胀预期因子 - 盈亏平衡通胀率/TIPS
  中观维度（中周期）：
    F3. 持仓动量因子 - 机构净多头变化趋势
    F4. 持仓极值因子 - 历史分位数+拥挤度检测
    F5. 持仓-价格背离因子 - 量价背离信号
    F10. 央行购金因子 - 央行购金动态追踪
    F11. ETF资金流因子 - GLD等黄金ETF资金流向
  微观维度（短周期）：
    F6. 技术趋势因子 - RSI/MACD/均线/布林带
    F7. 波动率因子 - ATR/波动率状态
    F8. 新闻情绪因子 - 实时新闻情绪分析
  日历维度（周期性）：
    F12. 季节性因子 - 黄金季节性规律

权重机制：动态权重（根据波动率状态和市场环境自适应调整）
"""

from typing import Dict, List, Optional
from datetime import datetime
import os

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_DATA_DIR = os.path.join(_PROJECT_ROOT, "data")
from .utils import load_json

FACTOR_PERIOD_MAP = {
    "short": ["price_trend", "volatility", "news_sentiment", "etf_flow"],
    "medium": ["momentum", "extreme", "divergence", "real_rate", "dollar", "inflation"],
    "long": ["cb_gold", "seasonality"],
}

PERIOD_HORIZONS = {
    "short": 5,
    "medium": 10,
    "long": 20,
}

PERIOD_THRESHOLD = {
    "short": 0.05,
    "medium": 0.03,
    "long": 0.02,
}

PERIOD_LABELS = {
    "short": "短期",
    "medium": "中期",
    "long": "长期",
}

PERIOD_WEIGHT_MULTIPLIER = {
    "short": 1.0,
    "medium": 1.2,
    "long": 1.0,
}


class GoldPricePredictor:
    """金价预测器 - 十二因子多周期模型"""

    def __init__(self, holdings_history: List[Dict], gold_prices: List[Dict],
                 news_sentiment: Optional[Dict] = None,
                 macro_data: Optional[Dict] = None):
        self.holdings_history = holdings_history
        self.gold_prices = gold_prices
        self.news_sentiment = news_sentiment
        self.macro_data = macro_data or {}

    def predict(self, today_data: Dict) -> Dict:
        """
        生成预测报告

        Returns:
            {
                "direction": "看多" / "看空" / "中性",
                "confidence": 75,
                "score": 0.35,
                "factors": {...},
                "reasoning": "...",
                "period_trends": {
                    "short": {"direction": ..., "score": ..., "confidence": ...},
                    "medium": {"direction": ..., "score": ..., "confidence": ...},
                    "long": {"direction": ..., "score": ..., "confidence": ...},
                },
            }
        """
        factors = {}

        factors["real_rate"] = self._calc_real_rate()
        factors["dollar"] = self._calc_dollar_factor()
        factors["inflation"] = self._calc_inflation()

        factors["momentum"] = self._calc_momentum(today_data)
        factors["extreme"] = self._calc_extreme_position(today_data)
        factors["divergence"] = self._calc_divergence()
        factors["cb_gold"] = self._calc_cb_gold()
        factors["etf_flow"] = self._calc_etf_flow()

        factors["price_trend"] = self._calc_price_trend()
        factors["volatility"] = self._calc_volatility()
        factors["news_sentiment"] = self._calc_news_sentiment()

        factors["seasonality"] = self._calc_seasonality(today_data)

        weights = self._dynamic_weights(factors)

        active_factors = {k: v for k, v in factors.items() if abs(v.get("score", 0)) >= 0.05 and k in weights}
        no_data_factors = {k for k, v in factors.items() if v.get("_no_data") and k in weights}
        active_weights = {k: weights[k] for k in active_factors}
        no_data_weight = sum(weights[k] for k in no_data_factors if k not in active_factors)
        active_total = sum(active_weights.values())
        if active_total > 0:
            redistribute = no_data_weight / active_total
            active_weights = {k: v * (1 + redistribute) for k, v in active_weights.items()}

        total_score = 0.0
        for k in active_factors:
            s = active_factors[k].get("score", 0)
            w = active_weights.get(k, 0)
            if s != s or w != w:
                continue
            total_score += s * w
        total_score = max(-1.0, min(1.0, total_score))

        INTERACTION_PAIRS = [
            ("real_rate", "dollar", 0.05),
            ("momentum", "divergence", 0.04),
            ("price_trend", "news_sentiment", 0.03),
        ]
        for f1, f2, coeff in INTERACTION_PAIRS:
            s1 = active_factors.get(f1, {}).get("score", 0)
            s2 = active_factors.get(f2, {}).get("score", 0)
            if abs(s1) >= 0.1 and abs(s2) >= 0.1 and s1 * s2 > 0:
                interaction = coeff * s1 * s2
                total_score += interaction
        total_score = max(-1.0, min(1.0, total_score))

        bull_score = sum(
            active_factors[k]["score"] * active_weights[k]
            for k in active_factors if active_factors[k]["score"] > 0
        )
        bear_score = sum(
            abs(active_factors[k]["score"]) * active_weights[k]
            for k in active_factors if active_factors[k]["score"] < 0
        )
        if bull_score > 0 and bear_score > 0:
            dominance = abs(bull_score - bear_score) / max(bull_score, bear_score)
            if dominance > 0.3:
                import math
                sigmoid_val = 1.0 / (1.0 + math.exp(-(dominance - 0.5) * 6))
                enhance = 1.0 + 0.3 * sigmoid_val
                total_score *= enhance
                total_score = max(-1.0, min(1.0, total_score))

        settings = load_json(os.path.join(_DATA_DIR, "web_settings.json")) or {}
        threshold = float(settings.get("pred_threshold", 0.08))

        if total_score > threshold:
            direction = "看多"
            confidence = self._calibrate_confidence(total_score, "bullish")
        elif total_score < -threshold:
            direction = "看空"
            confidence = self._calibrate_confidence(total_score, "bearish")
        else:
            direction = "中性"
            confidence = self._calibrate_confidence(total_score, "neutral")

        period_trends = self._calc_period_trends(factors, weights, threshold)

        period_conflict = self._detect_period_conflict(period_trends, direction)
        if period_conflict:
            confidence = max(30, confidence - period_conflict["penalty"])

        reasoning = self._generate_reasoning(factors, weights, total_score, direction, period_trends, period_conflict)

        factor_brief = []
        for key, label in FACTOR_LABELS.items():
            f = factors.get(key, {})
            s = f.get("score", 0)
            if abs(s) >= 0.05:
                factor_brief.append(f"{label}:{s:+.1f}")
        factors_text = " ".join(factor_brief)

        result = {
            "direction": direction,
            "confidence": confidence,
            "score": round(total_score, 2),
            "factors": factors,
            "reasoning": reasoning,
            "period_trends": period_trends,
            "_factors_text": factors_text,
        }
        if period_conflict:
            result["period_conflict"] = period_conflict
        return result

    def _detect_period_conflict(self, period_trends: Dict, overall_direction: str) -> Optional[Dict]:
        """
        检测周期间方向矛盾
        当短期与长期方向相反时，降低整体置信度
        当所有周期与整体方向矛盾时，大幅降低置信度
        当整体为中性但短期高置信度方向明确时，标注短期风险信号
        """
        if not period_trends:
            return None

        short_dir = period_trends.get("short", {}).get("direction", "中性")
        short_conf = period_trends.get("short", {}).get("confidence", 0)
        medium_dir = period_trends.get("medium", {}).get("direction", "中性")
        long_dir = period_trends.get("long", {}).get("direction", "中性")

        if overall_direction == "中性":
            if short_dir != "中性" and short_conf >= 60:
                return {
                    "warning": f"整体中性但短期{short_dir}（置信度{short_conf}%），需关注短期方向性风险",
                    "penalty": 0,
                    "conflict_periods": ["短期"],
                    "short_risk": short_dir,
                }
            return None

        dirs = [d for d in [short_dir, medium_dir, long_dir] if d != "中性"]
        if len(dirs) < 2:
            return None

        opposite = "看空" if overall_direction == "看多" else "看多"
        conflict_periods = []
        for period_key, period_dir in [("short", short_dir), ("medium", medium_dir), ("long", long_dir)]:
            if period_dir == opposite:
                conflict_periods.append(PERIOD_LABELS.get(period_key, period_key))

        if not conflict_periods:
            return None

        penalty = 0
        warning = ""

        if short_dir == opposite and long_dir == opposite:
            penalty = 15
            warning = f"短期与长期均{opposite}，与整体{overall_direction}方向严重矛盾"
        elif short_dir == opposite and medium_dir == opposite:
            penalty = 12
            warning = f"短期与中期均{opposite}，与整体{overall_direction}方向矛盾"
        elif len(conflict_periods) >= 2:
            penalty = 10
            warning = f"{'、'.join(conflict_periods)}与整体{overall_direction}方向矛盾"
        elif short_dir == opposite:
            penalty = 5
            warning = f"短期{opposite}与整体{overall_direction}方向不一致"
        elif long_dir == opposite:
            penalty = 5
            warning = f"长期{opposite}与整体{overall_direction}方向不一致"
        else:
            penalty = 3
            warning = f"{'、'.join(conflict_periods)}与整体方向略有分歧"

        return {"warning": warning, "penalty": penalty, "conflict_periods": conflict_periods}

    def _calc_period_trends(self, factors: Dict, weights: Dict, threshold: float) -> Dict:
        period_trends = {}
        for period, factor_keys in FACTOR_PERIOD_MAP.items():
            period_factors = {k: v for k, v in factors.items() if k in factor_keys}
            period_weights = {k: weights[k] for k in factor_keys if k in weights}

            active_period = {k: v for k, v in period_factors.items() if abs(v.get("score", 0)) >= 0.05 and k in period_weights}
            if not active_period:
                period_trends[period] = {
                    "direction": "中性",
                    "score": 0.0,
                    "confidence": 30,
                    "active_factors": 0,
                    "total_factors": len(factor_keys),
                    "label": PERIOD_LABELS[period],
                    "horizon": PERIOD_HORIZONS[period],
                }
                continue

            active_pw = {k: period_weights[k] for k in active_period}
            pw_total = sum(active_pw.values())
            if pw_total > 0:
                active_pw = {k: v / pw_total for k, v in active_pw.items()}

            period_score = sum(active_period[k]["score"] * active_pw[k] for k in active_period)

            period_mult = PERIOD_WEIGHT_MULTIPLIER.get(period, 1.0)
            period_score *= period_mult
            period_score = max(-1.0, min(1.0, period_score))

            bull_count = sum(1 for v in active_period.values() if v.get("score", 0) > 0)
            bear_count = sum(1 for v in active_period.values() if v.get("score", 0) < 0)
            total_active = len(active_period)

            if total_active >= 2 and bull_count != bear_count:
                majority = max(bull_count, bear_count)
                majority_ratio = majority / total_active
                min_majority = max(0.6, (total_active // 2 + 1) / total_active)
                if majority_ratio >= min_majority:
                    majority_dir = 1.0 if bull_count > bear_count else -1.0
                    boost = majority_ratio * 0.15
                    period_score = period_score + majority_dir * boost
                    period_score = max(-1.0, min(1.0, period_score))

            period_threshold = PERIOD_THRESHOLD.get(period, 0.03)

            if period_score > period_threshold:
                period_dir = "看多"
                period_conf = self._calibrate_confidence(period_score, "bullish")
            elif period_score < -period_threshold:
                period_dir = "看空"
                period_conf = self._calibrate_confidence(period_score, "bearish")
            else:
                period_dir = "中性"
                period_conf = self._calibrate_confidence(period_score, "neutral")

            if bull_count > bear_count and bear_count == 0 and total_active >= 2:
                period_conf = min(95, period_conf + 5)
            elif bear_count > bull_count and bull_count == 0 and total_active >= 2:
                period_conf = min(95, period_conf + 5)

            period_trends[period] = {
                "direction": period_dir,
                "score": round(period_score, 2),
                "confidence": period_conf,
                "active_factors": len(active_period),
                "total_factors": len(factor_keys),
                "label": PERIOD_LABELS[period],
                "horizon": PERIOD_HORIZONS[period],
            }

        return period_trends

    # ==================== 宏观维度 ====================

    def _percentile_score(self, value: float, history_key: str,
                         bullish_breakpoints: List[tuple],
                         bearish_breakpoints: List[tuple],
                         fallback_score: float = 0.0) -> tuple:
        """
        基于滚动分位数的动态评分
        当有历史数据时，用分位数确定阈值；否则回退到硬编码阈值
        bullish_breakpoints: [(percentile, score), ...] 从低到高
        bearish_breakpoints: [(percentile, score), ...] 从高到低
        返回 (score, used_percentile: bool)
        """
        historical = self._get_macro_history(history_key)
        if len(historical) < 60:
            return fallback_score, False

        sorted_vals = sorted(historical)
        n = len(sorted_vals)
        pct = 0.0
        for i, v in enumerate(sorted_vals):
            if v >= value:
                pct = i / n
                break
        else:
            pct = 1.0

        if pct <= 0.5:
            for bp_pct, bp_score in bullish_breakpoints:
                if pct <= bp_pct:
                    return bp_score, True
        else:
            for bp_pct, bp_score in bearish_breakpoints:
                if pct >= bp_pct:
                    return bp_score, True

        return 0.0, True

    def _get_macro_history(self, key: str) -> List[float]:
        try:
            from .db import get_macro_history
            history = get_macro_history(days=365)
            values = []
            for record in history:
                val = record.get("indicators", {}).get(key, {}).get("value")
                if val is not None:
                    try:
                        v = float(val)
                        if v == v:
                            values.append(v)
                    except (ValueError, TypeError):
                        pass
            return values
        except Exception:
            return []

    def _calc_real_rate(self) -> Dict:
        """
        F1. 实际利率因子（中期导向）
        核心逻辑：绝对水平 > 中期趋势 > 短期波动
        优先使用滚动分位数动态阈值，数据不足时回退到硬编码阈值
        """
        score = 0.0
        signals = []

        yield_data = self.macro_data.get("indicators", {}).get("us_10y_yield", {})
        yield_val = None
        yield_change = 0
        if yield_data and yield_data.get("value") is not None:
            try:
                yield_val = float(yield_data["value"])
                yield_change = float(yield_data.get("change", 0))
                if yield_val != yield_val:
                    yield_val = None
            except (ValueError, TypeError):
                yield_val = None

        if yield_val is not None:
            level_score, used_pct = self._percentile_score(
                yield_val, "us_10y_yield",
                bullish_breakpoints=[(0.20, 0.35), (0.35, 0.15)],
                bearish_breakpoints=[(0.80, -0.15), (0.90, -0.35)],
                fallback_score=0.0,
            )
            if used_pct:
                score += level_score
                if level_score > 0.15:
                    signals.append(f"美债10Y收益率{yield_val:.2f}%，处于历史低位(≤20%分位)，中期利多黄金")
                elif level_score > 0:
                    signals.append(f"美债10Y收益率{yield_val:.2f}%，低于中位数，中期偏利多")
                elif level_score < -0.15:
                    signals.append(f"美债10Y收益率{yield_val:.2f}%，处于历史高位(≥80%分位)，中期利空黄金")
                elif level_score < 0:
                    signals.append(f"美债10Y收益率{yield_val:.2f}%，高于中位数，中期偏利空")
                else:
                    signals.append(f"美债10Y收益率{yield_val:.2f}%，处于中性区间")
            else:
                if yield_val < 3.5:
                    score += 0.35
                    signals.append(f"美债10Y收益率{yield_val:.2f}%，绝对水平偏低，中期利多黄金")
                elif yield_val < 4.0:
                    score += 0.15
                    signals.append(f"美债10Y收益率{yield_val:.2f}%，中性偏低，中期偏利多")
                elif yield_val > 4.5:
                    score -= 0.35
                    signals.append(f"美债10Y收益率{yield_val:.2f}%，绝对水平偏高，中期利空黄金")
                elif yield_val > 4.2:
                    score -= 0.15
                    signals.append(f"美债10Y收益率{yield_val:.2f}%，中性偏高，中期偏利空")
                else:
                    signals.append(f"美债10Y收益率{yield_val:.2f}%，处于中性区间")

            if yield_change < -0.1:
                score += 0.15
                signals.append(f"日跌{abs(yield_change):.2f}bp，短期确认下行趋势")
            elif yield_change > 0.1:
                score -= 0.15
                signals.append(f"日涨{yield_change:.2f}bp，短期确认上行趋势")

        else:
            return {"score": 0.0, "signal": "无真实利率数据，实际利率因子暂不可用", "_no_data": True}

        score = max(-1.0, min(1.0, score))
        return {"score": score, "signal": "；".join(signals) if signals else "实际利率中性"}

    def _calc_dollar_factor(self) -> Dict:
        """
        F2. 美元因子（中期导向）
        核心逻辑：绝对水平 > 中期趋势 > 短期波动
        优先使用滚动分位数动态阈值，数据不足时回退到硬编码阈值
        """
        score = 0.0
        signals = []

        dxy_data = self.macro_data.get("indicators", {}).get("dxy", {})
        dxy_val = None
        dxy_change = 0
        if dxy_data and dxy_data.get("value") is not None:
            try:
                dxy_val = float(dxy_data["value"])
                dxy_change = float(dxy_data.get("change", 0))
                if dxy_val != dxy_val:
                    dxy_val = None
            except (ValueError, TypeError):
                dxy_val = None

        if dxy_val is not None:
            level_score, used_pct = self._percentile_score(
                dxy_val, "dxy",
                bullish_breakpoints=[(0.20, 0.30), (0.35, 0.10)],
                bearish_breakpoints=[(0.80, -0.10), (0.90, -0.30)],
                fallback_score=0.0,
            )
            if used_pct:
                score += level_score
                if level_score > 0.10:
                    signals.append(f"美元指数{dxy_val:.2f}，处于历史低位(≤20%分位)，中期利多黄金")
                elif level_score > 0:
                    signals.append(f"美元指数{dxy_val:.2f}，低于中位数，中期偏利多")
                elif level_score < -0.10:
                    signals.append(f"美元指数{dxy_val:.2f}，处于历史高位(≥80%分位)，中期利空黄金")
                elif level_score < 0:
                    signals.append(f"美元指数{dxy_val:.2f}，高于中位数，中期偏利空")
                else:
                    signals.append(f"美元指数{dxy_val:.2f}，处于中性区间")
            else:
                if dxy_val < 100:
                    score += 0.30
                    signals.append(f"美元指数{dxy_val:.2f}，绝对水平偏低，中期利多黄金")
                elif dxy_val < 103:
                    score += 0.10
                    signals.append(f"美元指数{dxy_val:.2f}，中性偏低，中期偏利多")
                elif dxy_val > 106:
                    score -= 0.30
                    signals.append(f"美元指数{dxy_val:.2f}，绝对水平偏高，中期利空黄金")
                elif dxy_val > 104:
                    score -= 0.10
                    signals.append(f"美元指数{dxy_val:.2f}，中性偏高，中期偏利空")
                else:
                    signals.append(f"美元指数{dxy_val:.2f}，处于中性区间")

            if dxy_change < -0.5:
                score += 0.15
                signals.append(f"日跌{abs(dxy_change):.2f}，短期确认走弱")
            elif dxy_change > 0.5:
                score -= 0.15
                signals.append(f"日涨{dxy_change:.2f}，短期确认走强")

        else:
            return {"score": 0.0, "signal": "无真实美元数据，美元因子暂不可用", "_no_data": True}

        score = max(-1.0, min(1.0, score))
        return {"score": score, "signal": "；".join(signals) if signals else "美元因子中性"}

    # ==================== 中观维度 ====================

    def _calc_momentum(self, today_data: Dict) -> Dict:
        """
        F3. 持仓动量因子
        分析机构净多头的近期变化趋势和加速度
        """
        score = 0.0
        signals = []

        if len(self.holdings_history) < 3:
            return {"score": 0.0, "signal": "历史数据不足", "_no_data": True}

        recent_changes = []
        for record in self.holdings_history[-5:]:
            total = sum(p.get("net_change", 0) for p in record.get("positions", []))
            recent_changes.append(total)

        recent_sum = sum(recent_changes)
        avg_change = recent_sum / len(recent_changes)

        if avg_change > 500:
            score += 0.5
            signals.append(f"机构日均加仓{avg_change:.0f}手，多头动量强劲")
        elif avg_change > 0:
            score += 0.2
            signals.append(f"机构日均小幅加仓{avg_change:.0f}手")
        elif avg_change < -500:
            score -= 0.5
            signals.append(f"机构日均减仓{abs(avg_change):.0f}手，空头动量强劲")
        elif avg_change < 0:
            score -= 0.2
            signals.append(f"机构日均小幅减仓{abs(avg_change):.0f}手")

        if len(recent_changes) >= 5:
            recent_3 = sum(recent_changes[-3:]) / 3
            earlier_2 = sum(recent_changes[:2]) / 2

            if earlier_2 > 0 and recent_3 < earlier_2 * 0.5:
                score -= 0.2
                signals.append("多头动量减速")
            elif earlier_2 < 0 and recent_3 > earlier_2 * 0.5:
                score += 0.2
                signals.append("空头动量减速")
            elif abs(recent_3) > abs(earlier_2) * 1.5 and earlier_2 != 0:
                if recent_3 > 0:
                    score += 0.2
                    signals.append("多头动量加速")
                else:
                    score -= 0.2
                    signals.append("空头动量加速")

        score = max(-1.0, min(1.0, score))
        return {"score": score, "signal": "；".join(signals) if signals else "持仓动量中性"}

    def _calc_extreme_position(self, today_data: Dict) -> Dict:
        """
        F4. 持仓极值因子（历史分位数 + 拥挤度检测）
        参考 CFTC 最佳实践：极值区 = 反转信号
        数据不足时衰减评分，避免短期分位数误导
        """
        score = 0.0
        signals = []

        if len(self.holdings_history) < 5:
            return {"score": 0.0, "signal": "历史数据不足（需≥5天）", "_no_data": True}

        current_net = sum(p.get("net", 0) for p in today_data.get("positions", []))

        historical_nets = []
        for record in self.holdings_history[:-1]:
            total = sum(p.get("net", 0) for p in record.get("positions", []))
            historical_nets.append(total)

        if not historical_nets:
            return {"score": 0.0, "signal": "无历史数据", "_no_data": True}

        sorted_nets = sorted(historical_nets)
        count_below = sum(1 for v in sorted_nets if v < current_net)
        percentile = count_below / len(sorted_nets) * 100

        data_sufficiency = min(1.0, len(historical_nets) / 20)
        if data_sufficiency < 1.0:
            signals.append(f"历史数据仅{len(historical_nets)}天（建议≥20天），分位数仅供参考")

        if percentile >= 90:
            score -= 0.5 * data_sufficiency
            signals.append(f"净多头处于历史{percentile:.0f}%分位，多头拥挤，反转风险大")
        elif percentile >= 75:
            score -= 0.2 * data_sufficiency
            signals.append(f"净多头处于历史{percentile:.0f}%分位，偏高需警惕")
        elif percentile <= 10:
            score += 0.5 * data_sufficiency
            if current_net > 0:
                signals.append(f"净多头处于历史{percentile:.0f}%分位，近期偏低但绝对值仍为正（{current_net:,}手），可能反弹")
            else:
                signals.append(f"净多头处于历史{percentile:.0f}%分位，极度看空，反转概率大")
        elif percentile <= 25:
            score += 0.2 * data_sufficiency
            signals.append(f"净多头处于历史{percentile:.0f}%分位，偏低可能反弹")

        recent_changes = []
        for record in self.holdings_history[-5:]:
            total = sum(p.get("net_change", 0) for p in record.get("positions", []))
            recent_changes.append(total)

        if recent_changes:
            same_dir = sum(1 for c in recent_changes if c * sum(recent_changes) > 0)
            if same_dir >= 4 and sum(recent_changes) > 0:
                score -= 0.2
                signals.append(f"近5日{same_dir}日同向加仓，拥挤度偏高")
            elif same_dir >= 4 and sum(recent_changes) < 0:
                score += 0.2
                signals.append(f"近5日{same_dir}日同向减仓，恐慌可能见底")

        score = max(-1.0, min(1.0, score))
        return {"score": score, "signal": "；".join(signals) if signals else "持仓极值中性"}

    def _calc_divergence(self) -> Dict:
        """
        F5. 持仓-价格背离因子
        机构加仓+价格跌 = 看多背离（聪明钱抄底）
        机构减仓+价格涨 = 看空背离（聪明钱出货）
        使用百分比变化统一量纲，避免绝对值与手数混合比较
        """
        score = 0.0
        signals = []

        if len(self.holdings_history) < 3 or len(self.gold_prices) < 3:
            return {"score": 0.0, "signal": "数据不足", "_no_data": True}

        holdings_by_date = {}
        holdings_pct_by_date = {}
        for record in self.holdings_history:
            d = record.get("date", "")
            if d:
                total = sum(p.get("net_change", 0) for p in record.get("positions", []))
                holdings_by_date[d] = total
                total_position = sum(abs(p.get("net", 0)) for p in record.get("positions", []))
                if total_position > 0:
                    holdings_pct_by_date[d] = total / total_position * 100
                else:
                    holdings_pct_by_date[d] = 0

        prices_by_date = {}
        price_pct_by_date = {}
        for i, p in enumerate(self.gold_prices):
            d = p.get("date", "")
            if d:
                prices_by_date[d] = p.get("change", 0)
                if i > 0 and self.gold_prices[i-1].get("close", 0) > 0:
                    price_pct_by_date[d] = (p["close"] - self.gold_prices[i-1]["close"]) / self.gold_prices[i-1]["close"] * 100
                else:
                    price_pct_by_date[d] = p.get("change", 0)

        common_dates = sorted(set(holdings_by_date.keys()) & set(prices_by_date.keys()))
        if len(common_dates) < 3:
            holdings_changes = [holdings_pct_by_date.get(d, 0) for d in sorted(holdings_pct_by_date.keys())[-5:]]
            price_changes = [price_pct_by_date.get(d, 0) for d in sorted(prices_by_date.keys())[-5:]]
        else:
            recent_dates = common_dates[-5:]
            holdings_changes = [holdings_pct_by_date.get(d, 0) for d in recent_dates]
            price_changes = [price_pct_by_date[d] for d in recent_dates]

        h_recent_pct = sum(holdings_changes[-3:])
        p_recent_pct = sum(price_changes[-3:])

        if h_recent_pct > 0.5 and p_recent_pct < -0.3:
            score += 0.6
            signals.append(f"看多背离：机构3日净加仓占比{h_recent_pct:.2f}%但金价跌{abs(p_recent_pct):.1f}%")
        elif h_recent_pct < -0.5 and p_recent_pct > 0.3:
            score -= 0.6
            signals.append(f"看空背离：机构3日净减仓占比{abs(h_recent_pct):.2f}%但金价涨{p_recent_pct:.1f}%")
        elif h_recent_pct > 0 and p_recent_pct > 0:
            score += 0.3
            signals.append("持仓与价格同向看多，趋势一致")
        elif h_recent_pct < 0 and p_recent_pct < 0:
            score -= 0.3
            signals.append("持仓与价格同向看空，趋势一致")

        if len(common_dates) >= 5:
            n = min(len(common_dates), 10)
            corr_dates = common_dates[-n:]
            h_corr = [holdings_pct_by_date.get(d, 0) for d in corr_dates]
            p_corr = [price_pct_by_date[d] for d in corr_dates]

            mean_h = sum(h_corr) / n
            mean_p = sum(p_corr) / n
            sum_xy = sum((h_corr[i] - mean_h) * (p_corr[i] - mean_p) for i in range(n))
            sum_xx = sum((x - mean_h) ** 2 for x in h_corr)
            sum_yy = sum((x - mean_p) ** 2 for x in p_corr)

            if sum_xx > 0 and sum_yy > 0:
                correlation = sum_xy / (sum_xx ** 0.5 * sum_yy ** 0.5)
                if correlation < -0.5:
                    if h_corr[-1] > 0:
                        score += 0.2
                        signals.append(f"持仓-价格负相关(r={correlation:.2f})，机构加仓预示反弹")
                    else:
                        score -= 0.2
                        signals.append(f"持仓-价格负相关(r={correlation:.2f})，机构减仓预示回调")
                elif correlation > 0.5:
                    signals.append(f"持仓-价格正相关(r={correlation:.2f})，趋势一致")

        score = max(-1.0, min(1.0, score))
        return {"score": score, "signal": "；".join(signals) if signals else "无背离信号"}

    # ==================== 微观维度 ====================

    def _calc_price_trend(self) -> Dict:
        """
        F6. 技术趋势因子（RSI + MACD + 均线 + 布林带）
        """
        score = 0.0
        signals = []

        if len(self.gold_prices) < 10:
            return {"score": 0.0, "signal": "金价数据不足", "_no_data": True}

        closes = [p["close"] for p in self.gold_prices]

        rsi = self._calc_rsi(closes, 14)
        if rsi is not None:
            if rsi > 70:
                score -= 0.3
                signals.append(f"RSI={rsi:.0f}超买区，回调风险")
            elif rsi > 50:
                score += 0.1
                signals.append(f"RSI={rsi:.0f}偏强")
            elif rsi < 30:
                score += 0.3
                signals.append(f"RSI={rsi:.0f}超卖区，反弹概率大")
            elif rsi < 40:
                score += 0.05
                signals.append(f"RSI={rsi:.0f}偏弱，接近超卖")

        macd_result = self._calc_macd(closes)
        if macd_result is not None:
            macd_val, signal_val, hist = macd_result
            prev_hist = self._calc_macd_hist_prev(closes)
            if prev_hist is not None and prev_hist <= 0 < hist:
                score += 0.25
                signals.append("MACD金叉，短期偏多")
            elif prev_hist is not None and prev_hist >= 0 > hist:
                score -= 0.25
                signals.append("MACD死叉，短期偏空")
            elif hist > 0:
                score += 0.1
                signals.append("MACD多头运行")
            elif hist < 0:
                score -= 0.1
                signals.append("MACD空头运行")

        if len(closes) >= 5:
            ma5 = sum(closes[-5:]) / 5
            current = closes[-1]
            if current > ma5 * 1.005:
                score += 0.15
                signals.append(f"金价在5日均线上方")
            elif current < ma5 * 0.995:
                score -= 0.15
                signals.append(f"金价在5日均线下方")

        if len(closes) >= 10:
            ma10 = sum(closes[-10:]) / 10
            ma10_prev = sum(closes[-11:-1]) / 10 if len(closes) >= 11 else ma10
            if ma10 > ma10_prev:
                score += 0.1
            elif ma10 < ma10_prev:
                score -= 0.1

        bb = self._calc_bollinger(closes, 20)
        if bb is not None:
            mid, upper, lower = bb
            current = closes[-1]
            if current > upper:
                score -= 0.15
                signals.append("突破布林上轨，超买")
            elif current < lower:
                score += 0.15
                signals.append("跌破布林下轨，超卖")
            bandwidth = (upper - lower) / mid * 100 if mid > 0 else 0
            if bandwidth < 2:
                signals.append(f"布林带收窄({bandwidth:.1f}%)，可能变盘")

        consecutive = 0
        for i in range(len(closes) - 1, 0, -1):
            if closes[i] > closes[i-1]:
                if consecutive >= 0: consecutive += 1
                else: break
            elif closes[i] < closes[i-1]:
                if consecutive <= 0: consecutive -= 1
                else: break
            else: break

        if consecutive >= 3:
            score -= 0.1
            signals.append(f"已连涨{consecutive}天，注意回调")
        elif consecutive <= -3:
            score += 0.1
            signals.append(f"已连跌{abs(consecutive)}天，可能反弹")

        score = max(-1.0, min(1.0, score))
        return {"score": score, "signal": "；".join(signals) if signals else "技术趋势中性"}

    def _calc_volatility(self) -> Dict:
        """
        F7. 波动率因子（ATR + 波动率状态 + 趋势方向）
        黄金波动率具有不对称性：避险驱动的高波动通常利多，趋势反转的高波动需结合方向判断
        """
        score = 0.0
        signals = []
        low_vol = False

        if len(self.gold_prices) < 5:
            return {"score": 0.0, "signal": "数据不足", "low_vol": False, "_no_data": True}

        closes = [p["close"] for p in self.gold_prices]

        atr = self._calc_atr(self.gold_prices, 14)
        if atr is not None and closes[-1] > 0:
            atr_pct = atr / closes[-1] * 100
            if atr_pct > 2.0:
                signals.append(f"ATR={atr_pct:.1f}%，高波动")
                if len(self.holdings_history) >= 1:
                    last = self.holdings_history[-1]
                    total_chg = sum(p.get("net_change", 0) for p in last.get("positions", []))
                    if total_chg > 0:
                        score += 0.2
                        signals.append("高波动+机构加仓，偏向看多")
                    elif total_chg < 0:
                        score -= 0.2
                        signals.append("高波动+机构减仓，偏向看空")
                    else:
                        score += 0.1
                        signals.append("高波动环境，黄金避险属性偏多")
                else:
                    score += 0.1
                    signals.append("高波动环境，黄金避险属性偏多")
            elif atr_pct < 0.8:
                signals.append(f"ATR={atr_pct:.1f}%，低波动，可能变盘")
                low_vol = True
            elif atr_pct > 1.5:
                if len(self.holdings_history) >= 1:
                    last = self.holdings_history[-1]
                    total_chg = sum(p.get("net_change", 0) for p in last.get("positions", []))
                    if total_chg > 0:
                        score += 0.1
                        signals.append("波动偏高+机构加仓，偏多")
                    elif total_chg < 0:
                        score -= 0.1
                        signals.append("波动偏高+机构减仓，偏空")

        daily_returns = [(closes[i] - closes[i-1]) / closes[i-1] * 100
                        for i in range(1, len(closes))]
        if daily_returns:
            avg_return = sum(daily_returns) / len(daily_returns)
            volatility = (sum((r - avg_return) ** 2 for r in daily_returns) / max(1, len(daily_returns) - 1)) ** 0.5
            if volatility > 1.5:
                if not signals:
                    signals.append(f"波动率偏高({volatility:.2f}%)")

        if len(self.holdings_history) >= 1:
            last = self.holdings_history[-1]
            total_chg = sum(p.get("net_change", 0) for p in last.get("positions", []))
            if abs(total_chg) >= 3000:
                signals.append(f"机构单日操作{total_chg:+,}手，极端信号")
                if total_chg > 0: score += 0.3
                else: score -= 0.3

        score = max(-1.0, min(1.0, score))
        return {"score": score, "signal": "；".join(signals) if signals else "波动率正常", "low_vol": low_vol}

    def _calc_news_sentiment(self) -> Dict:
        """
        F8. 新闻情绪因子
        """
        score = 0.0
        signals = []

        if not self.news_sentiment:
            return {"score": 0.0, "signal": "新闻数据暂无", "_no_data": True}

        sentiment_score = self.news_sentiment.get("sentiment_score", 0)
        bullish = self.news_sentiment.get("bullish_count", 0)
        bearish = self.news_sentiment.get("bearish_count", 0)
        total = bullish + bearish or 1

        if sentiment_score > 0.3:
            score += 0.5
            signals.append(f"新闻面偏多（利多{bullish}条 vs 利空{bearish}条）")
        elif sentiment_score > 0.1:
            score += 0.2
            signals.append(f"新闻面轻微偏多")
        elif sentiment_score < -0.3:
            score -= 0.5
            signals.append(f"新闻面偏空（利空{bearish}条 vs 利多{bullish}条）")
        elif sentiment_score < -0.1:
            score -= 0.2
            signals.append(f"新闻面轻微偏空")
        else:
            signals.append(f"新闻面中性")

        key_events = self.news_sentiment.get("key_events", [])
        for event in key_events:
            event_title = event.get("title", event) if isinstance(event, dict) else event
            event_lower = event_title.lower()
            if any(kw in event_lower for kw in ["降息", "rate cut", "鸽派", "dovish"]):
                score += 0.2
                signals.append(f"关键利好：{event_title[:30]}")
            elif any(kw in event_lower for kw in ["加息", "鹰派", "hawkish", "维持高利率"]):
                score -= 0.2
                signals.append(f"关键利空：{event_title[:30]}")
            elif any(kw in event_lower for kw in ["冲突", "战争", "地缘紧张", "conflict", "war"]):
                score += 0.15
                signals.append(f"地缘风险：{event_title[:30]}")

        if total >= 5:
            dominant = max(bullish, bearish)
            ratio = dominant / total
            if ratio >= 0.8:
                score += 0.1 if score > 0 else -0.1
                signals.append("新闻情绪高度一致，信号增强")

        confidence = self.news_sentiment.get("confidence", "medium")
        if confidence == "high":
            score += 0.05 if score > 0 else -0.05
            signals.append("LLM语义分析确认")

        score = max(-1.0, min(1.0, score))
        return {"score": score, "signal": "；".join(signals) if signals else "新闻情绪中性"}

    # ==================== 新增因子 ====================

    def _calc_inflation(self) -> Dict:
        """
        F9. 通胀预期因子（中期导向）
        核心逻辑：绝对水平 > 中期趋势 > 短期波动
        盈亏平衡通胀率绝对水平决定中期方向，日变化仅作辅助确认
        """
        score = 0.0
        signals = []

        bei_data = self.macro_data.get("indicators", {}).get("breakeven_inflation", {})
        if bei_data and bei_data.get("value") is not None:
            try:
                bei_val = float(bei_data["value"])
                bei_change = float(bei_data.get("change", 0))
                if bei_val != bei_val:
                    bei_val = None
            except (ValueError, TypeError):
                bei_val = None
        else:
            bei_val = None

        if bei_val is not None:
            if bei_val > 2.8:
                score += 0.30
                signals.append(f"盈亏平衡通胀率{bei_val:.2f}%，绝对水平偏高，中期利多黄金")
            elif bei_val > 2.4:
                score += 0.10
                signals.append(f"盈亏平衡通胀率{bei_val:.2f}%，中性偏高，中期偏利多")
            elif bei_val < 2.0:
                score -= 0.30
                signals.append(f"盈亏平衡通胀率{bei_val:.2f}%，绝对水平偏低，中期利空黄金")
            elif bei_val < 2.2:
                score -= 0.10
                signals.append(f"盈亏平衡通胀率{bei_val:.2f}%，中性偏低，中期偏利空")
            else:
                signals.append(f"盈亏平衡通胀率{bei_val:.2f}%，处于中性区间")

            if bei_change > 0.05:
                score += 0.15
                signals.append(f"日升{bei_change:+.2f}%，短期确认升温趋势")
            elif bei_change < -0.05:
                score -= 0.15
                signals.append(f"日降{bei_change:+.2f}%，短期确认降温趋势")
        else:
            oil_data = self.macro_data.get("indicators", {}).get("crude_oil", {})
            if oil_data and oil_data.get("change_pct") is not None:
                try:
                    oil_pct = float(oil_data["change_pct"])
                    if oil_pct > 5:
                        score += 0.10
                        signals.append(f"原油涨{oil_pct:.1f}%，隐含通胀预期升温（无TIPS数据，仅供参考）")
                    elif oil_pct < -5:
                        score -= 0.10
                        signals.append(f"原油跌{abs(oil_pct):.1f}%，隐含通胀预期降温（无TIPS数据，仅供参考）")
                except (ValueError, TypeError):
                    pass

            if not signals:
                return {"score": 0.0, "signal": "无TIPS数据，通胀预期因子暂不可用", "_no_data": True}

        score = max(-1.0, min(1.0, score))
        return {"score": score, "signal": "；".join(signals) if signals else "通胀预期中性"}

    def _calc_cb_gold(self) -> Dict:
        """
        F10. 央行购金因子
        全球央行是黄金最大买家之一，持续购金是长期结构性利多
        数据来源：新闻情绪中的央行购金关键词 + WGC季度数据
        """
        score = 0.0
        signals = []

        cb_buying_detected = False
        if self.news_sentiment:
            for event in self.news_sentiment.get("key_events", []):
                title = event.get("title", event) if isinstance(event, dict) else event
                t = title.lower()
                if any(kw in t for kw in ["央行购金", "购金", "gold reserve", "gold buying",
                                           "储备增加", "增持黄金", "gold holdings"]):
                    cb_buying_detected = True
                    score += 0.3
                    signals.append(f"央行购金信号：{title[:40]}")

            llm_summary = self.news_sentiment.get("llm_summary", "")
            if llm_summary:
                ls = llm_summary.lower()
                if any(kw in ls for kw in ["央行购金", "储备增加", "增持黄金"]):
                    if not cb_buying_detected:
                        cb_buying_detected = True
                        score += 0.2
                        signals.append("AI摘要提及央行购金动态")

        now = datetime.now()
        month = now.month
        q1_end_months = [3, 4, 5]
        q2_end_months = [6, 7, 8]
        q3_end_months = [9, 10, 11]
        q4_end_months = [12, 1, 2]

        if month in q1_end_months:
            signals.append("Q1为WGC央行购金数据发布期，关注上季度购金量")
        elif month in q3_end_months:
            signals.append("Q3为传统购金旺季（印度节日备货）")

        if not cb_buying_detected and not signals:
            signals.append("暂无最新央行购金新闻，央行购金因子暂为中性")

        score = max(-1.0, min(1.0, score))
        return {"score": score, "signal": "；".join(signals) if signals else "央行购金中性"}

    def _calc_etf_flow(self) -> Dict:
        """
        F11. ETF资金流因子
        优先使用ETF持仓量变化（吨数/shares outstanding），其次使用价格变化
        ETF持仓量增加 = 资金流入 = 看多；持仓量减少 = 资金流出 = 看空
        """
        score = 0.0
        signals = []

        gld_data = self.macro_data.get("indicators", {}).get("gld_etf", {})

        holdings_change = None
        if gld_data:
            hc = gld_data.get("holdings_change")
            if hc is not None:
                try:
                    holdings_change = float(hc)
                    if holdings_change != holdings_change:
                        holdings_change = None
                except (ValueError, TypeError):
                    holdings_change = None

        if holdings_change is not None:
            if holdings_change > 5:
                score += 0.3
                signals.append(f"GLD持仓增加{holdings_change:+.1f}吨，资金大幅流入")
            elif holdings_change > 1:
                score += 0.15
                signals.append(f"GLD持仓增加{holdings_change:+.1f}吨，资金小幅流入")
            elif holdings_change < -5:
                score -= 0.3
                signals.append(f"GLD持仓减少{abs(holdings_change):.1f}吨，资金大幅流出")
            elif holdings_change < -1:
                score -= 0.15
                signals.append(f"GLD持仓减少{abs(holdings_change):.1f}吨，资金小幅流出")
            else:
                signals.append(f"GLD持仓变化{holdings_change:+.1f}吨，资金流基本持平")
        else:
            gld_price = None
            gld_change_pct = 0
            if gld_data and gld_data.get("value") is not None:
                try:
                    gld_price = float(gld_data["value"])
                    gld_change_pct = float(gld_data.get("change_pct", 0))
                    if gld_price != gld_price:
                        gld_price = None
                except (ValueError, TypeError):
                    gld_price = None

            if gld_price is not None:
                if gld_change_pct > 1.5:
                    score += 0.3
                    signals.append(f"GLD ETF涨{gld_change_pct:+.1f}%，资金大幅流入黄金ETF(价格代理)")
                elif gld_change_pct > 0.5:
                    score += 0.15
                    signals.append(f"GLD ETF涨{gld_change_pct:+.1f}%，资金小幅流入(价格代理)")
                elif gld_change_pct < -1.5:
                    score -= 0.3
                    signals.append(f"GLD ETF跌{gld_change_pct:+.1f}%，资金大幅流出黄金ETF(价格代理)")
                elif gld_change_pct < -0.5:
                    score -= 0.15
                    signals.append(f"GLD ETF跌{gld_change_pct:+.1f}%，资金小幅流出(价格代理)")
                else:
                    signals.append(f"GLD ETF变动{gld_change_pct:+.1f}%，资金流基本持平")
            else:
                signals.append("GLD ETF数据暂不可用")
                score = max(-1.0, min(1.0, score))
                return {"score": score, "signal": "；".join(signals) if signals else "ETF资金流中性", "_no_data": True}

        if self.gold_prices and len(self.gold_prices) >= 2:
            gold_chg = (self.gold_prices[-1]["close"] - self.gold_prices[-2]["close"]) / self.gold_prices[-2]["close"] * 100
            etf_signal = holdings_change if holdings_change is not None else gld_change_pct if gld_price is not None else 0
            if etf_signal > 0 and gold_chg < -0.3:
                score += 0.1
                signals.append("ETF流入但金价下跌，逢低买入信号")
            elif etf_signal < 0 and gold_chg > 0.3:
                score -= 0.1
                signals.append("ETF流出但金价上涨，逢高卖出信号")

        score = max(-1.0, min(1.0, score))
        return {"score": score, "signal": "；".join(signals) if signals else "ETF资金流中性"}

    def _calc_seasonality(self, today_data: Dict = None) -> Dict:
        """
        F12. 季节性因子
        优先使用历史数据计算各月份平均收益率，数据不足时回退到硬编码
        参考：World Gold Council 季节性研究
        """
        score = 0.0
        signals = []

        if today_data and today_data.get("date"):
            try:
                date_str = today_data["date"]
                if len(date_str) >= 7:
                    month = int(date_str[5:7])
                    day = int(date_str[8:10]) if len(date_str) >= 10 else 1
                else:
                    now = datetime.now()
                    month = now.month
                    day = now.day
            except (ValueError, IndexError):
                now = datetime.now()
                month = now.month
                day = now.day
        else:
            now = datetime.now()
            month = now.month
            day = now.day

        empirical_score = self._calc_empirical_seasonality(month)
        if empirical_score is not None:
            score = empirical_score
            if score > 0.05:
                signals.append(f"历史{month}月平均偏多(经验评分{score:+.2f})")
            elif score < -0.05:
                signals.append(f"历史{month}月平均偏空(经验评分{score:+.2f})")
            else:
                signals.append(f"历史{month}月季节性中性(经验评分{score:+.2f})")
        else:
            seasonal_map = {
                1: (0.15, "1月中国春节备货需求，季节性偏多"),
                2: (0.10, "2月春节消费旺季，季节性偏多"),
                3: (-0.10, "3月春节后获利了结，季节性偏空"),
                4: (0.10, "4月印度婚礼季备货，季节性偏多"),
                5: (0.05, "5月印度婚礼季延续，季节性微多"),
                6: (-0.10, "6月传统淡季，季节性偏空"),
                7: (-0.05, "7月淡季延续，季节性微空"),
                8: (0.10, "8月印度节日+央行购金季，季节性偏多"),
                9: (0.10, "9月印度节日季+Q3央行购金数据发布，季节性偏多"),
                10: (0.10, "10月排灯节备货，季节性偏多"),
                11: (0.05, "11月年末布局开始，季节性微多"),
                12: (0.05, "12月年末调仓+来年布局，季节性微多"),
            }
            if month in seasonal_map:
                s, desc = seasonal_map[month]
                score = s
                signals.append(desc)

        if month == 1 and day <= 20:
            score += 0.05
            signals.append("春节前黄金消费高峰")
        elif month == 10 and day <= 20:
            score += 0.05
            signals.append("排灯节前黄金消费高峰")

        if month in [3, 6, 9, 12] and day >= 25:
            signals.append("月末/季末机构调仓，波动可能加大")

        if (month == 12 and day >= 28) or (month == 1 and day <= 3):
            score = 0.0
            signals.append("年末/年初流动性极低，季节性信号不可靠")

        score = max(-0.3, min(0.3, score))
        return {"score": score, "signal": "；".join(signals) if signals else "季节性中性"}

    def _calc_empirical_seasonality(self, month: int) -> Optional[float]:
        """
        基于历史金价数据计算各月份的经验季节性评分
        需要至少2年(24个月)的数据
        返回 None 表示数据不足
        """
        if len(self.gold_prices) < 60:
            return None

        monthly_returns = {}
        for i in range(1, len(self.gold_prices)):
            prev = self.gold_prices[i - 1]
            curr = self.gold_prices[i]
            try:
                date_str = curr.get("date", "")
                if len(date_str) < 7:
                    continue
                m = int(date_str[5:7])
                prev_close = float(prev.get("close", 0))
                curr_close = float(curr.get("close", 0))
                if prev_close <= 0 or curr_close <= 0:
                    continue
                ret = (curr_close - prev_close) / prev_close * 100
                if m not in monthly_returns:
                    monthly_returns[m] = []
                monthly_returns[m].append(ret)
            except (ValueError, TypeError):
                continue

        if month not in monthly_returns or len(monthly_returns[month]) < 2:
            return None

        total_months_with_data = sum(len(v) for v in monthly_returns.values())
        if total_months_with_data < 24:
            return None

        avg_ret = sum(monthly_returns[month]) / len(monthly_returns[month])
        all_avgs = [sum(v) / len(v) for v in monthly_returns.values() if len(v) >= 2]
        if not all_avgs:
            return None

        overall_avg = sum(all_avgs) / len(all_avgs)
        max_deviation = max(abs(a - overall_avg) for a in all_avgs)
        if max_deviation == 0:
            return 0.0

        normalized = (avg_ret - overall_avg) / max_deviation * 0.2
        return max(-0.3, min(0.3, normalized))

    # ==================== 置信度校准 ====================

    CONFIDENCE_CALIBRATION = {
        "bullish": [(0.08, 52), (0.15, 58), (0.25, 65), (0.40, 72), (0.60, 80), (0.80, 88), (1.00, 95)],
        "bearish": [(0.08, 52), (0.15, 58), (0.25, 65), (0.40, 72), (0.60, 80), (0.80, 88), (1.00, 95)],
        "neutral": [(0.00, 50), (0.03, 45), (0.05, 40), (0.07, 35), (0.08, 30)],
    }

    def _calibrate_confidence(self, score: float, direction: str) -> int:
        calibration_table = self.CONFIDENCE_CALIBRATION.get(direction, self.CONFIDENCE_CALIBRATION["neutral"])
        abs_score = abs(score)

        try:
            from .db import get_all_prediction_tracking
            tracking = get_all_prediction_tracking(days=365)
            verified = [r for r in tracking if r.get("verified") and r.get("prediction") not in ("中性",)]
            if len(verified) >= 30:
                score_buckets = {}
                for r in verified:
                    s = abs(r.get("score", 0))
                    correct = r.get("prediction") == r.get("actual_direction", "")
                    bucket = round(s * 5) / 5
                    if bucket not in score_buckets:
                        score_buckets[bucket] = {"correct": 0, "total": 0}
                    score_buckets[bucket]["total"] += 1
                    if correct:
                        score_buckets[bucket]["correct"] += 1

                if score_buckets:
                    empirical = []
                    for bucket in sorted(score_buckets.keys()):
                        stats = score_buckets[bucket]
                        if stats["total"] >= 3:
                            acc = stats["correct"] / stats["total"]
                            empirical.append((bucket, int(50 + acc * 45)))
                    if len(empirical) >= 3:
                        calibration_table = empirical
        except Exception:
            pass

        if abs_score <= calibration_table[0][0]:
            return calibration_table[0][1]

        for i in range(len(calibration_table) - 1):
            s1, c1 = calibration_table[i]
            s2, c2 = calibration_table[i + 1]
            if abs_score <= s2:
                if s2 == s1:
                    return c1
                ratio = (abs_score - s1) / (s2 - s1)
                return int(c1 + ratio * (c2 - c1))

        return calibration_table[-1][1]

    # ==================== 动态权重 ====================

    HIGH_CORRELATION_PAIRS = [
        ("real_rate", "inflation", 0.6),
        ("real_rate", "dollar", 0.5),
        ("momentum", "extreme", 0.5),
        ("momentum", "divergence", 0.4),
    ]

    def _adjust_for_collinearity(self, factors: Dict, weights: Dict) -> Dict:
        same_dir_pairs = []
        for f1, f2, threshold in self.HIGH_CORRELATION_PAIRS:
            s1 = factors.get(f1, {}).get("score", 0)
            s2 = factors.get(f2, {}).get("score", 0)
            if abs(s1) >= 0.1 and abs(s2) >= 0.1 and s1 * s2 > 0:
                same_dir_pairs.append((f1, f2))

        if not same_dir_pairs:
            return weights

        adjusted = dict(weights)
        for f1, f2 in same_dir_pairs:
            w1 = adjusted.get(f1, 0)
            w2 = adjusted.get(f2, 0)
            total_w = w1 + w2
            if total_w > 0:
                penalty = 0.75
                adjusted[f1] = w1 * penalty
                adjusted[f2] = w2 * penalty

        total = sum(adjusted.values())
        if total > 0:
            adjusted = {k: v / total for k, v in adjusted.items()}
        return adjusted

    def _dynamic_weights(self, factors: Dict) -> Dict:
        """
        动态权重：基准权重从设置文件读取，根据波动率自适应调整
        含多重共线性缓解：当高相关因子同向时自动衰减权重
        """
        base_weights = {
            "real_rate": 0.16,
            "dollar": 0.12,
            "inflation": 0.09,
            "momentum": 0.08,
            "extreme": 0.05,
            "divergence": 0.07,
            "cb_gold": 0.07,
            "etf_flow": 0.06,
            "price_trend": 0.10,
            "volatility": 0.06,
            "news_sentiment": 0.09,
            "seasonality": 0.05,
        }

        settings = load_json(os.path.join(_DATA_DIR, "web_settings.json"))
        if settings:
            key_map = {
                "w_real_rate": "real_rate", "w_dollar": "dollar",
                "w_inflation": "inflation",
                "w_momentum": "momentum", "w_extreme": "extreme",
                "w_divergence": "divergence",
                "w_cb_gold": "cb_gold", "w_etf_flow": "etf_flow",
                "w_price_trend": "price_trend", "w_volatility": "volatility",
                "w_news_sentiment": "news_sentiment",
                "w_seasonality": "seasonality",
            }
            for sk, wk in key_map.items():
                if settings.get(sk) is not None:
                    base_weights[wk] = float(settings[sk])

        weights = dict(base_weights)

        vol_score = abs(factors.get("volatility", {}).get("score", 0))
        low_vol = factors.get("volatility", {}).get("low_vol", False)

        if vol_score > 0.3:
            weights["momentum"] += 0.05
            weights["extreme"] += 0.03
            weights["real_rate"] -= 0.04
            weights["dollar"] -= 0.04
        elif low_vol:
            weights["real_rate"] += 0.04
            weights["dollar"] += 0.03
            weights["momentum"] -= 0.04
            weights["extreme"] -= 0.03

        extreme_score = abs(factors.get("extreme", {}).get("score", 0))
        if extreme_score > 0.3:
            weights["extreme"] += 0.05
            weights["momentum"] -= 0.03
            weights["price_trend"] -= 0.02

        weights = {k: max(0.01, v) for k, v in weights.items()}

        total = sum(weights.values())
        weights = {k: v / total for k, v in weights.items()}

        weights = self._adjust_for_collinearity(factors, weights)

        return weights

    # ==================== 技术指标计算 ====================

    @staticmethod
    def _calc_rsi(closes: List[float], period: int = 14) -> Optional[float]:
        """RSI 计算（Wilder EMA 方法）"""
        if len(closes) < period + 1:
            return None

        gains = []
        losses = []
        for i in range(1, len(closes)):
            change = closes[i] - closes[i-1]
            gains.append(max(0, change))
            losses.append(max(0, -change))

        if len(gains) < period:
            return None

        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi

    @staticmethod
    def _calc_macd(closes: List[float],
                   fast: int = 12, slow: int = 26, signal: int = 9) -> Optional[tuple]:
        """MACD 计算"""
        if len(closes) < slow + signal:
            return None

        def ema(data, period):
            multiplier = 2 / (period + 1)
            result = [data[0]]
            for price in data[1:]:
                result.append(price * multiplier + result[-1] * (1 - multiplier))
            return result

        ema_fast = ema(closes, fast)
        ema_slow = ema(closes, slow)
        macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]

        if len(macd_line) < signal:
            return None

        signal_line = ema(macd_line, signal)
        hist = macd_line[-1] - signal_line[-1]

        return (macd_line[-1], signal_line[-1], hist)

    @staticmethod
    def _calc_macd_hist_prev(closes: List[float],
                             fast: int = 12, slow: int = 26,
                             signal: int = 9) -> Optional[float]:
        """计算前一根MACD柱状图值，用于判断金叉/死叉"""
        if len(closes) < slow + signal + 1:
            return None

        def ema(data, period):
            multiplier = 2 / (period + 1)
            result = [data[0]]
            for price in data[1:]:
                result.append(price * multiplier + result[-1] * (1 - multiplier))
            return result

        ema_fast = ema(closes[:-1], fast)
        ema_slow = ema(closes[:-1], slow)
        macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]

        if len(macd_line) < signal:
            return None

        signal_line = ema(macd_line, signal)
        return macd_line[-1] - signal_line[-1]

    @staticmethod
    def _calc_bollinger(closes: List[float], period: int = 20,
                        num_std: float = 2.0) -> Optional[tuple]:
        """布林带计算（样本标准差）"""
        if len(closes) < period:
            return None

        recent = closes[-period:]
        mid = sum(recent) / period
        variance = sum((x - mid) ** 2 for x in recent) / (period - 1)
        std = variance ** 0.5

        return (mid, mid + num_std * std, mid - num_std * std)

    @staticmethod
    def _calc_atr(prices: List[Dict], period: int = 14) -> Optional[float]:
        """ATR 计算（Wilder 平滑法）"""
        if len(prices) < period + 1:
            return None

        true_ranges = []
        for i in range(1, len(prices)):
            high = prices[i].get("high", prices[i]["close"])
            low = prices[i].get("low", prices[i]["close"])
            prev_close = prices[i-1]["close"]

            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            true_ranges.append(tr)

        if len(true_ranges) < period:
            return None

        atr = sum(true_ranges[:period]) / period
        for i in range(period, len(true_ranges)):
            atr = (atr * (period - 1) + true_ranges[i]) / period
        return atr

    # ==================== 推理文本 ====================

    def _generate_reasoning(self, factors: Dict, weights: Dict,
                           score: float, direction: str,
                           period_trends: Dict = None,
                           period_conflict: Dict = None) -> str:
        """生成推理文本"""
        lines = []

        factor_names = {
            "real_rate": "实际利率",
            "dollar": "美元因子",
            "inflation": "通胀预期",
            "momentum": "持仓动量",
            "extreme": "持仓极值",
            "divergence": "背离信号",
            "cb_gold": "央行购金",
            "etf_flow": "ETF资金流",
            "price_trend": "技术趋势",
            "volatility": "波动率",
            "news_sentiment": "新闻情绪",
            "seasonality": "季节性",
        }

        for key, name in factor_names.items():
            f = factors.get(key, {})
            s = f.get("score", 0)
            if s > 0.2:
                lines.append(f"• {name}：偏多（{s:+.1f}）")
            elif s < -0.2:
                lines.append(f"• {name}：偏空（{s:+.1f}）")
            else:
                lines.append(f"• {name}：中性（{s:+.1f}）")

        if period_trends:
            lines.append("")
            for period_key in ["short", "medium", "long"]:
                pt = period_trends.get(period_key, {})
                if pt:
                    label = pt.get("label", PERIOD_LABELS.get(period_key, period_key))
                    pdir = pt.get("direction", "中性")
                    pscore = pt.get("score", 0)
                    horizon = pt.get("horizon", PERIOD_HORIZONS.get(period_key, 0))
                    pconf = pt.get("confidence", 0)
                    lines.append(f"▸ {label}趋势（{horizon}日）：{pdir}（评分{pscore:+.2f}，置信度{pconf}%）")

        if direction == "看多":
            lines.append(f"\n综合评分 {score:+.2f}，预测方向：{direction}")
            lines.append("核心逻辑：" + self._get_bull_logic(factors))
        elif direction == "看空":
            lines.append(f"\n综合评分 {score:+.2f}，预测方向：{direction}")
            lines.append("核心逻辑：" + self._get_bear_logic(factors))
        else:
            lines.append(f"\n综合评分 {score:+.2f}，预测方向：{direction}")
            lines.append("多空信号交织，建议观望为主")

        if period_conflict:
            lines.append(f"⚠️ {period_conflict['warning']}，置信度已下调")

        return "\n".join(lines)

    def _get_bull_logic(self, factors: Dict) -> str:
        parts = []
        if factors.get("real_rate", {}).get("score", 0) > 0.2:
            parts.append("实际利率下行")
        if factors.get("dollar", {}).get("score", 0) > 0.2:
            parts.append("美元偏弱")
        if factors.get("inflation", {}).get("score", 0) > 0.2:
            parts.append("通胀预期升温")
        if factors.get("momentum", {}).get("score", 0) > 0.2:
            parts.append("机构持续加仓")
        if factors.get("extreme", {}).get("score", 0) > 0.2:
            parts.append("持仓极低位可能反转")
        if factors.get("divergence", {}).get("score", 0) > 0.2:
            parts.append("持仓-价格看多背离")
        if factors.get("cb_gold", {}).get("score", 0) > 0.2:
            parts.append("央行购金")
        if factors.get("etf_flow", {}).get("score", 0) > 0.2:
            parts.append("ETF资金流入")
        if factors.get("price_trend", {}).get("score", 0) > 0.2:
            parts.append("技术面偏多")
        if factors.get("seasonality", {}).get("score", 0) > 0.1:
            parts.append("季节性偏多")
        return "，".join(parts) if parts else "多因子偏多"

    def _get_bear_logic(self, factors: Dict) -> str:
        parts = []
        if factors.get("real_rate", {}).get("score", 0) < -0.2:
            parts.append("实际利率上行")
        if factors.get("dollar", {}).get("score", 0) < -0.2:
            parts.append("美元偏强")
        if factors.get("inflation", {}).get("score", 0) < -0.2:
            parts.append("通胀预期降温")
        if factors.get("momentum", {}).get("score", 0) < -0.2:
            parts.append("机构持续减仓")
        if factors.get("extreme", {}).get("score", 0) < -0.2:
            parts.append("持仓极高位拥挤")
        if factors.get("divergence", {}).get("score", 0) < -0.2:
            parts.append("持仓-价格看空背离")
        if factors.get("etf_flow", {}).get("score", 0) < -0.2:
            parts.append("ETF资金流出")
        if factors.get("price_trend", {}).get("score", 0) < -0.2:
            parts.append("技术面偏空")
        if factors.get("seasonality", {}).get("score", 0) < -0.1:
            parts.append("季节性偏空")
        return "，".join(parts) if parts else "多因子偏空"
