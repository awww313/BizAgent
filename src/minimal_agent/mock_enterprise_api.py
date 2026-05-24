"""
第三阶段核心：Mock Enterprise API 与 Function Calling 工具定义
==============================================================
1. 模拟企业内部数据接口（库存/财务/销售）
2. 提供本地 HTTP Server 模式 (start_mock_server)
3. 提供直接调用的 Python 函数模式
4. 导出 OpenAI-compatible Tool Definitions

数据源：SQLite 企业业务库 (enterprise_db)，替代硬编码字典。
"""

import json
import logging
from collections.abc import MutableMapping
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from typing import Any

from .enterprise_db import db as enterprise_db

logger = logging.getLogger(__name__)


# ============================================================
# SQLite 数据源的字典视图 — 兼容旧代码中的 dict 接口
# analysis_ops / biz_agent 通过 INVENTORY_DB / FINANCIAL_DB /
# SALES_DB 读取数据，这些变量现在是实时查询 SQLite 的视图
# ============================================================

class DictView(MutableMapping):
    """SQLite 实时查询的 dict 兼容视图 — 每次访问都从数据库读取最新数据"""

    def __init__(self, load_fn):
        self._load_fn = load_fn

    def _data(self):
        return self._load_fn()

    def __getitem__(self, key):
        return self._data()[key]

    def __setitem__(self, key, value):
        self._data()[key] = value

    def __delitem__(self, key):
        d = self._data()
        del d[key]

    def __iter__(self):
        return iter(self._data())

    def __len__(self):
        return len(self._data())

    def __contains__(self, key):
        return key in self._data()

    def get(self, key, default=None):
        return self._data().get(key, default)

    def keys(self):
        return self._data().keys()

    def values(self):
        return self._data().values()

    def items(self):
        return self._data().items()

    def __repr__(self):
        return repr(self._data())


# ============================================================
# 企业数据库 — 实时从 SQLite 读取数据
# ============================================================
INVENTORY_DB = DictView(enterprise_db.get_all_inventory)
FINANCIAL_DB = DictView(enterprise_db.get_all_financial)
SALES_DB = DictView(enterprise_db.get_all_sales)
EMPLOYEES_DB = DictView(enterprise_db.get_all_employees)
CUSTOMERS_DB = DictView(enterprise_db.get_all_customers)


# ============================================================
# API 查询函数 — 可直接调用的 Python 函数
# ============================================================

def query_inventory(product_name: str) -> dict:
    """查询指定产品的当前库存"""
    result = INVENTORY_DB.get(product_name)
    if result:
        return {"status": "success", "data": result}
    return {"status": "success", "data": {"product": product_name, "stock": 0, "warehouse": "未知", "note": "未找到该产品信息"}}


def get_financial_report(month: str) -> dict:
    """获取指定月份的财务报表"""
    result = FINANCIAL_DB.get(month)
    if result:
        return {"status": "success", "data": result}
    return {"status": "success", "data": {"note": f"暂无 {month} 的财务数据"}}


def get_sales_summary(product_name: str, period: str = "total") -> dict:
    """获取产品销售汇总"""
    product_data = SALES_DB.get(product_name)
    if not product_data:
        return {"status": "success", "data": {"product": product_name, "note": "暂无该产品销售数据"}}

    if period == "total":
        return {"status": "success", "data": {"product": product_name, "period": "total", "total_sales": product_data.get("total", 0)}}
    elif period in product_data:
        return {"status": "success", "data": {"product": product_name, "period": period, "sales": product_data[period]}}
    else:
        return {"status": "success", "data": {"product": product_name, "period": period, "note": f"暂无 {period} 的销售数据"}}


# ============================================================
# 写操作函数 — 动态写入 SQLite 企业数据库
# ============================================================

def add_product(product_name: str, stock: int = 0, warehouse: str = "", unit: str = "件") -> dict:
    """添加新产品到库存"""
    ok = enterprise_db.add_product(product_name, stock, warehouse, unit)
    if ok:
        return {"status": "success", "data": {"product": product_name, "stock": stock, "warehouse": warehouse, "unit": unit}}
    return {"status": "error", "data": {"product": product_name, "note": "添加失败"}}


