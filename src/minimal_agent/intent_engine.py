"""
第五阶段核心：意图分类 + 关键词规则 + 参数提取 + 任务模板映射
===========================================================
将用户口语指令自动拆解为结构化意图，绑定对应 API 与输出范式。

流程:
  1. 预处理 — 清洗、归一化口语语句
  2. 意图识别 — 关键词规则 + 加权打分
  3. 参数提取 — 产品/时间/仓库实体抽取
  4. 模板匹配 — 意图 → API 绑定 + 输出 Schema
  5. 增强提示 — 将结构化意图拼接为 LLM 输入
"""

import re
import logging
from dataclasses import dataclass, field
from typing import Optional

from .reflection import ConfidenceLevel, get_confidence_level

logger = logging.getLogger(__name__)


# ============================================================
# 1. 意图定义 — 关键词规则 + 权重
# ============================================================

INTENT_DEFINITIONS = {
    "库存查询": {
        "keywords": [
            "库存", "存货", "存量", "还有多少", "还剩", "库存量",
            "仓", "仓库", "存了多少", "还有货吗", "缺货", "补货",
            "变动", "盘点",
            "inventory", "stock",
        ],
        "weight": 1.2,
        "description": "查询产品库存数量、存放位置和状态",
    },
    "财务报告": {
        "keywords": [
            "财务", "营收", "收入", "利润", "成本", "盈利",
            "财报", "财务报表", "利润率", "亏损", "毛利", "净利",
            "赚了", "花了", "收益", " revenue", "profit",
        ],
        "weight": 1.0,
        "description": "查询营收、利润、成本等财务指标",
    },
    "销售分析": {
        "keywords": [
            "销售", "销量", "卖出", "出售", "热销", "畅销",
            "卖了多少", "销售数据", "销售额", "卖得", "销售情况",
            "sales", "sold",
        ],
        "weight": 1.0,
        "description": "分析产品销售数据和趋势",
    },
    "综合简报": {
        "keywords": [
            "简报", "汇报", "周报", "月报", "概况", "总体情况",
            "综合", "总览", "概览", "总结", "全部", "所有",
            "全面", "全面分析", "经营状况", "经营",
            "brief", "overview", "summary",
        ],
        "weight": 1.3,
        "description": "生成综合业务概况报告",
    },
    "对比分析": {
        "keywords": [
            "对比", "比较", "哪个好", "优于", "差于", "差异",
            "区别", " versus", "哪个更", "相比", "差别", "优劣",
        ],
        "weight": 1.1,
        "description": "对比多个产品或时间段的关键指标",
    },
    "数据分析": {
        "keywords": [
            "趋势", "增长率", "变化", "指标", "图表", "走势",
            "上升", "下降", "增长", "减少", "波动", "环比", "同比",
        ],
        "weight": 1.0,
        "description": "分析业务数据趋势和关键指标",
    },
}

DEFAULT_INTENT = "default"

# 置信度等级阈值（与 reflection.ConfidenceLevel 保持一致）
CONFIDENCE_THRESHOLDS = {
    "high": 0.5,
    "medium": 0.2,
    "low": 0.0,
}

# ============================================================
# 2. 实体提取模式
# ============================================================

# 产品名称映射（输入文本 → 标准化产品名）
PRODUCT_PATTERNS = [
    (r"A\s*产?品", "A 产品"),
    (r"产?品\s*A", "A 产品"),
    (r"B\s*产?品", "B 产品"),
    (r"产?品\s*B", "B 产品"),
    (r"C\s*产?品", "C 产品"),
    (r"产?品\s*C", "C 产品"),
    (r"D\s*产?品", "D 产品"),
    (r"产?品\s*D", "D 产品"),
    # 独立字母匹配：处理 "D和C对比" 这类口语省略场景
    (r"(?<![A-Za-z])A(?![A-Za-z])", "A 产品"),
    (r"(?<![A-Za-z])B(?![A-Za-z])", "B 产品"),
    (r"(?<![A-Za-z])C(?![A-Za-z])", "C 产品"),
    (r"(?<![A-Za-z])D(?![A-Za-z])", "D 产品"),
]

