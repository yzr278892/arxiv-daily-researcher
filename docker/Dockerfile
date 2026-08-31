# syntax=docker/dockerfile:1
# 统一多阶段构建：`--target worker` 或 `--target webui`。
# 两个目标共享 slim 基底 + 公共依赖层（L2），各自的独有依赖只装一份；
# 构建后剥离 __pycache__（L1）。两个镜像合计比拆分前明显更省磁盘。

# ==================== 公共层：基底 + 时区 + 共享 Python 依赖 ====================
FROM python:3.12-slim AS common

# 国内网络默认使用阿里云镜像；遇到镜像源异常时可在 compose 构建时覆盖。
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ARG PIP_TRUSTED_HOST=mirrors.aliyun.com

LABEL maintainer="yzr278892"

ENV TZ=Asia/Shanghai \
    HOME=/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST}

RUN apt-get update && \
    apt-get install -y --no-install-recommends gosu tzdata && \
    rm -rf /var/lib/apt/lists/* && \
    ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && \
    echo $TZ > /etc/timezone

WORKDIR /app

RUN groupadd --gid 1000 adr && \
    useradd --uid 1000 --gid adr --home-dir /app --shell /bin/bash --no-create-home adr

COPY docker/runtime-user.sh /usr/local/bin/adr-runtime-user.sh
RUN chmod 0755 /usr/local/bin/adr-runtime-user.sh

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install \
      json5==0.13.0 requests==2.32.5 'webdavclient3>=3.14.0' && \
    find /usr/local/lib/python3.12 -name '__pycache__' -type d -prune -exec rm -rf {} +

# ==================== Worker 依赖层 ====================
FROM common AS worker-deps

COPY requirements-core.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install \
      -r requirements-core.txt && \
    find /usr/local/lib/python3.12 -name '__pycache__' -type d -prune -exec rm -rf {} +

# ==================== WebUI 依赖层 ====================
FROM common AS webui-deps

COPY requirements-webui.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install \
      -r requirements-webui.txt && \
    find /usr/local/lib/python3.12 -name '__pycache__' -type d -prune -exec rm -rf {} +

# ==================== Worker 最终镜像（target: worker） ====================
FROM worker-deps AS worker

LABEL description="ArXiv Daily Researcher - LLM-powered paper monitoring and analysis"

# cron 只属于 worker；WebUI 目标不装它。
RUN apt-get update && \
    apt-get install -y --no-install-recommends cron && \
    rm -rf /var/lib/apt/lists/*

COPY main.py VERSION* ./
# Worker does not include presentation-layer code.
COPY src/agents/ ./src/agents/
COPY src/config.py ./src/
COPY src/keyword_tracker/ ./src/keyword_tracker/
COPY src/modes/ ./src/modes/
COPY src/notifications/ ./src/notifications/
COPY src/parsers/ ./src/parsers/
COPY src/report/ ./src/report/
COPY src/scoring_policy.py ./src/
COPY src/sources/ ./src/sources/
COPY src/utils/ ./src/utils/
RUN touch ./src/__init__.py
COPY .env.example ./
COPY configs/ ./configs/

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

RUN mkdir -p /app/data/reports/daily_research/markdown \
             /app/data/reports/daily_research/html \
             /app/data/reports/trend_research/markdown \
             /app/data/reports/trend_research/html \
             /app/data/reports/keyword_trend/markdown \
             /app/data/reports/keyword_trend/html \
             /app/data/daily_research /app/data/keywords \
             /app/data/history /app/data/reference_pdfs \
             /app/data/downloaded_pdfs /app/logs \
    && chown -R adr:adr /app/data /app/logs

HEALTHCHECK --interval=60s --timeout=20s --retries=3 --start-period=30s \
    CMD python /app/src/utils/container_health.py worker || exit 1

ENTRYPOINT ["/entrypoint.sh"]

# ==================== Modern WebUI image (target: webui) ====================
FROM webui-deps AS webui

LABEL description="ArXiv Daily Researcher - modern management WebUI"

COPY src/utils/config_io.py ./src/utils/
COPY src/utils/history_maintenance.py ./src/utils/
COPY src/utils/backup.py ./src/utils/
COPY src/utils/daily_research_store.py ./src/utils/
COPY src/utils/legacy_history.py ./src/utils/
COPY src/utils/run_lock.py ./src/utils/
COPY src/utils/safe_url.py ./src/utils/
COPY src/utils/safe_markdown.py ./src/utils/
COPY src/utils/source_registry.py ./src/utils/
COPY src/utils/webdav_sync.py ./src/utils/
COPY src/utils/webui_trigger.py ./src/utils/
COPY src/utils/container_health.py ./src/utils/
COPY src/modern_webui/ ./src/modern_webui/
COPY src/notifications/ ./src/notifications/
COPY VERSION* ./
COPY .env.example ./
COPY configs/ ./configs/

RUN touch ./src/__init__.py ./src/utils/__init__.py

RUN mkdir -p /app/data /app/logs /app/.modern-webui && \
    chown -R adr:adr /app/data /app/logs /app/.modern-webui

COPY docker/webui-entrypoint.sh /webui-entrypoint.sh
RUN chmod 0755 /webui-entrypoint.sh

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=15s --retries=3 --start-period=20s \
    CMD python /app/src/utils/container_health.py webui --url http://127.0.0.1:8501/api/health || exit 1

ENTRYPOINT ["/webui-entrypoint.sh"]
CMD ["uvicorn", "src.modern_webui.app:app", "--host", "127.0.0.1", "--port", "8501"]
