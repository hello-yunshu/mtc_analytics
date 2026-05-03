# -*- coding: utf-8 -*-
"""
长期趋势分析模块 - 周变化、月变化、趋势方向、多空比变化
"""

from typing import List, Dict, Tuple, Optional
from datetime import datetime


class TrendAnalyzer:
    """长期趋势分析器"""
    
    def __init__(self, history: List[Dict]):
        self.history = history
    
    def analyze_long_term(self, today_data: Dict) -> Dict:
        """
        生成长期趋势分析报告
        
        Returns:
            {
                "history_days": 30,
                "week_analysis": {...},
                "month_analysis": {...},
                "broker_trends": [...],
                "long_short_ratio_trend": {...},
                "signals": [...],
            }
        """
        total_days = len(self.history)
        
        # 周分析（最近5个交易日）
        week = self._period_analysis(5)
        
        # 双周分析（最近10个交易日）
        biweek = self._period_analysis(10)
        
        # 月分析（最近20个交易日）
        month = self._period_analysis(20)
        
        # 各机构长期趋势
        broker_trends = self._broker_long_term_trends()
        
        # 多空比趋势
        ratio_trend = self._long_short_ratio_trend()
        
        # 长期信号
        signals = self._generate_long_term_signals(today_data)
        
        return {
            "history_days": total_days,
            "week": week,
            "biweek": biweek,
            "month": month,
            "broker_trends": broker_trends,
            "ratio_trend": ratio_trend,
            "signals": signals,
        }
    
    def _period_analysis(self, days: int) -> Dict:
        """分析最近N个交易日的整体趋势"""
        if len(self.history) < days:
            days = len(self.history)
        if days == 0:
            return {"days": 0}
        
        period = self.history[-days:]
        
        # 净多头合计变化
        net_changes = []
        for record in period:
            total = sum(p.get("net_change", 0) for p in record.get("positions", []))
            net_changes.append(total)
        
        total_change = sum(net_changes)
        avg_change = total_change / days
        
        # 加仓天数 vs 减仓天数
        up_days = sum(1 for c in net_changes if c > 0)
        down_days = sum(1 for c in net_changes if c < 0)
        flat_days = sum(1 for c in net_changes if c == 0)
        
        # 起始和结束的净多头合计
        first_total = sum(p.get("net", 0) for p in period[0].get("positions", []))
        last_total = sum(p.get("net", 0) for p in period[-1].get("positions", []))
        
        # 变化率
        change_pct = 0
        if first_total > 0:
            change_pct = (last_total - first_total) / first_total * 100
        
        # 趋势方向
        if change_pct > 5:
            direction = "强势偏多"
            direction_icon = "🔴🔴"
        elif change_pct > 2:
            direction = "偏多"
            direction_icon = "🔴"
        elif change_pct < -5:
            direction = "强势偏空"
            direction_icon = "🟢🟢"
        elif change_pct < -2:
            direction = "偏空"
            direction_icon = "🟢"
        else:
            direction = "震荡"
            direction_icon = "⚪"
        
        # 最大单日变化
        max_change = max(net_changes, key=abs) if net_changes else 0
        
        return {
            "days": days,
            "total_change": total_change,
            "avg_change": round(avg_change),
            "change_pct": round(change_pct, 1),
            "up_days": up_days,
            "down_days": down_days,
            "flat_days": flat_days,
            "first_total": first_total,
            "last_total": last_total,
            "direction": direction,
            "direction_icon": direction_icon,
            "max_single_day_change": max_change,
        }
    
    def _broker_long_term_trends(self) -> List[Dict]:
        """分析各机构的长期趋势"""
        if not self.history:
            return []
        
        # 获取最近一次的机构列表
        latest = self.history[-1]
        broker_names = [p["name"] for p in latest.get("positions", [])]
        
        trends = []
        for name in broker_names:
            # 收集该机构的历史净多头数据
            net_values = []
            for record in self.history:
                for pos in record.get("positions", []):
                    if pos["name"] == name:
                        net_values.append(pos.get("net", 0))
                        break
                else:
                    net_values.append(None)  # 该天没有该机构的数据
            
            # 过滤掉None
            valid_values = [v for v in net_values if v is not None]
            if len(valid_values) < 2:
                continue
            
            first = valid_values[0]
            last = valid_values[-1]
            change = last - first
            change_pct = (change / first * 100) if first > 0 else 0
            
            # 计算连续方向
            changes = [valid_values[i] - valid_values[i-1] for i in range(1, len(valid_values))]
            recent_changes = changes[-5:] if len(changes) >= 5 else changes
            
            # 最近趋势
            if len(recent_changes) >= 3:
                recent_sum = sum(recent_changes[-3:])
                if recent_sum > 0:
                    recent_trend = "加仓中"
                elif recent_sum < 0:
                    recent_trend = "减仓中"
                else:
                    recent_trend = "持平"
            else:
                recent_trend = "数据不足"
            
            trends.append({
                "name": name,
                "latest_net": last,
                "period_first": first,
                "change": change,
                "change_pct": round(change_pct, 1),
                "recent_trend": recent_trend,
                "data_points": len(valid_values),
            })
        
        return trends
    
    def _long_short_ratio_trend(self) -> Dict:
        """分析多空比趋势"""
        if not self.history:
            return {}
        
        # 使用历史数据中的 total_long 和 total_short
        ratios = []
        for record in self.history[-20:]:
            total_long = record.get("total_long", 0)
            total_short = record.get("total_short", 0)
            if total_short > 0:
                ratios.append({
                    "date": record["date"],
                    "ratio": round(total_long / total_short, 2),
                    "long": total_long,
                    "short": total_short,
                })
        
        if not ratios:
            return {}
        
        first_ratio = ratios[0]["ratio"]
        last_ratio = ratios[-1]["ratio"]
        
        # 多空比变化方向
        if last_ratio > first_ratio * 1.05:
            ratio_trend = "多头优势扩大"
        elif last_ratio < first_ratio * 0.95:
            ratio_trend = "空头优势扩大"
        else:
            ratio_trend = "基本稳定"
        
        return {
            "latest_ratio": last_ratio,
            "period_first_ratio": first_ratio,
            "change": round(last_ratio - first_ratio, 2),
            "trend": ratio_trend,
            "history": ratios[-5:],  # 最近5天
        }
    
    def _generate_long_term_signals(self, today_data: Dict) -> List[Dict]:
        """生成长期级别的警示信号"""
        from .analyzer import LEVEL_HIGH, LEVEL_MEDIUM, LEVEL_LOW
        
        signals = []
        
        if len(self.history) < 5:
            return signals
        
        # ---- 周级别信号 ----
        week = self._period_analysis(5)
        
        # 【高】周净变化超5%
        if abs(week["change_pct"]) >= 5:
            direction = "下降" if week["change_pct"] < 0 else "上升"
            signals.append({
                "level": LEVEL_HIGH,
                "type": "week_large_change",
                "message": f"前5大机构净多头周{direction}{abs(week['change_pct'])}%（{week['total_change']:+,}手）",
            })
        elif abs(week["change_pct"]) >= 3:
            direction = "下降" if week["change_pct"] < 0 else "上升"
            signals.append({
                "level": LEVEL_MEDIUM,
                "type": "week_medium_change",
                "message": f"前5大机构净多头周{direction}{abs(week['change_pct'])}%（{week['total_change']:+,}手）",
            })
        
        # 【高】周内全部同方向
        if week["up_days"] == week["days"]:
            signals.append({
                "level": LEVEL_HIGH,
                "type": "week_all_up",
                "message": f"过去{week['days']}个交易日全部加仓，周级别强烈看多",
            })
        elif week["down_days"] == week["days"]:
            signals.append({
                "level": LEVEL_HIGH,
                "type": "week_all_down",
                "message": f"过去{week['days']}个交易日全部减仓，周级别强烈看空",
            })
        
        # ---- 月级别信号 ----
        if len(self.history) >= 20:
            month = self._period_analysis(20)
            
            if abs(month["change_pct"]) >= 10:
                direction = "下降" if month["change_pct"] < 0 else "上升"
                signals.append({
                    "level": LEVEL_HIGH,
                    "type": "month_large_change",
                    "message": f"前5大机构净多头月{direction}{abs(month['change_pct'])}%，趋势显著",
                })
            elif abs(month["change_pct"]) >= 5:
                direction = "下降" if month["change_pct"] < 0 else "上升"
                signals.append({
                    "level": LEVEL_MEDIUM,
                    "type": "month_medium_change",
                    "message": f"前5大机构净多头月{direction}{abs(month['change_pct'])}%",
                })
        
        # ---- 机构长期信号 ----
        broker_trends = self._broker_long_term_trends()
        for bt in broker_trends:
            if bt["data_points"] < 5:
                continue
            
            # 某机构长期大幅变化（>20%）
            if abs(bt["change_pct"]) >= 20:
                direction = "缩水" if bt["change_pct"] < 0 else "增长"
                signals.append({
                    "level": LEVEL_HIGH,
                    "type": "broker_long_term_large",
                    "message": f"{bt['name']} 近{bt['data_points']}个交易日净多头{direction}{abs(bt['change_pct'])}%（{bt['change']:+,}手）",
                })
            elif abs(bt["change_pct"]) >= 10:
                direction = "缩水" if bt["change_pct"] < 0 else "增长"
                signals.append({
                    "level": LEVEL_MEDIUM,
                    "type": "broker_long_term_medium",
                    "message": f"{bt['name']} 近{bt['data_points']}个交易日净多头{direction}{abs(bt['change_pct'])}%",
                })
        
        # ---- 多空比信号 ----
        ratio_trend = self._long_short_ratio_trend()
        if ratio_trend and ratio_trend.get("change", 0) != 0:
            if ratio_trend["trend"] == "空头优势扩大" and abs(ratio_trend["change"]) >= 1:
                signals.append({
                    "level": LEVEL_MEDIUM,
                    "type": "ratio_bearish",
                    "message": f"多空比从{ratio_trend['period_first_ratio']:.1f}降至{ratio_trend['latest_ratio']:.1f}，空头优势扩大",
                })
            elif ratio_trend["trend"] == "多头优势扩大" and abs(ratio_trend["change"]) >= 1:
                signals.append({
                    "level": LEVEL_LOW,
                    "type": "ratio_bullish",
                    "message": f"多空比从{ratio_trend['period_first_ratio']:.1f}升至{ratio_trend['latest_ratio']:.1f}，多头优势扩大",
                })
        
        # 排序
        from .analyzer import LEVEL_ORDER
        signals.sort(key=lambda x: LEVEL_ORDER.get(x["level"], 99))
        
        return signals
