"""
智能数据可视化：自动生成折线 / 柱状 / 饼图
=============================================
基于业务数据自动选择图表类型，支持中文渲染，图片保存与路径返回。

图表类型自动匹配:
  - 财务时序 → 折线图 (revenue/cost/profit 趋势)
  - 销售对比 → 柱状图 (各产品销量)
  - 库存状态 → 饼图 (库存分布)
  - 占比分析 → 饼图
  - 汇总对比 → 柱状图
"""

import os
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# 图表输出目录
CHARTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "charts")


# ============================================================
# 用户友好的错误提示（技术型报错 → 纯中文提示）
# ============================================================

USER_FRIENDLY_ERRORS = {
    "matplotlib": "图表生成需要 matplotlib 库，当前环境未安装。已自动降级为纯文本分析。",
    "font": "系统缺少中文字体，图表中文可能显示异常。已尝试使用备用字体。",
    "data": "数据格式不符合图表生成要求，已自动降级为纯文本分析。",
    "save": "图表文件保存失败，已自动降级为纯文本分析。",
    "unknown": "图表生成时遇到技术问题，已自动降级为纯文本分析。",
}


def _user_error(reason: str = "unknown") -> dict:
    """
    生成用户友好的错误响应，禁止暴露原始报错信息。

    Returns:
        {"error": "用户友好的中文提示"}
    """
    return {"error": USER_FRIENDLY_ERRORS.get(reason, USER_FRIENDLY_ERRORS["unknown"])}


# ============================================================
# 中文字体配置
# ============================================================

def _setup_chinese_font():
    """配置 matplotlib 中文字体"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.font_manager import FontProperties, FontManager

        candidates = [
            "SimHei", "Microsoft YaHei", "Noto Sans SC",
            "DengXian", "STXihei", "Arial Unicode MS",
        ]

        fm = FontManager()
        available = {f.name for f in fm.ttflist}

        chosen = None
        for name in candidates:
            if name in available:
                chosen = name
                break

        if chosen:
            plt.rcParams["font.sans-serif"] = [chosen] + plt.rcParams["font.sans-serif"]
            plt.rcParams["font.family"] = "sans-serif"
        else:
            for f in fm.ttflist:
                if any(kw in f.name.lower() for kw in ["hei", "yahei", "song", "cjk", "noto"]):
                    plt.rcParams["font.sans-serif"] = [f.name] + plt.rcParams["font.sans-serif"]
                    plt.rcParams["font.family"] = "sans-serif"
                    chosen = f.name
                    break

        plt.rcParams["axes.unicode_minus"] = False
        return chosen
    except ImportError:
        logger.warning("[Visualizer] matplotlib 未安装，无法配置字体")
        return None
    except Exception as e:
        logger.warning("[Visualizer] 字体配置异常: %s", e)
        return None


# ============================================================
# 全局初始化
# ============================================================

_CHINESE_FONT = None
_MATPLOTLIB_AVAILABLE = None


def _check_matplotlib():
    """检查 matplotlib 是否可用"""
    global _MATPLOTLIB_AVAILABLE
    if _MATPLOTLIB_AVAILABLE is None:
        try:
            import matplotlib
            _MATPLOTLIB_AVAILABLE = True
        except ImportError:
            _MATPLOTLIB_AVAILABLE = False
            logger.warning("[Visualizer] matplotlib 不可用，图表功能已禁用")
    return _MATPLOTLIB_AVAILABLE


def _ensure_font():
    global _CHINESE_FONT
    if _CHINESE_FONT is None:
        if not _check_matplotlib():
            return None
        _CHINESE_FONT = _setup_chinese_font()
        logger.info("[Visualizer] 中文字体: %s", _CHINESE_FONT)
    return _CHINESE_FONT


def _ensure_charts_dir() -> str:
    """确保图表目录存在，返回绝对路径"""
    charts_dir = os.path.abspath(CHARTS_DIR)
    try:
        os.makedirs(charts_dir, exist_ok=True)
    except OSError as e:
        logger.warning("[Visualizer] 创建图表目录失败: %s", e)
    return charts_dir


def _generate_filename(prefix: str = "chart") -> str:
    """生成时间戳文件名"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    return f"{prefix}_{ts}.png"


# ============================================================
# 颜色方案
# ============================================================

COLORS = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
    "#aec7e8", "#ffbb78", "#98df8a", "#ff9896", "#c5b0d5",
]


# ============================================================
# 安全包装器
# ============================================================

