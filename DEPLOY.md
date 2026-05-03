# MTC Analytics - Docker 部署教程

## 1. 服务器准备

```bash
mkdir -p /opt/mtc_analytics/data
```

将项目文件上传至 `/opt/mtc_analytics/`，确保目录结构如下：

```
/opt/mtc_analytics/
├── Dockerfile
├── docker-compose.yml
├── entrypoint.sh
├── requirements.txt
├── app.py
├── main.py
├── core/
├── blueprints/
├── portal/
└── static/
```

## 2. 配置环境变量

```bash
cd /opt/mtc_analytics

cat > .env << 'EOF'
# Telegram 推送（可选）
TELEGRAM_BOT_TOKEN=你的Bot_Token
TELEGRAM_CHAT_ID=你的Chat_ID

# LLM 分析（可选，用于新闻情绪深度分析）
LLM_API_KEY=你的API_Key
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini

# 宏观指标（可选，FRED 数据源）
FRED_API_KEY=你的FRED_Key

# 运行模式：web | schedule | realtime | web+schedule | web+realtime
RUN_MODE=web+schedule

# 生产环境启用 HTTPS Cookie
HTTPS=true
EOF

chmod 600 .env
```

## 3. 构建与启动

```bash
cd /opt/mtc_analytics

# 构建镜像（首次或代码更新后）
docker compose build

# 启动服务（后台运行）
docker compose up -d

# 查看日志
docker compose logs -f

# 查看运行状态
docker compose ps
```

## 4. 首次登录获取密码

首次启动会自动生成随机密码，查看容器日志获取：

```bash
docker compose logs | grep "首次启动"
```

登录后请在 **设置页面** 立即修改密码。

## 5. 访问服务

```
http://服务器IP:8368/gold
```

端口映射为 `127.0.0.1:8368:8080`，如需外网访问，请配置 Nginx 反向代理。

## 6. Nginx 反向代理（推荐）

```nginx
server {
    listen 80;
    server_name your-domain.com;
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name your-domain.com;

    ssl_certificate     /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://127.0.0.1:8368;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE 支持
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 86400s;
    }
}
```

## 7. 常用运维命令

```bash
# 停止服务
docker compose down

# 重启服务
docker compose restart

# 更新代码后重新构建
docker compose build --no-cache
docker compose up -d

# 查看数据目录
ls -la ./data/

# 查看数据库统计
docker compose exec mtc_analytics python3 -c "from core.db import get_db_stats; print(get_db_stats())"

# 手动触发一次分析任务
docker compose exec mtc_analytics python3 main.py --run

# 查看安全日志
cat ./data/security.log
```

## 8. 运行模式说明

| 模式 | 说明 |
|------|------|
| `web` | 仅 Web 服务，不运行定时任务 |
| `schedule` | 仅定时任务（每天自动生成报告），无 Web 界面 |
| `realtime` | 仅实时监控模式，无 Web 界面 |
| `web+schedule` | Web + 定时任务（**推荐**） |
| `web+realtime` | Web + 实时监控 |

## 9. 数据目录结构

```
data/
├── .secret_key              # Flask Session 密钥（自动生成）
├── .default_password        # 初始密码哈希（自动生成）
├── web_settings.json        # Web 设置（密码/Token/权重等）
├── security.log             # 安全审计日志
├── gold_tracker.db          # SQLite 主数据库
└── reports/                 # 每日分析报告
```
