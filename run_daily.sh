#!/bin/bash

# ========================================
# ArXiv 每日研究系统 - 定时运行脚本
# ========================================

# 获取脚本所在目录的绝对路径
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

# 日志文件路径
LOG_FILE="$SCRIPT_DIR/logs/cron_$(date +%Y%m%d_%H%M%S).log"

# 确保日志目录存在
mkdir -p "$SCRIPT_DIR/logs"

# 记录开始时间
echo "========================================" >> "$LOG_FILE"
echo "开始时间: $(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
echo "========================================" >> "$LOG_FILE"

# 激活虚拟环境（如果存在）
if [ -d "$SCRIPT_DIR/venv" ]; then
    echo "激活虚拟环境..." >> "$LOG_FILE"
    source "$SCRIPT_DIR/venv/bin/activate"
elif [ -d "$SCRIPT_DIR/.venv" ]; then
    echo "激活虚拟环境..." >> "$LOG_FILE"
    source "$SCRIPT_DIR/.venv/bin/activate"
fi

# 运行主程序
echo "运行 main.py..." >> "$LOG_FILE"
python3 "$SCRIPT_DIR/main.py" >> "$LOG_FILE" 2>&1

# 记录退出状态
EXIT_CODE=$?
echo "" >> "$LOG_FILE"
echo "========================================" >> "$LOG_FILE"
echo "结束时间: $(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
echo "退出状态: $EXIT_CODE" >> "$LOG_FILE"
echo "========================================" >> "$LOG_FILE"

# 如果失败，发送通知（可选）
if [ $EXIT_CODE -ne 0 ]; then
    echo "ERROR: 程序执行失败，退出码 $EXIT_CODE" >> "$LOG_FILE"
fi

exit $EXIT_CODE
