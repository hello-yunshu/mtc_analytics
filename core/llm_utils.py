# -*- coding: utf-8 -*-
"""
Shared helpers for OpenAI-compatible LLM calls.

Provides a unified LLM service layer that can be shared across multiple
financial modules (gold, oil, forex, etc.).
"""

import copy
import json
import math
import os
import re
import requests
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse


DEFAULT_LLM_BASE_URL = "https://api.openai.com/v1"
DEFAULT_LLM_MODEL = "gpt-4o-mini"
DEFAULT_LLM_BUDGET = 10000
MAX_GLOBAL_LLM_BUDGET = 500000

FALLBACK_CONTEXT_WINDOW = 32000
FALLBACK_MAX_OUTPUT_TOKENS = 4096
CONTEXT_SAFETY_TOKENS = 128
MIN_COMPLETION_TOKENS = 64

MODEL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}$")
_CJK_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_DATA_DIR = os.path.join(_PROJECT_ROOT, "data")

_MODEL_LIMIT_PATTERNS: Tuple[Tuple[re.Pattern, int, int, str], ...] = (
    (re.compile(r"(?:^|/)gpt-4\.1"), 1047576, 32768, "OpenAI GPT-4.1 family"),
    (re.compile(r"(?:^|/)gpt-4o(?:-|$)|(?:^|/)chatgpt-4o"), 128000, 16384, "OpenAI GPT-4o family"),
    (re.compile(r"(?:^|/)o[134](?:-|$)|(?:^|/)o4-mini"), 200000, 32768, "OpenAI reasoning family"),
    (re.compile(r"(?:^|/)gpt-4-turbo"), 128000, 4096, "OpenAI GPT-4 Turbo"),
    (re.compile(r"(?:^|/)gpt-4(?:-|$)"), 8192, 4096, "OpenAI GPT-4"),
    (re.compile(r"(?:^|/)gpt-3\.5-turbo"), 16385, 4096, "OpenAI GPT-3.5 Turbo"),
    (re.compile(r"(?:^|/)deepseek-(chat|reasoner)"), 64000, 8192, "DeepSeek chat family"),
    (re.compile(r"(?:^|/)qwen-(max|plus|long|turbo|flash)"), 131072, 8192, "Qwen chat family"),
    (re.compile(r"(?:^|/)qwen[23]?|(?:^|/)qwq"), 131072, 8192, "Qwen open model family"),
    (re.compile(r"(?:^|/)claude-3"), 200000, 8192, "Claude 3 family"),
    (re.compile(r"(?:^|/)gemini-"), 1000000, 8192, "Gemini family"),
)


def _int_from_env(name: str, default: int, minimum: int = 1, maximum: int = 10_000_000) -> int:
    raw = os.environ.get(name, "")
    if raw == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def normalize_llm_budget(value, default: int = DEFAULT_LLM_BUDGET) -> int:
    if value in (None, ""):
        return default
    try:
        budget = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, min(MAX_GLOBAL_LLM_BUDGET, budget))


def normalize_llm_model(value: str, default: str = DEFAULT_LLM_MODEL) -> str:
    model = str(value or "").strip()
    if not model:
        return default
    if not MODEL_NAME_PATTERN.match(model):
        raise ValueError("模型名称只能包含字母、数字、点号、下划线、横线、斜杠、冒号或加号")
    return model


def normalize_llm_base_url(value: str, default: str = DEFAULT_LLM_BASE_URL) -> str:
    url = str(value or "").strip().rstrip("/")
    if not url:
        return default
    if len(url) > 500:
        raise ValueError("Base URL 过长")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Base URL 必须是 http 或 https 地址")
    return url


def normalize_llm_settings(settings: Dict) -> Dict:
    normalized = dict(settings or {})
    raw_budget = normalized.get("llm_budget", normalized.get("iteration_llm_budget", DEFAULT_LLM_BUDGET))
    normalized["llm_budget"] = normalize_llm_budget(raw_budget)
    normalized.pop("iteration_llm_budget", None)
    if "llm_model" in normalized:
        normalized["llm_model"] = normalize_llm_model(normalized.get("llm_model"))
    if "llm_base_url" in normalized:
        normalized["llm_base_url"] = normalize_llm_base_url(normalized.get("llm_base_url"))
    return normalized


