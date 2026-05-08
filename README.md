# MTC Analytics - AI 黄金分析平台

多维度黄金价格分析与预测平台，集成持仓追踪、宏观监控、新闻情绪、机构共识、十二因子预测与全维度警示引擎。

## 核心功能

- **📊 持仓追踪** — 东方财富龙虎榜，前 N 大机构多空/净持仓，四级警示系统（重/高/中/低），历史分位数与拥挤度检测
- **💰 实时金价** — 6 级 fallback（AKShare → Yahoo → Gold-API → Swissquote → 缓存），国内 SGE + 沪金期货，异动事件检测
- **📈 十二因子预测** — 宏观(实际利率/美元/通胀) + 中观(持仓动量/极值/背离/央行购金/ETF) + 微观(技术/波动率/情绪) + 日历(季节性)，动态权重，多周期趋势，置信度校准
- **🔔 十二维度警示** — 技术面/波动率/宏观/关联/背离/情绪/持仓/日历/交叉确认/极端风险/央行购金/ETF资金流
- **📰 新闻情绪** — 关键词 + LLM 混合策略，东方财富多频道，LLM 缓存节省 Token
- **🌍 宏观指标** — 5 级 fallback，美债/美元/VIX/原油/GLD ETF/通胀预期
- **🏛️ 机构共识** — 15+ 投行观点提取，共识与预测对比，自动调整置信度
- **🤖 模型自迭代** — 回测准确率驱动权重微调，可选 LLM 诊断，Token 预算控制
- **📲 Telegram 推送** — 每日报告 + 异动提醒 + 高级别警示
- **🖥️ Web 界面** — Flask + Blueprint，SSE 实时推送，走势图/警示面板/设置页面

## 技术栈

| 层级 | 技术 |
|------|------|
| Web | Flask 3.0 + Blueprint，30+ API，SSE 实时推送 |
| 数据库 | SQLite (WAL)，13 张表，线程安全，自动清理 |
| 数据源 | AKShare / Yahoo Finance / FRED / 东方财富 / Gold-API |
| AI | OpenAI 兼容 API（DeepSeek / Qwen / OpenAI），Token 预算 |
| 部署 | Docker + Gunicorn，5 种运行模式 |
| 安全 | 登录认证 + CSRF + IP 封禁 + 速率限制 + Fernet 加密 |

## 快速开始

### Docker 部署（推荐）

```bash
git clone https://github.com/hello-yunshu/mtc_analytics.git
cd mtc_analytics
mkdir -p data && chmod 777 data

# 可选：配置环境变量
cat > .env << 'EOF'
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
LLM_API_KEY=
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
FRED_API_KEY=
RUN_MODE=web+schedule
EOF

docker compose up --build -d
```

访问 `http://localhost:8368/gold`，首次密码：`cat data/.initial_password`

重置密码：`rm data/.default_password && docker restart mtc_analytics`

详细部署见 [DEPLOY.md](DEPLOY.md)

### 本地运行

```bash
pip install -r requirements.txt
mkdir -p data && chmod 777 data
python app.py              # Web 服务
python main.py --schedule  # 定时任务
```

## 运行模式

| 模式 | 说明 |
|------|------|
| `web` | 仅 Web 服务 |
| `schedule` | 仅定时任务 |
| `realtime` | 仅实时监控 |
| `web+schedule` | Web + 定时任务（**推荐**） |
| `web+realtime` | Web + 实时监控 |

## CLI 命令

```bash
python main.py --backfill       # 回填 30 天历史数据（首次推荐）
python main.py --test-fetch     # 测试数据获取
python main.py --test-bot       # 测试 Telegram Bot
python main.py --run            # 手动执行一次报告
python main.py --schedule       # 定时任务模式
python main.py --realtime       # 实时监控模式
```

## 项目结构

```
mtc_analytics/
├── app.py                    # Flask 主应用
├── main.py                   # CLI 主程序
├── entrypoint.sh             # Docker 入口
├── core/
│   ├── config.py             # 全局配置
│   ├── db.py                 # SQLite 数据库层
│   ├── security.py           # 安全中间件
│   ├── auth.py               # 认证/CSRF
│   ├── settings.py           # 设置管理器
│   ├── utils.py              # 工具函数/加密
│   ├── cache.py              # 线程安全缓存
│   ├── sse.py                # SSE 管理器
│   ├── gold_price.py         # 多源实时金价
│   ├── fetcher.py            # 持仓数据抓取
│   ├── analyzer.py           # 持仓分析引擎
│   ├── predictor.py          # 十二因子预测模型
│   ├── alert_engine.py       # 全维度警示引擎
│   ├── news_sentiment.py     # 新闻情绪分析
│   ├── macro_fetcher.py      # 宏观指标获取
│   ├── trend_analyzer.py     # 长期趋势分析
│   ├── institutional_consensus.py  # 机构共识
│   ├── model_iteration.py    # 模型自迭代
│   ├── llm_utils.py          # LLM 工具层
│   ├── reporter.py           # 报告生成
│   ├── telegram_bot.py       # Telegram 推送
│   └── backfill.py           # 历史数据回填
├── blueprints/gold/          # 黄金分析 Blueprint
├── portal/                   # Portal 首页
├── static/                   # CSS/JS 静态资源
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## 环境变量

| 变量 | 说明 |
|------|------|
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token |
| `TELEGRAM_CHAT_ID` | Telegram Chat ID |
| `LLM_API_KEY` | LLM API Key（也可在 Web 设置页配置） |
| `LLM_BASE_URL` | LLM API 地址，默认 OpenAI |
| `LLM_MODEL` | LLM 模型，默认 gpt-4o-mini |
| `FRED_API_KEY` | FRED API Key（宏观指标） |
| `RUN_MODE` | 运行模式，默认 web+schedule |

## License

GPL-3.0
