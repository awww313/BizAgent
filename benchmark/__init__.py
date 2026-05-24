"""
第五阶段：自动化评测引擎
========================
功能：
  1. 批量运行测试用例
  2. 记录格式稳定性、准确率、Token 消耗
  3. 生成详细报告（JSON + 表格）
  4. 多版本对照分析
"""

import json
import re
import time
import logging

from .dataset import DATASET

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)


# ============================================================
# 评测指标计算
# ============================================================

def check_valid_json(text: str) -> bool:
    """检查是否为合法 JSON"""
    if not text or not isinstance(text, str):
        return False
    # 尝试从 markdown 代码块提取
    code_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if code_match:
        text = code_match.group(1).strip()
    try:
        json.loads(text)
        return True
    except (json.JSONDecodeError, ValueError):
        return False


def check_keys(data: dict, expected_keys: list) -> dict:
    """检查 dict 是否包含预期的 key（支持嵌套）"""
    results = {}
    if data is None or not isinstance(data, dict):
        return {key: False for key in expected_keys}
    for key in expected_keys:
        if "." in key:
            parts = key.split(".")
            current = data
            found = True
            for part in parts:
                if isinstance(current, dict) and part in current:
                    current = current[part]
                else:
                    found = False
                    break
            results[key] = found
        else:
            results[key] = key in data
    return results


def check_forbidden(text: str, patterns: list) -> dict:
    """检查文本中是否包含禁止模式"""
    results = {}
    for pattern in patterns:
        results[pattern] = pattern in text
    return results


def check_values(data: dict, expected_values: dict) -> dict:
    """检查是否包含预期值"""
    results = {}
    data_str = json.dumps(data, ensure_ascii=False)
    for val, _ in expected_values.items():
        results[val] = val in data_str
    return results


def extract_json(text: str) -> dict:
    """从文本中提取 JSON"""
    if not text:
        return {}
    code_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if code_match:
        text = code_match.group(1).strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return {}


# ============================================================
# 评测运行器
# ============================================================

class BenchmarkRunner:
    """Benchmark 运行器"""

    def __init__(self, agent, name: str = "default"):
        """
        Args:
            agent: BizAgent 实例
            name: 版本名称（用于多版本对比）
        """
        self.agent = agent
        self.name = name

    def run_single(self, case: dict) -> dict:
        """运行单个测试用例"""
        question = case["question"]
        use_tools = case.get("use_tools", False)

        start_time = time.time()

        try:
            self.agent.reset_conversation()
            self.agent.task_type = case.get("task_type", "default")

            if use_tools:
                result = self.agent.chat_with_tools(question)
            else:
                result = self.agent.chat(question)

            elapsed = time.time() - start_time

            # 提取原始 response
            raw_text = json.dumps(result, ensure_ascii=False)

            # JSON 合法性
            is_valid = check_valid_json(raw_text)
            parsed = extract_json(raw_text) if is_valid else result

            # Key 检查
            key_results = check_keys(parsed, case.get("expected_keys", []))
            data_part = parsed.get("data") if isinstance(parsed.get("data"), dict) else parsed
            data_key_results = check_keys(data_part, case.get("data_keys", []))

            # 禁止模式检查
            forbidden_results = check_forbidden(
                raw_text, case.get("forbidden", [])
            )

            # 预期值检查
            value_results = check_values(
                parsed, case.get("expected_values", {})
            )

            # 综合判定：JSON 合法 + 所有必需 key 存在 + 无禁止模式
            all_keys_ok = all(key_results.values()) and all(data_key_results.values())
            no_forbidden = not any(forbidden_results.values())
            values_ok = all(value_results.values())

            passed = is_valid and all_keys_ok and no_forbidden and values_ok

            return {
                "question": question[:60],
                "category": case["category"],
                "passed": passed,
                "is_valid_json": is_valid,
                "key_check": all(key_results.values()),
                "data_key_check": all(data_key_results.values()),
                "no_forbidden": no_forbidden,
                "values_match": values_ok,
                "missing_keys": [k for k, v in key_results.items() if not v]
                              + [k for k, v in data_key_results.items() if not v],
                "forbidden_found": [k for k, v in forbidden_results.items() if v],
                "tokens": result.get("usage", {}).get("total_tokens", 0) if isinstance(result.get("usage"), dict) else 0,
                "elapsed": round(elapsed, 2),
                "error": None,
            }

        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"用例异常: {question[:40]}... -> {e}")
            return {
                "question": question[:60],
                "category": case["category"],
                "passed": False,
                "is_valid_json": False,
                "key_check": False,
                "data_key_check": False,
                "no_forbidden": False,
                "values_match": False,
                "missing_keys": [],
                "forbidden_found": [],
                "tokens": 0,
                "elapsed": round(elapsed, 2),
                "error": str(e),
            }

    def run_all(self, cases: list = None) -> dict:
        """运行全部测试用例"""
        if cases is None:
            cases = DATASET

        print(f"\n>>> Benchmark [{self.name}] 开始评测: {len(cases)} 个用例")

        results = []
        passed = 0
        total_tokens = 0
        total_time = 0.0

        for i, case in enumerate(cases):
            desc = case.get("description", "")[:40]
            print(f"  [{i + 1}/{len(cases)}] {desc}...", end=" ")

            r = self.run_single(case)
            results.append(r)

            if r["passed"]:
                passed += 1
                print(f"PASS ({r['elapsed']}s, {r['tokens']}t)")
            else:
                issues = []
                if not r["is_valid_json"]:
                    issues.append("JSON")
                if not r["key_check"]:
                    issues.append(f"keys({r['missing_keys']})")
                if not r["no_forbidden"]:
                    issues.append(f"forbidden({r['forbidden_found']})")
                if not r["values_match"]:
                    issues.append("values")
                if r["error"]:
                    issues.append(f"ERROR({r['error']})")
                print(f"FAIL [{'|'.join(issues)}] ({r['elapsed']}s, {r['tokens']}t)")

            total_tokens += r["tokens"]
            total_time += r["elapsed"]

        # 分类统计
        category_stats = {}
        for r in results:
            cat = r["category"]
            if cat not in category_stats:
                category_stats[cat] = {"total": 0, "passed": 0, "tokens": 0}
            category_stats[cat]["total"] += 1
            if r["passed"]:
                category_stats[cat]["passed"] += 1
            category_stats[cat]["tokens"] += r["tokens"]

        report = {
            "version": self.name,
            "total_cases": len(cases),
            "passed": passed,
            "failed": len(cases) - passed,
            "pass_rate": round(passed / max(len(cases), 1) * 100, 1),
            "total_tokens": total_tokens,
            "avg_tokens_per_case": round(total_tokens / max(len(cases), 1), 1),
            "total_time": round(total_time, 2),
            "avg_time_per_case": round(total_time / max(len(cases), 1), 2),
            "category_stats": category_stats,
            "results": results,
        }

        return report


