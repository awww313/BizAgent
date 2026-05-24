"""
第二阶段测试脚本：三层 Prompt + JSON 强格式约束
===============================================
测试用例：
  1. 销售分析 — 标准 JSON 输出
  2. 生成简报 — 任务级 Prompt 约束
  3. 未知话题 — 默认兜底
  4. 无数据场景 — 数据真实性约束
"""

import os
import json

from dotenv import load_dotenv

from minimal_agent import BizAgent

load_dotenv()

API_KEY = os.getenv("DEEPSEEK_API_KEY")
BASE_URL = os.getenv("DEEPSEEK_BASE_URL")


def print_result(title: str, result: dict):
    """格式化打印测试结果"""
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")
    print(f"  status : {result.get('status', 'N/A')}")

    if result.get("status") == "error":
        print(f"  error  : {result.get('error', '未知错误')}")
    else:
        data = result.get("data", {})
        print(f"  data   : {json.dumps(data, ensure_ascii=False, indent=4)}")

    print(f"{'=' * 60}\n")


def main():
    agent = BizAgent(
        api_key=API_KEY,
        base_url=BASE_URL,
        model="deepseek-chat",
        max_retries=2,
    )

    # ============================================================
    # Test 1: 销售分析 — 指定 task_type
    # ============================================================
    agent.task_type = "销售分析"
    res1 = agent.chat("请分析 2025 年第四季度的销售数据，总营收 580 万，同比增长 12%")
    print_result("Test 1: 销售分析", res1)

    # ============================================================
    # Test 2: 生成简报 — 任务级 Prompt 约束现状/问题/对策
    # ============================================================
    agent.task_type = "简报"
    res2 = agent.chat(
        "2025 年公司业务发展良好，但在市场竞争加剧的背景下，"
        "需要梳理当前的工作简报"
    )
    print_result("Test 2: 商务简报（三要素约束）", res2)

    # ============================================================
    # Test 3: 默认场景 — 未归类话题用兜底模板
    # ============================================================
    agent.task_type = "default"
    res3 = agent.chat("请介绍一下商务智能 BI 系统的核心功能")
    print_result("Test 3: 默认兜底模板", res3)

    # ============================================================
    # Test 4: chat_stream 自动推断任务类型
    # ============================================================
    res4 = agent.chat_stream("分析上个月电商平台的销售趋势和用户转化率")
    print_result("Test 4: chat_stream 自动推断", res4)

    # ============================================================
    # Test 5: 无数据场景 — 约束模型不编造数据
    # ============================================================
    agent.task_type = "default"
    res5 = agent.chat("我公司2025年的研发投入是多少？我还没有提供数据给你")
    print_result("Test 5: 无数据场景（不应编造）", res5)

    # ============================================================
    # 总结
    # ============================================================
    tests = [res1, res2, res3, res4, res5]
    success = sum(1 for r in tests if r.get("status") == "success")
    print(f"\n{'#' * 50}")
    print(f"  第二阶段测试完成：{success}/{len(tests)} 通过")
    print(f"{'#' * 50}\n")


if __name__ == "__main__":
    main()
