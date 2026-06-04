ARG ARCH
ARG ARM_MIRRORS_IP

FROM openeuler/openeuler:24.03

ARG ARCH
# get by: ping mirrors.huaweicloud.com
ARG ARM_MIRRORS_IP

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN echo "sslverify=false" >> /etc/yum.conf
RUN echo "insecure" >> ~/.curlrc
RUN sed -i 's/gpgcheck=1/gpgcheck=0/g' /etc/yum.conf /etc/yum.repos.d/openEuler.repo

# 只在 arm64 架构下执行这些步骤
RUN if [ "$ARCH" = "arm64" ]; then \
        sed -i '/^mirrorlist=/d' /etc/yum.repos.d/openEuler.repo && \
        sed -i '/^metalink=/d' /etc/yum.repos.d/openEuler.repo && \
        sed -i "/^\[OS\]/,/^\[/ s|^baseurl=.*|baseurl=http://${ARM_MIRRORS_IP}/openeuler/openEuler-24.03-LTS-SP3/OS/\\\$basearch|g" /etc/yum.repos.d/openEuler.repo && \
        sed -i "/^\[everything\]/,/^\[/ s|^baseurl=.*|baseurl=http://${ARM_MIRRORS_IP}/openeuler/openEuler-24.03-LTS-SP3/everything/\\\$basearch|g" /etc/yum.repos.d/openEuler.repo && \
        sed -i "/^\[EPOL\]/,/^\[/ s|^baseurl=.*|baseurl=http://${ARM_MIRRORS_IP}/openeuler/openEuler-24.03-LTS-SP3/EPOL/main/\\\$basearch|g" /etc/yum.repos.d/openEuler.repo && \
        sed -i "/^\[debuginfo\]/,/^\[/ s|^baseurl=.*|baseurl=http://${ARM_MIRRORS_IP}/openeuler/openEuler-24.03-LTS-SP3/debuginfo/\\\$basearch|g" /etc/yum.repos.d/openEuler.repo && \
        sed -i "/^\[source\]/,/^\[/ s|^baseurl=.*|baseurl=http://${ARM_MIRRORS_IP}/openeuler/openEuler-24.03-LTS-SP3/source|g" /etc/yum.repos.d/openEuler.repo && \
        sed -i "/^\[update\]/,/^\[/ s|^baseurl=.*|baseurl=http://${ARM_MIRRORS_IP}/openeuler/openEuler-24.03-LTS-SP3/update/\\\$basearch|g" /etc/yum.repos.d/openEuler.repo && \
        sed -i "/^\[update-source\]/,/^\[/ s|^baseurl=.*|baseurl=http://${ARM_MIRRORS_IP}/openeuler/openEuler-24.03-LTS-SP3/update/source|g" /etc/yum.repos.d/openEuler.repo && \
        sed -i 's/^gpgcheck=1/gpgcheck=0/' /etc/yum.repos.d/openEuler.repo && \
        sed -i 's/^sslverify=1/sslverify=0/' /etc/yum.repos.d/openEuler.repo && \
        cat /etc/yum.repos.d/openEuler.repo && \
        yum clean all && yum makecache; \
    else \
        echo "Skipping ARM-specific yum configuration for ARCH=$ARCH"; \
    fi


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


WORKDIR /app

COPY pyproject.toml README.md ./
COPY docker/requirements.txt ./docker/requirements.txt
COPY src ./src
COPY configs ./configs

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

EXPOSE 8321
EXPOSE 8322

# 默认走 HTTP; UDS 模式由调用方覆盖 JIUWENBOX_LISTEN, 例如
# `docker run -e JIUWENBOX_LISTEN=unix:///run/jiuwenbox/jiuwenbox.sock \
#             -v /tmp/jiuwenbox-sock:/run/jiuwenbox jiuwenbox`。
ENV JIUWENBOX_LISTEN=http://0.0.0.0:8321
CMD ["python3", "-m", "jiuwenbox.server.launcher"]
