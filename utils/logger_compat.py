"""日志兼容模块 - 支持 NoneBot 和 AstrBot 两种日志系统"""
from typing import Optional


class LoggerWrapper:
    """日志包装器，支持两种日志系统"""
    
    def __init__(self, astrbot_logger=None):
        self.astrbot_logger = astrbot_logger
    
    def debug(self, msg: str, *args, **kwargs):
        if self.astrbot_logger:
            self.astrbot_logger.debug(msg)
        else:
            print(f"[DEBUG] {msg}")
    
    def info(self, msg: str, *args, **kwargs):
        if self.astrbot_logger:
            self.astrbot_logger.info(msg)
        else:
            print(f"[INFO] {msg}")
    
    def warning(self, msg: str, *args, **kwargs):
        if self.astrbot_logger:
            self.astrbot_logger.warning(msg)
        else:
            print(f"[WARNING] {msg}")
    
    def error(self, msg: str, *args, exc_info=False, **kwargs):
        if self.astrbot_logger:
            self.astrbot_logger.error(msg)
        else:
            print(f"[ERROR] {msg}")


# 全局日志实例
_logger: Optional[LoggerWrapper] = None


def init_logger(astrbot_logger=None):
    """初始化日志系统"""
    global _logger
    _logger = LoggerWrapper(astrbot_logger)


def get_logger() -> LoggerWrapper:
    """获取日志实例"""
    global _logger
    if _logger is None:
        _logger = LoggerWrapper()
    return _logger
