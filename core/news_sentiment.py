# -*- coding: utf-8 -*-
"""
新闻情绪分析模块 - 混合策略：关键词快速筛选 + LLM 语义精确分析

数据源：
  1. 东方财富-黄金频道
  2. 东方财富-期货频道
  3. 东方财富-全球财经

情绪分析策略（混合）：
  - 关键词模式：带否定词检测 + 权重评分，快速免费
  - LLM 模式：批量调用大模型 API，语义理解精确
  - 混合模式（默认）：关键词先筛选，LLM 对关键新闻精确分析

缓存策略：每小时最多搜索一次（避免频繁调用）
"""

import re
import json
import os
import threading
import requests
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_DATA_DIR = os.path.join(_PROJECT_ROOT, "data")
from .utils import load_json
from .config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_ENABLED
from . import db

_news_cache = None
_news_cache_lock = threading.Lock()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

NEGATION_WORDS = [
    "不", "未", "无", "非", "没", "难", "失去", "缺乏",
    "不会", "不再", "不足", "并非", "难以",
    "not", "no", "never", "without", "hardly", "barely",
]

BULLISH_KEYWORDS = {
    "降息": 1.5, "宽松": 1.0, "QE": 1.5, "量化宽松": 1.5, "利率下调": 1.5,
    "鸽派": 1.2, "鸽声": 1.2, "rate cut": 1.5, "dovish": 1.2,
    "easing": 1.0, "lower rate": 1.5,
    "避险": 1.3, "地缘紧张": 1.2, "冲突升级": 1.3, "战争": 1.5,
    "制裁": 1.0, "局势紧张": 1.0,
    "risk off": 1.3, "geopolitical": 1.0, "conflict": 1.2, "sanction": 1.0, "war": 1.5,
    "央行购金": 1.5, "增持黄金": 1.5, "黄金储备": 1.2, "去美元化": 1.3, "购金": 1.3,
    "central bank gold": 1.5, "gold reserve": 1.2, "de-dollarization": 1.3,
    "衰退": 1.0, "经济下行": 1.0, "失业率上升": 0.8, "通胀回落": 1.0, "经济疲软": 1.0,
    "recession": 1.0, "downturn": 1.0, "slowdown": 1.0,
    "金价上涨": 1.5, "金价突破": 1.5, "黄金创新高": 1.5, "买盘涌入": 1.3, "黄金大涨": 1.5,
    "金价大涨": 1.5, "金价飙升": 1.5, "金价创新高": 1.5, "金价走高": 1.3, "黄金暴涨": 1.5,
    "金价企稳": 0.5, "金价止跌": 0.8, "企稳回升": 0.8,
    "gold rally": 1.5, "gold surge": 1.5, "gold record": 1.5, "gold high": 1.3,
    "美元走弱": 1.3, "美元下跌": 1.2, "美元指数下跌": 1.2,
    "dollar weak": 1.3, "USD drop": 1.2, "dollar index fall": 1.2,
    "债务危机": 1.3, "违约风险": 1.0, "银行危机": 1.3, "金融风险": 1.0,
    "debt crisis": 1.3, "default risk": 1.0, "banking crisis": 1.3,
    "飙升": 1.5, "大涨": 1.3, "暴涨": 1.5, "猛涨": 1.5, "劲升": 1.3, "大涨特涨": 1.5,
    "创新高": 1.3, "历史新高": 1.3, "新高": 1.0, "刷新高": 1.3,
    "强劲增长": 1.2, "强劲": 1.0, "需求增长": 1.0, "需求旺盛": 1.0, "需求强劲": 1.0,
    "买盘": 1.0, "抄底": 1.0, "逢低买入": 1.0, "加仓": 0.8, "增持": 1.0,
    "突破": 1.0, "站上": 0.8, "回升": 0.8, "反弹": 0.8, "走高": 0.8, "上行": 0.8,
    "看涨": 1.2, "看多": 1.2, "利多": 1.0, "利好": 1.0,
    "避险需求": 1.3, "避险情绪": 1.3, "避险升温": 1.3, "避险买盘": 1.3,
    "支撑": 0.8, "提振": 0.8, "推动": 0.5, "刺激": 0.5,
    "牛市": 1.2, "多头": 0.8,
    "surge": 1.5, "rally": 1.3, "soar": 1.5, "record high": 1.3, "bullish": 1.2,
}

