"""
任务状态追踪 + 执行日志系统
==========================
每轮对话生成 task_id，记录思考步骤/工具调用/代码执行/
耗时/成功失败，输出结构化日志（控制台 + JSONL 文件）。

可观测性是生产级 Agent 标配。
"""

import os
import json
import uuid
import time
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 日志目录（项目根目录下的 logs/）
LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
os.makedirs(LOG_DIR, exist_ok=True)

TASK_LOG_FILE = LOG_DIR / "tasks.jsonl"
STEP_LOG_FILE = LOG_DIR / "steps.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


class TaskTracker:
    """任务追踪器：记录每轮对话的执行全过程。

    用法:
        tracker = TaskTracker.get_or_create(session_id)
        task_id = tracker.start_task("分析销售数据", mode="tools")
        tracker.start_step("api_call", "调用 get_financial_report")
        # ... 执行 ...
        tracker.end_step("success", "返回 3 月营收 510 万")
        tracker.end_task("success", "分析完成")
        summary = tracker.get_task_summary(task_id)
    """

    _instances: dict[str, "TaskTracker"] = {}

    def __init__(self, session_id: Optional[str] = None):
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self.current_task_id: Optional[str] = None
        self.current_task: Optional[dict] = None
        self.current_step_id: Optional[str] = None
        self.current_step: Optional[dict] = None
        self.tasks: dict[str, dict] = {}
        self._step_start_time: Optional[float] = None
        self._task_start_time: Optional[float] = None

    @classmethod
    def get_or_create(cls, session_id: Optional[str] = None) -> "TaskTracker":
        """按 session_id 获取或创建追踪器（单例模式）"""
        if session_id and session_id in cls._instances:
            return cls._instances[session_id]
        tracker = cls(session_id)
        cls._instances[tracker.session_id] = tracker
        return tracker

    # ---- 任务生命周期 ----

    def start_task(self, task_description: str, mode: str = "single") -> str:
        """开始一个新任务，返回 task_id"""
        task_id = uuid.uuid4().hex[:12]
        self.current_task_id = task_id
        self._task_start_time = time.time()
        self.current_task = {
            "task_id": task_id,
            "session_id": self.session_id,
            "description": task_description[:200],
            "mode": mode,
            "status": "running",
            "steps": [],
            "total_duration": 0.0,
            "total_steps": 0,
            "failed_steps": 0,
            "created_at": _now(),
        }
        self.tasks[task_id] = self.current_task
        self._log_event("task_start", {
            "task_id": task_id,
            "session_id": self.session_id,
            "description": task_description[:200],
            "mode": mode,
        })
        logger.info("[Task:%s] 开始: %s", task_id, task_description[:80])
        return task_id

    def start_step(self, step_type: str, content: str) -> str:
        """记录一个步骤的开始，返回 step_id

        step_type 取值: thought | tool_call | code_execution | api_call | observation
        """
        if not self.current_task:
            return ""

        step_id = uuid.uuid4().hex[:8]
        self.current_step_id = step_id
        self._step_start_time = time.time()
        self.current_step = {
            "step_id": step_id,
            "task_id": self.current_task_id,
            "step_type": step_type,
            "content": content[:500],
            "started_at": _now(),
            "duration": 0.0,
            "status": "running",
        }
        return step_id

    def end_step(self, status: str = "success", result: str = ""):
        """结束当前步骤，记录耗时"""
        if not self.current_step or not self.current_task:
            return

        duration = time.time() - (self._step_start_time or time.time())
        self.current_step["duration"] = round(duration, 3)
        self.current_step["status"] = status
        self.current_step["result"] = result[:500]

        self.current_task["steps"].append(dict(self.current_step))
        self.current_task["total_steps"] += 1
        if status != "success":
            self.current_task["failed_steps"] += 1

        self._log_event("step_end", dict(self.current_step))

        level = logging.WARNING if status != "success" else logging.DEBUG
        logger.log(
            level,
            "[Step:%s] %s -> %s (%.2fs)",
            self.current_step_id,
            self.current_step["step_type"],
            status,
            duration,
        )
        self.current_step = None

    def end_task(self, status: str = "success", result: str = ""):
        """结束当前任务，返回任务完整记录"""
        if not self.current_task:
            return None

        self.current_task["status"] = status
        self.current_task["result"] = result[:500]
        self.current_task["total_duration"] = round(
            time.time() - (self._task_start_time or time.time()), 3
        )

        self._log_event("task_end", {
            "task_id": self.current_task_id,
            "status": status,
            "total_duration": self.current_task["total_duration"],
            "total_steps": self.current_task["total_steps"],
            "failed_steps": self.current_task["failed_steps"],
        })

        logger.info(
            "[Task:%s] %s (%.2fs, %d 步, %d 失败)",
            self.current_task_id,
            status,
            self.current_task["total_duration"],
            self.current_task["total_steps"],
            self.current_task["failed_steps"],
        )

        task = self.current_task
        self.current_task = None
        self.current_task_id = None
        return task

    # ---- 查询 ----

    def get_task_summary(self, task_id: Optional[str] = None) -> dict:
        """获取任务摘要"""
        tid = task_id or self.current_task_id
        task = self.tasks.get(tid)
        if not task:
            return {"error": f"任务 {tid} 不存在"}
        return {
            "task_id": task["task_id"],
            "description": task["description"],
            "mode": task["mode"],
            "status": task["status"],
            "total_steps": task["total_steps"],
            "failed_steps": task["failed_steps"],
            "total_duration": task["total_duration"],
        }

    def get_task_logs(self, task_id: Optional[str] = None) -> list[dict]:
        """获取任务的完整步骤日志"""
        tid = task_id or self.current_task_id
        task = self.tasks.get(tid)
        return task.get("steps", []) if task else []

    def get_recent_tasks(self, limit: int = 10) -> list[dict]:
        """获取最近 N 个任务的摘要"""
        summaries = []
        for tid in list(self.tasks.keys())[-limit:]:
            summaries.append(self.get_task_summary(tid))
        return summaries

    # ---- 日志持久化 ----

    def _log_event(self, event_type: str, data: dict):
        """写入 JSONL 结构化日志文件"""
        try:
            record = {"timestamp": _now(), "event_type": event_type, **data}
            with open(TASK_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning("写日志文件失败: %s", e)

    @staticmethod
    def read_recent_logs(limit: int = 30) -> list[dict]:
        """从日志文件读取最近的记录"""
        logs = []
        try:
            if TASK_LOG_FILE.exists():
                with open(TASK_LOG_FILE, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            logs.append(json.loads(line))
        except (OSError, json.JSONDecodeError):
            return []
        return logs[-limit:]

    # ---- 重置 ----

    def reset(self):
        """重置追踪器状态（保留已完成的 tasks）"""
        self.current_task_id = None
        self.current_task = None
        self.current_step_id = None
        self.current_step = None
        self._step_start_time = None
        self._task_start_time = None

    @classmethod
    def reset_all(cls):
        """重置所有追踪器"""
        cls._instances.clear()
