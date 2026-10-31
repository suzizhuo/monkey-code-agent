FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TIKTOKEN_CACHE_DIR=/tmp \
    HOME=/tmp \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONIOENCODING=utf-8 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    CAGE_WORKSPACE=/work \
    NPM_CONFIG_CACHE=/work/.npm \
    PIP_CACHE_DIR=/work/.pip-cache \
    DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git \
        ca-certificates \
        curl \
        wget \
        ripgrep \
        jq \
        unzip \
        zip \
        xz-utils \
        tar \
        less \
        vim-tiny \
        nano \
        procps \
        psmisc \
        file \
        tree \
        rsync \
        openssh-client \
        gnupg \
        tini \
        build-essential \
        pkg-config \
        cmake \
        nodejs \
        npm \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

RUN python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* \
    && chmod -R a+rX /ms-playwright

RUN git config --system --add safe.directory /work \
    && git config --system user.name "Cage Agent" \
    && git config --system user.email "agent@cage.local"

COPY server.py /app/server.py

EXPOSE 8080

USER nobody

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "/app/server.py"]
