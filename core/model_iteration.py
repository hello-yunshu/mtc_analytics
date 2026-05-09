# -*- coding: utf-8 -*-
"""
模型自迭代模块
- 无 LLM 时：基于回测准确率的规则化权重微调
- 有 LLM 时：LLM 辅助诊断 + 规则化权重微调
"""

import os
import re
import json
import copy
from datetime import datetime
from typing import Dict, List, Optional, Tuple

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_DATA_DIR = os.path.join(_PROJECT_ROOT, "data")
from .utils import load_json, save_json
from .llm_utils import (
    DEFAULT_LLM_BASE_URL, DEFAULT_LLM_BUDGET, DEFAULT_LLM_MODEL,
    get_model_token_limits, normalize_llm_base_url, normalize_llm_budget,
    normalize_llm_model, prepare_chat_request,
    validate_llm_json, BUDGET_CATEGORIES, BUDGET_CATEGORY_LABELS,
    get_category_budget, get_budget_ratios,
    get_llm_config, get_llm_settings, get_llm_budget,
    call_llm, get_token_usage, save_token_usage,
)
from .db import (
    get_all_prediction_tracking, get_iteration_state,
    update_iteration_state, insert_iteration_history,
    get_iteration_history, delete_latest_iteration_history,
    insert_weight_snapshot, get_weight_snapshot, get_latest_weight_snapshot,
)

MIN_WEIGHT = 0.01
MAX_WEIGHT = 0.25
MIN_FACTOR_OBSERVATIONS = 8
IC_EPSILON = 1e-12
LLM_MAX_TOKENS = 500
CONVERGENCE_THRESHOLD = 3
MAX_TOTAL_ADJUSTMENT = 0.06

FACTOR_PERIOD_MAP = {
    "short": ["price_trend", "volatility", "news_sentiment", "etf_flow"],
    "medium": ["momentum", "extreme", "divergence", "real_rate", "dollar", "inflation"],
    "long": ["cb_gold", "seasonality"],
}

PERIOD_VERIFY_FIELD = {
    "short": ("actual_direction_5d", "actual_change_pct_5d"),
    "medium": ("actual_direction_10d", "actual_change_pct_10d"),
    "long": ("actual_direction_20d", "actual_change_pct_20d"),
}

PERIOD_VERIFY_NAME = {
    "short": "5d",
    "medium": "10d",
    "long": "20d",
}

NEUTRAL_DIRECTIONS = {"", "中性", "中性（不参与准确率统计）"}

PERIOD_LABELS = {
    "short": "短期",
    "medium": "中期",
    "long": "长期",
}

PERIOD_ADJUST_MULTIPLIER = {
    "short": 1.0,
    "medium": 1.2,
    "long": 1.0,
}


def _get_config():
    defaults = {
        "min_samples": 20,
        "max_adjustment": 0.03,
        "llm_budget": DEFAULT_LLM_BUDGET,
        "llm_threshold": 0.4,
        "min_interval_days": 3,
    }
    try:
        from .config import (
            ITERATION_MIN_SAMPLES, ITERATION_MAX_ADJUSTMENT,
            ITERATION_LLM_MONTHLY_BUDGET, ITERATION_LLM_DIAGNOSE_THRESHOLD,
        )
        defaults["min_samples"] = ITERATION_MIN_SAMPLES
        defaults["max_adjustment"] = ITERATION_MAX_ADJUSTMENT
        defaults["llm_budget"] = ITERATION_LLM_MONTHLY_BUDGET
        defaults["llm_threshold"] = ITERATION_LLM_DIAGNOSE_THRESHOLD
    except ImportError:
        pass
    settings = load_json(os.path.join(_DATA_DIR, "web_settings.json")) or {}
    llm_budget = normalize_llm_budget(
        settings.get("llm_budget", settings.get("iteration_llm_budget", defaults["llm_budget"])),
        defaults["llm_budget"],
    )
    return {
        "min_samples": int(settings.get("iteration_min_samples", defaults["min_samples"])),
        "max_adjustment": float(settings.get("iteration_max_adjustment", defaults["max_adjustment"])),
        "llm_budget": llm_budget,
        "llm_threshold": float(settings.get("iteration_llm_threshold", defaults["llm_threshold"])),
        "min_interval_days": int(settings.get("iteration_min_interval_days", defaults["min_interval_days"])),
    }


WEIGHT_KEY_MAP = {
    "real_rate": "w_real_rate",
    "dollar": "w_dollar",
    "inflation": "w_inflation",
    "momentum": "w_momentum",
    "extreme": "w_extreme",
    "divergence": "w_divergence",
    "cb_gold": "w_cb_gold",
    "etf_flow": "w_etf_flow",
    "price_trend": "w_price_trend",
    "volatility": "w_volatility",
    "news_sentiment": "w_news_sentiment",
    "seasonality": "w_seasonality",
}

FACTOR_LABELS = {
    "real_rate": "实际利率",
    "dollar": "美元指数",
    "inflation": "通胀预期",
    "momentum": "持仓动量",
    "extreme": "持仓极值",
    "divergence": "背离信号",
    "cb_gold": "央行购金",
    "etf_flow": "ETF资金流",
    "price_trend": "价格趋势",
    "volatility": "波动率",
    "news_sentiment": "新闻情绪",
    "seasonality": "季节性",
}


def _get_iteration_data() -> Dict:
    state = get_iteration_state()
    history = get_iteration_history(limit=50)
    return {
        "history": history,
        "token_usage": {"month": state.get("token_month", ""), "used": state.get("token_used", 0)},
        "last_iteration_date": state.get("last_iteration_date", ""),
        "total_iterations": state.get("total_iterations", 0),
        "current_weights": state.get("current_weights", {}),
        "consecutive_no_change": state.get("consecutive_no_change", 0),
    }


def _save_iteration_data(data: Dict):
    state = {
        "token_month": data.get("token_usage", {}).get("month", ""),
        "token_used": int(data.get("token_usage", {}).get("used", 0)),
        "last_iteration_date": data.get("last_iteration_date", ""),
        "total_iterations": data.get("total_iterations", 0),
        "current_weights": data.get("current_weights", {}),
        "consecutive_no_change": data.get("consecutive_no_change", 0),
    }
    update_iteration_state(state)


