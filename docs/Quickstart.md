# 快速开始

JiuwenClaw提供两种安装方式：

**方式一：pip安装**  

​	适合自行管理Python环境的用户

**方式二：源码运行** 

​	适合基于JiuwenClaw进行二次开发适配的用户

环境依赖：

- python：>=3.11,<3.14
- nodejs：>=18.0.0（仅源码前端构建或 browser-use 功能需要，推荐 20 LTS）

可以使用`uv`或Anaconda新建虚拟环境

```bash
# 使用uv新建虚拟环境（支持 3.11、3.12、3.13 任一版本）
uv venv --python=3.11
# 或 uv venv --python=3.12
# 或 uv venv --python=3.13

# 使用Anaconda新建虚拟环境（支持 3.11、3.12、3.13 任一版本）
conda create -n JiuwenClaw python=3.11
# 或 conda create -n JiuwenClaw python=3.12
# 或 conda create -n JiuwenClaw python=3.13
```

**方式一：pip安装（推荐）**

命令行执行

```bash
# 创建名为 Jiuwenclaw 的虚拟环境
python -m venv jiuwenclaw

# 激活 Jiuwenclaw 虚拟环境
jiuwenclaw\Scripts\activate

# 安装 Jiuwenclaw
pip install jiuwenclaw
```

安装完成后执行命令行初始化和启动

```bash
# 初始化 JiuwenClaw (首次启动)
jiuwenclaw-init

# 启动 JiuwenClaw
jiuwenclaw-start
```

运行完成后即可在网页前端访问JiuwenClaw服务（默认网页本地访问 `http://localhost:5173`，如需远程访问可以执行入下命令）

``````
# 启动web服务
jiuwenclaw-web --host 0.0.0.0 --port 自定义端口

# 启动后端服务
jiuwenclaw-app
``````



**方式二：源码运行**

下载JiuwenClaw代码

```bash
  git clone https://gitcode.com/openjiuwen/jiuwenclaw.git
```

进入源码目录执行uv同步操作

```bash
  uv sync
```

进入前端目录 jiuwenclaw/web 安装依赖

```bash
  cd jiuwenclaw/web
  npm install
```

静态运行前端服务

```bash
  npm run build
  cd ../../
  uv run jiuwenclaw-start
```

动态运行前端服务

```bash
  cd ../../
  uv run jiuwenclaw-start dev
```

运行完成后即可在网页前端访问JiuwenClaw服务