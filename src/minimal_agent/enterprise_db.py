"""
企业业务数据库 — SQLite 动态可读写实现
======================================
替代硬编码的 Mock Dict，提供动态可读写的真实业务数据库。
支持库存、财务、销售、员工、客户五大业务实体的 CRUD + 高级写操作。
"""

import os
import sqlite3
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = DATA_DIR / "enterprise.db"
SCHEMA_VERSION = 4  # v4: 员工 status 改为 INTEGER（1在职/0已离职）



class EnterpriseDB:
    """企业业务数据库 — 单例，WAL 模式支持并发读写"""

    _instance: "EnterpriseDB | None" = None
    _initialized: bool = False

    def __new__(cls) -> "EnterpriseDB":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.db_path = str(DB_PATH)
        self._init_db()
        self._migrate_and_seed()
        logger.info("[EnterpriseDB] 已初始化: %s", self.db_path)

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_db_tables(self, conn: sqlite3.Connection):
        """执行建表 DDL（供初始化和迁移时共用）"""
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS inventory (
                product TEXT PRIMARY KEY,
                stock INTEGER NOT NULL DEFAULT 0,
                warehouse TEXT NOT NULL DEFAULT '',
                unit TEXT NOT NULL DEFAULT '件',
                last_updated TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS financial (
                period TEXT PRIMARY KEY,
                revenue INTEGER NOT NULL DEFAULT 0,
                cost INTEGER NOT NULL DEFAULT 0,
                profit INTEGER NOT NULL DEFAULT 0,
                margin REAL NOT NULL DEFAULT 0.0
            );

            CREATE TABLE IF NOT EXISTS sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product TEXT NOT NULL,
                period TEXT NOT NULL,
                amount INTEGER NOT NULL DEFAULT 0,
                UNIQUE(product, period)
            );

            CREATE TABLE IF NOT EXISTS employees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                department TEXT NOT NULL,
                position TEXT NOT NULL,
                salary INTEGER NOT NULL,
                hire_date TEXT NOT NULL,
                status INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                tier TEXT NOT NULL DEFAULT '普通',
                region TEXT NOT NULL DEFAULT '',
                total_spent INTEGER NOT NULL DEFAULT 0,
                last_purchase TEXT,
                contact TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_sales_product ON sales(product);
            CREATE INDEX IF NOT EXISTS idx_sales_period ON sales(period);
            CREATE INDEX IF NOT EXISTS idx_employees_dept ON employees(department);
            CREATE INDEX IF NOT EXISTS idx_customers_tier ON customers(tier);
            CREATE INDEX IF NOT EXISTS idx_customers_region ON customers(region);
        """)

    def _init_db(self):
        """创建所有表（IF NOT EXISTS，多次运行安全）"""
        conn = self._get_conn()
        try:
            self._init_db_tables(conn)
            conn.commit()
        finally:
            conn.close()

    def _migrate_and_seed(self):
        """检测 schema 版本，必要时迁移并重新填充"""
        conn = self._get_conn()
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]

            if version == SCHEMA_VERSION:
                # 版本一致，仅当数据库为空时填充
                count = conn.execute("SELECT COUNT(*) FROM inventory").fetchone()[0]
                if count == 0:
                    self._seed_all(conn)
                return

            # 版本不一致 → 重建所有表使 DDL 变更生效，然后重新填充
            logger.info("[EnterpriseDB] Schema 版本变化 (v%d → v%d)，重建表结构...", version, SCHEMA_VERSION)
            conn.executescript("""
                DROP TABLE IF EXISTS sales;
                DROP TABLE IF EXISTS financial;
                DROP TABLE IF EXISTS inventory;
                DROP TABLE IF EXISTS employees;
                DROP TABLE IF EXISTS customers;
            """)
            self._init_db_tables(conn)
            self._seed_all(conn)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.commit()
            logger.info("[EnterpriseDB] 迁移完成，当前版本 v%d", SCHEMA_VERSION)
        finally:
            conn.close()

    def _seed_all(self, conn: sqlite3.Connection):
        """填充所有种子数据（在已有连接中执行）"""
        # 库存 — 涵盖 5 个仓库，含新增的成都仓
        inventory = [
            ("A 产品", 150, "上海仓", "件", "2025-05-20"),
            ("B 产品", 320, "北京仓", "件", "2025-05-21"),
            ("C 产品", 85, "深圳仓", "台", "2025-05-19"),
            ("D 产品", 0, "广州仓", "箱", "2025-05-18"),
            ("智能音箱", 200, "上海仓", "台", "2025-05-22"),
            ("无线耳机", 450, "深圳仓", "副", "2025-05-23"),
            ("平板电脑", 60, "北京仓", "台", "2025-05-20"),
            ("机械键盘", 180, "上海仓", "个", "2025-05-21"),
            ("笔记本电脑", 120, "成都仓", "台", "2025-05-24"),
            ("智能手表", 250, "成都仓", "块", "2025-05-24"),
            ("咖啡机", 80, "成都仓", "台", "2025-05-23"),
            ("矿泉水", 2000, "成都仓", "箱", "2025-05-25"),
            ("打印纸", 1000, "广州仓", "箱", "2025-05-21"),
            ("墨盒", 180, "上海仓", "个", "2025-05-22"),
            ("文件夹", 500, "北京仓", "个", "2025-05-19"),
            ("洗发水", 300, "广州仓", "瓶", "2025-05-23"),
        ]
        conn.executemany(
            "INSERT INTO inventory (product, stock, warehouse, unit, last_updated) VALUES (?, ?, ?, ?, ?)",
            inventory,
        )

        # 财务 — 2025 全年逐月 + 季度汇总
        financial = [
            ("2025-01", 4_200_000, 2_500_000, 1_700_000, 0.405),
            ("2025-02", 3_800_000, 2_300_000, 1_500_000, 0.395),
            ("2025-03", 5_100_000, 2_900_000, 2_200_000, 0.431),
            ("2025-Q1", 13_100_000, 7_700_000, 5_400_000, 0.412),
            ("2025-04", 5_800_000, 3_200_000, 2_600_000, 0.448),
            ("2025-05", 6_200_000, 3_400_000, 2_800_000, 0.452),
            ("2025-06", 6_800_000, 3_600_000, 3_200_000, 0.471),
            ("2025-Q2", 18_800_000, 10_200_000, 8_600_000, 0.457),
            ("2025-07", 7_100_000, 3_800_000, 3_300_000, 0.465),
            ("2025-08", 6_500_000, 3_500_000, 3_000_000, 0.462),
            ("2025-09", 7_600_000, 4_000_000, 3_600_000, 0.474),
            ("2025-Q3", 21_200_000, 11_300_000, 9_900_000, 0.467),
            ("2025-10", 8_200_000, 4_300_000, 3_900_000, 0.476),
            ("2025-11", 8_800_000, 4_500_000, 4_300_000, 0.489),
            ("2025-12", 9_500_000, 4_800_000, 4_700_000, 0.495),
            ("2025-Q4", 26_500_000, 13_600_000, 12_900_000, 0.487),
            ("2025全年", 79_600_000, 42_800_000, 36_800_000, 0.462),
        ]
        conn.executemany(
            "INSERT INTO financial (period, revenue, cost, profit, margin) VALUES (?, ?, ?, ?, ?)",
            financial,
        )

        # 销售 — 16 种产品逐月数据
        sales = [
            # A 产品
            ("A 产品", "2025-01", 1200), ("A 产品", "2025-02", 1350), ("A 产品", "2025-03", 1500),
            ("A 产品", "2025-04", 1650), ("A 产品", "2025-05", 1800), ("A 产品", "2025-06", 2000),
            ("A 产品", "2025-07", 2100), ("A 产品", "2025-08", 1950), ("A 产品", "2025-09", 2200),
            ("A 产品", "2025-10", 2400), ("A 产品", "2025-11", 2600), ("A 产品", "2025-12", 2800),
            # B 产品
            ("B 产品", "2025-01", 800),  ("B 产品", "2025-02", 920),  ("B 产品", "2025-03", 1100),
            ("B 产品", "2025-04", 1200), ("B 产品", "2025-05", 1280), ("B 产品", "2025-06", 1350),
            ("B 产品", "2025-07", 1400), ("B 产品", "2025-08", 1320), ("B 产品", "2025-09", 1450),
            ("B 产品", "2025-10", 1550), ("B 产品", "2025-11", 1680), ("B 产品", "2025-12", 1750),
            # C 产品
            ("C 产品", "2025-01", 200),  ("C 产品", "2025-02", 180),  ("C 产品", "2025-03", 250),
            ("C 产品", "2025-04", 280),  ("C 产品", "2025-05", 310),  ("C 产品", "2025-06", 340),
            ("C 产品", "2025-07", 360),  ("C 产品", "2025-08", 330),  ("C 产品", "2025-09", 380),
            ("C 产品", "2025-10", 400),  ("C 产品", "2025-11", 420),  ("C 产品", "2025-12", 450),
            # D 产品（零库存，有历史销售）
            ("D 产品", "2025-01", 50),   ("D 产品", "2025-02", 45),   ("D 产品", "2025-03", 30),
            # 智能音箱
            ("智能音箱", "2025-01", 400), ("智能音箱", "2025-02", 450), ("智能音箱", "2025-03", 520),
            ("智能音箱", "2025-04", 580), ("智能音箱", "2025-05", 620), ("智能音箱", "2025-06", 700),
            ("智能音箱", "2025-07", 750), ("智能音箱", "2025-08", 690), ("智能音箱", "2025-09", 800),
            ("智能音箱", "2025-10", 880), ("智能音箱", "2025-11", 950), ("智能音箱", "2025-12", 1050),
            # 无线耳机
            ("无线耳机", "2025-01", 600), ("无线耳机", "2025-02", 680), ("无线耳机", "2025-03", 750),
            ("无线耳机", "2025-04", 820), ("无线耳机", "2025-05", 900), ("无线耳机", "2025-06", 980),
            ("无线耳机", "2025-07", 1050),("无线耳机", "2025-08", 950), ("无线耳机", "2025-09", 1100),
            ("无线耳机", "2025-10", 1200),("无线耳机", "2025-11", 1300),("无线耳机", "2025-12", 1450),
            # 平板电脑
            ("平板电脑", "2025-01", 150), ("平板电脑", "2025-02", 170), ("平板电脑", "2025-03", 190),
            ("平板电脑", "2025-04", 210), ("平板电脑", "2025-05", 230), ("平板电脑", "2025-06", 260),
            ("平板电脑", "2025-07", 280), ("平板电脑", "2025-08", 250), ("平板电脑", "2025-09", 300),
            ("平板电脑", "2025-10", 330), ("平板电脑", "2025-11", 360), ("平板电脑", "2025-12", 400),
            # 机械键盘
            ("机械键盘", "2025-01", 350), ("机械键盘", "2025-02", 380), ("机械键盘", "2025-03", 420),
            ("机械键盘", "2025-04", 460), ("机械键盘", "2025-05", 500), ("机械键盘", "2025-06", 540),
            ("机械键盘", "2025-07", 580), ("机械键盘", "2025-08", 530), ("机械键盘", "2025-09", 600),
            ("机械键盘", "2025-10", 650), ("机械键盘", "2025-11", 700), ("机械键盘", "2025-12", 750),
            # 笔记本电脑（成都仓新品）
            ("笔记本电脑", "2025-04", 80),  ("笔记本电脑", "2025-05", 95),  ("笔记本电脑", "2025-06", 110),
            ("笔记本电脑", "2025-07", 130), ("笔记本电脑", "2025-08", 120), ("笔记本电脑", "2025-09", 145),
            ("笔记本电脑", "2025-10", 160), ("笔记本电脑", "2025-11", 175), ("笔记本电脑", "2025-12", 200),
            # 智能手表（成都仓新品）
            ("智能手表", "2025-04", 200), ("智能手表", "2025-05", 230), ("智能手表", "2025-06", 260),
            ("智能手表", "2025-07", 290), ("智能手表", "2025-08", 270), ("智能手表", "2025-09", 310),
            ("智能手表", "2025-10", 340), ("智能手表", "2025-11", 370), ("智能手表", "2025-12", 410),
            # 咖啡机（成都仓新品）
            ("咖啡机", "2025-05", 50),  ("咖啡机", "2025-06", 60),  ("咖啡机", "2025-07", 70),
            ("咖啡机", "2025-08", 65),  ("咖啡机", "2025-09", 80),  ("咖啡机", "2025-10", 90),
            ("咖啡机", "2025-11", 95),  ("咖啡机", "2025-12", 110),
            # 矿泉水（成都仓新品）
            ("矿泉水", "2025-01", 1500), ("矿泉水", "2025-02", 1400), ("矿泉水", "2025-03", 1600),
            ("矿泉水", "2025-04", 1550), ("矿泉水", "2025-05", 1700), ("矿泉水", "2025-06", 1650),
            ("矿泉水", "2025-07", 1800), ("矿泉水", "2025-08", 1600), ("矿泉水", "2025-09", 1900),
            ("矿泉水", "2025-10", 2000), ("矿泉水", "2025-11", 1850), ("矿泉水", "2025-12", 2100),
            # 打印纸
            ("打印纸", "2025-01", 2000), ("打印纸", "2025-02", 1800), ("打印纸", "2025-03", 2200),
            ("打印纸", "2025-04", 2100), ("打印纸", "2025-05", 2400), ("打印纸", "2025-06", 2300),
            ("打印纸", "2025-07", 2500), ("打印纸", "2025-08", 2200), ("打印纸", "2025-09", 2600),
            ("打印纸", "2025-10", 2700), ("打印纸", "2025-11", 2500), ("打印纸", "2025-12", 2800),
            # 墨盒
            ("墨盒", "2025-01", 300),   ("墨盒", "2025-02", 280),   ("墨盒", "2025-03", 320),
            ("墨盒", "2025-04", 310),   ("墨盒", "2025-05", 350),   ("墨盒", "2025-06", 340),
            ("墨盒", "2025-07", 370),   ("墨盒", "2025-08", 330),   ("墨盒", "2025-09", 380),
            ("墨盒", "2025-10", 390),   ("墨盒", "2025-11", 370),   ("墨盒", "2025-12", 400),
            # 文件夹
            ("文件夹", "2025-01", 800), ("文件夹", "2025-02", 750), ("文件夹", "2025-03", 850),
            ("文件夹", "2025-04", 820), ("文件夹", "2025-05", 900), ("文件夹", "2025-06", 880),
            ("文件夹", "2025-07", 920), ("文件夹", "2025-08", 850), ("文件夹", "2025-09", 950),
            ("文件夹", "2025-10", 980), ("文件夹", "2025-11", 920), ("文件夹", "2025-12", 1000),
            # 洗发水
            ("洗发水", "2025-01", 500), ("洗发水", "2025-02", 480), ("洗发水", "2025-03", 550),
            ("洗发水", "2025-04", 530), ("洗发水", "2025-05", 600), ("洗发水", "2025-06", 580),
            ("洗发水", "2025-07", 620), ("洗发水", "2025-08", 570), ("洗发水", "2025-09", 640),
            ("洗发水", "2025-10", 660), ("洗发水", "2025-11", 620), ("洗发水", "2025-12", 680),
        ]
        conn.executemany(
            "INSERT INTO sales (product, period, amount) VALUES (?, ?, ?)",
            sales,
        )

        # 员工
        employees = [
            ("张伟",   "技术部", "高级工程师",  25000, "2020-03-15", 1),
            ("李娜",   "市场部", "市场总监",    30000, "2019-06-01", 1),
            ("王强",   "销售部", "销售经理",    22000, "2021-01-10", 1),
            ("赵芳",   "财务部", "财务主管",    20000, "2018-09-20", 1),
            ("刘洋",   "技术部", "产品经理",    23000, "2020-07-01", 1),
            ("陈静",   "人事部", "HR 经理",     18000, "2021-03-15", 1),
            ("孙磊",   "销售部", "销售代表",    15000, "2022-02-01", 1),
            ("周敏",   "技术部", "前端工程师",  18000, "2022-06-01", 1),
            ("吴涛",   "技术部", "后端工程师",  20000, "2021-09-15", 1),
            ("郑丽",   "市场部", "品牌专员",    14000, "2023-01-10", 1),
            ("黄鑫",   "技术部", "算法工程师",  28000, "2021-11-01", 1),
            ("林芳",   "财务部", "会计",        12000, "2023-06-15", 1),
            ("何明",   "销售部", "大客户经理",  26000, "2020-08-20", 1),
            ("唐雅",   "人事部", "招聘专员",    11000, "2024-01-10", 1),
            ("曹磊",   "技术部", "运维工程师",  17000, "2022-09-01", 1),
        ]
        conn.executemany(
            "INSERT INTO employees (name, department, position, salary, hire_date, status) VALUES (?, ?, ?, ?, ?, ?)",
            employees,
        )

        # 客户
        customers = [
            ("华为科技有限公司",   "VIP",   "华南", 2_500_000, "2025-12-18", "138****8888"),
            ("阿里巴巴集团",       "VIP",   "华东", 3_200_000, "2025-12-20", "139****9999"),
            ("腾讯计算机系统有限公", "VIP", "华南", 1_800_000, "2025-12-15", "137****7777"),
            ("京东集团",           "企业",  "华北", 1_200_000, "2025-12-10", "136****6666"),
            ("字节跳动科技",       "企业",  "华北",   900_000, "2025-11-30", "135****5555"),
            ("小米科技",           "企业",  "华东",   650_000, "2025-12-05", "134****4444"),
            ("网易集团",           "普通",  "华南",   350_000, "2025-10-20", "133****3333"),
            ("百度在线网络技术",   "普通",  "华北",   420_000, "2025-11-15", "132****2222"),
            ("美团科技",           "普通",  "华东",   280_000, "2025-09-25", "131****1111"),
            ("拼多多科技",         "普通",  "华东",   180_000, "2025-08-30", "130****0000"),
        ]
        conn.executemany(
            "INSERT INTO customers (name, tier, region, total_spent, last_purchase, contact) VALUES (?, ?, ?, ?, ?, ?)",
            customers,
        )
        conn.commit()
        logger.info("[EnterpriseDB] 种子数据已填充")

    # ==========================================================
    # Inventory CRUD
    # ==========================================================

    def get_inventory(self, product: str) -> Optional[dict]:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM inventory WHERE product = ?", [product]
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_all_inventory(self) -> dict[str, dict]:
        conn = self._get_conn()
        try:
            rows = conn.execute("SELECT * FROM inventory").fetchall()
            return {r["product"]: dict(r) for r in rows}
        finally:
            conn.close()

    def upsert_inventory(
        self, product: str, stock: int, warehouse: str = "", unit: str = "件"
    ) -> bool:
        conn = self._get_conn()
        try:
            conn.execute(
                """INSERT INTO inventory (product, stock, warehouse, unit, last_updated)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(product) DO UPDATE SET
                       stock=excluded.stock, warehouse=excluded.warehouse,
                       unit=excluded.unit, last_updated=excluded.last_updated""",
                [product, stock, warehouse, unit, datetime.now().strftime("%Y-%m-%d")],
            )
            conn.commit()
            return True
        except sqlite3.Error as e:
            logger.error("[EnterpriseDB] 库存写入失败: %s", e)
            return False
        finally:
            conn.close()

    # ==========================================================
    # Financial CRUD
    # ==========================================================

    def get_financial(self, period: str) -> Optional[dict]:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM financial WHERE period = ?", [period]
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_all_financial(self) -> dict[str, dict]:
        conn = self._get_conn()
        try:
            rows = conn.execute("SELECT * FROM financial").fetchall()
            return {r["period"]: dict(r) for r in rows}
        finally:
            conn.close()

    def upsert_financial(
        self, period: str, revenue: int, cost: int, profit: int, margin: float
    ) -> bool:
        conn = self._get_conn()
        try:
            conn.execute(
                """INSERT INTO financial (period, revenue, cost, profit, margin)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(period) DO UPDATE SET
                       revenue=excluded.revenue, cost=excluded.cost,
                       profit=excluded.profit, margin=excluded.margin""",
                [period, revenue, cost, profit, margin],
            )
            conn.commit()
            return True
        except sqlite3.Error as e:
            logger.error("[EnterpriseDB] 财务写入失败: %s", e)
            return False
        finally:
            conn.close()

    # ==========================================================
    # Sales CRUD
    # ==========================================================

    def get_product_sales(self, product: str) -> dict[str, int]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT period, amount FROM sales WHERE product = ? ORDER BY period",
                [product],
            ).fetchall()
            result = {r["period"]: r["amount"] for r in rows}
            if result:
                result["total"] = sum(v for v in result.values())
            return result
        finally:
            conn.close()

    def get_all_sales(self) -> dict[str, dict]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT product, period, amount FROM sales ORDER BY product, period"
            ).fetchall()
            result: dict[str, dict] = {}
            for r in rows:
                p = r["product"]
                period = r["period"]
                if p not in result:
                    result[p] = {}
                result[p][period] = r["amount"]
            for p in result:
                result[p]["total"] = sum(
                    v for k, v in result[p].items() if k != "total"
                )
            return result
        finally:
            conn.close()

    def upsert_sales(self, product: str, period: str, amount: int) -> bool:
        conn = self._get_conn()
        try:
            conn.execute(
                """INSERT INTO sales (product, period, amount) VALUES (?, ?, ?)
                   ON CONFLICT(product, period) DO UPDATE SET amount=excluded.amount""",
                [product, period, amount],
            )
            conn.commit()
            return True
        except sqlite3.Error as e:
            logger.error("[EnterpriseDB] 销售写入失败: %s", e)
            return False
        finally:
            conn.close()

    # ==========================================================
    # Employee CRUD
    # ==========================================================

    def get_employee(self, name: str) -> Optional[dict]:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM employees WHERE name = ?", [name]
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_all_employees(self) -> dict[str, dict]:
        """返回 {name: {id, name, department, position, salary, hire_date, status}, ...}"""
        conn = self._get_conn()
        try:
            rows = conn.execute("SELECT * FROM employees").fetchall()
            return {r["name"]: dict(r) for r in rows}
        finally:
            conn.close()

    def get_employees_by_department(self, department: str) -> list[dict]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM employees WHERE department = ?", [department]
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def add_employee(self, name: str, department: str, position: str, salary: int, hire_date: str = "", status: int = 1) -> bool:
        conn = self._get_conn()
        try:
            conn.execute(
                """INSERT INTO employees (name, department, position, salary, hire_date, status)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [name, department, position, salary, hire_date or datetime.now().strftime("%Y-%m-%d"), status],
            )
            conn.commit()
            return True
        except sqlite3.Error as e:
            logger.error("[EnterpriseDB] 员工添加失败: %s", e)
            return False
        finally:
            conn.close()

    def update_employee_salary(self, name: str, new_salary: int) -> bool:
        conn = self._get_conn()
        try:
            cur = conn.execute(
                "UPDATE employees SET salary = ? WHERE name = ?",
                [new_salary, name],
            )
            conn.commit()
            return cur.rowcount > 0
        except sqlite3.Error as e:
            logger.error("[EnterpriseDB] 薪资更新失败: %s", e)
            return False
        finally:
            conn.close()

    # ==========================================================
    # Customer CRUD
    # ==========================================================

    def get_customer(self, name: str) -> Optional[dict]:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM customers WHERE name = ?", [name]
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_all_customers(self) -> dict[str, dict]:
        """返回 {name: {id, name, tier, region, total_spent, last_purchase, contact}, ...}"""
        conn = self._get_conn()
        try:
            rows = conn.execute("SELECT * FROM customers").fetchall()
            return {r["name"]: dict(r) for r in rows}
        finally:
            conn.close()

    def get_customers_by_tier(self, tier: str) -> list[dict]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM customers WHERE tier = ? ORDER BY total_spent DESC", [tier]
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_customers_by_region(self, region: str) -> list[dict]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM customers WHERE region = ? ORDER BY total_spent DESC", [region]
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def add_customer(self, name: str, tier: str = "普通", region: str = "", total_spent: int = 0) -> bool:
        conn = self._get_conn()
        try:
            conn.execute(
                """INSERT INTO customers (name, tier, region, total_spent, last_purchase, contact)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [name, tier, region, total_spent, "", ""],
            )
            conn.commit()
            return True
        except sqlite3.Error as e:
            logger.error("[EnterpriseDB] 客户添加失败: %s", e)
            return False
        finally:
            conn.close()

    def update_customer_spent(self, name: str, additional: int) -> bool:
        """增加客户累计消费"""
        conn = self._get_conn()
        try:
            cur = conn.execute(
                "UPDATE customers SET total_spent = total_spent + ?, last_purchase = ? WHERE name = ?",
                [additional, datetime.now().strftime("%Y-%m-%d"), name],
            )
            conn.commit()
            return cur.rowcount > 0
        except sqlite3.Error as e:
            logger.error("[EnterpriseDB] 客户消费更新失败: %s", e)
            return False
        finally:
            conn.close()

    # ==========================================================
    # 高级写操作
    # ==========================================================

    def add_product(self, product: str, stock: int = 0, warehouse: str = "", unit: str = "件") -> bool:
        return self.upsert_inventory(product, stock, warehouse, unit)

    def adjust_stock(self, product: str, delta: int) -> bool:
        info = self.get_inventory(product)
        if not info:
            return False
        new_stock = max(0, info["stock"] + delta)
        return self.upsert_inventory(product, new_stock, info["warehouse"], info["unit"])

    def record_sale(self, product: str, period: str, amount: int) -> bool:
        return self.upsert_sales(product, period, amount)

    def add_financial_record(self, period: str, revenue: int, cost: int, profit: int, margin: float) -> bool:
        return self.upsert_financial(period, revenue, cost, profit, margin)

    # ==========================================================
    # 维护
    # ==========================================================

    def reset(self) -> bool:
        """清空并重新填充种子数据（同时清除手动添加的数据）"""
        conn = self._get_conn()
        try:
            conn.executescript("""
                DELETE FROM sales;
                DELETE FROM financial;
                DELETE FROM inventory;
                DELETE FROM employees;
                DELETE FROM customers;
            """)
            self._seed_all(conn)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.commit()
            logger.info("[EnterpriseDB] 已重置为种子数据")
            return True
        except sqlite3.Error as e:
            logger.error("[EnterpriseDB] 重置失败: %s", e)
            return False
        finally:
            conn.close()


# 全局单例
db: EnterpriseDB = EnterpriseDB()