BEARISH_KEYWORDS = {
    "加息": 1.5, "收紧": 1.2, "缩表": 1.3, "鹰派": 1.2, "利率上调": 1.5,
    "维持高利率": 1.5, "鹰声": 1.2,
    "rate hike": 1.5, "hawkish": 1.2, "tightening": 1.2, "higher rate": 1.5,
    "风险偏好": 1.0, "股市上涨": 0.8, "风险资产": 0.8, "获利了结": 1.3, "获利回吐": 1.3,
    "risk on": 1.0, "stock rally": 0.8, "risk appetite": 1.0, "profit taking": 1.3,
    "美元走强": 1.3, "美元上涨": 1.2, "美元指数上涨": 1.2, "美元反弹": 1.0,
    "dollar strong": 1.3, "USD rise": 1.2, "dollar index rise": 1.2,
    "金价下跌": 1.5, "金价暴跌": 1.5, "黄金回调": 1.0, "抛售": 1.3,
    "金价大跌": 1.5, "黄金跳水": 1.5, "金价走低": 1.3, "金价重挫": 1.5, "黄金暴跌": 1.5,
    "gold drop": 1.5, "gold fall": 1.3, "gold crash": 1.5, "gold selloff": 1.5,
    "经济强劲": 1.0, "就业超预期": 1.2, "通胀粘性": 1.3, "GDP超预期": 1.0, "数据强劲": 1.0,
    "strong economy": 1.0, "beat expectation": 1.0, "sticky inflation": 1.3,
    "通胀高企": 1.3, "通胀超预期": 1.3, "通胀升温": 1.2,
    "inflation high": 1.3, "hot inflation": 1.3,
    "停火": 1.2, "冲突缓和": 1.2, "谈判达成": 1.0, "和平协议": 1.2,
    "ceasefire": 1.2, "de-escalate": 1.2, "peace deal": 1.2,
    "甩卖": 1.5, "抛售黄金": 1.5, "集体抛售": 1.5, "大摩下调": 1.3,
    "暴跌": 1.5, "大跌": 1.3, "重挫": 1.3, "跳水": 1.3, "崩盘": 1.5,
    "连跌": 1.2, "新低": 1.0, "跌破": 1.2, "失守": 1.2, "下破": 1.2,
    "跌超": 1.2, "跌逾": 1.2, "下挫": 1.0, "走低": 0.8, "下行": 0.8, "回落": 0.5,
    "承压": 1.0, "打压": 1.0, "压制": 0.8, "拖累": 0.8,
    "看跌": 1.2, "看空": 1.2, "利空": 1.0,
    "减仓": 0.8, "减持": 1.0, "清仓": 1.3, "离场": 1.0,
    "避险消退": 1.2, "避险降温": 1.2, "避险减弱": 1.2,
    "阻力": 0.5, "压力": 0.5,
    "熊市": 1.2, "空头": 0.8, "做空": 1.0,
    "crash": 1.5, "plunge": 1.5, "slump": 1.3, "bearish": 1.2, "selloff": 1.5,
}

NEGATION_FLIP_MAP = {
    "降息": ("不降息", "未降息", "难降息", "降息.*降温", "降息.*无望", "降息.*落空"),
    "宽松": ("不宽松", "未宽松", "宽松.*降温"),
    "鸽派": ("不鸽", "非鸽派"),
    "避险": ("避险.*减弱", "避险.*消退", "避险.*降温", "避险.*下降", "避险.*回落"),
    "增持": ("不增持", "未增持", "停止增持", "放缓.*增持", "增持.*放缓"),
    "上涨": ("不涨", "未涨", "难涨", "上涨.*乏力", "涨势.*放缓"),
    "走弱": ("不再走弱", "未走弱"),
    "加息": ("不加息", "未加息"),
    "鹰派": ("非鹰派"),
    "走强": ("不再走强", "未走强", "走强.*放缓"),
    "下跌": ("不跌", "未跌", "跌势.*放缓", "下跌.*放缓", "跌幅.*收窄"),
    "抛售": ("抛售.*放缓", "抛售.*减弱", "抛售.*消退", "放缓.*抛售"),
    "大跌": ("大跌.*放缓", "跌幅.*收窄"),
    "暴跌": ("暴跌.*放缓"),
    "购金": ("放缓.*购金", "暂停.*购金", "停止.*购金"),
}

