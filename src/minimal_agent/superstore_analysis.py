"""
Superstore 数据分析算子
========================
针对 Superstore Sales 数据集的分析函数，遵循与 analysis_ops.py
相似的接口约定。

所有函数接受预查询的 API 结果字典作为输入：
  {
    "superstore_by_category": {...},
    "superstore_by_region": {...},
    ...
  }
"""

import logging
from typing import Any, Optional

from .analysis_ops import proportion, growth_rate, trend_direction

logger = logging.getLogger(__name__)


def ss_category_breakdown(api_results: dict) -> dict:
    """
    按产品类别的销售与利润构成分析。

    输入: superstore_by_category 的返回
    输出: 各品类销售额/利润占比、利润率排序
    """
    data = api_results.get("superstore_by_category", {})
    categories = data.get("data", {}).get("categories", [])
    if not categories:
        return {"note": "无类别数据"}

    total_sales = sum(c["total_sales"] for c in categories)
    total_profit = sum(c["total_profit"] for c in categories)

    for c in categories:
        c["sales_share"] = round(proportion(c["total_sales"], total_sales), 1)
        c["profit_share"] = round(proportion(c["total_profit"], total_profit), 1)

    best_margin = max(categories, key=lambda x: x["avg_margin_pct"])
    worst_margin = min(categories, key=lambda x: x["avg_margin_pct"])

    return {
        "categories": categories,
        "total_sales": round(total_sales, 2),
        "total_profit": round(total_profit, 2),
        "highest_margin_category": best_margin["category"],
        "highest_margin_pct": best_margin["avg_margin_pct"],
        "lowest_margin_category": worst_margin["category"],
        "lowest_margin_pct": worst_margin["avg_margin_pct"],
        "trend": trend_direction([c["total_sales"] for c in categories]),
    }


def ss_region_performance(api_results: dict) -> dict:
    """
    地区销售业绩对比。

    输入: superstore_by_region 的返回
    输出: 各地区的销售额排名、利润率、客户活跃度
    """
    data = api_results.get("superstore_by_region", {})
    regions = data.get("data", {}).get("regions", [])
    if not regions:
        return {"note": "无地区数据"}

    total = sum(r["total_sales"] for r in regions)
    best = max(regions, key=lambda x: x["total_sales"])
    worst = min(regions, key=lambda x: x["total_sales"]) if len(regions) > 1 else None

    result = {
        "regions": regions,
        "total_sales_all": round(total, 2),
        "top_region": best["region"],
        "top_region_sales": best["total_sales"],
        "top_region_share": round(proportion(best["total_sales"], total), 1),
    }
    if worst:
        result["bottom_region"] = worst["region"]
        result["bottom_region_sales"] = worst["total_sales"]
        result["bottom_region_share"] = round(proportion(worst["total_sales"], total), 1)

    # 利润最佳的 region
    best_profit = max(regions, key=lambda x: x["total_profit"])
    result["most_profitable_region"] = best_profit["region"]
    result["most_profitable_region_profit"] = best_profit["total_profit"]

    return result


def ss_segment_analysis(api_results: dict) -> dict:
    """
    客户群（Segment）分析。

    输入: superstore_by_segment 的返回
    输出: Consumer / Corporate / Home Office 的消费力和利润贡献
    """
    data = api_results.get("superstore_by_segment", {})
    segments = data.get("data", {}).get("segments", [])
    if not segments:
        return {"note": "无客户群数据"}

    total_sales = sum(s["total_sales"] for s in segments)
    for s in segments:
        s["sales_share"] = round(proportion(s["total_sales"], total_sales), 1)
        s["avg_sales_per_customer"] = round(s["total_sales"] / s["customer_count"], 2) if s["customer_count"] else 0

    best = max(segments, key=lambda x: x["total_sales"])
    return {
        "segments": segments,
        "total_sales": round(total_sales, 2),
        "largest_segment": best["segment"],
        "largest_segment_sales": best["total_sales"],
        "largest_segment_share": round(proportion(best["total_sales"], total_sales), 1),
    }