def adjust_stock(product_name: str, delta: int) -> dict:
    """调整产品库存数量（正数增加，负数减少）"""
    ok = enterprise_db.adjust_stock(product_name, delta)
    if ok:
        info = enterprise_db.get_inventory(product_name)
        return {"status": "success", "data": {"product": product_name, "adjustment": delta, "new_stock": info["stock"] if info else 0}}
    return {"status": "error", "data": {"product": product_name, "note": "调整失败，产品不存在"}}


def record_sale(product_name: str, period: str, amount: int) -> dict:
    """记录一笔销售数据"""
    ok = enterprise_db.record_sale(product_name, period, amount)
    if ok:
        return {"status": "success", "data": {"product": product_name, "period": period, "amount": amount}}
    return {"status": "error", "data": {"note": "记录失败"}}


def add_financial_record(period: str, revenue: int, cost: int, profit: int, margin: float) -> dict:
    """添加一条财务记录"""
    ok = enterprise_db.add_financial_record(period, revenue, cost, profit, margin)
    if ok:
        return {"status": "success", "data": {"period": period, "revenue": revenue, "cost": cost, "profit": profit, "margin": margin}}
    return {"status": "error", "data": {"note": "写入失败"}}


def reset_database() -> dict:
    """重置数据库到初始种子数据"""
    ok = enterprise_db.reset()
    return {"status": "success" if ok else "error", "data": {"note": "已重置为初始数据" if ok else "重置失败"}}


# ============================================================
# 员工查询/操作函数
# ============================================================

def query_employee(name: str) -> dict:
    """查询员工信息"""
    result = EMPLOYEES_DB.get(name)
    if result:
        return {"status": "success", "data": result}
    return {"status": "success", "data": {"note": f"未找到员工 '{name}'"}}


def list_employees_by_department(department: str) -> dict:
    """按部门查询员工列表"""
    rows = enterprise_db.get_employees_by_department(department)
    if rows:
        return {"status": "success", "data": {"department": department, "employees": rows, "count": len(rows)}}
    return {"status": "success", "data": {"department": department, "employees": [], "count": 0}}


def add_employee(name: str, department: str, position: str, salary: int, hire_date: str = "") -> dict:
    """添加新员工"""
    ok = enterprise_db.add_employee(name, department, position, salary, hire_date)
    if ok:
        return {"status": "success", "data": {"name": name, "department": department, "position": position, "salary": salary}}
    return {"status": "error", "data": {"note": "添加员工失败"}}


def update_salary(name: str, new_salary: int) -> dict:
    """调整员工薪资"""
    ok = enterprise_db.update_employee_salary(name, new_salary)
    if ok:
        return {"status": "success", "data": {"name": name, "new_salary": new_salary}}
    return {"status": "error", "data": {"note": f"未找到员工 '{name}'"}}


# ============================================================
# 客户查询/操作函数
# ============================================================

def query_customer(name: str) -> dict:
    """查询客户信息"""
    result = CUSTOMERS_DB.get(name)
    if result:
        return {"status": "success", "data": result}
    return {"status": "success", "data": {"note": f"未找到客户 '{name}'"}}


def list_customers_by_tier(tier: str) -> dict:
    """按等级查询客户列表"""
    rows = enterprise_db.get_customers_by_tier(tier)
    if rows:
        total = sum(r["total_spent"] for r in rows)
        return {"status": "success", "data": {"tier": tier, "customers": rows, "count": len(rows), "total_spent": total}}
    return {"status": "success", "data": {"tier": tier, "customers": [], "count": 0}}


def list_customers_by_region(region: str) -> dict:
    """按地区查询客户列表"""
    rows = enterprise_db.get_customers_by_region(region)
    if rows:
        total = sum(r["total_spent"] for r in rows)
        return {"status": "success", "data": {"region": region, "customers": rows, "count": len(rows), "total_spent": total}}
    return {"status": "success", "data": {"region": region, "customers": [], "count": 0}}


def add_customer(name: str, tier: str = "普通", region: str = "") -> dict:
    """添加新客户"""
    ok = enterprise_db.add_customer(name, tier, region)
    if ok:
        return {"status": "success", "data": {"name": name, "tier": tier, "region": region}}
    return {"status": "error", "data": {"note": "添加客户失败"}}


