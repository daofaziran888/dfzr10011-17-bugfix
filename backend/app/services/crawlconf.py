"""采集配置模板业务规则：状态流转、字段校验与筛选口径都收在这里。

生命周期固定按 草稿 → 已发布 → 已停用 → 已废弃 推进，不允许跳步；
每次推进都把版本号与配置参数快照一起落库，停用/废弃留有回执与依据，
已废弃是归档终态，不再接受任何动作。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "crawlconf"
REQUIRED_FIELDS = ["模板编号", "模板名称", "目标类型"]
STATUS_ORDER = ["草稿", "已发布", "已停用", "已废弃"]
TERMINAL_STATUS = STATUS_ORDER[-1]
# 动作 -> (允许执行的当前状态集合, 推进到的目标状态)
ACTION_RULES = {
    "发布模板": (("草稿", "已发布"), "已发布"),
    "停用模板": (("已发布",), "已停用"),
    "废弃模板": (("已停用",), "已废弃"),
}
NEGATIVE_ACTIONS = ["停用模板"]
PENDING_STATUSES = {"草稿", "已发布"}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _next_version(current: Any) -> str:
    """版本号按 v1、v2…… 递增；历史脏数据一律从 v1 重新开始。"""
    text = str(current or "").strip()
    if text.lower().startswith("v") and text[1:].isdigit():
        return f"v{int(text[1:]) + 1}"
    return "v1"


class CrawlconfService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("模板编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["配置参数"] = str(values.get("配置参数") or "").strip()
        entry["版本号"] = "v0"
        entry["版本历史"] = []
        entry["流转记录"] = []
        entry["status"] = STATUS_ORDER[0]
        entry["模板状态"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(
        self,
        entry_id: int,
        action: str,
        values: dict[str, Any] | None = None,
        remark: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"配置模板 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于采集配置模板可执行范围"
        sources, target = ACTION_RULES[action]
        current = str(entry.get("status") or "")
        if current == TERMINAL_STATUS:
            return None, f"配置模板已{TERMINAL_STATUS}，归档终态保持，不再执行「{action}」"
        if current not in sources:
            return None, (
                f"配置模板当前状态为「{current}」，不能执行「{action}」，"
                f"生命周期需按{'→'.join(STATUS_ORDER)}推进"
            )
        basis = str(remark or "").strip() or f"{action}：按生命周期{'→'.join(STATUS_ORDER)}推进"
        if action == "发布模板":
            return self._publish(entry, current, target, values or {}, basis)
        self._transition(entry, action, current, target, basis)
        return entry, f"配置模板已{action}"

    def _publish(
        self,
        entry: dict[str, Any],
        current: str,
        target: str,
        values: dict[str, Any],
        basis: str,
    ) -> tuple[dict[str, Any] | None, str]:
        new_params = values.get("配置参数")
        if new_params is not None and not str(new_params).strip():
            # 发布校验失败：只回退恢复上一份完整参数，版本号与状态保持不动
            self._rollback_params(entry)
            self._record(entry, "发布回退", current, current, f"{basis}；发布校验失败，已回退到上一份完整参数")
            return None, "发布校验失败：配置参数为空，已回退到上一份完整参数"
        if new_params is not None:
            entry["配置参数"] = str(new_params).strip()
        entry["版本号"] = _next_version(entry.get("版本号"))
        history = entry.setdefault("版本历史", [])
        # 并发发布只保留一个可用版本：新快照落库后，旧快照全部转为历史
        for snapshot in history:
            snapshot["可用"] = False
        history.append({
            "版本号": entry["版本号"],
            "配置参数": entry.get("配置参数", ""),
            "状态": target,
            "可用": True,
            "时间": _now(),
        })
        self._transition(entry, "发布模板", current, target, basis)
        return entry, f"配置模板已发布模板，当前版本 {entry['版本号']}"

    def _rollback_params(self, entry: dict[str, Any]) -> bool:
        history = [snap for snap in entry.get("版本历史") or [] if isinstance(snap, dict)]
        if not history:
            return False
        entry["配置参数"] = history[-1].get("配置参数", entry.get("配置参数", ""))
        return True

    def _transition(
        self,
        entry: dict[str, Any],
        action: str,
        current: str,
        target: str,
        basis: str,
    ) -> None:
        entry["status"] = target
        entry["模板状态"] = target
        entry["pending"] = target in PENDING_STATUSES
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        self._record(entry, action, current, target, basis)

    def _record(
        self,
        entry: dict[str, Any],
        action: str,
        source: str,
        target: str,
        basis: str,
    ) -> None:
        record = {
            "动作": action,
            "从状态": source,
            "到状态": target,
            "版本号": entry.get("版本号", ""),
            "依据": basis,
            "时间": _now(),
        }
        if action == "停用模板":
            record["回执"] = f"停用回执-{entry.get('模板编号', entry.get('id'))}-{entry.get('版本号', '')}"
            entry["停用回执"] = record["回执"]
        entry.setdefault("流转记录", []).append(record)
