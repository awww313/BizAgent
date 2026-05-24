"""
分级异常系统 + 指数退避重试
============================
将模型调用、超时、鉴权、限流、文件解析、数据为空等异常统一为中文友好提示。
"""

import time
import random
import logging
from functools import wraps

logger = logging.getLogger(__name__)

ERROR_MESSAGES = {
    "model_call": "模型调用失败，请检查网络连接或 API Key 是否有效",
    "timeout": "请求超时，服务暂时不可用，请稍后重试",
    "auth": "认证失败，请检查 API Key 是否正确",
    "rate_limit": "请求频率过高，请稍后重试",
    "file_parse": "文件解析失败，请检查文件格式是否正确，支持 TXT/CSV/JSON/MD/PDF/DOCX/XLSX",
    "data_empty": "暂无相关数据，请确认查询条件是否正确",
    "unknown": "系统繁忙，请稍后重试",
}


class AgentError(Exception):
    """Agent 基础异常"""

    def __init__(self, message=None, detail=None):
        self.message = message or ERROR_MESSAGES["unknown"]
        self.detail = detail
        super().__init__(self.message)


class ModelCallError(AgentError):
    """模型调用失败"""

    def __init__(self, detail=None):
        super().__init__(ERROR_MESSAGES["model_call"], detail)


class TimeoutError(AgentError):
    """请求超时"""

    def __init__(self, detail=None):
        super().__init__(ERROR_MESSAGES["timeout"], detail)


class AuthError(AgentError):
    """认证失败"""

    def __init__(self, detail=None):
        super().__init__(ERROR_MESSAGES["auth"], detail)


class RateLimitError(AgentError):
    """频率限制"""

    def __init__(self, detail=None):
        super().__init__(ERROR_MESSAGES["rate_limit"], detail)


class FileParseError(AgentError):
    """文件解析失败"""

    def __init__(self, detail=None):
        super().__init__(ERROR_MESSAGES["file_parse"], detail)


class DataNotFoundError(AgentError):
    """数据为空"""

    def __init__(self, detail=None):
        super().__init__(ERROR_MESSAGES["data_empty"], detail)


def retry_with_backoff(
    max_retries=3,
    base_delay=1.0,
    max_delay=30.0,
    backoff_factor=2.0,
    jitter=True,
    exceptions=(ModelCallError, TimeoutError, RateLimitError),
):
    """指数退避重试装饰器。

    只有 in exceptions 的异常会被重试，其他异常直接透传。
    所有重试耗尽后抛出最后一个异常。

    Args:
        max_retries: 最大重试次数
        base_delay: 初始延迟（秒）
        max_delay: 最大延迟（秒）
        backoff_factor: 退避因子
        jitter: 是否添加随机抖动
        exceptions: 需要重试的异常类型元组
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_retries:
                        delay = min(base_delay * (backoff_factor**attempt), max_delay)
                        if jitter:
                            delay = delay * (0.5 + random.random() * 0.5)
                        logger.warning(
                            "[Retry] %s 第 %d/%d 次失败: %s, %.1fs 后重试",
                            func.__name__,
                            attempt + 1,
                            max_retries,
                            e.message,
                            delay,
                        )
                        time.sleep(delay)
                except AgentError:
                    raise  # 其他 AgentError 不重试，直接透传
                except Exception as e:
                    raise ModelCallError(detail=str(e))

            # 所有重试耗尽
            if isinstance(last_exception, RateLimitError):
                raise RateLimitError(detail="已重试多次仍被限流，请降低请求频率")
            raise ModelCallError(
                detail=f"已重试 {max_retries} 次仍失败: {last_exception.detail}"
                if last_exception
                else "重试耗尽"
            )

        return wrapper

    return decorator