# ============================================================
# Function Calling Tool Definitions — OpenAI-compatible
# ============================================================
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "query_inventory",
            "description": "查询指定产品的当前库存数量和存放仓库位置。例如：'A 产品'、'B 产品'",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_name": {
                        "type": "string",
                        "description": "产品名称，如 'A 产品'、'B 产品'、'C 产品'"
                    }
                },
                "required": ["product_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_financial_report",
            "description": "获取指定月份的财务报表，包含营收、成本、利润和利润率",
            "parameters": {
                "type": "object",
                "properties": {
                    "month": {
                        "type": "string",
                        "description": "月份，格式为 YYYY-MM，如 '2025-03'；或季度如 '2025-Q1'"
                    }
                },
                "required": ["month"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_sales_summary",
            "description": "获取指定产品在某时间段的销售汇总数据",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_name": {
                        "type": "string",
                        "description": "产品名称，如 'A 产品'、'B 产品'"
                    },
                    "period": {
                        "type": "string",
                        "description": "时间周期，如 '2025-01'、'2025-Q1'、'total'"
                    }
                },
                "required": ["product_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "add_product",
            "description": "添加新产品到库存系统中",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_name": {
                        "type": "string",
                        "description": "产品名称"
                    },
                    "stock": {
                        "type": "integer",
                        "description": "初始库存数量"
                    },
                    "warehouse": {
                        "type": "string",
                        "description": "存放仓库位置"
                    },
                    "unit": {
                        "type": "string",
                        "description": "计量单位，如'件'、'台'、'箱'"
                    }
                },
                "required": ["product_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "adjust_stock",
            "description": "调整产品库存数量，正数增加库存，负数减少库存",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_name": {
                        "type": "string",
                        "description": "产品名称"
                    },
                    "delta": {
                        "type": "integer",
                        "description": "调整数量，正数增加，负数减少"
                    }
                },
                "required": ["product_name", "delta"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "record_sale",
            "description": "记录一笔销售数据",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_name": {
                        "type": "string",
                        "description": "产品名称"
                    },
                    "period": {
                        "type": "string",
                        "description": "销售月份，格式 YYYY-MM"
                    },
                    "amount": {
                        "type": "integer",
                        "description": "销售数量"
                    }
                },
                "required": ["product_name", "period", "amount"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "add_financial_record",
            "description": "添加一条财务月度记录",
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {
                        "type": "string",
                        "description": "月份，格式 YYYY-MM"
                    },
                    "revenue": {
                        "type": "integer",
                        "description": "营收金额（元）"
                    },
                    "cost": {
                        "type": "integer",
                        "description": "成本金额（元）"
                    },
                    "profit": {
                        "type": "integer",
                        "description": "利润金额（元）"
                    },
                    "margin": {
                        "type": "number",
                        "description": "利润率，如 0.35"
                    }
                },
                "required": ["period", "revenue", "cost", "profit", "margin"]
            }
        }
    },
    # ---- 员工工具 ----
    {
        "type": "function",
        "function": {
            "name": "query_employee",
            "description": "查询单个员工的基本信息，包括部门、职位、薪资、入职日期",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "员工姓名，如 '张伟'、'李娜'"
                    }
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_employees_by_department",
            "description": "按部门查询所有员工列表及其薪资信息",
            "parameters": {
                "type": "object",
                "properties": {
                    "department": {
                        "type": "string",
                        "description": "部门名称，如 '技术部'、'销售部'、'市场部'、'财务部'、'人事部'"
                    }
                },
                "required": ["department"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "add_employee",
            "description": "添加一名新员工",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "员工姓名"},
                    "department": {"type": "string", "description": "所属部门"},
                    "position": {"type": "string", "description": "职位名称"},
                    "salary": {"type": "integer", "description": "月薪（元）"},
                    "hire_date": {"type": "string", "description": "入职日期，格式 YYYY-MM-DD（可选）"}
                },
                "required": ["name", "department", "position", "salary"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_salary",
            "description": "调整员工薪资",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "员工姓名"},
                    "new_salary": {"type": "integer", "description": "新的月薪金额（元）"}
                },
                "required": ["name", "new_salary"]
            }
        }
    },
    # ---- 客户工具 ----
    {
        "type": "function",
        "function": {
            "name": "query_customer",
            "description": "查询单个客户的详细信息，包括等级、地区、累计消费金额",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "客户名称，如 '华为科技有限公司'"
                    }
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_customers_by_tier",
            "description": "按客户等级查询客户列表及消费总额",
            "parameters": {
                "type": "object",
                "properties": {
                    "tier": {
                        "type": "string",
                        "description": "客户等级：'VIP'、'企业'、'普通'"
                    }
                },
                "required": ["tier"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_customers_by_region",
            "description": "按地区查询客户列表及消费总额",
            "parameters": {
                "type": "object",
                "properties": {
                    "region": {
                        "type": "string",
                        "description": "地区：'华南'、'华东'、'华北'"
                    }
                },
                "required": ["region"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "add_customer",
            "description": "添加一个新客户",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "客户名称"},
                    "tier": {"type": "string", "description": "客户等级：VIP/企业/普通"},
                    "region": {"type": "string", "description": "所在地区"}
                },
                "required": ["name"]
            }
        }
    },
]

