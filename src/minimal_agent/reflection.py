"""
多层反思校验机制：意图置信度 / 数据充分性 / 事实验证 / 行动决策
==============================================================
在 chat_with_analysis 主流程中插入四个检查点：
  L1 — 意图置信度 + 参数完整性
  L2 — API 返回数据充分性
  L3 — LLM 输出事实交叉核验
  L4 — 综合评分 → 行动决策
"""

import re
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Any

logger = logging.getLogger(__name__)

# ============================================================
# 枚举 & 数据类
# ============================================================

class ConfidenceLevel(Enum):
    HIGH = "high"        # >= 0.5
    MEDIUM = "medium"    # >= 0.2
    LOW = "low"          # < 0.2
    UNKNOWN = "unknown"  # default 意图


def get_confidence_level(score: float) -> ConfidenceLevel:
    """将浮点分数映射到 ConfidenceLevel 枚举"""
    if score >= 0.5:
        return ConfidenceLevel.HIGH
    elif score >= 0.2:
        return ConfidenceLevel.MEDIUM
    elif score > 0:
        return ConfidenceLevel.LOW
    return ConfidenceLevel.UNKNOWN


class DataSufficiency(Enum):
    SUFFICIENT = "sufficient"
    PARTIAL = "partial"
    INSUFFICIENT = "insufficient"
    EMPTY = "empty"


@dataclass
class LayerResult:
    """单层反射检查结果"""
    layer_id: int
    passed: bool
    score: float = 0.0
    issues: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass
class ReflectionResult:
    """四层反射的综合结果"""
    l1: LayerResult
    l2: LayerResult
    l3: Optional[LayerResult] = None
    l4: Optional[LayerResult] = None
    overall_confidence: float = 1.0

    @property
    def needs_clarification(self) -> bool:
        """L1 严重失败 → 需要追问用户"""
        return not self.l1.passed and self.l1.score < 0.2

    @property
    def needs_regeneration(self) -> bool:
        """L3 存在幻觉 → 需要重新生成"""
        return self.l3 is not None and not self.l3.passed

    @property
    def should_attach_warning(self) -> bool:
        """整体置信度偏低 → 附加警告"""
        return self.overall_confidence < 0.4

    @property
    def should_downgrade_status(self) -> bool:
        """整体置信度极低 → 降级 status"""
        return self.overall_confidence < 0.2

    @property
    def warnings(self) -> list[str]:
        ws = []
        if self.needs_clarification:
            ws.append("意图不明确，建议用户重新表述")
        if self.needs_regeneration and self.l3:
            ws.extend(self.l3.issues)
        if self.should_attach_warning:
            ws.append("整体置信度偏低，部分数据可能不准确")
        return ws


# ============================================================
# 数字实体提取 & 事实比对
# ============================================================

_NUMBER_PATTERN = re.compile(r"([\d,]+(?:\.\d+)?)")


def _extract_numeric_claims(text: str) -> list[dict]:
    """从自然语言文本中提取带上下文的数字声明"""
    claims = []
    # 注意：　 是全角空格，\s 不匹配，所以用 [\s　] 或 [：:：]
    # 分隔符可能为 ：: 空格 全角空格 或直接相连
    _sep = r"[：:\s　]*[约为达]?"
    patterns = [
        # "总销售额：2297200.86" 或 "销售额 836154.0" 或 "销售 100万"
        (rf"(?:总)?(?:销量?|销售(?:额)?){_sep}\s*([\d,]+(?:\.\d+)?)", "sales"),
        # "总利润：286397.02" 或 "（利润 145455.0）" 或 "盈利 50000"
        (rf"(?:总)?(?:利润|盈利)(?:额|)?{_sep}\s*([\d,]+(?:\.\d+)?)", "profit"),
        # "平均利润率：12.03" 或 "毛利率 15.5%"
        (rf"(?:平均)?(?:利润率|毛利率){_sep}\s*([\d,]+(?:\.\d+)?)\s*%?", "margin_pct"),
        # "占比 23.5%" / "份额 10%"
        (rf"(?:占比|份额){_sep}\s*([\d,]+(?:\.\d+)?)\s*%", "share_pct"),
        # "增长 12.5%" / "下降 3.2%"
        (rf"(?:增长|下降|涨幅|跌幅){_sep}\s*([\d,]+(?:\.\d+)?)\s*%", "growth_pct"),
        # "营收 500万" / "收入 1000"
        (rf"(?:营收|收入|营业收入){_sep}\s*([\d,]+(?:\.\d+)?)", "revenue"),
        # "成本 300" / "营业成本 200"
        (rf"(?:成本|营业成本){_sep}\s*([\d,]+(?:\.\d+)?)", "cost"),
    ]
    for pattern, label in patterns:
        for m in re.finditer(pattern, text):
            raw = m.group(1).replace(",", "")
            try:
                val = float(raw)
                claims.append({"label": label, "text": m.group(0), "value": val})
            except ValueError:
                continue
    return claims


