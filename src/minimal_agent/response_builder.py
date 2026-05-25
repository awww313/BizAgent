"""
响应构建器：澄清问题 / 数据缺失通知 / 置信度警告
==================================================
当反射管线检测到异常时，使用本模块生成面向用户的友好响应。
"""

import logging
from .reflection import ConfidenceLevel

logger = logging.getLogger(__name__)


def get_system_capabilities() -> str:
    """返回系统能力描述文本"""
    return (
        "- 库存查询：查看各产品库存数量、仓库位置和状态\n"
        "- 财务报告：查询营收、成本、利润和利润率\n"
        "- 销售分析：查看产品销售数据、趋势和对比\n"
        "- 环比分析：逐月财务指标变化趋势\n"
        "- 产品对比：多产品销量对比\n"
        "- 极值分析：销量最高/最低产品\n"
        "- 趋势判断：自动识别增长/下降趋势\n"
        "- 员工查询：部门分布、薪资统计\n"
        "- 客户分析：分层分析、地区分布\n"
        "- 综合简报：一键生成全局经营简报\n"
        "- 图表可视化：自动生成趋势图、对比图、分布图"
    )


def build_clarifying_response(intent_name: str, confidence: float) -> dict:
    """
    当意图置信度低于阈值时，返回澄清引导响应。
    向用户列出系统能力，引导其重新表述问题。
    """
    return {
        "status": "success",
        "data": {
            "answer": (
                "我没有完全理解您的问题。"
                " 目前我可以帮您做以下事情：\n\n"
                f"{get_system_capabilities()}\n\n"
                "请重新描述您的问题，例如：\n"
                "- 「上个月营收多少」\n"
                "- 「A产品的库存情况」\n"
                "- 「第一季度销售趋势」"
            ),
            "need_clarification": True,
        },
        "reflection": {
            "l1_passed": False,
            "l1_score": confidence,
            "intent": intent_name,
        },
    }


def build_insufficient_data_response(api_names: list[str], notes: list[str]) -> dict:
    """
    当 API 返回数据不足时，返回透明告知响应。
    向用户说明哪些数据缺失，引导调整查询范围。

    Args:
        api_names: 缺失数据的 API 名称列表
        notes: 对应的缺失原因列表
    """
    details = "\n".join(
        [f"- {name}: {note}" for name, note in zip(api_names, notes)]
    )
    return {
        "status": "success",
        "data": {
            "answer": (
                "抱歉，目前系统中缺少部分数据，无法完成完整的分析：\n\n"
                f"{details}\n\n"
                "您可以尝试查询其他数据，或联系管理员补充数据。"
            ),
            "insufficient_data": True,
        },
        "reflection": {
            "l2_passed": False,
            "insufficient_apis": api_names,
        },
    }


def attach_confidence_warning(answer: str, level: ConfidenceLevel) -> str:
    """在回答末尾附加置信度警告文本。"""
    suffix_map = {
        ConfidenceLevel.LOW: "（部分数据可能不完整，建议核实后使用）",
        ConfidenceLevel.UNKNOWN: "（数据可靠性未经验证，请谨慎参考）",
    }
    suffix = suffix_map.get(level)
    if suffix:
        answer = answer.rstrip() + "\n\n" + suffix
    return answer
