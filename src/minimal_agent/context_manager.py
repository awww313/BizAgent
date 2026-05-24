"""
第四阶段核心：上下文滑动窗口管理
================================
两种裁剪策略：
  1. FixedWindow — 固定轮数裁剪，永远只保留最近 N 轮
  2. Summarization — 摘要法，将旧对话浓缩成"前情提要"

Token 计数使用 tiktoken（项目已安装）。
"""

import json
import logging

import tiktoken

logger = logging.getLogger(__name__)

DEFAULT_ENCODING = "gpt-4"


def count_tokens(text: str) -> int:
    """估算文本的 token 数"""
    if not text:
        return 0
    try:
        enc = tiktoken.encoding_for_model(DEFAULT_ENCODING)
        return len(enc.encode(text))
    except Exception:
        # 回退估算：中文约 2 字/token，英文约 4 字/token
        return len(text) // 2


def count_messages_tokens(messages: list) -> int:
    """估算一组消息的总 token 数"""
    total = 0
    for msg in messages:
        for key in ("content", "role", "name"):
            val = msg.get(key)
            if val:
                total += count_tokens(str(val))
        if "tool_calls" in msg:
            for tc in msg["tool_calls"]:
                total += count_tokens(json.dumps(tc.get("function", {})))
    return total


class FixedWindowStrategy:
    """固定轮数裁剪：永远只保留最近 N 轮对话"""

    def __init__(self, max_turns: int = 10):
        self.max_turns = max_turns

    def apply(self, messages: list) -> list:
        """从完整消息列表中裁剪出窗口内的子集"""
        # 找到 system prompt（始终保留）
        system_msgs = [m for m in messages if m["role"] == "system"]
        other_msgs = [m for m in messages if m["role"] != "system"]

        # 统计用户轮数
        user_turns = sum(1 for m in other_msgs if m["role"] == "user")
        if user_turns <= self.max_turns:
            return messages

        # tool 消息和对应的 assistant tool_calls 视为同一轮
        # 从后往前保留 max_turns 轮
        kept = []
        turn_count = 0
        for m in reversed(other_msgs):
            if m["role"] == "user":
                turn_count += 1
            if turn_count > self.max_turns:
                continue
            kept.append(m)
        kept.reverse()

        return system_msgs + kept

    def describe(self) -> str:
        return f"FixedWindow(max_turns={self.max_turns})"


class SummarizationStrategy:
    """摘要法：当对话过长时，把旧轮浓缩成"前情提要"

    流程：
      1. 对话轮数 > summary_threshold 时触发摘要
      2. 取旧轮（recent_turns 之前的部分）调用模型生成摘要
      3. 构建新上下文：system + 摘要 + 最近 recent_turns 轮
      4. 缓存摘要，仅当新内容加入 old 部分时重新生成
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str = "deepseek-chat",
        recent_turns: int = 3,
        summary_threshold: int = 6,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.recent_turns = recent_turns
        self.summary_threshold = summary_threshold

        # 缓存
        self._cached_summary = None
        self._cached_summary_for_turns = 0  # 当前摘要覆盖了多少轮

    def _call_summary_api(self, old_messages: list) -> str:
        """调用模型对旧消息生成摘要"""
        import requests

        text_parts = []
        for m in old_messages:
            role = m["role"]
            content = m.get("content", "") or ""
            if isinstance(content, str) and content.strip():
                text_parts.append(f"[{role}]: {content[:200]}")

        conversation_text = "\n".join(text_parts)

        prompt = f"""请将以下商务对话浓缩成一段 150-200 字的中文"前情提要"，只保留关键事实、数据和结论。不要客套话。

对话内容：
{conversation_text}