_API_VALUE_MAP = {
    # 企业版路径
    "sales":   ("get_sales_summary",    "data", "total_sales"),
    "profit":  ("get_financial_report", "data", "profit"),
    "revenue": ("get_financial_report", "data", "revenue"),
    "cost":    ("get_financial_report", "data", "cost"),
    "stock":   ("query_inventory",      "data", "stock"),
    "margin_pct": ("get_financial_report", "data", "margin"),
}

_SUPERSTORE_VALUE_MAP = {
    "sales":       ("superstore_overview", "data", "total_sales"),
    "profit":      ("superstore_overview", "data", "total_profit"),
    "margin_pct":  ("superstore_overview", "data", "avg_margin_pct"),
    "cost":        ("superstore_overview", "data", "total_cost"),
}


def _lookup_api_value(api_results: dict, label: str, is_superstore: bool = False) -> Optional[float]:
    """将文本标签映射回 API 原始数值"""
    map_ = _SUPERSTORE_VALUE_MAP if is_superstore else _API_VALUE_MAP
    path = map_.get(label)
    if not path:
        return None
    obj = api_results.get(path[0], {})
    for key in path[1:]:
        if isinstance(obj, dict):
            obj = obj.get(key, {})
        else:
            return None
    return float(obj) if isinstance(obj, (int, float)) else None


def _fact_check(llm_answer: str, api_results: dict, is_superstore: bool = False) -> list[dict]:
    """交叉核验 LLM 输出与 API 原始数据，返回不匹配项列表"""
    mismatches = []
    claims = _extract_numeric_claims(llm_answer)
    for claim in claims:
        api_val = _lookup_api_value(api_results, claim["label"], is_superstore=is_superstore)
        if api_val is None:
            continue
        llm_val = claim["value"]
        if api_val == 0:
            if llm_val != 0:
                mismatches.append({
                    "label": claim["label"],
                    "llm_value": llm_val,
                    "api_value": api_val,
                    "text": claim["text"],
                    "reason": "API 数据为 0 但 LLM 声称非零",
                })
        else:
            deviation = abs(llm_val - api_val) / api_val
            if deviation >= 0.10:
                mismatches.append({
                    "label": claim["label"],
                    "llm_value": llm_val,
                    "api_value": api_val,
                    "deviation": round(deviation, 3),
                    "text": claim["text"],
                    "reason": f"偏差 {deviation:.1%} >= 10%",
                })
    return mismatches


# ============================================================
# 反射管线
# ============================================================

