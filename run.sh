#!/usr/bin/env bash
# 妇科前沿助手 — 一键启动
set -e
cd "$(dirname "$0")"

if [ -f .env ]; then
  set -a; source .env; set +a
fi

DAYS="${DAYS:-7}"
MAX_AI="${MAX_AI:-50}"
PORT="${PORT:-8765}"

echo "===== 妇科前沿助手每日更新 ====="
echo "DAYS=$DAYS · MAX_AI=$MAX_AI"
echo ""

echo "--- 并行更新：论文 + 学术动态 ---"
NEWS_ARGS=()
for arg in "$@"; do
  [ "$arg" = "--no-ai" ] && NEWS_ARGS+=("--no-ai")
done

python3 fetch_papers.py --days "$DAYS" --max-ai "$MAX_AI" "$@" &
PAPERS_PID=$!
python3 fetch_news.py --days "$DAYS" "${NEWS_ARGS[@]}" &
NEWS_PID=$!

STATUS=0
if ! wait "$PAPERS_PID"; then
  echo "论文更新失败" >&2
  STATUS=1
fi
if ! wait "$NEWS_PID"; then
  echo "学术动态更新失败" >&2
  STATUS=1
fi
if [ "$STATUS" -ne 0 ]; then
  exit "$STATUS"
fi

echo ""
lsof -ti tcp:$PORT 2>/dev/null | xargs -r kill -9 2>/dev/null || true
echo "启动 http://localhost:$PORT/dashboard.html …"
nohup python3 server.py > .server.log 2>&1 &
sleep 1
open "http://localhost:$PORT/dashboard.html"
echo ""
echo "服务器已在后台运行(端口 $PORT)。停止: lsof -ti tcp:$PORT | xargs kill"