def _save_weight_snapshot(weights: Dict, reason: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"snapshot_{ts}"
    try:
        insert_weight_snapshot(name, reason, copy.deepcopy(weights))
    except Exception:
        pass
    return name


def _rollback_snapshot(snapshot_name: str) -> Optional[Dict]:
    if not snapshot_name or not re.match(r'^snapshot_\d{8}_\d{6}$', snapshot_name):
        return None
    try:
        snap = get_weight_snapshot(snapshot_name)
        if snap:
            return snap.get("weights")
    except Exception:
        pass
    return None


def _get_factor_period(factor_key: str) -> str:
    for period, factors in FACTOR_PERIOD_MAP.items():
        if factor_key in factors:
            return period
    return "medium"


def _is_directional_actual(direction: str) -> bool:
    return str(direction or "") not in NEUTRAL_DIRECTIONS


def _has_period_verification(record: Dict) -> bool:
    for dir_field, _ in PERIOD_VERIFY_FIELD.values():
        if _is_directional_actual(record.get(dir_field, "")):
            return True
    return False


def _is_iteration_sample(record: Dict) -> bool:
    if not record.get("verified"):
        return False
    if record.get("prediction") in ("中性",):
        return False
    return _is_directional_actual(record.get("actual_direction", "")) or _has_period_verification(record)


def _normalize_weights(weights: Dict) -> Dict:
    raw = {k: max(MIN_WEIGHT, float(v)) for k, v in weights.items()}
    if not raw:
        return {}

    remaining = set(raw.keys())
    fixed = {}
    remaining_mass = 1.0

    while remaining:
        total = sum(raw[k] for k in remaining)
        if total <= 0:
            equal = remaining_mass / len(remaining)
            fixed.update({k: equal for k in remaining})
            break

        scaled = {k: raw[k] / total * remaining_mass for k in remaining}
        over_cap = [k for k, v in scaled.items() if v > MAX_WEIGHT]
        if not over_cap:
            fixed.update(scaled)
            break

        for k in over_cap:
            fixed[k] = MAX_WEIGHT
            remaining.remove(k)
        remaining_mass = max(0.0, 1.0 - sum(fixed.values()))
        if remaining_mass <= 0:
            break

    total = sum(fixed.values())
    if total > 0:
        return {k: v / total for k, v in fixed.items()}
    return raw


def analyze_factor_accuracy(tracking: List[Dict], use_period_verify: bool = True) -> Dict:
    """
    分析每个因子的预测贡献准确率 + 信息系数(IC)
    当 use_period_verify=True 时，使用对应周期的验证字段：
      - 短期因子用5日验证
      - 中期因子用10日验证
      - 长期因子用20日验证
    新记录必须使用对应周期验证；旧记录缺少 verified_periods 时才回退到1日验证
    """
    cfg = _get_config()
    verified = [r for r in tracking if _is_iteration_sample(r)]
    if len(verified) < cfg["min_samples"]:
        return {}

    factor_stats = {}
    for factor_key in WEIGHT_KEY_MAP:
        factor_period = _get_factor_period(factor_key)
        dir_field, pct_field = PERIOD_VERIFY_FIELD.get(factor_period, ("actual_direction", "actual_change_pct"))

        correct = 0
        total = 0
        score_sum_correct = 0.0
        ic_numerators = []
        ic_denominators_x = []
        ic_denominators_y = []
        for r in verified:
            factors = r.get("factors_summary", {})
            f_info = factors.get(factor_key, {})
            f_score = f_info.get("score", 0)
            if abs(f_score) < 0.05:
                continue

            verified_periods = r.get("verified_periods", []) or []
            expected_period = PERIOD_VERIFY_NAME.get(factor_period)
            actual_dir = r.get(dir_field, "")
            actual_pct = r.get(pct_field, 0)
            if use_period_verify and verified_periods and expected_period not in verified_periods:
                continue
            if not _is_directional_actual(actual_dir):
                if use_period_verify and verified_periods:
                    continue
                actual_dir = r.get("actual_direction", "")
                actual_pct = r.get("actual_change_pct", 0)
                if not _is_directional_actual(actual_dir):
                    continue

            if actual_pct is None:
                actual_pct = 0
            try:
                actual_pct = float(actual_pct)
            except (ValueError, TypeError):
                actual_pct = 0.0

            total += 1
            f_direction = "看多" if f_score > 0 else "看空"
            if f_direction == actual_dir:
                correct += 1
                score_sum_correct += abs(f_score)

            ic_numerators.append(f_score)
            ic_denominators_x.append(f_score)
            ic_denominators_y.append(actual_pct)

        ic = None
        ic_significant = False
        if len(ic_numerators) >= 10:
            n = len(ic_numerators)
            mean_x = sum(ic_denominators_x) / n
            mean_y = sum(ic_denominators_y) / n
            cov_xy = sum((ic_denominators_x[i] - mean_x) * (ic_denominators_y[i] - mean_y) for i in range(n))
            var_x = sum((x - mean_x) ** 2 for x in ic_denominators_x)
            var_y = sum((y - mean_y) ** 2 for y in ic_denominators_y)
            pearson_ic = None
            if var_x > IC_EPSILON and var_y > IC_EPSILON:
                pearson_ic = cov_xy / (var_x ** 0.5 * var_y ** 0.5)

            sorted_idx_x = sorted(range(n), key=lambda i: ic_denominators_x[i])
            sorted_idx_y = sorted(range(n), key=lambda i: ic_denominators_y[i])
            rank_x = [0] * n
            rank_y = [0] * n
            for r, idx in enumerate(sorted_idx_x):
                rank_x[idx] = r + 1
            for r, idx in enumerate(sorted_idx_y):
                rank_y[idx] = r + 1
            d_sq = sum((rank_x[i] - rank_y[i]) ** 2 for i in range(n))
            spearman_ic = 1 - 6 * d_sq / (n * (n ** 2 - 1)) if n > 1 else 0.0

            ic = spearman_ic if abs(spearman_ic) <= 1.0 else pearson_ic
            ic_significant = abs(ic) > 2.0 / (n ** 0.5)

        if total > 0:
            factor_stats[factor_key] = {
                "correct": correct,
                "total": total,
                "accuracy": correct / total,
                "avg_score_when_correct": score_sum_correct / correct if correct > 0 else 0,
                "ic": round(ic, 4) if ic is not None else None,
                "ic_significant": ic_significant,
                "period": factor_period,
            }
    return factor_stats


def compute_overall_accuracy(tracking: List[Dict]) -> Dict:
    """计算整体和近期准确率（近期使用EWMA加权）+ Brier Score"""
    verified = [
        r for r in tracking
        if r.get("verified")
        and _is_directional_actual(r.get("actual_direction", ""))
    ]
    if not verified:
        return {"total": 0, "correct": 0, "accuracy": 0, "recent_accuracy": 0, "brier_score": None}

    directional = [r for r in verified if r.get("prediction") not in ("中性",)]
    total = len(directional)
    correct = sum(1 for r in directional if r.get("prediction") == r.get("actual_direction")) if directional else 0

    brier_score = None
    all_verified = verified
    if len(all_verified) >= 10:
        brier_sum = 0.0
        for r in all_verified:
            pred = r.get("prediction", "中性")
            conf = r.get("confidence", 50) / 100.0
            actual_dir = r.get("actual_direction", "中性")
            if pred == "看多":
                forecast = conf
            elif pred == "看空":
                forecast = 1.0 - conf
            else:
                forecast = 0.5
            if actual_dir == "看多":
                outcome = 1.0
            elif actual_dir == "看空":
                outcome = 0.0
            else:
                outcome = 0.5
            brier_sum += (forecast - outcome) ** 2
        brier_score = round(brier_sum / len(all_verified), 4)

    recent = directional[-20:] if directional else []
    ewma_alpha = 0.15
    ewma_acc = 0.5
    for r in recent:
        is_correct = 1.0 if r.get("prediction") == r.get("actual_direction") else 0.0
        ewma_acc = ewma_alpha * is_correct + (1 - ewma_alpha) * ewma_acc

    recent_correct = sum(1 for r in recent if r.get("prediction") == r.get("actual_direction"))

    return {
        "total": total,
        "correct": correct,
        "accuracy": correct / total if total > 0 else 0,
        "total_with_neutral": len(verified),
        "recent_total": len(recent),
        "recent_correct": recent_correct,
        "recent_accuracy": ewma_acc,
        "brier_score": brier_score,
    }


def rule_based_adjust(factor_stats: Dict, current_weights: Dict) -> Tuple[Dict, List[str]]:
    """
    基于规则微调权重（准确率 + IC 双指标）
    - 准确率 > 60% 且 IC > 0 的因子微增权重
    - 准确率 < 40% 或 IC < -0.1 的因子微减权重
    - IC 权重占40%，准确率权重占60%
    - 中期因子调整系数更大（1.5倍），确保中期准确率优先提升
    """
    cfg = _get_config()
    adjustments = {}
    reasons = []
    new_weights = copy.deepcopy(current_weights)

    for factor, stats in factor_stats.items():
        if factor not in new_weights:
            continue
        acc = stats["accuracy"]
        if stats.get("total", 0) < MIN_FACTOR_OBSERVATIONS:
            continue
        ic_raw = stats.get("ic")
        try:
            ic = float(ic_raw) if ic_raw is not None else 0.0
        except (TypeError, ValueError):
            ic = 0.0
        factor_period = stats.get("period", _get_factor_period(factor))
        period_mult = PERIOD_ADJUST_MULTIPLIER.get(factor_period, 1.0)
        period_label = PERIOD_LABELS.get(factor_period, "")
        sample_weight = min(1.0, stats.get("total", 0) / max(cfg["min_samples"], 1))

        combined_signal = 0.0
        if acc > 0.6:
            combined_signal += (acc - 0.5) * 0.6
        elif acc < 0.4:
            combined_signal -= (0.5 - acc) * 0.6

        if ic > 0.05:
            combined_signal += ic * 0.4
        elif ic < -0.05:
            combined_signal += ic * 0.4
        combined_signal *= sample_weight

        if combined_signal > 0.01:
            delta = min(cfg["max_adjustment"] * period_mult, combined_signal * 0.06 * period_mult)
            adjustments[factor] = delta
            reasons.append(f"{period_label}{FACTOR_LABELS.get(factor, factor)}准确率{acc:.0%}/IC={ic:+.2f}，权重+{delta:.3f}")
        elif combined_signal < -0.01:
            delta = -min(cfg["max_adjustment"] * period_mult, abs(combined_signal) * 0.06 * period_mult)
            adjustments[factor] = delta
            reasons.append(f"{period_label}{FACTOR_LABELS.get(factor, factor)}准确率{acc:.0%}/IC={ic:+.2f}，权重{delta:.3f}")

    for factor, delta in adjustments.items():
        new_weights[factor] = max(MIN_WEIGHT, new_weights.get(factor, 0.05) + delta)

    new_weights = _normalize_weights(new_weights)

    return new_weights, reasons


def llm_diagnose(tracking: List[Dict], factor_stats: Dict, overall_acc: Dict, *,
                 market_name: str = "黄金", factor_labels: Dict = None,
                 weight_key_map: Dict = None) -> Optional[Dict]:
    """
    使用 LLM 诊断模型失败原因并给出调整建议
    仅在近期准确率低于阈值时触发
    """
    cfg = _get_config()
    api_key, base_url, model, enabled = get_llm_config()
    if not enabled:
        return None

    _factor_labels = factor_labels or FACTOR_LABELS
    _weight_key_map = weight_key_map or WEIGHT_KEY_MAP

    recent_acc = overall_acc.get("recent_accuracy", 1.0)
    if recent_acc >= cfg["llm_threshold"] and overall_acc.get("accuracy", 1.0) >= cfg["llm_threshold"]:
        return None

    verified = [r for r in tracking if _is_iteration_sample(r)]
    recent_wrong = [r for r in verified[-10:]
                    if r.get("prediction") != r.get("actual_direction")]
    if not recent_wrong:
        return None

    cases_text = ""
    for i, r in enumerate(recent_wrong[-5:]):
        factors_brief = []
        for fk, fv in r.get("factors_summary", {}).items():
            s = fv.get("score", 0)
            if abs(s) >= 0.1:
                factors_brief.append(f"{_factor_labels.get(fk, fk)}:{s:+.1f}")
        cases_text += f"\n{i+1}. 预测{r['prediction']}(置信{r.get('confidence',0)}%) 实际{r['actual_direction']}({r.get('actual_change_pct',0):+.1f}%) 因子:{' '.join(factors_brief)}"

    factor_acc_text = ""
    for fk, stats in factor_stats.items():
        factor_acc_text += f"\n{_factor_labels.get(fk, fk)}: {stats['accuracy']:.0%}({stats['correct']}/{stats['total']})"

    valid_factors = ",".join(_weight_key_map.keys())

    history_text = ""
    try:
        recent_history = get_iteration_history(limit=3)
        for h in recent_history:
            adj_count = len(h.get("adjustments", []))
            history_text += f"\n- {h.get('date','')}: {h.get('mode','')} 调整{adj_count}项, 准确率{h.get('overall_accuracy',0):.0%}"
    except Exception:
        pass

    system_msg = {
        "role": "system",
        "content": (
            f"你是一位量化模型诊断专家。分析{market_name}预测模型的失败原因，给出因子权重调整建议。"
            "必须严格输出JSON格式，不要输出其他内容。"
        ),
    }
    user_msg = {
        "role": "user",
        "content": (
            f"{market_name}预测模型近期准确率仅{recent_acc:.0%}，分析失败原因并建议权重调整。\n"
            f"整体准确率:{overall_acc.get('accuracy',0):.0%}\n"
            f"近期错误案例:{cases_text}\n"
            f"各因子准确率:{factor_acc_text}\n"
            f"近期迭代历史:{history_text if history_text else '无'}\n"
            f'输出JSON:{{"diagnosis":"1句诊断","suggestions":[{{"factor":"因子名","action":"up/down","reason":"原因"}}],"confidence":0.8}}\n'
            f"factor必须为: {valid_factors}\n"
            "要求：1.diagnosis简洁精准 2.suggestions中reason需说明为何上调或下调 3.confidence为0-1的置信度 4.避免重复历史已尝试的调整"
        ),
    }
    messages = [system_msg, user_msg]

    result = call_llm(messages, category="diagnose",
                      max_tokens=LLM_MAX_TOKENS, temperature=0.2,
                      timeout=30, log_prefix="迭代")
    if result is None:
        return None

    content = result["content"]
    parsed = validate_llm_json(
        content,
        required_keys=["diagnosis", "suggestions", "confidence"],
        key_types={"diagnosis": str, "suggestions": list, "confidence": (int, float)},
    )
    if parsed is None:
        print("  [迭代] LLM 返回格式异常，跳过诊断")
        return None

    if not isinstance(parsed.get("suggestions"), list):
        print("  [迭代] LLM suggestions 格式异常，跳过诊断")
        return None

    for sug in parsed.get("suggestions", []):
        if not isinstance(sug, dict):
            continue
        factor = sug.get("factor", "")
        if factor not in _weight_key_map:
            sug["factor"] = ""
        action = sug.get("action", "")
        if action not in ("up", "down"):
            sug["action"] = ""
    parsed["suggestions"] = [s for s in parsed["suggestions"] if s.get("factor") and s.get("action")]

    print(f"  [迭代] LLM 诊断完成: {parsed.get('diagnosis', '')[:60]}")
    return parsed


def apply_llm_suggestions(llm_result: Dict, current_weights: Dict) -> Tuple[Dict, List[str]]:
    """将 LLM 建议转化为权重调整，调整幅度为规则调整的50%，中期因子调整幅度更大"""
    cfg = _get_config()
    new_weights = copy.deepcopy(current_weights)
    reasons = []

    llm_max_adj = cfg["max_adjustment"] * 0.5
    try:
        llm_confidence = float(llm_result.get("confidence", 0.5))
    except (TypeError, ValueError):
        llm_confidence = 0.5
    llm_confidence = max(0.0, min(1.0, llm_confidence))
    if llm_confidence < 0.4:
        return new_weights, reasons
    confidence_scale = max(0.4, llm_confidence)

    for sug in llm_result.get("suggestions", []):
        factor = sug.get("factor", "")
        action = sug.get("action", "")
        reason = sug.get("reason", "")

        if factor not in WEIGHT_KEY_MAP or factor not in new_weights:
            continue

        factor_period = _get_factor_period(factor)
        period_mult = PERIOD_ADJUST_MULTIPLIER.get(factor_period, 1.0)
        period_label = PERIOD_LABELS.get(factor_period, "")

        if action == "up":
            delta = min(llm_max_adj * period_mult, 0.01 * period_mult) * confidence_scale
            new_weights[factor] = new_weights.get(factor, 0.05) + delta
            reasons.append(f"LLM建议↑{period_label}{FACTOR_LABELS.get(factor, factor)}: {reason[:30]}")
        elif action == "down":
            delta = min(llm_max_adj * period_mult, 0.01 * period_mult) * confidence_scale
            new_weights[factor] = max(MIN_WEIGHT, new_weights.get(factor, 0.05) - delta)
            reasons.append(f"LLM建议↓{period_label}{FACTOR_LABELS.get(factor, factor)}: {reason[:30]}")

    new_weights = _normalize_weights(new_weights)

    return new_weights, reasons


def _write_weights_to_settings(new_weights: Dict):
    settings = load_json(os.path.join(_DATA_DIR, "web_settings.json")) or {}
    for factor_key, setting_key in WEIGHT_KEY_MAP.items():
        if factor_key in new_weights:
            settings[setting_key] = round(new_weights[factor_key], 4)
    save_json(os.path.join(_DATA_DIR, "web_settings.json"), settings)


def _adjust_pred_threshold(overall_acc: Dict) -> Optional[str]:
    acc = overall_acc.get("accuracy", 0.5)
    recent_acc = overall_acc.get("recent_accuracy", 0.5)
    total = overall_acc.get("total", 0)
    settings = load_json(os.path.join(_DATA_DIR, "web_settings.json")) or {}
    current = float(settings.get("pred_threshold", 0.08))
    new_val = current

    if total < 40:
        return None

    consecutive_high = 0
    consecutive_low = 0
    try:
        history = get_iteration_history(limit=5)
        for h in history:
            h_acc = h.get("overall_accuracy", 0.5)
            h_recent = h.get("recent_accuracy", 0.5)
            if h_acc > 0.6 and h_recent > 0.6:
                consecutive_high += 1
            else:
                consecutive_high = 0
            if h_acc < 0.4 or h_recent < 0.4:
                consecutive_low += 1
            else:
                consecutive_low = 0
    except Exception:
        pass

    if acc > 0.65 and recent_acc > 0.65 and consecutive_high >= 2:
        delta = min(0.005, (acc - 0.5) * 0.004)
        new_val = max(0.05, current - delta)
    elif (acc < 0.35 or recent_acc < 0.35) and consecutive_low >= 2:
        delta = min(0.01, (0.5 - min(acc, recent_acc)) * 0.006)
        new_val = min(0.20, current + delta)

    if abs(new_val - current) < 0.001:
        return None
    settings["pred_threshold"] = round(new_val, 3)
    save_json(os.path.join(_DATA_DIR, "web_settings.json"), settings)
    direction = "降低" if new_val < current else "提高"
    return f"预测阈值{direction}至{new_val:.3f}（准确率{acc:.0%}）"


def _consensus_based_adjust(tracking: List[Dict], current_weights: Dict, new_weights: Dict) -> List[str]:
    """
    基于机构共识对比的迭代调整

    逻辑：
    1. 统计近期预测中模型与机构共识的背离情况
    2. 当模型与共识背离且共识正确时，调整相关因子权重
    3. 当模型与共识一致且共识正确时，增强相关因子权重
    4. 中期因子调整幅度更大
    """
    reasons = []

    recent_with_consensus = []
    for r in tracking:
        if not r.get("verified"):
            continue
        if r.get("prediction") in ("中性",):
            continue
        if not _is_directional_actual(r.get("actual_direction", "")):
            continue
        alignment = r.get("consensus_alignment", {})
        if not alignment or not isinstance(alignment, dict):
            continue
        if alignment.get("alignment") == "no_data":
            continue
        recent_with_consensus.append(r)

    if len(recent_with_consensus) < 5:
        return reasons

    recent_with_consensus.sort(key=lambda x: x.get("date", ""), reverse=True)
    recent = recent_with_consensus[:20]

    divergent_consensus_correct = 0
    divergent_model_correct = 0
    aligned_correct = 0
    aligned_total = 0

    for r in recent:
        alignment = r.get("consensus_alignment", {})
        actual_dir = r.get("actual_direction", "")
        if not _is_directional_actual(actual_dir):
            continue
        consensus_dir = alignment.get("consensus_direction", "")
        model_dir = r.get("prediction", "")

        if alignment.get("alignment") == "divergent":
            if consensus_dir == actual_dir:
                divergent_consensus_correct += 1
            elif model_dir == actual_dir:
                divergent_model_correct += 1
        elif alignment.get("alignment") == "aligned":
            aligned_total += 1
            if model_dir == actual_dir:
                aligned_correct += 1

    divergent_total = divergent_consensus_correct + divergent_model_correct

    if divergent_total >= 3 and divergent_consensus_correct > divergent_model_correct:
        consensus_dominant_factors = {
            "momentum": 0.015, "extreme": 0.01, "divergence": 0.01,
            "cb_gold": 0.008, "etf_flow": 0.008,
            "real_rate": 0.005, "dollar": 0.005, "inflation": 0.005,
        }
        for factor, delta in consensus_dominant_factors.items():
            if factor in new_weights:
                factor_period = _get_factor_period(factor)
                period_mult = PERIOD_ADJUST_MULTIPLIER.get(factor_period, 1.0)
                adjusted_delta = delta * period_mult
                old_w = new_weights[factor]
                new_weights[factor] = max(MIN_WEIGHT, old_w + adjusted_delta)

        normalized = _normalize_weights(new_weights)
        new_weights.clear()
        new_weights.update(normalized)

        reasons.append(
            f"机构共识修正：近期{divergent_total}次背离中共识正确{divergent_consensus_correct}次，"
            f"微调因子权重向共识靠拢"
        )

    if aligned_total >= 3 and aligned_correct / aligned_total > 0.6:
        reasons.append(
            f"机构共识验证：近期{aligned_total}次一致预测中{aligned_correct}次正确"
            f"（{aligned_correct/aligned_total:.0%}），模型与共识协同良好"
        )

    return reasons


def run_iteration(force: bool = False) -> Dict:
    cfg = _get_config()
    tracking = get_all_prediction_tracking(days=365)
    result = {
        "status": "skipped",
        "reason": "",
        "adjustments": [],
        "diagnosis": "",
        "mode": "none",
        "timestamp": datetime.now().isoformat(),
    }

    verified = [r for r in tracking if _is_iteration_sample(r)]

    if len(verified) < cfg["min_samples"] and not force:
        result["reason"] = f"已验证非中性样本不足（{len(verified)}/{cfg['min_samples']}），暂不启动自迭代"
        print(f"  [迭代] {result['reason']}")
        return result

    iter_data = _get_iteration_data()
    today = datetime.now().strftime("%Y-%m-%d")
    if iter_data.get("last_iteration_date") == today and not force:
        result["reason"] = "今日已执行过自迭代"
        print(f"  [迭代] {result['reason']}")
        return result

    min_interval = cfg.get("min_interval_days", 3)
    last_iter_date = iter_data.get("last_iteration_date", "")
    if last_iter_date and not force:
        try:
            last_dt = datetime.strptime(last_iter_date, "%Y-%m-%d")
            days_since = (datetime.now() - last_dt).days
            if days_since < min_interval:
                result["reason"] = f"距上次迭代仅{days_since}天（最小间隔{min_interval}天），跳过"
                print(f"  [迭代] {result['reason']}")
                return result
        except ValueError:
            pass

    overall_acc = compute_overall_accuracy(tracking)
    factor_stats = analyze_factor_accuracy(tracking)

    if not factor_stats:
        result["reason"] = "因子统计数据不足"
        return result

    settings = load_json(os.path.join(_DATA_DIR, "web_settings.json")) or {}
    current_weights = {}
    for factor_key, setting_key in WEIGHT_KEY_MAP.items():
        if setting_key in settings and settings[setting_key] is not None:
            current_weights[factor_key] = float(settings[setting_key])
        else:
            current_weights[factor_key] = {
                "real_rate": 0.16, "dollar": 0.12, "inflation": 0.09,
                "momentum": 0.08, "extreme": 0.05, "divergence": 0.07,
                "cb_gold": 0.07, "etf_flow": 0.06, "price_trend": 0.10,
                "volatility": 0.06, "news_sentiment": 0.09, "seasonality": 0.05,
            }.get(factor_key, 0.05)

    snapshot_name = _save_weight_snapshot(current_weights, "自迭代前快照")

    new_weights, rule_reasons = rule_based_adjust(factor_stats, current_weights)

    api_key, base_url, model, llm_enabled = get_llm_config()
    llm_budget = get_llm_budget()
    llm_result = None
    llm_reasons = []

    if llm_enabled and llm_budget > 0:
        llm_result = llm_diagnose(tracking, factor_stats, overall_acc)
        if llm_result:
            new_weights, llm_reasons = apply_llm_suggestions(llm_result, new_weights)
            result["diagnosis"] = llm_result.get("diagnosis", "")
            result["mode"] = "llm+rule"
        else:
            result["mode"] = "rule"
    else:
        result["mode"] = "rule"

    all_reasons = rule_reasons + llm_reasons

    consensus_reasons = _consensus_based_adjust(tracking, current_weights, new_weights)
    if consensus_reasons:
        all_reasons.extend(consensus_reasons)

    total_adj = sum(abs(new_weights.get(k, 0) - current_weights.get(k, 0)) for k in new_weights)
    if total_adj > MAX_TOTAL_ADJUSTMENT:
        scale = MAX_TOTAL_ADJUSTMENT / total_adj
        for k in new_weights:
            delta = new_weights[k] - current_weights.get(k, 0)
            new_weights[k] = current_weights.get(k, 0) + delta * scale
        new_weights = _normalize_weights(new_weights)
        all_reasons.append(f"全局调整约束：总调整{total_adj:.4f}超限，缩放至{MAX_TOTAL_ADJUSTMENT}")

    effective_adj = sum(abs(new_weights.get(k, 0) - current_weights.get(k, 0)) for k in new_weights)
    if effective_adj < 0.001:
        all_reasons = []

    if not all_reasons:
        result["status"] = "no_change"
        result["reason"] = "所有因子准确率在正常范围，无需调整"
        iter_data["consecutive_no_change"] = iter_data.get("consecutive_no_change", 0) + 1
        if iter_data["consecutive_no_change"] >= CONVERGENCE_THRESHOLD:
            result["status"] = "converged"
            result["reason"] = f"连续{iter_data['consecutive_no_change']}次无调整，模型已收敛"
        _save_iteration_data(iter_data)
        print(f"  [迭代] {result['reason']}")
        return result

    _write_weights_to_settings(new_weights)

    threshold_adj = _adjust_pred_threshold(overall_acc)
    if threshold_adj:
        all_reasons.append(threshold_adj)

    iter_data["last_iteration_date"] = today
    iter_data["total_iterations"] = iter_data.get("total_iterations", 0) + 1
    iter_data["current_weights"] = new_weights
    iter_data["consecutive_no_change"] = 0
    history_record = {
        "date": today,
        "timestamp": datetime.now().isoformat(),
        "mode": result["mode"],
        "overall_accuracy": overall_acc.get("accuracy", 0),
        "recent_accuracy": overall_acc.get("recent_accuracy", 0),
        "verified_samples": len(verified),
        "adjustments": all_reasons,
        "snapshot": snapshot_name,
        "diagnosis": result.get("diagnosis", ""),
    }
    insert_iteration_history(history_record)
    _save_iteration_data(iter_data)

    result["status"] = "adjusted"
    result["adjustments"] = all_reasons
    result["new_weights"] = new_weights
    result["snapshot"] = snapshot_name

    print(f"  [迭代] 完成（模式:{result['mode']}）调整{len(all_reasons)}项:")
    for r in all_reasons:
        print(f"    - {r}")
    if result.get("diagnosis"):
        print(f"    诊断: {result['diagnosis'][:80]}")

    return result


def get_iteration_status() -> Dict:
    cfg = _get_config()
    iter_data = _get_iteration_data()
    tracking = get_all_prediction_tracking(days=365)

    verified = [
        r for r in tracking
        if r.get("verified")
        and r.get("prediction") not in ("中性",)
        and _is_directional_actual(r.get("actual_direction", ""))
    ]

    overall_acc = compute_overall_accuracy(tracking)
    factor_stats = analyze_factor_accuracy(tracking)

    api_key, _, model, llm_enabled = get_llm_config()
    llm_budget = get_llm_budget()
    llm_available = llm_enabled and llm_budget > 0
    llm_settings = get_llm_settings()
    llm_limits = get_model_token_limits(model, llm_settings)

    status = {
        "enabled": len(verified) >= cfg["min_samples"],
        "verified_samples": len(verified),
        "min_samples_required": cfg["min_samples"],
        "overall_accuracy": overall_acc,
        "factor_accuracy": {
            FACTOR_LABELS.get(k, k): {
                "accuracy": f"{v['accuracy']:.0%}",
                "correct": v["correct"],
                "total": v["total"],
                "ic": f"{v.get('ic', 0):+.2f}",
                "significant": _binomial_test(v["correct"], v["total"], bonferroni_factor=len(WEIGHT_KEY_MAP)),
                "period": PERIOD_LABELS.get(v.get("period", ""), ""),
            }
            for k, v in factor_stats.items()
        },
        "period_accuracy": _compute_period_accuracy(factor_stats),
        "consensus_stats": _compute_consensus_stats(tracking),
        "llm_available": llm_available,
        "llm_model": model,
        "llm_model_limits": llm_limits,
        "mode": "llm+rule" if llm_available else "rule",
        "last_iteration": iter_data.get("last_iteration_date", "从未"),
        "total_iterations": iter_data.get("total_iterations", 0),
        "token_usage": get_token_usage(),
        "token_budget": llm_budget,
        "token_budget_categories": {
            cat: {
                "ratio": ratio,
                "budget": get_category_budget(llm_budget, cat, llm_settings),
                "used": get_token_usage().get("categories", {}).get(cat, {}).get("used", 0),
                "label": BUDGET_CATEGORY_LABELS.get(cat, cat),
            }
            for cat, ratio in get_budget_ratios(llm_settings).items()
        },
        "recent_history": iter_data.get("history", [])[-5:],
        "current_weights": iter_data.get("current_weights", {}),
        "consecutive_no_change": iter_data.get("consecutive_no_change", 0),
        "converged": iter_data.get("consecutive_no_change", 0) >= CONVERGENCE_THRESHOLD,
        "pred_threshold": float((load_json(os.path.join(_DATA_DIR, "web_settings.json")) or {}).get("pred_threshold", 0.08)),
        "benchmark": _compute_benchmark(tracking),
        "token_efficiency": _compute_token_efficiency(llm_budget, llm_settings),
    }
    return status


def _compute_token_efficiency(total_budget: int, settings: Dict) -> Dict:
    token_usage = get_token_usage()
    month_key = datetime.now().strftime("%Y-%m")
    total_used = 0
    if token_usage.get("month") == month_key:
        total_used = int(token_usage.get("used", 0))

    now = datetime.now()
    day_of_month = now.day
    days_in_month = 30

    daily_avg = total_used / max(1, day_of_month)
    projected_monthly = daily_avg * days_in_month
    burn_rate = projected_monthly / total_budget if total_budget > 0 else 0

    category_alerts = {}
    cat_usage = token_usage.get("categories", {})
    for cat, ratio in get_budget_ratios(settings).items():
        cat_budget = get_category_budget(total_budget, cat, settings)
        cat_used = int(cat_usage.get(cat, {}).get("used", 0)) if cat_usage.get(cat, {}).get("month") == month_key else 0
        usage_pct = cat_used / cat_budget if cat_budget > 0 else 0
        if usage_pct > 0.8:
            category_alerts[cat] = f"{BUDGET_CATEGORY_LABELS.get(cat, cat)}预算已用{usage_pct:.0%}"

    return {
        "total_used": total_used,
        "total_budget": total_budget,
        "usage_pct": round(total_used / total_budget, 3) if total_budget > 0 else 0,
        "daily_avg": round(daily_avg, 0),
        "projected_monthly": round(projected_monthly, 0),
        "burn_rate": round(burn_rate, 2),
        "burn_rate_alert": burn_rate > 1.2,
        "category_alerts": category_alerts,
    }


def _compute_benchmark(tracking: List[Dict]) -> Dict:
    """
    计算模型性能基准：与随机游走(50%准确率)对比
    返回模型vs基准的胜率差和统计显著性
    """
    verified = [
        r for r in tracking
        if r.get("verified")
        and r.get("prediction") not in ("中性",)
        and _is_directional_actual(r.get("actual_direction", ""))
    ]
    if len(verified) < 10:
        return {"available": False, "reason": "样本不足"}

    correct = sum(1 for r in verified if r.get("prediction") == r.get("actual_direction"))
    model_acc = correct / len(verified)
    baseline_acc = 0.5

    import math
    n = len(verified)
    se = math.sqrt(0.5 * 0.5 / n)
    z_score = (model_acc - baseline_acc) / se if se > 0 else 0

    if z_score > 1.96:
        significance = "显著优于随机(p<0.05)"
    elif z_score > 1.645:
        significance = "可能优于随机(p<0.10)"
    elif z_score < -1.645:
        significance = "显著劣于随机"
    else:
        significance = "与随机无显著差异"

    bullish_count = sum(1 for r in verified if r.get("prediction") == "看多")
    bearish_count = sum(1 for r in verified if r.get("prediction") == "看空")
    bias = "偏多" if bullish_count > bearish_count * 1.3 else ("偏空" if bearish_count > bullish_count * 1.3 else "均衡")

    return {
        "available": True,
        "model_accuracy": f"{model_acc:.1%}",
        "random_baseline": "50.0%",
        "edge": f"{(model_acc - baseline_acc) * 100:+.1f}%",
        "z_score": round(z_score, 2),
        "significance": significance,
        "total_predictions": n,
        "directional_bias": bias,
    }


def grid_search_weights(tracking: List[Dict], current_weights: Dict,
                         step: float = 0.02, top_k: int = 3) -> Dict:
    """
    简化版网格搜索：对每个因子权重在 ±2*step 范围内搜索
    使用滚动窗口时间序列交叉验证，避免过拟合
    仅在总样本≥50时执行
    """
    verified = [
        r for r in tracking
        if r.get("verified")
        and r.get("prediction") not in ("中性",)
        and _is_directional_actual(r.get("actual_direction", ""))
    ]
    if len(verified) < 50:
        return {"available": False, "reason": f"样本不足({len(verified)}/50)"}

    n = len(verified)
    n_splits = max(2, n // 25)
    fold_size = n // n_splits
    if fold_size < 10:
        return {"available": False, "reason": f"每折样本不足({fold_size}/10)"}

    factor_keys = list(current_weights.keys())
    best_weights = dict(current_weights)
    best_val_acc = 0.0

    for _ in range(top_k):
        improved = False
        for fk in factor_keys:
            orig_w = best_weights.get(fk, 0.05)
            for delta in [-2 * step, -step, step, 2 * step]:
                test_w = dict(best_weights)
                test_w[fk] = max(MIN_WEIGHT, orig_w + delta)
                total = sum(test_w.values())
                if total <= 0:
                    continue
                test_w = {k: v / total for k, v in test_w.items()}

                val_correct = 0
                val_total = 0

                for fold_idx in range(n_splits):
                    val_start = fold_idx * fold_size
                    val_end = min(val_start + fold_size, n)
                    if fold_idx == n_splits - 1:
                        val_end = n

                    for i in range(val_start, val_end):
                        r = verified[i]
                        factors = r.get("factors_summary", {})
                        score = sum(
                            factors.get(k, {}).get("score", 0) * test_w.get(k, 0)
                            for k in factor_keys
                        )
                        threshold = float((load_json(os.path.join(_DATA_DIR, "web_settings.json")) or {}).get("pred_threshold", 0.08))
                        if score > threshold:
                            pred_dir = "看多"
                        elif score < -threshold:
                            pred_dir = "看空"
                        else:
                            continue
                        val_total += 1
                        if pred_dir == r.get("actual_direction", ""):
                            val_correct += 1

                acc = val_correct / val_total if val_total > 0 else 0
                if acc > best_val_acc:
                    best_val_acc = acc
                    best_weights = dict(test_w)
                    improved = True

        if not improved:
            break

    changes = {}
    for k in factor_keys:
        diff = best_weights.get(k, 0) - current_weights.get(k, 0)
        if abs(diff) > 0.001:
            changes[FACTOR_LABELS.get(k, k)] = f"{diff:+.3f}"

    return {
        "available": True,
        "best_accuracy": f"{best_val_acc:.1%}",
        "current_accuracy": f"{sum(1 for r in verified if r.get('prediction') == r.get('actual_direction')) / len(verified):.1%}",
        "weight_changes": changes,
        "best_weights": best_weights,
    }


def _binomial_test(correct: int, total: int, p_null: float = 0.5,
                   bonferroni_factor: int = 1) -> str:
    """
    二项检验：检验准确率是否显著偏离随机水平(p=0.5)
    使用正态近似 + Bonferroni多重比较校正
    返回 "p<0.05" / "p<0.10" / "不显著"
    """
    if total < 10:
        return "样本不足"
    import math
    p_hat = correct / total
    se = math.sqrt(p_null * (1 - p_null) / total)
    if se == 0:
        return "不显著"
    z = abs(p_hat - p_null) / se
    alpha_005 = 2.63 if bonferroni_factor >= 5 else (2.24 if bonferroni_factor >= 2 else 1.96)
    alpha_010 = 2.33 if bonferroni_factor >= 5 else (1.96 if bonferroni_factor >= 2 else 1.645)
    if z > alpha_005:
        return "p<0.05"
    elif z > alpha_010:
        return "p<0.10"
    else:
        return "不显著"


def _compute_period_accuracy(factor_stats: Dict) -> Dict:
    period_acc = {}
    for period_key, period_label in PERIOD_LABELS.items():
        period_factors = [s for k, s in factor_stats.items() if s.get("period") == period_key]
        if not period_factors:
            period_acc[period_key] = {
                "label": period_label,
                "accuracy": "N/A",
                "correct": 0,
                "total": 0,
            }
            continue
        total_correct = sum(s["correct"] for s in period_factors)
        total_count = sum(s["total"] for s in period_factors)
        period_acc[period_key] = {
            "label": period_label,
            "accuracy": f"{total_correct / total_count:.0%}" if total_count > 0 else "N/A",
            "correct": total_correct,
            "total": total_count,
        }
    return period_acc


def _compute_consensus_stats(tracking: List[Dict]) -> Dict:
    with_consensus = []
    for r in tracking:
        if not r.get("verified"):
            continue
        if r.get("prediction") in ("中性",):
            continue
        if not _is_directional_actual(r.get("actual_direction", "")):
            continue
        alignment = r.get("consensus_alignment", {})
        if not alignment or not isinstance(alignment, dict):
            continue
        if alignment.get("alignment") == "no_data":
            continue
        with_consensus.append(r)

    if not with_consensus:
        return {"available": False, "total": 0}

    aligned_count = 0
    divergent_count = 0
    aligned_correct = 0
    divergent_consensus_correct = 0
    divergent_model_correct = 0

    for r in with_consensus:
        alignment = r.get("consensus_alignment", {})
        actual_dir = r.get("actual_direction", "")
        if not _is_directional_actual(actual_dir):
            continue
        consensus_dir = alignment.get("consensus_direction", "")
        model_dir = r.get("prediction", "")
        align_type = alignment.get("alignment", "")

        if align_type == "aligned":
            aligned_count += 1
            if model_dir == actual_dir:
                aligned_correct += 1
        elif align_type == "divergent":
            divergent_count += 1
            if consensus_dir == actual_dir:
                divergent_consensus_correct += 1
            if model_dir == actual_dir:
                divergent_model_correct += 1

    return {
        "available": True,
        "total": len(with_consensus),
        "aligned_count": aligned_count,
        "aligned_correct": aligned_correct,
        "aligned_accuracy": f"{aligned_correct / aligned_count:.0%}" if aligned_count > 0 else "N/A",
        "divergent_count": divergent_count,
        "divergent_consensus_correct": divergent_consensus_correct,
        "divergent_model_correct": divergent_model_correct,
        "consensus_win_rate": f"{divergent_consensus_correct / divergent_count:.0%}" if divergent_count > 0 else "N/A",
    }


def rollback_last_iteration() -> Dict:
    iter_data = _get_iteration_data()
    history = iter_data.get("history", [])
    if not history:
        return {"status": "error", "reason": "没有迭代历史可回滚"}

    last = history[-1]
    snapshot_name = last.get("snapshot", "")
    if not snapshot_name:
        return {"status": "error", "reason": "未找到快照文件名"}

    old_weights = _rollback_snapshot(snapshot_name)
    if not old_weights:
        return {"status": "error", "reason": f"快照文件 {snapshot_name} 不存在"}

    _write_weights_to_settings(old_weights)

    iter_data["current_weights"] = old_weights
    iter_data["total_iterations"] = max(0, iter_data.get("total_iterations", 0) - 1)
    _save_iteration_data(iter_data)

    delete_latest_iteration_history()

    print(f"  [迭代] 已回滚到快照 {snapshot_name}")
    return {"status": "rolled_back", "snapshot": snapshot_name, "weights": old_weights}
