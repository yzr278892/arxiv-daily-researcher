import logging
import sys
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

# 尝试从配置中导入settings，以获取绝对路径
# 如果导入失败（比如单独测试这个文件时），则回退到当前目录
try:
    from config import settings
    LOG_DIR = settings.PROJECT_ROOT / "logs"
except ImportError:
    LOG_DIR = Path(__file__).resolve().parent.parent / "logs"

def setup_logger(name: str = "ArxivResearcher"):
    """
    配置并返回一个具有控制台和文件输出的Logger实例。
    
    参数:
        name (str): 日志记录器的名称，默认为"ArxivResearcher"
    
    返回值:
        logging.Logger: 配置好的Logger对象
    
    功能说明:
        - 日志会同时输出到控制台和文件
        - 控制台输出为INFO级别及以上
        - 文件类使用轮换处理，防止日志文件过大（单个文件最大5MB，保留3个备份）
        - 日志格式包含时间、级别、模块名和消息内容
    """
    # 1. 确保日志目录存在
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    
    # 2. 定义日志文件路径
    log_file_path = LOG_DIR / "system.log"

    # 3. 创建Logger对象
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    
    # 防止重复添加Handler（Jupyter或多次调用时稀有问题）
    if logger.handlers:
        return logger

    # 4. 定义日志格式
    # 格式：[时间] [日志级别] [模块名] - 消息
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)-15s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # 5. Handler: 控制台 (StreamHandler)
    # 指向标准输出（stdout）
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)
    logger.addHandler(console_handler)

    # 6. Handler: 文件 (RotatingFileHandler)
    # 特点：单个日志最大5MB，最多保留3个备份
    file_handler = RotatingFileHandler(
        log_file_path, 
        maxBytes=5*1024*1024, 
        backupCount=3, 
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)
    logger.addHandler(file_handler)

    return logger
