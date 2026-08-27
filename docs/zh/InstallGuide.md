# 安装指南（InstallGuide）

本文档说明 JiuwenSwarm 的安装步骤与常见问题。

## 环境要求

- Python 3.11 及以上
- 推荐使用 [uv](https://docs.astral.sh/uv/) 管理依赖
- 操作系统：Windows / macOS / Linux

## 安装步骤

```bash
# 1. 克隆仓库
git clone https://github.com/openJiuwen-ai/jiuwenswarm.git
cd jiuwenswarm

# 2. 创建虚拟环境
uv venv --python=3.11
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 3. 安装依赖
uv sync

# 4. 初始化并启动
uv run jiuwenswarm-init
uv run jiuwenswarm-start
```

## 常见问题

### 依赖安装失败（网络问题）

- 可配置镜像源后重试，如：`uv sync --default-index https://pypi.tuna.tsinghua.edu.cn/simple`

### Windows 下初始化失败

- 如果 Windows 用户目录包含中文字符，初始化可能因路径编码问题失败
- 可尝试将仓库放置到纯英文路径（如 `D:\dev\jiuwenswarm`）后重新初始化
- 反馈问题请附上初始化日志（`~/.jiuwenswarm/logs/`）

### 启动失败

- 确认依赖安装完整（`uv sync`）
- 查看启动日志定位原因

## 验证安装

```bash
jiuwenswarm --version
```

能看到版本号即安装成功。