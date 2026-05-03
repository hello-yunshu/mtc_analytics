# -*- coding: utf-8 -*-
"""
机构共识模块 - 从新闻中提取专业机构的黄金观点并计算共识

数据来源：
  1. 东方财富新闻（复用 news_sentiment 的抓取逻辑）
  2. LLM 辅助提取机构名称、观点方向、目标价

权威机构列表：
  投行：高盛、摩根大通、瑞银、花旗、摩根士丹利、巴克莱、德银、汇丰
  研究：世界黄金协会、凯投宏观、荷兰银行、道明证券、澳新银行

共识计算：
  - 统计各机构方向（看多/看空/中性）
  - 计算共识方向和共识强度
  - 提取目标价（如有）

迭代应用：
  - 模型方向与机构共识一致 → 增强置信度
  - 模型方向与机构共识背离 → 降低置信度，触发因子权重调整
"""

import re
import json
import os
import requests
from datetime import datetime
from typing import Dict, List, Optional, Tuple

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_DATA_DIR = os.path.join(_PROJECT_ROOT, "data")
from .utils import load_json, save_json

INSTITUTION_PATTERNS = [
    {"keys": ["高盛", "Goldman Sachs", "Goldman"], "name": "高盛", "name_en": "Goldman Sachs", "tier": 1},
    {"keys": ["摩根大通", "JPMorgan", "JP Morgan", "J.P. Morgan"], "name": "摩根大通", "name_en": "JPMorgan", "tier": 1},
    {"keys": ["瑞银", "UBS"], "name": "瑞银", "name_en": "UBS", "tier": 1},
    {"keys": ["花旗", "Citigroup", "Citi"], "name": "花旗", "name_en": "Citi", "tier": 1},
    {"keys": ["摩根士丹利", "Morgan Stanley"], "name": "摩根士丹利", "name_en": "Morgan Stanley", "tier": 1},
    {"keys": ["巴克莱", "Barclays"], "name": "巴克莱", "name_en": "Barclays", "tier": 2},
    {"keys": ["德银", "德意志银行", "Deutsche Bank"], "name": "德银", "name_en": "Deutsche Bank", "tier": 2},
    {"keys": ["汇丰", "HSBC"], "name": "汇丰", "name_en": "HSBC", "tier": 2},
    {"keys": ["世界黄金协会", "World Gold Council", "WGC"], "name": "世界黄金协会", "name_en": "WGC", "tier": 1},
    {"keys": ["凯投宏观", "Capital Economics"], "name": "凯投宏观", "name_en": "Capital Economics", "tier": 2},
    {"keys": ["荷兰银行", "ABN AMRO"], "name": "荷兰银行", "name_en": "ABN AMRO", "tier": 2},
    {"keys": ["道明证券", "TD Securities"], "name": "道明证券", "name_en": "TD Securities", "tier": 2},
    {"keys": ["澳新银行", "ANZ"], "name": "澳新银行", "name_en": "ANZ", "tier": 2},
    {"keys": ["法国兴业银行", "Societe Generale", "法兴"], "name": "法兴", "name_en": "Societe Generale", "tier": 2},
    {"keys": ["麦格理", "Macquarie"], "name": "麦格理", "name_en": "Macquarie", "tier": 2},
]

GOLD_VIEW_KEYWORDS_BULL = [
    "看涨", "看多", "上调", "目标价上调", "买入", "增持", "推荐买入",
    "金价将涨", "金价将升", "黄金将涨", "预计上涨", "预期上涨",
    "bullish", "upgrade", "raise target", "buy", "overweight",
    "看高", "有望上涨", "上行空间", "上涨目标",
]

GOLD_VIEW_KEYWORDS_BEAR = [
    "看跌", "看空", "下调", "目标价下调", "卖出", "减持", "回避",
    "金价将跌", "金价将降", "黄金将跌", "预计下跌", "预期下跌",
    "bearish", "downgrade", "cut target", "sell", "underweight",
    "看低", "下行风险", "下跌目标", "回调风险",
]

GOLD_VIEW_KEYWORDS_NEUTRAL = [
    "中性", "维持", "观望", "持有", "neutral", "hold", "maintain",
]

