FROM docker.m.daocloud.io/library/python:3.12-slim

LABEL maintainer="yzr278892"
LABEL description="ArXiv Daily Researcher - LLM-powered paper monitoring and analysis"

# Timezone and Python settings
ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    cron \
    tzdata \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone

WORKDIR /app

# Install Python dependencies (layer caching, use China mirror)
COPY requirements.txt .
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com -r requirements.txt

# Copy application code
COPY main.py ./
COPY src/ ./src/

# Copy default config files (will be overridden by volume mounts)
COPY .env.example ./
COPY configs/ ./configs/

# Copy entrypoint
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Create data directories with proper permissions
RUN mkdir -p /app/data/reports/daily_research/markdown \
             /app/data/reports/daily_research/html \
             /app/data/reports/trend_research/markdown \
             /app/data/reports/trend_research/html \
             /app/data/reports/keyword_trend/markdown \
             /app/data/reports/keyword_trend/html \
             /app/data/history /app/data/reference_pdfs \
             /app/data/downloaded_pdfs /app/logs \
    && chmod -R 777 /app/data /app/logs

# Health check
HEALTHCHECK --interval=300s --timeout=10s --retries=3 \
    CMD python -c "import sys; sys.path.insert(0,'src'); import config; print('OK')" || exit 1

ENTRYPOINT ["/entrypoint.sh"]