def print_report(report: dict, detailed: bool = False):
    """打印评测报告"""
    print(f"\n{'=' * 60}")
    print(f"  Benchmark 报告: {report['version']}")
    print(f"{'=' * 60}")
    print(f"  总用例: {report['total_cases']}")
    print(f"  通过:   {report['passed']}")
    print(f"  失败:   {report['failed']}")
    print(f"  通过率: {report['pass_rate']}%")
    print(f"  总 Token: {report['total_tokens']}")
    print(f"  平均 Token/用例: {report['avg_tokens_per_case']}")
    print(f"  总耗时: {report['total_time']}s")
    print(f"  平均耗时/用例: {report['avg_time_per_case']}s")

    print(f"\n  --- 分类统计 ---")
    print(f"  {'类别':<20} {'通过率':<10} {'Token':<10}")
    print(f"  {'-' * 40}")
    for cat, stats in report["category_stats"].items():
        rate = f"{stats['passed']}/{stats['total']}"
        print(f"  {cat:<20} {rate:<10} {stats['tokens']:<10}")

    if detailed:
        print(f"\n  --- 详细结果 ---")
        for r in report["results"]:
            status = "PASS" if r["passed"] else "FAIL"
            print(f"  [{status}] {r['question'][:50]} ({r['elapsed']}s)")


def compare_reports(*reports: dict) -> dict:
    """多版本对比分析"""
    print(f"\n{'#' * 60}")
    print(f"  多版本对比分析")
    print(f"{'#' * 60}")
    print(f"  {'版本':<20} {'通过率':<10} {'Token/用例':<14} {'耗时/用例':<12}")
    print(f"  {'-' * 56}")

    baseline = reports[0]
    for report in reports:
        pass_rate = f"{report['pass_rate']}%"
        avg_tokens = report['avg_tokens_per_case']
        avg_time = f"{report['avg_time_per_case']}s"

        if report is baseline:
            print(f"  {report['version']:<20} {pass_rate:<10} {avg_tokens:<14} {avg_time:<12} (基准)")
        else:
            token_diff = avg_tokens - baseline['avg_tokens_per_case']
            token_pct = (avg_tokens / max(baseline['avg_tokens_per_case'], 1) - 1) * 100
            token_str = f"{avg_tokens} ({token_pct:+.1f}%)"
            print(f"  {report['version']:<20} {pass_rate:<10} {token_str:<14} {avg_time:<12}")

    print(f"{'#' * 60}")

    return {
        "baseline": reports[0]["version"],
        "comparisons": [
            {
                "version": r["version"],
                "pass_rate": r["pass_rate"],
                "avg_tokens": r["avg_tokens_per_case"],
                "avg_time": r["avg_time_per_case"],
            }
            for r in reports
        ],
    }