# 时间映射
TIME_PATTERNS = [
    (r"2025[年\-/_]?0?1[月]?|1[月]|一月|1月份", "2025-01"),
    (r"2025[年\-/_]?0?2[月]?|2[月]|二月|2月份", "2025-02"),
    (r"2025[年\-/_]?0?3[月]?|3[月]|三月|3月份", "2025-03"),
    (r"2025[年\-/_]?0?4[月]?|4[月]|四月|4月份", "2025-04"),
    (r"2025[年\-/_]?0?5[月]?|5[月]|五月|5月份", "2025-05"),
    (r"2025[年\-/_]?0?6[月]?|6[月]|六月|6月份", "2025-06"),
    (r"2025[年\-/_]?0?7[月]?|7[月]|七月|7月份", "2025-07"),
    (r"2025[年\-/_]?0?8[月]?|8[月]|八月|8月份", "2025-08"),
    (r"2025[年\-/_]?0?9[月]?|9[月]|九月|9月份", "2025-09"),
    (r"2025[年\-/_]?10[月]?|10[月]|十月|10月份", "2025-10"),
    (r"2025[年\-/_]?11[月]?|11[月]|十一月|11月份", "2025-11"),
    (r"2025[年\-/_]?12[月]?|12[月]|十二月|12月份", "2025-12"),
    (r"第一季|1季度|Q1|第1季|一季度", "2025-Q1"),
    (r"第二季|2季度|Q2|第2季|二季度", "2025-Q2"),
    (r"第三季|3季度|Q3|第3季|三季度", "2025-Q3"),
    (r"第四季|4季度|Q4|第4季|四季度", "2025-Q4"),
    (r"今年|全年|年度|整年|全年度", "all"),
    (r"current|最近", "latest"),
]

# 仓库提取
WAREHOUSE_PATTERNS = [
    (r"上海仓", "上海仓"),
    (r"北京仓", "北京仓"),
    (r"深圳仓", "深圳仓"),
    (r"广州仓", "广州仓"),
    (r"成都仓", "成都仓"),
]

# 停用词（预处理时移除）
STOP_WORDS = {
    "帮我", "请", "一下", "我想", "我要", "我需要", "看看",
    "查查", "查一下", "给我", "能不能", "可以", "吗", "吧",
    "呢", "啊", "呀", "的", "了", "哦",
}

# ============================================================
# 3. 任务模板定义
# ============================================================