def get_model_token_limits(model: str, settings: Dict = None) -> Dict:
    try:
        safe_model = normalize_llm_model(model)
    except ValueError:
        safe_model = DEFAULT_LLM_MODEL
    model_key = safe_model.lower()

    context_window = FALLBACK_CONTEXT_WINDOW
    max_output_tokens = FALLBACK_MAX_OUTPUT_TOKENS
    source = "fallback"
    known = False
    for pattern, context, output, label in _MODEL_LIMIT_PATTERNS:
        if pattern.search(model_key):
            context_window = context
            max_output_tokens = output
            source = label
            known = True
            break

    settings = settings or {}
    context_window = int(settings.get("llm_context_window") or _int_from_env("LLM_CONTEXT_WINDOW", context_window))
    max_output_tokens = int(settings.get("llm_max_output_tokens") or _int_from_env("LLM_MAX_OUTPUT_TOKENS", max_output_tokens))
    context_window = max(MIN_COMPLETION_TOKENS + CONTEXT_SAFETY_TOKENS, context_window)
    max_output_tokens = max(1, min(max_output_tokens, context_window - CONTEXT_SAFETY_TOKENS))

    return {
        "model": safe_model,
        "known": known,
        "source": source,
        "context_window": context_window,
        "max_output_tokens": max_output_tokens,
    }


def estimate_token_count(value) -> int:
    text = str(value or "")
    if not text:
        return 0
    cjk_tokens = len(_CJK_PATTERN.findall(text))
    ascii_part = _CJK_PATTERN.sub(" ", text)
    ascii_chars = len(re.sub(r"\s+", "", ascii_part))
    ascii_tokens = math.ceil(ascii_chars / 4) if ascii_chars else 0
    return max(1, cjk_tokens + ascii_tokens)


def estimate_messages_tokens(messages: List[Dict]) -> int:
    total = 3
    for message in messages:
        total += 4
        total += estimate_token_count(message.get("role", ""))
        total += estimate_token_count(message.get("content", ""))
    return total


def prepare_chat_request(model: str, messages: List[Dict], max_tokens: int, temperature: float) -> Dict:
    limits = get_model_token_limits(model)
    requested_output = max(1, int(max_tokens))
    prompt_tokens = estimate_messages_tokens(messages)
    available_output = limits["context_window"] - prompt_tokens - CONTEXT_SAFETY_TOKENS
    capped_output = min(requested_output, limits["max_output_tokens"], max(0, available_output))

    if capped_output < MIN_COMPLETION_TOKENS:
        return {
            "ok": False,
            "reason": (
                f"上下文不足：prompt 约 {prompt_tokens} tokens，"
                f"模型窗口 {limits['context_window']} tokens"
            ),
            "prompt_tokens": prompt_tokens,
            "max_tokens": capped_output,
            "estimated_tokens": prompt_tokens + max(0, capped_output),
            "limits": limits,
        }

    return {
        "ok": True,
        "payload": {
            "model": limits["model"],
            "messages": messages,
            "temperature": temperature,
            "max_tokens": capped_output,
        },
        "prompt_tokens": prompt_tokens,
        "max_tokens": capped_output,
        "estimated_tokens": prompt_tokens + capped_output,
        "output_was_capped": capped_output < requested_output,
        "limits": limits,
    }


# ==================== Budget Management ====================

BUDGET_CATEGORIES = {
    "diagnose": 0.50,
    "reasoning": 0.20,
    "news": 0.20,
    "consensus": 0.10,
}

BUDGET_CATEGORY_LABELS = {
    "diagnose": "诊断",
    "reasoning": "推理",
    "news": "新闻",
    "consensus": "共识",
}

_TOKEN_USAGE_FILE = os.path.join(_DATA_DIR, "llm_token_usage.json")


def get_token_usage() -> Dict:
    from .utils import load_json
    data = load_json(_TOKEN_USAGE_FILE) or {"month": "", "used": 0}
    return data


def save_token_usage(data: Dict):
    from .utils import save_json
    save_json(_TOKEN_USAGE_FILE, data)


