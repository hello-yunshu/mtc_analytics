# MTC Analytics - AI 黄金分析平台

多维度黄金价格分析与预测平台，集成期货持仓追踪、宏观指标监控、新闻情绪分析、十二因子预测模型与全维度警示引擎。

## 核心功能

- **📊 持仓追踪** — 东方财富期货龙虎榜，多空/净持仓排名，连续变化与反转检测，四级警示系统
- **💰 实时金价** — 多源 fallback（AKShare / Yahoo / Gold-API / Swissquote），国内金价（SGE Au99.99 + 沪金期货）
- **📈 十二因子预测** — 宏观 4 因子 + 中观 4 因子 + 微观 3 因子 + 日历 1 因子，动态权重，回测验证
- **🔔 全维度警示** — 技术面 / 波动率 / 宏观 / 关联 / 背离 / 情绪 / 持仓 / 日历 / 交叉 / 极端 / 央行 / ETF，十二维度
- **📰 新闻情绪** — 关键词筛选 + LLM 语义分析混合策略，东方财富多频道数据源
- **🌍 宏观指标** — 美债收益率 / 美元指数 / VIX / 原油，多数据源 fallback
- **🤖 模型自迭代** — 基于回测准确率的规则化权重微调，可选 LLM 辅助诊断
- **📲 Telegram 推送** — 每日报告自动推送，金价异动实时提醒

## 技术架构

```
数据采集层  →  分析引擎层  →  模型迭代层  →  Web 展示层  →  通知推送层
```

| 层级 | 技术 |
|------|------|
| Web 框架 | Flask + Blueprint，30+ API 端点 |
| 数据库 | SQLite (WAL)，13 张表 |
| 实时推送 | SSE (Server-Sent Events) |
| 数据源 | AKShare / Yahoo Finance / FRED / 东方财富 |
| 部署 | Docker + Gunicorn，5 种运行模式 |
| 安全 | 登录认证 + CSRF + IP 封禁 + 速率限制 + 安全头 |

## 快速开始

### Docker 部署（推荐）

```bash
git clone https://github.com/hello-yunshu/mtc_analytics.git
cd mtc_analytics

# 创建数据目录并设置权限
mkdir -p data
chmod 777 data

# 配置环境变量（可选）
cat > .env << 'EOF'
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
LLM_API_KEY=
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
FRED_API_KEY=
RUN_MODE=web+schedule
EOF

# 构建并启动
docker compose up --build -d
```

访问 `http://localhost:8368/gold`

首次启动会自动生成随机密码，查看密码：
```bash
cat data/.initial_password
```

登录后请在 **设置页面** 立即修改密码。

详细部署教程见 [DEPLOY.md](DEPLOY.md)

### 本地运行

```bash
pip install -r requirements.txt

# 创建数据目录并设置权限
mkdir -p data
chmod 777 data

# Web 服务
python app.py

# 或定时任务模式
python main.py --schedule
```

## 运行模式

| 模式 | 命令 | 说明 |
|------|------|------|
| Web | `RUN_MODE=web` | 仅 Web 服务 |
| Schedule | `RUN_MODE=schedule` | 仅定时任务 |
| Realtime | `RUN_MODE=realtime` | 仅实时监控 |
| Web+Schedule | `RUN_MODE=web+schedule` | Web + 定时任务（推荐） |
| Web+Realtime | `RUN_MODE=web+realtime` | Web + 实时监控 |

## 项目结构

```
gold_tracker/
├── app.py                  # Flask 主应用入口
├── main.py                 # CLI 主程序（定时/实时/回填）
├── core/
│   ├── config.py           # 全局配置
│   ├── db.py               # SQLite 数据库层
│   ├── security.py         # 统一安全中间件
│   ├── utils.py            # 工具函数（JSON/Fernet 加密）
│   ├── gold_price.py       # 多源实时金价
│   ├── fetcher.py          # 东方财富持仓数据
│   ├── analyzer.py         # 持仓分析引擎
│   ├── predictor.py        # 十二因子预测模型
│   ├── alert_engine.py     # 全维度警示引擎
│   ├── news_sentiment.py   # 新闻情绪分析
│   ├── macro_fetcher.py    # 宏观指标获取
│   ├── trend_analyzer.py   # 长期趋势分析
│   ├── telegram_bot.py     # Telegram 推送
│   └── model_iteration.py  # 模型自迭代
├── blueprints/gold/        # Flask Blueprint
│   ├── routes.py           # API 路由
│   └── templates/gold.html # 主页面
├── portal/                 # Portal 首页
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## 环境变量

| 变量 | 必需 | 说明 |
|------|------|------|
| `TELEGRAM_BOT_TOKEN` | 否 | Telegram Bot Token |
| `TELEGRAM_CHAT_ID` | 否 | Telegram 推送 Chat ID |
| `LLM_API_KEY` | 否 | LLM API Key（新闻深度分析） |
| `LLM_BASE_URL` | 否 | LLM API 地址 |
| `LLM_MODEL` | 否 | LLM 模型名称 |
| `FRED_API_KEY` | 否 | FRED API Key（宏观指标） |
| `RUN_MODE` | 否 | 运行模式，默认 `web+schedule` |
| `HTTPS` | 否 | 生产环境设为 `true` 启用安全 Cookie |

## License

GPL-3.0