前情提要（150-200 字）："""

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 300,
        }

        try:
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=30,
            )
            resp.raise_for_status()
            summary = resp.json()["choices"][0]["message"]["content"].strip()
            logger.info(f"[Summarization] 生成摘要 ({len(summary)} 字): {summary[:100]}...")
            return summary
        except Exception as e:
            logger.warning(f"[Summarization] 摘要生成失败: {e}，使用简单截断")
            # 回退：取最后几条消息的关键内容
            fallback = []
            for m in old_messages[-4:]:
                c = m.get("content", "") or ""
                if isinstance(c, str) and c.strip():
                    fallback.append(f"{m['role']}: {c[:100]}")
            return "前情提要：" + " | ".join(fallback)

    def apply(self, messages: list) -> list:
        """应用摘要裁剪"""
        system_msgs = [m for m in messages if m["role"] == "system"]
        other_msgs = [m for m in messages if m["role"] != "system"]

        # 计算用户消息轮数
        user_indices = [i for i, m in enumerate(other_msgs) if m["role"] == "user"]
        total_turns = len(user_indices)

        if total_turns <= self.summary_threshold:
            return messages

        # 确定哪些是"旧轮"（超出 recent_turns 的部分）
        keep_start_idx = user_indices[-self.recent_turns] if self.recent_turns > 0 else len(other_msgs)
        old_msgs = other_msgs[:keep_start_idx]
        recent_msgs = other_msgs[keep_start_idx:]

        # 需要重新生成摘要吗？
        old_turn_count = sum(1 for m in old_msgs if m["role"] == "user")
        if self._cached_summary is None or old_turn_count > self._cached_summary_for_turns:
            self._cached_summary = self._call_summary_api(old_msgs)
            self._cached_summary_for_turns = old_turn_count

        summary_msg = {
            "role": "system",
            "content": f"[前情提要] {self._cached_summary}",
        }

        return system_msgs + [summary_msg] + recent_msgs

    def describe(self) -> str:
        return f"Summarization(recent_turns={self.recent_turns}, threshold={self.summary_threshold})"


class ContextManager:
    """上下文管理器：记录多轮对话 + 应用裁剪策略"""

    def __init__(self, strategy: str = "none", **kwargs):
        """
        Args:
            strategy: "none" | "fixed" | "summary"
            **kwargs: 传递给具体策略的参数
                - max_turns (fixed)
                - recent_turns, summary_threshold, api_key, base_url (summary)
        """
        self.strategy_name = strategy
        self._messages = []  # 完整消息历史

        if strategy == "fixed":
            self._impl = FixedWindowStrategy(
                max_turns=kwargs.get("max_turns", 10)
            )
        elif strategy == "summary":
            self._impl = SummarizationStrategy(
                api_key=kwargs["api_key"],
                base_url=kwargs["base_url"],
                model=kwargs.get("model", "deepseek-chat"),
                recent_turns=kwargs.get("recent_turns", 3),
                summary_threshold=kwargs.get("summary_threshold", 6),
            )
        else:
            self._impl = None

    def set_system(self, content: str):
        """设置或更新 system prompt"""
        if self._messages and self._messages[0]["role"] == "system":
            self._messages[0]["content"] = content
        else:
            self._messages.insert(0, {"role": "system", "content": content})

    def add_user(self, content: str):
        self._messages.append({"role": "user", "content": content})

    def add_assistant(self, content: str, tool_calls: list = None):
        msg = {"role": "assistant", "content": content or ""}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        self._messages.append(msg)

    def add_tool_result(self, tool_call_id: str, content: str):
        self._messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": content,
        })

    def get_context(self) -> list:
        """获取应用策略后的消息列表"""
        if self._impl is None:
            return self._messages
        return self._impl.apply(self._messages)

    def get_full_history(self) -> list:
        """获取完整消息历史"""
        return self._messages

    def stats(self) -> dict:
        """返回统计信息"""
        full = self._messages
        trimmed = self.get_context()
        return {
            "strategy": self.strategy_name,
            "strategy_detail": self._impl.describe() if self._impl else "none",
            "total_messages": len(full),
            "context_messages": len(trimmed),
            "total_tokens": count_messages_tokens(full),
            "context_tokens": count_messages_tokens(trimmed),
            "saved_tokens": count_messages_tokens(full) - count_messages_tokens(trimmed),
            "saved_percent": round(
                (1 - count_messages_tokens(trimmed) / max(count_messages_tokens(full), 1)) * 100,
                1,
            ),
        }

    def reset(self):
        """重置对话历史"""
        self._messages = []
        self._impl = None