NEWS_SOURCES = [
    {"name": "东方财富-黄金频道", "url": "https://gold.eastmoney.com/", "encoding": "utf-8"},
    {"name": "东方财富-期货频道", "url": "https://futures.eastmoney.com/", "encoding": "utf-8"},
    {"name": "东方财富-全球财经", "url": "https://finance.eastmoney.com/a/czqyw.html", "encoding": "utf-8"},
]

SEARCH_KEYWORDS = ["黄金", "金价", "美联储 利率", "美元指数"]

TITLE_KEYWORDS = [
    "黄金", "金价", "贵金属", "美联储", "利率", "美元",
    "通胀", "CPI", "PCE", "非农", "降息", "加息", "避险",
    "COMEX", "央行", "地缘", "衰退", "就业", "GDP",
    "国债", "收益率", "鹰派", "鸽派", "白银",
    "gold", "dollar", "Fed", "interest rate", "inflation",
]


def fetch_news_sentiment() -> Dict:
    """
    抓取最新黄金相关新闻并分析情绪（混合策略）

    Returns:
        {
            "timestamp": "...",
            "sentiment_score": -0.3,
            "sentiment": "偏空",
            "confidence": "high",
            "analyzer": "llm" / "keyword" / "hybrid",
            "bullish_count": 3,
            "bearish_count": 5,
            "neutral_count": 2,
            "news": [...],
            "key_events": [...],
            "llm_summary": "...",
            "sources_ok": [...],
            "sources_failed": [...],
        }
    """
    global _news_cache
    with _news_cache_lock:
        if _news_cache:
            cache_time = datetime.fromisoformat(_news_cache.get("timestamp", "2000-01-01"))
            if datetime.now() - cache_time < timedelta(hours=1):
                print(f"  新闻情绪使用缓存（{cache_time.strftime('%H:%M')}）")
                return _news_cache

    print(f"  正在抓取新闻...")

    all_news = []
    sources_ok = []
    sources_failed = []

    for keyword in SEARCH_KEYWORDS:
        try:
            news = _fetch_search_api(keyword, page_size=15)
            if news:
                all_news.extend(news)
                sources_ok.append(f"搜索-{keyword}")
                print(f"    搜索[{keyword}]: 获取 {len(news)} 条")
            else:
                sources_failed.append(f"搜索-{keyword}")
        except Exception as e:
            sources_failed.append(f"搜索-{keyword}")
            print(f"    [WARN] 搜索[{keyword}] 失败: {e}")

    if len(all_news) < 10:
        print(f"    搜索API结果不足({len(all_news)}条)，补充频道页抓取...")
        for source in NEWS_SOURCES:
            try:
                news = _fetch_eastmoney(source["url"], source["name"], source.get("encoding", "utf-8"))
                if news:
                    all_news.extend(news)
                    sources_ok.append(source["name"])
                    print(f"    {source['name']}: 获取 {len(news)} 条")
                else:
                    sources_failed.append(source["name"])
            except requests.exceptions.RequestException as e:
                sources_failed.append(source["name"])

    all_news = _deduplicate_news(all_news)

    cutoff_date = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    before_filter = len(all_news)
    all_news = [n for n in all_news if not n.get("date") or n["date"] >= cutoff_date]
    filtered_by_date = before_filter - len(all_news)
    if filtered_by_date > 0:
        print(f"    时效过滤：移除 {filtered_by_date} 条超过2天的旧新闻")

    if not all_news:
        result = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "sentiment_score": 0.0,
            "sentiment": "中性",
            "confidence": "low",
            "analyzer": "none",
            "bullish_count": 0,
            "bearish_count": 0,
            "neutral_count": 0,
            "news": [],
            "key_events": [],
            "llm_summary": "",
            "sources_ok": sources_ok,
            "sources_failed": sources_failed,
        }
        with _news_cache_lock:
            _news_cache = result
        return result

    prev_titles = _get_previous_day_titles()
    if prev_titles:
        before_count = len(all_news)
        all_news = [n for n in all_news if n["title"][:15] not in prev_titles]
        filtered_count = before_count - len(all_news)
        if filtered_count > 0:
            print(f"    跨天去重：过滤 {filtered_count} 条重复新闻")

    # Step 1: 关键词分析（带否定词检测 + 权重）
    kw_analyzed = []
    for item in all_news:
        sentiment, score, keywords = _analyze_sentiment_keyword(item["title"])
        kw_analyzed.append({
            "title": item["title"],
            "source": item["source"],
            "link": item.get("link", ""),
            "sentiment": sentiment,
            "kw_score": score,
            "keywords": keywords,
        })

    # Step 2: LLM 分析（如果已配置）
    llm_result = None
    if LLM_ENABLED:
        llm_result = _analyze_with_llm(kw_analyzed)

    # Step 3: 合并结果
    if llm_result:
        final_analyzed = _merge_results(kw_analyzed, llm_result)
        analyzer = "hybrid"
        confidence = "high"
    else:
        final_analyzed = kw_analyzed
        analyzer = "keyword"
        confidence = "medium"

    bullish = [n for n in final_analyzed if n["sentiment"] == "bullish"]
    bearish = [n for n in final_analyzed if n["sentiment"] == "bearish"]
    neutral = [n for n in final_analyzed if n["sentiment"] == "neutral"]

    total = len(final_analyzed) or 1
    directional = len(bullish) + len(bearish)
    neutral_ratio = len(neutral) / total

    if neutral_ratio > 0.6:
        confidence = "low"
    elif neutral_ratio > 0.4:
        if confidence == "high":
            confidence = "medium"

    bullish_weight = sum(abs(n.get("kw_score", 0.5)) for n in bullish) or len(bullish) * 0.5
    bearish_weight = sum(abs(n.get("kw_score", 0.5)) for n in bearish) or len(bearish) * 0.5
    total_weight = bullish_weight + bearish_weight

    if total_weight > 0:
        raw_score = (bullish_weight - bearish_weight) / total_weight
        direction_ratio = directional / total
        confidence_decay = 1.0
        if total < 3:
            confidence_decay = 0.4
        elif total < 5:
            confidence_decay = total / 5.0
        score = raw_score * (0.6 + 0.4 * direction_ratio) * confidence_decay
    else:
        score = 0.0

    score = max(-1.0, min(1.0, score))

    if score > 0.2:
        sentiment_label = "偏多"
    elif score < -0.2:
        sentiment_label = "偏空"
    else:
        sentiment_label = "中性"

    key_events = _extract_key_events(final_analyzed)

    llm_summary = ""
    if llm_result and llm_result.get("summary"):
        llm_summary = llm_result["summary"]

    result = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "sentiment_score": round(score, 2),
        "sentiment": sentiment_label,
        "confidence": confidence,
        "analyzer": analyzer,
        "bullish_count": len(bullish),
        "bearish_count": len(bearish),
        "neutral_count": len(neutral),
        "news": final_analyzed[:20],
        "key_events": key_events,
        "llm_summary": llm_summary,
        "sources_ok": sources_ok,
        "sources_failed": sources_failed,
    }

    with _news_cache_lock:
        _news_cache = result
    _archive_sentiment(result)

    src_info = f"（来源: {'+'.join(sources_ok)}）" if sources_ok else "（所有来源失败）"
    analyzer_info = f"[{analyzer}]" if analyzer != "keyword" else ""
    print(f"  新闻分析{src_info}{analyzer_info}: 利多{len(bullish)}条 利空{len(bearish)}条 中性{len(neutral)}条 → {sentiment_label}({score:+.2f})")
    if llm_summary:
        print(f"  LLM摘要: {llm_summary[:80]}")

    return result


