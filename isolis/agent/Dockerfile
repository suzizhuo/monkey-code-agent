# Isolis full-power agent image.
# Pinned digest-free tag: bookworm still ships openjdk-17; floating tags can lose it.
FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TIKTOKEN_CACHE_DIR=/tmp \
    HOME=/tmp \
    ANDROID_HOME=/opt/android-sdk \
    ANDROID_SDK_ROOT=/opt/android-sdk \
    GRADLE_USER_HOME=/tmp/gradle \
    NPM_CONFIG_CACHE=/tmp/npm \
    npm_config_cache=/tmp/npm

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        openjdk-17-jdk-headless \
        build-essential \
        pkg-config \
        cmake \
        git \
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
        ca-certificates; \
    jdir=$(ls -d /usr/lib/jvm/java-17-openjdk-* | head -n1); \
    ln -sfn "$jdir" /usr/lib/jvm/default-java; \
    rm -rf /var/lib/apt/lists/*

ENV JAVA_HOME=/usr/lib/jvm/default-java \
    PATH=/opt/android-sdk/platform-tools:/opt/android-sdk/cmdline-tools/latest/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

COPY requirements.txt /app/requirements.txt
RUN set -eux; \
    python -m pip install --no-cache-dir -r /app/requirements.txt; \
    python -m playwright install --with-deps chromium

# Android command-line tools + default packages, idempotent at build time.
COPY install-android-sdk.sh /app/install-android-sdk.sh
RUN chmod +x /app/install-android-sdk.sh; \
    ANDROID_HOME=/opt/android-sdk \
    CMDLINE_VERSION=11076708 \
    ANDROID_PACKAGES="platform-tools platforms;android-34 build-tools;34.0.0" \
    /app/install-android-sdk.sh

WORKDIR /app
COPY server.py /app/server.py

EXPOSE 8080

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "/app/server.py"]