TASK_TEMPLATES = {
    "库存查询": {
        "description": "查询指定产品的库存数量、存放仓库和库存状态",
        "api_bindings": [
            {"api": "query_inventory", "param_map": {"product": "product_name"}},
        ],
        "required_params": ["product"],
        "optional_params": [],
        "output_schema": {
            "type": "object",
            "properties": {
                "产品名称": "string — 产品名称",
                "库存数量": "string — 当前库存量（含单位）",
                "存放仓库": "string — 仓库位置",
                "库存状态": "string — 充足(>100) / 紧张(>50) / 缺货(<=50)",
                "补货建议": "string — 基于库存状态的建议",
            },
        },
        "analysis_prompt": "根据库存数据判断库存状态（>100为充足，>50为紧张，≤50为缺货），给出补货建议。",
        "output_example": {
            "产品名称": "A 产品",
            "库存数量": "150 件",
            "存放仓库": "上海仓",
            "库存状态": "充足",
            "补货建议": "当前库存充足，建议保持常规补货节奏。",
        },
    },
    "财务报告": {
        "description": "查询营业收入、成本、利润和利润率等财务指标",
        "api_bindings": [
            {"api": "get_financial_report", "param_map": {"time": "month"}},
        ],
        "required_params": ["time"],
        "optional_params": [],
        "output_schema": {
            "type": "object",
            "properties": {
                "报告期": "string — 月份或季度",
                "营业收入": "string — 营收金额（元）",
                "营业成本": "string — 成本金额（元）",
                "利润": "string — 利润金额（元）",
                "利润率": "string — 百分比",
                "分析结论": "string — 财务表现简评",
            },
        },
        "analysis_prompt": "分析财务数据，关注营收变化、成本控制和利润率趋势，给出财务健康度评估。",
        "output_example": {
            "报告期": "2025-03",
            "营业收入": "5,100,000 元",
            "营业成本": "2,900,000 元",
            "利润": "2,200,000 元",
            "利润率": "43.1%",
            "分析结论": "3月营收环比增长，利润率保持在健康水平。",
        },
    },
    "销售分析": {
        "description": "查询产品销量数据和销售趋势",
        "api_bindings": [
            {"api": "get_sales_summary", "param_map": {"product": "product_name", "time": "period"}},
        ],
        "required_params": ["product"],
        "optional_params": ["time"],
        "output_schema": {
            "type": "object",
            "properties": {
                "产品": "string — 产品名称",
                "销售周期": "string — 查询的时间范围",
                "总销量": "string — 总销售数量（件）",
                "月度明细": "object — 各月销量明细",
                "趋势判断": "string — 增长/下降/波动",
            },
        },
        "analysis_prompt": "分析销售数据，判断销售趋势（增长/下降），给出改进建议。",
        "output_example": {
            "产品": "A 产品",
            "销售周期": "2025年Q1",
            "总销量": "4,050 件",
            "月度明细": {"2025-01": 1200, "2025-02": 1350, "2025-03": 1500},
            "趋势判断": "逐月增长，趋势向好",
            "改进建议": "保持当前营销力度，建议关注B产品增长空间。",
        },
    },
    "综合简报": {
        "description": "生成包含库存、财务、销售全维度的综合业务简报",
        "api_bindings": [
            {"api": "query_inventory", "param_map": {}},
            {"api": "get_financial_report", "param_map": {"time": "month"}},
            {"api": "get_sales_summary", "param_map": {"product": "product_name", "time": "period"}},
        ],
        "required_params": [],
        "optional_params": ["time", "product"],
        "output_schema": {
            "type": "object",
            "properties": {
                "报告标题": "string",
                "生成时间": "string",
                "核心观点": "string — 一句话总结",
                "库存概况": "object — 各产品库存状态",
                "财务概况": "object — 关键财务指标",
                "销售概况": "object — 各产品销售情况",
                "风险提示": "array[string] — 发现的风险点",
                "行动建议": "array[string] — 建议的行动项",
            },
        },
        "analysis_prompt": "综合库存、财务、销售三方面数据，生成一份全面的业务简报。重点关注异常数据和跨域关联分析。",
        "output_example": {
            "报告标题": "2025年Q1 综合业务简报",
            "核心观点": "Q1整体表现良好，营收和销售均呈增长态势，但D产品需关注库存问题。",
            "库存概况": {"A产品": "充足", "B产品": "充足", "C产品": "紧张", "D产品": "缺货"},
            "风险提示": ["D产品已缺货，需紧急补货"],
            "行动建议": ["安排D产品补货", "评估C产品库存策略"],
        },
    },
    "对比分析": {
        "description": "对比多个产品或时间段的业务数据",
        "api_bindings": [
            {"api": "get_sales_summary", "param_map": {"product1": "product_name"}},
            {"api": "get_sales_summary", "param_map": {"product2": "product_name"}},
        ],
        "required_params": [],
        "optional_params": ["product1", "product2", "time"],
        "output_schema": {
            "type": "object",
            "properties": {
                "对比对象": "string — 对比的双方",
                "对比维度": "string — 销量/库存/财务等",
                "对比结果": "object — 详细对比数据",
                "分析结论": "string — 谁优谁劣及原因",
                "建议": "string — 基于对比的建议",
            },
        },
        "analysis_prompt": "从销量、增长率等维度全面对比，找出差异原因并给出建议。",
        "output_example": {
            "对比对象": "A 产品 vs B 产品",
            "对比维度": "2025 Q1 销量",
            "对比结果": {"A 产品": "4,050 件", "B 产品": "2,820 件", "差距": "A 产品领先 43.6%"},
            "分析结论": "A产品在市场上表现更好，B产品有增长空间",
            "建议": "加大B产品营销投入",
        },
    },
    "数据分析": {
        "description": "分析业务数据趋势和关键指标",
        "api_bindings": [
            {"api": "get_financial_report", "param_map": {"time": "month"}},
            {"api": "get_sales_summary", "param_map": {"product": "product_name", "time": "period"}},
        ],
        "required_params": [],
        "optional_params": ["product", "time"],
        "output_schema": {
            "type": "object",
            "properties": {
                "分析主题": "string",
                "关键指标": "object — 核心数据指标",
                "趋势分析": "string — 数据反映的趋势",
                "洞察发现": "array[string] — 数据分析发现",
                "建议": "array[string] — 基于数据的建议",
            },
        },
        "analysis_prompt": "深入分析数据背后的趋势和模式，提供数据驱动的洞察和建议。",
        "output_example": {
            "分析主题": "Q1 业务数据分析",
            "关键指标": {"总营收": "13,100,000 元", "总利润": "5,400,000 元", "平均利润率": "41.2%"},
            "趋势分析": "Q1营收逐月增长，3月达到峰值510万",
            "洞察发现": ["营收连续3月增长", "利润率稳定在40%左右"],
            "建议": ["关注成本控制", "加大C产品推广"],
        },
    },
}


