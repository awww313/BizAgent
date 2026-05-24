"""
第四阶段测试脚本：上下文滑动窗口管理
===================================
对比三种策略的 Token 消耗：
  none    — 无裁剪（基准）
  fixed   — 固定轮数裁剪（保留最近 N 轮）
  summary — 摘要法（旧轮浓缩+最近轮）

多轮对话流程模拟：商务场景 8 轮连续对话
"""

import os
import json

from dotenv import load_dotenv

from minimal_agent import BizAgent
from minimal_agent.context_manager import ContextManager, count_tokens, count_messages_tokens

load_dotenv()

API_KEY = os.getenv("DEEPSEEK_API_KEY")
BASE_URL = os.getenv("DEEPSEEK_BASE_URL")


# ============================================================
# 模拟商务连续对话（6 轮）
# ============================================================
CONVERSATION_TURNS = [
    "请分析 2025 年第一季度的整体销售情况",
    "其中哪个产品线增长最快？原因是什么？",
    "对比去年同期的数据怎么样？",
    "目前库存周转天数是多少，有没有缺货风险？",
    "针对库存问题，你建议采取什么改进措施？",
    "如果我们要把库存周转率提升 20%，需要多少资金投入？",
]


def run_with_strategy(strategy: str, **kwargs) -> dict:
    """用指定策略跑完整对话，返回统计结果"""
    agent = BizAgent(API_KEY, BASE_URL)
    agent.reset_conversation()

    summary = f"策略: {strategy}"
    if kwargs:
        summary += f" ({', '.join(f'{k}={v}' for k, v in kwargs.items())})"

    print(f"\n{'=' * 60}")
    print(f"  {summary}")
    print(f"{'=' * 60}")

    results = []
    for i, user_input in enumerate(CONVERSATION_TURNS):
        result = agent.chat_multi_turn(
            user_input,
            context_strategy=strategy,
            **kwargs,
        )
        results.append(result)

        status = "OK" if result.get("status") == "success" else "ERR"
        usage = result.get("usage", {})
        stats = result.get("stats", {})
        tokens = usage.get("total_tokens", 0)
        ctx_msgs = stats.get("context_messages", "?")
        print(f"  [{i + 1}/{len(CONVERSATION_TURNS)}] {status} | "
              f"token={tokens} | ctx_msgs={ctx_msgs}")

        if result.get("status") == "error":
            print(f"       ERROR: {result.get('error')}")
            break

    # 最终统计
    final_stats = results[-1].get("stats", {}) if results else {}
    total_usage = sum(
        r.get("usage", {}).get("total_tokens", 0) for r in results
    )

    print(f"\n  --- 汇总 ---")
    print(f"  总对话轮数: {len(results)}")
    print(f"  总 Token 消耗: {total_usage}")
    print(f"  最终消息数: {final_stats.get('context_messages', '?')}/{final_stats.get('total_messages', '?')}")
    print(f"  节省: {final_stats.get('saved_tokens', 0)} tokens ({final_stats.get('saved_percent', 0)}%)")

    return {
        "strategy": strategy,
        "turns": len(results),
        "total_tokens": total_usage,
        "saved_tokens": final_stats.get("saved_tokens", 0),
        "saved_percent": final_stats.get("saved_percent", 0),
        "final_context_msgs": final_stats.get("context_messages", 0),
        "final_total_msgs": final_stats.get("total_messages", 0),
    }


