"""
数据分析算子：同比、环比、占比、极值、均值、汇总等统计计算
==========================================================
直接基于业务数据做衍生分析，无需手动运算。

支持的数据源:
  - FINANCIAL_DB: {"2025-01": {"revenue": ..., "cost": ..., "profit": ..., "margin": ...}}
  - SALES_DB:    {"A 产品": {"2025-01": 1200, "2025-02": ..., "total": ...}}
  - INVENTORY_DB: {"A 产品": {"stock": 150, "warehouse": ..., ...}}
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ============================================================
# 基础算子
# ============================================================

def growth_rate(old_value: float, new_value: float) -> float:
    """计算增长率（百分比）"""
    if old_value == 0:
        return 0.0
    return round((new_value - old_value) / abs(old_value) * 100, 2)


def proportion(part: float, total: float) -> float:
    """计算占比（百分比）"""
    if total == 0:
        return 0.0
    return round(part / total * 100, 2)


def change_amount(old_value: float, new_value: float) -> float:
    """计算变化量"""
    return round(new_value - old_value, 2)


# ============================================================
# 财务数据分析
# ============================================================

def financial_mom(data: dict[str, dict], metric: str = "revenue") -> list[dict]:
    """
    财务环比分析：逐月计算指定指标的增长率。

    Args:
        data: FINANCIAL_DB 格式的财务数据
        metric: 指标名 (revenue / cost / profit / margin)

    Returns:
        [{"period", "previous_period", "current_value", "previous_value", "change", "growth_rate"}, ...]
    """
    months = sorted(data.keys())
    # 只保留月份粒度（去掉 Q1 这类汇总数据）
    months = [m for m in months if "-Q" not in m]

    results = []
    for i in range(1, len(months)):
        prev = months[i - 1]
        curr = months[i]
        old_val = data[prev].get(metric, 0)
        new_val = data[curr].get(metric, 0)
        results.append({
            "period": curr,
            "previous_period": prev,
            "current_value": new_val,
            "previous_value": old_val,
            "change": change_amount(old_val, new_val),
            "growth_rate": f"{growth_rate(old_val, new_val)}%",
        })

    return results


def financial_summary(data: dict[str, dict]) -> dict:
    """
    财务汇总统计：总营收、总成本、总利润、平均利润率、极值。

    Returns:
        {"total_revenue", "total_cost", "total_profit", "avg_margin",
         "max_revenue_month", "min_revenue_month", ...}
    """
    months = {k: v for k, v in data.items() if "-Q" not in k}

    revenues = [m["revenue"] for m in months.values()]
    costs = [m["cost"] for m in months.values()]
    profits = [m["profit"] for m in months.values()]
    margins = [m["margin"] for m in months.values()]

    if not revenues:
        return {}

    max_i = revenues.index(max(revenues))
    min_i = revenues.index(min(revenues))
    month_keys = list(months.keys())

    return {
        "total_revenue": sum(revenues),
        "total_cost": sum(costs),
        "total_profit": sum(profits),
        "avg_margin": f"{round(sum(margins) / len(margins) * 100, 1)}%",
        "max_revenue": max(revenues),
        "max_revenue_month": month_keys[max_i],
        "min_revenue": min(revenues),
        "min_revenue_month": month_keys[min_i],
        "avg_revenue": round(sum(revenues) / len(revenues)),
        "avg_cost": round(sum(costs) / len(costs)),
        "avg_profit": round(sum(profits) / len(profits)),
        "months_analyzed": len(months),
    }


# ============================================================
# 销售数据分析
# ============================================================

def sales_product_comparison(data: dict[str, dict]) -> list[dict]:
    """
    产品销量对比：各产品的总销量和占比。

    Returns:
        [{"product", "total_sales", "proportion"}, ...]
    """
    totals = {pid: info.get("total", 0) for pid, info in data.items()}
    grand_total = sum(totals.values())

    results = []
    for product, total in sorted(totals.items(), key=lambda x: x[1], reverse=True):
        results.append({
            "product": product,
            "total_sales": total,
            "proportion": f"{proportion(total, grand_total)}%",
        })

    return results


def sales_monthly_trend(data: dict[str, dict], product: Optional[str] = None) -> list[dict]:
    """
    销售月度趋势：指定产品或全部产品的逐月销量。

    Args:
        data: SALES_DB
        product: 产品名，None 则汇总全部

    Returns:
        [{"month", "sales", "product_count" (if aggregated)}, ...]
    """
    if product:
        product_data = data.get(product)
        if not product_data:
            return []
        months = sorted([k for k in product_data if k != "total"])
        return [
            {"month": m, "sales": product_data[m]} for m in months
        ]

    # 汇总全部产品
    all_months: dict[str, int] = {}
    for pid, info in data.items():
        for k, v in info.items():
            if k != "total":
                all_months[k] = all_months.get(k, 0) + v

    results = []
    for month in sorted(all_months.keys()):
        results.append({
            "month": month,
            "sales": all_months[month],
            "product_count": len(data),
        })
    return results


def sales_extreme(data: dict[str, dict]) -> dict:
    """
    销售极值分析：找出销量最高和最低的产品/月份。

    Returns:
        {"best_selling_product", "worst_selling_product",
         "best_month_total", "product_rank", ...}
    """
    totals = {pid: info.get("total", 0) for pid, info in data.items()}
    if not totals:
        return {}

    best_product = max(totals, key=totals.get)
    worst_product = min(totals, key=totals.get)

    return {
        "best_selling_product": best_product,
        "best_sales": totals[best_product],
        "worst_selling_product": worst_product,
        "worst_sales": totals[worst_product],
        "avg_sales_per_product": round(sum(totals.values()) / len(totals)),
        "product_rank": sorted(totals.items(), key=lambda x: x[1], reverse=True),
    }


# ============================================================
# 库存数据分析
# ============================================================

def inventory_status_analysis(data: dict[str, dict]) -> dict:
    """
    库存状态分析：库存总量、缺货情况、仓库分布。

    Returns:
        {"total_stock", "stock_status", "warehouse_distribution",
         "out_of_stock_products", "low_stock_products", ...}
    """
    total = sum(item["stock"] for item in data.values())

    statuses = {"充足": 0, "紧张": 0, "缺货": 0}
    out_of_stock = []
    low_stock = []

    for pid, info in data.items():
        stock = info["stock"]
        if stock <= 0:
            statuses["缺货"] += 1
            out_of_stock.append(pid)
        elif stock <= 50:
            statuses["紧张"] += 1
            low_stock.append(pid)
        else:
            statuses["充足"] += 1

    # 仓库分布
    warehouse_dist = {}
    for info in data.values():
        wh = info.get("warehouse", "未知")
        warehouse_dist[wh] = warehouse_dist.get(wh, 0) + info["stock"]

    return {
        "total_stock": total,
        "product_count": len(data),
        "stock_status": statuses,
        "warehouse_distribution": warehouse_dist,
        "out_of_stock_products": out_of_stock,
        "low_stock_products": low_stock,
        "avg_stock_per_product": round(total / len(data)) if data else 0,
    }


# ============================================================
# 通用分析
# ============================================================

def trend_direction(values: list[float]) -> str:
    """判断趋势方向"""
    if len(values) < 2:
        return "数据不足"
    increases = sum(1 for i in range(1, len(values)) if values[i] > values[i - 1])
    decreases = sum(1 for i in range(1, len(values)) if values[i] < values[i - 1])

    total = len(values) - 1
    ratio = increases / total

    if ratio >= 0.8:
        return "持续增长"
    elif ratio >= 0.6:
        return "总体增长"
    elif ratio <= 0.2:
        return "持续下降"
    elif ratio <= 0.4:
        return "总体下降"
    else:
        return "波动平稳"


def summarize_metrics(data: dict[str, float]) -> dict:
    """
    通用指标汇总：最大值、最小值、均值、总和。

    Args:
        data: {"label": value, ...}

    Returns:
        {"max": {"label", "value"}, "min": {"label", "value"},
         "avg", "total", "count"}
    """
    if not data:
        return {}

    values = list(data.values())
    labels = list(data.keys())
    max_idx = values.index(max(values))
    min_idx = values.index(min(values))

    return {
        "max": {"label": labels[max_idx], "value": values[max_idx]},
        "min": {"label": labels[min_idx], "value": values[min_idx]},
        "avg": round(sum(values) / len(values), 2),
        "total": round(sum(values), 2),
        "count": len(values),
    }


# ============================================================
# 员工数据分析
# ============================================================

def employee_headcount_by_department(data: dict[str, dict]) -> dict:
    """
    各部门人数统计和薪资概况。

    Returns:
        {"departments": [{"department", "count", "avg_salary", "total_salary"}, ...],
         "total_employees", "avg_salary_all"}
    """
    dept_stats: dict[str, dict] = {}
    for emp in data.values():
        dept = emp["department"]
        if dept not in dept_stats:
            dept_stats[dept] = {"count": 0, "salary_sum": 0}
        dept_stats[dept]["count"] += 1
        dept_stats[dept]["salary_sum"] += emp["salary"]

    departments = []
    for dept, stats in sorted(dept_stats.items()):
        departments.append({
            "department": dept,
            "count": stats["count"],
            "avg_salary": round(stats["salary_sum"] / stats["count"]),
            "total_salary": stats["salary_sum"],
        })

    total = sum(stats["count"] for stats in dept_stats.values())
    all_salaries = [emp["salary"] for emp in data.values()]

    return {
        "departments": departments,
        "total_employees": total,
        "avg_salary_all": round(sum(all_salaries) / len(all_salaries)) if all_salaries else 0,
        "max_salary": max(all_salaries) if all_salaries else 0,
        "min_salary": min(all_salaries) if all_salaries else 0,
    }


def employee_salary_distribution(data: dict[str, dict]) -> list[dict]:
    """
    员工薪资分布区间统计。

    Returns:
        [{"range": "0-10K", "count": N, "names": [...]}, ...]
    """
    brackets = [
        (0, 10000, "10K 以下"),
        (10001, 15000, "10K-15K"),
        (15001, 20000, "15K-20K"),
        (20001, 25000, "20K-25K"),
        (25001, 999999, "25K 以上"),
    ]

    result = []
    for lo, hi, label in brackets:
        matched = [emp for emp in data.values() if lo <= emp["salary"] <= hi]
        if matched:
            result.append({
                "range": label,
                "count": len(matched),
                "names": [m["name"] for m in matched],
            })

    return result


# ============================================================
# 客户数据分析
# ============================================================

def customer_tier_analysis(data: dict[str, dict]) -> dict:
    """
    客户分层分析：各等级客户数及消费额。

    Returns:
        {"tiers": [{"tier", "count", "total_spent", "avg_spent", "proportion"}, ...],
         "total_customers", "total_spent_all"}
    """
    tier_stats: dict[str, dict] = {}
    grand_total = sum(c["total_spent"] for c in data.values())

    for c in data.values():
        tier = c["tier"]
        if tier not in tier_stats:
            tier_stats[tier] = {"count": 0, "spent_sum": 0}
        tier_stats[tier]["count"] += 1
        tier_stats[tier]["spent_sum"] += c["total_spent"]

    tiers = []
    for tier, stats in sorted(tier_stats.items()):
        tiers.append({
            "tier": tier,
            "count": stats["count"],
            "total_spent": stats["spent_sum"],
            "avg_spent": round(stats["spent_sum"] / stats["count"]),
            "proportion": f"{proportion(stats['spent_sum'], grand_total)}%" if grand_total else "0%",
        })

    return {
        "tiers": tiers,
        "total_customers": len(data),
        "total_spent_all": grand_total,
    }


def customer_region_analysis(data: dict[str, dict]) -> dict:
    """
    客户地区分布分析。

    Returns:
        {"regions": [{"region", "count", "total_spent", "avg_spent"}, ...]}
    """
    region_stats: dict[str, dict] = {}
    for c in data.values():
        region = c["region"]
        if region not in region_stats:
            region_stats[region] = {"count": 0, "spent_sum": 0}
        region_stats[region]["count"] += 1
        region_stats[region]["spent_sum"] += c["total_spent"]

    regions = []
    for region, stats in sorted(region_stats.items()):
        regions.append({
            "region": region,
            "count": stats["count"],
            "total_spent": stats["spent_sum"],
            "avg_spent": round(stats["spent_sum"] / stats["count"]) if stats["count"] else 0,
        })

    return {"regions": regions}


# ============================================================
# 组合分析（给 BizAgent 调用）
# ============================================================

def auto_analyze(api_results: dict[str, Any]) -> dict:
    """
    根据 API 返回结果自动选择并执行分析算子。

    Args:
        api_results: {"query_inventory": {...}, "get_financial_report": {...}, "get_sales_summary": {...}}

    Returns:
        {"financial_mom": [...], "financial_summary": {...},
         "sales_comparison": [...], "sales_extreme": {...},
         "inventory_analysis": {...}, ...}
    """
    from .mock_enterprise_api import INVENTORY_DB, FINANCIAL_DB, SALES_DB, EMPLOYEES_DB, CUSTOMERS_DB

    analysis = {}

    # 财务分析
    if FINANCIAL_DB:
        try:
            analysis["financial_mom"] = financial_mom(FINANCIAL_DB)
            analysis["financial_summary"] = financial_summary(FINANCIAL_DB)
        except Exception as e:
            logger.warning("[Analysis] 财务分析失败: %s", e)

    # 销售分析
    if SALES_DB:
        try:
            analysis["sales_comparison"] = sales_product_comparison(SALES_DB)
            analysis["sales_extreme"] = sales_extreme(SALES_DB)
        except Exception as e:
            logger.warning("[Analysis] 销售分析失败: %s", e)

    # 库存分析
    if INVENTORY_DB:
        try:
            analysis["inventory_analysis"] = inventory_status_analysis(INVENTORY_DB)
        except Exception as e:
            logger.warning("[Analysis] 库存分析失败: %s", e)

    # 员工分析
    if EMPLOYEES_DB:
        try:
            analysis["employee_headcount"] = employee_headcount_by_department(EMPLOYEES_DB)
            analysis["employee_salary_distribution"] = employee_salary_distribution(EMPLOYEES_DB)
        except Exception as e:
            logger.warning("[Analysis] 员工分析失败: %s", e)

    # 客户分析
    if CUSTOMERS_DB:
        try:
            analysis["customer_tier_analysis"] = customer_tier_analysis(CUSTOMERS_DB)
            analysis["customer_region_analysis"] = customer_region_analysis(CUSTOMERS_DB)
        except Exception as e:
            logger.warning("[Analysis] 客户分析失败: %s", e)

    return analysis
