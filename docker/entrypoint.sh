#!/bin/bash
set -e

source /usr/local/bin/adr-runtime-user.sh

echo "================================================"
echo "  ArXiv Daily Researcher - Docker Container"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "================================================"

# Configuration with defaults.
# The daily run time is configured from the WebUI (runtime/config.json,
# "daily_research.run_time" as HH:MM) and installed at container start;
# there is deliberately no environment-variable override anymore.
CRON_SCHEDULE=""

adr_configure_runtime_user

# Create and verify all host-mounted application paths as the mapped NAS user.
# Root only touches container-internal cron/account files below, so new reports,
# SQLite files, logs and configuration backups never become root-owned.
for APP_DIRECTORY in \
    /app/data \
    /app/data/daily_research \
    /app/data/keywords \
    /app/data/reports/daily_research/markdown \
    /app/data/reports/daily_research/html \
    /app/data/reports/trend_research/markdown \
    /app/data/reports/trend_research/html \
    /app/data/reports/keyword_trend/markdown \
    /app/data/reports/keyword_trend/html \
    /app/data/history \
    /app/data/reference_pdfs \
    /app/data/downloaded_pdfs \
    /app/logs \
    /app/configs \
    /app/runtime; do
    adr_prepare_writable_directory "$APP_DIRECTORY"
done
adr_require_writable_file_if_present /app/.env

# Upgrade v4.1 deployments before cron reads the schedule. The helper only
# copies configs/config.json when runtime/config.json is still absent and
# leaves the source untouched for a safe rollback.
adr_run_as_user bash -c \
    'cd /app && PYTHONPATH=/app/src exec /usr/local/bin/python -c "from utils.config_io import ensure_runtime_config_path; ensure_runtime_config_path()"'

if [ -f /app/runtime/config.json ]; then
    CRON_SCHEDULE=$(python - <<'PYEOF'
import json, re

try:
    raw = open("/app/runtime/config.json", encoding="utf-8").read()
    raw = re.sub(r"^\s*//.*$", "", raw, flags=re.M)
    value = json.loads(raw).get("daily_research", {}).get("run_time")
    if isinstance(value, str) and re.fullmatch(r"\d{1,2}:\d{2}", value.strip()):
        hh, mm = value.strip().split(":")
        if 0 <= int(hh) <= 23 and 0 <= int(mm) <= 59:
            print(f"{int(mm)} {int(hh)}")
except Exception:
    pass
PYEOF
)
fi
if [ -n "$CRON_SCHEDULE" ]; then
    CRON_SCHEDULE="$CRON_SCHEDULE * * *"
else
    # 配置缺失或不可读时的兜底：中午 12 点。
    CRON_SCHEDULE="0 12 * * *"
fi
RUN_ON_STARTUP="${RUN_ON_STARTUP:-false}"
MODE="${MODE:-cron}"

echo "Mode: $MODE"
echo "Timezone: $TZ"
echo "Cron Schedule: $CRON_SCHEDULE"
echo "Run on Startup: $RUN_ON_STARTUP"

# Clean up stale log files
LOG_KEEP_DAYS="${LOG_KEEP_DAYS:-30}"
for LOG_PATTERN in \
    'cron_*.log' 'startup_*.log' 'daily_*.log' 'trend_*.log' \
    'webdav_*.log' 'keyword_*.log' 'legacy_import_*.log' \
    'history_data_repair_*.log' 'history_omission_scan_*.log' \
    'supplement_*.log' 'backfill_*.log' 'update_*.log'; do
    adr_run_as_user find /app/logs -name "$LOG_PATTERN" -type f \
        -mtime +"$LOG_KEEP_DAYS" -delete 2>/dev/null || true
done

# ==================== Interactive Setup Wizard ====================
# Run setup wizard on first deployment (no .env file) or when SETUP_WIZARD=true
SETUP_WIZARD="${SETUP_WIZARD:-auto}"
if [ "$SETUP_WIZARD" = "true" ]; then
    echo ""
    echo "Running interactive setup wizard..."
    adr_run_as_user bash -c 'cd /app && exec python src/utils/setup_wizard.py'
    echo "Setup wizard complete."
    echo ""
elif [ "$SETUP_WIZARD" = "auto" ] && [ ! -f /app/.env ]; then
    echo ""
    echo "No .env file detected — first deployment."
    echo "Running interactive setup wizard..."
    adr_run_as_user bash -c 'cd /app && exec python src/utils/setup_wizard.py'
    echo "Setup wizard complete."
    echo ""
fi

# ==================== Release Update Availability ====================
# A container must not replace its own image.  Check GitHub Releases after a
# normal worker start, then the dedicated cron task below checks daily even if
# daily research is not run.  The Python entry point observes the WebUI toggle
# and sends a notification only when a newer release is available.
if [ "$MODE" != "run-once" ] && [ "$RUN_ON_STARTUP" != "true" ]; then
    UPDATE_CHECK_LOG="/app/logs/update_$(date +%Y%m%d).log"
    echo "Checking published release availability in background..."
    adr_run_as_user bash -c \
        'cd /app && PYTHONPATH=/app/src exec /usr/local/bin/python -m utils.updater >> "$1" 2>&1' \
        _ "$UPDATE_CHECK_LOG" &
