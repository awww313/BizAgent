"""
会话持久化（SQLite 轻量存储）
============================
当前会话存储在内存中，重启服务后丢失。本模块提供
SQLite 持久化存储，支持会话/消息/任务日志的 CRUD。

表结构:
  sessions   — 会话元信息（id, created_at, updated_at, metadata）
  messages   — 消息记录（role, content, task_id, 时间戳）
  task_logs  — 任务执行日志（step_type, step_data, duration, status）
"""

import os
import json
import sqlite3
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = DATA_DIR / "sessions.db"


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


class SessionStore:
    """SQLite 会话存储（单例）"""

    _instance: "SessionStore | None" = None

    def __new__(cls) -> "SessionStore":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.db_path = str(DB_PATH)
        self._init_db()
        logger.info("[SessionStore] 已初始化: %s", self.db_path)

    # ---- 初始化 ----

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=3000")
        return conn

    def _init_db(self):
        conn = self._get_conn()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    task_id TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS task_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    step_type TEXT NOT NULL,
                    step_data TEXT NOT NULL,
                    duration REAL DEFAULT 0.0,
                    status TEXT NOT NULL DEFAULT 'running',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS eval_logs (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id        TEXT NOT NULL,
                    task_id           TEXT,
                    mode              TEXT NOT NULL,
                    user_input        TEXT NOT NULL,
                    intent            TEXT,
                    intent_confidence REAL,
                    latency_ms        INTEGER,
                    retry_count       INTEGER DEFAULT 0,
                    hallucination     INTEGER DEFAULT 0,
                    hallucination_detail TEXT,
                    reflection_score  REAL,
                    token_usage       TEXT,
                    status            TEXT NOT NULL DEFAULT 'success',
                    error             TEXT,
                    model_response    TEXT,
                    created_at        TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
                CREATE INDEX IF NOT EXISTS idx_task_logs_session ON task_logs(session_id);
                CREATE INDEX IF NOT EXISTS idx_task_logs_task  ON task_logs(task_id);
                CREATE INDEX IF NOT EXISTS idx_eval_logs_created ON eval_logs(created_at);
                CREATE INDEX IF NOT EXISTS idx_eval_logs_session ON eval_logs(session_id);
                CREATE INDEX IF NOT EXISTS idx_eval_logs_intent  ON eval_logs(intent);
            """)
            conn.commit()
        finally:
            conn.close()

    # ==================== 会话 CRUD ====================

    def create_session(self, session_id: str, metadata: Optional[dict] = None) -> bool:
        """创建新会话"""
        now = _now()
        conn = self._get_conn()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO sessions (id, created_at, updated_at, metadata) VALUES (?, ?, ?, ?)",
                [session_id, now, now, json.dumps(metadata or {}, ensure_ascii=False)],
            )
            conn.commit()
            return True
        except sqlite3.Error as e:
            logger.error("[SessionStore] 创建会话失败: %s", e)
            return False
        finally:
            conn.close()

    def update_session_metadata(self, session_id: str, metadata: dict) -> bool:
        """更新会话的 metadata（合并写入）"""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT metadata FROM sessions WHERE id = ?", [session_id]
            ).fetchone()
            if not row:
                return False
            existing = json.loads(row["metadata"]) if isinstance(row["metadata"], str) else (dict(row["metadata"]) if row["metadata"] else {})
            existing.update(metadata)
            conn.execute(
                "UPDATE sessions SET metadata = ?, updated_at = ? WHERE id = ?",
                [json.dumps(existing, ensure_ascii=False), _now(), session_id],
            )
            conn.commit()
            return True
        except sqlite3.Error as e:
            logger.error("[SessionStore] 更新会话 metadata 失败: %s", e)
            return False
        finally:
            conn.close()

    def get_session(self, session_id: str) -> Optional[dict]:
        """获取会话信息"""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM sessions WHERE id = ?", [session_id]
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def list_sessions(self, limit: int = 20) -> list[dict]:
        """列出最近的会话"""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT id, created_at, updated_at, metadata FROM sessions ORDER BY updated_at DESC LIMIT ?",
                [limit],
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def list_sessions_with_stats(self, limit: int = 20) -> list[dict]:
        """列出会话，附带首条消息预览和消息数"""
        conn = self._get_conn()
        try:
            rows = conn.execute("""
                SELECT
                    s.id,
                    s.created_at,
                    s.updated_at,
                    s.metadata,
                    (SELECT content FROM messages WHERE session_id = s.id
                     AND role = 'user' ORDER BY id ASC LIMIT 1) AS first_message,
                    (SELECT COUNT(*) FROM messages WHERE session_id = s.id) AS message_count
                FROM sessions s
                ORDER BY s.updated_at DESC LIMIT ?
            """, [limit]).fetchall()

            result = []
            for r in rows:
                d = dict(r)
                # 取首条消息前 80 字作为预览
                if d.get("first_message"):
                    d["preview"] = d["first_message"][:80]
                else:
                    d["preview"] = ""
                d.pop("first_message", None)
                # 解析 metadata JSON → 提取 mode
                meta_raw = d.pop("metadata", "{}")
                if isinstance(meta_raw, str):
                    try:
                        meta = json.loads(meta_raw)
                    except json.JSONDecodeError:
                        meta = {}
                elif isinstance(meta_raw, dict):
                    meta = meta_raw
                else:
                    meta = {}
                d["mode"] = meta.get("mode", "quick")
                result.append(d)
            return result
        finally:
            conn.close()

    def _touch_session(self, session_id: str):
        """更新会话的 updated_at 时间"""
        conn = self._get_conn()
        try:
            conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                [_now(), session_id],
            )
            conn.commit()
        finally:
            conn.close()

    def delete_all_sessions(self) -> int:
        """删除所有会话及其关联数据，返回删除数量"""
        conn = self._get_conn()
        try:
            count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            conn.executescript("DELETE FROM messages; DELETE FROM task_logs; DELETE FROM sessions;")
            conn.commit()
            logger.info("[SessionStore] 已清除全部 %d 个会话", count)
            return count
        except sqlite3.Error as e:
            logger.error("[SessionStore] 清除全部会话失败: %s", e)
            return 0
        finally:
            conn.close()

    def delete_session(self, session_id: str) -> bool:
        """删除会话及其所有关联数据"""
        conn = self._get_conn()
        try:
            conn.execute("DELETE FROM messages WHERE session_id = ?", [session_id])
            conn.execute("DELETE FROM task_logs WHERE session_id = ?", [session_id])
            conn.execute("DELETE FROM sessions WHERE id = ?", [session_id])
            conn.commit()
            return True
        except sqlite3.Error as e:
            logger.error("[SessionStore] 删除会话失败: %s", e)
            return False
        finally:
            conn.close()

    # ==================== 评估日志 ====================

    def save_eval_log(
        self,
        session_id: str,
        mode: str,
        user_input: str,
        task_id: Optional[str] = None,
        intent: Optional[str] = None,
        intent_confidence: Optional[float] = None,
        latency_ms: Optional[int] = None,
        retry_count: int = 0,
        hallucination: bool = False,
        hallucination_detail: Optional[str] = None,
        reflection_score: Optional[float] = None,
        token_usage: Optional[str] = None,
        status: str = "success",
        error: Optional[str] = None,
        model_response: Optional[str] = None,
    ) -> int:
        """保存一条评估日志"""
        conn = self._get_conn()
        try:
            cur = conn.execute(
                """INSERT INTO eval_logs
                   (session_id, task_id, mode, user_input, intent, intent_confidence,
                    latency_ms, retry_count, hallucination, hallucination_detail,
                    reflection_score, token_usage, status, error, model_response, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    session_id, task_id, mode, user_input, intent, intent_confidence,
                    latency_ms, retry_count, 1 if hallucination else 0, hallucination_detail,
                    reflection_score, token_usage, status, error, model_response, _now(),
                ],
            )
            conn.commit()
            return cur.lastrowid or -1
        except sqlite3.Error as e:
            logger.error("[SessionStore] 保存评估日志失败: %s", e)
            return -1
        finally:
            conn.close()

    def query_eval_logs(
        self,
        limit: int = 50,
        session_id: Optional[str] = None,
        intent: Optional[str] = None,
        status: Optional[str] = None,
        offset: int = 0,
    ) -> list[dict]:
        """查询评估日志，支持按 session / intent / status 筛选"""
        conn = self._get_conn()
        try:
            where = []
            params = []
            if session_id:
                where.append("session_id = ?")
                params.append(session_id)
            if intent:
                where.append("intent LIKE ?")
                params.append(f"%{intent}%")
            if status:
                where.append("status = ?")
                params.append(status)
            sql = "SELECT * FROM eval_logs"
            if where:
                sql += " WHERE " + " AND ".join(where)
            sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_eval_stats(self, days: int = 7) -> dict:
        """获取评估统计：平均延迟、成功率、意图分布、幻觉率"""
        conn = self._get_conn()
        try:
            total = conn.execute(
                "SELECT COUNT(*) FROM eval_logs WHERE created_at >= datetime('now', ? || ' days', 'localtime')",
                [f"-{days}"],
            ).fetchone()[0]

            if total == 0:
                return {
                    "total": 0, "avg_latency_ms": 0, "success_rate": 0,
                    "hallucination_rate": 0, "intent_distribution": [],
                    "daily_trend": [],
                }

            # 成功率
            success = conn.execute(
                "SELECT COUNT(*) FROM eval_logs WHERE status='success' AND created_at >= datetime('now', ? || ' days', 'localtime')",
                [f"-{days}"],
            ).fetchone()[0]

            # 平均延迟
            avg_lat = conn.execute(
                "SELECT COALESCE(AVG(latency_ms), 0) FROM eval_logs WHERE latency_ms IS NOT NULL AND created_at >= datetime('now', ? || ' days', 'localtime')",
                [f"-{days}"],
            ).fetchone()[0]

            # 幻觉率
            h_count = conn.execute(
                "SELECT COUNT(*) FROM eval_logs WHERE hallucination=1 AND created_at >= datetime('now', ? || ' days', 'localtime')",
                [f"-{days}"],
            ).fetchone()[0]

            # 意图分布
            intent_rows = conn.execute(
                "SELECT intent, COUNT(*) as cnt FROM eval_logs WHERE created_at >= datetime('now', ? || ' days', 'localtime') AND intent IS NOT NULL AND intent != '' GROUP BY intent ORDER BY cnt DESC LIMIT 10",
                [f"-{days}"],
            ).fetchall()

            # 每日趋势
            trend_rows = conn.execute(
                "SELECT DATE(created_at) as day, AVG(latency_ms) as avg_lat, COUNT(*) as cnt, SUM(hallucination) as h_cnt FROM eval_logs WHERE created_at >= datetime('now', ? || ' days', 'localtime') GROUP BY DATE(created_at) ORDER BY day",
                [f"-{days}"],
            ).fetchall()

            return {
                "total": total,
                "successful": success,
                "success_rate": round(success / total * 100, 1) if total else 0,
                "avg_latency_ms": round(avg_lat, 1) if avg_lat else 0,
                "hallucination_rate": round(h_count / total * 100, 1) if total else 0,
                "hallucination_count": h_count,
                "intent_distribution": [{"intent": r["intent"], "count": r["cnt"]} for r in intent_rows],
                "daily_trend": [{
                    "date": r["day"],
                    "avg_latency_ms": round(r["avg_lat"], 1) if r["avg_lat"] else 0,
                    "count": r["cnt"],
                    "hallucinations": r["h_cnt"] or 0,
                } for r in trend_rows],
            }
        finally:
            conn.close()

    def delete_eval_logs(self, session_id: Optional[str] = None) -> int:
        """删除评估日志，可选按 session 筛选。返回删除条数。"""
        conn = self._get_conn()
        try:
            if session_id:
                count = conn.execute(
                    "DELETE FROM eval_logs WHERE session_id = ?", [session_id]
                ).rowcount
            else:
                count = conn.execute("DELETE FROM eval_logs").rowcount
            conn.commit()
            return count
        except sqlite3.Error as e:
            logger.error("[SessionStore] 删除评估日志失败: %s", e)
            return 0
        finally:
            conn.close()