# ============================================================
# 预处理
# ============================================================

def preprocess(text: str) -> str:
    """清洗和归一化用户输入"""
    if not text:
        return ""

    original = text

    # 统一大小写
    text = text.strip()

    # 移除口语停用词（只移除开头和结尾的）
    for word in STOP_WORDS:
        # 开头的停用词
        if text.startswith(word):
            text = text[len(word):].strip()
        # 结尾的停用词
        if text.endswith(word):
            text = text[: -len(word)].strip()

    # 合并多余空白
    text = re.sub(r"\s+", " ", text)

    logger.debug("[Intent] 预处理: '%s' -> '%s'", original, text)
    return text


# ============================================================
# 意图识别
# ============================================================

def classify_intent(text: str) -> tuple[str, float, list[str]]:
    """
    基于关键词规则进行意图分类。

    Returns:
        (intent_name, score, matched_keywords)
    """
    text_lower = text.lower()
    scores = {}

    for intent_name, definition in INTENT_DEFINITIONS.items():
        matched = []
        for kw in definition["keywords"]:
            # 大小写不敏感匹配
            if kw.lower() in text_lower:
                matched.append(kw)

        if matched:
            raw_score = len(matched) / max(len(definition["keywords"]), 1)
            weighted_score = raw_score * definition["weight"]
            # 加一个匹配数量的 bonus，确保更多匹配的意图得分更高
            count_bonus = len(matched) * 0.05
            scores[intent_name] = weighted_score + count_bonus

    if not scores:
        return DEFAULT_INTENT, 0.0, []

    # 按得分排序，取最高分
    best_intent = max(scores, key=scores.get)
    best_score = scores[best_intent]
    best_keywords = [
        kw for kw in INTENT_DEFINITIONS[best_intent]["keywords"]
        if kw.lower() in text_lower
    ]

    logger.debug("[Intent] 分类: intent=%s, score=%.2f, keywords=%s",
                  best_intent, best_score, best_keywords)
    return best_intent, round(best_score, 3), best_keywords


# ============================================================
# 参数提取
# ============================================================