def _get_previous_day_titles() -> set:
    """从数据库读取前一天的新闻标题前15字，用于跨天去重"""
    try:
        history = db.get_news_sentiment_history(3)
        prev_titles = set()
        for record in history:
            key_events = record.get("key_events", [])
            if isinstance(key_events, str):
                try:
                    key_events = json.loads(key_events)
                except (json.JSONDecodeError, TypeError):
                    key_events = []
            for event in key_events:
                if isinstance(event, dict):
                    title = event.get("title", "")
                else:
                    title = str(event)
                if title:
                    prev_titles.add(title[:15])
        return prev_titles
    except Exception:
        return set()


def _fetch_eastmoney(url: str, source_name: str, encoding: str = "utf-8") -> List[Dict]:
    """从东方财富频道页抓取新闻（备用）"""
    news = []

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        resp.encoding = encoding

        kw_pattern = "|".join(TITLE_KEYWORDS)
        pattern = rf'<a[^>]+href="([^"]+)"[^>]*>([^<]*(?:{kw_pattern})[^<]*)</a>'
        matches = re.findall(pattern, resp.text, re.IGNORECASE)

        for link, title in matches:
            title = re.sub(r'&[a-zA-Z]+;', '', title).strip()
            if len(title) > 10 and len(title) < 120:
                news.append({
                    "title": title,
                    "link": link,
                    "source": source_name,
                })

    except requests.exceptions.RequestException as e:
        print(f"    [WARN] {source_name} 抓取异常: {e}")

    return news