TARGET_PRICE_PATTERN = re.compile(
    r'(?:目标价?|目标|预测|预计|预期|预估|forecast|target|estimate)'
    r'[^0-9]{0,10}'
    r'[\$￥]?\s*(\d{3,5})\s*(?:美元|美金|USD|dollar|\$)?',
    re.IGNORECASE
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

_consensus_cache = None


def _get_llm_config():
    settings = load_json(os.path.join(_DATA_DIR, "web_settings.json")) or {}
    api_key_raw = settings.get("llm_api_key", "")
    from .utils import decrypt_value
    secret = ""
    try:
        from flask import current_app
        secret = current_app.secret_key or ""
    except Exception:
        try:
            key_data = load_json(os.path.join(_DATA_DIR, ".secret_key"))
            if key_data and key_data.get("secret_key"):
                secret = key_data["secret_key"]
        except Exception:
            pass
    api_key = decrypt_value(api_key_raw, secret) if api_key_raw else ""
    if not api_key:
        api_key = os.environ.get("LLM_API_KEY", "")
    base_url = settings.get("llm_base_url", "") or "https://api.openai.com/v1"
    model = settings.get("llm_model", "") or "gpt-4o-mini"
    return api_key, base_url, model, bool(api_key)


def _identify_institution(text: str) -> Optional[Dict]:
    for inst in INSTITUTION_PATTERNS:
        for key in inst["keys"]:
            if key.lower() in text.lower():
                return inst
    return None


def _extract_direction(text: str) -> Optional[str]:
    bull_score = 0
    bear_score = 0
    neutral_score = 0
    text_lower = text.lower()
    for kw in GOLD_VIEW_KEYWORDS_BULL:
        if kw.lower() in text_lower:
            bull_score += 1
    for kw in GOLD_VIEW_KEYWORDS_BEAR:
        if kw.lower() in text_lower:
            bear_score += 1
    for kw in GOLD_VIEW_KEYWORDS_NEUTRAL:
        if kw.lower() in text_lower:
            neutral_score += 1
    if bull_score > bear_score and bull_score > neutral_score:
        return "看多"
    elif bear_score > bull_score and bear_score > neutral_score:
        return "看空"
    elif neutral_score > 0 and neutral_score >= bull_score and neutral_score >= bear_score:
        return "中性"
    elif bull_score > 0 and bull_score == bear_score:
        return "中性"
    return None


def _extract_target_price(text: str) -> Optional[float]:
    matches = TARGET_PRICE_PATTERN.findall(text)
    if matches:
        try:
            prices = [float(m) for m in matches]
            valid = [p for p in prices if 500 <= p <= 10000]
            if valid:
                return max(valid)
        except ValueError:
            pass
    return None


def _extract_from_news(news_list: List[Dict]) -> List[Dict]:
    views = []
    seen_institutions = set()

    for news in news_list:
        title = news.get("title", "")
        if not title:
            continue

        inst = _identify_institution(title)
        if not inst:
            continue

        inst_name = inst["name"]
        if inst_name in seen_institutions:
            continue

        direction = _extract_direction(title)
        if not direction:
            continue

        target_price = _extract_target_price(title)

        view = {
            "institution": inst_name,
            "institution_en": inst["name_en"],
            "tier": inst["tier"],
            "direction": direction,
            "target_price": target_price,
            "source_title": title[:80],
            "source_link": news.get("link", ""),
            "date": news.get("date", datetime.now().strftime("%Y-%m-%d")),
        }
        views.append(view)
        seen_institutions.add(inst_name)

    return views


def _llm_extract_views(news_list: List[Dict]) -> List[Dict]:
    api_key, base_url, model, enabled = _get_llm_config()
    if not enabled:
        return []

    gold_related = []
    for news in news_list:
        title = news.get("title", "")
        if not title:
            continue
        has_inst = any(
            any(key.lower() in title.lower() for key in inst["keys"])
            for inst in INSTITUTION_PATTERNS
        )
        has_gold = any(kw in title for kw in ["黄金", "金价", "gold", "Gold", "XAU"])
        if has_inst and has_gold:
            gold_related.append(title)

    if not gold_related:
        return []

    titles_text = "\n".join(f"{i+1}. {t}" for i, t in enumerate(gold_related[:15]))

    inst_names = ",".join(inst["name"] for inst in INSTITUTION_PATTERNS)

    prompt = (
        f"以下新闻标题可能包含机构对黄金的观点，提取每个机构的观点方向和目标价。\n"
        f"新闻：\n{titles_text}\n"
        f"机构列表：{inst_names}\n"
        f'输出JSON：{{"views":[{{"institution":"机构名","direction":"看多/看空/中性","target_price":3700,"source":"新闻序号"}}]}}\n'
        f"只输出明确表达观点的机构，忽略无明确观点的。direction只能是看多/看空/中性。target_price为美元/盎司，没有则为null。"
    )

    try:
        resp = requests.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 500,
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"].strip()

        json_match = re.search(r'\{[\s\S]*\}', content)
        if not json_match:
            return []

        result = json.loads(json_match.group())
        views = []
        seen = set()
        for v in result.get("views", []):
            inst_name = v.get("institution", "")
            if not inst_name or inst_name in seen:
                continue
            direction = v.get("direction", "")
            if direction not in ("看多", "看空", "中性"):
                continue
            target = v.get("target_price")
            if target is not None:
                try:
                    target = float(target)
                    if not (500 <= target <= 10000):
                        target = None
                except (ValueError, TypeError):
                    target = None

            tier = 2
            for inst in INSTITUTION_PATTERNS:
                if inst_name in inst["name"] or inst_name in inst["name_en"]:
                    tier = inst["tier"]
                    break

            views.append({
                "institution": inst_name,
                "institution_en": "",
                "tier": tier,
                "direction": direction,
                "target_price": target,
                "source_title": f"LLM提取(新闻#{v.get('source', '?')})",
                "source_link": "",
                "date": datetime.now().strftime("%Y-%m-%d"),
            })
            seen.add(inst_name)

        return views

    except Exception as e:
        print(f"  [机构共识] LLM提取失败: {e}")
        return []


def _merge_views(keyword_views: List[Dict], llm_views: List[Dict]) -> List[Dict]:
    merged = {}
    for v in keyword_views:
        merged[v["institution"]] = v

    for v in llm_views:
        name = v["institution"]
        if name not in merged:
            merged[name] = v
        else:
            existing = merged[name]
            if v.get("target_price") and not existing.get("target_price"):
                existing["target_price"] = v["target_price"]

    return list(merged.values())


def _compute_consensus(views: List[Dict]) -> Dict:
    if not views:
        return {
            "direction": "中性",
            "consensus_score": 0.0,
            "bull_count": 0,
            "bear_count": 0,
            "neutral_count": 0,
            "total_count": 0,
            "avg_target_price": None,
            "agreement_level": "无数据",
        }

    tier_weights = {1: 1.5, 2: 1.0}

    bull_score = 0
    bear_score = 0
    neutral_score = 0
    bull_count = 0
    bear_count = 0
    neutral_count = 0
    target_prices = []

    for v in views:
        tw = tier_weights.get(v.get("tier", 2), 1.0)

        view_date = v.get("date", "")
        if view_date:
            try:
                from datetime import datetime as dt
                view_dt = dt.strptime(view_date[:10], "%Y-%m-%d")
                days_old = (dt.now() - view_dt).days
                if days_old > 14:
                    tw *= 0.3
                elif days_old > 7:
                    tw *= 0.6
                elif days_old > 3:
                    tw *= 0.85
            except (ValueError, TypeError):
                pass

        direction = v.get("direction", "中性")
        if direction == "看多":
            bull_score += tw
            bull_count += 1
        elif direction == "看空":
            bear_score += tw
            bear_count += 1
        else:
            neutral_score += tw
            neutral_count += 1

        tp = v.get("target_price")
        if tp and isinstance(tp, (int, float)):
            target_prices.append(float(tp))

    total_score = bull_score + bear_score + neutral_score
    if total_score == 0:
        consensus_score = 0.0
    else:
        consensus_score = (bull_score - bear_score) / total_score

    if consensus_score > 0.25:
        direction = "看多"
    elif consensus_score < -0.25:
        direction = "看空"
    else:
        direction = "中性"

    total_count = bull_count + bear_count + neutral_count
    if total_count > 0:
        agreement = max(bull_count, bear_count, neutral_count) / total_count
        if agreement >= 0.7:
            agreement_level = "高度一致"
        elif agreement >= 0.5:
            agreement_level = "多数一致"
        else:
            agreement_level = "分歧较大"
    else:
        agreement_level = "无数据"

    avg_target = None
    if target_prices:
        avg_target = round(sum(target_prices) / len(target_prices), 0)

    return {
        "direction": direction,
        "consensus_score": round(consensus_score, 2),
        "bull_count": bull_count,
        "bear_count": bear_count,
        "neutral_count": neutral_count,
        "total_count": total_count,
        "avg_target_price": avg_target,
        "agreement_level": agreement_level,
    }


def fetch_institutional_consensus(news_data: Optional[Dict] = None) -> Dict:
    """
    获取机构共识
    1. 从新闻标题中关键词提取机构观点
    2. 可选LLM辅助提取
    3. 计算共识方向和强度
    """
    global _consensus_cache

    if news_data is None:
        try:
            from .news_sentiment import fetch_news_sentiment
            news_data = fetch_news_sentiment()
        except Exception:
            news_data = {}

    news_list = news_data.get("news", [])
    if not news_list:
        return {
            "institutions": [],
            "consensus": _compute_consensus([]),
            "timestamp": datetime.now().isoformat(),
            "source": "none",
        }

    keyword_views = _extract_from_news(news_list)

    llm_views = []
    api_key, base_url, model, llm_enabled = _get_llm_config()
    if llm_enabled and len(news_list) > 0:
        try:
            llm_views = _llm_extract_views(news_list)
        except Exception:
            pass

    all_views = _merge_views(keyword_views, llm_views)
    consensus = _compute_consensus(all_views)

    source = "keyword"
    if llm_views:
        source = "keyword+llm"

    result = {
        "institutions": all_views,
        "consensus": consensus,
        "timestamp": datetime.now().isoformat(),
        "source": source,
    }

    _consensus_cache = result
    return result


def get_manual_views() -> List[Dict]:
    settings = load_json(os.path.join(_DATA_DIR, "web_settings.json")) or {}
    return settings.get("manual_institutional_views", [])


def save_manual_views(views: List[Dict]):
    settings = load_json(os.path.join(_DATA_DIR, "web_settings.json")) or {}
    settings["manual_institutional_views"] = views
    save_json(os.path.join(_DATA_DIR, "web_settings.json"), settings)


def compute_consensus_with_manual(auto_views: List[Dict], manual_views: List[Dict]) -> Tuple[List[Dict], Dict]:
    merged = {}
    for v in auto_views:
        merged[v["institution"]] = v
    for v in manual_views:
        merged[v.get("institution", f"manual_{len(merged)}")] = v

    all_views = list(merged.values())
    consensus = _compute_consensus(all_views)
    return all_views, consensus


def compare_with_consensus(model_direction: str, consensus: Dict) -> Dict:
    """
    比较模型预测与机构共识
    返回对比结果，用于模型迭代参考
    """
    consensus_dir = consensus.get("direction", "中性")
    consensus_score = consensus.get("consensus_score", 0)
    total_count = consensus.get("total_count", 0)
    agreement_level = consensus.get("agreement_level", "无数据")

    if total_count == 0:
        return {
            "alignment": "no_data",
            "model_direction": model_direction,
            "consensus_direction": consensus_dir,
            "confidence_adjustment": 0,
            "description": "无机构共识数据",
        }

    if model_direction == consensus_dir:
        alignment = "aligned"
        confidence_adj = min(5, int(abs(consensus_score) * 10))
        desc = f"模型与机构共识一致（{consensus_dir}），共识强度{consensus_score:+.2f}"
    elif model_direction == "中性" and consensus_dir != "中性":
        alignment = "neutral_vs_conviction"
        confidence_adj = -3
        desc = f"模型中性但机构共识{consensus_dir}（{agreement_level}），需关注"
    elif consensus_dir == "中性" and model_direction != "中性":
        alignment = "conviction_vs_neutral"
        confidence_adj = 0
        desc = f"模型{model_direction}但机构共识中性，模型可能有先行信号"
    else:
        alignment = "divergent"
        confidence_adj = -5
        desc = f"模型{model_direction}与机构共识{consensus_dir}背离，需审视"

    return {
        "alignment": alignment,
        "model_direction": model_direction,
        "consensus_direction": consensus_dir,
        "consensus_score": consensus_score,
        "consensus_total": total_count,
        "agreement_level": agreement_level,
        "confidence_adjustment": confidence_adj,
        "description": desc,
    }
