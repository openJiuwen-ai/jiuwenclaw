FROM openeuler/openeuler:24.03

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN echo "sslverify=false" >> /etc/yum.conf
RUN echo "insecure" >> ~/.curlrc
RUN sed -i 's/gpgcheck=1/gpgcheck=0/g' /etc/yum.conf /etc/yum.repos.d/openEuler.repo

RUN yum makecache \
    && yum install -y \
        bubblewrap \
        iproute \
        iptables \
        iptables-nft \
        nftables \
        ca-certificates \
        nodejs \
        python3-pip \
        git \
        vim \
        unzip

WORKDIR /app

COPY pyproject.toml README.md ./
COPY docker/requirements.txt ./docker/requirements.txt
COPY src ./src
COPY configs ./configs

RUN pip install --break-system-packages --upgrade -i https://pypi.tuna.tsinghua.edu.cn/simple build setuptools wheel \
    && pip install --break-system-packages -i https://pypi.tuna.tsinghua.edu.cn/simple -r ./docker/requirements.txt \
    && python3 -m build --wheel --no-isolation \
    && pip install --break-system-packages -i https://pypi.tuna.tsinghua.edu.cn/simple dist/jiuwenbox-*.whl

RUN ln -s /usr/bin/python3 /usr/bin/python

EXPOSE 8321
EXPOSE 8322

CMD ["python3", "-m", "uvicorn", "jiuwenbox.server.app:app", "--host", "0.0.0.0", "--port", "8321"]