def get_budget_ratios(settings: Dict = None) -> Dict[str, float]:
    ratios = dict(BUDGET_CATEGORIES)
    if settings:
        custom = settings.get("llm_budget_ratios")
        if isinstance(custom, dict):
            for cat in BUDGET_CATEGORIES:
                if cat in custom:
                    try:
                        v = float(custom[cat])
                        if 0 <= v <= 1:
                            ratios[cat] = v
                    except (TypeError, ValueError):
                        pass
    total = sum(ratios.values())
    if total > 0:
        ratios = {k: v / total for k, v in ratios.items()}
    return ratios


def get_category_budget(total_budget: int, category: str, settings: Dict = None) -> int:
    ratios = get_budget_ratios(settings)
    ratio = ratios.get(category, 0.1)
    return max(0, int(total_budget * ratio))


def check_category_token_budget(token_usage: Dict, total_budget: int,
                                category: str, estimated_tokens: int,
                                settings: Dict = None) -> bool:
    month_key = datetime.now().strftime("%Y-%m")
    cat_usage = token_usage.get("categories", {}).get(category, {"month": "", "used": 0})
    if cat_usage.get("month") != month_key:
        cat_usage = {"month": month_key, "used": 0}
    cat_budget = get_category_budget(total_budget, category, settings)
    if int(cat_usage.get("used", 0)) + estimated_tokens > cat_budget:
        return False
    return True


def record_category_token_usage(token_usage: Dict, category: str, tokens_used: int):
    month_key = datetime.now().strftime("%Y-%m")
    if "categories" not in token_usage:
        token_usage["categories"] = {}
    cat_usage = token_usage.get("categories", {}).get(category, {"month": "", "used": 0})
    if cat_usage.get("month") != month_key:
        cat_usage = {"month": month_key, "used": 0}
    cat_usage["used"] = int(cat_usage.get("used", 0)) + tokens_used
    token_usage["categories"][category] = cat_usage


def register_budget_category(name: str, default_ratio: float, label: str):
    BUDGET_CATEGORIES[name] = default_ratio
    BUDGET_CATEGORY_LABELS[name] = label


# ==================== JSON Validation ====================

def validate_llm_json(content: str, required_keys: List[str],
                      key_types: Dict[str, type] = None) -> Optional[Dict]:
    json_match = re.search(r'\{[\s\S]*\}', content)
    if not json_match:
        return None
    try:
        result = json.loads(json_match.group())
    except (json.JSONDecodeError, ValueError):
        return None
    for key in required_keys:
        if key not in result:
            return None
    if key_types:
        for key, expected_type in key_types.items():
            if key in result and not isinstance(result[key], expected_type):
                if expected_type == float and isinstance(result[key], (int,)):
                    result[key] = float(result[key])
                else:
                    return None
    return result


# ==================== Unified LLM Config ====================

def get_llm_config() -> Tuple[str, str, str, bool]:
    from .utils import load_json, decrypt_value
    settings = load_json(os.path.join(_DATA_DIR, "web_settings.json")) or {}
    api_key_raw = settings.get("llm_api_key", "")
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
    api_key = decrypt_value(api_key_raw, secret) if api_key_raw else os.environ.get("LLM_API_KEY", "")
    try:
        base_url = normalize_llm_base_url(
            settings.get("llm_base_url", "") or os.environ.get("LLM_BASE_URL", DEFAULT_LLM_BASE_URL)
        )
    except ValueError:
        base_url = DEFAULT_LLM_BASE_URL
    try:
        model = normalize_llm_model(settings.get("llm_model", "") or os.environ.get("LLM_MODEL", DEFAULT_LLM_MODEL))
    except ValueError:
        model = DEFAULT_LLM_MODEL
    enabled = bool(api_key)
    return api_key, base_url, model, enabled


def get_llm_settings() -> Dict:
    from .utils import load_json
    return load_json(os.path.join(_DATA_DIR, "web_settings.json")) or {}


def get_llm_budget() -> int:
    settings = get_llm_settings()
    return normalize_llm_budget(
        settings.get("llm_budget", settings.get("iteration_llm_budget", DEFAULT_LLM_BUDGET))
    )


# ==================== Unified LLM Call ====================

