#!/bin/bash
set -e

echo "================================================"
echo "  ArXiv Daily Researcher - Docker Container"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "================================================"

# Configuration with defaults
CRON_SCHEDULE="${CRON_SCHEDULE:-0 8 * * *}"
RUN_ON_STARTUP="${RUN_ON_STARTUP:-false}"
MODE="${MODE:-cron}"

echo "Mode: $MODE"
echo "Timezone: $TZ"
echo "Cron Schedule: $CRON_SCHEDULE"
echo "Run on Startup: $RUN_ON_STARTUP"

# Ensure data directories exist
mkdir -p /app/data/reports/daily_research/markdown \
         /app/data/reports/daily_research/html \
         /app/data/reports/trend_research/markdown \
         /app/data/reports/trend_research/html \
         /app/data/reports/keyword_trend/markdown \
         /app/data/reports/keyword_trend/html \
         /app/data/history \
         /app/data/reference_pdfs /app/data/downloaded_pdfs \
         /app/logs

# Clean up stale log files
LOG_KEEP_DAYS="${LOG_KEEP_DAYS:-30}"
find /app/logs -name "cron_*.log" -type f -mtime +${LOG_KEEP_DAYS} -delete 2>/dev/null || true
find /app/logs -name "startup_*.log" -type f -mtime +${LOG_KEEP_DAYS} -delete 2>/dev/null || true

# ==================== Single Execution Mode ====================
if [ "$MODE" = "run-once" ]; then
    LOG_FILE="/app/logs/cron_$(date +%Y%m%d_%H%M%S).log"
    echo "Running in single-execution mode..."
    echo "Log: $LOG_FILE"
    cd /app && python main.py 2>&1 | tee "$LOG_FILE"
    exit ${PIPESTATUS[0]}
fi

# ==================== Cron Mode ====================

# Export environment variables for cron
# (cron does not inherit the container's environment by default)
printenv | grep -v "no_proxy" > /etc/environment

# Create the cron job
CRON_LOG="/app/logs/cron_\$(date +\%Y\%m\%d_\%H\%M\%S).log"
CRON_CMD="cd /app && /usr/local/bin/python main.py >> $CRON_LOG 2>&1"
echo "$CRON_SCHEDULE $CRON_CMD" > /etc/cron.d/arxiv-daily
chmod 0644 /etc/cron.d/arxiv-daily
crontab /etc/cron.d/arxiv-daily

echo "Cron job installed:"
crontab -l

# Run immediately on startup if configured
if [ "$RUN_ON_STARTUP" = "true" ]; then
    echo ""
    echo "Running initial execution..."
    cd /app && python main.py 2>&1 | tee /app/logs/startup_$(date +%Y%m%d_%H%M%S).log
    echo "Initial execution complete."
    echo ""
fi

# Start cron daemon
echo "Starting cron daemon..."
cron

# Keep container alive
echo "Container is running. Waiting for scheduled executions..."
echo "Schedule: $CRON_SCHEDULE"
echo ""

# Tail the system log to keep container alive and show output
touch /app/logs/system.log
tail -f /app/logs/system.log
