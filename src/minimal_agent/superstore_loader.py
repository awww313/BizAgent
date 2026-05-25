"""
Superstore Sales 数据集加载器
============================
自动从 Kaggle 下载 Sample - Superstore.csv 并导入 SQLite。

该数据集是 Tableau 官方的零售样本数据，包含 9,994 条美国超市
2016-2017 年的交易记录，覆盖 Furniture / Office Supplies / Technology
三大品类。

数据源: https://www.kaggle.com/datasets/vivek468/superstore-dataset-final
"""

import csv
import logging
import os
import io
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_CSV_FILENAME = "Sample - Superstore.csv"

# ---------- 内嵌 CSV（约 10KB 压缩，运行时解压） ----------
# 实际数据通过 kagglehub 下载，此文件只提供加载逻辑

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_CACHE_DIR = Path.home() / ".cache" / "kagglehub" / "datasets" / "vivek468" / "superstore-dataset-final" / "versions" / "1"


def _find_csv() -> Optional[str]:
    """查找已下载的 CSV 文件路径"""
    # 1. 检查 kagglehub 缓存
    kaggle_path = _CACHE_DIR / _CSV_FILENAME
    if kaggle_path.exists():
        return str(kaggle_path)
    # 2. 检查项目 data/ 目录
    local_path = _DATA_DIR / _CSV_FILENAME
    if local_path.exists():
        return str(local_path)
    return None


def download_csv(target_path: Optional[str] = None) -> str:
    """通过 kagglehub 下载数据集，返回 CSV 文件路径"""
    import kagglehub

    logger.info("[Superstore] 正在从 Kaggle 下载数据集 ...")
    dl_path = kagglehub.dataset_download("vivek468/superstore-dataset-final")
    csv_source = Path(dl_path) / _CSV_FILENAME
    if not csv_source.exists():
        raise FileNotFoundError(f"下载后未找到 {_CSV_FILENAME}")

    if target_path:
        import shutil
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        shutil.copy2(str(csv_source), str(target_path))
        logger.info("[Superstore] 已复制到 %s", target_path)
        return str(target_path)

    return str(csv_source)


def seed_superstore(db_instance=None, csv_path: Optional[str] = None) -> bool:
    """
    将 Superstore CSV 数据导入 SQLite 数据库。

    Args:
        db_instance: EnterpriseDB 实例（或 None 自动获取）
        csv_path: CSV 文件路径（None 则自动查找/下载）

    Returns:
        是否成功
    """
    from .enterprise_db import EnterpriseDB, DATA_DIR

    db = db_instance or EnterpriseDB()

    # 查找 CSV
    csv_file = csv_path or _find_csv()

    if not csv_file or not os.path.exists(csv_file):
        logger.info("[Superstore] CSV 未找到，尝试从 Kaggle 下载 ...")
        try:
            csv_file = download_csv(str(DATA_DIR / _CSV_FILENAME))
        except Exception as e:
            logger.error("[Superstore] 下载失败: %s", e)
            return False

    # 读取 CSV 并写入数据库
    conn = db._get_conn()
    try:
        # 清空旧数据避免重复
        conn.execute("DELETE FROM superstore_orders")
        conn.commit()

        with open(csv_file, encoding="cp1252") as f:
            reader = csv.DictReader(f)
            count = 0
            batch = []
            for row in reader:
                batch.append((
                    int(row["Row ID"]),
                    row["Order ID"],
                    _normalize_date(row["Order Date"]),
                    _normalize_date(row["Ship Date"]),
                    row["Ship Mode"],
                    row["Customer ID"],
                    row["Customer Name"],
                    row["Segment"],
                    row["Country"],
                    row["City"],
                    row["State"],
                    str(row.get("Postal Code", "")),
                    row["Region"],
                    row["Product ID"],
                    row["Category"],
                    row["Sub-Category"],
                    row["Product Name"],
                    float(row["Sales"]),
                    int(row["Quantity"]),
                    float(row["Discount"]),
                    float(row["Profit"]),
                ))
                count += 1
                if count % 2000 == 0:
                    conn.executemany(
                        """INSERT INTO superstore_orders
                           (row_id, order_id, order_date, ship_date, ship_mode,
                            customer_id, customer_name, segment, country, city, state,
                            postal_code, region, product_id, category, sub_category,
                            product_name, sales, quantity, discount, profit)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        batch,
                    )
                    conn.commit()
                    batch = []
                    logger.info("[Superstore] 已导入 %d 行 ...", count)

            # 剩余批次
            if batch:
                conn.executemany(
                    """INSERT INTO superstore_orders
                       (row_id, order_id, order_date, ship_date, ship_mode,
                        customer_id, customer_name, segment, country, city, state,
                        postal_code, region, product_id, category, sub_category,
                        product_name, sales, quantity, discount, profit)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    batch,
                )
                conn.commit()

        logger.info("[Superstore] 导入完成，共 %d 行", count)
        return True

    except Exception as e:
        logger.error("[Superstore] 导入失败: %s", e)
        conn.rollback()
        return False
    finally:
        conn.close()


def _normalize_date(date_str: str) -> str:
    """将 MM/DD/YYYY 转为 YYYY-MM-DD"""
    parts = date_str.split("/")
    if len(parts) == 3:
        return f"{parts[2]}-{parts[0].zfill(2)}-{parts[1].zfill(2)}"
    return date_str


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ok = seed_superstore()
    print(f"Superstore 数据加载: {'成功' if ok else '失败'}")