def _fetch_search_api(keyword: str, page_size: int = 20) -> List[Dict]:
    """从东方财富搜索API按时间抓取最新新闻"""
    news = []
    try:
        import urllib.parse
        param = json.dumps({
            "uid": "",
            "keyword": keyword,
            "type": ["cmsArticleWebOld"],
            "client": "web",
            "clientType": "web",
            "clientVersion": "curr",
            "param": {
                "cmsArticleWebOld": {
                    "searchScope": "default",
                    "sort": "default",
                    "pageIndex": 1,
                    "pageSize": page_size,
                    "preTag": "",
                    "postTag": "",
                }
            }
        }, ensure_ascii=False)
        url = f"https://search-api-web.eastmoney.com/search/jsonp?cb=_cb&param={urllib.parse.quote(param)}"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        text = resp.text
        json_str = text[text.index("(") + 1: text.rindex(")")]
        data = json.loads(json_str)
        articles = data.get("result", {}).get("cmsArticleWebOld", [])
        for art in articles:
            title = art.get("title", "").strip()
            link = art.get("url", "")
            date_str = art.get("date", "")
            content = art.get("content", "") or art.get("description", "") or art.get("abstract", "")
            summary = content[:200].strip() if content else ""
            if title and len(title) > 10 and len(title) < 120:
                news.append({
                    "title": title,
                    "link": link,
                    "source": f"搜索-{keyword}",
                    "date": date_str,
                    "summary": summary,
                })
    except Exception as e:
        print(f"    [WARN] 搜索API({keyword})异常: {e}")
    return news


def _deduplicate_news(news: List[Dict]) -> List[Dict]:
    """按标题去重"""
    seen = set()
    result = []
    for item in news:
        key = item["title"][:15]
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _has_negation_before(text: str, keyword: str) -> bool:
    """检测关键词前面是否有否定词"""
    idx = text.find(keyword)
    if idx <= 0:
        return False
    prefix = text[max(0, idx - 3):idx]
    for neg in NEGATION_WORDS:
        if neg in prefix:
            return True
    return False


def _check_negation_flip(text: str, keyword: str) -> bool:
    """检测否定翻转模式（支持正则，如'避险.*降温'匹配'避险需求降温'）"""
    if keyword in NEGATION_FLIP_MAP:
        for pattern in NEGATION_FLIP_MAP[keyword]:
            if re.search(pattern, text):
                return True
    return False


def _get_sentiment_calibration() -> Dict:
    """
    基于历史情绪准确率计算关键词权重校准系数
    如果利多关键词历史准确率偏低，降低其权重；利空同理
    """
    try:
        history = db.get_news_sentiment_history(30)
        if len(history) < 10:
            return {"bullish_multiplier": 1.0, "bearish_multiplier": 1.0}

        bull_correct = 0
        bull_total = 0
        bear_correct = 0
        bear_total = 0

        for record in history:
            score = record.get("sentiment_score", 0)
            if score is None:
                continue
            try:
                score = float(score)
            except (ValueError, TypeError):
                continue

            date_str = record.get("date", "")
            if not date_str:
                continue

            from .db import get_gold_prices
            prices = get_gold_prices(5)
            if len(prices) < 2:
                continue

            price_by_date = {p.get("date", ""): p for p in prices}
            prev_price = price_by_date.get(date_str)
            if not prev_price:
                sorted_dates = sorted(price_by_date.keys())
                idx = -1
                for si, sd in enumerate(sorted_dates):
                    if sd >= date_str:
                        idx = si
                        break
                if idx > 0:
                    prev_price = price_by_date.get(sorted_dates[idx - 1])
                elif sorted_dates:
                    prev_price = price_by_date.get(sorted_dates[-1])

            if not prev_price or prev_price.get("close", 0) <= 0:
                continue

            curr_close = prices[-1].get("close", 0) if prices else 0
            prev_close = prev_price.get("close", 0)
            if curr_close <= 0 or prev_close <= 0:
                continue

            actual_chg = (curr_close - prev_close) / prev_close * 100

            if score > 0.1:
                bull_total += 1
                if actual_chg > 0:
                    bull_correct += 1
            elif score < -0.1:
                bear_total += 1
                if actual_chg < 0:
                    bear_correct += 1

        bull_mult = 1.0
        bear_mult = 1.0

        if bull_total >= 5:
            bull_acc = bull_correct / bull_total
            if bull_acc < 0.4:
                bull_mult = 0.8
            elif bull_acc > 0.6:
                bull_mult = 1.1

        if bear_total >= 5:
            bear_acc = bear_correct / bear_total
            if bear_acc < 0.4:
                bear_mult = 0.8
            elif bear_acc > 0.6:
                bear_mult = 1.1

        return {"bullish_multiplier": bull_mult, "bearish_multiplier": bear_mult}
    except Exception:
        return {"bullish_multiplier": 1.0, "bearish_multiplier": 1.0}