def extract_params(text: str, intent: str) -> dict:
    """从文本中提取结构化参数"""
    params = {}
    text_lower = text.lower()

    # 提取产品名称
    products = []
    for pattern, product_name in PRODUCT_PATTERNS:
        if re.search(pattern, text):
            if product_name not in products:
                products.append(product_name)
    if products:
        params["product"] = products[0]
        if len(products) > 1:
            params["product1"] = products[0]
            params["product2"] = products[1]

    # 提取时间
    for pattern, time_val in TIME_PATTERNS:
        if re.search(pattern, text_lower):
            params["time"] = time_val
            break

    # 提取仓库
    for pattern, warehouse in WAREHOUSE_PATTERNS:
        if re.search(pattern, text):
            params["warehouse"] = warehouse
            break

    # 对比分析特化处理：检测两个产品
    if intent == "对比分析" and "product" in params and "product2" not in params:
        # 尝试提取第二组产品
        remaining = text
        for p in params.get("product", "").split(","):
            p = p.strip()
            remaining = remaining.replace(p, "", 1)
        for pattern, product_name in PRODUCT_PATTERNS:
            if product_name not in params.values() and re.search(pattern, remaining):
                if "product1" not in params:
                    params["product1"] = params.pop("product", product_name)
                params["product2"] = product_name
                break

    logger.debug("[Intent] 参数提取: %s", params)
    return params


# ============================================================
# 模板匹配
# ============================================================

def get_template(intent: str) -> Optional[dict]:
    """获取意图对应的任务模板"""
    template = TASK_TEMPLATES.get(intent)
    if template:
        logger.debug("[Intent] 匹配模板: %s", intent)
    else:
        logger.debug("[Intent] 未找到模板 for intent: %s", intent)
    return template


# ============================================================
# API 参数字典构建
# ============================================================

def build_api_args(api_binding: dict, params: dict) -> dict:
    """
    根据模板的 api_binding 和提取的 params，构建 API 参数字典。

    Example:
        api_binding = {"api": "query_inventory", "param_map": {"product": "product_name"}}
        params = {"product": "A 产品"}
        -> {"product_name": "A 产品"}
    """
    param_map = api_binding.get("param_map", {})
    args = {}
    for our_key, api_key in param_map.items():
        val = params.get(our_key)
        if val:
            args[api_key] = val
    return args


# ============================================================
# IntentResult — 意图识别结果
# ============================================================

@dataclass
class IntentResult:
    """意图分析的完整结果"""
    intent: str                              # 意图名称
    confidence: float                        # 置信度
    params: dict                             # 提取的参数
    template: Optional[dict]                 # 匹配的模板
    matched_keywords: list[str]              # 匹配的关键词
    original_input: str                      # 原始用户输入
    cleaned_input: str                       # 预处理后的输入
    api_tasks: list[dict] = field(default_factory=list)  # 待执行的 API 任务

    @property
    def is_recognized(self) -> bool:
        """是否有明确的意图识别结果"""
        return self.intent != DEFAULT_INTENT and self.confidence > 0

    @property
    def recognition_status(self) -> str:
        """
        返回识别状态: 'recognized' | 'ambiguous' | 'unrecognized'
          - recognized:  置信度 >= 0.5（高置信度）
          - ambiguous:   置信度 0.2 ~ 0.5（中置信度，可继续处理但需注意）
          - unrecognized: 置信度 < 0.2（低置信度或默认意图）
        """
        if self.intent == DEFAULT_INTENT or self.confidence <= 0:
            return "unrecognized"
        level = get_confidence_level(self.confidence)
        if level == ConfidenceLevel.HIGH:
            return "recognized"
        elif level == ConfidenceLevel.MEDIUM:
            return "ambiguous"
        return "unrecognized"

    def missing_params(self) -> list[str]:
        """列出缺失的必填参数"""
        if not self.template:
            return []
        required = self.template.get("required_params", [])
        return [p for p in required if p not in self.params]

    def summarize(self) -> str:
        """返回用户可读的意图分析摘要"""
        parts = [f"[意图识别] 类别: {self.intent} (置信度: {self.confidence:.0%})"]
        if self.matched_keywords:
            parts.append(f"  触发词: {', '.join(self.matched_keywords[:5])}")
        if self.params:
            param_str = ", ".join(f"{k}={v}" for k, v in self.params.items())
            parts.append(f"  提取参数: {param_str}")
        if self.api_tasks:
            apis = [t["api"] for t in self.api_tasks if "api" in t]
            parts.append(f"  执行计划: {', '.join(apis)}")
        return "\n".join(parts)


