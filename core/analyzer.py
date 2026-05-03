# -*- coding: utf-8 -*-
"""
分析引擎 - 多维度持仓变化分析、四级警示系统（重/高/中/低）
"""

from datetime import datetime, timedelta
from typing import List, Dict, Tuple

from . import db


# 警示级别定义
LEVEL_CRITICAL = "critical"   # 重 - 极度危险，需立即关注
LEVEL_HIGH = "high"           # 高 - 强烈信号，需重点关注
LEVEL_MEDIUM = "medium"       # 中 - 值得警惕
LEVEL_LOW = "low"             # 低 - 轻微变化，留意即可

LEVEL_ORDER = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}

LEVEL_ICON = {
    "critical": "🔴🔴",
    "high": "🔴",
    "medium": "🟡",
    "low": "🟢",
}

LEVEL_LABEL = {
    "critical": "【重】",
    "high": "【高】",
    "medium": "【中】",
    "low": "【低】",
}


class HoldingsAnalyzer:
    """持仓分析器 - 四级警示系统"""
    
    def __init__(self):
        self.history = self._load_history()
    
    def _load_history(self) -> List[Dict]:
        try:
            return db.get_holdings(days=180)
        except Exception:
            return []
    
    def add_today_data(self, today_data: Dict):
        for i, record in enumerate(self.history):
            if record["date"] == today_data["date"]:
                self.history[i] = today_data
                return
        self.history.append(today_data)
    
    def get_net_change_history(self, name: str, days: int = 10) -> List[int]:
        changes = []
        for record in self.history[-days:]:
            for pos in record.get("positions", []):
                if pos["name"] == name:
                    changes.append(pos.get("net_change", 0))
                    break
        return changes
    
    def get_total_net_history(self, days: int = 10) -> List[int]:
        """获取前5大机构净多头合计的历史变化"""
        totals = []
        for record in self.history[-days:]:
            total = sum(p.get("net_change", 0) for p in record.get("positions", []))
            totals.append(total)
        return totals
    
    # ==================== 单机构检测 ====================
    
    def detect_single_day_large_change(self, pos: Dict) -> Tuple[bool, str]:
        """
        【高】单日大幅变化 - 某机构单日净变化超过1000手
        """
        change = pos["net_change"]
        name = pos["name"]
        threshold = 1000
        
        if abs(change) >= 2000:
            direction = "大幅减仓" if change < 0 else "大幅加仓"
            return True, f"{name} 单日{direction} {abs(change):,} 手（超2000手阈值）"
        elif abs(change) >= threshold:
            direction = "减仓" if change < 0 else "加仓"
            return True, f"{name} 单日{direction} {abs(change):,} 手（超1000手阈值）"
        return False, ""
    
    def detect_reversal(self, name: str) -> Tuple[bool, str]:
        """
        【高】突然转向 - 前一天加仓，当天减仓（或反之）
        """
        changes = self.get_net_change_history(name, days=3)
        if len(changes) < 2:
            return False, ""
        
        yesterday = changes[-2]
        today = changes[-1]
        
        if yesterday > 0 and today < 0:
            return True, f"{name} 突然转向：昨日净增{yesterday}手 → 今日净减{abs(today)}手"
        elif yesterday < 0 and today > 0:
            return True, f"{name} 突然转向：昨日净减{abs(yesterday)}手 → 今日净增{today}手"
        
        return False, ""
    
    def detect_consecutive_change(self, name: str) -> Tuple[int, str]:
        """
        【中/高】连续同方向变化 - 3天警示，5天强警示
        """
        changes = self.get_net_change_history(name, days=10)
        if not changes:
            return 0, ""
        
        direction = None
        count = 0
        
        for change in reversed(changes):
            if change == 0:
                break
            if direction is None:
                direction = 1 if change > 0 else -1
                count = 1
            elif (change > 0 and direction == 1) or (change < 0 and direction == -1):
                count += 1
            else:
                break
        
        if count >= 5:
            direction_str = "净增" if direction == 1 else "净减"
            return count, f"{name} 连续{count}天{direction_str}多头"
        elif count >= 3:
            direction_str = "净增" if direction == 1 else "净减"
            return count, f"{name} 连续{count}天{direction_str}多头"
        
        return count, ""
    
    def detect_acceleration(self, name: str) -> Tuple[bool, str]:
        """
        【高】加速变化 - 变化幅度较前一天放大2倍以上
        """
        changes = self.get_net_change_history(name, days=3)
        if len(changes) < 2:
            return False, ""
        
        today = abs(changes[-1])
        yesterday = abs(changes[-2])
        
        if yesterday >= 200 and today >= yesterday * 2:
            ratio = today / yesterday
            return True, f"{name} 变化加速：今日{today:,}手，是昨日{yesterday:,}手的{ratio:.1f}倍"
        
        return False, ""
    
    def detect_net_position_shrink(self, pos: Dict) -> Tuple[bool, str]:
        """
        【中】净多头大幅缩水 - 单日净多头缩水超过10%
        """
        name = pos["name"]
        net = pos["net"]
        change = pos["net_change"]
        
        if net > 0 and change < 0:
            shrink_pct = abs(change) / net * 100
            if shrink_pct >= 20:
                return True, f"{name} 净多头单日缩水 {shrink_pct:.1f}%（{abs(change):,}/{net:,}手）"
            elif shrink_pct >= 10:
                return True, f"{name} 净多头单日缩水 {shrink_pct:.1f}%"
        
        return False, ""
    
    def detect_short_surge(self, pos: Dict) -> Tuple[bool, str]:
        """
        【高】空头暴增 - 某机构空头单日增加超过500手
        """
        name = pos["name"]
        short_change = pos.get("short_change", 0)
        
        if short_change >= 1000:
            return True, f"{name} 空头暴增 {short_change:,} 手"
        elif short_change >= 500:
            return True, f"{name} 空头增加 {short_change:,} 手"
        
        return False, ""
    
    # ==================== 整体市场检测 ====================
    
    def detect_unanimous_direction(self, positions: List[Dict]) -> Tuple[bool, str]:
        """
        【高】一致性方向 - 前5大机构全部同方向操作
        """
        if not positions:
            return False, ""
        
        changes = [p["net_change"] for p in positions]
        all_positive = all(c > 0 for c in changes)
        all_negative = all(c < 0 for c in changes)
        
        if all_positive:
            total = sum(changes)
            return True, f"前5大机构一致加仓！合计净增{total:,}手，强烈看多信号"
        elif all_negative:
            total = sum(changes)
            return True, f"前5大机构一致减仓！合计净减{abs(total):,}手，强烈看空信号"
        
        return False, ""
    
    def detect_total_large_change(self, positions: List[Dict]) -> Tuple[bool, str]:
        """
        【中】整体大幅变化 - 前5大机构净变化合计超过3000手
        """
        total = sum(p["net_change"] for p in positions)
        
        if abs(total) >= 5000:
            direction = "净减" if total < 0 else "净增"
            return True, f"前5大机构合计{direction}{abs(total):,}手（超5000手阈值）"
        elif abs(total) >= 3000:
            direction = "净减" if total < 0 else "净增"
            return True, f"前5大机构合计{direction}{abs(total):,}手（超3000手阈值）"
        
        return False, ""
    
    def detect_consecutive_total_change(self) -> Tuple[int, str]:
        """
        【中/高】整体连续同方向变化
        """
        totals = self.get_total_net_history(days=10)
        if len(totals) < 3:
            return 0, ""
        
        direction = None
        count = 0
        
        for t in reversed(totals):
            if t == 0:
                break
            if direction is None:
                direction = 1 if t > 0 else -1
                count = 1
            elif (t > 0 and direction == 1) or (t < 0 and direction == -1):
                count += 1
            else:
                break
        
        if count >= 5:
            direction_str = "减仓" if direction == -1 else "加仓"
            return count, f"前5大机构连续{count}天{direction_str}，趋势明确"
        elif count >= 3:
            direction_str = "减仓" if direction == -1 else "加仓"
            return count, f"前5大机构连续{count}天{direction_str}"
        
        return count, ""
    
    def detect_top1_dominance_change(self, positions: List[Dict]) -> Tuple[bool, str]:
        """
        【低】龙头变化 - 净多头最大的机构有显著变化
        """
        if not positions:
            return False, ""

        top1 = max(positions, key=lambda p: abs(p.get("net", 0)))
        change = top1["net_change"]

        if abs(change) >= 500:
            direction = "减仓" if change < 0 else "加仓"
            return True, f"龙头{top1['name']}{direction}{abs(change):,}手"

        return False, ""
    
    def detect_multi_broker_large_change(self, positions: List[Dict]) -> Tuple[bool, str]:
        """
        【高】多家机构同时大幅变化 - 2家以上机构单日变化超过500手
        """
        large_count = 0
        large_names = []
        for pos in positions:
            if abs(pos["net_change"]) >= 500:
                large_count += 1
                large_names.append(f"{pos['name']}({abs(pos['net_change']):,}手)")
        
        if large_count >= 3:
            return True, f"{large_count}家机构同时大幅操作：{'、'.join(large_names)}"
        elif large_count >= 2:
            return True, f"{large_count}家机构同时大幅操作：{'、'.join(large_names)}"
        
        return False, ""
    
    # ==================== 主入口 ====================
    
    def generate_alerts(self, today_data: Dict) -> List[Dict]:
        """
        生成今日所有警示（四级：重/高/中/低）
        """
        alerts = []
        positions = today_data.get("positions", [])
        
        # ---- 整体市场级别检测 ----
        
        # 【高】一致性方向
        ok, msg = self.detect_unanimous_direction(positions)
        if ok:
            alerts.append({"level": LEVEL_HIGH, "type": "unanimous", "message": msg})
        
        # 【中】整体大幅变化
        ok, msg = self.detect_total_large_change(positions)
        if ok:
            alerts.append({"level": LEVEL_MEDIUM, "type": "total_large", "message": msg})
        
        # 【高/中】整体连续变化
        days, msg = self.detect_consecutive_total_change()
        if days >= 5:
            alerts.append({"level": LEVEL_HIGH, "type": "total_consecutive_5", "message": msg})
        elif days >= 3:
            alerts.append({"level": LEVEL_MEDIUM, "type": "total_consecutive_3", "message": msg})
        
        # 【高】多家机构同时大幅变化
        ok, msg = self.detect_multi_broker_large_change(positions)
        if ok:
            alerts.append({"level": LEVEL_HIGH, "type": "multi_large", "message": msg})
        
        # ---- 单机构级别检测 ----
        
        for pos in positions:
            name = pos["name"]
            
            # 【高】单日大幅变化（>1000手）
            ok, msg = self.detect_single_day_large_change(pos)
            if ok:
                level = LEVEL_HIGH if abs(pos["net_change"]) >= 2000 else LEVEL_MEDIUM
                alerts.append({"level": level, "type": "large_change", "name": name, "message": msg})
            
            # 【高】突然转向
            ok, msg = self.detect_reversal(name)
            if ok:
                alerts.append({"level": LEVEL_HIGH, "type": "reversal", "name": name, "message": msg})
            
            # 【高】加速变化
            ok, msg = self.detect_acceleration(name)
            if ok:
                alerts.append({"level": LEVEL_HIGH, "type": "acceleration", "name": name, "message": msg})
            
            # 【中】净多头大幅缩水（>10%）
            ok, msg = self.detect_net_position_shrink(pos)
            if ok:
                alerts.append({"level": LEVEL_MEDIUM, "type": "net_shrink", "name": name, "message": msg})
            
            # 【高】空头暴增（>500手）
            ok, msg = self.detect_short_surge(pos)
            if ok:
                level = LEVEL_HIGH if pos.get("short_change", 0) >= 1000 else LEVEL_MEDIUM
                alerts.append({"level": level, "type": "short_surge", "name": name, "message": msg})
            
            # 【中/高】连续变化
            days, msg = self.detect_consecutive_change(name)
            if days >= 5:
                alerts.append({"level": LEVEL_HIGH, "type": "consecutive_5", "name": name, "message": msg})
            elif days >= 3:
                alerts.append({"level": LEVEL_MEDIUM, "type": "consecutive_3", "name": name, "message": msg})
        
        # 【低】龙头变化
        ok, msg = self.detect_top1_dominance_change(positions)
        if ok:
            alerts.append({"level": LEVEL_LOW, "type": "top1_change", "message": msg})
        
        # 去重（同一机构同类型只保留一个）
        seen = set()
        unique_alerts = []
        for alert in alerts:
            key = f"{alert.get('name', '')}_{alert['type']}"
            if key not in seen:
                seen.add(key)
                unique_alerts.append(alert)
        
        # 升级为 critical：多个 high 级别同时触发
        high_count = sum(1 for a in unique_alerts if a["level"] == LEVEL_HIGH)
        if high_count >= 3:
            unique_alerts.insert(0, {
                "level": LEVEL_CRITICAL,
                "type": "multi_high",
                "message": f"⚠️ {high_count}个高级信号同时触发，市场可能发生重大变化！"
            })

        # 单日净变化极端值
        total_net_change = sum(p.get("net_change", 0) for p in positions)
        if abs(total_net_change) >= 5000:
            direction = "加仓" if total_net_change > 0 else "减仓"
            unique_alerts.insert(0, {
                "level": LEVEL_CRITICAL,
                "type": "extreme_total_change",
                "message": f"⚠️ 前5大机构单日合计{direction}{abs(total_net_change):+,}手，极端信号！"
            })
        
        # 按级别排序
        unique_alerts.sort(key=lambda x: LEVEL_ORDER.get(x["level"], 99))
        
        return unique_alerts
    
    def get_summary_stats(self, today_data: Dict) -> Dict:
        positions = today_data.get("positions", [])
        
        total_net = sum(p["net"] for p in positions)
        total_net_change = sum(p["net_change"] for p in positions)
        
        increasing = sum(1 for p in positions if p["net_change"] > 0)
        decreasing = sum(1 for p in positions if p["net_change"] < 0)
        unchanged = sum(1 for p in positions if p["net_change"] == 0)
        
        return {
            "total_net": total_net,
            "total_net_change": total_net_change,
            "increasing_count": increasing,
            "decreasing_count": decreasing,
            "unchanged_count": unchanged,
            "trend": "偏多" if total_net_change > 0 else ("偏空" if total_net_change < 0 else "持平"),
        }