def _analyze_sentiment_keyword(text: str) -> Tuple[str, float, List[str]]:
    """
    关键词情绪分析（带否定词检测 + 权重评分）

    Returns:
        (sentiment, score, keywords)
        sentiment: "bullish" / "bearish" / "neutral"
        score: 加权得分，正数偏多，负数偏空
        keywords: 命中的关键词列表
    """
    text_lower = text.lower()

    sentiment_calibration = _get_sentiment_calibration()
    bull_mult = sentiment_calibration.get("bullish_multiplier", 1.0)
    bear_mult = sentiment_calibration.get("bearish_multiplier", 1.0)

    bullish_score = 0.0
    bearish_score = 0.0
    bullish_hits = []
    bearish_hits = []

    for kw, weight in BULLISH_KEYWORDS.items():
        if kw.lower() in text_lower:
            if _check_negation_flip(text_lower, kw) or _has_negation_before(text_lower, kw):
                bearish_score += weight * bull_mult * 0.8
                bearish_hits.append(f"¬{kw}")
            else:
                bullish_score += weight * bull_mult
                bullish_hits.append(kw)

    for kw, weight in BEARISH_KEYWORDS.items():
        if kw.lower() in text_lower:
            if _check_negation_flip(text_lower, kw) or _has_negation_before(text_lower, kw):
                bullish_score += weight * bear_mult * 0.8
                bullish_hits.append(f"¬{kw}")
            else:
                bearish_score += weight * bear_mult
                bearish_hits.append(kw)

    total = bullish_score + bearish_score
    if total == 0:
        return "neutral", 0.0, []

    net_score = (bullish_score - bearish_score) / total

    if net_score > 0.1:
        return "bullish", round(net_score, 2), bullish_hits[:3]
    elif net_score < -0.1:
        return "bearish", round(net_score, 2), bearish_hits[:3]
    else:
        return "neutral", round(net_score, 2), []


