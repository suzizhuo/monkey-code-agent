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
    JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 \
    ANDROID_HOME=/opt/android-sdk \
    ANDROID_SDK_ROOT=/opt/android-sdk \
    GRADLE_USER_HOME=/work/.gradle \
    NPM_CONFIG_CACHE=/work/.npm \
    PIP_CACHE_DIR=/work/.pip-cache \
    DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        openjdk-17-jdk-headless \
        build-essential \
        pkg-config \
        cmake \
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
        nodejs \
        npm \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

RUN python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* \
    && chmod -R a+rX /ms-playwright

RUN mkdir -p /opt/android-sdk \
    && git config --system --add safe.directory /work \
    && git config --system user.name "Isolis Agent" \
    && git config --system user.email "agent@isolis.local" \
    && chmod -R a+rwX /opt/android-sdk

COPY server.py /app/server.py
COPY install-android-sdk.sh /app/install-android-sdk.sh
RUN chmod +x /app/install-android-sdk.sh

EXPOSE 8080

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "/app/server.py"]