# ============================================================
# IntentEngine — 编排引擎
# ============================================================

class IntentEngine:
    """意图引擎：编排预处理 → 分类 → 提取 → 模板匹配全流程"""

    def __init__(self):
        self._history: list[IntentResult] = []

    def get_confidence_level(self, score: float) -> ConfidenceLevel:
        """将浮点置信度分数映射到 ConfidenceLevel"""
        return get_confidence_level(score)

    def process(self, text: str) -> IntentResult:
        """
        对用户输入执行完整的意图分析流程。

        Args:
            text: 用户原始输入

        Returns:
            IntentResult 包含完整的分析结果
        """
        # Step 1: 预处理
        cleaned = preprocess(text)

        # Step 2: 意图分类
        intent, confidence, keywords = classify_intent(cleaned)

        # Step 3: 参数提取
        params = extract_params(cleaned, intent)

        # Step 4: 模板匹配
        template = get_template(intent)

        # Step 5: 构建 API 任务列表
        api_tasks = []
        if template:
            for binding in template.get("api_bindings", []):
                args = build_api_args(binding, params)
                api_tasks.append({
                    "api": binding["api"],
                    "args": args,
                    "param_map": binding.get("param_map", {}),
                })

        result = IntentResult(
            intent=intent,
            confidence=confidence,
            params=params,
            template=template,
            matched_keywords=keywords,
            original_input=text,
            cleaned_input=cleaned,
            api_tasks=api_tasks,
        )

        self._history.append(result)
        logger.info("[IntentEngine] %s", result.summarize().replace("\n", " | "))
        return result

    def build_enhanced_prompt(self, result: IntentResult) -> str:
        """
        将意图分析结果拼接为增强提示词，注入 LLM 指导其按模板输出。

        生成的提示包含:
          - 意图识别摘要
          - API 调用结果（如果有）
          - 模板输出 schema
          - 分析指导
        """
        parts = []

        # ---- 意图分析结果 ----
        parts.append("【意图识别结果】")
        parts.append(f"业务类别: {result.intent}（置信度: {result.confidence:.0%}）")
        if result.matched_keywords:
            parts.append(f"触发关键词: {', '.join(result.matched_keywords[:5])}")
        if result.params:
            param_str = "、".join(f"{k}={v}" for k, v in result.params.items())
            parts.append(f"提取参数: {param_str}")
        parts.append("")

        # ---- API 执行计划 ----
        if result.api_tasks:
            parts.append("【自动执行计划】")
            for i, task in enumerate(result.api_tasks, 1):
                api_name = task["api"]
                args = task.get("args", {})
                if args:
                    args_str = ", ".join(f"{k}={v}" for k, v in args.items())
                    parts.append(f"  {i}. 调用 {api_name}({args_str}) 获取数据")
                else:
                    parts.append(f"  {i}. 调用 {api_name}() 获取数据（使用全部可用数据）")
            parts.append("")

        # ---- 模板输出格式 ----
        if result.template:
            schema = result.template.get("output_schema", {})
            parts.append("【输出格式要求】")
            parts.append("请严格按照以下 schema 输出 JSON：")
            props = schema.get("properties", {})
            for field_name, field_desc in props.items():
                parts.append(f"  - {field_name}: {field_desc}")
            if "analysis_prompt" in result.template:
                parts.append("")
                parts.append(f"【分析指导】\n{result.template['analysis_prompt']}")

        return "\n".join(parts)

    def get_recent_history(self, limit: int = 5) -> list[IntentResult]:
        """获取最近的意图分析历史"""
        return self._history[-limit:]

    def clear_history(self):
        """清空历史"""
        self._history.clear()