# 全局单例

    def save_message(
        self, session_id: str, role: str, content: str, task_id: Optional[str] = None
    ) -> int:
        """保存一条消息，返回消息 ID"""
        conn = self._get_conn()
        try:
            cur = conn.execute(
                "INSERT INTO messages (session_id, role, content, task_id, created_at) VALUES (?, ?, ?, ?, ?)",
                [session_id, role, content, task_id, _now()],
            )
            conn.commit()
            self._touch_session(session_id)
            return cur.lastrowid or -1
        except sqlite3.Error as e:
            logger.error("[SessionStore] 保存消息失败: %s", e)
            return -1
        finally:
            conn.close()

    def save_messages(
        self, session_id: str, messages: list[dict[str, Any]], task_id: Optional[str] = None
    ) -> int:
        """批量保存消息"""
        conn = self._get_conn()
        now = _now()
        try:
            data = [
                [session_id, m["role"], m.get("content", "") or "", task_id, now]
                for m in messages
            ]
            conn.executemany(
                "INSERT INTO messages (session_id, role, content, task_id, created_at) VALUES (?, ?, ?, ?, ?)",
                data,
            )
            conn.commit()
            self._touch_session(session_id)
            return len(data)
        except sqlite3.Error as e:
            logger.error("[SessionStore] 批量保存消息失败: %s", e)
            return -1
        finally:
            conn.close()

    def get_messages(
        self, session_id: str, limit: int = 200
    ) -> list[dict[str, Any]]:
        """获取会话消息历史"""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT role, content, task_id, created_at FROM messages "
                "WHERE session_id = ? ORDER BY id ASC LIMIT ?",
                [session_id, limit],
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ==================== 任务日志 ====================

    def save_task_log(
        self,
        session_id: str,
        task_id: str,
        step_type: str,
        step_data: dict,
        duration: float = 0.0,
        status: str = "running",
    ) -> int:
        """保存一条任务步骤日志"""
        conn = self._get_conn()
        try:
            cur = conn.execute(
                "INSERT INTO task_logs (session_id, task_id, step_type, step_data, duration, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    session_id,
                    task_id,
                    step_type,
                    json.dumps(step_data, ensure_ascii=False),
                    duration,
                    status,
                    _now(),
                ],
            )
            conn.commit()
            return cur.lastrowid or -1
        except sqlite3.Error as e:
            logger.error("[SessionStore] 保存任务日志失败: %s", e)
            return -1
        finally:
            conn.close()

    def get_task_logs(self, task_id: str, limit: int = 200) -> list[dict[str, Any]]:
        """获取一个任务的完整执行日志"""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM task_logs WHERE task_id = ? ORDER BY id ASC LIMIT ?",
                [task_id, limit],
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                try:
                    d["step_data"] = json.loads(d["step_data"])
                except (json.JSONDecodeError, TypeError):
                    d["step_data"] = {}
                result.append(d)
            return result
        finally:
            conn.close()

    def get_session_logs(
        self, session_id: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        """获取会话的所有任务日志（去重摘要）"""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT DISTINCT task_id, status, created_at FROM task_logs "
                "WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                [session_id, limit],
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_recent_tasks(self, limit: int = 20) -> list[dict[str, Any]]:
        """获取所有会话的最近任务"""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT task_id, session_id, step_type, status, duration, created_at "
                "FROM task_logs ORDER BY id DESC LIMIT ?",
                [limit],
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


# 全局单例
store: SessionStore = SessionStore()