fi

# ==================== Single Execution Mode ====================
if [ "$MODE" = "run-once" ]; then
    LOG_FILE="/app/logs/cron_$(date +%Y%m%d_%H%M%S).log"
    echo "Running in single-execution mode..."
    echo "Log: $LOG_FILE"
    RESULT=0
    adr_run_as_user bash -c \
        'set -o pipefail; cd /app && python main.py 2>&1 | tee "$1"' \
        _ "$LOG_FILE" || RESULT=$?
    exit "$RESULT"
fi

# ==================== Cron Mode ====================

# cron does not inherit the container's environment by default.  The worker
# loads application settings from the mounted /app/.env, so never copy the
# whole process environment here: that would persist API keys, webhook URLs,
# and SMTP/WebDAV passwords in the container filesystem.  Keep only the
# non-sensitive runtime values needed by cron-launched Python processes.
{
    printf 'PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\n'
    printf 'PYTHONUNBUFFERED=1\n'
    printf 'PYTHONDONTWRITEBYTECODE=1\n'
    if [ -n "${TZ:-}" ]; then
        printf 'TZ=%s\n' "$TZ"
    fi
} > /etc/environment
chmod 0644 /etc/environment

# Create the cron job
CRON_LOG="/app/logs/cron_\$(date +\%Y\%m\%d_\%H\%M\%S).log"
CRON_CMD="cd /app && /usr/local/bin/python main.py >> $CRON_LOG 2>&1"
WEBDAV_CRON_LOG="/app/logs/webdav_\$(date +\%Y\%m\%d).log"
WEBDAV_CRON_CMD="cd /app && PYTHONPATH=/app/src /usr/local/bin/python -m utils.webdav_scheduler >> $WEBDAV_CRON_LOG 2>&1"
# 关键词标准化/趋势报告：每天 0 点静默执行，与日报主流程解耦。
KEYWORD_CRON_LOG="/app/logs/keyword_\$(date +\%Y\%m\%d).log"
KEYWORD_CRON_CMD="cd /app && PYTHONPATH=/app/src /usr/local/bin/python -m modes.keyword_maintenance >> $KEYWORD_CRON_LOG 2>&1"
UPDATE_CRON_LOG="/app/logs/update_\$(date +\%Y\%m\%d).log"
UPDATE_CRON_CMD="cd /app && PYTHONPATH=/app/src /usr/local/bin/python -m utils.updater >> $UPDATE_CRON_LOG 2>&1"
{
    echo "$CRON_SCHEDULE $CRON_CMD"
    # This lightweight tick only performs a transfer when config.json selects
    # WebDAV's scheduled mode and its own cron expression matches.  It keeps
    # the established cron/watcher/tail container lifecycle unchanged.
    echo "* * * * * $WEBDAV_CRON_CMD"
    echo "0 0 * * * $KEYWORD_CRON_CMD"
    # Independent from daily research: update availability remains observable
    # when the research task is disabled, queued, or otherwise not run.
    echo "17 9 * * * $UPDATE_CRON_CMD"
} > /etc/cron.d/arxiv-daily
chmod 0644 /etc/cron.d/arxiv-daily
crontab -u "$ADR_APP_USER" /etc/cron.d/arxiv-daily

echo "Cron job installed:"
crontab -u "$ADR_APP_USER" -l

# Run immediately on startup if configured
if [ "$RUN_ON_STARTUP" = "true" ]; then
    echo ""
    echo "Running initial execution..."
    STARTUP_LOG="/app/logs/startup_$(date +%Y%m%d_%H%M%S).log"
    adr_run_as_user bash -c \
        'set -o pipefail; cd /app && python main.py 2>&1 | tee "$1"' \
        _ "$STARTUP_LOG"
    echo "Initial execution complete."
    echo ""
fi

# ==================== WebUI Trigger File Watcher ====================
# The WebUI (in a separate, thin container) puts validated
# JSON requests in this shared queue.  Do not delete requests on startup: they
# are durable user actions and must survive a worker restart.
TRIGGER_DIR="/app/data/run/webui_triggers"
PID_FILE="/app/data/run/webui_triggered.pid"
adr_run_as_user mkdir -p "$TRIGGER_DIR/status"
WATCHER_HEARTBEAT="$TRIGGER_DIR/.watcher-heartbeat"
adr_run_as_user touch "$WATCHER_HEARTBEAT"
# Status receipts and archived restart markers are bounded operational audit
# data. Run a startup pass so stale files are cleaned even when no new WebUI
# request is submitted for a while.
adr_run_as_user python /app/src/utils/webui_trigger.py \
    --maintain-trigger-files --data-dir /app/data \
    || echo "[trigger-watcher] Trigger-file maintenance failed; will retry on the next startup/request"

