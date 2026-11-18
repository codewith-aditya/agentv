FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/home/sandbox \
    TERM=dumb \
    NO_COLOR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        coreutils \
        curl \
        ffmpeg \
        git \
        jq \
        nodejs \
        npm \
        procps \
        ripgrep \
        unzip \
        wget \
        zip \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/runner-requirements.txt
RUN pip install --no-cache-dir -r /tmp/runner-requirements.txt && rm /tmp/runner-requirements.txt

RUN useradd --create-home --uid 1000 --shell /bin/bash sandbox

COPY mcp_echo.py /opt/agentv/mcp_echo.py
RUN mkdir -p /workspace /opt/agentv \
    && chown -R sandbox:sandbox /workspace /opt/agentv

USER sandbox
WORKDIR /workspace

CMD ["sleep", "infinity"]