def _analyze_with_llm(kw_analyzed: List[Dict]) -> Optional[Dict]:
    """
    使用 LLM 对新闻进行批量语义分析（Token 优化版）

    优化策略：
    1. 只发送关键词分析不确定的新闻（|kw_score| < 0.5），而非全部
    2. 压缩 prompt 指令，移除冗余说明
    3. 精简输出格式，用单字符代替完整单词
    4. 截断过长标题（>40字）
    5. 降低 max_tokens 限制

    Returns:
        {
            "items": {"title": {"sentiment": "b"/"e"/"n", "reason": "..."}},
            "summary": "...",
            "score_adjustment": 0.0,
        }
    """
    if not LLM_ENABLED:
        return None

    uncertain = [n for n in kw_analyzed if abs(n.get("kw_score", 0)) < 0.5]
    uncertain.sort(key=lambda x: abs(x.get("kw_score", 0)), reverse=True)
    selected = uncertain[:10]

    if not selected:
        return None

    titles_text = "\n".join([
        f"{i+1}.{n['title'][:60]}" + (f" | {n.get('summary','')[:80]}" if n.get('summary') else "")
        for i, n in enumerate(selected)
    ])

    prompt = (
        "分析以下黄金新闻标题对金价的影响，输出JSON：\n"
        f"{titles_text}\n"
        '格式：{"r":[{"i":1,"s":"b/e/n"}],"sm":"1句总结","sa":0.0}\n'
        "s:b=利多,e=利空,n=中性;sa:-0.3~+0.3微调;注意否定词"
    )

    try:
        print(f"  正在调用 LLM 分析 {len(selected)} 条新闻...")
        resp = requests.post(
            f"{LLM_BASE_URL.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {LLM_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": LLM_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 300,
            },
            timeout=30,
        )
        resp.raise_for_status()

        data = resp.json()
        content = data["choices"][0]["message"]["content"].strip()

        json_match = re.search(r'\{[\s\S]*\}', content)
        if not json_match:
            print(f"  [WARN] LLM 返回格式异常，跳过 LLM 分析")
            return None

        llm_data = json.loads(json_match.group())

        SENTIMENT_MAP = {"b": "bullish", "e": "bearish", "n": "neutral"}

        items = llm_data.get("r", [])
        summary = llm_data.get("sm", "")
        score_adjustment = float(llm_data.get("sa", 0))
        score_adjustment = max(-0.3, min(0.3, score_adjustment))

        item_map = {}
        for item in items:
            idx = item.get("i", 0) - 1
            if 0 <= idx < len(selected):
                s_code = item.get("s", "n")
                item_map[selected[idx]["title"]] = {
                    "sentiment": SENTIMENT_MAP.get(s_code, "neutral"),
                    "reason": "",
                }

        print(f"  LLM 分析完成: {len(item_map)} 条新闻，摘要: {summary[:50]}")

        return {
            "items": item_map,
            "summary": summary,
            "score_adjustment": score_adjustment,
        }

    except requests.exceptions.RequestException as e:
        print(f"  [WARN] LLM API 调用失败: {e}")
        return None
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        print(f"  [WARN] LLM 返回解析失败: {e}")
        return None


def _merge_results(kw_analyzed: List[Dict], llm_result: Dict) -> List[Dict]:
    """
    合并关键词和 LLM 分析结果

    策略：
    - LLM 分析过的新闻，以 LLM 结果为准
    - 未被 LLM 分析的新闻，保留关键词结果
    - 应用 LLM 的 score_adjustment
    """
    llm_items = llm_result.get("items", {})
    merged = []

    for item in kw_analyzed:
        title = item["title"]
        if title in llm_items:
            llm_item = llm_items[title]
            merged.append({
                "title": title,
                "source": item["source"],
                "link": item.get("link", ""),
                "sentiment": llm_item["sentiment"],
                "kw_score": item.get("kw_score", 0),
                "keywords": item.get("keywords", []),
                "llm_reason": llm_item.get("reason", ""),
            })
        else:
            merged.append({
                "title": title,
                "source": item["source"],
                "link": item.get("link", ""),
                "sentiment": item["sentiment"],
                "kw_score": item.get("kw_score", 0),
                "keywords": item.get("keywords", []),
            })

    return merged


def _extract_key_events(analyzed_news: List[Dict]) -> List[Dict]:
    """从新闻中提取关键事件（包含链接）"""
    events = []

    priority_keywords = [
        "美联储", "Fed", "降息", "加息", "利率决议", "利率决定",
        "非农", "CPI", "PCE", "GDP",
        "战争", "冲突", "制裁", "地缘", "停火",
        "央行", "购金", "增持",
        "鹰派", "鸽派",
        "创新高", "暴跌", "大跌", "大涨",
        "抛售", "甩卖",
    ]

    for news in analyzed_news:
        text = news["title"]
        for kw in priority_keywords:
            if kw.lower() in text.lower() and len(events) < 5:
                event_text = text[:60]
                if not any(e.get("title") == event_text for e in events):
                    events.append({
                        "title": event_text,
                        "link": news.get("link", ""),
                    })
                break

    return events


def _archive_sentiment(result: Dict):
    """归档情绪数据到数据库"""
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        db.upsert_news_sentiment(today, result)
    except Exception as e:
        print(f"  [WARN] 新闻情绪归档失败: {e}")


_DEFAULT_BULLISH_KEYWORDS = None
_DEFAULT_BEARISH_KEYWORDS = None
_DEFAULT_NEGATION_WORDS = None


