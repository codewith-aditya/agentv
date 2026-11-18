FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git tini libatomic1 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Docker CLI is only used to launch MCP stdio servers inside the runner container.
ARG DOCKER_CLI_VERSION=27.5.1
RUN curl -fsSL "https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKER_CLI_VERSION}.tgz" -o /tmp/docker.tgz \
    && tar -xzf /tmp/docker.tgz -C /tmp \
    && install -m 0755 /tmp/docker/docker /usr/local/bin/docker \
    && rm -rf /tmp/docker /tmp/docker.tgz

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

COPY agentv ./agentv
COPY runner ./runner
COPY plugins ./plugins

RUN mkdir -p /app/data /workspace

EXPOSE 8080
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "agentv"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/healthz'%os.environ.get('WEB_PORT','8080'),timeout=3)"