def safe_chart(chart_func):
    """装饰器/包装器：捕获图表生成中的所有异常，自动降级为纯文本"""
    def wrapper(*args, **kwargs):
        if not _check_matplotlib():
            return _user_error("matplotlib")
        try:
            return chart_func(*args, **kwargs)
        except (ImportError, ModuleNotFoundError):
            return _user_error("matplotlib")
        except (ValueError, TypeError, IndexError, KeyError) as e:
            logger.warning("[Visualizer] 数据异常 (%s): %s", chart_func.__name__, e)
            return _user_error("data")
        except (OSError, PermissionError) as e:
            logger.warning("[Visualizer] 文件异常 (%s): %s", chart_func.__name__, e)
            return _user_error("save")
        except Exception as e:
            logger.warning("[Visualizer] 图表生成异常 (%s): %s", chart_func.__name__, e)
            return _user_error("unknown")
    return wrapper


# ============================================================
# 图表生成函数
# ============================================================

@safe_chart
def line_chart(
    x_data: list[str],
    y_data_list: list[dict],
    title: str = "趋势图",
    x_label: str = "",
    y_label: str = "",
    filename: Optional[str] = None,
    figsize: tuple = (10, 5),
) -> dict:
    """折线图：适用于时序趋势展示。"""
    _ensure_font()
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=figsize)

    for i, series in enumerate(y_data_list):
        ax.plot(
            x_data, series["values"],
            marker="o", linewidth=2, markersize=5,
            color=COLORS[i % len(COLORS)],
            label=series["label"],
        )

    ax.set_title(title, fontsize=14, fontweight="bold", pad=15)
    ax.set_xlabel(x_label, fontsize=11)
    ax.set_ylabel(y_label, fontsize=11)
    ax.legend(loc="best", fontsize=10)
    ax.grid(True, alpha=0.3)
    ax.set_xlim(-0.3, len(x_data) - 0.7)

    for series in y_data_list:
        for i, v in enumerate(series["values"]):
            ax.annotate(
                f"{v:,.0f}", (i, v),
                textcoords="offset points", xytext=(0, 10),
                ha="center", fontsize=8, color=COLORS[y_data_list.index(series) % len(COLORS)],
            )

    fig.tight_layout()
    return _save_and_return(fig, filename, "line", title)


@safe_chart
def bar_chart(
    categories: list[str],
    values: list[float],
    title: str = "柱状图",
    x_label: str = "",
    y_label: str = "",
    filename: Optional[str] = None,
    figsize: tuple = (10, 5),
    horizontal: bool = False,
    color: Optional[str] = None,
) -> dict:
    """柱状图：适用于分类对比。"""
    _ensure_font()
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=figsize)
    bar_color = color or COLORS[0]

    if horizontal:
        bars = ax.barh(categories, values, color=bar_color, edgecolor="white", linewidth=0.5)
        for bar, v in zip(bars, values):
            ax.text(v + max(values) * 0.01, bar.get_y() + bar.get_height() / 2,
                    f"{v:,.0f}", va="center", fontsize=9)
    else:
        bars = ax.bar(categories, values, color=bar_color, edgecolor="white", linewidth=0.5)
        for bar, v in zip(bars, values):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + max(values) * 0.01,
                    f"{v:,.0f}", ha="center", fontsize=9)

    ax.set_title(title, fontsize=14, fontweight="bold", pad=15)
    ax.set_xlabel(x_label, fontsize=11)
    ax.set_ylabel(y_label, fontsize=11)
    ax.grid(True, alpha=0.3, axis="y" if not horizontal else "x")

    fig.tight_layout()
    return _save_and_return(fig, filename, "bar", title)


@safe_chart
def grouped_bar_chart(
    categories: list[str],
    series_list: list[dict],
    title: str = "分组柱状图",
    x_label: str = "",
    y_label: str = "",
    filename: Optional[str] = None,
    figsize: tuple = (10, 5),
) -> dict:
    """分组柱状图：适用于多系列对比。"""
    _ensure_font()
    import matplotlib.pyplot as plt
    import numpy as np

    fig, ax = plt.subplots(figsize=figsize)

    n_series = len(series_list)
    n_cats = len(categories)
    bar_width = 0.8 / n_series
    x = np.arange(n_cats)

    for i, series in enumerate(series_list):
        offset = (i - n_series / 2) * bar_width + bar_width / 2
        bars = ax.bar(
            x + offset, series["values"],
            bar_width * 0.9,
            label=series["label"],
            color=COLORS[i % len(COLORS)],
            edgecolor="white", linewidth=0.5,
        )

    ax.set_xticks(x)
    ax.set_xticklabels(categories, fontsize=10)
    ax.set_title(title, fontsize=14, fontweight="bold", pad=15)
    ax.set_xlabel(x_label, fontsize=11)
    ax.set_ylabel(y_label, fontsize=11)
    ax.legend(loc="best", fontsize=10)
    ax.grid(True, alpha=0.3, axis="y")

    fig.tight_layout()
    return _save_and_return(fig, filename, "grouped_bar", title)


