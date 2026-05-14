#!/bin/bash
# start-dev.sh

pkill -f "vite.*jiuwenclaw"
pkill -f "jiuwenclaw.app"

# 等待并验证
for i in {1..5}; do
    if ! lsof -i :5173 > /dev/null 2>&1; then
        echo "✅ 端口 5173 已释放"
        break
    fi
    sleep 0.5
done

echo "" > /home/mage/.jiuwenclaw/agent/.logs/channel.log
echo "" > /home/mage/.jiuwenclaw/agent/.logs/channel.json
echo "" > /home/mage/.jiuwenclaw/agent/.logs/gateway.log
echo "" > /home/mage/.jiuwenclaw/agent/.logs/gateway.json
echo "" > /home/mage/.jiuwenclaw/agent/.logs/agent_server.log
echo "" > /home/mage/.jiuwenclaw/agent/.logs/agent_server.json
echo "" > /home/mage/.jiuwenclaw/agent/.logs/full.log
echo "" > /home/mage/.jiuwenclaw/agent/.logs/full.json

# 配置镜像源
export UV_INDEX_URL="https://pypi.tuna.tsinghua.edu.cn/simple/"

# 同步依赖（如果需要）
uv sync
uv run jiuwenclaw-start dev

# 直接运行开发服务器（跳过 uv sync，假设依赖已经安装）
# uv run --no-sync jiuwenclaw-start dev

# 仅启动backend，复用已有前端
# uv run jiuwenclaw-start app
