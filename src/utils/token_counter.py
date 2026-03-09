"""
Token 消耗追踪模块

提供线程安全的全局 Token 计数器，统计本次运行中所有 LLM 调用的 token 消耗。
支持按模型分类统计，供报告和通知展示。
"""

import threading
from dataclasses import dataclass, field
from typing import Dict, Any


@dataclass
class _ModelUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class TokenCounter:
    """
    线程安全的全局 Token 计数器（单例）。

    使用方式:
        from utils.token_counter import token_counter
        token_counter.add("gpt-4o-mini", prompt_tokens=100, completion_tokens=50)
        summary = token_counter.get_summary()
        token_counter.reset()
    """

    _instance = None
    _cls_lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._cls_lock:
                if cls._instance is None:
                    obj = super().__new__(cls)
                    obj._usage: Dict[str, _ModelUsage] = {}
                    obj._lock = threading.Lock()
                    cls._instance = obj
        return cls._instance

    def add(self, model: str, prompt_tokens: int, completion_tokens: int) -> None:
        """记录一次 LLM 调用的 token 消耗。"""
        if not model:
            model = "unknown"
        with self._lock:
            if model not in self._usage:
                self._usage[model] = _ModelUsage()
            self._usage[model].prompt_tokens += prompt_tokens
            self._usage[model].completion_tokens += completion_tokens

    def get_summary(self) -> Dict[str, Any]:
        """
        返回当前 token 消耗汇总。

        返回格式:
            {
                "by_model": {
                    "model-name": {"prompt": N, "completion": M, "total": N+M},
                    ...
                },
                "total_prompt": N,
                "total_completion": M,
                "total": N+M,
                "has_data": bool
            }
        """
        with self._lock:
            by_model = {
                model: {
                    "prompt": u.prompt_tokens,
                    "completion": u.completion_tokens,
                    "total": u.total_tokens,
                }
                for model, u in self._usage.items()
            }
            total_prompt = sum(u.prompt_tokens for u in self._usage.values())
            total_completion = sum(u.completion_tokens for u in self._usage.values())
            return {
                "by_model": by_model,
                "total_prompt": total_prompt,
                "total_completion": total_completion,
                "total": total_prompt + total_completion,
                "has_data": bool(self._usage),
            }

    def reset(self) -> None:
        """重置计数器（在每次运行开始时调用）。"""
        with self._lock:
            self._usage.clear()

    def format_markdown(self) -> str:
        """格式化为 Markdown 表格字符串，用于报告展示。"""
        summary = self.get_summary()
        if not summary["has_data"]:
            return ""

        lines = []
        lines.append(
            f"- **总计**: {summary['total']:,} tokens "
            f"（输入 {summary['total_prompt']:,} + 输出 {summary['total_completion']:,}）"
        )

        if len(summary["by_model"]) > 1:
            lines.append("")
            lines.append("| 模型 | 输入 | 输出 | 合计 |")
            lines.append("|------|------|------|------|")
            for model, usage in summary["by_model"].items():
                lines.append(
                    f"| {model} | {usage['prompt']:,} | {usage['completion']:,} | {usage['total']:,} |"
                )

        return "\n".join(lines)

    def format_text(self) -> str:
        """格式化为单行文本，用于通知模板。"""
        summary = self.get_summary()
        if not summary["has_data"]:
            return "N/A"
        return (
            f"共 {summary['total']:,} tokens "
            f"（输入 {summary['total_prompt']:,} / 输出 {summary['total_completion']:,}）"
        )


# 模块级单例，直接 import 使用
token_counter = TokenCounter()