def _save_defaults():
    global _DEFAULT_BULLISH_KEYWORDS, _DEFAULT_BEARISH_KEYWORDS, _DEFAULT_NEGATION_WORDS
    if _DEFAULT_BULLISH_KEYWORDS is None:
        _DEFAULT_BULLISH_KEYWORDS = dict(BULLISH_KEYWORDS)
    if _DEFAULT_BEARISH_KEYWORDS is None:
        _DEFAULT_BEARISH_KEYWORDS = dict(BEARISH_KEYWORDS)
    if _DEFAULT_NEGATION_WORDS is None:
        _DEFAULT_NEGATION_WORDS = list(NEGATION_WORDS)


def _parse_keyword_string(raw):
    new_kw = {}
    for item in raw.strip().split(","):
        item = item.strip()
        if not item:
            continue
        if ":" in item:
            k, v = item.rsplit(":", 1)
            try:
                new_kw[k.strip()] = float(v.strip())
            except ValueError:
                new_kw[k.strip()] = 1.0
        else:
            new_kw[item] = 1.0
    return new_kw


def reload_keywords_from_settings():
    """从设置文件重新加载关键词配置"""
    global BULLISH_KEYWORDS, BEARISH_KEYWORDS, NEGATION_WORDS
    _save_defaults()
    settings = load_json(os.path.join(_DATA_DIR, "web_settings.json"))
    if not settings:
        return

    if "bullish_keywords" in settings:
        try:
            raw = settings["bullish_keywords"]
            if isinstance(raw, dict):
                BULLISH_KEYWORDS = dict(raw) if raw else dict(_DEFAULT_BULLISH_KEYWORDS)
            elif isinstance(raw, str):
                if raw.strip():
                    new_kw = _parse_keyword_string(raw)
                    BULLISH_KEYWORDS = new_kw if new_kw else dict(_DEFAULT_BULLISH_KEYWORDS)
                else:
                    BULLISH_KEYWORDS = dict(_DEFAULT_BULLISH_KEYWORDS)
        except (ValueError, AttributeError):
            pass

    _reload_bearish_and_negation(settings)


def reload_llm_config():
    """从设置文件重新加载 LLM 配置（热更新）"""
    global LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_ENABLED
    from . import config
    settings = load_json(os.path.join(_DATA_DIR, "web_settings.json"))
    if not settings:
        return

    if "llm_api_key" in settings:
        raw = settings["llm_api_key"] or ""
        from .utils import decrypt_value
        try:
            from web_app import app
            secret = app.secret_key or ""
        except Exception:
            secret = ""
        LLM_API_KEY = decrypt_value(raw, secret) if raw else ""
        config.LLM_API_KEY = LLM_API_KEY
        LLM_ENABLED = bool(LLM_API_KEY)
        config.LLM_ENABLED = LLM_ENABLED
    if settings.get("llm_base_url"):
        LLM_BASE_URL = settings["llm_base_url"]
        config.LLM_BASE_URL = LLM_BASE_URL
    if settings.get("llm_model"):
        LLM_MODEL = settings["llm_model"]
        config.LLM_MODEL = LLM_MODEL


def _reload_bearish_and_negation(settings):
    """内部辅助：从设置重新加载看空关键词和否定词"""
    global BEARISH_KEYWORDS, NEGATION_WORDS
    _save_defaults()
    if "bearish_keywords" in settings:
        try:
            raw = settings["bearish_keywords"]
            if isinstance(raw, dict):
                BEARISH_KEYWORDS = dict(raw) if raw else dict(_DEFAULT_BEARISH_KEYWORDS)
            elif isinstance(raw, str):
                if raw.strip():
                    new_kw = _parse_keyword_string(raw)
                    BEARISH_KEYWORDS = new_kw if new_kw else dict(_DEFAULT_BEARISH_KEYWORDS)
                else:
                    BEARISH_KEYWORDS = dict(_DEFAULT_BEARISH_KEYWORDS)
        except (ValueError, AttributeError):
            pass

    if "negation_words" in settings:
        try:
            raw = settings["negation_words"]
            if isinstance(raw, list):
                NEGATION_WORDS = list(raw) if raw else list(_DEFAULT_NEGATION_WORDS)
            elif isinstance(raw, str):
                if raw.strip():
                    words = [w.strip() for w in raw.strip().split(",") if w.strip()]
                    NEGATION_WORDS = words if words else list(_DEFAULT_NEGATION_WORDS)
                else:
                    NEGATION_WORDS = list(_DEFAULT_NEGATION_WORDS)
        except (ValueError, AttributeError):
            pass
