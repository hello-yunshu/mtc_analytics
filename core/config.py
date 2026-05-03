# -*- coding: utf-8 -*-
"""
配置文件 - Telegram Bot 信息从环境变量读取
"""

import os

# ==================== Telegram Bot 配置 ====================
# 优先从环境变量读取，未设置则使用默认值（不推送）
# 设置方式：export TELEGRAM_BOT_TOKEN="your_token"
#          export TELEGRAM_CHAT_ID="your_chat_id"
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID_HERE")

# ==================== 跟踪配置 ====================
# 跟踪的机构数量（前N大）
TOP_N = 5

# 警示阈值
ALERT_CONSECUTIVE_DAYS_3 = 3   # 连续3天同方向变化 → 警示
ALERT_CONSECUTIVE_DAYS_5 = 5   # 连续5天同方向变化 → 强警示
ALERT_REVERSAL = True           # 突然转向（前一天增加，当天减少，或反之）→ 警示

# ==================== 数据存储 ====================
DATA_DIR = "data"

# ==================== 定时任务 ====================
# 每天执行时间（24小时制，北京时间）
SCHEDULE_HOUR = 9                # 上午9点生成报告
SCHEDULE_MINUTE = 0
SCHEDULE_HOUR2 = 17              # 下午5点生成报告
SCHEDULE_MINUTE2 = 0
TELEGRAM_PUSH_HOUR = 18          # Telegram 推送时间（下午报告之后）
TELEGRAM_PUSH_MINUTE = 0

# ==================== LLM 情绪分析配置 ====================
# 兼容 OpenAI API 格式（DeepSeek / Qwen / OpenAI / 自定义均可）
# 设置方式：export LLM_API_KEY="your_api_key"
#          export LLM_BASE_URL="https://api.deepseek.com/v1"  （可选，默认 OpenAI）
#          export LLM_MODEL="deepseek-chat"                    （可选，默认 gpt-4o-mini）
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")
LLM_ENABLED = bool(LLM_API_KEY)

# ==================== 模型自迭代配置 ====================
ITERATION_MIN_SAMPLES = int(os.environ.get("ITERATION_MIN_SAMPLES", "20"))
ITERATION_MAX_ADJUSTMENT = float(os.environ.get("ITERATION_MAX_ADJUSTMENT", "0.03"))
ITERATION_LLM_MONTHLY_BUDGET = int(os.environ.get("ITERATION_LLM_MONTHLY_BUDGET", "6000"))
ITERATION_LLM_DIAGNOSE_THRESHOLD = float(os.environ.get("ITERATION_LLM_DIAGNOSE_THRESHOLD", "0.4"))

# ==================== FRED API 配置 ====================
# 联邦储备经济数据 API（免费，用于宏观指标）
# 申请地址：https://fred.stlouisfed.org/docs/api/api_key.html
# 设置方式：export FRED_API_KEY="your_api_key"
FRED_API_KEY = os.environ.get("FRED_API_KEY", "")