# 函数名到实际函数的映射
FUNCTION_MAP = {
    "query_inventory": query_inventory,
    "get_financial_report": get_financial_report,
    "get_sales_summary": get_sales_summary,
    "add_product": add_product,
    "adjust_stock": adjust_stock,
    "record_sale": record_sale,
    "add_financial_record": add_financial_record,
    "reset_database": reset_database,
    # 员工
    "query_employee": query_employee,
    "list_employees_by_department": list_employees_by_department,
    "add_employee": add_employee,
    "update_salary": update_salary,
    # 客户
    "query_customer": query_customer,
    "list_customers_by_tier": list_customers_by_tier,
    "list_customers_by_region": list_customers_by_region,
    "add_customer": add_customer,
}


# ============================================================
# HTTP Server — 以 REST API 形式暴露企业接口
# ============================================================
MOCK_HOST = "127.0.0.1"
MOCK_PORT = 8899


class MockEnterpriseAPIHandler(BaseHTTPRequestHandler):
    """模拟企业 API 的 HTTP Server"""

    def _send_json(self, data: dict, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        params = parse_qs(parsed.query)

        if path == "/api/inventory":
            product = params.get("product_name", [None])[0]
            if not product:
                self._send_json({"error": "缺少参数 product_name"}, 400)
                return
            self._send_json(query_inventory(product))

        elif path == "/api/financials":
            month = params.get("month", [None])[0]
            if not month:
                self._send_json({"error": "缺少参数 month"}, 400)
                return
            self._send_json(get_financial_report(month))

        elif path == "/api/sales":
            product = params.get("product_name", [None])[0]
            period = params.get("period", ["total"])[0]
            if not product:
                self._send_json({"error": "缺少参数 product_name"}, 400)
                return
            self._send_json(get_sales_summary(product, period))

        elif path == "/api/employee":
            name = params.get("name", [None])[0]
            if not name:
                self._send_json({"error": "缺少参数 name"}, 400)
                return
            self._send_json(query_employee(name))

        elif path == "/api/employees":
            dept = params.get("department", [None])[0]
            if dept:
                self._send_json(list_employees_by_department(dept))
            else:
                self._send_json({"status": "success", "data": list(EMPLOYEES_DB.values())})

        elif path == "/api/customer":
            name = params.get("name", [None])[0]
            if not name:
                self._send_json({"error": "缺少参数 name"}, 400)
                return
            self._send_json(query_customer(name))

        elif path == "/api/customers":
            tier = params.get("tier", [None])[0]
            region = params.get("region", [None])[0]
            if tier:
                self._send_json(list_customers_by_tier(tier))
            elif region:
                self._send_json(list_customers_by_region(region))
            else:
                self._send_json({"status": "success", "data": list(CUSTOMERS_DB.values())})

        elif path == "/api/health":
            self._send_json({"status": "ok", "service": "mock-enterprise-api"})

        else:
            self._send_json({"error": f"未知路径: {path}"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b"{}"
        try:
            params = json.loads(body)
        except json.JSONDecodeError:
            self._send_json({"error": "无效的 JSON"}, 400)
            return

        if path == "/api/inventory/add":
            product = params.get("product_name")
            if not product:
                self._send_json({"error": "缺少参数 product_name"}, 400)
                return
            self._send_json(add_product(
                product,
                stock=params.get("stock", 0),
                warehouse=params.get("warehouse", ""),
                unit=params.get("unit", "件"),
            ))

        elif path == "/api/inventory/adjust":
            product = params.get("product_name")
            delta = params.get("delta")
            if not product or delta is None:
                self._send_json({"error": "缺少参数 product_name 或 delta"}, 400)
                return
            self._send_json(adjust_stock(product, int(delta)))

        elif path == "/api/sales/record":
            product = params.get("product_name")
            period = params.get("period")
            amount = params.get("amount")
            if not product or not period or amount is None:
                self._send_json({"error": "缺少参数 product_name, period 或 amount"}, 400)
                return
            self._send_json(record_sale(product, period, int(amount)))

        elif path == "/api/financials/add":
            period = params.get("period")
            if not period:
                self._send_json({"error": "缺少参数 period"}, 400)
                return
            self._send_json(add_financial_record(
                period,
                params.get("revenue", 0),
                params.get("cost", 0),
                params.get("profit", 0),
                params.get("margin", 0.0),
            ))

        elif path == "/api/reset":
            self._send_json(reset_database())

        elif path == "/api/employee/add":
            name = params.get("name")
            if not name:
                self._send_json({"error": "缺少参数 name"}, 400)
                return
            self._send_json(add_employee(
                name,
                department=params.get("department", ""),
                position=params.get("position", ""),
                salary=params.get("salary", 0),
                hire_date=params.get("hire_date", ""),
            ))

        elif path == "/api/employee/salary":
            name = params.get("name")
            new_salary = params.get("new_salary")
            if not name or new_salary is None:
                self._send_json({"error": "缺少参数 name 或 new_salary"}, 400)
                return
            self._send_json(update_salary(name, int(new_salary)))

        elif path == "/api/customer/add":
            name = params.get("name")
            if not name:
                self._send_json({"error": "缺少参数 name"}, 400)
                return
            self._send_json(add_customer(
                name,
                tier=params.get("tier", "普通"),
                region=params.get("region", ""),
            ))

        else:
            self._send_json({"error": f"未知路径: {path}"}, 404)

    def log_message(self, format, *args):
        logger.info(f"[MockAPI] {args[0]} {args[1]} {args[2]}")


def start_mock_server(host: str = MOCK_HOST, port: int = MOCK_PORT):
    """启动 Mock Enterprise API Server（阻塞）"""
    server = HTTPServer((host, port), MockEnterpriseAPIHandler)
    logger.info(f"[MockAPI] Mock Enterprise API Server 启动: http://{host}:{port}")
    logger.info(f"[MockAPI]   GET  /api/inventory?product_name=xxx")
    logger.info(f"[MockAPI]   GET  /api/financials?month=YYYY-MM")
    logger.info(f"[MockAPI]   GET  /api/sales?product_name=xxx&period=xxx")
    logger.info(f"[MockAPI]   GET  /api/employee?name=xxx")
    logger.info(f"[MockAPI]   GET  /api/employees?department=技术部")
    logger.info(f"[MockAPI]   GET  /api/customer?name=xxx")
    logger.info(f"[MockAPI]   GET  /api/customers?tier=VIP")
    logger.info(f"[MockAPI]   POST /api/inventory/add")
    logger.info(f"[MockAPI]   POST /api/inventory/adjust")
    logger.info(f"[MockAPI]   POST /api/sales/record")
    logger.info(f"[MockAPI]   POST /api/financials/add")
    logger.info(f"[MockAPI]   POST /api/employee/add")
    logger.info(f"[MockAPI]   POST /api/employee/salary")
    logger.info(f"[MockAPI]   POST /api/customer/add")
    logger.info(f"[MockAPI]   POST /api/reset")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("[MockAPI] Server 已停止")
        server.server_close()


def start_mock_server_background(host: str = MOCK_HOST, port: int = MOCK_PORT):
    """在后台线程启动 Mock Server，返回 server 对象"""
    import threading
    server = HTTPServer((host, port), MockEnterpriseAPIHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info(f"[MockAPI] 后台 Mock Server 已启动: http://{host}:{port}")
    return server


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    start_mock_server()