class ReflectionPipeline:
    """四层反射管线 — 意图 → 数据 → 事实 → 行动"""

    # 权重配置
    W1 = 0.35  # 意图
    W2 = 0.30  # 数据
    W3 = 0.35  # 事实（基线 0.5，若未运行则视为 1.0）

    # 阈值
    CLARIFY_THRESHOLD = 0.2    # L1 低于此值 → 直接返回澄清
    WARNING_THRESHOLD = 0.4    # overall 低于此值 → 附加警告
    DOWNGRADE_THRESHOLD = 0.2  # overall 低于此值 → 降级 status

    @staticmethod
    def run_layer1(intent: str, confidence: float, params: dict, template: Optional[dict] = None) -> LayerResult:
        """
        L1 — 意图置信度 + 参数完整性检查。

        评分逻辑:
          - raw: min(1.0, confidence * 2) 将 0-0.5 区间映射到 0-1
          - 每项缺失必填参数减 0.2
          - intent == "default" 或 confidence <= 0 时 score = 0
        """
        issues = []

        if intent == "default" or confidence <= 0:
            return LayerResult(
                layer_id=1,
                passed=False,
                score=0.0,
                issues=["意图未识别（default）"],
                metadata={"intent": intent, "confidence": confidence},
            )

        score = min(1.0, confidence * 2.0)

        # 参数完整性
        if template:
            required = template.get("required_params", [])
            missing = [p for p in required if p not in params]
            if missing:
                penalty = len(missing) * 0.2
                score = max(0.0, score - penalty)
                issues.append(f"缺少必填参数: {', '.join(missing)}")

        passed = score >= ReflectionPipeline.CLARIFY_THRESHOLD
        if not passed:
            issues.append(f"意图置信度偏低 (score={score:.2f})")

        return LayerResult(
            layer_id=1,
            passed=passed,
            score=round(score, 3),
            issues=issues,
            metadata={"intent": intent, "confidence": confidence, "params": params},
        )

    @staticmethod
    def run_layer2(api_results: dict) -> LayerResult:
        """
        L2 — API 返回数据充分性检查。

        检查每项 API 结果:
          - _sufficiency 标记
          - note 字段
          - 结果是否为空 dict
        """
        issues = []
        sufficiency_map = {}
        total_score = 1.0
        result_count = len(api_results)

        if not api_results:
            return LayerResult(
                layer_id=2,
                passed=False,
                score=0.0,
                issues=["无 API 返回数据"],
                metadata={"api_sufficiency": {}, "result_count": 0},
            )

        score_sum = 0.0
        for api_name, data in api_results.items():
            sufficiency = data.get("_sufficiency", "full") if isinstance(data, dict) else "unknown"
            sufficiency_map[api_name] = sufficiency

            if sufficiency == "empty":
                score_sum += 0.0
                note = data.get("_note") or data.get("data", {}).get("note", "") if isinstance(data, dict) else ""
                issues.append(f"{api_name}: 无数据 — {note}" if note else f"{api_name}: 无数据")
            elif sufficiency == "partial":
                score_sum += 0.3
                issues.append(f"{api_name}: 数据不完整")
            else:
                score_sum += 1.0

        avg_score = score_sum / result_count if result_count else 0
        passed = avg_score >= 0.5

        return LayerResult(
            layer_id=2,
            passed=passed,
            score=round(avg_score, 3),
            issues=issues,
            metadata={"api_sufficiency": sufficiency_map, "result_count": result_count},
        )

    @staticmethod
    def run_layer3(llm_output: dict, api_results: dict, is_superstore: bool = False) -> LayerResult:
        """
        L3 — LLM 输出事实验证。

        从 answer 字段提取数字并与 API 原始数据交叉比对。
        偏差 >= 10% 标记为幻觉。
        is_superstore=True 时使用 superstore 数据路径匹配。
        """
        answer = ""
        if isinstance(llm_output, dict):
            answer = llm_output.get("answer", "") or llm_output.get("data", {}).get("answer", "") or str(llm_output)
        elif isinstance(llm_output, str):
            answer = llm_output

        mismatches = _fact_check(answer, api_results, is_superstore=is_superstore)

        if mismatches:
            issues = [f"事实不匹配: {m['text']} (LLM={m.get('llm_value', '?')}, API={m.get('api_value', '?')}, {m['reason']})" for m in mismatches]
            score = max(0.0, 1.0 - len(mismatches) * 0.3)
            return LayerResult(
                layer_id=3,
                passed=False,
                score=round(score, 3),
                issues=issues,
                metadata={"mismatches": mismatches},
            )

        return LayerResult(
            layer_id=3,
            passed=True,
            score=1.0,
            issues=[],
            metadata={"mismatches": []},
        )

    @staticmethod
    def run_layer4(l1: LayerResult, l2: LayerResult, l3: Optional[LayerResult] = None) -> LayerResult:
        """
        L4 — 综合行动决策。

        overall = w1 * L1_score + w2 * L2_score + w3 * (L3_score 或 1.0)
        """
        l3_score = l3.score if l3 else 1.0
        weights = (ReflectionPipeline.W1, ReflectionPipeline.W2, ReflectionPipeline.W3)
        overall = weights[0] * l1.score + weights[1] * l2.score + weights[2] * l3_score

        issues = []
        if overall < ReflectionPipeline.WARNING_THRESHOLD:
            issues.append(f"整体置信度偏低 ({overall:.2f})，已附加置信度提示")
        if overall < ReflectionPipeline.DOWNGRADE_THRESHOLD:
            issues.append(f"整体置信度极低 ({overall:.2f})，已降级状态")

        return LayerResult(
            layer_id=4,
            passed=overall >= ReflectionPipeline.WARNING_THRESHOLD,
            score=round(overall, 3),
            issues=issues,
            metadata={
                "weights": list(weights),
                "l1_score": l1.score,
                "l2_score": l2.score,
                "l3_score": l3_score,
            },
        )

    @classmethod
    def run(cls, intent: str, confidence: float, params: dict,
            template: Optional[dict], api_results: dict,
            llm_output: Optional[dict] = None,
            skip_l3: bool = False,
            is_superstore: bool = False) -> ReflectionResult:
        """端到端执行四层反射"""
        l1 = cls.run_layer1(intent, confidence, params, template)
        l2 = cls.run_layer2(api_results)

        l3 = None
        if llm_output is not None and not skip_l3:
            l3 = cls.run_layer3(llm_output, api_results, is_superstore=is_superstore)
        elif not skip_l3:
            l3 = LayerResult(layer_id=3, passed=True, score=1.0)

        l4 = cls.run_layer4(l1, l2, l3)

        overall = l4.score
        return ReflectionResult(l1=l1, l2=l2, l3=l3, l4=l4, overall_confidence=overall)