def call_llm(messages: List[Dict], *, category: str,
             max_tokens: int = 500, temperature: float = 0.2,
             timeout: int = 30, log_prefix: str = "LLM") -> Optional[Dict]:
    """
    Unified LLM API call entry point.
    - Automatically gets config and checks budget
    - Automatically records token usage
    - Returns {"content": str, "tokens_used": int} or None
    """
    api_key, base_url, model, enabled = get_llm_config()
    if not enabled:
        return None

    settings = get_llm_settings()
    budget = get_llm_budget()
    if budget <= 0:
        return None

    prepared = prepare_chat_request(model, messages, max_tokens, temperature)
    if not prepared["ok"]:
        print(f"  [{log_prefix}] {prepared['reason']}，跳过")
        return None

    token_usage = get_token_usage()
    if not check_category_token_budget(token_usage, budget, category, prepared["estimated_tokens"], settings):
        print(f"  [{log_prefix}] {category} 预算不足，跳过")
        return None

    if prepared["output_was_capped"]:
        print(f"  [{log_prefix}] max_tokens 已按模型限制调整为 {prepared['max_tokens']}")

    try:
        print(f"  [{log_prefix}] 正在调用 LLM ({category})...")
        resp = requests.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=prepared["payload"],
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()

        usage = data.get("usage", {})
        tokens_used = usage.get("total_tokens", prepared["estimated_tokens"])

        month_key = datetime.now().strftime("%Y-%m")
        if token_usage.get("month") != month_key:
            token_usage["month"] = month_key
            token_usage["used"] = 0
        token_usage["used"] = int(token_usage.get("used", 0)) + tokens_used
        record_category_token_usage(token_usage, category, tokens_used)
        save_token_usage(token_usage)

        content = data["choices"][0]["message"]["content"].strip()
        print(f"  [{log_prefix}] 完成（{tokens_used} tokens）")
        return {"content": content, "tokens_used": tokens_used}

    except Exception as e:
        print(f"  [{log_prefix}] 调用失败: {e}")
        return None


# ==================== Reasoning Generation ====================

REASONING_MAX_TOKENS = 400
REASONING_MIN_INTERVAL_HOURS = 6

_REASONING_STATE_FILE = os.path.join(_DATA_DIR, "llm_reasoning_state.json")


def _get_reasoning_state() -> Dict:
    from .utils import load_json
    return load_json(_REASONING_STATE_FILE) or {}


def _save_reasoning_state(state: Dict):
    from .utils import save_json
    save_json(_REASONING_STATE_FILE, state)


def generate_llm_reasoning(*, market_name: str, direction: str, score: float,
                           confidence: int, factors_text: str) -> Optional[str]:
    """
    Generate LLM reasoning text for a market prediction.
    market_name: market label (e.g. "黄金", "原油")
    factors_text: pre-formatted factor scores string from caller
    """
    api_key, base_url, model, enabled = get_llm_config()
    if not enabled:
        return None

    settings = get_llm_settings()
    min_interval = REASONING_MIN_INTERVAL_HOURS
    try:
        min_interval = max(1, int(settings.get("llm_reasoning_interval", REASONING_MIN_INTERVAL_HOURS)))
    except (TypeError, ValueError):
        pass

    reasoning_state = _get_reasoning_state()
    last_call = reasoning_state.get("last_reasoning_ts", "")
    if last_call:
        try:
            last_dt = datetime.fromisoformat(last_call)
            hours_since = (datetime.now() - last_dt).total_seconds() / 3600
            if hours_since < min_interval:
                return None
        except (ValueError, TypeError):
            pass

    system_msg = {
        "role": "system",
        "content": (
            f"你是一位专业的{market_name}市场分析师。根据模型输出和因子评分，"
            "撰写简洁有洞察力的推理文本。只输出推理文本，不要输出JSON或其他格式。"
        ),
    }
    user_msg = {
        "role": "user",
        "content": (
            f"{market_name}预测模型输出：方向{direction}，评分{score:+.2f}，置信度{confidence}%\n"
            f"因子评分：{factors_text}\n"
            f"用2-3句中文写一段专业推理，解释为何预测{direction}，需结合关键因子，语言简洁有洞察力。"
        ),
    }
    messages = [system_msg, user_msg]

    result = call_llm(messages, category="reasoning",
                      max_tokens=REASONING_MAX_TOKENS, temperature=0.3,
                      timeout=20, log_prefix="推理")
    if result is None:
        return None

    content = result["content"]
    if len(content) < 10:
        return None

    reasoning_state["last_reasoning_ts"] = datetime.now().isoformat()
    _save_reasoning_state(reasoning_state)

    return content
