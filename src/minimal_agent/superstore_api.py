"""
Superstore Sales API — Function Calling 工具函数
=================================================
提供可被 LLM 调用的结构化查询接口，遵循与 mock_enterprise_api.py
相同的返回值约定（{status, data, _sufficiency}）。

数据来源: enterprise_db 的 superstore_orders SQLite 表。
"""

import logging
from typing import Optional

from .enterprise_db import db as enterprise_db
from .superstore_loader import seed_superstore

logger = logging.getLogger(__name__)


def _ensure_data():
    """确保 Superstore 数据已加载，未加载则自动导入"""
    if not enterprise_db.is_superstore_loaded():
        logger.info("[Superstore] 数据未加载，自动导入 ...")
        ok = seed_superstore(enterprise_db)
        if not ok:
            logger.warning("[Superstore] 导入失败，API 可能返回空结果")


# ============================================================
# API 查询函数
# ============================================================

def superstore_overview() -> dict:
    """Superstore 数据集概览：总订单数、日期范围、产品类别、地区"""
    _ensure_data()
    stats = enterprise_db.get_superstore_stats()
    profit = enterprise_db.query_superstore_profitability()
    return {
        "status": "success",
        "data": {**stats, **profit},
        "_sufficiency": "full",
    }


def superstore_by_category() -> dict:
    """按产品大类汇总销售额、利润、利润率"""
    _ensure_data()
    rows = enterprise_db.query_superstore_by_category()
    if rows:
        return {"status": "success", "data": {"categories": rows}, "_sufficiency": "full"}
    return {"status": "success", "data": {"note": "暂无数据"}, "_sufficiency": "empty"}


def superstore_by_subcategory(category: str = "") -> dict:
    """按子类别汇总，可指定大类筛选"""
    _ensure_data()
    cat = category.strip() or None
    rows = enterprise_db.query_superstore_subcategory(cat)
    if rows:
        return {"status": "success", "data": {"subcategories": rows}, "_sufficiency": "full"}
    return {"status": "success", "data": {"note": "暂无数据"}, "_sufficiency": "empty"}


def superstore_by_region() -> dict:
    """按地区汇总销售额、利润、客户数"""
    _ensure_data()
    rows = enterprise_db.query_superstore_by_region()
    if rows:
        return {"status": "success", "data": {"regions": rows}, "_sufficiency": "full"}
    return {"status": "success", "data": {"note": "暂无数据"}, "_sufficiency": "empty"}


def superstore_by_segment() -> dict:
    """按客户群（Consumer/Corporate/Home Office）汇总"""
    _ensure_data()
    rows = enterprise_db.query_superstore_by_segment()
    if rows:
        return {"status": "success", "data": {"segments": rows}, "_sufficiency": "full"}
    return {"status": "success", "data": {"note": "暂无数据"}, "_sufficiency": "empty"}


def superstore_monthly_trend() -> dict:
    """按月销售趋势（折线图用）"""
    _ensure_data()
    rows = enterprise_db.query_superstore_monthly_trend()
    if rows:
        return {"status": "success", "data": {"trends": rows}, "_sufficiency": "full"}
    return {"status": "success", "data": {"note": "暂无数据"}, "_sufficiency": "empty"}


def superstore_top_products(n: int = 10) -> dict:
    """销售额最高的 Top N 产品"""
    _ensure_data()
    rows = enterprise_db.query_superstore_top_products(n)
    if rows:
        return {"status": "success", "data": {"top_products": rows, "limit": n}, "_sufficiency": "full"}
    return {"status": "success", "data": {"note": "暂无数据"}, "_sufficiency": "empty"}


def superstore_loss_products(n: int = 10) -> dict:
    """亏损最严重的产品排名"""
    _ensure_data()
    rows = enterprise_db.query_superstore_loss_products(n)
    if rows:
        return {"status": "success", "data": {"loss_products": rows, "limit": n}, "_sufficiency": "full"}
    return {"status": "success", "data": {"note": "暂无亏损产品"}, "_sufficiency": "empty"}


def superstore_by_state() -> dict:
    """按州的地理销售分布"""
    _ensure_data()
    rows = enterprise_db.query_superstore_by_state()
    if rows:
        return {"status": "success", "data": {"states": rows}, "_sufficiency": "full"}
    return {"status": "success", "data": {"note": "暂无数据"}, "_sufficiency": "empty"}


def superstore_by_ship_mode() -> dict:
    """按配送方式汇总"""
    _ensure_data()
    rows = enterprise_db.query_superstore_ship_mode()
    if rows:
        return {"status": "success", "data": {"ship_modes": rows}, "_sufficiency": "full"}
    return {"status": "success", "data": {"note": "暂无数据"}, "_sufficiency": "empty"}


def superstore_by_year() -> dict:
    """按年度汇总"""
    _ensure_data()
    rows = enterprise_db.query_superstore_by_year()
    if rows:
        return {"status": "success", "data": {"years": rows}, "_sufficiency": "full"}
    return {"status": "success", "data": {"note": "暂无数据"}, "_sufficiency": "empty"}


# ============================================================
# Function Calling Tool Definitions
# ============================================================
SUPERSTORE_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "superstore_overview",
            "description": "获取 Superstore 数据集整体概览：总销售额、总利润、平均利润率、订单总数、客户数、日期范围、产品类别、地区",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "superstore_by_category",
            "description": "按产品大类（Furniture / Office Supplies / Technology）汇总销售额、利润、利润率、销量",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "superstore_by_subcategory",
            "description": "按子类别汇总销售数据，可筛选指定大类。例如: Furniture 下的 Bookcases / Chairs / Tables",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "产品大类（可选）：Furniture / Office Supplies / Technology，留空则返回全部",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "superstore_by_region",
            "description": "按地区（East / West / Central / South）汇总销售额、利润、利润率、客户数",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "superstore_by_segment",
            "description": "按客户群（Consumer / Corporate / Home Office）汇总销售额、利润、订单数",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "superstore_monthly_trend",
            "description": "按月销售趋势（适用于折线图）：每月总销售额、总利润、总销量",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "superstore_top_products",
            "description": "销售额最高的 Top N 产品明细",
            "parameters": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer", "description": "返回条数，默认 10"}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "superstore_loss_products",
            "description": "亏损最严重的产品排名，帮助识别亏损项",
            "parameters": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer", "description": "返回条数，默认 10"}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "superstore_by_state",
            "description": "按州的地理销售分布，展示各州的销售额、利润、利润率",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "superstore_by_ship_mode",
            "description": "按配送方式（Standard Class / Second Class / First Class / Same Day）汇总订单数和销售额",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "superstore_by_year",
            "description": "按年度汇总销售额、利润、销量",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

# 函数名 → 实际函数的映射
SUPERSTORE_FUNCTION_MAP = {
    "superstore_overview": superstore_overview,
    "superstore_by_category": superstore_by_category,
    "superstore_by_subcategory": superstore_by_subcategory,
    "superstore_by_region": superstore_by_region,
    "superstore_by_segment": superstore_by_segment,
    "superstore_monthly_trend": superstore_monthly_trend,
    "superstore_top_products": superstore_top_products,
    "superstore_loss_products": superstore_loss_products,
    "superstore_by_state": superstore_by_state,
    "superstore_by_ship_mode": superstore_by_ship_mode,
    "superstore_by_year": superstore_by_year,
}
