FROM ubuntu:24.04

WORKDIR /tmp

ARG ARCH=arm64
ARG PYTHON_VERSION=python3.12
ARG jiuwenclaw_version=0.1.10

ENV DEBIAN_FRONTEND=noninteractive
RUN useradd --create-home --shell /bin/bash app
RUN touch /home/app/.bashrc

RUN apt-get update && apt-get install -y --no-install-recommends \
    vim tar zip unzip git curl wget dos2unix make gcc g++ ccache cmake \
    ${PYTHON_VERSION} \
    python3-dev \
    ${PYTHON_VERSION}-venv \
    python3-pip \
    nodejs npm && \
    rm -rf /var/lib/apt/lists/*

RUN ln -sf /usr/bin/python3 /usr/bin/python
RUN rm /usr/lib/python3.12/EXTERNALLY-MANAGED

USER app

RUN pip3 config set global.index-url https://mirrors.huaweicloud.com/repository/pypi/simple
ENV PATH="/home/app/.local/bin:$PATH"
RUN python3 -m pip --version && \
    pip3 install --upgrade setuptools build wheel

RUN sed -i '$a\export PATH=/usr/local/bin:$PATH' /home/app/.bashrc && \
    sed -i '$a\export LOGURU_FORMAT="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message!r}</level>"' /home/app/.bashrc

RUN touch /home/app/.vimrc && \
    echo 'set fileencodings=utf-8,ucs-bom,gb18030,gbk,gb2312,cp936' >> /home/app/.vimrc && \
    echo 'set termencoding=utf-8' >> /home/app/.vimrc && \
    echo 'set encoding=utf-8' >> /home/app/.vimrc

RUN npm config set registry https://repo.huaweicloud.com/repository/npm/
RUN pip install cffi cryptography async_timeout
RUN pip install jiuwenclaw==${jiuwenclaw_version} jiuwenclaw-tui
ENV HOME=/home/app

WORKDIR /home/app

RUN echo '#!/bin/bash\n\
CONFIG_FILE="/home/app/.jiuwenclaw/config/config.yaml"\n\
echo "Checking configuration file: $CONFIG_FILE"\n\
if [ ! -f "$CONFIG_FILE" ]; then\n\
    echo "Configuration file not found. Initializing JiuwenClaw..."\n\
    printf "yes\n1\n" | jiuwenclaw-init\n\
    if [ ! -f "$CONFIG_FILE" ]; then\n\
        echo "Error: Initialization failed or did not create config file."\n\
        echo "Please check if more interactive inputs are required."\n\
        exit 1\n\
    fi\n\
    echo "Initialization complete. Config file created."\n\
else\n\
    echo "Configuration file exists. Skipping initialization."\n\
fi\n\
echo "Starting jiuwenclaw-app in background..."\n\
jiuwenclaw-app > /home/app/jiuwenclaw-app.log 2>&1 &\n\
echo "Starting jiuwenclaw-web in foreground on port 5173..."\n\
exec jiuwenclaw-web --host 0.0.0.0 --port 5173\n\
' > /home/app/start.sh && \
    chmod +x /home/app/start.sh

CMD ["/home/app/start.sh"]