def ss_monthly_trend_analysis(api_results: dict) -> dict:
    """
    月度/年度趋势分析。

    输入: superstore_monthly_trend + superstore_by_year 的返回
    输出: 趋势方向、峰值月份、增长/下降幅度
    """
    monthly_data = api_results.get("superstore_monthly_trend", {})
    trends = monthly_data.get("data", {}).get("trends", [])
    if not trends:
        return {"note": "无时间序列数据"}

    sales_values = [t["total_sales"] for t in trends]
    profit_values = [t["total_profit"] for t in trends]
    months = [t["month"] for t in trends]

    # 计算环比
    mom_changes = []
    for i in range(1, len(trends)):
        prev_sales = trends[i - 1]["total_sales"]
        curr_sales = trends[i]["total_sales"]
        if prev_sales > 0:
            change = round((curr_sales - prev_sales) / prev_sales * 100, 2)
        else:
            change = 0
        mom_changes.append({
            "month": months[i],
            "previous_month": months[i - 1],
            "sales_change_pct": change,
        })

    best_idx = sales_values.index(max(sales_values))
    worst_idx = sales_values.index(min(sales_values))

    return {
        "months": months,
        "monthly_sales": sales_values,
        "monthly_profit": profit_values,
        "best_month": months[best_idx],
        "best_month_sales": sales_values[best_idx],
        "worst_month": months[worst_idx],
        "worst_month_sales": sales_values[worst_idx],
        "sales_trend": trend_direction(sales_values),
        "profit_trend": trend_direction(profit_values),
        "total_sales_all": round(sum(sales_values), 2),
        "total_profit_all": round(sum(profit_values), 2),
        "avg_monthly_sales": round(sum(sales_values) / len(sales_values), 2),
        "mom_changes": mom_changes,
    }


def ss_profitability_analysis(api_results: dict) -> dict:
    """
    综合盈利能力分析。

    输入: superstore_overview + superstore_by_category 的返回
    输出: 整体利润率、各类别利润率对比、亏损产品分析
    """
    overview = api_results.get("superstore_overview", {})
    ov_data = overview.get("data", {})

    result = {
        "total_sales": ov_data.get("total_sales", 0),
        "total_profit": ov_data.get("total_profit", 0),
        "avg_margin_pct": ov_data.get("avg_margin_pct", 0),
        "avg_discount": ov_data.get("avg_discount", 0),
        "total_orders": ov_data.get("total_orders", 0),
        "unique_customers": ov_data.get("unique_customers", 0),
    }

    # 补充类别利润率排名
    cat_data = api_results.get("superstore_by_category", {})
    categories = cat_data.get("data", {}).get("categories", [])
    if categories:
        sorted_cats = sorted(categories, key=lambda x: x["avg_margin_pct"], reverse=True)
        result["category_margin_ranking"] = [
            {"category": c["category"], "margin_pct": c["avg_margin_pct"]}
            for c in sorted_cats
        ]

    # 亏损产品信息（如有）
    loss_data = api_results.get("superstore_loss_products", {})
    loss_products = loss_data.get("data", {}).get("loss_products", [])
    if loss_products:
        result["loss_product_count"] = len(loss_products)
        result["worst_loss"] = {
            "product": loss_products[0]["product_name"],
            "loss": round(loss_products[0]["total_profit"], 2),
        }

    return result


def ss_top_products_analysis(api_results: dict) -> dict:
    """
    Top 产品销售分析。

    输入: superstore_top_products 的返回
    输出: 畅销品特征分布
    """
    data = api_results.get("superstore_top_products", {})
    products = data.get("data", {}).get("top_products", [])
    if not products:
        return {"note": "无产品数据"}

    # 统计品类分布
    category_count = {}
    for p in products:
        cat = p["category"]
        category_count[cat] = category_count.get(cat, 0) + 1

    category_distribution = [
        {"category": cat, "count": cnt, "share": f"{round(cnt / len(products) * 100, 1)}%"}
        for cat, cnt in sorted(category_count.items(), key=lambda x: x[1], reverse=True)
    ]

    total_sales = sum(p["total_sales"] for p in products)
    total_profit = sum(p["total_profit"] for p in products)

    return {
        "top_products": products,
        "category_distribution": category_distribution,
        "total_sales_top": round(total_sales, 2),
        "total_profit_top": round(total_profit, 2),
        "avg_sales_per_product": round(total_sales / len(products), 2),
        "avg_profit_per_product": round(total_profit / len(products), 2),
    }


# ============================================================
# 组合分析入口（给 BizAgent 调用）
# ============================================================

def auto_analyze_superstore(api_results: dict) -> dict:
    """
    根据 API 返回结果自动选择并执行 Superstore 分析算子。

    Args:
        api_results: {"superstore_by_category": {...}, "superstore_by_region": {...}, ...}

    Returns:
        {"ss_category_breakdown": {...}, "ss_region_performance": {...}, ...}
    """
    analysis = {}

    names = {
        "ss_category_breakdown": ss_category_breakdown,
        "ss_region_performance": ss_region_performance,
        "ss_segment_analysis": ss_segment_analysis,
        "ss_monthly_trend_analysis": ss_monthly_trend_analysis,
        "ss_profitability_analysis": ss_profitability_analysis,
        "ss_top_products_analysis": ss_top_products_analysis,
    }

    for name, func in names.items():
        # 找出该分析需要的输入 key（从函数签名推断）
        try:
            result = func(api_results)
            if result and "note" not in result:
                analysis[name] = result
        except Exception as e:
            logger.warning("[SuperstoreAnalysis] %s 失败: %s", name, e)

    return analysis