def run_summary_strategy() -> dict:
    """单独跑摘要策略（需要额外传 api_key/base_url）"""
    agent = BizAgent(API_KEY, BASE_URL)
    agent.reset_conversation()

    print(f"\n{'=' * 60}")
    print(f"  Summarization(recent_turns=2, threshold=4)")
    print(f"{'=' * 60}")

    results = []
    for i, user_input in enumerate(CONVERSATION_TURNS):
        result = agent.chat_multi_turn(
            user_input,
            context_strategy="summary",
            system_prompt=None,  # 使用默认
            recent_turns=2,
            summary_threshold=4,
        )
        results.append(result)

        status = "OK" if result.get("status") == "success" else "ERR"
        usage = result.get("usage", {})
        stats = result.get("stats", {})
        tokens = usage.get("total_tokens", 0)
        ctx_msgs = stats.get("context_messages", "?")
        print(f"  [{i + 1}/{len(CONVERSATION_TURNS)}] {status} | "
              f"token={tokens} | ctx_msgs={ctx_msgs}")

        if result.get("status") == "error":
            print(f"       ERROR: {result.get('error')}")
            break

    final_stats = results[-1].get("stats", {}) if results else {}
    total_usage = sum(
        r.get("usage", {}).get("total_tokens", 0) for r in results
    )

    print(f"\n  --- 汇总 ---")
    print(f"  总对话轮数: {len(results)}")
    print(f"  总 Token 消耗: {total_usage}")
    print(f"  最终消息数: {final_stats.get('context_messages', '?')}/{final_stats.get('total_messages', '?')}")
    print(f"  节省: {final_stats.get('saved_tokens', 0)} tokens ({final_stats.get('saved_percent', 0)}%)")

    return {
        "strategy": "summary",
        "turns": len(results),
        "total_tokens": total_usage,
        "saved_tokens": final_stats.get("saved_tokens", 0),
        "saved_percent": final_stats.get("saved_percent", 0),
        "final_context_msgs": final_stats.get("context_messages", 0),
        "final_total_msgs": final_stats.get("total_messages", 0),
    }


def test_token_counter():
    """验证 token 计数工具"""
    print(f"\n>>> Token 计数验证")
    text_en = "Hello, this is a test message with about twenty words in it for counting purposes."
    text_cn = "你好，这是一个商务智能助手的测试消息，用于验证中文字符的令牌计数功能。"
    print(f"  英文 ({len(text_en)} chars): {count_tokens(text_en)} tokens")
    print(f"  中文 ({len(text_cn)} chars): {count_tokens(text_cn)} tokens")

    msgs = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "分析一下销售数据"},
        {"role": "assistant", "content": '{"status": "success", "data": {"answer": "好的"}}'},
    ]
    print(f"  消息列表: {count_messages_tokens(msgs)} tokens")
    print(f"  Token 计数工具正常\n")


def main():
    print("=" * 60)
    print("  第四阶段：上下文滑动窗口管理")
    print("  目标：降低 30%-40% 的 Token 成本")
    print("=" * 60)

    # Test 0: Token 计数验证
    test_token_counter()

    # Test 1: 无裁剪（基准线）
    r_none = run_with_strategy("none")

    # Test 2: 固定轮数裁剪
    r_fixed = run_with_strategy("fixed", max_turns=4)

    # Test 3: 摘要法
    r_summary = run_summary_strategy()

    # ============================================================
    # 对比总结
    # ============================================================
    print(f"\n{'#' * 60}")
    print(f"  策略对比总结")
    print(f"{'#' * 60}")
    print(f"  {'策略':<20} {'Token 消耗':<14} {'节省比例':<10}")
    print(f"  {'-' * 44}")

    baseline = r_none["total_tokens"]
    for r in [r_none, r_fixed, r_summary]:
        if r is r_none:
            saving = "基准线"
        else:
            pct = (1 - r["total_tokens"] / max(baseline, 1)) * 100
            saving = f"{pct:.1f}%"
        print(f"  {r['strategy']:<20} {r['total_tokens']:<14} {saving:<10}")

    print(f"\n  {'=' * 44}")
    print(f"  固定轮数裁剪: {r_fixed.get('total_tokens', 0)} tokens "
          f"({(1-r_fixed['total_tokens']/max(baseline,1))*100:.0f}% 基准线)")
    print(f"  摘要法:       {r_summary.get('total_tokens', 0)} tokens "
          f"({(1-r_summary['total_tokens']/max(baseline,1))*100:.0f}% 基准线)")
    print(f"{'#' * 60}\n")


if __name__ == "__main__":
    main()
