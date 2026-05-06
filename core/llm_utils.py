# -*- coding: utf-8 -*-
"""
Shared helpers for OpenAI-compatible LLM calls.
"""

import math
import os
import re
from typing import Dict, List, Tuple
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