# A container restart kills the child process with it.  Return an atomically
# claimed request to the queue so a SIGKILL/redeploy cannot silently lose a
# manual user action.  A normal completed request removes its .running file.
for CLAIMED_FILE in "$TRIGGER_DIR"/*.running; do
    [ -e "$CLAIMED_FILE" ] || continue
    REQUEST_FILE="${CLAIMED_FILE%.running}.json"
    if [ ! -e "$REQUEST_FILE" ]; then
        adr_run_as_user mv "$CLAIMED_FILE" "$REQUEST_FILE" \
            || echo "[trigger-watcher] Failed to recover $CLAIMED_FILE"
    fi
done

trigger_watcher() {
    echo "[trigger-watcher] Started. Polling $TRIGGER_DIR every 5s..."
    # The WebUI restart button drops this marker into the shared volume; a
    # worker restart re-runs this entrypoint, reinstalling cron from config.
    # The marker is archived before restarting so the request remains
    # auditable. A bounded maintenance pass prevents repeated restarts from
    # growing the shared volume forever.
    RESTART_MARKER="$TRIGGER_DIR/restart_worker.request"
    while true; do
        adr_run_as_user touch "$WATCHER_HEARTBEAT"
        if [ -e "$RESTART_MARKER" ]; then
            RESTART_ARCHIVE="$RESTART_MARKER.done-$(date +%Y%m%dT%H%M%S%N)"
            RESTART_ARCHIVE_SUFFIX=2
            while [ -e "$RESTART_ARCHIVE" ]; do
                RESTART_ARCHIVE="$RESTART_MARKER.done-$(date +%Y%m%dT%H%M%S%N)-$RESTART_ARCHIVE_SUFFIX"
                RESTART_ARCHIVE_SUFFIX=$((RESTART_ARCHIVE_SUFFIX + 1))
            done
            if adr_run_as_user mv "$RESTART_MARKER" "$RESTART_ARCHIVE" 2>/dev/null; then
                adr_run_as_user python /app/src/utils/webui_trigger.py \
                    --maintain-trigger-files --data-dir /app/data \
                    || echo "[trigger-watcher] Trigger-file maintenance failed after restart request"
                echo "[trigger-watcher] WebUI restart request: restarting container..."
                # PID 1 在独立 PID namespace 内默认丢弃一切信号（含 KILL）；
                # 只有注册了 handler 的信号才会送达，见文件末尾的 trap。
                kill -TERM 1
            else
                echo "[trigger-watcher] Failed to archive WebUI restart request; leaving it queued"
            fi
        fi
        # Normal research requests may pass queued history maintenance. The
        # selector also applies the saved idle/time-window policy before a
        # history request is claimed, so it never blocks this watcher merely
        # by being the lexicographically first JSON file.
        REQUEST_FILE=$(adr_run_as_user python /app/src/utils/webui_trigger.py \
            --next-eligible-request --data-dir /app/data) || \
            echo "[trigger-watcher] Eligible-request selection failed; will retry"
        if [ -n "$REQUEST_FILE" ]; then
            CLAIMED_FILE="${REQUEST_FILE%.json}.running"
            # Atomic claim prevents a future watcher implementation or a manual
            # operator invocation from executing the same request twice.
            if adr_run_as_user mv "$REQUEST_FILE" "$CLAIMED_FILE" 2>/dev/null; then
                LOG_FILE="/app/logs/manual_$(date +%Y%m%d_%H%M%S).log"
                echo "[trigger-watcher] Claimed request: $CLAIMED_FILE"
                # Run synchronously to preserve FIFO ordering and avoid two
                # resource-heavy WebUI requests competing in one worker.
                # ``set -e`` applies to this shell too.  Keep a rejected or
                # failed manual request from terminating the watcher loop (and
                # therefore the otherwise healthy cron container).
                RESULT=0
                adr_run_as_user bash -c \
                    'exec python /app/src/utils/webui_trigger.py "$1" --pid-file "$2" >> "$3" 2>&1' \
                    _ "$CLAIMED_FILE" "$PID_FILE" "$LOG_FILE" || RESULT=$?
                echo "[trigger-watcher] Request finished with exit=$RESULT"
            fi
        fi
        sleep 5
    done
}

trigger_watcher &

# Start cron daemon
echo "Starting cron daemon..."
cron

# Keep container alive
echo "Container is running. Waiting for scheduled executions..."
echo "Schedule: $CRON_SCHEDULE"
echo ""

# Keep the container alive by tailing the system log.
# The tail runs as a child (not exec'd): as PID 1, bash in its own PID
# namespace drops every signal it has no handler for — including SIGKILL —
# so the restart path works by installing a TERM handler that terminates
# the tail and lets this script (PID 1) exit normally. The same handler
# also gives `docker stop` a clean, fast shutdown.
adr_run_as_user touch /app/logs/system.log
tail -f /app/logs/system.log &
TAIL_PID=$!
trap 'kill -TERM "$TAIL_PID" 2>/dev/null' TERM INT
wait "$TAIL_PID" || true
