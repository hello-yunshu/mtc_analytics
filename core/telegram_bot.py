# -*- coding: utf-8 -*-
"""
Telegram Bot 推送模块
"""

import requests
from typing import Optional


class TelegramBot:
    """Telegram Bot 推送器"""
    
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{token}"
    
    def send_message(self, text: str, parse_mode: str = "Markdown") -> bool:
        """
        发送消息
        
        Args:
            text: 消息内容
            parse_mode: 解析模式 ("Markdown" 或 "HTML")
        
        Returns:
            是否发送成功
        """
        url = f"{self.base_url}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        
        try:
            resp = requests.post(url, json=payload, timeout=30)
            resp.raise_for_status()
            result = resp.json()
            
            if result.get("ok"):
                print(f"[OK] Telegram 消息发送成功")
                return True
            else:
                print(f"[ERROR] Telegram 发送失败: {result.get('description', '未知错误')}")
                return False
                
        except requests.exceptions.RequestException as e:
            print(f"[ERROR] Telegram 请求失败: {e}")
            return False
    
    def test_connection(self) -> bool:
        """测试 Bot 连接"""
        url = f"{self.base_url}/getMe"
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            result = resp.json()
            if result.get("ok"):
                bot_info = result["result"]
                print(f"[OK] Bot 连接成功: @{bot_info.get('username', 'unknown')}")
                return True
            return False
        except Exception as e:
            print(f"[ERROR] Bot 连接失败: {e}")
            return False
