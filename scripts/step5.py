"""
第五阶段主入口：Benchmark 评测体系搭建
======================================
运行方式：
  python step5.py              — 全量 50+ 用例评测
  python step5.py --quick      — 快速模式（各分类取前 3 条）
  python step5.py --category 销售分析 — 指定分类评测
"""

import os
import sys

from dotenv import load_dotenv

from minimal_agent import BizAgent
from benchmark import BenchmarkRunner, print_report, compare_reports
from benchmark.dataset import (
    DATASET,
    get_categories,
    get_cases_by_category,
)

load_dotenv()

API_KEY = os.getenv("DEEPSEEK_API_KEY")
BASE_URL = os.getenv("DEEPSEEK_BASE_URL")


def run_full_benchmark():
    """全量评测"""
    agent = BizAgent(API_KEY, BASE_URL)
    runner = BenchmarkRunner(agent, name="BizAgent v1 (DeepSeek + 三层Prompt)")

    print("=" * 60)
    print("  第五阶段：Benchmark 评测体系搭建")
    print(f"  数据集: {len(DATASET)} 个用例")
    for cat, count in get_categories().items():
        print(f"    {cat}: {count} 个")
    print("=" * 60)

    report = runner.run_all()

    print_report(report, detailed=True)

    return report


def run_quick_benchmark():
    """快速模式：每类取前 3 个"""
    cases = []
    for cat in get_categories():
        cases.extend(get_cases_by_category(cat)[:3])

    agent = BizAgent(API_KEY, BASE_URL)
    runner = BenchmarkRunner(agent, name="BizAgent v1 (Quick)")

    print("=" * 60)
    print("  快速模式: {} 个用例".format(len(cases)))
    print("=" * 60)

    report = runner.run_all(cases)
    print_report(report)

    return report


def run_category_benchmark(category: str):
    """按分类评测"""
    cases = get_cases_by_category(category)
    if not cases:
        print(f"未找到分类: {category}")
        print(f"可选分类: {list(get_categories().keys())}")
        return None

    agent = BizAgent(API_KEY, BASE_URL)
    runner = BenchmarkRunner(agent, name=f"BizAgent v1 ({category})")

    print("=" * 60)
    print(f"  分类评测: {category} ({len(cases)} 个用例)")
    print("=" * 60)

    report = runner.run_all(cases)
    print_report(report)

    return report


if __name__ == "__main__":
    # 解析命令行参数
    args = sys.argv[1:]

    if "--quick" in args:
        run_quick_benchmark()
    elif "--category" in args:
        idx = args.index("--category")
        cat = args[idx + 1] if idx + 1 < len(args) else ""
        run_category_benchmark(cat)
    else:
        report = run_full_benchmark()

        # 保存报告
        import json
        report_path = "benchmark_report.json"
        # 去掉 results 中的详细数据（太长）
        summary = {k: v for k, v in report.items() if k != "results"}
        summary["category_stats"] = {
            k: v for k, v in report["category_stats"].items()
        }
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print(f"\n报告已保存: {report_path}")
