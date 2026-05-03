#!/bin/bash
set -e

RUN_MODE="${RUN_MODE:-web+schedule}"

echo "========================================"
echo "  MTC Analytics - 多维度金融分析平台"
echo "  运行模式: ${RUN_MODE}"
echo "========================================"

start_web() {
    echo "[Web] 启动 Web 服务 (gunicorn)..."
    exec gunicorn \
        --bind 0.0.0.0:8080 \
        --workers 2 \
        --threads 4 \
        --timeout 120 \
        --access-logfile - \
        --error-logfile - \
        "app:create_app()"
}

start_schedule() {
    echo "[Schedule] 启动定时任务模式..."
    exec python main.py --schedule
}

start_realtime() {
    echo "[Realtime] 启动实时监控模式..."
    exec python main.py --realtime
}

start_web_and_schedule() {
    echo "[Web+Schedule] 启动 Web + 定时任务..."
    python main.py --schedule &
    exec gunicorn \
        --bind 0.0.0.0:8080 \
        --workers 2 \
        --threads 4 \
        --timeout 120 \
        --access-logfile - \
        --error-logfile - \
        "app:create_app()"
}

start_web_and_realtime() {
    echo "[Web+Realtime] 启动 Web + 实时监控..."
    python main.py --realtime &
    exec gunicorn \
        --bind 0.0.0.0:8080 \
        --workers 2 \
        --threads 4 \
        --timeout 120 \
        --access-logfile - \
        --error-logfile - \
        "app:create_app()"
}

case "${RUN_MODE}" in
    web)
        start_web
        ;;
    schedule)
        start_schedule
        ;;
    realtime)
        start_realtime
        ;;
    web+schedule)
        start_web_and_schedule
        ;;
    web+realtime)
        start_web_and_realtime
        ;;
    *)
        echo "未知模式: ${RUN_MODE}"
        echo "可用模式: web | schedule | realtime | web+schedule | web+realtime"
        exit 1
        ;;
esac
