# ========================================
# ArXiv 每日研究系统 - 定时运行脚本 (Windows PowerShell)
# ========================================

# 设置编码为 UTF-8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

# 获取脚本所在目录
$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $SCRIPT_DIR

# 日志文件路径
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LOG_FILE = Join-Path $SCRIPT_DIR "logs\cron_$timestamp.log"

# 确保日志目录存在
$logsDir = Join-Path $SCRIPT_DIR "logs"
if (-not (Test-Path $logsDir)) {
    New-Item -ItemType Directory -Path $logsDir -Force | Out-Null
}

# 日志写入函数
function Write-Log {
    param([string]$Message)
    Add-Content -Path $LOG_FILE -Value $Message -Encoding UTF8
}

# 记录开始时间
Write-Log "========================================"
Write-Log "开始时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Log "========================================"

# 检查并设置虚拟环境
function Setup-Venv {
    $venvDir = $null

    # 检查是否存在虚拟环境
    $venvPath = Join-Path $SCRIPT_DIR "venv"
    $dotVenvPath = Join-Path $SCRIPT_DIR ".venv"

    if (Test-Path $venvPath) {
        $venvDir = $venvPath
    } elseif (Test-Path $dotVenvPath) {
        $venvDir = $dotVenvPath
    }

    # 如果虚拟环境不存在，则创建
    if (-not $venvDir) {
        Write-Log "虚拟环境不存在，正在创建..."
        $venvDir = $venvPath

        # 检查 python 是否可用
        $pythonCmd = $null
        if (Get-Command python -ErrorAction SilentlyContinue) {
            $pythonCmd = "python"
        } elseif (Get-Command python3 -ErrorAction SilentlyContinue) {
            $pythonCmd = "python3"
        } elseif (Get-Command py -ErrorAction SilentlyContinue) {
            $pythonCmd = "py"
        } else {
            Write-Log "ERROR: Python 未找到，请先安装 Python 3"
            exit 1
        }

        # 创建虚拟环境
        $result = & $pythonCmd -m venv $venvDir 2>&1
        Write-Log $result
        if ($LASTEXITCODE -ne 0) {
            Write-Log "ERROR: 创建虚拟环境失败"
            exit 1
        }
        Write-Log "虚拟环境创建成功: $venvDir"
    }

    # 激活虚拟环境
    Write-Log "激活虚拟环境: $venvDir"
    $activateScript = Join-Path $venvDir "Scripts\Activate.ps1"
    if (Test-Path $activateScript) {
        . $activateScript
    } else {
        Write-Log "ERROR: 找不到激活脚本: $activateScript"
        exit 1
    }

    # 检查依赖是否已安装
    $checkResult = & python -c "import arxiv" 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Log "依赖未完整安装，正在安装..."

        $requirementsFile = Join-Path $SCRIPT_DIR "requirements.txt"
        if (Test-Path $requirementsFile) {
            $pipResult = & pip install -r $requirementsFile 2>&1
            Write-Log ($pipResult | Out-String)
            if ($LASTEXITCODE -ne 0) {
                Write-Log "ERROR: 安装依赖失败"
                exit 1
            }
            Write-Log "依赖安装成功"
        } else {
            Write-Log "WARNING: requirements.txt 不存在"
        }
    } else {
        Write-Log "依赖检查通过"
    }
}

# 执行虚拟环境设置
Setup-Venv

# 运行主程序
Write-Log "运行 main.py..."
$mainScript = Join-Path $SCRIPT_DIR "main.py"
$output = & python $mainScript 2>&1
Write-Log ($output | Out-String)

# 记录退出状态
$EXIT_CODE = $LASTEXITCODE
Write-Log ""
Write-Log "========================================"
Write-Log "结束时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Log "退出状态: $EXIT_CODE"
Write-Log "========================================"

# 如果失败，记录错误
if ($EXIT_CODE -ne 0) {
    Write-Log "ERROR: 程序执行失败，退出码 $EXIT_CODE"
}

exit $EXIT_CODE
