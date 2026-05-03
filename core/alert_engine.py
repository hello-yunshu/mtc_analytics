# -*- coding: utf-8 -*-
"""
全维度警示引擎 - 十二维度警示信号体系

参考框架：
- 东证期货《黄金择时因子及多周期合成》(2025)
- CFTC持仓分析最佳实践
- 湍流增强黄金定价模型
- Bridgewater风险平价模型
- World Gold Council《Gold as a strategic asset》

十二维度警示体系：
  D1. 技术面警示 - RSI/MACD/布林带/均线/支撑阻力/价格-指标背离/趋势强度
  D2. 波动率警示 - ATR极端/骤变/低波动变盘/历史分位/单日极端
  D3. 宏观面警示 - 美债收益率/美元指数/VIX/原油/金油比/收益率曲线/实际利率
  D4. 关联数据警示 - 金价-美元/美债/原油关联性翻转/滚动相关/多资产共振/跨市场传导
  D5. 量价背离警示 - 持仓-价格背离/相关性反转/价格-指标背离
  D6. 情绪面警示 - 新闻极端/关键事件/情绪背离/央行购金/社交媒体/分析师共识
  D7. 持仓结构警示 - 机构操作/一致性/拥挤度
  D8. 日历事件警示 - FOMC/CPI/NFP/PPI/GDP/期权到期/季节性/央行会议
  D9. 交叉确认警示 - 多维度同向/维度矛盾
  D10. 极端风险警示 - 黑天鹅/系统性风险/流动性
  D11. 央行购金警示 - 央行购金动态/储备变化/WGC数据
  D12. ETF资金流警示 - GLD/IAU资金流入流出/持仓量变化
"""

from typing import Dict, List, Optional
import math
from datetime import datetime, timedelta

from .analyzer import LEVEL_CRITICAL, LEVEL_HIGH, LEVEL_MEDIUM, LEVEL_LOW, LEVEL_ORDER
from .utils import load_json


DIMENSION_LABEL = {
    "technical": "📐 技术面",
    "volatility": "🌊 波动率",
    "macro": "🏛️ 宏观面",
    "correlation": "🔄 关联数据",
    "divergence": "🔀 量价背离",
    "sentiment": "📰 情绪面",
    "position": "🏢 持仓结构",
    "calendar": "📅 日历事件",
    "cross": "🔗 交叉确认",
    "extreme": "⚠️ 极端风险",
    "cb_gold": "🏦 央行购金",
    "etf_flow": "📊 ETF资金流",
}

DIMENSION_ORDER = {
    "extreme": 0,
    "cross": 1,
    "calendar": 2,
    "correlation": 3,
    "position": 4,
    "divergence": 5,
    "technical": 6,
    "volatility": 7,
    "macro": 8,
    "sentiment": 9,
    "cb_gold": 10,
    "etf_flow": 11,
}

DISPLAY_DIMENSION_ORDER = [
    "extreme", "cross", "calendar", "correlation",
    "position", "divergence", "technical", "volatility",
    "macro", "sentiment", "cb_gold", "etf_flow",
]


