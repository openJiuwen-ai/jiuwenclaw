ARG MIRRORS_URL

FROM openeuler/openeuler:24.03

ARG MIRRORS_URL

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN echo "sslverify=false" >> /etc/yum.conf
RUN echo "insecure" >> ~/.curlrc
RUN sed -i 's/gpgcheck=1/gpgcheck=0/g' /etc/yum.conf /etc/yum.repos.d/openEuler.repo
RUN sed -i '/^mirrorlist=/d' /etc/yum.repos.d/openEuler.repo && \
    sed -i '/^metalink=/d' /etc/yum.repos.d/openEuler.repo && \
    sed -i "/^\[OS\]/,/^\[/ s|^baseurl=.*|baseurl=${MIRRORS_URL}/openEuler-24.03-LTS-SP4/OS/\\\$basearch|g" /etc/yum.repos.d/openEuler.repo && \
    sed -i "/^\[everything\]/,/^\[/ s|^baseurl=.*|baseurl=${MIRRORS_URL}/openEuler-24.03-LTS-SP4/everything/\\\$basearch|g" /etc/yum.repos.d/openEuler.repo && \
    sed -i "/^\[EPOL\]/,/^\[/ s|^baseurl=.*|baseurl=${MIRRORS_URL}/openEuler-24.03-LTS-SP4/EPOL/main/\\\$basearch|g" /etc/yum.repos.d/openEuler.repo && \
    sed -i "/^\[debuginfo\]/,/^\[/ s|^baseurl=.*|baseurl=${MIRRORS_URL}/openEuler-24.03-LTS-SP4/debuginfo/\\\$basearch|g" /etc/yum.repos.d/openEuler.repo && \
    sed -i "/^\[source\]/,/^\[/ s|^baseurl=.*|baseurl=${MIRRORS_URL}/openEuler-24.03-LTS-SP4/source|g" /etc/yum.repos.d/openEuler.repo && \
    sed -i "/^\[update\]/,/^\[/ s|^baseurl=.*|baseurl=${MIRRORS_URL}/openEuler-24.03-LTS-SP4/update/\\\$basearch|g" /etc/yum.repos.d/openEuler.repo && \
    sed -i "/^\[update-source\]/,/^\[/ s|^baseurl=.*|baseurl=${MIRRORS_URL}/openEuler-24.03-LTS-SP4/update/source|g" /etc/yum.repos.d/openEuler.repo && \
    sed -i 's/^gpgcheck=1/gpgcheck=0/' /etc/yum.repos.d/openEuler.repo && \
    sed -i 's/^sslverify=1/sslverify=0/' /etc/yum.repos.d/openEuler.repo && \
    cat /etc/yum.repos.d/openEuler.repo && \
    yum clean all && yum makecache


RUN yum makecache \
    && yum install -y \
        bubblewrap \
        iproute \
        iptables \
        iptables-nft \
        nftables \
        ca-certificates \
        python3-pip \
        git \
        vim \
        unzip

RUN useradd --create-home --shell /bin/bash app

WORKDIR /app

COPY pyproject.toml README.md ./
COPY docker/requirements.txt ./docker/requirements.txt
COPY src ./src
COPY src/jiuwenbox/configs /app/configs

RUN pip install --break-system-packages --upgrade \
    --progress-bar off \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    build setuptools wheel \
    && pip install --break-system-packages \
    --progress-bar off \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    -r ./docker/requirements.txt \
    && python3 -m build --wheel --no-isolation \
    && pip install --break-system-packages \
    --progress-bar off \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    dist/jiuwenbox-*.whl

RUN ln -s /usr/bin/python3 /usr/bin/python

RUN mkdir -p /opt/jiuwenbox-pip && \
    chown app:app /opt/jiuwenbox-pip

EXPOSE 8321
EXPOSE 8322

# 默认走 HTTP; UDS 模式由调用方覆盖 JIUWENBOX_LISTEN, 例如
# `docker run -e JIUWENBOX_LISTEN=unix:///run/jiuwenbox/jiuwenbox.sock \
#             -v /tmp/jiuwenbox-sock:/run/jiuwenbox jiuwenbox`。
ENV JIUWENBOX_LISTEN=http://0.0.0.0:8321
CMD ["python3", "-m", "jiuwenbox.server.launcher"]
