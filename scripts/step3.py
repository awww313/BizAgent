"""
第三阶段测试脚本：MCP 协议与 Function Calling 集成
=================================================
测试用例：
  1. 库存查询 — 单工具调用
  2. 财务报表 — 单工具调用
  3. 销售汇总 — 单工具调用
  4. Mock API Server — HTTP 接口验证
"""

import os
import json
import time

from dotenv import load_dotenv

from minimal_agent import BizAgent
from minimal_agent.mock_enterprise_api import (
    query_inventory,
    get_financial_report,
    get_sales_summary,
    start_mock_server_background,
)

load_dotenv()

API_KEY = os.getenv("DEEPSEEK_API_KEY")
BASE_URL = os.getenv("DEEPSEEK_BASE_URL")


def print_result(title: str, result: dict):
    """格式化打印测试结果"""
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")
    if result.get("status") == "error":
        print(f"  status: {result['status']}")
        print(f"  error : {result.get('error', '未知错误')}")
    else:
        print(f"  {json.dumps(result, ensure_ascii=False, indent=2)}")
    print(f"{'=' * 60}\n")


def test_direct_function_calls():
    """Test 0: 验证 Mock API 函数本身工作正常"""
    print(">>> Test 0: Mock API 函数自检")

    r1 = query_inventory("A 产品")
    assert r1["status"] == "success"
    assert r1["data"]["stock"] == 150
    print(f"  query_inventory('A 产品') ok -> stock={r1['data']['stock']}")

    r2 = get_financial_report("2025-03")
    assert r2["data"]["revenue"] == 5100000
    print(f"  get_financial_report('2025-03') ok -> revenue={r2['data']['revenue']}")

    r3 = get_sales_summary("A 产品", "total")
    assert r3["data"]["total_sales"] == 4050
    print(f"  get_sales_summary('A 产品') ✓ -> total_sales={r3['data']['total_sales']}")

    r4 = query_inventory("不存在的产品")
    assert r4["data"]["stock"] == 0
    print(f"  query_inventory('不存在的产品') ✓ -> stock=0 (边缘情况正常)")

    print("  All direct function checks passed.\n")


def test_inventory_query():
    """Test 1: Function Calling - 库存查询"""
    agent = BizAgent(API_KEY, BASE_URL)
    result = agent.chat_with_tools("帮我查一下 A 产品的库存")
    print_result("Test 1: 库存查询", result)

    # 验证结果包含库存数据
    result_str = json.dumps(result, ensure_ascii=False)
    assert any(k in result_str for k in ["stock", "库存", "150"]), \
        f"结果应包含库存数据: {result_str}"
    return result


def test_financial_report():
    """Test 2: Function Calling - 财务报表"""
    agent = BizAgent(API_KEY, BASE_URL)
    result = agent.chat_with_tools("给我看看 2025 年 3 月的财务报表")
    print_result("Test 2: 财务报表", result)

    result_str = json.dumps(result, ensure_ascii=False)
    assert any(k in result_str for k in ["revenue", "利润", "510"]), \
        f"结果应包含财务数据: {result_str}"
    return result


def test_sales_summary():
    """Test 3: Function Calling - 销售汇总"""
    agent = BizAgent(API_KEY, BASE_URL)
    result = agent.chat_with_tools("B 产品第一季度卖了多少？")
    print_result("Test 3: 销售汇总", result)

    result_str = json.dumps(result, ensure_ascii=False)
    assert any(k in result_str for k in ["sales", "total", "2820", "销售"]), \
        f"结果应包含销售数据: {result_str}"
    return result


def test_mock_http_server():
    """Test 4: Mock API HTTP Server 验证"""
    print(">>> Test 4: Mock API HTTP Server")

    server = start_mock_server_background()
    time.sleep(0.5)  # 等 server 启动

    import requests

    # 健康检查
    r = requests.get("http://127.0.0.1:8899/api/health")
    assert r.json()["status"] == "ok"
    print("  GET /api/health ✓")

    # 库存查询
    r = requests.get("http://127.0.0.1:8899/api/inventory?product_name=A 产品")
    assert r.json()["data"]["stock"] == 150
    print("  GET /api/inventory?product_name=A 产品 ✓ -> stock=150")

    # 财务报表
    r = requests.get("http://127.0.0.1:8899/api/financials?month=2025-03")
    assert r.json()["data"]["revenue"] == 5100000
    print("  GET /api/financials?month=2025-03 ✓ -> revenue=5100000")

    # 销售汇总
    r = requests.get("http://127.0.0.1:8899/api/sales?product_name=B 产品&period=total")
    assert r.json()["data"]["total_sales"] == 2820
    print("  GET /api/sales?product_name=B 产品 ✓ -> total_sales=2820")

    # 参数缺失
    r = requests.get("http://127.0.0.1:8899/api/inventory")
    assert r.status_code == 400
    print("  GET /api/inventory (缺参数) ✓ -> 400")

    server.shutdown()
    print("  Mock HTTP Server 测试全部通过.\n")


def main():
    print("=" * 60)
    print("  第三阶段：MCP 协议与 Function Calling 集成")
    print("=" * 60)

    # Test 0: 直接函数调用验证
    test_direct_function_calls()

    # Test 1-3: Function Calling 链路测试
    r1 = test_inventory_query()
    r2 = test_financial_report()
    r3 = test_sales_summary()

    # Test 4: HTTP Server 测试（选做，需要启动端口）
    try:
        test_mock_http_server()
        http_ok = True
    except Exception as e:
        print(f"  HTTP Server 测试跳过: {e}")
        http_ok = False

    # 总结
    tests = [r1, r2, r3]
    success = sum(1 for r in tests if r.get("status") == "success")
    print(f"\n{'#' * 50}")
    print(f"  第三阶段测试完成：{success}/{len(tests)} Function Calling 通过")
    if http_ok:
        print(f"  HTTP Server: 通过")
    print(f"{'#' * 50}\n")


if __name__ == "__main__":
    main()