class AlertEngine:

    def __init__(self, holdings_history: List[Dict], gold_prices: List[Dict],
                 macro_data: Optional[Dict] = None,
                 news_sentiment: Optional[Dict] = None,
                 prediction: Optional[Dict] = None,
                 support_resistance: Optional[Dict] = None,
                 enabled_dimensions: Optional[Dict] = None,
                 alert_threshold_large: int = 1000):
        self.holdings_history = holdings_history
        self.gold_prices = gold_prices
        self.macro_data = macro_data or {}
        self.news_sentiment = news_sentiment
        self.prediction = prediction
        self.support_resistance = support_resistance
        self.enabled_dimensions = enabled_dimensions or {
            "technical": True, "volatility": True, "macro": True,
            "correlation": True, "divergence": True, "sentiment": True,
            "position": True, "calendar": True, "cross": True, "extreme": True,
            "cb_gold": True, "etf_flow": True,
        }
        self.alert_threshold_large = alert_threshold_large

    def generate_all_alerts(self, today_data: Dict) -> List[Dict]:
        alerts = []
        dim_map = {
            "technical": self._detect_technical,
            "volatility": self._detect_volatility,
            "macro": self._detect_macro,
            "correlation": self._detect_correlation,
            "divergence": self._detect_divergence,
            "sentiment": self._detect_sentiment,
            "position": lambda: self._detect_position(today_data),
            "calendar": self._detect_calendar,
            "cross": self._detect_cross_dimension,
            "extreme": self._detect_extreme_risk,
            "cb_gold": self._detect_cb_gold,
            "etf_flow": self._detect_etf_flow,
        }
        for dim_key, detector in dim_map.items():
            if self.enabled_dimensions.get(dim_key, True):
                try:
                    alerts.extend(detector())
                except Exception:
                    pass

        seen = set()
        unique = []
        for a in alerts:
            key = f"{a.get('dimension', '')}_{a.get('type', '')}_{a.get('message', '')[:30]}"
            if key not in seen:
                seen.add(key)
                unique.append(a)

        unique.sort(key=lambda x: (DIMENSION_ORDER.get(x.get("dimension", ""), 99),
                                    LEVEL_ORDER.get(x["level"], 99)))
        return unique

    # ==================== D1. 技术面警示 ====================

    def _detect_technical(self) -> List[Dict]:
        alerts = []
        dim = "technical"
        if len(self.gold_prices) < 15:
            return alerts
        closes = [p["close"] for p in self.gold_prices]

        rsi = self._calc_rsi(closes, 14)
        if rsi is not None:
            if rsi > 85:
                alerts.append({"level": LEVEL_CRITICAL, "dimension": dim, "type": "rsi_extreme_overbought",
                               "message": f"RSI={rsi:.0f}极度超买，强烈反转信号！"})
            elif rsi > 70:
                alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "rsi_overbought",
                               "message": f"RSI={rsi:.0f}超买区，注意回调风险"})
            elif rsi > 60:
                alerts.append({"level": LEVEL_LOW, "dimension": dim, "type": "rsi_bullish",
                               "message": f"RSI={rsi:.0f}偏强，多头占优"})
            elif rsi < 15:
                alerts.append({"level": LEVEL_CRITICAL, "dimension": dim, "type": "rsi_extreme_oversold",
                               "message": f"RSI={rsi:.0f}极度超卖，强烈反弹信号！"})
            elif rsi < 30:
                alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "rsi_oversold",
                               "message": f"RSI={rsi:.0f}超卖区，可能反弹"})
            elif rsi < 40:
                alerts.append({"level": LEVEL_LOW, "dimension": dim, "type": "rsi_bearish",
                               "message": f"RSI={rsi:.0f}偏弱，空头占优"})

        if len(closes) >= 20 and rsi is not None:
            recent_high_idx = -1
            prev_high_idx = -1
            for i in range(len(closes) - 1, max(len(closes) - 20, 0), -1):
                if recent_high_idx == -1 and closes[i] >= closes[i - 1] and closes[i] >= closes[i + 1 if i + 1 < len(closes) else i]:
                    recent_high_idx = i
                elif recent_high_idx != -1 and prev_high_idx == -1 and closes[i] >= closes[i - 1] and (i == 0 or closes[i] >= closes[i - 1]):
                    prev_high_idx = i
                    break
            if recent_high_idx > 0 and prev_high_idx > 0:
                if closes[recent_high_idx] > closes[prev_high_idx]:
                    rsi_at_recent = self._calc_rsi(closes[:recent_high_idx + 1], 14)
                    rsi_at_prev = self._calc_rsi(closes[:prev_high_idx + 1], 14)
                    if rsi_at_recent and rsi_at_prev and rsi_at_recent < rsi_at_prev:
                        alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "price_rsi_bearish_divergence",
                                       "message": f"价格新高但RSI未创新高（{rsi_at_prev:.0f}→{rsi_at_recent:.0f}），看空背离"})
                elif closes[recent_high_idx] < closes[prev_high_idx]:
                    rsi_at_recent = self._calc_rsi(closes[:recent_high_idx + 1], 14)
                    rsi_at_prev = self._calc_rsi(closes[:prev_high_idx + 1], 14)
                    if rsi_at_recent and rsi_at_prev and rsi_at_recent > rsi_at_prev:
                        alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "price_rsi_bullish_divergence",
                                       "message": f"价格新低但RSI未创新低（{rsi_at_prev:.0f}→{rsi_at_recent:.0f}），看多背离"})

        if len(closes) >= 26:
            macd_result = self._calc_macd(closes)
            if macd_result is not None:
                macd_val, signal_val, hist = macd_result
                prev_hist = self._calc_macd_hist_prev(closes)
                if prev_hist is not None:
                    if prev_hist <= 0 and hist > 0:
                        alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "macd_golden_cross",
                                       "message": f"MACD金叉确认，短期偏多信号（柱状图{hist:+.2f}）"})
                    elif prev_hist >= 0 and hist < 0:
                        alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "macd_death_cross",
                                       "message": f"MACD死叉确认，短期偏空信号（柱状图{hist:+.2f}）"})

                if abs(hist) > 0 and len(closes) >= 30:
                    recent_hists = self._calc_recent_macd_hists(closes, 5)
                    if recent_hists and len(recent_hists) >= 3:
                        if all(h > 0 for h in recent_hists) and recent_hists[-1] < recent_hists[-2]:
                            alerts.append({"level": LEVEL_LOW, "dimension": dim, "type": "macd_momentum_fade_bull",
                                           "message": "MACD多头动能衰减，上涨力度减弱"})
                        elif all(h < 0 for h in recent_hists) and recent_hists[-1] > recent_hists[-2]:
                            alerts.append({"level": LEVEL_LOW, "dimension": dim, "type": "macd_momentum_fade_bear",
                                           "message": "MACD空头动能衰减，下跌力度减弱"})

        if len(closes) >= 20:
            bb = self._calc_bollinger(closes, 20)
            if bb is not None:
                mid, upper, lower = bb
                current = closes[-1]
                bandwidth = (upper - lower) / mid * 100 if mid > 0 else 0
                if current > upper:
                    alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "bollinger_breakout_up",
                                   "message": f"价格{current:.0f}突破布林上轨{upper:.0f}，超买信号"})
                elif current < lower:
                    alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "bollinger_breakout_down",
                                   "message": f"价格{current:.0f}跌破布林下轨{lower:.0f}，超卖信号"})
                if bandwidth < 1.5:
                    alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "bollinger_squeeze",
                                   "message": f"布林带极度收窄（带宽{bandwidth:.1f}%），变盘在即"})
                elif bandwidth < 3.0:
                    alerts.append({"level": LEVEL_LOW, "dimension": dim, "type": "bollinger_narrow",
                                   "message": f"布林带收窄（带宽{bandwidth:.1f}%），可能即将变盘"})

        if len(closes) >= 10:
            ma5 = sum(closes[-5:]) / 5
            ma10 = sum(closes[-10:]) / 10
            prev_ma5 = sum(closes[-6:-1]) / 5
            prev_ma10 = sum(closes[-11:-1]) / 10
            if prev_ma5 <= prev_ma10 and ma5 > ma10:
                alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "ma_golden_cross",
                               "message": f"MA5({ma5:.0f})上穿MA10({ma10:.0f})，短期均线金叉"})
            elif prev_ma5 >= prev_ma10 and ma5 < ma10:
                alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "ma_death_cross",
                               "message": f"MA5({ma5:.0f})下穿MA10({ma10:.0f})，短期均线死叉"})
            if len(closes) >= 20:
                ma20 = sum(closes[-20:]) / 20
                prev_ma10_2 = sum(closes[-12:-2]) / 10
                prev_ma20_2 = sum(closes[-22:-2]) / 20
                if prev_ma10_2 <= prev_ma20_2 and ma10 > ma20:
                    alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "ma10_ma20_golden",
                                   "message": f"MA10上穿MA20，中期均线金叉，趋势转多"})
                elif prev_ma10_2 >= prev_ma20_2 and ma10 < ma20:
                    alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "ma10_ma20_death",
                                   "message": f"MA10下穿MA20，中期均线死叉，趋势转空"})

        if self.support_resistance:
            current = closes[-1] if closes else 0
            if current > 0:
                for level in self.support_resistance.get("resistance", []):
                    val = level.get("value", 0)
                    if val > 0 and abs(current - val) / val < 0.005:
                        alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "price_near_resistance",
                                       "message": f"价格{current:.0f}逼近阻力位{level.get('level','')}{val:.0f}，关注突破"})
                        break
                for level in self.support_resistance.get("support", []):
                    val = level.get("value", 0)
                    if val > 0 and abs(current - val) / val < 0.005:
                        alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "price_near_support",
                                       "message": f"价格{current:.0f}逼近支撑位{level.get('level','')}{val:.0f}，关注破位"})
                        break

        consecutive = 0
        for i in range(len(closes) - 1, 0, -1):
            if closes[i] > closes[i - 1]:
                if consecutive >= 0:
                    consecutive += 1
                else:
                    break
            elif closes[i] < closes[i - 1]:
                if consecutive <= 0:
                    consecutive -= 1
                else:
                    break
            else:
                break
        if consecutive >= 5:
            alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "consecutive_rise",
                           "message": f"已连涨{consecutive}天，短期严重超买，回调风险极大"})
        elif consecutive >= 3:
            alerts.append({"level": LEVEL_LOW, "dimension": dim, "type": "consecutive_rise_mild",
                           "message": f"已连涨{consecutive}天，注意回调"})
        elif consecutive <= -5:
            alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "consecutive_fall",
                           "message": f"已连跌{abs(consecutive)}天，短期严重超卖，反弹概率大"})
        elif consecutive <= -3:
            alerts.append({"level": LEVEL_LOW, "dimension": dim, "type": "consecutive_fall_mild",
                           "message": f"已连跌{abs(consecutive)}天，可能反弹"})

        if len(self.gold_prices) >= 2:
            latest = self.gold_prices[-1]
            if latest.get("high") and latest.get("low") and latest.get("close"):
                intraday_range = latest["high"] - latest["low"]
                intraday_pct = intraday_range / latest["close"] * 100 if latest["close"] > 0 else 0
                if intraday_pct > 3.0:
                    alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "intraday_extreme_range",
                                   "message": f"日内波幅{intraday_pct:.1f}%（{latest['high']:.0f}-{latest['low']:.0f}），极端波动"})
                elif intraday_pct > 2.0:
                    alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "intraday_large_range",
                                   "message": f"日内波幅{intraday_pct:.1f}%，波动较大"})

        return alerts

    # ==================== D2. 波动率警示 ====================

    def _detect_volatility(self) -> List[Dict]:
        alerts = []
        dim = "volatility"
        if len(self.gold_prices) < 15:
            return alerts
        closes = [p["close"] for p in self.gold_prices]

        atr = self._calc_atr(self.gold_prices, 14)
        if atr is not None and closes[-1] > 0:
            atr_pct = atr / closes[-1] * 100
            if atr_pct > 3.0:
                alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "atr_extreme_high",
                               "message": f"ATR={atr_pct:.1f}%，极端高波动，市场剧烈震荡"})
            elif atr_pct > 2.0:
                alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "atr_high",
                               "message": f"ATR={atr_pct:.1f}%，高波动状态，注意风险控制"})
            elif atr_pct > 1.5:
                alerts.append({"level": LEVEL_LOW, "dimension": dim, "type": "atr_elevated",
                               "message": f"ATR={atr_pct:.1f}%，波动偏高"})
            if atr_pct < 0.5:
                alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "atr_extreme_low",
                               "message": f"ATR={atr_pct:.1f}%，极度低波动，大级别变盘临近"})
            elif atr_pct < 0.8:
                alerts.append({"level": LEVEL_LOW, "dimension": dim, "type": "atr_low",
                               "message": f"ATR={atr_pct:.1f}%，低波动，可能酝酿变盘"})

        if len(self.gold_prices) >= 30:
            atr_current = self._calc_atr(self.gold_prices[-15:], 14)
            atr_prev = self._calc_atr(self.gold_prices[-30:-15], 14)
            if atr_current and atr_prev and atr_prev > 0:
                atr_change = (atr_current - atr_prev) / atr_prev * 100
                if atr_change > 80:
                    alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "atr_surge",
                                   "message": f"ATR较前期飙升{atr_change:.0f}%，波动率急剧放大"})
                elif atr_change > 40:
                    alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "atr_rise",
                                   "message": f"ATR较前期上升{atr_change:.0f}%，波动率增加"})
                elif atr_change < -40:
                    alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "atr_collapse",
                                   "message": f"ATR较前期下降{abs(atr_change):.0f}%，波动率收缩"})

        daily_returns = [(closes[i] - closes[i - 1]) / closes[i - 1] * 100
                         for i in range(1, len(closes))]
        if len(daily_returns) >= 10:
            recent_vol = (sum(r ** 2 for r in daily_returns[-5:]) / 5) ** 0.5
            earlier_vol = (sum(r ** 2 for r in daily_returns[-10:-5]) / 5) ** 0.5
            if earlier_vol > 0:
                vol_ratio = recent_vol / earlier_vol
                if vol_ratio > 2.0:
                    alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "vol_regime_shift_high",
                                   "message": f"波动率状态切换：近5日波动是前5日的{vol_ratio:.1f}倍，进入高波动模式"})
                elif vol_ratio < 0.5:
                    alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "vol_regime_shift_low",
                                   "message": f"波动率状态切换：近5日波动仅为前5日的{vol_ratio:.1f}倍，进入低波动模式"})

        if len(daily_returns) >= 20:
            all_vols = []
            for i in range(0, len(daily_returns) - 4, 5):
                chunk = daily_returns[i:i + 5]
                if len(chunk) >= 3:
                    all_vols.append((sum(r ** 2 for r in chunk) / len(chunk)) ** 0.5)
            if len(all_vols) >= 3:
                current_vol = all_vols[-1]
                sorted_vols = sorted(all_vols)
                rank = 0
                for i, v in enumerate(sorted_vols):
                    if v >= current_vol:
                        rank = i
                        break
                else:
                    rank = len(sorted_vols)
                percentile = rank / len(sorted_vols) * 100
                if percentile >= 90:
                    alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "vol_percentile_high",
                                   "message": f"当前波动率处于近期{percentile:.0f}%分位，极端高波动"})
                elif percentile <= 10:
                    alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "vol_percentile_low",
                                   "message": f"当前波动率处于近期{percentile:.0f}%分位，极度低波动"})

        if daily_returns:
            latest_ret = daily_returns[-1]
            if abs(latest_ret) > 3.0:
                alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "daily_extreme_move",
                               "message": f"单日{'暴涨' if latest_ret > 0 else '暴跌'}{abs(latest_ret):.1f}%，极端行情"})
            elif abs(latest_ret) > 2.0:
                alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "daily_large_move",
                               "message": f"单日{'大涨' if latest_ret > 0 else '大跌'}{abs(latest_ret):.1f}%"})

        return alerts

    # ==================== D3. 宏观面警示 ====================

    def _detect_macro(self) -> List[Dict]:
        alerts = []
        dim = "macro"
        indicators = self.macro_data.get("indicators", {})

        yield_10y = indicators.get("us_10y_yield", {})
        if yield_10y and yield_10y.get("value") is not None:
            try:
                yv = float(yield_10y["value"])
                yc = float(yield_10y.get("change", 0))
                if yv == yv:
                    if yc > 0.15:
                        alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "yield_sharp_rise",
                                       "message": f"美债10Y收益率日涨{yc:.2f}%至{yv:.2f}%，利率大幅上行利空黄金"})
                    elif yc > 0.05:
                        alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "yield_rise",
                                       "message": f"美债10Y收益率涨{yc:+.2f}%至{yv:.2f}%，利率上行压力"})
                    elif yc < -0.15:
                        alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "yield_sharp_drop",
                                       "message": f"美债10Y收益率日跌{abs(yc):.2f}%至{yv:.2f}%，利率下行利多黄金"})
                    elif yc < -0.05:
                        alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "yield_drop",
                                       "message": f"美债10Y收益率跌{yc:+.2f}%至{yv:.2f}%，利率下行支撑"})
                    if yv > 4.8:
                        alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "yield_extreme_high",
                                       "message": f"美债10Y收益率{yv:.2f}%极端高位，黄金严重承压"})
                    elif yv > 4.5:
                        alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "yield_high",
                                       "message": f"美债10Y收益率{yv:.2f}%偏高，黄金承压"})
                    elif yv < 3.5:
                        alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "yield_extreme_low",
                                       "message": f"美债10Y收益率{yv:.2f}%极端低位，黄金获强力支撑"})
                    elif yv < 4.0:
                        alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "yield_low",
                                       "message": f"美债10Y收益率{yv:.2f}%偏低，黄金获支撑"})

                    yv_pct = self._macro_percentile("us_10y_yield", yv)
                    if yv_pct is not None:
                        if yv_pct >= 95:
                            alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "yield_pct_extreme_high",
                                           "message": f"美债10Y收益率处于历史{yv_pct:.0f}%分位，极端高位"})
                        elif yv_pct <= 5:
                            alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "yield_pct_extreme_low",
                                           "message": f"美债10Y收益率处于历史{yv_pct:.0f}%分位，极端低位"})
            except (ValueError, TypeError):
                pass

        yield_5y = indicators.get("us_5y_yield", {})
        yield_2y = indicators.get("us_2y_yield", {})
        if yield_5y.get("value") is not None and yield_10y.get("value") is not None:
            try:
                y5 = float(yield_5y["value"])
                y10 = float(yield_10y["value"])
                spread = y10 - y5
                if spread < 0:
                    alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "yield_curve_inversion",
                                   "message": f"收益率曲线倒挂！10Y-5Y利差{spread:.2f}%，经济衰退信号利多黄金"})
                elif spread < 0.2:
                    alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "yield_curve_flat",
                                   "message": f"收益率曲线平坦化（10Y-5Y利差仅{spread:.2f}%），关注衰退风险"})
            except (ValueError, TypeError):
                pass
        if yield_2y.get("value") is not None and yield_10y.get("value") is not None:
            try:
                y2 = float(yield_2y["value"])
                y10 = float(yield_10y["value"])
                spread_2y = y10 - y2
                if spread_2y < -0.5:
                    alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "deep_inversion",
                                   "message": f"深度倒挂！10Y-2Y利差{spread_2y:.2f}%，强烈衰退信号"})
            except (ValueError, TypeError):
                pass

        dxy_data = indicators.get("dxy", {})
        if dxy_data and dxy_data.get("value") is not None:
            try:
                dv = float(dxy_data["value"])
                dc = float(dxy_data.get("change", 0))
                if dv == dv:
                    if dc > 0.8:
                        alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "dxy_sharp_rise",
                                       "message": f"美元指数日涨{dc:.2f}至{dv:.2f}，大幅走强利空黄金"})
                    elif dc > 0.3:
                        alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "dxy_rise",
                                       "message": f"美元指数涨{dc:+.2f}至{dv:.2f}，走强压力"})
                    elif dc < -0.8:
                        alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "dxy_sharp_drop",
                                       "message": f"美元指数日跌{abs(dc):.2f}至{dv:.2f}，大幅走弱利多黄金"})
                    elif dc < -0.3:
                        alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "dxy_drop",
                                       "message": f"美元指数跌{dc:+.2f}至{dv:.2f}，走弱支撑"})
                    if dv > 110:
                        alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "dxy_extreme_high",
                                       "message": f"美元指数{dv:.2f}极端高位，黄金严重承压"})
                    elif dv < 95:
                        alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "dxy_extreme_low",
                                       "message": f"美元指数{dv:.2f}极端低位，黄金获强力支撑"})
            except (ValueError, TypeError):
                pass

        vix_data = indicators.get("vix", {})
        if vix_data and vix_data.get("value") is not None:
            try:
                vv = float(vix_data["value"])
                vc = float(vix_data.get("change", 0))
                if vv == vv:
                    if vv > 35:
                        alerts.append({"level": LEVEL_CRITICAL, "dimension": dim, "type": "vix_panic",
                                       "message": f"VIX={vv:.1f}极度恐慌！避险需求激增利多黄金"})
                    elif vv > 25:
                        alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "vix_high",
                                       "message": f"VIX={vv:.1f}恐慌升温，避险需求增加"})
                    elif vv > 20:
                        alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "vix_elevated",
                                       "message": f"VIX={vv:.1f}偏高，市场波动加大"})
                    elif vv < 13:
                        alerts.append({"level": LEVEL_LOW, "dimension": dim, "type": "vix_complacent",
                                       "message": f"VIX={vv:.1f}极度低迷，市场过度乐观需警惕"})
                    if vc > 8:
                        alerts.append({"level": LEVEL_CRITICAL, "dimension": dim, "type": "vix_surge",
                                       "message": f"VIX单日飙升{vc:.1f}点！恐慌急剧升温"})
                    elif vc > 5:
                        alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "vix_rise",
                                       "message": f"VIX涨{vc:.1f}点，恐慌情绪上升"})
                    elif vc > 3:
                        alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "vix_rise_mild",
                                       "message": f"VIX涨{vc:.1f}点，市场波动加大"})

                    vix_pct = self._macro_percentile("vix", vv)
                    if vix_pct is not None:
                        if vix_pct >= 95:
                            alerts.append({"level": LEVEL_CRITICAL, "dimension": dim, "type": "vix_pct_extreme",
                                           "message": f"VIX处于历史{vix_pct:.0f}%分位，极端恐慌水平"})
                        elif vix_pct <= 5:
                            alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "vix_pct_complacent",
                                           "message": f"VIX处于历史{vix_pct:.0f}%分位，过度乐观需警惕"})
            except (ValueError, TypeError):
                pass

        oil_data = indicators.get("crude_oil", {})
        if oil_data and oil_data.get("value") is not None:
            try:
                ov = float(oil_data["value"])
                oc_pct = float(oil_data.get("change_pct", 0))
                if ov == ov and ov > 0:
                    if self.gold_prices:
                        gold_price = self.gold_prices[-1]["close"]
                        gold_oil_ratio = gold_price / ov
                        if gold_oil_ratio > 35:
                            alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "gold_oil_ratio_high",
                                           "message": f"金油比={gold_oil_ratio:.1f}（金价{gold_price:.0f}/油{ov:.0f}），极端高位，黄金相对原油严重高估或原油暴跌"})
                        elif gold_oil_ratio > 28:
                            alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "gold_oil_ratio_elevated",
                                           "message": f"金油比={gold_oil_ratio:.1f}偏高，黄金相对原油偏贵"})
                        elif gold_oil_ratio < 15:
                            alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "gold_oil_ratio_low",
                                           "message": f"金油比={gold_oil_ratio:.1f}极端低位，黄金相对原油便宜或原油暴涨"})
                        elif gold_oil_ratio < 20:
                            alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "gold_oil_ratio_low_mild",
                                           "message": f"金油比={gold_oil_ratio:.1f}偏低，黄金相对原油便宜"})

                    if abs(oc_pct) > 5:
                        direction = "暴涨" if oc_pct > 0 else "暴跌"
                        alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "oil_extreme_move",
                                       "message": f"WTI原油{direction}{abs(oc_pct):.1f}%至${ov:.1f}，通胀预期{'升温' if oc_pct > 0 else '降温'}"})
                    elif abs(oc_pct) > 3:
                        direction = "大涨" if oc_pct > 0 else "大跌"
                        alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "oil_large_move",
                                       "message": f"WTI原油{direction}{abs(oc_pct):.1f}%至${ov:.1f}"})
            except (ValueError, TypeError):
                pass

        return alerts

    # ==================== D4. 关联数据警示 ====================

    def _detect_correlation(self) -> List[Dict]:
        alerts = []
        dim = "correlation"
        indicators = self.macro_data.get("indicators", {})

        if not self.gold_prices or len(self.gold_prices) < 2:
            return alerts

        gold_chg_pct = 0
        if len(self.gold_prices) >= 2:
            gold_chg_pct = (self.gold_prices[-1]["close"] - self.gold_prices[-2]["close"]) / self.gold_prices[-2]["close"] * 100

        if len(self.gold_prices) >= 10:
            rolling_correlations = self._calc_rolling_correlations()
            for pair_name, corr_info in rolling_correlations.items():
                if corr_info.get("flip_detected"):
                    alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": f"corr_flip_{pair_name}",
                                   "message": f"{pair_name}相关性翻转：5日相关{corr_info['short_corr']:+.2f} vs 20日相关{corr_info['long_corr']:+.2f}，结构性变化信号"})

        dxy_data = indicators.get("dxy", {})
        if dxy_data and dxy_data.get("change_pct") is not None:
            try:
                dxy_pct = float(dxy_data["change_pct"])
                dxy_val = float(dxy_data.get("value", 0))
                if gold_chg_pct > 0.5 and dxy_pct > 0.3:
                    alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "gold_dxy_same_up",
                                   "message": f"金价涨{gold_chg_pct:.1f}%同时美元涨{dxy_pct:.1f}%，传统负相关被打破，异常信号"})
                elif gold_chg_pct < -0.5 and dxy_pct < -0.3:
                    alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "gold_dxy_same_down",
                                   "message": f"金价跌{gold_chg_pct:.1f}%同时美元跌{dxy_pct:.1f}%，传统负相关被打破，异常信号"})
                elif gold_chg_pct > 0.3 and dxy_pct < -0.2:
                    alerts.append({"level": LEVEL_LOW, "dimension": dim, "type": "gold_dxy_normal_negative",
                                   "message": f"金价涨+美元跌，传统负相关正常运作，趋势确认"})
                elif gold_chg_pct < -0.3 and dxy_pct > 0.2:
                    alerts.append({"level": LEVEL_LOW, "dimension": dim, "type": "gold_dxy_normal_negative_bear",
                                   "message": f"金价跌+美元涨，传统负相关正常运作，空头趋势确认"})

                if dxy_val > 0 and self.gold_prices:
                    gp = self.gold_prices[-1]["close"]
                    if gp > 0:
                        ratio = gp / dxy_val
                        if len(self.gold_prices) >= 5:
                            prev_gp = self.gold_prices[-2]["close"]
                            prev_dxy = float(dxy_data.get("value", dxy_val)) - float(dxy_data.get("change", 0))
                            if prev_dxy > 0 and prev_gp > 0:
                                prev_ratio = prev_gp / prev_dxy
                                ratio_chg = (ratio - prev_ratio) / prev_ratio * 100
                                if abs(ratio_chg) > 3:
                                    direction = "扩大" if ratio_chg > 0 else "收窄"
                                    alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "gold_dxy_ratio_shift",
                                                   "message": f"金价/美元比值{direction}{abs(ratio_chg):.1f}%，相对定价偏移"})
            except (ValueError, TypeError):
                pass

        yield_data = indicators.get("us_10y_yield", {})
        if yield_data and yield_data.get("change") is not None:
            try:
                yc = float(yield_data["change"])
                yv = float(yield_data.get("value", 0))
                if gold_chg_pct > 0.5 and yc > 0.05:
                    alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "gold_yield_same_up",
                                   "message": f"金价涨{gold_chg_pct:.1f}%同时美债收益率涨{yc:+.2f}%，传统负相关被打破"})
                elif gold_chg_pct < -0.5 and yc < -0.05:
                    alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "gold_yield_same_down",
                                   "message": f"金价跌{gold_chg_pct:.1f}%同时美债收益率跌{yc:+.2f}%，传统负相关被打破"})

                if yv > 0 and self.gold_prices:
                    gp = self.gold_prices[-1]["close"]
                    if gp > 0 and yv > 0:
                        real_yield_proxy = yv - 2.0
                        if real_yield_proxy > 2.5:
                            alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "real_yield_extreme_high",
                                           "message": f"隐含实际利率约{real_yield_proxy:.1f}%极端高位，黄金严重承压"})
                        elif real_yield_proxy > 2.0:
                            alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "real_yield_high",
                                           "message": f"隐含实际利率约{real_yield_proxy:.1f}%偏高，黄金承压"})
                        elif real_yield_proxy < 0:
                            alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "real_yield_negative",
                                           "message": f"隐含实际利率约{real_yield_proxy:.1f}%为负，黄金获强力支撑"})
                        elif real_yield_proxy < 0.5:
                            alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "real_yield_low",
                                           "message": f"隐含实际利率约{real_yield_proxy:.1f}%偏低，黄金获支撑"})
            except (ValueError, TypeError):
                pass

        oil_data = indicators.get("crude_oil", {})
        if oil_data and oil_data.get("change_pct") is not None:
            try:
                oil_pct = float(oil_data["change_pct"])
                oil_val = float(oil_data.get("value", 0))
                if abs(gold_chg_pct) > 0.3 and abs(oil_pct) > 2:
                    if gold_chg_pct > 0 and oil_pct < -3:
                        alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "gold_up_oil_crash",
                                       "message": f"金价涨{gold_chg_pct:.1f}%但原油暴跌{oil_pct:.1f}%，避险需求主导"})
                    elif gold_chg_pct < 0 and oil_pct > 3:
                        alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "gold_down_oil_surge",
                                       "message": f"金价跌{gold_chg_pct:.1f}%但原油暴涨{oil_pct:.1f}%，风险偏好主导"})

                if oil_val > 0 and self.gold_prices:
                    gp = self.gold_prices[-1]["close"]
                    if gp > 0:
                        go_ratio = gp / oil_val
                        if go_ratio > 35:
                            alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "gold_oil_decouple_high",
                                           "message": f"金油比{go_ratio:.1f}极端高位，黄金与原油严重脱钩，危机模式"})
                        elif go_ratio < 15:
                            alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "gold_oil_decouple_low",
                                           "message": f"金油比{go_ratio:.1f}极端低位，通胀交易主导"})
            except (ValueError, TypeError):
                pass

        vix_data = indicators.get("vix", {})
        if vix_data and vix_data.get("value") is not None:
            try:
                vix_val = float(vix_data["value"])
                vix_chg = float(vix_data.get("change", 0))
                if vix_val > 25 and gold_chg_pct < -1.0:
                    alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "vix_high_gold_fall",
                                   "message": f"VIX={vix_val:.0f}恐慌但金价跌{abs(gold_chg_pct):.1f}%，黄金避险属性失效，流动性危机信号"})
                elif vix_val > 20 and gold_chg_pct > 0.5:
                    alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "vix_high_gold_rise",
                                   "message": f"VIX={vix_val:.0f}恐慌+金价涨{gold_chg_pct:.1f}%，避险需求推升黄金"})
                elif vix_chg > 3 and gold_chg_pct > 0:
                    alerts.append({"level": LEVEL_LOW, "dimension": dim, "type": "vix_surge_gold_safe",
                                   "message": f"VIX急升{vix_chg:.1f}点+金价涨，恐慌中黄金发挥避险功能"})
            except (ValueError, TypeError):
                pass

        if len(self.gold_prices) >= 10:
            closes = [p["close"] for p in self.gold_prices]
            dxy_vals = []
            for p in self.gold_prices:
                d = p.get("date", "")
                dxy_vals.append(None)

            if dxy_data and dxy_data.get("value") is not None:
                try:
                    dxy_current = float(dxy_data["value"])
                    dxy_change = float(dxy_data.get("change", 0))
                    dxy_prev = dxy_current - dxy_change
                    if len(closes) >= 5 and dxy_prev > 0:
                        gold_5d = closes[-5:]
                        gold_trend = (gold_5d[-1] - gold_5d[0]) / gold_5d[0] * 100
                        dxy_trend = (dxy_current - dxy_prev) / dxy_prev * 100 * 5
                        if gold_trend > 2 and dxy_trend > 1:
                            alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "multi_day_correlation_break",
                                           "message": f"近5日金价涨{gold_trend:.1f}%+美元涨{dxy_trend:.1f}%，持续正相关异常"})
                        elif gold_trend < -2 and dxy_trend < -1:
                            alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "multi_day_correlation_break_down",
                                           "message": f"近5日金价跌{gold_trend:.1f}%+美元跌{dxy_trend:.1f}%，持续正相关异常"})
                except (ValueError, TypeError):
                    pass

        return alerts

    # ==================== D5. 量价背离警示 ====================

    def _detect_divergence(self) -> List[Dict]:
        alerts = []
        dim = "divergence"
        if len(self.holdings_history) < 3 or len(self.gold_prices) < 3:
            return alerts

        holdings_by_date = {}
        for record in self.holdings_history:
            d = record.get("date", "")
            if d:
                total = sum(p.get("net_change", 0) for p in record.get("positions", []))
                holdings_by_date[d] = total

        prices_by_date = {}
        for p in self.gold_prices:
            d = p.get("date", "")
            if d:
                prices_by_date[d] = p.get("change", 0)

        common_dates = sorted(set(holdings_by_date.keys()) & set(prices_by_date.keys()))
        if len(common_dates) < 3:
            holdings_changes = [holdings_by_date[d] for d in sorted(holdings_by_date.keys())[-5:]]
            price_changes = [prices_by_date.get(d, 0) for d in sorted(prices_by_date.keys())[-5:]]
        else:
            recent_dates = common_dates[-5:]
            holdings_changes = [holdings_by_date[d] for d in recent_dates]
            price_changes = [prices_by_date[d] for d in recent_dates]

        h_recent = sum(holdings_changes[-3:])
        p_recent = sum(price_changes[-3:])

        if h_recent > 500 and p_recent < -10:
            alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "bull_divergence_strong",
                           "message": f"强看多背离：机构3日加仓{h_recent:,}手但金价跌${abs(p_recent):.0f}，聪明钱抄底"})
        elif h_recent > 200 and p_recent < 0:
            alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "bull_divergence",
                           "message": f"看多背离：机构加仓{h_recent:,}手但金价跌${abs(p_recent):.0f}"})
        elif h_recent < -500 and p_recent > 10:
            alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "bear_divergence_strong",
                           "message": f"强看空背离：机构3日减仓{abs(h_recent):,}手但金价涨${p_recent:.0f}，聪明钱出货"})
        elif h_recent < -200 and p_recent > 0:
            alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "bear_divergence",
                           "message": f"看空背离：机构减仓{abs(h_recent):,}手但金价涨${p_recent:.0f}"})

        if len(holdings_changes) >= 5 and len(price_changes) >= 5:
            n = min(len(holdings_changes), len(price_changes))
            h = holdings_changes[-n:]
            p = price_changes[-n:]
            mean_h = sum(h) / n
            mean_p = sum(p) / n
            cov = sum((h[i] - mean_h) * (p[i] - mean_p) for i in range(n))
            std_h = (sum((x - mean_h) ** 2 for x in h) / max(1, n - 1)) ** 0.5
            std_p = (sum((x - mean_p) ** 2 for x in p) / max(1, n - 1)) ** 0.5
            if std_h > 0 and std_p > 0:
                correlation = cov / (std_h * std_p)
                if correlation < -0.5:
                    direction = "机构逆势加仓预示反弹" if h[-1] > 0 else "机构逆势减仓预示回调"
                    alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "correlation_reversal",
                                   "message": f"持仓-价格负相关(r={correlation:.2f})，{direction}"})
                elif correlation > 0.7:
                    direction = "同向上涨趋势强劲" if h[-1] > 0 else "同向下跌趋势明确"
                    alerts.append({"level": LEVEL_LOW, "dimension": dim, "type": "correlation_strong_positive",
                                   "message": f"持仓-价格强正相关(r={correlation:.2f})，{direction}"})

        if len(self.holdings_history) >= 2 and len(self.gold_prices) >= 2:
            last = self.holdings_history[-1]
            total_chg = sum(p.get("net_change", 0) for p in last.get("positions", []))
            price_chg_pct = (self.gold_prices[-1]["close"] - self.gold_prices[-2]["close"]) / self.gold_prices[-2]["close"] * 100
            if total_chg > 500 and price_chg_pct > 0.5:
                alerts.append({"level": LEVEL_LOW, "dimension": dim, "type": "sync_bull",
                               "message": f"量价同向看多：机构加仓{total_chg:,}手+金价涨{price_chg_pct:.1f}%"})
            elif total_chg < -500 and price_chg_pct < -0.5:
                alerts.append({"level": LEVEL_LOW, "dimension": dim, "type": "sync_bear",
                               "message": f"量价同向看空：机构减仓{abs(total_chg):,}手+金价跌{abs(price_chg_pct):.1f}%"})

        return alerts

    # ==================== D6. 情绪面警示 ====================

    def _detect_sentiment(self) -> List[Dict]:
        alerts = []
        dim = "sentiment"
        if not self.news_sentiment:
            return alerts

        score = self.news_sentiment.get("sentiment_score", 0)
        bullish = self.news_sentiment.get("bullish_count", 0)
        bearish = self.news_sentiment.get("bearish_count", 0)
        total = bullish + bearish or 1

        if score > 0.5:
            alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "sentiment_extreme_bull",
                           "message": f"新闻面极度偏多（评分{score:+.2f}，利多{bullish} vs 利空{bearish}），注意情绪过热"})
        elif score > 0.2:
            alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "sentiment_bull",
                           "message": f"新闻面偏多（评分{score:+.2f}），利多占优"})
        elif score < -0.5:
            alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "sentiment_extreme_bear",
                           "message": f"新闻面极度偏空（评分{score:+.2f}，利空{bearish} vs 利多{bullish}），恐慌可能见底"})
        elif score < -0.2:
            alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "sentiment_bear",
                           "message": f"新闻面偏空（评分{score:+.2f}），利空占优"})

        for event in self.news_sentiment.get("key_events", []):
            title = event.get("title", event) if isinstance(event, dict) else event
            t = title.lower()
            if any(kw in t for kw in ["降息", "rate cut", "鸽派", "dovish", "量化宽松", "qe"]):
                alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "key_event_dovish",
                               "message": f"关键利好事件：{title[:50]}"})
            elif any(kw in t for kw in ["加息", "rate hike", "鹰派", "hawkish", "缩表", "qt"]):
                alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "key_event_hawkish",
                               "message": f"关键利空事件：{title[:50]}"})
            elif any(kw in t for kw in ["冲突", "战争", "地缘", "conflict", "war", "制裁", "sanction"]):
                alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "key_event_geopolitical",
                               "message": f"地缘风险事件：{title[:50]}"})
            elif any(kw in t for kw in ["央行购金", "购金", "gold reserve", "gold buying"]):
                alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "cb_gold_buying",
                               "message": f"央行购金信号：{title[:50]}"})
            elif any(kw in t for kw in ["通胀", "cpi", "inflation", "物价"]):
                alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "inflation_news",
                               "message": f"通胀相关事件：{title[:50]}"})
            elif any(kw in t for kw in ["衰退", "recession", "经济放缓", "失业率", "unemployment"]):
                alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "recession_news",
                               "message": f"经济衰退相关：{title[:50]}"})
            elif any(kw in t for kw in ["关税", "tariff", "贸易战", "trade war"]):
                alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "trade_news",
                               "message": f"贸易政策事件：{title[:50]}"})
            elif any(kw in t for kw in ["黑天鹅", "崩盘", "暴跌", "crash", "black swan"]):
                alerts.append({"level": LEVEL_CRITICAL, "dimension": dim, "type": "black_swan_news",
                               "message": f"极端事件：{title[:50]}"})

        if total >= 5:
            dominant = max(bullish, bearish)
            ratio = dominant / total
            if ratio >= 0.85:
                direction = "偏多" if bullish > bearish else "偏空"
                alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "sentiment_unanimous",
                               "message": f"新闻情绪高度一致（{direction}{ratio:.0%}），信号增强但需防反转"})

        if self.gold_prices and len(self.gold_prices) >= 2:
            gold_chg = (self.gold_prices[-1]["close"] - self.gold_prices[-2]["close"]) / self.gold_prices[-2]["close"] * 100
            if score > 0.3 and gold_chg < -0.5:
                alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "sentiment_price_diverge_bull",
                               "message": f"新闻偏多但金价跌{abs(gold_chg):.1f}%，情绪与价格背离，需警惕"})
            elif score < -0.3 and gold_chg > 0.5:
                alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "sentiment_price_diverge_bear",
                               "message": f"新闻偏空但金价涨{gold_chg:.1f}%，情绪与价格背离，可能见底"})

        if self.holdings_history:
            last = self.holdings_history[-1]
            total_chg = sum(p.get("net_change", 0) for p in last.get("positions", []))
            if score > 0.2 and total_chg < -500:
                alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "sentiment_position_diverge",
                               "message": "情绪与持仓背离：新闻偏多但机构减仓，需警惕"})
            elif score < -0.2 and total_chg > 500:
                alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "sentiment_position_diverge_bull",
                               "message": "情绪与持仓背离：新闻偏空但机构加仓，可能见底"})

        llm_summary = self.news_sentiment.get("llm_summary", "")
        if llm_summary:
            ls = llm_summary.lower()
            if any(kw in ls for kw in ["强烈看多", "大幅上涨", "突破", "牛市"]):
                alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "llm_bullish",
                               "message": f"AI摘要偏多：{llm_summary[:60]}"})
            elif any(kw in ls for kw in ["强烈看空", "大幅下跌", "破位", "熊市"]):
                alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "llm_bearish",
                               "message": f"AI摘要偏空：{llm_summary[:60]}"})

        return alerts

    # ==================== D7. 持仓结构警示 ====================

    def _detect_position(self, today_data: Dict) -> List[Dict]:
        alerts = []
        dim = "position"
        positions = today_data.get("positions", [])
        if not positions:
            return alerts

        threshold = self.alert_threshold_large
        threshold_high = threshold * 2
        threshold_extreme = threshold * 5

        changes = [p["net_change"] for p in positions]
        if all(c > 0 for c in changes):
            alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "unanimous_long",
                           "message": f"前5大机构一致加仓！合计净增{sum(changes):,}手"})
        elif all(c < 0 for c in changes):
            alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "unanimous_short",
                           "message": f"前5大机构一致减仓！合计净减{abs(sum(changes)):,}手"})

        total_nc = sum(p.get("net_change", 0) for p in positions)
        if abs(total_nc) >= threshold_extreme:
            d = "加仓" if total_nc > 0 else "减仓"
            alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "total_extreme",
                           "message": f"前5大合计{d}{abs(total_nc):,}手，极端信号"})
        elif abs(total_nc) >= threshold * 3:
            d = "加仓" if total_nc > 0 else "减仓"
            alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "total_large",
                           "message": f"前5大合计{d}{abs(total_nc):,}手"})

        large_count = sum(1 for p in positions if abs(p["net_change"]) >= threshold / 2)
        if large_count >= 3:
            names = [f"{p['name']}({abs(p['net_change']):,})" for p in positions if abs(p["net_change"]) >= threshold / 2]
            alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "multi_large",
                           "message": f"{large_count}家机构同时大幅操作：{'、'.join(names)}"})

        for pos in positions:
            name = pos["name"]
            chg = pos["net_change"]
            if abs(chg) >= threshold_high:
                d = "大幅减仓" if chg < 0 else "大幅加仓"
                alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "single_large",
                               "message": f"{name} 单日{d} {abs(chg):,} 手"})
            elif abs(chg) >= threshold:
                d = "减仓" if chg < 0 else "加仓"
                alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "single_change",
                               "message": f"{name} 单日{d} {abs(chg):,} 手"})
            sc = pos.get("short_change", 0)
            if sc >= threshold:
                alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "short_surge",
                               "message": f"{name} 空头暴增 {sc:,} 手"})
            elif sc >= threshold / 2:
                alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "short_increase",
                               "message": f"{name} 空头增加 {sc:,} 手"})
            net = pos["net"]
            if net > 0 and chg < 0:
                pct = abs(chg) / net * 100
                if pct >= 20:
                    alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "net_shrink_severe",
                                   "message": f"{name} 净多头缩水{pct:.1f}%"})
                elif pct >= 10:
                    alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "net_shrink",
                                   "message": f"{name} 净多头缩水{pct:.1f}%"})

        if len(self.holdings_history) >= 2:
            for pos in positions:
                name = pos["name"]
                hist = []
                for rec in self.holdings_history[-3:]:
                    for p in rec.get("positions", []):
                        if p["name"] == name:
                            hist.append(p.get("net_change", 0))
                            break
                if len(hist) >= 2:
                    y = hist[-2]
                    t = pos["net_change"]
                    if y > 0 and t < 0 and abs(y) >= 200 and abs(t) >= 200:
                        alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "reversal",
                                       "message": f"{name} 突然转向：昨+{y}→今{t}"})
                    elif y < 0 and t > 0 and abs(y) >= 200 and abs(t) >= 200:
                        alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "reversal",
                                       "message": f"{name} 突然转向：昨{y}→今+{t}"})
                    elif y > 0 and t < 0 and (abs(y) >= 100 or abs(t) >= 100):
                        alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "reversal_mild",
                                       "message": f"{name} 方向变化：昨+{y}→今{t}"})
                    elif y < 0 and t > 0 and (abs(y) >= 100 or abs(t) >= 100):
                        alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "reversal_mild",
                                       "message": f"{name} 方向变化：昨{y}→今+{t}"})
                    if abs(y) >= 200 and abs(t) >= abs(y) * 2:
                        alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "acceleration",
                                       "message": f"{name} 变化加速：今日{abs(t):,}手是昨日{abs(y):,}手的{abs(t)/abs(y):.1f}倍"})

        if len(self.holdings_history) >= 5:
            current_net = sum(p.get("net", 0) for p in positions)
            hist_nets = [sum(p.get("net", 0) for p in r.get("positions", [])) for r in self.holdings_history[:-1]]
            if hist_nets:
                sorted_n = sorted(hist_nets)
                rank = 0
                for i, v in enumerate(sorted_n):
                    if v >= current_net:
                        rank = i
                        break
                else:
                    rank = len(sorted_n)
                pct = rank / len(sorted_n) * 100
                data_sufficiency = min(1.0, len(hist_nets) / 20)
                if data_sufficiency < 1.0:
                    if pct >= 90:
                        alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "crowded_high",
                                       "message": f"净多头历史{pct:.0f}%分位，偏高（数据仅{len(hist_nets)}天，仅供参考）"})
                    elif pct <= 10:
                        if current_net > 0:
                            alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "crowded_low",
                                           "message": f"净多头历史{pct:.0f}%分位，近期偏低但绝对值仍正（{current_net:,}手），数据仅{len(hist_nets)}天"})
                        else:
                            alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "crowded_low",
                                           "message": f"净多头历史{pct:.0f}%分位，极度看空"})
                else:
                    if pct >= 90:
                        alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "crowded_high",
                                       "message": f"净多头历史{pct:.0f}%分位，多头拥挤"})
                    elif pct <= 10:
                        alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "crowded_low",
                                       "message": f"净多头历史{pct:.0f}%分位，极度看空"})

        return alerts

    # ==================== D8. 日历事件警示 ====================

    def _detect_calendar(self) -> List[Dict]:
        alerts = []
        dim = "calendar"
        try:
            today = datetime.now().date()
        except Exception:
            return alerts

        key_events = [
            {"name": "FOMC利率决议", "months": [1, 3, 5, 6, 7, 9, 10, 12], "impact": "critical",
             "desc": "美联储利率决策，直接影响美元和黄金定价"},
            {"name": "美国CPI数据", "months": list(range(1, 13)), "impact": "high",
             "desc": "通胀数据影响美联储政策预期"},
            {"name": "美国非农就业(NFP)", "months": list(range(1, 13)), "impact": "high",
             "desc": "就业数据影响美联储政策路径"},
            {"name": "美联储会议纪要", "months": list(range(1, 13)), "impact": "medium",
             "desc": "揭示FOMC委员政策立场细节"},
            {"name": "美国PPI数据", "months": list(range(1, 13)), "impact": "medium",
             "desc": "生产者物价是CPI先行指标"},
            {"name": "美国GDP数据", "months": [1, 4, 7, 10], "impact": "high",
             "desc": "经济增长数据影响货币政策预期"},
            {"name": "美国零售销售", "months": list(range(1, 13)), "impact": "medium",
             "desc": "消费数据反映经济健康度"},
            {"name": "ECB利率决议", "months": [1, 3, 4, 6, 7, 9, 10, 12], "impact": "medium",
             "desc": "欧央行政策影响欧元/美元，间接影响黄金"},
            {"name": "BOJ利率决议", "months": [1, 3, 4, 6, 7, 9, 10, 12], "impact": "low",
             "desc": "日央行政策影响日元，间接影响美元"},
        ]

        upcoming = []
        for event in key_events:
            for m in event["months"]:
                try:
                    event_date = datetime(today.year, m, 1).date()
                    while event_date.weekday() >= 5:
                        event_date += timedelta(days=1)
                    if event_date.month != m:
                        event_date = datetime(today.year, m, 1).date()

                    days_diff = (event_date - today).days
                    if 0 <= days_diff <= 14:
                        upcoming.append((days_diff, event))
                except (ValueError, OverflowError):
                    pass

        upcoming.sort(key=lambda x: x[0])

        for days_diff, event in upcoming:
            impact = event["impact"]
            level_map = {"critical": LEVEL_HIGH, "high": LEVEL_MEDIUM, "medium": LEVEL_LOW, "low": LEVEL_LOW}
            base_level = level_map.get(impact, LEVEL_LOW)

            if days_diff == 0:
                if impact == "critical":
                    actual_level = LEVEL_CRITICAL
                elif impact == "high":
                    actual_level = LEVEL_HIGH
                else:
                    actual_level = LEVEL_MEDIUM
                alerts.append({"level": actual_level, "dimension": dim,
                               "type": f"event_today_{impact}",
                               "message": f"📌 今日发布：{event['name']}！{event['desc']}"})
            elif days_diff <= 1:
                alerts.append({"level": base_level, "dimension": dim,
                               "type": f"event_imminent_{impact}",
                               "message": f"⚠️ {event['name']}明日发布，{event['desc']}"})
            elif days_diff <= 3:
                alerts.append({"level": base_level, "dimension": dim,
                               "type": f"event_3d_{impact}",
                               "message": f"{event['name']}将在{days_diff}天后发布（约{event['desc'][:20]}）"})
            elif days_diff <= 7:
                if impact in ("critical", "high"):
                    alerts.append({"level": LEVEL_LOW, "dimension": dim,
                                   "type": f"event_7d_{impact}",
                                   "message": f"{event['name']}将于下周发布，提前做好仓位管理"})

        if today.weekday() == 4:
            alerts.append({"level": LEVEL_LOW, "dimension": dim, "type": "friday_close",
                           "message": "周五收盘，周末持仓风险需注意，建议控制仓位"})

        if today.month in [1, 4, 7, 10] and today.day <= 5:
            alerts.append({"level": LEVEL_LOW, "dimension": dim, "type": "quarter_start",
                           "message": "季初首周，机构仓位调整频繁，波动可能加大"})

        if today.month == 12 and today.day >= 20:
            alerts.append({"level": LEVEL_LOW, "dimension": dim, "type": "year_end",
                           "message": "年末流动性下降，机构平仓结算，波动可能异常"})

        if today.month == 1 and today.day <= 10:
            alerts.append({"level": LEVEL_LOW, "dimension": dim, "type": "year_start",
                           "message": "年初机构重新布局，趋势可能不明确"})

        return alerts

    # ==================== D9. 交叉确认警示 ====================

    def _detect_cross_dimension(self) -> List[Dict]:
        alerts = []
        dim = "cross"
        dim_scores = self._calc_dimension_scores()
        if not dim_scores:
            return alerts

        bullish_dims = sum(1 for s in dim_scores.values() if self._safe_score(s) > 0.2)
        bearish_dims = sum(1 for s in dim_scores.values() if self._safe_score(s) < -0.2)
        total_dims = len(dim_scores)

        if bullish_dims >= 5:
            details = [f"{k}({self._safe_score(v):+.1f})" for k, v in dim_scores.items() if self._safe_score(v) > 0.2]
            alerts.append({"level": LEVEL_CRITICAL, "dimension": dim, "type": "multi_dim_bullish",
                           "message": f"多维度强烈看多！{bullish_dims}/{total_dims}个维度偏多：{', '.join(details)}"})
        elif bullish_dims >= 4:
            details = [f"{k}({self._safe_score(v):+.1f})" for k, v in dim_scores.items() if self._safe_score(v) > 0.2]
            alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "multi_dim_bullish_mild",
                           "message": f"多维度偏多：{bullish_dims}个维度看多：{', '.join(details)}"})
        elif bullish_dims >= 3:
            details = [f"{k}({self._safe_score(v):+.1f})" for k, v in dim_scores.items() if self._safe_score(v) > 0.2]
            alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "multi_dim_bullish_weak",
                           "message": f"部分维度偏多：{bullish_dims}个维度看多：{', '.join(details)}"})

        if bearish_dims >= 5:
            details = [f"{k}({self._safe_score(v):+.1f})" for k, v in dim_scores.items() if self._safe_score(v) < -0.2]
            alerts.append({"level": LEVEL_CRITICAL, "dimension": dim, "type": "multi_dim_bearish",
                           "message": f"多维度强烈看空！{bearish_dims}/{total_dims}个维度偏空：{', '.join(details)}"})
        elif bearish_dims >= 4:
            details = [f"{k}({self._safe_score(v):+.1f})" for k, v in dim_scores.items() if self._safe_score(v) < -0.2]
            alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "multi_dim_bearish_mild",
                           "message": f"多维度偏空：{bearish_dims}个维度看空：{', '.join(details)}"})
        elif bearish_dims >= 3:
            details = [f"{k}({self._safe_score(v):+.1f})" for k, v in dim_scores.items() if self._safe_score(v) < -0.2]
            alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "multi_dim_bearish_weak",
                           "message": f"部分维度偏空：{bearish_dims}个维度看空：{', '.join(details)}"})

        if bullish_dims >= 2 and bearish_dims >= 2:
            alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "dim_conflict",
                           "message": f"维度矛盾：{bullish_dims}个偏多 vs {bearish_dims}个偏空，市场分歧大"})

        extreme_dims = [k for k, v in dim_scores.items() if abs(self._safe_score(v)) > 0.5]
        if len(extreme_dims) >= 2:
            direction = "偏多" if self._safe_score(dim_scores[extreme_dims[0]]) > 0 else "偏空"
            alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "extreme_anomaly",
                           "message": f"异常幅度检测：{len(extreme_dims)}个维度评分极端{direction}({', '.join(extreme_dims)})，可能存在异常信号"})

        return alerts

    # ==================== D10. 极端风险警示 ====================

    def _detect_extreme_risk(self) -> List[Dict]:
        alerts = []
        dim = "extreme"
        dim_scores = self._calc_dimension_scores()
        if not dim_scores:
            return alerts

        avg = sum(self._safe_score(v) for v in dim_scores.values()) / len(dim_scores) if dim_scores else 0
        if math.isnan(avg):
            avg = 0.0

        if abs(avg) > 0.5:
            d = "看多" if avg > 0 else "看空"
            alerts.append({"level": LEVEL_CRITICAL, "dimension": dim, "type": "systemic_risk",
                           "message": f"系统性{d}风险！综合评分{avg:+.2f}，多维度极端共振"})

        if self.prediction:
            conf = self.prediction.get("confidence", 50)
            sc = self.prediction.get("score", 0)
            if conf >= 75 and abs(sc) > 0.4:
                d = "看多" if sc > 0 else "看空"
                alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "high_confidence",
                               "message": f"高置信度{d}信号（置信度{conf}%，评分{sc:+.2f}）"})

        indicators = self.macro_data.get("indicators", {})
        vix_val = None
        yield_chg = 0
        dxy_chg = 0
        try:
            vix_val = float(indicators.get("vix", {}).get("value", 0)) if indicators.get("vix", {}).get("value") else None
        except (ValueError, TypeError):
            pass
        try:
            yield_chg = float(indicators.get("us_10y_yield", {}).get("change", 0)) if indicators.get("us_10y_yield", {}).get("change") else 0
        except (ValueError, TypeError):
            pass
        try:
            dxy_chg = float(indicators.get("dxy", {}).get("change", 0)) if indicators.get("dxy", {}).get("change") else 0
        except (ValueError, TypeError):
            pass

        if vix_val and vix_val > 30 and abs(yield_chg) > 0.1 and abs(dxy_chg) > 0.5:
            alerts.append({"level": LEVEL_CRITICAL, "dimension": dim, "type": "black_swan",
                           "message": f"黑天鹅预警！VIX={vix_val:.0f}+美债{yield_chg:+.2f}%+美元{dxy_chg:+.2f}"})

        try:
            from .db import get_price_events, get_price_event_stats
            stats = get_price_event_stats(90)
            if stats and stats.get("total", 0) > 0:
                surge_count = stats.get("surge_count", 0)
                crash_count = stats.get("crash_count", 0)
                recent = stats.get("recent", [])

                if surge_count + crash_count >= 5:
                    alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "frequent_extreme_moves",
                                   "message": f"近90天金价异动{surge_count + crash_count}次（暴涨{surge_count}/暴跌{crash_count}），市场极度不稳定"})

                if recent:
                    latest = recent[0]
                    event_ts = latest.get("timestamp", "")
                    if event_ts:
                        try:
                            from datetime import datetime as _dt
                            event_time = _dt.fromisoformat(event_ts)
                            hours_ago = (_dt.now() - event_time).total_seconds() / 3600
                            if hours_ago < 24:
                                etype = latest.get("event_type", "")
                                epct = latest.get("change_pct", 0)
                                eprice = latest.get("price", 0)
                                direction = "暴涨" if etype == "surge" else "暴跌"
                                alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "recent_extreme_move",
                                               "message": f"近24h金价{direction}{abs(epct):.1f}%至${eprice:.0f}，异动事件需关注后续走势"})
                        except (ValueError, TypeError):
                            pass

                recent_events = get_price_events(7)
                if len(recent_events) >= 3:
                    directions = set(e.get("event_type", "") for e in recent_events)
                    if len(directions) >= 2:
                        alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "whipsaw",
                                       "message": f"近7天金价暴涨暴跌交替出现{len(recent_events)}次，剧烈震荡模式，方向不明"})

                if crash_count >= 3 and surge_count == 0:
                    alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "downtrend_crashes",
                                   "message": f"近90天暴跌{crash_count}次但无暴涨，单边下跌趋势中"})
                elif surge_count >= 3 and crash_count == 0:
                    alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "uptrend_surges",
                                   "message": f"近90天暴涨{surge_count}次但无暴跌，单边上涨趋势中"})
        except Exception:
            pass

        return alerts

    # ==================== 辅助方法 ====================

    def _detect_cb_gold(self) -> List[Dict]:
        alerts = []
        dim = "cb_gold"

        if self.news_sentiment:
            for event in self.news_sentiment.get("key_events", []):
                title = event.get("title", event) if isinstance(event, dict) else event
                t = title.lower()
                if any(kw in t for kw in ["央行购金", "购金", "gold reserve", "gold buying",
                                           "储备增加", "增持黄金", "gold holdings"]):
                    alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "cb_buying_news",
                                   "message": f"央行购金动态：{title[:50]}"})

            llm_summary = self.news_sentiment.get("llm_summary", "")
            if llm_summary:
                ls = llm_summary.lower()
                if any(kw in ls for kw in ["央行购金", "储备增加", "增持黄金", "central bank"]):
                    alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "cb_buying_llm",
                                   "message": f"AI摘要提及央行购金：{llm_summary[:60]}"})

        if self.prediction:
            cb_score = self.prediction.get("factors", {}).get("cb_gold", {}).get("score", 0)
            if cb_score > 0.2:
                alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "cb_bullish",
                               "message": "央行购金因子偏多，长期结构性支撑黄金"})
            elif cb_score < -0.2:
                alerts.append({"level": LEVEL_LOW, "dimension": dim, "type": "cb_bearish",
                               "message": "央行购金因子偏空，需关注央行售金动态"})

        now = datetime.now()
        month = now.month
        if month in [4, 5]:
            alerts.append({"level": LEVEL_LOW, "dimension": dim, "type": "cb_season",
                           "message": "印度婚礼季+央行Q1购金数据发布期，关注购金量"})
        elif month in [9, 10]:
            alerts.append({"level": LEVEL_LOW, "dimension": dim, "type": "cb_season_q3",
                           "message": "排灯节前+央行Q3购金数据发布期，关注购金量"})

        return alerts

    def _detect_etf_flow(self) -> List[Dict]:
        alerts = []
        dim = "etf_flow"

        gld_data = self.macro_data.get("indicators", {}).get("gld_etf", {})
        if gld_data and gld_data.get("value") is not None:
            try:
                gld_change_pct = float(gld_data.get("change_pct", 0))
                gld_val = float(gld_data["value"])
                if gld_val == gld_val:
                    if gld_change_pct > 2.0:
                        alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "etf_large_inflow",
                                       "message": f"GLD ETF涨{gld_change_pct:+.1f}%至${gld_val:.1f}，资金大幅流入"})
                    elif gld_change_pct > 1.0:
                        alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "etf_inflow",
                                       "message": f"GLD ETF涨{gld_change_pct:+.1f}%，资金流入"})
                    elif gld_change_pct < -2.0:
                        alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "etf_large_outflow",
                                       "message": f"GLD ETF跌{gld_change_pct:+.1f}%至${gld_val:.1f}，资金大幅流出"})
                    elif gld_change_pct < -1.0:
                        alerts.append({"level": LEVEL_MEDIUM, "dimension": dim, "type": "etf_outflow",
                                       "message": f"GLD ETF跌{gld_change_pct:+.1f}%，资金流出"})

                    if self.gold_prices and len(self.gold_prices) >= 2:
                        gold_chg = (self.gold_prices[-1]["close"] - self.gold_prices[-2]["close"]) / self.gold_prices[-2]["close"] * 100
                        if gld_change_pct > 1.0 and gold_chg < -0.5:
                            alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "etf_divergence_bull",
                                           "message": f"ETF流入但金价跌，逢低买入信号"})
                        elif gld_change_pct < -1.0 and gold_chg > 0.5:
                            alerts.append({"level": LEVEL_HIGH, "dimension": dim, "type": "etf_divergence_bear",
                                           "message": f"ETF流出但金价涨，逢高卖出信号"})
            except (ValueError, TypeError):
                pass

        if self.prediction:
            etf_score = self.prediction.get("factors", {}).get("etf_flow", {}).get("score", 0)
            if etf_score > 0.2:
                alerts.append({"level": LEVEL_LOW, "dimension": dim, "type": "etf_bullish",
                               "message": "ETF资金流因子偏多"})
            elif etf_score < -0.2:
                alerts.append({"level": LEVEL_LOW, "dimension": dim, "type": "etf_bearish",
                               "message": "ETF资金流因子偏空"})

        return alerts

    # ==================== 原有辅助方法 ====================

    def _calc_dimension_scores(self) -> Dict[str, float]:
        scores = {}
        if self.prediction:
            factors = self.prediction.get("factors", {})
            scores["宏观"] = (self._safe_score(factors.get("real_rate", {}).get("score", 0)) +
                             self._safe_score(factors.get("dollar", {}).get("score", 0))) / 2
            scores["持仓"] = (self._safe_score(factors.get("momentum", {}).get("score", 0)) +
                             self._safe_score(factors.get("extreme", {}).get("score", 0)) +
                             self._safe_score(factors.get("divergence", {}).get("score", 0))) / 3
            scores["技术"] = self._safe_score(factors.get("price_trend", {}).get("score", 0))
            scores["波动率"] = self._safe_score(factors.get("volatility", {}).get("score", 0))
            scores["情绪"] = self._safe_score(factors.get("news_sentiment", {}).get("score", 0))
        else:
            if len(self.gold_prices) >= 14:
                closes = [p["close"] for p in self.gold_prices]
                rsi = self._calc_rsi(closes, 14)
                if rsi is not None:
                    if rsi > 60:
                        scores["技术"] = 0.2 if rsi <= 70 else -0.3
                    elif rsi < 40:
                        scores["技术"] = -0.2 if rsi >= 30 else 0.3
                    else:
                        scores["技术"] = 0.0
            if self.holdings_history:
                last = self.holdings_history[-1]
                tc = sum(p.get("net_change", 0) for p in last.get("positions", []))
                scores["持仓"] = 0.3 if tc > 500 else (-0.3 if tc < -500 else 0.0)
            indicators = self.macro_data.get("indicators", {})
            ms = 0
            try:
                yc = float(indicators.get("us_10y_yield", {}).get("change", 0))
                ms += -0.3 if yc > 0.05 else (0.3 if yc < -0.05 else 0)
            except (ValueError, TypeError):
                pass
            try:
                dc = float(indicators.get("dxy", {}).get("change", 0))
                ms += -0.3 if dc > 0.3 else (0.3 if dc < -0.3 else 0)
            except (ValueError, TypeError):
                pass
            if ms != 0:
                scores["宏观"] = max(-1, min(1, ms))
            if self.news_sentiment:
                scores["情绪"] = self.news_sentiment.get("sentiment_score", 0)

        indicators = self.macro_data.get("indicators", {})
        corr_score = 0
        if self.gold_prices and len(self.gold_prices) >= 2:
            gold_chg = (self.gold_prices[-1]["close"] - self.gold_prices[-2]["close"]) / self.gold_prices[-2]["close"] * 100
            try:
                dxy_pct = float(indicators.get("dxy", {}).get("change_pct", 0))
                if gold_chg > 0.3 and dxy_pct > 0.2:
                    corr_score += 0.4
                elif gold_chg < -0.3 and dxy_pct < -0.2:
                    corr_score += 0.4
                elif gold_chg > 0.3 and dxy_pct < -0.2:
                    corr_score -= 0.2
                elif gold_chg < -0.3 and dxy_pct > 0.2:
                    corr_score -= 0.2
            except (ValueError, TypeError):
                pass
            try:
                yc = float(indicators.get("us_10y_yield", {}).get("change", 0))
                if gold_chg > 0.3 and yc > 0.05:
                    corr_score += 0.3
                elif gold_chg < -0.3 and yc < -0.05:
                    corr_score += 0.3
            except (ValueError, TypeError):
                pass
        if corr_score != 0:
            scores["关联"] = max(-1, min(1, corr_score))

        try:
            vix_val = float(indicators.get("vix", {}).get("value", 0))
            if vix_val > 25:
                scores["波动率"] = scores.get("波动率", 0) + 0.3
            elif vix_val < 13:
                scores["波动率"] = scores.get("波动率", 0) - 0.2
        except (ValueError, TypeError):
            pass

        return scores

    @staticmethod
    def _safe_score(val):
        if val is None or (isinstance(val, float) and math.isnan(val)):
            return 0.0
        return float(val)

    @staticmethod
    def _calc_rsi(closes: List[float], period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            c = closes[i] - closes[i - 1]
            gains.append(max(0, c))
            losses.append(max(0, -c))
        if len(gains) < period:
            return None
        ag = sum(gains[:period]) / period
        al = sum(losses[:period]) / period
        for i in range(period, len(gains)):
            ag = (ag * (period - 1) + gains[i]) / period
            al = (al * (period - 1) + losses[i]) / period
        if al == 0:
            return 100.0
        return 100 - (100 / (1 + ag / al))

    @staticmethod
    def _calc_macd(closes: List[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Optional[tuple]:
        if len(closes) < slow + signal:
            return None
        def ema(data, p):
            m = 2 / (p + 1)
            r = [data[0]]
            for x in data[1:]:
                r.append(x * m + r[-1] * (1 - m))
            return r
        ef = ema(closes, fast)
        es = ema(closes, slow)
        ml = [f - s for f, s in zip(ef, es)]
        if len(ml) < signal:
            return None
        sl = ema(ml, signal)
        return (ml[-1], sl[-1], ml[-1] - sl[-1])

    def _calc_macd_hist_prev(self, closes: List[float]) -> Optional[float]:
        if len(closes) < 28:
            return None
        r = self._calc_macd(closes[:-1])
        return r[2] if r else None

    def _calc_recent_macd_hists(self, closes: List[float], n: int = 5) -> List[float]:
        hists = []
        for i in range(n, 0, -1):
            sub = closes[:len(closes) - i + 1] if i > 1 else closes
            if len(sub) < 36:
                continue
            r = self._calc_macd(sub)
            if r:
                hists.append(r[2])
        return hists

    @staticmethod
    def _calc_bollinger(closes: List[float], period: int = 20, num_std: float = 2.0) -> Optional[tuple]:
        if len(closes) < period:
            return None
        r = closes[-period:]
        mid = sum(r) / period
        var = sum((x - mid) ** 2 for x in r) / (period - 1)
        std = var ** 0.5
        return (mid, mid + num_std * std, mid - num_std * std)

    def _calc_rolling_correlations(self) -> Dict:
        """
        计算金价与宏观指标的滚动相关系数
        检测短期(5日)与长期(20日)相关性的结构性变化
        """
        result = {}
        if len(self.gold_prices) < 20:
            return result

        gold_pcts = []
        for i in range(1, len(self.gold_prices)):
            prev = self.gold_prices[i-1].get("close", 0)
            curr = self.gold_prices[i].get("close", 0)
            if prev > 0:
                gold_pcts.append((curr - prev) / prev * 100)
            else:
                gold_pcts.append(0)

        macro_series = {}
        macro_history = self._get_macro_history_for_corr()
        for key, values in macro_history.items():
            if len(values) >= len(gold_pcts):
                macro_series[key] = values[-len(gold_pcts):]

        def pearson(x, y):
            n = len(x)
            if n < 3:
                return 0.0
            mx = sum(x) / n
            my = sum(y) / n
            cov = sum((x[i] - mx) * (y[i] - my) for i in range(n))
            vx = sum((xi - mx) ** 2 for xi in x)
            vy = sum((yi - my) ** 2 for yi in y)
            if vx == 0 or vy == 0:
                return 0.0
            return cov / (vx ** 0.5 * vy ** 0.5)

        pair_names = {"dxy": "金价-美元", "us_10y_yield": "金价-美债"}
        for key, m_pcts in macro_series.items():
            min_len = min(len(gold_pcts), len(m_pcts))
            if min_len < 20:
                continue

            gp = gold_pcts[-min_len:]
            mp = m_pcts[-min_len:]

            short_n = min(5, min_len)
            long_n = min(20, min_len)

            short_corr = pearson(gp[-short_n:], mp[-short_n:])
            long_corr = pearson(gp[-long_n:], mp[-long_n:])

            flip_detected = False
            if short_corr * long_corr < 0 and abs(short_corr) > 0.3:
                flip_detected = True
            elif abs(short_corr - long_corr) > 0.6:
                flip_detected = True

            result[pair_names.get(key, key)] = {
                "short_corr": round(short_corr, 2),
                "long_corr": round(long_corr, 2),
                "flip_detected": flip_detected,
            }

        return result

    def _get_macro_history_for_corr(self) -> Dict[str, List[float]]:
        try:
            from .db import get_macro_history
            history = get_macro_history(days=60)
            series = {"dxy": [], "us_10y_yield": []}
            for record in history:
                indicators = record.get("indicators", {})
                for key in series:
                    val = indicators.get(key, {}).get("change_pct")
                    if val is not None:
                        try:
                            series[key].append(float(val))
                        except (ValueError, TypeError):
                            pass
            return series
        except Exception:
            return {"dxy": [], "us_10y_yield": []}

    def _macro_percentile(self, key: str, value: float) -> Optional[float]:
        try:
            from .db import get_macro_history
            history = get_macro_history(days=365)
            vals = []
            for record in history:
                v = record.get("indicators", {}).get(key, {}).get("value")
                if v is not None:
                    try:
                        vf = float(v)
                        if vf == vf:
                            vals.append(vf)
                    except (ValueError, TypeError):
                        pass
            if len(vals) < 30:
                return None
            vals.sort()
            n = len(vals)
            for i, v in enumerate(vals):
                if v >= value:
                    return round(i / n * 100, 1)
            return 100.0
        except Exception:
            return None

    @staticmethod
    def _calc_atr(prices: List[Dict], period: int = 14) -> Optional[float]:
        if len(prices) < period + 1:
            return None
        trs = []
        for i in range(1, len(prices)):
            h = prices[i].get("high", prices[i]["close"])
            l = prices[i].get("low", prices[i]["close"])
            pc = prices[i - 1]["close"]
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        if len(trs) < period:
            return None
        return sum(trs[-period:]) / period


def generate_dimension_summary(alerts: List[Dict]) -> Dict:
    summary = {}
    for a in alerts:
        d = a.get("dimension", "unknown")
        if d not in summary:
            summary[d] = {"total": 0, "critical": 0, "high": 0, "medium": 0, "low": 0}
        summary[d]["total"] += 1
        lv = a.get("level", "low")
        if lv in summary[d]:
            summary[d][lv] += 1
    return summary
