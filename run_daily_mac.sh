#!/bin/bash

# ========================================
# ArXiv 每日研究系统 - 定时运行脚本 (macOS)
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

# 检查并设置虚拟环境
setup_venv() {
    local venv_dir=""

    # 检查是否存在虚拟环境
    if [ -d "$SCRIPT_DIR/venv" ]; then
        venv_dir="$SCRIPT_DIR/venv"
    elif [ -d "$SCRIPT_DIR/.venv" ]; then
        venv_dir="$SCRIPT_DIR/.venv"
    fi

    # 如果虚拟环境不存在，则创建
    if [ -z "$venv_dir" ]; then
        echo "虚拟环境不存在，正在创建..." >> "$LOG_FILE"
        venv_dir="$SCRIPT_DIR/venv"

        # 检查 python3 是否可用（macOS 优先检查 brew 安装的 python）
        if command -v python3 &> /dev/null; then
            PYTHON_CMD="python3"
        elif command -v /usr/local/bin/python3 &> /dev/null; then
            PYTHON_CMD="/usr/local/bin/python3"
        elif command -v /opt/homebrew/bin/python3 &> /dev/null; then
            PYTHON_CMD="/opt/homebrew/bin/python3"
        else
            echo "ERROR: python3 未找到，请先安装 Python 3 (推荐使用 brew install python3)" >> "$LOG_FILE"
            exit 1
        fi

        # 创建虚拟环境
        $PYTHON_CMD -m venv "$venv_dir" >> "$LOG_FILE" 2>&1
        if [ $? -ne 0 ]; then
            echo "ERROR: 创建虚拟环境失败" >> "$LOG_FILE"
            exit 1
        fi
        echo "虚拟环境创建成功: $venv_dir" >> "$LOG_FILE"
    fi

    # 激活虚拟环境
    echo "激活虚拟环境: $venv_dir" >> "$LOG_FILE"
    source "$venv_dir/bin/activate"

    # 检查依赖是否已安装（通过检查关键包）
    if ! python3 -c "import arxiv" &> /dev/null; then
        echo "依赖未完整安装，正在安装..." >> "$LOG_FILE"

        if [ -f "$SCRIPT_DIR/requirements.txt" ]; then
            pip install -r "$SCRIPT_DIR/requirements.txt" >> "$LOG_FILE" 2>&1
            if [ $? -ne 0 ]; then
                echo "ERROR: 安装依赖失败" >> "$LOG_FILE"
                exit 1
            fi
            echo "依赖安装成功" >> "$LOG_FILE"
        else
            echo "WARNING: requirements.txt 不存在" >> "$LOG_FILE"
        fi
    else
        echo "依赖检查通过" >> "$LOG_FILE"
    fi
}

# 执行虚拟环境设置
setup_venv

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
