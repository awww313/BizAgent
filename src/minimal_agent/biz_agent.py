"""
第二阶段+第三阶段核心：三层 Prompt + JSON 约束 + Function Calling
==============================================================
BizAgent 整合了：
  1. 系统级 Prompt（角色定义）
  2. 任务级 Prompt（按任务类型切换 schema）
  3. 用户级 Prompt（动态拼接输入）
  4. Function Calling（模型 → 调用 Mock API → 格式化回复）
  + DeepSeek JSON mode 强制输出 JSON
  + 后端 JSON 解析与校验
  + 异常处理（非 JSON 输出捕获并重试）
"""

import json
import re
import inspect
import logging
from typing import Optional

import requests

from .prompts import BIZ_SYSTEM_PROMPT, TASK_PROMPTS
from .mock_enterprise_api import TOOL_DEFINITIONS, FUNCTION_MAP, INVENTORY_DB, FINANCIAL_DB, SALES_DB, EMPLOYEES_DB, CUSTOMERS_DB
from .context_manager import ContextManager, count_messages_tokens
from .task_tracker import TaskTracker
from .session_store import store as session_store
from .intent_engine import IntentEngine, IntentResult
from .analysis_ops import auto_analyze, financial_mom, financial_summary, sales_product_comparison, inventory_status_analysis
from .visualizer import auto_chart, line_chart, bar_chart, pie_chart, clean_old_charts
from .exceptions import (
    AgentError,
    ModelCallError,
    TimeoutError,
    AuthError,
    RateLimitError,
    FileParseError,
    DataNotFoundError,
    retry_with_backoff,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# 有工具模式的系统 Prompt
BIZ_SYSTEM_PROMPT_WITH_TOOLS = BIZ_SYSTEM_PROMPT + """

## Function Calling 能力
你可以调用企业内部数据接口来获取实时数据：
- query_inventory: 查询产品库存
- get_financial_report: 获取财务报表
- get_sales_summary: 获取销售汇总

当用户的问题涉及具体业务数据时，优先调用对应接口获取真实数据，不要编造。
获取到数据后，用自然语言向用户汇报结果，将数据融入叙述中。如果需要展示表格型数据，放在 details 字段中。"""


def _has_required_args(func, args: dict) -> tuple[bool, list[str]]:
    """检查参数字典是否满足函数必需的参数"""
    missing = []
    try:
        sig = inspect.signature(func)
        for name, param in sig.parameters.items():
            if param.kind in (inspect.Parameter.VAR_KEYWORD, inspect.Parameter.VAR_POSITIONAL):
                continue
            if param.default is inspect.Parameter.empty and name not in args:
                missing.append(name)
    except (ValueError, TypeError):
        pass  # 无法检查签名时放行
    return len(missing) == 0, missing



# 意图 → 相关分析类型映射（用于过滤图表和分析报告中的无关数据）
INTENT_ANALYSIS_MAP = {
    "库存查询": {"inventory_analysis"},
    "财务报告": {"financial_mom", "financial_summary"},
    "销售分析": {"sales_comparison", "sales_extreme"},
    "综合简报": {"financial_mom", "financial_summary", "sales_comparison", "sales_extreme", "inventory_analysis", "employee_headcount", "customer_tier_analysis"},
    "对比分析": {"sales_comparison"},
    "数据分析": {"financial_mom", "financial_summary", "sales_comparison", "sales_extreme"},
    "default": {"financial_mom", "financial_summary", "sales_comparison", "sales_extreme", "inventory_analysis"},
}


def _filter_analysis_by_intent(analysis_results: dict, intent: str) -> dict:
    """根据意图只保留相关的分析结果"""
    allowed = INTENT_ANALYSIS_MAP.get(intent, INTENT_ANALYSIS_MAP["default"])
    return {k: v for k, v in analysis_results.items() if k in allowed}


# API 调用结果过滤（防止非相关数据泄露到 LLM prompt）
INTENT_API_MAP = {
    "库存查询": {"query_inventory"},
    "财务报告": {"get_financial_report"},
    "销售分析": {"get_sales_summary"},
    "综合简报": {"query_inventory", "get_financial_report", "get_sales_summary"},
    "对比分析": {"get_sales_summary"},
    "数据分析": {"get_financial_report", "get_sales_summary"},
}


def _filter_api_by_intent(api_results: dict, intent: str) -> dict:
    """根据意图只保留相关的 API 返回数据"""
    allowed = INTENT_API_MAP.get(intent, set(api_results.keys()))
    return {k: v for k, v in api_results.items() if k in allowed}


class BizAgent:
    """商务智能 Agent：三层 Prompt + 结构化 JSON 输出 + Function Calling"""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str = "deepseek-chat",
        task_type: str = "default",
        max_retries: int = 2,
        tools: list = None,
        session_id: str = None,
        enable_tracking: bool = True,
        enable_persistence: bool = False,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.task_type = task_type
        self.max_retries = max_retries
        self.tools = tools or TOOL_DEFINITIONS
        self.session_id = session_id
        self._enable_tracking = enable_tracking
        self._enable_persistence = enable_persistence
        self._tracker: Optional[TaskTracker] = None
        self._intent_engine = IntentEngine()

    def _ensure_session(self):
        """确保 session 在持久化存储中存在"""
        if self._enable_persistence:
            if not self.session_id:
                self.session_id = self._get_tracker().session_id
            session_store.create_session(self.session_id)

    def _get_tracker(self) -> TaskTracker:
        """获取或创建当前会话的 TaskTracker"""
        if self._tracker is None:
            self._tracker = TaskTracker.get_or_create(self.session_id)
            if self.session_id is None:
                self.session_id = self._tracker.session_id
        return self._tracker

    def _save_messages_to_store(self, messages: list, task_id: str = None):
        """批量保存消息到持久化存储"""
        if not self._enable_persistence:
            return
        try:
            session_store.save_messages(self.session_id, messages, task_id=task_id)
        except Exception as e:
            logger.warning("[Persist] 保存消息失败: %s", e)

    def _build_data_context(self) -> str:
        """构建企业数据上下文，注入到系统 Prompt 中"""
        lines = ["\n## 企业数据（可直接用于回答）"]

        lines.append("\n### 库存数据")
        for pid, info in INVENTORY_DB.items():
            lines.append(f"- {info['product']}：库存 {info['stock']} {info['unit']}，位于 {info['warehouse']}")

        lines.append("\n### 财务数据")
        for month, info in FINANCIAL_DB.items():
            lines.append(f"- {month}：营收 {info['revenue']:,} 元，成本 {info['cost']:,} 元，利润 {info['profit']:,} 元，利润率 {info['margin']*100:.1f}%")

        lines.append("\n### 销售数据")
        for pid, sales in SALES_DB.items():
            details = "，".join([f"{m}: {v} 件" for m, v in sales.items() if m != "total"])
            lines.append(f"- {pid}：{details}，合计 {sales['total']} 件")

        lines.append("\n### 员工信息")
        dept_order = {}
        for emp in EMPLOYEES_DB.values():
            dept = emp["department"]
            if dept not in dept_order:
                dept_order[dept] = []
            dept_order[dept].append(emp)
        for dept, members in sorted(dept_order.items()):
            names = "、".join([f"{m['name']}({m['position']})" for m in members])
            lines.append(f"- {dept}（{len(members)}人）：{names}")

        lines.append("\n### 客户信息")
        for tier in ("VIP", "企业", "普通"):
            tier_clients = [c for c in CUSTOMERS_DB.values() if c["tier"] == tier]
            if tier_clients:
                names = "、".join([c["name"] for c in tier_clients])
                total = sum(c["total_spent"] for c in tier_clients)
                lines.append(f"- {tier}客户（{len(tier_clients)}家，累计消费 {total:,} 元）：{names}")

        return "\n".join(lines)

    def _build_messages(self, user_input: str) -> list:
        """构建三层 Prompt 消息"""
        data_context = self._build_data_context()
        system_prompt = BIZ_SYSTEM_PROMPT + data_context
        task_prompt = TASK_PROMPTS.get(self.task_type, TASK_PROMPTS["default"])
        user_prompt = f"【任务要求】\n{task_prompt}\n\n【用户输入】\n{user_input}"

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    def _call_api(self, messages: list, use_json_mode: bool = True, tools: list = None) -> dict:
        """调用大模型 API，含分级异常处理和追踪"""
        tracker = self._get_tracker() if self._enable_tracking else None
        step_id = None

        if tracker:
            content_summary = f"model={self.model}, messages={len(messages)}, tools={bool(tools)}, json_mode={use_json_mode}"
            step_id = tracker.start_step("api_call", content_summary)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.1,
        }
        if use_json_mode:
            payload["response_format"] = {"type": "json_object"}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=60,
            )

            if response.status_code == 401:
                raise AuthError(detail="API Key 无效或未配置")
            if response.status_code == 429:
                raise RateLimitError(detail=f"请求被限流: {response.text[:200]}")
            if response.status_code == 503:
                raise TimeoutError(detail="模型服务暂不可用")

            response.raise_for_status()
            result = response.json()

            if tracker and step_id:
                usage = result.get("usage", {})
                tracker.end_step("success",
                    f"tokens: {usage.get('total_tokens', '?')} "
                    f"(prompt={usage.get('prompt_tokens', '?')}, "
                    f"completion={usage.get('completion_tokens', '?')})"
                )
            return result

        except requests.exceptions.Timeout:
            if tracker and step_id:
                tracker.end_step("failed", "请求超时")
            raise TimeoutError(detail=f"请求超时 (60s)")
        except requests.exceptions.ConnectionError:
            if tracker and step_id:
                tracker.end_step("failed", "网络连接失败")
            raise ModelCallError(detail=f"无法连接到 {self.base_url}，请检查网络")
        except requests.exceptions.HTTPError as e:
            if tracker and step_id:
                tracker.end_step("failed", f"HTTP {e.response.status_code}")
            raise ModelCallError(detail=f"HTTP {e.response.status_code}: {e.response.text[:200]}")
        except (AgentError, requests.RequestException) as e:
            if tracker and step_id:
                tracker.end_step("failed", str(e)[:200])
            raise ModelCallError(detail=str(e)[:300])
        except Exception as e:
            if tracker and step_id:
                tracker.end_step("failed", str(e)[:200])
            logger.error("[API] 未预期的异常: %s", e, exc_info=True)
            raise ModelCallError(detail=f"未预期错误: {str(e)[:200]}")

    def _validate_json(self, raw: str) -> dict:
        """解析并校验 JSON（兼容 DeepSeek 末尾多余 } 的情况）"""
        if not raw:
            raise ValueError("空响应")

        code_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
        if code_match:
            raw = code_match.group(1).strip()

        raw = raw.strip()

        # DeepSeek JSON mode 偶尔会在末尾追加多余的 }
        for _ in range(5):
            try:
                parsed = json.loads(raw)
                if not isinstance(parsed, dict):
                    raise ValueError(f"返回不是 JSON 对象: {type(parsed)}")
                if "status" not in parsed:
                    parsed["status"] = "success"
                return parsed
            except json.JSONDecodeError as e:
                # 只有错误在末尾时才尝试移除多余的 }
                if raw.endswith("}") and e.pos >= len(raw) - 2:
                    raw = raw[:-1]  # 每次只移除一个字符
                else:
                    raise

        # 最后一次尝试
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError(f"返回不是 JSON 对象: {type(parsed)}")
        if "status" not in parsed:
            parsed["status"] = "success"
        return parsed

    def chat(self, user_input: str) -> dict:
        """
        普通对话：三层 Prompt + JSON 约束，不含 Function Calling。

        返回格式: {"status": "success"|"error", "data": {...}, "error": "..."}
        """
        self._ensure_session()
        messages = self._build_messages(user_input)
        logger.info("[BizAgent] chat: task_type=%s, input=%s...", self.task_type, user_input[:50])

        # 任务追踪
        tracker = self._get_tracker() if self._enable_tracking else None
        task_id = None
        if tracker:
            task_id = tracker.start_task(user_input[:200], mode="single")
            tracker.start_step("thought", f"开始处理 {self.task_type} 类型的请求")

        last_error = None
        for attempt in range(1 + self.max_retries):
            try:
                raw_resp = self._call_api(messages)
                raw_content = raw_resp["choices"][0]["message"]["content"]

                if tracker:
                    status = "success" if attempt == 0 else f"retry#{attempt}"
                    tracker.start_step("validate", f"JSON 校验 (attempt {attempt + 1})")

                result = self._validate_json(raw_content)

                if tracker:
                    tracker.end_step("success", "JSON 校验通过")
                    # 保存最终结果
                    if task_id:
                        tracker.end_task("success", str(result)[:200])
                        self._save_messages_to_store([
                            {"role": "user", "content": user_input},
                            {"role": "assistant", "content": json.dumps(result, ensure_ascii=False)},
                        ], task_id)

                return result

            except json.JSONDecodeError as e:
                last_error = "系统处理异常，请稍后重试。"
                logger.warning("[BizAgent] %s", last_error)
                if tracker:
                    tracker.start_step("retry", f"JSON 解析失败 (attempt {attempt + 1})，重试")
                messages.append({"role": "assistant", "content": raw_content})
                messages.append({
                    "role": "user",
                    "content": "输出不是合法的 JSON。请只输出合法的 JSON 对象，不要包含任何其他文字。",
                })

            except (requests.RequestException, KeyError, ModelCallError) as e:
                last_error = "系统处理异常，请稍后重试。"
                logger.error("[BizAgent] %s", last_error)
                break

            except ValueError as e:
                last_error = "系统处理异常，请稍后重试。"
                logger.warning("[BizAgent] %s", last_error)

        if tracker and task_id:
            tracker.end_task("failed", last_error)
            self._save_messages_to_store([
                {"role": "user", "content": user_input},
                {"role": "assistant", "content": json.dumps({"status": "error", "error": last_error}, ensure_ascii=False)},
            ], task_id)
        return {"status": "error", "error": "系统处理异常，请稍后重试。", "data": None}

    def chat_stream(self, topic: str) -> dict:
        """便捷方法：自动推断任务类型后调用 chat()"""
        topic_lower = topic.lower()
        if any(kw in topic_lower for kw in ["简报", "汇报", "周报", "月报"]):
            self.task_type = "简报"
        elif any(kw in topic_lower for kw in ["销售", "营收", " revenue", "sales"]):
            self.task_type = "销售分析"
        elif any(kw in topic_lower for kw in ["数据", "分析", "指标", "趋势"]):
            self.task_type = "数据分析"
        else:
            self.task_type = "default"

        return self.chat(topic)

    # ============================================================
    # Function Calling 核心逻辑
    # ============================================================

    def _execute_tool(self, tool_call: dict) -> str:
        """执行单个工具调用，返回 JSON 字符串结果"""
        func_name = tool_call["function"]["name"]
        func_args = json.loads(tool_call["function"]["arguments"])

        func = FUNCTION_MAP.get(func_name)
        if not func:
            return json.dumps({"error": f"未知工具: {func_name}"}, ensure_ascii=False)

        logger.info(f"[FunctionCall] -> {func_name}({func_args})")
        result = func(**func_args)
        result_str = json.dumps(result, ensure_ascii=False)
        logger.info(f"[FunctionCall] <- {result_str[:200]}")
        return result_str

    def chat_with_tools(self, user_input: str) -> dict:
        """
        Function Calling 对话：模型自动判断需要调用的企业 API，
        执行后返回结构化 JSON 结果。

        完整链路：
          用户请求 → 模型识别函数 → 调 Mock API → 模型格式化回复
        """
        self._ensure_session()
        data_context = self._build_data_context()
        messages = [
            {"role": "system", "content": BIZ_SYSTEM_PROMPT_WITH_TOOLS + data_context},
            {"role": "user", "content": user_input},
        ]

        logger.info("[BizAgent] chat_with_tools: input=%s...", user_input[:60])

        # 任务追踪
        tracker = self._get_tracker() if self._enable_tracking else None
        task_id = None
        if tracker:
            task_id = tracker.start_task(user_input[:200], mode="tools")

        try:
            # Step 1: 第一次调用，带工具定义
            if tracker:
                tracker.start_step("thought", "模型识别意图并判断是否需要调用工具")

            resp1 = self._call_api(messages, use_json_mode=False, tools=self.tools)
            msg1 = resp1["choices"][0]["message"]

            # 检查是否有 tool_calls
            if not msg1.get("tool_calls"):
                logger.info("[FunctionCall] 模型直接回复，无需调用工具")
                if tracker:
                    tracker.start_step("observation", "模型直接回答了问题，未调用工具")
                content = msg1.get("content", "")
                try:
                    result = self._validate_json(content)
                    if tracker:
                        tracker.end_step("success", "JSON 校验通过")
                        tracker.end_task("success", str(result)[:200])
                        self._save_messages_to_store([
                            {"role": "user", "content": user_input},
                            {"role": "assistant", "content": json.dumps(result, ensure_ascii=False)},
                        ], task_id)
                    return result
                except (json.JSONDecodeError, ValueError):
                    result = {"status": "success", "data": {"answer": content}}
                    if tracker:
                        tracker.end_task("success", str(result)[:200])
                        self._save_messages_to_store([
                            {"role": "user", "content": user_input},
                            {"role": "assistant", "content": json.dumps(result, ensure_ascii=False)},
                        ], task_id)
                    return result

            # Step 2: 执行工具调用
            tool_call = msg1["tool_calls"][0]
            func_name = tool_call["function"]["name"]

            if tracker:
                tracker.start_step("tool_call", f"调用 {func_name}({tool_call['function']['arguments']})")

            tool_result = self._execute_tool(tool_call)

            if tracker:
                tracker.end_step("success", f"{func_name} 返回: {tool_result[:200]}")

            messages.append(msg1)
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call["id"],
                "content": tool_result,
            })

            # Step 3: 第二次调用，让模型基于工具结果生成 JSON 回复
            if tracker:
                tracker.start_step("thought", "基于工具返回结果生成最终回复")

            resp2 = self._call_api(messages, use_json_mode=False)
            final_content = resp2["choices"][0]["message"]["content"]
            logger.info("[FunctionCall] 最终回复: %s", final_content[:200])

            try:
                result = self._validate_json(final_content)
            except (json.JSONDecodeError, ValueError):
                result = {"status": "success", "data": {"answer": final_content}}

            if tracker:
                tracker.end_task("success", str(result)[:200])
                self._save_messages_to_store([
                    {"role": "user", "content": user_input},
                    {"role": "assistant", "content": json.dumps(result, ensure_ascii=False)},
                ], task_id)

            return result

        except (requests.RequestException, KeyError, json.JSONDecodeError, ModelCallError) as e:
            err_msg = str(e)[:200]
            logger.error("[BizAgent] chat_with_tools 失败: %s", err_msg)
            if not isinstance(e, AgentError):
                err_msg = "系统处理异常，请稍后重试。"
            if tracker and task_id:
                tracker.end_task("failed", err_msg)
                self._save_messages_to_store([
                    {"role": "user", "content": user_input},
                    {"role": "assistant", "content": json.dumps({"status": "error", "error": err_msg}, ensure_ascii=False)},
                ], task_id)
            return {"status": "error", "error": err_msg, "data": None}

    # ============================================================
    # 快速对话：纯自然语言问答，无工具调用，无图表
    # ============================================================

    def chat_quick(self, user_input: str) -> dict:
        """
        快速对话：
          - 含 Function Calling，可查询企业数据库
          - 多轮上下文管理（summary 策略）
          - 快速响应，适合日常简单查询
        """
        self._ensure_session()

        # 懒初始化 ContextManager
        if not hasattr(self, "_quick_ctx") or self._quick_ctx is None:
            quick_system = (
                "你是「智友」，一个专业的商务智能助手。\n\n"
                "## 规则\n"
                "1. 用纯中文自然语言回答，语言简洁清晰。\n"
                "2. 你可以调用企业数据接口查询库存、财务、销售、员工等信息。\n"
                "3. 根据查询到的真实数据回答用户问题，数据如有不确定，明确说明。\n"
                "4. 不要使用表情符号。\n"
                "5. 回答应简短直接，适合快速阅读。"
            )
            self._quick_ctx = ContextManager(
                strategy="summary",
                api_key=self.api_key,
                base_url=self.base_url,
                recent_turns=4,
                summary_threshold=8,
            )
            self._quick_ctx.set_system(quick_system)

        # 记录总消耗
        if not hasattr(self, "_quick_tokens"):
            self._quick_tokens = 0

        self._quick_ctx.add_user(user_input)
        context_msgs = self._quick_ctx.get_context()

        logger.info("[Quick] context_msgs=%d, history=%d",
            len(context_msgs), len(self._quick_ctx.get_full_history()))

        try:
            # 第一次调用，带工具定义
            resp = self._call_api(context_msgs, use_json_mode=False, tools=self.tools)
            msg = resp["choices"][0]["message"]
            content = msg.get("content", "")
            tool_calls = msg.get("tool_calls")
            usage = resp.get("usage", {})
            self._quick_tokens += usage.get("total_tokens", 0)

            # 处理工具调用
            if tool_calls:
                # 把模型的回复加入上下文
                assistant_msg = {"role": "assistant", "content": content or None}
                if tool_calls:
                    assistant_msg["tool_calls"] = tool_calls
                context_msgs.append(assistant_msg)

                # 逐个执行工具
                for tc in tool_calls:
                    tool_result = self._execute_tool(tc)
                    context_msgs.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": tool_result,
                    })

                # 第二次调用，获取最终自然语言回复
                resp2 = self._call_api(context_msgs, use_json_mode=False)
                content = resp2["choices"][0]["message"].get("content", "")

            # 记入历史
            self._quick_ctx.add_assistant(content)

            # 持久化
            self._save_messages_to_store([
                {"role": "user", "content": user_input},
                {"role": "assistant", "content": content},
            ])

            return {
                "status": "success",
                "data": {"answer": content},
            }

        except (requests.RequestException, ModelCallError) as e:
            err_msg = str(e)[:200]
            logger.error("[Quick] 请求失败: %s", err_msg)
            if not isinstance(e, AgentError):
                err_msg = "系统处理异常，请稍后重试。"
            return {"status": "error", "error": err_msg, "data": None}

    def chat_smart(self, user_input: str) -> dict:
        """已弃用：使用 chat_quick() 或 chat_with_analysis() 替代。"""
        logger.warning("[Deprecated] chat_smart() 已弃用，使用 chat_quick()")
        return self.chat_quick(user_input)

    # ============================================================
    # 第四阶段：多轮对话 + 滑动窗口上下文管理
    # ============================================================

    def chat_multi_turn(
        self,
        user_input: str,
        context_strategy: str = "none",
        system_prompt: str = None,
        **strategy_kwargs,
    ) -> dict:
        """
        多轮对话：自动维护对话历史 + 上下文裁剪。

        Args:
            user_input: 用户输入
            context_strategy: "none" | "fixed" | "summary"
            system_prompt: 系统提示（首次调用时设置，后续复用）
            **strategy_kwargs:
                - max_turns (fixed 策略)
                - recent_turns, summary_threshold (summary 策略)

        返回: {"status": "success"|"error", "data": {...}, "stats": {...}}
        """
        self._ensure_session()

        # 懒初始化 ContextManager
        if not hasattr(self, "_ctx_mgr") or self._ctx_mgr is None:
            self._ctx_mgr = ContextManager(
                strategy=context_strategy,
                api_key=self.api_key,
                base_url=self.base_url,
                **strategy_kwargs,
            )
            self._ctx_mgr.set_system(system_prompt or BIZ_SYSTEM_PROMPT + self._build_data_context())

        # 记录总消耗统计
        if not hasattr(self, "_total_tokens_used"):
            self._total_tokens_used = 0
            self._total_calls = 0

        self._ctx_mgr.add_user(user_input)
        context_msgs = self._ctx_mgr.get_context()

        turn_number = self._total_calls + 1
        logger.info(
            "[MultiTurn] turn#%d, strategy=%s, context_msgs=%d, full_history=%d",
            turn_number,
            context_strategy,
            len(context_msgs),
            len(self._ctx_mgr.get_full_history()),
        )

        # 任务追踪
        tracker = self._get_tracker() if self._enable_tracking else None
        task_id = None
        if tracker:
            task_id = tracker.start_task(
                f"[Turn#{turn_number}] {user_input[:150]}", mode="multi"
            )

        try:
            if tracker:
                tracker.start_step("api_call",
                    f"多轮对话 turn#{turn_number}, strategy={context_strategy}")

            resp = self._call_api(context_msgs)
            raw_content = resp["choices"][0]["message"]["content"]

            if tracker:
                tracker.end_step("success", "模型响应成功")

            # 统计 token
            usage = resp.get("usage", {})
            self._total_tokens_used += usage.get("total_tokens", 0)
            self._total_calls += 1

            try:
                result = self._validate_json(raw_content)
            except (json.JSONDecodeError, ValueError):
                result = {"status": "success", "data": {"answer": raw_content}}

            # 把 assistant 回复记入历史
            self._ctx_mgr.add_assistant(raw_content)

            if tracker and task_id:
                tracker.end_task("success", str(result)[:200])
                self._save_messages_to_store(
                    [{"role": "user", "content": user_input},
                     {"role": "assistant", "content": raw_content}],
                    task_id,
                )

            return {
                "status": result.get("status", "success"),
                "data": result.get("data", result),
                "stats": self._ctx_mgr.stats(),
                "usage": {
                    "total_tokens": usage.get("total_tokens", 0),
                    "accumulated_tokens": self._total_tokens_used,
                },
            }

        except (requests.RequestException, KeyError, json.JSONDecodeError, ModelCallError) as e:
            err_msg = str(e)[:200]
            logger.error("[MultiTurn] 失败: %s", err_msg)
            if not isinstance(e, AgentError):
                err_msg = "系统处理异常，请稍后重试。"
            if tracker and task_id:
                tracker.end_task("failed", err_msg)
            return {"status": "error", "error": err_msg, "data": None}

    # ============================================================
    # 第五阶段：意图分类 + 参数提取 + 模板映射
    # ============================================================

    def chat_with_intent(self, user_input: str) -> dict:
        """
        意图驱动对话：自动识别意图 → 提取参数 → 调用 API → 模板化输出。

        完整链路:
          用户口语 → 意图分类 → 参数提取 → 模板匹配
          → 自动调用企业 API → 按模板 Schema 生成结构化 JSON

        Args:
            user_input: 用户自然语言输入

        Returns:
            {
                "status": "success"|"error",
                "data": {...},
                "intent": {...},   # 意图分析详情
                "error": "..."
            }
        """
        self._ensure_session()

        logger.info("[IntentMode] 输入: %s", user_input[:80])

        tracker = self._get_tracker() if self._enable_tracking else None
        task_id = None
        if tracker:
            task_id = tracker.start_task(user_input[:200], mode="intent")

        try:
            # ---- Step 1: 意图分析 ----
            if tracker:
                tracker.start_step("intent_analysis", "意图分类 + 参数提取")

            result = self._intent_engine.process(user_input)

            if tracker:
                tracker.end_step("success", f"意图={result.intent}, 置信度={result.confidence:.0%}, 参数={result.params}")

            if not result.is_recognized:
                # 未识别到明确意图，回退到普通 chat
                logger.info("[IntentMode] 未识别到明确意图，回退到默认模式")
                if tracker:
                    tracker.start_step("fallback", "未识别意图，回退默认模式")
                    tracker.end_step("success", "fallback to chat()")

                fallback = self.chat(user_input)
                fallback["intent"] = {
                    "recognized": False,
                    "intent": result.intent,
                    "confidence": result.confidence,
                }
                if tracker and task_id:
                    tracker.end_task("success" if fallback.get("status") == "success" else "failed", str(fallback)[:200])
                return fallback

            # ---- Step 2: 执行 API 调用 ----
            api_results = {}
            if result.api_tasks:
                if tracker:
                    tracker.start_step("api_execution", f"执行 {len(result.api_tasks)} 个 API")

                for task in result.api_tasks:
                    api_name = task["api"]
                    args = task.get("args", {})
                    func = FUNCTION_MAP.get(api_name)
                    if func:
                        ok, missing = _has_required_args(func, args)
                        if not ok:
                            logger.warning("[IntentMode] API %s 缺少参数 %s，跳过", api_name, missing)
                            api_results[api_name] = {"status": "skipped", "note": f"缺少必要参数（{'、'.join(missing)}），请补充查询条件"}
                            if tracker:
                                tracker.start_step("tool_call", f"{api_name} -> 跳过（缺 {missing}）")
                                tracker.end_step("failed", f"缺少参数: {missing}")
                            continue
                        try:
                            if tracker:
                                tracker.start_step("tool_call", f"{api_name}({args})")
                            api_data = func(**args)
                            api_results[api_name] = api_data
                            if tracker:
                                tracker.end_step("success", str(api_data)[:200])
                        except Exception as e:
                            logger.warning("[IntentMode] API %s 调用失败: %s", api_name, e)
                            api_results[api_name] = {"status": "error", "error": str(e)}
                            if tracker:
                                tracker.end_step("failed", str(e)[:200])
                    else:
                        logger.warning("[IntentMode] 未知 API: %s", api_name)
                        api_results[api_name] = {"status": "error", "error": f"未知 API: {api_name}"}

                if tracker:
                    tracker.end_step("success", f"API 结果: {len(api_results)} 个")

            # ---- Step 3: 构建增强提示 — 注入意图分析 + API 结果 + 模板 schema ----
            if tracker:
                tracker.start_step("build_prompt", "构建增强提示词")

            enhanced_messages = self._build_intent_messages(result, api_results)

            if tracker:
                tracker.end_step("success", f"消息数={len(enhanced_messages)}")

            # ---- Step 4: 调用模型生成结构化输出 ----
            if tracker:
                tracker.start_step("api_call", f"模型生成结构化输出 (intent={result.intent})")

            raw_resp = self._call_api(enhanced_messages, use_json_mode=False)
            raw_content = raw_resp["choices"][0]["message"]["content"]

            if tracker:
                tracker.end_step("success", "模型响应成功")

            # ---- Step 5: 解析 JSON 结果 ----
            if tracker:
                tracker.start_step("validate", "JSON 校验")

            try:
                output = self._validate_json(raw_content)
            except (json.JSONDecodeError, ValueError):
                output = {"status": "success", "data": {"回答": raw_content}}

            if tracker:
                tracker.end_step("success", "JSON 校验通过")

            # 组装最终返回
            final = {
                "status": output.get("status", "success"),
                "data": output.get("data", output),
                "intent": {
                    "recognized": True,
                    "intent": result.intent,
                    "confidence": result.confidence,
                    "params": result.params,
                    "matched_keywords": result.matched_keywords,
                    "api_calls": list(api_results.keys()),
                },
            }

            if tracker and task_id:
                tracker.end_task("success", f"意图={result.intent}, 结果={str(final)[:200]}")
                self._save_messages_to_store([
                    {"role": "user", "content": user_input},
                    {"role": "assistant", "content": json.dumps(final, ensure_ascii=False)},
                ], task_id)

            return final

        except (requests.RequestException, KeyError, json.JSONDecodeError, ModelCallError) as e:
            err_msg = str(e)[:200]
            logger.error("[IntentMode] 失败: %s", err_msg)
            if not isinstance(e, AgentError):
                err_msg = "系统处理异常，请稍后重试。"
            if tracker and task_id:
                tracker.end_task("failed", err_msg)
            return {"status": "error", "error": err_msg, "data": None}

    def _build_intent_messages(self, result: IntentResult, api_results: dict) -> list:
        """
        构建意图模式的消息列表：
          system prompt + 数据上下文 + 意图分析结果 + API 返回数据 + 模板输出要求
        """
        # 基础 system prompt + 数据上下文
        data_context = self._build_data_context()

        # 增强提示：意图分析 + 模板 schema
        enhanced_prompt = self._intent_engine.build_enhanced_prompt(result)

        # 拼接 API 返回的数据
        api_section = ""
        if api_results:
            api_section = "\n\n【API 返回数据】\n"
            for api_name, data in api_results.items():
                api_section += f"\n--- {api_name} ---\n"
                api_section += json.dumps(data, ensure_ascii=False, indent=2)

        # 构建完整的 user prompt
        full_prompt = (
            f"{enhanced_prompt}"
            f"{api_section}"
            f"\n\n【用户原始输入】\n{result.original_input}"
        )

        system_content = (
            BIZ_SYSTEM_PROMPT
            + data_context
            + "\n\n## 意图驱动模式\n"
            + "你正在以意图驱动模式工作。上方已给出【意图识别结果】和【API 返回数据】（如有），"
            + "请严格按照【输出格式要求】中的 schema 生成 JSON 回复。\n"
            + "所有字段名使用中文，数据要真实反映 API 返回的内容。"
        )

        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": full_prompt},
        ]

    # ============================================================
    # 第六阶段：数据分析算子 + 智能可视化
    # ============================================================

    def chat_with_analysis(self, user_input: str, generate_chart: bool = True) -> dict:
        """
        增强分析对话：意图识别 → 数据获取 → 统计分析 → 可视化 → 结构化输出。

        完整链路:
          用户口语 → 意图分类 → 调 API 取数
          → 自动计算同比/环比/占比/极值/均值
          → 自动生成折线/柱状/饼图
          → 按模板 Schema 输出结构化 JSON

        Args:
            user_input: 用户输入
            generate_chart: 是否自动生成可视化图表

        Returns:
            {
                "status": "success"|"error",
                "data": {...},        # 模板化业务数据
                "analysis": {...},     # 统计分析结果
                "charts": [...],       # 图表路径列表
                "intent": {...},       # 意图分析
                "error": "..."
            }
        """
        self._ensure_session()
        logger.info("[AnalysisMode] 输入: %s", user_input[:80])

        tracker = self._get_tracker() if self._enable_tracking else None
        task_id = None
        if tracker:
            task_id = tracker.start_task(user_input[:200], mode="analysis")

        try:
            # ---- Step 1: 意图分析 ----
            if tracker:
                tracker.start_step("intent_analysis", "意图分类")

            result = self._intent_engine.process(user_input)

            if tracker:
                tracker.end_step("success", f"意图={result.intent}, 置信度={result.confidence:.0%}")

            if not result.is_recognized:
                logger.info("[AnalysisMode] 未识别意图，回退到 chat()")
                if tracker:
                    tracker.start_step("fallback", "未识别意图")
                    tracker.end_step("success", "fallback")
                fallback = self.chat(user_input)
                fallback["analysis"] = {}
                fallback["charts"] = []
                if tracker and task_id:
                    tracker.end_task("success" if fallback.get("status") == "success" else "failed", str(fallback)[:200])
                return fallback

            # ---- Step 2: 执行 API 调用 ----
            api_results = {}
            for task in (result.api_tasks or []):
                api_name = task["api"]
                args = task.get("args", {})
                func = FUNCTION_MAP.get(api_name)
                if func:
                    ok, missing = _has_required_args(func, args)
                    if not ok:
                        logger.warning("[AnalysisMode] API %s 缺少参数 %s，跳过", api_name, missing)
                        api_results[api_name] = {"note": f"查询条件不足（缺少{'、'.join(missing)}），已使用全部可用数据"}
                        if tracker:
                            tracker.start_step("tool_call", f"{api_name} -> 跳过（缺 {missing}）")
                            tracker.end_step("failed", f"缺少参数: {missing}")
                        continue
                    try:
                        if tracker:
                            tracker.start_step("tool_call", f"{api_name}({args})")
                        api_data = func(**args)
                        api_results[api_name] = api_data
                        if tracker:
                            tracker.end_step("success", str(api_data)[:200])
                    except Exception as e:
                        logger.warning("[AnalysisMode] API %s 失败: %s", api_name, e)
                        api_results[api_name] = {"error": str(e)}
                        if tracker:
                            tracker.end_step("failed", str(e)[:200])

            # ---- Step 3: 统计分析 ----
            if tracker:
                tracker.start_step("analysis", "执行统计计算")

            clean_old_charts()
            all_analysis = auto_analyze(api_results)
            # 按意图过滤分析结果，只保留相关类型
            analysis_results = _filter_analysis_by_intent(all_analysis, result.intent)
            logger.info("[AnalysisMode] 意图=%s, 分析类型=%s", result.intent, list(analysis_results.keys()))

            if tracker:
                analysis_summary = {k: str(v)[:80] for k, v in analysis_results.items()}
                tracker.end_step("success", f"分析完成: {list(analysis_results.keys())}")

            # ---- Step 4: 智能可视化（按意图选择图表类型） ----
            charts = []
            chart_error_hint = None
            if generate_chart and analysis_results:
                if tracker:
                    tracker.start_step("visualization", "生成图表")

                try:
                    intent = result.intent
                    # 按意图选择主要图表类型
                    if intent == "库存查询":
                        # 优先库存分析图
                        inv = analysis_results.get("inventory_analysis", {})
                        if inv and inv.get("warehouse_distribution"):
                            labels_wh = list(inv["warehouse_distribution"].keys())
                            values_wh = list(inv["warehouse_distribution"].values())
                            pc = pie_chart(labels_wh, values_wh, title="库存仓库分布")
                            if "error" not in pc:
                                charts.append(pc)
                    elif intent in ("财务报告", "数据分析"):
                        # 优先财务趋势图
                        mom = analysis_results.get("financial_mom", [])
                        if mom and len(mom) >= 2:
                            months = [item["period"] for item in mom]
                            values = [item["current_value"] for item in mom]
                            lc = line_chart(months, [{"label": "指标值", "values": values}], title=f"财务环比趋势 — {intent}")
                            if "error" not in lc:
                                charts.append(lc)
                    elif intent in ("销售分析", "对比分析"):
                        # 优先销售对比柱状图
                        comparison = analysis_results.get("sales_comparison", [])
                        if comparison:
                            labels = [c["product"] for c in comparison]
                            values = [c["total_sales"] for c in comparison]
                            bc = bar_chart(labels, values, title="产品销量对比", y_label="销量（件）")
                            if "error" not in bc:
                                charts.append(bc)
                    elif intent in ("综合简报",):
                        # 综合简报：尝试生成 财务汇总柱状图
                        summary = analysis_results.get("financial_summary", {})
                        if summary and summary.get("months_analyzed", 0) > 0:
                            labels_list = ["总营收", "总成本", "总利润"]
                            values_list = [
                                summary.get("total_revenue", 0),
                                summary.get("total_cost", 0),
                                summary.get("total_profit", 0),
                            ]
                            if any(v != 0 for v in values_list):
                                bc = bar_chart(labels_list, values_list, title="财务汇总", y_label="金额（元）")
                                if "error" not in bc:
                                    charts.append(bc)

                    # 如果主要图表未生成，尝试 auto_chart 兜底
                    if not charts:
                        chart_result = auto_chart(analysis_results, data_source=intent)
                        if "error" not in chart_result:
                            charts.append(chart_result)
                            logger.info("[AnalysisMode] 兜底图表已生成: %s", chart_result["chart_url"])
                        else:
                            chart_error_hint = chart_result["error"]
                            logger.info("[AnalysisMode] auto_chart 未命中: %s", chart_error_hint)

                    # 二次兜底：尝试其他数据
                    if not charts:
                        comparison = analysis_results.get("sales_comparison", [])
                        if comparison:
                            labels = [c["product"] for c in comparison]
                            values = [c["total_sales"] for c in comparison]
                            bc = bar_chart(labels, values, title="产品销量对比", y_label="销量（件）")
                            if "error" not in bc:
                                charts.append(bc)

                        inv = analysis_results.get("inventory_analysis", {})
                        if inv and inv.get("warehouse_distribution"):
                            labels_wh = list(inv["warehouse_distribution"].keys())
                            values_wh = list(inv["warehouse_distribution"].values())
                            pc = pie_chart(labels_wh, values_wh, title="库存仓库分布")
                            if "error" not in pc:
                                charts.append(pc)

                    if not charts and not chart_error_hint:
                        chart_error_hint = "暂无可视化的数据维度"

                except Exception as e:
                    logger.warning("[AnalysisMode] 图表生成异常，已降级为纯文本: %s", e)
                    chart_error_hint = "图表生成时遇到技术问题，已自动降级为纯文本分析。"

                if tracker:
                    tracker.end_step("success", f"生成了 {len(charts)} 个图表")

            # ---- Step 5: 构建增强提示并调模型 ----
            if tracker:
                tracker.start_step("build_prompt", "构建分析增强提示")

            enhanced_messages = self._build_analysis_messages(
                result, api_results, analysis_results, charts,
                chart_degraded=bool(chart_error_hint),
                chart_error_hint=chart_error_hint,
            )

            if tracker:
                tracker.end_step("success", f"消息数={len(enhanced_messages)}")

            # ---- Step 6: 模型生成结构化输出 ----
            if tracker:
                tracker.start_step("api_call", f"模型生成 (intent={result.intent})")

            raw_resp = self._call_api(enhanced_messages, use_json_mode=False)
            raw_content = raw_resp["choices"][0]["message"]["content"]

            if tracker:
                tracker.end_step("success", "模型响应成功")

            # ---- Step 7: 解析 ----
            if tracker:
                tracker.start_step("validate", "JSON 校验")

            try:
                output = self._validate_json(raw_content)
            except (json.JSONDecodeError, ValueError):
                output = {"status": "success", "data": {"回答": raw_content}}

            if tracker:
                tracker.end_step("success", "JSON 校验通过")

            # 组装最终返回
            final = {
                "status": output.get("status", "success"),
                "data": output.get("data", output),
                "analysis": analysis_results,
                "charts": charts,
                "intent": {
                    "recognized": True,
                    "intent": result.intent,
                    "confidence": result.confidence,
                    "params": result.params,
                    "matched_keywords": result.matched_keywords,
                    "api_calls": list(api_results.keys()),
                },
            }

            if tracker and task_id:
                tracker.end_task("success", f"分析完成, {len(charts)} 图表")

            # 持久化到 SQLite（供历史记录展示）
            self._save_messages_to_store([
                {"role": "user", "content": user_input},
                {"role": "assistant", "content": json.dumps(final, ensure_ascii=False)},
            ], task_id)

            return final

        except (requests.RequestException, KeyError, json.JSONDecodeError, ModelCallError) as e:
            err_msg = str(e)[:200]
            logger.error("[AnalysisMode] 失败: %s", err_msg)
            # AgentError 已自带用户友好消息；其他异常转换为友好提示
            if not isinstance(e, AgentError):
                err_msg = "系统处理异常，请稍后重试。"
            if tracker and task_id:
                tracker.end_task("failed", err_msg)
            return {"status": "error", "error": err_msg, "data": None, "analysis": {}, "charts": []}

    def _build_analysis_messages(
        self,
        result: IntentResult,
        api_results: dict,
        analysis_results: dict,
        charts: list[dict],
        chart_degraded: bool = False,
        chart_error_hint: Optional[str] = None,
    ) -> list:
        """构建分析模式的增强消息"""
        data_context = self._build_data_context()
        intent_prompt = self._intent_engine.build_enhanced_prompt(result)

        # API 结果（按意图过滤）
        filtered_apis = _filter_api_by_intent(api_results, result.intent)
        api_section = ""
        if filtered_apis:
            api_section = "\n\n【API 返回数据】\n"
            for api_name, data in filtered_apis.items():
                api_section += f"\n--- {api_name} ---\n"
                api_section += json.dumps(data, ensure_ascii=False, indent=2)

        # 统计分析结果
        analysis_section = ""
        if analysis_results:
            analysis_section = "\n\n【统计分析结果】\n"
            for name, data in analysis_results.items():
                analysis_section += f"\n--- {name} ---\n"
                analysis_section += json.dumps(data, ensure_ascii=False, indent=2, default=str)

        # 图表信息
        chart_section = ""
        if charts:
            chart_section = "\n\n【已生成图表】\n"
            for c in charts:
                chart_section += f"- {c.get('title', '')} ({c.get('chart_type', '')}): {c.get('chart_url', '')}\n"
        elif chart_degraded:
            chart_section = f"\n\n【图表提示】\n{chart_error_hint or '图表生成不可用，请以纯文本形式输出分析报告。'}\n"

        # 分析指导
        analysis_guide = (
            "\n\n## 分析报告要求\n"
            "你正在以增强分析模式工作。请生成一份完整的自然语言分析报告：\n"
            "1. 基于【API 返回数据】和【统计分析结果】生成报告\n"
            "2. 引用具体数据（如环比增长率、占比、极值等），将数据融入叙述中\n"
            "3. 报告的 answer 字段必须是纯自然语言文本，分段落撰写，语言流畅专业\n"
            "4. 禁止在 answer 中使用 Markdown 标记（**、*、# 等），不要包含技术性描述、置信度或 API 调用信息\n"
            "5. 如果生成了图表，只需简要提及（如「详见下图」），不要输出图片链接或 Markdown 图片标记\n"
            "6. 结构化数据（表格、指标汇总等）放在 details 字段中作为补充\n"
            "7. 优先输出清晰的分析结论和关键数据要点，确保回答流畅专业、无冗余信息\n"
            "\n"
            "【统一输出格式 — 必须严格遵守】\n"
            "无论业务类别是什么，始终输出以下 JSON 结构：\n"
            "{\n"
            "  \"answer\": \"这里写完整的自然语言分析报告，分段落、引用具体数据\",\n"
            "  \"details\": {\n"
            "    \"业务类别\": \"识别到的业务类型\",\n"
            "    \"关键指标\": { ... 模板定义的字段全部放在这里 ... },\n"
            "    \"原始数据\": { ... 其他结构化数据 ... }\n"
            "  }\n"
            "}\n"
            "answer 字段必须存在且有内容，不得为空。details 中的字段按模板 schema 输出。"
        )

        full_prompt = (
            f"{intent_prompt}"
            f"{api_section}"
            f"{analysis_section}"
            f"{chart_section}"
            f"{analysis_guide}"
            f"\n\n【用户输入】\n{result.original_input}"
        )

        system_content = (
            BIZ_SYSTEM_PROMPT
            + data_context
            + "\n\n## 增强分析模式\n"
            + "你是一个具备数据分析和可视化能力的商务智能助手。"
            + "上方已给出意图分析、API 数据、统计分析结果和图表。"
            + "请基于真实数据用自然语言撰写分析报告，所有数据点融入叙述中，"
            + "禁止直接输出 JSON 键值对。结构化数据放在 details 字段。"
            + "\n"
            + "【重要 — 聚焦用户问题】\n"
            + f"本次用户的业务类别是【{result.intent}】，请围绕此范围进行分析，\n"
            + "不要超出用户询问的范围去分析其他业务领域。例如：\n"
            + "  - 用户查库存 → 只分析库存状况，不写财务或销售报告\n"
            + "  - 用户查财务 → 只分析财务指标，不写库存或销售报告\n"
            + "  - 用户查销售 → 只分析销售数据，不写财务或库存报告\n"
            + "  - 综合简报 → 可以覆盖多个方面\n"
        )

        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": full_prompt},
        ]

    def reset_conversation(self):
        """重置对话历史、追踪器和意图引擎状态"""
        if hasattr(self, "_ctx_mgr") and self._ctx_mgr is not None:
            self._ctx_mgr.reset()
            self._ctx_mgr = None
        if hasattr(self, "_smart_ctx") and self._smart_ctx is not None:
            self._smart_ctx.reset()
            self._smart_ctx = None
        if self._tracker is not None:
            self._tracker.reset()
        if hasattr(self, "_intent_engine") and self._intent_engine is not None:
            self._intent_engine.clear_history()
        self._total_tokens_used = 0
        self._total_calls = 0
        if hasattr(self, "_smart_tokens"):
            self._smart_tokens = 0
            self._smart_calls = 0