@safe_chart
def pie_chart(
    labels: list[str],
    values: list[float],
    title: str = "占比图",
    filename: Optional[str] = None,
    figsize: tuple = (8, 6),
    show_percent: bool = True,
) -> dict:
    """饼图：适用于占比/分布展示。"""
    _ensure_font()
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=figsize)

    colors = COLORS[:len(labels)]
    explode = [0.05] * len(labels)

    wedges, texts, autotexts = ax.pie(
        values,
        labels=labels,
        colors=colors,
        autopct="%1.1f%%" if show_percent else None,
        startangle=90,
        explode=explode,
        pctdistance=0.75,
        textprops={"fontsize": 10},
    )

    total = sum(values)
    ax.text(0, 0, f"合计\n{total:,.0f}", ha="center", va="center", fontsize=11, fontweight="bold")

    ax.set_title(title, fontsize=14, fontweight="bold", pad=15)

    fig.tight_layout()
    return _save_and_return(fig, filename, "pie", title)


# ============================================================
# 自动图表（根据数据智能选择类型）
# ============================================================

@safe_chart
def auto_chart(
    data: dict,
    data_source: str = "",
    filename: Optional[str] = None,
) -> dict:
    """根据数据特征自动选择图表类型并生成。"""
    if not data:
        return _user_error("data")

    mom = data.get("financial_mom", [])
    if mom and len(mom) >= 2:
        months = [item["period"] for item in mom]
        series = []
        for metric in ["current_value"]:
            values = [item[metric] for item in mom]
            series.append({"label": "指标值", "values": values})
        title = f"财务环比趋势" + (f" — {data_source}" if data_source else "")
        return line_chart(months, series, title=title, filename=filename)

    comparison = data.get("sales_comparison", [])
    if comparison:
        labels = [item["product"] for item in comparison]
        values = [item["total_sales"] for item in comparison]
        title = f"产品销量对比" + (f" — {data_source}" if data_source else "")
        return bar_chart(labels, values, title=title, y_label="销量（件）", filename=filename)

    summary = data.get("financial_summary", {})
    if summary and summary.get("months_analyzed", 0) > 0:
        labels_list = ["总营收", "总成本", "总利润"]
        values_list = [
            summary.get("total_revenue", 0),
            summary.get("total_cost", 0),
            summary.get("total_profit", 0),
        ]
        if any(v != 0 for v in values_list):
            title = f"财务汇总" + (f" — {data_source}" if data_source else "")
            return bar_chart(labels_list, values_list, title=title, y_label="金额（元）", filename=filename)

    inv = data.get("inventory_analysis", {})
    if inv and inv.get("warehouse_distribution"):
        labels_wh = list(inv["warehouse_distribution"].keys())
        values_wh = list(inv["warehouse_distribution"].values())
        title = f"库存仓库分布" + (f" — {data_source}" if data_source else "")
        return pie_chart(labels_wh, values_wh, title=title, filename=filename)

    return _user_error("data")


# ============================================================
# 保存工具
# ============================================================

_CHART_COUNTER = 0


def _save_and_return(
    fig,
    filename: Optional[str],
    chart_type: str,
    title: str,
) -> dict:
    """保存图表并返回路径信息"""
    global _CHART_COUNTER

    charts_dir = _ensure_charts_dir()
    fname = filename or _generate_filename(chart_type)
    filepath = os.path.join(charts_dir, fname)

    fig.savefig(filepath, dpi=150, bbox_inches="tight", facecolor="white")
    import matplotlib.pyplot as plt
    plt.close(fig)

    _CHART_COUNTER += 1
    logger.info("[Visualizer] 已生成 %s: %s (%s)", chart_type, filepath, title)

    return {
        "chart_path": os.path.abspath(filepath),
        "chart_url": f"/charts/{fname}",
        "chart_type": chart_type,
        "title": title,
        "file_size_kb": round(os.path.getsize(filepath) / 1024, 1) if os.path.exists(filepath) else 0,
    }


def clean_old_charts(max_age_hours: int = 24):
    """清理过期图表文件"""
    try:
        import time
        charts_dir = _ensure_charts_dir()
        now = time.time()
        removed = 0
        for fname in os.listdir(charts_dir):
            fpath = os.path.join(charts_dir, fname)
            if os.path.isfile(fpath) and fname.endswith(".png"):
                age_hours = (now - os.path.getmtime(fpath)) / 3600
                if age_hours > max_age_hours:
                    os.remove(fpath)
                    removed += 1
        if removed:
            logger.info("[Visualizer] 清理了 %d 个过期图表", removed)
    except Exception as e:
        logger.warning("[Visualizer] 清理图表异常: %s", e)
